# ruff: noqa: E501
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class VersionState(enum.StrEnum):
    DOWNLOADED = "downloaded"  # 已下载并归档
    PARSED = "parsed"  # 已解析、已条款化
    NEEDS_OCR = "needs_ocr"  # 没有文字层(扫描件),待 OCR
    FAILED = "failed"  # 解析失败(原文已归档,可重跑)


class Document(Base):
    """一份官方文件(以来源 URL 为身份)。内容变化时产生新的版本,旧版本保留。"""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("site_id", "source_url", name="uq_documents_site_url"),
        CheckConstraint(
            "relevance IN ('relevant','related','irrelevant','out_of_scope','needs_review')",
            name="ck_documents_relevance",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_sites.id", ondelete="CASCADE"), index=True
    )
    country_code: Mapped[str] = mapped_column(String(2), index=True)
    source_url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(500))
    title_zh: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 信源清单里的中文名，引用时与原名并列
    # 汽车数据跨境业务里这份法规属于哪一类（个人数据与跨境、网络安全、车辆准入与自动驾驶、通信与无线电…）、
    # 核心 / 配套 / 外围；用户的法规目录、国家简报、合规文件的依据都按它组织（app.knowledge.business_topic）
    business_topic: Mapped[str | None] = mapped_column(String(32), nullable=True)
    business_weight: Mapped[str | None] = mapped_column(String(16), nullable=True)
    language: Mapped[str] = mapped_column(String(8))
    document_type: Mapped[str] = mapped_column(String(32))
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 文件所属的法规体系(如 sg-av-rules)与文件性质(ACT/REGULATION/AMENDMENT/GUIDANCE...)
    instrument_family: Mapped[str | None] = mapped_column(String(128), nullable=True)
    instrument_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # S3 采集阶段记录文件级相关性(relevant / irrelevant / needs_review),供 S2 过滤
    relevance: Mapped[str] = mapped_column(
        String(16), default="relevant", server_default="relevant"
    )
    relevance_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 管理员把首次入库、没有公布/施行日期的法规“标记为新法规”：推送给订阅的企业一次（同一部只能标一次）
    news_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    news_marked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # 管理员把首次入库、没有公布/施行日期的法规“标记为新法规”：推送给订阅的企业一次（同一部只能标一次）
    news_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    news_marked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # 相关性判定来源: rule(规则初判) / llm(大模型) / manual(人工覆盖,自动流程不得改写)
    relevance_source: Mapped[str] = mapped_column(String(16), default="rule", server_default="rule")
    # 长法规“按业务相关章节入库”：哪一版的哪些部分在范围内（见 app.ingestion.intake_scope）；空 = 整份
    intake_scope: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))


class LegalStatus(enum.StrEnum):
    UNKNOWN = "unknown"  # 现行状态未经核实(默认,不得当作现行有效)
    IN_FORCE = "in_force"
    AMENDED = "amended"
    REPEALED = "repealed"
    DRAFT = "draft"  # 草案、法案：尚未生效，不得当作现行法引用


NOT_CITABLE_STATUSES = (LegalStatus.REPEALED.value, LegalStatus.DRAFT.value)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_no", name="uq_versions_doc_no"),
        UniqueConstraint(
            "document_id", "content_hash", "parse_revision", name="uq_versions_doc_hash_rev"
        ),
        CheckConstraint(
            "state IN ('downloaded','parsed','needs_ocr','failed')", name="ck_versions_state"
        ),
        CheckConstraint(
            "legal_status IN ('unknown','in_force','amended','repealed','draft')",
            name="ck_versions_legal_status",
        ),
        CheckConstraint("text_source IN ('text_layer','ocr')", name="ck_versions_text_source"),
        CheckConstraint(
            "review_status IN ('pending_review','approved','rejected')",
            name="ck_versions_review_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    version_no: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))  # SHA-256(原文字节)
    # 同一份存档文件被重新解析（例如改用页面图像重新切分）时递增；旧版本与旧条款保留作历史
    parse_revision: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    archive_path: Mapped[str] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mime_type: Mapped[str] = mapped_column(String(128))
    final_url: Mapped[str] = mapped_column(String(2048))
    http_etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    http_last_modified: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    state: Mapped[str] = mapped_column(String(16), default=VersionState.DOWNLOADED.value)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parse_strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 文本来源:PDF 文字层,或 OCR(OCR 文本必须人工核对,准确率不能当作原文)
    text_source: Mapped[str] = mapped_column(
        String(16), default="text_layer", server_default="text_layer"
    )
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ocr_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    check_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # 扫描件识别质量检查的结论（通过/哪一项没过）
    check_items: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # 可能识别错的段落：逐段“没错 / 改一下”
    review_status: Mapped[str] = mapped_column(
        String(24), default="pending_review", server_default="pending_review"
    )

    # 版本元数据:来自官方文本或一期核对,metadata_source 说明出处;未知就留空,不猜
    official_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    version_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 公报摘录页：只把其中这部法令的正文条款化（见 ingestion/excerpt.py）；空表示整份文件
    excerpt: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    published_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    repealed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amended_dates: Mapped[list[date] | None] = mapped_column(ARRAY(Date), nullable=True)
    legal_status: Mapped[str] = mapped_column(
        String(16), default=LegalStatus.UNKNOWN.value, server_default="unknown"
    )
    metadata_source: Mapped[str | None] = mapped_column(String(32), nullable=True)


class PipelineEvent(Base):
    """流水线每一步的结果,用于排错和重跑。"""

    __tablename__ = "pipeline_events"
    __table_args__ = (CheckConstraint("status IN ('ok','error')", name="ck_events_status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    step: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(8))
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Clause(Base):
    """从原文切出的条款。original_text 与原文逐字一致,不做改写。"""

    __tablename__ = "clauses"
    __table_args__ = (
        UniqueConstraint("version_id", "ordinal", name="uq_clauses_version_ordinal"),
        Index("ix_clauses_version", "version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    clause_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    heading: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 所在的 PART/CHAPTER/附表,如 "PART 6A NOTIFICATION OF DATA BREACHES";与条号一起唯一定位一条
    section_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    original_text: Mapped[str] = mapped_column(Text)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_hash: Mapped[str] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(8))
    ocr_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    needs_review: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class ClauseReading(Base):
    """A second, cross-checked reading of a clause whose text layer lost or displaced glyphs.

    The clause keeps its text-layer text untouched (it is the evidence and is hash-checked).
    This reading is only ever used as input to search and translation, never quoted as evidence.
    Only readings that both routes of the page support are stored.
    """

    __tablename__ = "clause_readings"
    __table_args__ = (UniqueConstraint("clause_id", name="uq_clause_readings_clause"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clause_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clauses.id", ondelete="CASCADE")
    )
    reading_text: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(32), default="dual_route")
    confirmed_words: Mapped[int] = mapped_column(Integer, default=0)
    unresolved_spans: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
