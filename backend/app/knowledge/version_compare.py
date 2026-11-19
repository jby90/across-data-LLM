"""Clause-by-clause comparison of two versions of one document.

Clauses are paired by clause number when the number is unique in both versions, and by text
similarity in order for the rest. A pair whose text differs (ignoring whitespace) is "changed" and
carries a word-level diff; a clause with no partner is "added" (only in the newer version) or
"removed" (only in the older one). Nothing here knows a country, a language or a law."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

SIMILAR = 0.6  # 两条没有相同条号的条款，文字相似度达到这个值才算同一条的改动
MAX_PAIR_CHARS = 6000  # 相似度只比较开头这部分，保证长条款也不拖慢


@dataclass
class Side:
    clause_id: str
    ordinal: int
    number: str | None
    heading: str | None
    text: str


@dataclass
class Pair:
    status: str  # added / removed / changed / unchanged
    base: Side | None
    target: Side | None
    diff: list[dict] = field(default_factory=list)  # changed 才有


def squash(text: str) -> str:
    return " ".join((text or "").split())


def _number_key(number: str | None) -> str | None:
    key = re.sub(r"\s+", "", (number or "")).casefold()
    return key or None


def _unique_numbers(items: list[Side]) -> dict[str, Side]:
    seen: dict[str, list[Side]] = {}
    for it in items:
        k = _number_key(it.number)
        if k:
            seen.setdefault(k, []).append(it)
    return {k: v[0] for k, v in seen.items() if len(v) == 1}


def word_diff(old: str, new: str) -> list[dict]:
    """[{"op": "equal"|"delete"|"insert", "text": …}] over whitespace-separated words."""
    a, b = squash(old).split(" "), squash(new).split(" ")
    out: list[dict] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append({"op": "equal", "text": " ".join(a[i1:i2])})
            continue
        if i2 > i1:
            out.append({"op": "delete", "text": " ".join(a[i1:i2])})
        if j2 > j1:
            out.append({"op": "insert", "text": " ".join(b[j1:j2])})
    return out


def _similar(a: str, b: str) -> float:
    a, b = squash(a)[:MAX_PAIR_CHARS], squash(b)[:MAX_PAIR_CHARS]
    if not a or not b:
        return 0.0
    m = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if m.real_quick_ratio() < SIMILAR or m.quick_ratio() < SIMILAR:
        return 0.0
    return m.ratio()


def compare(base: list[Side], target: list[Side]) -> list[Pair]:
    base = sorted(base, key=lambda s: s.ordinal)
    target = sorted(target, key=lambda s: s.ordinal)
    partner: dict[str, Side] = {}  # target.clause_id -> base side

    b_nums, t_nums = _unique_numbers(base), _unique_numbers(target)
    for key, t in t_nums.items():
        b = b_nums.get(key)
        if b is not None:
            partner[t.clause_id] = b

    # 没有同号条款的：在两个已配对锚点之间，按顺序做相似度配对
    paired_base = {b.clause_id for b in partner.values()}
    b_index = {b.clause_id: i for i, b in enumerate(base)}
    last_b = -1
    cursor_b = 0
    for t_i, t in enumerate(target):
        if t.clause_id in partner:
            last_b = b_index[partner[t.clause_id].clause_id]
            cursor_b = last_b + 1
            continue
        # 下一个锚点在 base 里的位置，限制搜索窗口
        upper = len(base)
        for later in target[t_i + 1 :]:
            if later.clause_id in partner:
                upper = b_index[partner[later.clause_id].clause_id]
                break
        best, best_i = 0.0, -1
        for i in range(cursor_b, upper):
            cand = base[i]
            if cand.clause_id in paired_base:
                continue
            score = _similar(cand.text, t.text)
            if score > best:
                best, best_i = score, i
        if best >= SIMILAR:
            partner[t.clause_id] = base[best_i]
            paired_base.add(base[best_i].clause_id)
            cursor_b = best_i + 1

    out: list[Pair] = []
    emitted_base: set[str] = set()

    def flush_removed_before(limit_index: int) -> None:
        for i in range(0, limit_index):
            b = base[i]
            if b.clause_id not in paired_base and b.clause_id not in emitted_base:
                emitted_base.add(b.clause_id)
                out.append(Pair("removed", b, None))

    for t in target:
        b = partner.get(t.clause_id)
        if b is None:
            out.append(Pair("added", None, t))
            continue
        flush_removed_before(b_index[b.clause_id])
        if squash(b.text) == squash(t.text):
            out.append(Pair("unchanged", b, t))
        else:
            out.append(Pair("changed", b, t, word_diff(b.text, t.text)))
    flush_removed_before(len(base))
    return out


def summarize(pairs: list[Pair]) -> dict:
    counts = {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}
    for p in pairs:
        counts[p.status] += 1
    return counts
