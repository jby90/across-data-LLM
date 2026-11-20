# ruff: noqa: E501
"""Upload a company document into an assessment conversation and have it set against the target
country's requirements. The body of the upload request is the file itself (no multipart form), so
no extra library is needed; the file name travels in the query."""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import attachment_jobs as jobs
from app.analysis import attachments as att
from app.analysis.document_templates import Rendered, build_docx, build_pdf
from app.analysis.models import AnalysisTask
from app.analysis.report_exporter import content_disposition, named
from app.analysis.workflow_engine import is_overseas_country
from app.core.db import get_session
from app.iam.deps import Principal, require_user_side

router = APIRouter(prefix="/api/analysis/tasks/{task_id}/attachments", tags=["analysis-attachments"])
UserDep = Annotated[Principal, Depends(require_user_side)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _task(session: AsyncSession, task_id: uuid.UUID, user: Principal, *, for_update: bool = False) -> AnalysisTask:
    from app.analysis.chat_advisor import viewer_owner

    if for_update:
        task = await session.scalar(
            select(AnalysisTask)
            .where(AnalysisTask.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    else:
        task = await session.get(AnalysisTask, task_id)
    owner = viewer_owner(task) if task is not None else None
    if task is None or task.org_id != user.org_id or (owner is not None and owner != str(user.user_id)):
        raise HTTPException(404, "对话不存在")
    return task


def _writable(task: AnalysisTask) -> AnalysisTask:
    if task.archived_at is not None:
        raise HTTPException(409, "这个对话在归档里，恢复后才能继续")
    return task


async def _attachment(session: AsyncSession, task: AnalysisTask, attachment_id: uuid.UUID) -> att.TaskAttachment:
    row = await session.get(att.TaskAttachment, attachment_id)
    if row is None or row.task_id != task.id:
        raise HTTPException(404, "这份文件不在本次对话中")
    return row


def _view(row: att.TaskAttachment, review: att.AttachmentReview | None = None, in_merged: bool = False) -> dict:
    return {
        "in_merged": in_merged,
        "message_id": str(row.message_id) if row.message_id else None,
        "id": str(row.id),
        "filename": row.filename,
        "size_bytes": row.size_bytes,
        "pages": row.pages,
        "readable": row.readable,
        "note": row.note,
        "chars": len(row.text),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "review_state": jobs.effective_state(row),
        "review_seconds": jobs.elapsed_seconds(row) if jobs.effective_state(row) == "running" else 0,
        "review_error": row.review_error if jobs.effective_state(row) == "failed" else None,
        "reviewed": review is not None,
        "review_country": review.country_code if review else None,
        "summary": review.summary if review else None,
    }


async def _readable_files(session: AsyncSession, task: AnalysisTask) -> list[att.TaskAttachment]:
    rows = (
        await session.scalars(
            select(att.TaskAttachment)
            .where(att.TaskAttachment.task_id == task.id, att.TaskAttachment.readable.is_(True))
            .order_by(att.TaskAttachment.created_at)
        )
    ).all()
    return list(rows)


@router.get("/merged-review")
async def read_merged_review(task_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    task = await _task(session, task_id, user)
    files = await _readable_files(session, task)
    review = await jobs.latest_merged(session, task, files)
    if review is None:
        raise HTTPException(404, "这些文件合起来还没有做过对照（文件有增减后需要重新对照）")
    return {"summary": review.summary, "blocks": review.blocks, "files": [f.filename for f in files]}


@router.get("/merged-review/export.{kind}")
async def export_merged(task_id: uuid.UUID, kind: str, user: UserDep, session: SessionDep) -> Response:
    if kind not in ("pdf", "docx"):
        raise HTTPException(404, "不支持的格式")
    task = await _task(session, task_id, user)
    task_title = task.title
    review = await jobs.latest_merged(session, task, await _readable_files(session, task))
    if review is None:
        raise HTTPException(404, "还没有做过合并对照")
    rendered = Rendered(blocks=review.blocks)
    if kind == "pdf":
        await session.close()
        data, media = await asyncio.to_thread(build_pdf, rendered, "合规现状对照（多份文件合并）"), "application/pdf"
    else:
        data = build_docx(rendered, "合规现状对照（多份文件合并）")
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return Response(data, media_type=media, headers={"Content-Disposition": content_disposition(named(task_title, "合规现状对照（多份文件）", ext=kind))})


@router.get("")
async def list_attachments(task_id: uuid.UUID, user: UserDep, session: SessionDep) -> list[dict]:
    task = await _task(session, task_id, user)
    rows = (
        await session.scalars(
            select(att.TaskAttachment).where(att.TaskAttachment.task_id == task.id).order_by(att.TaskAttachment.created_at)
        )
    ).all()
    readable = [r for r in rows if r.readable]
    merged = await jobs.latest_merged(session, task, readable) if len(readable) >= 2 else None
    ids = set((merged.summary or {}).get("attachment_ids") or []) if merged else set()
    return [_view(r, await jobs.latest_review(session, r.id), str(r.id) in ids) for r in rows]


@router.post("", status_code=201)
async def upload_attachment(
    task_id: uuid.UUID, filename: str, request: Request, user: UserDep, session: SessionDep
) -> dict:
    """The request body is the file. Reads it in pieces and refuses anything over the size limit."""
    task = _writable(await _task(session, task_id, user, for_update=True))
    filename = filename.strip().replace("\\", "/").rsplit("/", 1)[-1][:255]
    if not filename:
        raise HTTPException(422, "缺少文件名")
    try:
        att.kind_of(filename)
    except att.UploadError as exc:
        raise HTTPException(422, str(exc)) from exc
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > att.MAX_BYTES:
        raise HTTPException(413, f"文件超过 {att.MAX_BYTES // 1024 // 1024} MB，请压缩或拆分后再传")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > att.MAX_BYTES:
            raise HTTPException(413, f"文件超过 {att.MAX_BYTES // 1024 // 1024} MB，请压缩或拆分后再传")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(422, "文件是空的")
    count = await session.scalar(select(func.count(att.TaskAttachment.id)).where(att.TaskAttachment.task_id == task.id))
    if (count or 0) >= att.MAX_FILES:
        raise HTTPException(409, f"一次对话最多放 {att.MAX_FILES} 份文件，请先删除不需要的")
    sha = att.digest(data)
    existing = await session.scalar(
        select(att.TaskAttachment).where(att.TaskAttachment.task_id == task.id, att.TaskAttachment.sha256 == sha)
    )
    if existing is not None:
        return _view(existing, await jobs.latest_review(session, existing.id))
    try:
        text, pages, readable, note = att.extract(filename, data)
    except att.UploadError as exc:
        raise HTTPException(422, str(exc)) from exc
    row = att.TaskAttachment(
        org_id=task.org_id,
        task_id=task.id,
        filename=filename,
        mime=att.ALLOWED[att.kind_of(filename)],
        size_bytes=len(data),
        sha256=sha,
        pages=pages,
        text=text,
        readable=readable,
        note=note,
        created_by=user.user_id,
    )
    session.add(row)
    await session.flush()
    await session.commit()
    await session.refresh(row)
    return _view(row)


@router.post("/review-pending")
async def review_pending(task_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    """Start (or restart) the comparison of the files in this conversation; it runs in the
    background and the conclusion is written into the conversation when it is done."""
    task = _writable(await _task(session, task_id, user))
    if not is_overseas_country(task.country_code):
        raise HTTPException(409, "还没有确定目标国家：请先在对话里说明要对照哪个国家")
    started = await jobs.start_review(session, task)
    return {"started": [f.filename for f in started]}


@router.delete("/{attachment_id}")
async def delete_attachment(
    task_id: uuid.UUID, attachment_id: uuid.UUID, user: UserDep, session: SessionDep
) -> dict:
    task = await _task(session, task_id, user)
    row = await _attachment(session, task, attachment_id)
    await session.delete(row)
    await session.commit()
    return {"removed": True}


@router.get("/{attachment_id}/review")
async def read_review(task_id: uuid.UUID, attachment_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    task = await _task(session, task_id, user)
    row = await _attachment(session, task, attachment_id)
    review = await jobs.latest_review(session, row.id)
    if review is None:
        raise HTTPException(404, "还没有做过对照")
    return {**_view(row, review), "blocks": review.blocks}


async def _export(task_id: uuid.UUID, attachment_id: uuid.UUID, user: Principal, session: AsyncSession, kind: str) -> Response:
    task = await _task(session, task_id, user)
    row = await _attachment(session, task, attachment_id)
    filename = row.filename
    review = await jobs.latest_review(session, row.id)
    if review is None:
        raise HTTPException(404, "还没有做过对照")
    rendered = Rendered(blocks=review.blocks)
    context = f"合规现状对照 | {filename}"
    if kind == "pdf":
        await session.close()
        data, media = await asyncio.to_thread(build_pdf, rendered, context), "application/pdf"
    else:
        data = build_docx(rendered, context)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    base = filename.rsplit(".", 1)[0]
    return Response(data, media_type=media, headers={"Content-Disposition": content_disposition(named(base, "合规现状对照", ext=kind))})


@router.get("/{attachment_id}/review/export.pdf")
async def export_pdf(task_id: uuid.UUID, attachment_id: uuid.UUID, user: UserDep, session: SessionDep) -> Response:
    return await _export(task_id, attachment_id, user, session, "pdf")


@router.get("/{attachment_id}/review/export.docx")
async def export_docx(task_id: uuid.UUID, attachment_id: uuid.UUID, user: UserDep, session: SessionDep) -> Response:
    return await _export(task_id, attachment_id, user, session, "docx")
