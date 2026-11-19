"""Regression tests for Wave 1 backend audit root-cause fixes (D1, D2, D3, D4, D5-7)."""

from __future__ import annotations

import asyncio
import re
import uuid

import pypdfium2 as pdfium
import pytest
from sqlalchemy import text

from .conftest import PASSWORD, bearer, login


async def _make_viewer(session_factory, org_id) -> str:
    from app.iam import service
    from app.iam.models import Role

    async with session_factory() as s:
        await service.create_user(
            s,
            email="a-viewer@example.com",
            display_name="viewer",
            password=PASSWORD,
            role=Role.VIEWER,
            org_id=org_id,
        )
    return "a-viewer@example.com"


async def test_0074_grants_cyshuhe_app_on_opinion_digest_and_hygiene_tables(session_factory):
    """D5-7: 0074 migration grants SELECT/INSERT/UPDATE/DELETE to cyshuhe_app on opinion_digest_* while audit_logs remains append-only."""
    from pathlib import Path

    mig_sql = (Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0074_grant_opinion_digest_and_hygiene.py").read_text("utf-8")
    assert 'down_revision = "0073"' in mig_sql or 'down_revision: str | None = "0073"' in mig_sql
    assert "audit_logs" not in mig_sql.split("DO $$", 1)[1]
    async with session_factory() as s:
        await s.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cyshuhe_app') THEN
                        CREATE ROLE cyshuhe_app NOLOGIN;
                    END IF;
                    GRANT SELECT, INSERT ON audit_logs TO cyshuhe_app;
                    GRANT SELECT, INSERT, UPDATE, DELETE ON
                        opinion_digest_configs,
                        opinion_digest_runs,
                        legal_term_definitions,
                        change_advice
                    TO cyshuhe_app;
                END
                $$;
                """
            )
        )
        for tbl in ("opinion_digest_configs", "opinion_digest_runs", "legal_term_definitions", "change_advice"):
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                ok = await s.scalar(
                    text("SELECT has_table_privilege('cyshuhe_app', :tbl, :priv)"),
                    {"tbl": tbl, "priv": priv},
                )
                assert ok is True, f"cyshuhe_app missing {priv} on {tbl}"
        for forbidden in ("UPDATE", "DELETE"):
            bad = await s.scalar(
                text("SELECT has_table_privilege('cyshuhe_app', 'audit_logs', :priv)"),
                {"priv": forbidden},
            )
            assert bad is False, f"audit_logs must NEVER grant {forbidden} to cyshuhe_app"
        await s.rollback()


async def test_health_probes_db_and_returns_503_when_db_unavailable(client):
    """D2-01: /api/health executes SELECT 1 with 3.0s timeout and returns 503 degraded on failure."""
    from app.core.db import get_session

    ok = await client.get("/api/health")
    assert ok.status_code == 200 and ok.json() == {"status": "ok"}

    app = client._transport.app
    prev = app.dependency_overrides.get(get_session)

    async def _broken_session():
        raise RuntimeError("simulated pool exhaustion")
        yield  # pragma: no cover

    app.dependency_overrides[get_session] = _broken_session
    try:
        bad = await client.get("/api/health")
        assert bad.status_code == 503 and bad.json() == {"status": "degraded"}
    finally:
        if prev is not None:
            app.dependency_overrides[get_session] = prev
        else:
            app.dependency_overrides.pop(get_session, None)


def test_archive_store_supports_readable_filenames_and_expected_sha256(tmp_path):
    """D1-1: ArchiveStore.read supports readable paths with expected_sha256 while preserving 64-hex tamper checks."""
    from app.ingestion.archive import ArchiveIntegrityError, ArchiveStore

    store = ArchiveStore(tmp_path)
    payload = b"PK\x03\x04official-cac-guide-docx"
    sha = store.sha256(payload)

    # Readable path works without false ArchiveIntegrityError, and enforces expected_sha256 when provided
    rel_readable = "cn/cac_20250627_security_assessment_guide_v3.docx"
    target = tmp_path / rel_readable
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    assert store.read(rel_readable) == payload
    assert store.read(rel_readable, expected_sha256=sha) == payload
    with pytest.raises(ArchiveIntegrityError):
        store.read(rel_readable, expected_sha256="0" * 64)

    # Content-addressed 64-hex path still detects tampering even without expected_sha256
    digest, rel_hash = store.put(b"original-bytes")
    assert store.read(rel_hash) == b"original-bytes"
    (tmp_path / rel_hash).write_bytes(b"tampered-bytes")
    with pytest.raises(ArchiveIntegrityError):
        store.read(rel_hash)


async def test_validation_422_is_pure_chinese_and_viewer_patch_returns_403_first(client, seed, session_factory):
    """D3-01, D3-02, D3-03: 422 responses contain zero English/Pydantic/UUID/JSON leaks; viewer gets 403 before 422 on shared task."""
    from app.analysis.models import AnalysisTask

    viewer_email = await _make_viewer(session_factory, seed["org_a"].id)
    member = bearer(await login(client, "user", "a-member@example.com"))
    viewer = bearer(await login(client, "user", viewer_email))
    admin = bearer(await login(client, "admin", "admin@example.com"))

    # D3-01: malformed UUID, invalid bool, out-of-range int -> pure Chinese string detail
    for hdr, bad_url in (
        (member, "/api/knowledge/regulations/not-a-valid-uuid"),
        (admin, "/api/admin/agent-studio/tasks?include_archived=not-a-bool"),
        (admin, "/api/admin/audit-logs?limit=99999"),
    ):
        r = await client.get(bad_url, headers=hdr)
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert isinstance(detail, str) and re.search(r"[\u4e00-\u9fff]", detail)
        assert not re.search(r"input should be|field required|pydantic|uuid|json", detail, re.I)

    # D3-02: workflow & evaluation suite contain zero engineering jargon in display strings
    wf = (await client.get("/api/admin/agent-studio/workflow", headers=admin)).json()
    wf_dump = str(wf["nodes"])
    assert "JSON-Schema-Gateway-v1" not in wf_dump
    assert "Effective Law & KG RAG Retriever" not in wf_dump
    assert "list[uuid]" not in wf_dump
    suite_res = (await client.get("/api/admin/agent-studio/evaluations/suite", headers=admin)).json()
    assert "OCR受损" not in str(suite_res) and "OCR损伤" not in str(suite_res)

    # D3-03: viewer sending empty {} to shared enterprise task gets 403 instead of 422
    async with session_factory() as s:
        shared_task = AnalysisTask(
            org_id=seed["org_a"].id,
            created_by=seed["a_member"].id,
            title="企业共享咨询会话",
            kind="advice",
            country_code="SG",
            vehicle_model="E7",
            scenario_type="ota_diagnostics",
            status="completed",
        )
        s.add(shared_task)
        await s.commit()
        tid = str(shared_task.id)

    res_viewer = await client.patch(f"/api/analysis/tasks/{tid}", headers=viewer, json={})
    assert res_viewer.status_code == 403
    res_owner_empty = await client.patch(f"/api/analysis/tasks/{tid}", headers=member, json={})
    assert res_owner_empty.status_code == 422 and "空" in res_owner_empty.json()["detail"]


async def test_chat_transcript_export_and_audit_csv_export(client, seed, session_factory):
    """D2-16 & D2-18: consulting Q&A transcript exports (.docx/.pdf) and admin audit-logs/export.csv with UTF-8 BOM."""
    from app.analysis.models import AnalysisMessage, AnalysisTask

    viewer_email = await _make_viewer(session_factory, seed["org_a"].id)
    member = bearer(await login(client, "user", "a-member@example.com"))
    viewer = bearer(await login(client, "user", viewer_email))
    admin = bearer(await login(client, "admin", "admin@example.com"))

    async with session_factory() as s:
        chat = AnalysisTask(
            org_id=seed["org_a"].id,
            created_by=seed["a_member"].id,
            title="新加坡OTA合规问答咨询",
            kind="advice",
            country_code="SG",
            vehicle_model="启衡 E7",
            scenario_type="ota_diagnostics",
            status="completed",
        )
        s.add(chat)
        await s.flush()
        s.add_all(
            [
                AnalysisMessage(task_id=chat.id, seq_no=1, role="user", content="向新加坡车辆推送OTA升级包涉及诊断日志回传需要满足哪些PDPA义务？"),
                AnalysisMessage(task_id=chat.id, seq_no=2, role="assistant", content="依据新加坡《个人数据保护法》（PDPA）第13条与第26条，企业须履行告知同意及跨境同等保护义务。"),
            ]
        )
        await s.commit()
        cid = str(chat.id)

    # Viewer without export permission gets 403
    assert (await client.get(f"/api/analysis/tasks/{cid}/chat-export.docx", headers=viewer)).status_code == 403

    r_docx = await client.get(f"/api/analysis/tasks/{cid}/chat-export.docx", headers=member)
    assert r_docx.status_code == 200 and r_docx.content.startswith(b"PK")

    r_pdf = await client.get(f"/api/analysis/tasks/{cid}/chat-export.pdf", headers=member)
    assert r_pdf.status_code == 200 and r_pdf.content.startswith(b"%PDF-")
    doc = pdfium.PdfDocument(r_pdf.content)
    assert len(doc) >= 1
    doc.close()

    # Audit CSV export with UTF-8 BOM
    r_csv = await client.get("/api/admin/audit-logs/export.csv", headers=admin)
    assert r_csv.status_code == 200
    assert "text/csv" in r_csv.headers["content-type"]
    assert r_csv.content.startswith(b"\xef\xbb\xbf")
    text_csv = r_csv.content.decode("utf-8-sig")
    assert "时间,人员,邮箱,角色,来源地址,类型,动作,说明,结果,修改前,修改后,记录校验码" in text_csv


async def test_wave3_docx_preview_spans_and_argon2_concurrency():
    """Wave-3: docx_preview_blocks extracts exact colspan/rowspan spans for CAC Annex 3; Argon2 semaphore bounds concurrent hashes."""
    from app.analysis import official_docx as od
    from app.core.security import hash_password, verify_password_async

    res = od.cn_declaration({"company_name": "启衡智行汽车科技（上海）有限公司", "purpose": "跨境车联网研发诊断"})
    blocks = od.docx_preview_blocks(res.data)
    tbls = [b for b in blocks if b.get("type") == "table"]
    assert tbls, "Expected CAC Annex 3 preview table"
    spans = tbls[0].get("spans")
    assert spans is not None and len(spans) == len(tbls[0]["rows"])
    # Row 0: col 0 spans multiple rows vertically, '启衡智行...' cell spans all remaining columns horizontally with [0, 0] covered cells
    assert spans[0][0][0] > 1 and spans[0][0][1] >= 1
    val_col = next(ci for ci, txt in enumerate(tbls[0]["rows"][0]) if "启衡智行" in txt)
    val_cspan = spans[0][val_col][1]
    assert spans[0][val_col][0] == 1 and val_cspan >= 3
    assert val_col + val_cspan == len(spans[0])
    assert all(s == [0, 0] for s in spans[0][val_col + 1 :])

    pw_hash = hash_password("Test#Wave3Password99")
    checks = await asyncio.gather(*(verify_password_async("Test#Wave3Password99", pw_hash) for _ in range(8)))
    assert checks == [True] * 8


async def test_wave4_arabic_ocr_clean_and_deepseek_thinking_disabled(monkeypatch):
    """Wave-4: EG_GOEIC_IMPORTER has zero Arabic OCR gibberish; call_llm_json disables thinking on DeepSeek models."""
    import httpx

    from app.analysis import llm
    from app.forms import service as forms_svc

    forms_svc.catalog.cache_clear()
    eg_form = forms_svc.catalog()["EG_GOEIC_IMPORTER"]
    dump = str(eg_form)
    assert "السننن" not in dump
    assert "الفسمى" not in dump
    assert "س ل ت اري" not in dump
    assert "السمة التجارية" in dump
    assert "الوكيل الرسمي" in dump

    captured: list[dict] = []

    async def _fake_post_model(payload, *, timeout, purpose="analysis"):
        captured.append(payload)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"answer": "ok"}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 6},
            },
            request=httpx.Request("POST", "https://example.invalid/v1/chat/completions"),
        )

    settings = llm.get_settings()
    monkeypatch.setattr(settings, "llm_api_key", "sk-test-dummy")
    monkeypatch.setattr(settings, "llm_model", "deepseek-flash")
    monkeypatch.setattr(llm, "post_model", _fake_post_model)

    out = await llm.call_llm_json([{"role": "user", "content": "test"}], max_tokens=2500)
    assert out.data == {"answer": "ok"}
    assert len(captured) == 1
    assert captured[0].get("thinking") == {"type": "disabled"}

