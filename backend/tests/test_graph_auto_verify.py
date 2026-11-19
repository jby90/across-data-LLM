"""图谱自动核验：机械闸门、遗漏不算错、主体错不放行、二次核对、模型不可用留给人。"""

import pytest

from app.knowledge import graph_auto_verify as gv

CLAUSE = "The controller shall notify the Commission within 72 hours of becoming aware of a data breach. A fine of up to 10,000 dollars applies."


def edge(**over):
    base = {
        "relation_type": "REQUIRES", "source_type": "INSTRUMENT", "source_name": "数据保护法",
        "target_type": "OBLIGATION", "target_name": "数据泄露须在72小时内通知监管机构",
        "evidence_quote": "shall notify the Commission within 72 hours", "clause": CLAUSE, "clause_current": True,
    }
    return {**base, **over}


def node(**over):
    base = {
        "node_type": "OBLIGATION", "name_zh": "72小时内通知", "summary": "", "name_orig": "notify the Commission within 72 hours",
        "party": None, "condition": None, "clause": CLAUSE, "clause_current": True,
    }
    return {**base, **over}


def test_mechanical_gates_stop_what_cannot_be_checked_without_asking_a_model():
    assert gv.mechanical_edge(edge()) is None
    assert "不在所引条款" in gv.mechanical_edge(edge(evidence_quote="shall notify the Ministry within 24 hours"))
    assert "现行版本" in gv.mechanical_edge(edge(clause_current=False))
    assert "没有可核对的引文" in gv.mechanical_edge(edge(evidence_quote=""))
    assert gv.mechanical_node(node()) is None
    assert "没有依据条款" in gv.mechanical_node(node(clause=""))
    # 节点自带的原文短语不是改写就是译文时很常见，不能因此拒绝：交给模型对照条文判断
    assert gv.mechanical_node(node(name_orig="a phrase that the clause never contains")) is None
    assert "现行版本" in gv.mechanical_node(node(clause_current=False))
    # 空白差异（PDF 折行）不影响引文比对
    assert gv.mechanical_edge(edge(evidence_quote="shall  notify the\nCommission within 72 hours")) is None


def fake(monkeypatch, answers):
    calls = []

    async def ask(system, payload, clause, quote, window):
        calls.append(window)
        return answers.pop(0)

    monkeypatch.setattr(gv, "_ask", ask)
    return calls


async def test_a_faithful_statement_is_verified_and_an_omission_is_only_a_note(monkeypatch):
    calls = fake(monkeypatch, [{"faithful": "yes", "defects": [], "omissions": "条文还写明了主体是数据控制者", "reason": ""}])
    out = await gv.verify_statement("node", node())
    assert out["verdict"] == "auto" and out["stage"] == 1 and "数据控制者" in out["note"] and calls == [3500]


async def test_a_wrong_party_is_not_waved_through_and_two_readings_agreeing_retire_it(monkeypatch):
    bad = {"faithful": "no", "defects": ["wrong_party"], "omissions": "", "reason": "条文的主体是数据控制者，不是处理者"}
    calls = fake(monkeypatch, [bad, bad])
    out = await gv.verify_statement("node", node(party="数据处理者"))
    assert out["verdict"] == "retire" and out["stage"] == 2 and "义务主体与条文不符" in out["note"]
    assert out["defects"] == ["wrong_party"] and calls == [3500, 6000]


async def test_readings_that_disagree_or_doubt_go_to_a_person_not_to_retirement(monkeypatch):
    no = {"faithful": "no", "defects": ["not_in_clause"], "omissions": "", "reason": "条文没有"}
    unsure = {"faithful": "unsure", "defects": [], "omissions": "", "reason": "条文被动句，主体不明"}
    for answers in ([no, unsure], [unsure, unsure], [unsure, no]):
        fake(monkeypatch, list(answers))
        assert (await gv.verify_statement("node", node()))["verdict"] == "human"


async def test_the_second_independent_reading_can_clear_what_the_first_doubted(monkeypatch):
    calls = fake(monkeypatch, [
        {"faithful": "unsure", "defects": [], "omissions": "", "reason": "条文被动句"},
        {"faithful": "yes", "defects": [], "omissions": "", "reason": ""},
    ])
    out = await gv.verify_statement("edge", edge())
    assert out == {"verdict": "auto", "note": "二次核对通过", "defects": [], "stage": 2} and calls == [3500, 6000]


async def test_an_unavailable_model_leaves_the_statement_for_a_person_never_for_auto(monkeypatch):
    fake(monkeypatch, [None])
    out = await gv.verify_statement("edge", edge())
    assert out["verdict"] == "human" and "暂不可用" in out["note"]


async def test_a_mechanical_failure_never_calls_the_model(monkeypatch):
    calls = fake(monkeypatch, [])
    out = await gv.verify_statement("edge", edge(evidence_quote="not in the clause at all, sorry"))
    assert out["verdict"] == "human" and out["stage"] == 0 and calls == []


async def test_run_many_keeps_order(monkeypatch):
    fake(monkeypatch, [{"faithful": "yes", "defects": [], "omissions": "", "reason": ""}] * 3)
    res = await gv.run_many([("node", node()), ("node", node()), ("edge", edge())], concurrency=1)
    assert [r["verdict"] for r in res] == ["auto"] * 3


@pytest.mark.parametrize("kind", ["node", "edge"])
def test_payloads_never_carry_the_clause_text_itself(kind):
    p = gv.node_payload(node()) if kind == "node" else gv.edge_payload(edge())
    assert CLAUSE not in str(p)


async def test_the_batch_writes_verified_retired_and_person_needed_and_never_touches_a_decided_item(
    session_factory, monkeypatch
):
    from app.knowledge import graph_verify_run as run
    from app.knowledge.models import KgEdge, KgNode

    from .review_s1_s2_contracts import make_document

    async with session_factory() as s:
        _, version, clauses = await make_document(s, ["The controller shall notify the Commission within 72 hours of a breach."])
        version.state = "parsed"
        edges = []
        for n, status in enumerate(("pending_review", "pending_review", "pending_review", "approved")):
            src = KgNode(country_code="SG", node_type="INSTRUMENT", node_key=f"v:law{n}", name_zh="法规", review_status="approved")
            dst = KgNode(country_code="SG", node_type="OBLIGATION", node_key=f"v:ob{n}", name_zh="义务", review_status="pending_review")
            s.add_all([src, dst])
            await s.flush()
            e = KgEdge(
                country_code="SG", source_node_id=src.id, target_node_id=dst.id, relation_type="REQUIRES",
                evidence_clause_id=clauses[0].id, evidence_quote="shall notify the Commission", extractor_model="t",
                confidence=0.9, judge_verdict="yes", is_structural=False, review_status=status,
            )
            s.add(e)
            await s.flush()
            edges.append(e)
        await s.commit()
        ids = [str(e.id) for e in edges]

    verdicts = iter(["auto", "retire", "human"])

    async def fake(kind, row):
        v = next(verdicts)
        return {"verdict": v, "note": {"auto": "", "retire": "义务主体与条文不符：x", "human": "条文被动句，主体不明"}[v],
                "defects": [], "stage": 2}

    monkeypatch.setattr(run.graph_verify, "verify_statement", fake)
    stats = await run.verify_graph(session_factory, kind="edges", concurrency=1)
    assert stats == {"checked": 3, "auto": 1, "retired": 1, "human_needed": 1, "failed": 0}

    async with session_factory() as s:
        got = {str(e.id): await s.get(KgEdge, e.id) for e in edges}
    states = [(got[i].review_status, got[i].verify_state) for i in ids]
    assert sorted(states[:3]) == sorted([("pending_review", "auto"), ("stale", "retired"), ("pending_review", "human_needed")])
    assert states[3] == ("approved", None)  # 人已批准的不再核验
    assert all(got[i].verify_note is not None for i in ids[:3] if got[i].verify_state != "auto")

    # 重跑：已核验的不再处理
    again = await run.verify_graph(session_factory, kind="edges")
    assert again["checked"] == 0
