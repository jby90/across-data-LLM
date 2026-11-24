"""Regression tests for 46-country generalization, dynamic compliance document applicability,
multi-script 0-.notdef PDF export, 46-country currency conservation, and Gulf/Maghreb form annexes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pypdfium2 as pdfium
from fontTools.ttLib import TTFont

from app.analysis import chat, clause_gate, doc_applicability, doc_rules, doc_sections
from app.analysis.document_basis import _ASEAN
from app.analysis.pdf_renderer import render_pdf, verified_fonts
from app.analysis.report_exporter import _country_label_for
from app.forms import derive
from app.forms.service import _build_overflow_annex_pdf
from app.ingestion import source_lock
from app.knowledge.numeric_integrity import missing_amounts, normalize


def test_dynamic_applicability_never_forces_all_eight_documents():
    """Verify the existing dynamic need/na/unknown rules remain intact:
    not all 8 compliance documents are required when enterprise facts exclude them."""
    # Case 1: Non-personal vehicle telemetry (0 natural persons) -> DPIA, LIA, TIA must be 'na'
    res_no_pi = doc_applicability.judge(
        source="德国",
        recipient="中国",
        source_in_library=True,
        values={
            "data_volume": "0",
            "sensitive_pi": "否",
            "data_types": "纯车辆总线工况与脱敏传感器状态参数，不含任何个人信息",
            "lawful_basis": "履行合同所必需（不以合法利益为依据）",
            "issues": "无",
            "entry_type": "无",
        },
        labels={},
        grounds={
            "organized": True,
            "approval": doc_applicability.Ground("向第三国传输个人数据须满足适当保障", "《联邦数据保护法》第1条", True),
            "approval_personal_only": True,
            "contract": doc_applicability.Ground("标准数据保护条款", "《欧盟通用数据保护条例》第46条", True),
            "adequacy": None,
            "transfer": None,
            "dpia": doc_applicability.Ground("数据保护影响评估", "《欧盟通用数据保护条例》第35条", True),
            "legit": None,
        },
        cn=None,
        overseas="德国",
        overseas_known=True,
        admission=[],
        function_zh="远程诊断与OTA",
    )
    assert res_no_pi["dpia"]["state"] == "na"
    assert res_no_pi["lia"]["state"] == "na"
    assert res_no_pi["tia"]["state"] == "na"
    assert res_no_pi["remediation"]["state"] == "na"
    assert res_no_pi["admission"]["state"] == "na"
    assert res_no_pi["declaration"]["state"] == "na"

    # Case 2: Personal data present, consent basis, no compliance issues -> LIA & remediation are 'na', DPIA & TIA are 'need'
    res_consent = doc_applicability.judge(
        source="巴西",
        recipient="德国",
        source_in_library=True,
        values={
            "data_volume": "8万人",
            "sensitive_pi": "否",
            "data_types": "车主账号与车辆定位轨迹",
            "lawful_basis": "取得数据主体单独明示同意",
            "issues": "无",
        },
        labels={},
        grounds={
            "organized": True,
            "approval": None,
            "approval_personal_only": False,
            "contract": doc_applicability.Ground("国际数据传输标准合同条款", "《通用个人数据保护法》第33条", True),
            "adequacy": None,
            "transfer": None,
            "dpia": doc_applicability.Ground("个人数据保护影响报告", "《通用个人数据保护法》第38条", True),
            "legit": None,
        },
        cn=None,
        overseas="巴西",
        overseas_known=True,
        admission=["ANATEL 车载通信模块型式认证"],
        function_zh="车联网遥测与云同步",
    )
    assert res_consent["lia"]["state"] == "na"
    assert res_consent["remediation"]["state"] == "na"
    assert res_consent["dpia"]["state"] == "need"
    assert res_consent["tia"]["state"] == "need"
    assert res_consent["contract"]["state"] == "need"
    assert res_consent["admission"]["state"] == "need"


def test_46_country_currencies_and_bengali_thai_digits():
    assert normalize("১২৩,৪৫৬ ฿500 ٪٥٠ (๔๕)") == "123,456 ฿500 50% (45)"
    assert missing_amounts("AED 500,000", "处 500,000 阿联酋迪拉姆罚款") == []
    assert missing_amounts("AED 500,000", "处 500,000 摩洛哥迪拉姆罚款") == ["AED 500000"]
    assert missing_amounts("SAR 5,000,000", "最高 5,000,000 沙特里亚尔罚款") == []
    assert missing_amounts("KRW 30,000,000", "处以 30,000,000 韩元以下罚款") == []
    assert missing_amounts("R$ 50,000,000", "最高 50,000,000 巴西雷亚尔罚金") == []
    assert missing_amounts("INR 2,500,000", "处 2,500,000 印度卢比罚款") == []
    assert missing_amounts("PLN 100,000", "处 100,000 波兰兹罗提罚款") == []
    assert missing_amounts("ZAR 10,000,000", "处 10,000,000 南非兰特罚金") == []


def test_bundled_fonts_cover_all_8_scripts_and_render_zero_notdef_pdf():
    vf = verified_fonts()
    assert {"inter", "notosanssc", "notonaskharabic", "notosanskr", "notosansthai", "notosansbengali", "notosanshebrew"} <= set(vf)

    fdir = Path(__file__).resolve().parents[1] / "app" / "analysis" / "fonts"
    cmaps: set[int] = set()
    for ttf_path in fdir.rglob("*.ttf"):
        cmaps |= set(TTFont(ttf_path).getBestCmap().keys())

    multilingual_line = (
        "中文合规评估 · Tiếng Việt Đạo luật · Polski ąćęłńóśźż · Türkçe ğüşıöç · "
        "Русский Федеральный закон · العربية قانون البيانات 4G/5G · "
        "日本語 個人情報保護法 · 한국어 개인정보 보호법 · "
        "ภาษาไทย พระราชบัญญัติคุ้มครองข้อมูลส่วนบุคคล · "
        "বাংলা তথ্য ও যোগাযোগ প্রযুক্তি আইন · עברית חוק הגנת הפרטיות"
    )
    missing = [ch for ch in multilingual_line if not ch.isspace() and ord(ch) not in cmaps]
    assert missing == []

    pdf_bytes = render_pdf(
        SimpleNamespace(country_code="KR", vehicle_model="QH-S9"),
        SimpleNamespace(consulting=None),
        dimension=None,
        context="多语种原典引证零豆腐块核验",
        country="韩国",
        notices=[],
        blocks=[
            {"t": "h1", "text": "多法域原典引证渲染核验"},
            {"t": "p", "text": multilingual_line},
        ],
    )
    doc = pdfium.PdfDocument(pdf_bytes)
    assert len(doc) >= 1
    extracted = doc[0].get_textpage().get_text_range()
    assert "개인정보" in extracted
    assert "個人情報" in extracted


def test_chat_aliases_asean_exporter_labels_and_gulf_vs_maghreb_annex():
    assert chat.match_country("德国 BfDI 关于智能网联汽车数据出境要求") == "DE"
    assert chat.match_country("巴西 ANPD 标准合同备案") == "BR"
    assert chat.match_country("沙特 SDAIA 个人数据出境风险评估") == "SA"
    assert chat.match_country("韩国 PIPC 跨境传输告知") == "KR"
    assert {"SG", "MY", "ID", "TH", "VN", "PH"} <= set(_ASEAN)
    assert "波兰" in doc_sections._EU_SUPERVISORS["PL"][1]
    assert "德国" in doc_rules._NAME_FORMS["DE"]
    assert _country_label_for("DE") == "德国 (Germany)"
    assert derive.country_name("SA", "fr") == "Arabie saoudite"
    assert derive.country_name("BR", "en") == "Brazil"
    assert source_lock.is_official_host("https://publications.europa.eu/resource/celex/32019R2144")

    # Foreign official annex for Gulf Arabic form (AE) must use English (zero Chinese leak!), while Maghreb (DZ) uses French
    ae_pdf = _build_overflow_annex_pdf(
        {"country": "AE", "language": "ar", "code": "AE_TDRA_TA", "version": "2025", "name_zh": "阿联酋型式批准"},
        [["#1", "Equipment Model", "Connected Telematics Unit T-Box 5G V2X"]],
        cjk_filled=False,
    )
    ae_txt = pdfium.PdfDocument(ae_pdf)[0].get_textpage().get_text_range()
    assert "Official Form Continuation Sheet" in ae_txt
    assert not any("\u4e00" <= c <= "\u9fff" for c in ae_txt)

    dz_pdf = _build_overflow_annex_pdf(
        {"country": "DZ", "language": "ar", "code": "DZ_ARPCE_TA", "version": "2025", "name_zh": "阿尔及利亚型式批准"},
        [["#1", "Modèle", "Module télématique 5G"]],
        cjk_filled=False,
    )
    dz_txt = pdfium.PdfDocument(dz_pdf)[0].get_textpage().get_text_range()
    assert "Annexe complémentaire" in dz_txt
    assert not any("\u4e00" <= c <= "\u9fff" for c in dz_txt)


def test_clause_gate_blocks_untranslated_foreign_scripts_outside_parentheses():
    ok_with_paren = "数据控制者应当向个人信息保护委员会（개인정보 보호위원회）申报跨境传输事项。"
    assert clause_gate.why_unusable(ok_with_paren, "韩国个人信息保护法", "개인정보처리자는...") == ""

    bad_untranslated_ko = "数据控制者应当遵守 개인정보 보호법 제28조의8 에 따른 국외 이전 요건规定。"
    assert clause_gate.why_unusable(bad_untranslated_ko, "韩国个人信息保护法", "...") == "夹着大段未译的外文"
