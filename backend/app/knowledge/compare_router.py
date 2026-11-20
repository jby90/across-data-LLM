# ruff: noqa: E501
"""Version comparison for the knowledge base: list a document's versions, compare two of them."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.iam.deps import Principal, require_admin_side, require_user_side
from app.ingestion.layout_safety import clause_layout_available
from app.ingestion.models import Clause, Document, DocumentVersion
from app.ingestion.safety import document_available
from app.knowledge.models import ClauseTranslation
from app.knowledge.version_compare import Side, compare, summarize

router = APIRouter(prefix="/api/knowledge/documents", tags=["knowledge-user"])
UserDep = Annotated[Principal, Depends(require_user_side)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
MAX_ROWS = 400


async def _document(
    session: AsyncSession, document_id: uuid.UUID, *, admin_view: bool = False
) -> Document:
    stmt = select(Document).where(Document.id == document_id)
    if not admin_view:
        stmt = stmt.where(
            document_available(),
            Document.relevance.notin_(("irrelevant", "out_of_scope")),
        )
    doc = (await session.execute(stmt)).scalar_one_or_none()
    if doc is None:
        raise HTTPException(404, "法规不存在")
    return doc


async def _parsed_versions(
    session: AsyncSession, document_id: uuid.UUID, *, admin_view: bool = False
) -> list[DocumentVersion]:
    stmt = select(DocumentVersion).where(
        DocumentVersion.document_id == document_id,
        DocumentVersion.state == "parsed",
    )
    if not admin_view:
        stmt = stmt.where(DocumentVersion.review_status != "rejected")
    return list((await session.scalars(stmt.order_by(DocumentVersion.version_no))).all())


def _kind(versions: list[DocumentVersion], index: int) -> str:
    """A version is an official text (re-readings of the same file are folded into one, see
    ``app.ingestion.version_merge``): named by the official amendment name or publication date."""
    v = versions[index]
    if v.version_label:
        return v.version_label
    return f"{v.published_date.isoformat()} 发布的官方文本" if v.published_date else ("首次收录的官方文本" if index == 0 else "官方修订文本")


async def _list_versions_impl(
    document_id: uuid.UUID, session: AsyncSession, *, admin_view: bool = False
) -> dict:
    doc = await _document(session, document_id, admin_view=admin_view)
    versions = await _parsed_versions(session, doc.id, admin_view=admin_view)
    out = []
    for i, v in enumerate(versions):
        c_stmt = select(Clause.id).where(Clause.version_id == v.id)
        if not admin_view:
            c_stmt = c_stmt.where(clause_layout_available())
        count = (await session.execute(c_stmt)).scalars().all()
        out.append(
            {
                "version_no": v.version_no,
                "kind": _kind(versions, i),
                "fetched_at": v.fetched_at.isoformat() if v.fetched_at else None,
                "published_date": v.published_date.isoformat() if v.published_date else None,
                "clauses": len(count),
            }
        )
    return {"title": doc.title, "versions": out}


@router.get("/{document_id}/versions")
async def list_versions(document_id: uuid.UUID, _: UserDep, session: SessionDep) -> dict:
    return await _list_versions_impl(document_id, session, admin_view=False)


async def _sides(
    session: AsyncSession, version_id: uuid.UUID, *, admin_view: bool = False
) -> list[Side]:
    stmt = select(Clause).where(Clause.version_id == version_id)
    if not admin_view:
        stmt = stmt.where(clause_layout_available())
    rows = (await session.scalars(stmt.order_by(Clause.ordinal))).all()
    return [
        Side(str(c.id), c.ordinal, c.clause_number, c.heading, c.original_text or "") for c in rows
    ]


async def _translations(session: AsyncSession, clause_ids: list[str]) -> dict[str, str]:
    if not clause_ids:
        return {}
    rows = (
        await session.execute(
            select(ClauseTranslation.clause_id, ClauseTranslation.translated_text).where(
                ClauseTranslation.clause_id.in_([uuid.UUID(c) for c in clause_ids]),
                ClauseTranslation.is_current.is_(True),
                ClauseTranslation.quality_check_passed.is_(True),
                ClauseTranslation.review_status.in_(("approved", "machine_translated", "pending_review")),
            )
        )
    ).all()
    buckets: dict[str, list[str]] = {}
    for cid, text in rows:
        buckets.setdefault(str(cid), []).append(text)
    return {k: v[0] for k, v in buckets.items() if len(v) == 1 and v[0]}


def _side_out(s: Side | None, zh: dict[str, str]) -> dict | None:
    if s is None:
        return None
    return {
        "clause_id": s.clause_id,
        "number": s.number,
        "heading": s.heading,
        "text": s.text,
        "translated": zh.get(s.clause_id),
    }


async def _compare_versions_impl(
    document_id: uuid.UUID,
    session: AsyncSession,
    base: int,
    target: int,
    only_changes: bool,
    offset: int,
    *,
    admin_view: bool = False,
) -> dict:
    doc = await _document(session, document_id, admin_view=admin_view)
    if base == target:
        raise HTTPException(422, "请选择两个不同的版本")
    versions = {v.version_no: v for v in await _parsed_versions(session, doc.id, admin_view=admin_view)}
    if base not in versions or target not in versions:
        raise HTTPException(404, "版本不存在")
    pairs = compare(
        await _sides(session, versions[base].id, admin_view=admin_view),
        await _sides(session, versions[target].id, admin_view=admin_view),
    )
    counts = summarize(pairs)
    shown = [p for p in pairs if not only_changes or p.status != "unchanged"]
    page = shown[offset : offset + MAX_ROWS]
    ids = [s.clause_id for p in page for s in (p.base, p.target) if s is not None]
    zh = await _translations(session, ids)
    return {
        "title": doc.title,
        "base_version": base,
        "target_version": target,
        "same_source": versions[base].content_hash == versions[target].content_hash,
        "counts": counts,
        "total_rows": len(shown),
        "offset": offset,
        "rows": [
            {
                "status": p.status,
                "base": _side_out(p.base, zh),
                "target": _side_out(p.target, zh),
                "diff": p.diff,
            }
            for p in page
        ],
    }


@router.get("/{document_id}/compare")
async def compare_versions(
    document_id: uuid.UUID,
    _: UserDep,
    session: SessionDep,
    base: int = Query(ge=1, le=2147483647, description="较早的版本号"),
    target: int = Query(ge=1, le=2147483647, description="较新的版本号"),
    only_changes: bool = True,
    offset: int = Query(0, ge=0, le=2147483647),
) -> dict:
    return await _compare_versions_impl(
        document_id, session, base, target, only_changes, offset, admin_view=False
    )


# 管理端同样可以逐条对照两个版本：核对一部法规改了什么
admin_router = APIRouter(prefix="/api/admin/knowledge/documents", tags=["knowledge-admin"])
AdminSideDep = Annotated[Principal, Depends(require_admin_side)]


@admin_router.get("/{document_id}/versions")
async def admin_list_versions(document_id: uuid.UUID, _user: AdminSideDep, session: SessionDep) -> dict:
    return await _list_versions_impl(document_id, session, admin_view=True)


@admin_router.get("/{document_id}/compare")
async def admin_compare_versions(
    document_id: uuid.UUID,
    _user: AdminSideDep,
    session: SessionDep,
    base: int = Query(ge=1, le=2147483647),
    target: int = Query(ge=1, le=2147483647),
    only_changes: bool = True,
    offset: int = Query(0, ge=0, le=2147483647),
) -> dict:
    return await _compare_versions_impl(
        document_id, session, base, target, only_changes, offset, admin_view=True
    )
