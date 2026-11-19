"""Replace pages of an image-recognised document with a better reading, with a check.

A second recogniser reads the pages the first one could not read. Its text is accepted for a page
only when it is a real improvement: it must be usable text (see ``page_quality``), and it must not
contradict the first reading where the first reading is trustworthy. Numbers are where a wrong
reading does legal damage (article, decision and year numbers), so every number that both readings
share a context for has to agree; a page whose numbers disagree where the old text was itself
readable is left as it was and reported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ingestion.page_quality import page_is_usable

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUMBER = re.compile(r"\d{1,4}")
MAX_HEADER_LINES = 3  # 页眉只可能出现在页首的前几行
MIN_AGREE_WORDS = 40  # 两个引擎互相印证至少要有这么多词，词太少的页不算印证
MIN_AGREEMENT = 0.6  # 两个独立引擎读出的词集合的重合度（交集/并集）
MIN_NEW_WORDS = 25  # 短页（只有一两条）也是正常页；新读数只要成句即可
MAX_DIACRITIC_RATIO = 0.04  # 实测：编造的经文体 6.5%–21%，官方法律文本最高 2.2%；取两者之间
_DIACRITICS = re.compile("[ً-ْٰ]")
_ARABIC_LETTER = re.compile("[ء-ي]")
_NOISE_LINE = re.compile(  # 字母夹符号的短乱码行（如 toXIM&+）；邮箱、网址、百分数不算
    r"(?=.*[A-Za-z]{3})[A-Za-z&+%$#=~^]{3,30}"
)
_NOISE_SYMBOL = re.compile(r"[&+%$#=~^]")


@dataclass(frozen=True)
class Verdict:
    accept: bool
    reason: str
    text: str


def numbers_of(text: str) -> list[str]:
    return _NUMBER.findall((text or "").translate(_ARABIC_DIGITS))


# 第二引擎偶尔把版面块的类别与坐标留在文字里：“title[246, 106, 314, 120]Article 11”
_BLOCK_MARK = re.compile(
    r"\b(?:doc_title|sub_title|title|text|table|header|footer|image|figure|formula|list|caption|"
    r"seal|number|page_number|footnote|reference|algorithm)\[\s*\d+(?:\s*,\s*\d+){3}\s*\]"
)
# 引擎原始输出里的块标记：“<BLOCK>title<LEVEL1>[252, 190, 308, 202]<FORK>Article 3”
_RAW_BLOCK = re.compile(
    r"<BLOCK>[A-Za-z_]*(?:<[A-Za-z0-9_]+>)?\[\s*\d+(?:\s*,\s*\d+){3}\s*\]<FORK>"
)
# 第二引擎把标题写成 Markdown 粗体：“**الفصل 43** : …”。粗体星号不是法律文字
_BOLD = re.compile(r"\*\*")
_BREAK = re.compile(r"<br\s*/?>", re.I)
_ROW_END = re.compile(r"</tr\s*>", re.I)
_CELL_END = re.compile(r"</t[dh]\s*>", re.I)
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")


def clean_model_markup(text: str) -> str:
    """The second model writes a table as HTML. Keep its words: a row becomes a line, cells in a
    row are separated by " | ", line breaks inside a cell become spaces, every tag is dropped."""
    out = _BOLD.sub("", _BREAK.sub(" ", _BLOCK_MARK.sub("", _RAW_BLOCK.sub("", text or ""))))
    out = _CELL_END.sub(" | ", out)
    out = _ROW_END.sub(chr(10), out)
    out = _TAG.sub("", out)
    # surplus cell separators at the end of a row
    out = re.sub(r"( [|] )+(?=" + chr(10) + r"|$)", "", out)
    return re.sub(r"[ " + chr(9) + r"]+", " ", out).strip()


def strip_page_furniture(text: str, language: str) -> str:
    """Drop lines in the head of the page that are not part of the text: a page mark, and runs of
    Latin letters above an Arabic text (an institution's letterhead in another language)."""
    lines = [ln for ln in clean_model_markup(text).splitlines() if ln.strip()]
    out: list[str] = []
    arabic = language.split("-")[0].lower() in ("ar", "fa", "ur")
    for i, ln in enumerate(lines):
        if i < MAX_HEADER_LINES and arabic:
            letters = [c for c in ln if c.isalpha()]
            if letters and sum(1 for c in letters if c.isascii()) / len(letters) > 0.9:
                continue
        out.append(ln.strip())
    return chr(10).join(out)


def truncate_loop(text: str, size: int = 80, times: int = 12) -> str:
    """Keep what a page said before the model fell into a repetition loop.

    A loop is a stretch that repeats the same ``size`` characters ``times`` times or more and runs
    to the end of the page; text before its first repeat is kept (cut at a line end) and the loop
    is dropped. A page whose repeats are scattered, or that does not end in a loop, is returned
    unchanged: ordinary legal wording repeats a phrase a few times without being a loop. The
    caller still decides whether what is left is usable."""
    flat = text or ""
    if len(flat) < size * times:
        return flat
    seen: dict[str, list[int]] = {}
    for i in range(len(flat) - size):
        positions = seen.setdefault(flat[i : i + size], [])
        positions.append(i)
        if len(positions) < times:
            continue
        start = positions[0]
        # 循环是一直重复到页尾的：从第一次出现起，后面要全是这一段的重复
        tail = flat[start:]
        unit = positions[1] - start
        # 单元很短（“تحرير ” 6 个字符）或较长都可能；检查的是整个尾部都由这个单元重复构成
        if unit > 0 and tail.startswith(flat[start : start + unit] * min(times, len(tail) // unit)):
            cut = flat.rfind(chr(10), 0, start)
            return flat[: cut if cut > 0 else start].rstrip()
    return flat


def _loops(text: str, size: int = 80, times: int = 12) -> bool:
    """The same stretch of text over and over: the model stuck in a repetition loop."""
    flat = " ".join((text or "").split())
    if len(flat) < size * times:
        return False
    seen: dict[str, int] = {}
    for i in range(len(flat) - size):
        chunk = flat[i : i + size]
        seen[chunk] = seen.get(chunk, 0) + 1
        if seen[chunk] >= times:
            return True
    return False


def looks_invented(text: str) -> bool:
    """Signs of a reading the recogniser made up rather than read: vowel-marked scripture style
    in a modern legal text, lines of symbols mixed into letters, or a repetition loop."""
    if _loops(text):
        return True
    letters = len(_ARABIC_LETTER.findall(text))
    if letters and len(_DIACRITICS.findall(text)) / letters > MAX_DIACRITIC_RATIO:
        return True
    return any(
        _NOISE_LINE.fullmatch(ln.strip()) and _NOISE_SYMBOL.search(ln)
        for ln in text.splitlines()
        if len(ln) < 40
    )


def engines_agree(first: str, second: str) -> bool:
    """Two independent recognisers read the same words from the page.

    The check on how many function words a page holds is only a proxy and fails on pages of
    terms, lists and contents; two different engines landing on the same words is much stronger
    evidence that the page was read right."""
    from app.ingestion.text_accuracy import words_of

    a, b = set(words_of(clean_model_markup(first))), set(words_of(clean_model_markup(second)))
    if len(a) < MIN_AGREE_WORDS or len(b) < MIN_AGREE_WORDS:
        return False
    return len(a & b) / len(a | b) >= MIN_AGREEMENT


def _is_dropped_digit(short: str, long: str) -> bool:
    """``short`` is ``long`` with one digit missing (the usual misreading of a figure in a table:
    "50" read as "5", "250" as "25")."""
    if len(long) != len(short) + 1:
        return False
    return any(long[:i] + long[i + 1 :] == short for i in range(len(long)))


def _explained_share(old_n: set[str], new_n: set[str]) -> float:
    """Share of the old reading's numbers that the new reading either repeats or explains as a
    digit-dropped misreading of one of its own numbers. A different figure is not explained."""
    explained = sum(1 for n in old_n if n in new_n or any(_is_dropped_digit(n, m) for m in new_n))
    return explained / len(old_n)


MIN_KEPT_SHARE = 0.7  # 新读数的词数不得少于可读原读数的这个比例


def judge(old: str, new: str, language: str, *, corroborated: bool = False) -> Verdict:
    """Accept ``new`` for a page only if it is usable (or a second engine read the same words)
    and does not contradict a readable ``old``."""
    new = strip_page_furniture(new, language)
    if looks_invented(new):
        return Verdict(False, "新读数含编造迹象，不采用", old)
    if not (corroborated or page_is_usable(new, language, MIN_NEW_WORDS)):
        return Verdict(False, "新读数不成文", old)
    # 循环被截掉、或模型漏读了大块内容时，新读数会比可读的原读数短得多：换上去等于删掉这部分
    if page_is_usable(old, language) and len(new.split()) < MIN_KEPT_SHARE * len(old.split()):
        return Verdict(False, "新读数比原读数少了一大段，保留原读数", old)
    if page_is_usable(old, language):
        # 旧读数本身可用：两边都出现的数字必须一致。旧读数按“条款起始页”取文字，可能比这一页
        # 多出相邻页的数字，所以任一方向的数字大半对得上即可；两个方向都对不上才是矛盾
        old_n, new_n = set(numbers_of(old)), set(numbers_of(new))
        if (
            old_n
            and new_n
            and max(_explained_share(old_n, new_n), _explained_share(new_n, old_n)) < 0.5
        ):
            return Verdict(False, "两次读数的数字对不上，保留原读数", old)
    return Verdict(True, "采用新读数", new)


def merge_pages(
    old_pages: dict[int, str],
    replacements: dict[int, str],
    language: str,
    corroborated: frozenset[int] | set[int] = frozenset(),
):
    """New text per page number, plus a verdict list for the replaced pages."""
    out = dict(old_pages)
    verdicts: dict[int, Verdict] = {}
    for page, new in replacements.items():
        v = judge(old_pages.get(page, ""), new, language, corroborated=page in corroborated)
        verdicts[page] = v
        if v.accept:
            out[page] = v.text
    return out, verdicts


__all__ = [
    "Verdict",
    "clean_model_markup",
    "engines_agree",
    "truncate_loop",
    "judge",
    "merge_pages",
    "numbers_of",
    "strip_page_furniture",
]


_HAS_DIGIT = re.compile(r"\d")


def vote_readings(primary: str, other: str, witness: str) -> str:
    """Settle the places where two readings of a page differ, using a third reading as witness.

    ``primary`` is the text to keep; wherever ``other`` has a different word (or a few) in the
    same place, the other's variant is taken only when the witness supports it and does not
    support the primary's. Figures follow the same rule, so a number is never replaced by one
    that only a single engine saw, and with no witness support the primary stays. Insertions and
    deletions are left alone: only like-for-like substitutions are voted on."""
    import difflib

    from app.ingestion.text_accuracy import words_of

    a, b = primary.split(), other.split()
    support = set(words_of(witness)) | set((witness or "").translate(_ARABIC_DIGITS).split())

    def seen(tokens: list[str]) -> int:
        return sum(1 for t in tokens if t in support or t.translate(_ARABIC_DIGITS) in support)

    out: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        mine, theirs = a[i1:i2], b[j1:j2]
        if tag == "replace" and len(mine) == len(theirs) and len(mine) <= 3:
            # 逐个词投票：只有“对方的词有见证读数支持、我的词没有”才换；一个只有单个引擎读到的
            # 数字不会因为同一块里别的词被换而被带进来
            out.extend(
                t if seen([t]) and not seen([m]) else m for m, t in zip(mine, theirs, strict=True)
            )
        else:
            out.extend(mine)
    # 保持原有的换行结构：逐词替换后按原来每行的词数重新分行
    lines, k = [], 0
    for line in primary.splitlines():
        n = len(line.split())
        lines.append(" ".join(out[k : k + n]))
        k += n
    return chr(10).join(lines)


MIN_LINE_SUPPORT = 0.3  # 一行里的词，至少这么大比例要在另一个引擎的读数里出现过
MIN_LINE_WORDS = 3  # 比这更短的行（页码、编号）不判断
HEAD_LINES = 12  # 文件第一页的页首这么多行是抬头区（印章、徽章、手写填写的日期与编号）


def drop_unsupported_lines(
    primary: str, secondary: str, head_lines: int = 0
) -> tuple[str, list[str]]:
    """Remove the lines of ``primary`` that an independent second reading does not support.

    A recogniser looking at a letterhead, a seal or a stamp can write fluent text that is not on
    the page (a different country's name, an invented date or reference number). Fluent text
    cannot be told from real text by looking at it, but a second engine reading the same page will
    not produce the same words for it. A line is dropped when fewer than ``MIN_LINE_SUPPORT`` of
    its words occur anywhere in the second reading. On the first page of a document pass
    ``head_lines=HEAD_LINES``: in that letterhead zone (the first ``head_lines`` lines)
    a line is also dropped when it carries a figure the second reading does
    not contain: there the figures are filled in by hand or stamped and are the likeliest to be
    made up. Returns the kept text and the dropped lines (for the log)."""
    from app.ingestion.text_accuracy import words_of

    support = set(words_of(secondary))
    figures = set(numbers_of(secondary))
    kept: list[str] = []
    dropped: list[str] = []
    seen = 0  # 非空行的序号：被删掉的行留下的空行不算进抬头区
    for line in primary.splitlines():
        if not line.strip():
            kept.append(line)
            continue
        head = seen < head_lines
        seen += 1
        words = [
            w for w in words_of(line) if len(w) >= 2 and not w.isdigit()
        ]  # 单字母、纯数字不当证据
        if not support:
            kept.append(line)
            continue
        unsupported_figure = head and any(n not in figures for n in numbers_of(line))
        # 抬头区里任何一行都要被第二读数支持（徽章、印章、他种文字的抬头最容易被读成噪声或编造）
        if (len(words) < MIN_LINE_WORDS and not head and not unsupported_figure) or (
            not words and not unsupported_figure
        ):
            kept.append(line)
            continue
        share = sum(1 for w in words if w in support) / len(words) if words else 1.0
        if share < MIN_LINE_SUPPORT or unsupported_figure:
            dropped.append(line)
        else:
            kept.append(line)
    return chr(10).join(kept), [d for d in dropped if d.strip()]
