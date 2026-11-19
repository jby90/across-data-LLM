"""入库把关：文书性质（非法律资料不进法律库）与“同一文件挂多个标题”检查。"""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.rls import system_scope
from app.ingestion import pipeline
from app.ingestion.legal_nature import NatureVerdict
from app.ingestion.models import Clause, Document, DocumentVersion, PipelineEvent
from app.sources.models import SourceSite


async def make_doc(s, site, title, content_hash, *, relevance="relevant", source="llm"):
    doc = Document(
        site_id=site.id,
        country_code="SG",
        source_url=f"https://example.org/{title[:8]}-{content_hash[:6]}",
        title=title,
        language="en",
        document_type="LAW",
        relevance=relevance,
        relevance_source=source,
    )
    s.add(doc)
    await s.flush()
    ver = DocumentVersion(
        document_id=doc.id,
        version_no=1,
        content_hash=content_hash,
        archive_path="aa/" + content_hash,
        size_bytes=10,
        mime_type="application/pdf",
        final_url=doc.source_url,
        state="parsed",
        page_count=3,
    )
    s.add(ver)
    await s.flush()
    s.add(
        Clause(
            version_id=ver.id,
            ordinal=1,
            original_text="Application form. Please fill in the fields below.",
            text_hash="h",
            language="en",
        )
    )
    await s.flush()
    return doc, ver


async def site(s):
    row = SourceSite(
        country_code="SG",
        publisher="测试机构",
        language="en",
        document_type="LAW",
        entry_url="https://example.org/index",
        strategy="INDEX_PAGE",
        config={"extensions": [".pdf"]},
        terms_status="approved",
        enabled=True,
    )
    s.add(row)
    await s.flush()
    return row


def stub(monkeypatch, verdict):
    async def fake(**kwargs):
        return verdict

    monkeypatch.setattr(pipeline, "classify_legal_nature", fake)


async def events(s, version, step):
    return (
        (
            await s.execute(
                select(PipelineEvent).where(
                    PipelineEvent.version_id == version.id, PipelineEvent.step == step
                )
            )
        )
        .scalars()
        .all()
    )


@pytest.mark.asyncio
async def test_non_legal_document_is_kept_out_of_the_legal_library(session_factory, monkeypatch):
    stub(monkeypatch, NatureVerdict("non_legal", "high", "这是办事申请表格"))
    with system_scope("test"):
        async with session_factory() as s:
            doc, ver = await make_doc(s, await site(s), "AV application form", "a1" * 32)
            await pipeline.screen_document(s, doc, ver)
            assert doc.relevance == "out_of_scope" and "非法律资料" in doc.relevance_note
            assert len(await events(s, ver, "legal_nature")) == 1


@pytest.mark.asyncio
async def test_admission_material_is_kept_not_excluded(session_factory, monkeypatch):
    """自动驾驶道路试验的申请须知与表格不是法律，但车企做准入合规要用：保留，不能当非法律资料排除。"""
    stub(monkeypatch, NatureVerdict("admission_material", "high", "官方发布的试验申请材料"))
    with system_scope("test"):
        async with session_factory() as s:
            doc, ver = await make_doc(s, await site(s), "Application for Trial of AVs", "ad" * 32)
            await pipeline.screen_document(s, doc, ver)
            assert doc.relevance == "relevant"
            found = await events(s, ver, "legal_nature")
            assert len(found) == 1 and "准入申报材料" in found[0].message


@pytest.mark.asyncio
async def test_legal_document_is_left_alone(session_factory, monkeypatch):
    stub(monkeypatch, NatureVerdict("legislation", "high", "立法文本"))
    with system_scope("test"):
        async with session_factory() as s:
            doc, ver = await make_doc(s, await site(s), "Personal Data Protection Act", "b2" * 32)
            await pipeline.screen_document(s, doc, ver)
            assert doc.relevance == "relevant"


@pytest.mark.asyncio
async def test_low_confidence_goes_to_review_instead_of_being_guessed(session_factory, monkeypatch):
    stub(monkeypatch, NatureVerdict("non_legal", "low", "拿不准"))
    with system_scope("test"):
        async with session_factory() as s:
            doc, ver = await make_doc(s, await site(s), "Some notice", "c3" * 32)
            await pipeline.screen_document(s, doc, ver)
            assert doc.relevance == "needs_review"


@pytest.mark.asyncio
async def test_manual_decision_is_never_overwritten(session_factory, monkeypatch):
    stub(monkeypatch, NatureVerdict("non_legal", "high", "这是表格"))
    with system_scope("test"):
        async with session_factory() as s:
            doc, ver = await make_doc(s, await site(s), "Manual", "d4" * 32, source="manual")
            await pipeline.screen_document(s, doc, ver)
            assert doc.relevance == "relevant"


@pytest.mark.asyncio
async def test_unavailable_model_changes_nothing(session_factory, monkeypatch):
    stub(monkeypatch, None)
    with system_scope("test"):
        async with session_factory() as s:
            doc, ver = await make_doc(s, await site(s), "Unavailable", "e5" * 32)
            await pipeline.screen_document(s, doc, ver)
            assert doc.relevance == "relevant" and not await events(s, ver, "legal_nature")


@pytest.mark.asyncio
async def test_same_file_under_two_titles_is_reported(session_factory, monkeypatch):
    stub(monkeypatch, None)
    same = "f6" * 32
    with system_scope("test"):
        async with session_factory() as s:
            st = await site(s)
            await make_doc(s, st, "Personal Data Protection Act 2012", same)
            doc2, ver2 = await make_doc(s, st, "Application for Trial of Autonomous Vehicles", same)
            # 同一文档的唯一约束是 (document_id, content_hash)，不同文档可以有相同哈希，所以能出现挂错
            await pipeline.screen_document(s, doc2, ver2)
            found = await events(s, ver2, "duplicate_file")
            assert len(found) == 1 and "Personal Data Protection Act" in found[0].message


@pytest.mark.asyncio
async def test_screening_can_be_switched_off(session_factory, monkeypatch):
    from app.ingestion import legal_nature

    monkeypatch.setattr(get_settings(), "legal_nature_screening_enabled", False)
    verdict = await legal_nature.classify_legal_nature(
        title="x", source_url="u", document_type="LAW", country_code="SG", sample_text="t"
    )
    assert verdict is None


def test_admission_material_is_a_known_nature_and_is_kept():
    from app.ingestion.legal_nature import KEEP_NATURES, LEGAL_NATURES, NATURES

    assert "admission_material" in NATURES
    assert "admission_material" in KEEP_NATURES and "admission_material" not in LEGAL_NATURES
    assert "non_legal" not in KEEP_NATURES
