"""入库质量卡：同一套指标适用于任何国家与语言；不达标要写明原因。"""

from app.ingestion.quality_card import ClauseSample, assess


def _clauses(numbers, text="Every organisation shall protect personal data.", translated=True):
    return [ClauseSample(str(n), text, translated) for n in numbers]


def test_a_clean_document_passes():
    card = assess(_clauses(range(1, 21)), "en", needs_translation=True)
    assert card.passed and card.number_continuity == 1.0 and card.translated == 1.0


def test_missing_articles_are_reported_with_the_rate():
    card = assess(_clauses([n for n in range(1, 21) if n % 3]), "en", needs_translation=False)
    assert not card.passed and any("条没识别出来" in p for p in card.problems)


def test_an_amendment_lists_only_the_changed_articles_so_gaps_are_expected():
    numbers = [3, 7, 12, 18, 25, 31]
    assert assess(
        _clauses(numbers), "en", needs_translation=False, instrument_kind="AMENDMENT"
    ).passed
    assert not assess(_clauses(numbers), "en", needs_translation=False).passed


def test_text_in_the_wrong_script_or_with_broken_glyphs_is_unreadable():
    garbled = "\ufffd\ufffd\ufffd\ufffd شسيب" * 3
    card = assess(_clauses(range(1, 11), text=garbled), "ar", needs_translation=False)
    assert any("可能识别错了" in p for p in card.problems)
    arabic = "يجب على كل جهة حماية البيانات الشخصية"
    assert assess(_clauses(range(1, 11), text=arabic), "ar", needs_translation=False).passed


def test_untranslated_clauses_lower_the_translation_coverage():
    clauses = _clauses(range(1, 11)) + _clauses(range(11, 21), translated=False)
    card = assess(clauses, "en", needs_translation=True)
    assert card.translated == 0.5 and any("没有中文" in p for p in card.problems)


def test_a_guide_without_numbered_articles_is_judged_on_the_other_measures_only():
    card = assess(
        [ClauseSample(None, "Section about the scheme.", True) for _ in range(8)],
        "en",
        needs_translation=True,
    )
    assert card.passed and card.number_continuity is None


def test_nothing_parsed_is_a_failure_in_every_language():
    assert not assess([], "fr", needs_translation=True).passed


async def test_the_admin_endpoint_lists_cards_and_can_show_only_the_failing_ones(
    client, seed, session_factory
):
    from .conftest import bearer, login
    from .review_s1_s2_contracts import make_document

    async with session_factory() as session:
        await make_document(session, ["Organisations must protect personal data."])
        await session.commit()
    admin = bearer(await login(client, "admin", "admin@example.com"))
    allrows = await client.get("/api/admin/quality-cards", headers=admin)
    assert allrows.status_code == 200 and allrows.json()
    row = allrows.json()[0]
    assert {"title", "clauses", "readable", "translated", "passed", "problems"} <= set(row)
    failing = await client.get("/api/admin/quality-cards?only_failing=true", headers=admin)
    assert all(not r["passed"] and r["problems"] for r in failing.json())
    member = bearer(await login(client, "user", "a-member@example.com"))
    assert (await client.get("/api/admin/quality-cards", headers=member)).status_code in (401, 403)


def test_a_shortfall_is_said_as_how_many_clauses_lack_chinese_not_as_a_threshold():
    clauses = [ClauseSample(None, "Plain text of a clause.", i < 94) for i in range(100)]
    card = assess(clauses, "en", needs_translation=True)
    assert "还有 6 条没有中文" in card.problems and not any("%" in p for p in card.problems)
    almost = [ClauseSample(None, "Plain text of a clause.", i < 949) for i in range(1000)]
    assert "还有 51 条没有中文" in assess(almost, "en", needs_translation=True).problems


def test_normal_latin_words_in_an_arabic_document_are_content_not_garbage():
    from app.ingestion.quality_card import _readable_ratio

    arabic = "منشور استيراد رقم بشأن شروط استيراد السيارات للاستعمال الشخصي " * 3
    letterhead = "Faculty of Computers & Information\nMinistry Of Finance\nNTRA\n"
    assert _readable_ratio([letterhead + arabic], "ar") > 0.95
    garbage = "toXIM&t WICYOEO alOOEXH " * 6
    assert _readable_ratio([garbage + arabic], "ar") < 0.9


def test_short_table_cells_are_outside_the_translated_share_but_a_real_clause_is_not():
    from app.ingestion.quality_card import is_short_fragment

    assert is_short_fragment("4. 100,000 $0.012 per number")
    assert is_short_fragment("Amount" + chr(10) + "1. 5,000 $100")
    assert not is_short_fragment("The organisation must notify the Commission without delay.")


async def test_cards_in_does_not_count_short_untranslated_cells_but_counts_real_gaps(
    session_factory,
):
    from app.ingestion.quality_card import cards_in
    from app.knowledge.models import ClauseTranslation

    from .review_s1_s2_contracts import make_document

    prose = "The organisation must protect personal data in its possession or control. "
    async with session_factory() as session:
        texts = [prose + str(n) for n in range(1, 11)] + ["4. 100,000 $0.012 per number"]
        doc, version, clauses = await make_document(session, texts)
        doc.language = "en"
        version.state = "parsed"
        for c in clauses[:9]:  # 9 条正文有合格译文；第 10 条正文没有；最后一条是短单元格
            session.add(
                ClauseTranslation(
                    clause_id=c.id,
                    translated_text="译文",
                    engine="llm",
                    model_name="t",
                    prompt_version="t",
                    quality_check_passed=True,
                    review_status="machine_translated",
                    is_current=True,
                )
            )
        await session.commit()
        card = next(c["card"] for c in await cards_in(session) if c["title"] == doc.title)
        # 短单元格与“仅数字行”同样处理：按“无需单独译文”算已满足 → 10/11；
        # 没有这条规则时是 9/11（81.8%），第 10 条正文的真缺口仍然算缺
        assert abs(card.translated - 10 / 11) < 1e-9
        assert "还有 1 条没有中文" in card.problems  # 90.9% 仍低于 95%：真缺口不被掩盖


def test_english_letterhead_lines_in_an_arabic_scan_are_not_misreadings():
    from app.ingestion.quality_card import _readable_ratio

    # 海关通函的英文信头、地址、编号是原件本来的样子，不算识别错；夹在正文里的乱码照样算
    assert _readable_ratio(["C. D. Of Customs Politics & Procedures"], "ar") is None
    assert _readable_ratio(["ALEXANDRIA PORT, GATE 14, A3 BUILDING, 4th FLOOR"], "ar") is None
    assert _readable_ratio(["وزارة المالية\nMinistry Of Finance"], "ar") == 1.0
    assert _readable_ratio(["الممسوحة ضوئياً بـ CamScanner"], "ar") == 1.0
    assert _readable_ratio(["toXIM WICYOEO"], "ar") == 0.0
    assert _readable_ratio(["قانون toXIM WICYOEO الاستيراد"], "ar") < 0.9
