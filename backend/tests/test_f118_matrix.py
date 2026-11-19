"""F118 第五部分（按数据类别的大表）：控件按版面定位，勾选与填写落在对的格子里，官方表自身的瑕疵（文本框形式的勾选框）也能填。"""

import json

import pypdfium2 as pdfium
import pytest

from app.forms import f118_matrix, service
from app.forms.pdf_fill import list_fields


def form():
    return service.get_form("MA_F118")


def matrix():
    return next(i for s in form()["sections"] for i in s["items"] if i["kind"] == "matrix")["matrix"]


def test_the_matrix_covers_32_categories_and_every_control_is_used_once():
    m = matrix()
    assert len(m["rows"]) == 32 and {r["group_zh"] for r in m["rows"]} == {"个人身份识别", "行为", "职业信息", "财务状况", "敏感数据"}
    keys = [k for r in m["rows"] for k in f118_matrix._row_keys(r)]
    assert len(keys) == len(set(keys))
    pdf_keys = {c.key for c in list_fields((service.OFFICIAL / form()["file"]).read_bytes())}
    assert set(keys) <= pdf_keys
    assert len(m["text_marks"]) >= 3  # 官方 PDF 里被做成文本框的勾选框
    assert form()["controls_mapped"] == form()["controls_total"] == 946  # 官方表 946 个控件全部对应到栏目（含两行栏目的续行）


def test_the_row_layout_is_checked_by_position_not_by_the_unreliable_control_names():
    # 官方文件里“Service de santé”这个名字的控件，实际位置是“分支机构”：按名字映射会填错格子
    fields = list_fields((service.OFFICIAL / form()["file"]).read_bytes())
    by_key = {f.key: f for f in fields}
    row = next(r for r in matrix()["rows"] if r["id"] == "nom")
    sous, admin = by_key[row["recipients"]["soustraitant"]], by_key[row["recipients"]["administration"]]
    succ, fin = by_key[row["recipients"]["succursales"]], by_key[row["recipients"]["financiers"]]
    sante, autres = by_key[row["recipients"]["sante"]], by_key[row["recipients"]["autres"]]
    assert sous.top > succ.top > sante.top and admin.top > fin.top > autres.top  # 三行：上、中、下
    assert sous.left < admin.left and succ.left < fin.left and sante.left < autres.left  # 左、右


def test_answers_keep_only_known_rows_and_offered_choices_and_empty_clears():
    f = form()
    raw = {"matrix_v": {
        "localisation": {"on": True, "persons": ["clients", "nimporte"], "origin": ["personne"], "recipients": ["soustraitant"],
                         "purpose": "Diagnostic à distance", "duration": "180 jours " + "x" * 100},
        "inconnue": {"on": True, "purpose": "x"},
        "photo": {"on": False, "purpose": "ignoré"},
    }}
    clean = service.clean_answers(f, raw)["matrix_v"]
    assert list(clean) == ["localisation"]
    assert clean["localisation"]["persons"] == ["clients"]  # 不在表里的选项被丢弃
    assert len(clean["localisation"]["duration"]) <= 30
    assert service.clean_answers(f, {"matrix_v": {}}) == {"matrix_v": {}}  # 全部取消勾选 = 清空


def test_the_export_ticks_and_writes_each_row_in_its_own_boxes_including_text_box_marks():
    f = form()
    answers = {"matrix_v": {
        "localisation": {"on": True, "persons": ["clients", "autres"], "persons_other": "Piétons", "origin": ["personne"],
                         "recipients": ["soustraitant", "autres"], "recipients_other": "Siège en Chine",
                         "purpose": "Diagnostic et amélioration de la conduite assistée", "duration": "180 jours"},
        "mode_vie": {"on": True, "persons": ["clients"], "origin": ["autre"], "origin_other": "Capteurs",
                     "recipients": ["sante"], "purpose": "Essai", "duration": "1 an"},  # “sante”在官方 PDF 里是文本框形式的勾选框
        "id_autres": {"on": True, "label_other": "Plaque", "persons": ["clients"], "purpose": "Test", "duration": "1 an"},
    }}
    data, report, too_long = service.render(f, answers, {})
    assert not too_long and not report.unknown
    pdf = pdfium.PdfDocument(data)
    page5 = (pdf[4].get_textpage().get_text_bounded() or "").replace(chr(0xA0), " ")
    for expected in ("Piétons", "Siège en Chine", "Diagnostic et amélioration", "180 jours", "Plaque", "Capteurs"):
        assert expected in page5
    assert page5.count("✓") >= 8 and page5.count("X") >= 1  # 勾选框写“✓”，文本框形式的写“X”


def test_missing_questions_ask_for_the_category_then_for_purpose_and_people_of_each_chosen_row():
    f = form()
    base = [q["id"] for q in service.missing_questions(f, {}, {})]
    assert "matrix_v" in base
    ans = service.clean_answers(f, {"matrix_v": {"photo": {"on": True}}})
    asked = [q["id"] for q in service.missing_questions(f, ans, {})]
    assert "matrix_v" not in asked and "matrix_v/photo/purpose" in asked and "matrix_v/photo/persons" in asked
    ok = service.clean_answers(f, {"matrix_v": {"photo": {"on": True, "persons": ["clients"], "purpose": "x"}}})
    assert not [q for q in service.missing_questions(f, ok, {}) if q["id"].startswith("matrix_v")]


def test_a_layout_that_does_not_match_is_reported_not_silently_accepted():
    fields = list_fields((service.OFFICIAL / form()["file"]).read_bytes())
    broken = [f for f in fields if not (f.page == 4 and f.kind == "check" and 140 < f.left < 260 and 400 < f.top < 430)]
    with pytest.raises(AssertionError, match="勾选框"):
        f118_matrix.build(broken)
    assert json.dumps(f118_matrix.public(matrix()))  # 页面用的摘要可序列化，且不含控件名
    assert "pdf" not in json.dumps(f118_matrix.public(matrix()))


def test_the_exported_form_embeds_only_the_characters_it_needs_not_the_whole_cjk_font():
    f = form()
    answers = {"ctrl_nom": "示例汽车科技有限公司", "matrix_v": {"photo": {"on": True, "persons": ["clients"], "purpose": "诊断", "duration": "180 jours"}}}
    data, _, _ = service.render(f, answers, {})
    original = (service.OFFICIAL / f["file"]).stat().st_size
    assert len(data) < original + 600_000  # 整套字体约 8 MB，子集后只比原件多几百 KB
    page = (pdfium.PdfDocument(data)[1].get_textpage().get_text_bounded() or "")
    assert "示例汽车科技有限公司" in page  # 子集没有丢掉要写的字


def test_a_long_other_recipient_starts_after_autres_and_goes_on_the_line_below():
    from app.forms import f118_matrix

    matrix = next(i for s in form()["sections"] for i in s["items"] if i["kind"] == "matrix")["matrix"]
    loc = next(r for r in matrix["rows"] if r["id"] == "localisation")
    texts, _checked, _ = f118_matrix.apply(matrix, {"localisation": {"on": True, "persons": [], "origin": ["autre"], "recipients": ["autres"],
                                                                     "origin_other": "Capteurs GNSS du véhicule connecté",
                                                                     "recipients_other": "Centre de recherche à Shanghai", "duration": "2 ans"}})
    assert texts[loc["recipients_other"]] == "Centre de" and texts[loc["recipients_other2"]] == "recherche à Shanghai"
    assert texts[loc["origin_other"]] == "Capteurs GNSS du" and texts[loc["origin_other2"]] == "véhicule connecté"


def test_derived_telematics_rows_use_vehicle_sensors_and_fit_every_f118_box():
    f = form()
    facts_service = {
        "source_country": "MA",
        "recipient_country": "CN",
        "recipient_adequate": "否",
        "function_key": "connected_vehicle_cloud",
        "project_stage": "量产与销售阶段",
    }
    data, report, too_long = service.render(f, {}, facts_service)
    assert not too_long and not report.overflow and not report.unknown
    page5 = (pdfium.PdfDocument(data)[4].get_textpage().get_text_bounded() or "").replace(chr(0xA0), " ")
    assert "Capteurs du" in page5 and "véhicule" in page5
    assert "Services connectés, navigation" in page5
    assert "Durée du" in page5

    facts_trial = {**facts_service, "project_stage": "研发与试验阶段（样车、公共道路试验）"}
    data_t, report_t, too_long_t = service.render(f, {}, facts_trial)
    assert not too_long_t and not report_t.overflow
    page5_t = (pdfium.PdfDocument(data_t)[4].get_textpage().get_text_bounded() or "").replace(chr(0xA0), " ")
    assert "Essais routiers, télédiagnostic" in page5_t and "essais" in page5_t

    anrt = service.get_form("MA_ANRT_AGREMENT")
    ant = next(i for s in anrt["sections"] for i in s["items"] if i["id"] == "q_antenne_type")
    assert ant["option_labels_zh"] == {"o1": "内置（Intégrée）", "o2": "外置（Externe）"}

