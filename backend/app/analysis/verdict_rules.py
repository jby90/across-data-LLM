# ruff: noqa: E501
"""标准判定结论范本与确定性结论同步引擎（确保全套评估与跟踪报告结论自洽、零矛盾）。"""

from __future__ import annotations

import copy
import re
from typing import Any

from app.analysis import risk_items
from app.analysis.document_basis import NAMES, country_of

VERDICT_DOC_CODES: tuple[str, ...] = (
    "risk_report",
    "dpia",
    "lia",
    "tia",
    "remediation",
    "admission",
)

VERDICT_DOC_NAMES: dict[str, str] = {
    "risk_report": "汽车数据跨境风险评估报告",
    "dpia": "数据保护影响评估（DPIA）",
    "lia": "合法利益评估（LIA）",
    "tia": "数据跨境传输影响评估（TIA）",
    "remediation": "合规问题整改跟踪报告",
    "admission": "海外属地市场准入材料清单及申报说明报告",
}

AVAILABLE_TOKENS: tuple[str, ...] = (
    "【数据发出国】",
    "【数据接收国】",
    "【业务场景】",
    "【风险总数】",
    "【初评高风险数】",
    "【未化解高风险数】",
    "【中风险数】",
    "【低风险数】",
    "【缺失项数】",
    "【法定前置程序】",
    "【属地准入事项数】",
)

DEFAULT_VERDICT_RULES: dict[str, list[dict[str, str]]] = {
    "risk_report": [
        {
            "tier": "pass",
            "condition": "属法定免予前置申报情形，必填信息完整且高、中风险均为 0 项",
            "label": "通过",
            "tone": "done",
            "template": (
                "经对照【数据发出国】现行数据跨境监管法规对【业务场景】开展核查，本次传输符合法定免予前置行政申报条件，"
                "当前未化解高风险 0 项、中风险 0 项、低风险【低风险数】项。综合判定为通过：按企业内控台账归档留存，"
                "持续执行最小必要字段控制、传输日志审计与到期安全删除。"
            ),
        },
        {
            "tier": "conditional",
            "condition": "必填要素齐全且当前未化解高风险为 0 项（含需履行前置申报备案或含受控中低风险）",
            "label": "有条件通过",
            "tone": "done",
            "template": (
                "经对照【数据发出国】现行数据跨境监管法规对【业务场景】开展核查，本项目共识别【风险总数】项风险，"
                "落实保护措施后当前剩余高风险 0 项、中风险【中风险数】项、低风险【低风险数】项。综合判定为有条件通过："
                "须在正式向【数据接收国】启动传输前办结【法定前置程序】，持续执行最小必要字段范围与到期安全删除机制；"
                "发生业务范围扩大、规模跨越法定阈值或接收国法规变化时须重新开展评估。"
            ),
        },
        {
            "tier": "deferred",
            "condition": "当前存在未化解高风险（大于 0 项）或尚缺核心必填要素",
            "label": "暂缓放行",
            "tone": "danger",
            "template": (
                "经核查，本项目当前尚有【未化解高风险数】项高风险事项未压降至可控范围（待补必填信息【缺失项数】项）。"
                "本次自【数据发出国】向【数据接收国】的数据传输综合判定为暂缓放行：须先行完成源端脱敏与加密整改、"
                "复评确认高风险清零并补齐申报要素后，方可启动跨境传输申报与上线流程。"
            ),
        },
        {
            "tier": "rejected",
            "condition": "拟传输数据命中发出国法定禁止向境外提供的红线目录",
            "label": "不予放行",
            "tone": "danger",
            "template": (
                "经对照【数据发出国】现行法律法规核查，本次拟传输字段触及法定禁止向境外提供的数据红线，综合判定为不予放行："
                "严禁向【数据接收国】传输相关原始数据，须改为境内本地化存储与离线处理方案。"
            ),
        },
    ],
    "dpia": [
        {
            "tier": "conditional",
            "condition": "必填要素齐全且落实保护措施后剩余高风险为 0 项",
            "label": "有条件通过",
            "tone": "done",
            "template": (
                "经对照【数据发出国】数据保护影响评估规定核查，【业务场景】处理目的明确正当，出境字段已收敛于最小必要范围，"
                "落实源端去标识化与访问权限控制后当前剩余高风险 0 项、中风险【中风险数】项、低风险【低风险数】项，"
                "无需启动高剩余风险监管事先磋商，综合核定为有条件通过。"
            ),
        },
        {
            "tier": "deferred",
            "condition": "存在未化解高风险或尚缺核心评估要素",
            "label": "暂缓放行",
            "tone": "danger",
            "template": (
                "经评估，本项目当前存在【未化解高风险数】项未化解高侵害风险（待补必填要素【缺失项数】项），"
                "综合核定为暂缓放行：在完成源端降敏整改将高风险压降至中低风险区间（或依法完成监管事先磋商）前，不得启动跨境传输。"
            ),
        },
    ],
    "lia": [
        {
            "tier": "conditional",
            "condition": "三步测试要素完整且当前未化解高风险为 0 项",
            "label": "有条件通过",
            "tone": "done",
            "template": (
                "经目的正当性、必要性排他及权利平衡三步测试核查，在落实【数据发出国】源端脱敏、显著透明告知及便捷异议退出通道的前提下，"
                "本单位围绕【业务场景】主张的正当安全与履约利益未凌驾于自然人基本权利之上，综合核定为有条件通过。"
            ),
        },
        {
            "tier": "deferred",
            "condition": "存在未化解高风险或权衡要素待补齐",
            "label": "暂缓放行",
            "tone": "danger",
            "template": (
                "经三步测试核查，当前尚存【未化解高风险数】项高侵害风险未消除（待补要素【缺失项数】项），个人权益减损尚未得到充分对冲，"
                "综合核定为暂缓放行：未完成脱敏加固与权利保障补证前，不得援引合法利益开展跨境处理。"
            ),
        },
    ],
    "tia": [
        {
            "tier": "conditional",
            "condition": "跨境链路核查要素齐全且当前未化解高风险为 0 项",
            "label": "有条件通过",
            "tone": "done",
            "template": (
                "经对照跨境传输影响评估六步法核查，在叠加【数据发出国】境内密钥独立留存、双向鉴权加密通道及公权力调取审查通知机制后，"
                "出境数据在【数据接收国】可持续获得与【数据发出国】法定要求实质同等的保护水平，综合核定为有条件通过。"
            ),
        },
        {
            "tier": "deferred",
            "condition": "存在未化解高风险或跨境保护要素待核实",
            "label": "暂缓放行",
            "tone": "danger",
            "template": (
                "经六步法核查，本项目当前尚有【未化解高风险数】项高风险事项未受控（待补要素【缺失项数】项），"
                "综合核定为暂缓放行：须先行补足加密隔离与调取抗辩控制措施并复评消除高风险后，方可启动向【数据接收国】的数据传输。"
            ),
        },
    ],
    "remediation": [
        {
            "tier": "cleared",
            "condition": "必填项完整且当前未化解高风险已清零",
            "label": "高风险已清零",
            "tone": "done",
            "template": (
                "经逐项核对整改台账，本项目初评识别的【风险总数】项风险（含初评高风险【初评高风险数】项）均已落实技术改造与制度控制，"
                "经复评确认当前未化解高风险为 0 项，满足进入正式申报与上线准备阶段的放行条件。"
            ),
        },
        {
            "tier": "deferred",
            "condition": "仍有未化解高风险或整改核验要素待补齐",
            "label": "尚有高风险待整改",
            "tone": "danger",
            "template": (
                "经核对整改台账，本项目当前仍有【未化解高风险数】项高风险事项尚未完成闭环降级（待补整改信息【缺失项数】项），"
                "判定为暂缓放行：须按既定责任部门与完成时限落实源端整改并通过复测核销后方可放行上线。"
            ),
        },
    ],
    "admission": [
        {
            "tier": "permit_required",
            "condition": "目标市场存在适用的法定属地准入或事先许可事项",
            "label": "须先办结属地许可",
            "tone": "done",
            "template": (
                "对照【数据接收国】现行属地监管法规，本车型开展【业务场景】共涉及【属地准入事项数】项法定属地准入与登记许可事项。"
                "综合判定结论：须按本报告所列办理顺序向当地主管机关递交法定申报材料并取得批复回执后，方可开展当地实车入网与商业运营。"
            ),
        },
        {
            "tier": "no_permit",
            "condition": "当前业务范围未触发专门属地前置行政许可事项",
            "label": "按常规合规留档",
            "tone": "done",
            "template": (
                "对照【数据接收国】现行在册法规检索，当前【业务场景】未触发专门前置行政准入审批事项；"
                "建议按当地通用数据保护与车辆技术标准留存合规证明备查，并在实车交付前向当地主管机关复核最新办事要求。"
            ),
        },
    ],
}

_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")
_PROHIBITED_RE = re.compile(r"(?:命中|属于|涉及)[^。；\n]{0,16}(?:禁止出境|禁止向境外提供|负面清单禁止|国家核心数据|测绘原始成果)")
_POSITIVE_VERDICT_RE = re.compile(r"有条件通过|无条件通过|整体出境安全风险可控|处于低位可接受区间|处于低风险可控范围|实质同等的?保护|实质相当的?保护|未凌驾于")
_NEGATIVE_VERDICT_RE = re.compile(r"暂缓放行|不予放行|不得直接启动|不得援引合法利益|严禁出境放行|暂缓启动")


def get_effective_rules(code: str, definition: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Return customized verdict rules stored on template definition, falling back to defaults."""
    defaults = copy.deepcopy(DEFAULT_VERDICT_RULES.get(code, []))
    if not definition or not isinstance(definition, dict):
        return defaults
    raw = definition.get("verdict_rules")
    if not isinstance(raw, list) or not raw:
        return defaults
    by_tier = {str(item.get("tier") or ""): item for item in raw if isinstance(item, dict)}
    merged: list[dict[str, str]] = []
    for d in defaults:
        ovr = by_tier.get(d["tier"])
        if ovr:
            merged.append(
                {
                    "tier": d["tier"],
                    "condition": str(ovr.get("condition") or d["condition"]).strip()[:300],
                    "label": str(ovr.get("label") or d["label"]).strip()[:48],
                    "tone": d["tone"],
                    "template": str(ovr.get("template") or d["template"]).strip()[:2000],
                }
            )
        else:
            merged.append(d)
    return merged


def validate_verdict_rules(code: str, rules: Any) -> list[dict[str, str]]:
    """Validate admin-edited verdict rules strictly (0 null bytes, 0 surrogates, known tiers)."""
    from app.analysis.document_templates import TemplateError

    if code not in VERDICT_DOC_CODES:
        raise TemplateError("该合规文件不包含独立判定结论范本")
    if not isinstance(rules, list) or not rules:
        raise TemplateError("标准判定结论范本列表不能为空")
    allowed_tiers = {r["tier"] for r in DEFAULT_VERDICT_RULES[code]}
    default_by_tier = {r["tier"]: r for r in DEFAULT_VERDICT_RULES[code]}
    cleaned: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in rules:
        if not isinstance(item, dict):
            raise TemplateError("结论范本条目格式不正确")
        tier = str(item.get("tier") or "").strip()
        if tier not in allowed_tiers:
            raise TemplateError("包含不支持的判定档位标识")
        if tier in seen:
            raise TemplateError("判定档位重复")
        seen.add(tier)
        label = str(item.get("label") or "").strip()
        condition = str(item.get("condition") or "").strip()
        tmpl = str(item.get("template") or "").strip()
        for field_name, val, max_len in (
            ("判定结论名称", label, 48),
            ("触发条件说明", condition, 300),
            ("固定结论标准文本", tmpl, 2000),
        ):
            if not val:
                raise TemplateError(f"{field_name}不能为空")
            if len(val) > max_len:
                raise TemplateError(f"{field_name}超出长度限制（最多 {max_len} 字）")
            if "\x00" in val or _SURROGATE_RE.search(val):
                raise TemplateError(f"{field_name}包含非法字符")
            if "——" in val or "“" in val or "”" in val or "「" in val or "」" in val:
                raise TemplateError(f"{field_name}请使用平实公文表述，不使用引号或破折号")
        for tok_m in re.finditer(r"【[^【】]+】", tmpl):
            if tok_m.group(0) not in AVAILABLE_TOKENS:
                raise TemplateError(f"固定结论标准文本包含未定义的动态要素：{tok_m.group(0)}")
        cleaned.append(
            {
                "tier": tier,
                "condition": condition,
                "label": label,
                "tone": default_by_tier[tier]["tone"],
                "template": tmpl,
            }
        )
    # Preserve any unsubmitted default tiers in canonical order
    by_tier = {c["tier"]: c for c in cleaned}
    return [by_tier.get(d["tier"], copy.deepcopy(d)) for d in DEFAULT_VERDICT_RULES[code]]


def _fill_tokens(template_str: str, ctx: dict[str, Any]) -> str:
    out = template_str
    mapping = {
        "【数据发出国】": str(ctx["src_zh"]),
        "【数据接收国】": str(ctx["rcp_zh"]),
        "【业务场景】": str(ctx["scenario"]),
        "【风险总数】": str(ctx["total_risks"]),
        "【初评高风险数】": str(ctx["init_high"]),
        "【未化解高风险数】": str(ctx["high_left"]),
        "【中风险数】": str(ctx["med_left"]),
        "【低风险数】": str(ctx["low_left"]),
        "【缺失项数】": str(ctx["missing_count"]),
        "【法定前置程序】": str(ctx["route_proc"]),
        "【属地准入事项数】": str(ctx["admission_count"]),
    }
    for k, v in mapping.items():
        out = out.replace(k, v)
    return out


def _short_route_proc(r_kind: str, r_text: str, src_zh: str) -> str:
    """Convert verbose route explanation sentences into a clean noun phrase for 【法定前置程序】."""
    if r_kind == "security_assessment" or "申报数据出境安全评估" in r_text:
        return "网信部门数据出境安全评估申报"
    if r_kind == "standard_contract" or "订立标准合同" in r_text or "标准合同备案" in r_text:
        return "个人信息出境标准合同订立与备案（或个人信息保护认证）"
    if r_kind == "exempt" or "免予" in r_text or "免于" in r_text:
        return "企业数据出境内控台账留存"
    cleaned = re.sub(r"^[^：:]{2,16}[：:]\s*", "", r_text).strip().rstrip("。；;")
    if cleaned and len(cleaned) <= 42 and "待判定" not in cleaned:
        return cleaned
    return f"{src_zh}法定数据出境申报、备案或事先授权手续"


def compute_doc_verdict(
    code: str,
    values: dict[str, str],
    route_info: dict[str, Any] | None = None,
    *,
    task_country_code: str | None = None,
    missing_count: int = 0,
    admission_count: int = 0,
    definition: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Deterministically compute the live verdict badge & filled conclusion text for a document."""
    if code not in VERDICT_DOC_CODES:
        return None

    route = route_info or {}
    src_raw = str(values.get("source_country") or "").strip()
    rcp_raw = str(values.get("recipient_country") or "").strip()
    src_code = country_of(src_raw) or (task_country_code if not src_raw else None)
    rcp_code = country_of(rcp_raw)
    src_zh = NAMES.get(src_code or "", src_raw or "数据发出国")
    rcp_zh = NAMES.get(rcp_code or "", rcp_raw or "数据接收国")
    scenario = str(values.get("scenario") or "汽车数据跨境业务").strip()

    raw_risk = str(values.get("risk_rating") or "")
    r_sum = risk_items.summary(raw_risk) if raw_risk.strip() else None
    total_risks = r_sum["total"] if r_sum else 0
    init_high = r_sum["init_counts"]["高风险"] if r_sum else 0
    high_left = r_sum["current_counts"]["高风险"] if r_sum else 0
    med_left = r_sum["current_counts"]["中风险"] if r_sum else 0
    low_left = r_sum["current_counts"]["低风险"] if r_sum else 0

    r_kind = str(route.get("route") or route.get("kind") or "").strip()
    r_text = str(route.get("text") or "").strip()
    route_proc = _short_route_proc(r_kind, r_text, src_zh)

    imp_txt = f"{values.get('important_data', '')} {values.get('data_types', '')}"
    is_prohibited = bool(_PROHIBITED_RE.search(imp_txt))
    blocked = is_prohibited or high_left > 0 or missing_count > 0

    rules = {r["tier"]: r for r in get_effective_rules(code, definition)}
    ctx = {
        "src_zh": src_zh,
        "rcp_zh": rcp_zh,
        "scenario": scenario,
        "total_risks": total_risks,
        "init_high": init_high,
        "high_left": high_left,
        "med_left": med_left,
        "low_left": low_left,
        "missing_count": missing_count,
        "route_proc": route_proc,
        "admission_count": admission_count,
    }

    if code == "risk_report":
        if is_prohibited:
            picked = rules["rejected"]
        elif blocked:
            picked = rules["deferred"]
        elif (r_kind == "exempt" or "免予" in r_text or "免于" in r_text) and high_left == 0 and med_left == 0:
            picked = rules["pass"]
        else:
            picked = rules["conditional"]
    elif code in ("dpia", "lia", "tia"):
        picked = rules["deferred"] if blocked else rules["conditional"]
    elif code == "remediation":
        if blocked:
            picked = copy.deepcopy(rules["deferred"])
            if high_left > 0:
                picked["label"] = f"尚有 {high_left} 项高风险待整改"
            else:
                picked["label"] = "待补齐整改信息"
                picked["tone"] = "warn"
        else:
            picked = copy.deepcopy(rules["cleared"])
            if init_high == 0:
                picked["label"] = "风险已受控"
    else:  # admission
        picked = rules["permit_required"] if admission_count > 0 else rules["no_permit"]
        blocked = missing_count > 0

    tone = picked["tone"]
    if code in ("risk_report", "dpia", "lia", "tia") and blocked and high_left == 0 and not is_prohibited:
        tone = "warn"

    return {
        "tier": picked["tier"],
        "label": picked["label"],
        "tone": tone,
        "text": _fill_tokens(picked["template"], ctx),
        "blocked": blocked,
        "high_left": high_left,
        "missing_count": missing_count,
    }


def reconcile_conclusion_text(raw_text: str, verdict: dict[str, Any] | None) -> str:
    """Ensure manual/cached ``risk_conclusion`` never contradicts live high-risk blocking state."""
    txt = (raw_text or "").strip()
    if not verdict:
        return txt
    if not txt:
        return verdict["text"]
    if verdict["high_left"] > 0 and _POSITIVE_VERDICT_RE.search(txt):
        return verdict["text"]
    if not verdict["blocked"] and _NEGATIVE_VERDICT_RE.search(txt):
        return verdict["text"]
    return txt


_CONCL_HEADING_PREFIXES: dict[str, tuple[str, ...]] = {
    "dpia": ("6.", "七、"),
    "lia": ("四、",),
    "tia": ("六、",),
    "risk_report": ("八、",),
}


def reconcile_paper_blocks(
    paper: list[dict[str, Any]] | None,
    code: str,
    verdict: dict[str, Any] | None,
) -> list[dict[str, Any]] | None:
    """Synchronize cached narrative concluding paragraphs when risk level changes flip the verdict."""
    if not paper or not verdict or code not in _CONCL_HEADING_PREFIXES:
        return paper
    prefixes = _CONCL_HEADING_PREFIXES[code]
    out = copy.deepcopy(paper)
    in_concl = False
    for blk in out:
        btype = blk.get("type")
        btxt = str(blk.get("text") or "").strip()
        if btype == "heading":
            in_concl = any(btxt.startswith(p) for p in prefixes)
            continue
        if not in_concl or btype != "paragraph":
            continue
        if btxt.startswith(("结论性研判结果", "正式裁断理由")):
            continue
        if verdict["high_left"] > 0 and _POSITIVE_VERDICT_RE.search(btxt):
            blk["text"] = verdict["text"]
        elif not verdict["blocked"] and _NEGATIVE_VERDICT_RE.search(btxt):
            blk["text"] = verdict["text"]
    return out
