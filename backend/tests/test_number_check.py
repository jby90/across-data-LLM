"""简报、合规文件里的数字核对：换了写法的同一个数要对得上，条文里没有的数仍然挡住。"""

import pytest

from app.analysis.document_narrative import _check_paragraph, _numbers, source_numbers


@pytest.mark.parametrize(
    "written, law",
    [
        ("罚款 100 万埃及镑", "a fine of EGP 1,000,000"),
        ("罚款 100 万埃及镑", "غرامة لا تقل عن مليون جنيه"),
        ("罚款 20 万至 200 万埃及镑", "not less than two hundred thousand pounds and not exceeding two million pounds"),
        ("须在 90 天内通知", "within ninety days"),
        ("须在 90 天内通知", "dans un délai de quatre-vingt-dix jours"),
        ("须在 90 天内通知", "خلال تسعين يوما"),
        ("须在 90 天内通知", "خلال ٩٠ يوما"),
        ("罚款 100 万埃及镑", "处以一百万埃及镑罚款"),
        ("期限 30 日", "期限为三十日"),
        ("罚款 150 万第纳尔", "une amende de 1,5 million de dinars"),
        ("罚款 50000 第纳尔", "amende de 50 000 dinars"),
        ("罚款 5 万第纳尔", "amende de cinquante mille dinars"),
        ("罚款 300 万", "ثلاثة ملايين"),
        ("罚款 500 万", "خمسة ملايين جنيه"),
    ],
)
def test_the_same_figure_written_another_way_is_found_in_the_law(written, law):
    assert not (_numbers(written) - source_numbers(law)), (_numbers(written), source_numbers(law))


def test_a_figure_with_a_magnitude_counts_only_as_its_full_value():
    assert _numbers("100 万") == {"1000000"}
    assert _numbers("3 千米") == {"3"}  # 千米是长度单位，不是量级
    assert _numbers("1 000 000") == {"1000000"}


def test_a_figure_the_law_does_not_have_is_still_stopped():
    offered = {1: {"document": "法", "clause": "第 1 条", "text": "罚款 1,000,000 埃镑"}}
    ok, why = _check_paragraph({"text": "罚款 200 万埃及镑", "cites": [1]}, offered, source_numbers(offered[1]["text"]))
    assert ok is None and "200 万" in why
    ok, _ = _check_paragraph({"text": "罚款 100 万埃及镑", "cites": [1]}, offered, source_numbers(offered[1]["text"]))
    assert ok is not None


@pytest.mark.parametrize(
    "written, law",
    [
        ("2001年4月14日法令第9条", "Décret n° 2001-830 du 14 avril 2001"),
        ("2004年7月27日组织法第8条", "قانون أساسي عدد 63 لسنة 2004 مؤرخ في 27 جويلية 2004"),
        ("1998年9月21日", "le 21 septembre 1998"),
        ("2001年4月14日", "Decree of April 14, 2001"),
        ("2018年10月10日", "قانون رقم 81 تاريخ 10/10/2018"),
        ("2020年1月1日", "le 1er janvier 2020"),
        ("2019年3月", "mars 2019"),
        ("2012年10月22日", "٢٢ تشرين الأول ٢٠١٢"),
    ],
)
def test_a_date_in_any_language_of_the_law_is_matched_as_a_whole(written, law):
    # 所引条文带着条号（“第 9 条”），法规名里带着日期
    offered = {1: {"document": law, "clause": "第 9 条 第 8 条", "text": "", "original": law}}
    ok, why = _check_paragraph({"text": f"依据{written}的规定。", "cites": [1]}, offered, source_numbers(law + " 第 9 条 第 8 条"))
    assert ok is not None, why


def test_a_wrong_date_is_stopped_and_the_reason_names_it():
    law = "Décret n° 2001-830 du 14 avril 2001"
    offered = {1: {"document": law, "clause": "", "text": "", "original": law}}
    ok, why = _check_paragraph({"text": "依据2001年5月14日法令。", "cites": [1]}, offered, source_numbers(law))
    assert ok is None and why == "“2001年5月14日”在所引条文里没找到"


def test_a_missing_figure_is_named_as_written():
    offered = {1: {"document": "法", "clause": "第 1 条", "text": "罚款 1,000,000 埃镑"}}
    ok, why = _check_paragraph({"text": "罚款 200 万埃及镑", "cites": [1]}, offered, source_numbers(offered[1]["text"]))
    assert ok is None and why == "“200 万埃及镑”在所引条文里没找到"
