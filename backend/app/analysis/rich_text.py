"""报告文字里的加粗标记。

模型只能用 `**重点**` 这一种标记表示要加粗的词句；程序把它拆成 (文字, 是否加粗) 片段，
网页、PDF、Word 各自安全地画出来。不接受任何 HTML 或 Markdown 其他语法，
也不保留标记本身：未配对的 `**` 当作普通字符去掉，不会让整段变粗。
"""

from __future__ import annotations

import re

_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
MAX_BOLD_RATIO = 0.35  # 加粗内容占比过高等于没有重点


def parse_bold(text: str) -> list[tuple[str, bool]]:
    """把带 `**` 标记的文字拆成片段列表；任何情况下片段拼起来都不含 `**`。"""
    text = text or ""
    out: list[tuple[str, bool]] = []
    pos = 0
    for m in _BOLD.finditer(text):
        if m.start() > pos:
            out.append((text[pos : m.start()], False))
        inner = m.group(1)
        if inner.strip():
            out.append((inner, True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    # 未配对的残留标记不留在正文里
    return [(seg.replace("**", ""), bold) for seg, bold in out if seg.replace("**", "")]


def strip_bold(text: str) -> str:
    return "".join(seg for seg, _ in parse_bold(text))


def same_text(a: str | None, b: str | None) -> bool:
    """忽略加粗标记和首尾空白后文字是否相同；用来判断用户是否真的改了内容。"""
    return strip_bold(a or "").strip() == strip_bold(b or "").strip()


def bold_ratio(text: str) -> float:
    segs = parse_bold(text)
    total = sum(len(s) for s, _ in segs)
    return sum(len(s) for s, b in segs if b) / total if total else 0.0


def limit_bold(text: str, max_ratio: float = MAX_BOLD_RATIO) -> str:
    """加粗过多属于版式问题，不值得为此整段打回：按出现顺序保留加粗，超过上限的后续加粗取消。

    只改动标记，不改动任何文字，所以不影响内容校验。"""
    segments = parse_bold(text)
    total = sum(len(s) for s, _ in segments)
    if not total or bold_ratio(text) <= max_ratio:
        return "".join(f"**{s}**" if b else s for s, b in segments)
    budget, used, out = max_ratio * total, 0, []
    for seg, bold in segments:
        if bold and used + len(seg) <= budget:
            used += len(seg)
            out.append(f"**{seg}**")
        else:
            out.append(seg)
    return "".join(out)


def bold_problems(label: str, text: str) -> list[str]:
    """仍然要求模型自己把标记写对；占比过高由 limit_bold 在入库前收敛，不再因此打回。"""
    problems = []
    if text.count("**") % 2:
        problems.append(f"{label}里的 ** 加粗标记没有成对")
    return problems
