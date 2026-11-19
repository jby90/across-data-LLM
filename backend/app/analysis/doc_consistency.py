# ruff: noqa: E501
"""合规评估项目的平台检查：同一项目里不同的题前后对不上、项目和官方表格对不上。

只是提示，挂在对应题目旁：不改变文件状态，不算进“缺 N 项”，不挡下载成稿。
数字、期限、名称是从文字里读的：两边都能明确读出来才比，读不准的不报（宁可漏报，不误报）。不调模型。

分档沿用准入材料检查（``app.admission.plan.findings``）的“必须 / 建议”：
- 必须：与官方表格不一致、接收方所在国与项目流向不一致（比的是明确的值）；
- 建议：人数、保存期限、接收方名称这类从文字里读出来再比的。
"""

from __future__ import annotations

import re

from app.admission.plan import _same
from app.analysis.cn_route import _yes, persons
from app.analysis.document_basis import NAMES, country_of

MUST, ADVISE = "must", "advise"

# ---------- 人数 ----------
_YEAR = re.compile(r"(?:19|20)\d{2}\s*年(?:度)?")
# 不是人数的数字：期限、比例、车辆、数据量、距离等
_NOT_PEOPLE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:亿|万|千|百)?\s*(?:个)?(?:人次|年|月|周|天|日|小时|分钟|秒|岁|%|％|辆|台|部|套|次|条|项|类|种|个字段|字段|帧|张|段|家|个国家|公里|千米|km|GB|TB|MB|PB|G|T)",
    re.I,
)
_NUM = re.compile(r"\d+(?:\.\d+)?\s*(?:亿|万|千|百)?")
_PEOPLE_WORD = re.compile(r"人|用户|车主|名|位|自然人")
_UPPER = re.compile(r"以下|不足|少于|不超过|以内|低于|不到")
_LOWER = re.compile(r"以上|超过|多于|逾|不少于|至少|高于")


def _counts(text: str) -> list[int]:
    """Every number of people a free-text answer gives (years, periods, vehicles and the like left
    out); empty when it is not about people."""
    t = str(text or "").replace(",", "").replace("，", " ")
    if not t.strip() or t.strip().startswith("暂不确定") or not _PEOPLE_WORD.search(t):
        return []
    t = _NOT_PEOPLE.sub(" ", _YEAR.sub(" ", t))
    return [n for n in (persons(m.group(0)) for m in _NUM.finditer(t)) if n is not None]


def _count(text: str) -> int | None:
    """The one number of people a free-text answer gives; None when it gives none or several
    different ones (“车主 5 万人、乘客 10 万人”)."""
    values = set(_counts(text))
    return values.pop() if len(values) == 1 else None


def _people(n: int) -> str:
    if n >= 10_000 and n % 1_000 == 0:
        return f"约 {n / 10_000:g} 万人"
    return f"约 {n:,} 人"


def _more_than(a_text: str, b_text: str) -> tuple[int, int] | None:
    """(a, b) when a's number of people is surely more than b's. With several numbers in one
    answer (“当年 2 万人，未来三年约 10 万人”、“车主 5 万、乘客 3 万”) a counts at its smallest and b at
    all of them added up, so a reading that could be right is never reported. a not given as an
    upper bound (“10 万以下”), b not given as a lower bound (“100 万以上”)."""
    a, b = _counts(a_text), _counts(b_text)
    if not a or not b or _UPPER.search(a_text) or _LOWER.search(b_text):
        return None
    low, high = min(a), sum(dict.fromkeys(b))
    return (low, high) if low > high else None


# ---------- 保存期限 ----------
_CN_DIGIT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_KEEP = re.compile(
    r"(?:保存|保留|存储|留存|存放)(?:期限|期|时间|时长)?(?:为|是|不超过|最长|至多|：|:)?\s*"
    r"(\d+(?:\.\d+)?|[一二两三四五六七八九十]{1,3})\s*(个月|月|年|周|天|日)"
)
_DAYS = {"年": 365, "个月": 30, "月": 30, "周": 7, "天": 1, "日": 1}


def _cn_number(s: str) -> float | None:
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s)
    if s == "十":
        return 10
    if "十" in s:
        head, _, tail = s.partition("十")
        tens = _CN_DIGIT.get(head, 1) if head else 1
        return tens * 10 + (_CN_DIGIT.get(tail, 0) if tail else 0)
    return _CN_DIGIT.get(s)


def _period(text: str) -> tuple[int, str] | None:
    """The one retention period a free-text answer names, (days, as written); None when it names
    none or several different ones (“原始视频 30 天、脱敏数据 3 年”)."""
    found: dict[int, str] = {}
    for m in _KEEP.finditer(str(text or "")):
        n = _cn_number(m.group(1))
        if n:
            found.setdefault(round(n * _DAYS[m.group(2)]), f"{m.group(1)} {m.group(2)}".replace("  ", " "))
    return next(iter(found.items())) if len(found) == 1 else None


# ---------- 接收方名称 ----------
_ORG_ZH = re.compile(r"[一-龥A-Za-z0-9（）()·&]{2,40}?(?:股份有限公司|有限责任公司|有限公司|公司)")
_ORG_LATIN = re.compile(
    r"\b[A-Z][A-Za-z0-9&'.\- ]{1,50}?\s(?:Co\.?,?\s*Ltd\.?|Pte\.?\s*Ltd\.?|Ltd\.?|LLC|Inc\.?|GmbH|S\.?A\.?R\.?L\.?|SARL|S\.A\.|SAS|SA|PLC|Limited)\b"
)
_LEAD = re.compile(r"^(?:与|和|同|向|由|将|对|在|为|是|系|即|经|给|把|及|并|其|位于|设在)+")
_GENERIC = re.compile(r"该|本|此|其|上述|对方|接收方|境外|母公司$|子公司$|分公司$|关联公司$|合资公司$|^公司$|贵|我")
_COUNTRY_WORDS = sorted(NAMES.values(), key=len, reverse=True)


def _orgs(text: str) -> set[str]:
    out: set[str] = set()
    t = str(text or "")
    for m in _ORG_ZH.finditer(t):
        name = _LEAD.sub("", m.group(0))
        core = re.sub(r"(股份有限公司|有限责任公司|有限公司|公司)$", "", name)
        for c in _COUNTRY_WORDS:  # “位于埃及的开罗车联网有限公司”里前面的国家名、地名不算名称的一部分时也能对上
            core = core.removeprefix(c)
        if len(core) >= 3 and not _GENERIC.search(name):
            out.add(name)
    out |= {m.group(0).strip() for m in _ORG_LATIN.finditer(t)}
    return out


def _one_org(text: str) -> str | None:
    names = list(_orgs(text))
    if len(names) == 1:
        return names[0]
    if names and all(_same(names[0], n) for n in names[1:]):
        return max(names, key=len)
    return None


def _country_named(text: str) -> str | None:
    v = str(text or "").strip()
    if not v or v.startswith("暂不确定"):
        return None
    return country_of(v)


# ---------- 题对题 ----------
def _issue(rule: str, key: str, text: str, level: str, to: dict) -> dict:
    return {"id": f"{rule}:{key}", "rule": rule, "key": key, "text": text, "level": level, "to": to}


def _q(key: str) -> dict:
    return {"kind": "question", "key": key}


def between_questions(values: dict[str, str], labels: dict[str, str], recipient_country: str) -> list[dict]:
    """Contradictions between two different questions of the project (both sides must read clearly)."""
    out: list[dict] = []
    v = {k: str(x or "") for k, x in values.items()}

    def lab(k: str) -> str:
        return labels.get(k, k)

    def pair(rule: str, a: str, b: str, text_a: str, text_b: str, level: str = ADVISE) -> None:
        out.append({**_issue(rule, a, text_a, level, _q(b)), "pair": rule})
        out.append({**_issue(rule, b, text_b, level, _q(a)), "pair": rule})

    # 出境人数多于企业处理的总规模
    if "data_volume" in v and "pi_scale" in v:
        hit = _more_than(v["data_volume"], v["pi_scale"])
        if hit:
            pair("volume_scale", "data_volume", "pi_scale",
                 f"出境人数{_people(hit[0])}，多于“{lab('pi_scale')}”的{_people(hit[1])}",
                 f"处理规模{_people(hit[1])}，少于“{lab('data_volume')}”里出境的{_people(hit[0])}")
    # 敏感个人信息的人数多于出境人数
    if "sensitive_pi" in v and "data_volume" in v and _yes(v["sensitive_pi"]):
        hit = _more_than(v["sensitive_pi"], v["data_volume"])
        if hit:
            pair("sensitive_volume", "sensitive_pi", "data_volume",
                 f"敏感个人信息{_people(hit[0])}，多于“{lab('data_volume')}”的{_people(hit[1])}",
                 f"出境人数{_people(hit[1])}，少于“{lab('sensitive_pi')}”里敏感个人信息的{_people(hit[0])}")
    # 出境人数多于处理涉及的人数
    if "data_volume" in v and "data_subjects" in v:
        hit = _more_than(v["data_volume"], v["data_subjects"])
        if hit:
            pair("volume_subjects", "data_volume", "data_subjects",
                 f"出境人数{_people(hit[0])}，多于“{lab('data_subjects')}”的{_people(hit[1])}",
                 f"涉及{_people(hit[1])}，少于“{lab('data_volume')}”里出境的{_people(hit[0])}")
    # 保存期限两处写得不一样
    if "retention_and_blackbox" in v and "legal_doc_terms" in v:
        a, b = _period(v["retention_and_blackbox"]), _period(v["legal_doc_terms"])
        if a and b and a[0] != b[0]:
            pair("retention", "retention_and_blackbox", "legal_doc_terms",
                 f"保存期限“{a[1]}”，与“{lab('legal_doc_terms')}”里的“{b[1]}”不一致",
                 f"约定的保存期限“{b[1]}”，与“{lab('retention_and_blackbox')}”里的“{a[1]}”不一致")
    # 接收方所在国与项目流向的接收国
    route_c = country_of(recipient_country)
    flagged: set[str] = set()
    for k in ("recipient", "cross_border_mechanism"):
        c = _country_named(v.get(k, "")) if k in v else None
        if route_c and c and c != route_c:
            flagged.add(k)
            out.append(_issue("recipient_country", k, f"写的接收国是“{NAMES.get(c, c)}”，项目流向的接收国是“{NAMES.get(route_c, route_c)}”",
                              MUST, {"kind": "route"}))
    if "recipient" in v and "cross_border_mechanism" in v:
        # 两题写的接收国不一样（流向没定接收国、或两题都没和流向冲突时才单独报）
        ca, cb = _country_named(v["recipient"]), _country_named(v["cross_border_mechanism"])
        if ca and cb and ca != cb and not flagged:
            pair("recipient_pair_country", "recipient", "cross_border_mechanism",
                 f"接收方所在国“{NAMES.get(ca, ca)}”，与“{lab('cross_border_mechanism')}”里的“{NAMES.get(cb, cb)}”不一致",
                 f"写的接收国“{NAMES.get(cb, cb)}”，与“{lab('recipient')}”里的“{NAMES.get(ca, ca)}”不一致", MUST)
        na, nb = _one_org(v["recipient"]), _one_org(v["cross_border_mechanism"])
        if na and nb and not _same(na, nb):
            pair("recipient_name", "recipient", "cross_border_mechanism",
                 f"接收方“{na}”，与“{lab('cross_border_mechanism')}”里的“{nb}”不一致",
                 f"写的接收方“{nb}”，与“{lab('recipient')}”里的“{na}”不一致")
    return out


# ---------- 与官方表格 ----------
_CJK = re.compile(r"[一-龥]")


def _comparable(a: str, b: str) -> bool:
    """Both in the same writing: a form in French or Arabic is filled in that language, so a value
    the user translated on the form is not “不一致”."""
    return bool(_CJK.search(a)) == bool(_CJK.search(b))


def _differs(mine: str, theirs: str) -> bool:
    a, b = _count(mine), _count(theirs)
    if a is not None and b is not None:
        return a != b
    return not _same(mine, theirs)


def _short(s: str, n: int = 30) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n] + "……"


def with_forms(forms: list[dict]) -> list[dict]:
    """Each official form of the project against the project: only where the user changed the value
    on the form and it now differs from the same item in the project.

    ``forms``: {instance_id, name, items: [{id, label, fact, given, project, key}]} — ``given`` is
    what the user typed on the form, ``project`` the project's value of the same fact, ``key`` the
    project question it belongs to."""
    out: list[dict] = []
    for f in forms:
        for it in f["items"]:
            mine, theirs = str(it.get("project") or "").strip(), str(it.get("given") or "").strip()
            if not mine or not theirs or mine.startswith("暂不确定") or not _comparable(mine, theirs) or not _differs(mine, theirs):
                continue
            out.append(_issue(
                f"form:{f['code']}:{it['id']}", it["key"],
                f"与《{f['name']}》“{it['label']}”不一致（表里是“{_short(theirs)}”）", MUST,
                {"kind": "form", "instance_id": f["instance_id"], "item": it["id"], "name": f["name"]},
            ))
    return out


def summary(issues: list[dict]) -> list[dict]:
    """One entry per problem (a pair of questions counts once), for “有 N 处前后不一致”."""
    seen: set[str] = set()
    out = []
    for i in issues:
        mark = i.get("pair") or i["id"]
        if mark in seen:
            continue
        seen.add(mark)
        out.append({"key": i["key"], "text": i["text"], "level": i["level"], "to": i["to"]})
    return out
