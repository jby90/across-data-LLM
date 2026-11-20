# ruff: noqa: E501
"""Which outbound route Chinese law prescribes for the project, from what the enterprise answered:
security assessment, standard contract (or certification), or exempt from both — so the page can
say, per document, whether it is needed and why (“因为你填了……”).

The thresholds are those of the 《促进和规范数据跨境流动规定》 (CAC, 2024-03-22), articles 5, 7, 8:
- security assessment: a critical-information-infrastructure operator providing personal
  information or important data abroad; important data; since 1 January of the year, personal
  information of 1,000,000 people or more, or sensitive personal information of 10,000 or more;
- standard contract or certification: personal information of 100,000 to under 1,000,000 people,
  or sensitive personal information of fewer than 10,000;
- exempt: personal information of fewer than 100,000 people (no sensitive personal information,
  no important data, not a CII operator).
On the security-assessment route the project page also counts the two legal periods (``validity``):
the self-assessment must be done within three months before filing (申报指南（第三版）附件 4 注（二）),
so filing is due by the completion date plus three months; the result is valid for three years from
the day it is issued (article 9 of the same provisions), and an extension may be applied for within
60 working days before it expires — said in words only, since working days depend on holiday
schedules that cannot be counted reliably.
Only answers the enterprise gave are used; an unanswered fact leaves the route open and says
which answer decides it."""

from __future__ import annotations

import re
from datetime import date

from app.core import dates

BASIS = "《促进和规范数据跨境流动规定》第五条、第七条、第八条"


_EXPLICIT_YES_HEAD_RE = re.compile(r"^是(?:$|[\s，,。；;：:（(])")
_TARGET_PATTERNS = {
    "is_cii": r"关键信息基础设施(?:运营者)?|关基",
    "important_data": r"重要数据",
    "sensitive_pi": r"敏感个人(?:信息|数据)",
    "": r"重要数据|敏感个人(?:信息|数据)|关键信息基础设施(?:运营者)?|关基",
}
_ADVERSATIVE_SPLIT_RE = re.compile(r"[，,；;]\s*(?:但|不过|惟|然而)是?")
_HEAD_NEG_RE = re.compile(
    r"^(?:否|没有|无|不含|不涉及|不包含|不属于|不是|未收到|未被|未经|非)|"
    r"^(?:不|未)(?:$|[\s，,。；;：:（(])"
)
_HEAD_POS_RE = re.compile(
    r"^(?:是|有|含|包含|涉及|属于)|(?:^|今年|现|目前|已)(?:已收到.*认定|已被认定|经认定)"
)
_UNIT_MUL = {"亿": 1e8, "万": 1e4, "千": 1e3, "百": 1e2}
_CAL_DATE_RE = re.compile(
    r"(?:19|20)\d{2}\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号]?)?)?|\d{1,2}\s*月\s*\d{1,2}\s*[日号]"
)
_NON_PERSON_UNIT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:亿|万|千|百)?\s*(?:个)?(?:"
    r"年|月|周|天|日|小时|分钟|秒|工作日|岁|%|％|"
    r"辆|台|部|套|次|条|项|类|种|个字段|字段|帧|张|段|家|个国家|路|份|"
    r"公里|千米|km\b|GB\b|TB\b|MB\b|PB\b|KB\b|字节|元|万元|亿元"
    r")",
    re.IGNORECASE,
)
_PERSON_NOUN_OPT = r"(?:个|名|位)?(?:自然人|个人|车主|用户|驾驶员|乘客|员工|客户|消费者|主体|人)?"
_CAP_PAREN_REFINE_RE = re.compile(
    rf"(?:(?:不满|不足|少于|低于|小于|未满|未达)\s*(\d+(?:\.\d+)?)\s*(亿|万|千|百)?\s*{_PERSON_NOUN_OPT}"
    rf"|(\d+(?:\.\d+)?)\s*(亿|万|千|百)?\s*{_PERSON_NOUN_OPT}\s*(?:以下|以内))"
    r"\s*[（(]([^）)]+)[）)]"
)
_CAP_COMMA_REFINE_RE = re.compile(
    rf"(?:(?:不满|不足|少于|低于|小于|未满|未达)\s*(\d+(?:\.\d+)?)\s*(亿|万|千|百)?\s*{_PERSON_NOUN_OPT}"
    rf"|(\d+(?:\.\d+)?)\s*(亿|万|千|百)?\s*{_PERSON_NOUN_OPT}\s*(?:以下|以内))"
    r"\s*[，,]\s*(?!(?:未来|今后|三年|3\s*年|累计))(?:预计|约|大概|实际|具体|目前|其中|即)\s*(?:约\s*|为\s*)?"
    rf"(\d+(?:\.\d+)?\s*(?:亿|万|千|百)?\s*{_PERSON_NOUN_OPT})"
)
_EXCEPT_POS_RE = re.compile(
    r"除(?:了)?([^，,。；;\n]{1,80}?)(?:以?外|之外)[，,\s]*(?:其余|其他|其他数据|均|皆|都)?\s*(?:不|无|没有|未|非)"
)
_ONLY_HEAD_RE = re.compile(r"^(?:仅|只)(?:含|有|涉及|包含|传输|处理|提供|限于|为|属于)\s*(.+)$")
_SENSITIVE_SUBTYPE_RE = re.compile(
    r"敏感个人(?:信息|数据)|生物识别|人脸|面部|指纹|声纹|虹膜|步态|基因|医疗|健康|生理|心率|"
    r"行踪|行车轨迹|精准定位|精确位置|定位轨迹|身份证|护照|驾驶证|银行卡|金融账户|征信|信贷|财产|"
    r"宗教|特定身份|不满十四周岁|未成年人|儿童"
)
_IMPORTANT_SUBTYPE_RE = re.compile(
    r"重要数据|测绘|高精度地图|地理信息|军事|国防|政务|充电网运行|车外视频|车外图像|交通流量|敏感区域"
)
_NON_TARGET_DISCLAIMER_RE = re.compile(
    r"一般|普通|常规|公开|匿名|脱敏后(?:的)?一般|非敏感|非重要|基础工况|纯机械|纯工况"
)
_POSITIVE_PI_TOKEN_RE = re.compile(
    r"个人(?:信息|数据)|车主|用户|驾驶员|乘客|员工|客户|自然人|手机号|电话|邮箱|姓名|账号|身份证|轨迹|车牌|VIN\b",
    re.IGNORECASE,
)
_EXPLICIT_NO_PI_RE = re.compile(
    r"不(?:涉及|含|包含|处理|传输)(?:任何)?个人(?:数据|信息)|无个人(?:数据|信息)|没有个人(?:数据|信息)|"
    r"^\s*(?:0|零)\s*(?:人|个|名|位)?\s*$|^\s*(?:无|不涉及)\s*$"
)


def _eval_non_adversative(s: str, target: str) -> bool | None:
    s = (s or "").strip()
    if not s or s.startswith("暂不确定"):
        return None
    if _EXPLICIT_YES_HEAD_RE.match(s):
        return True
    tpat = _TARGET_PATTERNS.get(target, _TARGET_PATTERNS[""])
    m_exc = _EXCEPT_POS_RE.search(s)
    if m_exc:
        exc = m_exc.group(1).strip()
        if target == "is_cii":
            if re.search(tpat, exc) and not re.search(r"^(?:非|不)", exc):
                return True
        else:
            has_subtype = (
                bool(re.search(tpat, exc))
                or (target in ("", "sensitive_pi") and bool(_SENSITIVE_SUBTYPE_RE.search(exc)))
                or (target in ("", "important_data") and bool(_IMPORTANT_SUBTYPE_RE.search(exc)))
            )
            if has_subtype or not _NON_TARGET_DISCLAIMER_RE.search(exc):
                return True
    cii_neg = (
        r"未收到[^，。；;\n]{0,14}认定|未被认定|未经认定|非关键信息基础设施|不属于关键信息基础设施|"
        if target in ("", "is_cii")
        else ""
    )
    whole_neg_re = re.compile(
        cii_neg
        + rf"(?:不|非|无|未|没有)(?:属于|是|含|包含|涉及|构成|存在)?\s*(?:任何|相关)?(?:{tpat})"
        + rf"|(?:{tpat})\s*[：:，,]?\s*(?:不涉及|不含|不包含|不属于|无|没有|否)(?:$|[，,。；;\s])"
    )
    if whole_neg_re.search(s):
        return False
    clauses = [c.strip() for c in re.split(r"[，,；;]+", s) if c.strip()]
    has_pos = False
    has_neg = False
    for c in clauses:
        m_only = _ONLY_HEAD_RE.match(c)
        if m_only:
            body_c = m_only.group(1).strip()
            if re.search(r"^(?:非|不)", body_c):
                has_neg = True
            elif re.search(tpat, body_c):
                has_pos = True
            elif (
                target in ("", "sensitive_pi")
                and _SENSITIVE_SUBTYPE_RE.search(body_c)
                and not _NON_TARGET_DISCLAIMER_RE.search(body_c)
            ):
                has_pos = True
            elif (
                target in ("", "important_data")
                and _IMPORTANT_SUBTYPE_RE.search(body_c)
                and not _NON_TARGET_DISCLAIMER_RE.search(body_c)
            ):
                has_pos = True
            elif _NON_TARGET_DISCLAIMER_RE.search(body_c):
                continue
            elif target in ("sensitive_pi", "important_data") and has_neg:
                has_pos = True
            continue
        if _HEAD_NEG_RE.match(c):
            has_neg = True
        elif _HEAD_POS_RE.search(c):
            has_pos = True
    if has_pos:
        return True
    if has_neg:
        return False
    return None


def _yes(v: str, target: str = "") -> bool | None:
    v = (v or "").strip()
    if not v or v.startswith("暂不确定"):
        return None
    if _ADVERSATIVE_SPLIT_RE.search(v):
        parts = _ADVERSATIVE_SPLIT_RE.split(v)
        tail_res = _eval_non_adversative(parts[-1], target)
        if tail_res is not None:
            return tail_res
        return _eval_non_adversative(parts[0], target)
    return _eval_non_adversative(v, target)


def has_no_personal_data(volume: str, sensitive_pi: str = "", data_types: str = "") -> bool:
    """True ONLY when the enterprise explicitly states no personal data / 0 persons AND no clause
    affirms sensitive PI, positive person counts, or personal-data subtypes."""
    if _yes(sensitive_pi, "sensitive_pi") is True:
        return False
    vol = (volume or "").strip()
    dtypes = (data_types or "").strip()
    if not vol and not dtypes:
        return False
    if vol:
        n = persons(vol)
        if n is not None and n > 0:
            return False
        if _ADVERSATIVE_SPLIT_RE.search(vol):
            tail = _ADVERSATIVE_SPLIT_RE.split(vol)[-1].strip()
            if _POSITIVE_PI_TOKEN_RE.search(tail) and not _EXPLICIT_NO_PI_RE.search(tail):
                return False
    if dtypes:
        for seg in re.split(r"[；;\n]+", dtypes):
            seg_clean = re.sub(r"[（(][^）)]*(?:不含|不涉及|无)个人(?:信息|数据)[^）)]*[）)]", "", seg).strip()
            if not seg_clean or _EXPLICIT_NO_PI_RE.search(seg_clean):
                continue
            if _ADVERSATIVE_SPLIT_RE.search(seg_clean):
                seg_clean = _ADVERSATIVE_SPLIT_RE.split(seg_clean)[-1].strip()
            if _POSITIVE_PI_TOKEN_RE.search(seg_clean) and not _HEAD_NEG_RE.match(seg_clean):
                return False
    if vol:
        return bool(_EXPLICIT_NO_PI_RE.search(vol))
    return bool(_EXPLICIT_NO_PI_RE.search(dtypes))


def _scan_person_numbers(s: str) -> list[int]:
    vals: list[int] = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(亿|万|千|百)?", s):
        n = int(float(m[1]) * _UNIT_MUL.get(m[2] or "", 1))
        before = s[max(0, m.start() - 10) : m.start()]
        after = s[m.end() : m.end() + 16]
        is_strict_cap = bool(
            re.search(r"(?:不满|不足|少于|低于|小于|未满|未达)\s*$", before)
            or re.match(rf"^\s*{_PERSON_NOUN_OPT}\s*(?:以下|以内)(?!\s*以上)", after)
        )
        vals.append(max(0, n - 1) if is_strict_cap else n)
    return vals


def persons(v: str) -> int | None:
    """The number of people a free-text answer gives (“已传 2 万人；三年内约 10 万人” → the larger).
    Upper-bound disclaimers like “不满 100 万人” / “不足 1 万人” are treated as strictly below the cap,
    while parenthetical or comma-appositive estimates refining a cap (“不满 100 万人（预计约 15 万人）”,
    “当年累计不满 100 万人，预计约 5 万人”) use that estimate."""
    raw = str(v or "")
    if re.search(r"(?:^|[\s：:，,（(=])-\s*\d", raw):
        return None
    cleaned = _NON_PERSON_UNIT_RE.sub(" ", _CAL_DATE_RE.sub(" ", raw.replace(",", "")))

    def _refine_cap(m: re.Match) -> str:
        num_s = m.group(1) or m.group(3)
        unit_s = m.group(2) or m.group(4) or ""
        cap_val = max(0, int(float(num_s) * _UNIT_MUL.get(unit_s, 1)) - 1)
        inner = m.group(5)
        if not re.search(r"不满|不足|少于|低于|小于|未满|未达|以下|以内", inner):
            inner_vals = _scan_person_numbers(inner.replace("，", " "))
            if inner_vals and max(inner_vals) <= cap_val:
                return f" {max(inner_vals)}人 "
        return m.group(0)

    cleaned = _CAP_PAREN_REFINE_RE.sub(_refine_cap, cleaned)
    cleaned = _CAP_COMMA_REFINE_RE.sub(_refine_cap, cleaned).replace("，", " ")
    nums = _scan_person_numbers(cleaned)
    if nums:
        return max(nums)
    if re.search(r"(?:不满|不足|少于)\s*百\s*万|百\s*万以下", cleaned):
        return 999_999
    if re.search(r"(?:不满|不足|少于)\s*十\s*万|十\s*万以下", cleaned):
        return 50_000
    if re.search(r"(?:不满|不足|少于)\s*一\s*万|一\s*万以下", cleaned):
        return 5_000
    raw_s = raw.strip()
    if raw_s and _EXPLICIT_NO_PI_RE.search(raw_s):
        if _ADVERSATIVE_SPLIT_RE.search(raw_s):
            tail = _ADVERSATIVE_SPLIT_RE.split(raw_s)[-1].strip()
            if _POSITIVE_PI_TOKEN_RE.search(tail) and not _EXPLICIT_NO_PI_RE.search(tail):
                return None
        return 0
    return None


def judge(values: dict[str, str]) -> dict:
    cii = _yes(values.get("is_cii", ""), "is_cii")
    important = _yes(values.get("important_data", ""), "important_data")
    sensitive_text = values.get("sensitive_pi", "")
    sensitive = _yes(sensitive_text, "sensitive_pi")
    n = persons(values.get("data_volume", ""))  # 出境涉及的人数（不是企业处理的总规模）
    no_pi_explicit = has_no_personal_data(
        values.get("data_volume", ""), sensitive_text, values.get("data_types", "")
    )
    if n is None and no_pi_explicit:
        n = 0
    n_sensitive = persons(sensitive_text) if sensitive else 0
    if sensitive and not n_sensitive and n is not None and n < 10_000:
        n_sensitive = n  # 出境总人数不足 1 万，敏感个人信息涉及的人数必然也不足 1 万：不再追问
    because: list[str] = []
    if cii:
        because.append("是关键信息基础设施运营者")
    if important:
        because.append("含重要数据")
    if n is not None and n >= 1_000_000:
        because.append(f"涉及约 {n:,} 人（100 万人以上）")
    if sensitive and n_sensitive and n_sensitive >= 10_000:
        because.append(f"敏感个人信息约 {n_sensitive:,} 人（1 万人以上）")
    if because:
        return _out("security_assessment", "须申报数据出境安全评估", because, [])
    asks = [(key, label) for key, label, val in (("is_cii", "是否关键信息基础设施运营者", cii), ("important_data", "是否含重要数据", important),
                                                  ("sensitive_pi", "是否含敏感个人信息", sensitive)) if val is None]
    if n is None:
        asks.append(("data_volume", "涉及自然人数量"))
    if sensitive and not n_sensitive:
        asks.append(("sensitive_pi", "敏感个人信息涉及的人数"))  # 在“是否含敏感个人信息”一题选“是”后写明
    if asks:
        return _out("unknown", "还不能判断走哪条出境路径", [], [label for _, label in asks], [key for key, _ in asks])
    if (n or 0) >= 100_000 or sensitive:
        why = [f"涉及约 {n:,} 人（10 万至 100 万人）"] if (n or 0) >= 100_000 else []
        if sensitive:
            why.append("含敏感个人信息（不足 1 万人）")
        return _out("standard_contract", "订立标准合同并备案（或通过个人信息保护认证）", why + ["非关键信息基础设施运营者、不含重要数据"], [])
    if (n == 0 or no_pi_explicit) and not sensitive:
        out = _out(
            "exempt",
            "免于申报安全评估、订立标准合同和认证",
            ["不向境外提供个人信息（涉及自然人 0 人）", "不含敏感个人信息、重要数据"],
            [],
        )
        out["docs"]["risk_report"] = {
            "applies": False,
            "why": "按规定免于申报安全评估，且不向境外提供个人信息，风险自评估报告与个人信息保护影响评估报告均不用准备",
            "basis": _CP + "第五条、第七条",
        }
        out["docs"]["dpia"] = {
            "applies": False,
            "why": "不向境外提供个人信息，不用进行个人信息保护影响评估",
            "basis": _CP + "第十条",
        }
        return out
    return _out("exempt", "免于申报安全评估、订立标准合同和认证", [f"涉及约 {n:,} 人（不足 10 万人）", "不含敏感个人信息、重要数据"], [])


# 数据从中国出境时各份文件按出境路径是否需要、为什么、依据哪一条（法规库收录的四部规定）。
# applies 为 None：路径还没定，这份文件还不能判断。不在表里的文件由 doc_applicability 按通用规则判断。
_SA, _SC, _CP = "《数据出境安全评估办法》", "《个人信息出境标准合同办法》", "《促进和规范数据跨境流动规定》"
DOCS: dict[str, dict[str, tuple[bool | None, str, str]]] = {
    # 不需要的文件，原因写成企业看得懂的一句话：按规定走哪条路，所以哪份不用准备、改看哪份
    "declaration": {
        "security_assessment": (True, "按规定须申报数据出境安全评估，申报表随申报递交", _CP + "第七条"),
        "standard_contract": (False, "按规定走标准合同，不需要申报安全评估，这份申报表不用准备", _CP + "第八条"),
        "exempt": (False, "按规定免于申报安全评估和订立标准合同，这份申报表不用准备", _CP + "第五条"),
        "unknown": (None, "出境路径定了才知道要不要申报安全评估", _CP + "第七条"),
    },
    "contract": {
        "security_assessment": (True, "申报安全评估时须附与境外接收方拟订立的法律文件", _SA + "第六条、第九条"),
        "standard_contract": (True, "须按标准合同订立，并在生效后 10 个工作日内向省级网信部门备案", _SC + "第七条"),
        "exempt": (True, "免于备案；仍须与接收方约定数据保护责任，以合同写明", ""),
        "unknown": (None, "出境路径定了才知道是附安全评估的法律文件，还是订立标准合同", _CP + "第七条、第八条"),
    },
    "risk_report": {
        "security_assessment": (True, "申报安全评估前须开展数据出境风险自评估，报告随申报递交", _SA + "第五条、第六条"),
        "standard_contract": (False, "按规定走标准合同，不需要申报安全评估，风险自评估报告不用准备；要做的个人信息保护影响评估见“个人信息保护影响评估报告”", _SC + "第五条"),
        "exempt": (False, "按规定免于申报安全评估，风险自评估报告不用准备；向境外提供个人信息仍要做个人信息保护影响评估，见“个人信息保护影响评估报告”", _CP + "第十条"),
        "unknown": (None, "出境路径定了才知道要不要做数据出境风险自评估", _CP + "第七条"),
    },
    "dpia": {
        "security_assessment": (False, "按规定须申报安全评估，个人信息保护影响评估并入数据出境风险自评估报告，这份不用单独准备", _SA + "第五条"),
        "standard_contract": (True, "按规定走标准合同，备案时须附个人信息保护影响评估报告", _SC + "第五条、第七条"),
        "exempt": (True, "向境外提供个人信息须进行个人信息保护影响评估", _CP + "第十条"),
        "unknown": (None, "出境路径定了才知道个人信息保护影响评估是单独成报告，还是并入风险自评估报告", _CP + "第十条"),
    },
    "tia": {
        "security_assessment": (True, "安全评估要评估境外接收方所在国家或地区的数据保护政策法规和网络环境", _SA + "第八条"),
        "standard_contract": (True, "影响评估要评估境外接收方所在国家或地区的个人信息保护政策和法规", _SC + "第五条"),
    },
}


def _out(route: str, text: str, because: list[str], missing: list[str], missing_keys: list[str] | None = None) -> dict:
    return {
        "route": route, "text": text, "because": because, "missing": missing, "missing_keys": missing_keys or [], "basis": BASIS,
        "docs": {code: {"applies": m[route][0], "why": m[route][1], "basis": m[route][2]} for code, m in DOCS.items() if route in m},
        "validity": None,  # 走安全评估时由 validity() 填上两个期限
    }


ROUTE_DOCS = ("declaration", "risk_report", "contract", "dpia")
_TRIGGERS = [
    ["申报数据出境安全评估", "关键信息基础设施运营者向境外提供个人信息或重要数据；含重要数据；当年累计向境外提供 100 万人以上个人信息，或 1 万人以上敏感个人信息",
     "数据出境安全评估申报表、数据出境风险自评估报告、与境外接收方拟订立的法律文件", _CP + "第七条"],
    ["订立个人信息出境标准合同并备案（或个人信息保护认证）", "非关键信息基础设施运营者、不含重要数据，当年累计向境外提供 10 万人以上、不满 100 万人个人信息，或不满 1 万人敏感个人信息",
     "个人信息出境标准合同、个人信息保护影响评估报告", _CP + "第八条"],
    ["免于申报和订立标准合同", "当年累计向境外提供不满 10 万人个人信息，且不含敏感个人信息和重要数据", "按规定履行告知、同意和安全保护义务", _CP + "第五条"],
]


def pending_note(route: dict, title: str = "") -> list[dict]:
    """The page put in a package while the route is open: what decides it and what each route needs,
    instead of two sets of filing documents."""
    blocks = [{"type": "title", "text": "出境路径判断待补"}]
    if title:
        blocks.append({"type": "paragraph", "text": f"项目：{title}"})
    blocks.append({"type": "paragraph", "text": f"数据从中国发出，走哪条出境路径按{BASIS}判断。还要写明：{'、'.join(route.get('missing') or [])}。写明后只生成所走路径的文件。"})
    blocks.append({"type": "table", "head": ["出境路径", "适用情形", "要准备的文件", "依据"], "rows": [list(r) for r in _TRIGGERS]})
    return blocks


# 两个期限的依据（项目页上逐条列出；规定第九条链到法规知识库）
GUIDE = "《数据出境安全评估申报指南（第三版）》"
FILING_BASIS = (GUIDE, "附件 4 注（二）", "报告所述自评估活动为本次申报前 3 个月内完成")
RESULT_BASIS = (_CP, "第九条", "通过数据出境安全评估的结果有效期为 3 年，自评估结果出具之日起计算")
EXTEND_BASIS = (GUIDE, "三、（一）", "可以在评估结果有效期届满前 60 个工作日内申请延长评估结果有效期")
EXTEND_TEXT = "有效期届满前 60 个工作日内可申请延长"


def _period(until: date, today: date) -> dict:
    days = dates.days_left(until, today)
    return {"date": until.isoformat(), "days_left": days, "state": dates.expiry_state(days)}


def validity(assessed_on: str, issued_on: str, today: date | None = None) -> dict:
    """The two periods of the security-assessment route, from the dates the enterprise gave:
    filing due by self-assessment completion + 3 months; result valid until issue date + 3 years.
    A date not given leaves its period open (the page asks for it); once the result is issued the
    filing deadline no longer runs."""
    today = today or date.today()
    assessed, issued = dates.parse(assessed_on), dates.parse(issued_on)
    filing = None
    if assessed and not issued:
        filing = _period(dates.add_months(assessed, 3), today)
    result = _period(dates.add_months(issued, 36), today) if issued else None
    return {
        "assessed_on": assessed.isoformat() if assessed else "",
        "issued_on": issued.isoformat() if issued else "",
        "filing": filing,  # 申报截止日（拿到评估结果后不再计）
        "result": result,  # 评估结果到期日
        "extend": EXTEND_TEXT,
        "basis": [{"law": law, "article": art, "point": point} for law, art, point in (FILING_BASIS, RESULT_BASIS, EXTEND_BASIS)],
    }
