import pytest

from app.ingestion.clauses import match_header, split_clauses
from app.ingestion.discovery import extract_links, next_page_url, normalise_url
from app.ingestion.parsing import ParseError, detect_kind, parse_document

from .helpers import make_pdf

# ---------------- 链接发现 ----------------

INDEX_HTML = """
<html><body>
<a href="/laws/pdpa.pdf">Personal Data Protection Act</a>
<a href="/laws/pdpa.pdf#page=2">duplicate with fragment</a>
<a href="https://other.example.org/g/guide.PDF">Advisory Guideline</a>
<a href="/img/logo.png">logo</a>
<a href="mailto:x@example.org">mail</a>
<a href="javascript:void(0)">js</a>
<a href="/laws/road.pdf">Road Traffic Rules</a>
<a href="/next?p=2" class="next">next</a>
</body></html>
"""


def test_extract_links_filters_dedupes_and_resolves():
    links = extract_links(INDEX_HTML, "https://example.org/index")
    urls = [c.url for c in links]
    assert urls == [
        "https://example.org/laws/pdpa.pdf",
        "https://other.example.org/g/guide.PDF",
        "https://example.org/laws/road.pdf",
    ]
    assert links[0].title == "Personal Data Protection Act"


def test_extract_links_keywords_and_extensions():
    links = extract_links(INDEX_HTML, "https://example.org/", keywords=["personal data"])
    assert [c.url for c in links] == ["https://example.org/laws/pdpa.pdf"]
    png = extract_links(INDEX_HTML, "https://example.org/", extensions=[".png"])
    assert [c.url for c in png] == ["https://example.org/img/logo.png"]


def test_extract_links_selector_scopes_to_container():
    html = '<div id="a"><a href="/x.pdf">x</a></div><div id="b"><a href="/y.pdf">y</a></div>'
    links = extract_links(html, "https://example.org/", selector="#b a[href]")
    assert [c.url for c in links] == ["https://example.org/y.pdf"]


def test_next_page_url():
    assert (
        next_page_url(INDEX_HTML, "https://example.org/index", "a.next")
        == "https://example.org/next?p=2"
    )
    assert next_page_url(INDEX_HTML, "https://example.org/index", None) is None
    assert next_page_url(INDEX_HTML, "https://example.org/index", "a.missing") is None


def test_normalise_url():
    assert normalise_url("https://e.org/a#x") == "https://e.org/a"
    assert normalise_url("ftp://e.org/a") is None


# ---------------- 条款切分 ----------------


@pytest.mark.parametrize(
    "line,number",
    [
        ("Article 5 Data transfer", "5"),
        ("Section 26. Transfer of personal data", "26"),
        ("Regulation 3A", "3A"),
        ("Article premier", "premier"),
        ("Art. 12 – Sanctions", "12"),
        ("1.1 The PDPA governs the collection of data", "1.1"),
        ("12. The organisation must keep records.", "12"),
        ("第十二条 个人信息出境应当", "十二"),
        ("第12条 个人信息", "12"),
        ("المادة 7 نقل البيانات", "7"),
        ("المادة ٧", "٧"),
    ],
)
def test_match_header_positive(line, number):
    m = match_header(line)
    assert m is not None and m[0] == number


@pytest.mark.parametrize(
    "line",
    [
        "In 2021 the revised act came into force.",
        "2021 revision of the act",
        "see Article",
        "Fine not exceeding $2,000",
        "",
    ],
)
def test_match_header_negative(line):
    assert match_header(line) is None


def test_match_header_heading_only_for_keyword_style():
    assert match_header("Article 5 Data transfer") == ("5", "Data transfer")
    assert match_header("1.1 The PDPA governs the collection of data")[1] is None


def test_split_numbered_english_keeps_text_verbatim_and_pages():
    pages = [
        "Preamble text line\nArticle 1 Scope\nThis Act applies to all.\n",
        "Article 2 Definitions\n(a) personal data means data.\nmore\n",
    ]
    drafts, strategy = split_clauses(pages)
    assert strategy == "numbered"
    assert [d.clause_number for d in drafts] == [None, "1", "2"]
    assert drafts[1].text == "Article 1 Scope\nThis Act applies to all."
    assert drafts[1].heading == "Scope"
    assert drafts[2].page_start == 2 and drafts[0].page_start == 1
    assert [d.ordinal for d in drafts] == [0, 1, 2]


def test_split_chinese_and_arabic():
    zh, s1 = split_clauses(["第一条 为了保护个人信息。\n第二条 本法适用于境内。\n补充说明。"])
    assert s1 == "numbered" and [d.clause_number for d in zh] == ["一", "二"]
    assert zh[1].text.endswith("补充说明。")
    ar, s2 = split_clauses(["المادة 1 التعريفات\nنص\nالمادة 2 النطاق\nنص ثان"])
    assert s2 == "numbered" and [d.clause_number for d in ar] == ["1", "2"]


def test_split_falls_back_to_paragraphs_and_says_so():
    drafts, strategy = split_clauses(["First paragraph line one\nline two\n\nSecond paragraph\n"])
    assert strategy == "paragraph" and len(drafts) == 2
    assert (
        drafts[0].clause_number is None and drafts[0].text == "First paragraph line one\nline two"
    )


def test_split_line_mode_for_html():
    drafts, strategy = split_clauses(["alpha\nbeta\ngamma"], paragraph_mode="line")
    assert strategy == "line" and [d.text for d in drafts] == ["alpha", "beta", "gamma"]


def test_split_empty():
    assert split_clauses(["  \n \n"]) == ([], "empty")


def test_single_header_is_not_enough_for_numbered():
    _, strategy = split_clauses(["Article 1 Only one\ntext"])
    assert strategy == "paragraph"


# ---------------- 文档解析 ----------------


def test_parse_pdf_pages_and_text():
    pdf = make_pdf(
        [["Article 1 Scope", "This Act applies."], ["Article 2 Terms", "More text here."]]
    )
    doc = parse_document(pdf, "application/pdf")
    assert doc.kind == "pdf" and len(doc.pages) == 2
    assert "Article 1 Scope" in doc.pages[0] and "Article 2 Terms" in doc.pages[1]
    assert not doc.looks_scanned


def test_pdf_without_text_layer_looks_scanned():
    doc = parse_document(make_pdf([[], [], []]), "application/pdf")
    assert len(doc.pages) == 3 and doc.char_count == 0 and doc.looks_scanned


def test_detect_kind_by_magic_not_just_mime():
    assert detect_kind(b"%PDF-1.7 ...", "application/octet-stream") == "pdf"
    assert detect_kind(b"<!DOCTYPE html><html>", "") == "html"
    assert detect_kind(b"plain", "text/plain") == "text"
    with pytest.raises(ParseError):
        detect_kind(b"PK\x03\x04", "application/zip")


def test_parse_html_drops_scripts_and_keeps_text():
    html = b"<html><head><style>x{}</style></head><body><nav>menu</nav><script>evil()</script><h1>Title</h1><p>Body  text</p></body></html>"
    doc = parse_document(html, "text/html")
    assert doc.pages == ["Title\nBody text"]


def test_corrupt_pdf_raises_parse_error():
    with pytest.raises(ParseError):
        parse_document(b"%PDF-1.4\nthis is not a pdf at all", "application/pdf")


def test_headings_in_the_middle_of_a_line_are_split_when_they_continue_the_sequence():
    from app.ingestion.clauses import split_inline_headings

    page = "\n".join(
        [
            "الفصل 1 : يسري هذا القانون على كل معاملة.",
            "نص إضافي. الفصل 2 : يتولى المزود إصدار الشهادات وفقا لكراس الشروط.",
            "الفصل:3 يتعين على كل مزود مسك سجل إلكتروني.",
        ]
    )
    assert any(x.startswith("الفصل 2 :") for x in split_inline_headings([page])[0].splitlines())
    drafts, strategy = split_clauses([page])
    assert strategy == "numbered" and [d.clause_number for d in drafts] == ["1", "2", "3"]


def test_a_reference_to_another_article_is_never_taken_for_a_heading():
    from app.ingestion.clauses import split_inline_headings

    page = "\n".join(
        [
            "الفصل 1 : أحكام عامة.",
            "الفصل 2 : وفقا لكراس الشروط المنصوص عليه بالفصل 3 : من هذا القانون.",
        ]
    )
    # the reference is glued to a preposition (بالفصل), so it stays inside article 2
    assert len(split_inline_headings([page])[0].splitlines()) == 2


def test_latin_article_headings_inside_a_line_follow_the_same_rule():
    from app.ingestion.clauses import split_inline_headings

    page = "Art. 1 - La présente loi fixe les règles. Art. 2 - Le document électronique fait foi."
    assert split_inline_headings([page])[0].splitlines()[1].startswith("Art. 2 -")


def test_article_headings_are_never_removed_as_a_repeating_page_header():
    from app.ingestion.parsing import strip_running_headers

    pages = []
    for n in range(1, 11):  # one article heading at the top of every page
        pages.append(
            f"المادة ({n})\nنص المادة رقم {n} يشرح الالتزام ويحدد المطلوب على وجه مختلف {n * 7}"
        )
    cleaned, _ = strip_running_headers(pages)
    assert all(f"المادة ({n})" in cleaned[n - 1] for n in range(1, 11))
    drafts, _ = split_clauses(cleaned)
    assert [d.clause_number for d in drafts] == [f"({n})" for n in range(1, 11)]


def test_a_real_repeating_header_is_still_removed_next_to_article_headings():
    from app.ingestion.parsing import strip_running_headers

    body = "نص المادة يشرح الالتزام ويحدد المطلوب من المسؤول عن المعالجة بوضوح"
    pages = [f"الجريدة الرسمية - العدد 7\nالمادة ({n})\n{body}" for n in range(1, 11)]
    cleaned, removed = strip_running_headers(pages)
    assert removed and all("الجريدة الرسمية" not in p for p in cleaned)
    assert all(f"المادة ({n})" in cleaned[n - 1] for n in range(1, 11))


def test_a_dash_left_beside_an_arabic_article_number_is_not_part_of_the_number():
    from app.ingestion.clauses import clean_number

    assert clean_number("-1") == "1" and clean_number("12-") == "12" and clean_number(" –7 ") == "7"
    assert clean_number("(88)") == "(88)" and clean_number("26A") == "26A"
    assert clean_number("-") is None and clean_number(None) is None


def _german(count, with_contents=True):
    sec = chr(167)
    contents = (
        [f"{sec} {i}" + chr(10) + f"Titel {i}" for i in range(1, count + 1)]
        if with_contents
        else []
    )
    body = []
    for i in range(1, count + 1):
        body += [f"{sec} {i}", f"Titel {i}", f"(1) Text von Paragraf {i}. " * 3]
    return chr(10).join(contents + body)


def test_german_paragraph_headings_split_a_statute_into_numbered_sections():
    from app.ingestion.clauses import split_clauses

    drafts, strategy = split_clauses([_german(8)], paragraph_mode="line")
    numbers = [d.clause_number for d in drafts if d.clause_number]
    assert strategy == "numbered"
    assert numbers[-8:] == [str(i) for i in range(1, 9)]  # 正文按序；目录不另成条款序列
    assert any("Text von Paragraf 5" in d.text for d in drafts if d.clause_number == "5")


def test_a_lettered_section_such_as_1a_is_a_clause_of_its_own():
    from app.ingestion.clauses import split_clauses

    sec = chr(167)
    text = chr(10).join(
        f"{sec} {n}" + chr(10) + "Titel" + chr(10) + "Inhalt des Paragrafen mit genügend Text. " * 2
        for n in ("1", "1a", "1b", "2", "3")
    )
    numbers = [
        d.clause_number for d in split_clauses([text], paragraph_mode="line")[0] if d.clause_number
    ]
    assert numbers[-5:] == ["1", "1a", "1b", "2", "3"]


def test_a_note_in_the_document_head_is_not_taken_for_the_first_article():
    from app.ingestion.clauses import split_clauses

    head = [
        "Verordnung über etwas",
        "Fußnote",
        "Art. 13 Satz 1 dieser V am 1.9.2023 in Kraft getreten.",
    ]
    sec = chr(167)
    body = [
        f"{sec} {i}" + chr(10) + "Titel" + chr(10) + "Inhalt genug Text hier. " * 2
        for i in range(1, 6)
    ]
    drafts, _ = split_clauses([chr(10).join(head + body)], paragraph_mode="line")
    numbers = [d.clause_number for d in drafts if d.clause_number]
    assert numbers == ["1", "2", "3", "4", "5"]
    assert "Art. 13 Satz 1" in drafts[0].text  # 文字没丢，只是不当条款


def test_a_document_that_really_starts_mid_sequence_keeps_its_first_article():
    from app.ingestion.clauses import split_clauses

    sec = chr(167)
    body = [
        f"{sec} {i}" + chr(10) + "Titel" + chr(10) + "Inhalt genug Text hier. " * 2
        for i in (16, 17, 18, 19)
    ]
    numbers = [
        d.clause_number
        for d in split_clauses([chr(10).join(body)], paragraph_mode="line")[0]
        if d.clause_number
    ]
    assert numbers == ["16", "17", "18", "19"]


_EGOV = (
    '<?xml version="1.0" encoding="UTF-8"?><Law Lang="ja" LawType="Act"><LawNum>令和一年法律第一号</LawNum>'
    "<LawBody><LawTitle>試験法</LawTitle><MainProvision><Chapter><ChapterTitle>第一章　総則</ChapterTitle>"
    "<Article><ArticleCaption>（目的）</ArticleCaption><ArticleTitle>第一条</ArticleTitle>"
    "<Paragraph><ParagraphNum/><ParagraphSentence><Sentence>この法律は目的を定める。</Sentence></ParagraphSentence></Paragraph></Article>"
    "<Article><ArticleCaption>（定義）</ArticleCaption><ArticleTitle>第二条</ArticleTitle>"
    "<Paragraph><ParagraphNum/><ParagraphSentence><Sentence>用語の意味は次のとおり。</Sentence></ParagraphSentence>"
    "<Item><ItemTitle>一</ItemTitle><ItemSentence><Sentence>個人情報</Sentence></ItemSentence></Item></Paragraph></Article>"
    "</Chapter></MainProvision></LawBody></Law>"
)


def test_a_japanese_statute_in_official_xml_becomes_numbered_articles():
    from app.ingestion.clauses import split_clauses
    from app.ingestion.parsing import parse_document

    doc = parse_document(_EGOV.encode("utf-8"), "application/xml")
    assert doc.kind == "egov"
    lines = doc.pages[0].splitlines()
    assert (
        lines[0] == "試験法" and "第一条 （目的）" in lines and "この法律は目的を定める。" in lines
    )
    drafts, strategy = split_clauses(doc.pages, paragraph_mode="line")
    assert strategy == "numbered"
    assert [d.clause_number for d in drafts if d.clause_number] == ["一", "二"]
    assert "個人情報" in drafts[-1].text


def test_a_broken_xml_is_a_parse_error_not_a_crash():
    import pytest

    from app.ingestion.parsing import ParseError, parse_document

    with pytest.raises(ParseError):
        parse_document(b'<?xml version="1.0"?><Law Lang="ja"><LawBody>', "application/xml")


def _arabic_law(ref_line):
    fasl = "الفصل"
    parts = []
    for n in range(1, 13):
        parts.append(
            f"{fasl} {n} : نص الفصل رقم {n} من القانون وهو نص طويل بما يكفي لأن يكون فصلا. " * 2
        )
        if n == 7:
            parts.append(ref_line)
    return chr(10).join(parts)


def test_a_line_that_starts_with_a_reference_to_a_later_article_does_not_swallow_the_next_ones():
    from app.ingestion.clauses import split_clauses

    fasl = "الفصل"
    law = _arabic_law(f"{fasl} 34 من هذا القانون.")
    numbers = [
        d.clause_number for d in split_clauses([law], paragraph_mode="line")[0] if d.clause_number
    ]
    assert numbers == [str(n) for n in range(1, 13)]


def test_a_french_reference_at_the_start_of_a_wrapped_line_is_not_an_article():
    from app.ingestion.clauses import split_clauses

    body = []
    for n in range(1, 11):
        body.append(f"Art. {n}. - Texte de l'article {n} avec assez de mots pour une clause. " * 2)
        if n == 5:
            body.append("Art. 29 du présent décret sont fixées par arrêté.")
    numbers = [
        d.clause_number
        for d in split_clauses([chr(10).join(body)], paragraph_mode="line")[0]
        if d.clause_number
    ]
    assert numbers == [str(n) for n in range(1, 11)]


def test_a_real_gap_where_the_skipped_article_never_appears_is_kept():
    from app.ingestion.clauses import split_clauses

    body = [
        f"Article {n}. - Texte de l'article {n} avec assez de mots pour une clause. " * 2
        for n in (1, 2, 4, 5, 6)  # 第 3 条被废止，文中没有
    ]
    numbers = [
        d.clause_number
        for d in split_clauses([chr(10).join(body)], paragraph_mode="line")[0]
        if d.clause_number
    ]
    assert numbers == ["1", "2", "4", "5", "6"]


def test_article_number_reads_plain_numbers_in_every_written_form_and_nothing_else():
    from app.ingestion.clauses import article_number

    assert (
        article_number("12") == 12 and article_number("(12)") == 12 and article_number("-12") == 12
    )
    assert article_number("١٢") == 12 and article_number(" 7 ") == 7
    assert article_number("12a") is None and article_number("1.2") is None
    assert article_number(None) is None and article_number("") is None
    assert article_number("12345") is None  # 年份、编号等不是条号
    assert article_number("premier") == 1 and article_number("1er") == 1


def test_a_colon_before_the_article_number_is_not_part_of_the_number():
    from app.ingestion.clauses import article_number, clean_number

    assert clean_number(":89") == "89" and clean_number("：33") == "33"
    assert article_number(":89") == 89 and article_number("89-") == 89
    assert clean_number(":") is None


def test_french_upper_case_article_headings_are_recognised():
    from app.ingestion.clauses import article_number, split_clauses

    pages = [
        "DÉCRÈTE :\n\nARTICLE PREMIER. – On entend par autorité nationale l'autorité compétente.\n\n"
        "ART. 2. – En application de l'article 9 de la loi précitée, le prestataire agit.\n\n"
        "ART. 3. – Le certificat est délivré après examen du dossier complet.\n\n"
        "ART. 4. – Le certificat qualifié d'authentification comporte les mentions requises.\n\n"
        "ART. 5. – Le prestataire du service conserve les preuves pendant cinq ans."
    ]
    drafts, _ = split_clauses(pages)
    numbers = [article_number(d.clause_number) for d in drafts if d.clause_number]
    assert numbers == [1, 2, 3, 4, 5]


def test_section_lines_are_structure_when_the_document_numbers_its_articles():
    from app.ingestion.clauses import article_number, split_clauses

    body = "Le prestataire conserve les éléments de preuve pendant la durée prévue par la loi."
    pages = [
        "\n".join(
            [f"Article {n}\n{body} {n}" for n in range(1, 6)]
            + ["Section 1. – De la signature électronique sécurisée"]
            + [f"Article {n}\n{body} {n}" for n in range(6, 12)]
        )
    ]
    drafts, _ = split_clauses(pages)
    numbers = [article_number(d.clause_number) for d in drafts if d.clause_number]
    assert numbers == list(range(1, 12))  # “Section 1.”不把序列重置，6–11 条都在


def test_arabic_first_article_and_bold_markers_are_handled():
    from app.ingestion.clauses import article_number, split_clauses
    from app.ingestion.page_replace import clean_model_markup

    body = "يضبط هذا القانون القواعد العامة المنظمة للمبادلات والتجارة الإلكترونية"
    raw = "\n".join(
        [f"الفصل الأول : {body}", f"**الفصل 2** : {body}", f"**الفصل 3** : {body}"]
        + [f"**الفصل {n}** : {body}" for n in range(4, 8)]
    )
    cleaned = clean_model_markup(raw)
    assert "**" not in cleaned
    drafts, _ = split_clauses([cleaned])
    assert [article_number(d.clause_number) for d in drafts] == list(range(1, 8))
    # “الفصل الأول أحكام عامة”（没有冒号）是章标题，不是第一条
    chapter_pages = [
        "\n".join(["الفصل الأول أحكام عامة"] + [f"الفصل {n} : {body}" for n in range(1, 5)])
    ]
    chapter, _ = split_clauses(chapter_pages)
    assert [article_number(d.clause_number) for d in chapter if d.clause_number] == [1, 2, 3, 4]
    assert not any((d.clause_number or "") == "الأول" for d in chapter)


def test_arabic_article_heading_with_a_colon_and_no_space_is_recognised():
    from app.ingestion.clauses import article_number, split_clauses

    body = "تتولى المصلحة المختصة إصدار الرخص وفق الشروط المحددة في هذا القانون"
    pages = [
        "\n".join(
            [f"المادة {n}\n{body}" for n in (1, 2)]
            + [f"المادة:3\n{body}", f"المادة: 4:\n{body}", f"المادة 5\n{body}"]
        )
    ]
    drafts, _ = split_clauses(pages)
    assert [article_number(d.clause_number) for d in drafts if d.clause_number] == [1, 2, 3, 4, 5]


def test_multilevel_decimal_clauses_and_ocr_transposed_madda_are_split():
    from app.ingestion.clauses import split_clauses

    cloud_pages = [
        "\n".join(
            [
                "3.1.1 Access Control Policy",
                "The Cloud Service Provider must establish, document, approve, and periodically review a comprehensive access control policy based on business and security requirements across all cloud environments.",
                "3.1.1.1 User Registration",
                "Formal user registration and de-registration procedures must be implemented for assigning and revoking access rights across all multi-tenant cloud information systems and administrative interfaces.",
                "3.1.1.2 Privilege Management",
                "The allocation and use of privileged access rights must be strictly restricted, logged, and controlled through formal authorization workflows approved by the security officer.",
                "3.1.2.1 Password Management",
                "The allocation of secret authentication information must be controlled through a formal management process that enforces strong complexity and periodic rotation rules.",
            ]
        )
    ]
    drafts, strategy = split_clauses(cloud_pages)
    assert strategy == "numbered"
    assert [d.clause_number for d in drafts] == ["3.1.1", "3.1.1.1", "3.1.1.2", "3.1.2.1"]

    ar_ocr_pages = [
        "\n".join(
            [
                "املادة 1\nتحدد هذه المقتضيات شروط المصادقة على المركبات ذات المحرك ومقطوراتها وأجهزتها التقنية.",
                "امل\ufffeادة 2\nيجب إيداع ملف المصادقة لدى المصلحة المختصة قبل عرض المركبة في السوق الوطنية.",
                "المادة 3\nتسلم شهادة المصادقة بعد التحقق من المطابقة التقنية للمركبة مع المعايير المعتمدة.",
                "املادة 4\nتخضع المركبات المستوردة للمراقبة التقنية والوثائقية عند دخولها إلى التراب الوطني.",
            ]
        )
    ]
    ar_drafts, ar_strategy = split_clauses(ar_ocr_pages)
    assert ar_strategy == "numbered"
    assert [d.clause_number for d in ar_drafts] == ["1", "2", "3", "4"]


def test_clean_translation_always_drops_long_arabic_parentheticals():
    from app.ingestion.noise import clean_translation

    raw = "数据控制者应当向国家个人数据保护委员会（الهيئة الوطنية لحماية المعطيات ذات الطابع الشخصي بالمملكة）提交事先申报。"
    cleaned = clean_translation(raw)
    assert cleaned == "数据控制者应当向国家个人数据保护委员会提交事先申报。"

