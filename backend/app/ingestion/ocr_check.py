# ruff: noqa: E501
"""Recognition check of a scanned law, passage by passage.

A passage whose recognised text does not read as the law's language (the measure the intake
quality card uses) may have been misread. If none is, and the article numbers run without gaps, the
version is recorded as checked (its own definitions then enter the term library, as after a manual
check). Otherwise the passages are listed for the administrator (``check_items``) with a note in
plain words — “第 1 页有 3 段文字可能识别错了” — to confirm each one (“没错”) or correct it (“改一下”,
kept as the clause's reading text; the archived original stays as it is) next to the page image.
Once every passage is settled the version counts as checked. A decision the administrator made by
hand is never overwritten.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.models import Clause, Document, DocumentVersion
from app.ingestion.quality_card import MIN_READABLE, ClauseSample, _readable_ratio, assess


def _pages(pages: list[int]) -> str:
    pages = sorted(set(pages))
    if not pages:
        return ""
    if len(pages) <= 3:
        return "第 " + "、".join(map(str, pages)) + " 页"
    return f"第 {pages[0]}–{pages[-1]} 页"


def note_for(items: list[dict]) -> str:
    left = [i for i in items if i.get("state") == "pending"]
    if not left:
        return ""
    where = _pages([i["page"] for i in left if i.get("page")])
    return f"{where}有 {len(left)} 段文字可能识别错了" if where else f"有 {len(left)} 段文字可能识别错了"


async def check_recognition(session: AsyncSession, version: DocumentVersion) -> tuple[bool, list[dict], str]:
    doc = await session.get(Document, version.document_id)
    language = doc.language if doc else ""
    rows = (
        await session.execute(
            select(Clause.id, Clause.clause_number, Clause.original_text, Clause.page_start)
            .where(Clause.version_id == version.id)
            .order_by(Clause.ordinal)
        )
    ).all()
    card = assess(
        [ClauseSample(n, t, True) for _, n, t, _ in rows],
        language,
        needs_translation=False,
        instrument_kind=doc.instrument_kind if doc else None,
    )
    items = [
        {"clause_id": str(cid), "page": page, "state": "pending"}
        for cid, _, t, page in rows
        if (r := _readable_ratio([t], language)) is not None and r < MIN_READABLE
    ]
    missing = next((p for p in card.problems if p.endswith("条没识别出来")), "")
    if card.passed and not items:
        return True, [], ""
    note = "；".join(x for x in (note_for(items), missing) if x) or "有些文字可能识别错了"
    return False, items, note


async def apply_recognition_check(session: AsyncSession, version: DocumentVersion) -> bool | None:
    """Check a scanned version that nobody has decided on yet. Returns whether it passed (None: not
    a scan, or already decided by the administrator)."""
    if version.text_source != "ocr" or version.review_status != "pending_review" or version.state != "parsed":
        return None
    passed, items, note = await check_recognition(session, version)
    version.check_note = note or None
    version.check_items = items or None
    if passed:
        from app.knowledge.version_check import apply_version_check

        await apply_version_check(session, version.id, True)
    elif items:
        try:  # 没读出来的页先自动换更高分辨率、按原文语言再读一遍，读通了就不打扰人
            await reread_pages(session, version)
            passed = version.review_status == "approved"
        except Exception:  # noqa: BLE001  识别引擎不可用时留给管理员
            pass
    await session.flush()
    return passed


async def settle_item(session: AsyncSession, version: DocumentVersion, clause_id: uuid.UUID, text: str | None) -> DocumentVersion:
    """One passage settled: ``text`` None means “没错”; otherwise the corrected text, kept as the
    clause's reading text (translation and analysis read it; the archived original is untouched).
    When none is left, the version counts as checked."""
    from app.ingestion.models import ClauseReading

    items = [dict(i) for i in (version.check_items or [])]
    hit = next((i for i in items if i["clause_id"] == str(clause_id)), None)
    if hit is None:
        raise LookupError("这一段不在待核对的段落里")
    if text is not None and text.strip():
        row = await session.scalar(select(ClauseReading).where(ClauseReading.clause_id == clause_id))
        if row is None:
            session.add(ClauseReading(clause_id=clause_id, reading_text=text.strip(), method="manual"))
        else:
            row.reading_text, row.method = text.strip(), "manual"
        hit["state"] = "fixed"
    else:
        hit["state"] = "ok"
    version.check_items = items
    version.check_note = note_for(items) or None
    if all(i["state"] != "pending" for i in items):
        from app.knowledge.version_check import apply_version_check

        await apply_version_check(session, version.id, True)
    await session.flush()
    return version


async def reread_pages(session: AsyncSession, version: DocumentVersion, pages: list[int] | None = None) -> dict:
    """Read the pages of the passages still waiting again (higher resolution, the law's own language);
    a passage whose new reading reads well replaces its text as the clause's reading and is settled.
    Returns how many passages are now readable and how many are left."""
    from app.cli import _ocr_engine
    from app.core.config import get_settings
    from app.ingestion.archive import ArchiveStore
    from app.ingestion.models import ClauseReading

    doc = await session.get(Document, version.document_id)
    raw = ArchiveStore(get_settings().archive_dir).read(
        version.archive_path, expected_sha256=version.content_hash
    )
    items = [dict(i) for i in (version.check_items or [])]
    want = {i["page"] for i in items if i.get("state") == "pending" and i.get("page")}
    if pages:
        want &= set(pages)
    engine = _ocr_engine()
    fixed = 0
    for page in sorted(want):
        text = engine.read_page(raw, page, doc.language if doc else "")
        r = _readable_ratio([text], doc.language if doc else "")
        if not text or r is None or r < MIN_READABLE:
            continue
        for i in items:
            if i.get("page") == page and i.get("state") == "pending":
                cid = uuid.UUID(i["clause_id"])
                row = await session.scalar(select(ClauseReading).where(ClauseReading.clause_id == cid))
                if row is None:
                    session.add(ClauseReading(clause_id=cid, reading_text=text, method="reocr_page"))
                else:
                    row.reading_text, row.method = text, "reocr_page"
                i["state"] = "fixed"
                fixed += 1
    version.check_items = items
    version.check_note = note_for(items) or None
    if items and all(i["state"] != "pending" for i in items):
        from app.knowledge.version_check import apply_version_check

        await apply_version_check(session, version.id, True)
    await session.flush()
    return {"fixed": fixed, "left": sum(1 for i in items if i["state"] == "pending")}

