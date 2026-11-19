# ruff: noqa: E501
"""The regulation library as users see it — one definition for every count and list.

A document is in the user's library when it is available (no safety hold), not judged out of
scope, not peripheral to the automotive cross-border data business (app.knowledge.business_topic:
do-not-call register, lists of statutory bodies… — kept for completeness, found by name search),
and its latest parsed version has at least one clause. Its size is counted in articles
(``reading.reading_units``), not in stored rows. The country list, the regulation browser and the
country briefing all use this, so the same country shows the same number everywhere.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.models import Clause, Document, DocumentVersion
from app.ingestion.safety import document_available
from app.knowledge.reading import reading_units


def in_scope():
    return Document.relevance.notin_(("irrelevant", "out_of_scope"))


@dataclass
class _Light:
    id: uuid.UUID
    ordinal: int
    clause_number: str | None
    heading: str | None
    page_start: int | None
    original_text: str


@dataclass
class Entry:
    document: Document
    version: DocumentVersion
    articles: int


async def light_rows(session: AsyncSession, version_ids: list) -> dict:
    """version id → its clause rows with just enough text to tell articles apart."""
    if not version_ids:
        return {}
    rows = (
        await session.execute(
            select(
                Clause.version_id,
                Clause.id,
                Clause.ordinal,
                Clause.clause_number,
                Clause.heading,
                Clause.page_start,
                func.left(Clause.original_text, 80),
                func.length(Clause.original_text),
            ).where(Clause.version_id.in_(version_ids))
        )
    ).all()
    out: dict = {}
    for vid, cid, ordinal, number, heading, page, head, length in rows:
        # 只取开头 80 字；长度超过的补占位，让“短注记”的判断仍按全文长度
        text = head if (length or 0) <= 80 else head + " " * ((length or 0) - 80)
        out.setdefault(vid, []).append(_Light(cid, ordinal, number, heading, page, text))
    return out


async def latest_parsed(session: AsyncSession, document_ids: list) -> dict:
    if not document_ids:
        return {}
    versions = (
        await session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id.in_(document_ids), DocumentVersion.state == "parsed")
            .order_by(DocumentVersion.document_id, DocumentVersion.version_no.desc())
        )
    ).all()
    out: dict = {}
    for v in versions:
        out.setdefault(v.document_id, v)
    return out


async def library(session: AsyncSession, country_code: str | None = None, *, include_peripheral: bool = False) -> list[Entry]:
    """The documents of a country (or of every country) in the user's library. ``include_peripheral``:
    also the documents kept only for completeness (for a search by name)."""
    stmt = select(Document).where(document_available(), in_scope()).order_by(Document.title)
    if not include_peripheral:
        stmt = stmt.where(or_(Document.business_weight.is_(None), Document.business_weight != "peripheral"))
    if country_code:
        stmt = stmt.where(Document.country_code == country_code.upper())
    docs = list((await session.scalars(stmt)).all())
    latest = await latest_parsed(session, [d.id for d in docs])
    rows = await light_rows(session, [v.id for v in latest.values()])
    out = []
    for d in docs:
        v = latest.get(d.id)
        if v is None or not rows.get(v.id):
            continue
        out.append(Entry(d, v, len(reading_units(rows[v.id]))))
    return out


async def articles_in(session: AsyncSession, version_id) -> int:
    rows = await light_rows(session, [version_id])
    return len(reading_units(rows.get(version_id, [])))


USABLE_TRANSLATION = ("approved", "machine_translated", "pending_review")


async def article_of(session: AsyncSession, clause_id, cache: dict | None = None):
    """The article a stored row belongs to: (head row, its rows in reading order, Chinese text or
    ""). The Chinese text is joined from each row's usable translation; a row without one is
    marked where it falls."""
    from app.knowledge.models import ClauseTranslation
    from app.knowledge.reading import joined

    cache = {} if cache is None else cache
    clause = await session.get(Clause, clause_id)
    if clause is None:
        return None
    if clause.version_id not in cache:
        rows = list(
            (
                await session.scalars(select(Clause).where(Clause.version_id == clause.version_id))
            ).all()
        )
        cache[clause.version_id] = reading_units(rows)
    unit = next((u for u in cache[clause.version_id] if clause.id in u.ids), None)
    if unit is None:
        return None
    trs = (
        await session.scalars(
            select(ClauseTranslation).where(
                ClauseTranslation.clause_id.in_(unit.ids),
                ClauseTranslation.is_current.is_(True),
                ClauseTranslation.quality_check_passed.is_(True),
                ClauseTranslation.review_status.in_(USABLE_TRANSLATION),
            )
        )
    ).all()
    by = {}
    for tr in trs:
        by.setdefault(tr.clause_id, []).append(tr)
    parts = [by[r.id][0].translated_text if len(by.get(r.id, [])) == 1 else None for r in unit.rows]
    zh = joined([p or "〔此段暂无中文〕" for p in parts]) if any(parts) else ""
    return unit.head, unit.rows, zh


_RELATIONS_CACHE: dict[frozenset, tuple[float, dict]] = {}


async def relation_counts(session: AsyncSession, version_ids: list) -> dict:
    """version id → relations shown under “关系解读” (same filters as the reader)."""
    import time

    if not version_ids:
        return {}
    key = frozenset(version_ids)
    from app.knowledge.cache_sync import use_cached

    hit = _RELATIONS_CACHE.get(key)
    if use_cached(hit):
        return hit[1]
    from sqlalchemy.orm import aliased

    from app.knowledge.models import KgEdge, KgNode
    from app.knowledge.user_relations import reader_edge_conditions

    src, dst = aliased(KgNode), aliased(KgNode)
    rows = await session.execute(
        select(Clause.version_id, func.count(KgEdge.id))
        .join(Clause, Clause.id == KgEdge.evidence_clause_id)
        .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(src, src.id == KgEdge.source_node_id)
        .join(dst, dst.id == KgEdge.target_node_id)
        .where(
            Clause.version_id.in_(version_ids),
            *reader_edge_conditions(src, dst),
        )
        .group_by(Clause.version_id)
    )
    counts = dict(rows.all())
    _RELATIONS_CACHE[key] = (time.monotonic(), counts)
    return counts


SIDE_TITLE = r"amendment|修正|[（(]修订[)）]|修订法|\bbill\b|法案|explanatory|解释性|commencement|生效公告"


def main_law(entries: list[Entry], relations: dict) -> Entry | None:
    """The country's central law for data compliance: a law in force as enacted (not an amending
    act, a bill or an explanatory note) with the most relations in the graph, then the most
    articles. The page opens it first and the briefing names it."""
    import re

    from app.knowledge.router import public_doc_type

    def side(e: Entry) -> bool:
        return bool(re.search(SIDE_TITLE, f"{e.document.title} {e.document.title_zh or ''}", re.I))

    laws = [e for e in entries if public_doc_type(e.document.document_type, e.document.instrument_kind) == "LAW"]
    # 车辆数据跨境首先是个人数据保护与跨境传输：该类的核心法律优先，其次任一核心法律
    pd_core = [e for e in laws if e.document.business_topic == "personal_data" and e.document.business_weight == "core"]
    core = [e for e in laws if e.document.business_weight == "core"]
    for pool in (pd_core, core, [e for e in laws if not side(e) and e.document.relevance == "relevant"], [e for e in laws if not side(e)], laws):
        if pool:
            return max(pool, key=lambda e: (relations.get(e.version.id, 0), e.articles))
    return None
