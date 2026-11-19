"""Reading a regulation one article at a time: the pieces of an over-long article stored after the
last article are put back where they belong, amendment notes stay with their article, and
article numbers read the same way in every language."""

from dataclasses import dataclass

from app.knowledge.reading import article_label, pieces, reading_units


@dataclass
class R:
    id: str
    ordinal: int
    clause_number: str | None
    heading: str | None
    page_start: int | None
    original_text: str


def _doc():
    return [
        R("pre", 0, None, None, 1, "An Act to govern the collection of personal data."),
        R("a1", 1, "1", "Short title", 1, "Short title\n1. This Act is the Personal Data Protection Act."),
        R("a2", 2, "2", "Interpretation", 1, "Interpretation\n2.—(1) In this Act —\n“advisory committee” means …"),
        R("note", 3, None, None, 1, "[40/2020]"),
        R("a3", 4, "3", "Purpose", 2, "Purpose\n3. The purpose of this Act is …"),
        # 定义条太长，当年被切成两段，存放在全文最后
        R("p1", 5, "2.1", "Interpretation", 1, "“personal data” means data, whether true or not, about an individual …"),
        R("p2", 6, "2.2", "Interpretation", 1, "“processing” means the carrying out of any operation …"),
        # 真正的第 2.1 段（正文以自己的编号开头）不是切出来的片段
        R("g21", 7, "2.1", "Guidance", 3, "2.1 Organisations should …"),
    ]


def test_the_pieces_of_a_long_article_are_read_inside_it_and_notes_stay_with_their_article():
    units = reading_units(_doc())
    assert [u.head.id for u in units] == ["pre", "a1", "a2", "a3", "g21"]
    interpretation = units[2]
    assert interpretation.ids == ["a2", "p1", "p2", "note"]  # 各段在本条原文之后、修订注记之前
    assert set(pieces(_doc())) == {"p1", "p2"}


def test_a_document_without_pieces_reads_as_stored():
    rows = [r for r in _doc() if r.id not in ("p1", "p2")]
    assert [u.ids for u in reading_units(rows)] == [["pre"], ["a1"], ["a2", "note"], ["a3"], ["g21"]]


def test_article_numbers_read_the_same_in_every_language():
    assert article_label("43") == "第43条"
    assert article_label("premier") == "第1条"
    assert article_label("الأولى") == "第1条"
    assert article_label("الحادية عشرة") == "第11条"
    assert article_label("الخامسة والعشرون") == "第25条"
    assert article_label("(27)") == "第27条"
    assert article_label("19.8") == "19.8"  # 指南的段号照印
    assert article_label("") == ""


def test_pieces_of_a_guidance_paragraph_numbered_like_19_8_join_it():
    rows = [
        R("p18", 0, "19.7", None, 124, "19.7 Organisations are encouraged …"),
        R("p19", 1, "19.8", None, 124, "19.8 The examples below … In order to perform"),
        R("p20", 2, "19.9", None, 128, "19.9 In setting out contractual clauses …"),
        R("x1", 3, "19.8.1", None, 124, "its obligation under its contract with Karen …"),
        R("x2", 4, "19.8.2", None, 124, "(including personal data such as his identification …"),
    ]
    units = reading_units(rows)
    assert [u.ids for u in units] == [["p18"], ["p19", "x1", "x2"], ["p20"]]
