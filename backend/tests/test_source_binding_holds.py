import hashlib

from sqlalchemy import func, select

from app.analysis.models import ReportVersion
from app.analysis.tools import ToolBox
from app.cli import batch_translate
from app.ingestion.models import Clause, Document
from app.ingestion.safety import document_is_held, report_held_citation_ids
from app.knowledge.history import propose
from app.knowledge.models import ClauseTranslation

from .conftest import bearer, login
from .review_s1_s2_contracts import make_document, make_edge


async def test_hold_preserves_sources_and_approvals_but_blocks_agent_and_translation(
    session_factory, monkeypatch
):
    from app.knowledge import translator

    async def forbidden(*args, **kwargs):
        raise AssertionError("held sources must not reach the model")

    monkeypatch.setattr(translator, "_call_llm_translate", forbidden)
    text = "An organisation must protect personal data under this Act and ensure legal safeguards."
    async with session_factory() as session:
        doc, _, clauses = await make_document(session, [text])
        edge, _, _ = await make_edge(session, clauses[0], "approved")
        session.add(
            ClauseTranslation(
                clause_id=clauses[0].id,
                translated_text="组织应依法保护个人数据。",
                engine="llm",
                model_name="test",
                review_status="approved",
            )
        )
        await session.commit()
        await propose(session, doc, {"safety_hold": True, "reason": "wrong archive binding"})
        await session.commit()
        assert await document_is_held(session, doc.id)
        assert await ToolBox(session, "SG")._load_candidates() == []
        assert await ToolBox(session, "SG").query_kg_obligations([str(clauses[0].id)]) == []
        assert edge.review_status == "approved" and clauses[0].original_text == text
        assert clauses[0].text_hash == hashlib.sha256(text.encode()).hexdigest()
        assert await session.scalar(select(func.count()).select_from(Clause)) == 1
    stats = await batch_translate("SG", sf=session_factory, dry_run=True)
    assert stats["total_eligible"] == 0


async def test_held_source_is_visible_to_admin_but_not_public_knowledge(
    client, seed, session_factory
):
    async with session_factory() as session:
        doc, _, clauses = await make_document(session)
        edge, source, _ = await make_edge(session, clauses[0], "approved")
        source.document_id = doc.id
        session.add(
            ClauseTranslation(
                clause_id=clauses[0].id,
                translated_text="组织应保护个人数据。",
                engine="llm",
                model_name="test",
                review_status="approved",
            )
        )
        await session.commit()
        await propose(session, doc, {"safety_hold": True, "reason": "wrong archive binding"})
        await session.commit()
        doc_id, clause_id, edge_id = str(doc.id), str(clauses[0].id), str(edge.id)
    admin = bearer(await login(client, "admin", "admin@example.com"))
    user = bearer(await login(client, "user", "a-member@example.com"))
    docs = (await client.get("/api/admin/documents", headers=admin)).json()
    assert docs[0]["safety_hold"] is True
    assert (await client.get(f"/api/admin/documents/{doc_id}", headers=admin)).status_code == 200
    assert (
        await client.post(f"/api/admin/knowledge/documents/{doc_id}/translate", headers=admin)
    ).status_code == 409
    assert (
        await client.get(f"/api/knowledge/regulations/{doc_id}", headers=user)
    ).status_code == 409
    assert (
        await client.get(f"/api/analysis/clauses/{clause_id}/citation-card", headers=user)
    ).status_code == 409
    overview = (await client.get("/api/knowledge/overview", headers=user)).json()
    country = next(row for row in overview if row["country_code"] == "SG")
    assert country["clauses_count"] == country["translations_count"] == 0
    assert country["approved_translations_count"] == country["kg_edges_count"] == 0
    topics = (await client.get("/api/knowledge/topics?country_code=SG", headers=user)).json()
    assert not any(item["document_id"] == doc_id for item in topics["items"])
    assert (
        await client.post(
            f"/api/admin/knowledge/edges/{edge_id}/review",
            headers=admin,
            json={"decision": "approved"},
        )
    ).status_code == 409


async def test_report_hold_checks_nested_actual_citation_ids(session_factory):
    async with session_factory() as session:
        doc, _, clauses = await make_document(session)
        await session.commit()
        await propose(session, doc, {"safety_hold": True, "reason": "wrong archive binding"})
        await session.commit()
        report = ReportVersion(dimensions_json=[{"citations": [{"clause_id": str(clauses[0].id)}]}])
        assert await report_held_citation_ids(session, report) == [clauses[0].id]


async def test_out_of_scope_documents_are_hidden_from_users_but_kept_for_admins(
    client, seed, session_factory
):
    """范围外的文件（法院改革法、门户页、办事表格等）不出现在用户的法规目录和统计里；
    文件原样留在库里，管理员仍然可见，按链接直接打开也不受影响。
    同一份文档先在“核心”状态下断言可见，再改成“范围外”断言消失——证明靠的就是这一个字段。"""
    async with session_factory() as session:
        doc, _, clauses = await make_document(session)
        session.add(
            ClauseTranslation(
                clause_id=clauses[0].id,
                translated_text="组织应保护个人数据。",
                engine="llm",
                model_name="test",
                review_status="machine_translated",
            )
        )
        await session.commit()
        doc_id = str(doc.id)
    user = bearer(await login(client, "user", "a-member@example.com"))
    admin = bearer(await login(client, "admin", "admin@example.com"))

    async def user_view():
        listed = (
            await client.get("/api/knowledge/regulations?country_code=SG", headers=user)
        ).json()
        overview = (await client.get("/api/knowledge/overview", headers=user)).json()
        sg = next(row for row in overview if row["country_code"] == "SG")
        return {r["id"] for r in listed}, sg

    ids, sg = await user_view()
    assert doc_id in ids
    assert sg["documents_count"] == 1 and sg["clauses_count"] == 1 and sg["translations_count"] == 1

    async with session_factory() as session:
        row = await session.get(Document, doc.id)
        row.relevance = "out_of_scope"
        await session.commit()
    # 这里绕过管理端接口直接改库：管理端修改后会清用户端缓存，测试里照做
    from app.knowledge.cache_sync import clear_user_caches

    clear_user_caches()
    ids, sg = await user_view()
    assert doc_id not in ids
    # 统计和目录口径一致：概览里的文档、条款、译文都不再包含范围外的文件
    assert sg["documents_count"] == 0 and sg["clauses_count"] == 0 and sg["translations_count"] == 0
    # 管理员仍然看得到；用户按链接直接打开也不受影响（引用跳转不会 404）
    admin_ids = {d["id"] for d in (await client.get("/api/admin/documents", headers=admin)).json()}
    assert doc_id in admin_ids
    assert (
        await client.get(f"/api/knowledge/regulations/{doc_id}", headers=user)
    ).status_code == 200


def test_admission_materials_get_their_own_public_category():
    """官方准入申请须知与表格不是立法，但车企要用：用户端单独归为“准入申报材料”，不混进监管指南。"""
    from app.knowledge.router import _doc_type_zh, public_doc_type

    assert public_doc_type("GUIDANCE", "ADMISSION_GUIDANCE") == "ADMISSION"
    assert public_doc_type("GUIDANCE", "REGULATOR_GUIDANCE") == "GUIDANCE"
    assert public_doc_type("LAW", None) == "LAW"
    assert "准入申报材料" in _doc_type_zh(
        "ADMISSION", "Application for Trial of Autonomous Motor Vehicles"
    )
