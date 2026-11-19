"""阿拉伯文法规扫描件：补全“读不出编号的条标题”。

埃及这类法律的条标题是“المادة”加一个用特殊字体印的数字。识别引擎常常把“المادة”读对，
数字却丢了、或只剩半个（“ماده”单独成行，或“ماده3”“ماده1”这种残缺编号）。
但正文是读出来的，所以这不是内容缺失，只是条号定位缺失。

法律条文的编号是从 1 开始逐条递增的，不会跳、不会倒退。所以：
1. 按页序把所有“条标题行”找出来（它们本身很好认：一行只有“ماده”，最多带几个数字）；
2. 读到了完整编号的标题当锚点（比上一条大 1～4，说明中间漏认了几个标题），其余按顺序递推；
3. 残缺数字只当旁证，不当依据：识别出的 “2” 可能是 7 的误读，顺序比单个字符可靠。

只在能找到足够多条标题行时才启用；找到的标题少，说明这份文件不是这种排版，原样不动。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ingestion.article_numbers import normalize_arabic

# 一行里除了“ماده”（可带定冠词）只有 0～3 位数字
# 埃及、黎巴嫩用“المادة”，突尼斯用“الفصل”；各自独立递增，输出时保留原来的词
_HEADING = re.compile(r"^\s*(?:ال)?(ماده|فصل)\s*[:\-–(\[]?\s*\(?\s*([0-9]{0,3})\s*\)?\s*$")
# 标题后面直接带条款小标题的行：“المادة ١١٠ الولوج غير المشروع”，或只有小标题没有数字。
# 为了不把正文里的引用当标题，小标题不得以连接词、介词开头，且不能太长
_TITLED = re.compile(
    r"^\s*(?:ال)?(ماده|فصل)\s*[:\-–(\[]?\s*\(?\s*([0-9]{0,3})\s*\)?\s+(\S.{2,70})$"
)
_NOT_A_TITLE = (
    "من",
    "في",
    "فى",
    "و",
    "على",
    "إلى",
    "الى",
    "أو",
    "او",
    "ان",
    "أن",
    "التي",
    "الذي",
    "الى",
    "عن",
    "هذه",
    "هذا",
    "ذلك",
    "بهذا",
    "وفق",
    "وفقا",
    "بموجب",
    "المنصوص",
    "المشار",
    "اعلاه",
    "أعلاه",
    "رقم",
    "تاريخ",
    "ما",
    "لا",
    "قد",
)
_WORDS = {"ماده": "المادة", "فصل": "الفصل"}
_DIGITS_ONLY = re.compile(r"^\s*([0-9]{1,3})\s*$")
MIN_HEADINGS = 6  # 少于这个数不启用
_MAX_SKIP = 4  # 锚点比上一条大多少以内，才认为是“中间漏认了几个标题”


# “المادة ٥٩ : يحظر …”：标题与正文在同一行。下面的规律补全只认“标题单独成行”的排版，对这种
# 排版会把读对了的标题当成“没认出来”，按顺序重写时反而吞掉条款（实测：整份只剩 49 条）
_INLINE_HEADING = re.compile(
    r"^\s*(?:ال)?(?:مادة|ماده|فصل)\s*[:\-–(\[]?\s*\(?\s*([0-9٠-٩]{1,4})\s*\)?\s*[:\-–]\s*\S", re.M
)
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _numbers_already_read(pages: list[str]) -> bool:
    """True when most headings share their line with the article's text and carry a number that
    counts up from the one before it.

    The order-based repair assumes a heading stands alone on its line. A text printed with the
    heading and the first sentence together is read correctly by the engine already; rewriting it
    from the order alone would swallow the headings it did not recognise."""
    found = [
        int(m.group(1).translate(_AR_DIGITS))
        for page in pages
        for m in _INLINE_HEADING.finditer(page)
    ]
    if len(found) < MIN_HEADINGS:
        return False
    # 一个读错的编号会让相邻两步都“不递增”，所以每个编号拿它前面第二个来比，也算对得上
    fine = sum(
        1
        for i in range(1, len(found))
        if 0 < found[i] - found[i - 1] <= _MAX_SKIP
        or (i >= 2 and 0 < found[i] - found[i - 2] <= 2 * _MAX_SKIP)
    )
    return fine / (len(found) - 1) >= 0.8


def _fix_isolated_misreads(pages: list[str]) -> list[str]:
    """Correct a heading whose number was read wrongly between two headings that agree.

    "139, 140, 1, 142": the middle one is a misread of 141. Only a number that breaks a run
    (the headings on both sides are consecutive across it) is rewritten, and only to the number
    that run requires; anything less clear is left exactly as read."""
    located: list[tuple[int, re.Match, int]] = []
    for p, page in enumerate(pages):
        for m in _INLINE_HEADING.finditer(page):
            located.append((p, m, int(m.group(1).translate(_AR_DIGITS))))
    fixes: dict[int, list[tuple[int, int, int]]] = {}
    for k in range(1, len(located) - 1):
        before, here, after = located[k - 1][2], located[k][2], located[k + 1][2]
        if after - before == 2 and here != before + 1:
            p, m, _ = located[k]
            fixes.setdefault(p, []).append((m.start(1), m.end(1), before + 1))
    out = []
    for p, page in enumerate(pages):
        for start, end, number in sorted(fixes.get(p, []), reverse=True):
            page = page[:start] + str(number) + page[end:]
        out.append(page)
    return out


@dataclass
class HeadingStats:
    found: int = 0  # 找到多少条标题行
    from_anchor: int = 0  # 其中编号来自读出的完整数字
    inferred: int = 0  # 其中编号由顺序推出
    conflicts: int = 0  # 读出的数字与推出的编号对不上（识别噪声，以顺序为准）
    last: int = 0  # 最后一条的编号
    bis: int = 0  # “之一（مكررة）”条款个数，不占序号


def repair_headings(pages: list[str]) -> tuple[list[str], HeadingStats]:
    """返回补好编号的页文本。标题行改写成“المادة (N)”，下游的条款切分器就能认出来。"""
    norm = [normalize_arabic(t).splitlines() for t in pages]
    raw = [t.splitlines() for t in pages]
    events: list[
        tuple[int, int, str, int | None, str]
    ] = []  # (页, 行, 读出的数字, 数字在下一行时的行号, 标题用词)
    for p, lines in enumerate(norm):
        for i, line in enumerate(lines):
            m = _HEADING.match(line)
            if not m:
                t = _TITLED.match(line)
                if not t:
                    continue
                first = t.group(3).split()[0]
                if first.rstrip(":،,.") in _NOT_A_TITLE or t.group(3).rstrip().endswith((".", "؛")):
                    continue
                # “الفصل الأول أحكام عامة”是章标题，不是条：只有“الفصل + 数字”才是条标题；
                # “المادة الوحيدة”是颁布条款，排在第 1 条之前，不占条号
                if t.group(1) == "فصل" and not t.group(2):
                    continue
                if t.group(3).startswith(("وحيد", "الوحيد")):
                    continue
                m = t
            word, digits, extra = m.group(1), m.group(2), None
            if not digits and i + 1 < len(lines):
                nxt = _DIGITS_ONLY.match(lines[i + 1])
                if nxt:
                    digits, extra = nxt.group(1), i + 1
            events.append((p, i, digits, extra, word))
    stats = HeadingStats(found=len(events))
    if _numbers_already_read(pages):
        # 编号基本读对了（标题与正文同行也读得对）：不要用顺序去改写整份，只修孤立读错的几个。
        # 这一步要放在“标题行太少就不处理”之前：同行排版里单独成行的标题本来就少
        return _fix_isolated_misreads(pages), stats
    if len(events) < MIN_HEADINGS:
        return pages, stats

    drop: set[tuple[int, int]] = set()
    assigned: dict[tuple[int, int], tuple[int | str, str]] = {}
    last_by_word: dict[str, int] = {}
    for p, i, digits, extra, word in events:
        prev = last_by_word.get(word, 0)
        n = prev + 1
        if "مكرر" in norm[p][i]:
            # “第 N 条之一（مكررة）”是附在上一条之后的条款，不占新的条号，后面的条号不能因此错位
            assigned[(p, i)] = (f"{prev}-مكررة", word)
            stats.bis += 1
            continue
        # 只有完整数字（≥2 位，或个位数且正好是下一条）才可当锚点
        read = int(digits) if digits else 0
        # 识别常把前导的“1”丢掉（“١٣٢”读成“٣٢”）：读出的数字补回前导 1 后正好接上时按补回的算
        candidates = [read]
        if digits and len(digits) <= 2 and prev >= 99:
            candidates.append(int("1" + digits))
        hit = next(
            (
                c
                for c in candidates
                if digits and prev < c <= prev + _MAX_SKIP and (len(str(c)) >= 2 or c == n)
            ),
            None,
        )
        if hit is not None:
            n = hit
            stats.from_anchor += 1
        else:
            stats.inferred += 1
            if digits and digits not in str(n):
                stats.conflicts += 1
        assigned[(p, i)] = (n, word)
        if extra is not None:
            drop.add((p, extra))
        last_by_word[word] = n
    stats.last = max(last_by_word.values(), default=0)

    out: list[str] = []
    for p, lines in enumerate(raw):
        kept = []
        for i, line in enumerate(lines):
            if (p, i) in drop:
                continue
            if (p, i) in assigned:
                number, word = assigned[(p, i)]
                kept.append(f"{_WORDS[word]} ({number})")
            else:
                kept.append(line)
        out.append("\n".join(kept))
    return out, stats


# 右到左排版里，模型有时把标题行的词序读反：“: (٥) مادة” 而不是 “مادة (٥) :”。
# 条款切分器认的是前一种顺序，反序的标题会整条并进上一条。只处理“整行就是一个标题”的情形
_REVERSED_HEADING = re.compile(
    r"^\s*[:：.\-–]?\s*\(?\s*([0-9٠-٩۰-۹]{1,4})\s*\)?\s*[:：.\-–]?\s*(مادة|المادة|فصل|الفصل)\s*$"
)


# “مادة ( ٤٥ ) :”：括号里夹着空格，切分器认不出这是条标题
_SPACED_HEADING = re.compile(
    r"^\s*((?:ال)?(?:مادة|فصل))\s*\(\s*([0-9٠-٩۰-۹]{1,4})\s*\)\s*([:：]?)\s*$"
)


def fix_reversed_headings(pages: list[str]) -> tuple[list[str], int]:
    """Normalise heading lines the reading got wrong: reverse word order ("(٥) مادة :") and spaces
    inside the brackets ("مادة ( ٤٥ ) :") are put back to "مادة (٥)" / "مادة (٤٥) :"."""
    fixed = 0
    out: list[str] = []
    for page in pages:
        lines = []
        for line in page.splitlines():
            spaced = _SPACED_HEADING.match(line)
            if spaced and ("( " in line or " )" in line):
                lines.append(f"{spaced.group(1)} ({spaced.group(2)}) {spaced.group(3)}".rstrip())
                fixed += 1
                continue
            m = _REVERSED_HEADING.match(line)
            if m:
                word = "المادة" if m.group(2).startswith("ال") else m.group(2)
                lines.append(f"{word} ({m.group(1)})")
                fixed += 1
            else:
                lines.append(line)
        out.append(chr(10).join(lines))
    return out, fixed


# 单独成行的条标题：“مادة (٣٣)”。识别把个别数字读错（٣ 读成 ٢、٦ 读成 ۰ 等）时，按“条号逐条加一”
# 的顺序把读错的补回来。只在“后面很快又接上了应有的号”时才改，宁可不改也不改错
_STANDALONE_HEADING = re.compile(r"^(\s*(?:ال)?مادة\s*)\(\s*([0-9٠-٩۰-۹]{1,4})\s*\)(\s*[:：]?\s*)$")
_SEQ_LOOKAHEAD = 8  # 往后最多看几个标题找“接上了”的那一个


def repair_heading_sequence(pages: list[str]) -> tuple[list[str], int]:
    """Correct misread article numbers using the order of the headings.

    Articles are numbered 1, 2, 3 … without gaps. A heading whose number is not the previous plus
    one is rewritten to that number when, within the next few headings, one carries exactly the
    number the count would have reached: then the headings in between were misread and the count
    is certain. A number that cannot be tied to what follows is left exactly as read."""
    located: list[tuple[int, int, re.Match]] = []
    for p, page in enumerate(pages):
        for i, line in enumerate(page.splitlines()):
            m = _STANDALONE_HEADING.match(line)
            if m:
                located.append((p, i, m))
    numbers = [int(m.group(2).translate(_AR_DIGITS)) for _, _, m in located]
    fixes: dict[tuple[int, int], int] = {}
    for k in range(1, len(numbers)):
        prev = numbers[k - 1]
        if numbers[k] == prev + 1:
            continue
        for j in range(k + 1, min(len(numbers), k + 1 + _SEQ_LOOKAHEAD)):
            if numbers[j] == prev + (j - k + 1):
                for t in range(k, j):
                    numbers[t] = prev + (t - k + 1)
                    fixes[(located[t][0], located[t][1])] = numbers[t]
                break
    if not fixes:
        return pages, 0
    out = []
    for p, page in enumerate(pages):
        lines = page.splitlines()
        for (fp, fi), n in fixes.items():
            if fp == p:
                m = _STANDALONE_HEADING.match(lines[fi])
                lines[fi] = f"{m.group(1)}({n}){m.group(3)}"
        out.append(chr(10).join(lines))
    return out, len(fixes)
