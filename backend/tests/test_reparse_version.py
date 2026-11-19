"""重新切分：同一份存档文件可以有多个解析版本；旧版本与旧条款保留，新分析读最新版本。"""

import pytest
from sqlalchemy import func, select

from app.cli import reparse_version
from app.ingestion.models import Clause, DocumentVersion
from app.ingestion.reparse import reparse_from_pages

from .review_s1_s2_contracts import make_document

BODY = "هذا نص المادة الذي يشرح الالتزام ويحدد المطلوب من المسؤول عن المعالجة بوضوح تام"


def _pages(count=8):
    lines = []
    for n in range(1, count + 1):
        lines += [f"المادة {n}", f"{BODY} رقم {n}"]
    return ["\n".join(lines)]


async def _setup(session_factory):
    async with session_factory() as session:
        doc, version, clauses = await make_document(session, ["نص قديم للمادة الأولى"])
        doc.language = "ar"
        version.state = "parsed"
        await session.commit()
        return str(version.id), str(doc.id)


async def test_a_reparse_replaces_the_reading_and_the_law_keeps_one_version(session_factory):
    """同一份存档文件再读一遍不是新版本：新读法替换旧读法，旧条文并入后删掉，这部法规仍只有一个版本。"""
    version_id, doc_id = await _setup(session_factory)
    async with session_factory() as session:
        res = await reparse_from_pages(session, version_id, _pages(), "ar", note="test")
        await session.commit()
    assert res["version_no"] == 2 and res["clauses"] >= 8 and res["folded"]["folded"] == 1
    async with session_factory() as session:
        versions = (
            await session.scalars(select(DocumentVersion).order_by(DocumentVersion.version_no))
        ).all()
        assert [v.parse_revision for v in versions] == [2]
        assert versions[0].text_source == "ocr" and versions[0].state == "parsed"
        after = await session.scalar(select(func.count()).select_from(Clause))
        assert after == res["clauses"]  # 只剩新读法的条文
        assert await session.get(DocumentVersion, version_id) is None


async def test_numbers_are_recovered_in_the_new_version(session_factory):
    version_id, _ = await _setup(session_factory)
    async with session_factory() as session:
        await reparse_from_pages(session, version_id, _pages(), "ar", note="test")
        await session.commit()
        new = await session.scalar(select(DocumentVersion).where(DocumentVersion.version_no == 2))
        numbers = (
            await session.scalars(select(Clause.clause_number).where(Clause.version_id == new.id))
        ).all()
    assert {"(1)", "(8)"} <= set(numbers)


async def test_only_a_parsed_version_with_readable_pages_can_be_reparsed(session_factory):
    version_id, _ = await _setup(session_factory)
    async with session_factory() as session:
        with pytest.raises(ValueError):
            await reparse_from_pages(session, version_id, ["   "], "ar", note="x")


async def test_the_dry_run_changes_nothing(session_factory, tmp_path):
    import json

    version_id, _ = await _setup(session_factory)
    ocr = tmp_path / "ocr.json"
    ocr.write_text(json.dumps({"1": _pages()[0]}, ensure_ascii=False), "utf-8")
    res = await reparse_version(version_id, str(ocr), dry_run=True, sf=session_factory)
    assert res["dry_run"] and res["clauses"] >= 8
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(DocumentVersion)) == 1


async def test_importing_the_same_file_again_after_a_reparse_is_still_recognised(
    session_factory,
):
    from app.ingestion.archive import ArchiveStore

    version_id, doc_id = await _setup(session_factory)
    async with session_factory() as session:
        res = await reparse_from_pages(session, version_id, _pages(), "ar", note="test")
        await session.commit()
        digest = (
            await session.get(DocumentVersion, __import__("uuid").UUID(res["new_version_id"]))
        ).content_hash
        # the lookup used by manual import, crawl and upload must not trip over two versions
        rows = (
            await session.execute(
                select(DocumentVersion)
                .where(
                    DocumentVersion.document_id == __import__("uuid").UUID(doc_id),
                    DocumentVersion.content_hash == digest,
                )
                .order_by(DocumentVersion.version_no.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        assert rows is not None and rows.version_no == 2
        assert ArchiveStore.sha256(b"x")


GOOD_PAGE = " ".join(
    ["في هذا القانون من على أن التي الذي هذا عن او كل ما لا اذا وفقا بشان القانون"] * 9
)


async def test_a_better_page_reading_replaces_a_garbled_page_and_a_bad_one_changes_nothing(
    session_factory,
):
    from app.ingestion.reparse import apply_page_replacements

    version_id, _ = await _setup(session_factory)
    async with session_factory() as session:
        res = await apply_page_replacements(session, version_id, {1: "1 ٣ ~ 2_ /4 i:na"}, "ar")
        await session.commit()
    assert res["status"] == "kept_old" and 1 in res["rejected"]
    async with session_factory() as session:
        res = await apply_page_replacements(session, version_id, {1: GOOD_PAGE}, "ar")
        await session.commit()
        versions = (await session.scalars(select(DocumentVersion))).all()
    assert res["status"] == "replaced" and res["accepted"] == [1] and len(versions) == 1  # 旧读法已并入


FUNC = "في هذا القانون من على أن التي الذي هذا عن او كل ما لا اذا وفقا بشان القانون"


def _article(n: int) -> str:
    return f"المادة {n}\n" + " ".join([FUNC] * 9)


async def _numbered_ocr_document(session_factory, count=6):
    """An image-read document: one article per page, so page n holds article n."""
    async with session_factory() as session:
        texts = [_article(n) for n in range(1, count + 1)]
        doc, version, clauses = await make_document(session, texts)
        doc.language = "ar"
        version.text_source = "ocr"
        for n, clause in enumerate(clauses, 1):
            clause.page_start = n
            clause.clause_number = str(n)
        await session.commit()
        return str(version.id)


async def test_a_page_reading_that_makes_other_articles_vanish_is_refused(session_factory):
    from app.ingestion.reparse import apply_page_replacements

    version_id = await _numbered_ocr_document(session_factory)
    # 新读数本身通顺，但页里没有第 4 条的标题：切分后第 4 条消失
    lost = " ".join([FUNC] * 12)
    async with session_factory() as session:
        res = await apply_page_replacements(session, version_id, {4: lost}, "ar")
        await session.commit()
        versions = (await session.scalars(select(DocumentVersion))).all()
    assert res["status"] == "kept_old" and 4 in res["rejected"] and len(versions) == 1


async def test_a_page_reading_that_keeps_every_article_is_taken(session_factory):
    from app.ingestion.reparse import apply_page_replacements

    version_id = await _numbered_ocr_document(session_factory)
    better = _article(4) + " " + FUNC
    async with session_factory() as session:
        res = await apply_page_replacements(session, version_id, {4: better}, "ar")
        await session.commit()
    assert res["status"] == "replaced" and res["accepted"] == [4]


async def test_a_document_without_numbering_may_lose_a_block_when_garbled_text_is_replaced(
    session_factory,
):
    from app.ingestion.reparse import apply_page_replacements

    async with session_factory() as session:
        texts = [" ".join([FUNC] * 12) for _ in range(5)]
        _, version, clauses = await make_document(session, texts)
        version.text_source = "ocr"
        for n, clause in enumerate(clauses, 1):
            clause.page_start, clause.clause_number = n, None
        document_id, version_id = version.document_id, str(version.id)
        await session.commit()
    # 一页换成较短的正确读数：无条号文件按长度切块，块数可能少，但内容没有丢
    async with session_factory() as session:
        res = await apply_page_replacements(session, version_id, {3: " ".join([FUNC] * 9)}, "ar")
        await session.commit()
    assert res["status"] == "replaced" and res["accepted"] == [3], res
    assert document_id


async def test_apply_pages_keeps_the_text_before_a_repetition_loop(
    session_factory, monkeypatch, tmp_path
):
    import json

    from app.cli import apply_pages

    version_id = await _numbered_ocr_document(session_factory)
    monkeypatch.setattr("app.cli.get_sessionmaker", lambda: session_factory)
    # 真实正文不会由同一句话重复构成：每句各不相同
    body = " ".join(f"{FUNC} الفقرة {chr(0x0627 + k)} من هذه المادة رقم {k}." for k in range(1, 9))
    good = _article(4) + chr(10) + body
    looped = good + chr(10) + " \times و" * 500  # 好的正文后面跟着一长串重复
    index = tmp_path / "i.json"
    result = tmp_path / "r.json"
    index.write_text(json.dumps({"p4": {"version": version_id, "page": "4"}}), encoding="utf-8")
    result.write_text(json.dumps({"p4": looped}), encoding="utf-8")
    stats = await apply_pages(str(index), str(result))
    assert stats["replaced"] == 1 and stats["detail"][0]["accepted"] == [4], stats


def test_a_recut_is_refused_when_it_is_worse_than_the_current_version():
    from app.ingestion.reparse import regression_reason

    ok = dict(
        old_numbers={1, 2, 3, 4, 5},
        old_chars=20_000,
        old_pages=10,
        new_numbers={1, 2, 3, 4, 5, 6},
        new_chars=20_100,
        new_pages=10,
    )
    assert regression_reason(**ok) is None
    # 只读了一部分页：整份重切会把文档切成几条
    assert "只读了" in regression_reason(**{**ok, "new_pages": 4})
    # 文字量大减（例如只读了部分页，但页数凑够）
    assert "文字量" in regression_reason(**{**ok, "new_chars": 5_000})
    # 条号变少
    assert "条号" in regression_reason(**{**ok, "new_numbers": {1, 2, 3}})
    # 现有版本几乎没读出内容（乱码）：文字量与条号都可以涨，不拦
    garbage = dict(old_numbers=set(), old_chars=111, old_pages=2, new_numbers=set(), new_chars=4600)
    assert regression_reason(**{**garbage, "new_pages": 2}) is None
    # 现有版本只有零星几个“条号”（乱码里的数字）：不当底线，好读数可以替换它
    assert regression_reason(**{**ok, "old_numbers": {1}, "new_numbers": set()}) is None
    # 未知页数（老数据）不因页数拦
    assert regression_reason(**{**ok, "old_pages": 0, "new_pages": 1}) is None


async def test_a_partial_reading_does_not_become_the_latest_version(session_factory):
    version_id, doc_id = await _setup(session_factory)
    async with session_factory() as session:
        version = await session.get(DocumentVersion, version_id)
        version.page_count = 10
        await session.commit()
        before = await session.scalar(
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.document_id == doc_id)
        )
        with pytest.raises(ValueError, match="只读了"):
            await reparse_from_pages(session, version_id, _pages(), "ar", note="partial")
        await session.rollback()
        after = await session.scalar(
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.document_id == doc_id)
        )
        assert after == before


def test_trimming_a_file_to_its_own_instrument_is_allowed_only_when_asked_for():
    from app.ingestion.reparse import regression_reason

    kw = dict(
        old_numbers=set(range(1, 13)) | {19, 20, 21, 22},
        old_chars=10_600,
        old_pages=2,
        new_numbers=set(range(1, 13)),
        new_chars=8_100,
        new_pages=2,
    )
    assert regression_reason(**kw) is not None
    assert regression_reason(**kw, allow_shrink=True) is None
    # 即使允许裁剪，只读了一部分页仍然拒绝
    assert "只读了" in regression_reason(**{**kw, "new_pages": 1}, allow_shrink=True)
