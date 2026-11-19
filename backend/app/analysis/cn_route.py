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


def _yes(v: str) -> bool | None:
    v = (v or "").strip()
    if not v or v.startswith("暂不确定"):
        return None
    if re.match(r"^(否|不|没有|无|未|非)", v) or re.search(r"不(是|属于|含|涉及)|不包含", v[:12]):
        return False
    if re.match(r"^(是|有|含|涉及|属于)", v):
        return True
    return None


def persons(v: str) -> int | None:
    """The number of people a free-text answer gives (“已传 2 万人；三年内约 10 万人” → the larger)."""
    v = (v or "").replace(",", "").replace("，", " ")
    if re.search(r"(?:^|[\s：:，,（(=])-\s*\d", v):
        return None
    nums = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(亿|万|千|百)?", v):
        n = float(m[1]) * {"亿": 1e8, "万": 1e4, "千": 1e3, "百": 1e2}.get(m[2] or "", 1)
        nums.append(int(n))
    if not nums:
        if re.search(r"不足\s*10\s*万|少于\s*10\s*万|10\s*万以下", v):
            return 50_000
        return None
    return max(nums)


def judge(values: dict[str, str]) -> dict:
    cii = _yes(values.get("is_cii", ""))
    important = _yes(values.get("important_data", ""))
    sensitive_text = values.get("sensitive_pi", "")
    sensitive = _yes(sensitive_text)
    n = persons(values.get("data_volume", ""))  # 出境涉及的人数（不是企业处理的总规模）
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
