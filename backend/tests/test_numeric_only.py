import pytest

from app.knowledge.numeric_integrity import is_numeric_only


@pytest.mark.parametrize(
    "text",
    ["12.50", "S$ 5,000", "٣٠٠ EGP", "1 000 000 LBP", "(5%) — 2024", "€ 40"],
)
def test_figures_and_amounts_alone_need_no_translation(text):
    assert is_numeric_only(text)


@pytest.mark.parametrize(
    "text",
    [
        "Fee 12.50",  # an English label is content
        "per number 300",
        "Frais 40 €",  # French label
        "رسوم ٣٠٠",  # Arabic label
        "第三条 100",
        "Made 12",  # 'Made' is a word, not the MAD currency code
        "",
        "---",  # no figure at all
    ],
)
def test_any_word_in_any_alphabet_keeps_a_fragment_translatable(text):
    assert not is_numeric_only(text)


@pytest.mark.parametrize("text", ["m Il I Il 1", "32.10 32.09 من 32.12", "Il 5 ,", "a b 12"])
def test_debris_without_a_real_word_needs_no_translation(text):
    assert is_numeric_only(text)


@pytest.mark.parametrize(
    "text",
    [
        "4.1 Objective...............................................................................................10",
        "1.3 September 2023 Cyber Security Agency of Singapore Revised process",
        "1.1 April 2021 Cyber Security Agency of Singapore Minor editorial revisions",
        "2.3 Définitions......................................................... 7",
    ],
)
def test_contents_lines_and_revision_rows_need_no_translation(text):
    assert is_numeric_only(text)


@pytest.mark.parametrize(
    "text",
    [
        "1.3 The organisation shall notify the Commission within 3 days of a data breach in April.",
        "Article 12 The controller shall delete personal data when it is no longer needed.",
        "5.1 Objective The objective of this scheme is to certify products. See page 10.",
    ],
)
def test_real_clauses_are_still_translated(text):
    assert not is_numeric_only(text)


def test_percent_written_in_words_is_the_same_number_as_the_percent_sign():
    from app.knowledge.numeric_integrity import canonical, normalize

    for source in ("up to 40 percent of", "40 per cent", "40 pour cent", "40 Percent"):
        assert "40%" in normalize(source), source
    assert canonical("40%") == ("40", True)
    # 普通数字不会被当成百分数；“percentage”之类的词也不触发
    assert "40%" not in normalize("40 days") and "40%" not in normalize("40 percentage points")


def test_a_contact_fragment_with_only_an_address_is_not_translatable_text():
    from app.knowledge.numeric_integrity import is_numeric_only

    assert is_numeric_only("43 -----@tra.gov.eg")
    assert is_numeric_only("www.pdpc.gov.sg 2012")
    assert not is_numeric_only("Contact the Commission at dpo@example.sg within 3 days")


# 目录逐行判断：真目录不能被当成正文；夹着点线的正文（表单、表格）要翻译
CONTENTS_BLOCKS = [
    "Chapitre III : Saisie des véhicules…..........… 103 64\nChapitre IV : Immobilisation des véhicules… 104 à 106 65\n"
    "Chapitre V : Application des peines…............ 107 à 109 67",
    "5.5 Deliverables...........................................21\nREFERENCES.......................................22\n"
    "ACRONYMS..........................................22",
    "الفهرس\n٣ .................................................... الملخص التنفيذي\n٤ .................................................... مقدمة",
    "6. Prendre en compte de la sécurité des systèmes d’information lors des\nachats...............................................14",
    "3. ANNEXE C : CATÉGORIE DES INCIDENTS À REMONTER AU MACERT...................................",
]
LAW_WITH_LEADERS = [
    "Article 5\nLe poids maximal autorisé par essieu ne doit pas dépasser,\nles limites suivantes:\n"
    "1- Essieu non moteur simple : ...............………..............10 T\n2- Essieux tandem des remorques et semi-remorques :\n"
    "La somme des charges des essieux ne doit pas dépasser les valeurs fixées au tableau ci-après, selon la distance\n"
    "entre les essieux mesurée de centre à centre et le nombre de roues de chaque essieu considéré pour le calcul.",
    "- Ingestion de\nboissons alcoolisées :\nNon Oui, lesquels ?…...………..\n……………………………\n Heure : …….. Minute : .…\n"
    "− Constitution physique: Normale Maigre Obèse\n− Lésions et blessures : Non Oui (préciser leur nature et leur localisation sur le corps du conducteur)",
    "5.5 Deliverables...........................................21\nREFERENCES.......................................22\n"
    "NOTICE\nThe Cyber Security Agency of Singapore makes no warranty of any kind with regard to this material and shall not be\n"
    "liable for errors contained herein or for incidental or consequential damages in connection with the use of this material.\n"
    "1 INTRODUCTION\nThis document sets out the requirements of the scheme for the developers and the testing laboratories.",
]


@pytest.mark.parametrize("text", CONTENTS_BLOCKS)
def test_a_real_table_of_contents_is_not_translated(text):
    assert is_numeric_only(text)


@pytest.mark.parametrize("text", LAW_WITH_LEADERS)
def test_law_with_dot_leaders_or_a_contents_tail_is_translated(text):
    assert not is_numeric_only(text)
