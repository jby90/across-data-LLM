"""条标题补全：标题行读出来了、编号丢了，按逐条递增的规律补回。"""

from app.ingestion.article_numbers import article_numbers
from app.ingestion.heading_sequence import MIN_HEADINGS, repair_headings


def _law(headings: list[str]) -> list[str]:
    return ["\n".join(f"{h}\nنص المادة رقم كذا يلتزم المتحكم" for h in headings)]


def test_bare_headings_are_numbered_in_order():
    pages, stats = repair_headings(_law(["ماده"] * 8))
    assert article_numbers(pages[0], "ar") == set(range(1, 9))
    assert stats.found == 8 and stats.last == 8 and stats.inferred == 8


def test_a_readable_number_re_anchors_after_a_missed_heading():
    # 第 10、11 条的标题整个没读出来：读到完整的 12 时应当跳到 12，而不是错标成 10
    headings = ["ماده"] * 9 + ["ماده12", "ماده"]
    pages, stats = repair_headings(_law(headings))
    assert article_numbers(pages[0], "ar") == {*range(1, 10), 12, 13}
    assert stats.from_anchor == 1


def test_a_garbled_digit_does_not_override_the_order():
    # “ماده2”出现在第 7 位：残缺数字只是旁证，顺序为准
    headings = ["ماده"] * 6 + ["ماده2", "ماده"]
    pages, stats = repair_headings(_law(headings))
    assert article_numbers(pages[0], "ar") == set(range(1, 9))
    assert stats.conflicts == 1


def test_number_on_the_next_line_is_merged_into_the_heading():
    pages, _ = repair_headings(_law(["ماده"] * 5 + ["ماده\n6"]))
    text = pages[0]
    assert 6 in article_numbers(text, "ar") and "\n6\n" not in text


def test_too_few_headings_leave_the_text_untouched():
    original = _law(["ماده"] * (MIN_HEADINGS - 1))
    pages, stats = repair_headings(original)
    assert pages == original and stats.found == MIN_HEADINGS - 1


def test_sentence_mentioning_an_article_is_not_a_heading():
    original = ["وذلك وفقا لاحكام الماده من هذا القانون\n" * 10]
    assert repair_headings(original)[1].found == 0


def test_engine_applies_the_repair_to_arabic_results_only():
    from app.ingestion.ocr import OcrEngine, OcrResult

    raw = OcrResult(pages=_law(["ماده"] * 8), mean_confidence=80.0, languages="ara")
    fixed = OcrEngine._with_headings(raw, "ar")
    assert article_numbers(fixed.pages[0], "ar") == set(range(1, 9))
    assert OcrEngine._with_headings(raw, "fr") is raw


def test_clause_splitter_accepts_a_stretched_article_heading():
    from app.ingestion.clauses import split_clauses

    pages = ["المسادة الأولى\nيعمل بأحكام هذا القانون\nالمادة الثانية\nتسري أحكام هذا القانون"]
    drafts, _ = split_clauses(pages, paragraph_mode="blank")
    numbers = [d.clause_number for d in drafts if d.clause_number]
    assert len(numbers) == 2 and numbers[0].endswith("الأولى")


def test_tunisian_fasl_headings_are_numbered_and_kept_as_fasl():
    pages, stats = repair_headings(_law(["الفصل"] * 8))
    assert stats.found == 8 and stats.last == 8
    assert article_numbers(pages[0], "ar") == set(range(1, 9))
    assert "الفصل (8)" in pages[0] and "المادة (" not in pages[0]


def test_each_heading_word_keeps_its_own_sequence():
    headings = ["الفصل"] * 3 + ["ماده"] * 3 + ["الفصل"] * 3
    pages, stats = repair_headings(_law(headings))
    text = pages[0]
    assert text.count("الفصل (4)") == 1 and text.count("المادة (3)") == 1
    assert stats.found == 9


def test_clause_splitter_numbers_fasl_headings_but_not_chapter_titles():
    from app.ingestion.clauses import split_clauses

    pages = [
        "الباب الأول\nالفصل 1\nيحق لكل شخص حماية معطياته\nالفصل 2\nتنطبق أحكام هذا القانون\n"
        "الفصل ٣\nلا تنطبق على الاستعمال الشخصي\nالفصل الأول\nعنوان باب"
    ]
    drafts, _ = split_clauses(pages, paragraph_mode="blank")
    numbers = [d.clause_number for d in drafts if d.clause_number]
    assert numbers == ["1", "2", "٣"]


def _page(*blocks):
    return ["\n".join(blocks)]


def _numbers(pages):
    from app.ingestion.clauses import split_clauses

    drafts, _ = split_clauses(pages)
    return [d.clause_number for d in drafts if d.clause_number]


def test_a_heading_followed_by_its_own_title_is_still_a_heading():
    body = "هذا نص المادة الذي يشرح الالتزام ويحدد المطلوب من المسؤول عن المعالجة بوضوح"
    blocks = []
    for n in range(1, 9):
        blocks += [f"المادة {n}", body]
    blocks[12:14] = ["المادة ٧ الولوج غير المشروع الى نظام", body]
    fixed, stats = repair_headings(_page(*blocks))
    assert stats.last == 8 and "المادة (7)" in fixed[0]


def test_a_chapter_title_is_not_an_article_and_the_promulgation_article_takes_no_number():
    body = "هذا نص المادة الذي يشرح الالتزام ويحدد المطلوب من المسؤول عن المعالجة بوضوح"
    blocks = ["المادة الوحيدة", body]
    for n in range(1, 8):
        blocks += [f"المادة {n}", body]
        if n == 3:
            blocks += ["الفصل الاول احكام عامة"]
    fixed, stats = repair_headings(_page(*blocks))
    assert stats.last == 7 and stats.found == 7


def test_an_article_bis_does_not_shift_the_numbers_after_it():
    body = "هذا نص المادة الذي يشرح الالتزام ويحدد المطلوب من المسؤول عن المعالجة بوضوح"
    blocks = []
    for n in range(1, 5):
        blocks += [f"المادة {n}", body]
    blocks += ["المادة 4 مكررة", body]
    for n in range(5, 9):
        blocks += [f"المادة {n}", body]
    fixed, stats = repair_headings(_page(*blocks))
    assert stats.last == 8 and stats.bis == 1 and "المادة (4-مكررة)" in fixed[0]
    assert "المادة (5)" in fixed[0] and "المادة (8)" in fixed[0]


def test_a_leading_one_lost_by_recognition_is_restored_when_it_fits_the_sequence():
    body = "هذا نص المادة الذي يشرح الالتزام ويحدد المطلوب من المسؤول عن المعالجة بوضوح"
    blocks = []
    for n in range(1, 111):
        blocks += [f"المادة {n}", body]
    blocks += ["المادة 11", body, "المادة 12", body]  # 111, 112 read without the leading 1
    blocks += ["المادة 13", body]
    fixed, stats = repair_headings(_page(*blocks))
    assert stats.last == 113 and "المادة (112)" in fixed[0]


def test_a_quoted_article_inside_an_amending_article_is_not_split_off():
    body = "هذا نص المادة الذي يشرح الالتزام ويحدد المطلوب من المسؤول عن المعالجة بوضوح"
    blocks = []
    for n in range(1, 41):
        blocks += [f"المادة {n}", body]
        if n == 30:
            blocks += [
                "المادة ٣٥ مكرر استبدال هذا النص",
                "تصوير القاصرين في المواد الاباحية يعاقب عليه",
            ]
    numbers = _numbers(repair_headings(_page(*blocks))[0])
    assert "(30)" in numbers and "(31)" in numbers
    assert not any(n.startswith("٣٥") for n in numbers)


def test_a_clean_reading_is_not_rewritten_but_an_isolated_misread_number_is_fixed():
    from app.ingestion.heading_sequence import repair_headings

    pages = [
        "\n".join(f"المادة {n} : نص المادة رقم {n} يحدد الالتزام بوضوح." for n in (136, 137, 138)),
        "\n".join(
            f"المادة {n} : نص المادة رقم {n} يحدد الالتزام بوضوح." for n in (139, 140, 1, 142, 143)
        ),
        "المادة 144 : نص المادة رقم 144 يحدد الالتزام بوضوح.",
    ]
    out, _ = repair_headings(pages)
    joined = "\n".join(out)
    assert "المادة 141 :" in joined and "المادة 1 :" not in joined  # 孤立读错的补成 141
    assert "نص المادة رقم 138 يحدد الالتزام بوضوح." in joined  # 其余原样，不被改写成“(N)”
    assert out[0] == pages[0] and out[2] == pages[2]


def test_headings_read_in_reverse_word_order_are_put_back():
    from app.ingestion.heading_sequence import fix_reversed_headings

    pages = [
        "\n".join(
            [
                ": (٥) مادة",
                "نص المادة الخامسة",
                "مادة (٦) :",
                ": (7) المادة",
                "إشارة إلى المادة (١٠) من هذا القانون",
            ]
        )
    ]
    out, fixed = fix_reversed_headings(pages)
    assert fixed == 2
    lines = out[0].splitlines()
    assert lines[0] == "مادة (٥)" and lines[3] == "المادة (7)"
    assert lines[2] == "مادة (٦) :" and lines[4].startswith("إشارة إلى")  # 其他行不动


def test_misread_article_numbers_are_repaired_from_the_count_and_unclear_ones_are_left():
    from app.ingestion.heading_sequence import repair_heading_sequence

    def page(*nums):
        return "\n".join(f"مادة ({n})\nنص المادة الذي يحدد الالتزام" for n in nums)

    # 1 2 2 4 5：第三个“2”其实是 3；  29 35 21 22 23 34：35、21、22、23 是 30–33
    pages = [page(1, 2, 2, 4, 5), page(28, 29, 35, 21, 22, 23, 34, 35)]
    fixed, count = repair_heading_sequence(pages)
    assert count == 5
    assert "مادة (3)" in fixed[0] and fixed[0].count("مادة (2)") == 1
    for n in (30, 31, 32, 33):
        assert f"مادة ({n})" in fixed[1]
    # 后面接不上的不改：10 之后是 40，没有任何一个能证明中间是什么
    untouched, count2 = repair_heading_sequence([page(8, 9, 10, 40, 41, 42)])
    assert count2 == 0 and "مادة (40)" in untouched[0]
