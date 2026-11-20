# ruff: noqa: E501
"""Audit log search and Excel export (platform administrators). Read-only: there is no endpoint
that edits or deletes a record, and the database refuses it as well."""

from __future__ import annotations

import io
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.report_exporter import content_disposition
from app.audit import describe
from app.audit.models import AuditLog
from app.audit.service import display_action, set_detail
from app.core.db import get_session
from app.iam.deps import Principal, require_platform_admin

router = APIRouter(prefix="/api/admin/audit-logs", tags=["audit"])
AdminDep = Annotated[Principal, Depends(require_platform_admin)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]

CATEGORY_ZH = {"login": "登录", "export": "导出", "change": "新增/修改", "delete": "删除"}
ROLE_ZH = {
    "platform_admin": "超级管理员",
    "ops_admin": "运维管理员",
    "org_admin": "企业用户",
    "member": "企业用户",
    "viewer": "普通查看者",
    "reviewer": "已停用账号",
}
MAX_EXPORT_ROWS = 50000


class Filters:
    def __init__(
        self,
        start: datetime | None = Query(None, description="起始时间"),
        end: datetime | None = Query(None, description="结束时间（含）"),
        actor: str | None = Query(None, max_length=128, description="人员：姓名或邮箱"),
        category: str | None = Query(None, pattern="^(login|export|change|delete)$"),
        q: str | None = Query(None, max_length=128, description="动作或内容关键字"),
        ip: str | None = Query(None, max_length=64),
        active_only: bool = Query(False, description="仅展示在用正式企业与正式账号操作留痕"),
    ):
        from datetime import UTC

        from fastapi import HTTPException

        def _norm_dt(dt_val: datetime | None) -> datetime | None:
            if dt_val is None:
                return None
            if dt_val.tzinfo is None:
                return dt_val.replace(tzinfo=UTC)
            try:
                return dt_val.astimezone(UTC)
            except OverflowError:
                raise HTTPException(422, "时间筛选参数超出有效年份范围，请调整日期后重试") from None

        self.start, self.end, self.actor, self.category, self.q, self.ip, self.active_only = (
            _norm_dt(start), _norm_dt(end), actor, category, q, ip, active_only,
        )

    def apply(self, stmt):
        if self.active_only:
            from app.iam.models import Organization
            from app.iam.service import TRIAL_ORG_MARKER

            active_formal_orgs = select(Organization.id).where(
                Organization.is_active.is_(True),
                ~Organization.name.like(f"%{TRIAL_ORG_MARKER}%"),
                ~Organization.name.like("压测临时车企_%"),
            )
            stmt = stmt.where(
                or_(AuditLog.org_id.is_(None), AuditLog.org_id.in_(active_formal_orgs)),
                or_(AuditLog.summary.is_(None), ~AuditLog.summary.ilike(f"%{TRIAL_ORG_MARKER}%")),
                or_(AuditLog.summary.is_(None), ~AuditLog.summary.ilike("%压测临时车企_%")),
                or_(AuditLog.actor_email.is_(None), ~AuditLog.actor_email.ilike("bench_%")),
                or_(AuditLog.actor_email.is_(None), ~AuditLog.actor_email.ilike("loadtest_%")),
                or_(AuditLog.actor_email.is_(None), ~AuditLog.actor_email.ilike("audit_trial_%")),
                or_(AuditLog.actor_email.is_(None), ~AuditLog.actor_email.ilike("gallery_trial_%")),
                or_(AuditLog.actor_email.is_(None), ~AuditLog.actor_email.ilike("browser_trial_%")),
                or_(AuditLog.actor_name.is_(None), ~AuditLog.actor_name.ilike("bench_%")),
                or_(AuditLog.actor_name.is_(None), ~AuditLog.actor_name.ilike("loadtest_%")),
            )
        if self.start:
            stmt = stmt.where(AuditLog.at >= self.start)
        if self.end:
            try:
                upper_end = self.end + timedelta(seconds=1)
                stmt = stmt.where(AuditLog.at < upper_end)
            except OverflowError:
                stmt = stmt.where(AuditLog.at <= self.end)
        if self.actor:
            like = f"%{self.actor}%"
            stmt = stmt.where(or_(AuditLog.actor_name.ilike(like), AuditLog.actor_email.ilike(like)))
        if self.category:
            stmt = stmt.where(AuditLog.category == self.category)
        if self.q:
            like = f"%{self.q}%"
            conds = [AuditLog.action.ilike(like), AuditLog.summary.ilike(like)]
            for (_, tpl), zh in describe.ROUTE_ACTIONS.items():
                if self.q in zh:
                    if "{" not in tpl:
                        conds.append(AuditLog.path == tpl)
                    else:
                        prefix = tpl.split("{", 1)[0]
                        if prefix and prefix != "/api/":
                            conds.append(AuditLog.path.startswith(prefix))
            stmt = stmt.where(or_(*conds))
        if self.ip:
            stmt = stmt.where(AuditLog.ip == self.ip)
        return stmt


def _out(r: AuditLog) -> dict:
    act = display_action(r.category, r.action, r.method, r.path)
    hum = describe.humanize(r.summary)
    clean_before = describe.humanize_payload(r.before)
    clean_after = describe.humanize_payload(r.after)
    return {
        "id": str(r.id),
        "at": r.at.isoformat(),
        "actor_name": r.actor_name,
        "actor_email": r.actor_email,
        "actor_role": r.actor_role,
        "ip": r.ip,
        "category": r.category,
        "category_zh": CATEGORY_ZH.get(r.category, r.category),
        "action": act,
        "path": r.path,
        "status": r.status,
        "summary": hum if (hum and hum.strip()) else describe.fallback_summary(r.category, act, r.method, r.path, r.status),
        "before": clean_before,
        "after": clean_after,
        "before_text": describe.readable(clean_before),
        "after_text": describe.readable(clean_after),
        "content_hash": r.content_hash,
    }


@router.get("")
async def search(
    admin: AdminDep,
    session: SessionDep,
    f: Annotated[Filters, Depends()],
    offset: int = Query(0, ge=0, le=2147483647),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    total = (await session.execute(f.apply(select(func.count()).select_from(AuditLog)))).scalar_one()
    rows = (
        (await session.execute(f.apply(select(AuditLog)).order_by(AuditLog.at.desc()).offset(offset).limit(limit)))
        .scalars()
        .all()
    )
    return {"total": total, "items": [_out(r) for r in rows]}


@router.get("/export.xlsx")
async def export_xlsx(
    request: Request, admin: AdminDep, session: SessionDep, f: Annotated[Filters, Depends()]
) -> Response:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    rows = (
        (await session.execute(f.apply(select(AuditLog)).order_by(AuditLog.at.desc()).limit(MAX_EXPORT_ROWS)))
        .scalars()
        .all()
    )
    wb = Workbook()
    from app.core.spreadsheet import write_safe_xlsx_cell

    ws = wb.active
    ws.title = "审计记录"
    head = ["时间", "人员", "邮箱", "角色", "来源地址", "类型", "动作", "说明", "结果", "修改前", "修改后", "记录校验码"]
    for j, h in enumerate(head, 1):
        write_safe_xlsx_cell(ws, 1, j, h).font = Font(bold=True)

    for i, r in enumerate(rows, 2):
        act = display_action(r.category, r.action, r.method, r.path)
        hum = describe.humanize(r.summary)
        summ = hum if (hum and hum.strip()) else describe.fallback_summary(r.category, act, r.method, r.path, r.status)
        clean_before = describe.humanize_payload(r.before)
        clean_after = describe.humanize_payload(r.after)
        values = [
            r.at.astimezone().strftime("%Y-%m-%d %H:%M:%S"), r.actor_name, r.actor_email, ROLE_ZH.get(r.actor_role, r.actor_role),
            r.ip, CATEGORY_ZH.get(r.category, r.category), act, summ,
            "成功" if r.status < 400 else f"未通过（{describe.status_reason_zh(r.status)}）", describe.readable(clean_before), describe.readable(clean_after), r.content_hash,
        ]
        for j, v in enumerate(values, 1):
            write_safe_xlsx_cell(ws, i, j, v, neutralize_formula_prefix=True)
    for col, width in zip("ABCDEFGHIJKL", (20, 14, 26, 14, 16, 12, 30, 40, 10, 40, 40, 20), strict=False):
        ws.column_dimensions[col].width = width
    buf = io.BytesIO()
    wb.save(buf)
    set_detail(request, summary=f"导出审计记录 {len(rows)} 条")
    name = f"审计记录_{datetime.now():%Y%m%d_%H%M}.xlsx"
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition(name)},
    )


@router.get("/export.csv")
async def export_csv(
    request: Request, admin: AdminDep, session: SessionDep, f: Annotated[Filters, Depends()]
) -> Response:
    import csv

    from app.core.spreadsheet import sanitize_csv_cell

    rows = (
        (await session.execute(f.apply(select(AuditLog)).order_by(AuditLog.at.desc()).limit(MAX_EXPORT_ROWS)))
        .scalars()
        .all()
    )
    sio = io.StringIO()
    writer = csv.writer(sio, lineterminator="\r\n")
    head = ["时间", "人员", "邮箱", "角色", "来源地址", "类型", "动作", "说明", "结果", "修改前", "修改后", "记录校验码"]
    writer.writerow(head)
    for r in rows:
        act = display_action(r.category, r.action, r.method, r.path)
        hum = describe.humanize(r.summary)
        summ = hum if (hum and hum.strip()) else describe.fallback_summary(r.category, act, r.method, r.path, r.status)
        clean_before = describe.humanize_payload(r.before)
        clean_after = describe.humanize_payload(r.after)
        raw_row = [
            r.at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            r.actor_name or "",
            r.actor_email or "",
            ROLE_ZH.get(r.actor_role, r.actor_role),
            r.ip or "",
            CATEGORY_ZH.get(r.category, r.category),
            act,
            summ,
            "成功" if r.status < 400 else f"未通过（{describe.status_reason_zh(r.status)}）",
            describe.readable(clean_before),
            describe.readable(clean_after),
            r.content_hash,
        ]
        writer.writerow([sanitize_csv_cell(v) for v in raw_row])
    set_detail(request, summary=f"导出审计记录 {len(rows)} 条")
    name = f"审计记录_{datetime.now():%Y%m%d_%H%M}.csv"
    payload = ("\ufeff" + sio.getvalue()).encode("utf-8")
    return Response(
        payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition(name)},
    )
