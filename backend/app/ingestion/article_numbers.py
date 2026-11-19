"""条号识别：从识别出的文字里找出“第几条”，用来衡量一份法规识别得完整不完整。

法律条文的编号是连续的（第 1 条、第 2 条……），所以识别出了多少个不同的条号、缺了哪几个，
是比“识别了多少字”可靠得多的完整度信号：字符数会被页眉页脚、水印噪声干扰，条号不会。

为什么要先规范化：阿拉伯语里同一个字有很多写法——ي 和 ى、ا 和 أ 和 إ、ة 和 ه，
再加上变音符号和拉长符，OCR 输出里“第一条”可以有十几种拼法。逐个枚举写法永远追不上，
所以先把文字规范成统一形式再匹配。

埃及的法律用序数词写条号（المادة الأولى / الثانية / الثالثة …），突尼斯用“الفصل + 数字”，
摩洛哥是法语 Article + 数字，所以三种写法都要认。
"""

from __future__ import annotations

import re

# 东阿拉伯数字（U+0660–0669）和波斯数字（U+06F0–06F9）→ 西阿拉伯数字。必须先于去变音符，
# 否则宽泛的字符范围会把数字当成“变音符”一起删掉。
_DIGITS = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_DIGITS.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})
# 只列明确的“标记类”字符，不写大跨度范围：
#   U+064B–065F 变音符（tanwin、fatha、damma、kasra、shadda、sukun 等）
#   U+0670 上标 alef；U+0640 拉长符 tatweel；U+06D6–06DC / U+06DF–06E8 / U+06EA–06ED 古兰经标记
#   U+200E/200F/202A–202E 方向控制字符
_MARKS = re.compile("[ً-ٰٟـۖ-ۜ۟-۪ۨ-ۭ‎‏‪-‮]")
_LETTER_VARIANTS = {
    ord("أ"): "ا",
    ord("إ"): "ا",
    ord("آ"): "ا",
    ord("ٱ"): "ا",
    ord("ى"): "ي",
    ord("ة"): "ه",
    ord("ؤ"): "و",
    ord("ئ"): "ي",
}


def normalize_arabic(text: str) -> str:
    """把阿拉伯文规范成统一形式：数字转西阿拉伯数字，去变音符和拉长符，统一字母变体。"""
    t = (text or "").translate(_DIGITS)
    t = _MARKS.sub("", t)
    return t.translate(_LETTER_VARIANTS)


# 规范化之后的序数词（阴性，修饰“ماده / فصل”）。个位、十几、几十几由规则生成。
_ONES = {
    2: "ثانيه",
    3: "ثالثه",
    4: "رابعه",
    5: "خامسه",
    6: "سادسه",
    7: "سابعه",
    8: "ثامنه",
    9: "تاسعه",
}
_UNITS_WITH_TENS = {1: "حاديه", **_ONES}  # 十几 / 几十几里，“一”写作 حادية
_TENS = {20: "عشرون|عشرين", 30: "ثلاثون|ثلاثين", 40: "اربعون|اربعين", 50: "خمسون|خمسين"}


def _ordinals() -> dict[int, str]:
    out: dict[int, str] = {1: "اولي|اول", **_ONES, 10: "عاشره"}
    for n in range(11, 20):  # 十一 … 十九：الحاديه عشره、الثانيه عشره …
        out[n] = rf"{_UNITS_WITH_TENS[n - 10]}\s*عشر[ه]?"
    for tens, word in _TENS.items():  # 二十 … 五十九：العشرون、الحاديه والعشرون …
        out[tens] = word
        for u in range(1, 10):
            # “الحادية والعشرون”：连接词“و”和后面的十位数词合写，十位数词前面还有定冠词“ال”
            out[tens + u] = rf"{_UNITS_WITH_TENS[u]}\s*و\s*(?:ال)?(?:{word})"
    return out


# “ماده”之间可能夹进一个多余的字母（书法字体拉长被读成“مساده”），一并认作标题
_HEAD = r"(?:ال)?(?:م[ء-ي]?اده|فصل)"
_PREFIX = rf"(?m)^[\s\(\[«]*{_HEAD}\s*[:\-–(\[]?\s*\(?\s*"
_ARABIC_DIGIT = re.compile(_PREFIX + r"([0-9]{1,3})(?![0-9])")
# 序数词前面都带定冠词“ال”（الاولي、الثانيه、الثالثه …）。
# 每个模式自己带结尾约束：后面不能再跟字母，也不能再跟“عشر / و…ون”，
# 所以“الثانيه عشره”（十二）不会被“الثانيه”（二）吞掉，不依赖匹配顺序。
_ARABIC_ORDINAL = [
    (n, re.compile(_PREFIX + rf"(?:ال)?(?:{pat})(?![ء-ي])(?!\s*(?:عشر|و\s*(?:ال)?[ء-ي]))"))
    for n, pat in _ordinals().items()
]
_FRENCH = re.compile(r"(?m)^\s*Art(?:icle|\.)\s*(premier|\d{1,3})\b", re.IGNORECASE)


def arabic_article_numbers(text: str) -> set[int]:
    t = normalize_arabic(text)
    found = {int(m.group(1)) for m in _ARABIC_DIGIT.finditer(t)}
    # 每个标题行只归给一个序数词（结尾约束已经保证不会有两个同时匹配）
    for line in t.splitlines():
        for n, rx in _ARABIC_ORDINAL:
            if rx.match(line):
                found.add(n)
                break
    return found


def french_article_numbers(text: str) -> set[int]:
    out = set()
    for m in _FRENCH.finditer(text or ""):
        v = m.group(1)
        out.add(1 if v.lower() == "premier" else int(v))
    return out


def article_numbers(text: str, language: str) -> set[int]:
    lang = (language or "").split("-")[0].lower()
    if lang in ("ar", "fa", "ur"):
        return arabic_article_numbers(text)
    if lang == "fr":
        return french_article_numbers(text)
    return set()


def completeness(texts: list[str], language: str, *, expected_max: int | None = None) -> dict:
    """一份文档的识别完整度：识别出多少个条号、最高到第几条、缺了哪些。

    expected_max 是这部法官方的总条数。给得出时一定要传：它既是分母，也是上限——
    超过总条数的数字（页码、年份、文号被误当条号）直接丢掉；0 不是条号，也丢掉。

    不给 expected_max 时，只能用识别出的最高条号当总数，这会低估缺口（读不到最后几条时看不出来），
    此时按“与相邻条号的间隔”剔除孤立的大数：比前一个条号大 25 以上、后面又没有紧跟的条号，视为噪声。
    注意这个间隔规则会误杀真条号（条标题偶尔才读得出来时，相邻两个条号本来就可能隔得很远），
    所以有官方总条数时不用它。"""
    found = sorted(n for n in article_numbers("\n".join(texts), language) if n >= 1)
    if expected_max:
        cleaned = [n for n in found if n <= expected_max]
    else:
        cleaned = []
        for n in found:
            if cleaned and n - cleaned[-1] > 25 and not [m for m in found if n < m <= n + 3]:
                continue
            cleaned.append(n)
    top = expected_max or (max(cleaned) if cleaned else 0)
    have = set(cleaned)
    return {
        "found": len(have),
        "expected": top,
        "ratio": round(len(have) / top, 3) if top else 0.0,
        "missing": [n for n in range(1, top + 1) if n not in have],
    }
