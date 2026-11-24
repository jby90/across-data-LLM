"""Conservative numeric/unit checks; no substring matches or inferred currencies."""

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

NUMBER = r"\d+(?:,\d{3})*(?:\.\d+)?"
NUMERIC = re.compile(rf"(?<!\d){NUMBER}(?:\s*%)?(?!\d)")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    # Arabic, Persian, Bengali, and Thai decimal digits map deterministically to ASCII 0-9.
    text = text.translate(
        str.maketrans(
            "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹০১২৩৪৫৬৭৮৯๐๑๒๓๔๕๖๗๘๙٫٬",
            "0123456789012345678901234567890123456789.,",
        )
    )
    text = re.sub(rf"٪\s*({NUMBER})", r"\1%", text)
    text = text.replace("٪", "%")
    text = re.sub(rf"百分之\s*({NUMBER})", r"\1%", text)
    # “40 percent / 40 per cent / 40 pour cent”与“40%”是同一个数；写法不同不算译错
    return re.sub(
        rf"({NUMBER})\s*(?:percent|per\s*cent|pour\s*cent|pourcent|بالمئة|في المئة)(?![A-Za-z])",
        r"\1%",
        text,
        flags=re.IGNORECASE,
    )


def canonical(token: str) -> tuple[str, bool]:
    percent = token.strip().endswith("%")
    value = token.replace(",", "").replace("%", "").strip()
    try:
        value = format(Decimal(value).normalize(), "f")
    except InvalidOperation:
        pass
    return value, percent


def tokens(text: str) -> set[tuple[str, bool]]:
    return {canonical(match.group()) for match in NUMERIC.finditer(normalize(text))}


# Bare '$' remains ambiguous: it must stay '$', not silently become USD/EUR/SGD.
CURRENCIES = {
    "SGD": r"S\$|SGD|新加坡元|新元",
    "USD": r"US\$|USD|美元|美金",
    "EUR": r"€|EUR|euros?|欧元",
    "GBP": r"£|GBP|英镑",
    "EGP": r"EGP|埃及镑",
    "TND": r"TND|突尼斯第纳尔",
    "MAD": r"MAD|摩洛哥迪拉姆",
    "LBP": r"LBP|黎巴嫩镑",
    "CNY": r"CNY|RMB|人民币",
    "JPY": r"JPY|日元|日圆",
    "KRW": r"₩|KRW|韩元|韩圆",
    "THB": r"฿|THB|泰铢",
    "MYR": r"MYR|马来西亚林吉特|林吉特",
    "IDR": r"IDR|印度尼西亚盾|印尼盾|印尼卢比",
    "VND": r"₫|VND|越南盾",
    "PHP": r"₱|PHP|菲律宾比索",
    "AED": r"AED|阿联酋迪拉姆",
    "SAR": r"SAR|沙特里亚尔",
    "QAR": r"QAR|卡塔尔里亚尔",
    "KWD": r"KWD|科威特第纳尔",
    "OMR": r"OMR|阿曼里亚尔",
    "DZD": r"DZD|阿尔及利亚第纳尔",
    "RUB": r"₽|RUB|俄罗斯卢布|卢布",
    "KZT": r"₸|KZT|哈萨克斯坦坚戈|坚戈",
    "KGS": r"KGS|吉尔吉斯斯坦索姆|索姆",
    "TRY": r"₺|TRY|土耳其里拉",
    "ILS": r"₪|ILS|以色列新谢克尔|新谢克尔|谢克尔",
    "INR": r"₹|INR|印度卢比",
    "PKR": r"PKR|巴基斯坦卢比",
    "BDT": r"৳|BDT|孟加拉塔卡|塔卡",
    "AUD": r"A\$|AUD|澳大利亚元|澳元",
    "CAD": r"C\$|CAD|加拿大元|加元",
    "BRL": r"R\$|BRL|巴西雷亚尔|雷亚尔",
    "MXN": r"Mex\$|MXN|墨西哥比索",
    "ARS": r"ARS|阿根廷比索",
    "CLP": r"CLP|智利比索",
    "COP": r"COP|哥伦比亚比索",
    "PEN": r"S/\.|PEN|秘鲁索尔|索尔",
    "ZAR": r"ZAR|南非兰特|兰特",
    "NGN": r"₦|NGN|尼日利亚奈拉|奈拉",
    "PLN": r"zł|PLN|波兰兹罗提|兹罗提",
    "SEK": r"SEK|瑞典克朗",
    "DOLLAR_UNSPECIFIED": r"(?<![A-Za-z])\$|dollars?",
}


def amounts(text: str) -> set[tuple[str, str]]:
    text = normalize(text)
    found = set()
    for currency, marker in CURRENCIES.items():
        # Codes and English currency names must be whole tokens: "Made" is not MAD.
        # ASCII-only boundaries allow a Chinese unit directly next to Chinese prose.
        marker = rf"(?<![A-Za-z])(?:{marker})(?![A-Za-z])"
        # Explicit codes/symbols before or immediately after the same numeric amount.
        pattern = re.compile(
            rf"(?:{marker})\s*({NUMBER})(?!\d)|"
            rf"(?<!\d)({NUMBER})\s*(?:{marker})",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            found.add((currency, canonical(match.group(1) or match.group(2))[0]))
    return found


def missing_amounts(original: str, translated: str) -> list[str]:
    return [
        f"{currency} {value}" for currency, value in sorted(amounts(original) - amounts(translated))
    ]


MONTHS = {
    word: index
    for index, group in enumerate(
        (
            "january janvier يناير جانفي",
            "february février فيفري فبراير",
            "march mars مارس",
            "april avril أبريل افريل",
            "may mai مايو ماي",
            "june juin يونيو جوان",
            "july juillet يوليو جويلية",
            "august août أغسطس اوت",
            "september septembre سبتمبر",
            "october octobre أكتوبر اكتوبر",
            "november novembre نوفمبر",
            "december décembre ديسمبر",
        ),
        1,
    )
    for word in group.split()
}


def _valid(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def dates(text: str) -> set[tuple[int, int, int]]:
    """Complete, real calendar dates only. "1.0 October 2020" is a version number then a month,
    not the 0th of October, and "31 February 2020" is not a date at all."""
    text = normalize(text).lower()
    found = set()
    for year, month, day in re.findall(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text):
        found.add((int(year), int(month), int(day)))
    for year, month, day in re.findall(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", text):
        found.add((int(year), int(month), int(day)))
    month_words = "|".join(sorted(MONTHS, key=len, reverse=True))
    for day, month, year in re.findall(
        rf"(?<![\d.])(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+day\s+of)?\s+({month_words})\s*[,،]?\s*(\d{{4}})(?!\d)",
        text,
    ):
        found.add((int(year), MONTHS[month], int(day)))
    for month, day, year in re.findall(rf"({month_words})\s+(\d{{1,2}}),?\s+(\d{{4}})(?!\d)", text):
        found.add((int(year), MONTHS[month], int(day)))
    return {d for d in found if _valid(*d)}


def separate_explicit_dates(text: str):
    """Analysis-only comparison: complete valid dates are identities, not spare numbers.

    Does not change originals, translations or the translation-review protocol.
    Ambiguous/partial dates are not inferred. Invalid explicit dates stay visible.
    """
    normalized = normalize(text).casefold()
    month_words = "|".join(sorted(MONTHS, key=len, reverse=True))
    patterns = [
        (r"(?<!\d)(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", "ymd"),
        (r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", "ymd"),
        (
            rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+day\s+of)?\s+({month_words})\s*[,،]?\s*(\d{{4}})(?!\d)",
            "dmy",
        ),
        (rf"({month_words})\s+(\d{{1,2}}),?\s+(\d{{4}})(?!\d)", "mdy"),
    ]
    values, spans, invalid = set(), [], []
    for pattern, order in patterns:
        for match in re.finditer(pattern, normalized):
            a, b, c = match.groups()
            year, month, day = (
                (int(a), int(b), int(c))
                if order == "ymd"
                else (int(c), MONTHS[b], int(a))
                if order == "dmy"
                else (int(c), MONTHS[a], int(b))
            )
            try:
                date(year, month, day)
            except ValueError:
                invalid.append(match.group())
                continue
            values.add((year, month, day))
            spans.append(match.span())
    characters = list(normalized)
    for start, end in spans:
        characters[start:end] = " " * (end - start)
    return tokens("".join(characters)), values, invalid


_CURRENCY_MARKERS = re.compile(
    "|".join(f"(?:{marker})" for marker in CURRENCIES.values()), re.IGNORECASE
)


_MIN_WORD_LETTERS = 3
# 货币码只在不紧贴字母时才算（“Made”里的 MAD 不是货币）
_STANDALONE_CURRENCY = re.compile(
    r"(?<![^\W\d_])(?:" + "|".join(f"(?:{m})" for m in CURRENCIES.values()) + r")(?![^\W\d_])",
    re.IGNORECASE,
)


_CONTACT = re.compile(r"[^\s@]+@\S+|(?:https?://|www\.)\S+", re.IGNORECASE)
_LEADER_DOTS = re.compile(r"\.{8,}|…{3,}|。{8,}")  # 目录里页码前的点线
_REVISION_ROW = re.compile(
    r"^\s*\d+(?:\.\d+)*\s+(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December|janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|"
    r"novembre|d[ée]cembre)\s+\d{4}(?!\d)",
    re.I,
)


_LEADER = r"(?:\.{3,}|…|。{3,})[.…。\s]*"
_PAGES = r"\d{1,4}(?:\s*(?:à|-|–|to)\s*\d{1,4})?(?:\s+\d{1,4}){0,2}"
# 一行目录：标题 + 点线 + 页码（从右往左排的文字页码在点线前），页码在行尾
_CONTENTS_ENTRY = re.compile(
    rf"(?:{_LEADER}:?\s*{_PAGES}|(?<![\d.]){_PAGES}\s*(?:\.{{3,}}|…{{2,}}|。{{3,}})[.…。]*)\s*$"
    # 从右往左排的文字抽出来是“页码 …… 标题”
    rf"|^\s*{_PAGES}\s*(?:\.{{8,}}|…{{3,}}|。{{8,}})[.…。]*\s*\S"
)
# 页码被切到下一段的目录行：以章节编号或“章、条、附件”开头、以长点线结尾
# （表单里“姓名：……”以标签开头，不算）
_NUMBERED_TITLE = re.compile(
    r"^\s*(?:\d+(?:[.\-]\d+)*[.\-)]?|[IVXLC]+[.\-)]|(?:Chapitre|Chapter|CHAPITRE|CHAPTER|Section|SECTION|Article|"
    r"TITRE|Titre|Annexe|ANNEXE|Annex|ANNEX|Part|PART|المادة|الفصل|الباب)\b)\s*\S.*?(?:\.{8,}|…{3,}|。{8,})[.…。]*\s*[:：]?\s*$"
)
# 页码排在另一栏、被抽到别处的目录行：标题以长点线收尾，前面没有“标签：”（表单的填空行带冒号）
_TRAILING_LEADER = re.compile(r"^[^:：]*[^\W\d_][^:：]*?(?:\.{8,}|…{3,}|。{8,})[.…。\s]*$")
# 法典目录常见的“条号范围 + 页码”结尾（“… 1 à 20 79”），没有点线
_RANGE_PAGE = re.compile(r"\b\d{1,4}\s*(?:à|-|–|to)\s*\d{1,4}\s+\d{1,4}\s*$")
_WORD3 = re.compile(r"[^\W\d_]{3,}")
MAX_CONTENTS_GAP_WORDS = 30  # 两个目录项之间（以及首尾）最多容许的折行字数，按 3 个字母以上的词计
# 大段目录（十项以上、目录项占有字的行五分之一以上）里，长标题会折好几行、还夹着页眉水印，容许宽一些
LONG_CONTENTS_GAP_WORDS = 100


def _has_leader(s: str) -> bool:
    return "..." in s or "…" in s or "。。。" in s


def _is_contents(text: str) -> bool:
    """A table of contents, judged line by line: an entry is “title … page” on one line (or a
    numbered title ending in dot leaders whose page went to the next fragment, or “… 1 à 20 79”).
    The text is a table of contents when, between its entries (and before and after them), there
    are only wrapped titles; a stretch of running text — a table of contents followed by the body,
    or a form or table inside the law with dot leaders to fill in (“Poids : ……… T”) — makes it law
    to be translated."""
    if not text or (not _has_leader(text) and not _RANGE_PAGE.search(text)):
        return False
    lines = [ln for ln in text.splitlines() if ln.strip()]
    worded = sum(1 for ln in lines if _WORD3.search(ln))  # 页眉水印拆出来的零碎字母行不算
    # 带页码的目录项是确凿的；只以点线收尾的行，在没有任何页码的多行文字里多半是表单的填空
    paged = any(
        (_has_leader(ln) and _CONTENTS_ENTRY.search(ln)) or _RANGE_PAGE.search(ln) for ln in lines
    )
    weak_ok = paged or worded <= 1
    entries, gap, widest = 0, 0, 0
    for ln in lines:
        has_ld = _has_leader(ln)
        strong = (has_ld and _CONTENTS_ENTRY.search(ln)) or _RANGE_PAGE.search(ln)
        weak = has_ld and (_NUMBERED_TITLE.match(ln) or _TRAILING_LEADER.match(ln))
        if strong or (weak and weak_ok):
            entries += 1
            widest, gap = max(widest, gap), 0
        else:
            gap += len(_WORD3.findall(ln))
    widest = max(widest, gap)
    if not entries:
        return False
    if widest <= MAX_CONTENTS_GAP_WORDS:
        return True
    return entries >= 10 and entries * 5 >= worded and widest <= LONG_CONTENTS_GAP_WORDS


def _is_front_matter(text: str) -> bool:
    """A table of contents (see ``_is_contents``) or a row of a document's own revision history
    ("1.3 September 2023 <agency> Revised process"). They carry no legal content, and a model that
    translates them invents days and dates."""
    flat = " ".join((text or "").split())
    return _is_contents(text) or (bool(_REVISION_ROW.match(flat)) and len(flat) < 220)


_PROSE_WORD9 = re.compile(r"(?<![@:/.\w])[^\W\d_]{9,}(?![\w.@])")


def is_numeric_only(text: str) -> bool:
    """True when a fragment has nothing to translate: numbers and symbols only, a table-of-contents
    line, or a revision-history row of the document itself (see below for the first kind).

    That is: only numbers, amounts, symbols and punctuation (price-list cells, bare figures),
    or no real word at all — only one- and two-letter debris (OCR noise such as "m Il I Il 1",
    a stray "من" next to a figure). A word of three or more letters in any alphabet keeps the
    fragment translatable.
    """
    if _is_front_matter(text):
        return True
    if text and len(_PROSE_WORD9.findall(text)) >= 2:
        return False
    # 邮箱、网址不是要翻译的词（“43 -----@tra.gov.eg”这样的联系方式碎片）
    rest = _STANDALONE_CURRENCY.sub(" ", _CONTACT.sub(" ", normalize(text)))
    if not any(ch.isalnum() for ch in rest):
        return False  # 只有符号/标点：不是“数字片段”，保持原判断
    words = re.findall(r"[^\W\d_]+", rest)
    return not any(len(w) >= _MIN_WORD_LETTERS for w in words)
