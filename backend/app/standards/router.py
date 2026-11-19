"""Standards directory for the admin side: counts by status and data class, and the entries."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.iam.deps import Principal, require_platform_admin
from app.standards.models import StandardEntry

router = APIRouter(prefix="/api/admin/standards", tags=["standards"])
AdminDep = Annotated[Principal, Depends(require_platform_admin)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UNCOUNTED = ("withdrawn", "draft")  # 已废止与制定中的列出，但不计入现行数量
KIND_LABEL = {"mandatory": "强制性", "recommended": "推荐性", "guidance": "指导性"}
STATUS_LABEL = {
    "in_force": "现行",
    "upcoming": "即将实施",
    "withdrawn": "已废止",
    "draft": "制定中",
}


@router.get("/summary")
async def summary(_: AdminDep, session: SessionDep) -> dict:
    """Counts of entries that count toward the library; withdrawn and draft ones are only listed."""
    rows = (
        await session.execute(
            select(StandardEntry.country_code, StandardEntry.status, func.count()).group_by(
                StandardEntry.country_code, StandardEntry.status
            )
        )
    ).all()
    by_country: dict[str, dict[str, int]] = {}
    for country, status, n in rows:
        by_country.setdefault(country, {})[status] = n
    by_topic: dict[str, int] = {}
    for (topics,) in await session.execute(
        select(StandardEntry.topics).where(StandardEntry.status.notin_(UNCOUNTED))
    ):
        for topic in topics or []:
            by_topic[topic] = by_topic.get(topic, 0) + 1
    counted = sum(
        n for st in by_country.values() for status, n in st.items() if status not in UNCOUNTED
    )
    return {
        "counted": counted,
        "by_country": by_country,
        "by_topic": by_topic,
    }


@router.get("/items")
async def items(
    _: AdminDep,
    session: SessionDep,
    country: str | None = Query(None, max_length=2),
    status: str | None = Query(None, max_length=16),
    q: str | None = Query(None, max_length=60),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=2147483647),
) -> list[dict]:
    stmt = select(StandardEntry).order_by(
        StandardEntry.published_on.desc().nulls_last(), StandardEntry.number
    )
    if country:
        cc = country.strip().upper()
        if cc in ("CN", "ZZ"):
            stmt = stmt.where(StandardEntry.country_code == cc)
        else:
            cnt_q = select(func.count()).select_from(StandardEntry).where(
                StandardEntry.country_code == cc
            )
            has_own = await session.scalar(cnt_q)
            codes = (cc,) if has_own else (cc, "ZZ")
            stmt = stmt.where(StandardEntry.country_code.in_(codes))
    if status:
        stmt = stmt.where(StandardEntry.status == status)
    if q:
        stmt = stmt.where(
            StandardEntry.title.ilike(f"%{q}%") | StandardEntry.number.ilike(f"%{q}%")
        )
    rows = (await session.scalars(stmt.limit(limit).offset(offset))).all()
    return [
        {
            "id": str(r.id),
            "country_code": r.country_code,
            "number": r.number,
            "title": r.title,
            "kind": KIND_LABEL.get(r.kind, r.kind),
            "status": STATUS_LABEL.get(r.status, r.status),
            "published_on": r.published_on.isoformat() if r.published_on else None,
            "effective_on": r.effective_on.isoformat() if r.effective_on else None,
            "topics": r.topics or [],
            "detail_url": r.detail_url,
            "publisher": r.publisher,
        }
        for r in rows
    ]
