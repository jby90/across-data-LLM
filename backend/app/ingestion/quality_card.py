# ruff: noqa: E501
"""Intake quality card: one verdict per document on whether its parsed text can be relied on.

Every new text, whatever its country, language or how it was read (text layer or OCR), is judged by
the same measures, so adding sources cannot quietly lower the accuracy of recognition, review and
translation. A document below a threshold is listed with the reason, to be fixed or left out of
citations; thresholds are plain constants so they can be tuned in one place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ingestion.clauses import article_number

MIN_READABLE = 0.90  # 正文字符中属于该语言文字的比例
MIN_NUMBER_CONTINUITY = 0.90  # 条号 1..最大号 中实际出现的比例
# 修订法、指南的条号本来就不连续（只列被修改的条）；这类文件不按条号连续率判定
_NUMBERING_NOT_EXPECTED = ("AMENDMENT", "GUIDANCE")
MIN_TRANSLATED = 0.95  # 需翻译条款中已有合格译文的比例

_SCRIPTS = {
    "ar": re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]"),
    "zh": re.compile(r"[\u4e00-\u9fff]"),
}
_LATIN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
_LETTER = re.compile(r"[^\W\d_]")
_BROKEN = re.compile(r"[\uFFFD\uE000-\uF8FF\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass(frozen=True)
class ClauseSample:
    clause_number: str | None
    text: str
    translated: bool  # 已有合格译文


@dataclass
class QualityCard:
    clauses: int
    readable: float | None
    number_continuity: float | None  # 条款没有数字编号（指南、手册）时为 None
    duplicate_numbers: float
    translated: float | None  # 没有需翻译条款时为 None
    problems: list[str] = field(default_factory=list)
    scope_note: str = ""  # 按业务相关章节入库时：范围内多少条、已译多少条

    @property
    def passed(self) -> bool:
        return not self.problems


_FOREIGN_WORD = re.compile(r"[A-Za-z]{3,}")
_LEGIT_LATIN = re.compile(r"[A-Z]?[a-z]+|[A-Z]{2,6}|(?:[A-Z][a-z]+){2,3}")  # 末一种：CamScanner 这类品牌名
_LATIN_TOKEN = re.compile(r"[A-Za-z0-9]+")
_LATIN_WORD_OK = re.compile(r"[A-Z]?[a-z]+|[A-Z]{1,12}|(?:[A-Z][a-z]+){2,3}|\d+[a-z]{0,2}|[A-Z]\d+|\d+")


def _foreign_line(line: str, script) -> bool:
    """A whole line in another script inside a non-Latin text: an English letterhead or address
    (“Customs Systems & Procedures Sector”, “ALEXANDRIA PORT, GATE 14, A3 BUILDING, 4th FLOOR”) or a
    bare number like “D.2.1”. It is content, not a misreading, so it is left out of the measure.
    Garbage (“toXIM WICYOEO”) is not: it needs ordinary words with lower-case letters to pass."""
    if any(script.match(ch) for ch in line):
        return False
    tokens = _LATIN_TOKEN.findall(line)
    letters = sum(1 for ch in line if ch.isascii() and ch.isalpha())
    if letters <= 2:
        return True
    return len(tokens) >= 2 and all(_LATIN_WORD_OK.fullmatch(t) for t in tokens) and any(re.search(r"[a-z]", t) for t in tokens)


MIN_TRANSLATABLE_WORDS = 5  # 与实现报告口径一致：词数少于它的短片段不进译文覆盖的分母


def is_short_fragment(text: str) -> bool:
    """A table cell or a label with fewer than ``MIN_TRANSLATABLE_WORDS`` words ("4. 100,000 $0.012
    per number"). It is not prose a translation can be judged on by itself; the translation gate
    still fails it standing alone (it needs its real header), so it is outside the share of
    clauses that "must be translated" rather than counted as translated."""
    return len(re.findall(r"[^\W\d_]+", text or "")) < MIN_TRANSLATABLE_WORDS


def _readable_ratio(texts: list[str], language: str) -> float | None:
    script = _SCRIPTS.get(language.split("-")[0].lower(), _LATIN)
    letters = good = broken = 0
    for text in texts:
        scored = text
        if script is not _LATIN:
            # 整行是另一种文字的（英文信头、地址、编号）是内容，不是乱码：整行不计
            scored = "\n".join(ln for ln in text.split("\n") if not _foreign_line(ln, script))
            # 他种文字的正常词（英文抬头、机构缩写、网址）是内容，不是乱码：不计入分母。
            # 乱码长得不一样（“toXIM”“WICYOEO”：词中夹大写、或很长的全大写串）
            scored = _FOREIGN_WORD.sub(
                lambda m: "" if _LEGIT_LATIN.fullmatch(m.group(0)) else m.group(0), scored
            )
        found = _LETTER.findall(scored)
        letters += len(found)
        good += sum(1 for ch in found if script.match(ch))
        broken += len(_BROKEN.findall(text))
    if letters == 0:
        return None
    return max(0.0, (good - broken) / letters)


def todo(card: QualityCard, pages: list[int], scanned: bool) -> list[dict]:
    """入库质量页“还要做什么”：每件事一句话，配一个去做的动作（review 去核对 / translate 补译）。检查通过的不列。"""
    out = []
    for p in card.problems:
        if p.startswith("有 ") and "可能识别错了" in p:
            where = ("第 " + "、".join(map(str, sorted(set(pages))[:6])) + " 页") if pages else ""
            out.append({"text": f"{where}{p.replace('有 ', '有 ', 1)}" if where else p, "action": "review", "label": "去核对"})
        elif p.startswith("有些条没识别出来"):
            out.append({"text": p.replace("有些条没识别出来（", "有些条没识别出来（"), "action": "review", "label": "去核对"})
        elif p.startswith("还有 ") and p.endswith("没有中文"):
            out.append({"text": p, "action": "translate", "label": "补译"})
        else:
            out.append({"text": p, "action": "review", "label": "去看看"})
    return out


def _pct(v: float) -> str:
    """整数百分比；四舍五入后会和门槛一样时多保留一位，避免“95%，低于 95%”。"""
    whole = f"{v:.0%}"
    return whole if v < 0.9995 and abs(round(v * 100) - v * 100) < 0.05 else f"{v * 100:.1f}%"


def assess(
    clauses: list[ClauseSample],
    language: str,
    *,
    needs_translation: bool,
    instrument_kind: str | None = None,
    in_scope: list[bool] | None = None,
) -> QualityCard:
    """``in_scope`` (one flag per clause) marks a document taken in by its business-relevant
    sections: translation coverage is then counted over the in-scope clauses only; whether the text
    reads well and the numbering is complete are still judged on the whole document."""
    problems: list[str] = []
    if not clauses:
        return QualityCard(0, None, None, 0.0, None, ["没有解析出任何条款"])
    readable = _readable_ratio([c.text for c in clauses], language)
    if readable is not None and readable < MIN_READABLE:
        bad = sum(1 for c in clauses if (r := _readable_ratio([c.text], language)) is not None and r < MIN_READABLE)
        problems.append(f"有 {bad} 段文字可能识别错了")

    numbers = [n for c in clauses if (n := article_number(c.clause_number)) is not None]
    continuity = None
    duplicates = 0.0
    if len(numbers) >= 5 and instrument_kind not in _NUMBERING_NOT_EXPECTED:
        continuity = len(set(numbers)) / max(numbers)
        duplicates = (len(numbers) - len(set(numbers))) / len(numbers)
        if continuity < MIN_NUMBER_CONTINUITY:
            problems.append(f"有 {max(numbers) - len(set(numbers))} 条没识别出来")

    translated = None
    scope_note = ""
    counted = clauses if in_scope is None else [c for c, keep in zip(clauses, in_scope, strict=True) if keep]
    if in_scope is not None:
        scope_note = f"只收与业务相关的章节：{len(counted)} 条（全文 {len(clauses)} 条）"
    if needs_translation and counted:
        translated = sum(1 for c in counted if c.translated) / len(counted)
        if translated < MIN_TRANSLATED:
            missing = sum(1 for c in counted if not c.translated)
            problems.append(f"还有 {missing} 条没有中文")
    return QualityCard(len(clauses), readable, continuity, duplicates, translated, problems, scope_note)


_CARDS_CACHE: dict[tuple, list[dict]] = {}


def clear_cards_cache() -> None:
    _CARDS_CACHE.clear()


async def build_cards(sf, country: str | None = None) -> list[dict]:
    """Same as ``cards_in`` with its own session (used by the command line)."""
    async with sf() as session:
        return await cards_in(session, country)


async def cards_in(s, country: str | None = None) -> list[dict]:
    """A card for the latest parsed version of every in-scope document (optionally one country)."""
    import copy

    from sqlalchemy import exists, func, select, text

    from app.ingestion.intake_scope import scoped
    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge.models import ClauseTranslation
    from app.knowledge.numeric_integrity import is_numeric_only

    cc_key = country.upper() if country else "*"
    stamp_row = (
        await s.execute(
            text(
                "select (select md5(coalesce(string_agg(id::text || country_code || title || relevance || language || coalesce(instrument_kind,'') || coalesce(intake_scope::text,''), ',' order by id), '')) from documents where relevance in ('relevant','related')),"
                " (select md5(coalesce(string_agg(id::text || version_no::text || state || coalesce(text_source,'') || coalesce(review_status,'') || coalesce(check_note,'') || coalesce(legal_status,''), ',' order by id), '')) from document_versions where state='parsed'),"
                " (select count(*)::text || ':' || coalesce(max(id::text), '') || ':' || coalesce(max(updated_at)::text, '') from clause_translations where is_current and quality_check_passed and review_status in ('machine_translated','pending_review','approved'))"
            )
        )
    ).one()
    stamp = (cc_key, *stamp_row)
    cached = _CARDS_CACHE.get(stamp)
    if cached is not None:
        return copy.deepcopy(cached)

    latest = (
        select(
            DocumentVersion.document_id,
            func.max(DocumentVersion.version_no).label("version_no"),
        )
        .where(DocumentVersion.state == "parsed")
        .group_by(DocumentVersion.document_id)
        .subquery()
    )
    query = (
        select(Document, DocumentVersion)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .join(
            latest,
            (latest.c.document_id == Document.id)
            & (latest.c.version_no == DocumentVersion.version_no),
        )
        .where(Document.relevance.in_(("relevant", "related")))
        .order_by(Document.country_code, Document.title)
    )
    if country:
        query = query.where(Document.country_code == country.upper())
    doc_vers = (await s.execute(query)).all()
    if not doc_vers:
        return []

    ver_ids = [v.id for _, v in doc_vers]
    has_tr = exists(
        select(1).where(
            ClauseTranslation.clause_id == Clause.id,
            ClauseTranslation.is_current.is_(True),
            ClauseTranslation.quality_check_passed.is_(True),
            ClauseTranslation.review_status.in_(
                ("machine_translated", "pending_review", "approved")
            ),
        )
    )
    all_clause_rows = (
        await s.execute(
            select(
                Clause.version_id,
                Clause.clause_number,
                Clause.original_text,
                Clause.ordinal,
                Clause.page_start,
                has_tr,
            )
            .where(Clause.version_id.in_(ver_ids))
            .order_by(Clause.version_id, Clause.ordinal)
        )
    ).all()
    by_ver: dict = {}
    for vid, n, t, o, pg, c in all_clause_rows:
        by_ver.setdefault(vid, []).append((n, t, o, pg, bool(c)))

    out = []
    for doc, version in doc_vers:
        rows = by_ver.get(version.id, [])
        inside = scoped(doc.intake_scope, version.id)
        flags = None if inside is None else [o in inside for _, _, o, _, _ in rows]
        card = assess(
            [
                ClauseSample(n, t, bool(c) or is_numeric_only(t) or is_short_fragment(t))
                for n, t, _, _, c in rows
            ],
            doc.language,
            needs_translation=not doc.language.lower().startswith("zh"),
            instrument_kind=doc.instrument_kind,
            in_scope=flags,
        )
        if flags is not None:
            done = sum(1 for (_, _, _, _, c), keep in zip(rows, flags, strict=True) if keep and c)
            card.scope_note += f"，已有中文 {done} 条"
        bad_pages = (
            [pg for _, t, _, pg, _ in rows if pg and (r := _readable_ratio([t], doc.language)) is not None and r < MIN_READABLE]
            if card.readable is not None and card.readable < MIN_READABLE
            else []
        )
        todo_items = todo(card, bad_pages, version.text_source == "ocr")
        # 扫描件还没核对完：和法规文档页说法一致，算“还要处理”
        if version.text_source == "ocr" and version.review_status != "approved" and not any(t["action"] == "review" for t in todo_items):
            todo_items.insert(0, {"text": version.check_note or "扫描件还没对照原文核对", "action": "review", "label": "去核对"})
        out.append(
            {
                "document_id": str(doc.id),
                "todo": todo_items,
                "country": doc.country_code,
                "title": doc.title,
                "language": doc.language,
                "legal_status": version.legal_status,
                "card": card,
            }
        )
    if len(_CARDS_CACHE) > 16:
        _CARDS_CACHE.clear()
    _CARDS_CACHE[stamp] = copy.deepcopy(out)
    return out


def render(cards: list[dict]) -> str:
    def pct(v):
        return "—" if v is None else f"{v:.0%}"

    lines = [
        "| 国家 | 文档 | 语言 | 条款数 | 可读率 | 条号连续 | 译文覆盖 | 结论 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in cards:
        c: QualityCard = row["card"]
        verdict = "达标" if c.passed else "；".join(c.problems)
        title = row["title"].replace("|", "/")[:60]
        lines.append(
            f"| {row['country']} | {title} | {row['language']} | {c.clauses} | {pct(c.readable)} "
            f"| {pct(c.number_continuity)} | {pct(c.translated)} | {verdict} |"
        )
    failed = sum(1 for r in cards if not r["card"].passed)
    lines.append(f"\n共 {len(cards)} 份，未达标 {failed} 份。")
    return "\n".join(lines)
