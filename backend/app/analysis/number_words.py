# ruff: noqa: E501
"""Figures written out in words or in other scripts, so a figure the model restates in digits can be
found in the law it cites: “ninety days”, “quatre-vingt-dix jours”, “تسعين يوما”, “一百万埃镑”,
“1,5 million”, “٩٠ يوما”. Used on the source side only (the clauses a text may rest on): reading
more figures there can only accept a correct restatement, never let a figure the law lacks through.
"""

from __future__ import annotations

import re

from app.ingestion.article_numbers import normalize_arabic

# 东阿拉伯数字（阿拉伯文、波斯文两套）与阿拉伯文的千分位、小数点
EASTERN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٬٫", "01234567890123456789,.")

_EN = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1_000,
    "million": 1_000_000, "millions": 1_000_000, "billion": 1_000_000_000, "billions": 1_000_000_000,
}
_FR = {
    "zéro": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8,
    "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16,
    "vingt": 20, "vingts": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60, "cent": 100,
    "cents": 100, "mille": 1_000, "mil": 1_000, "million": 1_000_000, "millions": 1_000_000,
    "milliard": 1_000_000_000, "milliards": 1_000_000_000,
}
_AR_BASE = {
    "واحد": 1, "واحده": 1, "احد": 1, "احدي": 1, "اثنان": 2, "اثنين": 2, "اثنتان": 2, "اثنتين": 2, "اثني": 2,
    "ثلاث": 3, "ثلاثه": 3, "اربع": 4, "اربعه": 4, "خمس": 5, "خمسه": 5, "ست": 6, "سته": 6, "سبع": 7, "سبعه": 7,
    "ثمان": 8, "ثماني": 8, "ثمانيه": 8, "تسع": 9, "تسعه": 9, "عشر": 10, "عشره": 10,
    "عشرون": 20, "عشرين": 20, "ثلاثون": 30, "ثلاثين": 30, "اربعون": 40, "اربعين": 40, "خمسون": 50, "خمسين": 50,
    "ستون": 60, "ستين": 60, "سبعون": 70, "سبعين": 70, "ثمانون": 80, "ثمانين": 80, "تسعون": 90, "تسعين": 90,
    "مائه": 100, "مئه": 100, "مايه": 100, "مائتان": 200, "مائتين": 200, "مائتي": 200, "مئتان": 200, "مئتين": 200, "مئتي": 200,
    "الف": 1_000, "الاف": 1_000, "الفا": 1_000, "الفان": 2_000, "الفين": 2_000, "الفي": 2_000,
    "مليون": 1_000_000, "ملايين": 1_000_000, "مليونا": 1_000_000, "مليونان": 2_000_000, "مليونين": 2_000_000,
    "مليار": 1_000_000_000, "مليارات": 1_000_000_000,
}
for _w, _v in [("ثلاث", 3), ("اربع", 4), ("خمس", 5), ("ست", 6), ("سبع", 7), ("ثمان", 8), ("تسع", 9)]:
    _AR_BASE[_w + "مائه"] = _AR_BASE[_w + "مئه"] = _v * 100
_SCALES = {1_000, 1_000_000, 1_000_000_000}
_DONE_PAIR = {2_000, 2_000_000}  # “ألفان”“مليونان”：本身就是一个完整的数
_CONNECT = {"and", "et", "و"}
_TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[^\W\d_]+", re.UNICODE)


def _word_value(token: str) -> int | None:
    t = token.lower()
    if t in _EN:
        return _EN[t]
    if t in _FR:
        return _FR[t]
    a = normalize_arabic(token)
    for cand in (a, a[1:] if a.startswith("و") else None, a[2:] if a.startswith("ال") else None, a[3:] if a.startswith("وال") else None):
        if cand and cand in _AR_BASE:
            return _AR_BASE[cand]
    return None


def _digit_value(token: str) -> float | None:
    raw = token
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):
        return float(re.sub(r"[.,]", "", raw))
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def _combine(values: list[float]) -> float:
    total = current = 0.0
    for v in values:
        if v in _DONE_PAIR:
            total += max(current, 1) * v if current else v
            current = 0
        elif v in _SCALES:
            total += max(current, 1) * v
            current = 0
        elif v == 100:
            current = (current or 1) * 100
        elif current and v == 20 and current == 4:  # quatre-vingt(s) = 80
            current = 80
        else:
            current += v
    return total + current


def word_figures(text: str) -> set[float]:
    """Every figure in the text, read across words and digits (``1,5 million`` → 1500000)."""
    text = (text or "").translate(EASTERN_DIGITS)
    out: set[float] = set()
    run: list[float] = []

    def flush():
        if run:
            out.add(_combine(run))
            out.update(v for v in run if v not in _SCALES)
            run.clear()

    for token in _TOKEN.findall(text.replace("-", " ")):
        if token[0].isdigit():
            v = _digit_value(token)
            if v is None:
                flush()
                continue
            flush()  # 数字只和后面的量级词组合（2 million），不和前面的词相加
            run.append(v)
            continue
        if token.lower() in _CONNECT or normalize_arabic(token) == "و":
            continue
        v = _word_value(token)
        if v is None:
            flush()
            continue
        run.append(float(v))
    flush()
    return out


_ZH_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_ZH_UNIT = {"十": 10, "百": 100, "千": 1_000}
_ZH_BIG = {"万": 10_000, "亿": 100_000_000}
_ZH_RUN = re.compile(r"[零〇一二两三四五六七八九十百千万亿]+")


def _zh_value(s: str) -> int:
    total = section = number = 0
    for ch in s:
        if ch in _ZH_DIGIT:
            number = _ZH_DIGIT[ch]
        elif ch in _ZH_UNIT:
            section += (number or 1) * _ZH_UNIT[ch]
            number = 0
        else:
            total += (section + number) * _ZH_BIG[ch]
            section = number = 0
    return total + section + number


def chinese_figures(text: str) -> set[float]:
    """Figures written in Chinese numerals (“一百万”“九十日”“三十”), and digits followed by 万/亿."""
    out = {float(_zh_value(m)) for m in _ZH_RUN.findall(text or "") if any(c not in "万亿" for c in m)}
    return out


# ---------------- 日期：按“年、月、日”整体对，不拆成单个数字 ----------------
_MONTH_GROUPS = (
    "january jan janvier janv يناير جانفي كانون_الثاني",
    "february feb février fevrier févr fevr فبراير فيفري شباط",
    "march mar mars مارس آذار اذار",
    "april apr avril avr أبريل ابريل افريل أفريل نيسان",
    "may mai مايو ماي أيار ايار",
    "june jun juin يونيو يونيه جوان حزيران",
    "july jul juillet juil يوليو يوليه جويلية تموز",
    "august aug août aout أغسطس اغسطس أوت اوت آب",
    "september sep sept septembre سبتمبر أيلول ايلول",
    "october oct octobre أكتوبر اكتوبر تشرين_الأول تشرين_الاول",
    "november nov novembre نوفمبر تشرين_الثاني",
    "december dec déc décembre decembre ديسمبر كانون_الأول كانون_الاول",
)
MONTH_NUMBER = {w.replace("_", " "): i for i, g in enumerate(_MONTH_GROUPS, 1) for w in g.split()}
_MONTH_RE = "|".join(sorted((re.escape(w) for w in MONTH_NUMBER), key=len, reverse=True))
_FIRST = r"(?:1er|1st|premier|1)"


def _valid(y: int, m: int, d: int) -> bool:
    import datetime as _dt

    try:
        _dt.date(y, m, d)
    except ValueError:
        return False
    return 1900 <= y <= 2100


def source_dates(text: str) -> set[tuple[int, int, int]]:
    """Dates a law states, in any of its scripts: “14 avril 2001”, “27 جويلية 2004”, “April 14, 2001”,
    “2001-04-14”, “14/04/2001”, “2001年4月14日”."""
    t = (text or "").translate(EASTERN_DIGITS).lower()
    out: set[tuple[int, int, int]] = set()
    for d, m, y in re.findall(rf"(?<![\d.])(\d{{1,2}}|{_FIRST})(?:st|nd|rd|th|er)?\s*(?:day of\s+)?({_MONTH_RE})\.?\s*[,،]?\s*(\d{{4}})", t):
        out.add((int(y), MONTH_NUMBER[m], 1 if not d.isdigit() else int(d)))
    for m, d, y in re.findall(rf"({_MONTH_RE})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})", t):
        out.add((int(y), MONTH_NUMBER[m], int(d)))
    for y, m, d in re.findall(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)", t):
        out.add((int(y), int(m), int(d)))
    for d, m, y in re.findall(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(?!\d)", t):
        out.add((int(y), int(m), int(d)))
    for y, m, d in re.findall(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", t):
        out.add((int(y), int(m), int(d)))
    return {x for x in out if _valid(*x)}


def source_months(text: str) -> set[tuple[int, int]]:
    """Year and month a law states (“avril 2001”, “2001年4月”), for a statement that gives no day."""
    t = (text or "").translate(EASTERN_DIGITS).lower()
    out = {(y, m) for y, m, _ in source_dates(t)}
    for m, y in re.findall(rf"({_MONTH_RE})\.?\s*[,،]?\s*(\d{{4}})", t):
        out.add((int(y), MONTH_NUMBER[m]))
    for y, m in re.findall(r"(\d{4})\s*年\s*(\d{1,2})\s*月", t):
        out.add((int(y), int(m)))
    return out


_ZH_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?")


def written_dates(text: str) -> tuple[list[tuple[str, tuple[int, ...]]], str]:
    """The dates a Chinese statement writes (“2001年4月14日”“2004年7月”), and the text with them taken
    out so their parts are not checked again as loose figures."""
    found = []
    for m in _ZH_DATE.finditer(text or ""):
        y, mo, d = int(m.group(1)), int(m.group(2)), m.group(3)
        found.append((m.group(0).replace(" ", ""), (y, mo, int(d)) if d else (y, mo)))
    return found, _ZH_DATE.sub(" ", text or "")
