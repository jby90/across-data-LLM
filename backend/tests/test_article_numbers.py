"""条号识别：三种写法（埃及序数词、突尼斯“الفصل + 数字”、摩洛哥法语）都要认，并且不被噪声带偏。"""

import pytest

from app.ingestion.article_numbers import (
    article_numbers,
    completeness,
    normalize_arabic,
)


def test_normalization_unifies_letter_variants_and_strips_marks():
    # 同一个词的不同写法规范成同一个形式
    # 带不带变音符、أ/ا 混用、ى/ي 混用，规范后都是同一个词
    assert normalize_arabic("الأولى") == normalize_arabic("الاولي") == "الاولي"
    assert normalize_arabic("الْأُولَى") == "الاولي"
    assert normalize_arabic("المـــادة") == normalize_arabic("المادة")  # 拉长符
    # 东阿拉伯数字转成西阿拉伯数字；“ة”统一成“ه”，所以规范后是“ماده”
    assert normalize_arabic("مادة ٥") == "ماده 5"
    assert normalize_arabic("الفصل ٢٤") == "الفصل 24"  # 数字不能被当成变音符号误删


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("المادة الأولى", 1),
        ("( المـادة الأولي )", 1),  # 书法字体的拉长符、括号、ي/ى 混用
        ("المادة الثانية", 2),
        ("المادة الثالثة", 3),
        ("المادة العاشرة", 10),
        ("المادة الحادية عشرة", 11),
        ("المادة الثانية عشرة", 12),
        ("المادة الحادية والعشرون", 21),
        ("المادة الخامسة والثلاثون", 35),
        ("مادة (٥)", 5),
        ("الفصل 17", 17),
        ("الفصل ٢٤ ينطبق", 24),
    ],
)
def test_arabic_article_headings_are_recognised(line, expected):
    assert expected in article_numbers(line + "\nنص المادة", "ar")


def test_ordinal_twelve_is_not_swallowed_as_two():
    found = article_numbers("المادة الثانية عشرة\nنص", "ar")
    assert 12 in found and 2 not in found


def test_a_reference_inside_a_sentence_is_not_a_heading():
    # 句中引用别的条款不是条文标题
    assert article_numbers("وذلك وفقا لأحكام المادة 5 من هذا القانون", "ar") == set()


def test_french_articles():
    text = "Article premier\nblabla\nArt. 2 : texte\nArticle 15\nsuite"
    assert article_numbers(text, "fr") == {1, 2, 15}


def test_unsupported_languages_return_nothing():
    assert article_numbers("Section 5.", "en") == set()


def test_completeness_reports_gaps():
    pages = ["الفصل 1 نص", "الفصل 2 نص", "الفصل 4 نص", "الفصل 5 نص"]
    result = completeness(pages, "ar")
    assert result["found"] == 4 and result["expected"] == 5 and result["missing"] == [3]


def test_a_stray_large_number_does_not_inflate_the_denominator():
    # 页码或年份被当成条号时（254），不能把“应有条数”撑大，让完整度显得惨淡
    pages = [f"الفصل {n} نص" for n in range(1, 11)] + ["الفصل 254 نص"]
    result = completeness(pages, "ar")
    assert result["expected"] == 10 and result["missing"] == [] and result["ratio"] == 1.0


def test_expected_total_exposes_missing_tail():
    # 读到第 10 条但官方说共 20 条：不传总数会误以为完整
    pages = [f"الفصل {n} نص" for n in range(1, 11)]
    assert completeness(pages, "ar")["ratio"] == 1.0
    assert completeness(pages, "ar", expected_max=20)["missing"] == list(range(11, 21))


def test_with_the_official_total_a_sparse_but_real_heading_is_not_thrown_away():
    # 条标题偶尔才读得出来：前面读到 1-9，后面零星读到 35、39、43、49，它们都是真条号，不能当噪声剔除
    pages = [f"الفصل {n} نص" for n in (1, 2, 3, 4, 5, 6, 7, 9, 35, 39, 43, 49)]
    result = completeness(pages, "ar", expected_max=49)
    assert result["found"] == 12 and result["expected"] == 49
    assert 35 in {n for n in range(1, 50)} - set(result["missing"])


def test_numbers_above_the_official_total_and_zero_are_dropped():
    pages = ["الفصل 0 نص", "الفصل 3 نص", "الفصل 254 نص"]  # 0 不是条号；254 超过总条数（页码之类）
    result = completeness(pages, "ar", expected_max=49)
    assert result["found"] == 1 and 3 not in result["missing"] and 254 not in result["missing"]


def test_heading_with_a_stretched_letter_is_still_a_heading():
    # 埃及法颁布条款的第一条，标题被读成“المسادة الأولى”（多了一个“س”）
    assert 1 in article_numbers("المسادة الأولى\nيعمل بأحكام هذا القانون", "ar")
    assert 2 in article_numbers("المسادة الثانية\nنص", "ar")
