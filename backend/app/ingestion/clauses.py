# ruff: noqa: E501
"""通用条款切分。

识别的编号样式:
- 新加坡法条:`26.—(1) ...`、`26A. ...`(条标题在编号行的前一行)、附表段落 `20. ...`
- 指引段落号:`19.4` 单独成行,正文在后面几行
- 英文 Section/Article/Regulation/Rule N、法文 Article N/premier、中文 第N条、阿拉伯文 المادة N

可靠性措施(法条定位准确率的关键):
- 编号必须递增才算新条款,正文里的列表项("1. ...")不会被误当成条款;
- 一份文件内数字编号的"层级模式"锁定(单级如 26 / 两级如 19.4),不混用;
- PART/CHAPTER/SCHEDULE 作为结构标记,写入每条的 section_path,不混进条款正文;
- 目录页(每条只有一行标题)识别后合并为一个"目录"块,不当作条款;
- 原文逐字保留。识别不出编号时退化为按段落切分,并在 strategy 里如实标明。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ingestion.source_layout import atomic_line_blocks

_ARABIC_DIGITS = "٠-٩"
_THAI_DIGITS = "๐-๙"
_FULLWIDTH_DIGITS = "０-９"
_CN_NUM = "一二三四五六七八九十百千零〇两"

# 关键词式/中文/日文/韩文/泰文/俄文/阿拉伯文编号:按结构特征识别各法域条号
_KEYWORD_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^(?:(?:Section|SECTION|Article|ARTICLE|Artikel|ARTIKEL|Art[ií]culo|ART[IÍ]CULO|Artigo|ARTIGO|Articolo|ARTICOLO|"
        r"Regulation|REGULATION|Rule|RULE|Clause|CLAUSE|Madde|MADDE|GEÇİCİ\s+MADDE|Geçici\s+Madde|"
        r"Điều|ĐIỀU|Pasal|PASAL|Seksyen|SEKSYEN|Peraturan|PERATURAN|Art|ART)\s+|(?:Art|ART)[.,]\s*)"
        r"(?P<num>\d{1,3}\s*[–—]\s*\d{1,3}|\(\d+[A-Za-z]?\)|\d+[A-Za-z]?(?:\.\d+)*(?:[º°])?|[Pp]remier|PREMIER|1er|[Úú]nico|ÚNICO)"
        r"(?=\s|[.:—–-]|$)(?!\s*(?:Rev\.?\s*)?Ed\.?\b)(?!.*\bChapter\s+[A-Z0-9.-]+\s*$)[\s.:—–-]*(?P<rest>.*)$",
    ),
    re.compile(rf"^第(?P<num>[{_CN_NUM}\d{_FULLWIDTH_DIGITS}]+(?:[の之][{_CN_NUM}\d{_FULLWIDTH_DIGITS}]+)?)条[\s、.:：　]*(?P<rest>.*)$"),
    # 韩文法律条号：`제1조(목적)`、`제28조의8(...)`、`제28조부터 제31조까지 삭제`
    re.compile(r"^제\s*(?P<num>\d+\s*조부터\s*제\s*\d+)\s*조까지(?=\s|$)[\s.:—–-]*(?P<rest>.*)$"),
    re.compile(r"^제\s*(?P<num>\d+조의\s*\d+)(?=\s|\(|$)[\s.:—–-]*(?P<rest>.*)$"),
    re.compile(r"^제\s*(?P<num>\d+)\s*조(?!(?:의|\d))(?=\s|\(|$)[\s.:—–-]*(?P<rest>.*)$"),
    # 泰文法律/规章条号：`มาตรา ๑`、`มาตรา 37/1`、`ข้อ ๑`
    re.compile(rf"^(?:มาตรา|ข้อ)\s*(?P<num>[\d{_THAI_DIGITS}]+(?:/[\d{_THAI_DIGITS}]+)?)(?=\s|$)[\s.:—–-]*(?P<rest>.*)$"),
    # 西里尔字母法域（俄/哈/吉/乌）：`Статья 1.`、`1-бап.`、`1-берене.`
    re.compile(r"^(?:Статья|СТАТЬЯ|Стаття|СТАТТЯ|Бап|БАП|Берене|БЕРЕНЕ)\s+(?P<num>\d+(?:[.-]\d+)*)\b[\s.:—–-]*(?P<rest>.*)$"),
    re.compile(r"^(?P<num>\d+(?:[.-]\d+)*)\s*-\s*(?:бап|БАП|берене|БЕРЕНЕ)\b[\s.:—–-]*(?P<rest>.*)$"),
    # 德语区（`§ 12`）与北欧瑞典法典（`1 §`、`2 a §`）
    re.compile(r"^§\s*(?P<num>\d+[a-z]?)\b[\s.:—–-]*(?P<rest>.*)$"),
    re.compile(r"^(?P<num>\d{1,3}(?:\s*[a-z])?)\s*§(?=\s|$)[\s.:—–-]*(?P<rest>.*)$"),
    # “المادة”偶尔被读成“المسادة”（书法拉长）或“املادة”（OCR 字母颠倒、夹 \ufffe/\ufffd），一并认作标题
    re.compile(
        rf"^(?:ال[\ufffe]*م(?:س)?|ام[\ufffe]*ل|م(?:س)?)[\ufffe]*ادة(?:\s+|\s*[:：]\s*)[\ufffe\ufffd]*(?P<num>[\d{_ARABIC_DIGITS}]+|\S+)[\s:：.\-–—]*(?P<rest>.*)$"
    ),
    # 突尼斯法律的条标题是“الفصل”加数字；后面不是数字的“الفصل الأول”是章标题，不在此列
    re.compile(
        rf"^(?:ال)?فصل[\s:：]*\(?(?P<num>[\d{_ARABIC_DIGITS}]+)\)?[\s:：.\-–—]*(?P<rest>.*)$"
    ),
]
# 突尼斯式法律把第一条写成“الفصل الأول :”。只有整份文件用“الفصل N”给条编号时才这样认：
# 用“المادة N”编号的法律里，“الفصل الأول :”是章标题，认成第 1 条会把条号序列重置
_FASL_FIRST = re.compile(r"^(?:ال)?فصل\s+(?P<num>الأول|الاول)\s*[:：](?P<rest>.*)$")
_FASL_NUMBERED = re.compile(rf"^(?:ال)?فصل[\s:：]*\(?[\d{_ARABIC_DIGITS}]+\)?")
_MADDA_NUMBERED = re.compile(rf"^(?:ال[\ufffe]*م(?:س)?|ام[\ufffe]*ل|م(?:س)?)[\ufffe]*ادة[\s:：]*\(?[\d{_ARABIC_DIGITS}]+\)?")
# 新加坡/英联邦法条:26.—(1) / 26A. In this ... / 7(1) Except where ... / 单独成行 2. / 1 Purposes
_ARTICLE_HEAD = re.compile(
    r"^(?:(?:Article|ARTICLE|Artikel|ARTIKEL|Art[ií]culo|ART[IÍ]CULO|Artigo|ARTIGO|Articolo|ARTICOLO|"
    r"Madde|MADDE|Điều|ĐIỀU|Pasal|PASAL|Seksyen|SEKSYEN|Peraturan|PERATURAN|"
    r"Статья|СТАТЬЯ|Стаття|СТАТТЯ|Бап|БАП|Берене|БЕРЕНЕ|Art|ART)\s+|(?:Art|ART)[.,]\s*)"
    r"\(?(?:\d+|[Pp]remier|PREMIER|1er)\b"
    rf"|^第[{_CN_NUM}\d{_FULLWIDTH_DIGITS}]+(?:[の之][{_CN_NUM}\d{_FULLWIDTH_DIGITS}]+)?条"
    r"|^제\s*\d+\s*조"
    rf"|^(?:มาตรา|ข้อ)\s*[\d{_THAI_DIGITS}]+"
)
_SECTION_WORD = re.compile(r"^(?:Section|SECTION)\s+\d")
_PARAGRAPH_SYMBOL = re.compile(r"^§\s*\d")
_AR_FIRST = ("الأول", "الاول")
_SG_SECTION = re.compile(
    r"^(?:"
    r"(?P<num>\d{1,3}[A-Z]{0,3})\.(?:\s*[—–-]\s*|\s+)(?!(?:Rev\.?\s*)?Ed\.?\b)(?P<rest>\S.*)$"
    r"|(?P<num_sub>\d{1,3}(?:\.\d{1,2})?[A-Z]{0,2})\(1\)\s+(?P<rest_sub>[A-Z“\"(].*)$"
    r"|(?P<num_ocr>\d{1,3}[A-Z]{0,2})(?:[,:]\s+|\s+)(?P<rest_ocr>(?:\(1\)\s+[A-Z]|[A-Z][^.\n]{2,110}[.:][—–-]).*)$"
    r"|(?P<num_rng>\d{1,3}\s*[-–—]\s*\d{1,3})[.]?\s+(?P<rest_rng>[A-Z].*)$"
    r"|(?P<num_bare>\d{1,3}[A-Z]{0,2})\.\s*$"
    r")"
)
# 英联邦现代无点号条标题: `1 Purposes`、`63 Review of Act`(排除日期与目录末尾页码)
_SPACE_SECTION = re.compile(
    r"^(?P<num>\d{1,3}[A-Z]{0,2})\s+"
    r"(?!(?:January|February|March|April|May|June|July|August|September|October|November|December|Ed\.|Rev\.|No\.|Vol\.)\b)"
    r"(?P<rest>[A-Z][A-Za-z0-9 ,’'()—–/-]{2,85})(?<!\s\d)(?<!\s\d\d)(?<!\s\d\d\d)$"
)
_SPACE_SECTION_BODY = re.compile(
    r"^(?:\(1\)\s+[A-Z“\"(]|(?:The|A|An|In|For|If|Despite|Subject|Nothing|Every|Each|No|Without|On|Before|After|When|Where|Any|This|Except|Unless|To|It)\s+[A-Za-z]|[A-Z][A-Za-z0-9 ’'()-]{1,45}\s+(?:is|are|may|must|shall|applies|apply|does|do|has|have|means|includes)\b)"
)
# 多级小数编号:1.1 text / 19.4 / 3.1.1.1(最多六级,正文可在后面行)
_DECIMAL = re.compile(r"^(?P<num>\d{1,3}(?:\.\d{1,3}){1,5})[.)]?(?:\s+(?P<rest>\S.*))?\s*$")
# 通用"1. Text"(要求大写字母/西里尔大写/引号/括号开头,减少误判)
_SIMPLE = re.compile(r"^(?P<num>\d{1,3})[.)]\s+(?!(?:Rev\.?\s*)?Ed\.?\b)(?P<rest>[A-ZА-ЯЁ“\"(].*)$")

_PART = re.compile(
    r"^(?P<kind>PART|CHAPTER|DIVISION|Part|Chapter)\s+(?P<id>\d+[A-Z]?|[IVXLC]+[A-Z]?)\b"
    r"[\s:.—–-]*(?P<title>.*)$"
)
_SCHEDULE = re.compile(
    r"^(?:(?:THE\s+)?(?:(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH)\s+)?SCHEDULE(?:\s+\d+[A-Z]?)?\b|Schedule\s+(?:\d+[A-Z]?|[IVXLC]+)(?:\s*[—–:-].*)?$|JADUAL\s+(?:PERTAMA|KEDUA|KETIGA|KEEMPAT|KELIMA|\d+[A-Z]?)\b|LAWS\s+REPEALED\s+OR\s+AMENDED\b)(?P<rest>.*)$"
)
_DIVISION = re.compile(r"^(?P<label>Division\s+\d+[A-Z]?\s*[—–-]\s*\S.*)$")
_CONTINUED = re.compile(r"continued\s*$", re.IGNORECASE)
_TOC_LEADER = re.compile(r"\.{4,}|…{2,}")

_MAX_HEADING_LEN = 120
_HEADING_LINE_MAX = 80
_MAX_JUMP = 15
_SPARSE_AVG_CHARS = 8000  # 平均每条超过这个字数且很稀疏,说明编号不可靠
_CHUNK_TARGET = 2500


@dataclass
class ClauseDraft:
    ordinal: int
    clause_number: str | None
    heading: str | None
    text: str
    page_start: int
    page_end: int
    section_path: str | None = None


def match_header(line: str) -> tuple[str, str | None] | None:
    """无状态地判断一行是不是条款开头。返回 (编号, 小标题) 或 None。

    注意:数字编号样式在真实切分时还要经过递增校验(见 split_clauses),这里只做样式匹配。
    """
    stripped = line.strip()
    for pattern in _KEYWORD_PATTERNS:
        m = pattern.match(stripped)
        if m:
            rest = m.group("rest").strip()
            heading = rest if rest and len(rest) <= _MAX_HEADING_LEN else None
            return m.group("num"), heading
    nh = _numeric_header(stripped)
    if nh is not None:
        return nh[0], None
    return None


def is_article_heading(line: str) -> bool:
    """A line that opens an article by keyword (Article 5 / المادة (5) / 第5条 / الفصل 5).

    Page-furniture detection must never treat such a line as a repeating page header: with the
    digits normalised, every article heading of a law looks identical."""
    stripped = line.strip()
    return any(pattern.match(stripped) for pattern in _KEYWORD_PATTERNS)


_ALL_DIGITS_TO_ASCII = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩" + "๐๑๒๓๔๕๖๗๘๙" + "０１２３４５６７８９",
    "0123456789" + "0123456789" + "0123456789",
)
_AR_TO_ASCII = _ALL_DIGITS_TO_ASCII
_ARTICLE_RANGE_RE = re.compile(r"^(\d{1,3})\s*[–—-]\s*(\d{1,3})$")


def clean_number(num: str | None) -> str | None:
    """A clause number without the dash that right-to-left typesetting leaves beside it
    (``المادة 1-`` is read as ``-1``), ordinal degree signs (``1º``), or leading colons."""
    if num is None:
        return None
    cleaned = num.strip().strip("-–—−").strip()
    # “المادة :89” 这种排版把冒号留在编号前，条号是 89，不是 “:89”
    cleaned = cleaned.lstrip(":：").strip()
    cleaned = re.sub(r"^(\d+)[.]?[º°]$", r"\1", cleaned)
    cleaned = re.sub(r"^(\d+)\s*조부터\s*제\s*(\d+)$", r"\1–\2", cleaned)
    cleaned = re.sub(r"^(\d+)조의\s*(\d+)$", r"\1의\2", cleaned)
    cleaned = re.sub(r"^(\d+)\s+([a-zA-Z])$", r"\1\2", cleaned)
    rm = _ARTICLE_RANGE_RE.match(cleaned)
    if rm and 1 <= int(rm.group(2)) - int(rm.group(1)) <= 30:
        cleaned = f"{int(rm.group(1))}–{int(rm.group(2))}"
    return cleaned or None


def article_number(num: str | None) -> int | None:
    """The integer of a plain article number: ``12``, ``(12)``, ``-12``, ``1º``, Arabic/Thai/fullwidth digits.
    Lettered (``12a``) and decimal/sub-article (``1.2``, ``28의8``) numbers are not plain article numbers."""
    if not num:
        return None
    cleaned = num.strip().strip("()-–—−").strip().lstrip(":：").strip().translate(_ALL_DIGITS_TO_ASCII)
    cleaned = re.sub(r"^(\d+)[.]?[º°]$", r"\1", cleaned)
    if cleaned.lower() in ("premier", "première", "1er", "1ère", "único", "unico") or cleaned in _AR_FIRST:
        return 1  # 法文、西/葡文、阿文“第一条/唯一条”
    return int(cleaned) if cleaned.isascii() and cleaned.isdigit() and len(cleaned) <= 4 else None


def article_numbers_covered(num: str | None) -> list[int]:
    """Plain article integers accounted for by ``num``, expanding consolidated repealed/omitted
    ranges such as ``17–25`` (`Art. 17–25. (uchylone)` / `64–74 Consequential amendments`)."""
    if not num:
        return []
    cleaned = clean_number(num) or ""
    rm = _ARTICLE_RANGE_RE.match(cleaned.translate(_ALL_DIGITS_TO_ASCII))
    if rm:
        a, b = int(rm.group(1)), int(rm.group(2))
        if 1 <= b - a <= 30:
            return list(range(a, b + 1))
    single = article_number(num)
    return [single] if single is not None else []


_SortKey = tuple[int, tuple[int, ...], str]


def _key(num: str) -> _SortKey:
    """编号的排序键:26A -> (26,(),'A');19.4 -> (19,(4,),'');28의8 -> (28,(8,),'');17–25 -> (25,(),'');premier -> (1,(),'')。"""
    num = num.translate(_ALL_DIGITS_TO_ASCII).strip("()").strip()
    num = re.sub(r"^(\d+)[.]?[º°]$", r"\1", num)
    num = re.sub(r"^(\d+)\s*조부터\s*제\s*(\d+)$", r"\1–\2", num)
    num = re.sub(r"^(\d+)(?:조의|의)\s*(\d+)$", r"\1.\2", num)
    num = re.sub(r"^(\d+)\s+([a-zA-Z])$", r"\1\2", num)
    if num.lower() in ("premier", "1er", "único", "unico") or num in _AR_FIRST:
        return 1, (), ""
    rm = _ARTICLE_RANGE_RE.match(num)
    if rm:
        a, b = int(rm.group(1)), int(rm.group(2))
        if 1 <= b - a <= 30:
            return b, (), ""
    if "." in num or "/" in num or "-" in num:
        parts_str = re.split(r"[./-]", num)
        if all(p.isdigit() for p in parts_str if p):
            parts = [int(p) for p in parts_str if p]
            return parts[0], tuple(parts[1:]), ""
    m = re.match(r"^(\d+)([A-Za-z]*)$", num)
    if not m:
        raise ValueError(num)
    return int(m.group(1)), (), m.group(2).upper()


def _numeric_header(line: str) -> tuple[str, str, str] | None:
    """数字式编号头。返回 (编号, 模式 single/decimal, 行内剩余文字)。"""
    s = re.sub(r"^[|¦]+\s*", "", line.strip())
    m = _DECIMAL.match(s)
    if m:
        return m.group("num"), "decimal", (m.group("rest") or "")
    m = _SG_SECTION.match(s)
    if m:
        num = (
            m.group("num")
            or m.group("num_sub")
            or m.group("num_ocr")
            or m.group("num_rng")
            or m.group("num_bare")
        )
        rest = (
            m.group("rest")
            or m.group("rest_sub")
            or m.group("rest_ocr")
            or m.group("rest_rng")
            or ""
        )
        return num, "single", rest
    m = _SIMPLE.match(s)
    if m:
        return m.group("num"), "single", m.group("rest")
    return None


def _looks_like_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > _HEADING_LINE_MAX:
        return False
    if s[0].islower() or s[0] in "(“\"'—–-0123456789[":
        return False
    if s[-1] in ".;:,—–-":
        return False
    if _PART.match(s) or _SCHEDULE.match(s) or _TOC_LEADER.search(s):
        return False
    return True


def _is_toc_entry(line: str) -> bool:
    """像目录条目的行:PART/Division 标记,或"编号 + 不带句末标点的短标题"。"""
    if _PART.match(line) or _DIVISION.match(line):
        return True
    nh = _numeric_header(line)
    if nh is None:
        return False
    rest = nh[2].strip()
    if not rest:  # 只有编号、没有标题(如指引里单独成行的 19.4):是正文段落号,不是目录条目
        return False
    return len(rest) <= 70 and not rest.endswith((".", ";", ":", ",", "—", "–", "。"))


def _visual_len(line: str) -> int:
    return sum(2 if ("\u0e00" <= ch <= "\u0e7f" or "\u2e80" <= ch <= "\ud7af") else 1 for ch in line)


def _toc_pages(pages: list[str]) -> set[int]:
    """识别目录页(页码从 1 起):多数行是"编号 + 短标题",或带点线页码的短行。

    目录页的文字并入一个"目录"块,不参与条款识别。无点线引导符的目录仅可能出现在文件前部，
    避免把正文后半部含大量短列表项的页面误吞入目录块。
    """
    toc: set[int] = set()
    prev_is_toc = False
    front_limit = max(6, len(pages) // 5)
    for n, page in enumerate(pages, start=1):
        lines = [ln.strip() for ln in page.splitlines() if ln.strip()]
        is_toc = False
        if len(lines) >= 4:
            starts = sum(1 for ln in lines if _is_toc_entry(ln))
            dotted = sum(1 for ln in lines if _TOC_LEADER.search(ln))
            long_lines = sum(1 for ln in lines if _visual_len(ln) > 100)
            if long_lines == 0:
                has_dots = dotted / len(lines) >= 0.4
                in_front = n <= front_limit or prev_is_toc
                threshold = 0.3 if prev_is_toc else 0.4
                is_toc = has_dots or (in_front and starts / len(lines) >= threshold)
            if len(lines) < 8 and not prev_is_toc:
                is_toc = False
        if is_toc:
            toc.add(n)
        prev_is_toc = is_toc
    return toc


def _longest_chain(cands: list[tuple[int, str, str, _SortKey, int]]) -> list[int]:
    """在候选(行号, 编号, 模式, 排序键, 段)中,选出最长的"严格递增且每步跳跃不大"的链,返回候选下标。

    比逐个判断更稳:修订法案里引用的"新增条文"(如 29A.—(1))编号会和正文编号交错,
    逐个判断会被它带偏,取最长链则能保住真正的正文序列。
    """
    n = len(cands)
    if n == 0:
        return []
    best = [1] * n
    prev = [-1] * n
    for i in range(n):
        ki = cands[i][3]
        for j in range(i):
            kj = cands[j][3]
            if ki > kj and ki[0] - kj[0] <= _MAX_JUMP and best[j] + 1 > best[i]:
                best[i], prev[i] = best[j] + 1, j
    end = max(range(n), key=lambda i: (best[i], -cands[i][3][0], -i))
    chain = []
    while end != -1:
        chain.append(end)
        end = prev[end]
    return chain[::-1]


def _choose_numeric(flat: list[tuple[int, str]], toc_pages: set[int]) -> tuple[set[int], int]:
    """决定哪些数字编号行是真正的条款开头。返回 (被接受的行下标集合, 接受的数量)。"""
    cands: list[tuple[int, str, str, _SortKey, int]] = []
    seg = 0
    for idx, (page_no, line) in enumerate(flat):
        if page_no in toc_pages:
            continue
        s = line.strip()
        sm = _SCHEDULE.match(s)
        if sm and not _CONTINUED.search(s) and s.upper() == s:
            seg += 1  # 附表是新的编号段
            continue
        if _PART.match(s) or _DIVISION.match(s):
            continue
        nh = _numeric_header(s)
        if nh is None:
            continue
        try:
            key = _key(nh[0])
        except ValueError:
            continue
        cands.append((idx, nh[0], nh[1], key, seg))

    accepted: set[int] = set()
    for seg_id in sorted({c[4] for c in cands}):
        in_seg = [c for c in cands if c[4] == seg_id]
        best_pick: list[int] = []
        for mode in ("single", "decimal"):
            pool = [c for c in in_seg if c[2] == mode]
            picked: list[int] = []
            remaining = pool
            for round_no in range(4):  # 第一条链是主序列;之后只接受从 1 重新开始、且足够长的序列
                chain = _longest_chain(remaining)
                if not chain:
                    break
                members = [remaining[k] for k in chain]
                if round_no > 0 and not (len(members) >= 5 and members[0][3][0] == 1):
                    break
                picked.extend(m[0] for m in members)
                taken = {m[0] for m in members}
                remaining = [c for c in remaining if c[0] not in taken]
            if len(picked) > len(best_pick):
                best_pick = picked
        accepted.update(best_pick)
    return accepted, len(accepted)


@dataclass
class _Raw:
    number: str | None
    heading: str | None
    lines: list[str]
    page_start: int
    page_end: int
    path: str | None
    seq: int  # 所属编号序列(目录/正文/附表各是一个序列)


# 行中间的条标题：版面把几条排在同一行时（“……。الفصل 12 : يتولى ……”），条标题不在行首，
# 按行首识别会把几条并成一条。只在“编号恰好接着上一条往下数”时才从行中拆开，
# 前面是字母的“بالفصل 12”之类引用、没有冒号或破折号的“الفصل 12 من هذا القانون”都不会被拆
_INLINE_HEADING = re.compile(
    r"(?:(?<![\w؀-ۿ])(?:(?:ال)?فصل|(?:ال[\ufffe]*م(?:س)?|ام[\ufffe]*ل|م(?:س)?)[\ufffe]*ادة|(?:Art|ART)(?:icle\b|[.,])?)\s*[:：]?\s*"
    r"(?P<num>\d{1,3})\s*(?:[:：.]|\s[-–—]\s)"
    r"|(?<=[.!?]\s)(?P<num_cyr>\d{1,2})(?=\.\s+[А-ЯЁ]))"
)
_LEADING_NUMBER = re.compile(
    r"^\s*(?:(?:(?:ال)?فصل|(?:ال[\ufffe]*م(?:س)?|ام[\ufffe]*ل|م(?:س)?)[\ufffe]*ادة|(?:Art|ART)(?:icle\b|[.,])?)[\s:：]*\(?(?P<num>\d{1,3})\b"
    r"|(?P<num_cyr>\d{1,2})\.\s+[А-ЯЁ])"
)
_INLINE_MAX_SKIP = 2


def split_inline_headings(pages: list[str]) -> list[str]:
    """Break a line before an article heading that sits in the middle of it."""
    last = 0
    out: list[str] = []
    for page in pages:
        lines: list[str] = []
        for line in page.splitlines():
            lead = _LEADING_NUMBER.match(line)
            if lead:
                n = int(lead.group("num") or lead.group("num_cyr"))
                if last < n <= last + _INLINE_MAX_SKIP + 1 or n == 1:
                    last = n
            cut = 0
            while True:
                found = next(
                    (
                        m
                        for m in _INLINE_HEADING.finditer(line, cut + 1 if cut else 1)
                        if last < int(m.group("num") or m.group("num_cyr")) <= last + _INLINE_MAX_SKIP
                    ),
                    None,
                )
                if found is None:
                    break
                lines.append(line[: found.start()].rstrip())
                line = line[found.start() :]
                last = int(found.group("num") or found.group("num_cyr"))
                cut = 0
            lines.append(line)
        out.append("\n".join(x for x in lines if x.strip()))
    return out


def _split_numbered(pages: list[str]) -> tuple[list[_Raw], bool]:
    raws: list[_Raw] = []
    current: _Raw | None = None
    path: str | None = None
    part_label: str | None = None
    division_label: str | None = None
    schedule_label: str | None = None
    toc_pages = _toc_pages(pages)
    toc_raw: _Raw | None = None
    last_key: _SortKey | None = None
    kw_last: _SortKey | None = None
    seq = 0

    def compute_path() -> str | None:
        return " / ".join(x for x in (schedule_label, part_label, division_label) if x) or None

    def new_sequence() -> None:
        nonlocal last_key, kw_last, seq
        last_key, kw_last = None, None
        seq += 1

    flat: list[tuple[int, str]] = []
    atomic = {}
    for page_no, page in enumerate(pages, start=1):
        lines = [line.rstrip() for line in page.splitlines() if line.strip()]
        blocks = atomic_line_blocks("\n".join(lines))
        base = len(flat)
        for start, (end, label) in blocks.items():
            atomic[base + start] = (base + end, label)
        if blocks:
            toc_pages.discard(page_no)
        flat.extend((page_no, line) for line in lines)

    accepted_numeric, numeric_count = _choose_numeric(flat, toc_pages)
    keyword_count = sum(
        1
        for pg, ln in flat
        if pg not in toc_pages and any(pat.match(ln.strip()) for pat in _KEYWORD_PATTERNS)
    )
    # 法国式法律：条用“Article N / ART. N”，章节用“Section 1. – De la …”。
    # 文件里已有足够多的条标题时，
    # “Section N”是章节标题，不是条——否则它把条号序列重置为 1，后面整段条被当成交叉引用丢掉
    article_heads = sum(
        1
        for pg, ln in flat
        if pg not in toc_pages
        and (_ARTICLE_HEAD.match(ln.strip()) or _FASL_NUMBERED.match(ln.strip()) or _MADDA_NUMBERED.match(ln.strip()))
    )
    numeric_style = (
        numeric_count >= 2
        and numeric_count >= keyword_count
        and not (article_heads >= 5 and numeric_count < article_heads * 4)
    )
    sections_are_structure = article_heads >= 5
    fasl_heads = sum(
        1 for pg, ln in flat if pg not in toc_pages and _FASL_NUMBERED.match(ln.strip())
    )
    madda_heads = sum(
        1 for pg, ln in flat if pg not in toc_pages and _MADDA_NUMBERED.match(ln.strip())
    )
    first_as_article = fasl_heads >= 5 and madda_heads < 5
    # where each article number is headed in the text: a heading that jumps ahead is a reference to
    # another article ("Article 34 of this Act") when the number that should come next still
    # follows later in the text
    heads_by_number: dict[int, list[int]] = {}
    for idx, (pg, ln) in enumerate(flat):
        if pg in toc_pages:
            continue
        s_ln = ln.strip()
        if sections_are_structure and (_SECTION_WORD.match(s_ln) or _PARAGRAPH_SYMBOL.match(s_ln)):
            continue
        for pat in _KEYWORD_PATTERNS:
            hm = pat.match(s_ln)
            if hm:
                covered = article_numbers_covered(hm.group("num"))
                if covered:
                    for c_num in covered:
                        heads_by_number.setdefault(c_num, []).append(idx)
                else:
                    try:
                        hk = _key(hm.group("num"))
                    except ValueError:
                        break
                    if not hk[2]:
                        heads_by_number.setdefault(hk[0], []).append(idx)
                break

    i = 0
    while i < len(flat):
        page_no, line = flat[i]
        s = line.strip()

        if i in atomic:
            end, label = atomic[i]
            new_sequence()
            raws.append(
                _Raw(
                    None,
                    label,
                    [ln for _, ln in flat[i:end]],
                    page_no,
                    flat[end - 1][0],
                    label,
                    seq,
                )
            )
            current = None
            i = end
            continue

        # ---- 目录页:整页并入"目录"块,不参与条款识别 ----
        if page_no in toc_pages:
            if toc_raw is None:
                toc_raw = _Raw(None, "目录(自动识别)", [], page_no, page_no, None, seq)
                raws.append(toc_raw)
            toc_raw.lines.append(line)
            toc_raw.page_end = page_no
            current = None
            i += 1
            continue

        # ---- 结构标记:附表 ----
        sm = _SCHEDULE.match(s)
        if sm and _CONTINUED.search(s):  # "THE SCHEDULE — continued" 是续页标记,丢弃
            i += 1
            continue
        if sm and s.upper() == s:
            schedule_label = s
            part_label = None
            division_label = None
            path = compute_path()
            new_sequence()
            current = None
            i += 1
            continue

        # ---- 结构标记:PART/CHAPTER ----
        pm = _PART.match(s)
        if (
            pm
            and not _TOC_LEADER.search(s)
            and (pm.group("kind").isupper() or not pm.group("title"))
        ):
            title_parts = [pm.group("title").strip()] if pm.group("title").strip() else []
            consumed = 1
            j = i + 1
            while j < len(flat) and len(title_parts) < 3 and flat[j][0] not in toc_pages:
                nxt = flat[j][1].strip()
                if (
                    nxt
                    and nxt.upper() == nxt
                    and any(ch.isalpha() for ch in nxt)
                    and len(nxt) <= 80
                    and not _numeric_header(nxt)
                    and not _PART.match(nxt)
                    and not _SCHEDULE.match(nxt)
                ):
                    title_parts.append(nxt)
                    consumed += 1
                    j += 1
                else:
                    break
            title = " ".join(title_parts)
            part_label = f"{pm.group('kind').upper()} {pm.group('id')}" + (
                f" {title}" if title else ""
            )
            division_label = None
            path = compute_path()
            current = None
            i += consumed
            continue

        # ---- 结构标记:Division ----
        dm = _DIVISION.match(s)
        if dm and not _TOC_LEADER.search(s):
            division_label = dm.group("label")
            path = compute_path()
            current = None
            i += 1
            continue

        # ---- 关键词/中文/阿拉伯文编号 ----
        header: tuple[str, str | None] | None = None
        for pattern in (*_KEYWORD_PATTERNS, *([_FASL_FIRST] if first_as_article else [])):
            m = pattern.match(s)
            if m:
                rest = m.group("rest").strip()
                header = (m.group("num"), rest if rest and len(rest) <= _MAX_HEADING_LEN else None)
                break
        if header is not None and sections_are_structure and (
            _SECTION_WORD.match(s) or _PARAGRAPH_SYMBOL.match(s)
        ):
            division_label = s
            path = compute_path()
            current = None
            i += 1
            continue
        if header is not None:
            try:
                hkey: _SortKey | None = _key(header[0])
            except ValueError:
                hkey = None  # 中文数字等无法比较:不做递增校验
            ok = True
            prev_tail = current.lines[-1].strip() if current and current.lines else ""
            # 行首紧接上一行介词（如 "... بالعددين 3 و4 من\nالفصل 10 من مجلة..." 或 "... under\nSection 5 of..."）是句内交叉引用，绝不是新条标题
            if prev_tail and re.search(
                r"(?:\b(?:of|under|in|to|from|au|aux|du|des|de|dans|sur|par)|(?:^|\s)(?:من|في|إلى|على|عن|بمقتضى|طبقا))\s*$",
                prev_tail,
                re.IGNORECASE,
            ):
                ok = False
            elif re.match(
                r"^(?:(?:ال)?فصل|(?:ال[\ufffe]*م(?:س)?|ام[\ufffe]*ل|م(?:س)?)[\ufffe]*ادة)\s+\d+\s+من\s+(?:مجلة|القانون|المرسوم|الأمر|هذا)\b"
                r"|^(?:Article|ARTICLE|Section|SECTION|Clause|CLAUSE|Rule|RULE|Art\.?|ART\.?|Artikel|Art[ií]culo|Artigo|Articolo|Điều|Madde|Pasal)\s+\(?\d+[A-Za-z]?\)?\s+(?:of|under|in|to|TFEU|TEU|du|de|des|der|del|della)\b",
                s,
            ):
                ok = False
            elif numeric_style:
                ok = False  # 本文件用数字编号,行首的 "Article 29 of ..." 是交叉引用
            elif (
                kw_last is None
                and hkey is not None
                and hkey[0] > 5
                and any(j > i for j in heads_by_number.get(1, ()))
            ):
                ok = False  # 序言/导言段落在第 1 条之前行首引用的他法条号（如 GDPR 序言引 Article 263 TFEU）
            elif hkey is not None and kw_last is not None:
                if hkey > kw_last and hkey[0] - kw_last[0] <= _MAX_JUMP:
                    ok = not (
                        hkey[0] - kw_last[0] > 1
                        and any(j > i for j in heads_by_number.get(kw_last[0] + 1, ()))
                    )
                elif hkey[0] == 1 and hkey[1][:1] <= (1,) and kw_last[0] > 3 and not hkey[2]:
                    new_sequence()  # 回到 1:新的序列(如附件)
                else:
                    ok = False  # 行首的交叉引用(如 "Article 29 of the GDPR ..."),不是新条款
            if ok:
                if hkey is not None:
                    kw_last = hkey
                current = _Raw(header[0], header[1], [line], page_no, page_no, path, seq)
                raws.append(current)
                i += 1
                continue

        # ---- 数字式编号(需递增校验)----
        nh = _numeric_header(s)
        if nh is not None and numeric_style and i in accepted_numeric:
            num, nmode, _rest = nh
            key = _key(num)
            accept = True
            if accept:
                if last_key is not None and key < last_key:
                    new_sequence()  # 编号回落:新的序列(如附件、正文跟在目录之后)
                last_key = key
                heading_lines: list[str] = []
                if nmode == "single" and current is not None:
                    # 条标题是编号行的前一行(最多两行,以大写开头、不以句号等结尾)
                    floor = 1 if current.number is not None else 0
                    while (
                        len(heading_lines) < 2
                        and len(current.lines) > floor
                        and _looks_like_heading(current.lines[-1])
                    ):
                        heading_lines.insert(0, current.lines.pop().strip())
                head_text = " ".join(heading_lines) if heading_lines else None
                current = _Raw(num, head_text, [*heading_lines, line], page_no, page_no, path, seq)
                current.page_start = page_no
                raws.append(current)
                i += 1
                continue

        # ---- 普通行 ----
        if current is None:
            current = _Raw(None, None, [], page_no, page_no, path, seq)
            raws.append(current)
        current.lines.append(line)
        current.page_end = page_no
        i += 1
    return raws, numeric_style


_SENT_END_CHARS = (".", ":", ";", "?", "!", "؟", "؛", "。", "；", "！", "？", "”", "»", ")", "」", "』")


def _chunk_by_size(pages: list[str]) -> list[ClauseDraft]:
    """没有可靠条款编号时,按约 2500 字在完整句末标点断开,记录页码范围。条款编号为空。"""
    drafts: list[ClauseDraft] = []
    block: list[str] = []
    size = 0
    start = end = 1
    for page_no, page in enumerate(pages, start=1):
        lines = [ln.rstrip() for ln in page.splitlines() if ln.strip()]
        for idx, line in enumerate(lines):
            if not block:
                start = page_no
            block.append(line)
            end = page_no
            size += len(line)
            at_page_end = idx == len(lines) - 1
            if size >= _CHUNK_TARGET and (
                line.endswith(_SENT_END_CHARS) or (size >= 8500 and at_page_end)
            ):
                drafts.append(ClauseDraft(len(drafts), None, None, "\n".join(block), start, end))
                block, size = [], 0
    if block:
        drafts.append(ClauseDraft(len(drafts), None, None, "\n".join(block), start, end))
    return drafts


def _drop_stray_lead(raws: list[_Raw]) -> list[_Raw]:
    """A note in the head of a document ("Art. 13 ... entered into force") can look like the
    first article. When the numbering then restarts lower, in a sequence of its own, the first
    block was not a clause: its text stays with the preamble and it loses the number."""
    numbered = [i for i, r in enumerate(raws) if r.number is not None]
    if len(numbered) < 3:
        return raws
    first, second = numbered[0], numbered[1]
    try:
        k1, k2 = _key(raws[first].number or ""), _key(raws[second].number or "")
    except ValueError:
        return raws
    # a real sequence (a contents list before the body, an annex after it) has several members;
    # a lone numbered block that is followed by a sequence restarting lower is a note
    alone = all(r.seq != raws[first].seq for r in raws[first + 1 :] if r.number is not None)
    if k2 >= k1 or not alone:
        return raws
    lead = raws[first]
    if first > 0 and raws[first - 1].number is None:
        raws[first - 1].lines.extend(lead.lines)
        raws[first - 1].page_end = lead.page_end
        return [r for i, r in enumerate(raws) if i != first]
    lead.number, lead.heading = None, None
    return raws


def _build(raws: list[_Raw]) -> list[ClauseDraft]:
    raws = _drop_stray_lead(raws)
    drafts: list[ClauseDraft] = []
    for r in raws:
        text = "\n".join(r.lines).strip()
        if not text:
            continue
        drafts.append(
            ClauseDraft(
                ordinal=len(drafts),
                clause_number=clean_number(r.number),
                heading=r.heading,
                text=text,
                page_start=r.page_start,
                page_end=r.page_end,
                section_path=r.path,
            )
        )
    return drafts


def split_clauses(
    pages: list[str], *, paragraph_mode: str = "blank"
) -> tuple[list[ClauseDraft], str]:
    """返回 (条款列表, 策略名)。strategy: numbered / paragraph / line / empty。

    paragraph_mode: 退化为段落切分时的依据,blank=空行(文本),line=每行(HTML)。
    """
    raws, numeric_style = _split_numbered(split_inline_headings(pages))
    numbered = [r for r in raws if r.number is not None]
    if any(r.path and r.path.startswith(("TABLE /", "REFERENCE /")) for r in raws):
        return _build(raws), "layout"
    if len(numbered) >= 2:
        total_chars = sum(len(pg) for pg in pages)
        plain_nums = [n for r in numbered for n in article_numbers_covered(r.number)]
        partial_numeric = (
            len(pages) >= 12
            and len(plain_nums) >= 5
            and (
                (numeric_style and (len(set(plain_nums)) / max(plain_nums)) < 0.85)
                or (
                    len(numbered) / len(pages) < 0.5
                    and (len(set(plain_nums)) / max(plain_nums)) < 0.75
                )
            )
        )
        sparse = (
            len(pages) >= 20
            and len(numbered) / len(pages) < 0.15
            and total_chars / len(numbered) > _SPARSE_AVG_CHARS
        ) or partial_numeric
        if not sparse:
            return _build(raws), "numbered"
        if paragraph_mode == "blank":
            return _chunk_by_size(pages), "chunk"

    paras: list[ClauseDraft] = []
    block: list[str] = []
    start = end = 1

    def flush() -> None:
        nonlocal block
        text = "\n".join(block).strip()
        if text:
            paras.append(ClauseDraft(len(paras), None, None, text, start, end))
        block = []

    for page_no, page in enumerate(pages, start=1):
        for line in page.splitlines():
            if not line.strip():
                if paragraph_mode == "blank":
                    flush()
                continue
            if paragraph_mode == "line":
                flush()
            if not block:
                start = page_no
            block.append(line.rstrip())
            end = page_no
    flush()
    if not paras:
        return [], "empty"
    if paragraph_mode == "blank" and len(paras) == 1 and len(pages) > 1:
        # PDF 文字里没有可靠的空行:按页切分,但若页尾停在半句中,将跨页续句拉入上一块避免断句
        per_page = [
            ClauseDraft(n, None, None, p.strip(), n + 1, n + 1)
            for n, p in enumerate(pages)
            if p.strip()
        ]
        k = 0
        while k + 1 < len(per_page):
            cur_lines = per_page[k].text.splitlines()
            nxt_lines = per_page[k + 1].text.splitlines()
            if cur_lines and nxt_lines and not cur_lines[-1].rstrip().endswith(_SENT_END_CHARS):
                take = 0
                for idx_ln, ln in enumerate(nxt_lines):
                    if idx_ln >= 8:
                        break
                    take = idx_ln + 1
                    if ln.rstrip().endswith(_SENT_END_CHARS):
                        break
                if 0 < take < len(nxt_lines):
                    per_page[k].text = "\n".join(cur_lines + nxt_lines[:take]).strip()
                    per_page[k].page_end = per_page[k + 1].page_start
                    per_page[k + 1].text = "\n".join(nxt_lines[take:]).strip()
                elif take == len(nxt_lines):
                    per_page[k].text = "\n".join(cur_lines + nxt_lines).strip()
                    per_page[k].page_end = per_page[k + 1].page_end
                    per_page.pop(k + 1)
                    continue
            k += 1
        for i, d in enumerate(per_page):
            d.ordinal = i
        return per_page, "page"
    return paras, "paragraph" if paragraph_mode == "blank" else "line"
