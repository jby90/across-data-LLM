# ruff: noqa: E501
"""Administrator's check of a scanned law's recognised text."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession


async def apply_version_check(session: AsyncSession, version_id: uuid.UUID, approved: bool) -> dict:
    """Record the check of a scanned law's recognised text; once confirmed, the law's own definitions
    enter the term library (they are held back from OCR text until it has been checked)."""
    from app.ingestion.models import DocumentVersion
    from app.knowledge.legal_terms import rebuild_library

    version = await session.get(DocumentVersion, version_id)
    if version is None:
        raise LookupError("版本不存在")
    version.review_status = "approved" if approved else "rejected"
    await session.flush()
    return await rebuild_library(session, None, version.document_id) if approved else {}
