"""用户 11.10 定：平台把要填的内容叫“填写项”，不用“题”。助手回复里漏出的“题”换成“项”，别的词不动。"""

from app.analysis.wording import items_not_questions as fix


def test_question_words_in_a_reply_become_items():
    assert fix("这一题问的是保存期限，跟上一题不一样。") == "这一项问的是保存期限，跟上一项不一样。"
    assert fix("本题可以写“无”；每道题都有示例。") == "这一项可以写“无”；每一项都有示例。"
    assert fix("按题目选项里的否定项写。") == "按填写项选项里的否定项写。"


def test_other_words_with_the_character_are_kept():
    text = "这个问题的标题和主题不变，题材也不变。"
    assert fix(text) == text
