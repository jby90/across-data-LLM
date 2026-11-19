# ruff: noqa: E501
"""Collected public-opinion items about automotive data, and the sources they come from.

Opinion items are kept apart from the legal library on purpose: a news item must never be
indexed, cited or translated as if it were law. Each item keeps its source, link and the text it
was graded on, and the grade keeps the rules and the facts (with the sentences) that produced it.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class OpinionSource(Base):
    __tablename__ = "opinion_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    publisher: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))  # regulator / industry / media
    entry_url: Mapped[str] = mapped_column(String(1024), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class OpinionItem(Base):
    __tablename__ = "opinion_items"
    __table_args__ = (
        Index("ix_opinion_items_level_date", "level", "published_on"),
        Index("ix_opinion_items_fetched", "fetched_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opinion_sources.id", ondelete="SET NULL"), nullable=True
    )
    publisher: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    published_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    body: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    matched_keywords: Mapped[list] = mapped_column(JSONB, default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # grading (None until graded)
    level: Mapped[str | None] = mapped_column(String(8), nullable=True)
    rules: Mapped[list] = mapped_column(JSONB, default=list)
    bases: Mapped[list] = mapped_column(JSONB, default=list)
    facts: Mapped[dict] = mapped_column(JSONB, default=dict)
    scale_unknown: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grading_version: Mapped[str | None] = mapped_column(String(32), nullable=True)


class OpinionDigestConfig(Base):
    __tablename__ = "opinion_digest_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    weekday: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))  # 1=周一 .. 7=周日
    hour_utc: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))  # 默认北京时间 09:00
    lookback_days: Mapped[int] = mapped_column(Integer, default=7, server_default=text("7"))
    min_level: Mapped[str] = mapped_column(String(16), default="low", server_default=text("'low'"))
    recipients: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    smtp_host: Mapped[str] = mapped_column(String(255), default="", server_default=text("''"))
    smtp_port: Mapped[int] = mapped_column(Integer, default=587, server_default=text("587"))
    smtp_user: Mapped[str] = mapped_column(String(255), default="", server_default=text("''"))
    smtp_from: Mapped[str] = mapped_column(String(255), default="", server_default=text("''"))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OpinionDigestRun(Base):
    __tablename__ = "opinion_digest_runs"
    __table_args__ = (Index("ix_opinion_digest_runs_created", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    subject: Mapped[str] = mapped_column(String(255))
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    items_json: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    html_body: Mapped[str] = mapped_column(Text, default="")
    delivery_mode: Mapped[str] = mapped_column(String(32), default="portal_only")
    recipients: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(String(32), default="sent")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

