"""扫描页识别质量：这一页有没有读出成段、通顺的文字。

为什么需要它：只数“条号”会误判。埃及第 151 号法的扫描件，条标题用书法字体、以序数词书写，
最容易漏读；而正文其实读出来了。实测同一份文件，原有引擎 31 页里 25 页整页是空的，
PaddleOCR 读出了 29 页，
但按条号数量两者只相差 3 对 8。所以择优必须先看“整页读空了多少”，再看条号。

“通顺”用阿拉伯语高频功能词（في / من / على / أن / التي …）在本页词里的占比来近似：
读成乱码的页，这类词会明显偏少；读通顺的页，占比稳定在一成以上。法语、英语同理。
这是一个粗但稳的代理指标，不需要标准答案。
"""

from __future__ import annotations

import re

from app.ingestion.article_numbers import article_numbers, normalize_arabic

MIN_WORDS = 80  # 一页正常的法律文字至少有几百个词；低于此值多半是整页没读出来
MIN_FUNCTION_RATIO = 0.10

_FUNCTION_WORDS = {
    "ar": set(
        normalize_arabic(
            "في من على ان التي الذي هذا الي عن او كل ما لا اذا وفقا بشان القانون البيانات الشخصيه"
        ).split()
    ),
    "fr": set("de la le les des du et en un une que qui dans pour par sur au aux est sont".split()),
    "en": set("the of and to in a is that for be or by with as on this shall any".split()),
}
_WORD = {
    "ar": re.compile(r"[ء-ي]{2,}"),
    "fr": re.compile(r"[a-zA-ZÀ-ÿ]{1,}"),
    "en": re.compile(r"[a-zA-Z]{1,}"),
}


def _lang(language: str) -> str:
    lang = (language or "").split("-")[0].lower()
    return "ar" if lang in ("ar", "fa", "ur") else lang if lang in ("fr", "en") else "en"


def page_is_usable(text: str, language: str, min_words: int = MIN_WORDS) -> bool:
    lang = _lang(language)
    body = normalize_arabic(text) if lang == "ar" else (text or "").lower()
    words = _WORD[lang].findall(body)
    if len(words) < min_words:
        return False
    return sum(1 for w in words if w in _FUNCTION_WORDS[lang]) / len(words) >= MIN_FUNCTION_RATIO


def document_quality(pages: list[str], language: str) -> tuple[int, int, int]:
    """(可用页数, 识别出的不同条号个数, 字符数)：按这个顺序比较，越大越好。

    可用页数在最前：整页读空是最严重的缺陷。其次是条号数量：条号是引用定位的依据。最后才比字符数。"""
    usable = sum(1 for p in pages if page_is_usable(p, language))
    numbers = len(article_numbers("\n".join(pages), language))
    return usable, numbers, sum(len(p) for p in pages)
