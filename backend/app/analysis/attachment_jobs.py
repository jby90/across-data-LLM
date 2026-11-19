# ruff: noqa: E501
"""Reviewing uploaded files against the target country runs in the background, owned by the server.

The state (running / done / failed, when it started) is stored per file, so each conversation shows
only its own, a page reload or another window sees the same, and the assistant can answer “is it
still running?” from facts. When it ends, the conclusion is written into the conversation itself."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analysis import attachments as att
from app.analysis import gap_review
from app.analysis.document_narrative import NarrativeError
from app.analysis.models import AnalysisTask
from app.analysis.workflow_engine import country_name_zh, is_overseas_country

log = logging.getLogger(__name__)
STALE_SECONDS = 15 * 60  # a job that has not finished by then (server restarted) counts as failed
_background: set[asyncio.Task] = set()


def effective_state(row: att.TaskAttachment) -> str | None:
    if row.review_state == "running" and row.review_started_at is not None:
        if (datetime.now(UTC) - row.review_started_at).total_seconds() > STALE_SECONDS:
            return "failed"
    return row.review_state


def elapsed_seconds(row: att.TaskAttachment) -> int:
    if row.review_started_at is None:
        return 0
    return max(0, int((datetime.now(UTC) - row.review_started_at).total_seconds()))


async def latest_review(session: AsyncSession, attachment_id: uuid.UUID) -> att.AttachmentReview | None:
    return await session.scalar(
        select(att.AttachmentReview)
        .where(att.AttachmentReview.attachment_id == attachment_id, att.AttachmentReview.summary["attachment_ids"].astext.is_(None))
        .order_by(att.AttachmentReview.created_at.desc())
        .limit(1)
    )


async def latest_merged(session: AsyncSession, task: AnalysisTask, files: list[att.TaskAttachment]) -> att.AttachmentReview | None:
    """The newest all-files comparison that is still about exactly the files now in the conversation."""
    if not files:
        return None
    ids = sorted(str(f.id) for f in files)
    rows = (
        await session.scalars(
            select(att.AttachmentReview)
            .where(att.AttachmentReview.attachment_id.in_([f.id for f in files]), att.AttachmentReview.summary["attachment_ids"].astext.is_not(None))
            .order_by(att.AttachmentReview.created_at.desc())
            .limit(5)
        )
    ).all()
    for r in rows:
        if sorted(r.summary.get("attachment_ids") or []) == ids and r.country_code == task.country_code:
            return r
    return None


async def sent_files(session: AsyncSession, task: AnalysisTask) -> list[att.TaskAttachment]:
    """Files that left the composer with a message (what the conversation is about)."""
    return list(
        (
            await session.scalars(
                select(att.TaskAttachment)
                .where(att.TaskAttachment.task_id == task.id, att.TaskAttachment.message_id.is_not(None))
                .order_by(att.TaskAttachment.created_at)
            )
        ).all()
    )


async def needs_review(session: AsyncSession, task: AnalysisTask, files: list[att.TaskAttachment]) -> bool:
    """True when the readable files have no valid review for the current country and none is running."""
    readable = [f for f in files if f.readable]
    if not readable or any(effective_state(f) == "running" for f in readable):
        return False
    if not is_overseas_country(task.country_code):
        return False
    if len(readable) >= 2:
        return await latest_merged(session, task, readable) is None
    one = await latest_review(session, readable[0].id)
    return one is None or one.country_code != task.country_code


async def start_review(session: AsyncSession, task: AnalysisTask) -> list[att.TaskAttachment]:
    """Mark the conversation's readable files as under review and start the job. Returns the files
    now under review (empty when there is nothing to do)."""
    files = [f for f in await sent_files(session, task) if f.readable]
    if not await needs_review(session, task, files):
        return []
    now = datetime.now(UTC)
    for f in files:
        f.review_state, f.review_started_at, f.review_error = "running", now, None
    await session.commit()
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    job = asyncio.create_task(_job(factory, task.id, [f.id for f in files]))
    _background.add(job)
    job.add_done_callback(_background.discard)
    return files


async def _job(factory: async_sessionmaker, task_id: uuid.UUID, file_ids: list[uuid.UUID]) -> None:
    from app.analysis.chat import add_message
    from app.core.redaction import bind_egress, llm_scope
    from app.iam.models import Organization

    async with factory() as session:
        try:
            task = await session.get(AnalysisTask, task_id)
            if task is None:
                return
            files = list((await session.scalars(select(att.TaskAttachment).where(att.TaskAttachment.id.in_(file_ids)).order_by(att.TaskAttachment.created_at))).all())
            org = await session.get(Organization, task.org_id) if task.org_id else None
            with llm_scope():
                bind_egress(session, org)  # 公司名称与自定义词在出站前先做脱敏
                await perform_review(session, task, files)
        except Exception as exc:  # noqa: BLE001 - 后台任务：失败要写进对话，不能悄悄消失
            log.exception("文件对照失败")
            await session.rollback()
            why = str(exc) if isinstance(exc, NarrativeError) else "服务暂时不可用"
            await session.execute(
                select(AnalysisTask.id).where(AnalysisTask.id == task_id).with_for_update()
            )
            for f in (await session.scalars(select(att.TaskAttachment).where(att.TaskAttachment.id.in_(file_ids)))).all():
                f.review_state, f.review_error = "failed", why[:500]
            await add_message(
                session, task_id, "assistant",
                f"文件对照没有完成（{why}）。你可以直接说“重新对照”，或点文件旁的“重试”。",
            )
            await session.commit()


async def perform_review(session: AsyncSession, task: AnalysisTask, files: list[att.TaskAttachment]) -> None:
    """One file: review it; two or more: review them together as the company's current state.
    Saves the result, marks the files done and writes the conclusion into the conversation."""
    from app.analysis.chat import add_message

    country = task.country_code
    if len(files) >= 2:
        review = await gap_review.review_documents(session, [(f.filename, f.text) for f in files], country)
        summary = {**review.counts, "dropped": len(review.dropped), "gaps": review.gaps, "attachment_ids": sorted(str(f.id) for f in files)}
        subject = f"{len(files)} 份文件合起来"
        anchor = files[-1]
    else:
        review = await gap_review.review_document(session, files[0].filename, files[0].text, country)
        summary = {**review.counts, "dropped": len(review.dropped), "gaps": review.gaps}
        subject = f"《{files[0].filename}》"
        anchor = files[0]
    await session.execute(
        select(AnalysisTask.id).where(AnalysisTask.id == task.id).with_for_update()
    )
    session.add(
        att.AttachmentReview(
            org_id=task.org_id, attachment_id=anchor.id, country_code=country,
            blocks=review.blocks, summary=summary, tokens=review.tokens,
        )
    )
    for f in files:
        f.review_state, f.review_error = "done", None
    name = country_name_zh(country)
    c = review.counts
    lines = [f"{subject}对照{name}要求完成：已涵盖 {c['covered']} 项，部分涵盖 {c['partial']} 项，未涵盖 {c['missing']} 项。"]
    if review.gaps:
        lines.append("最突出的缺口：" + "；".join(f"{i}. {g}" for i, g in enumerate(review.gaps, 1)) + "。")
    lines.append("逐项的差距和补充建议，点上面文件旁的“查看对照”，也可以下载 Word / PDF。")
    await add_message(session, task.id, "assistant", "\n".join(lines))
    # 合规评估现在是“生成合规文件 + 咨询助手”：对照完不再把用户引回旧的八维度评估问卷
    await session.commit()
