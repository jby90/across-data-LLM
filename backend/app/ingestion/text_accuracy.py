"""Word-level accuracy of a page reading against a hand transcription, with the kinds of error.

The reading and the transcription are cut into words (letters of any alphabet, digits kept as
their own words), then aligned. A word of the transcription is *right* when the reading has it at
the aligned place; the rest are counted as missed (nothing there), misread (another word there)
or, for words in the reading with no counterpart, extra. A number that is misread is counted
again under ``number_errors`` because wrong numbers are where a reading does legal damage.
Arabic letters are normalised first (diacritics, alef and yaa variants) so a different way of
writing the same letter is not an error.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.ingestion.article_numbers import normalize_arabic

_WORD = re.compile(r"\d+|[^\W\d_]+")


def words_of(text: str) -> list[str]:
    return _WORD.findall(normalize_arabic(text or "").lower())


def compare(reading: str, truth: str) -> dict:
    got, ref = words_of(reading), words_of(truth)
    right = missed = misread = extra = 0
    number_errors = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, ref, got, autojunk=False).get_opcodes():
        if tag == "equal":
            right += i2 - i1
        elif tag == "delete":
            missed += i2 - i1
            number_errors += sum(1 for w in ref[i1:i2] if w.isdigit())
        elif tag == "insert":
            extra += j2 - j1
        else:  # replace：对齐处读成了别的词；多出的或缺的算漏读、多读
            n_ref, n_got = i2 - i1, j2 - j1
            paired = min(n_ref, n_got)
            misread += paired
            missed += n_ref - paired
            extra += n_got - paired
            number_errors += sum(1 for w in ref[i1:i2] if w.isdigit())
    total = len(ref)
    return {
        "words": total,
        "right": right,
        "missed": missed,
        "misread": misread,
        "extra": extra,
        "number_errors": number_errors,
        "accuracy": round(right / total, 4) if total else None,
    }


def compare_unordered(reading: str, truth: str) -> dict:
    """Word-level accuracy that ignores the order of the words, for table pages.

    A table's cells are laid out in two dimensions and the engines read them in different orders
    (a right-to-left header row is read left to right, rows may come before their header), so a
    sequence comparison counts every cell that moved as an error. What matters for a table is that
    each cell's words and figures are present and right, so compare the multisets of words.
    ``right`` counts words of the truth found in the reading; ``missed`` those not found; ``extra``
    words in the reading that are not in the truth; ``number_errors`` figures of the truth that are
    not in the reading."""
    from collections import Counter

    got, ref = Counter(words_of(reading)), Counter(words_of(truth))
    right = sum((got & ref).values())
    missed_words = ref - got
    extra_words = got - ref
    total = sum(ref.values())
    return {
        "words": total,
        "right": right,
        "missed": sum(missed_words.values()),
        "extra": sum(extra_words.values()),
        "number_errors": sum(c for w, c in missed_words.items() if any(ch.isdigit() for ch in w)),
        "accuracy": round(right / total, 4) if total else 1.0,
    }
