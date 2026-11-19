"""否定词一致性校验：要能抓到真错误，也不能误伤正确译文。"""

import pytest

from app.knowledge.modality import modality_issues


@pytest.mark.parametrize(
    ("original", "translated"),
    [
        # 英语：禁止性规定被译丢了否定
        (
            "An organisation must not disclose personal data without consent.",
            "组织应当在取得同意的情况下披露个人数据。",
        ),
        (
            "No person shall transfer the data outside Singapore.",
            "任何人可以将数据传输至新加坡境外。",
        ),
        # 法语
        ("Il est interdit de traiter des données sensibles.", "可以处理敏感数据。"),
        ("Les données ne doivent pas être conservées.", "数据应当保存。"),
        # 阿拉伯语（含文本层把“لا”存成“ال”的写法）
        ("لا يجوز نقل البيانات خارج الدولة.", "可以将数据转移至国外。"),
        ("ال يجوز نقل البيانات خارج الدولة.", "可以将数据转移至国外。"),
        # 凭空译出禁止
        (
            "The organisation shall appoint a data protection officer.",
            "组织不得任命数据保护负责人。",
        ),
    ],
)
def test_real_negation_errors_are_caught(original, translated):
    assert modality_issues(original, translated), (original, translated)


@pytest.mark.parametrize(
    ("original", "translated"),
    [
        (
            "An organisation must not disclose personal data without consent.",
            "未经同意，组织不得披露个人数据。",
        ),
        (
            "No person shall transfer the data outside Singapore.",
            "任何人不得将数据传输至新加坡境外。",
        ),
        ("Il est interdit de traiter des données sensibles.", "禁止处理敏感数据。"),
        # 法语 ne … que 是“仅”，不是禁止
        (
            "Les données ne peuvent être traitées que pour des finalités licites.",
            "数据仅可为合法目的进行处理。",
        ),
        # 短句法语也要认得出
        ("On ne peut préalablement renoncer au droit d’accès.", "不得事先放弃访问权。"),
        ("لا يجوز نقل البيانات خارج الدولة.", "不得将数据转移至国外。"),
        ("ال يجوز نقل البيانات خارج الدولة.", "不得将数据转移至国外。"),
        # “不可抗力”“不准确”是描述性用法
        ("Liability is excluded in cases of force majeure.", "因不可抗力免除责任。"),
        (
            "Where the data is inaccurate, the organisation shall correct it.",
            "数据不准确的，组织应当更正。",
        ),
        (
            "The organisation shall not be required to disclose the information.",
            "组织无需披露该信息。",
        ),
        # 陈述性 shall 译成“有权”不算错
        (
            "A District Court shall have jurisdiction to try any offence.",
            "地区法院有权审理任何罪行。",
        ),
        # 空文本、未覆盖语言不检查
        ("", "任何内容"),
        ("这是中文原文，不应被检查。", "不得"),
    ],
)
def test_correct_translations_are_not_flagged(original, translated):
    assert modality_issues(original, translated) == [], (original, translated)


def test_verify_translation_rejects_lost_prohibition_so_it_gets_retried():
    from app.knowledge.translator import verify_translation

    original = "No person shall transfer the personal data outside Singapore."
    ok, issues, _ = verify_translation(original, "任何人可以将个人数据传输至新加坡境外。", [])
    assert not ok and any("否定词" in i for i in issues)
    ok, issues, _ = verify_translation(original, "任何人不得将个人数据传输至新加坡境外。", [])
    assert ok, issues


def test_arabic_prohibition_written_with_vowel_marks_is_still_recognised():
    from app.knowledge.modality import modality_issues

    original = "المادة 65 ُ يحظّر ُ على أي كان أن يلحق أي ضرر بإشارات السير أو يلصق عليها إعلانات."
    assert modality_issues(original, "任何人均不得对交通标志造成任何损害，或在其上粘贴公告。") == []
    # 译文写了禁止、原文确实没有禁止/否定：仍然要报
    plain = "المادة 66 على السائق أن يشغل المصابيح عند الليل."
    assert modality_issues(plain, "驾驶员不得在夜间开灯。")
