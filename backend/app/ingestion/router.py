# ruff: noqa: E501
import base64
import uuid
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi import (
    Path as ApiPath,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.iam.deps import Principal, require_admin_side, require_platform_admin
from app.ingestion.archive import ArchiveIntegrityError, ArchiveStore
from app.ingestion.fetcher import validate_public_http_url
from app.ingestion.models import Clause, Document, DocumentVersion, LegalStatus, PipelineEvent
from app.ingestion.ocr import OcrEngine, OcrUnavailable
from app.ingestion.pipeline import assess_document_relevance, parse_version
from app.ingestion.safety import holds_for_documents
from app.ingestion.source_observations import registered_acquisition
from app.knowledge.models import KnowledgeAuditRecord
from app.sources.models import SourceSite

router = APIRouter(prefix="/api/admin", tags=["documents"])


def clean_admin_process_text(raw: str | None) -> str | None:
    if not raw:
        return raw
    import re as _re

    s = raw.strip()
    if s.startswith("{") and s.endswith("}"):
        return "已完成版面分栏、页码与页眉页脚结构校正"
    if "Failed to load document" in s or "PDFium" in s or "Data format error" in s:
        return "文件结构损坏或并非标准版式文档，无法解析"
    from app.core.jargon import scrub_engineering_jargon

    s = _re.sub(r"(?:Paddle)?OCR\s*(\d+)\s*页", r"版面文字识别共 \1 页", s, flags=_re.I)
    s = _re.sub(r"\.json\b", "数据文件", s, flags=_re.I)
    s = scrub_engineering_jargon(
        s,
        ocr_zh="版面文字识别",
        llm_zh="智能分析模型",
        rag_zh="法条检索增强",
        json_zh="结构化数据",
        uuid_zh="唯一标识",
        pydantic_zh="字段校验",
        cjk_zh="中文汉字",
    )
    s = _re.sub(r"[（(][A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Timeout)[)）]", "", s)
    s = _re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Timeout)\s*[:：]?\s*", "", s)
    return s.strip(" ：:;；,，") or "处理异常，请核查原文或稍后重试"


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    version_no: int
    content_hash: str
    size_bytes: int
    mime_type: str
    fetched_at: datetime
    state: str
    page_count: int | None
    char_count: int | None
    parse_strategy: str | None
    error: str | None
    text_source: str
    ocr_confidence: float | None
    ocr_required: bool = False
    review_status: str = "pending_review"
    check_note: str | None = None
    official_number: str | None
    version_label: str | None
    published_date: date | None
    effective_date: date | None
    repealed_date: date | None
    legal_status: str
    metadata_source: str | None

    @field_validator("error", "check_note", mode="after")
    @classmethod
    def _scrub_error(cls, v: str | None) -> str | None:
        return clean_admin_process_text(v)


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    safety_hold: bool = False
    safety_note: str | None = None
    site_id: uuid.UUID
    country_code: str
    source_url: str
    title: str
    language: str
    document_type: str
    discovered_at: datetime
    last_checked_at: datetime | None
    last_error: str | None
    instrument_family: str | None = None
    instrument_kind: str | None = None
    relevance: str = "relevant"
    relevance_note: str | None = None
    relevance_source: str = "rule"
    news_marked_at: datetime | None = None
    has_date: bool = False  # 最新版有公布或施行日期（没有的首次入库不推送，可由管理员标记为新法规）
    latest_version: VersionOut | None = None

    @field_validator("last_error", "safety_note", "relevance_note", mode="after")
    @classmethod
    def _scrub_last_error(cls, v: str | None) -> str | None:
        return clean_admin_process_text(v)


class DocumentDetail(DocumentOut):
    versions: list[VersionOut]


class RelevanceOverrideIn(BaseModel):
    """管理员/法务人工覆盖文件级相关性(覆盖后自动流程不得改写)。"""

    relevance: Literal["relevant", "related", "out_of_scope"]
    note: str | None = Field(default=None, max_length=500)


class ClauseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ordinal: int
    clause_number: str | None
    heading: str | None
    section_path: str | None
    original_text: str
    page_start: int | None
    page_end: int | None
    language: str
    ocr_required: bool = False
    needs_review: bool = True


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step: str
    status: str
    message: str | None
    created_at: datetime

    @field_validator("message", mode="after")
    @classmethod
    def _scrub_message(cls, v: str | None) -> str | None:
        return clean_admin_process_text(v)


class ManualUploadIn(BaseModel):
    """管理员手工上传官方原文(如源站开启了 WAF 验证码挑战时的合规兜底入口)。"""

    site_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    source_url: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=500)
    mime_type: str = Field(default="application/pdf", max_length=128)
    content_base64: str = Field(min_length=8)
    official_number: str | None = Field(default=None, max_length=128)
    version_label: str | None = Field(default=None, max_length=255)
    published_date: date | None = None
    effective_date: date | None = None


async def _versions_by_doc(
    session: AsyncSession, doc_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[DocumentVersion]]:
    if not doc_ids:
        return {}
    rows = (
        await session.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id.in_(doc_ids))
            .order_by(DocumentVersion.document_id, DocumentVersion.version_no.desc())
        )
    ).scalars()
    grouped: dict[uuid.UUID, list[DocumentVersion]] = {}
    for v in rows:
        grouped.setdefault(v.document_id, []).append(v)
    return grouped


@router.get("/documents", response_model=list[DocumentOut], summary="文档库列表")
async def list_documents(
    country_code: str | None = None,
    site_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=2147483647),
    _: Principal = Depends(require_admin_side),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Document).order_by(Document.discovered_at.desc(), Document.id)
    if country_code:
        stmt = stmt.where(Document.country_code == country_code.upper())
    if site_id:
        from app.sources.router import site_document_filter

        stmt = stmt.where(await site_document_filter(session, site_id))
    docs = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    versions = await _versions_by_doc(session, [d.id for d in docs])
    holds = await holds_for_documents(session, [d.id for d in docs])
    out = []
    for d in docs:
        item = DocumentOut.model_validate(d)
        item.safety_hold = d.id in holds
        item.safety_note = holds.get(d.id, {}).get("reason")
        vs = versions.get(d.id)
        item.latest_version = VersionOut.model_validate(vs[0]) if vs else None
        out.append(item)
    return out


@router.post(
    "/documents/upload",
    response_model=DocumentDetail,
    status_code=status.HTTP_201_CREATED,
    summary="手动上传官方原文",
)
async def upload_document_version(
    body: ManualUploadIn,
    _: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        raw_bytes = base64.b64decode(body.content_base64, validate=True)
    except Exception:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "上传文件内容编码解析失败，请重新选择文件"
        ) from None
    settings = get_settings()
    if not raw_bytes or len(raw_bytes) > settings.crawl_max_bytes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "文件为空或超过体积上限")

    doc: Document | None = None
    if body.document_id is not None:
        doc = (
            await session.execute(
                select(Document).where(Document.id == body.document_id).with_for_update()
            )
        ).scalar_one_or_none()
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")
    else:
        if body.site_id is None or not body.source_url:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "新建文档上传必须选择所属采集站点并填写官方原文链接",
            )
        site = await session.get(SourceSite, body.site_id)
        if site is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "站点不存在")
        try:
            clean_url = validate_public_http_url(body.source_url)
        except ValueError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        doc = (
            await session.execute(
                select(Document)
                .where(Document.site_id == site.id, Document.source_url == clean_url)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if doc is None:
            title_val = (body.title or clean_url).strip()[:500]
            norm_dtype = (site.document_type or "OTHER").strip().upper() or "OTHER"
            rel_val, rel_note = assess_document_relevance(title_val, clean_url, norm_dtype)
            await session.execute(
                pg_insert(Document)
                .values(
                    id=uuid.uuid4(),
                    site_id=site.id,
                    country_code=site.country_code,
                    source_url=clean_url,
                    title=title_val,
                    language=site.language,
                    document_type=norm_dtype,
                    relevance=rel_val,
                    relevance_note=rel_note,
                )
                .on_conflict_do_nothing(index_elements=["site_id", "source_url"])
            )
            await session.flush()
            doc = (
                await session.execute(
                    select(Document)
                    .where(Document.site_id == site.id, Document.source_url == clean_url)
                    .with_for_update()
                )
            ).scalar_one()

    if body.title and body.title.strip():
        doc.title = body.title.strip()[:500]
    if not doc.relevance_note:
        doc.relevance, doc.relevance_note = assess_document_relevance(
            doc.title, doc.source_url, doc.document_type
        )
    doc.last_checked_at = datetime.now(UTC)
    doc.last_error = None

    archive = ArchiveStore(settings.archive_dir)
    digest = ArchiveStore.sha256(raw_bytes)
    existing = (
        await session.execute(
            select(DocumentVersion)
            .where(
                DocumentVersion.document_id == doc.id,
                DocumentVersion.content_hash == digest,
            )
            .order_by(DocumentVersion.version_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is None:
        stored_digest, rel = archive.put(raw_bytes)
        last_no = (
            await session.execute(
                select(DocumentVersion.version_no)
                .where(DocumentVersion.document_id == doc.id)
                .order_by(DocumentVersion.version_no.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        version = DocumentVersion(
            document_id=doc.id,
            version_no=(last_no or 0) + 1,
            content_hash=stored_digest,
            archive_path=rel,
            size_bytes=len(raw_bytes),
            mime_type=body.mime_type,
            final_url=doc.source_url[:2048],
            fetched_at=datetime.now(UTC),
            official_number=body.official_number,
            version_label=body.version_label,
            published_date=body.published_date,
            effective_date=body.effective_date,
            legal_status=LegalStatus.UNKNOWN.value,
            metadata_source="manual_upload",
        )
        session.add(version)
        from sqlalchemy.exc import IntegrityError

        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT, "同一文档正在并发上传新版本，请稍后重试"
            ) from None
        session.add(
            PipelineEvent(
                version_id=version.id,
                step="upload",
                status="ok",
                message="管理员手动上传官方原文，已完成归档校验",
            )
        )
        ocr = OcrEngine(
            settings.tesseract_cmd, settings.tessdata_dir, settings.ocr_dpi, settings.paddle_python
        )
        await parse_version(session, archive, version, doc.language, ocr)
        await session.commit()
    else:
        await session.commit()

    versions = (await _versions_by_doc(session, [doc.id])).get(doc.id, [])
    base = DocumentOut.model_validate(doc)
    version_out = [VersionOut.model_validate(v) for v in versions]
    return DocumentDetail(
        **base.model_dump(exclude={"latest_version"}),
        latest_version=version_out[0] if version_out else None,
        versions=version_out,
    )


@router.get("/quality-cards/references", summary="指向已不存在条文的引用有几处")
async def dangling_references(
    _: Principal = Depends(require_admin_side),
    session: AsyncSession = Depends(get_session),
):
    from app.ingestion.ref_check import scan

    return {"dangling": await scan(session, cached=True)}


@router.post("/quality-cards/references/repair", summary="把指向已不存在条文的引用改指向现存条文")
async def repair_references(
    _: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
):
    from app.ingestion.ref_check import repair, scan

    done = await repair(session)
    await session.commit()
    return {**done, "dangling": await scan(session)}


@router.get("/quality-cards", summary="入库质量：哪些法规可以用、哪些还要处理")
async def quality_cards(
    country_code: str | None = None,
    only_failing: bool = False,
    _: Principal = Depends(require_admin_side),
    session: AsyncSession = Depends(get_session),
):
    from app.ingestion.quality_card import cards_in

    rows = await cards_in(session, country_code)
    out = []
    for row in rows:
        card = row["card"]
        if only_failing and card.passed:
            continue
        out.append(
            {
                "country_code": row["country"],
                "title": row["title"],
                "language": row["language"],
                "clauses": card.clauses,
                "readable": card.readable,
                "number_continuity": card.number_continuity,
                "translated": card.translated,
                "problems": card.problems,
                "scope_note": card.scope_note,
                "document_id": row["document_id"],
                "todo": row["todo"],
                "passed": card.passed and not row["todo"],
            }
        )
    return out


@router.get("/documents/{document_id}", response_model=DocumentDetail, summary="文档详情与全部版本")
async def get_document(
    document_id: uuid.UUID,
    _: Principal = Depends(require_admin_side),
    session: AsyncSession = Depends(get_session),
):
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")
    versions = (await _versions_by_doc(session, [doc.id])).get(doc.id, [])
    base = DocumentOut.model_validate(doc)
    version_out = [VersionOut.model_validate(v) for v in versions]
    return DocumentDetail(
        **base.model_dump(exclude={"latest_version", "has_date"}),
        has_date=bool(versions and (versions[0].effective_date or versions[0].published_date)),
        latest_version=version_out[0] if version_out else None,
        versions=version_out,
    )


class MarkNewIn(BaseModel):
    published_date: date | None = None  # 官方公布日期（选填）：填了就写进这部法规最新版的公布日期


@router.post("/documents/{document_id}/mark-new", summary="标记为新法规：推送给订阅的企业一次")
async def mark_document_new(
    document_id: uuid.UUID,
    body: MarkNewIn,
    request: Request,
    principal: Principal = Depends(require_admin_side),
    session: AsyncSession = Depends(get_session),
):
    """A law first collected without a publication or effective date is not pushed. The administrator
    confirms it is newly issued: it is pushed once to the subscribed enterprises like any newly
    collected law (impact and what to do written for it). One law can be marked only once."""
    from app.audit.service import set_detail
    from app.updates import subscriptions as subs

    doc = await session.get(Document, document_id, with_for_update=True)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")
    if doc.news_marked_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "这部法规已经标记过一次，不会再推送")
    version = await session.scalar(
        select(DocumentVersion).where(DocumentVersion.document_id == doc.id, DocumentVersion.state == "parsed").order_by(DocumentVersion.version_no.desc()).limit(1)
    )
    if version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "这部法规还没有解析完成，解析后才能标记")
    if doc.relevance not in subs.SHOWN_RELEVANCE:
        raise HTTPException(status.HTTP_409_CONFLICT, "这部法规与汽车数据和准入业务无关，不向企业展示，不能推送")
    if body.published_date and body.published_date > datetime.now(UTC).date():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "公布日期不能晚于今天")
    if body.published_date:
        version.published_date = body.published_date
    doc.news_marked_at, doc.news_marked_by = datetime.now(UTC), principal.user_id
    await session.flush()
    await subs.ensure_advice(session, f"doc:{doc.id}", doc)  # 影响说明和要做的事先排上，订阅的企业打开通知时已写好
    set_detail(
        request,
        summary=f"把《{doc.title_zh or doc.title}》标记为新法规" + (f"，公布日期 {body.published_date.isoformat()}" if body.published_date else ""),
        before=None,
        after={"news_marked": True, "published_date": body.published_date.isoformat() if body.published_date else None},
    )
    await session.commit()
    subs.schedule_writing()
    return {"id": str(doc.id), "news_marked_at": doc.news_marked_at}


@router.get("/documents/{document_id}/provenance", summary="只读来源与归档完整性核验")
async def document_provenance(
    document_id: uuid.UUID,
    _: Principal = Depends(require_admin_side),
    session: AsyncSession = Depends(get_session),
) -> dict:
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")
    site = await session.get(SourceSite, doc.site_id)
    archive = ArchiveStore(get_settings().archive_dir)
    rows = []
    versions = (await _versions_by_doc(session, [doc.id])).get(doc.id, [])
    receipts = (
        await session.scalars(
            select(KnowledgeAuditRecord).where(
                KnowledgeAuditRecord.target_type == "documents",
                KnowledgeAuditRecord.target_id == doc.id,
                KnowledgeAuditRecord.action == "official_source_acquired",
            )
        )
    ).all()
    for version in versions:
        error = None
        try:
            raw = archive.read(version.archive_path, expected_sha256=version.content_hash)
            integrity_ok = (
                archive.sha256(raw) == version.content_hash and len(raw) == version.size_bytes
            )
            if not integrity_ok:
                error = "归档原文与入库校验记录不一致"
        except (OSError, ArchiveIntegrityError):
            integrity_ok = False
            error = "归档原文不可读或完整性核验未通过"
        events = (
            await session.scalars(
                select(PipelineEvent)
                .where(PipelineEvent.version_id == version.id)
                .order_by(PipelineEvent.created_at, PipelineEvent.id)
            )
        ).all()
        steps = {event.step for event in events}
        # A URL and a matching hash prove provenance/integrity, not legal authenticity.
        acquisition = (
            "manual_upload"
            if "upload" in steps
            else ("pipeline_fetch" if "download" in steps else "legacy_or_unrecorded")
        )
        registered = [
            receipt
            for audit in receipts
            if integrity_ok and (receipt := registered_acquisition(audit, version, doc)) is not None
        ]
        receipt = registered[0] if len({r["source_nature"] for r in registered}) == 1 else None
        if acquisition == "legacy_or_unrecorded" and receipt is not None:
            acquisition = "registered_download"
        if acquisition == "legacy_or_unrecorded" and version.metadata_source == "manual_import":
            acquisition = "official_import"  # 官方站点下载后经来源锁导入
        if acquisition == "legacy_or_unrecorded" and not receipts and integrity_ok and (version.final_url or "").startswith("http"):
            acquisition = "official_site"  # 没留下载记录，但有官方网址、原文与入库时一致：按官方网站下载算
        rows.append(
            {
                "version_id": str(version.id),
                "version_no": version.version_no,
                "content_hash": version.content_hash,
                "fetched_at": version.fetched_at,
                "final_url": version.final_url,
                "metadata_source": version.metadata_source,
                "acquisition": acquisition,
                "registered_download": receipt,
                "pipeline_refetch_verified": "refetch" in steps,
                "integrity_ok": integrity_ok,
                "integrity_error": error,
                "review_status": version.review_status,
                "legal_status": version.legal_status,
                "effective_date": version.effective_date,
                "ocr_required": version.ocr_required,
                "official_authenticity": "pending_human_review",
                "events": [EventOut.model_validate(event).model_dump() for event in events],
            }
        )
    from app.ingestion.bindings import binding_resolutions

    return {
        "binding_resolutions": await binding_resolutions(session, doc.id),
        "source_integrity_holds": list((await holds_for_documents(session, [doc.id])).values()),
        "document_id": str(doc.id),
        "title": doc.title,
        "country_code": doc.country_code,
        "source_url": doc.source_url,
        "language": doc.language,
        "publisher": site.publisher if site else None,
        "terms_status": site.terms_status if site else None,
        "terms_approval_scope": site.terms_approval_scope if site else None,
        "versions": rows,
    }


@router.patch(
    "/documents/{document_id}/relevance",
    response_model=DocumentOut,
    summary="人工覆盖文件级相关性(核心/相关/范围外)",
)
async def override_document_relevance(
    document_id: uuid.UUID,
    body: RelevanceOverrideIn,
    principal: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
):
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")
    doc.relevance = body.relevance
    doc.relevance_source = "manual"
    doc.relevance_note = (body.note or "").strip() or "管理员人工覆盖相关性判定"
    await session.commit()
    await session.refresh(doc)
    return DocumentOut.model_validate(doc)


async def _version_or_404(session: AsyncSession, version_id: uuid.UUID) -> DocumentVersion:
    v = await session.get(DocumentVersion, version_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "版本不存在")
    return v


@router.get("/versions/{version_id}/original", summary="下载经完整性核验的归档原文")
async def download_original(
    version_id: uuid.UUID,
    inline: bool = False,
    _: Principal = Depends(require_admin_side),
    session: AsyncSession = Depends(get_session),
):
    import re
    from urllib.parse import quote

    from app.analysis.workflow_engine import COUNTRY_NAMES_ZH

    version = await _version_or_404(session, version_id)
    try:
        archive = ArchiveStore(get_settings().archive_dir)
        raw = archive.read(version.archive_path, expected_sha256=version.content_hash)
    except (OSError, ArchiveIntegrityError):
        raise HTTPException(409, "归档原文不存在或完整性异常，已拒绝下载") from None
    if archive.sha256(raw) != version.content_hash or len(raw) != version.size_bytes:
        raise HTTPException(409, "归档原文与入库校验记录不一致，已拒绝下载")
    doc = await session.get(Document, version.document_id)
    cc_zh = COUNTRY_NAMES_ZH.get(doc.country_code if doc else "", "官方")
    raw_title = (doc.title_zh or doc.title) if doc else "法规文件"
    safe_title = re.sub(r'[\\/:*?"<>|\r\n]+', "_", raw_title).strip()[:60] or "法规文件"
    mime_low = (version.mime_type or "").lower()
    arch_low = (version.archive_path or "").lower()
    if mime_low == "application/pdf" or arch_low.endswith(".pdf"):
        extension = ".pdf"
    elif "wordprocessingml" in mime_low or arch_low.endswith(".docx"):
        extension = ".docx"
    elif mime_low == "application/msword" or arch_low.endswith(".doc"):
        extension = ".doc"
    elif "spreadsheetml" in mime_low or arch_low.endswith(".xlsx"):
        extension = ".xlsx"
    elif "html" in mime_low or arch_low.endswith((".html", ".htm")):
        extension = ".html"
    elif mime_low.startswith("text/plain") or arch_low.endswith(".txt"):
        extension = ".txt"
    else:
        extension = ".bin"
    utf8_name = quote(f"{cc_zh}_{safe_title}_官方原文{extension}")
    show = inline and extension == ".pdf"  # 对照原文：在浏览器里直接打开到对应页
    disp_mode = "inline" if show else "attachment"
    media_type = (
        "application/pdf"
        if show
        else (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if extension == ".docx"
            else "application/octet-stream"
        )
    )
    return Response(
        raw,
        media_type=media_type,
        headers={
            "Content-Disposition": f"{disp_mode}; filename=\"official_original{extension}\"; filename*=UTF-8''{utf8_name}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


def _archive_pdf(version) -> bytes:
    try:
        archive = ArchiveStore(get_settings().archive_dir)
        raw = archive.read(version.archive_path, expected_sha256=version.content_hash)
    except (OSError, ArchiveIntegrityError):
        raise HTTPException(409, "原文文件打不开") from None
    if archive.sha256(raw) != version.content_hash:
        raise HTTPException(409, "原文文件与入库时不一致")
    return raw


@router.get("/versions/{version_id}/check-items", summary="扫描件待核对的段落")
async def check_items(
    version_id: uuid.UUID,
    _: Principal = Depends(require_admin_side),
    session: AsyncSession = Depends(get_session),
):
    """可能识别错的每一段：识别出的文字、在第几页、是否已处理、改过的话改成了什么。"""
    from app.ingestion.models import ClauseReading

    version = await _version_or_404(session, version_id)
    items = version.check_items or []
    ids = [uuid.UUID(i["clause_id"]) for i in items]
    clauses = {c.id: c for c in (await session.scalars(select(Clause).where(Clause.id.in_(ids)))).all()} if ids else {}
    readings = dict((await session.execute(select(ClauseReading.clause_id, ClauseReading.reading_text).where(ClauseReading.clause_id.in_(ids)))).all()) if ids else {}
    out = []
    for i in items:
        c = clauses.get(uuid.UUID(i["clause_id"]))
        if c is None:
            continue
        out.append({**i, "clause_number": c.clause_number, "text": readings.get(c.id) or c.original_text})
    return {"note": version.check_note, "review_status": version.review_status, "items": out}


class SettleIn(BaseModel):
    text: str | None = Field(default=None, max_length=50000)


@router.post("/versions/{version_id}/check-items/{clause_id}", summary="核对一段扫描件文字")
async def settle_check_item(
    version_id: uuid.UUID,
    clause_id: uuid.UUID,
    body: SettleIn,
    _: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
):
    """“没错”（不带 text）或“改一下”（带改好的文字）。全部处理完，这份扫描件记为已核对。"""
    from app.ingestion.ocr_check import settle_item

    version = await _version_or_404(session, version_id)
    try:
        await settle_item(session, version, clause_id, body.text)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    await session.commit()
    return {"review_status": version.review_status, "note": version.check_note}


@router.post("/versions/{version_id}/pages/{page}/reread", summary="重新识别扫描件的某一页")
async def reread_page(
    version_id: uuid.UUID,
    page: int = ApiPath(..., ge=-2147483648, le=2147483647),
    _: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
):
    """更高分辨率、按原文语言再读这一页；读通的段落自动处理掉。"""
    import pypdfium2 as pdfium

    from app.ingestion.ocr_check import reread_pages

    version = await _version_or_404(session, version_id)
    if version.mime_type != "application/pdf":
        raise HTTPException(404, "这份原文不是扫描版页面文档")
    if page < 1:
        raise HTTPException(404, "没有这一页")
    try:
        done = await reread_pages(session, version, [page])
    except OcrUnavailable:
        raise HTTPException(
            422, "当前运行环境尚未启用版面文字识别服务，请使用人工核对或联系管理员"
        ) from None
    except (FileNotFoundError, ArchiveIntegrityError, OSError):
        raise HTTPException(422, "归档原文缺失或已损坏，无法读取该页") from None
    except pdfium.PdfiumError:
        raise HTTPException(422, "原文文件结构异常，无法解析页面图像") from None
    await session.commit()
    return {**done, "review_status": version.review_status, "note": version.check_note}


@router.get("/versions/{version_id}/pages/{page}.png", summary="原文某一页的图")
async def page_image(
    version_id: uuid.UUID,
    page: int = ApiPath(..., ge=-2147483648, le=2147483647),
    _: Principal = Depends(require_admin_side),
    session: AsyncSession = Depends(get_session),
):
    import asyncio
    import io

    import pypdfium2 as pdfium

    from app.ingestion.parsing import _PDFIUM_LOCK

    version = await _version_or_404(session, version_id)
    if version.mime_type != "application/pdf":
        raise HTTPException(404, "这份原文不是扫描版页面文档")
    raw_pdf = _archive_pdf(version)

    def _render_png_sync() -> bytes:
        with _PDFIUM_LOCK:
            try:
                pdf = pdfium.PdfDocument(raw_pdf)
            except pdfium.PdfiumError:
                raise HTTPException(422, "原文文件结构异常，无法渲染页面图像") from None
            page_obj = None
            bitmap = None
            try:
                if not 1 <= page <= len(pdf):
                    raise HTTPException(404, "没有这一页")
                page_obj = pdf[page - 1]
                bitmap = page_obj.render(scale=1.6)
                image = bitmap.to_pil()
                buf = io.BytesIO()
                image.save(buf, format="PNG")
                return buf.getvalue()
            except pdfium.PdfiumError:
                raise HTTPException(422, "原文页面渲染失败，请核查归档文件完整性") from None
            finally:
                if bitmap is not None:
                    bitmap.close()
                if page_obj is not None:
                    page_obj.close()
                pdf.close()

    png_bytes = await asyncio.to_thread(_render_png_sync)
    return Response(png_bytes, media_type="image/png", headers={"Cache-Control": "private, max-age=600"})


class VersionCheckIn(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=1000)


@router.post("/versions/{version_id}/check", summary="扫描件原文核对")
async def check_version(
    version_id: uuid.UUID,
    body: VersionCheckIn,
    user: Principal = Depends(require_admin_side),
    session: AsyncSession = Depends(get_session),
):
    """管理员核对扫描件识别出的文字：记为识别无误（或有误）。"""
    from app.knowledge.version_check import apply_version_check

    version = await _version_or_404(session, version_id)
    await apply_version_check(session, version.id, body.approve)
    await session.commit()
    return {"submitted": False, "review_status": version.review_status}


@router.get("/versions/{version_id}/clauses", response_model=list[ClauseOut], summary="版本条款")
async def list_clauses(
    version_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=2147483647),
    _: Principal = Depends(require_admin_side),
    session: AsyncSession = Depends(get_session),
):
    await _version_or_404(session, version_id)
    stmt = (
        select(Clause)
        .where(Clause.version_id == version_id)
        .order_by(Clause.ordinal)
        .limit(limit)
        .offset(offset)
    )
    return (await session.execute(stmt)).scalars().all()


@router.get("/versions/{version_id}/events", response_model=list[EventOut], summary="流水线事件")
async def list_events(
    version_id: uuid.UUID,
    _: Principal = Depends(require_admin_side),
    session: AsyncSession = Depends(get_session),
):
    await _version_or_404(session, version_id)
    stmt = (
        select(PipelineEvent)
        .where(PipelineEvent.version_id == version_id)
        .order_by(PipelineEvent.id)
    )
    return (await session.execute(stmt)).scalars().all()
