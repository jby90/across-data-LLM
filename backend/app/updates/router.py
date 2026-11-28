# ruff: noqa: E501
"""Authenticated station reminders. No email is sent or external messaging configured."""

import asyncio
import re
import time as _time
import uuid
import weakref as _weakref
from collections import OrderedDict as _OrderedDict
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.analysis.models import AnalysisTask
from app.core.db import get_session
from app.iam.deps import Principal, require_platform_admin, require_user_side
from app.ingestion.models import Document, DocumentVersion
from app.updates import subscriptions as subs
from app.updates.models import (
    RegulatoryDiscovery,
    UpdateImpact,
    UserNotification,
    UserSubscription,
    VersionChange,
)
from app.updates.service import now_utc

user_router = APIRouter(prefix="/api/notifications", tags=["notifications"])
admin_router = APIRouter(prefix="/api/admin/updates", tags=["updates"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[Principal, Depends(require_user_side)]
AdminDep = Annotated[Principal, Depends(require_platform_admin)]


_ORG_REFRESH_LOCKS: _weakref.WeakValueDictionary[uuid.UUID, asyncio.Lock] = _weakref.WeakValueDictionary()
_ORG_REFRESH_AT: _OrderedDict[uuid.UUID, float] = _OrderedDict()


@user_router.get("")
async def list_notifications(
    user: UserDep,
    session: SessionDep,
    offset: int = Query(0, ge=0, le=2147483647),
    limit: int = Query(50, ge=1, le=100),
):
    from app.updates.notices import refresh

    pending = False
    if user.org_id is not None:
        lock = _ORG_REFRESH_LOCKS.setdefault(user.org_id, asyncio.Lock())
        now_m = _time.monotonic()
        # Bell polling (?limit=1) reuses a recent refresh within 15s to avoid concurrent write contention
        if limit > 1 or now_m - _ORG_REFRESH_AT.get(user.org_id, 0.0) > 15.0:
            async with lock:
                if limit > 1 or _time.monotonic() - _ORG_REFRESH_AT.get(user.org_id, 0.0) > 15.0:
                    await refresh(session, user.org_id)
                    pending = await subs.push(session, user.org_id)
                    await session.commit()
                    _ORG_REFRESH_AT[user.org_id] = _time.monotonic()
    # 同一次法规变化只留一条：已有订阅通知的，“影响评估报告”的那几条并进订阅通知里
    mine = aliased(UserNotification)
    folded = select(mine.change_id).where(
        mine.user_id == user.user_id, mine.kind == "subscription", mine.change_id.is_not(None)
    )
    conditions = (
        UserNotification.user_id == user.user_id,
        UserNotification.org_id == user.org_id,
        ~and_(UserNotification.kind == "source_change", UserNotification.change_id.in_(folded)),
    )
    unread = await session.scalar(
        select(func.count())
        .select_from(UserNotification)
        .where(*conditions, UserNotification.read_at.is_(None))
    )
    total = await session.scalar(
        select(func.count()).select_from(UserNotification).where(*conditions)
    )
    rows = (
        await session.scalars(
            select(UserNotification)
            .where(*conditions)
            .order_by(UserNotification.created_at.desc(), UserNotification.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    from app.analysis.workflow_engine import country_name_zh
    from app.iam.models import Role
    from app.knowledge.countries import resolve_country_code

    def _adapt_viewer_body(text: str | None) -> str:
        raw = text or ""
        if user.role != Role.VIEWER:
            return raw
        return (
            raw.replace("打开简报对照新结论核对，必要时更新已生成的合规文件。", "可打开简报查阅最新合规结论。")
            .replace("打开简报对照新结论核对。", "可打开简报查阅最新合规结论。")
            .replace("已生成的申请材料是按改动前的内容写的，打开项目重新生成。", "可打开项目查阅最新准入要求与材料说明。")
            .replace("已生成的申报材料系依据修订前口径编制，打开项目重新生成。", "可打开项目查阅最新准入要求与材料说明。")
            .replace("打开项目按新要求准备。", "可打开项目查阅最新准入要求。")
        )

    def _extract_country_links(notif: UserNotification) -> list[dict]:
        ccs: list[str] = []
        title = (notif.title or "").strip()
        if title.endswith("合规简报已更新"):
            for part in title[: -len("合规简报已更新")].split("、"):
                cc = resolve_country_code(part.strip())
                if len(cc) == 2 and cc.isalpha() and cc not in ccs:
                    ccs.append(cc)
        m = re.search(r"country=([A-Za-z]{2})", notif.link or "")
        if m:
            cc = m.group(1).upper()
            if cc not in ccs:
                ccs.append(cc)
        return [
            {
                "country_code": cc,
                "country_name": country_name_zh(cc),
                "label": f"查看{country_name_zh(cc)}简报",
                "link": f"/knowledge?country={cc}",
            }
            for cc in ccs
        ]

    items = []
    for notification in rows:
        if notification.kind == "subscription":
            sub_item = await _subscription_item(session, user, notification)
            sub_item["body"] = _adapt_viewer_body(sub_item.get("body"))
            items.append(sub_item)
            continue
        if notification.kind != "source_change":
            payload = {
                "id": str(notification.id),
                "kind": notification.kind,
                "created_at": notification.created_at,
                "read_at": notification.read_at,
                "title": notification.title,
                "body": _adapt_viewer_body(notification.body),
                "link": notification.link,
            }
            if notification.kind == "whitepaper":
                payload["country_links"] = _extract_country_links(notification)
            items.append(payload)
            continue
        change = await session.get(VersionChange, notification.change_id)
        task = await session.get(AnalysisTask, notification.task_id)
        document = await session.get(Document, change.document_id) if change else None
        if change is None or task is None or document is None or task.org_id != user.org_id:
            continue
        impacts = list(
            (
                await session.scalars(
                    select(UpdateImpact).where(
                        UpdateImpact.task_id == task.id,
                        UpdateImpact.change_id == change.id,
                        UpdateImpact.org_id == user.org_id,
                    )
                )
            ).all()
        )
        items.append(
            {
                "id": str(notification.id),
                "kind": "source_change",
                "created_at": notification.created_at,
                "read_at": notification.read_at,
                "task_id": str(task.id),
                "task_kind": task.kind,
                "task_title": task.title,
                "document_title": document.title_zh or document.title,
                "country_code": document.country_code,
                "source_url": document.source_url,
                "summary": change.summary,
                "change_id": str(change.id),
                "change_kind": change.change_kind,
                "dimension_codes": sorted({i.dimension_code for i in impacts}),
                "state": "open" if any(i.state == "open" for i in impacts) else "resolved",
                "due_at": min((i.due_at for i in impacts), default=None),
                "human_review": "pending_review",
                "advice": await _advice_for(session, f"change:{change.id}", document),
            }
        )
    if any((i.get("advice") or {}).get("state") == "pending" for i in items):
        pending = True
    await session.commit()
    if pending:
        subs.schedule_writing()
    return {"items": items, "unread": unread, "total": total, "offset": offset, "limit": limit}


async def _advice_for(session, subject, document):
    await subs.ensure_advice(session, subject, document)
    return await subs.advice_out(session, subject)


async def _subscription_item(session, user, notification) -> dict:
    """A subscribed change: before/after, impact, what to do, and this enterprise's projects it touches."""
    dk = str(notification.dedupe_key or "")
    subject = f"change:{notification.change_id}" if notification.change_id else (dk.split(":", 1)[1] if ":" in dk else dk)
    advice = await subs.advice_out(session, subject)
    related: list[dict] = []
    if notification.change_id:
        # 引用了改动条文的评估报告：可按新条文重新评估（用那条“影响评估报告”通知的入口）
        impacts = (
            await session.execute(
                select(UpdateImpact.task_id, AnalysisTask.title, AnalysisTask.kind)
                .join(AnalysisTask, AnalysisTask.id == UpdateImpact.task_id)
                .where(
                    UpdateImpact.org_id == user.org_id,
                    UpdateImpact.change_id == notification.change_id,
                    AnalysisTask.archived_at.is_(None),
                )
                .distinct()
            )
        ).all()
        for task_id, title, kind in impacts:
            notice_id = await session.scalar(
                select(UserNotification.id).where(
                    UserNotification.user_id == user.user_id,
                    UserNotification.kind == "source_change",
                    UserNotification.change_id == notification.change_id,
                    UserNotification.task_id == task_id,
                )
            )
            related.append({"task_id": str(task_id), "title": title, "kind": kind, "notice_id": str(notice_id) if notice_id else None, "cites": True})
    # 新收录的法规：合规评估项目都还没有引用它，不派活
    # 准入项目：这个国家的准入项目；事项所依据的就是这部法规的，申请材料要按新条文重新生成
    from app.admission.models import AdmissionEntry, AdmissionProject
    from app.ingestion.models import Clause

    doc = None
    if "doc=" in (notification.link or ""):
        try:
            doc = await session.get(Document, uuid.UUID(notification.link.split("doc=", 1)[1].split("&", 1)[0]))
        except (ValueError, IndexError):
            doc = None
    base = None
    if doc is not None and not notification.change_id:
        from app.analysis.document_narrative import DocumentNarrative

        members = await subs.batch_members(session, doc) or [doc]
        base = next((b for b in [await subs.amended_base(session, m) for m in members] if b is not None), None)
        if base is not None:
            # 修改法律：合规评估项目的文件引用了被修改法律的条文的，要按修改后的条文重新生成
            tasks = (
                await session.scalars(
                    select(AnalysisTask).where(
                        AnalysisTask.org_id == user.org_id, AnalysisTask.archived_at.is_(None), AnalysisTask.kind == "documents",
                        func.upper(AnalysisTask.country_code) == doc.country_code,
                    )
                )
            ).all()
            base_clauses = select(Clause.id).join(DocumentVersion, DocumentVersion.id == Clause.version_id).where(DocumentVersion.document_id == base.id)
            for task in tasks:
                cited = {
                    str(x)
                    for r in (await session.scalars(select(DocumentNarrative.report).where(DocumentNarrative.task_id == task.id))).all()
                    for x in (r or {}).get("cited_clause_ids") or []
                }
                ids = [uuid.UUID(x) for x in cited if re.fullmatch(r"[0-9a-f-]{36}", x)]
                hit = bool(ids) and bool(await session.scalar(select(Clause.id).where(Clause.id.in_(ids), Clause.id.in_(base_clauses)).limit(1)))
                related.append({"task_id": str(task.id), "title": task.title, "kind": task.kind, "notice_id": None, "cites": hit, "amended": True})
        member_ids = {str(m.id) for m in members}
    else:
        member_ids = {str(doc.id)} if doc is not None else set()
    if doc is not None:
        rows = (
            await session.execute(
                select(AdmissionProject, AdmissionEntry)
                .join(AdmissionEntry, AdmissionEntry.id == AdmissionProject.entry_id)
                .where(AdmissionProject.org_id == user.org_id, AdmissionEntry.country_code == doc.country_code)
            )
        ).all()
        for proj, entry in rows:
            on_this = False
            if entry.evidence_clause_id:
                ver = await session.scalar(
                    select(DocumentVersion.document_id).join(Clause, Clause.version_id == DocumentVersion.id).where(Clause.id == entry.evidence_clause_id)
                )
                on_this = str(ver) in member_ids
            on_this = on_this or bool(member_ids & set((entry.profile or {}).get("law_ids") or []))  # 事项依据的法规里有这一部（同批的几部之一）
            related.append({"admission_id": str(proj.id), "title": proj.title, "kind": "admission", "notice_id": None, "cites": on_this})
    return {
        "id": str(notification.id),
        "kind": "subscription",
        "revised": bool(notification.change_id) or base is not None,
        "country_code": (re.search(r"country=([A-Z]+)", notification.link or "") or [None, ""])[1],
        "created_at": notification.created_at,
        "read_at": notification.read_at,
        "title": notification.title,
        "body": notification.body,
        "link": notification.link,
        "advice": advice,
        "related": related,
    }


class SubscriptionIn(BaseModel):
    countries: list[str] = Field(default_factory=list, max_length=60)
    doc_types: list[str] = Field(default_factory=list, max_length=10)
    topics: list[str] = Field(default_factory=list, max_length=10)


async def _subscription_out(session, sub) -> dict:
    return {
        "countries": list(sub.countries or []) if sub else [],
        "doc_types": list(sub.doc_types or []) if sub else [],
        "topics": list(sub.topics or []) if sub else [],
        "summary": subs.describe(sub),
        "since": sub.created_at if sub else None,
        "options": await subs.options(session),
    }


async def org_subscription(session, org_id):
    """The enterprise's subscription (one per enterprise: every account sees the same notices)."""
    return await session.scalar(
        select(UserSubscription).where(UserSubscription.org_id == org_id).order_by(UserSubscription.created_at.asc()).limit(1)
    )


@user_router.get("/subscription")
async def get_subscription(user: UserDep, session: SessionDep):
    return await _subscription_out(session, await org_subscription(session, user.org_id))


@user_router.get("/weekly-digests")
async def list_weekly_digests(
    _: UserDep, session: SessionDep, limit: int = Query(8, ge=1, le=20)
) -> list[dict]:
    """User-portal view of minimalist weekly automotive compliance digests (linked to country briefings)."""
    from app.opinion import weekly_digest as wd
    from app.opinion.models import OpinionDigestRun

    rows = (
        await session.scalars(
            select(OpinionDigestRun).order_by(OpinionDigestRun.created_at.desc()).limit(limit)
        )
    ).all()
    out: list[dict] = []
    for r in rows:
        raw_items = list(r.items_json or [])
        cleaned_items: list[dict] = []
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            item_copy = dict(it)
            if isinstance(item_copy.get("summary"), str):
                s = wd.finalize_digest_summary(item_copy["summary"])
                item_copy["summary"] = s or item_copy.get("title") or ""
            inferred_cc, inferred_lbl = wd.infer_country_tag(
                str(item_copy.get("title") or ""),
                str(item_copy.get("publisher") or ""),
                str(item_copy.get("summary") or ""),
            )
            if inferred_lbl and (inferred_cc == "CN" or not item_copy.get("country_label")):
                item_copy["country_code"] = inferred_cc or None
                item_copy["country_label"] = inferred_lbl
            cleaned_items.append(item_copy)
        span_days = (r.period_end - r.period_start).days if r.period_start and r.period_end else 0
        period_label = (
            f"近期归档动态精选（共 {r.item_count} 条）"
            if span_days > 21
            else f"{r.period_start.isoformat()} 至 {r.period_end.isoformat()}"
        )
        out.append(
            {
                "id": str(r.id),
                "subject": r.subject,
                "period_start": r.period_start.isoformat(),
                "period_end": r.period_end.isoformat(),
                "period_label": period_label,
                "item_count": r.item_count,
                "high_count": sum(1 for x in cleaned_items if x.get("level") == "high"),
                "medium_count": sum(1 for x in cleaned_items if x.get("level") == "medium"),
                "low_count": sum(1 for x in cleaned_items if x.get("level") == "low"),
                "items": cleaned_items,
                "sent_at": r.created_at.isoformat() if r.created_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return out


@user_router.put("/subscription")
async def set_subscription(body: SubscriptionIn, user: UserDep, session: SessionDep):
    """Save what to subscribe to; nothing chosen = no subscription. Changes collected from now on
    are pushed (not the ones before)."""
    from sqlalchemy import delete as sa_delete
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    opts = await subs.options(session)
    allowed = {k: {o["value"] for o in v} for k, v in opts.items()}
    chosen = {k: list(dict.fromkeys(v for v in getattr(body, k) if v in allowed[k])) for k in allowed}
    if not any(chosen.values()):
        await session.execute(sa_delete(UserSubscription).where(UserSubscription.org_id == user.org_id))
        await session.commit()
        return await _subscription_out(session, None)
    now = now_utc()
    stmt = (
        pg_insert(UserSubscription)
        .values(
            id=uuid.uuid4(),
            org_id=user.org_id,
            user_id=user.user_id,
            countries=chosen["countries"],
            doc_types=chosen["doc_types"],
            topics=chosen["topics"],
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["org_id"],
            set_={
                "user_id": user.user_id,
                "countries": chosen["countries"],
                "doc_types": chosen["doc_types"],
                "topics": chosen["topics"],
                "updated_at": now,
            },
        )
    )
    await session.execute(stmt)
    await session.commit()
    sub = await org_subscription(session, user.org_id)
    return await _subscription_out(session, sub)


async def own_notification(session, principal, notification_id):
    row = await session.scalar(
        select(UserNotification)
        .where(
            UserNotification.id == notification_id,
            UserNotification.user_id == principal.user_id,
            UserNotification.org_id == principal.org_id,
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, "通知不存在")
    return row


@user_router.post("/read-all")
async def read_all(request: Request, user: UserDep, session: SessionDep):
    """打开通知列表就算看过：当时所有未读记为已读，返回这次新到的几条，页面上标“新”。"""
    from sqlalchemy import update

    fresh = (
        await session.scalars(
            select(UserNotification.id).where(
                UserNotification.user_id == user.user_id, UserNotification.org_id == user.org_id, UserNotification.read_at.is_(None)
            )
        )
    ).all()
    if fresh:
        await session.execute(update(UserNotification).where(UserNotification.id.in_(fresh)).values(read_at=now_utc()))
        await session.commit()
    else:
        request.state.audit_skip = True
    return {"fresh": [str(i) for i in fresh]}


@user_router.post("/{notification_id}/read")
async def read_notification(notification_id: uuid.UUID, user: UserDep, session: SessionDep):
    row = await own_notification(session, user, notification_id)
    row.read_at = row.read_at or now_utc()
    await session.commit()
    return {"id": str(row.id), "read_at": row.read_at}


@user_router.post("/{notification_id}/reevaluate")
async def reevaluate(notification_id: uuid.UUID, user: UserDep, session: SessionDep):
    from app.analysis.router import _start

    notification = await own_notification(session, user, notification_id)
    if notification.task_id is None:
        raise HTTPException(409, "这条通知不对应评估报告")
    task = await session.scalar(
        select(AnalysisTask)
        .where(AnalysisTask.id == notification.task_id, AnalysisTask.org_id == user.org_id)
        .with_for_update()
    )
    if task is None:
        raise HTTPException(404, "未找到该合规评估项目")
    if task.status in ("running", "awaiting_user_facts"):
        raise HTTPException(409, "请先完成正在运行的任务或当前业务追问")
    if task.sufficiency_score < 100:
        raise HTTPException(409, "请先补齐本任务的业务事实")
    notification.read_at = notification.read_at or now_utc()
    # Existing reports remain intact. A new version resolves the operational impact
    # only if it actually cites the currently observed source in the affected dimension.
    await _start(session, task)
    return {"task_id": str(task.id), "status": task.status}


def discovery_query():
    # Aggregate once in SQL; do not load every document/version into the API process.
    versions = (
        select(
            DocumentVersion.document_id,
            func.min(DocumentVersion.parsed_at)
            .filter(DocumentVersion.state == "parsed")
            .label("completed"),
            func.bool_or(
                (DocumentVersion.state == "parsed") & DocumentVersion.parsed_at.is_(None)
            ).label("unknown"),
        )
        .group_by(DocumentVersion.document_id)
        .subquery()
    )
    return (
        select(
            RegulatoryDiscovery.id,
            RegulatoryDiscovery.country_code,
            func.coalesce(func.nullif(Document.title_zh, ""), Document.title, RegulatoryDiscovery.title).label("title"),
            RegulatoryDiscovery.source_url,
            RegulatoryDiscovery.discovered_at,
            versions.c.completed.label("first_parsed_at"),
            func.coalesce(versions.c.unknown, False).label("history_unknown"),
        )
        .outerjoin(
            Document,
            or_(
                Document.id == RegulatoryDiscovery.document_id,
                RegulatoryDiscovery.document_id.is_(None)
                & (Document.site_id == RegulatoryDiscovery.site_id)
                & (Document.source_url == RegulatoryDiscovery.source_url),
            ),
        )
        .outerjoin(versions, versions.c.document_id == Document.id)
    )


def discovery_items(rows, now):
    items = []
    for row in rows:
        item = dict(row)
        if item["history_unknown"]:
            item["first_parsed_at"] = None
        finished = item["first_parsed_at"]
        item["overdue"] = not item["history_unknown"] and (finished or now) > item[
            "discovered_at"
        ] + timedelta(days=30)
        item["elapsed_hours"] = (
            None
            if item["history_unknown"]
            else round(((finished or now) - item["discovered_at"]).total_seconds() / 3600, 2)
        )
        items.append(item)
    return items


@admin_router.get("/discoveries")
async def discoveries(
    _: AdminDep,
    session: SessionDep,
    offset: int = Query(0, ge=0, le=2147483647),
    limit: int = Query(50, ge=1, le=100),
    country: str | None = Query(None, min_length=1, max_length=8),
):
    query = discovery_query()
    if country:
        query = query.where(RegulatoryDiscovery.country_code == country.upper())
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        (
            await session.execute(
                query.order_by(RegulatoryDiscovery.discovered_at.desc(), RegulatoryDiscovery.id)
                .offset(offset)
                .limit(limit)
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": discovery_items(rows, now_utc()),
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@admin_router.get("/sla")
async def sla(_: AdminDep, session: SessionDep):
    from sqlalchemy import case

    from app.iam import service as iam_service
    from app.iam.models import Organization

    now = now_utc()
    trial_pat = f"%{iam_service.TRIAL_ORG_MARKER}%"
    groups = (
        select(
            UpdateImpact.task_id,
            UpdateImpact.change_id,
            func.bool_or(UpdateImpact.state == "open").label("is_open"),
            func.min(UpdateImpact.due_at).label("due"),
            func.bool_or(
                case(
                    (UpdateImpact.state == "open", UpdateImpact.due_at < now),
                    else_=func.coalesce(UpdateImpact.resolved_at, UpdateImpact.detected_at)
                    > UpdateImpact.due_at,
                )
            ).label("was_overdue"),
        )
        .outerjoin(Organization, Organization.id == UpdateImpact.org_id)
        .where(
            or_(
                UpdateImpact.org_id.is_(None),
                and_(Organization.is_active.is_(True), ~Organization.name.like(trial_pat)),
            )
        )
        .group_by(UpdateImpact.task_id, UpdateImpact.change_id)
        .subquery()
    )
    counts = (
        (
            await session.execute(
                select(
                    func.count().filter(groups.c.is_open.is_(True)).label("pending_updates"),
                    func.count()
                    .filter(groups.c.was_overdue.is_(True))
                    .label("overdue_72h"),
                    func.count().filter(groups.c.is_open.is_(False)).label("resolved_updates"),
                ).select_from(groups)
            )
        )
        .mappings()
        .one()
    )
    query = discovery_query()
    tracked = query.subquery()
    known = tracked.c.history_unknown.is_(False)
    intake = (
        (
            await session.execute(
                select(
                    func.count()
                    .filter(known & tracked.c.first_parsed_at.is_(None))
                    .label("pending"),
                    func.count()
                    .filter(known & tracked.c.first_parsed_at.is_not(None))
                    .label("completed"),
                    func.count()
                    .filter(
                        known
                        & tracked.c.first_parsed_at.is_not(None)
                        & (tracked.c.first_parsed_at <= tracked.c.discovered_at + timedelta(days=30))
                    )
                    .label("completed_on_time"),
                    func.count()
                    .filter(
                        known
                        & (
                            func.coalesce(tracked.c.first_parsed_at, now)
                            > tracked.c.discovered_at + timedelta(days=30)
                        )
                    )
                    .label("overdue_30d"),
                    func.count()
                    .filter(tracked.c.history_unknown.is_(True))
                    .label("history_unknown"),
                ).select_from(tracked)
            )
        )
        .mappings()
        .one()
    )
    untracked = await session.scalar(
        select(func.count())
        .select_from(Document)
        .where(
            ~select(RegulatoryDiscovery.id)
            .where(
                or_(
                    RegulatoryDiscovery.document_id == Document.id,
                    (RegulatoryDiscovery.site_id == Document.site_id)
                    & (RegulatoryDiscovery.source_url == Document.source_url),
                )
            )
            .exists()
        )
    )
    late_notices = await session.scalar(
        select(func.count())
        .select_from(UserNotification)
        .join(UpdateImpact, UpdateImpact.id == UserNotification.impact_id)
        .join(Organization, Organization.id == UserNotification.org_id)
        .where(
            UserNotification.created_at > UpdateImpact.due_at,
            Organization.is_active.is_(True),
            ~Organization.name.like(trial_pat),
        )
    )
    rows = (
        (await session.execute(query.order_by(RegulatoryDiscovery.discovered_at.desc()).limit(50)))
        .mappings()
        .all()
    )
    total = await session.scalar(select(func.count()).select_from(RegulatoryDiscovery))
    return {
        **dict(counts),
        "late_notification_records": late_notices,
        "intake": {
            **dict(intake),
            "untracked_documents": untracked,
            "definition": "发现至首次成功解析入库；与官方公布日期、人工审批分开，历史时间不推测",
        },
        "discoveries": discovery_items(rows, now),
        "discoveries_total": total,
        "countries": list(
            (
                await session.scalars(
                    select(RegulatoryDiscovery.country_code)
                    .distinct()
                    .order_by(RegulatoryDiscovery.country_code)
                )
            ).all()
        ),
        "human_review": "pending_review",
        "email_enabled": False,
    }


_RELATION_TO_DIMS: dict[str, str] = {
    "RESTRICTS_CROSS_BORDER": "D3",
    "REQUIRES_ADMISSION": "D7",
    "PENALIZES": "D6",
    "SUPERVISES": "D6",
    "PROHIBITS": "D3",
    "REQUIRES": "D4",
    "GOVERNS": "D1",
    "EXEMPTS": "D1",
    "APPLIES_TO_SCENARIO": "D2",
    "AMENDS": "D1",
    "REPEALS": "D1",
    "PUBLISHES": "D1",
}

_DIM_KEYWORDS: tuple[tuple[str, re.Pattern], ...] = (
    ("D2", re.compile(r"核心数据|重要数据|敏感个人|个人数据定义|分类分级|personal data|sensitive data", re.I)),
    ("D3", re.compile(r"跨境|出境|境外|传输|标准合同|充分性|cross-border|transfer", re.I)),
    ("D4", re.compile(r"同意|告知|授权|撤回|主体权利|consent|notice", re.I)),
    ("D5", re.compile(r"存储|留存|销毁|删除|本地化|retention|storage|deletion|localization", re.I)),
    ("D6", re.compile(r"处罚|罚款|罚金|违约|责任|监管调查|penalty|fine|sanction", re.I)),
    ("D7", re.compile(r"准入|型式批准|认证|许可|备案|申报|type approval|homologation|certification", re.I)),
)


@admin_router.get("/changes")
@admin_router.get("/source-changes")
async def changes(
    _: AdminDep,
    session: SessionDep,
    offset: int = Query(0, ge=0, le=2147483647),
    limit: int = Query(50, ge=1, le=100),
    country: str | None = Query(None, min_length=1, max_length=8),
):
    from app.knowledge.models import KgEdge

    query = select(VersionChange, Document).join(Document, Document.id == VersionChange.document_id)
    if country:
        query = query.where(Document.country_code == country.upper())
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await session.execute(
            query.order_by(VersionChange.detected_at.desc(), VersionChange.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()

    change_ids = [c.id for c, _ in rows]
    impacts_by_change: dict[uuid.UUID, set[str]] = {}
    if change_ids:
        imp_rows = (
            await session.execute(
                select(UpdateImpact.change_id, UpdateImpact.dimension_code).where(
                    UpdateImpact.change_id.in_(change_ids)
                )
            )
        ).all()
        for cid, dim_code in imp_rows:
            if dim_code and re.fullmatch(r"D[1-8]", str(dim_code).upper()):
                impacts_by_change.setdefault(cid, set()).add(str(dim_code).upper())

    all_edge_ids: set[uuid.UUID] = set()
    for c, _ in rows:
        for raw_eid in c.graph_edge_ids or []:
            try:
                all_edge_ids.add(uuid.UUID(str(raw_eid)))
            except (ValueError, TypeError):
                continue
    edge_rel_by_id: dict[str, str] = {}
    if all_edge_ids:
        edge_rows = (
            await session.execute(
                select(KgEdge.id, KgEdge.relation_type).where(KgEdge.id.in_(all_edge_ids))
            )
        ).all()
        edge_rel_by_id = {str(eid): str(rel) for eid, rel in edge_rows}

    def _infer_affected_dims(c: VersionChange, d: Document) -> list[str]:
        dims: set[str] = set(impacts_by_change.get(c.id, set()))
        for raw_eid in c.graph_edge_ids or []:
            rel = edge_rel_by_id.get(str(raw_eid))
            if rel and rel in _RELATION_TO_DIMS:
                dims.add(_RELATION_TO_DIMS[rel])
        if not dims:
            blob = f"{d.title or ''} {c.summary or ''} {c.changes_json or ''}"
            for dim_code, pat in _DIM_KEYWORDS:
                if pat.search(blob):
                    dims.add(dim_code)
        ordered = sorted(dims)
        return ordered[:7] if ordered else ["D1"]

    items = [
        {
            "id": str(c.id),
            "document_title": d.title_zh or d.title,
            "country_code": d.country_code,
            "source_url": d.source_url,
            "old_version_id": str(c.old_version_id),
            "new_version_id": str(c.new_version_id),
            "detected_at": c.detected_at,
            "kind": c.change_kind,
            "summary": c.summary,
            "changes": c.changes_json,
            "graph_edge_ids": c.graph_edge_ids,
            "content_hashes": c.content_hashes,
            "affected_dims": _infer_affected_dims(c, d),
            "human_review": "pending_review",
        }
        for c, d in rows
    ]
    return {"items": items, "total": total, "offset": offset, "limit": limit}


async def _build_sla_linkage_report(session: AsyncSession) -> dict:
    """Comprehensive 72h monitoring response + patrol timeliness + 30d intake + cross-module linkage report."""
    from sqlalchemy import not_

    from app.admission.models import AdmissionEntry, AdmissionStep
    from app.admission.router import SHOWN, _basis, amount_conflicts, legal_basis
    from app.analysis.workflow_engine import country_name_zh
    from app.knowledge.whitepaper import CountryWhitepaper
    from app.sources.attention import attention_of
    from app.sources.models import SourceSite

    def _capped_pct(numerator: int, denominator: int) -> float:
        if denominator <= 0:
            return 100.0
        if numerator <= 0:
            return 0.0
        if numerator >= denominator:
            return 100.0
        return min(99.9, round((numerator / denominator) * 100.0, 1))

    now = now_utc()
    sla_data = await sla(None, session)  # type: ignore[arg-type]

    pending_u = int(sla_data["pending_updates"] or 0)
    overdue_72h = int(sla_data["overdue_72h"] or 0)
    resolved_u = int(sla_data["resolved_updates"] or 0)
    total_u = pending_u + resolved_u
    on_time_72h = max(0, total_u - overdue_72h)
    rate_72h = _capped_pct(on_time_72h, total_u) if total_u > 0 else 100.0

    single_file = and_(SourceSite.strategy == "DIRECT", SourceSite.enabled.is_(False))
    monitor_sites = list(
        (await session.scalars(select(SourceSite).where(not_(single_file)))).all()
    )
    enabled_sites = [s for s in monitor_sites if s.enabled]
    active_sites = len(enabled_sites)
    failed_sites = sum(
        1
        for s in enabled_sites
        if attention_of(
            enabled=s.enabled,
            terms_status=s.terms_status,
            consecutive_failures=s.consecutive_failures,
            last_run_at=s.last_run_at,
            interval_hours=s.interval_hours,
            now=now,
        )
        is not None
    )
    healthy_sites = max(0, active_sites - failed_sites)
    patrol_rate = _capped_pct(healthy_sites, active_sites) if active_sites > 0 else 0.0

    intake = sla_data["intake"]
    completed_in = int(intake["completed"] or 0)
    completed_on_time_in = int(intake.get("completed_on_time") or 0)
    pending_in = int(intake["pending"] or 0)
    overdue_30d = int(intake["overdue_30d"] or 0)
    tracked_in = completed_in + pending_in

    wp_rows = (
        await session.execute(
            select(CountryWhitepaper.country_code, func.max(CountryWhitepaper.version_no))
            .group_by(CountryWhitepaper.country_code)
        )
    ).all()
    wp_by_cc = {cc: int(v or 0) for cc, v in wp_rows if cc and cc not in ("ZZ", "INTL", "--")}

    cand_entries = list(
        (
            await session.scalars(
                select(AdmissionEntry).where(
                    AdmissionEntry.merged_into.is_(None),
                    AdmissionEntry.review_status.in_(SHOWN),
                )
            )
        ).all()
    )
    adm_by_cc: dict[str, int] = {}
    if cand_entries:
        cand_ids = [e.id for e in cand_entries]
        steps_rows = list(
            (
                await session.scalars(
                    select(AdmissionStep).where(AdmissionStep.entry_id.in_(cand_ids))
                )
            ).all()
        )
        steps_by_entry: dict[uuid.UUID, list[AdmissionStep]] = {}
        for st in steps_rows:
            steps_by_entry.setdefault(st.entry_id, []).append(st)
        for e in cand_entries:
            if not e.country_code or e.country_code in ("ZZ", "INTL", "--"):
                continue
            e_steps = steps_by_entry.get(e.id, [])
            if not e_steps:
                continue
            laws, _ = await legal_basis(session, e, await _basis(session, e.evidence_clause_id))
            if not laws:
                continue
            if await amount_conflicts(session, e, e_steps):
                continue
            adm_by_cc[e.country_code] = adm_by_cc.get(e.country_code, 0) + 1

    chg_rows = (
        await session.execute(
            select(Document.country_code, func.count(VersionChange.id))
            .join(Document, Document.id == VersionChange.document_id)
            .group_by(Document.country_code)
        )
    ).all()
    chg_by_cc = {cc: int(n or 0) for cc, n in chg_rows if cc and cc not in ("ZZ", "INTL", "--")}

    disc_all = discovery_items(
        (await session.execute(discovery_query().order_by(RegulatoryDiscovery.country_code))).mappings().all(),
        now,
    )
    disc_by_cc: dict[str, dict[str, int]] = {}
    for d in disc_all:
        cc = d["country_code"]
        if not cc or cc in ("ZZ", "INTL", "--"):
            continue
        bucket = disc_by_cc.setdefault(cc, {"discovered": 0, "parsed": 0, "overdue_30d": 0})
        bucket["discovered"] += 1
        if d["first_parsed_at"] or d["history_unknown"]:
            bucket["parsed"] += 1
        if d["overdue"]:
            bucket["overdue_30d"] += 1

    from sqlalchemy import text as _sa_text

    doc_rows = (
        await session.execute(
            _sa_text(
                """
                WITH latest_dv AS (
                    SELECT DISTINCT ON (document_id)
                           id, document_id, legal_status
                    FROM document_versions
                    WHERE state = 'parsed'
                      AND review_status <> 'rejected'
                      AND coalesce(legal_status, 'in_force') <> 'draft'
                    ORDER BY document_id, version_no DESC, parse_revision DESC
                )
                SELECT d.country_code, count(DISTINCT d.id) AS doc_cnt
                FROM documents d
                JOIN latest_dv dv ON dv.document_id = d.id
                WHERE d.relevance IN ('relevant', 'related')
                  AND coalesce(dv.legal_status, 'in_force') NOT IN ('repealed', 'draft')
                  AND NOT EXISTS (
                      SELECT 1 FROM knowledge_change_proposals p
                      WHERE p.target_type = 'documents'
                        AND p.target_id = d.id
                        AND p.status IN ('pending_review', 'approved')
                        AND coalesce((p.payload->>'safety_hold')::boolean, false) = true
                  )
                GROUP BY d.country_code
                """
            )
        )
    ).all()
    docs_by_cc = {
        cc: int(n or 0)
        for cc, n in doc_rows
        if cc and cc not in ("ZZ", "INTL", "--")
    }
    total_library_docs = sum(int(n or 0) for cc, n in doc_rows if cc)
    intake_denom = tracked_in
    intake_numer = min(completed_in, max(0, completed_on_time_in))
    intake_rate = _capped_pct(intake_numer, intake_denom) if intake_denom > 0 else 100.0

    from app.iam import service as iam_service
    from app.iam.models import Organization

    trial_pat = f"%{iam_service.TRIAL_ORG_MARKER}%"
    notices_pushed = int(
        (
            await session.scalar(
                select(func.count())
                .select_from(UserNotification)
                .join(Organization, Organization.id == UserNotification.org_id)
                .where(Organization.is_active.is_(True), ~Organization.name.like(trial_pat))
            )
        )
        or 0
    )

    all_ccs = [
        cc
        for cc in sorted(set(docs_by_cc) | set(disc_by_cc) | set(wp_by_cc) | set(adm_by_cc) | set(chg_by_cc))
        if cc
        and cc not in ("ZZ", "INTL", "--")
        and (
            docs_by_cc.get(cc, 0) > 0
            or wp_by_cc.get(cc, 0) > 0
            or adm_by_cc.get(cc, 0) > 0
            or chg_by_cc.get(cc, 0) > 0
            or disc_by_cc.get(cc, {}).get("discovered", 0) > 0
        )
    ]
    by_country = [
        {
            "country_code": cc,
            "country_name_zh": country_name_zh(cc),
            "in_library_count": docs_by_cc.get(cc, 0),
            "discovered_count": disc_by_cc.get(cc, {}).get("discovered", 0),
            "parsed_count": disc_by_cc.get(cc, {}).get("parsed", 0),
            "overdue_30d": disc_by_cc.get(cc, {}).get("overdue_30d", 0),
            "changes_count": chg_by_cc.get(cc, 0),
            "whitepaper_published": bool(wp_by_cc.get(cc, 0)),
            "whitepaper_version": wp_by_cc.get(cc, 0),
            "admission_entries": adm_by_cc.get(cc, 0),
        }
        for cc in all_ccs
    ]

    return {
        "generated_at": now.isoformat(),
        "response_72h": {
            "total_impacts": total_u,
            "pending_updates": pending_u,
            "resolved_updates": resolved_u,
            "overdue_72h": overdue_72h,
            "on_time_rate_pct": rate_72h,
            "has_triggers": total_u > 0,
        },
        "patrol_timeliness": {
            "active_sites": active_sites,
            "healthy_sites": healthy_sites,
            "failed_sites": failed_sites,
            "on_schedule_rate_pct": patrol_rate,
        },
        "intake_30d": {
            "tracked": tracked_in,
            "completed": completed_in,
            "pending": pending_in,
            "overdue_30d": overdue_30d,
            "on_time_rate_pct": intake_rate,
            "in_library_documents_total": total_library_docs,
            "has_triggers": tracked_in > 0,
        },
        "linkage": {
            "countries_covered": len(all_ccs),
            "whitepapers_published": len(wp_by_cc),
            "admission_entries_total": sum(adm_by_cc.values()),
            "enterprise_notices_pushed": notices_pushed,
        },
        "by_country": by_country,
    }


@admin_router.get("/report")
async def updates_sla_report(_: AdminDep, session: SessionDep) -> dict:
    return await _build_sla_linkage_report(session)


@admin_router.get("/report/export.xlsx")
async def export_updates_sla_report(_: AdminDep, session: SessionDep) -> Response:
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    from app.analysis.report_exporter import content_disposition
    from app.core.spreadsheet import write_safe_xlsx_cell

    rep = await _build_sla_linkage_report(session)
    wb = Workbook()
    ws = wb.active
    ws.title = "时效与联动总览"

    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor="FFFFFF")
    head_font = Font(name="Microsoft YaHei", size=11, bold=True, color="000000")
    body_font = Font(name="Microsoft YaHei", size=10, color="000000")

    for col_i, h in enumerate(["指标维度", "核心考核口径", "当前统计值", "达标率 / 状态"], 1):
        write_safe_xlsx_cell(ws, 1, col_i, h)
    r72 = rep["response_72h"]
    pat = rep["patrol_timeliness"]
    in30 = rep["intake_30d"]
    lnk = rep["linkage"]
    summary_rows = [
        [
            "72 小时修订响应达标率",
            "法规条文变更后 72 小时内完成影响核定与通知推送",
            f"累计触发 {r72['total_impacts']} 项（待处理 {r72['pending_updates']}，已处理完成 {r72['resolved_updates']}，超期 {r72['overdue_72h']}）",
            f"{r72['on_time_rate_pct']}%" if r72["total_impacts"] > 0 else "周期内无新增触发项",
        ],
        [
            "官方来源按期检查率",
            "已启用官方站点按计划周期完成自动检查的比例",
            f"启用站点 {pat['active_sites']} 个（连通正常 {pat['healthy_sites']}，待处理 {pat['failed_sites']}）",
            f"{pat['on_schedule_rate_pct']}%" if pat["active_sites"] > 0 else "暂无启用站点",
        ],
        [
            "30 日新规入库及时率",
            "新发现法规自发现起 30 日内完成解析拆条入库",
            f"在库有效法规 {in30['in_library_documents_total']} 份 · 近周期新发现跟踪 {in30['tracked']} 份（已入库 {in30['completed']}，处理中 {in30['pending']}，超期 {in30['overdue_30d']}）",
            f"{in30['on_time_rate_pct']}%" if in30["tracked"] > 0 else "周期内无新增触发项",
        ],
        [
            "跨模块业务联动覆盖",
            "法规库变更向国家简报、属地准入与企业通知同步",
            f"覆盖 {lnk['countries_covered']} 国 · 已发布简报 {lnk['whitepapers_published']} 国 · 准入事项 {lnk['admission_entries_total']} 项",
            f"累计推送企业提醒 {lnk['enterprise_notices_pushed']} 条",
        ],
    ]
    for sr_i, sr in enumerate(summary_rows, 2):
        for col_i, val in enumerate(sr, 1):
            write_safe_xlsx_cell(ws, sr_i, col_i, val)
    for row_idx, row_cells in enumerate(ws.iter_rows(min_row=1, max_row=ws.max_row), 1):
        for cell in row_cells:
            cell.border = border
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if row_idx == 1:
                cell.fill = head_fill
                cell.font = head_font
            else:
                cell.font = body_font
        ws.row_dimensions[row_idx].height = 28 if row_idx == 1 else 32
    for col_letter, width in (("A", 24), ("B", 42), ("C", 50), ("D", 24)):
        ws.column_dimensions[col_letter].width = width

    ws2 = wb.create_sheet("分国别入库与联动明细")
    for col_i, h in enumerate(
        ["国家或地区", "在库生效法规数", "新发现跟踪数", "已完成入库数", "30日超期数", "条文修订记录", "国家简报状态", "关联准入事项数"],
        1,
    ):
        write_safe_xlsx_cell(ws2, 1, col_i, h)
    for c_i, c in enumerate(rep["by_country"], 2):
        row_vals = [
            c["country_name_zh"],
            c["in_library_count"],
            c["discovered_count"],
            c["parsed_count"],
            c["overdue_30d"],
            c["changes_count"],
            "已发布" if c["whitepaper_published"] else ("国内法规底座" if c["country_code"] == "CN" else "未发布"),
            c["admission_entries"],
        ]
        for col_i, val in enumerate(row_vals, 1):
            write_safe_xlsx_cell(ws2, c_i, col_i, val, neutralize_formula_prefix=True)
    for row_idx, row_cells in enumerate(ws2.iter_rows(min_row=1, max_row=ws2.max_row), 1):
        for cell in row_cells:
            cell.border = border
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if row_idx == 1:
                cell.fill = head_fill
                cell.font = head_font
            else:
                cell.font = body_font
        ws2.row_dimensions[row_idx].height = 26
    for col_letter, width in (("A", 16), ("B", 16), ("C", 14), ("D", 14), ("E", 14), ("F", 16), ("G", 18), ("H", 16)):
        ws2.column_dimensions[col_letter].width = width

    buf = io.BytesIO()
    wb.save(buf)
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition("法规监测72小时响应与联动统计报表.xlsx")},
    )

