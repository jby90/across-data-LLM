# ruff: noqa: E501
"""Opinion monitoring for the admin side: the graded items, the sources and their last run."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import set_detail
from app.core.db import get_session
from app.iam.deps import Principal, require_platform_admin
from app.opinion import weekly_digest as wd
from app.opinion.models import OpinionDigestRun, OpinionItem, OpinionSource

router = APIRouter(prefix="/api/admin/opinion", tags=["opinion"])
AdminDep = Annotated[Principal, Depends(require_platform_admin)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
LEVEL_LABEL = {"high": "高", "medium": "中", "low": "低"}
_ORDER = case((OpinionItem.level == "high", 0), (OpinionItem.level == "medium", 1), else_=2)


@router.get("/summary")
async def summary(_: AdminDep, session: SessionDep, days: int = Query(30, ge=1, le=365)) -> dict:
    since = date.fromordinal(date.today().toordinal() - days)
    rows = (
        await session.execute(
            select(OpinionItem.level, func.count())
            .where(OpinionItem.published_on >= since)
            .group_by(OpinionItem.level)
        )
    ).all()
    all_rows = (
        await session.execute(select(OpinionItem.level, func.count()).group_by(OpinionItem.level))
    ).all()
    counts = {level or "ungraded": n for level, n in rows}
    total_counts = {level or "ungraded": n for level, n in all_rows}
    total = await session.scalar(select(func.count()).select_from(OpinionItem))
    return {"days": days, "counts": counts, "total_counts": total_counts, "total": total}


@router.get("/items")
async def items(
    _: AdminDep,
    session: SessionDep,
    level: Literal["high", "medium", "low"] | None = None,
    kind: Literal["regulator", "industry", "media"] | None = None,
    q: str | None = Query(None, max_length=60),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=2147483647),
) -> list[dict]:
    stmt = select(OpinionItem).order_by(
        _ORDER, OpinionItem.published_on.desc().nulls_last(), OpinionItem.fetched_at.desc()
    )
    if level:
        stmt = stmt.where(OpinionItem.level == level)
    if kind:
        stmt = stmt.where(OpinionItem.kind == kind)
    if q:
        stmt = stmt.where(OpinionItem.title.ilike(f"%{q}%"))
    rows = (await session.scalars(stmt.limit(limit).offset(offset))).all()
    return [
        {
            "id": str(r.id),
            "title": r.title,
            "publisher": r.publisher,
            "kind": r.kind,
            "url": r.url,
            "published_on": r.published_on.isoformat() if r.published_on else None,
            "level": r.level,
            "level_label": LEVEL_LABEL.get(r.level or "", "待分级"),
            "reasons": r.bases or [],
            "scale_unknown": r.scale_unknown,
        }
        for r in rows
    ]


@router.get("/items/{item_id}")
async def item(item_id: uuid.UUID, _: AdminDep, session: SessionDep) -> dict:
    row = await session.get(OpinionItem, item_id)
    if row is None:
        raise HTTPException(404, "资讯不存在")
    evidence = (row.facts or {}).get("evidence", {})
    clean_excerpt = wd.strip_portal_noise(row.body or "")[:600]
    return {
        "id": str(row.id),
        "title": row.title,
        "publisher": row.publisher,
        "url": row.url,
        "published_on": row.published_on.isoformat() if row.published_on else None,
        "level": row.level,
        "level_label": LEVEL_LABEL.get(row.level or "", "待分级"),
        "reasons": row.bases or [],
        "evidence": [s for s in evidence.values() if s],
        "scale_unknown": row.scale_unknown,
        "excerpt": clean_excerpt,
    }


def _clean_source_note(note: str | None, status: str | None) -> str:
    raw = (note or "").strip()
    if not raw:
        return "尚未采集"
    if status == "failed" or ":" in raw and any(c.isascii() and c.isalpha() for c in raw[:24]):
        return "官方站点连接超时或访问受限，稍后自动重试"
    return raw


def _clean_run_note(note: str | None) -> str:
    import re

    raw = (note or "").strip()
    if not raw:
        return "已同步发布至用户端行业合规周报专栏"
    raw = re.sub(r"（[^（）]*\.eml）", "", raw)
    raw = re.sub(r"\([^()]*\.eml\)", "", raw)
    raw = raw.replace("通过 SMTP 推送", "通过邮件推送").replace("配置 SMTP 服务器", "配置发信邮件服务器")
    raw = re.sub(r"SMTP\s*连接未就绪（[^）]*）", "邮件服务器暂未连通", raw)
    return raw


@router.get("/sources")
async def sources(_: AdminDep, session: SessionDep) -> list[dict]:
    rows = (await session.scalars(select(OpinionSource).order_by(OpinionSource.publisher, OpinionSource.entry_url))).all()
    item_counts = dict(
        (await session.execute(select(OpinionItem.publisher, func.count()).group_by(OpinionItem.publisher))).all()
    )
    grouped: dict[str, dict] = {}
    for r in rows:
        g = grouped.get(r.publisher)
        clean_note = _clean_source_note(r.last_note, r.last_status)
        if g is None:
            grouped[r.publisher] = {
                "id": str(r.id),
                "publisher": r.publisher,
                "kind": r.kind,
                "entry_url": r.entry_url,
                "enabled": r.enabled,
                "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
                "last_status": r.last_status,
                "last_note": clean_note,
                "_sections": 1,
            }
        else:
            g["_sections"] += 1
            if r.last_run_at and (not g["last_run_at"] or r.last_run_at.isoformat() > g["last_run_at"]):
                g["last_run_at"] = r.last_run_at.isoformat()
                g["last_status"] = r.last_status
                g["last_note"] = clean_note
    out = []
    for pub, g in grouped.items():
        sections = g.pop("_sections")
        total_pub = int(item_counts.get(pub, 0) or 0)
        if g["last_status"] != "failed" and total_pub > 0:
            g["last_note"] = f"已归档汽车数据与法规监管动态 {total_pub} 条" + (
                f"（覆盖 {sections} 个官方栏目）" if sections > 1 else ""
            )
        elif sections > 1 and g["last_note"]:
            g["last_note"] = f"{g['last_note']}（覆盖 {sections} 个官方栏目）"
        out.append(g)
    return out


_SIMPLE_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _validate_recipient_list(raw: list[str] | None) -> list[str] | None:
    if raw is None:
        return None
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("收件人邮箱格式不正确")
        addr = item.strip()
        if not addr:
            continue
        if len(addr) > 254 or any(ord(c) < 33 for c in addr) or not _SIMPLE_EMAIL_RE.fullmatch(addr):
            raise ValueError("收件人邮箱地址格式无效，请填写规范的电子邮箱")
        if addr not in out:
            out.append(addr)
    return out


class DigestConfigIn(BaseModel):
    enabled: bool = True
    weekday: int = Field(default=1, ge=1, le=7)
    hour_utc: int = Field(default=1, ge=0, le=23)
    lookback_days: int = Field(default=7, ge=1, le=90)
    min_level: Literal["high", "medium", "low"] = "low"
    recipients: list[str] = Field(default_factory=list, max_length=50)
    smtp_host: str = Field(default="", max_length=255)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str = Field(default="", max_length=255)
    smtp_from: str = Field(default="", max_length=255)

    @field_validator("recipients")
    @classmethod
    def _check_rcpts(cls, v: list[str]) -> list[str]:
        return _validate_recipient_list(v) or []

    @field_validator("smtp_host", "smtp_user", "smtp_from")
    @classmethod
    def _check_no_crlf(cls, v: str) -> str:
        s = v.strip()
        if any(ord(c) < 32 for c in s):
            raise ValueError("发件配置不能包含换行或控制字符")
        return s


class DigestSendIn(BaseModel):
    recipients: list[str] | None = Field(default=None, max_length=50)

    @field_validator("recipients")
    @classmethod
    def _check_send_rcpts(cls, v: list[str] | None) -> list[str] | None:
        return _validate_recipient_list(v)


def _cfg_out(cfg) -> dict:
    return {
        "enabled": bool(cfg.enabled),
        "weekday": cfg.weekday,
        "hour_utc": cfg.hour_utc,
        "lookback_days": cfg.lookback_days,
        "min_level": cfg.min_level,
        "recipients": list(cfg.recipients or []),
        "smtp_host": cfg.smtp_host or "",
        "smtp_port": cfg.smtp_port or 587,
        "smtp_user": cfg.smtp_user or "",
        "smtp_from": cfg.smtp_from or "",
        "next_run_at": cfg.next_run_at.isoformat() if cfg.next_run_at else None,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


@router.get("/digest-config")
async def get_digest_config(_: AdminDep, session: SessionDep) -> dict:
    cfg = await wd.get_or_create_config(session)
    await session.commit()
    return _cfg_out(cfg)


@router.put("/digest-config")
async def update_digest_config(
    body: DigestConfigIn, request: Request, admin: AdminDep, session: SessionDep
) -> dict:
    from app.iam.models import Role

    cfg = await wd.get_or_create_config(session)
    clean_rcpts = list(
        dict.fromkeys(r.strip() for r in body.recipients if isinstance(r, str) and "@" in r.strip())
    )
    cfg.enabled = body.enabled
    cfg.weekday = body.weekday
    cfg.hour_utc = body.hour_utc
    cfg.lookback_days = body.lookback_days
    cfg.min_level = body.min_level
    cfg.recipients = clean_rcpts
    if admin.role == Role.PLATFORM_ADMIN:
        cfg.smtp_host = body.smtp_host.strip()
        cfg.smtp_port = body.smtp_port
        cfg.smtp_user = body.smtp_user.strip()
        cfg.smtp_from = body.smtp_from.strip()
    cfg.next_run_at = (
        wd.compute_next_digest_run(datetime.now(UTC), body.weekday, body.hour_utc)
        if body.enabled
        else None
    )
    await session.commit()
    await session.refresh(cfg)
    set_detail(request, summary=f"配置舆情合规周报定时推送（启用：{body.enabled}，收件人：{len(clean_rcpts)} 个）")
    return _cfg_out(cfg)


@router.get("/digest-preview")
async def preview_digest(
    _: AdminDep,
    session: SessionDep,
    days: int = Query(7, ge=1, le=90),
    min_level: Literal["high", "medium", "low"] = "low",
) -> dict:
    payload = await wd.build_digest_payload(session, lookback_days=days, min_level=min_level)
    return {
        "subject": payload["subject"],
        "period_label": payload["period_label"],
        "item_count": payload["item_count"],
        "items": payload["items"],
        "html_body": payload["html_body"],
    }


@router.post("/digest-send")
async def send_digest(
    body: DigestSendIn, request: Request, _: AdminDep, session: SessionDep
) -> dict:
    run = await wd.execute_digest_send(session, override_recipients=body.recipients)
    await session.commit()
    await session.refresh(run)
    set_detail(request, summary=f"生成并推送《{run.subject}》（共 {run.item_count} 条资讯）")
    return {
        "id": str(run.id),
        "subject": run.subject,
        "period_start": run.period_start.isoformat(),
        "period_end": run.period_end.isoformat(),
        "item_count": run.item_count,
        "delivery_mode": run.delivery_mode,
        "status": run.status,
        "note": run.note,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


@router.get("/digest-runs")
async def list_digest_runs(
    _: AdminDep, session: SessionDep, limit: int = Query(20, ge=1, le=50)
) -> list[dict]:
    rows = (
        await session.scalars(
            select(OpinionDigestRun).order_by(OpinionDigestRun.created_at.desc()).limit(limit)
        )
    ).all()
    return [
        {
            "id": str(r.id),
            "subject": r.subject,
            "period_start": r.period_start.isoformat(),
            "period_end": r.period_end.isoformat(),
            "item_count": r.item_count,
            "delivery_mode": r.delivery_mode,
            "recipients": list(r.recipients or []),
            "status": r.status,
            "note": _clean_run_note(r.note),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]

