"""How a regulation is read: its clauses in reading order, one article per unit.

Two kinds of stored rows are not articles of their own and are put back where they belong:

* the pieces of an over-long article. Long articles were once cut into pieces of about 2,400
  characters; each piece was stored as a row numbered ``<article>.<k>`` (``2.1``, ``2.2`` …) with
  the article's heading and page, and appended after the last article of the document. Read as
  they are stored, the “Interpretation” article stops in the middle of its definitions and the
  rest of the definitions turn up after the schedules. A piece is recognised by its number, by
  carrying the same heading and page as an earlier row, and by not starting with its own number
  (a genuine paragraph ``2.1`` starts with “2.1”).
* a short unnumbered line such as an amendment note (``[40/2020]``) printed under an article.

Rows keep their ids (evidence, translations and citations point at them); only the view changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

_PIECE = re.compile(r"^(.+)\.(\d+)$")
_NOTE_MAX = 60  # 不超过这个长度、没有条号和标题的一行（多是修订注记），并到上一条后面


class Row(Protocol):
    id: object
    ordinal: int
    clause_number: str | None
    heading: str | None
    page_start: int | None
    original_text: str


@dataclass
class Unit:
    rows: list = field(default_factory=list)

    @property
    def head(self):
        return self.rows[0]

    @property
    def ids(self) -> list:
        return [r.id for r in self.rows]


def _starts_with_number(row: Row) -> bool:
    return (row.original_text or "").lstrip().startswith(row.clause_number or "\0")


def pieces(rows: list[Row]) -> dict:
    """Row id → the row (article) it is a piece of."""
    rows = sorted(rows, key=lambda r: r.ordinal)
    out: dict = {}
    i = 0
    while i < len(rows):
        row = rows[i]
        m = _PIECE.match(row.clause_number or "")
        if not m or m.group(2) != "1" or _starts_with_number(row):
            i += 1
            continue
        base = m.group(1)
        run, j = [row], i + 1
        while j < len(rows) and (rows[j].clause_number or "") == f"{base}.{len(run) + 1}":
            run.append(rows[j])
            j += 1
        same = [
            r
            for r in rows[:i]
            if r.id not in out
            and (r.heading or "") == (row.heading or "")
            and r.page_start == row.page_start
        ]
        # 先找条号正好是 base 的那条（指南的段号“19.8”本身也带小数点）；找不到时取同页同标题、
        # 本身不像片段的前一行（如没有条号的目录）
        earlier = [r for r in same if (r.clause_number or "") == base] or [
            r for r in same if not _PIECE.match(r.clause_number or "") and not _is_note(r)
        ]
        if earlier and row.ordinal > earlier[-1].ordinal + 1:
            for r in run:
                out[r.id] = earlier[-1]
        i = j
    return out


def _is_note(row: Row) -> bool:
    text = (row.original_text or "").strip()
    return not row.clause_number and not row.heading and len(text) <= _NOTE_MAX and row.ordinal > 0


def reading_units(rows: list[Row]) -> list[Unit]:
    """The document's articles in reading order, each with the rows that make it up."""
    rows = sorted(rows, key=lambda r: r.ordinal)
    owner = pieces(rows)
    units: list[Unit] = []
    by_head: dict = {}
    for row in rows:
        if row.id in owner:
            continue
        if (
            not units
            and not row.clause_number
            and len(rows) > 1
            and (row.original_text or "").lstrip().startswith("设为首页加入收藏")
        ):
            continue
        if units and _is_note(row):
            units[-1].rows.append(row)
            continue
        unit = Unit([row])
        by_head[row.id] = unit
        units.append(unit)
    for row in rows:
        head = owner.get(row.id)
        if head is not None and head.id in by_head:
            unit = by_head[head.id]
            # 各段排在本条原有内容之后、注记之前（注记总在条文末尾）
            at = next((k for k, r in enumerate(unit.rows) if k and _is_note(r)), len(unit.rows))
            unit.rows.insert(at, row)
        elif head is not None:
            unit = Unit([row])
            by_head[row.id] = unit
            units.append(unit)
    return units


def joined(texts: list[str]) -> str:
    return "\n".join(t.strip("\n") for t in texts if t)


_AR_UNITS = {
    "الأولى": 1,
    "الأول": 1,
    "الحادية": 1,
    "الحادي": 1,
    "الثانية": 2,
    "الثاني": 2,
    "الثالثة": 3,
    "الثالث": 3,
    "الرابعة": 4,
    "الرابع": 4,
    "الخامسة": 5,
    "الخامس": 5,
    "السادسة": 6,
    "السادس": 6,
    "السابعة": 7,
    "السابع": 7,
    "الثامنة": 8,
    "الثامن": 8,
    "التاسعة": 9,
    "التاسع": 9,
    "العاشرة": 10,
    "العاشر": 10,
}
_AR_TENS = {
    "العشرون": 20,
    "الثلاثون": 30,
    "الأربعون": 40,
    "الخمسون": 50,
    "الستون": 60,
    "السبعون": 70,
    "الثمانون": 80,
    "التسعون": 90,
    "المائة": 100,
    "المئة": 100,
}


def _arabic_ordinal(text: str) -> int | None:
    total, seen = 0, False
    for word in re.sub(r"[()（）:：.]", " ", text).split():
        word = word[1:] if word.startswith("وال") else word
        if word in _AR_UNITS:
            total += _AR_UNITS[word]
        elif word in ("عشرة", "عشر"):
            total += 10
        elif word in _AR_TENS:
            total += _AR_TENS[word]
        else:
            return None
        seen = True
    return total if seen else None


def article_label(raw: str | None) -> str:
    """“43” → “第43条”, “premier” → “第1条”, “الحادية عشرة” / “(27)” → “第11条” / “第27条”; section
    numbers (“19.8”) as printed. Same rule as packages/shared/src/articleLabel.ts."""
    digits_map = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    value = (raw or "").strip().translate(digits_map)
    if not value:
        return ""
    if re.fullmatch(r"premier|1er|first", value, re.I):
        return "第1条"
    if re.fullmatch(r"(وحيدة|unique|sole)\s*[:：]?", value, re.I):
        return "单一条款"
    plain = re.sub(r"^[(（]\s*|\s*[)）]$", "", value)
    if (
        re.fullmatch(r"\d{1,4}[A-Za-z]?", plain)
        and not re.fullmatch(r"(?:19|20)\d{2}", plain)
        and (plain == value or value[0] in "(（")
    ):
        return f"第{plain}条"
    if re.fullmatch(r"[一二三四五六七八九十百零]+", value):
        return f"第{value}条"
    if re.search(r"[؀-ۿ]", value) and (n := _arabic_ordinal(value)):
        return f"第{n}条"
    return value
