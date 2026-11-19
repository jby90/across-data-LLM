from sqlalchemy import select

from app.ingestion.archive import ArchiveStore
from app.ingestion.models import Clause, DocumentVersion, PipelineEvent
from app.ingestion.parsing import parse_document, strip_running_headers

from .helpers import make_pdf
from .test_pipeline import Site, add_site, run


def test_strips_repeating_header_footer_and_page_numbers():
    pages = [
        "Data Protection Act 2012\nArticle 1 Scope\nBody one\nPage 1 of 4",
        "Data Protection Act 2012\nbody continues\nPage 2 of 4",
        "Data Protection Act 2012\nArticle 2 Terms\nBody two\nPage 3 of 4",
        "Data Protection Act 2012\nArticle 3 More\nBody three\nPage 4 of 4",
    ]
    cleaned, removed = strip_running_headers(pages)
    assert removed == ["Data Protection Act #", "Page # of #"]
    assert cleaned[0] == "Article 1 Scope\nBody one"
    assert all("Page" not in p and "Data Protection Act" not in p for p in cleaned)


def test_keeps_content_that_does_not_repeat():
    pages = [
        "Article 1 Scope\nBody one\nSee the schedule",
        "Article 2 Terms\nBody two\nOther words here",
        "Article 3 More\nBody three\nFinal words",
    ]
    cleaned, removed = strip_running_headers(pages)
    assert removed == [] and cleaned == pages


def test_too_few_pages_are_left_untouched():
    pages = ["Header\nbody a\nPage 1", "Header\nbody b\nPage 2"]
    assert strip_running_headers(pages) == (pages, [])


def test_only_edge_lines_are_removed_not_identical_body_lines():
    pages = [
        "Header X\nArticle 1\nthis exact line\nmore\nfooter text one\nPage 1",
        "Header X\nArticle 2\nthis exact line\nmore\nfooter text two\nPage 2",
        "Header X\nArticle 3\nthis exact line\nmore\nfooter text three\nPage 3",
    ]
    cleaned, _ = strip_running_headers(pages)
    assert all("this exact line" in p for p in cleaned)  # 正文里重复的行不是页眉,保留
    assert all("Header X" not in p and "Page" not in p for p in cleaned)


def test_arabic_indic_digits_in_page_numbers():
    pages = [f"عنوان ثابت\nنص {i}\nصفحة {d}" for i, d in enumerate("١٢٣٤", 1)]
    cleaned, removed = strip_running_headers(pages)
    assert "صفحة #" in removed and all("صفحة" not in p for p in cleaned)


def test_real_pdf_pages_are_cleaned_end_to_end_inputs():
    pdf = make_pdf(
        [
            ["ACME Act 2012", "Article 1 Scope", "Applies to data.", "Page 1"],
            ["ACME Act 2012", "continued text", "Page 2"],
            ["ACME Act 2012", "Article 2 Transfer", "Needs approval.", "Page 3"],
        ]
    )
    doc = parse_document(pdf, "application/pdf")
    cleaned, removed = strip_running_headers(doc.pages)
    assert "ACME Act #" in removed and "Page #" in removed
    assert all("ACME" not in p and "Page" not in p for p in cleaned)


async def test_pipeline_removes_furniture_from_clauses_and_logs_it(session_factory, tmp_path):
    state = Site()
    state.pdf = make_pdf(
        [
            ["ACME Act 2012", "Article 1 Scope", "Applies to data.", "Page 1"],
            ["ACME Act 2012", "continued text", "Page 2"],
            ["ACME Act 2012", "Article 2 Transfer", "Needs approval.", "Page 3"],
        ]
    )
    site_id = await add_site(
        session_factory, strategy="DIRECT", entry="https://example.org/laws/act.pdf", config={}
    )
    _, archive = await run(session_factory, tmp_path, state, site_id)
    async with session_factory() as s:
        v = (await s.execute(select(DocumentVersion))).scalar_one()
        clauses = (
            (
                await s.execute(
                    select(Clause).where(Clause.version_id == v.id).order_by(Clause.ordinal)
                )
            )
            .scalars()
            .all()
        )
        text = "\n".join(c.original_text for c in clauses)
        assert "ACME" not in text and "Page" not in text and "continued text" in text
        events = (
            (await s.execute(select(PipelineEvent).where(PipelineEvent.step == "clean")))
            .scalars()
            .all()
        )
        assert len(events) == 1 and "ACME Act #" in events[0].message
        # 原文件在归档里完整保留,页眉页脚没有丢
        assert b"ACME Act 2012" in archive.read(v.archive_path)
    assert isinstance(archive, ArchiveStore)


def test_gazette_mastheads_are_removed_even_in_a_two_page_text_but_cited_gazettes_stay():
    from app.ingestion.parsing import strip_gazette_mastheads

    cases_removed = [
        "Page 3028 Journal Officiel de la République Tunisienne — 19 septembre 2008 N° 76",
        "N° 49 Journal Officiel de la République Tunisienne — 16 avril 2024 Page 1261",
        "الجريدة الرسمية - العدد ٥ مكرر (أ) في ٤ فبراير سنة ٢٠٠٩",
        "٢٨ الجريدة الرسمية - العدد ٣٧ مكرر (ب) في ١٥ سبتمبر سنة ٢٠٢٠",
        "الجريدة الرسمية - العدد ٤ - ٢٠٢٦/١/٢٢",
    ]
    for line in cases_removed:
        out, n = strip_gazette_mastheads(f"نص قبل\n{line}\nنص بعد")
        assert n == 1 and line not in out and "نص قبل" in out and "نص بعد" in out, line
    # 页码单独成行紧挨报头：一并去掉；离得远的数字行不动
    out, _ = strip_gazette_mastheads("a\n٢٥\nالجريدة الرسمية - العدد ٣٢ في ١٤ أغسطس سنة ٢٠١٨\nb\n٧\nc")
    assert out == "a\nb\n٧\nc"
    # 正文里引用公报的句子不是报头
    for cite in [
        "Cette loi a été publiée au Journal Officiel de la République Tunisienne n° 12 du 3 mars 2004.",
        "نُشر القانون في الجريدة الرسمية العدد ١٢ بتاريخ ٣ مارس ٢٠٠٤ ويعمل به من اليوم التالي لنشره.",
        "Vu le Journal Officiel",
    ]:
        assert strip_gazette_mastheads(cite) == (cite, 0), cite


def test_running_headers_reports_the_masthead_pass_for_short_documents():
    pages = ["Art. 1\ntext\nJournal Officiel de la République Tunisienne — 7 mai 2002 Page 1117", "Art. 2\ntext"]
    cleaned, removed = strip_running_headers(pages)
    assert removed == ["公报报头"] and all("Journal" not in p for p in cleaned)
