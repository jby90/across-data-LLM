"""扫描件 OCR 升级：新识别结果确实更好才替换旧条款，否则原样保留。"""

from sqlalchemy import select

from app.ingestion.archive import ArchiveStore
from app.ingestion.models import Clause, DocumentVersion, PipelineEvent
from app.ingestion.ocr import OcrResult
from app.ingestion.pipeline import ingest_site, upgrade_ocr_version

from .helpers import make_fetcher
from .test_ocr import SCAN_PAGES, make_scanned_pdf
from .test_pipeline import Site, add_site

PROSE = (
    "the controller shall ensure that the processing of any data is in accordance with this act "
    "and that the data is kept for the period required by law and is not used for any other purpose "
)


def _page(*articles: int) -> str:
    return "\n".join(f"Article {n}\n{PROSE * 2}" for n in articles)


class FakeOcr:
    available = True

    def __init__(self, pages: list[str]) -> None:
        self.pages = pages
        self.calls = 0

    def run(self, content: bytes, language: str, max_pages: int | None = None) -> OcrResult:
        self.calls += 1
        return OcrResult(pages=self.pages, mean_confidence=80.0, languages="eng")


async def _ingest(session_factory, tmp_path, ocr) -> ArchiveStore:
    state = Site()
    state.pdf = make_scanned_pdf(SCAN_PAGES)
    site_id = await add_site(
        session_factory, strategy="DIRECT", entry="https://example.org/laws/act.pdf", config={}
    )
    archive = ArchiveStore(tmp_path / "a")
    await ingest_site(
        session_factory, make_fetcher(state.handler), archive, site_id, trigger="manual", ocr=ocr
    )
    return archive


async def test_better_ocr_replaces_old_clauses_and_keeps_a_record(session_factory, tmp_path):
    archive = await _ingest(session_factory, tmp_path, FakeOcr(["", _page(1)]))
    better = FakeOcr([_page(1, 2), _page(3, 4)])
    async with session_factory() as s:
        v = (await s.execute(select(DocumentVersion))).scalar_one()
        res = await upgrade_ocr_version(s, archive, v, "en", better)
        await s.commit()
    assert res["status"] == "upgraded" and better.calls == 1  # 只识别一次
    async with session_factory() as s:
        v = (await s.execute(select(DocumentVersion))).scalar_one()
        numbers = (
            (await s.execute(select(Clause.clause_number).where(Clause.version_id == v.id)))
            .scalars()
            .all()
        )
        assert {"1", "2", "3", "4"} <= set(numbers) and v.state == "parsed"
        ev = (
            await s.execute(select(PipelineEvent).where(PipelineEvent.step == "ocr_upgrade"))
        ).scalar_one()
        assert "旧条款开头存档" in ev.message


async def test_equal_or_worse_ocr_leaves_everything_untouched(session_factory, tmp_path):
    archive = await _ingest(session_factory, tmp_path, FakeOcr([_page(1, 2), _page(3, 4)]))
    async with session_factory() as s:
        v = (await s.execute(select(DocumentVersion))).scalar_one()
        before = (
            (await s.execute(select(Clause.id).where(Clause.version_id == v.id))).scalars().all()
        )
        res = await upgrade_ocr_version(s, archive, v, "en", FakeOcr(["", _page(1)]))
        await s.commit()
        after = (
            (await s.execute(select(Clause.id).where(Clause.version_id == v.id))).scalars().all()
        )
    assert res["status"] == "kept_old" and sorted(before) == sorted(after)
