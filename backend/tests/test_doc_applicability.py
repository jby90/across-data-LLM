"""8 份文件的适用性判断：任意发出国 → 接收国都给出“需要 / 按目前情况不需要 / 还不能判断”，原因引用企业的回答；
依据取自发出国的法规；拿不准时判“需要”，只有企业的回答明确满足条件才判“不需要”。"""

from app.analysis import cn_route
from app.analysis.doc_applicability import NA, NEED, UNKNOWN, Ground, judge

CODES = {"declaration", "risk_report", "contract", "remediation", "admission", "dpia", "lia", "tia"}
LABELS = {"data_volume": "涉及自然人数量", "lawful_basis": "处理的合法性基础", "issues": "发现的合规问题", "entry_type": "准入事项"}
# 某个已收录国家的已核验要求（国家、条号都是虚构的：规则不认国家）
GROUNDS = {
    "approval": Ground("跨境传输个人数据须获主管机构授权", "《个人数据法》第12条", True),
    "contract": Ground("合同须要求接收方提供同等保护", "《个人数据条例》第3条", True),
    "adequacy": Ground("接收国须提供充分保护水平", "《个人数据法》第11条", True),
    "transfer": Ground("接收国须提供充分保护水平", "《个人数据法》第11条", True),
    "dpia": None,
    "legit": Ground("以合法利益为依据须评估", "《指南》第4条", True),
    "approval_personal_only": True,
    "organized": True,
}


def run(values=None, *, source="甲国", recipient="乙国", grounds=GROUNDS, in_library=True, cn=None, admission=(), known=True):
    return judge(
        source=source, recipient=recipient, source_in_library=in_library, values=values or {}, labels=LABELS,
        grounds=grounds if in_library else None, cn=cn, overseas=recipient, overseas_known=known,
        admission=list(admission), function_zh="车联网上云",
    )


def test_every_document_gets_one_of_three_states_with_a_reason():
    for kwargs in ({}, {"in_library": False}, {"grounds": {k: None for k in GROUNDS} | {"organized": True}}):
        r = run(**kwargs)
        assert set(r) == CODES
        for d in r.values():
            assert d["state"] in (NEED, NA, UNKNOWN)
            assert d["why"].strip()
            assert d["label"] in ("需要", "按目前情况不需要", "还不能判断")


def test_a_country_not_in_the_library_is_needed_by_the_general_rule_never_left_blank():
    r = run(in_library=False, source="丙国")
    for code in ("declaration", "risk_report", "contract", "dpia", "tia"):
        assert r[code]["state"] == NEED
        assert "按各国通行的要求" in r[code]["why"] and "丙国" in r[code]["why"]


def test_the_grounds_come_from_the_sending_countrys_law_with_the_article():
    r = run()
    assert r["declaration"]["state"] == NEED
    assert r["declaration"]["basis"] == "《个人数据法》第12条"
    assert "甲国" in r["declaration"]["why"]
    assert r["contract"]["basis"] == "《个人数据条例》第3条"
    assert r["tia"]["basis"] == "《个人数据法》第11条"


def test_a_requirement_the_library_does_not_show_is_never_read_as_an_exemption():
    nothing = {k: None for k in GROUNDS} | {"approval_personal_only": False, "organized": True}
    r = run(grounds=nothing)
    assert r["declaration"]["state"] == NEED
    assert r["contract"]["state"] == NEED
    assert r["tia"]["state"] == NEED


def test_no_personal_data_at_all_is_the_answer_that_makes_the_personal_data_documents_unneeded():
    r = run({"data_volume": "0", "sensitive_pi": "否"})
    for code in ("dpia", "lia", "tia"):
        assert r[code]["state"] == NA
        assert "涉及自然人数量：0" in r[code]["why"]
    # 发出国报批的都是个人数据出境：不涉及个人信息时申报也不需要
    assert r["declaration"]["state"] == NA
    # 风险评估与合同照旧需要（拿不准判需要）
    assert r["risk_report"]["state"] == NEED and r["contract"]["state"] == NEED


def test_when_the_sending_country_also_restricts_non_personal_data_no_personal_data_does_not_exempt_the_declaration():
    r = run({"data_volume": "不涉及个人信息"}, grounds={**GROUNDS, "approval_personal_only": False})
    assert r["declaration"]["state"] == NEED


def test_sensitive_data_answered_yes_keeps_the_personal_data_documents_needed_and_quotes_the_answer():
    r = run({"data_volume": "0", "sensitive_pi": "是，约 2000 人"})
    assert r["dpia"]["state"] == NEED
    assert "是，约 2000 人" in r["dpia"]["why"]


def test_lia_depends_on_the_lawful_basis_and_can_jump_to_the_question():
    r = run()
    assert r["lia"]["state"] == UNKNOWN and r["lia"]["missing_keys"] == ["lawful_basis"]
    assert r["lia"]["missing"] == ["处理的合法性基础"]
    r = run({"lawful_basis": "合法利益：车辆安全监测"})
    assert r["lia"]["state"] == NEED and r["lia"]["basis"] == "《指南》第4条"
    r = run({"lawful_basis": "取得同意"})
    assert r["lia"]["state"] == NA and "取得同意" in r["lia"]["why"]
    # 认不出写的是什么依据：拿不准，判需要
    r = run({"lawful_basis": "按公司内部规定"})
    assert r["lia"]["state"] == NEED


def test_remediation_needs_an_issue_list_and_none_found_is_not_needed():
    assert run()["remediation"]["state"] == UNKNOWN
    assert run()["remediation"]["missing_keys"] == ["issues"]
    r = run({"issues": "无"})
    assert r["remediation"]["state"] == NA
    r = run({"issues": "1. 原始图像未脱敏上传 2. 未告知路人"})
    assert r["remediation"]["state"] == NEED and "2 条" in r["remediation"]["why"]


def test_admission_follows_the_items_the_library_holds_for_the_overseas_end():
    r = run(admission=["电信设备型式批准", "个人数据处理申报"])
    assert r["admission"]["state"] == NEED and "2 项" in r["admission"]["why"]
    # 库里有须办理的事项时，回答“无”也不改判
    assert run({"entry_type": "无"}, admission=["电信设备型式批准"])["admission"]["state"] == NEED
    assert run({"entry_type": "无"})["admission"]["state"] == NA
    assert run()["admission"]["state"] == UNKNOWN
    assert run(known=False)["admission"]["state"] == NEED


def test_china_as_sending_country_keeps_the_route_states():
    unknown = cn_route.judge({})
    r = run(source="中国", in_library=True, grounds=None, cn=unknown)
    assert r["declaration"]["state"] == UNKNOWN
    assert r["declaration"]["missing_keys"] == ["is_cii", "important_data", "sensitive_pi", "data_volume"]
    # 路径没定：申报件不出两套，合同和评估报告也等路径定了再判断
    assert r["contract"]["state"] == UNKNOWN and r["risk_report"]["state"] == UNKNOWN
    exempt = cn_route.judge({"is_cii": "否", "important_data": "否", "sensitive_pi": "否", "data_volume": "约 3 万人"})
    r = run({"data_volume": "约 3 万人", "sensitive_pi": "否"}, source="中国", grounds=None, cn=exempt)
    assert r["declaration"]["state"] == NA
    assert "约 30,000 人" in r["declaration"]["why"]  # 原因引用企业的回答
    assert r["declaration"]["basis"].endswith("第五条")
    # 免于申报：风险自评估报告不用准备，个人信息保护影响评估仍要做（原因写明改看哪份）
    assert r["risk_report"]["state"] == NA and "个人信息保护影响评估" in r["risk_report"]["why"] and r["risk_report"]["basis"].endswith("第十条")
    assert r["dpia"]["state"] == NEED
    sa = cn_route.judge({"is_cii": "是", "important_data": "否", "sensitive_pi": "否", "data_volume": "约 3 万人"})
    r = run({"data_volume": "约 3 万人"}, source="中国", grounds=None, cn=sa)
    assert r["declaration"]["state"] == NEED and "关键信息基础设施" in r["declaration"]["why"]
