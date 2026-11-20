# ruff: noqa: E501
"""Clause text as shown to a reader or quoted in a document — the stored text stays as it was.

Gazette scans and PDFs carry the running header of each page (“الوقائع المصرية — العدد ٢٤٤ تابع …”,
“Journal officiel de la République tunisienne — N° 61”) and, in some files, the gazette's table of
contents glued to the last article. Splitting the files again would mean re-translating and
re-extracting them; the reading view and the quotes drop these lines instead. A line is a running
header only when it is short and starts with the gazette's name (or is just an issue number), so a
sentence that mentions the gazette (“本法自在官方公报上公布之日起生效”) is never touched.
Evidence quotes are checked against the stored text, which is not changed.
"""

from __future__ import annotations

import re

_CONTENTS = re.compile(
    r"^\s*(?:(الفهرس|فهرس|المحتويات|المحتوى|محتويات|sommaire|table des mati[eè]res|contents|table of contents|目\s*录)\s*$|目\s*录\s+\d{1,2}\s+\S+)",
    re.IGNORECASE,
)
# 页眉：短、带数字（期号、页码）、不像一句话
_PAGE_HEADER = re.compile(r"^(?=.*[\d٠-٩])[^。.!?；;:：]{1,40}$")
_LEAD = r"^[\s\d٠-٩#\-–—|(（《]*"
_RUNNING = [
    re.compile(_LEAD + r"(الجريدة\s*الرسمية|الوقائع\s*المصرية)", re.U),
    re.compile(r"^\s*(العدد|عدد)\s*[\d٠-٩]*\s*(مكرر|تابع)?\s*[\d٠-٩]*\s*$", re.U),
    re.compile(_LEAD + r"(journal\s+officiel|bulletin\s+officiel)\b", re.IGNORECASE),
    re.compile(_LEAD + r"((突尼斯共和国|埃及|黎巴嫩|摩洛哥)?(官方公报|政府公报)|《(埃及)?公报》)"),
]
_SENTENCE = re.compile(r"本法|本条例|本决定|公布|生效|援引|执行|يعمل|ينشر|يُنشر|publié|entre en vigueur|sera publié", re.IGNORECASE)
_MAX = 70


_DANGLING_GAZETTE_INTRO = re.compile(
    r"(?:\b(?:au|dans le|du|in the|at the)|(?:^|\s)(?:في|فى|بـ|بالجريدة))\s*$",
    re.IGNORECASE,
)
_INLINE_ZH_TOC = re.compile(
    r"^(.{20,300}?)\s+目\s*录\s+(?=(?:序言|前言|引言|1[\s.、]|一[、\s]|第[一1][章编部分]|\d+\s+\S))"
)
_CLAUSE_HEAD = re.compile(
    r"(?m)^\s*(?:第\s*[(（\d一二三四五六七八九十百]|المادة|الفصل|article\b|section\b)",
    re.IGNORECASE,
)


def _running_header(line: str, prev_line: str = "") -> bool:
    s = line.strip()
    if not s or len(s) > _MAX or _SENTENCE.search(s):
        return False
    if re.search(r"[.。]\s*$", s):
        return False
    if prev_line and _DANGLING_GAZETTE_INTRO.search(prev_line.strip()):
        return False
    return any(p.search(s) for p in _RUNNING)


def cut_at_contents(text: str) -> str:
    """Drop a gazette table of contents glued to the end of a clause (with the header lines before it)."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if i and _CONTENTS.match(line):
            kept = lines[:i]
            while kept and (len(kept[-1].strip()) <= 3 or _PAGE_HEADER.match(kept[-1].strip())):
                kept.pop()
            candidate = "\n".join(kept).rstrip()
            if len(candidate) >= 120 or (len(candidate) >= 12 and _CLAUSE_HEAD.search(candidate)):
                return candidate
        m_in = _INLINE_ZH_TOC.match(line)
        if m_in and (i > 0 or len(text) >= 800):
            kept = [*lines[:i], m_in.group(1).rstrip()]
            candidate = "\n".join(kept).rstrip()
            if len(candidate) >= 120 or (len(candidate) >= 12 and _CLAUSE_HEAD.search(candidate)):
                return candidate
    return text


_CTRL_PUA = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ue000-\uf8ff]")


def for_reading(text: str | None) -> str:
    if not text:
        return text or ""
    from app.ingestion.noise import clean_text, clean_translation

    text = _CTRL_PUA.sub("", text.replace("\ufffd", ""))
    text = cut_at_contents(text)
    raw_lines = text.split("\n")
    lines: list[str] = []
    for line in raw_lines:
        prev_nonempty = next((x for x in reversed(lines) if x.strip()), "")
        if _running_header(line, prev_nonempty):
            continue
        lines.append(line)
    text = "\n".join(lines).strip("\n") or text
    # 网页导航、下载链接、地址电话、下一章标题；中文译文另去句中的公报页眉和阿拉伯文残片（见 noise）
    cleaned = clean_translation(text) if _CJK.search(text[:200]) else clean_text(text)
    return _CTRL_PUA.sub("", cleaned)


_CJK = re.compile(r"[一-鿿]")
