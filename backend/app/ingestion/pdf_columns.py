"""Reading order for the text layer of PDF pages set in columns.

pdfium returns a page's text in the order the file stores it. Gazettes and many official
publications store a two-column page line by line across both columns, so a sentence of the left
column is followed by the line of the right column at the same height and the clauses that are cut
from this text mix two articles. This regroups the page's text rectangles into line segments, looks
for a gutter no line crosses (the same test the image recogniser's output goes through) and reads
column after column. A page without such a gutter returns None and keeps pdfium's own text, so
ordinary single-column files are not touched at all.
"""

from __future__ import annotations

from app.ingestion.ocr_layout import find_gutter, reading_order

MIN_PAGE_CHARS = 600  # a short page cannot show a gutter reliably
MIN_RECTS = 40
MAX_RECTS = 20000
SEGMENT_GAP = 0.022  # a horizontal gap wider than this share of the page width ends a segment
LINE_TOLERANCE = 0.6  # lines are told apart by this share of the typical glyph height

Rect = tuple[float, float, float, float]  # left, bottom, right, top in PDF space


def _lines(rects: list[Rect]) -> list[list[Rect]]:
    """Group glyph rectangles into lines, top of the page first."""
    heights = sorted(t - b for (_, b, _, t) in rects)
    if not heights:
        return []
    typical = heights[len(heights) // 2]
    ordered = sorted(rects, key=lambda r: -((r[1] + r[3]) / 2))
    lines: list[list[Rect]] = []
    centre = 0.0
    for r in ordered:
        c = (r[1] + r[3]) / 2
        if not lines or abs(c - centre) > LINE_TOLERANCE * typical:
            lines.append([r])
            centre = c
        else:
            lines[-1].append(r)
            centre = sum((x[1] + x[3]) / 2 for x in lines[-1]) / len(lines[-1])
    return lines


def _segments(rects: list[Rect], width: float) -> list[Rect]:
    """Runs of text on one line that are not separated by a wide gap."""
    out: list[Rect] = []
    for line in _lines(rects):
        line.sort(key=lambda r: r[0])
        x0, b, x1, t = line[0]
        for nx0, nb, nx1, nt in line[1:]:
            if nx0 - x1 > SEGMENT_GAP * width:
                out.append((x0, b, x1, t))
                x0, b, x1, t = nx0, nb, nx1, nt
            else:
                b, x1, t = min(b, nb), max(x1, nx1), max(t, nt)
        out.append((x0, b, x1, t))
    return out


def _mostly_arabic(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 50:
        return False
    return sum(1 for c in letters if "؀" <= c <= "ۿ") / len(letters) > 0.5


def column_text(page, text_page) -> str | None:
    """The page's text in column reading order, or None when the page is not set in columns."""
    try:
        return _column_text(page, text_page)
    except Exception:  # noqa: BLE001 - an ordering aid must never make a file unreadable
        return None


def _column_text(page, text_page) -> str | None:
    count = text_page.count_rects()
    if count < MIN_RECTS or count > MAX_RECTS:
        return None
    width, height = page.get_size()
    rects = [text_page.get_rect(k) for k in range(count)]
    rects = [r for r in rects if r[2] > r[0] and r[3] > r[1]]
    segments = _segments(rects, width)
    boxes = [(x0, height - t, x1, height - b) for (x0, b, x1, t) in segments]
    if find_gutter(boxes, width) is None:
        return None
    texts = []
    for x0, b, x1, t in segments:
        pad = (t - b) * 0.12  # stay out of the neighbouring line
        texts.append(text_page.get_text_bounded(x0 - 0.5, b + pad, x1 + 0.5, t - pad).strip())
    order = reading_order(boxes, width, rtl=_mostly_arabic(" ".join(texts)))
    text = "\n".join(texts[i] for i in order if texts[i])
    return text if len(text) >= MIN_PAGE_CHARS else None
