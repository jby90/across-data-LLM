from app.ingestion.page_replace import judge, merge_pages, numbers_of, strip_page_furniture

AR_OK = " ".join(
    ["في هذا القانون من على أن التي الذي هذا عن او كل ما لا اذا وفقا بشان القانون"] * 8
)
AR_JUNK = "1 1 ٣ ~ 2_ /4 : 2 ن 2 i:na i ن خات رن دل ذم 1 +.* م ∴"


def test_numbers_are_read_across_arabic_and_latin_digits():
    assert numbers_of("القرار رقم ٨٣ لسنة ٢٠٠٥ و 12") == ["83", "2005", "12"]


def test_a_latin_letterhead_above_arabic_text_is_dropped_but_the_text_stays():
    text = "<PAGE>" + chr(10) + "Faculty of Computers & Information" + chr(10) + AR_OK
    cleaned = strip_page_furniture(text.replace("<PAGE>", ""), "ar")
    assert "Faculty" not in cleaned and "القانون" in cleaned


def test_a_garbled_old_page_is_replaced_by_a_usable_new_reading():
    v = judge(AR_JUNK, AR_OK, "ar")
    assert v.accept and v.text.startswith("في هذا القانون")


def test_an_unusable_new_reading_never_replaces_anything():
    v = judge(AR_OK, AR_JUNK, "ar")
    assert not v.accept and v.text == AR_OK


def test_when_the_old_page_is_readable_a_contradicting_number_set_keeps_the_old_one():
    old = AR_OK + " القرار رقم 83 لسنة 2005 المادة 12 والمادة 14"
    new = AR_OK + " القرار رقم 91 لسنة 1999 المادة 33 والمادة 41"
    assert not judge(old, new, "ar").accept
    agree = AR_OK + " القرار رقم 83 لسنة 2005 المادة 12 والمادة 15"
    assert judge(old, agree, "ar").accept


def test_merging_touches_only_the_replaced_pages():
    merged, verdicts = merge_pages({1: AR_JUNK, 2: AR_OK}, {1: AR_OK}, "ar")
    assert merged[1].startswith("في") and merged[2] == AR_OK and verdicts[1].accept


def test_invented_readings_are_refused():
    from app.ingestion.page_replace import looks_invented

    vowelled = "قَسْرَارُ رَضِيَصِي مَجْلِسِي الْسَّمَوَارَةِ " * 10
    assert looks_invented(vowelled)
    assert looks_invented("مادة 1\ntoXIM&+\nنص")
    assert not looks_invented("مادة (135) تفرض على أعمال التجارة الإلكترونية الضرائب والرسوم")


def test_short_page_of_one_article_is_accepted():
    from app.ingestion.page_replace import judge

    short = (
        "المادة 135: تفرض على أعمال التجارة الإلكترونية الضرائب والرسوم المفروضة على الأعمال "
        "غير الإلكترونية وفق القانون المرعي الإجراء ويراعى في ذلك ما ورد في القانون وفي النصوص "
        "التي تنظم هذه الأعمال على ما يلي من الأحكام التي ترد في هذا القانون."
    )
    assert judge("", short, "ar").accept


def test_an_email_line_or_percentage_is_not_noise_but_symbol_gibberish_is():
    from app.ingestion.page_replace import looks_invented

    assert not looks_invented(
        "• Email : plainte@cndp.ma\nTaux : 50% des cas\nTél +212 5 37 57 11 24"
    )
    assert looks_invented("texte normal\ntoXIM&+\nsuite")
    assert looks_invented("texte normal\n+HOeE+\nsuite")


def test_html_tables_from_the_model_become_plain_lines_without_tags():
    from app.ingestion.page_replace import clean_model_markup

    html = (
        "<table><tr><td>الضابط<br/>الأول</td><td>وصف الضابط</td></tr>"
        "<tr><td>١.٢</td><td>يجب وضع قائمة</td></tr></table>"
    )
    out = clean_model_markup(html)
    assert "<" not in out and ">" not in out
    assert out.splitlines() == ["الضابط الأول | وصف الضابط", "١.٢ | يجب وضع قائمة"]
    assert clean_model_markup("نص عادي بلا وسوم") == "نص عادي بلا وسوم"


def test_a_repetition_loop_is_taken_for_invented_text_but_a_normal_table_is_not():
    from app.ingestion.page_replace import looks_invented

    assert looks_invented("المادة 5 يجب على المنظمة حماية البيانات الشخصية. " * 40)
    rows = " ".join(
        f"الضابط {n} يجب وضع قائمة جرد للأصول رقم {n} ثم مراجعتها" for n in range(1, 30)
    )
    assert not looks_invented(rows)


_LETTERS = "بتثجحخدذرزسشصضطظعغ"
TERMS = " ".join(  # 60 个互不相同的“词”（只含字母，数字会被拆成单独的词）
    a + b + c + "ات" for a in _LETTERS[:5] for b in _LETTERS[5:9] for c in _LETTERS[9:12]
)


def test_two_engines_reading_the_same_words_corroborate_a_page_of_terms():
    from app.ingestion.page_replace import engines_agree, judge

    other = TERMS + " زيادة"
    assert engines_agree(TERMS, other)
    assert not engines_agree(
        TERMS,
        " ".join(
            c + b + a + "ون" for a in _LETTERS[6:11] for b in _LETTERS[:4] for c in _LETTERS[12:15]
        ),
    )
    assert not engines_agree("قصير جدا", "قصير جدا")  # 词太少不算印证
    # 术语页功能词少，单看不成文；有第二个引擎印证就采用，仍要过编造检查
    assert not judge("", TERMS, "ar").accept
    assert judge("", TERMS, "ar", corroborated=True).accept
    invented = "ٱلْقَانُونُ ٱلْأَوَّلُ " * 40
    assert not judge("", invented, "ar", corroborated=True).accept


def test_a_page_that_falls_into_a_loop_keeps_what_came_before_the_loop():
    from app.ingestion.page_replace import truncate_loop

    good = "\n".join(
        f"المادة {n} : نص المادة رقم {n} يحدد الالتزام بوضوح تام." for n in range(1, 6)
    )
    looped = good + "\n" + " \times و" * 400
    cut = truncate_loop(looped)
    assert cut.startswith("المادة 1 :") and "المادة 5 :" in cut and "\times" not in cut
    assert truncate_loop(good) == good  # 没有循环的页原样不动


def test_old_reading_with_dropped_digits_does_not_block_the_new_one():
    old = AR_OK + " ٥ ألف جنيه ٢ ألف جنيه ١ ألاف جنيه"
    new = AR_OK + " ٥٠ ألف جنيه ٢٠ ألف جنيه ١٠ ألاف جنيه"
    assert judge(old, new, "ar").accept


def test_old_reading_with_different_figures_still_blocks_the_new_one():
    old = AR_OK + " ١٥ ألف جنيه ٢٥ ألف جنيه ٤١ ألف جنيه"
    new = AR_OK + " ٩٠ ألف جنيه ٣٧ ألف جنيه ٦٨ ألف جنيه"
    assert not judge(old, new, "ar").accept


def test_old_reading_spanning_neighbouring_pages_does_not_block_a_correct_new_page():
    old = AR_OK + " ٥ ألف جنيه ١٠ آلاف ٢٠ ألف ٢٥ ألف ٣٧ ألف ٤٠ ألف ٦٠ ألف ٨٠ ألف ٩٠ ألف ٧٠ ألف"
    new = AR_OK + " ٥ ألف جنيه ١٠ آلاف ٢٠ ألف ٢٥ ألف"
    assert judge(old, new, "ar").accept


def test_vote_takes_the_variant_a_third_reading_supports_and_never_a_lone_figure():
    from app.ingestion.page_replace import vote_readings

    primary = "أجهزة الهاتف الحكومية 15,000,000 ميمقراطية"
    other = "أجهزة الهاتف الخلوية 10,000,000 ميغاهرتز"
    witness = "أجهزة الهاتف الخلوية 15,000,000 ميغاهرتز"
    voted = vote_readings(primary, other, witness)
    assert "الخلوية" in voted and "ميغاهرتز" in voted  # 词：第三读数支持别人的
    assert "15,000,000" in voted and "10,000,000" not in voted  # 数字：第三读数不支持就不换
    assert vote_readings(primary, other, "") == primary  # 没有见证读数就不动


def test_a_new_reading_that_lost_a_large_part_of_a_readable_page_is_refused():
    tail = " ".join(
        "كلمه" + "ابتثجحخ"[i % 7] + "دذرزسش"[(i // 7) % 6] + "صضطظع"[(i // 42) % 5]
        for i in range(80)
    )
    old = AR_OK + " " + tail
    v = judge(old, AR_OK, "ar", corroborated=True)
    assert not v.accept and "少了一大段" in v.reason
    # 内容基本完整的新读数不受影响
    assert judge(old, AR_OK + " " + " ".join(tail.split()[:60]), "ar", corroborated=True).accept


def test_lines_a_second_engine_does_not_support_are_dropped_and_real_lines_kept():
    from app.ingestion.page_replace import HEAD_LINES, drop_unsupported_lines

    fake = "الجمهورية الجزائرية الديمقراطية الشعبية"
    real = "تحيين التوجهات الوطنية لأمن نظم المعلومات"
    primary = "\n".join([fake, real, "منشور رقم 2/2023", "2/1"])
    secondary = "المملكة المغربية رئيس الحكومة\n" + real + "\nمنشور رقم 2/2023"
    kept, dropped = drop_unsupported_lines(primary, secondary)
    assert dropped == [fake]
    assert real in kept and "2/1" in kept and "الجزائرية" not in kept
    # 没有第二个读数时什么都不删
    assert drop_unsupported_lines(primary, "")[1] == []

    # 第一页的抬头区：带“第二读数里没有的数字”的行也删（手写日期、编号最容易被编造）
    letter = "الرباط، في : 1000/2020\nمنشور رقم 2000/2020\n" + "\n".join([real] * 11)
    second = "الرباط في 14\nمنشور 02\n" + real
    kept2, dropped2 = drop_unsupported_lines(letter, second, head_lines=HEAD_LINES)
    assert "الرباط، في : 1000/2020" in dropped2 and "منشور رقم 2000/2020" in dropped2
    # 不是第一页（head_lines=0）时，正文里数字不一致不在这里处理（交给两次读数一致性闸门）
    body_line = "وفي سنة 2020 تم إغناء الترسانة القانونية لبلادنا بالقانون رقم 05.20"
    assert drop_unsupported_lines(body_line, body_line.replace("05.20", "05.2"))[1] == []


def test_block_coordinate_markers_left_by_the_engine_are_removed():
    from app.ingestion.page_replace import clean_model_markup

    raw = "title[246, 106, 314, 120]Article 11\n\ntext[10, 20, 30, 40]Le certificat électronique\nsub_title[1,2,3,4]Chapitre"
    out = clean_model_markup(raw)
    assert "[246" not in out and "title[" not in out and "text[" not in out
    assert (
        out.startswith("Article 11") and "Le certificat électronique" in out and "Chapitre" in out
    )
    # 正常文字里的方括号引用不受影响
    assert clean_model_markup("see table[1] of the schedule") == "see table[1] of the schedule"


def test_raw_block_markers_of_the_engine_output_are_removed_before_tags_are_stripped():
    from app.ingestion.page_replace import clean_model_markup

    raw = "x<BLOCK>title<LEVEL1>[252, 190, 308, 202]<FORK>Article 3\n\n<BLOCK>text[1, 2, 3, 4]<FORK>Le titre"
    out = clean_model_markup(raw)
    assert "[252" not in out and "BLOCK" not in out and "FORK" not in out and "LEVEL" not in out
    assert "Article 3" in out and "Le titre" in out
