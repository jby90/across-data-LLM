"""官方表单：目录与原件一致、值的来源顺序、缺项成问题、选项只收官方有的、导出的是官方原件。"""

import json
import shutil

import pypdfium2 as pdfium
import pytest

from app.forms import service
from app.forms.pdf_fill import list_fields


def form():
    return service.get_form("MA_F118")


def test_catalogue_matches_the_stored_official_file_and_every_control_is_used_once():
    f = form()
    assert f["country"] == "MA" and f["direction"] == "export" and f["version"] == "2025-09"
    keys = {c.key for c in list_fields((service.OFFICIAL / f["file"]).read_bytes())}
    used = []
    for s in f["sections"]:
        for i in s["items"]:
            if i["kind"] == "matrix":  # 第五部分的大表：每一格的控件键由 f118_matrix 给出
                from app.forms.f118_matrix import _row_keys

                used += [k for r in i["matrix"]["rows"] for k in _row_keys(r)]
            else:
                used += list((i["options"] or {}).values()) if i["kind"] in ("radio", "check") else [i["pdf_key"], *filter(None, [i.get("pdf_key2")])]
    assert set(used) <= keys and len(used) == len(set(used))
    assert f["controls_mapped"] == len(used) and f["controls_total"] == len(keys)


def test_a_changed_official_file_is_detected_instead_of_silently_used(tmp_path, monkeypatch):
    f = form()
    copy = tmp_path / "official"
    shutil.copytree(service.OFFICIAL, copy)
    (copy / f["file"]).write_bytes((copy / f["file"]).read_bytes() + b"\n%tampered")
    monkeypatch.setattr(service, "OFFICIAL", copy)
    service.catalog.cache_clear()
    try:
        with pytest.raises(service.CatalogError, match="指纹不一致"):
            service.catalog()
    finally:
        monkeypatch.undo()
        service.catalog.cache_clear()


def test_values_come_from_the_users_answer_first_then_the_assessment():
    f = form()
    facts = {"company_name": "示例汽车科技有限公司", "recipient": "中国总部数据中心", "data_types": "车辆位置轨迹", "purpose": "远程诊断"}
    state = {st.item["id"]: st for _, states in service.resolve(f, {}, facts) for st in states}
    assert (state["ctrl_nom"].value, state["ctrl_nom"].source) == ("示例汽车科技有限公司", "assessment")
    assert state["file_purpose"].value == "远程诊断" and state["file_mode"].source == "missing"
    state = {st.item["id"]: st for _, states in service.resolve(f, {"ctrl_nom": "改过的名称"}, facts) for st in states}
    assert (state["ctrl_nom"].value, state["ctrl_nom"].source) == ("改过的名称", "answer")


def test_what_is_missing_becomes_questions_and_optional_items_are_not_asked():
    f = form()
    facts = {"company_name": "X 公司", "recipient": "Y 公司"}
    asked = {q["id"] for q in service.missing_questions(f, {}, facts)}
    assert "ctrl_nom" not in asked and "file_mode" in asked and "consent" in asked
    assert "ctrl_fax" not in asked and "proc_1_denom" not in asked  # 选填项不追问
    q = next(q for q in service.missing_questions(f, {}, facts) if q["id"] == "file_mode")
    assert q["question"] and q["section"] == "III"
    answered = {"file_mode": "加密专线"}
    assert "file_mode" not in {q["id"] for q in service.missing_questions(f, answered, facts)}


def test_answers_keep_only_the_forms_items_and_choices_only_from_its_options():
    f = form()
    clean = service.clean_answers(
        f,
        {"ctrl_nom": "  公司   名称 ", "no_such": "x", "ctrl_type": "morale", "consent": "maybe", "dest_kind": ["siege", "bogus"], "file_name": "x" * 500},
    )
    assert clean == {"ctrl_nom": "公司 名称", "ctrl_type": "morale", "dest_kind": ["siege"], "file_name": "x" * 120}


def test_the_export_is_the_official_file_with_values_checks_and_a_report_of_what_did_not_fit():
    f = form()
    answers = {"ctrl_type": "morale", "dest_kind": ["siege"], "consent": "oui", "file_name": "Données télématiques", "file_mode": "x" * 600}
    facts = {"company_name": "示例汽车科技有限公司", "recipient": "Siège Chine"}
    data, report, too_long = service.render(f, answers, facts)
    pdf = pdfium.PdfDocument(data)
    assert len(pdf) == 9
    page2 = (pdf[1].get_textpage().get_text_bounded() or "").replace(chr(0xA0), " ")
    assert "示例汽车科技有限公司" in page2 and "✓" in page2
    page4 = (pdf[3].get_textpage().get_text_bounded() or "").replace(chr(0xA0), " ")
    assert "Données télématiques" in page4
    assert [t["id"] for t in too_long] == ["file_mode"]  # 放不下的不写，告诉用户缩短
    assert json.dumps(service.summary(f))  # 摘要可序列化


def tn():
    return service.get_form("TN_INPDP_TRANSFERT")


def test_the_tunisian_form_has_no_controls_so_its_boxes_come_from_the_catalogue():
    f = tn()
    assert not list_fields((service.OFFICIAL / f["file"]).read_bytes())  # 官方文件里确实没有表单控件
    fields = service.fixed_fields(f)
    used = []
    for s in f["sections"]:
        for i in s["items"]:
            used += list((i["options"] or {}).values()) if i["kind"] in ("radio", "check") else [i["pdf_key"]]
    assert sorted(used) == sorted(b.key for b in fields)  # 每个栏位恰好被一个栏目使用
    assert all(b.right > b.left and b.top > b.bottom for b in fields)
    assert service.fixed_fields(form()) is None  # 有控件的表不受影响


def test_the_tunisian_export_puts_values_cells_ruled_lines_and_split_email_at_their_places():
    f = tn()
    answers = {
        "s2_nature": "societe", "s2_identite": "Société Exemple SARL", "s2_tel_fixe": "71 123 456", "s2_email": "contact@exemple.tn",
        "s3_finalite": "Diagnostic à distance des véhicules.", "s4_nature": "maison_mere", "s4_tel_port": "13800138000",
        "s6_categories": ["localisation", "sante"], "s7_garanties": ["contrat_resp"], "s8_nom": "Ben Ali, Directeur",
    }
    data, report, too_long = service.render(f, answers, {})
    assert not report.overflow and not report.unknown and not too_long
    pdf = pdfium.PdfDocument(data)
    assert len(pdf) == 2
    p1 = (pdf[0].get_textpage().get_text_bounded() or "").replace(chr(0xA0), " ")
    assert "Société Exemple SARL" in p1 and "contact" in p1 and "exemple.tn" in p1 and "Diagnostic" in p1
    assert p1.count("✓") == 2  # 申请人“公司”、受益人“母公司”
    p2 = (pdf[1].get_textpage().get_text_bounded() or "").replace(chr(0xA0), " ")
    assert p2.count("✓") == 3 and "Ben Ali" in p2  # 2 个数据类别 + 1 项保障措施
    # 逐格：8 位电话每位单独一格，不是一整串
    assert all(d in p1 for d in "1234567")


def test_a_value_that_does_not_fit_the_tunisian_ruled_lines_is_reported_not_written():
    data, report, too_long = service.render(tn(), {"s3_finalite": "mot " * 200, "s2_email": "pas-un-email"}, {})
    assert set(report.overflow) == {"s3_finalite", "s2_email"}
    assert {t["id"] for t in too_long} == {"s3_finalite", "s2_email"}


def test_moroccos_processing_declaration_maps_every_control_once_by_its_position():
    """F211：1206 个控件全部按位置对应到栏目，没有一个被两处使用；第五部分大表 32 行，多“可访问部门”一列、没有“处理目的”列。"""
    from app.forms import service

    form = service.get_form("MA_F211")
    assert form["direction"] == "processing" and form["controls_total"] == form["controls_mapped"] == 1206
    matrix = next(i for s in form["sections"] for i in s["items"] if i["kind"] == "matrix")["matrix"]
    assert len(matrix["rows"]) == 32 and all(r["purpose"] is None and len(r["access"]) == 6 for r in matrix["rows"])
    # 第 9 页控件名与位置不一致的那一块：按位置对应
    items = {i["id"]: i for s in form["sections"] for i in s["items"]}
    assert items["svc_qualite"]["pdf_key"] == "N Pièce dIdentité_5" and items["svc_nationalite"]["pdf_key"] == "Email_9"
    answers = service.clean_answers(form, {"matrix_v": {"localisation": {"on": True, "persons": ["clients"], "access": ["it"], "duration": "1 an"}}})
    data, report, too_long = service.render(form, answers, {"recipient": "Example SARL", "purpose": "Diagnostic à distance"})
    assert data[:4] == b"%PDF" and not report.overflow and not report.unknown and not too_long
    assert not [q for q in service.missing_questions(form, answers, {}) if q["id"].endswith("/purpose")]  # 这张大表不问“处理目的”


def test_moroccos_prior_authorisation_maps_every_control_once_by_its_position():
    """F112（事先授权）：1241 个控件全部按页面上印的栏目对应；比 F211 多申请事由、敏感数据前提、处理目的、是否转往国外四部分。"""
    from app.forms import service

    form = service.get_form("MA_F112")
    assert form["direction"] == "processing" and form["applies_if"] == "sensitive"
    assert form["controls_total"] == form["controls_mapped"] == 1241
    items = {i["id"]: i for s in form["sections"] for i in s["items"]}
    # 第 3 页法定代表人的证件类型三个框从左到右是 C.N.I、护照、其他，控件名却是 #2、#1、无后缀
    assert items["ctrl_rep_id_type"]["options"]["cni"] == "Gr ID identi respo traitement#2"
    assert {"qualif", "frame_a", "finalite", "transfer"} <= set(items)
    answers = service.clean_answers(form, {"qualif": ["sensibles"], "finalite": "autres", "transfer": "oui",
                                           "matrix_v": {"biometrie": {"on": True, "persons": ["clients"], "access": ["it"], "duration": "1 an"}}})
    data, report, too_long = service.render(form, answers, {"ma_processor": "Example SARL", "purpose": "Surveillance de la vigilance du conducteur"})
    assert data[:4] == b"%PDF" and not report.overflow and not report.unknown and not too_long


def test_every_forms_version_fits_the_stored_instance_column():
    """A form instance records the form's version (``form_instances.form_version``): a longer version
    string makes creating the instance fail, so the enterprise could not open that form at all."""
    from app.forms.models import FormInstance

    limit = FormInstance.__table__.c.form_version.type.length
    service.catalog.cache_clear()
    too_long = {code: f["version"] for code, f in service.catalog().items() if len(str(f.get("version") or "")) > limit}
    assert not too_long


def test_text_the_font_cannot_draw_is_reported_not_written_as_boxes():
    """阿拉伯文等填表字体画不出（也不能逐字从右往左连写）的值：不写进官方表，导出时报出来请企业改用法文或英文。"""
    form = service.get_form("TN_ATTT_RECEPTION")
    item = next(i for s in form["sections"] for i in s["items"] if i["kind"] == "text" and not i.get("fact"))
    data, report, too_long = service.render(form, {item["id"]: "عمليات أخرى"}, {})
    assert item["pdf_key"] in report.unwritable and item["pdf_key"] not in report.placed + report.shrunk
    assert any(t["id"] == item["id"] and "法文或英文" in t["label"] for t in too_long)


def test_page_limit_follows_the_box_but_a_saved_value_is_never_cut_to_it():
    """页面输入框的上限按格子写得下的字数；保存时按登记的上限留存（助手写进来的长内容不悄悄截掉，导出时报“放不进”）。"""
    f = form()
    item = next(i for s in f["sections"] for i in s["items"] if i["id"] == "ctrl_adresse")
    assert item["max_chars"] < item["store_chars"] == 400
    assert service.clean_answers(f, {"ctrl_adresse": "x" * 390}) == {"ctrl_adresse": "x" * 390}
