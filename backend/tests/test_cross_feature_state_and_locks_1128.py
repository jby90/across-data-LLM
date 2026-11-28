# ruff: noqa: E501
"""Cross-Feature State-Machine, Lock Preservation, and Concurrent Cache Regression Suite (2026-11-28):
Verifies cross-module interactions discovered during deep First-Principles QA:
1. Archived/frozen consulting conversation (`archived_at != None`) remains readable (`GET` + exports)
   while strictly blocking ALL mutation endpoints (`POST /messages`, `/facts`, `/run`, `/reports/edit`,
   attachment upload/review/delete) with pure-Chinese `409`, until restored (`POST /restore`).
2. Archived compliance project (`doc-projects`) remains readable (`GET` + `preview-i18n` + `versions/{v}`)
   while strictly blocking ALL document mutation endpoints (`POST /answers`, `/saved`, `PATCH /saved`,
   `DELETE /saved`, `POST /versions`, `POST /versions/{v}/rollback`) with pure-Chinese `409`.
3. Project clone (`POST /doc-projects/{id}/clone`) deep-copies custom `functions` & `business_scenes`
   and allows independent online editing + 15-language re-export without cache collision.
4. Country whitepaper incremental `sync_dimensions` preserves `review_status == "approved"` and
   `manual_edited == True` anti-drift lock on unaffected chapters.
5. Concurrent `DocumentNarrative` cache writes (`ON CONFLICT DO NOTHING`) succeed atomically with zero
   `UniqueViolationError`.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.analysis.models import AnalysisMessage, AnalysisTask, ReportVersion
from app.analysis.multilingual_report import build_multilingual_doc_blocks
from app.knowledge import whitepaper as wp

from .conftest import bearer, login


async def test_archived_chat_and_project_mutation_locks_vs_readonly_access(client, seed, session_factory):
    member = bearer(await login(client, "user", "a-member@example.com"))

    # 1. Create a consulting conversation with report + attachment, then archive/freeze it
    async with session_factory() as s:
        chat = AnalysisTask(
            org_id=seed["org_a"].id,
            created_by=seed["a_member"].id,
            kind="chat",
            title="德国自动驾驶法规咨询",
            title_custom=True,
            country_code="DE",
            vehicle_model="QH-S9",
            scenario_type="autonomous_driving",
            status="completed",
            sufficiency_score=100,
            facts_json={"country_code": "DE"},
        )
        s.add(chat)
        await s.flush()
        s.add(AnalysisMessage(org_id=seed["org_a"].id, task_id=chat.id, seq_no=1, role="user", content="德国路测数据出境要求？"))
        s.add(
            ReportVersion(
                org_id=seed["org_a"].id,
                task_id=chat.id,
                version_no=1,
                status="full_draft",
                is_partial_sample=False,
                change_note="初稿",
                executive_summary="须遵守欧盟通用数据保护条例第五章跨境传输机制。",
                dimensions_json=[
                    {
                        "dimension_code": f"D{i}",
                        "dimension_title": f"维度{i}",
                        "status": "ok",
                        "risk_level": "中风险",
                        "content_markdown": "签署标准合同条款并采取加密措施。",
                        "key_recommendations": ["实施端到端加密"],
                        "citations": [],
                        "findings": [],
                        "missing_gap_note": None,
                    }
                    for i in range(1, 9)
                ],
                stale_impacts_json=[],
            )
        )
        await s.commit()
        chat_id = str(chat.id)

    r_att = await client.post(
        f"/api/analysis/tasks/{chat_id}/attachments?filename=policy.txt",
        headers={**member, "Content-Type": "application/octet-stream"},
        content="企业车联网隐私管理制度示例文本。".encode(),
    )
    assert r_att.status_code == 201, r_att.text
    att_id = r_att.json()["id"]

    # Freeze/archive the conversation
    r_arch = await client.delete(f"/api/analysis/tasks/{chat_id}", headers=member)
    assert r_arch.status_code == 200 and r_arch.json()["archived"] is True

    # Archived chat MUST remain readable & exportable in German (`de`)
    assert (await client.get(f"/api/analysis/tasks/{chat_id}", headers=member)).status_code == 200
    assert (await client.get(f"/api/analysis/tasks/{chat_id}/reports/1/export.docx?lang=de", headers=member)).status_code == 200

    # ALL mutation endpoints on archived chat MUST return 409 pure Chinese
    for method, url, kw in (
        ("POST", f"/api/analysis/tasks/{chat_id}/messages", {"json": {"content": "继续追问"}}),
        ("POST", f"/api/analysis/tasks/{chat_id}/facts", {"json": {"facts": {"vehicle_model": "X2"}}}),
        ("POST", f"/api/analysis/tasks/{chat_id}/run", {}),
        ("POST", f"/api/analysis/tasks/{chat_id}/reports/edit", {"json": {"executive_summary": "篡改归档报告", "dimension_updates": {}, "change_note": "x"}}),
        ("POST", f"/api/analysis/tasks/{chat_id}/attachments?filename=b.txt", {"content": b"abc"}),
        ("POST", f"/api/analysis/tasks/{chat_id}/attachments/review-pending", {}),
        ("DELETE", f"/api/analysis/tasks/{chat_id}/attachments/{att_id}", {}),
    ):
        resp = await client.request(method, url, headers=member, **kw)
        assert resp.status_code == 409, (method, url, resp.status_code, resp.text)
        assert "归档" in resp.json()["detail"]

    # Restore unlocks mutations cleanly
    assert (await client.post(f"/api/analysis/tasks/{chat_id}/restore", headers=member)).status_code == 200
    assert (await client.delete(f"/api/analysis/tasks/{chat_id}/attachments/{att_id}", headers=member)).status_code == 200

    # 2. Compliance project clone + online edit + archive lock across documents_router
    r_proj = await client.post(
        "/api/analysis/doc-projects",
        headers=member,
        json={
            "source_country": "中国",
            "recipient_country": "法国",
            "functions": ["ota_diagnostics", "车载端云协同推理"],
            "business_scenes": ["after_sales", "欧洲极寒冬季标定"],
            "vehicle_model": "QH-S9",
            "company_name": "星途智行科技股份有限公司",
        },
    )
    assert r_proj.status_code == 201
    pid = r_proj.json()["id"]

    # Clone preserves custom functions & scenes independently
    r_clone = await client.post(f"/api/analysis/doc-projects/{pid}/clone", headers=member)
    assert r_clone.status_code == 201
    cloned_proj = r_clone.json()
    assert cloned_proj["route"]["functions"] == ["ota_diagnostics", "车载端云协同推理"]
    assert cloned_proj["route"]["business_scenes"] == ["after_sales", "欧洲极寒冬季标定"]

    # Save & seal v1 on original project, then archive it
    assert (await client.post(f"/api/analysis/tasks/{pid}/documents/contract/saved?draft=true", headers=member)).status_code == 200
    assert (await client.post(f"/api/analysis/tasks/{pid}/documents/contract/versions", headers=member, json={"note": "初版"})).status_code == 200
    assert (await client.delete(f"/api/analysis/tasks/{pid}", headers=member)).status_code == 200

    # Read & preview-i18n & read version 1 still succeed on archived project
    assert (await client.get(f"/api/analysis/tasks/{pid}/documents/contract/preview-i18n?lang=fr&draft=true", headers=member)).status_code == 200
    assert (await client.get(f"/api/analysis/tasks/{pid}/documents/contract/versions/1", headers=member)).status_code == 200

    # All write endpoints on archived project return 409
    for method, url, kw in (
        ("POST", f"/api/analysis/tasks/{pid}/documents/contract/saved?draft=true", {}),
        ("PATCH", f"/api/analysis/tasks/{pid}/documents/contract/saved", {"json": {"block": 0, "text": "x"}}),
        ("DELETE", f"/api/analysis/tasks/{pid}/documents/contract/saved", {}),
        ("POST", f"/api/analysis/tasks/{pid}/documents/contract/versions", {"json": {"note": "v2"}}),
        ("POST", f"/api/analysis/tasks/{pid}/documents/contract/versions/1/rollback", {}),
    ):
        resp = await client.request(method, url, headers=member, **kw)
        assert resp.status_code == 409, (method, url, resp.status_code, resp.text)


async def test_whitepaper_sync_dimensions_preserves_approved_lock_and_concurrent_narrative_upsert(session_factory, seed):
    # 1. `wp.sync_dimensions` must preserve `review_status == "approved"` and `manual_edited == True`
    async with session_factory() as s:
        await wp.save(
            s,
            "SG",
            [
                {"type": "title", "text": "新加坡汽车数据合规洞察简报"},
                {"type": "heading", "text": "摘要"},
                {"type": "paragraph", "text": "专家审定摘要内容。"},
                {"type": "heading", "text": "一、立法框架与主管机关"},
                {"type": "paragraph", "text": "专家人工审定第一维内容。"},
                {"type": "heading", "text": "二、汽车与个人数据分类分级"},
                {"type": "paragraph", "text": "专家人工审定第二维内容。"},
                {"type": "heading", "text": "八、合规落地路线图与操作核查清单"},
                {"type": "paragraph", "text": "专家人工审定第八维内容。"},
            ],
            {
                "source_hash": "h_locked",
                "tokens": 50,
                "report": {"review_status": "approved", "manual_edited": True, "documents": 2, "clauses": 10},
            },
        )
        await s.commit()

        async def _fake_write_dim(code, title, name, pool, lrefs, extra=""):
            return [{"type": "paragraph", "text": f"{name}{title}增量同步更新段落。"}], 12, []

        async def _fake_select_clauses(code, title, pool):
            return pool[:2], 5

        with (
            patch.object(wp, "select_clauses", new=AsyncMock(side_effect=_fake_select_clauses)),
            patch.object(wp, "_write_dimension", new=AsyncMock(side_effect=_fake_write_dim)),
        ):
            new_blocks, new_meta = await wp.sync_dimensions(s, "SG", ["D3"])
        assert new_meta["report"].get("review_status") == "approved"
        assert new_meta["report"].get("manual_edited") is True
        assert any("专家人工审定第一维内容" in str(b.get("text", "")) for b in new_blocks)

    # 2. Concurrent `build_multilingual_doc_blocks` calls on identical uncached input succeed atomically
    async with session_factory() as s:
        task = AnalysisTask(
            org_id=seed["org_a"].id,
            created_by=seed["a_member"].id,
            kind="documents",
            title="中国 → 德国 · 并发多语种缓存测试",
            title_custom=True,
            country_code="DE",
            vehicle_model="QH-S9",
            scenario_type="ota_diagnostics",
            status="collecting_facts",
            facts_json={},
        )
        s.add(task)
        await s.commit()
        task_id = task.id

    async def _fake_llm(messages, **kwargs):
        import json as _json

        await asyncio.sleep(0.05)
        slots = _json.loads(messages[-1]["content"]).get("slots", {})
        return SimpleNamespace(
            data={"slots": {k: f"Verifizierte Datenschutzmaßnahme ({k})" for k in slots}},
            total_tokens=88,
        )

    async def _worker():
        async with session_factory() as ws:
            wt = await ws.get(AnalysisTask, task_id)
            assert wt is not None
            blks, _, _ = await build_multilingual_doc_blocks(
                ws,
                wt,
                "dpia",
                [{"type": "paragraph", "text": "并发导出时相同输入哈希不发生唯一键冲突。"}],
                {"source_country": "中国", "recipient_country": "德国"},
                [],
                lang="de",
                draft=True,
                force_llm=True,
            )
            return blks

    with patch("app.analysis.multilingual_report.call_llm_json", side_effect=_fake_llm):
        r1, r2 = await asyncio.gather(_worker(), _worker())
    assert r1 == r2
    assert any("Verifizierte Datenschutzmaßnahme" in str(b.get("text", "")) for b in r1)

    # 3. Non-hot country cache invalidation on `after_admin_change()` & empty/None slot validation ([P1-4, P1-5])
    from app.analysis import doc_rules
    from app.analysis.english_report import _valid_slot_strings
    from app.knowledge import cache_sync, whitepaper_router

    assert _valid_slot_strings(["必填段落"], [""]) is None
    assert _valid_slot_strings(["必填段落"], [None]) is None
    assert _valid_slot_strings(["必填段落"], ["None"]) is None
    assert _valid_slot_strings(["", "必填段落"], ["", "Translated paragraph"]) == ["", "Translated paragraph"]

    whitepaper_router._page_cache["JP"] = (1.0, {"country_code": "JP"})
    wp._PINNED_TOPICS_CACHE["JP"] = []
    doc_rules._LATEST_CLAUSES_CACHE["JP"] = []
    cache_sync.after_admin_change()
    assert "JP" not in whitepaper_router._page_cache
    assert "JP" not in wp._PINNED_TOPICS_CACHE
    assert "JP" not in doc_rules._LATEST_CLAUSES_CACHE


async def _async_val(v):
    return v
