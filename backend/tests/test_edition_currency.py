from datetime import date
from types import SimpleNamespace

from app.analysis.edition_currency import kind_weight, superseded_documents
from app.analysis.retrieval import ClauseIndex


def test_older_base_edition_with_the_same_title_is_superseded_but_other_instruments_are_not():
    entries = [
        ("new", "fam-a", "BASE", "Data Act", date(2021, 12, 31)),
        ("old", "fam-a", "BASE", "Data  Act", date(2012, 12, 3)),  # same text, older edition
        ("amend", "fam-a", "AMENDMENT", "Data Act", date(2020, 12, 10)),
        ("regs", "fam-a", "BASE", "Data Regulations 2014", date(2014, 1, 1)),  # other instrument
        ("other", "fam-b", "BASE", "Other Act", date(2001, 1, 1)),
    ]
    out = superseded_documents(entries)
    assert set(out) == {"old"}
    assert out["old"]["superseded_by_edition"] == "2021-12-31"


def test_unknown_family_kind_title_or_date_is_never_treated_as_superseded():
    entries = [
        ("a", None, "BASE", "T", date(2020, 1, 1)),
        ("b", None, "BASE", "T", date(2010, 1, 1)),
        ("c", "fam", None, "T", date(2010, 1, 1)),
        ("d", "fam", "BASE", "T", None),
        ("e", "fam", "BASE", "T", date(2020, 1, 1)),
        ("f", "fam", "BASE", None, date(2010, 1, 1)),
    ]
    assert superseded_documents(entries) == {}


def test_guidance_ranks_below_binding_text_only_when_scores_are_comparable():
    assert kind_weight("LAW") == kind_weight("") == kind_weight(None) == 1.0
    assert kind_weight("REGULATOR_GUIDANCE") < 1.0
    assert kind_weight("HISTORICAL_GUIDANCE") < kind_weight("REGULATOR_GUIDANCE")

    def clause(cid, kind):
        return SimpleNamespace(
            clause_id=cid,
            text="consent to collect personal data",
            translated_text="",
            heading="consent",
            instrument_kind=kind,
        )

    # Identical wording: the guidance clause would win the id tie-break without the weight.
    index = ClauseIndex([clause("a-guide", "REGULATOR_GUIDANCE"), clause("b-law", "LAW")])
    ranked = [c.clause_id for c, _ in index.rank("consent", "consent personal data")]
    assert ranked == ["b-law", "a-guide"]


def test_general_documents_rank_below_core_ones_only_when_scores_are_comparable():
    def clause(cid, relevance, text="consent to collect personal data"):
        return SimpleNamespace(
            clause_id=cid,
            text=text,
            translated_text="",
            heading="consent",
            instrument_kind="LAW",
            relevance=relevance,
        )

    # 词句相同、id 排在前面的是“弱相关”文件：不加权时它会赢下同分的排序
    tied = ClauseIndex([clause("a-general", "related"), clause("b-core", "relevant")])
    assert [c.clause_id for c, _ in tied.rank("consent", "consent personal data")] == [
        "b-core",
        "a-general",
    ]
    # 弱相关文件明显更贴题时，仍然排在前面：降权只是平手时的次序，不是过滤
    better = ClauseIndex(
        [
            clause("a-general", "related", "consent to collect personal data consent consent"),
            clause("b-core", "relevant", "an unrelated provision about fees"),
        ]
    )
    assert [c.clause_id for c, _ in better.rank("consent", "consent personal data")][0] == (
        "a-general"
    )
