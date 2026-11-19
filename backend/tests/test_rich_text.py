"""加粗标记：解析规则、安全性、对模型文字的约束。纯函数测试，不依赖数据库。"""

import pytest

from app.analysis import consulting as c
from app.analysis.rich_text import (
    MAX_BOLD_RATIO,
    bold_problems,
    bold_ratio,
    limit_bold,
    parse_bold,
    same_text,
    strip_bold,
)


def test_parse_bold_splits_segments():
    assert parse_bold("先**签协议**再上线") == [("先", False), ("签协议", True), ("再上线", False)]
    assert parse_bold("没有标记") == [("没有标记", False)]
    assert parse_bold("") == []
    assert parse_bold("**整句**") == [("整句", True)]


@pytest.mark.parametrize(
    "text",
    ["孤立的 ** 星号", "**没闭合", "闭合了**", "****", "** **", "a**b**c**d", "**a**b**"],
)
def test_parse_never_leaves_markers_and_never_bolds_by_accident(text):
    segments = parse_bold(text)
    assert all("**" not in seg for seg, _ in segments)
    # 未配对的标记不能让后面整段变粗
    if text == "**没闭合":
        assert segments == [("没闭合", False)]
    if text == "孤立的 ** 星号":
        assert not any(bold for _, bold in segments)


def test_markup_inside_bold_stays_text():
    assert parse_bold("**<b>x</b>**") == [("<b>x</b>", True)]
    assert strip_bold("**<b>x</b>** y") == "<b>x</b> y"


def test_same_text_ignores_bold_markers_and_edges():
    assert same_text("先**签协议**", " 先签协议 ")
    assert same_text(None, "")
    assert not same_text("先签协议", "先签合同")


def test_bold_ratio_and_rules():
    assert bold_ratio("**ab**cdefgh") == pytest.approx(0.25)  # 2 / 8
    assert bold_ratio("") == 0.0
    assert bold_problems("结论", "**只加粗一小段** 其余都是正常文字，占比很低，没有问题") == []
    assert any("成对" in p for p in bold_problems("结论", "**没闭合 的标记"))
    # 占比过高不再作为打回理由，而是由 limit_bold 收敛
    assert bold_problems("结论", "**整句话都加粗了所以没有重点**") == []


def test_limit_bold_only_touches_markers_never_words():
    text = (
        "**第一处重点内容**，普通文字很多很多很多很多很多很多很多，**第二处重点内容较长较长较长**"
    )
    out = limit_bold(text)
    assert strip_bold(out) == strip_bold(text)  # 文字一个字都不改
    assert bold_ratio(out) <= MAX_BOLD_RATIO
    assert out.startswith("**第一处重点内容**")  # 按出现顺序保留靠前的
    assert "**第二处" not in out
    # 本来就合规的原样保留
    ok = "先**签协议**再上线，其余都是普通文字，不需要任何收敛，也没有别的重点内容"
    assert limit_bold(ok) == ok
    assert limit_bold("") == ""
    # 单个超长加粗放不进预算时整个取消，仍然不改文字
    assert limit_bold("**整句话都加粗了**") == "整句话都加粗了"


def test_text_problems_strip_bold_before_fact_checks():
    corpus = "最迟 3 个日历日通知"
    # 加粗不能用来藏数字或绕过照抄检查
    ok = (
        "发现泄露并评估为需要上报之后，必须在 **3 个日历日** 内通知监管机构，并同步通知受影响的个人"
    )
    assert c.text_problems("结论", ok, corpus, []) == []
    bad = c.text_problems(
        "结论",
        "发现泄露之后，影响 **500 人** 以上时必须通知监管机构，并同步通知受影响的个人",
        corpus,
        [],
    )
    assert any("500" in p for p in bad)
    src = "接收方须受有约束力的义务约束并提供不低于本法的保护水平，否则不得传输"
    assert any("照抄" in p for p in c.text_problems("结论", f"**{src}**", corpus, [src]))


@pytest.mark.parametrize(
    "leak", ["见 D3 的结论", "按 F-01 处理", "先做 A-02", "这是 evidence_id", "原子规则要求"]
)
def test_internal_codes_and_system_words_are_rejected(leak):
    problems = c.text_problems("结论", leak, "", [])
    assert any("内部编号或系统词" in p for p in problems)


def test_plain_words_that_look_like_codes_are_not_flagged():
    # D 或 A 在更长的词里、或后面不是编号形式时不算内部编号
    assert c.text_problems("结论", "ADD 规范与 DATA 清单无关", "", []) == []
    assert c.text_problems("结论", "D 公司", "", []) == []
    assert c.text_problems("结论", "A 公司和 D 公司", "", []) == []
