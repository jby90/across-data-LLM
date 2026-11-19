import pytest
from sqlalchemy import func, select

from app.cli import batch_translate
from app.knowledge import translator
from app.knowledge.models import ClauseTranslation, KnowledgeAuditRecord

from .conftest import bearer, login
from .review_s1_s2_contracts import make_document


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Made on 29 March 2018", set()),
        ("55\nMade on 21 January 2015", set()),
        ("MAD 500", {("MAD", "500")}),
        ("500 MAD", {("MAD", "500")}),
        ("费用为500摩洛哥迪拉姆，应当缴纳。", {("MAD", "500")}),
        ("USDOLLAR 500", set()),
    ],
)
def test_currency_markers_do_not_match_inside_ordinary_words(text, expected):
    from app.knowledge.numeric_integrity import amounts

    assert amounts(text) == expected


@pytest.mark.parametrize(
    "original,zh,expected",
    [
        ("A maximum of 10% applies.", "最高适用百分之10，应当依法计算并执行。", True),
        ("The fee is $500.", "费用为500欧元，应按规定缴纳相关费用。", False),
        ("The fee is SGD 500.", "费用为500新元，应按规定缴纳相关费用。", True),
        ("On 19 December 2022 it enters into force.", "于2022年11月19日起正式生效施行。", False),
        ("On 19 December 2022 it enters into force.", "于2022年12月19日起正式生效施行。", True),
        ("The number is ١٠.", "该项编号应当按照规定记录为10并予以保存。", True),
    ],
)
def test_numeric_units_and_dates(original, zh, expected):
    assert translator.verify_translation(original, zh, [])[0] is expected


async def test_failed_retry_keeps_good_current_text_and_all_attempt_costs(
    session_factory, monkeypatch
):
    async with session_factory() as session:
        _, _, clauses = await make_document(session)

        async def good(*args, **kwargs):
            return "组织必须保护个人数据并依法通知监管机构处理泄露。", "fake", 10, 20, 5

        monkeypatch.setattr(translator, "_call_llm_translate", good)
        first = await translator.translate_clause(session, clauses[0], "SG", [])
        await session.commit()
        text = first.translated_text

        async def bad(*args, **kwargs):
            return "not Chinese", "fake", 3, 4, 2

        monkeypatch.setattr(translator, "_call_llm_translate", bad)
        second = await translator.translate_clause(session, clauses[0], "SG", [])
        await session.commit()
        assert first.id != second.id
        assert first.is_current and first.translated_text == text
        assert not second.is_current and second.review_status == "translation_failed"
        assert second.tokens_in == 6 and second.tokens_out == 8
        assert await session.scalar(select(func.count()).select_from(ClauseTranslation)) == 2
        assert await session.scalar(select(func.count()).select_from(KnowledgeAuditRecord)) == 2


async def test_dry_run_full_missing_is_read_only(session_factory, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("dry run must not call model")

    monkeypatch.setattr(translator, "_call_llm_translate", forbidden)
    async with session_factory() as session:
        await make_document(session, [f"Clause {i}: Protect personal data." for i in range(21)])
        await session.commit()
    stats = await batch_translate("SG", sf=session_factory, dry_run=True)
    assert stats["total"] == 21 and not stats["limited"]
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ClauseTranslation)) == 0


async def test_translation_pagination_reaches_beyond_first_page(client, seed, session_factory):
    async with session_factory() as session:
        _, _, clauses = await make_document(session, [f"Clause {i}" for i in range(13)])
        for clause in clauses:
            session.add(
                ClauseTranslation(
                    clause_id=clause.id,
                    translated_text="个人数据依法保护。",
                    engine="llm",
                    model_name="test",
                    review_status="machine_translated",
                )
            )
        await session.commit()
    headers = bearer(await login(client, "admin", "admin@example.com"))
    first = await client.get("/api/admin/knowledge/translations?limit=10", headers=headers)
    second = await client.get(
        "/api/admin/knowledge/translations?limit=10&offset=10", headers=headers
    )
    assert first.headers["X-Total-Count"] == "13"
    assert len(first.json()) == 10 and len(second.json()) == 3
    assert not {row["id"] for row in first.json()} & {row["id"] for row in second.json()}


async def test_direct_translation_sends_explicit_mode_and_tracks_real_usage(monkeypatch):
    import json

    import httpx

    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", "test-only")
    monkeypatch.setattr(settings, "llm_translation_thinking", "disabled")
    original_client = httpx.AsyncClient

    def handler(request):
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in body
        assert "__CYSH_LITERAL_A__" in body["messages"][1]["content"]
        return httpx.Response(
            200,
            json={
                "model": "actual-response-model",
                "choices": [
                    {"message": {"content": "最高比例为__CYSH_LITERAL_A__，应依法履行规定。"}}
                ],
                "usage": {"prompt_tokens": 123, "completion_tokens": 45},
            },
        )

    monkeypatch.setattr(
        translator.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    result = await translator._call_llm_translate("The maximum percentage is 10%.", "en", [], [])
    assert result[1:4] == ("actual-response-model", 123, 45)
    assert result[0] == "最高比例为10%，应依法履行规定。"


def test_numeric_protection_is_lossless_and_does_not_weaken_currency_gate():
    source = "Section 128(1)(a): $200,000 and SGD 500, maximum 10%. Made on 19 December 2022."
    safe, mapping = translator._protect_numeric_literals(source)
    assert "$200,000" not in safe and "SGD 500" not in safe and "10%" not in safe
    for marker, value in mapping.items():
        safe = safe.replace(marker, value)
    assert safe == source
    assert not translator.verify_translation(
        "A fee of $500 applies.", "费用为500欧元，应当按规定缴纳。", []
    )[0]


def test_bare_citation_remains_a_failed_translation_without_padding():
    assert not translator.verify_translation("[40/2020]", "[40/2020]", [])[0]
    assert not translator.verify_translation(
        "[40/2020]", "原文所引用的相关法规编号标记为：[40/2020]", []
    )[0]


def test_derived_clause_identifier_does_not_invent_a_number_inside_a_year():
    original = "The organisation must protect personal data. [40/2020]"
    translated = "组织必须依法保护相关个人数据并遵守规定。[40/2020]"
    assert translator.verify_translation(original, translated, [], clause_number="53.2")[0]
    assert not translator.verify_translation(
        original, translated.replace("2020", "2021"), [], clause_number="53.2"
    )[0]


@pytest.mark.parametrize(
    "source,expected",
    [
        ("1 February 2021", "2021年2月1日"),
        ("30 جويلية 2004", "2004年7月30日"),
        ("27 juillet 2004", "2004年7月27日"),
        ("December 19, 2022", "2022年12月19日"),
    ],
)
def test_explicit_date_format_is_exact_and_wrong_year_day_remains_rejected(source, expected):
    safe, mapping = translator._protect_explicit_dates(source)
    assert safe == "__CYSH_DATE_A__" and mapping[safe] == expected
    assert translator.dates(source) == translator.dates(mapping[safe])
    assert not translator.verify_translation(
        source, "法律条款明确载明的相关日期为2020年1月1日。", []
    )[0]


def test_privacy_placeholders_preserve_legal_reference_without_sending_original():
    text = "Reference 2020-1234; call +65 6123 4567; contact person@example.com."
    safe, mapping = translator._redact_with_mapping(text)
    assert "person@example.com" not in safe and "2020-1234" not in safe
    assert "__CYSH_PRIVATE_" in safe
    for marker, original in mapping.items():
        safe = safe.replace(marker, original)
    assert safe == text


@pytest.mark.parametrize("keep_marker", [True, False])
async def test_transport_restores_private_tokens_or_fails_quality(monkeypatch, keep_marker):
    import json

    import httpx

    settings = translator.get_settings()
    monkeypatch.setattr(settings, "llm_api_key", "test-only")
    original_client = httpx.AsyncClient
    original = "Contact person@example.com for lawful personal data protection."

    def handler(request):
        body = json.loads(request.content)
        assert "person@example.com" not in json.dumps(body)
        assert "__CYSH_PRIVATE_A__" in body["messages"][1]["content"]
        content = "请联系相关部门依法保护个人数据并履行保护义务。"
        if keep_marker:
            content += "__CYSH_PRIVATE_A__"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(
        translator.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    result = await translator._call_llm_translate(original, "en", [], [])
    assert ("person@example.com" in result[0]) is keep_marker
    assert translator.verify_translation(original, result[0], [])[0] is keep_marker


async def test_the_same_passage_is_translated_once_and_reused_after_it_passes(
    session_factory, monkeypatch
):
    calls = []

    async def good(chunk, *args, **kwargs):
        calls.append(chunk)
        return "组织必须保护个人数据并依法通知监管机构处理泄露。", "fake", 10, 20, 5

    monkeypatch.setattr(translator, "_call_llm_translate", good)
    text = "Organisations must protect personal data and notify the regulator of a breach."
    async with session_factory() as session:
        _, _, clauses = await make_document(session, [text, text])
        first = await translator.translate_clause(session, clauses[0], "SG", [])
        second = await translator.translate_clause(session, clauses[1], "SG", [])
        await session.commit()
    assert len(calls) == 1  # 第二条相同原文直接复用
    assert second.quality_check_passed and second.tokens_in == 0
    assert second.translated_text == first.translated_text


async def test_a_long_clause_only_redoes_the_passage_that_failed(session_factory, monkeypatch):
    sentence = "Each organisation shall protect the personal data held in its possession. "
    first_part = sentence * 22
    second_part = "Every operator shall keep an audit record of access to the system. " * 22
    text = first_part + "\n" + second_part
    seen: dict[str, int] = {}
    second_fails = {"on": True}

    async def fake(chunk, *args, **kwargs):
        seen[chunk] = seen.get(chunk, 0) + 1
        if "audit record" in chunk and second_fails["on"]:
            return "still English only", "fake", 1, 1, 1
        zh = (
            "每个组织都应当保护其持有的个人数据。"
            if "organisation" in chunk
            else "每个运营者都应当保存系统访问的审计记录。"
        )
        return zh * 12, "fake", 5, 5, 1

    monkeypatch.setattr(translator, "_call_llm_translate", fake)
    async with session_factory() as session:
        _, _, clauses = await make_document(session, [text])
        failed = await translator.translate_clause(session, clauses[0], "SG", [])
        await session.commit()
        assert not failed.quality_check_passed
        passing_chunk_calls = sum(n for c, n in seen.items() if "organisation" in c)
        second_fails["on"] = False
        again = await translator.translate_clause(session, clauses[0], "SG", [])
        await session.commit()
    assert again.quality_check_passed
    # 第一轮通过的块没有再请求模型
    assert sum(n for c, n in seen.items() if "organisation" in c) == passing_chunk_calls


def _attempt(clause, text, *, current, passed=False, prompt=translator.PROMPT_VERSION):
    from datetime import UTC, datetime

    return ClauseTranslation(
        clause_id=clause.id,
        is_current=current,
        translated_text=text,
        engine="llm",
        model_name="fake",
        prompt_version=prompt,
        glossary_hits=[],
        unmatched_terms=[],
        quality_check_passed=passed,
        quality_issues=[] if passed else ["旧规则下未通过"],
        review_status="machine_translated" if passed else "translation_failed",
        updated_at=datetime.now(UTC),
    )


async def test_reverify_restores_translations_the_current_rules_accept_and_keeps_the_rest_failed(
    session_factory,
):
    from app.cli import reverify_translations

    async with session_factory() as session:
        _, _, clauses = await make_document(
            session,
            [
                "Contact the Commission by e-mail contact@cndp.ma or on www.cndp.ma, "
                "or visit the office in Rabat during working hours.",
                "The organisation must notify the Commission of a data breach without delay.",
            ],
        )
        # 旧规则把网址、邮箱算进“未译部分”而误判；现行规则只看该翻的文字
        toc_text = "请通过电子邮件 contact@cndp.ma 或网站 www.cndp.ma 联系委员会，或在办公时间到拉巴特的办公室。"
        toc = _attempt(clauses[0], toc_text, current=True)
        untouched = _attempt(
            clauses[1], "The organisation must notify the Commission of a breach.", current=True
        )
        session.add_all([toc, untouched])
        await session.commit()
    stats = await reverify_translations("SG", sf=session_factory)
    assert stats["restored"] == 1 and stats["still_failing"] == 1
    async with session_factory() as session:
        rows = {
            r.translated_text: r for r in (await session.scalars(select(ClauseTranslation))).all()
        }
    restored = rows[toc_text]
    assert restored.quality_check_passed and restored.review_status == "machine_translated"
    bad = rows["The organisation must notify the Commission of a breach."]
    assert not bad.quality_check_passed and bad.review_status == "translation_failed"


async def test_a_clause_that_keeps_failing_is_not_retried_forever(session_factory, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("an exhausted clause must not reach the model")

    monkeypatch.setattr(translator, "_call_llm_translate", forbidden)
    async with session_factory() as session:
        _, _, clauses = await make_document(
            session, ["Organisations must protect personal data.", "Operators must keep logs."]
        )
        session.add_all(
            [_attempt(clauses[0], "bad", current=i == 2) for i in range(3)]
            + [_attempt(clauses[1], "bad", current=True)]
        )
        await session.commit()
    stats = await batch_translate("SG", only_missing=True, sf=session_factory, dry_run=True)
    assert stats["retry_exhausted"] == 1 and stats["total"] == 1
    again = await batch_translate(
        "SG", only_missing=True, sf=session_factory, dry_run=True, max_attempts=0
    )
    assert again["retry_exhausted"] == 0 and again["total"] == 2


async def test_documents_outside_the_three_businesses_are_not_translated(session_factory):
    async with session_factory() as session:
        doc, _, _ = await make_document(
            session, [f"Clause {i}: Protect personal data." for i in range(5)]
        )
        doc.relevance = "out_of_scope"
        await session.commit()
    stats = await batch_translate("SG", sf=session_factory, dry_run=True)
    assert stats["total"] == 0
    async with session_factory() as session:
        doc = (
            await session.scalars(
                select(__import__("app.ingestion.models", fromlist=["Document"]).Document)
            )
        ).first()
        doc.relevance = "relevant"
        await session.commit()
    assert (await batch_translate("SG", sf=session_factory, dry_run=True))["total"] == 5
