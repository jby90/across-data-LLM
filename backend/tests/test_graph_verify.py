"""图谱自动核验（verification_status）：具体且核对过的义务与语义边自动核验；
结论分歧、原则性表述、标题推断的边留给人确认；管理端图谱接口带出核验状态与统计，人工确认走原有接口。

（原文件在 11-04 被我误覆盖，此处按其编译副本里的常量与调用顺序重写，断言与被测对象保持不变。）"""

import uuid

from app.knowledge.graph_verify import verify_edge, verify_node
from app.knowledge.models import KgEdge, KgNode, NodeType, RelationType

from .conftest import bearer, login


def _node(node_type, review_status="pending_review", attributes=None, evidence_clause_id=None):
    return KgNode(
        id=uuid.uuid4(),
        country_code="SG",
        node_type=node_type,
        node_key=f"SG:{node_type}:{uuid.uuid4().hex[:8]}",
        name_zh="测试节点",
        review_status=review_status,
        attributes=attributes if attributes is not None else {"party_checked": True, "node_class": "concrete"},
        evidence_clause_id=evidence_clause_id or uuid.uuid4(),
    )


def _edge(src, dst, relation_type, evidence_quote, confidence=0.9, judge_verdict="yes", is_structural=False,
          review_status="pending_review"):
    return KgEdge(
        id=uuid.uuid4(),
        country_code="SG",
        source_node_id=src.id,
        target_node_id=dst.id,
        relation_type=relation_type,
        evidence_clause_id=uuid.uuid4(),
        evidence_quote=evidence_quote,
        extractor_model="deepseek-chat",
        confidence=confidence,
        judge_verdict=judge_verdict,
        is_structural=is_structural,
        review_status=review_status,
    )


def test_concrete_checked_obligation_and_semantic_edge_are_auto_verified():
    inst = _node(NodeType.INSTRUMENT.value, attributes={})
    obl = _node(
        NodeType.OBLIGATION.value,
        attributes={
            "party_checked": True,
            "node_class": "concrete",
            "node_check": {"first": {"verdict": "supported"}, "second": {"verdict": "supported"}},
        },
    )
    inst_status, _ = verify_node(inst)
    obl_status, _ = verify_node(obl)
    assert inst_status == "auto_verified"
    assert obl_status == "auto_verified"

    quote = "Section 24: An organisation must protect personal data in its possession."
    edge = _edge(inst, obl, RelationType.REQUIRES.value, quote)
    edge_status, _ = verify_edge(
        edge, src_node=inst, dst_node=obl, clause_text=quote, src_status=inst_status, dst_status=obl_status
    )
    assert edge_status == "auto_verified"
    assert edge.review_status == "pending_review"  # approved 只留给人工确认，自动核验不改它


def test_split_verdict_generic_and_title_inferred_items_require_confirmation():
    split = _node(
        NodeType.OBLIGATION.value,
        attributes={
            "party_checked": True,
            "node_class": "concrete",
            "node_check": {"first": {"verdict": "unsupported"}, "second": {"verdict": "supported"}},
        },
    )
    status, reason = verify_node(split)
    assert status == "needs_confirmation" and "不一致" in reason

    generic = _node(NodeType.OBLIGATION.value, attributes={"party_checked": True, "node_class": "generic"})
    g_status, g_reason = verify_node(generic)
    assert g_status == "needs_confirmation" and "原则性" in g_reason

    quote = "An organisation must protect personal data."
    inst = _node(NodeType.INSTRUMENT.value, attributes={})
    edge = _edge(inst, generic, RelationType.REQUIRES.value, quote)
    e_status, e_reason = verify_edge(
        edge, src_node=inst, dst_node=generic, clause_text=quote, src_status="auto_verified", dst_status=g_status
    )
    assert e_status == "needs_confirmation" and "待人工确认" in e_reason

    law = "Personal Data Protection (Amendment) Act 2020"
    amend_quote = "Short title: Personal Data Protection (Amendment) Act 2020."
    amend = _edge(inst, inst, RelationType.AMENDS.value, amend_quote, judge_verdict="title_inferred")
    a_status, a_reason = verify_edge(amend, src_node=inst, dst_node=inst, clause_text=amend_quote)
    assert a_status == "needs_confirmation" and "修正案" in a_reason
    assert law in amend_quote


async def test_graph_payload_and_human_confirmation_endpoints(client, seed, session_factory):
    quote = "An organisation shall appoint a data protection officer."
    from .review_s1_s2_contracts import make_document

    async with session_factory() as s:
        doc, dv, clauses = await make_document(s, [quote])
        dv.state = "parsed"
        cl = clauses[0]
        inst = KgNode(
            country_code="SG", node_type=NodeType.INSTRUMENT.value, node_key="SG:INSTRUMENT:PDPA_TEST",
            name_zh="新加坡个人数据保护法", document_id=doc.id, evidence_clause_id=cl.id,
            review_status="pending_review", attributes={},
        )
        obl_ok = KgNode(
            country_code="SG", node_type=NodeType.OBLIGATION.value, node_key="SG:OBLIGATION:DPO_TEST",
            name_zh="指定数据保护官", document_id=doc.id, evidence_clause_id=cl.id,
            review_status="pending_review", attributes={"party_checked": True, "node_class": "concrete"},
        )
        obl_amb = KgNode(
            country_code="SG", node_type=NodeType.OBLIGATION.value, node_key="SG:OBLIGATION:GEN_TEST",
            name_zh="遵守本法一般原则", document_id=doc.id, evidence_clause_id=cl.id,
            review_status="pending_review", attributes={"party_checked": True, "node_class": "generic"},
        )
        s.add_all([inst, obl_ok, obl_amb])
        await s.flush()
        edge_ok = KgEdge(
            country_code="SG", source_node_id=inst.id, target_node_id=obl_ok.id,
            relation_type=RelationType.REQUIRES.value, evidence_clause_id=cl.id, evidence_quote=quote,
            extractor_model="deepseek-chat", confidence=0.92, judge_verdict="yes", is_structural=False,
            review_status="pending_review",
        )
        edge_amb = KgEdge(
            country_code="SG", source_node_id=inst.id, target_node_id=obl_amb.id,
            relation_type=RelationType.REQUIRES.value, evidence_clause_id=cl.id, evidence_quote=quote,
            extractor_model="deepseek-chat", confidence=0.9, judge_verdict="yes", is_structural=False,
            review_status="pending_review",
        )
        s.add_all([edge_ok, edge_amb])
        await s.commit()
        obl_amb_id, edge_ok_id, obl_ok_id = str(obl_amb.id), str(edge_ok.id), str(obl_ok.id)

    admin = bearer(await login(client, "admin", "admin@example.com"))
    graph = (await client.get("/api/admin/knowledge/graph?country_code=SG", headers=admin)).json()
    edges_by_id = {e["id"]: e for e in graph["edges"]}
    nodes_by_id = {n["id"]: n for n in graph["nodes"]}
    assert nodes_by_id[obl_ok_id]["verification_status"] == "auto_verified"
    assert nodes_by_id[obl_amb_id]["verification_status"] == "needs_confirmation"
    assert edges_by_id[edge_ok_id]["verification_status"] == "auto_verified"
    assert graph["quality_summary"]["auto_verified_edges_count"] >= 1
    assert graph["quality_summary"]["needs_confirmation_nodes_count"] >= 1

    # 人工确认走原有接口：确认后状态变成“已由法务人员人工确认”
    r_node = await client.post(
        f"/api/admin/knowledge/nodes/{obl_amb_id}/review", json={"decision": "approved"}, headers=admin
    )
    assert r_node.status_code == 200
    assert r_node.json()["verification_status"] == "approved"


def test_the_model_reading_of_the_clause_can_hold_back_what_the_rule_gates_let_through():
    obl = _node(NodeType.OBLIGATION.value)
    assert verify_node(obl)[0] == "auto_verified"
    obl.verify_state, obl.verify_note = "human_needed", "义务主体与条文不符：条文主体是 TRA"
    assert verify_node(obl) == ("needs_confirmation", "义务主体与条文不符：条文主体是 TRA")
    obl.verify_state = "auto"  # 模型核验通过：不改变规则闸门的结论
    assert verify_node(obl)[0] == "auto_verified"

    quote = "Section 24: An organisation must protect personal data."
    inst = _node(NodeType.INSTRUMENT.value, attributes={})
    edge = _edge(inst, obl, RelationType.REQUIRES.value, quote)
    assert verify_edge(edge, src_node=inst, dst_node=obl, clause_text=quote)[0] == "auto_verified"
    edge.verify_state, edge.verify_note = "human_needed", "关系类型或方向与条文不符"
    assert verify_edge(edge, src_node=inst, dst_node=obl, clause_text=quote) == (
        "needs_confirmation",
        "关系类型或方向与条文不符",
    )
    edge.review_status = "approved"  # 人工确认永远优先
    assert verify_edge(edge, src_node=inst, dst_node=obl, clause_text=quote)[0] == "approved"
