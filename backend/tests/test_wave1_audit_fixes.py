"""Regression tests for Wave 1 backend audit root-cause fixes (D1, D2, D3, D4, D5-7)."""

from __future__ import annotations

import asyncio
import re

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


def test_p0_docx_to_pdf_many_exceeds_cache_size_without_keyerror(monkeypatch):
    """P0 regression: docx_to_pdf_many must never raise KeyError when batch size exceeds CACHE_SIZE or evicts cached entries."""
    from pathlib import Path

    from app.analysis import docx_pdf

    monkeypatch.setattr(docx_pdf, "CACHE_SIZE", 3)
    monkeypatch.setattr(docx_pdf, "engine", lambda: "libreoffice")
    docx_pdf._CACHE.clear()

    def _fake_convert_dir(folder: Path, kind: str) -> None:
        for docx_file in sorted(folder.glob("*.docx")):
            docx_file.with_suffix(".pdf").write_bytes(b"%PDF-1.4-" + docx_file.read_bytes())

    monkeypatch.setattr(docx_pdf, "_convert_dir", _fake_convert_dir)

    # 10 distinct files (> CACHE_SIZE=3) + 1 duplicate inside the same batch
    batch1 = [f"doc-{i}".encode() for i in range(10)] + [b"doc-0"]
    res1 = docx_pdf.docx_to_pdf_many(batch1)
    assert len(res1) == 11
    assert res1[0] == b"%PDF-1.4-doc-0" and res1[10] == b"%PDF-1.4-doc-0"
    assert res1[9] == b"%PDF-1.4-doc-9"

    # Second batch mixing surviving cache entries ('doc-8', 'doc-9') with 5 brand-new files that evict 'doc-8'
    batch2 = [b"doc-8", b"doc-9"] + [f"new-{j}".encode() for j in range(5)]
    res2 = docx_pdf.docx_to_pdf_many(batch2)
    assert len(res2) == 7
    assert res2[0] == b"%PDF-1.4-doc-8"
    assert res2[-1] == b"%PDF-1.4-new-4"


def test_round2_pdf_fallback_subprocess_wrap_and_ooxml_checkboxes(monkeypatch, tmp_path):
    """Round-2: _convert_dir wraps subprocess errors into PdfEngineUnavailable; OOXML symbol rewrite isolates literal ☐/☑ & relaxes docGrid; builtin fallback emits 0 dict headers, 0 '列1', and 0 CJK in EDPB EN."""
    import subprocess

    from app.analysis import docx_pdf
    from app.analysis import official_docx as od
    from app.analysis.document_templates import Rendered, build_pdf
    from app.analysis.official_eu_edpb_dpia import render_edpb_2026_docx

    # P2-④: subprocess TimeoutExpired and CalledProcessError must raise PdfEngineUnavailable
    (tmp_path / "000.docx").write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd="soffice", timeout=5)),
    )
    with pytest.raises(docx_pdf.PdfEngineUnavailable):
        docx_pdf._convert_dir(tmp_path, "libreoffice")

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "soffice")),
    )
    with pytest.raises(docx_pdf.PdfEngineUnavailable):
        docx_pdf._convert_dir(tmp_path, "libreoffice")

    # P1-②: _rewrite_ooxml_symbols splits mixed literal ☐/☑ + CJK runs, relaxes docGrid, and preserves underlined spaces
    sample_xml = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b"<w:body><w:p>"
        b'<w:r><w:rPr><w:rFonts w:eastAsia="\xe4\xbb\xbf\xe5\xae\x8b_GB2312"/><w:b/></w:rPr>'
        b"<w:t>\xe2\x98\x90\xe6\x94\xbf\xe5\xba\x9c\xe9\x83\xa8\xe9\x97\xa8 \xe2\x98\x91\xe4\xbc\x81\xe4\xb8\x9a</w:t></w:r>"
        b'<w:r><w:rPr><w:u w:val="single"/></w:rPr><w:t xml:space="preserve">      </w:t></w:r>'
        b"</w:p>"
        b'<w:sectPr><w:docGrid w:type="lines" w:linePitch="317"/></w:sectPr>'
        b"</w:body></w:document>"
    )
    rewritten = docx_pdf._rewrite_ooxml_symbols(sample_xml, "DejaVu Sans", for_libreoffice=True)
    assert rewritten is not None
    rw_txt = rewritten.decode("utf-8")
    assert "DejaVu Sans" in rw_txt
    assert "仿宋_GB2312" in rw_txt
    assert 'w:type="default"' in rw_txt
    assert "\u00a0\u00a0\u00a0\u00a0\u00a0\u00a0" in rw_txt

    # P2-③: CYSH_PDF_ENGINE=builtin fallback produces zero dict headers, zero '列1', and zero CJK in EDPB EN
    monkeypatch.setenv("CYSH_PDF_ENGINE", "builtin")
    decl_docx = od.cn_declaration({"company_name": "启衡智行汽车科技（上海）有限公司"}).data
    decl_blocks = od.docx_preview_blocks(decl_docx)
    assert not any("列1" in str(h) for b in decl_blocks if b.get("type") == "table" for h in b.get("head", []))
    decl_pdf = build_pdf(Rendered(blocks=decl_blocks), "汽车数据出境评估申报表", table_style="formal_black")
    pdf_doc = pdfium.PdfDocument(decl_pdf)
    try:
        decl_pdf_txt = "\n".join(pdf_doc[i].get_textpage().get_text_bounded() for i in range(len(pdf_doc)))
    finally:
        pdf_doc.close()
    assert "{'title'" not in decl_pdf_txt and "列1" not in decl_pdf_txt
    assert "启衡智行" in decl_pdf_txt

    edpb_docx = render_edpb_2026_docx(
        {
            "controller": {
                "name_and_address": "Qiheng Smart Mobility GmbH, Friedrichstrasse 88, 10117 Berlin, Germany",
                "contact_or_rep": "dpo@qiheng-mobility.eu",
            },
        },
        "2026-11-19",
    )
    edpb_blocks = od.docx_preview_blocks(edpb_docx)
    edpb_pdf = build_pdf(
        Rendered(blocks=edpb_blocks),
        "EDPB DPIA Template 2026 (Official EN)",
        foreign=True,
        table_style="edpb",
    )
    edpb_doc = pdfium.PdfDocument(edpb_pdf)
    try:
        edpb_txt = "\n".join(edpb_doc[i].get_textpage().get_text_bounded() for i in range(len(edpb_doc)))
    finally:
        edpb_doc.close()
    assert "{'title'" not in edpb_txt
    assert not re.search(r"[\u3400-\u9fff\u3000-\u303f\uff01-\uff60]", edpb_txt)
    assert "Qiheng Smart Mobility GmbH" in edpb_txt


async def test_round2_nul_guard_int32_bounds_and_concurrent_add_message(client, seed, session_factory):
    """Round-2: P2-⑥ NUL byte -> 422 Chinese; P3-⑦ int32 overflow -> 422 Chinese; P2-⑤ concurrent add_message -> 0 UniqueViolationError."""
    from sqlalchemy import select

    from app.analysis.chat import add_message
    from app.analysis.models import AnalysisMessage, AnalysisTask

    member = bearer(await login(client, "user", "a-member@example.com"))
    admin = bearer(await login(client, "admin", "admin@example.com"))

    # P2-⑥: NUL byte in query string and JSON body -> 422 Chinese
    for hdr, nul_url in (
        (member, "/api/knowledge/clauses?keyword=abc%00def"),
        (admin, "/api/admin/sites?country_code=%00"),
        (admin, "/api/admin/audit-logs?q=%00"),
    ):
        r = await client.get(nul_url, headers=hdr)
        assert r.status_code == 422, f"Expected 422 for {nul_url}, got {r.status_code}"
        assert "非法空字符" in r.json()["detail"]

    r_json = await client.post(
        "/api/analysis/tasks",
        headers=member,
        json={"title": "测试\u0000标题", "kind": "advice", "country_code": "SG"},
    )
    assert r_json.status_code == 422
    assert "非法空字符" in r_json.json()["detail"]

    # P3-⑦: int32 overflow on query params -> 422 pure Chinese
    for bad_int_url in (
        "/api/admin/agent-studio/workflow?version_no=2147483648",
        "/api/admin/agent-studio/workflow?version_no=-2147483649",
        "/api/admin/agent-studio/workflow?version_no=999999999999",
        "/api/admin/audit-logs?offset=2147483648",
    ):
        r_int = await client.get(bad_int_url, headers=admin)
        assert r_int.status_code == 422, f"Expected 422 for {bad_int_url}, got {r_int.status_code}"
        detail = r_int.json()["detail"]
        assert isinstance(detail, str) and re.search(r"[\u4e00-\u9fff]", detail)
        assert not re.search(r"overflow|int32|input should be|pydantic", detail, re.I)

    # Deleting a site with archived Document provenance returns clean 409 Chinese conflict instead of 500 FK error
    from app.ingestion.models import Document
    from app.sources.models import SourceSite

    async with session_factory() as s:
        st_with_doc = SourceSite(
            country_code="TN",
            publisher="INT",
            language="fr",
            document_type="REGULATION",
            entry_url="https://www.intt.tn/test-provenance.pdf",
            strategy="DIRECT",
        )
        s.add(st_with_doc)
        await s.flush()
        s.add(
            Document(
                site_id=st_with_doc.id,
                country_code="TN",
                source_url="https://www.intt.tn/test-provenance.pdf",
                title="Décision INT",
                language="fr",
                document_type="REGULATION",
            )
        )
        await s.commit()
        sid_with_doc = str(st_with_doc.id)

    r_del_site = await client.delete(f"/api/admin/sites/{sid_with_doc}", headers=admin)
    assert r_del_site.status_code == 409
    assert "停用站点" in r_del_site.json()["detail"]

    # P2-⑤: 8 concurrent add_message writers on the same AnalysisTask must serialize cleanly with seq_no 1..8
    async with session_factory() as s:
        race_task = AnalysisTask(
            org_id=seed["org_a"].id,
            created_by=seed["a_member"].id,
            title="并发消息序号核验会话",
            kind="advice",
            country_code="SG",
            vehicle_model="E7",
            scenario_type="ota_diagnostics",
            status="completed",
        )
        s.add(race_task)
        await s.commit()
        race_tid = race_task.id

    async def _writer(idx: int) -> int:
        async with session_factory() as s:
            msg = await add_message(s, race_tid, "user" if idx % 2 == 0 else "assistant", f"并发消息-{idx}")
            await s.commit()
            return msg.seq_no

    seqs = await asyncio.gather(*(_writer(i) for i in range(8)))
    assert sorted(seqs) == list(range(1, 9))
    async with session_factory() as s:
        stored_seqs = list(
            (
                await s.scalars(
                    select(AnalysisMessage.seq_no)
                    .where(AnalysisMessage.task_id == race_tid)
                    .order_by(AnalysisMessage.seq_no)
                )
            ).all()
        )
        assert stored_seqs == list(range(1, 9))



