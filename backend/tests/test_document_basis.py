"""编制依据（项目页上的说明，文件里不再印）：不假定出入境国是谁；发出国有官方模板用官方，没有按欧盟标准。"""

from app.analysis.document_basis import basis_lines, country_of


def _text(code, values, assessed="MA"):
    return " ".join(basis_lines(code, assessed, values))


def test_the_countries_are_what_the_user_named_not_what_we_assume():
    assert country_of("中国") == "CN" and country_of("摩洛哥") == "MA" and country_of("SG") == "SG"
    assert country_of("中国和新加坡") is None  # 同时出现两个国家，不猜
    assert country_of("") is None and country_of(None) is None


def test_data_leaving_china_uses_the_cac_template_and_the_other_end_the_eu_standard_when_no_template():
    t = _text("declaration", {"source_country": "中国", "recipient_country": "埃及"})
    assert "网信办" in t or "国家网信办" in t
    assert "附件 3" in t
    assert "埃及" not in t  # 出境申报只对应发出国；接收国一端要办的处理申报属于属地准入


def test_data_leaving_morocco_uses_its_official_form_and_china_as_recipient_has_no_cac_form_claimed():
    t = _text("declaration", {"source_country": "摩洛哥", "recipient_country": "中国"})
    assert "CNDP F118" in t
    assert "附件 3" not in t  # 数据不是从中国发出，不用网信办出境申报表
    assert "中国）" not in t  # 中国是接收方：不声称有网信办的申报表


def test_data_leaving_a_country_without_a_form_uses_the_general_structure_and_the_eu_standard():
    t = _text("declaration", {"source_country": "埃及", "recipient_country": "中国"})
    assert "埃及" in t and "欧盟标准" in t


def test_risk_report_follows_the_same_rule_and_the_ends_not_named_are_asked_for():
    cn = _text("risk_report", {"source_country": "中国", "recipient_country": "新加坡"})
    assert "附件 4" in cn  # 风险评估报告 = 申报指南附件 4；标准合同路径的影响评估报告是另一份文件（DPIA，附件 5）
    assert "附件 5" not in _text("risk_report", {"source_country": "埃及", "recipient_country": "中国"})
    assert "尚未填写" in _text("risk_report", {})


def test_contract_and_papers_state_their_standard_without_naming_a_country():
    assert "SCC" in _text("contract", {}, "EG")
    assert "东盟" in _text("contract", {}, "SG") and "东盟" not in _text("contract", {}, "EG")
    assert "GDPR" in _text("dpia", {}) and "EDPB" in _text("tia", {})


def test_contract_for_data_leaving_china_is_the_official_standard_contract_and_otherwise_the_eu_structure():
    cn = _text("contract", {"source_country": "中国", "recipient_country": "新加坡"}, "SG")
    assert "个人信息出境标准合同" in cn and "严格按照附件订立" in cn and "10 个工作日" in cn
    assert "SCC" not in cn and "东盟" in cn  # 新加坡是接收方：东盟条款作为可选工具
    other = _text("contract", {"source_country": "埃及", "recipient_country": "中国"}, "EG")
    assert "SCC" in other and "个人信息出境标准合同" not in other
