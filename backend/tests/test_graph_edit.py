import hashlib

from .conftest import bearer, login

BODY = "The organisation shall notify the Commission within 3 days of a data breach."


def _h(text):
    return hashlib.sha256(text.encode()).hexdigest()


async def _world(session_factory):
    """One document with one clause, plus an instrument node and an obligation node."""
    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge.models import KgNode
    from app.sources.models import SourceSite

    async with session_factory() as s:
        site = SourceSite(
            country_code="SG",
            publisher="X",
            language="en",
            document_type="LAW",
            entry_url="https://e.example/edit",
            strategy="DIRECT",
            terms_status="pending",
            enabled=False,
        )
        s.add(site)
        await s.flush()
        doc = Document(
            site_id=site.id,
            country_code="SG",
            title="Edit test law",
            document_type="LAW",
            language="en",
            source_url="https://e.example/edit",
        )
        s.add(doc)
        await s.flush()
        ver = DocumentVersion(
            document_id=doc.id,
            version_no=1,
            content_hash="e" * 64,
            archive_path="a",
            mime_type="application/pdf",
            size_bytes=1,
            final_url="https://e.example/edit",
            state="parsed",
        )
        s.add(ver)
        await s.flush()
        clause = Clause(
            version_id=ver.id,
            ordinal=1,
            clause_number="1",
            language="en",
            original_text=BODY,
            text_hash=_h(BODY),
        )
        s.add(clause)
        await s.flush()

        def node(name, ntype, status="approved"):
            n = KgNode(
                country_code="SG",
                node_type=ntype,
                node_key=name,
                name_zh=name,
                evidence_clause_id=clause.id,
                review_status=status,
            )
            s.add(n)
            return n

        law, duty = node("某法", "INSTRUMENT"), node("通报义务", "OBLIGATION")
        await s.flush()
        await s.commit()
        return law.id, duty.id, clause.id


async def test_rename_create_relabel_retire_and_restore(session_factory):
    import pytest
    from sqlalchemy import select

    from app.knowledge import graph_edit
    from app.knowledge.models import KgEdge, KgNode, KnowledgeAuditRecord

    law_id, duty_id, clause_id = await _world(session_factory)
    async with session_factory() as s:
        # 改名：回到待复核，留前后快照
        node = await graph_edit.rename_node(s, duty_id, name_zh="  数据泄露  通报义务 ")
        assert node.name_zh == "数据泄露 通报义务" and node.review_status == "pending_review"
        assert node.attributes["manual_edit"]["action"] == "rename"
        await s.commit()

        # 新增关系：依据原句必须真在条款里
        with pytest.raises(graph_edit.GraphEditError, match="不在所选条款"):
            await graph_edit.create_edge(
                s,
                source_node_id=law_id,
                target_node_id=duty_id,
                relation_type="REQUIRES",
                evidence_clause_id=clause_id,
                evidence_quote="This sentence is not in the clause at all.",
            )
        with pytest.raises(graph_edit.GraphEditError, match="太短"):
            await graph_edit.create_edge(
                s,
                source_node_id=law_id,
                target_node_id=duty_id,
                relation_type="REQUIRES",
                evidence_clause_id=clause_id,
                evidence_quote="shall",
            )
        with pytest.raises(graph_edit.GraphEditError, match="十二类"):
            await graph_edit.create_edge(
                s,
                source_node_id=law_id,
                target_node_id=duty_id,
                relation_type="LIKES",
                evidence_clause_id=clause_id,
                evidence_quote="notify the Commission within 3 days",
            )
        edge = await graph_edit.create_edge(
            s,
            source_node_id=law_id,
            target_node_id=duty_id,
            relation_type="REQUIRES",
            evidence_clause_id=clause_id,
            evidence_quote="notify  the Commission within 3 days",  # 空白差异不影响
        )
        assert edge.review_status == "pending_review" and edge.extractor_model == "manual-edit"
        await s.commit()
        with pytest.raises(graph_edit.GraphEditError, match="已有同样"):
            await graph_edit.create_edge(
                s,
                source_node_id=law_id,
                target_node_id=duty_id,
                relation_type="REQUIRES",
                evidence_clause_id=clause_id,
                evidence_quote="notify the Commission within 3 days",
            )

        # 改关系类型：留痕，回到待复核
        edge = await graph_edit.change_edge_relation(s, edge.id, relation_type="PROHIBITS")
        assert edge.relation_type == "PROHIBITS"
        await s.commit()

        # 恢复：用改名前的快照把名字和状态找回来
        before = await s.scalar(
            select(KnowledgeAuditRecord).where(
                KnowledgeAuditRecord.target_id == duty_id,
                KnowledgeAuditRecord.action == "before_manual_edit",
            )
        )
        restored = await graph_edit.restore_from_history(s, before.id)
        assert restored.name_zh == "通报义务" and restored.review_status == "approved"
        await s.commit()

        # 停用节点：挂在它上面的关系一并停用；停用后不能再改
        retired, edges = await graph_edit.retire_node(s, duty_id)
        assert retired.review_status == "superseded" and edges == 1
        await s.commit()
        got = (await s.scalars(select(KgEdge).where(KgEdge.target_node_id == duty_id))).one()
        assert got.review_status == "superseded"
        with pytest.raises(graph_edit.GraphEditError, match="已停用"):
            await graph_edit.rename_node(s, duty_id, name_zh="x")
        kinds = {
            r.action
            for r in (
                await s.scalars(
                    select(KnowledgeAuditRecord).where(KnowledgeAuditRecord.target_id == duty_id)
                )
            ).all()
        }
        assert {
            "before_manual_edit",
            "after_manual_edit",
            "before_restore",
            "after_restore",
            "before_manual_retire",
            "after_manual_retire",
        } <= kinds
        assert (await s.get(KgNode, duty_id)) is not None  # 没有物理删除


async def test_create_node_rules(session_factory):
    import pytest

    from app.knowledge import graph_edit

    _, _, clause_id = await _world(session_factory)
    async with session_factory() as s:
        with pytest.raises(graph_edit.GraphEditError, match="必须指定依据条款"):
            await graph_edit.create_node(
                s, country_code="SG", node_type="OBLIGATION", name_zh="新义务"
            )
        with pytest.raises(graph_edit.GraphEditError, match="不属于该国家"):
            await graph_edit.create_node(
                s,
                country_code="EG",
                node_type="OBLIGATION",
                name_zh="新义务",
                evidence_clause_id=clause_id,
            )
        with pytest.raises(graph_edit.GraphEditError, match="六类"):
            await graph_edit.create_node(s, country_code="SG", node_type="PERSON", name_zh="张三")
        node = await graph_edit.create_node(
            s,
            country_code="SG",
            node_type="OBLIGATION",
            name_zh="新义务",
            evidence_clause_id=clause_id,
        )
        assert node.review_status == "pending_review" and node.node_key.startswith("manual:")
        regulator = await graph_edit.create_node(
            s, country_code="sg", node_type="REGULATOR", name_zh="某监管机构"
        )
        assert regulator.evidence_clause_id is None
        with pytest.raises(graph_edit.GraphEditError, match="已有同类型同名称"):
            await graph_edit.create_node(
                s, country_code="SG", node_type="REGULATOR", name_zh="某监管机构"
            )


async def test_edit_endpoints_need_admin_side_and_report_conflicts(client, seed, session_factory):
    law_id, duty_id, clause_id = await _world(session_factory)
    admin = bearer(await login(client, "admin", "admin@example.com"))
    member = bearer(await login(client, "user", "a-member@example.com"))

    url = f"/api/admin/knowledge/nodes/{duty_id}"
    assert (await client.patch(url, json={"name_zh": "x"}, headers=member)).status_code == 401
    ok = await client.patch(url, json={"name_zh": "改名后"}, headers=admin)
    assert ok.status_code == 200 and ok.json()["review_status"] == "pending_review"

    bad = await client.post(
        "/api/admin/knowledge/edges",
        json={
            "source_node_id": str(law_id),
            "target_node_id": str(duty_id),
            "relation_type": "REQUIRES",
            "evidence_clause_id": str(clause_id),
            "evidence_quote": "这句话不在条款原文里面，所以必须被拒绝",
        },
        headers=admin,
    )
    assert bad.status_code == 409 and "原文" in bad.json()["detail"]

    made = await client.post(
        "/api/admin/knowledge/edges",
        json={
            "source_node_id": str(law_id),
            "target_node_id": str(duty_id),
            "relation_type": "REQUIRES",
            "evidence_clause_id": str(clause_id),
            "evidence_quote": "notify the Commission within 3 days",
        },
        headers=admin,
    )
    assert made.status_code == 201
    gone = await client.delete(f"/api/admin/knowledge/edges/{made.json()['id']}", headers=admin)
    assert gone.status_code == 200 and gone.json()["review_status"] == "superseded"
    again = await client.delete(f"/api/admin/knowledge/edges/{made.json()['id']}", headers=admin)
    assert again.status_code == 409


async def test_merging_a_duplicate_moves_its_relations_and_retires_it(session_factory):
    import pytest
    from sqlalchemy import select

    from app.knowledge import graph_edit
    from app.knowledge.models import KgEdge, KgNode

    law_id, duty_id, clause_id = await _world(session_factory)
    async with session_factory() as s:
        twin = await graph_edit.create_node(
            s,
            country_code="SG",
            node_type="OBLIGATION",
            name_zh="通报义务（副本）",
            evidence_clause_id=clause_id,
        )
        other = await graph_edit.create_node(
            s, country_code="SG", node_type="REGULATOR", name_zh="某监管机构"
        )
        await s.flush()
        quote = "notify the Commission within 3 days"
        # 保留节点已有 law→duty(REQUIRES)；副本上有同样的一条和一条独有的
        await graph_edit.create_edge(
            s,
            source_node_id=law_id,
            target_node_id=duty_id,
            relation_type="REQUIRES",
            evidence_clause_id=clause_id,
            evidence_quote=quote,
        )
        await graph_edit.create_edge(
            s,
            source_node_id=law_id,
            target_node_id=twin.id,
            relation_type="REQUIRES",
            evidence_clause_id=clause_id,
            evidence_quote=quote,
        )
        await graph_edit.create_edge(
            s,
            source_node_id=other.id,
            target_node_id=twin.id,
            relation_type="SUPERVISES",
            evidence_clause_id=clause_id,
            evidence_quote=quote,
        )
        with pytest.raises(graph_edit.GraphEditError, match="同一国家、同一类型"):
            await graph_edit.merge_nodes(s, duty_id, other.id)
        out = await graph_edit.merge_nodes(s, duty_id, twin.id)
        assert out == {"edges_moved": 1, "edges_retired": 1}
        await s.commit()
        live = (
            await s.scalars(
                select(KgEdge).where(
                    KgEdge.target_node_id == duty_id, KgEdge.review_status != "superseded"
                )
            )
        ).all()
        assert sorted(e.relation_type for e in live) == ["REQUIRES", "SUPERVISES"]
        dropped = await s.get(KgNode, twin.id)
        assert dropped.review_status == "superseded"
        assert dropped.attributes["merged_into"] == str(duty_id)
