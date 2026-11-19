"""Import one officially published file that was obtained by hand (e.g. downloaded from the
regulator's own site) through the same archive, parse and quality gates as every other source.

The source site row is created inactive with unconfirmed terms, so nothing here enables
crawling; metadata that the caller does not supply stays empty (never guessed) and the legal
status stays unknown. Repeatable: the same bytes for the same URL are not imported twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingestion.archive import ArchiveStore
from app.ingestion.egov_xml import is_egov_xml
from app.ingestion.models import Document, DocumentVersion, LegalStatus, PipelineEvent
from app.ingestion.ocr import OcrEngine
from app.ingestion.pipeline import assess_document_relevance, parse_version
from app.ingestion.source_lock import require_official
from app.ingestion.word_text import is_doc, is_docx
from app.sources.models import SourceSite, Strategy


@dataclass
class ManualSource:
    country_code: str
    file: Path
    source_url: str
    title: str
    language: str
    document_type: str
    publisher: str
    official_number: str | None = None
    published_date: date | None = None
    note: str = "官方站点人工下载后导入"
    confirmed_official_by: str | None = None  # 仅非名单内站点需要
    # 调用方明确知道文件性质时指定，不靠标题关键词猜（例如官方申报表格是准入申报材料，须保留）
    instrument_kind: str | None = None
    relevance: str | None = None  # relevant / related，不接受 out_of_scope 以外的随意值
    # 现行状态：不知道就不填（默认未核实）；草案、法案必须填 draft，不得当作现行法引用
    legal_status: str | None = None
    # 公报摘录页：{"number": 官方编号, "start": 起始正则, "end": 结束正则}，只取其中这部法令
    excerpt: dict | None = None


@dataclass
class ManualImportResult:
    document_id: str
    version_id: str | None
    already_present: bool
    state: str | None


def _mime_of(content: bytes, name: str) -> str:
    """The type follows the bytes and the file name, not an assumption that a file is a PDF."""
    head = content[:1024].lstrip()
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head[:200].lower().startswith((b"<!doctype html", b"<html")) or name.lower().endswith(
        (".htm", ".html")
    ):
        return "text/html"
    if is_egov_xml(content):
        return "application/xml"
    if is_docx(content):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if is_doc(content):
        return "application/msword"
    return "text/plain" if name.lower().endswith(".txt") else "application/pdf"


async def import_manual_file(
    source: ManualSource,
    sf: async_sessionmaker[AsyncSession],
    archive: ArchiveStore,
    ocr: OcrEngine | None = None,
) -> ManualImportResult:
    country = source.country_code.upper()
    basis = require_official(source.source_url, source.confirmed_official_by)  # 来源锁
    content = source.file.read_bytes()
    digest = ArchiveStore.sha256(content)
    async with sf() as s:
        site = (
            await s.execute(
                select(SourceSite).where(
                    SourceSite.country_code == country,
                    SourceSite.entry_url == source.source_url,
                )
            )
        ).scalar_one_or_none()
        if site is None:
            site = SourceSite(
                country_code=country,
                publisher=source.publisher,
                language=source.language,
                document_type=source.document_type,
                entry_url=source.source_url,
                strategy=Strategy.DIRECT.value,
                config={},
                terms_note=source.note,
            )
            s.add(site)
            await s.flush()
        doc = (
            await s.execute(
                select(Document).where(
                    Document.site_id == site.id, Document.source_url == source.source_url
                )
            )
        ).scalar_one_or_none()
        if doc is None:
            relevance, relevance_note = assess_document_relevance(
                source.title, source.source_url, source.document_type
            )
            if source.relevance:
                if source.relevance not in ("relevant", "related"):
                    raise ValueError("导入时只能指定 relevant 或 related")
                relevance, relevance_note = source.relevance, "导入时由操作者明确指定"
            doc = Document(
                site_id=site.id,
                country_code=country,
                source_url=source.source_url,
                title=source.title[:500],
                language=source.language,
                document_type=source.document_type,
                instrument_kind=source.instrument_kind,
                relevance=relevance,
                relevance_note=relevance_note,
            )
            s.add(doc)
            await s.flush()
        existing = (
            await s.execute(
                select(DocumentVersion)
                .where(
                    DocumentVersion.document_id == doc.id, DocumentVersion.content_hash == digest
                )
                .order_by(DocumentVersion.version_no.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            # 上次中断或失败留下的版本（识别到一半被停掉、识别引擎当时不可用）要重新解析，
            # 否则它会永远停在失败状态；已解析成功的原样返回，不重复写入
            if existing.state in ("failed", "needs_ocr", "downloaded"):
                s.add(
                    PipelineEvent(
                        version_id=existing.id,
                        step="import",
                        status="ok",
                        message="上次导入未完成，重新解析",
                    )
                )
                await parse_version(s, archive, existing, source.language, ocr)
                await s.commit()
                return ManualImportResult(str(doc.id), str(existing.id), False, existing.state)
            await s.commit()
            return ManualImportResult(str(doc.id), str(existing.id), True, existing.state)
        legal_status = source.legal_status or LegalStatus.UNKNOWN.value
        if legal_status not in {s.value for s in LegalStatus}:
            raise ValueError(f"现行状态不合法：{legal_status}")
        stored_digest, rel = archive.put(content)
        last_no = (
            await s.execute(
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
            size_bytes=len(content),
            mime_type=_mime_of(content, source.file.name),
            final_url=source.source_url[:2048],
            fetched_at=datetime.now(UTC),
            official_number=source.official_number,
            excerpt=source.excerpt,
            published_date=source.published_date,
            legal_status=legal_status,
            metadata_source="manual_import",
        )
        s.add(version)
        await s.flush()
        s.add(
            PipelineEvent(
                version_id=version.id,
                step="import",
                status="ok",
                message=f"{basis}，人工导入,sha256={digest[:12]}",
            )
        )
        await parse_version(s, archive, version, source.language, ocr)
        await s.commit()
        return ManualImportResult(str(doc.id), str(version.id), False, version.state)
