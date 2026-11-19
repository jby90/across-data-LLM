"""Reading order for recognised text lines on pages that are set in columns.

The recogniser returns lines ordered by their top edge, so on a two-column page (government
gazettes) a line of the left column is followed by the line of the right column that happens to sit
at the same height, and sentences are cut in half. This puts the lines back in reading order: find
a vertical gutter that (almost) no line crosses, and within each stretch of the page between
full-width lines (titles, dates) read one column to the end before the next. A page without such a
gutter keeps the recogniser's order. Right-to-left languages read the right column first.
Nothing here depends on a language or a country; it only looks at where the lines are.
"""

from __future__ import annotations

from collections.abc import Sequence

Box = tuple[float, float, float, float]  # x1, y1, x2, y2

MIN_LINES_PER_COLUMN = 4
MAX_CROSSING_SHARE = 0.12  # lines allowed to cross the gutter (titles, signatures)
GUTTER_SEARCH = (0.30, 0.70)  # where the gutter may lie, as a share of the page width
MIN_GUTTER_FRACTION = 0.012  # the clear strip must be at least this share of the page width


def find_gutter(boxes: Sequence[Box], width: float) -> float | None:
    """The x of the vertical gutter, or None when the page is not set in two columns."""
    if len(boxes) < 2 * MIN_LINES_PER_COLUMN or width <= 0:
        return None
    lo, hi = GUTTER_SEARCH[0] * width, GUTTER_SEARCH[1] * width
    step = max(width / 400, 1.0)
    best: tuple[int, float] | None = None
    x = lo
    while x <= hi:
        crossing = sum(1 for b in boxes if b[0] < x < b[2])
        if best is None or crossing < best[0]:
            best = (crossing, x)
        x += step
    if best is None or best[0] > MAX_CROSSING_SHARE * len(boxes):
        return None
    # widen to the clear strip around the best x: the gutter must be a real gap, not a hairline
    left_edges = [b[2] for b in boxes if b[2] <= best[1]]
    right_edges = [b[0] for b in boxes if b[0] >= best[1]]
    if not left_edges or not right_edges:
        return None
    if min(right_edges) - max(left_edges) < MIN_GUTTER_FRACTION * width:
        return None
    left = sum(1 for b in boxes if b[2] <= best[1])
    right = sum(1 for b in boxes if b[0] >= best[1])
    if left < MIN_LINES_PER_COLUMN or right < MIN_LINES_PER_COLUMN:
        return None
    return (max(left_edges) + min(right_edges)) / 2


def reading_order(boxes: Sequence[Box], width: float, *, rtl: bool = False) -> list[int]:
    """Indexes of ``boxes`` in reading order."""
    by_top = sorted(range(len(boxes)), key=lambda i: (boxes[i][1], boxes[i][0]))
    gutter = find_gutter(boxes, width)
    if gutter is None:
        return by_top
    out: list[int] = []
    left: list[int] = []
    right: list[int] = []

    def flush() -> None:
        first, second = (right, left) if rtl else (left, right)
        out.extend(first)
        out.extend(second)
        left.clear()
        right.clear()

    for i in by_top:
        x1, _, x2, _ = boxes[i]
        if x1 < gutter < x2:  # a full-width line closes the columns above it
            flush()
            out.append(i)
        elif x2 <= gutter:
            left.append(i)
        else:
            right.append(i)
    flush()
    return out
