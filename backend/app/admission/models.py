# ruff: noqa: E501
"""Overseas market admission: what a country requires before a vehicle or data service may be put
on its market (entries and their steps, each tied to the clause that states it), and an
organisation's own handling of an entry (project, progress ledger, material drafts)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _pk():
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now():
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class AdmissionEntry(Base):
    __tablename__ = "admission_entries"
    __table_args__ = (Index("ux_admission_entries_edge", "edge_id", unique=True),)

    id: Mapped[uuid.UUID] = _pk()
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    name_zh: Mapped[str] = mapped_column(String(256))
    category: Mapped[str] = mapped_column(String(32))
    authority_zh: Mapped[str] = mapped_column(String(256), default="")
    summary_zh: Mapped[str] = mapped_column(Text, default="")
    edge_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    evidence_clause_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clauses.id", ondelete="SET NULL"), nullable=True
    )
    evidence_quote: Mapped[str] = mapped_column(Text, default="")
    review_status: Mapped[str] = mapped_column(String(24), default="pending_review")
    # 与另一事项是同一办理事项（同一受理机构、同一件事）：并入那一项，用户端只显示那一项
    merged_into: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    functions: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")  # 适用业务（function_tags）
    # 固定栏目（item_profile）：本地测试、本地代表、申请语言、有效期，法规没写的为“待核实”
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    # 法规列的每项材料怎么准备（material_kinds）：写进申请材料的信息 / 官方表格 / 申请材料本身 / 递交时自带的原件
    materials_plan: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    # 管理员手改过的栏目（name_zh / authority_zh / summary_zh / materials_plan）：重新抽取、去重、补中文名等命令不覆盖
    edited: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AdmissionStep(Base):
    __tablename__ = "admission_steps"

    id: Mapped[uuid.UUID] = _pk()
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admission_entries.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    title_zh: Mapped[str] = mapped_column(String(256))
    detail_zh: Mapped[str] = mapped_column(Text, default="")
    materials: Mapped[list[str]] = mapped_column(JSONB, default=list)
    evidence_clause_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clauses.id", ondelete="SET NULL"), nullable=True
    )
    evidence_quote: Mapped[str] = mapped_column(Text, default="")
    edited: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # 管理员手改过：命令不覆盖


class AdmissionProject(Base):
    __tablename__ = "admission_projects"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admission_entries.id")
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(256))
    facts: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, server_default="{}")
    applicant: Mapped[str] = mapped_column(String(256), default="")
    vehicle_model: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    # 平台以外发生的事，由用户记下：{"submitted": {...}, "result": {...}}（见 router.MilestoneIn）
    milestones: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")  # 已停用
    function_key: Mapped[str] = mapped_column(String(32), default="", server_default="")  # 为哪项业务准备
    entry_seen_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 上次提醒时事项的内容指纹
    # 取得的批文：编号、签发日、到期日、文件（到期提醒）
    approval: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AdmissionProgress(Base):
    __tablename__ = "admission_progress"

    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admission_projects.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admission_steps.id", ondelete="SET NULL"), nullable=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    title_zh: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), default="todo")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    owner: Mapped[str] = mapped_column(String(128), default="")
    # law = 条文规定的环节；generic = 条文没写环节时的通用环节；custom = 企业自己加的
    kind: Mapped[str] = mapped_column(String(16), default="custom", server_default="custom")
    note: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AdmissionDraft(Base):
    __tablename__ = "admission_drafts"

    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admission_projects.id", ondelete="CASCADE"), index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    name_zh: Mapped[str] = mapped_column(String(128))
    blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    missing: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # 生成这一版时填好的官方表格（代码、名称、填写内容），这一版可以原样再下载
    forms: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    # 对应这一版申请书（对外申请文书部分）译成受理机构官方语言并经反向核验的正式稿
    foreign_draft: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = _now()


class AdmissionIssue(Base):
    """Something wrong or missing in the filled material: found by the platform's check (kind “check”)
    or written down by the enterprise (kind “manual”); closed when fixed."""

    __tablename__ = "admission_issues"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admission_projects.id", ondelete="CASCADE"), index=True
    )
    progress_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admission_progress.id", ondelete="SET NULL"), nullable=True
    )
    question: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")  # check = 平台检查发现
    target: Mapped[str] = mapped_column(String(128), default="", server_default="")  # 指向哪一项（info:/form:/file:/draft）
    answer: Mapped[str] = mapped_column(Text, default="", server_default="")
    state: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AdmissionFile(Base):
    """A document kept with the filing: what was submitted, receipts, the authority's decision."""

    __tablename__ = "admission_files"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admission_projects.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(100), default="", server_default="")
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)  # 下载时才读
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = _now()
