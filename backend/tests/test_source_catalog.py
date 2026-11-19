"""国家信源清单：数据驱动的批量入库。清单格式出错、文件被替换、来源不在名单内，都不能入库。"""

import hashlib
import json

import pytest
from sqlalchemy import select

from app.ingestion.archive import ArchiveStore
from app.ingestion.models import Document
from app.ingestion.source_catalog import (
    CATEGORIES,
    CatalogError,
    import_catalog,
    load_catalog,
    summarize,
)

from .helpers import make_pdf

PAGES = [["Article 1 Scope", "This Act applies to every organisation that handles personal data."]]
OFFICIAL = "https://assets.egazette.gov.sg/2024/Acts%20Supplement/19.pdf"


def _write(tmp_path, items, country="SG"):
    pdf = make_pdf(PAGES)
    (tmp_path / "a.pdf").write_bytes(pdf)
    sha = hashlib.sha256(pdf).hexdigest()
    entries = []
    for e in items:
        base = {
            "category": "法律",
            "title": "Cybersecurity (Amendment) Act 2024",
            "status": "ingest",
            "language": "en",
            "document_type": "LAW",
            "publisher": "新加坡政府电子公报",
            "url": OFFICIAL,
            "file": "a.pdf",
            "sha256": sha,
        }
        entries.append({**base, **e})
    path = tmp_path / f"catalog_{country}.json"
    path.write_text(json.dumps({"country": country, "items": entries}, ensure_ascii=False), "utf-8")
    return path


def test_every_category_of_the_requirement_is_a_valid_choice():
    assert len(CATEGORIES) == 10 and "汽车数据专项" in CATEGORIES and "智驾标准" in CATEGORIES


@pytest.mark.parametrize(
    "bad",
    [
        {"category": "随便写"},
        {"status": "done"},
        {"sha256": "abc123"},  # 必须是完整 64 位
        {"url": "https://www.cliffordchance.com/x.pdf"},  # 不在发布机构名单
        {"url": ""},
        {"document_type": "BLOG"},
        {"published_date": "2024/05/01"},
    ],
)
def test_a_malformed_entry_is_rejected_when_the_catalogue_loads(tmp_path, bad):
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, [bad]))


def test_entries_that_are_not_imported_must_say_why(tmp_path):
    for status in ("hold", "manual", "unavailable", "listed"):
        with pytest.raises(CatalogError):
            load_catalog(
                _write(tmp_path, [{"status": status, "url": "", "file": "", "sha256": ""}])
            )
        ok = load_catalog(
            _write(
                tmp_path,
                [{"status": status, "url": "", "file": "", "sha256": "", "note": "已尝试多个站点"}],
            )
        )
        assert ok.items[0].status == status


def test_unknown_fields_are_not_silently_ignored(tmp_path):
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, [{"remark": "拼错的字段"}]))


def test_summary_shows_every_category_so_gaps_are_visible(tmp_path):
    catalog = load_catalog(
        _write(
            tmp_path,
            [
                {},
                {
                    "category": "智驾标准",
                    "status": "hold",
                    "note": "付费标准节选",
                    "url": "",
                    "file": "",
                    "sha256": "",
                },
            ],
        )
    )
    table = summarize(catalog)
    assert set(table) == set(CATEGORIES)
    assert table["法律"]["ingest"] == 1 and table["智驾标准"]["hold"] == 1
    assert sum(table["处罚"].values()) == 0  # 没有条目的类别也列出来，缺口一眼可见


async def test_only_ingest_entries_are_imported_and_a_second_run_changes_nothing(
    session_factory, tmp_path
):
    path = _write(
        tmp_path,
        [
            {},
            {
                "title": "付费标准节选",
                "status": "hold",
                "note": "需用户决定",
                "url": "",
                "file": "",
                "sha256": "",
            },
        ],
    )
    archive = ArchiveStore(tmp_path / "archive")
    first = await import_catalog(load_catalog(path), session_factory, archive)
    second = await import_catalog(load_catalog(path), session_factory, archive)
    assert [r["result"] for r in first] == ["parsed"]
    assert [r["result"] for r in second] == ["already_present"]
    async with session_factory() as s:
        docs = (await s.execute(select(Document))).scalars().all()
        assert len(docs) == 1 and docs[0].country_code == "SG"


async def test_a_file_that_no_longer_matches_its_registered_hash_is_not_imported(
    session_factory, tmp_path
):
    path = _write(tmp_path, [{}])
    (tmp_path / "a.pdf").write_bytes(make_pdf([["Article 9", "A different text altogether."]]))
    with pytest.raises(CatalogError):
        await import_catalog(load_catalog(path), session_factory, ArchiveStore(tmp_path / "ar"))
    async with session_factory() as s:
        assert (await s.execute(select(Document))).first() is None


async def test_dry_run_checks_files_but_writes_nothing(session_factory, tmp_path):
    path = _write(tmp_path, [{}])
    out = await import_catalog(
        load_catalog(path), session_factory, ArchiveStore(tmp_path / "ar"), dry_run=True
    )
    assert out[0]["result"] == "dry_run"
    async with session_factory() as s:
        assert (await s.execute(select(Document))).first() is None


def test_an_already_ingested_entry_needs_no_file_or_hash(tmp_path):
    catalog = load_catalog(
        _write(
            tmp_path,
            [{"status": "ingested", "url": "", "file": "", "sha256": "", "note": ""}],
        )
    )
    assert catalog.items[0].status == "ingested"


def test_legal_status_must_be_a_known_value_and_draft_is_one(tmp_path):
    assert (
        load_catalog(_write(tmp_path, [{"legal_status": "draft"}])).items[0].legal_status == "draft"
    )
    with pytest.raises(CatalogError):
        load_catalog(_write(tmp_path, [{"legal_status": "pending"}]))


async def test_a_draft_is_imported_with_its_status_and_never_as_law_in_force(
    session_factory, tmp_path
):
    from app.ingestion.models import DocumentVersion

    path = _write(tmp_path, [{"title": "Land Transport Bill", "legal_status": "draft"}])
    await import_catalog(load_catalog(path), session_factory, ArchiveStore(tmp_path / "ar"))
    async with session_factory() as s:
        version = (await s.execute(select(DocumentVersion))).scalar_one()
        assert version.legal_status == "draft"


async def test_concurrent_import_keeps_going_when_one_file_fails(session_factory, tmp_path):
    good = make_pdf(PAGES)
    (tmp_path / "a.pdf").write_bytes(good)
    (tmp_path / "b.pdf").write_bytes(b"this is not a pdf")
    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    base = {
        "category": "法律",
        "status": "ingest",
        "language": "en",
        "document_type": "LAW",
        "publisher": "新加坡政府电子公报",
    }
    items = [
        {**base, "title": "Good Act", "url": OFFICIAL, "file": "a.pdf", "sha256": sha(good)},
        {
            **base,
            "title": "Broken",
            "url": OFFICIAL.replace("19.pdf", "20.pdf"),
            "file": "b.pdf",
            "sha256": sha(b"this is not a pdf"),
        },
    ]
    path = tmp_path / "catalog_SG.json"
    path.write_text(json.dumps({"country": "SG", "items": items}, ensure_ascii=False), "utf-8")
    out = await import_catalog(
        load_catalog(path), session_factory, ArchiveStore(tmp_path / "ar"), concurrency=2
    )
    by_title = {r["title"]: r for r in out}
    assert by_title["Good Act"]["result"] == "parsed"
    assert by_title["Broken"]["result"] in ("failed", "error")  # 记录下来，不拖累其他文件


def _with_pages(tmp_path, pages, country="SG"):
    path = _write(tmp_path, [{}], country)
    raw = json.loads(path.read_text("utf-8"))
    raw["entry_pages"] = pages
    path.write_text(json.dumps(raw, ensure_ascii=False), "utf-8")
    return load_catalog(path)


async def test_entry_pages_become_disabled_monitoring_sites_and_a_rerun_adds_nothing(
    session_factory, tmp_path
):
    from app.ingestion.source_catalog import MONITOR_INTERVAL_HOURS, register_entry_pages
    from app.sources.models import SourceSite

    catalog = _with_pages(
        tmp_path,
        [
            {
                "publisher": "新加坡政府电子公报",
                "url": "https://www.egazette.gov.sg/",
                "note": "目录",
            },
            {"publisher": "某论坛", "url": "https://random-forum.example.com/laws"},
        ],
    )
    first = await register_entry_pages(catalog, session_factory)
    assert [r["result"] for r in first] == ["registered", "rejected"]  # 非官方站点不登记
    again = await register_entry_pages(catalog, session_factory)
    assert [r["result"] for r in again] == ["already_present", "rejected"]
    async with session_factory() as s:
        site = (await s.execute(select(SourceSite))).scalar_one()
    assert site.enabled is False and site.terms_status == "pending"  # 由平台管理员审核后启用
    assert site.interval_hours == MONITOR_INTERVAL_HOURS == 72
    assert site.strategy == "INDEX_PAGE" and site.language == "en"


async def test_entry_page_dry_run_writes_nothing(session_factory, tmp_path):
    from app.ingestion.source_catalog import register_entry_pages
    from app.sources.models import SourceSite

    catalog = _with_pages(
        tmp_path, [{"publisher": "新加坡政府电子公报", "url": "https://www.egazette.gov.sg/"}]
    )
    rows = await register_entry_pages(catalog, session_factory, dry_run=True)
    assert rows[0]["result"] == "dry_run"
    async with session_factory() as s:
        assert (await s.execute(select(SourceSite))).first() is None


async def test_the_catalogues_chinese_title_is_recorded_and_a_differently_written_title_still_matches(session_factory, tmp_path):
    from app.ingestion.source_catalog import apply_title_zh

    path = _write(tmp_path, [{"title_zh": "网络安全（修订）法 2024"}])
    await import_catalog(load_catalog(path), session_factory, ArchiveStore(tmp_path / "archive"))
    async with session_factory() as s:
        doc = (await s.execute(select(Document))).scalars().one()
        assert doc.title_zh == "网络安全（修订）法 2024"
        doc.title_zh, doc.title, doc.source_url = None, "Cybersecurity (Amendment) Act 2024 (consolidated text)", OFFICIAL + "?v=2"
        await s.commit()
    # 地址变了、标题多了括注：名称主体相同仍然对得上；已有中文名的不覆盖
    assert await apply_title_zh(load_catalog(path), session_factory) == 1
    assert await apply_title_zh(load_catalog(path), session_factory) == 0
    # 英文标题里 " - " 后面是附属条例，不是同一部法：不能套用母法的中文名
    other = _write(tmp_path, [{"title": "Cybersecurity Act 2018", "title_zh": "网络安全法 2018", "url": OFFICIAL + "?v=3"}])
    async with session_factory() as s:
        doc = (await s.execute(select(Document))).scalars().one()
        doc.title_zh, doc.title = None, "Cybersecurity Act 2018 - Cybersecurity (Appeals) Regulations 2025"
        await s.commit()
    assert await apply_title_zh(load_catalog(other), session_factory) == 0
