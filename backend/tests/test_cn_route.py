"""中国出境路径判断：只用企业填的答案；缺什么就说缺什么，并给出对应题目的 key 以便跳过去回答。"""

from app.analysis.cn_route import judge

BASE = {"is_cii": "否", "important_data": "否"}


def test_unanswered_lists_labels_and_keys():
    r = judge({})
    assert r["route"] == "unknown"
    assert r["missing_keys"] == ["is_cii", "important_data", "sensitive_pi", "data_volume"]
    assert len(r["missing"]) == len(r["missing_keys"])


def test_sensitive_yes_without_count_points_back_to_sensitive_question():
    r = judge({**BASE, "sensitive_pi": "是", "data_volume": "约 20 万人"})
    assert r["route"] == "unknown"
    assert r["missing"] == ["敏感个人信息涉及的人数"]
    assert r["missing_keys"] == ["sensitive_pi"]


def test_sensitive_count_written_after_yes_decides_route():
    r = judge({**BASE, "sensitive_pi": "是，约 2 万人", "data_volume": "约 20 万人"})
    assert r["route"] == "security_assessment"
    assert r["docs"]["declaration"]["applies"] is True
    r = judge({**BASE, "sensitive_pi": "是，约 5000 人", "data_volume": "约 20 万人"})
    assert r["route"] == "standard_contract"


def test_small_total_bounds_sensitive_count_without_asking():
    # 出境总共不足 1 万人：敏感个人信息必然不足 1 万人，不再追问人数
    r = judge({**BASE, "sensitive_pi": "是", "data_volume": "约 3000 人"})
    assert r["route"] == "standard_contract"
    assert r["missing"] == []


def test_exempt_route_marks_declaration_not_needed():
    r = judge({**BASE, "sensitive_pi": "否", "data_volume": "约 3 万人"})
    assert r["route"] == "exempt"
    assert r["docs"]["declaration"]["applies"] is False
    assert r["docs"]["contract"]["applies"] is True
