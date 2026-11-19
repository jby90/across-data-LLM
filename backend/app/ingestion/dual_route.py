"""Two-route verification of a page read from a PDF's text layer.

Some PDFs carry a text layer with missing or displaced glyphs (typical for Arabic legal texts set in
calligraphic fonts): letters are dropped or split off from their words, and lines come out in a
different order, so the text reads "الطابع الإل أو ميزا" instead of "الطابع الإلزامي أو الاختياري".
The page image, read by OCR, has those words right but sometimes drops small marks such as digits
or mistakes one letter. Neither route is trusted alone.

The page is compared word by word, with each route's words in reading order:

  * a text-layer word that the OCR route also has is confirmed and kept as the source text;
  * a stretch of text-layer words the OCR route does not have ("the damaged span") is looked up in
    the OCR words: when the OCR route has a stretch that lines up with it on both sides, the OCR
    words replace the damaged span; numerals always come from the text layer;
  * a damaged span that cannot be lined up is left as the text layer has it and is reported, so a
    reader is told where the text could not be confirmed instead of it passing silently.

Nothing here is specific to a language, a country or a document.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
MIN_GAP_RATIO = 0.5  # 受损片段与候选 OCR 片段的骨架相似度下限


@dataclass
class Span:
    layer: str  # 文字层里受损的片段
    ocr: str | None  # 对上的 OCR 片段（没对上为 None）
    action: str  # replaced / unresolved


@dataclass
class Reconciliation:
    text: str
    confirmed: int = 0
    spans: list[Span] = field(default_factory=list)

    @property
    def replaced(self) -> int:
        return sum(1 for s in self.spans if s.action == "replaced")

    @property
    def unresolved(self) -> int:
        return sum(1 for s in self.spans if s.action == "unresolved")


def skeleton(word: str) -> str:
    """Letters only, without marks, tatweel or digits."""
    s = unicodedata.normalize("NFKD", word or "").casefold()
    return "".join(
        c for c in s if c.isalpha() and c != "ـ" and unicodedata.category(c) not in ("Mn", "Me")
    )


def _is_number(word: str) -> bool:
    return word.translate(_DIGITS).isdigit()


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text or "")


def _keys(words: list[str]) -> list[str]:
    return [skeleton(w) or w.translate(_DIGITS) for w in words]


def reconcile(text_layer: str, ocr: str) -> Reconciliation:
    """Reconcile one page (or one passage) read by both routes."""
    layer_words = _tokens(text_layer)
    ocr_words = _tokens(ocr)
    lk, ok = _keys(layer_words), _keys(ocr_words)
    matcher = SequenceMatcher(None, lk, ok, autojunk=False)
    out: list[str] = []
    result = Reconciliation(text="")
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out.extend(layer_words[i1:i2])
            result.confirmed += i2 - i1
            continue
        layer_span = layer_words[i1:i2]
        ocr_span = ocr_words[j1:j2]
        if tag == "delete" or not ocr_span:
            # text-layer words the image route does not have. Numerals are the thing the image
            # route is known to lose, so they stand on the text layer alone. A word of three or
            # more letters stands too (the image route may simply have skipped it); a one- or
            # two-letter piece is a split-off fragment and is dropped. Only when a fragment was
            # dropped does the span count as unresolved, because then a word may be missing.
            numbers = [w for w in layer_span if _is_number(w)]
            words = [w for w in layer_span if not _is_number(w) and len(skeleton(w)) > 2]
            fragments = [w for w in layer_span if not _is_number(w) and len(skeleton(w)) <= 2]
            out.extend(w for w in layer_span if w in numbers or w in words)
            if fragments:
                result.spans.append(Span(" ".join(layer_span), None, "unresolved"))
            continue
        if tag == "insert":
            continue  # words only the OCR route has (a line the layer lost): not added unseen
        # "replace": a damaged stretch of the text layer against the OCR reading of the same place
        similarity = SequenceMatcher(
            None, "".join(_keys(layer_span)), "".join(_keys(ocr_span)), autojunk=False
        ).ratio()
        numbers = [w for w in layer_span if _is_number(w)]
        ocr_numbers = [w for w in ocr_span if _is_number(w)]
        words_only = [w for w in ocr_span if not _is_number(w)]
        if (
            similarity >= MIN_GAP_RATIO
            and words_only
            and (not ocr_numbers or ocr_numbers == numbers)
        ):
            out.extend(numbers + words_only if numbers and not ocr_numbers else ocr_span)
            result.spans.append(Span(" ".join(layer_span), " ".join(ocr_span), "replaced"))
        else:
            out.extend(layer_span)
            result.spans.append(Span(" ".join(layer_span), " ".join(ocr_span), "unresolved"))
    result.text = " ".join(out)
    return result


def _window(clause_words: list[str], page_words: list[str]) -> list[str]:
    """The stretch of the page's image-route words that covers this clause.

    The clause is placed on the page by its sound words (words of three or more letters that
    appear in both): from the first to the last of them, widened a little at each end for words
    the text layer lost. This keeps every other clause on the page out of the comparison."""
    ckeys = {skeleton(w) for w in clause_words if len(skeleton(w)) > 2}
    positions = [i for i, w in enumerate(page_words) if skeleton(w) in ckeys]
    if len(positions) < 3:
        return []
    # a clause is a dense run: drop positions far away from the bulk of the matches
    positions.sort()
    best = (0, 0)
    start = 0
    for end in range(len(positions)):
        while positions[end] - positions[start] > 2 * len(clause_words) + 8:
            start += 1
        if end - start > best[1] - best[0]:
            best = (start, end)
    lo, hi = positions[best[0]], positions[best[1]]
    slack = max(3, len(clause_words) // 6)
    return page_words[max(0, lo - slack) : hi + 1 + slack]


def reading_for_clause(clause_text: str, ocr_page_text: str) -> tuple[str, Reconciliation] | None:
    """The cross-checked reading of a clause, or None when the two routes do not settle it.

    The clause is compared with the part of the page that covers it, not with the whole page.
    A reading is produced only when at least one damaged word is recovered from the image route
    and no span stays unresolved. Anything else leaves the clause as it is, so a reading never
    contains a word that neither route supports.
    """
    window = _window(_tokens(clause_text), _tokens(ocr_page_text))
    if not window:
        return None
    result = reconcile(clause_text, " ".join(window))
    if result.replaced == 0 or result.unresolved > 0:
        return None
    return result.text, result
