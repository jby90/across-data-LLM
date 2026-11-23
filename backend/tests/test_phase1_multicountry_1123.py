"""Regression tests for multi-country dictionaries, RTF parsing, multi-jurisdiction clause
segmentation, and multi-script quality card assessment (2026-11-23)."""

from __future__ import annotations

from app.analysis.document_basis import country_of
from app.analysis.workflow_engine import country_name_zh, is_overseas_country
from app.ingestion.clauses import article_numbers_covered, split_clauses
from app.ingestion.parsing import detect_kind, parse_document
from app.ingestion.quality_card import ClauseSample, _readable_ratio, assess
from app.ingestion.rtf_text import is_rtf, rtf_paragraphs


def test_multicountry_names_and_overseas_registry():
    for code, expected in (
        ("BD", "孟加拉国"),
        ("KG", "吉尔吉斯斯坦"),
        ("KW", "科威特"),
        ("OM", "阿曼"),
        ("EU", "欧盟"),
        ("BR", "巴西"),
        ("PH", "菲律宾"),
    ):
        assert country_name_zh(code) == expected
        assert is_overseas_country(code) is True
        assert country_of(expected) == code


def test_rtf_parser_decodes_cp1252_unicode_and_skips_header_destinations():
    raw_rtf = (
        b"{\\rtf1\\ansi\\ansicpg1252\\deff0{\\fonttbl{\\f0 Arial;}}"
        b"{\\colortbl;\\red0\\green0\\blue0;}"
        b"Artikel 1. Algemene bepalingen\\par "
        b"Deze wet is van toepassing op de verwerking van persoonsgegevens (co\\'f6rdinatie).\\par "
        b"Artikel 2. Toezicht \\u8212? Autoriteit Persoonsgegevens\\par}"
    )
    assert is_rtf(raw_rtf) is True
    assert detect_kind(raw_rtf, "application/octet-stream") == "rtf"
    paras = rtf_paragraphs(raw_rtf)
    assert paras[0] == "Artikel 1. Algemene bepalingen"
    assert "coördinatie" in paras[1]
    assert "—" in paras[2]

    parsed = parse_document(raw_rtf, "application/rtf")
    drafts, strategy = split_clauses(parsed.pages)
    assert strategy == "numbered"
    assert [d.clause_number for d in drafts] == ["1", "2"]


def test_multijurisdiction_clause_segmentation_and_section_vs_article_disambiguation():
    # Dutch law where § N is a structural sub-chapter ("Paragraaf") and Artikel 1..6 are articles
    dutch_pages = [
        "\n".join(
            [
                "§ 1. Algemeen",
                "Artikel 1. Begripsbepalingen",
                "In deze wet wordt verstaan onder beveiliging van netwerk- en informatiesystemen.",
                "Artikel 2. Toepassingsbereik",
                "Deze wet geldt voor vitale aanbieders.",
                "Artikel 3. Bevoegde autoriteit",
                "Onze Minister wijst de autoriteit aan.",
                "§ 2. Beveiligingseisen",
                "Artikel 4. Zorgplicht",
                "De aanbieder neemt passende technische en organisatorische maatregelen.",
                "Artikel 5. Meldplicht",
                "De aanbieder meldt elk incident onverwijld.",
                "Artikel 6. Handhaving",
                "De autoriteit kan een last onder dwangsom opleggen.",
            ]
        )
    ]
    drafts, strat = split_clauses(dutch_pages)
    assert strat == "numbered"
    assert [d.clause_number for d in drafts if d.clause_number] == ["1", "2", "3", "4", "5", "6"]
    assert drafts[3].section_path == "§ 2. Beveiligingseisen"

    # Korean law with 제N조, sub-article 제3조의2, and deleted range 제4조부터 제6조까지 삭제
    korean_pages = [
        "\n".join(
            [
                "제1조(목적) 이 법은 개인정보의 처리 및 보호에 관하여 필요한 사항을 정한다.",
                "제2조(정의) 이 법에서 사용하는 용어의 뜻은 다음과 같다.",
                "제3조(개인정보 보호 원칙) 개인정보처리자는 목적을 명확하게 하여야 한다.",
                "제3조의2(추가적 이용) 개인정보처리자는 당초 수집 목적과 관련성이 있는 경우 이용할 수 있다.",
                "제4조부터 제6조까지 삭제 <2020. 2. 4.>",
                "제7조(개인정보 보호위원회) 국무총리 소속으로 개인정보 보호위원회를 둔다.",
            ]
        )
    ]
    kr_drafts, kr_strat = split_clauses(korean_pages)
    assert kr_strat == "numbered"
    kr_nums = [d.clause_number for d in kr_drafts if d.clause_number]
    assert kr_nums == ["1", "2", "3", "3의2", "4–6", "7"]
    assert article_numbers_covered("4–6") == [4, 5, 6]
    kr_card = assess(
        [ClauseSample(d.clause_number, d.text, True) for d in kr_drafts],
        "ko",
        needs_translation=False,
    )
    assert kr_card.passed and kr_card.number_continuity == 1.0

    # Cyrillic inline sequential clause splitting (`... . 2. ...`)
    ru_pages = [
        "1. Утвердить прилагаемое Положение об обработке персональных данных. "
        "2. Настоящее постановление вступает в силу со дня опубликования."
    ]
    ru_drafts, ru_strat = split_clauses(ru_pages)
    assert ru_strat == "numbered"
    assert [d.clause_number for d in ru_drafts if d.clause_number] == ["1", "2"]


def test_multiscript_readability_ratios_pass_native_scripts_and_extended_latin():
    assert _readable_ratio(["Федеральный закон о персональных данных операторов"], "ru") == 1.0
    assert _readable_ratio(["この法律は、個人情報の適正な取扱いを確保する。"], "ja") == 1.0
    assert _readable_ratio(["개인정보처리자는 정보주체의 동의를 받아야 한다."], "ko") == 1.0
    assert _readable_ratio(["พระราชบัญญัติคุ้มครองข้อมูลส่วนบุคคล พ.ศ. ๒๕๖๒"], "th") == 1.0
    assert _readable_ratio(["חוק הגנת הפרטיות חל על עיבוד מידע אישי בישראל"], "he") == 1.0
    assert _readable_ratio(["Nghị định bảo vệ dữ liệu cá nhân tại Việt Nam"], "vi") == 1.0
    assert _readable_ratio(["Ustawa o krajowym systemie cyberbezpieczeństwa oraz obowiązkach"], "pl") == 1.0
    assert _readable_ratio(["Kişisel verilerin yurt dışına aktarılmasına ilişkin yönetmelik"], "tr") == 1.0
