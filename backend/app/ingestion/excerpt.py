"""Cut a gazette page that carries several instruments down to the one the document is about.

Official gazettes print instruments one after another, so an excerpt page often starts in the
middle of another decree and ends in the next. The archived file stays the whole page; only the
text that becomes clauses is cut: from where the instrument's own heading starts to where the next
instrument's heading starts. The start is given by the instrument's official number (``2008-3026``)
or by an explicit pattern, and the end is the next heading line of another instrument.
"""

from __future__ import annotations

import re

_NUMBER = re.compile(r"^\s*(?:n[°o]?\s*)?(\d{2,4})\s*[-–—/]\s*(\d{1,4})\s*$", re.IGNORECASE)
# a heading line that opens an instrument: "Décret n° 2008-3026 du …", "Arrêté du ministre …"
_HEADING = re.compile(
    r"^\s*(?:d[ée]cret(?:-loi)?|arr[êe]t[ée]|loi(?:\s+organique)?|ordonnance)\b"
    r"(?:\s+n[°o]?\s*(?P<num>\d{2,4}\s*[-–—/]\s*\d{1,4}))?",
    re.IGNORECASE,
)


_DATED = re.compile(r"\s+du\s+(?:\d{1,2}(?:er)?|…)\s+\S+\s+\d{4}", re.IGNORECASE)
# a line that stops on a connector or a comma has not finished its sentence
_CONTINUES = re.compile(
    r"(?:,|\b(?:le|la|les|l'|du|de|des|par|et|au|aux|n°|n|loi|d[ée]cret|modifi[ée]|compl[ée]t[ée]))$",
    re.IGNORECASE,
)


def start_pattern(official_number: str) -> re.Pattern[str] | None:
    """A pattern for ``official_number`` as gazettes print it (``n° 2008-3026``)."""
    m = _NUMBER.match(official_number or "")
    if not m:
        return None
    year, serial = m.groups()
    return re.compile(rf"n[°o]?\s*{year}\s*[-–—/]\s*{serial}\b", re.IGNORECASE)


def _norm_num(text: str) -> str:
    return re.sub(r"\s+", "", text or "").replace("–", "-").replace("—", "-").replace("/", "-")


def cut_excerpt(
    pages: list[str], *, official_number: str = "", start: str = "", end: str = ""
) -> list[str]:
    """The pages reduced to the wanted instrument; unchanged when the start cannot be found."""
    pattern = re.compile(start, re.IGNORECASE) if start else start_pattern(official_number)
    if pattern is None:
        return pages
    text = chr(10).join(pages)
    # the start is a heading line, not a "vu le décret n° …" reference inside another text
    begin = None
    for m in pattern.finditer(text):
        line_start = text.rfind(chr(10), 0, m.start()) + 1
        head = text[line_start : m.end()]
        if _HEADING.match(head) or start:
            begin = line_start
            break
    if begin is None:
        return pages
    own = _norm_num(official_number)
    rest = text[begin:]
    stop = len(rest)
    if end:
        e = re.search(end, rest, re.IGNORECASE)
        if e:
            stop = e.start()
    else:
        first_line_end = rest.find(chr(10)) + 1 or len(rest)
        body = rest[first_line_end:]
        for m in re.finditer(r"(?m)^.*$", body):
            h = _HEADING.match(m.group(0))
            if not (h and h.group("num") and (not own or _norm_num(h.group("num")) != own)):
                continue
            if _DATED.match(m.group(0)[h.end() :]) is None:
                continue  # 引用别的令：“…modifié par le Décret n° 2002-1 ,” 后面不跟“du <日期>”
            before = body[: m.start()].rstrip()
            if _CONTINUES.search(before):
                continue  # 上一行没写完：这一行是折行后的引用，不是新法令的标题
            stop = first_line_end + m.start()
            break
    if stop <= 0:
        return pages
    # keep the page structure (and so the page numbers clauses are located by): pages before the
    # start and after the end become empty, the first and last kept pages are trimmed
    lo, hi = begin, begin + stop
    out: list[str] = []
    offset = 0
    for page in pages:
        a, b = offset, offset + len(page)
        out.append(page[max(lo, a) - a : min(hi, b) - a] if b > lo and a < hi else "")
        offset = b + 1  # the joining newline
    return out
