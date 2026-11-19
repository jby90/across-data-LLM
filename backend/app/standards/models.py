"""The directory of standards (number, title, status, dates), kept apart from the legal library.

Standards are copyrighted by the body that publishes them, so only directory information is
stored: no full text. Each entry keeps where it was listed, so it can be re-checked at the source.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class StandardEntry(Base):
    __tablename__ = "standard_entries"
    __table_args__ = (Index("ix_standard_entries_country_status", "country_code", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    country_code: Mapped[str] = mapped_column(String(2))
    number: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(16))  # mandatory / recommended / guidance
    status: Mapped[str] = mapped_column(String(16))  # in_force / upcoming / withdrawn / draft
    published_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    publisher: Mapped[str] = mapped_column(String(128))
    source_url: Mapped[str] = mapped_column(String(1024))
    detail_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    topics: Mapped[list] = mapped_column(JSONB, default=list)
    matched_keywords: Mapped[list] = mapped_column(JSONB, default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
