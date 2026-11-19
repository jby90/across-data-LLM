from app.analysis.pipeline import _roadmap_basis


def _dim(code, status, cites):
    return {"dimension_code": code, "status": status, "citations": cites}


def test_roadmap_basis_takes_verified_clauses_of_other_dimensions_once():
    dims = [
        _dim("D3", "COMPLETE", [{"clause_id": "a"}, {"clause_id": "b"}]),
        _dim("D4", "COMPLETE", [{"clause_id": "b"}, {"citation_tag": "t1"}]),
        _dim("D5", "PARTIAL_GAP", [{"clause_id": "zzz"}]),  # 缺口方面的条款不作依据
        _dim("D8", "PARTIAL_GAP", []),
    ]
    got = _roadmap_basis(dims)
    assert [c.get("clause_id") or c.get("citation_tag") for c in got] == ["a", "b", "t1"]


def test_roadmap_basis_empty_when_nothing_verified():
    assert _roadmap_basis([_dim("D3", "PARTIAL_GAP", []), _dim("D8", "PARTIAL_GAP", [])]) == []


def test_the_same_question_under_different_fact_keys_is_asked_once():
    from app.analysis.user_facts import merge_same_questions, same_question

    permit_1 = (
        "贵司是否已就向中国研发中心传输个人数据取得埃及个人数据保护中心颁发的跨境传输许可或批准？"
    )
    permit_2 = (
        "贵司是否已就向中国研发中心跨境传输个人数据取得埃及个人数据保护中心（PDPC）的许可或批准？"
    )
    entity = "贵司在埃及是否已设立法人实体或分支机构，作为车辆数据处理的运营主体？"
    investigation = (
        "目前埃及监管机构是否已就贵司的个人数据处理启动调查、发出违规通知或作出处罚决定？"
    )
    assert same_question(permit_1, permit_2)
    assert not same_question(permit_1, entity) and not same_question(entity, investigation)

    requests = {
        "pdpc_cross_border_authorization": {"question_zh": permit_1, "dimension_codes": ["D3"]},
        "cross_border_authorization_status": {
            "question_zh": permit_2,
            "dimension_codes": ["D1", "D8"],
        },
        "eg_legal_entity_status": {"question_zh": entity, "dimension_codes": ["D6"]},
    }
    merged = merge_same_questions(requests)
    assert list(merged) == ["pdpc_cross_border_authorization", "eg_legal_entity_status"]
    kept = merged["pdpc_cross_border_authorization"]
    assert kept["dimension_codes"] == ["D1", "D3", "D8"]
    assert kept["also_answers"] == ["cross_border_authorization_status"]


def test_ontology_attributes_are_shown_only_when_their_quote_is_in_the_clause():
    from app.knowledge.graph_evidence import ontology_attributes

    clause = ("12", "Law", "A controller must keep records for two years.  Fines up to $5,000.")
    stored = {
        "ontology": {
            "retention_period": {"value": "保存 2 年", "quote": "keep records for two years"},
            "penalty_amount": {
                "value": "罚款 5,000",
                "quote": "Fines up to $9,999",
            },  # 引文不在原文
            "admission_materials": {"value": "x", "quote": "A controller must keep records"},
        }
    }
    values, provenance = ontology_attributes(stored, clause)
    assert values == {"retention_period": "保存 2 年", "admission_checklist": "x"}
    assert provenance["retention_period"][0]["evidence_quote"] == "keep records for two years"
    assert "penalty_amount" not in values  # 引文对不上原文就不展示
    assert ontology_attributes(stored, None) == ({}, {})
    assert ontology_attributes({}, clause) == ({}, {})


def test_a_data_category_target_is_a_data_scope_not_an_applicable_industry():
    from types import SimpleNamespace
    from uuid import uuid4

    from app.knowledge.graph_evidence import evidence_attributes

    src, dst, cid = uuid4(), uuid4(), uuid4()
    nodes = {
        src: SimpleNamespace(id=src, node_type="OBLIGATION", name_zh="数据泄露通报义务"),
        dst: SimpleNamespace(id=dst, node_type="DATA_CATEGORY", name_zh="个人数据"),
    }
    edge = SimpleNamespace(
        id=uuid4(),
        is_structural=False,
        judge_verdict="yes",
        review_status="approved",
        source_node_id=src,
        target_node_id=dst,
        evidence_clause_id=cid,
        evidence_quote="personal data",
        relation_type="APPLIES_TO_SCENARIO",
    )
    values, _ = evidence_attributes(
        [edge], nodes, {cid: ("5", "Law", "notify about personal data")}, node_id=src
    )
    assert "data_scope" in values and "applicable_industry" not in values
