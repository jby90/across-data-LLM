"""Append verified canonical-source pointers; never rebind old bytes, clauses or approvals."""

import re
from urllib.parse import urlsplit

from sqlalchemy import select

from app.ingestion.models import Document, DocumentVersion
from app.ingestion.parsing import parse_document
from app.ingestion.safety import document_is_held
from app.knowledge.history import payload_hash
from app.knowledge.models import KnowledgeAuditRecord


def normalized_identity(text):
    return re.sub(r"\s+", " ", text).strip().casefold()


async def record_canonical_binding(
    session,
    archive,
    held_document_id,
    replacement_version_id,
    *,
    expected_identity,
    expected_edition,
    acquisition_note,
):
    """This is a machine identity check, not legal approval or a claim of current validity."""
    held = await session.get(Document, held_document_id)
    version = await session.get(DocumentVersion, replacement_version_id)
    if not held or not version or not await document_is_held(session, held.id):
        raise ValueError("必须指定已隔离的待修复文档与有效的替换归档版本")
    replacement = await session.get(Document, version.document_id)
    if replacement.id == held.id or replacement.country_code != held.country_code:
        raise ValueError("替换文档必须为同一法域下的独立法规记录")
    official_hosts = {"assets.egazette.gov.sg", "sso.agc.gov.sg"}
    source = urlsplit(replacement.source_url)
    final = urlsplit(version.final_url)
    if held.country_code != "SG" or any(
        url.scheme != "https" or url.hostname not in official_hosts for url in (source, final)
    ):
        raise ValueError("替换文档必须来自已登记的新加坡官方权威发布源")
    if await document_is_held(session, replacement.id):
        raise ValueError("所选替换文档本身处于安全隔离状态，不可作为权威源")
    raw = archive.read(version.archive_path, expected_sha256=version.content_hash)
    if archive.sha256(raw) != version.content_hash or len(raw) != version.size_bytes:
        raise ValueError("替换归档文件哈希或字节大小校验不一致")
    parsed = parse_document(raw, version.mime_type)
    cover = parsed.pages[0] if parsed.pages else ""
    if not expected_identity or not expected_edition:
        raise ValueError("必须明确填写预期的法规标识与版本信息")
    if any(
        normalized_identity(value) not in normalized_identity(cover)
        for value in (expected_identity, expected_edition)
    ):
        raise ValueError("替换文本封面未能证明所要求的法规名称与版本")
    payload = dict(
        held_document_id=str(held.id),
        canonical_document_id=str(replacement.id),
        canonical_version_id=str(version.id),
        official_source_url=replacement.source_url,
        final_url=version.final_url,
        content_hash=version.content_hash,
        size_bytes=len(raw),
        pages=len(parsed.pages),
        cover_evidence=cover,
        expected_identity=expected_identity,
        expected_edition=expected_edition,
        acquisition_note=acquisition_note,
        review_status="pending_review",
        old_binding_remains_held=True,
        legal_status=version.legal_status,
        human_approval_inherited=False,
    )
    digest = payload_hash(payload)
    existing = await session.scalar(
        select(KnowledgeAuditRecord).where(
            KnowledgeAuditRecord.target_id == held.id,
            KnowledgeAuditRecord.action == "source_binding_resolution",
            KnowledgeAuditRecord.content_hash == digest,
        )
    )
    if existing:
        return existing
    record = KnowledgeAuditRecord(
        target_type="documents",
        target_id=held.id,
        action="source_binding_resolution",
        payload=payload,
        content_hash=digest,
    )
    session.add(record)
    await session.flush()
    return record


async def binding_resolutions(session, document_id):
    return [
        row.payload
        for row in (
            await session.scalars(
                select(KnowledgeAuditRecord)
                .where(
                    KnowledgeAuditRecord.target_type == "documents",
                    KnowledgeAuditRecord.target_id == document_id,
                    KnowledgeAuditRecord.action == "source_binding_resolution",
                )
                .order_by(KnowledgeAuditRecord.created_at.desc(), KnowledgeAuditRecord.id)
            )
        ).all()
    ]
