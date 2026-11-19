"""核验规则按“该翻的内容”判定：不翻的东西不拖累占比，数字密集的块切得更短。"""

import pytest

from app.knowledge.translator import (
    split_clause_into_chunks,
    translatable_cjk_ratio,
    verify_translation,
)


@pytest.mark.parametrize(
    "original,translation",
    [
        (
            "Contact the Commission: +212 5 37 57 11 24, contact@cndp.ma, www.cndp.ma. "
            "The office is located at Avenue Al Arz, Secteur 4, Hay Riad, Rabat Maroc.",
            "联系委员会：+212 5 37 57 11 24，contact@cndp.ma，www.cndp.ma。办公地点位于摩洛哥拉巴特海伊里亚德区4区阿尔兹大道。",
        ),
        (
            "TLP: AMBER. The web user interface controls for the GDPR and the CII scheme apply to every "
            "organisation operating a designated system 12 times a year.",
            "TLP: AMBER。针对 GDPR 与 CII 方案的网页界面控制措施适用于每年运行指定系统 12 次的所有组织。",
        ),
    ],
)
def test_urls_emails_acronyms_and_figures_do_not_count_against_the_chinese_share(
    original, translation
):
    passed, issues, _ = verify_translation(original, translation, [])
    assert not any("CJK" in i for i in issues), issues


def test_a_short_fragment_keeps_the_strict_share_so_it_cannot_be_padded():
    # a table cell or a heading with figures: no exemption, however faithful
    source, faithful = "Amount\n1. 5,000 $100", "金额\n1. 5,000 $100"
    assert any("CJK" in i for i in verify_translation(source, faithful, [])[1])


def test_a_translation_left_in_the_source_language_still_fails():
    passed, issues, _ = verify_translation(
        "The organisation must notify the Commission of a data breach without delay.",
        "The organisation must notify the Commission 通报 of a data breach without delay.",
        [],
    )
    assert any("CJK" in i for i in issues)


def test_a_term_that_must_keep_its_original_wording_is_not_counted_as_untranslated():
    hit = {
        "source_term": "Public\nTelecommunications Services",
        "target_zh": "Public Telecommunications Services",
        "preserve_original": True,
    }
    passed, issues, hits = verify_translation(
        "Public\nTelecommunications Services",
        "Public Telecommunications Services（公共电信服务）",
        [hit],
    )
    assert hits[0]["matched_in_translation"]
    assert not any("术语" in i for i in issues), issues


def test_nothing_translatable_means_the_ratio_is_not_a_failure():
    assert translatable_cjk_ratio("12 34 www.example.org GDPR") == 1.0


def test_number_dense_text_is_cut_into_small_blocks():
    codes = " ".join(str(3926908100 + i) for i in range(120))
    chunks = split_clause_into_chunks(codes)
    assert len(chunks) >= 4
    assert " ".join(chunks).split() == codes.split()  # 不丢不改
    assert all(len(c.split()) <= 30 for c in chunks)


def test_ordinary_long_text_is_still_cut_by_length_only():
    text = " ".join(
        f"Sentence number {i} says something plain about the scheme." for i in range(80)
    )
    chunks = split_clause_into_chunks(text)
    assert all(len(c) <= 1500 for c in chunks) and len(chunks) >= 3


def test_a_version_number_before_a_month_is_not_a_date_and_missing_digits_are_still_caught():
    from app.knowledge.numeric_integrity import dates

    assert dates("Version 1.0 October 2020 Cyber Security Agency") == set()
    assert dates("31 February 2020") == set()
    assert dates("On 19 December 2022 it enters into force.") == {(2022, 12, 19)}
    passed, issues, _ = verify_translation(
        "Version 1.0 October 2020 Cyber Security Agency of Singapore",
        "2020年10月 1.0 版 新加坡网络安全局",
        [],
    )
    assert not any("日期" in i for i in issues)


def test_a_number_the_model_dropped_is_still_refused_even_without_the_placeholder_rule():
    passed, issues, _ = verify_translation(
        "The fee is 500 dirhams for each application filed with the Authority.",
        "每份提交给管理局的申请均须缴纳费用。",
        [],
    )
    assert not passed and any("500" in i for i in issues)
