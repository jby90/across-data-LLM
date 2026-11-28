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

from app.ingestion.clauses import article_numbers_covered

MIN_READABLE = 0.90  # 正文字符中属于该语言文字的比例
MIN_NUMBER_CONTINUITY = 0.90  # 条号 1..最大号 中实际出现的比例
# 修订法、指南的条号本来就不连续（只列被修改的条）；这类文件不按条号连续率判定
_NUMBERING_NOT_EXPECTED = ("AMENDMENT", "GUIDANCE")
MIN_TRANSLATED = 0.95  # 需翻译条款中已有合格译文的比例

_CYRILLIC = re.compile(r"[\u0400-\u052F]")
_SCRIPTS = {
    "ar": re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]"),
    "zh": re.compile(r"[\u4e00-\u9fff]"),
    "ja": re.compile(r"[\u3040-\u30FF\u31F0-\u31FF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\uFF66-\uFF9F]"),
    "ko": re.compile(r"[\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F\u4E00-\u9FFF\uF900-\uFAFF]"),
    "th": re.compile(r"[\u0E00-\u0E7F]"),
    "he": re.compile(r"[\u0590-\u05FF]"),
    "bn": re.compile(r"[\u0980-\u09FF]"),
    "ru": _CYRILLIC,
    "kk": _CYRILLIC,
    "ky": _CYRILLIC,
    "uk": _CYRILLIC,
    "be": _CYRILLIC,
    "bg": _CYRILLIC,
    "sr": _CYRILLIC,
    "mk": _CYRILLIC,
}
_LATIN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ\u0100-\u024F\u1E00-\u1EFF]")
_LETTER = re.compile(r"[^\W\d_]")
_NON_LETTER = re.compile(r"[\W\d_]+")
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
_FOREIGN_SHORT_OK = re.compile(r"\b(?:of|in|to|by|or|on|at|as|an|is|be|No)\b")
_LEGIT_LATIN = re.compile(r"[A-Z]?[a-z]+|[A-Z]{2,6}|(?:[A-Z][a-z]+){2,3}")  # 末一种：CamScanner 这类品牌名
_LATIN_TOKEN = re.compile(r"[A-Za-z0-9]+")
_LATIN_WORD_OK = re.compile(r"[A-Z]?[a-z]+|[A-Z]{1,12}|(?:[A-Z][a-z]+){2,3}|\d+[a-z]{0,2}|[A-Z]\d+|\d+")


def _foreign_line(line: str, script) -> bool:
    """A whole line in another script inside a non-Latin text: an English letterhead or address
    (“Customs Systems & Procedures Sector”, “ALEXANDRIA PORT, GATE 14, A3 BUILDING, 4th FLOOR”) or a
    bare number like “D.2.1”. It is content, not a misreading, so it is left out of the measure.
    Garbage (“toXIM WICYOEO”) is not: it needs ordinary words with lower-case letters to pass."""
    if script.search(line):
        return False
    tokens = _LATIN_TOKEN.findall(line)
    letters = len(re.findall(r"[A-Za-z]", line))
    if letters <= 2:
        return True
    if len(tokens) == 1 and re.fullmatch(r"[A-Z]?[a-z]{3,}", tokens[0]):
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
            scored = _FOREIGN_SHORT_OK.sub("", scored)
        alpha_only = _NON_LETTER.sub("", scored)
        letters += len(alpha_only)
        good += len(script.findall(alpha_only))
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

    numbers = [n for c in clauses for n in article_numbers_covered(c.clause_number)]
    decimal_clauses = sum(
        1
        for c in clauses
        if c.clause_number and re.match(r"^\d+\.\d+", c.clause_number.strip())
    )
    continuity = None
    duplicates = 0.0
    # 章节小数制法典（如 1.1..20.20）不用单层整数 1..N 衡量条号连续率
    if (
        len(numbers) >= 5
        and decimal_clauses < len(numbers) * 2
        and instrument_kind not in _NUMBERING_NOT_EXPECTED
    ):
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
_EXEMPT_FRAG_CACHE: dict[tuple, bool] = {}


def clear_cards_cache() -> None:
    _CARDS_CACHE.clear()
    _EXEMPT_FRAG_CACHE.clear()


async def build_cards(sf, country: str | None = None) -> list[dict]:
    """Same as ``cards_in`` with its own session (used by the command line)."""
    async with sf() as session:
        return await cards_in(session, country)


async def cards_in(s, country: str | None = None) -> list[dict]:
    """A card for the latest parsed version of every in-scope document (optionally one country)."""
    import copy

    from sqlalchemy import func, select, text

    from app.ingestion.intake_scope import scoped
    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.ingestion.noise import clean_text
    from app.knowledge.models import ClauseTranslation
    from app.knowledge.numeric_integrity import is_numeric_only

    cc_key = country.upper() if country else "*"
    stamp_row = (
        await s.execute(
            text(
                "select (select md5(coalesce(string_agg(id::text || country_code || title || relevance || language || coalesce(instrument_kind,'') || coalesce(intake_scope::text,''), ',' order by id), '')) from documents where relevance in ('relevant','related')),"
                " (select md5(coalesce(string_agg(id::text || version_no::text || state || coalesce(text_source,'') || coalesce(review_status,'') || coalesce(check_note,'') || coalesce(legal_status,''), ',' order by id), '')) from document_versions where state='parsed'),"
                " (select count(*)::text || ':' || coalesce(max(id::text), '') || ':' || coalesce(max(updated_at)::text, '') from clause_translations where is_current and quality_check_passed and review_status in ('machine_translated','pending_review','approved')),"
                " (select count(*)::text || ':' || md5(coalesce(string_agg(coalesce(clause_number,'') || ':' || text_hash, ',' order by id), '')) from clauses),"
                " (select count(*)::text || ':' || md5(coalesce(string_agg(clause_id::text || ':' || md5(reading_text), ',' order by clause_id), '')) from clause_readings)"
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
        select(
            Document.id,
            Document.country_code,
            Document.title,
            Document.language,
            Document.instrument_kind,
            Document.intake_scope,
            DocumentVersion.id,
            DocumentVersion.text_source,
            DocumentVersion.review_status,
            DocumentVersion.check_note,
            DocumentVersion.legal_status,
        )
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
    doc_snapshots = list((await s.execute(query)).all())
    if not doc_snapshots:
        return []

    ver_ids = [row[6] for row in doc_snapshots]
    qualified_ids = set(
        (
            await s.scalars(
                select(ClauseTranslation.clause_id)
                .where(
                    ClauseTranslation.is_current.is_(True),
                    ClauseTranslation.quality_check_passed.is_(True),
                    ClauseTranslation.review_status.in_(
                        ("machine_translated", "pending_review", "approved")
                    ),
                )
                .group_by(ClauseTranslation.clause_id)
                .having(func.count(ClauseTranslation.id) == 1)
            )
        ).all()
    )
    all_clause_rows = (
        await s.execute(
            select(
                Clause.id,
                Clause.version_id,
                Clause.clause_number,
                Clause.original_text,
                Clause.ordinal,
                Clause.page_start,
            )
            .where(Clause.version_id.in_(ver_ids))
            .order_by(Clause.version_id, Clause.ordinal)
        )
    ).all()
    by_ver: dict = {}
    for cid, vid, n, t, o, pg in all_clause_rows:
        by_ver.setdefault(vid, []).append((n, t, o, pg, cid in qualified_ids))
    del all_clause_rows, qualified_ids

    def _compute_cards_sync() -> list[dict]:
        built_out = []
        for (
            doc_id,
            cc_code,
            title_val,
            lang_val,
            inst_kind,
            intake_sc,
            ver_id,
            txt_src,
            rev_st,
            chk_note,
            leg_st,
        ) in doc_snapshots:
            rows = by_ver.get(ver_id, [])
            inside = scoped(intake_sc, ver_id)
            flags = None if inside is None else [o in inside for _, _, o, _, _ in rows]
            def _is_exempt(ver_u, ord_i: int, txt_s: str) -> bool:
                k = (ver_u, ord_i, hash(txt_s))
                hit_ex = _EXEMPT_FRAG_CACHE.get(k)
                if hit_ex is not None:
                    return hit_ex
                clean_t = clean_text(txt_s)
                val = (
                    is_short_fragment(clean_t)
                    or is_numeric_only(clean_t)
                    or not any(ch.isalpha() for ch in clean_t)
                )
                if len(_EXEMPT_FRAG_CACHE) < 60000:
                    _EXEMPT_FRAG_CACHE[k] = val
                return val

            card = assess(
                [
                    ClauseSample(n, t, bool(c) or _is_exempt(ver_id, o, t))
                    for n, t, o, _, c in rows
                ],
                lang_val,
                needs_translation=not lang_val.lower().startswith("zh"),
                instrument_kind=inst_kind,
                in_scope=flags,
            )
            if flags is not None:
                done = sum(1 for (_, _, _, _, c), keep in zip(rows, flags, strict=True) if keep and c)
                card.scope_note += f"，已有中文 {done} 条"
            bad_pages = (
                [
                    pg
                    for _, t, _, pg, _ in rows
                    if pg and (r := _readable_ratio([t], lang_val)) is not None and r < MIN_READABLE
                ]
                if card.readable is not None and card.readable < MIN_READABLE
                else []
            )
            todo_items = todo(card, bad_pages, txt_src == "ocr")
            # 扫描件还没核对完：和法规文档页说法一致，算“还要处理”
            if (
                txt_src == "ocr"
                and rev_st != "approved"
                and not any(t["action"] == "review" for t in todo_items)
            ):
                todo_items.insert(
                    0, {"text": chk_note or "扫描件还没对照原文核对", "action": "review", "label": "去核对"}
                )
            built_out.append(
                {
                    "document_id": str(doc_id),
                    "todo": todo_items,
                    "country": cc_code,
                    "title": title_val,
                    "language": lang_val,
                    "legal_status": leg_st,
                    "card": card,
                }
            )
        return built_out

    import asyncio

    out = await asyncio.to_thread(_compute_cards_sync)
    by_ver.clear()
    doc_snapshots.clear()
    for k in [k for k in _CARDS_CACHE if k[0] == cc_key]:
        _CARDS_CACHE.pop(k, None)
    if len(_CARDS_CACHE) > 6:
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
