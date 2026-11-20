# ruff: noqa: E501
"""The main risks of a transfer as rows the enterprise fills in — one risk per row, each with its
likelihood and severity (低 / 中 / 高), the measures against it, who is in charge and by when — and
what is made of them: the risk level by the likelihood × severity matrix, before-vs-after risk grade
tracking across remediation iterations, controllable-risk exclusion for customizing formal submission
reports, the risk register and 3 × 3 matrix in the risk assessment report and DPIA, and the full
evolution ledger in the remediation tracking report.
"""

from __future__ import annotations

import json
import re
import uuid

STRUCTURED = frozenset({"risk_rating"})  # 按行填写的题
LONG_KEYS = frozenset({"risk_rating", "issues", "measures"})  # 可能比一般回答长（整改项会追加进来）
MAX_CHARS = 8000
LEVELS = ("低", "中", "高")
LEVEL_NAME = ("低风险", "中风险", "高风险")
# 可能性（行）× 严重程度（列）→ 等级，与风险评估报告“三、风险分级”的分级表一致；同步映射 L1/L3/L5 × S1/S3/S5（1–25 分）量化标尺
MATRIX = ((0, 0, 1), (0, 1, 2), (1, 2, 2))
QUANT_SCALE = {"低": 1, "中": 3, "高": 5}
NEEDED = (("risk", "风险内容"), ("likelihood", "可能性"), ("severity", "严重程度"), ("measures", "应对措施"))
FIELDS = (
    "id",
    "risk",
    "likelihood",
    "severity",
    "measures",
    "owner",
    "due",
    "mitigated",
    "init_likelihood",
    "init_severity",
)
UNSURE = "暂不确定"


def is_unsure(value: str | None) -> bool:
    return (value or "").strip().startswith(UNSURE)


def _norm_risk(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip())[:32]


def _row(raw: dict) -> dict:
    row = {k: str(raw.get(k) or "").strip() for k in FIELDS}
    for k in ("likelihood", "severity", "init_likelihood", "init_severity"):
        if row[k] not in LEVELS:
            row[k] = ""
    if not (row["init_likelihood"] and row["init_severity"]):
        row["init_likelihood"] = ""
        row["init_severity"] = ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["due"]):
        row["due"] = row["due"][:48]
        if row["due"].count("（") > row["due"].count("）"):
            row["due"] += "）" * (row["due"].count("（") - row["due"].count("）"))
    row["mitigated"] = "1" if row["mitigated"] in ("1", "true", "True", "已闭环", "已可控") else ""
    row["id"] = row["id"][:12] or uuid.uuid4().hex[:8]
    return row


def parse(value: str | None) -> list[dict]:
    """The rows of an answer. A paragraph written before rows existed stays whole as one row's risk."""
    text = (value or "").strip()
    if not text or is_unsure(text):
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, list):
            rows = [_row(r) for r in data if isinstance(r, dict)]
            return [r for r in rows if any(r[k] for k in FIELDS if k != "id")]
    return [_row({"id": "r1", "risk": text})]


def dump(rows: list[dict]) -> str:
    return json.dumps([_row(r) for r in rows], ensure_ascii=False, separators=(",", ":")) if rows else ""


def level(row: dict) -> str:
    if row.get("likelihood") not in LEVELS or row.get("severity") not in LEVELS:
        return ""
    return LEVEL_NAME[MATRIX[LEVELS.index(row["likelihood"])][LEVELS.index(row["severity"])]]


def init_level(row: dict) -> str:
    il, isv = row.get("init_likelihood", ""), row.get("init_severity", "")
    if il in LEVELS and isv in LEVELS:
        return LEVEL_NAME[MATRIX[LEVELS.index(il)][LEVELS.index(isv)]]
    return level(row)


def _score(lv: str) -> int:
    return LEVEL_NAME.index(lv) if lv in LEVEL_NAME else -1


def quant_triplet(likelihood: str, severity: str) -> tuple[int, int, int] | None:
    """Deterministic 5x5 quantitative risk score (1, 3, 5 scale -> 1..25) aligned monotonically with MATRIX."""
    if likelihood not in QUANT_SCALE or severity not in QUANT_SCALE:
        return None
    ql, qs = QUANT_SCALE[likelihood], QUANT_SCALE[severity]
    return ql, qs, ql * qs


def merge_history(prev_value: str | None, new_value: str | None) -> str:
    """Preserve each risk's initial grade across remediation edits or document re-imports so the user
    can always see before-vs-after risk reduction, unless ``reset_init`` is explicitly requested."""
    raw_text = (new_value or "").strip()
    if not raw_text or is_unsure(raw_text) or not raw_text.startswith("["):
        return raw_text
    try:
        incoming = json.loads(raw_text)
    except ValueError:
        return raw_text
    if not isinstance(incoming, list):
        return raw_text
    prev_rows = parse(prev_value)
    by_id = {r["id"]: r for r in prev_rows if r.get("id")}
    by_risk = {_norm_risk(r["risk"]): r for r in prev_rows if _norm_risk(r.get("risk", ""))}
    merged: list[dict] = []
    for raw in incoming:
        if not isinstance(raw, dict):
            continue
        reset_init = bool(raw.get("reset_init"))
        r = _row(raw)
        if not any(r[k] for k in FIELDS if k != "id"):
            continue
        if reset_init:
            if level(r):
                r["init_likelihood"], r["init_severity"] = r["likelihood"], r["severity"]
            else:
                r["init_likelihood"], r["init_severity"] = "", ""
        else:
            raw_id = str(raw.get("id") or "").strip()
            prev = (by_id.get(r["id"]) if raw_id else None) or by_risk.get(_norm_risk(r["risk"]))
            if prev:
                if not raw_id and prev.get("id"):
                    r["id"] = prev["id"]
                pil = prev["init_likelihood"] or prev["likelihood"]
                pis = prev["init_severity"] or prev["severity"]
                if pil in LEVELS and pis in LEVELS:
                    r["init_likelihood"], r["init_severity"] = pil, pis
            if not (r["init_likelihood"] and r["init_severity"]) and level(r):
                r["init_likelihood"], r["init_severity"] = r["likelihood"], r["severity"]
        merged.append(r)
    return dump(merged)


def summary(value: str | None) -> dict:
    """Structured before-vs-after evolution summary for UI display and overview cards."""
    rows = parse(value)
    init_counts = {k: 0 for k in LEVEL_NAME}
    current_counts = {k: 0 for k in LEVEL_NAME}
    downgraded = 0
    upgraded = 0
    excluded = 0
    changes: list[dict] = []
    for n, r in enumerate(rows, 1):
        lv = level(r)
        ilv = init_level(r)
        if ilv in init_counts:
            init_counts[ilv] += 1
        if r.get("mitigated") == "1":
            excluded += 1
            changes.append({
                "index": n,
                "id": r["id"],
                "risk": r["risk"],
                "from_level": ilv or lv or "待定级",
                "to_level": "已可控剔除",
                "state": "excluded",
            })
            continue
        if lv in current_counts:
            current_counts[lv] += 1
        if ilv and lv and ilv != lv:
            is_down = _score(lv) < _score(ilv)
            if is_down:
                downgraded += 1
            else:
                upgraded += 1
            changes.append({
                "index": n,
                "id": r["id"],
                "risk": r["risk"],
                "from_level": ilv,
                "to_level": lv,
                "state": "down" if is_down else "up",
            })
        elif lv:
            changes.append({
                "index": n,
                "id": r["id"],
                "risk": r["risk"],
                "from_level": ilv or lv,
                "to_level": lv,
                "state": "same",
            })
    return {
        "total": len(rows),
        "active_count": len(rows) - excluded,
        "excluded_count": excluded,
        "downgraded_count": downgraded,
        "upgraded_count": upgraded,
        "has_change": bool(downgraded or upgraded or excluded),
        "init_counts": init_counts,
        "current_counts": current_counts,
        "changes": changes,
    }


def lacking(value: str | None) -> list[str]:
    """What each row still lacks (“风险 2：严重程度、应对措施”); empty when every risk is complete."""
    out = []
    for n, row in enumerate(parse(value), 1):
        gaps = [label for key, label in NEEDED if not row[key]]
        if gaps:
            out.append(f"风险 {n}：{'、'.join(gaps)}")
    return out


def _change_phrase(row: dict) -> str:
    lv = level(row)
    ilv = init_level(row)
    if ilv and lv and ilv != lv:
        verb = "整改后降为" if _score(lv) < _score(ilv) else "调整为"
        return f"初评{ilv}，{verb}{lv}"
    return ""


def _who(row: dict, include_change: bool = True) -> str:
    parts = [
        _change_phrase(row) if include_change else "",
        f"责任人：{row['owner']}" if row["owner"] else "",
        f"完成时限：{row['due']}" if row["due"] else "",
    ]
    return "；".join(p for p in parts if p)


def text(value: str | None, blank: str = "") -> str:
    """The active remaining rows as plain text for formal submission reports and narrative prompts.
    Controllable risks marked ``mitigated == '1'`` are excluded from formal submission scope."""
    if not (value or "").strip() or is_unsure(value):
        return (value or "").strip()
    all_rows = parse(value)
    active = [r for r in all_rows if r.get("mitigated") != "1"]
    excluded_n = len(all_rows) - len(active)
    if all_rows and not active:
        return f"初评识别的 {excluded_n} 项可控风险均已落实技术与组织措施完成闭环核销，当前正式申报口径无未闭环高、中风险项。"
    lines = []
    for n, r in enumerate(active, 1):
        lv = level(r)
        line = (
            f"风险 {n}：{r['risk'] or blank}。可能性{r['likelihood'] or blank}，严重程度{r['severity'] or blank}"
            + (f"，风险等级为{lv}" if lv else "")
            + f"。应对措施：{r['measures'] or blank}"
        )
        who = _who(r)
        lines.append(line + (f"（{who}）" if who else "") + "。")
    if excluded_n > 0:
        lines.append(f"另注：初评识别的其余 {excluded_n} 项可控风险已完成整改核销并从正式报告风险清单中剔除（详见整改跟踪台账）。")
    return "\n".join(lines)


def shown(key: str, value: str | None, blank: str = "") -> str:
    """Any answer as text: the rows of a row-wise question become lines, other answers stay as they are."""
    return text(value, blank) if key in STRUCTURED else (value or "")


def _evolution_sentence(s: dict) -> str:
    ic = s["init_counts"]
    cc = s["current_counts"]
    parts = []
    if s["downgraded_count"]:
        parts.append(f"经整改降级 {s['downgraded_count']} 项")
    if s["excluded_count"]:
        parts.append(f"核销剔除已可控风险 {s['excluded_count']} 项")
    if s["upgraded_count"]:
        parts.append(f"上调风险等级 {s['upgraded_count']} 项")
    mid = "、".join(parts)
    return (
        f"风险整改前后变化说明：本项目初评阶段共定级高风险 {ic['高风险']} 项、中风险 {ic['中风险']} 项、低风险 {ic['低风险']} 项；"
        f"{mid}后，当前正式报告剩余高风险 {cc['高风险']} 项、中风险 {cc['中风险']} 项、低风险 {cc['低风险']} 项。"
    )


def _balance_brackets(s: str) -> str:
    for op, cl in (("（", "）"), ("(", ")"), ("“", "”"), ("【", "】")):
        diff = s.count(op) - s.count(cl)
        if diff > 0:
            s = s + (cl * diff)
    return s


def _measure_adequacy_note(measures: str, blank: str = "", risk_desc: str = "") -> str:
    m = (measures or "").strip().rstrip("。；; ")
    if not m or m == blank or is_unsure(m):
        return f"{blank or '待补充防控措施'}（补齐具体控制方案后核定其适当性）"
    lead_clause = re.split(r"[；;。]", m)[0].strip().rstrip("，, ")
    if len(lead_clause) > 68:
        cut = max(lead_clause.rfind("，", 0, 68), lead_clause.rfind("、", 0, 68))
        lead_clause = lead_clause[:cut] if cut >= 20 else lead_clause
    lead_clause = _balance_brackets(lead_clause)

    if re.search(r"批文|数字签名|事先授权|备案|安全评估|防火墙", m) and re.search(r"阻断|门禁|审批|核验|禁止", m):
        return f"属“刚性审批门禁”控制：{lead_clause}；将法定批准状态转化为系统放行硬约束，从技术上阻断人工误操作或脚本绕过前置程序触发传输。"
    if re.search(r"最小权限|访问控制|按工单|工单号|白名单工程师|实名授权|双因子|堡垒机|只读调阅|权限颗粒度", m):
        return f"属“最小权限与审计追责”控制：{lead_clause}；以此收敛境外人员可触达的数据半径，防止单点账号越权查询或批量导出。"
    if re.search(r"二次[^。；]{0,8}质检|(?:漏检|异常帧)[^。；]{0,8}丢弃", m):
        return f"属“前置质检淘汰”控制：{lead_clause}；在出境关口先行剔除脱敏置信度异常样本，阻断残余可识别画面流入跨境队列。"
    if re.search(r"电子围栏|自动停录|默认关闭|不采集|不出车", m):
        return f"属“源头采集阻断”控制：{lead_clause}；在车载采集源头直接截断非必要敏感信息的生成路径，消除后续外流前提。"
    m_mask_probe = re.sub(
        r"(?:严禁|禁止|不得|杜绝)[^；。，\n]{0,16}?(?:未|未经|不做)\s*(?:任何|有效|不可逆)?\s*(?:脱敏|去标识化?|匿名化?|模糊|遮蔽|擦除|假名化)[^；。，\n]*|"
        r"(?:未|未经|不做)\s*(?:任何|有效|不可逆)?\s*(?:脱敏|去标识化?|匿名化?|模糊|遮蔽|擦除|假名化)[^；。\n]{0,20}?(?:严禁|禁止|不得|不予|杜绝)[^；。\n]*",
        " ",
        m,
    )
    has_unmasked = bool(
        re.search(r"(?:未|未经|不做|未完成|无)\s*(?:任何|有效|不可逆)?\s*(?:擦除|脱敏|模糊|去标识化?|假名化|匿名化?|遮蔽)", m_mask_probe)
    )
    if not has_unmasked and re.search(r"擦除|脱敏|模糊|去标识|假名化|匿名|剥离绝对", m):
        return f"属“源端降敏减害”控制：{lead_clause}；先行剥离直接识别自然人的特征锚点，即便发生极端异常亦无法直接还原特定个体。"
    if re.search(r"加密|TLS|mTLS|证书|密钥|HSM|专线|隧道", m, re.I):
        return f"属“链路与存储机密性”控制：{lead_clause}；阻断跨网传输窃听、中间人篡改及未授权明文读取，显著压降外部攻击实现概率。"
    if re.search(r"删除|销毁|清理|清零|留存|期满|到期", m):
        return f"属“暴露窗口收敛”控制：{lead_clause}；防止历史数据在境外长期沉淀衍生次生用途或超范围调取隐患。"
    if re.search(r"合同|协议|再转移|连带|追偿|违约", m):
        return f"属“法律契约锁定”控制：{lead_clause}；以可执行违约追责条款封堵境外超目的使用与擅自向第三方再转移风险。"
    return f"针对“{risk_desc[:22] or '该项风险'}”触发环节落实定向控制（{lead_clause}），可实质降低风险发生概率与潜在损害范围。"


def _acceptability_label(lv: str, blank: str = "", *, initial: bool = False) -> str:
    if lv == "高风险":
        return "不可直接接受（须追加措施压降）" if initial else "不可直接放行（须限期压降至中、低风险或履行事前申报审批）"
    if lv == "中风险":
        return "需落实防控措施压降或约束" if initial else "有条件可接受（须确保既有防控持续有效并纳入周期复审）"
    if lv == "低风险":
        return "可接受（维持基础内控）" if initial else "可接受（维持常态化安全监测与审计留痕）"
    return blank or "待定级核定"


def blocks(value: str | None, blank: str = "") -> list[dict]:
    """The formal report's risk register, safeguard adequacy & residual-risk closure table, and 3 × 3 likelihood × severity matrix."""
    all_rows = parse(value)
    active = [r for r in all_rows if r.get("mitigated") != "1"]
    cells: dict[tuple[int, int], list[str]] = {}
    for n, r in enumerate(active, 1):
        if level(r):
            cells.setdefault((LEVELS.index(r["likelihood"]), LEVELS.index(r["severity"])), []).append(f"风险 {n}")
    grid = [
        [f"可能性{LEVELS[i]}"] + [
            LEVEL_NAME[MATRIX[i][j]] + ("：" + "、".join(cells[(i, j)]) if (i, j) in cells else "") for j in range(3)
        ]
        for i in (2, 1, 0)
    ]
    matrix = [
        {"type": "paragraph", "text": "风险等级判定与分布表（按发生可能性与影响严重程度综合判定；量化分值采用 1/3/5 分五级标尺，1–4 分为低风险、5–12 分为中风险、15–25 分为高风险）："},
        {"type": "table", "head": ["可能性＼严重程度", "严重程度：低", "严重程度：中", "严重程度：高"], "rows": grid,
         "widths": [16, 28, 28, 28]},
    ]
    if not all_rows:
        said = (value or "").strip() or blank
        return ([{"type": "paragraph", "text": "主要风险评估与应对清单：" + said}] if said else []) + matrix
    s = summary(value)
    tail = [{"type": "paragraph", "text": _evolution_sentence(s)}] if s["has_change"] else []
    if not active:
        closed_note = {
            "type": "paragraph",
            "text": "剩余风险可接受性结论：遵循“消除＞降低＞转移＞接受”风险处置优先级，初评识别的可控风险均已通过技术与组织措施消除或核销，当前正式评估口径无未化解高、中风险阻断项，剩余风险处于可接受水平，无需启动高剩余风险事前监管咨询程序。",
        }
        return [*tail, *matrix, closed_note]
    register = [
        [f"风险 {n}", r["risk"] or blank, r["likelihood"] or blank, r["severity"] or blank, level(r) or blank,
         r["measures"] or blank, _who(r) or "—"]
        for n, r in enumerate(active, 1)
    ]
    closure_rows = []
    res_scores: list[int] = []
    for n, r in enumerate(active, 1):
        lv = level(r)
        ilv = init_level(r)
        il = r.get("init_likelihood") or r.get("likelihood") or ""
        isv = r.get("init_severity") or r.get("severity") or ""
        iq = quant_triplet(il, isv)
        rq = quant_triplet(r.get("likelihood", ""), r.get("severity", ""))
        if rq is not None:
            res_scores.append(rq[2])
        init_score_txt = f"可能性{il}（{iq[0]}分）×严重程度{isv}（{iq[1]}分）＝{iq[2]}/25分" if iq else f"可能性{il or blank}／严重程度{isv or blank}"
        res_score_txt = f"可能性{r['likelihood']}（{rq[0]}分）×严重程度{r['severity']}（{rq[1]}分）＝{rq[2]}/25分" if rq else f"可能性{r['likelihood'] or blank}／严重程度{r['severity'] or blank}"
        init_cell = (
            f"{ilv}（{init_score_txt}；{_acceptability_label(ilv, blank, initial=True)}）"
            if ilv
            else (blank or "待定级")
        )
        res_cell = f"{lv}（{res_score_txt}）" if lv else (blank or "待定级")
        closure_rows.append([
            f"风险 {n}",
            init_cell,
            _measure_adequacy_note(r["measures"], blank, r["risk"]),
            res_cell,
            _acceptability_label(lv, blank, initial=False),
        ])
    cc = s["current_counts"]
    ungraded_n = sum(1 for r in active if not level(r))
    max_res = max(res_scores) if res_scores else 0
    if ungraded_n:
        verdict_para = (
            f"剩余风险可接受性结论：遵循“消除＞降低＞转移＞接受”风险处置优先级，当前共有 {ungraded_n} 项风险尚未完成可能性或严重程度定级；"
            "在补齐全部风险量化定级与对应防控措施核验前，剩余风险暂不能认定为可接受。"
        )
    elif cc["高风险"] > 0:
        verdict_para = (
            f"剩余风险可接受性结论：遵循“消除＞降低＞转移＞接受”风险处置优先级，当前正式评估范围内含高风险 {cc['高风险']} 项（最高剩余风险分值 {max_res}/25 分）、中风险 {cc['中风险']} 项、低风险 {cc['低风险']} 项。"
            "依据高风险不直接接受红线，现阶段不得带病放行；须限期落实追加技术与合同防控措施将全部高风险项压降至 12/25 分及以下（中、低风险区间），无法压降的须依法履行事前监管咨询或安全评估审批。"
        )
    else:
        verdict_para = (
            f"剩余风险可接受性结论：遵循“消除＞降低＞转移＞接受”风险处置优先级，经逐项核验技术防护、合同约束与组织审计措施的适当性与有效性，"
            f"当前正式评估范围内高风险为 0 项（剩余中风险 {cc['中风险']} 项、低风险 {cc['低风险']} 项，最高剩余分值 {max_res}/25 分），"
            "全部剩余风险均已压降至可接受区间，未触发因高剩余风险无法消减而须向主管监管机关提请事前监管咨询的情形。"
        )
    return [
        {"type": "paragraph", "text": "主要风险评估与应对清单："},
        {"type": "table", "head": ["编号", "风险事项", "可能性", "严重程度", "风险等级", "防控与应对措施", "责任人与时限"],
         "rows": register, "widths": [10, 19, 9, 11, 11, 21, 19]},
        *matrix,
        {"type": "paragraph", "text": "防控措施适当性与有效性论证及固有—剩余风险量化核定表（遵循“消除＞降低＞转移＞接受”处置优先级）："},
        {"type": "table", "head": ["风险项", "固有风险量化初评", "措施适当性与有效性论证", "剩余风险量化重估", "可接受性核定"],
         "rows": closure_rows, "widths": [11, 22, 30, 17, 20]},
        *tail,
        {"type": "paragraph", "text": verdict_para},
    ]


def evolution_blocks(value: str | None, blank: str = "") -> list[dict]:
    """Full before-vs-after risk evolution ledger for the internal remediation tracking report,
    covering both downgraded risks and closed controllable risks excluded from formal reports."""
    rows = parse(value)
    if not rows:
        return [{"type": "paragraph", "text": "当前尚未录入具体风险分级与前后整改记录。"}]
    s = summary(value)
    table_rows: list[list[str]] = []
    for n, r in enumerate(rows, 1):
        lv = level(r) or blank
        ilv = init_level(r) or lv
        if r.get("mitigated") == "1":
            now_state = "已可控闭环（已从正式报告剔除）"
        elif ilv and lv != blank and ilv != lv:
            verb = "降级" if _score(lv) < _score(ilv) else "上调"
            now_state = f"{lv}（由{ilv}{verb}）"
        else:
            now_state = lv
        who = _who(r, include_change=False) or "—"
        table_rows.append([f"风险 {n}", r["risk"] or blank, ilv, r["measures"] or blank, now_state, who])
    lead = _evolution_sentence(s) if s["has_change"] else f"本项目当前共登记 {len(rows)} 项跨境数据安全风险，逐项跟踪如下："
    return [
        {"type": "paragraph", "text": lead},
        {
            "type": "table",
            "head": ["编号", "风险事项", "初评等级", "防控与整改措施", "当前状态与剩余等级", "责任人与时限"],
            "rows": table_rows,
            "widths": [10, 22, 12, 24, 18, 14],
        },
    ]


def _compact_remed_text(s: str) -> str:
    return re.sub(r"[\s，,。；;：:！!？?、（）()【】\[\]“”\"'—\-]+", "", s or "")


def _already_synced_to_remediation(
    n: int, r: dict, existing_issues: str, existing_measures: str
) -> bool:
    if not existing_issues and not existing_measures:
        return False
    rc = _compact_remed_text(r.get("risk", ""))
    mc = _compact_remed_text(r.get("measures", ""))
    ic = _compact_remed_text(existing_issues)
    ec = _compact_remed_text(existing_measures)
    if rc and len(rc) >= 6:
        if rc in ic or (len(rc) >= 14 and rc[:14] in ic):
            return True
    if mc and len(mc) >= 10:
        if (mc in ec or (len(mc) >= 16 and mc[:16] in ec)) and (not ic or f"风险{n}" in ic or (rc and rc[:8] in ic)):
            return True
    return False


def remediation(
    value: str | None,
    done: list[str],
    existing_issues: str = "",
    existing_measures: str = "",
) -> tuple[list[str], list[str], list[str]]:
    """The remediation items of the complete risks not turned into items before: (their ids, the
    problem lines, the measure lines). A risk without its grades or measures is not an item yet.
    A controllable risk explicitly marked mitigated ('1') is already closed and skipped."""
    done_set = set(done or [])
    ids, issues, measures = [], [], []
    for n, r in enumerate(parse(value), 1):
        if r.get("mitigated") == "1" or r["id"] in done_set or not level(r) or not r["measures"]:
            continue
        if _already_synced_to_remediation(n, r, existing_issues, existing_measures):
            continue
        ids.append(r["id"])
        ilv = init_level(r)
        lv = level(r)
        grade_note = f"初评{ilv}→当前{lv}" if ilv and ilv != lv else lv
        issues.append(f"风险 {n}：{r['risk']}（{grade_note}，取自风险评估）")
        who = _who(r, include_change=False)
        measures.append(f"针对风险 {n}：{r['measures']}" + (f"（{who}）" if who else "（责任人与完成时限待定）"))
    return ids, issues, measures


def pending_count(
    value: str | None,
    done: list[str],
    existing_issues: str = "",
    existing_measures: str = "",
) -> int:
    return len(remediation(value, done, existing_issues, existing_measures)[0])
