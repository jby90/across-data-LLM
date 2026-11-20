# ruff: noqa: E501
"""Content-change observations and operational reminders, separate from legal approvals."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RegulatoryDiscovery(Base):
    __tablename__ = "regulatory_discoveries"
    __table_args__ = (
        UniqueConstraint("site_id", "source_url", name="uq_discovery_site_url"),
        Index("ix_discovery_country_time", "country_code", "discovered_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_sites.id", ondelete="SET NULL"), index=True
    )
    country_code: Mapped[str] = mapped_column(String(8))
    source_url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(500))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), index=True, nullable=True
    )


class SourceFetchObservation(Base):
    __tablename__ = "source_fetch_observations"
    __table_args__ = (Index("ix_source_fetch_doc_sequence", "document_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id"), index=True
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id")
    )
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id"), nullable=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    comparison_basis: Mapped[str] = mapped_column(String(32))


class VersionChange(Base):
    __tablename__ = "version_changes"
    __table_args__ = (UniqueConstraint("origin_key", name="uq_version_change_origin"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    origin_key: Mapped[str] = mapped_column(String(100))
    source_observation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_fetch_observations.id"), nullable=True, unique=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id"))
    old_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id"), index=True
    )
    new_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id")
    )
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    change_kind: Mapped[str] = mapped_column(String(40))
    summary: Mapped[str] = mapped_column(Text)
    changes_json: Mapped[list] = mapped_column(JSONB)
    graph_edge_ids: Mapped[list] = mapped_column(JSONB)
    content_hashes: Mapped[dict] = mapped_column(JSONB)


class UpdateImpact(Base):
    __tablename__ = "update_impacts"
    __table_args__ = (
        UniqueConstraint(
            "report_version_id", "dimension_code", "change_id", name="uq_report_change_impact"
        ),
        Index("ix_update_impacts_state_due", "state", "due_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_tasks.id", ondelete="CASCADE"), index=True
    )
    report_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("report_versions.id", ondelete="CASCADE")
    )
    dimension_code: Mapped[str] = mapped_column(String(8))
    change_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("version_changes.id"), index=True
    )
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(24), default="open")
    resolved_report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("report_versions.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserNotification(Base):
    __tablename__ = "user_notifications"
    __table_args__ = (
        UniqueConstraint("task_id", "change_id", "user_id", name="uq_change_recipient"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index("uq_notification_user_key", "user_id", "dedupe_key", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    impact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("update_impacts.id", ondelete="CASCADE"), index=True, nullable=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_tasks.id", ondelete="CASCADE"), index=True, nullable=True
    )
    change_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("version_changes.id"), index=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 法规条文变化影响评估报告：source_change（上面三栏有值）；简报出新版：whitepaper；准入事项有变化：admission
    kind: Mapped[str] = mapped_column(String(24), default="source_change", server_default="source_change")
    title: Mapped[str] = mapped_column(Text, default="", server_default="")
    body: Mapped[str] = mapped_column(Text, default="", server_default="")
    link: Mapped[str] = mapped_column(String(512), default="", server_default="")
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)


class UserSubscription(Base):
    """需求 2.3：一个企业订阅哪些法规变化（国家、法规类型、业务领域；一栏不选 = 这一栏不限）。"""

    __tablename__ = "user_subscriptions"
    __table_args__ = (UniqueConstraint("org_id", name="uq_user_subscriptions_org"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    countries: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    doc_types: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    topics: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChangeAdvice(Base):
    """一次法规变化（修订或新收录）写给企业看的内容：变了的条文前后对照、影响说明、整改要做的事。
    每次变化只写一次，所有提到这次变化的通知共用。state: pending / ready / failed。"""

    __tablename__ = "change_advice"

    # change:<version_changes.id>（已收录法规的修订）或 doc:<documents.id>（新收录的法规）
    subject: Mapped[str] = mapped_column(String(80), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    pairs: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    impact_zh: Mapped[str] = mapped_column(Text, default="", server_default="")
    actions: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    state: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    model: Mapped[str] = mapped_column(String(128), default="", server_default="")
    tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
