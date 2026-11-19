"""官方 PDF 表单填写：版式不动，值落在字段位置，放不下的如实报告而不是压在别的栏目上。"""

from pathlib import Path

import pypdfium2 as pdfium
import pytest

from app.forms.pdf_fill import fill_pdf, list_fields

F118 = Path(__file__).resolve().parents[1] / "app" / "forms" / "official" / "MA_CNDP_F118_2025-09.pdf"


@pytest.fixture(scope="module")
def source() -> bytes:
    return F118.read_bytes()


def page_text(pdf_bytes: bytes, index: int) -> str:
    text = pdfium.PdfDocument(pdf_bytes)[index].get_textpage().get_text_bounded() or ""
    return text.replace(" ", " ")  # 文字提取把盖印文字里的空格读成不间断空格


def test_the_official_form_is_read_with_unique_keys_and_all_three_control_kinds(source):
    fields = list_fields(source)
    assert len(fields) == 946 and len({f.key for f in fields}) == 946
    assert {f.kind for f in fields} == {"text", "check", "radio"}
    names = {f.name for f in fields}
    assert {"Nom  Raison sociale1", "Pays1", "NRC1"} <= names
    # 同名控件（单选组的各个选项）用 #序号 区分
    assert {"Gr Ident Respo Trait", "Gr Ident Respo Trait#1"} <= {f.key for f in fields}


def test_values_land_on_the_page_in_chinese_and_french_and_the_form_itself_is_unchanged(source):
    before = page_text(source, 1)
    out, report = fill_pdf(
        source,
        {"Nom  Raison sociale1": "示例汽车科技有限公司", "Pays1": "Chine", "Activité1": "Véhicules connectés — données"},
        {"Gr Ident Respo Trait#1"},
    )
    after = page_text(out, 1)
    for expected in ("示例汽车科技有限公司", "Chine", "Véhicules connectés — données"):
        assert expected in after, expected
    assert "✓" in after
    assert report.unknown == [] and report.overflow == []
    # 官方原文一字不少（盖印只增加内容，不改原有文字）
    for line in before.splitlines():
        assert line in after
    assert len(pdfium.PdfDocument(out)) == len(pdfium.PdfDocument(source)) == 9


def test_a_long_value_is_shrunk_then_not_written_when_it_still_cannot_fit(source):
    long_ok = "Example Auto Technology Co., Ltd. Beijing"
    too_long = "x" * 400
    out, report = fill_pdf(source, {"Nom  Raison sociale1": long_ok, "Adresse1": too_long})
    assert "Nom  Raison sociale1" in report.shrunk + report.placed
    assert report.overflow == ["Adresse1"]
    assert too_long[:50] not in page_text(out, 1)  # 放不下的不写，不压在旁边的印刷栏目上


def test_a_narrow_tall_cell_wraps_its_words_instead_of_overflowing(source):
    cell = next(f for f in list_fields(source) if f.key == "Durée de traitementRow3")
    assert cell.height > 40 and cell.width < 60  # 官方表格里的窄高单元格
    out, report = fill_pdf(source, {cell.key: "douze mois renouvelable annuellement"})
    assert cell.key in report.wrapped and report.overflow == []
    assert "douze mois" in " ".join(page_text(out, cell.page).split())


def test_unknown_keys_and_wrong_kind_are_reported_not_ignored(source):
    _, report = fill_pdf(source, {"no such field": "x", "Gr Ident Respo Trait": "x"}, {"Pays1", "nothing"})
    assert sorted(report.unknown) == ["Gr Ident Respo Trait", "Pays1", "no such field", "nothing"]


def test_filling_twice_from_the_same_source_gives_the_same_page_text(source):
    values = {"Pays1": "Chine", "Ville1": "Beijing"}
    a, _ = fill_pdf(source, values)
    b, _ = fill_pdf(source, values)
    assert page_text(a, 1) == page_text(b, 1)
