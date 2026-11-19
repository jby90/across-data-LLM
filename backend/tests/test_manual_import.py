"""手工导入官方文件：只收名单内发布机构的站点，同一文件不重复入库，性质由操作者明确指定。"""

from datetime import date

import pytest
from sqlalchemy import select

from app.ingestion.archive import ArchiveStore
from app.ingestion.manual_import import ManualSource, import_manual_file
from app.ingestion.models import Document, DocumentVersion, PipelineEvent
from app.ingestion.source_lock import SourceNotOfficial

from .helpers import make_pdf

PAGES = [
    [
        "Article 1 Scope",
        "This Act applies to every organisation that collects personal data in the country.",
        "Article 2 Transfer",
        "An organisation must not transfer personal data outside the country without consent.",
    ]
]
OFFICIAL = "https://www.cndp.ma/wp-content/uploads/2025/09/CNDP-Transfert-Etranger-F118.pdf"


def _source(tmp_path, url=OFFICIAL, **kwargs) -> ManualSource:
    file = tmp_path / "x.pdf"
    file.write_bytes(make_pdf(PAGES))
    return ManualSource(
        country_code="ma",
        file=file,
        source_url=url,
        title=kwargs.pop("title", "Formulaire de transfert de données à l'étranger"),
        language="en",
        document_type="GUIDANCE",
        publisher="CNDP",
        published_date=date(2025, 9, 1),
        **kwargs,
    )


async def test_official_file_is_imported_once_and_basis_is_recorded(session_factory, tmp_path):
    archive = ArchiveStore(tmp_path / "a")
    first = await import_manual_file(_source(tmp_path), session_factory, archive)
    again = await import_manual_file(_source(tmp_path), session_factory, archive)
    assert first.state == "parsed" and not first.already_present
    assert again.already_present and again.version_id == first.version_id
    async with session_factory() as s:
        assert len((await s.execute(select(DocumentVersion))).scalars().all()) == 1
        event = (
            await s.execute(select(PipelineEvent).where(PipelineEvent.step == "import"))
        ).scalar_one()
        assert "摩洛哥国家个人数据保护监督委员会" in event.message  # 记录依据：哪个发布机构


async def test_a_site_outside_the_publisher_list_is_refused_and_nothing_is_stored(
    session_factory, tmp_path
):
    archive = ArchiveStore(tmp_path / "a")
    with pytest.raises(SourceNotOfficial):
        await import_manual_file(
            _source(tmp_path, url="https://nhrclb.org/archives/8228"), session_factory, archive
        )
    async with session_factory() as s:
        assert (await s.execute(select(Document))).first() is None


async def test_title_keywords_do_not_decide_scope_when_the_operator_states_it(
    session_factory, tmp_path
):
    # “formulaire” 会让标题规则把它当成办事表格；官方申报表格应当保留并归为申报材料
    archive = ArchiveStore(tmp_path / "a")
    await import_manual_file(
        _source(tmp_path, instrument_kind="ADMISSION_GUIDANCE", relevance="related"),
        session_factory,
        archive,
    )
    async with session_factory() as s:
        doc = (await s.execute(select(Document))).scalar_one()
        assert doc.relevance == "related" and doc.instrument_kind == "ADMISSION_GUIDANCE"


async def test_without_an_explicit_choice_the_title_rule_still_applies(session_factory, tmp_path):
    archive = ArchiveStore(tmp_path / "a")
    await import_manual_file(_source(tmp_path), session_factory, archive)
    async with session_factory() as s:
        assert (await s.execute(select(Document))).scalar_one().relevance == "irrelevant"


async def test_only_in_scope_values_can_be_stated(session_factory, tmp_path):
    with pytest.raises(ValueError):
        await import_manual_file(
            _source(tmp_path, relevance="out_of_scope"),
            session_factory,
            ArchiveStore(tmp_path / "a"),
        )


async def test_legal_status_is_stored_and_unknown_values_are_refused(session_factory, tmp_path):
    from app.ingestion.models import DocumentVersion

    await import_manual_file(
        _source(tmp_path, legal_status="draft"), session_factory, ArchiveStore(tmp_path / "a")
    )
    async with session_factory() as s:
        assert (await s.execute(select(DocumentVersion))).scalar_one().legal_status == "draft"
    with pytest.raises(ValueError):
        await import_manual_file(
            _source(tmp_path, url="https://www.cndp.ma/other.pdf", legal_status="maybe"),
            session_factory,
            ArchiveStore(tmp_path / "b"),
        )


async def test_a_version_left_failed_by_an_interrupted_import_is_parsed_again(
    session_factory, tmp_path
):
    from sqlalchemy import delete

    from app.ingestion.models import Clause, DocumentVersion

    archive = ArchiveStore(tmp_path / "a")
    first = await import_manual_file(_source(tmp_path), session_factory, archive)
    async with session_factory() as s:  # 模拟识别到一半被中断：版本停在失败状态
        v = await s.get(DocumentVersion, first.version_id)
        v.state = "failed"
        v.error = "OcrUnavailable: 未找到可用的 OCR 引擎"
        await s.execute(delete(Clause).where(Clause.version_id == v.id))  # 中断时还没有条款
        await s.commit()
    again = await import_manual_file(_source(tmp_path), session_factory, archive)
    assert again.version_id == first.version_id and not again.already_present
    assert again.state == "parsed"


def test_the_stored_type_follows_the_file_not_an_assumption_of_pdf():
    from app.ingestion.manual_import import _mime_of

    assert _mime_of(b"%PDF-1.7 ...", "a.pdf") == "application/pdf"
    assert _mime_of(b"<!DOCTYPE html><html>", "a.bin") == "text/html"
    assert _mime_of("第一条 为了规范".encode(), "law.txt") == "text/plain"
    assert _mime_of(b"unknown", "mystery") == "application/pdf"  # 原有行为不变
