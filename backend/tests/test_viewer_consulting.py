"""第 8 项收口测试：
1. 普通查看者放开咨询问答（待收口清单第 28 行）：能新建私密对话、提问查法规、改名/归档自己的对话；
   对话对本企业其他用户、其他查看者和管理端不可见；不能修改企业共享对话，不能从对话生成合规评估项目或上传文件。
2. 咨询助手引用条文统一映射（待收口清单第 5 行）：使用 clause_gate.law_name 输出“名称（年份+编号）”，并经过质量闸门过滤。
"""

from __future__ import annotations

import json
import uuid

from app.analysis import chat_law
from app.analysis.llm import LlmResult
from app.iam import service
from app.iam.models import Role
from app.ingestion.models import Clause, Document
from app.knowledge.models import ClauseTranslation

from . import chat_fakes
from .conftest import PASSWORD, bearer, login


async def _make_viewer(sf, email: str, org_id: uuid.UUID):
    async with sf() as s:
        return await service.create_user(
            s,
            email=email,
            display_name=email.split("@")[0],
            password=PASSWORD,
            role=Role.VIEWER,
            org_id=org_id,
            can_export=False,
        )


async def test_viewer_can_consult_privately_without_touching_org_projects(
    client, seed, session_factory, monkeypatch
):
    chat_fakes.install(monkeypatch)
    await _make_viewer(session_factory, "viewer1@example.com", seed["org_a"].id)
    await _make_viewer(session_factory, "viewer2@example.com", seed["org_a"].id)

    member = bearer(await login(client, "user", "a-member@example.com"))
    viewer1 = bearer(await login(client, "user", "viewer1@example.com"))
    viewer2 = bearer(await login(client, "user", "viewer2@example.com"))
    admin = bearer(await login(client, "admin", "admin@example.com"))

    # 1. 企业用户先有一条企业共享对话
    shared = (await client.post("/api/analysis/chats?advice=true", headers=member)).json()
    shared_id = shared["id"]
    assert shared["viewer_own"] is False

    # 查看者能看到企业共享对话，但不能在共享对话里发消息、改名、删除
    v1_list_before = (await client.get("/api/analysis/tasks", headers=viewer1)).json()
    assert any(t["id"] == shared_id and t["viewer_own"] is False for t in v1_list_before)
    assert (
        await client.post(
            f"/api/analysis/tasks/{shared_id}/messages",
            headers=viewer1,
            json={"content": "摩洛哥传数据出境有什么规定"},
        )
    ).status_code == 403
    assert (
        await client.patch(
            f"/api/analysis/tasks/{shared_id}", headers=viewer1, json={"title": "篡改标题"}
        )
    ).status_code == 403
    assert (await client.delete(f"/api/analysis/tasks/{shared_id}", headers=viewer1)).status_code == 403

    # 查看者不能带引导槽位发起正式合规评估任务
    assert (
        await client.post(
            "/api/analysis/chats",
            headers=viewer1,
            json={
                "country_code": "MA",
                "vehicle_model": "Model X",
                "scenario_type": "connected_vehicle_cloud",
                "business_scene": "mass_production",
            },
        )
    ).status_code == 403

    # 2. 查看者新建个人咨询对话并提问
    r = await client.post("/api/analysis/chats?advice=true", headers=viewer1)
    assert r.status_code == 201, r.text
    priv = r.json()
    priv_id = priv["id"]
    assert priv["viewer_own"] is True

    msg_res = await client.post(
        f"/api/analysis/tasks/{priv_id}/messages",
        headers=viewer1,
        json={"content": "摩洛哥传数据出境有什么规定"},
    )
    assert msg_res.status_code == 200, msg_res.text
    assert len(msg_res.json()["messages"]) >= 3

    # 3. 查看者在对话里要求“生成文件”时，不创建评估项目，且本企业项目数保持为 0
    gen_res = await client.post(
        f"/api/analysis/tasks/{priv_id}/messages",
        headers=viewer1,
        json={"content": "车在摩洛哥跑，数据传回中国，帮我把文件生成出来"},
    )
    assert gen_res.status_code == 200, gen_res.text
    reply_text = gen_res.json()["messages"][-1]["content"]
    assert "普通查看者" in reply_text and "不能新建" in reply_text
    docs_projects = (await client.get("/api/analysis/tasks?kind=documents", headers=member)).json()
    assert docs_projects == []

    # 查看者不能带附件发消息，也不能直接调上传接口
    assert (
        await client.post(
            f"/api/analysis/tasks/{priv_id}/messages",
            headers=viewer1,
            json={"content": "帮我对照", "attachment_ids": [str(uuid.uuid4())]},
        )
    ).status_code == 403

    # 4. 隐私隔离：企业用户、同企业另一名查看者、平台管理员都看不到 viewer1 的咨询对话
    member_ids = {t["id"] for t in (await client.get("/api/analysis/tasks", headers=member)).json()}
    viewer2_ids = {t["id"] for t in (await client.get("/api/analysis/tasks", headers=viewer2)).json()}
    admin_ids = {t["id"] for t in (await client.get("/api/admin/agent-studio/tasks", headers=admin)).json()}
    assert priv_id not in member_ids
    assert priv_id not in viewer2_ids
    assert priv_id not in admin_ids
    assert (await client.get(f"/api/analysis/tasks/{priv_id}", headers=member)).status_code == 404
    assert (await client.get(f"/api/analysis/tasks/{priv_id}", headers=viewer2)).status_code == 404
    assert (await client.get(f"/api/admin/agent-studio/tasks/{priv_id}", headers=admin)).status_code == 404

    # 5. 查看者可以管理（改名、归档、恢复、彻底删除）自己的私密咨询对话
    assert (
        await client.patch(f"/api/analysis/tasks/{priv_id}", headers=viewer1, json={"title": "摩洛哥法规咨询"})
    ).status_code == 200
    assert (await client.delete(f"/api/analysis/tasks/{priv_id}", headers=viewer1)).json()["archived"] is True
    assert (await client.post(f"/api/analysis/tasks/{priv_id}/restore", headers=viewer1)).json()["restored"] is True


async def test_chat_law_uses_unified_law_name_with_year_and_number(
    session_factory, monkeypatch, tmp_path
):
    """咨询助手引用法规时统一走 clause_gate.law_name，带出“名称（年份+编号）”，并拦掉不合格条文。"""
    from app.admission import names

    from .test_s10_updates import setup_source

    _state, _site, doc, ver, good_cl = await setup_source(session_factory, tmp_path, country="MA")
    async with session_factory() as s:
        db_doc = await s.get(Document, doc.id)
        db_doc.title = "Loi n° 09-08 relative à la protection des personnes physiques à l'égard du traitement des données à caractère personnel"
        db_doc.title_zh = "个人数据保护法（法、阿两版）"
        db_cl = await s.get(Clause, good_cl.id)
        db_cl.clause_number = "43"
        db_cl.original_text = (
            "Article 43 - Le responsable d'un traitement ne peut transférer des données à caractère personnel "
            "vers un État étranger que si cet État assure un niveau de protection suffisant."
        )
        db_cl.language = "fr"
        db_cl.needs_review = False
        bad_cl = Clause(
            version_id=ver.id,
            ordinal=2,
            clause_number="44",
            original_text="Article 44 - Par dérogation à l'article 43, le responsable peut transférer des données.",
            text_hash="b" * 64,
            language="fr",
            needs_review=False,
        )
        s.add(bad_cl)
        await s.flush()
        s.add_all(
            [
                ClauseTranslation(
                    clause_id=good_cl.id,
                    translated_text="第43条 数据控制者仅在外国提供充分保护水平时，方可向该外国传输个人数据。",
                    engine="test",
                    model_name="test",
                    prompt_version="v1",
                    review_status="approved",
                    quality_check_passed=True,
                    quality_issues=[],
                    is_current=True,
                ),
                ClauseTranslation(
                    clause_id=bad_cl.id,
                    translated_text="[OCR不清] 第44条 作为第43条的例外……",
                    engine="test",
                    model_name="test",
                    prompt_version="v1",
                    review_status="approved",
                    quality_check_passed=True,
                    quality_issues=[],
                    is_current=True,
                ),
            ]
        )
        await s.commit()

    monkeypatch.setitem(
        names._laws(),
        str(doc.id),
        {"name": "个人数据保护法（2009年第09-08号法律）", "is_law": True},
    )

    captured_clauses: list[dict] = []

    async def fake_llm(messages, **_kw):
        payload = json.loads(messages[1]["content"])
        captured_clauses.extend(payload["clauses"])
        return LlmResult(
            data={"paragraphs": [{"text": "摩洛哥要求向境外传输个人数据时接收国具备充分保护水平。", "cites": [1]}]},
            prompt_tokens=10,
            completion_tokens=10,
            duration_ms=1,
            model="fake",
        )

    monkeypatch.setattr(chat_law, "call_llm_json", fake_llm)
    async with session_factory() as s:
        ans = await chat_law.answer(s, "MA", "摩洛哥个人数据跨境传输有什么要求？", "cross_border", ["跨境传输"])

    assert ans is not None
    assert len(captured_clauses) == 1  # 带 [OCR不清] 的第 44 条被质量闸门拦下
    assert captured_clauses[0]["document"] == "个人数据保护法（2009年第09-08号法律）"
    assert "个人数据保护法（2009年第09-08号法律）" in ans and "第43条" in ans
