import re
from types import SimpleNamespace

import pytest

from app.analysis import golden_eval as ge
from app.analysis.golden_spec import TOPICS


def cand(url, label, text, clause_id="c", title="T", translated=""):
    return SimpleNamespace(
        source_url=url,
        label=label,
        text=text,
        clause_id=clause_id,
        document_title=title,
        translated_text=translated,
    )


def expected(url, label, text):
    return {
        "document_title": "T",
        "source_url": url,
        "clause_label": label,
        "source_text_sha256": ge.text_sha256(text),
    }


def case_with(groups):
    return {"id": "X-K", "required_groups": groups}


def test_frozen_suite_covers_five_countries_with_three_question_styles():
    suite = ge.load_suite()
    cases = suite["cases"]
    assert len({c["id"] for c in cases}) == len(cases)
    assert {c["country_code"] for c in cases} == {"SG", "EG", "TN", "MA", "LB"}
    by_topic = {}
    for c in cases:
        by_topic.setdefault(c["topic_id"], []).append(c)
    for topic_id, rows in by_topic.items():
        assert sorted(r["style"] for r in rows) == sorted(ge.STYLES), topic_id
        assert len({r["question"] for r in rows}) == 3, topic_id
        assert len({r["country_code"] for r in rows}) == 1
    for code in ("SG", "EG", "TN", "MA", "LB"):
        assert sum(c["country_code"] == code for c in cases) >= 24
    assert {c["dimension_code"] for c in cases} <= {f"D{i}" for i in range(1, 9)}
    assert suite["human_review"] is None


def test_frozen_expectations_are_pinned_to_source_hashes():
    for case in ge.load_suite()["cases"]:
        assert case["required_groups"]
        for group in case["required_groups"]:
            assert group
            for e in group:
                assert re.fullmatch(r"[0-9a-f]{64}", e["source_text_sha256"])
                assert e["source_url"].startswith("http")
                assert e["clause_label"] and e["document_title"]


def test_questions_never_quote_article_numbers_of_the_answer():
    pattern = re.compile(
        r"第\s*[0-9０-９]+\s*[条條章节]|[Aa]rticle\s*\d|\bs\.?\s?\d{1,2}[A-Z]?\b|[§]"
    )
    for case in ge.load_suite()["cases"]:
        assert not pattern.search(case["question"]), case["id"]


def test_frozen_suite_matches_spec_so_it_cannot_go_stale():
    suite = {c["id"]: c for c in ge.load_suite()["cases"]}
    assert len(suite) == 3 * len(TOPICS)
    for topic in TOPICS:
        for style in ge.STYLES:
            case = suite[f"{topic['topic_id']}-{style[0].upper()}"]
            assert case["question"] == topic["questions"][style]
            assert case["country_code"] == topic["country_code"]
            assert case["dimension_code"] == topic["dimension_code"]
            assert len(case["required_groups"]) == len(topic["required_groups"])


def test_resolve_locator_requires_exactly_one_clause():
    pool = [
        cand("u1", "13", "alpha", title="Act A"),
        cand("u2", "13", "beta", title="Act A"),
        cand("u2", "14", "gamma", title="Act A", translated="含有 关键词 的译文"),
    ]
    with pytest.raises(ge.LocatorError):
        ge.resolve_locator(pool, {"doc": "Act A", "label": "13"})  # two matches
    with pytest.raises(ge.LocatorError):
        ge.resolve_locator(pool, {"doc": "Act A", "label": "99"})  # none
    assert ge.resolve_locator(pool, {"doc": "Act A", "label": "13", "url": "u2"}).text == "beta"
    assert (
        ge.resolve_locator(pool, {"doc": "Act A", "label": "14", "anchor": "关键词"}).text
        == "gamma"
    )


def test_build_suite_reports_every_unresolved_locator_at_once():
    topics = [
        {
            "topic_id": "ZZ-01",
            "country_code": "ZZ",
            "dimension_code": "D1",
            "topic": "t",
            "required_groups": [[{"doc": "A", "label": "1"}], [{"doc": "A", "label": "2"}]],
            "questions": {s: s for s in ge.STYLES},
        }
    ]
    with pytest.raises(ge.LocatorError) as caught:
        ge.build_suite(topics, {"ZZ": []})
    assert str(caught.value).count("ZZ-01") == 2


def test_retrieval_scoring_counts_groups_with_alternatives_and_rank():
    a, b, c = (cand("u", lbl, f"text {lbl}", clause_id=lbl) for lbl in "ABC")
    case = case_with(
        [
            [expected("u", "B", "text B"), expected("u", "C", "text C")],  # any alternative
            [expected("u", "Z", "text Z")],  # never retrieved
        ]
    )
    corpus = {ge.candidate_key(x) for x in (a, b, c)} | {
        ge.expected_key(expected("u", "Z", "text Z"))
    }
    score = ge.score_retrieval(case, [a, b, c], corpus)
    assert (score["groups_total"], score["groups_found"]) == (2, 1)
    assert score["first_gold_rank"] == 2 and score["top1_is_gold"] is False
    assert score["source_drift"] == []


def test_changed_text_does_not_inherit_old_expectation_and_is_flagged_as_drift():
    old = expected("u", "1", "original text")
    changed = cand("u", "1", "edited text", clause_id="1")
    score = ge.score_retrieval(case_with([[old]]), [changed], {ge.candidate_key(changed)})
    assert score["groups_found"] == 0
    assert score["source_drift"] == [["1"]]
    assert score["top1_is_gold"] is False


def test_citation_counts_are_strict_and_ignore_unknown_clause_ids():
    gold = cand("u", "1", "one", clause_id="id1")
    other = cand("u", "2", "two", clause_id="id2")
    case = case_with([[expected("u", "1", "one")]])
    by_id = {"id1": gold, "id2": other}
    findings = [{"clause_id": "id1"}, {"clause_id": "id2"}, {"clause_id": "ghost"}]
    assert ge.citation_counts(case, findings, by_id) == {"cited_correct": 1, "cited_groups": 1}
    assert ge.citation_counts(case, [{"clause_id": "id2"}], by_id)["cited_correct"] == 0


def _row(**kw):
    row = {
        "country_code": "SG",
        "style": "keyword",
        "dimension_code": "D1",
        "groups_total": 2,
        "groups_found": 2,
        "first_gold_rank": 1,
        "top1_is_gold": True,
        "source_drift": [],
        "generated": False,
    }
    row.update(kw)
    return row


def test_aggregate_leaves_unmeasured_metrics_empty_and_gates_never_pass_on_none():
    agg = ge.aggregate([_row(), _row(groups_found=1, first_gold_rank=3, top1_is_gold=False)])
    overall = agg["overall"]
    assert overall["recall_at_k"] == 3 / 4
    assert overall["hit_at_1"] == 1 / 2
    assert overall["location_accuracy"] is None and overall["fact_consistency"] is None
    gates = ge.evaluate_gates(overall)
    assert gates["recall"]["status"] == "未达标"
    assert gates["location"]["status"] == "未测量"
    assert gates["fact_consistency"]["status"] == "未测量"


def test_gates_use_documented_thresholds_inclusively():
    overall = {"recall_at_k": 0.90, "location_accuracy": 0.7999, "fact_consistency": 0.90}
    gates = ge.evaluate_gates(overall)
    assert gates["recall"]["status"] == "达标"
    assert gates["location"]["status"] == "未达标"
    assert gates["fact_consistency"]["status"] == "达标"
    assert ge.GATES == {"recall": 0.90, "location": 0.80, "fact_consistency": 0.90}


def test_findings_metrics_only_count_answered_questions():
    answered = _row(
        generated=True,
        findings_total=4,
        cited_correct=3,
        cited_groups=2,
        faithful_findings=4,
        judged_findings=4,
        supported_findings=3,
    )
    agg = ge.aggregate([answered, _row(country_code="EG")])
    overall = agg["overall"]
    assert overall["location_accuracy"] == 3 / 4
    assert overall["fact_consistency"] == 3 / 4
    assert overall["judge_coverage"] == 1
    assert agg["by_country"]["EG"]["location_accuracy"] is None
    assert set(agg["by_country"]) == {"SG", "EG"}


def _cands(*ids):
    return [cand("u", f"L{i}", f"text {i}", clause_id=i) for i in ids]


async def test_relevance_judge_maps_verdicts_and_never_guesses(monkeypatch):
    from app.analysis import llm

    async def good(messages, **kwargs):
        rows = [{"index": i, "relevant": v, "reason": "x"} for i, v in enumerate(["yes", "no"])]
        return SimpleNamespace(data={"judgments": rows}, total_tokens=7)

    monkeypatch.setattr(llm, "call_llm_json", good)
    cfg = {"model": "m", "timeout": 5}
    case = {"topic": "t", "question": "q"}
    verdicts, tokens, error = await ge.judge_relevance(case, _cands("a", "b"), cfg)
    assert verdicts == {0: "yes", 1: "no"} and tokens == 7 and error is None

    async def bad(messages, **kwargs):  # missing index, invalid value
        rows = [{"index": 0, "relevant": "maybe", "reason": "x"}]
        return SimpleNamespace(data={"judgments": rows}, total_tokens=3)

    monkeypatch.setattr(llm, "call_llm_json", bad)
    verdicts, _, error = await ge.judge_relevance(case, _cands("a", "b"), cfg)
    assert verdicts == {} and error  # no verdict, nothing counted as relevant


def test_relevance_counts_follow_the_cited_clause_not_a_frozen_list():
    cands = _cands("a", "b", "c")
    verdicts = {0: "yes", 1: "partial", 2: "no"}
    findings = [{"clause_id": "a"}, {"clause_id": "b"}, {"clause_id": "ghost"}]
    counts = ge.relevance_counts(verdicts, cands, findings)
    assert counts == {
        "judged_relevance_n": 3,
        "relevant_in_topk": 1,
        "top1_relevant": True,
        "cited_judged": 2,
        "cited_relevant": 1,
    }


def test_judged_metrics_aggregate_and_gate_independently_of_the_strict_ones():
    answered = _row(
        generated=True,
        findings_total=2,
        judged_relevance_n=5,
        relevant_in_topk=3,
        top1_relevant=True,
        cited_judged=2,
        cited_relevant=2,
    )
    miss = _row(
        generated=True,
        judged_relevance_n=5,
        relevant_in_topk=0,
        top1_relevant=False,
        cited_judged=0,
        cited_relevant=0,
    )
    overall = ge.aggregate([answered, miss])["overall"]
    assert overall["judged_precision_at_k"] == 3 / 10
    assert overall["judged_answerable"] == 1 / 2 and overall["judged_top1"] == 1 / 2
    assert overall["judged_location"] == 1
    gates = ge.evaluate_gates(overall)
    assert gates["recall_judged"]["status"] == "未达标"
    assert gates["location_judged"]["status"] == "达标"
    unmeasured = ge.evaluate_gates(ge.aggregate([_row()])["overall"])
    assert unmeasured["recall_judged"]["status"] == "未测量"


async def test_corpus_fingerprint_changes_only_for_the_country_whose_data_changed(session_factory):
    from app.analysis.golden_cli import corpus_fingerprints
    from app.ingestion.models import Clause, DocumentVersion

    from .review_s1_s2_contracts import make_document

    async with session_factory() as session:
        _, _, _ = await make_document(session, ["Clause one about personal data."])
        await session.commit()
    first = await corpus_fingerprints(session_factory)
    assert list(first) == ["SG"] and await corpus_fingerprints(session_factory) == first
    async with session_factory() as session:
        version = (await session.scalars(__import__("sqlalchemy").select(DocumentVersion))).first()
        session.add(
            DocumentVersion(
                document_id=version.document_id,
                version_no=2,
                content_hash="b" * 64,
                archive_path="SG/new.pdf",
                mime_type="application/pdf",
                size_bytes=1,
                final_url=version.final_url,
                state="parsed",
            )
        )
        await session.commit()
    second = await corpus_fingerprints(session_factory)
    assert second["SG"] != first["SG"]
    assert Clause  # 新版本没有条款也算数据变动：文本换了，旧评测结果就不能再用
