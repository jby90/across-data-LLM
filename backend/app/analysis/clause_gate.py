# ruff: noqa: E501  规则是长正则
"""Which clauses may be quoted in a document at all — one gate for the compliance documents, the
country briefings and the admission material (and for re-checking the library).

A clause is kept out when its Chinese text cannot be relied on as it stands:
* no Chinese translation (the original is never quoted in its place);
* the translation is garbled: many untranslated foreign words, replacement characters, article
  ranges that run backwards (“第 61 条至第 1 条”), recognition markers (“OCR不清”);
* it carries a person's telephone number or e-mail address;
* it is a gazette preamble, a signature block or an administrative reference line, not a rule;
* its document is an official form, not a law (forms are filled, not cited).

Laws are named the same way everywhere: the Chinese name without notes on which copy the library
holds (“（人权委员会转载版）”“（现行合订本）”“（阿文）”).
"""

from __future__ import annotations

import re

_ARABIC = re.compile(r"[؀-ۿݐ-ݿ]")
_OTHER_FOREIGN_SCRIPT = re.compile(
    r"[\u0400-\u04ff\u0590-\u05ff\u0980-\u09ff\u0e00-\u0e7f\u3040-\u309f\uac00-\ud7af]"
)
_LATIN_WORD = re.compile(r"(?<![A-Za-z])[a-z]{4,}(?![A-Za-z])")
_UNCLEAR = re.compile(r"OCR(?![-–][ABab]\b)|识别不清|原文不清|字迹不清|（不清）|\(illegible\)|�")
_PHONE = re.compile(r"\+\s?\d{2,3}\s?-?\s?\d{6,}|(?<!\d)1[3-9]\d{9}(?!\d)|(电话|手机|Tel|Mobile)[ \t]*[:：]?[ \t]*(?=(?:[ \t\-()]*\d){7,})\+?\d[\d \t\-()]{6,}", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.\-]+@[\w\-]+\.[\w.]+")
_PREAMBLE = re.compile(r"^\s*(鉴于|经查阅|经审阅|根据宪法|以人民名义|兹颁布|兹决定|决定如下|此致|敬启|尊敬的|行政参考)")
_SIGNATURE = re.compile(r"(部长|总理|总统|主席|署长|局长)\s*[一-鿿·]{2,12}\s*(博士)?\s*$")
_RANGE = re.compile(r"第\s*(\d+)\s*条至第\s*(\d+)\s*条")
_FORM_TITLE = re.compile(r"申请表|申报表|表格|申请书模板|\bForm\b|formulaire|F\d{3}\b", re.IGNORECASE)
_COPY_NOTE = re.compile(r"[（(][^（）()]*(版|原文|合订|转载|阿文|法文|英文|译本|公报|官方文本|两版)[^（）()]*[）)]")


_BY_TITLE: dict[str, str] = {}
_BY_COUNTRY_TITLE: dict[str, dict[str, str]] = {}
_AMBIGUOUS_TITLES: set[str] = set()


def clear_title_names() -> None:
    _BY_TITLE.clear()
    _BY_COUNTRY_TITLE.clear()
    _AMBIGUOUS_TITLES.clear()


async def load_title_names(session) -> None:
    """Fill the title → site-wide name map (scoped by country_code so homonymous laws across
    jurisdictions never overwrite one another). Reads the documents' titles once per process."""
    if _BY_TITLE or _BY_COUNTRY_TITLE:
        return
    from sqlalchemy import text as sql

    from app.admission.names import law_name as table_name

    rows = (await session.execute(sql("select id::text, country_code, title, title_zh from documents"))).all()
    for did, cc, title, title_zh in rows:
        named = table_name(did, "")
        if not named:
            continue
        cmap = _BY_COUNTRY_TITLE.setdefault((cc or "").upper(), {})
        for t in (title, title_zh):
            if not t:
                continue
            variants = [t]
            compact = re.sub(
                r"(?<=[\u4e00-\u9fff])\s+(?=[\dA-Za-z])|(?<=[\dA-Za-z])\s+(?=[\u4e00-\u9fff])",
                "",
                t,
            )
            if compact != t:
                variants.append(compact)
            nospace = re.sub(r"\s+", "", t)
            if re.search(r"[\u4e00-\u9fff]", t) and nospace != t and nospace not in variants:
                variants.append(nospace)
            for v in variants:
                cmap.setdefault(v, named)
                if v in _BY_TITLE and _BY_TITLE[v] != named:
                    _AMBIGUOUS_TITLES.add(v)
                else:
                    _BY_TITLE.setdefault(v, named)


def law_name(title: str | None, document_id: str | None = None, country_code: str | None = None) -> str:
    """The Chinese name of a law as documents show it: one name however many copies the library holds
    (the site-wide name table ``app.admission.names`` first: “名称（年份 + 编号）”)."""
    if document_id:
        from app.admission.names import law_name as table_name

        named = table_name(str(document_id), "")
        if named:
            return named
    if country_code and title:
        cmap = _BY_COUNTRY_TITLE.get(country_code.upper())
        if cmap and title in cmap:
            return cmap[title]
    if title and title in _BY_TITLE and title not in _AMBIGUOUS_TITLES:
        return _BY_TITLE[title]
    name = _COPY_NOTE.sub("", title or "").strip()
    return re.sub(r"\s{2,}", " ", name) or (title or "")


def is_form(title: str | None) -> bool:
    return bool(_FORM_TITLE.search(title or ""))


def chinese_text(zh: str | None, original: str | None = "") -> str:
    """The Chinese text of a clause: its translation, or its original when the original is Chinese."""
    t = (zh or "").strip()
    if t in ("", "(未提供译文)", "（未提供译文）"):
        o = (original or "").strip()
        return o if len(re.findall(r"[一-鿿]", o)) > len(o) * 0.3 else ""
    return t


def why_unusable(zh: str | None, title: str | None = "", original: str | None = "", forms_ok: bool = False) -> str:
    """Why a clause cannot be quoted ("" when it can)."""
    t = chinese_text(zh, original)
    if t in ("", "(未提供译文)", "（未提供译文）"):
        return "缺中文译文"
    if is_form(title) and not forms_ok:
        return "官方表格"
    o = original or ""
    if o and len(re.findall(r"[ﭐ-﻿�]", o)) > max(5, len(o) * 0.05):
        return "原文识别乱码"
    if _UNCLEAR.search(t):
        return "识别不清"
    bare = re.sub(r"[（(][^（）()]{0,120}[）)]", "", t)  # 括注的原文术语不算
    letters = len(re.findall(r"[一-鿿]", bare))
    if (
        len(_ARABIC.findall(bare)) > 12
        or len(_OTHER_FOREIGN_SCRIPT.findall(bare)) > 12
        or len(re.findall(r"[A-Za-z]", bare)) > max(80, letters)
    ):
        return "夹着大段未译的外文"
    if len(_LATIN_WORD.findall(bare)) >= 3 or len(re.findall(r"(?<![A-Za-z])(of|the|and|to)(?![A-Za-z])", bare)) >= 3:
        return "夹着未译的外文词"
    if _PHONE.search(t) or (_EMAIL.search(t) and len(t) < 600):
        return "含个人电话或邮箱"
    head = t[:60]
    if _PREAMBLE.search(head) or _SIGNATURE.search(t[-40:]):
        return "公报序言或签署"
    for a, b in _RANGE.findall(t):
        if int(b) < int(a):
            return "条号顺序错乱"
    if re.search(r"(?:^|[\n；;。])\s*(?:T|ZE\s*\d+|ss)\s+[一-鿿]", t):
        return "列举编号错乱"
    if o:
        nums = set(re.findall(r"\d+", o.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))))
        cited = [n for n in re.findall(r"第\s*(\d+)\s*条", t[12:])]  # 开头本条的条号不算
        if nums and len(cited) >= 2 and sum(1 for n in cited if n not in nums) >= 2:
            return "交叉引用的条号和原文对不上"
    return ""


def usable(c: dict) -> bool:
    return not why_unusable(c.get("quote_zh"), c.get("original_title") or c.get("document_title"), c.get("quote_original"))


def script_ok(original: str | None, language: str | None) -> bool:
    """Whether an original reads as its stated language (a French decree whose text came out as
    Arabic presentation forms or replacement characters was recognised wrongly and goes back to
    ingestion)."""
    o = original or ""
    if not o.strip():
        return False
    bad = len(re.findall(r"[ﭐ-﻿�]", o))
    if bad > max(5, len(o) * 0.05):
        return False
    lang = (language or "").lower()
    if lang in ("fr", "en"):
        return len(re.findall(r"[A-Za-zÀ-ÿ]", o)) >= len(o) * 0.4
    if lang == "ar":
        return len(re.findall(r"[؀-ۿ]", o)) >= len(o) * 0.3
    return True


_UNIT_AFTER_NUM = (
    r"(?:年|个月|月|日|天|周|小时|时|分钟|分|秒|工作日|自然日|人|万|千|百|亿|元|镑|迪拉姆|第纳尔|新元|里拉|美元|欧元|"
    r"项|条|款|目|章|节|段|编|步|处|份|部|类|种|个|次|家|名|位|辆|台|套|张|页|米|千米|公里|倍|成|级|档|号|版|分之)"
)
_MARGIN_LINENO_LINE = re.compile(
    rf"(?m)(?:^|\n)[ \t]*(?:5|10|15|20|25|30|35|40)[ \t]+(?:\n|[ \t]*(?!{_UNIT_AFTER_NUM})(?=[\u4e00-\u9fff（(第“\"A-Za-z]))"
)
_MARGIN_LINENO_INLINE = re.compile(
    rf"(?<=[，。；：！？）)])[ \t]+(?:5|10|15|20|25|30|35|40)[ \t]+(?!{_UNIT_AFTER_NUM})(?=[\u4e00-\u9fff（(])"
)


def clean_margin_numbers(text: str) -> str:
    """Strip Gazette / statute PDF margin line numbers (5, 10, 15, 20, 25...) leaked by OCR."""
    if not text:
        return ""
    out = _MARGIN_LINENO_LINE.sub(lambda m: "\n" if m.group(0).startswith("\n") else "", text)
    out = _MARGIN_LINENO_INLINE.sub("", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def strip_own_label(label: str, text: str) -> str:
    """“第36条：第36条 ……” → the excerpt without repeating the article number it is shown under."""
    t = clean_margin_numbers(text or "").lstrip()
    m = re.match(r"^(【)?(第\s*[0-9一二三四五六七八九十百]+[A-Za-z]?\s*(条|项|段|节))(】)?\s*(?:[（(][^）)\n]{0,40}[）)])?\s*[:：/／\-—–。.]*\s*", t)
    if m and re.sub(r"\s", "", m.group(2)) == re.sub(r"\s", "", label or ""):
        return t[m.end():].lstrip(" /／\n。.")
    return t


def _close_delims_before_ellipsis(prefix: str) -> str:
    s = prefix.rstrip()
    for op, cl in (("《", "》"), ("（", "）"), ("(", ")"), ("“", "”"), ("【", "】")):
        diff = s.count(op) - s.count(cl)
        if diff > 0:
            s = s + (cl * diff)
    return s


_ABBREV_BEFORE_DOT_RE = re.compile(
    r"(?:^|[\s(\[])(?:Art|Arts|Sec|Secs|No|Nos|Ch|Fig|Figs|al|etc|vs|cf|para|paras|sub|pt|vol|rev|ed|e\.g|i\.e|\d+|[A-Za-z]|[ivxlcdmIVXLCDM]{1,5})$"
)
_DANGLING_LINE_TAIL_RE = re.compile(
    r"(?:[:：—–\-]|(?:以及|及|和|或|且|并|与|and|or|ou|et|أو|و)|(?:^|[\s（(])(?:\d+\.|[a-zA-Z]\.|[一二三四五六七八九十]+、))\s*$",
    re.IGNORECASE,
)


def find_clean_sentence_cut(text: str, limit: int) -> int:
    """Return the index (end-exclusive) of the cleanest sentence/clause boundary ``<= limit``,
    or ``-1`` when none exists past ``limit // 3``. Never cuts right after a list item prefix
    (``5. ``, ``6. ``) or right after a dangling conjunction (``；以及\\n``)."""
    prefix = text[:limit]
    best = -1
    for ch in ("。", "；", ";", "！", "？"):
        pos = prefix.rfind(ch)
        if pos >= 0 and pos + 1 > best:
            best = pos + 1
    for m in re.finditer(r'[A-Za-z0-9)\]"”\u2019]\.(?=\s|$)', prefix):
        dot_end = m.end()
        before_dot = prefix[: dot_end - 1]
        if not _ABBREV_BEFORE_DOT_RE.search(before_dot) and dot_end > best:
            best = dot_end
    for sep in ("\n", " / "):
        pos = prefix.rfind(sep)
        while pos >= limit // 3:
            before_sep = prefix[:pos].rstrip()
            if before_sep and not _DANGLING_LINE_TAIL_RE.search(before_sep):
                if pos > best:
                    best = pos
                break
            pos = prefix.rfind(sep, 0, pos)
    return best if best >= limit // 3 else -1


def _strip_dangling_tail(fragment: str) -> str:
    s = fragment.rstrip(" /\n\t")
    while True:
        nxt = _DANGLING_LINE_TAIL_RE.sub("", s).rstrip(" ，,：:/\n\t")
        if nxt == s or not nxt:
            break
        s = nxt
    return s


def cut(text: str, limit: int = 220) -> str:
    """An excerpt that ends at the end of a sentence or an item, marked when cut."""
    text = clean_margin_numbers(text or "")
    if len(text) <= limit:
        return text
    cut_end = find_clean_sentence_cut(text, limit)
    if cut_end > 0:
        return _close_delims_before_ellipsis(_strip_dangling_tail(text[:cut_end])) + "……"
    end = limit
    if end < len(text) and text[end - 1].isascii() and text[end].isascii():
        while end > limit - 24 and text[end - 1].isalnum():
            end -= 1
    return _close_delims_before_ellipsis(_strip_dangling_tail(text[:end])) + "……"


_HISTORICAL = re.compile(r"颁布原文|历史指南|历史版本|旧版|已废止|已失效|原始版本")
_YEAR_HEAD = re.compile(r"^\s*(?:新加坡|摩洛哥|埃及|突尼斯|黎巴嫩)?\s*(\d{4})\s*年\s*")
_YEAR_TAIL = re.compile(r"[（(]\s*\d{4}\s*年[^）)]*[）)]|\s*\d{4}\s*$")


def superseded_titles(titles: list[str]) -> set[str]:
    """Titles of documents that are not the law as it stands: a text labelled as the original
    promulgation or a historical guide, and — of two instruments with the same name and different
    years (“2014年个人数据保护条例”“2021年个人数据保护条例”) — the earlier one."""
    out = {t for t in titles if t and _HISTORICAL.search(t)}
    groups: dict[str, list[tuple[int, str]]] = {}
    for t in titles:
        m = _YEAR_HEAD.match(t or "")
        if not m or t in out:
            continue
        base = _YEAR_TAIL.sub("", t[m.end():]).strip()
        if len(base) >= 6 and not re.search(r"修订|公告|令$|勘误|生效|废除|撤销|任命", base):
            groups.setdefault(base, []).append((int(m.group(1)), t))
    for items in groups.values():
        newest = max(y for y, _ in items)
        out |= {t for y, t in items if y < newest}
    return out


_MISTRANSLATED = {"电信研究与研究中心": "电信研究中心", "国家电信管理局": "国家电信监管局", "电信管理局（ANRT）": "电信监管局（ANRT）"}


def unify_law_names(text: str, country_code: str | set[str] | list[str] | tuple[str, ...] | None = None) -> str:
    """One name for every law (the site-wide name table, scoped to active country_code(s) when given
    so homonymous titles like 《个人数据保护法》 never resolve to another country's statute)."""
    if not text or (not _BY_TITLE and not _BY_COUNTRY_TITLE):
        return text
    if country_code:
        codes = {country_code.upper()} if isinstance(country_code, str) else {str(c).upper() for c in country_code if c}
        scoped: dict[str, str] = {}
        ambig: set[str] = set()
        for cc in sorted(codes):
            for t, nm in _BY_COUNTRY_TITLE.get(cc, {}).items():
                if t in scoped and scoped[t] != nm:
                    ambig.add(t)
                else:
                    scoped[t] = nm
        active_map = {t: nm for t, nm in scoped.items() if t not in ambig}
    else:
        active_map = {t: nm for t, nm in _BY_TITLE.items() if t not in _AMBIGUOUS_TITLES}

    out = text
    for title, name in sorted(active_map.items(), key=lambda kv: -len(kv[0])):
        if title != name and len(title) >= 6 and f"《{title}》" in out:
            out = out.replace(f"《{title}》", f"《{name}》")
    for name in sorted(set(active_map.values()), key=len, reverse=True):
        m = re.search(r"（第(\d{2,4}-\d{1,4})号法律）", name)
        if not m:
            continue
        num = m.group(1)
        # 只换正文里单独出现的简写；《》里的别的法规名（“实施第09-08号法律的法令”“网站遵守第09-08号法合规指南”）不动
        parts = re.split(r"(《[^》]*》)", out)
        for k in range(0, len(parts), 2):
            parts[k] = re.sub(rf"(?<![\d-])第\s*{re.escape(num)}\s*号法律?(?![律》）\d])(?!的第|合规|违法)", f"《{name}》", parts[k])
        out = "".join(parts)
        out = re.sub(rf"《{re.escape(num)}号[^》]{{0,10}}个人数据保护法》", f"《{name}》", out)
    out = re.sub(r"(（第\d{2,4}-\d{1,4}号法律）)\s*（(?:以下)?简称[^）]{0,20}）", r"\1", out)  # 全称之后不再留“（简称第09-08号法）”
    for a, b in _MISTRANSLATED.items():
        out = out.replace(a, b)
    return out
