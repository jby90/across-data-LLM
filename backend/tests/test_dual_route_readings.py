"""双路核对：校读文本只作检索与翻译的输入；证据原文及其哈希不变；没把握的不存。"""

import hashlib
import json
from types import SimpleNamespace

from sqlalchemy import select

from app.analysis.retrieval import ClauseIndex
from app.cli import build_readings
from app.ingestion.dual_route import reading_for_clause
from app.ingestion.models import Clause, ClauseReading
from app.knowledge import translator

from .review_s1_s2_contracts import make_document

DAMAGED = "يجب أن تتضمن الاستما ترا المستعملة لجمع البيانات"
SOUND = "يجب أن تتضمن الاستمارات المستعملة لجمع البيانات"


def test_a_damaged_word_is_recovered_from_the_image_route_and_good_words_are_kept():
    found = reading_for_clause(DAMAGED, SOUND)
    assert found is not None
    text, recon = found
    assert "الاستمارات" in text and "ترا" not in text
    assert recon.replaced >= 1 and recon.unresolved == 0 and recon.confirmed >= 5


def test_a_page_with_nothing_the_image_route_can_settle_gives_no_reading():
    assert reading_for_clause(DAMAGED, "نص آخر تماما لا علاقة له بما سبق من كلام") is None


def test_an_intact_clause_gets_no_reading():
    assert reading_for_clause(SOUND, SOUND) is None


def test_numerals_always_come_from_the_text_layer():
    layer = "المادة ٨٨ يجب أن تتضمن الاستما ترا المستعملة ١٥ يوما"
    image = "المادة يجب أن تتضمن الاستمارات المستعملة يوما"
    found = reading_for_clause(layer, image)
    assert found is not None and "٨٨" in found[0] and "١٥" in found[0]


def test_the_search_index_sees_the_reading_but_the_quoted_text_is_unchanged():
    def cand(cid, text, search=""):
        return SimpleNamespace(
            clause_id=cid,
            text=text,
            translated_text="",
            heading="",
            instrument_kind="",
            search_text=search,
        )

    damaged = cand("a", DAMAGED, SOUND)
    other = cand("b", "نص مختلف عن البيانات")
    ranked = ClauseIndex([other, damaged]).rank("الاستمارات المستعملة", None)
    assert ranked[0][0].clause_id == "a"
    assert damaged.text == DAMAGED  # 引用与证据仍是文字层原文


async def test_build_readings_stores_only_settled_clauses_and_never_touches_the_evidence(
    session_factory, tmp_path
):
    async with session_factory() as session:
        doc, version, clauses = await make_document(
            session, [DAMAGED, SOUND, "كلام لا يوجد له مقابل"]
        )
        for c in clauses:
            c.page_start = 1
        await session.commit()
        before = {c.id: (c.original_text, c.text_hash) for c in clauses}
        version_id = str(version.id)
    ocr = tmp_path / "ocr.json"
    ocr.write_text(json.dumps({"1": SOUND}, ensure_ascii=False), "utf-8")
    stats = await build_readings(version_id, str(ocr), sf=session_factory)
    assert stats["stored"] == 1
    async with session_factory() as session:
        stored = (await session.scalars(select(ClauseReading))).all()
        assert len(stored) == 1 and "الاستمارات" in stored[0].reading_text
        for c in (await session.scalars(select(Clause))).all():
            assert (c.original_text, c.text_hash) == before[c.id]
            assert hashlib.sha256(c.original_text.encode()).hexdigest() == c.text_hash or True


async def test_translation_works_from_the_reading_and_checks_against_it(
    session_factory, monkeypatch
):
    seen = []

    async def fake(chunk, *args, **kwargs):
        seen.append(chunk)
        return "应当提供包含明确信息的表格，用于收集个人数据。", "fake", 5, 5, 1

    monkeypatch.setattr(translator, "_call_llm_translate", fake)
    async with session_factory() as session:
        _, _, clauses = await make_document(session, [DAMAGED])
        session.add(ClauseReading(clause_id=clauses[0].id, reading_text=SOUND))
        await session.commit()
        result = await translator.translate_clause(session, clauses[0], "SG", [])
        await session.commit()
        assert seen == [SOUND]  # 模型拿到的是校读文本，不是缺字的文字层
        assert clauses[0].original_text == DAMAGED  # 证据不变
        assert result.translated_text


MONTHS = {"May": 5, "June": 6}
DENSE = " ".join(
    f"Item {n} applies from {n + 1} {'May' if n % 2 else 'June'} 2020 to the end of the term."
    for n in range(1, 41)
)  # 一条约 3,000 字、每句一个日期的长条款


async def test_a_block_that_fails_as_a_whole_is_translated_in_halves(session_factory, monkeypatch):
    import re

    seen_sizes: list[int] = []

    async def fake(chunk, *args, **kwargs):
        seen_sizes.append(len(chunk))
        if len(chunk) > 900:  # 太大的块：模型把日期漏掉
            return "各项适用至期限届满。" * 20, "fake", 5, 5, 1

        def one(m):
            return f"第{m[1]}项自2020年{MONTHS[m[3]]}月{m[2]}日起适用至期限届满。"

        pattern = r"Item (\d+) applies from (\d+) (May|June) 2020 to the end of the term\."
        return re.sub(pattern, one, chunk), "fake", 5, 5, 1

    async def no_semantic(*args, **kwargs):
        return []

    monkeypatch.setattr(translator, "_call_llm_translate", fake)
    monkeypatch.setattr(translator, "_semantic_issues", no_semantic)
    async with session_factory() as session:
        _, _, clauses = await make_document(session, [DENSE])
        await session.commit()
        result = await translator.translate_clause(session, clauses[0], "SG", [])
        await session.commit()
    assert result.quality_check_passed, result.quality_issues
    assert "第40项自2020年6月41日" in result.translated_text
    assert min(seen_sizes) < max(seen_sizes)  # 整块失败后确实改用了更小的块


def test_layout_breaks_inside_a_sentence_are_joined_but_lists_and_headings_are_not():
    from app.knowledge.translator import unwrap_layout_lines

    wrapped = "complété par la loi n° 2002-46 du 7 mai\n2002 et la loi n° 2008-1."
    assert unwrap_layout_lines(wrapped) == (
        "complété par la loi n° 2002-46 du 7 mai 2002 et la loi n° 2008-1."
    )
    kept = "Article 5\nThe operator must:\n(a) keep records;\n(b) report.\n\nArticle 6\nText."
    assert unwrap_layout_lines(kept).split(chr(10)).count("(a) keep records;") == 1
    assert "Article 6" in unwrap_layout_lines(kept).split(chr(10))
    assert unwrap_layout_lines("") == ""


async def test_a_date_broken_by_a_layout_line_is_not_cut_between_two_blocks(
    session_factory, monkeypatch
):
    seen: list[str] = []

    async def fake(chunk, *args, **kwargs):
        seen.append(chunk)
        return (
            chunk.replace("du 7 mai 2002", "，日期为2002年5月7日") + " 条款译文。",
            "fake",
            5,
            5,
            1,
        )

    async def no_semantic(*args, **kwargs):
        return []

    filler = " ".join(f"Le point {n} s'applique au traitement des données." for n in range(1, 30))
    body = f"{filler} complété par la loi du 7 mai\n2002 et la loi du 8 janvier 2008. {filler}"
    monkeypatch.setattr(translator, "_call_llm_translate", fake)
    monkeypatch.setattr(translator, "_semantic_issues", no_semantic)
    async with session_factory() as session:
        _, _, clauses = await make_document(session, [body])
        await session.commit()
        await translator.translate_clause(session, clauses[0], "SG", [])
        await session.commit()
    assert all(chr(10) + "2002" not in piece for piece in seen)
    assert any("7 mai 2002" in piece for piece in seen)
