"""舆情分级：等级由规则表决定；每个“是”必须有原文句子；规模不明不凭空估数。"""

import pytest

from app.opinion.grading import HIGH, LOW, MEDIUM, Facts, grade, keep_supported


def ev(*keys):
    return {k: "原文中的一句话" for k in keys}


def test_nothing_triggering_is_low():
    assert grade(Facts()).level == LOW


@pytest.mark.parametrize(
    "facts,rule",
    [
        (Facts(outside_video_or_plates=True), "outside-video"),
        (Facts(sensitive_area=True), "sensitive-area"),
        (Facts(important_data=("充电网运行数据",)), "important-data"),
        (Facts(people=150_000), "people-over-100k"),
        (Facts(harm_happened=True), "harm"),
        (Facts(crosses_border=True, people=1_000_000), "security-assessment"),
        (Facts(crosses_border=True, sensitive_people=10_000), "security-assessment"),
    ],
)
def test_the_high_tier_rules(facts, rule):
    g = grade(facts)
    assert g.level == HIGH and rule in g.rules and g.bases


@pytest.mark.parametrize(
    "facts",
    [
        Facts(crosses_border=True, people=100_000),
        Facts(crosses_border=True, sensitive_people=9_999),
        Facts(precise_location_or_biometrics=True),
        Facts(policy_signal=True),
    ],
)
def test_the_medium_tier_rules(facts):
    assert grade(facts).level == MEDIUM


def test_the_boundaries_of_the_thresholds():
    assert grade(Facts(crosses_border=True, people=99_999)).level == LOW
    assert grade(Facts(crosses_border=True, people=100_000)).level == MEDIUM
    assert grade(Facts(crosses_border=True, people=1_000_000)).level == HIGH
    assert grade(Facts(people=100_000)).level == LOW  # 10 万人整不算“超过 10 万”
    # 两条规定同时适用时取更严的：出境 10 万–100 万人本属“标准合同或认证”档，
    # 但超过 10 万人的个人信息同时是汽车数据规定里的重要数据，所以为高
    assert grade(Facts(people=100_001)).level == HIGH
    assert grade(Facts(crosses_border=True, people=999_999)).level == HIGH


def test_the_highest_applicable_rule_wins_and_is_named():
    g = grade(Facts(policy_signal=True, outside_video_or_plates=True))
    assert g.level == HIGH and g.rules == ("outside-video",)


def test_an_unknown_head_count_is_reported_not_guessed():
    g = grade(Facts(crosses_border=True))
    assert g.level == LOW and g.scale_unknown


def test_a_claim_without_a_supporting_sentence_is_dropped():
    claimed = Facts(outside_video_or_plates=True, harm_happened=True, evidence=ev("harm_happened"))
    assert grade(keep_supported(claimed)).rules == ("harm",)
    assert grade(keep_supported(Facts(people=2_000_000, crosses_border=True))).level == LOW


def test_the_same_facts_always_give_the_same_grade():
    facts = Facts(crosses_border=True, people=500_000, evidence=ev("crosses_border", "people"))
    assert {grade(keep_supported(facts)).level for _ in range(5)} == {HIGH}


def test_the_split_is_fixed_by_the_address_and_roughly_four_to_six():
    from app.opinion.evaluate import split_of

    urls = [f"https://www.example.gov.cn/news/{i}.html" for i in range(1000)]
    first = [split_of(u) for u in urls]
    assert first == [split_of(u) for u in urls]  # 重新采集不会让条目换组
    dev = first.count("dev") / len(first)
    assert 0.35 < dev < 0.45


def test_metrics_report_recall_per_grade_and_on_the_agreed_items():
    from app.opinion.evaluate import metrics

    rows = (
        [{"final": "high", "predicted": "high", "agreed": True}] * 3
        + [{"final": "high", "predicted": "low", "agreed": False}]
        + [{"final": "low", "predicted": "low", "agreed": True}] * 6
    )
    m = metrics(rows)
    assert m["n"] == 10 and abs(m["accuracy"] - 0.9) < 1e-9
    assert m["recall"]["high"] == {"n": 4, "recall": 0.75}
    assert m["recall"]["low"]["recall"] == 1.0
    assert m["agreed_n"] == 9 and m["agreed_accuracy"] == 1.0
    assert m["confusion"]["high->low"] == 1


def test_a_policy_sentence_that_only_says_data_in_passing_is_not_a_data_policy_signal():
    from app.opinion.extract import facts_from_model

    body = "标题\n今年共实施汽车召回232次，召回数据同比增长近三成，监管部门发布了统计通报。"
    data = {
        "facts": {
            "policy_signal": {
                "value": True,
                "quote": "召回数据同比增长近三成，监管部门发布了统计通报。",
            }
        }
    }
    assert facts_from_model(data, body).policy_signal is False
    body2 = "标题\n监管部门发布《汽车数据安全管理若干规定》征求意见稿。"
    data2 = {
        "facts": {
            "policy_signal": {
                "value": True,
                "quote": "监管部门发布《汽车数据安全管理若干规定》征求意见稿。",
            }
        }
    }
    assert facts_from_model(data2, body2).policy_signal is True


def test_a_policy_mentioned_only_as_background_does_not_make_the_item_a_policy_item():
    from app.opinion.extract import facts_from_model

    sentence = "市场监管总局曾发布汽车远程升级（OTA）技术召回监管的通知，要求生产者备案。"
    body = "2021年召回乘用车873万辆" + chr(10) + "全年召回数据如下。" * 120 + sentence
    data = {"facts": {"policy_signal": {"value": True, "quote": sentence}}}
    assert facts_from_model(data, body).policy_signal is False
    # 标题就在讲这件事：算
    body2 = (
        "工信部发布智能网联汽车标准体系建设指南"
        + chr(10)
        + "为加快构建标准体系，现印发指南。"
        + sentence
    )
    data2 = {"facts": {"policy_signal": {"value": True, "quote": sentence}}}
    assert facts_from_model(data2, body2).policy_signal is True


def test_a_second_look_is_asked_only_when_faces_outside_the_car_are_mentioned_but_not_reported():
    from app.opinion.extract import _needs_second_look, merge_facts
    from app.opinion.grading import Facts

    text = "4项要求涵盖车外人脸信息等匿名化处理、默认不收集座舱数据。"
    assert _needs_second_look(text, Facts())
    assert not _needs_second_look(text, Facts(outside_video_or_plates=True))
    assert not _needs_second_look("座舱数据在车内处理。", Facts())
    merged = merge_facts(
        Facts(policy_signal=True, evidence={"policy_signal": "甲"}),
        Facts(outside_video_or_plates=True, evidence={"outside_video_or_plates": "乙"}),
    )
    assert merged.policy_signal and merged.outside_video_or_plates
    assert merged.evidence == {"policy_signal": "甲", "outside_video_or_plates": "乙"}
