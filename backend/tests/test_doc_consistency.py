"""第 4 条：平台检查结果挂在题目上（题对题、与官方表格）。"""

from app.analysis import doc_consistency as C

LABELS = {"pi_scale": "处理个人信息规模", "data_volume": "涉及自然人数量", "sensitive_pi": "是否含敏感个人信息",
          "data_subjects": "涉及的人群与规模", "retention_and_blackbox": "保存期限与存放位置",
          "legal_doc_terms": "法律文件约定的责任义务", "recipient": "境外接收方", "cross_border_mechanism": "接收方与传输保障机制"}


def keys(issues):
    return sorted((i["rule"], i["key"]) for i in issues)


def test_count_reads_one_number_of_people_only():
    assert C._count("约 5 万人") == 50_000
    assert C._count("2024 年起累计约 800 人") == 800
    assert C._count("车主约 5 万人、乘客约 10 万人") is None  # 两个不同的数，读不准
    assert C._count("保存 3 年") is None
    assert C._count("人脸数据 3 类") is None
    assert C._count("暂不确定") is None


def test_volume_more_than_scale_flags_both_questions():
    issues = C.between_questions({"data_volume": "约 5 万人", "pi_scale": "约 3 万人"}, LABELS, "埃及")
    assert keys(issues) == [("volume_scale", "data_volume"), ("volume_scale", "pi_scale")]
    assert "约 5 万人" in issues[0]["text"] and "处理个人信息规模" in issues[0]["text"]
    assert issues[0]["to"] == {"kind": "question", "key": "pi_scale"}
    assert len(C.summary(issues)) == 1  # 一对题算一处


def test_bounds_and_unclear_are_not_flagged():
    assert C.between_questions({"data_volume": "约 5 万人", "pi_scale": "100 万人以上"}, LABELS, "") == []
    assert C.between_questions({"data_volume": "10 万人以下", "pi_scale": "约 3 万人"}, LABELS, "") == []
    assert C.between_questions({"data_volume": "约 3 万人", "pi_scale": "约 5 万人"}, LABELS, "") == []
    assert C.between_questions({"data_volume": "当年 2 万人，未来三年约 10 万人", "pi_scale": "约 3 万人"}, LABELS, "") == []


def test_sensitive_more_than_volume():
    issues = C.between_questions({"sensitive_pi": "是：约 2 万人", "data_volume": "约 8000 人"}, LABELS, "")
    assert keys(issues) == [("sensitive_volume", "data_volume"), ("sensitive_volume", "sensitive_pi")]
    assert C.between_questions({"sensitive_pi": "否", "data_volume": "约 8000 人"}, LABELS, "") == []


def test_volume_more_than_subjects():
    issues = C.between_questions({"data_volume": "约 5 万人", "data_subjects": "车主，约 2 万人"}, LABELS, "")
    assert keys(issues) == [("volume_subjects", "data_subjects"), ("volume_subjects", "data_volume")]


def test_retention_periods():
    v = {"retention_and_blackbox": "数据保存 3 年，存放在开罗机房，到期删除", "legal_doc_terms": "合同约定接收方保存期限为 5 年，期满删除"}
    issues = C.between_questions(v, LABELS, "")
    assert keys(issues) == [("retention", "legal_doc_terms"), ("retention", "retention_and_blackbox")]
    same = {"retention_and_blackbox": "保存三年", "legal_doc_terms": "保存期限为 3 年"}
    assert C.between_questions(same, LABELS, "") == []
    several = {"retention_and_blackbox": "原始视频保存 30 天，脱敏数据保存 3 年", "legal_doc_terms": "保存 5 年"}
    assert C.between_questions(several, LABELS, "") == []


def test_recipient_country_and_name():
    v = {"recipient": "新加坡星联科技有限公司，位于新加坡", "cross_border_mechanism": "与埃及开罗车联网服务有限公司签订标准合同"}
    issues = C.between_questions(v, LABELS, "埃及")
    rules = keys(issues)
    assert ("recipient_country", "recipient") in rules and ("recipient_country", "cross_border_mechanism") not in rules
    assert ("recipient_name", "recipient") in rules
    ok = {"recipient": "开罗车联网服务有限公司，埃及开罗", "cross_border_mechanism": "与开罗车联网服务有限公司签订标准合同"}
    assert C.between_questions(ok, LABELS, "埃及") == []
    # 写到“该公司”、或名称读不出来，不比
    vague = {"recipient": "开罗车联网服务有限公司", "cross_border_mechanism": "与该公司签订标准合同"}
    assert C.between_questions(vague, LABELS, "埃及") == []


def test_forms_only_flag_user_changes_that_differ():
    forms = [{"code": "X", "instance_id": "i1", "name": "某表", "items": [
        {"id": "a", "label": "申请主体名称", "fact": "company_name", "key": "company_name", "given": "乙公司", "project": "甲汽车有限公司"},
        {"id": "b", "label": "人数", "fact": "data_volume", "key": "data_volume", "given": "约50000人", "project": "约 5 万人"},
        {"id": "c", "label": "Raison sociale", "fact": "company_name", "key": "company_name", "given": "Société Jia", "project": "甲汽车有限公司"},
        {"id": "d", "label": "名称", "fact": "company_name", "key": "company_name", "given": "甲汽车有限公司（中国）", "project": "甲汽车有限公司"},
    ]}]
    issues = C.with_forms(forms)
    assert [i["to"]["item"] for i in issues] == ["a"]
    assert issues[0]["level"] == C.MUST and "《某表》" in issues[0]["text"]


def test_several_numbers_count_conservatively():
    v = {"data_volume": "当年已传 8 万人，未来三年约 20 万人", "pi_scale": "约 5 万人"}
    assert keys(C.between_questions(v, LABELS, "")) == [("volume_scale", "data_volume"), ("volume_scale", "pi_scale")]
    # 涉及的人群写了几类：加起来比，不误报
    v = {"data_volume": "约 6 万人", "data_subjects": "车主约 5 万人、乘客约 3 万人"}
    assert C.between_questions(v, LABELS, "") == []
    assert C._count("约 3 万人次") is None


def test_project_guide_uses_the_same_words():
    from app.analysis import project_guide as G
    issues = C.between_questions({"data_volume": "约 5 万人", "pi_scale": "约 3 万人"}, LABELS, "")
    view = {"title": "t", "route": {}, "documents": [], "applicability": {},
            "questions": [{"key": k, "label": v, "hint": "", "used_in": [], "value": ""} for k, v in LABELS.items()],
            "final": {"ok": False, "skipped": [], "issues": C.summary(issues)}}
    assert G._project_view(view)["issues"] == [f"涉及自然人数量：{issues[0]['text']}"]
    assert "有 1 处前后不一致" in G.fallback(view)
