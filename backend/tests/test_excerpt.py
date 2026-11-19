from app.ingestion.excerpt import cut_excerpt, start_pattern

PAGES = [
    "Art. 4 - fin du décret précédent.\nDécret n° 2008-2638 du 15 septembre 2008, relatif au VoIP\nArt. 1 - voip",
    "Décret n° 2008-3026 du 15 septembre 2008, fixant les conditions\nVu le décret n° 2008-2638 du 15 septembre 2008\nArt. 1 - texte propre\n"
    "Art. 2 - suite\nDécret n° 2008-3027 du 15 septembre 2008, autre\nArt. 1 - autre",
]


def test_only_the_wanted_instrument_is_kept_and_pages_stay_numbered():
    out = cut_excerpt(PAGES, official_number="2008-3026")
    assert len(out) == 2 and out[0] == ""  # 页数不变，页码仍对得上
    assert out[1].startswith("Décret n° 2008-3026") and "texte propre" in out[1]
    assert "Art. 2 - suite" in out[1] and "3027" not in out[1] and "autre" not in out[1]
    assert "Vu le décret n° 2008-2638" in out[1]  # 引用别的令的“Vu”行不当成结尾


def test_an_instrument_that_spans_pages_is_kept_across_them():
    pages = [
        "x\nDécret n° 2004-1250 du 25 mai 2004\nArt. 1 a",
        "Art. 2 b\nArrêté n° 2004-9 du 1er juin 2004\nz",
    ]
    out = cut_excerpt(pages, official_number="2004-1250")
    assert out[0].startswith("Décret n° 2004-1250") and out[1].strip() == "Art. 2 b"


def test_unfindable_or_unparseable_numbers_leave_the_pages_alone():
    assert cut_excerpt(PAGES, official_number="1999-1") == PAGES
    assert cut_excerpt(PAGES, official_number="") == PAGES
    assert start_pattern("n'importe quoi") is None


def test_an_explicit_start_and_end_pattern_win():
    out = cut_excerpt(PAGES, start=r"Art\. 2 - suite", end=r"Décret n° 2008-3027")
    assert out[1].strip() == "Art. 2 - suite"


def test_a_reference_wrapped_onto_a_new_line_is_not_taken_for_the_next_instrument():
    pages = [
        "Décret n° 2014-2152 du 19 mai 2014, relatif à x\nVu le décret n° 2002-3158 du 17 décembre 2002, tel que modifié par le\n"
        "Décret n° 2013-1 du 3 mars 2013 ,\nArt. 1 - propre\nFait à Tunis, le 19 mai 2014.\n"
        "Décret n° 2014-2153 du 19 mai 2014, autre\nArt. 1 - autre"
    ]
    out = cut_excerpt(pages, official_number="2014-2152")[0]
    assert "Art. 1 - propre" in out and "2014-2153" not in out and "autre" not in out
