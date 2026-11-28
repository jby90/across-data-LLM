"""Conservative numeric/unit checks; no substring matches or inferred currencies."""

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

NUMBER = r"\d+(?:,\d{3})*(?:\.\d+)?"
NUMERIC = re.compile(rf"(?<!\d){NUMBER}(?:\s*%)?(?!\d)")

_PERCENT_WORDS_AFTER = (
    r"percent|per\s*cent|pour\s*cent|pourcent|prozent|procent|"
    r"per\s*cento|percento|por\s*ciento|por\s*cento|persen|phần\s*trăm|"
    r"процент(?:а|ов|ам|ами|ах)?|بالمئة|بالمائة|في المئة|في المائة|"
    r"パーセント|퍼센트|เปอร์เซ็นต์"
)
_CURRENCY_DOT_PREFIX_RE = re.compile(
    r"(?i)(?:€|EUR|US\$|USD|S\$|SGD|A\$|AUD|C\$|CAD|R\$|BRL|Mex\$|MXN|S/\.|PEN|Rp\.?|IDR|RM|MYR|"
    r"£|GBP|₺|TRY|TL|₫|VND|₱|PHP|₽|RUB|₸|KZT|KGS|₪|ILS|₹|INR|PKR|৳|BDT|₦|NGN|₩|KRW|฿|THB|"
    r"PLN|ARS|CLP|COP|ZAR|SEK|CHF|MAD|DH|TND|DT|DZD|EGP|LBP|AED|SAR|QAR|KWD|OMR|CNY|RMB|JPY|\$|"
    r"円|원|บาท|"
    r"欧元|美元|美金|英镑|新加坡元|新元|澳大利亚元|澳元|加拿大元|加元|人民币|日元|日圆|韩元|韩圆|泰铢|马来西亚林吉特|林吉特|"
    r"瑞士法郎|瑞郎|法郎|"
    r"巴西雷亚尔|雷亚尔|印度尼西亚盾|印尼盾|印尼卢比|土耳其里拉|里拉|越南盾|波兰兹罗提|兹罗提|菲律宾比索|"
    r"墨西哥比索|阿根廷比索|智利比索|哥伦比亚比索|比索|秘鲁索尔|索尔|迪拉姆|阿联酋迪拉姆|摩洛哥迪拉姆|"
    r"第纳尔|突尼斯第纳尔|科威特第纳尔|阿尔及利亚第纳尔|里亚尔|沙特里亚尔|卡塔尔里亚尔|阿曼里亚尔|"
    r"埃及镑|黎巴嫩镑|俄罗斯卢布|卢布|瑞典克朗|克朗|哈萨克斯坦坚戈|坚戈|吉尔吉斯斯坦索姆|索姆|"
    r"以色列新谢克尔|新谢克尔|谢克尔|印度卢比|巴基斯坦卢比|孟加拉塔卡|塔卡|南非兰特|兰特|尼日利亚奈拉|奈拉)\s*$"
)
_CURRENCY_DOT_SUFFIX_RE = re.compile(
    r"(?i)^\s*(?:€|EUR|euros?|US\$|USD|dollars?|S\$|SGD|A\$|AUD|C\$|CAD|R\$|reais|real|BRL|Mex\$|MXN|"
    r"S/\.|soles|PEN|Rp\.?|rupiah|IDR|RM|MYR|ringgit|£|GBP|Egyptian\s+pounds?|pounds?|livres?|"
    r"₺|TRY|TL|t[üu]rk\s+liras\w*|liras?\b|₫|đồng|VND|₱|PHP|pesos?|ARS|CLP|COP|"
    r"zł|PLN|złot\w*|₽|RUB|rubles?|рубл\w*|₸|KZT|KGS|₪|ILS|shekels?|₹|INR|PKR|rupees?|৳|BDT|taka|₦|NGN|naira|ZAR|rand|"
    r"SEK|kronor|couronnes?|(?:Swiss\s+)?francs?|CHF|MAD|DH|dirhams?|TND|DT|DZD|KWD|dinars?|EGP|LBP|AED|SAR|QAR|OMR|riyals?|rials?|"
    r"CNY|RMB|JPY|円|KRW|₩|원|THB|฿|บาท|درهم\w*|دينار\w*|جنيه\w*|ليرة|ريال|"
    r"欧元|瑞士法郎|瑞郎|法郎|巴西雷亚尔|雷亚尔|印度尼西亚盾|印尼盾|印尼卢比|土耳其里拉|里拉|越南盾|波兰兹罗提|兹罗提|"
    r"墨西哥比索|阿根廷比索|智利比索|哥伦比亚比索|菲律宾比索|比索|秘鲁索尔|索尔|"
    r"美元|美金|英镑|新加坡元|新元|澳大利亚元|澳元|加拿大元|加元|人民币|日元|日圆|韩元|韩圆|泰铢|马来西亚林吉特|林吉特|"
    r"迪拉姆|阿联酋迪拉姆|摩洛哥迪拉姆|第纳尔|突尼斯第纳尔|科威特第纳尔|阿尔及利亚第纳尔|埃及镑|黎巴嫩镑|"
    r"里亚尔|沙特里亚尔|卡塔尔里亚尔|阿曼里亚尔|卢布|俄罗斯卢布|瑞典克朗|克朗|"
    r"哈萨克斯坦坚戈|坚戈|吉尔吉斯斯坦索姆|索姆|以色列新谢克尔|新谢克尔|谢克尔|印度卢比|巴基斯坦卢比|孟加拉塔卡|塔卡|南非兰特|兰特|尼日利亚奈拉|奈拉)"
)


def _collapse_dot_thousands(text: str) -> str:
    # 1. Two or more 3-digit dot groups (e.g. 1.000.000, 50.000.000.) are unambiguously thousands.
    text = re.sub(
        r"(?<![\d.])([1-9]\d{0,2}(?:\.\d{3}){2,})(?!\d|\.\d)",
        lambda m: m.group(1).replace(".", ""),
        text,
    )

    # 2. Single 3-digit dot group (e.g. 50.000 Euro, Rp 5.000., 10.000): treat as thousands when
    #    adjacent to currency markers or when >= 10.000 with exact .000/.500 triad.
    def _repl_single(m: re.Match[str]) -> str:
        head, tail = m.group(1), m.group(2)
        before = text[max(0, m.start() - 16) : m.start()]
        after = text[m.end() : m.end() + 34]
        after_no_cents = re.sub(r"^,\d{1,2}(?!\d)\s*", " ", after)
        if (
            _CURRENCY_DOT_PREFIX_RE.search(before)
            or _CURRENCY_DOT_SUFFIX_RE.search(after)
            or _CURRENCY_DOT_SUFFIX_RE.search(after_no_cents)
        ):
            return head + tail
        if len(head) >= 2 and tail == "000":
            return head + tail
        return m.group(0)

    return re.sub(r"(?<![\d.])([1-9]\d{0,2})\.(\d{3})(?!\d|\.\d)", _repl_single, text)


def _convert_currency_comma_decimals(text: str) -> str:
    """Convert European/Indonesian/Brazilian comma-decimal cents adjacent to currency markers
    (e.g. '5000,00 EUR' -> '5000.00 EUR', 'Rp 5000,50' -> 'Rp 5000.50')."""

    def _repl(m: re.Match[str]) -> str:
        before = text[max(0, m.start() - 16) : m.start()]
        after = text[m.end() : m.end() + 28]
        if _CURRENCY_DOT_PREFIX_RE.search(before) or _CURRENCY_DOT_SUFFIX_RE.search(after):
            return f"{m.group(1)}.{m.group(2)}"
        return m.group(0)

    return re.sub(r"(?<![\d.,])(\d+),(\d{1,2})(?!\d)", _repl, text)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    # Arabic, Persian, Bengali, and Thai decimal digits map deterministically to ASCII 0-9.
    text = text.translate(
        str.maketrans(
            "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹০১২৩৪৫৬৭৮৯๐๑๒๓๔๕๖๗๘๙٫٬",
            "0123456789012345678901234567890123456789.,",
        )
    )
    # Collapse space / NBSP / narrow-NBSP thousands separators ("20 000 000 EUR", "50\u00a0000").
    text = re.sub(
        r"(?<![\d/.\-])([1-9]\d{0,2})((?:[ \u00a0\u202f]\d{3})+)(?![\d/.\-])",
        lambda m: m.group(1) + re.sub(r"[ \u00a0\u202f]+", "", m.group(2)),
        text,
    )
    text = _collapse_dot_thousands(text)
    text = _convert_currency_comma_decimals(text)
    # Convert comma decimals before percentage / metric units (incl. "yüzde 2,5" / "ร้อยละ 2,5").
    unit_tail = rf"%|{_PERCENT_WORDS_AFTER}|°|[kK]\b|kg\b|g\b|mg\b|m\b|cm\b|mm\b|km\b|l\b|ml\b"
    text = re.sub(
        rf"(?<!\d)(\d+),(\d{{1,2}})(?=\s*(?:{unit_tail}))",
        r"\1.\2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"((?:百分之|yüzde|ร้อยละ)\s*)(\d+),(\d{1,2})(?!\d)",
        r"\1\2.\3",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(rf"٪\s*({NUMBER})", r"\1%", text)
    text = text.replace("٪", "%")
    text = re.sub(rf"百分之\s*({NUMBER})", r"\1%", text)
    text = re.sub(rf"(?<![A-Za-zÀ-ž])yüzde\s*({NUMBER})", r"\1%", text, flags=re.IGNORECASE)
    text = re.sub(rf"ร้อยละ\s*({NUMBER})", r"\1%", text)
    # 多语种“40 percent / 40 pour cent / 40 Prozent / 40 por ciento”与“40%”是同一个数
    return re.sub(
        rf"({NUMBER})\s*(?:{_PERCENT_WORDS_AFTER})(?![A-Za-zÀ-žА-Яа-яЁё])",
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
    "EGP": r"EGP|جنيه(?:اً|ا)?\s+مصري(?:اً|ا)?|livres?\s+[ée]gyptiennes?|Egyptian\s+pounds?|埃及镑",
    "TND": r"TND|دينار(?:اً|ا)?\s+تونسي(?:اً|ا)?|dinars?\s+tunisiens?|突尼斯第纳尔",
    "MAD": r"MAD|درهم(?:اً|ا)?\s+مغربي(?:اً|ا)?|dirhams?\s+marocains?|摩洛哥迪拉姆",
    "LBP": r"LBP|ليرة\s+لبنانية|livres?\s+libanaises?|黎巴嫩镑",
    "CNY": r"CNY|RMB|人民币",
    "JPY": r"JPY|(?<![一-鿿])円|日元|日圆",
    "KRW": r"₩|KRW|(?<![가-힣])원(?![가-힣])|韩元|韩圆",
    "THB": r"฿|THB|บาท|泰铢",
    "MYR": r"RM|MYR|ringgit|马来西亚林吉特|林吉特",
    "IDR": r"Rp\.?|IDR|rupiah|印度尼西亚盾|印尼盾|印尼卢比",
    "VND": r"₫|VND|越南盾",
    "PHP": r"₱|PHP|菲律宾比索",
    "AED": r"AED|阿联酋迪拉姆",
    "SAR": r"SAR|沙特里亚尔",
    "QAR": r"QAR|卡塔尔里亚尔",
    "KWD": r"KWD|科威特第纳尔",
    "OMR": r"OMR|阿曼里亚尔",
    "DZD": r"DZD|阿尔及利亚第纳尔",
    "RUB": r"₽|RUB|рубл(?:ей|я|ь)|俄罗斯卢布|卢布",
    "KZT": r"₸|KZT|哈萨克斯坦坚戈|坚戈",
    "KGS": r"KGS|吉尔吉斯斯坦索姆|索姆",
    "TRY": r"₺|TRY|TL|t[üu]rk\s+liras\w*|liras?\s+turques?|土耳其里拉",
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
    "PLN": r"zł|PLN|złot(?:y|ych|ego)|波兰兹罗提|兹罗提",
    "SEK": r"SEK|kronor|瑞典克朗",
    "CHF": r"CHF|francs?\s+suisses?|Swiss\s+francs?|瑞士法郎|瑞郎",
    "DOLLAR_UNSPECIFIED": r"(?<![A-Za-z])\$|dollars?",
}

_ALPHA_BOUND = r"[A-Za-zÀ-ÖØ-öø-ÿĀ-žА-Яа-яЁё\u0600-\u06ff]"


def amounts(text: str) -> set[tuple[str, str]]:
    text = normalize(text)
    found = set()
    for currency, marker in CURRENCIES.items():
        # Codes and alphabetic currency names must not match inside words (e.g. "Made" != MAD,
        # Polish "złącza" != zł), while allowing Chinese units directly next to Chinese prose.
        marker = rf"(?<!{_ALPHA_BOUND})(?:{marker})(?!{_ALPHA_BOUND})"
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
            "january janvier يناير جانفي gennaio enero janeiro januar januari stycznia ocak января",
            "february février فيفري فبراير febbraio febrero fevereiro "
            "februar februari lutego şubat февраля شباط",
            "march mars مارس آذار اذار marzo março märz maart marca maret mart марта",
            "april avril أبريل افريل نيسان aprile abril kwietnia nisan апреля",
            "may mai مايو ماي أيار ايار maggio mayo maio mei maja mayıs mayis maj мая",
            "june juin يونيو جوان حزيران giugno junio junho juni czerwca haziran июня",
            "july juillet يوليو يوليوز جويلية تموز luglio julio julho juli lipca temmuz июля",
            "august août أغسطس اوت غشت آب agosto augustus sierpnia agustus ağustos agustos "
            "augusti августа",
            "september septembre سبتمبر شتنبر أيلول ايلول settembre septiembre setembro "
            "września eylül eylul сентября",
            "october octobre أكتوبر اكتوبر ottobre octubre outubro "
            "oktober października ekim октября",
            "november novembre نوفمبر نونبر noviembre novembro listopada kasım kasim ноября",
            "december décembre ديسمبر دجنبر dicembre diciembre dezembro dezember "
            "grudzień grudnia desember aralık aralik декабря",
        ),
        1,
    )
    for word in group.split()
}
MONTHS.update(
    {
        "كانون الثاني": 1,
        "تشرين الأول": 10,
        "تشرين الاول": 10,
        "تشرين الثاني": 11,
        "كانون الأول": 12,
        "كانون الاول": 12,
    }
)


def _valid(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


_DMY_DATE_RE = (
    r"(?<![\d.])(\d{1,2})(?:st|nd|rd|th|er|º|°|\.)?(?:\s+(?:day\s+of|de|del))?\s+"
    r"(__MONTHS__)(?:\s+(?:de|del|tahun|سنة|لسنة|عام))?\s*[,،]?\s*(\d{4})(?!\d)"
)


def _fold_date_text(text: str) -> str:
    # Collapse Turkish uppercase 'İ' casefold dot ('i\u0307' -> 'i') and dotless 'ı' -> 'i'
    # so both 'EKİM'/'ekim' and 'MAYIS'/'mayıs' fold deterministically.
    return normalize(text).casefold().replace("i\u0307", "i").replace("ı", "i")


_CJK_KO_DATE_RE = r"(\d{4})\s*[年년]\s*(\d{1,2})\s*[月월]\s*(\d{1,2})\s*[日일]"
_VN_DATE_RE = r"(?:ngày\s*)?(\d{1,2})\s*tháng\s*(\d{1,2})\s*năm\s*(\d{4})(?!\d)"


def dates(text: str) -> set[tuple[int, int, int]]:
    """Complete, real calendar dates only. "1.0 October 2020" is a version number then a month,
    not the 0th of October, and "31 February 2020" is not a date at all."""
    text = _fold_date_text(text)
    found = set()
    for year, month, day in re.findall(_CJK_KO_DATE_RE, text):
        found.add((int(year), int(month), int(day)))
    for day, month, year in re.findall(_VN_DATE_RE, text):
        found.add((int(year), int(month), int(day)))
    for year, month, day in re.findall(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", text):
        found.add((int(year), int(month), int(day)))
    month_words = "|".join(sorted(MONTHS, key=len, reverse=True))
    for day, month, year in re.findall(_DMY_DATE_RE.replace("__MONTHS__", month_words), text):
        found.add((int(year), MONTHS[month], int(day)))
    for month, day, year in re.findall(rf"({month_words})\s+(\d{{1,2}}),?\s+(\d{{4}})(?!\d)", text):
        found.add((int(year), MONTHS[month], int(day)))
    return {d for d in found if _valid(*d)}


def separate_explicit_dates(text: str):
    """Analysis-only comparison: complete valid dates are identities, not spare numbers.

    Does not change originals, translations or the translation-review protocol.
    Ambiguous/partial dates are not inferred. Invalid explicit dates stay visible.
    """
    normalized = _fold_date_text(text)
    month_words = "|".join(sorted(MONTHS, key=len, reverse=True))
    patterns = [
        (rf"(?<!\d){_CJK_KO_DATE_RE}", "ymd"),
        (rf"(?<!\d){_VN_DATE_RE}", "dmy_num"),
        (r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", "ymd"),
        (_DMY_DATE_RE.replace("__MONTHS__", month_words), "dmy"),
        (rf"({month_words})\s+(\d{{1,2}}),?\s+(\d{{4}})(?!\d)", "mdy"),
    ]
    values, spans, invalid = set(), [], []
    for pattern, order in patterns:
        for match in re.finditer(pattern, normalized):
            a, b, c = match.groups()
            year, month, day = (
                (int(a), int(b), int(c))
                if order == "ymd"
                else (int(c), int(b), int(a))
                if order == "dmy_num"
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
