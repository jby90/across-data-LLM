# ruff: noqa: E501
"""Actionable delivery aids for compliance document projects:
1. Third-country local law verification checklist (Upgrade 3: honest 6-country boundary + actionable 5-point local counsel verification & single-PDF admin import closure);
2. Core narrative field information-density precheck & scenario-tailored draft expansion with explicit confirmation markers (Upgrade 5);
3. Pre-submission manual legal/security sign-off checklist for formal regulatory filing packages (Upgrade 6).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.analysis.document_basis import NAMES, country_of

_EU_EEA_CODES = frozenset({
    "EU", "DE", "FR", "IT", "ES", "NL", "BE", "AT", "SE", "PL", "IE", "DK", "FI", "PT",
    "GR", "CZ", "HU", "RO", "BG", "HR", "SK", "SI", "LT", "LV", "EE", "LU", "MT", "CY",
    "IS", "LI", "NO",
})

_SCENARIO_LABELS: dict[str, str] = {
    "ota_diagnostics": "远程故障诊断与软件升级（OTA）",
    "connected_vehicle_cloud": "车联网运行状态同步与云端服务",
    "autonomous_trial": "智能驾驶路测采集与算法训练",
    "cockpit_biometrics": "智能座舱交互与生物特征处理",
    "hd_mapping": "高精地图要素更新与空间位置服务",
    "after_sales": "海外销售客户管理与售后维保协同",
}

# 关键论述填报项的要素完整度规则：(维度名称, 命中正则)
_DENSITY_FACETS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "purpose": [
        ("车载业务触发条件", re.compile(r"触发|故障|告警|升级|启动|开启|请求|维护|诊断|路测|交互|下单|维保|地图|偏置")),
        ("跨境协同具体用途", re.compile(r"用于|支撑|实现|排查|核验|分析|训练|履约|服务|修复|验证|更新|结算")),
        ("最小必要排除边界", re.compile(r"仅|不涉及|不含|排除|最小|必需|必要|限度|白名单")),
    ],
    "data_types": [
        ("具体车载或客户字段", re.compile(r"码|日志|参数|状态|电压|温度|转速|里程|坐标|轨迹|影像|视频|语音|姓名|电话|车架号|VIN|报文|传感器|特征")),
        ("个人/敏感属性说明", re.compile(r"敏感|定位|位置|轨迹|人脸|声纹|生物|个人|自然人|不含敏感|去标识")),
        ("源端脱敏或精度控制", re.compile(r"脱敏|模糊|精度|假名|哈希|掩码|剥离|过滤|擦除|降频|匿名")),
    ],
    "risk_measures": [
        ("源端采集脱敏过滤", re.compile(r"脱敏|模糊|去标识|假名|白名单|剥离|掩码|最小")),
        ("跨境传输加密鉴权", re.compile(r"专线|加密|TLS|证书|双向鉴权|通道|校验|签名")),
        ("境外隔离与最小权限", re.compile(r"隔离|最小权限|审批|分级|角色|访问控制|密钥|专人")),
        ("操作审计与应急管理", re.compile(r"日志|审计|留痕|应急|制度|培训|协议|定期")),
    ],
    "security_tech": [
        ("采集端脱敏预处理", re.compile(r"收集|采集|车端|脱敏|模糊|去标识|白名单|过滤")),
        ("传输通道加密校验", re.compile(r"传输|专线|TLS|加密|鉴权|通道|完整性")),
        ("存储加密与访问控制", re.compile(r"存储|隔离|权限|审批|密钥|日志|审计")),
        ("期满安全删除机制", re.compile(r"删除|销毁|清除|到期|备份")),
    ],
    "retention_and_blackbox": [
        ("明确留存期限", re.compile(r"\d+\s*(?:年|个月|月|天|日|小时)|期限|届满|保存至")),
        ("境外物理存放位置", re.compile(r"机房|数据中心|云|服务器|本地|位于|区域|节点")),
        ("到期含备份删除方式", re.compile(r"删除|销毁|清除|覆写|备份|留痕|记录")),
    ],
    "necessity": [
        ("单车/跨境协同不可替代性", re.compile(r"单车|基线|研发|总部|核心|复现|定位|比对|协同|无法")),
        ("本地或匿名化替代方案排他检验", re.compile(r"本地|离线|汇总|合成|匿名|替代|满足不了|难以")),
    ],
}


def _scenario_phrase(scenario_type: str) -> str:
    return _SCENARIO_LABELS.get(scenario_type or "", "智能网联汽车跨境数据协同业务")


def _build_draft_template(
    key: str,
    current_val: str,
    *,
    scenario_type: str,
    source_country: str,
    recipient_country: str,
    vehicle_model: str,
) -> str:
    scene = _scenario_phrase(scenario_type)
    src = source_country or "发出国"
    rcp = recipient_country or "接收国"
    veh = vehicle_model or "目标网联车型"
    seed = current_val.strip().rstrip("。；; ")
    prefix = f"{seed}；" if seed else ""

    if key == "purpose":
        return (
            f"{prefix}围绕{veh}在{src}运行期间的{scene}需求，仅当车载终端或业务系统触发预设事件条件时，"
            f"将必要的运行与服务参数同步至{rcp}指定团队，用于定位偶发异常、验证软件兼容性及保障行车与服务安全；"
            "严格遵循最小必要原则，不用于商业营销画像等无关用途【请结合本企业实际业务触发条件核对确认】。"
        )
    if key == "data_types":
        return (
            f"{prefix}主要包括与{scene}直接关联的【请确认具体字段：如电控故障诊断码、零部件运行工况参数、软件版本号】；"
            "车辆关联标识在出境前置节点完成去标识化假名映射，涉及位置信息的预先降低定位精度，明确排除与本次目的无关的车内外音视频及通讯录数据【请按实际出境字段白名单核对确认】。"
        )
    if key in ("risk_measures", "security_tech"):
        return (
            f"{prefix}技术措施：（1）采集与出境前：车端及前置网关执行字段白名单过滤与去标识化脱敏；（2）跨境传输：建立双向证书鉴权的加密专用传输通道并实施报文完整性校验；"
            f"（3）境外存储与使用：在{rcp}受控机房实施逻辑隔离加密存储与独立密钥管理，严格执行岗位最小权限与审批授权；（4）删除销毁：留存期满自动同步安全清除生产主库与备份数据。"
            "管理措施：建立数据分类分级、跨境审批、全量操作审计日志留痕及安全事件应急响应制度【请按本企业实际技术与管理配置核对确认】。"
        )
    if key == "retention_and_blackbox":
        return (
            f"{prefix}留存期限为【请确认具体保存期限，如：自采集之日起保存 X 年/月】；物理存放于{rcp}【请确认具体城市及自有/云服务商受控数据中心名称】逻辑隔离存储环境；"
            "保存期限届满或业务目的达成后，系统自动对主库及全部灾备副本执行不可恢复的安全删除并留存销毁审计记录。"
        )
    if key == "necessity":
        return (
            f"{prefix}{scene}高度依赖核心工程与服务平台的历史基线比对及跨批次问题溯源能力；经评估，若仅依赖{src}本地离线处理、采用汇总统计样本或彻底剥离车辆关联编码，"
            "将无法复现单车特定硬件批次的偶发工况，亦无法定向下发修复方案，故在严格限定最小字段白名单的前提下开展跨境传输具有排他必要性【请结合实际替代方案评估情况核对确认】。"
        )
    return current_val


def evaluate_field_density(
    key: str,
    value: str,
    *,
    scenario_type: str = "",
    source_country: str = "",
    recipient_country: str = "",
    vehicle_model: str = "",
) -> dict[str, Any] | None:
    """Inspect whether a filled core narrative field has enough concrete compliance facets to
    support substantive report generation. Never alters ``missing`` status or blocks draft/final."""
    rules = _DENSITY_FACETS.get(key)
    if not rules:
        return None
    val = (value or "").strip()
    if not val or val.startswith("暂不确定"):
        return None
    missing_facets = [name for name, pat in rules if not pat.search(val)]
    min_len = 22 if key in ("purpose", "necessity") else 28
    # Well-elaborated answers (>= 45 chars with at most 2 regex misses, or >= min_len with <= 1 miss) pass cleanly
    if len(val) >= 45 and len(missing_facets) <= 2:
        return None
    if len(val) >= min_len and len(missing_facets) <= 1:
        return None
    if not missing_facets and len(val) >= 16:
        return None
    shown_missing = missing_facets or ["具体业务触发边界与管控细节"]
    prefix_msg = (
        f"当前填写偏简略（{len(val)} 字），"
        if len(val) < 36
        else "当前描述可进一步细化关键合规维度，"
    )
    return {
        "missing_facets": shown_missing,
        "message": f"{prefix_msg}生成深度合规报告建议补充：{'、'.join(shown_missing)}",
        "draft_template": _build_draft_template(
            key,
            val,
            scenario_type=scenario_type,
            source_country=source_country,
            recipient_country=recipient_country,
            vehicle_model=vehicle_model,
        ),
    }


def build_third_country_checklists(
    source_country: str,
    recipient_country: str,
    library_codes: set[str],
    *,
    scenario_type: str = "",
    vehicle_model: str = "",
) -> list[dict[str, Any]]:
    """Build structured 5-point statutory verification checklists for any flow endpoint outside
    the platform's verified 6-country regulation library (CN + SG/MA/EG/TN/LB or newly imported countries)."""
    out: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    scene = _scenario_phrase(scenario_type)
    veh = vehicle_model or "智能网联车型"

    for role_label, raw_name in (("数据发出国", source_country), ("数据接收国", recipient_country)):
        cname = (raw_name or "").strip()
        if not cname or cname in seen_names:
            continue
        code = country_of(cname)
        # CN always has domestic CAC statutory rules & official templates; library_codes holds DB-ingested countries
        if code == "CN" or (code and code in library_codes):
            continue
        seen_names.add(cname)
        disp = NAMES.get(code, cname) if code else cname
        in_eu = bool(code and code in _EU_EEA_CODES)
        framework_note = (
            f"{disp}属于欧盟/欧洲经济区法域，当前报告结构已直接采用欧盟通用数据保护条例（GDPR）及欧洲数据保护委员会（EDPB）标准框架；"
            f"但{disp}本国数据保护成文实施细则、国家监管机关备案程序及智能网联汽车专门规章尚未纳入平台现行条文库。"
            if in_eu
            else f"{disp}的属地法律法规原文当前尚未纳入平台现行核验条文库（平台严守零编造法条原则，不凭模型记忆虚构未入库法条，已按国际通行高标准框架生成报告底稿）。"
        )
        items = [
            {
                "seq": "核查项 1",
                "topic": "属地个人数据与车端敏感数据法定界定",
                "detail": f"核查{disp}现行数据保护法是否将车辆识别代号（VIN）、高精度行车轨迹、座舱语音/影像及驾驶行为特征列为个人数据或特殊类别敏感数据，明确围绕「{veh} · {scene}」的法定告知与同意颗粒度。",
            },
            {
                "seq": "核查项 2",
                "topic": "数据本地化强制要求与跨境传输前置工具",
                "detail": f"核查{disp}对汽车运行、地理测绘或客户数据是否设有境内存储强制义务；作为{role_label}时，确认适用充分性白名单、标准合同条款备案还是主管机关逐案事先授权。",
            },
            {
                "seq": "核查项 3",
                "topic": "当地公权力机关执法调取程序与抗辩边界",
                "detail": f"核查{disp}司法、执法或国家安全机关调取商业车联网数据的法定职权、司法令状前置程序、比例原则边界，以及是否允许接收方在被调取时向数据发出方履行即时通报义务。",
            },
            {
                "seq": "核查项 4",
                "topic": "属地独立监管机关登记、本地代表与处罚标准",
                "detail": f"核实{disp}主管数据保护机构法定全称、数据控制者是否须在当地指定法定代表或数据保护官（DPO）、安全事件法定报告时限（如小时级上限）及违规行政罚款计算基准。",
            },
            {
                "seq": "核查项 5",
                "topic": "智能网联汽车准入、无线电与软件升级专门监管",
                "detail": f"核查{disp}交通、通信及测绘主管部门针对车载蜂窝通信模块（T-Box）型号核准、道路测试牌照及远程软件升级（OTA）备案的行业专门规定。",
            },
        ]
        out.append(
            {
                "country": disp,
                "country_code": code or "",
                "role": role_label,
                "in_eu_eea": in_eu,
                "framework_note": framework_note,
                "import_guide": (
                    f"闭环扩充指引：如企业法务或当地合作律所已取得{disp}官方发布的法规原文文件（PDF / Word），"
                    "可由管理员在管理端【法规管理 → 单份导入】上传入库，系统完成条文切分与译文核对后将自动纳入本项目的法条引用与原文校验。"
                ),
                "items": items,
            }
        )
    return out


def render_third_country_blocks(project_title: str, checklists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert ``third_country_checklists`` into formal ``Rendered`` blocks for online preview and Word/PDF/Excel export."""
    blocks: list[dict[str, Any]] = [
        {"type": "title", "text": f"{project_title} — 库外法域属地法律待核查事项清单"},
        {
            "type": "paragraph",
            "text": f"编制日期：{date.today().isoformat()}　｜　用途：供企业法务、合规团队及境外合作律所逐项核实未入库法域的属地强制性规定，严防遗漏属地前置审批或本地化义务。",
        },
    ]
    for idx, ck in enumerate(checklists, 1):
        blocks.append({"type": "heading", "text": f"{idx}、{ck['country']}（{ck['role']}）属地法律核查清单"})
        blocks.append({"type": "paragraph", "text": ck["framework_note"]})
        blocks.append(
            {
                "type": "table",
                "head": ["核查编号", "核查维度", "具体核查要点与业务对齐要求", "核查结论与律所意见（待填）"],
                "widths": [12, 22, 46, 20],
                "rows": [[it["seq"], it["topic"], it["detail"], "□ 已核实合规\n□ 需补充整改"] for it in ck["items"]],
            }
        )
        blocks.append({"type": "paragraph", "text": ck["import_guide"]})
    return blocks


_UNCONFIRMED_MARKER_RE = re.compile(r"【待补充】|【请[^】]{1,30}确认[^】]*】|暂不确定")


def build_signoff_checklist(overview: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
    """Generate a tailored pre-submission manual verification & departmental sign-off checklist
    covering the 'last mile' items that require human legal/security verification before stamping."""
    route = overview.get("route") or {}
    src = route.get("source_country") or values.get("source_country") or "发出国"
    rcp = route.get("recipient_country") or values.get("recipient_country") or "接收国"
    company = route.get("company_name") or values.get("company_name") or "【待核定申报主体全称】"
    vehicle = route.get("vehicle_model") or values.get("vehicle_model") or "【待核定车型/车队范围】"
    cn = overview.get("cn_route") or {}
    appl = overview.get("applicability") or {}
    docs = overview.get("documents") or []
    req_docs = [d for d in docs if appl.get(d["code"], {}).get("state") != "na"]

    # Determine primary filing authority & statutory deadline reminder
    if country_of(src) == "CN":
        if cn.get("route") == "security_assessment":
            authority = "所在地省级网信部门（初审后报国家网信办开展数据出境安全评估）"
            deadline_note = "《数据出境风险自评估报告》须在正式提交申报之日前 3 个月内完成；取得评估结果后有效期 3 年。"
        elif cn.get("route") == "standard_contract":
            authority = "所在地省级网信部门（个人信息出境标准合同备案管理机关）"
            deadline_note = "须在《个人信息出境标准合同》生效之日起 10 个工作日内向所在地省级网信部门提交合同原件与《个人信息保护影响评估报告》完成备案。"
        elif cn.get("route") == "exempt":
            authority = "企业内部合规归档备查（符合法定免予申报情形，无需向网信部门前置申报备案）"
            deadline_note = "当跨境用途、字段范围或年累计出境人数发生变更触及法定阈值时，须立即重新开展路径判定并履行前置程序。"
        else:
            authority = "所在地省级网信部门（待补充规模与敏感信息情况后最终核定法定路径）"
            deadline_note = "请先补齐涉及自然人数量、敏感个人信息及重要数据认定情况以锁定法定申报时限。"
    else:
        of_list = [f for d in req_docs for f in (d.get("official_forms") or [])]
        if of_list:
            def _fmt_auth(f_item: dict) -> str:
                az = str(f_item.get("authority_zh") or "").strip()
                sh = str(f_item.get("short") or "").strip()
                if not sh or sh.lower() in az.lower():
                    return re.sub(r"（([^）]+)）\s*（\1）", r"（\1）", az)
                return f"{az}（{sh}）"

            authority = "、".join(dict.fromkeys(_fmt_auth(f) for f in of_list if _fmt_auth(f)))
            deadline_note = "须在启动跨境传输前按发出国主管机关要求递交已签章的官方原表及配套评估证明材料。"
        else:
            authority = f"{src}及{rcp}主管数据保护监管机关 / 企业数据合规委员会存档备查"
            deadline_note = "建议在跨境链路正式投产上线前完成全套合规评估文书内部会签与跨境传输协议双方法定代表人签署。"

    # Scan for any unconfirmed placeholders across questions
    unconfirmed_fields: list[str] = []
    for q in overview.get("questions") or []:
        if appl and q.get("used_in") and all(appl.get(c, {}).get("state") == "na" for c in q["used_in"]):
            continue
        v = str(q.get("value") or "").strip()
        if not v or _UNCONFIRMED_MARKER_RE.search(v):
            unconfirmed_fields.append(q.get("label") or q.get("key") or "")

    credit_val = str(values.get("credit_code") or "").strip() or "（待核对营业执照 / 商业登记证原件）"
    legal_rep = str(values.get("legal_rep") or "").strip() or "（待核对法定代表人身份证件及职务）"
    sec_officer = str(values.get("security_officer") or "").strip() or "（待核对数据安全负责人 / DPO 正式任命书）"
    eff_proof = str(values.get("effectiveness_proof") or "").strip() or "（待核对等保测评报告、ISO 27001/27701 或 UN R155/R156 证书编号）"
    gov_law = str(values.get("governing_law") or "").strip() or "（待法务核定准据法与仲裁/管辖法院条款）"
    retention_val = str(values.get("retention_and_blackbox") or "").strip() or "（待研发与运维核实境外机房位置与到期自动清除脚本）"

    entity_rows = [
        ["1", "申报主体法定全称与证照代码", f"当前填报：{company}｜代码：{credit_val}", "法务部 / 行政部", "□ 原件核对一致"],
        ["2", "法定代表人及经办人授权委托文件", f"当前填报：{legal_rep}；核验经办人授权委托书及身份证件复印件加盖公章情况", "法务部", "□ 已备齐签章件"],
        ["3", "数据安全负责人 / DPO 正式任命文件", f"当前填报：{sec_officer}；核对内部红头任命文件与管理架构图一致", "数据合规与安全部", "□ 已核对一致"],
        ["4", "股权穿透与近两年行政处罚核实", "对照国家企业信用信息公示系统及内部法务台账，确认近两年是否存在网信、工信或测绘主管部门行政处罚及整改结案证明", "法务部", "□ 已核实无误"],
    ]

    tech_rows = [
        ["1", "出境数据字段白名单与线上网关一致性", f"围绕「{vehicle}」实车抓包与出境前置网关配置，核实实际上传字段未超出报告所列白名单，且车内外敏感特征已按声明完成源端脱敏", "车载研发部 / 安全团队", "□ 实测核验通过"],
        ["2", "网络安全测评与管理体系认证证书文号", f"当前填报：{eff_proof}；逐字核对测评报告编号、签发机构名称及证书有效期限是否在有效期内", "信息安全部", "□ 证书原件已核"],
        ["3", "境外存储逻辑隔离与到期含备份删除实效", f"当前填报：{retention_val}；核验境外存储环境最小权限矩阵、密钥独立托管方案及到期自动清除定时任务记录", "云平台运维部", "□ 配置核验通过"],
    ]

    contract_rows = [
        ["1", "跨境传输协议正式签署与违约连带责任", f"核对与境外接收方签署的正式合同是否完整纳入唯一目的限制、禁止擅自向第三国再转移、违约赔偿上限及准据法（{gov_law}）", "法务部 / 采购商务部", "□ 法务终审通过"],
        ["2", "外国公权力调取通报与法定审批阻断条款", "确认合同已明确约定接收方收到当地司法/执法调取要求时的合法性审查、即时通报义务，以及未经发出国主管机关依法批准不得径行提供数据的阻断条款", "首席合规官 / 法务部", "□ 条款已锁定"],
        ["3", "本案卷必备交付文件齐套与法定递交时限", f"应出文件共 {len(req_docs)} 份（{'、'.join(d['name'] for d in req_docs)}）；时限要求：{deadline_note}", "项目经办人", "□ 齐套待递交"],
    ]

    blocks: list[dict[str, Any]] = [
        {"type": "title", "text": f"{overview.get('title', '跨境合规项目')} — 正式申报递交前人工核签确认单"},
        {
            "type": "paragraph",
            "text": f"生成日期：{date.today().isoformat()}　｜　受理/备案去向：{authority}",
        },
        {
            "type": "paragraph",
            "text": (
                "核签提示：系统已完成法规条文匹配、报告初稿编制与跨表一致性校验。正式打印盖章或向监管机关递交前，"
                "请各责任部门对照下表完成证照原件、线上技术配置及商务合同签署的最终人工复核。"
                + (
                    f"【注意：检测到当前项目仍有 {len(unconfirmed_fields)} 处含待补充/待确认标记（{'、'.join(unconfirmed_fields[:5])}），请先行补齐后再定稿签章！】"
                    if unconfirmed_fields
                    else "【当前项目全部必填项均已填写完备，无待补充占位符。】"
                )
            ),
        },
        {"type": "heading", "text": "一、主体资质、证照原件与授权文件核对"},
        {
            "type": "table",
            "head": ["序号", "核验事项", "本项目待核对内容与要求", "责任部门", "复核确认"],
            "widths": [8, 22, 42, 14, 14],
            "rows": entity_rows,
        },
        {"type": "heading", "text": "二、实车采集网关、安全测评证书与境外环境核实"},
        {
            "type": "table",
            "head": ["序号", "核验事项", "本项目待核对内容与要求", "责任部门", "复核确认"],
            "widths": [8, 22, 42, 14, 14],
            "rows": tech_rows,
        },
        {"type": "heading", "text": "三、跨境合同关键法律条款与法定递交时限把关"},
        {
            "type": "table",
            "head": ["序号", "核验事项", "本项目待核对内容与要求", "责任部门", "复核确认"],
            "widths": [8, 22, 42, 14, 14],
            "rows": contract_rows,
        },
        {"type": "heading", "text": "四、内部会签与法定代表人/授权代表签章栏"},
        {
            "type": "table",
            "head": ["会签角色", "复核意见", "签字 / 日期"],
            "widths": [28, 44, 28],
            "rows": [
                ["车载业务与研发负责人", "确认出境数据字段范围、采集触发频次及车端脱敏配置与实车系统一致。", "签字：__________\n日期：____年__月__日"],
                ["信息安全与云运维负责人", "确认加密传输通道、境外存储隔离、最小权限及备份销毁机制真实有效。", "签字：__________\n日期：____年__月__日"],
                ["数据合规官 / 法务负责人", "确认适用法定路径准确、跨境合同条款完备、证照与认证文号核对无误。", "签字：__________\n日期：____年__月__日"],
                ["企业法定代表人（或授权代表）", "批准按上述核定材料正式签署并履行监管申报/备案或合规归档程序。", "签章：__________\n日期：____年__月__日"],
            ],
        },
    ]

    return {
        "authority": authority,
        "deadline_note": deadline_note,
        "required_docs_count": len(req_docs),
        "unconfirmed_count": len(unconfirmed_fields),
        "check_items_count": len(entity_rows) + len(tech_rows) + len(contract_rows),
        "blocks": blocks,
    }
