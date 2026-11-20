"""Adversarial target-convergence regression tests (2026-11-19):
- Gateway UTF-8 / null-byte / lone-surrogate / NaN sanitization & pure-Chinese HTTP errors
- Audit log control-character scrubbing & Excel export safety
- SSO email validation, .local domain rejection on create_user, and 10-way concurrent SSO idempotency
- Foreign-key cascade delete on AnalysisTask with UpdateImpact & UserNotification dependents
- 10-way concurrent UserSubscription atomic upsert
- Redactor case-insensitive residual placeholder leak guard
- PoliteFetcher CGNAT (100.100.100.200) & decimal/hex IP SSRF blocking + HTML /robots.txt fallback
- Corrupted .docx XML upload graceful 422 handling
- Foreign official forms: static Regular-400 font, pure-French MA_F214 annex, builtin SG_IMDA_SDOC 0-CJK header, TN 8-box phone normalization
- Non-China declaration title & label localization + CAC declaration <w:cantSplit/>
- Knowledge graph 8-attribute node editing via PATCH /api/admin/knowledge/nodes/{id}
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import zipfile
from datetime import UTC, datetime, timedelta

import httpx
import openpyxl
import pypdfium2 as pdfium
import pytest
from sqlalchemy import func, select

from app.analysis.models import AnalysisTask, ReportVersion
from app.core.redaction import Redactor
from app.core.security import create_portal_ticket
from app.forms import pdf_fill
from app.forms import service as forms_service
from app.iam import service as iam_service
from app.iam.models import Role
from app.iam.service import RuleError
from app.ingestion.fetcher import BlockedError, PoliteFetcher, is_blocked_host
from app.ingestion.models import Clause, Document, DocumentVersion
from app.sources.models import SourceSite
from app.updates.models import UpdateImpact, UserNotification, UserSubscription, VersionChange
from tests.conftest import PASSWORD, bearer, login

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
JARGON_RE = re.compile(
    r"\b(Pydantic|Traceback|KeyError|TypeError|AttributeError|ValidationError|UUID|JSON|LLM|OCR|RAG)\b",
    re.I,
)


@pytest.mark.asyncio
async def test_gateway_input_sanitization_and_chinese_http_errors(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))

    # 1. Invalid UTF-8 bytes on application/json -> 422 Chinese
    r_utf8 = await client.post(
        "/api/auth/user/login",
        content=b'{"email": "\xff\xfe", "password": "x"}',
        headers={"Content-Type": "application/json"},
    )
    assert r_utf8.status_code == 422
    assert CJK_RE.search(r_utf8.json()["detail"])
    assert not JARGON_RE.search(r_utf8.json()["detail"])

    # 2. Null byte \u0000 and lone UTF-16 surrogate \ud800 -> 422 Chinese
    for bad_json in (
        '{"email": "a\\u0000@example.com", "password": "x"}',
        '{"email": "a\\ud800@example.com", "password": "x"}',
        '{"score": NaN}',
    ):
        r_bad = await client.post(
            "/api/auth/user/login",
            content=bad_json.encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        assert r_bad.status_code == 422
        detail = str(r_bad.json().get("detail", ""))
        assert CJK_RE.search(detail)
        assert not JARGON_RE.search(detail)

    # 3. Unknown route & wrong method -> Chinese 404 / 405 (no Starlette English 'Not Found')
    r_404 = await client.get("/api/non-existent-endpoint-1119", headers=auth)
    assert r_404.status_code == 404
    assert r_404.json()["detail"] != "Not Found"
    assert CJK_RE.search(r_404.json()["detail"])

    r_405 = await client.delete("/api/health", headers=auth)
    assert r_405.status_code == 405
    assert r_405.json()["detail"] != "Method Not Allowed"
    assert CJK_RE.search(r_405.json()["detail"])


@pytest.mark.asyncio
async def test_audit_log_control_char_scrubbing_and_xlsx_export(client, seed):
    admin_auth = bearer(await login(client, "admin", "admin@example.com"))
    user_auth = bearer(await login(client, "user", "a-member@example.com"))

    # Trigger an audited write containing C0 control chars in query string
    await client.post(
        "/api/analysis/doc-projects?note=%01%07%1f",
        headers=user_auth,
        json={"source_country": "新加坡", "recipient_country": "摩洛哥", "function": "ota_diagnostics"},
    )
    r_xlsx = await client.get("/api/admin/audit-logs/export.xlsx", headers=admin_auth)
    assert r_xlsx.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r_xlsx.content))
    assert wb.active.max_row >= 2


@pytest.mark.asyncio
async def test_sso_email_validation_local_domain_guard_and_concurrent_exchange(client, seed, session_factory):
    # 1. .local email rejected in create_user before poisoning DB
    async with session_factory() as s:
        with pytest.raises(RuleError, match="邮箱"):
            await iam_service.create_user(
                s,
                email="superadmin@coldboot.local",
                display_name="内网超管",
                password=PASSWORD,
                role=Role.PLATFORM_ADMIN,
                org_id=None,
            )

    # 2. Invalid email in signed SSO ticket rejected cleanly without 500
    bad_ticket = create_portal_ticket(
        email="bad-user@corp.local",
        display_name="测试员",
        org_name="测试车企有限公司",
        tier="enterprise",
    )
    r_bad = await client.post("/api/auth/portal/exchange", json={"ticket": bad_ticket})
    assert r_bad.status_code in (401, 422)
    assert CJK_RE.search(r_bad.json()["detail"])

    # 3. 10-way concurrent SSO exchange for a brand-new enterprise user -> 0 500s
    good_ticket = create_portal_ticket(
        email="concurrent-sso@qiheng-auto.com",
        display_name="并发单点用户",
        org_name="并发联调汽车科技有限公司",
        tier="enterprise",
    )
    resps = await asyncio.gather(
        *[client.post("/api/auth/portal/exchange", json={"ticket": good_ticket}) for _ in range(10)]
    )
    statuses = [r.status_code for r in resps]
    assert 500 not in statuses
    assert all(st == 200 for st in statuses), statuses


@pytest.mark.asyncio
async def test_fk_cascade_task_delete_and_concurrent_subscriptions(client, seed, session_factory):
    user_auth = bearer(await login(client, "user", "a-member@example.com"))

    # Create task + synthetic UpdateImpact + UserNotification referencing it, then permanent delete
    async with session_factory() as s:
        site = SourceSite(
            country_code="SG",
            publisher="SG Official",
            language="en",
            document_type="law",
            entry_url="https://sso.agc.gov.sg",
        )
        s.add(site)
        await s.flush()
        doc = Document(
            site_id=site.id,
            country_code="SG",
            title="PDPA 2012",
            source_url="https://sso.agc.gov.sg/Act/PDPA2012",
            document_type="law",
            language="en",
        )
        s.add(doc)
        await s.flush()
        dv1 = DocumentVersion(
            document_id=doc.id,
            version_no=1,
            content_hash="a" * 64,
            archive_path="SG/v1.pdf",
            size_bytes=1024,
            mime_type="application/pdf",
            final_url="https://sso.agc.gov.sg/Act/PDPA2012",
            state="parsed",
        )
        dv2 = DocumentVersion(
            document_id=doc.id,
            version_no=2,
            content_hash="b" * 64,
            archive_path="SG/v2.pdf",
            size_bytes=1048,
            mime_type="application/pdf",
            final_url="https://sso.agc.gov.sg/Act/PDPA2012",
            state="parsed",
        )
        s.add_all([dv1, dv2])
        await s.flush()
        now = datetime.now(UTC)
        vc = VersionChange(
            origin_key="test-origin-sg-pdpa-v2",
            document_id=doc.id,
            old_version_id=dv1.id,
            new_version_id=dv2.id,
            detected_at=now,
            change_kind="bytes_only",
            summary="官方原文版式更新",
            changes_json=[],
            graph_edge_ids=[],
            content_hashes={"old": "a" * 64, "new": "b" * 64},
        )
        s.add(vc)
        task = AnalysisTask(
            org_id=seed["org_a"].id,
            created_by=seed["a_member"].id,
            title="待删除联动测试对话",
            country_code="SG",
            vehicle_model="QH-X1",
            scenario_type="ota_diagnostics",
            status="completed",
            kind="chat",
        )
        s.add(task)
        await s.flush()
        rv = ReportVersion(
            org_id=seed["org_a"].id,
            task_id=task.id,
            version_no=1,
            change_note="初稿",
            executive_summary="摘要",
            dimensions_json=[],
            stale_impacts_json=[],
            created_by=seed["a_member"].id,
        )
        s.add(rv)
        await s.flush()
        imp = UpdateImpact(
            change_id=vc.id,
            org_id=seed["org_a"].id,
            task_id=task.id,
            report_version_id=rv.id,
            dimension_code="D3",
            detected_at=now,
            due_at=now + timedelta(hours=72),
        )
        s.add(imp)
        await s.flush()
        note = UserNotification(
            org_id=seed["org_a"].id,
            user_id=seed["a_member"].id,
            change_id=vc.id,
            impact_id=imp.id,
            task_id=task.id,
            kind="source_change",
            title="法规条文修订提醒",
            body="请核查影响",
        )
        s.add(note)
        await s.commit()
        tid = str(task.id)

    # Archive then permanently delete -> 200 OK (0 ForeignKeyViolationError!)
    r_arch = await client.delete(f"/api/analysis/tasks/{tid}", headers=user_auth)
    assert r_arch.status_code == 200
    r_perm = await client.delete(f"/api/analysis/tasks/{tid}?permanent=true", headers=user_auth)
    assert r_perm.status_code == 200

    # 10-way concurrent PUT /api/notifications/subscription -> 0 500s, single row in DB
    sub_resps = await asyncio.gather(
        *[
            client.put(
                "/api/notifications/subscription",
                headers=user_auth,
                json={"countries": ["SG"], "doc_types": ["law"], "topics": []},
            )
            for _ in range(10)
        ]
    )
    assert all(r.status_code == 200 for r in sub_resps), [r.text for r in sub_resps]
    async with session_factory() as s:
        cnt = await s.scalar(
            select(func.count()).select_from(UserSubscription).where(UserSubscription.org_id == seed["org_a"].id)
        )
        assert cnt == 1


@pytest.mark.asyncio
async def test_redactor_leak_guard_and_ssrf_cgnat_decimal_blocking():
    red = Redactor(aliases=("甲车企机密研究院",))
    masked = red.redact("甲车企机密研究院车端密钥 sk-1234567890abcdefghijklmnop")
    assert "甲车企机密研究院" not in masked
    with pytest.raises(ValueError):
        red.restore(f"模型异常回显了小写占位符 {red.prefix.strip('_').lower()}_zz 未映射")

    # SSRF: CGNAT 100.100.100.200 (Aliyun metadata), decimal IP, hex IP, .internal suffix
    for bad_host in (
        "100.100.100.200",
        "100.64.0.1",
        "2130706433",
        "0x7f.0.0.1",
        "0177.0.0.1",
        "metadata.internal",
    ):
        assert is_blocked_host(bad_host) is True, f"Expected {bad_host} to be blocked"

    async with httpx.AsyncClient() as hc:
        fetcher = PoliteFetcher(hc, user_agent="cyshuhe-test/1.0")
        with pytest.raises(BlockedError):
            await fetcher.get("http://100.100.100.200/latest/meta-data/")


@pytest.mark.asyncio
async def test_corrupted_docx_upload_returns_clean_422(client, seed):
    user_auth = bearer(await login(client, "user", "a-member@example.com"))
    r_proj = await client.post(
        "/api/analysis/doc-projects",
        headers=user_auth,
        json={"source_country": "新加坡", "recipient_country": "摩洛哥", "function": "ota_diagnostics"},
    )
    assert r_proj.status_code in (200, 201)
    pid = r_proj.json()["id"]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", b"<w:document><unclosed")
    r_up = await client.post(
        f"/api/analysis/doc-projects/{pid}/suggest-file?filename=broken.docx",
        headers=user_auth,
        content=buf.getvalue(),
    )
    assert r_up.status_code == 422
    assert CJK_RE.search(r_up.json()["detail"])


@pytest.mark.asyncio
async def test_foreign_forms_regular_font_french_annex_builtin_header_and_non_cn_declaration(client, seed):
    # 1. Static Regular-400 font loaded (never Thin hairline)
    assert pdf_fill.FONT_PATH.exists()

    # 2. MA_F214 overflow annex is 100% French with 0 CJK
    f214 = forms_service.get_form("MA_F214")
    txt_item = next(i for s in f214["sections"] for i in s["items"] if i["kind"] == "text")
    pdf_214, _, _ = forms_service.render(
        f214,
        {txt_item["id"]: "Diagnostic telematique et maintenance predictive des vehicules connectes " * 8},
        {"company_name": "Qiheng Mobility Maroc SARL"},
        allow_annex=True,
    )
    doc_214 = pdfium.PdfDocument(pdf_214)
    annex_txt = doc_214[len(doc_214) - 1].get_textpage().get_text_range()
    assert "Annexe" in annex_txt
    assert not CJK_RE.findall(annex_txt)
    assert b"NotoSansSC-Thin" not in pdf_214

    # 3. TN 8-box phone normalization (+216 71 856 418 -> 71856418 without overflow)
    tn_comm = forms_service.get_form("TN_INPDP_COMM")
    if tn_comm is not None:
        pdf_tn, rep_tn, too_long_tn = forms_service.render(
            tn_comm,
            {"contact_phone": "+216 71 856 418"},
            {"company_name": "Qiheng Mobility Tunisia SARL"},
        )
        assert pdf_tn[:4] == b"%PDF"
        assert not any(x["id"] == "contact_phone" for x in too_long_tn)

    # 4. Builtin SG_IMDA_SDOC PDF export has 0 CJK in header & body
    user_auth = bearer(await login(client, "user", "a-member@example.com"))
    r_inst = await client.post("/api/forms/instances", headers=user_auth, json={"form_code": "SG_IMDA_SDOC"})
    assert r_inst.status_code in (200, 201)
    iid = r_inst.json()["id"]
    prev_eng = os.environ.get("CYSH_PDF_ENGINE")
    os.environ["CYSH_PDF_ENGINE"] = "builtin"
    try:
        r_sdoc = await client.get(f"/api/forms/instances/{iid}/export.pdf", headers=user_auth)
        assert r_sdoc.status_code == 200
        doc_sdoc = pdfium.PdfDocument(r_sdoc.content)
        txt_sdoc = "\n".join(doc_sdoc[p].get_textpage().get_text_range() for p in range(len(doc_sdoc)))
        assert not CJK_RE.findall(txt_sdoc)
    finally:
        if prev_eng is None:
            os.environ.pop("CYSH_PDF_ENGINE", None)
        else:
            os.environ["CYSH_PDF_ENGINE"] = prev_eng

    # 5. Non-China route declaration localized vs China route official CAC <w:cantSplit/>
    r_non_cn = await client.post(
        "/api/analysis/doc-projects",
        headers=user_auth,
        json={"source_country": "埃及", "recipient_country": "法国", "function": "ota_diagnostics"},
    )
    non_cn_id = r_non_cn.json()["id"]
    r_prev = await client.get(f"/api/analysis/tasks/{non_cn_id}/documents/declaration/preview", headers=user_auth)
    assert r_prev.status_code == 200
    assert r_prev.json()["name"] == "跨境数据传输合规申报概览表"
    assert r_prev.json()["blocks"][0]["text"] == "跨境数据传输合规申报概览表"

    r_cn = await client.post(
        "/api/analysis/doc-projects",
        headers=user_auth,
        json={"source_country": "中国", "recipient_country": "埃及", "function": "ota_diagnostics"},
    )
    cn_id = r_cn.json()["id"]
    r_cn_docx = await client.get(
        f"/api/analysis/tasks/{cn_id}/documents/declaration/export.docx?draft=true",
        headers=user_auth,
    )
    assert r_cn_docx.status_code == 200
    xml_cn = zipfile.ZipFile(io.BytesIO(r_cn_docx.content)).read("word/document.xml").decode("utf-8")
    assert "数据出境安全评估申报表" in xml_cn
    assert "cantSplit" in xml_cn


@pytest.mark.asyncio
async def test_kg_node_eight_attributes_manual_edit(client, seed, session_factory):
    admin_auth = bearer(await login(client, "admin", "admin@example.com"))
    async with session_factory() as s:
        site = SourceSite(
            country_code="SG",
            publisher="SG",
            language="en",
            document_type="law",
            entry_url="https://sso.agc.gov.sg/acts",
        )
        s.add(site)
        await s.flush()
        doc = Document(
            site_id=site.id,
            country_code="SG",
            title="PDPA",
            source_url="https://sso.agc.gov.sg/PDPA",
            document_type="law",
            language="en",
        )
        s.add(doc)
        await s.flush()
        dv = DocumentVersion(
            document_id=doc.id,
            version_no=1,
            content_hash="c" * 64,
            archive_path="SG/pdpa.pdf",
            size_bytes=2048,
            mime_type="application/pdf",
            final_url="https://sso.agc.gov.sg/PDPA",
            state="parsed",
        )
        s.add(dv)
        await s.flush()
        cl = Clause(
            version_id=dv.id,
            ordinal=1,
            clause_number="26",
            original_text="An organisation must not transfer any personal data to a country or territory outside Singapore.",
            text_hash="d" * 64,
            language="en",
        )
        s.add(cl)
        await s.commit()
        cid = str(cl.id)

    r_create = await client.post(
        "/api/admin/knowledge/nodes",
        headers=admin_auth,
        json={
            "country_code": "SG",
            "node_type": "OBLIGATION",
            "name_zh": "跨境传输等效保护义务",
            "evidence_clause_id": cid,
        },
    )
    assert r_create.status_code == 201
    nid = r_create.json()["id"]

    r_patch = await client.patch(
        f"/api/admin/knowledge/nodes/{nid}",
        headers=admin_auth,
        json={
            "name_zh": "跨境传输等效保护义务",
            "summary": "第 26 条跨境限制要求",
            "attributes": {
                "applicable_industry": "智能网联汽车与车联网运营企业",
                "cross_border_condition": "签署具法律约束力标准合同并确保同等保护水平",
                "obligated_party": "数据控制组织",
            },
        },
    )
    assert r_patch.status_code == 200
    attrs = r_patch.json()["attributes"]
    assert attrs["applicable_industry"] == "智能网联汽车与车联网运营企业"
    assert attrs["cross_border_condition"] == "签署具法律约束力标准合同并确保同等保护水平"
    assert attrs["obligated_party"] == "数据控制组织"


@pytest.mark.asyncio
async def test_round2_redteam_convergence_1119(client, seed, session_factory):
    from app.analysis.document_instances import build_xlsx
    from app.analysis.document_narrative import DocumentNarrative

    admin_auth = bearer(await login(client, "admin", "admin@example.com"))
    user_auth = bearer(await login(client, "user", "a-member@example.com"))

    # 1. +json media type NUL bypass blocked at middleware
    r_plus_json = await client.post(
        "/api/analysis/doc-projects",
        content='{"source_country": "新加坡\\u0000", "recipient_country": "德国", "function": "ota_diagnostics"}'.encode(),
        headers={**user_auth, "Content-Type": "application/problem+json"},
    )
    assert r_plus_json.status_code == 422
    assert CJK_RE.search(r_plus_json.json()["detail"])

    # 2. Naive datetime & year-9999 boundary on recent_papers & audit-logs
    r_recent = await client.get(
        "/api/analysis/doc-projects/papers/recent?since=2026-11-19T00:00:00",
        headers=user_auth,
    )
    assert r_recent.status_code == 200

    r_audit = await client.get(
        "/api/admin/audit-logs?start=2026-11-19T00:00:00&end=9999-12-31T23:59:59Z",
        headers=admin_auth,
    )
    assert r_audit.status_code == 200

    # 3. build_xlsx strips XML illegal control characters (\x01, \x0b, \x0c)
    xlsx_data = build_xlsx(
        [{"type": "paragraph", "text": "合规措施\x01\x0b\x0c已完成"}],
        "测试\x0b文件",
    )
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_data))
    assert "合规措施已完成" in str(wb.active.cell(1, 1).value)

    # 4. Viewer (even with can_export=True & stale cache row) cannot trigger uncached EDPB EN LLM write
    async with session_factory() as s:
        viewer_u = await iam_service.create_user(
            s,
            email="viewer-export@example.com",
            display_name="viewer_export",
            password=PASSWORD,
            role=Role.VIEWER,
            org_id=seed["org_a"].id,
            can_export=True,
        )
    viewer_auth = bearer(await login(client, "user", viewer_u.email))
    r_eu = await client.post(
        "/api/analysis/doc-projects",
        headers=user_auth,
        json={"source_country": "突尼斯", "recipient_country": "法国", "function": "autonomous_trial"},
    )
    eu_id = r_eu.json()["id"]
    async with session_factory() as s:
        s.add(
            DocumentNarrative(
                org_id=seed["org_a"].id,
                task_id=r_eu.json()["id"],
                code="dpia_edpb_en",
                inputs_hash="stale-old-hash",
                blocks=[],
                report={"edpb_data": {"controller": {"name": "Old"}}},
                tokens=10,
            )
        )
        await s.commit()
    for ep in (
        f"/api/analysis/tasks/{eu_id}/documents/dpia/preview-edpb-en",
        f"/api/analysis/tasks/{eu_id}/documents/dpia/export-edpb-en.docx?draft=true",
        f"/api/analysis/tasks/{eu_id}/documents/dpia/export-edpb-en.pdf?draft=true",
    ):
        r_v = await client.get(ep, headers=viewer_auth)
        assert r_v.status_code == 403, f"{ep} -> {r_v.status_code} {r_v.text}"

    # 5. Foreign overflow annex strips CJK from version & avoids raw t35 slot labels
    lb_form = forms_service.get_form("LB_TRA_TA")
    if lb_form is not None:
        item0 = next(i for s in lb_form["sections"] for i in s["items"] if i["kind"] == "text")
        pdf_lb, _, _ = forms_service.render(
            lb_form,
            {item0["id"]: "Long technical specification of Class 1 RTTE automotive telematics unit " * 12},
            {"company_name": "Qiheng Smart Mobility SAL"},
            allow_annex=True,
        )
        doc_lb = pdfium.PdfDocument(pdf_lb)
        annex_lb = doc_lb[len(doc_lb) - 1].get_textpage().get_text_range()
        assert not CJK_RE.findall(annex_lb)
        assert not re.search(r"\bt\d+\b", annex_lb)

    # 6. Admin org creation rejects blank & trial-marker org names
    for bad_org in ("   ", "测试车企（体验空间-12345678）"):
        r_org = await client.post("/api/admin/orgs", headers=admin_auth, json={"name": bad_org})
        assert r_org.status_code == 422
        assert CJK_RE.search(r_org.json()["detail"])


@pytest.mark.asyncio
async def test_round3_redteam_convergence_1119(client, seed, session_factory):
    from app.analysis import official_docx as od
    from app.analysis import official_eu_edpb_dpia as edpb
    from app.knowledge.router import _scrub_quality_issues_zh

    admin_auth = bearer(await login(client, "admin", "admin@example.com"))

    # 1. SSO portal exchange & company_by_full_name block trial-space reserved marker
    trial_ticket = create_portal_ticket(
        email="intruder-sso@qiheng-auto.com",
        display_name="攻击测试员",
        org_name="张三（体验空间-1a2b3c4d）",
        tier="enterprise",
    )
    r_trial_sso = await client.post("/api/auth/portal/exchange", json={"ticket": trial_ticket})
    assert r_trial_sso.status_code == 422
    assert CJK_RE.search(r_trial_sso.json()["detail"])

    # 2. Deeply nested JSON (650 levels) with \u0000 or NaN returns 422 without RecursionError (500)
    deep_nul = ("[" * 650 + '"\\u0000"' + "]" * 650).encode("utf-8")
    r_deep = await client.post(
        "/api/auth/user/login",
        content=deep_nul,
        headers={"Content-Type": "application/json"},
    )
    assert r_deep.status_code == 422
    assert CJK_RE.search(r_deep.json()["detail"])

    # 3. Official DOCX & EDPB EN strip XML 1.0 illegal control chars (\x0b, \x0c, \x01) cleanly
    res_sc = od.cn_standard_contract(
        {
            "company_name": "启衡汽车\x0b科技有限公司\x0c",
            "recipient": "Qiheng\x01SG Pte. Ltd.",
            "purpose": "远程诊断\x07与电池预警",
            "recipient_country": "新加坡",
        }
    )
    blocks_sc = od.docx_preview_blocks(res_sc.data)
    assert any("启衡汽车科技有限公司" in str(b) for b in blocks_sc)

    edpb_bytes = edpb.render_edpb_2026_docx(
        edpb._sanitise_edpb_tree(
            {"controller": {"name_and_address": "Qiheng\x0bMobility\x0cSARL\x02"}, "processing_name": "OTA\x07Diag"},
            [],
        ),
        "2026-11-19",
    )
    blocks_edpb = od.docx_preview_blocks(edpb_bytes)
    assert blocks_edpb

    # 4. Translation quality_issues scrubbing leaves 0 LLM/CJK jargon
    scrubbed = _scrub_quality_issues_zh(
        [
            "LLM 服务不可用或未配置密钥，未生成译文",
            "CJK 字符占比未达标: 12.0% < 30.0%",
            "LLM unavailable; no context translation",
        ]
    )
    assert len(scrubbed) == 3
    assert all(not JARGON_RE.search(x) and "CJK" not in x for x in scrubbed)

    # 5. MA_F118 matrix overflow annex shows authentic French label (never Matrix/localisation)
    f118 = forms_service.get_form("MA_F118")
    mat_item = next(i for s in f118["sections"] for i in s["items"] if i["kind"] == "matrix")
    pdf_118, _, _ = forms_service.render(
        f118,
        {
            mat_item["id"]: {
                "localisation": {
                    "on": True,
                    "persons": ["clients"],
                    "origin": ["personne"],
                    "recipients": ["soustraitant"],
                    "purpose": "Diagnostic telematique continu et analyse de securite routiere transfrontaliere " * 6,
                    "duration": "6 mois",
                }
            }
        },
        {"company_name": "Qiheng Maroc SARL"},
        allow_annex=True,
    )
    doc_118 = pdfium.PdfDocument(pdf_118)
    annex_118 = doc_118[len(doc_118) - 1].get_textpage().get_text_range()
    assert "Matrix/" not in annex_118 and "matrix/" not in annex_118
    assert "Localisation géographique" in annex_118

    # 6. TN_ATTT_RECEPTION overflow tag uses French (Voir Annexe #1) matching its French annex
    tn_attt = forms_service.get_form("TN_ATTT_RECEPTION")
    if tn_attt is not None:
        txt_it = next(
            i
            for s in tn_attt["sections"]
            for i in s["items"]
            if i["kind"] == "text" and "phone" not in i["id"] and "email" not in i["id"]
        )
        pdf_attt, _, _ = forms_service.render(
            tn_attt,
            {txt_it["id"]: "Homologation par type de vehicule particulier connecte avec boitier telematique " * 8},
            {"company_name": "Qiheng Tunisia SARL"},
            allow_annex=True,
        )
        doc_attt = pdfium.PdfDocument(pdf_attt)
        p1_attt = doc_attt[0].get_textpage().get_text_range()
        assert "See Annex" not in p1_attt

    # 7. Ingestion reread_page & page_image handle corrupted PDF & missing OCR gracefully (no 500)
    async with session_factory() as s:
        site = SourceSite(
            country_code="SG",
            publisher="SG Test",
            language="en",
            document_type="law",
            entry_url="https://sso.agc.gov.sg/test",
        )
        s.add(site)
        await s.flush()
        doc_obj = Document(
            site_id=site.id,
            country_code="SG",
            title="Test Law",
            source_url="https://sso.agc.gov.sg/test/1",
            document_type="law",
            language="en",
        )
        s.add(doc_obj)
        await s.flush()
        from app.core.config import get_settings
        from app.ingestion.archive import ArchiveStore

        bad_bytes = b"not-a-valid-pdf-binary"
        digest, rel_path = ArchiveStore(get_settings().archive_dir).put(bad_bytes)
        ver_obj = DocumentVersion(
            document_id=doc_obj.id,
            version_no=1,
            content_hash=digest,
            archive_path=rel_path,
            size_bytes=len(bad_bytes),
            mime_type="application/pdf",
            final_url="https://sso.agc.gov.sg/test/1",
            state="downloaded",
        )
        s.add(ver_obj)
        await s.commit()
        vid = str(ver_obj.id)

    r_img = await client.get(f"/api/admin/versions/{vid}/pages/1.png", headers=admin_auth)
    assert r_img.status_code == 422
    assert not JARGON_RE.search(r_img.json()["detail"])
    r_rr = await client.post(f"/api/admin/versions/{vid}/pages/1/reread", headers=admin_auth)
    assert r_rr.status_code in (200, 422)
    if r_rr.status_code == 422:
        assert not JARGON_RE.search(r_rr.json()["detail"])


@pytest.mark.asyncio
async def test_round4_redteam_convergence_1119(client, seed, monkeypatch):
    admin_auth = bearer(await login(client, "admin", "admin@example.com"))
    user_auth = bearer(await login(client, "user", "a-member@example.com"))

    # 1. CN route risk_report & dpia strip XML 1.0 illegal control chars (\x0b, \x0c, \x01) cleanly
    r_cn = await client.post(
        "/api/analysis/doc-projects",
        headers=user_auth,
        json={
            "source_country": "中国",
            "recipient_country": "新加坡",
            "function": "ota_diagnostics",
            "company_name": "启衡汽车\x0b科技有限公司\x0c",
            "scenario_text": "车载远程诊断\x07与电池预警",
        },
    )
    assert r_cn.status_code in (200, 201)
    cn_pid = r_cn.json()["id"]
    for doc_code in ("risk_report", "dpia"):
        r_prev = await client.get(f"/api/analysis/tasks/{cn_pid}/documents/{doc_code}/preview", headers=user_auth)
        assert r_prev.status_code == 200
        r_xlsx = await client.get(
            f"/api/analysis/tasks/{cn_pid}/documents/{doc_code}/export.xlsx?draft=true", headers=user_auth
        )
        assert r_xlsx.status_code == 200

    # 2. Extreme timezone-offset datetimes (9999-12-31T23:59:59-08:00 / 0001-01-01T00:00:00+08:00) -> 422 (no 500)
    for ep in (
        "/api/admin/audit-logs?end=9999-12-31T23:59:59-08:00",
        "/api/admin/audit-logs?start=0001-01-01T00:00:00%2B08:00",
        "/api/admin/audit-logs/export.xlsx?end=9999-12-31T23:59:59-08:00",
    ):
        r_dt = await client.get(ep, headers=admin_auth)
        assert r_dt.status_code == 422
        assert CJK_RE.search(r_dt.json()["detail"])
    r_rec = await client.get(
        "/api/analysis/doc-projects/papers/recent?since=9999-12-31T23:59:59-08:00", headers=user_auth
    )
    assert r_rec.status_code == 422

    # 3. 8-way concurrent PATCH /api/analysis/doc-projects/{id} -> 0 409/500 on uq_step_trace_task_seq
    patch_resps = await asyncio.gather(
        *[
            client.patch(
                f"/api/analysis/doc-projects/{cn_pid}",
                headers=user_auth,
                json={"title": f"并发标题核验_{idx}"},
            )
            for idx in range(8)
        ]
    )
    assert all(r.status_code == 200 for r in patch_resps), [(r.status_code, r.text) for r in patch_resps]

    # 4. PoliteFetcher.post_form & post_json strictly enforce robots.txt disallow
    async def fake_check_target(self, url: str) -> str:
        return "example.gov.sg"

    async def fake_robots_disallowed(self, url: str) -> bool:
        return False

    monkeypatch.setattr(PoliteFetcher, "_check_target", fake_check_target)
    monkeypatch.setattr(PoliteFetcher, "_robots_allowed", fake_robots_disallowed)
    async with httpx.AsyncClient() as hc:
        pf = PoliteFetcher(hc, user_agent="cyshuhe-test/1.0")
        with pytest.raises(BlockedError, match="robots.txt"):
            await pf.post_form("https://example.gov.sg/search", {"q": "test"})
        with pytest.raises(BlockedError, match="robots.txt"):
            await pf.post_json("https://example.gov.sg/api/search", {"q": "test"})


@pytest.mark.asyncio
async def test_round5_redteam_convergence_1119(client, seed, session_factory):
    from types import SimpleNamespace

    from app.analysis import attachments as att
    from app.audit.service import _is_trusted_proxy
    from app.core.redaction import org_redaction_aliases, redact
    from app.ingestion import ref_check, word_text
    from app.ingestion.router import clean_admin_process_text

    admin_auth = bearer(await login(client, "admin", "admin@example.com"))

    # 1. ref_check._SKIP excludes all append-only audit/observation/version_changes tables
    for immutable_tbl in (
        "audit_logs",
        "knowledge_audit_records",
        "llm_call_audit",
        "version_changes",
        "source_fetch_observations",
        "agent_workflow_configs",
    ):
        assert immutable_tbl in ref_check._SKIP

    # 2. Trial-space org_redaction_aliases masks bare company name AND admin IAM blocks trial-space writes
    async with session_factory() as s:
        from app.iam.models import Organization

        trial_user, _ = await iam_service.register_portal_user(
            s,
            email="trial-redaction-1119@qiheng-auto.com",
            display_name="体验访客",
            org_name="华星智联汽车研究院",
            password=PASSWORD,
        )
        trial_org = await s.get(Organization, trial_user.org_id)
        aliases = org_redaction_aliases(trial_org)
        assert "华星智联汽车研究院" in aliases
        masked, _ = redact("我们华星智联汽车研究院计划向新加坡传输遥测日志", aliases=aliases)
        assert "华星智联汽车研究院" not in masked

    r_red = await client.patch(
        f"/api/admin/orgs/{trial_user.org_id}/redaction",
        headers=admin_auth,
        json={"terms": ["机密词"]},
    )
    assert r_red.status_code == 404
    r_add_u = await client.post(
        "/api/admin/users",
        headers=admin_auth,
        json={
            "email": "illegal-trial-member@qiheng-auto.com",
            "display_name": "越权成员",
            "password": PASSWORD,
            "role": "member",
            "org_id": str(trial_user.org_id),
        },
    )
    assert r_add_u.status_code == 422
    r_patch_u = await client.patch(
        f"/api/admin/users/{trial_user.id}",
        headers=admin_auth,
        json={"role": "member", "can_export": True},
    )
    assert r_patch_u.status_code == 403

    # 3. client_ip CIDR subnet matching supports standard Docker/private CIDR blocks
    trusted = ["127.0.0.1/32", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
    assert _is_trusted_proxy("127.0.0.1", trusted) is True
    assert _is_trusted_proxy("172.18.0.5", trusted) is True
    assert _is_trusted_proxy("203.0.113.9", trusted) is False

    # 4. Admin process text sanitizer removes OCR/JSON/PDFium/exception class names
    for raw_err in (
        '{"columns": 2, "header_stripped": true}',
        "ParseError: PDF 解析失败: Failed to load document (PDFium: Data format error)",
        "扫描件无文字层,需要 OCR",
        "网络错误(ConnectTimeout),无法确认规则,不抓取",
    ):
        cleaned = clean_admin_process_text(raw_err)
        assert cleaned and not JARGON_RE.search(cleaned)
        assert "PDFium" not in cleaned and "ConnectTimeout" not in cleaned

    # 5. .docx Zip Bomb uncompressed size cap blocks oversized XML before DOM allocation
    fake_info = SimpleNamespace(filename="word/document.xml", file_size=40 * 1024 * 1024)
    assert fake_info.file_size > att._MAX_DOCX_XML_BYTES
    assert fake_info.file_size > word_text._MAX_DOCX_XML_BYTES

    # 6. User whitepaper country_page exposes has_whitepaper boolean for empty-DB cold boot
    user_auth = bearer(await login(client, "user", "a-member@example.com"))
    r_wp_page = await client.get("/api/knowledge/whitepapers/SG/page", headers=user_auth)
    assert r_wp_page.status_code == 200
    assert r_wp_page.json().get("has_whitepaper") is False


@pytest.mark.asyncio
async def test_round6_redteam_convergence_1119(client, seed, session_factory):
    from app.audit.models import AuditLog
    from app.audit.service import _clean_text

    admin_auth = bearer(await login(client, "admin", "admin@example.com"))

    # 1. Workflow import with invalid Chinese model_name returns pure Chinese 400 (no Pydantic ValidationError repr)
    r_wf = await client.get("/api/admin/agent-studio/workflow", headers=admin_auth)
    assert r_wf.status_code == 200
    wf_cfg = r_wf.json()
    dsl_nodes = [dict(n) for n in wf_cfg["nodes"]]
    for n in dsl_nodes:
        if n["node_id"] == "node_dimension_analyst":
            n["model_name"] = "非法模型名"
    r_imp = await client.post(
        "/api/admin/agent-studio/workflow/import",
        headers=admin_auth,
        json={
            "version": "v-bad-model-1119",
            "dsl_payload": {"workflow": {"graph": {"nodes": dsl_nodes, "edges": wf_cfg["edges"]}}},
        },
    )
    assert r_imp.status_code == 400
    imp_detail = r_imp.json()["detail"]
    assert CJK_RE.search(imp_detail)
    assert "validation error" not in imp_detail.lower()
    assert "input_value=" not in imp_detail
    assert "NodeExecutionSettings" not in imp_detail

    # 2. Self-registered trial user never sees internal （体验空间-xxxxxxxx） marker in form preview or chat API
    r_reg = await client.post(
        "/api/auth/user/register",
        json={
            "email": "trial-clean-name-1119@qiheng-auto.com",
            "display_name": "体验员小周",
            "org_name": "星途智行科技有限公司",
            "password": PASSWORD,
        },
    )
    assert r_reg.status_code in (200, 201)
    trial_hdr = {"Authorization": f"Bearer {r_reg.json()['access_token']}"}
    r_fprev = await client.get("/api/forms/preview/MA_F118", headers=trial_hdr)
    assert r_fprev.status_code == 200
    assert "体验空间-" not in r_fprev.text
    r_tchat = await client.post("/api/analysis/chats", headers=trial_hdr, json={})
    assert r_tchat.status_code in (200, 201)
    assert r_tchat.json()["org_name"] == "星途智行科技有限公司"
    assert "体验空间-" not in r_tchat.text
    r_tlist = await client.get("/api/analysis/tasks", headers=trial_hdr)
    assert r_tlist.status_code == 200
    assert "体验空间-" not in r_tlist.text

    # 3. Audit _clean_text never exceeds limit (off-by-one fixed) and >512-char path still writes AuditLog
    assert len(_clean_text("a" * 600, 512)) == 512
    assert len(_clean_text("测" * 200, 128)) == 128
    long_path = "/api/admin/document-templates/" + ("x" * 560)
    await client.put(long_path, headers=admin_auth, json={})
    async with session_factory() as s:
        logged = await s.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.path.like("/api/admin/document-templates/xxxx%"))
        )
        assert logged and logged >= 1


async def test_round_1120_five_translation_gates_five_extraction_gates_and_report_wisdom_polish(
    monkeypatch,
) -> None:
    """Verify upgraded 5 Translation Gates, 5 Extraction Gates, negation-aware sensor classification,
    cross-document differentiation, contract Appendix I splitting, and admission dossier polish."""
    from types import SimpleNamespace

    from app.admission.engine import _clean_name, _tighten_zh_spaces
    from app.analysis import doc_rules, doc_sections
    from app.analysis.document_templates import render
    from app.analysis.document_templates_default import DEFAULT_TEMPLATES
    from app.knowledge import extractor, translator

    # --- 1. Upgraded 5 Translation Gates ---
    # Gate 1: Reject AI refusal meta-talk, line-number padding, [REDACTED_EMAIL], and <占位符>
    for polluted in (
        "本次请求无法执行翻译：待翻译原文仅包含占位符，不含任何可翻译的法文法律条款正文。",
        "4012903011 版面行号占位符原样保留\n4012903019 版面行号占位符原样保留",
        "请填写 AV06 表并提交至 [REDACTED_EMAIL] 备案。",
        "成功申请人应按规定粘贴 <占位符> 标识。",
    ):
        ok_p, iss_p, _ = translator.verify_translation(
            "Submit Form AV06 within 24 hours to the Authority.", polluted, []
        )
        assert not ok_p and any("严禁入库" in x for x in iss_p), (polluted, iss_p)

    # Gate 3: Hardware '微处理器' passes, while legal 'data processor -> 数据处理器' is rejected
    ok_hw, iss_hw, _ = translator.verify_translation(
        "Each microprocessor module on the data processor board must be tested.",
        "数据处理者主板上的每个微处理器模块均必须经过测试。",
        [],
    )
    assert ok_hw, iss_hw
    ok_bad_proc, iss_bad_proc, _ = translator.verify_translation(
        "The data processor shall process data only on instructions.",
        "数据处理器仅可按指令处理数据。",
        [],
    )
    assert not ok_bad_proc and any("处理器" in x for x in iss_bad_proc)

    # Gate 3b: Article bis / مكرر must be '之二', never '重复'
    ok_bis, iss_bis, _ = translator.verify_translation(
        "Article 29 bis: The operator must notify the Authority.",
        "第29条（重复）：运营者必须通知主管机关。",
        [],
    )
    assert not ok_bis and any("之二" in x for x in iss_bis)
    assert translator.verify_translation(
        "Article 29 bis: The operator must notify the Authority.",
        "第29条之二：运营者必须通知主管机关。",
        [],
    )[0]

    # --- 2. Upgraded 5 Extraction Gates (Gate 4: Specificity & Quantified Penalty Fidelity) ---
    res_vague = await extractor.evaluate_kg_candidate(
        relation_type="GOVERNS",
        source_type="INSTRUMENT",
        source_name="Guide relatif à la classification des données",
        target_type="DATA_CATEGORY",
        target_name="数据",
        evidence_quote="La suppression constitue la phase ultime du cycle de vie de certaines catégories de données.",
        clause_original_text="La suppression constitue la phase ultime du cycle de vie de certaines catégories de données.",
        document_title="Guide relatif à la classification des données",
        use_llm_judge=False,
    )
    assert not res_vague.admitted and res_vague.stage == "entity_specificity"

    res_hollow_pen = await extractor.evaluate_kg_candidate(
        relation_type="PENALIZES",
        source_type="INSTRUMENT",
        source_name="Loi 2004-63",
        target_type="PENALTY",
        target_name="违规罚则",
        evidence_quote="Est puni de trois mois d’emprisonnement et d’une amende de trois mille dinars.",
        clause_original_text="Art. 93 : Est puni de trois mois d’emprisonnement et d’une amende de trois mille dinars.",
        document_title="Loi organique n° 2004-63",
        use_llm_judge=False,
    )
    assert not res_hollow_pen.admitted and res_hollow_pen.stage == "penalty_numeric_fidelity"

    res_quant_pen = await extractor.evaluate_kg_candidate(
        relation_type="PENALIZES",
        source_type="INSTRUMENT",
        source_name="Loi 2004-63",
        target_type="PENALTY",
        target_name="3个月监禁及3000第纳尔罚金",
        evidence_quote="Est puni de trois mois d’emprisonnement et d’une amende de trois mille dinars.",
        clause_original_text="Art. 93 : Est puni de trois mois d’emprisonnement et d’une amende de trois mille dinars.",
        document_title="Loi organique n° 2004-63",
        use_llm_judge=False,
    )
    assert res_quant_pen.admitted and res_quant_pen.stage == "passed"

    # --- 3. Report Negation Guard & Cross-Document Differentiation ---
    async def _fake_prof(_s, _cc, _lbl):
        return ("《中华人民共和国个人信息保护法》", "国家网信部门")

    async def _fake_legit(_s, _cc):
        return {"state": "无", "cite": "《中华人民共和国个人信息保护法》第十三条"}

    monkeypatch.setattr(doc_sections, "_source_jurisdiction_profile", _fake_prof)
    monkeypatch.setattr(doc_rules, "legit_basis", _fake_legit)

    ota_task = SimpleNamespace(
        country_code="SG",
        scenario_type="ota_diagnostics",
        vehicle_model="启衡 QH-S9",
        facts_json={},
    )
    ota_vals = {
        "source_country": "中国",
        "recipient_country": "新加坡",
        "vehicle_model": "启衡 QH-S9",
        "purpose": "开展高压三电远程故障诊断与安全预警协同",
        "retention_and_blackbox": "境外节点存储于新加坡专属私有云集群，保存期限严格限定为180天；期满由存储引擎自动彻底删除。",
        "exterior_video_and_gnss": (
            "严禁采集或跨境传输车外摄像头视频图像、人脸车牌特征及座舱音视频；"
            "仅在碰撞紧急救援或热失控告警瞬间传输经加密的故障码及单帧脱敏定位坐标。"
        ),
        "data_types": "假名化车辆识别令牌、电芯温升参数与故障码（不含车外视频图像、座舱音视频及生物识别特征）。",
    }
    ov_blks = await doc_sections.dpia_overview_blocks(None, ota_task, ota_vals, {"text": "须申报数据出境安全评估"})
    ov_dump = str(ov_blks)
    assert "车内外光学" not in ov_dump
    sp_blks = await doc_sections.dpia_special_data_note_blocks(None, ota_task, ota_vals)
    assert "不采集车内外音视频" in sp_blks[0]["text"]
    assert "已完成不可逆人脸车牌遮蔽" not in sp_blks[0]["text"]
    lb_blks = await doc_sections.dpia_legal_basis_blocks(None, ota_task, ota_vals, {"text": "须申报数据出境安全评估"})
    assert "座舱乘员状态感知" not in str(lb_blks)

    # Cross-document differentiation across dpia / lia / risk_report / tia
    nec_dpia = str(doc_sections.necessity_alternatives_blocks(ota_task, "dpia", ota_vals))
    nec_lia = str(doc_sections.necessity_alternatives_blocks(ota_task, "lia", ota_vals))
    nec_risk = str(doc_sections.necessity_alternatives_blocks(ota_task, "risk_report", ota_vals))
    assert len({nec_dpia, nec_lia, nec_risk}) == 3

    sg_dpia = str(doc_sections.safeguard_adequacy_blocks(ota_task, "dpia", ota_vals, {}))
    sg_tia = str(doc_sections.safeguard_adequacy_blocks(ota_task, "tia", ota_vals, {}))
    sg_risk = str(doc_sections.safeguard_adequacy_blocks(ota_task, "risk_report", ota_vals, {}))
    assert len({sg_dpia, sg_tia, sg_risk}) == 3

    # Contract signature entity extraction & Appendix I (八)/(九) split
    contract_def = next(d for c, _, d in DEFAULT_TEMPLATES if c == "contract")
    c_vals = {f["key"]: f"值-{f['key']}" for f in contract_def["facts"]}
    c_vals.update(
        source_country="中国",
        recipient_country="新加坡",
        company_name="启衡智行汽车科技（上海）有限公司",
        recipient="Qiheng Mobility Asia-Pacific Pte. Ltd.；新加坡纬壹科技城1号；远程诊断支持",
        retention_and_blackbox="境外节点存储于新加坡专属私有云集群，保存期限严格限定为180天；期满自动安全删除。",
    )
    c_rendered = render(contract_def, c_vals, [])
    c_text = "\n".join(b.get("text", "") for b in c_rendered.blocks)
    assert "境外接收方（盖章）：Qiheng Mobility Asia-Pacific Pte. Ltd.　　授权代表" in c_text
    line_8 = next(b["text"] for b in c_rendered.blocks if str(b.get("text", "")).startswith("（八）出境后保存期限："))
    line_9 = next(b["text"] for b in c_rendered.blocks if str(b.get("text", "")).startswith("（九）出境后保存地点："))
    assert line_8 != line_9 and "180天" in line_8 and "新加坡专属私有云集群" in line_9

    # Admission material name deduplication & space tightening & non-maker filter
    assert _clean_name("技术构造文件（TCF）（TCF）") == "技术构造文件（TCF）"
    assert _clean_name("符合性声明（DoC）（Declaration of Conformity (DoC)）") == "符合性声明（DoC）"
    assert _tighten_zh_spaces("向新加坡资讯通信媒体发展局（IMDA） 注册") == "向新加坡资讯通信媒体发展局（IMDA）注册"
    assert doc_rules._NOT_FOR_MAKERS.search("违反驾驶学校或出租车计价器核准规定")

    # Page-prefixed Arabic Official Gazette running header stripped symmetrically on source side
    from app.ingestion.noise import clean_text

    eg_src = "المادة (84)\nيضع مجلس الإدارة قواعد للرقابة على البنوك.\n٤٤الجريدة الرسمية  العدد ٣٧ مكرر  فى ١٥ سبتمبر سنة ٢٢\nوتنشر القواعد وفقا للقانون."
    assert "٤٤الجريدة الرسمية" not in clean_text(eg_src)
    assert "المادة (84)" in clean_text(eg_src)

    # Sub-chunk flex CJK & preserve_original ASCII term case-insensitivity (standalone short cells stay strict)
    assert not translator.verify_translation("Amount\n1. 5,000 $100", "金额\n1. 5,000 $100", [])[0]
    ok_sub, iss_sub, _ = translator.verify_translation(
        "Données sensibles C0 C1 C2 C3 C4 I0 I1 I2 I3 I4",
        "敏感数据 C0 C1 C2 C3 C4 I0 I1 I2 I3 I4",
        [],
        allow_flex_cjk=True,
    )
    assert ok_sub and not iss_sub
    ok_imp, iss_imp, _ = translator.verify_translation(
        "L'évaluation mesure les impacts potentiels sur la sécurité des systèmes.",
        "评估用于衡量对系统安全可能产生的影响（impact）。",
        [{"source_term": "impacts", "canonical_source": "Impact", "target_zh": "Impact", "preserve_original": True}],
    )
    assert ok_imp and not iss_imp


async def test_round8_blind_audit_convergence_guards(session_factory):
    import zipfile
    from types import SimpleNamespace

    from app.admission.engine import _clean_name
    from app.admission.foreign_letter import verify_foreign_blocks
    from app.analysis import doc_sections, official_docx, official_eu_edpb_dpia
    from app.analysis.router import _humanize_schema_dict
    from app.knowledge import extractor, graph_edit, translator
    from app.knowledge.graph_evidence import active_edge_filters
    from app.knowledge.models import KgEdge, KgNode, NodeType, RelationType
    from app.updates.router import _subscription_item

    # 1. Deformed CYSH marker & meta-chatter guard in verify_translation
    ok_cysh, iss_cysh, _ = translator.verify_translation(
        "The controller shall keep records for 5 years.",
        "控制者应保存记录5年（_CYSH_PRIVATE_A_）。",
        [],
    )
    assert not ok_cysh and any("脱敏占位符" in x for x in iss_cysh)
    ok_meta, iss_meta, _ = translator.verify_translation(
        "The controller shall keep records.",
        "抱歉，我无法对上述残缺文本进行翻译，请提供完整的待翻译法条原文。",
        [],
    )
    assert not ok_meta and any("模型拒答说明" in x for x in iss_meta)

    # 2. >8-char hollow penalty & English word-numeral penalty fidelity in Gate 4
    ok_pen, code_pen, _ = extractor.validate_entity_specificity_and_penalty_fidelity(
        relation_type=RelationType.PENALIZES,
        source_type=NodeType.INSTRUMENT,
        source_name="个人数据保护法",
        target_type=NodeType.PENALTY,
        target_name="违反跨境传输规定的行政罚款",
        evidence_quote="shall be liable on conviction to imprisonment for a term not exceeding two years",
    )
    assert not ok_pen and code_pen == "penalty_numeric_fidelity"

    # 3. Object-fronted negation stripping & '未脱敏' inversion prevention in doc_sections
    stripped = doc_sections._strip_negated_clauses("仅同步三电故障码；车外视频不采集，座舱摄像头与麦克风均不开启，无GNSS轨迹")
    assert "车外视频" not in stripped and "摄像头" not in stripped and "GNSS" not in stripped
    raw_unmasked = "原始车外视频与精确GNSS轨迹未脱敏、未经去标识化直接回传境外"
    assert doc_sections._UNMASKED_RE.search(raw_unmasked)

    # 4. Comma/label/paren recipient entity name cleaning in Standard Contract
    parsed_rec = official_docx._parse_recipient_info(
        "名称：AutoCloud GmbH，地址：德国慕尼黑工业街8号，主营业务：车联网云端诊断",
        "",
    )
    assert parsed_rec["name"] == "AutoCloud GmbH"
    assert "德国慕尼黑" in parsed_rec["address"]
    assert official_docx._clean_recipient_entity_name("AutoCloud GmbH（地址：德国慕尼黑，主营业务：云端诊断）") == "AutoCloud GmbH"

    # 5. EDPB 2026 EN DPIA Table 33 & Table 17 mutual-exclusivity (no substring checkbox collision)
    import xml.etree.ElementTree as ET

    edpb_docx = official_eu_edpb_dpia.render_edpb_2026_docx(
        {"decision": {"outcome": "NOT APPROVED", "justification": "High residual risk remains unmitigated."}}
    )
    with zipfile.ZipFile(__import__("io").BytesIO(edpb_docx)) as zf:
        root_edpb = ET.fromstring(zf.read("word/document.xml"))
    t17 = root_edpb.findall(".//" + official_docx.W + "tbl")[17]
    t17_c3 = t17.findall(official_docx.W + "tr")[1].findall(official_docx.W + "tc")[3]
    p_part = next(p for p in t17_c3.findall(official_docx.W + "p") if "Partially implemented" in "".join(p.itertext()))
    p_impl = next(p for p in t17_c3.findall(official_docx.W + "p") if "".join(p.itertext()).strip() == "Implemented")
    assert not any(sym.get(official_docx.W + "char") == official_docx.CHECKED for sym in p_part.iter(official_docx.W + "sym"))
    assert any(sym.get(official_docx.W + "char") == official_docx.CHECKED for sym in p_impl.iter(official_docx.W + "sym"))

    t33 = root_edpb.findall(".//" + official_docx.W + "tbl")[33]
    rej_p = next(p for p in t33.findall(".//" + official_docx.W + "p") if "REJECTED:" in "".join(p.itertext()))
    app_p = next(p for p in t33.findall(".//" + official_docx.W + "p") if "APPROVED: The processing may proceed [immediately]" in "".join(p.itertext()))
    assert any(sym.get(official_docx.W + "char") == official_docx.CHECKED for sym in rej_p.iter(official_docx.W + "sym"))
    assert not any(sym.get(official_docx.W + "char") == official_docx.CHECKED for sym in app_p.iter(official_docx.W + "sym"))

    edpb_app = official_eu_edpb_dpia.render_edpb_2026_docx({"decision": {"outcome": "APPROVED"}})
    with zipfile.ZipFile(__import__("io").BytesIO(edpb_app)) as zf2:
        t33_app = ET.fromstring(zf2.read("word/document.xml")).findall(".//" + official_docx.W + "tbl")[33]
    cond_p2 = next(p for p in t33_app.findall(".//" + official_docx.W + "p") if "CONDITIONALLY APPROVED:" in "".join(p.itertext()))
    app_p2 = next(p for p in t33_app.findall(".//" + official_docx.W + "p") if "APPROVED: The processing may proceed [immediately]" in "".join(p.itertext()))
    assert any(sym.get(official_docx.W + "char") == official_docx.CHECKED for sym in app_p2.iter(official_docx.W + "sym"))
    assert not any(sym.get(official_docx.W + "char") == official_docx.CHECKED for sym in cond_p2.iter(official_docx.W + "sym"))

    # 5b. CJK-adjacent jargon scrub & prohibited-unmasked / unmasked vision branches
    from app.analysis.router import _humanize_workflow_text

    scrubbed_wf = _humanize_workflow_text("不能推断OCR损坏处，返回JSON格式结果")
    assert "OCR" not in scrubbed_wf and "JSON" not in scrubbed_wf
    assert "路人" not in doc_sections._strip_negated_clauses("仅限签约车主本人，无路人及未成年人")
    prohibited_probe = doc_sections._PROHIBITED_UNMASKED_RE.sub(
        " ", "车端实时遮蔽人脸车牌与降精坐标；未经脱敏的原始视频严禁上传出境"
    )
    assert not doc_sections._UNMASKED_RE.search(prohibited_probe)

    # 5c. Round 10 guards: 4-part recipient parsing, cn_route upper-bound caps, CERT authority dedup, ingestion jargon scrub
    from app.admission.names import normalize_body
    from app.analysis import cn_route, pdf_renderer, risk_items
    from app.ingestion.router import clean_admin_process_text

    r4 = official_docx._parse_recipient_info("某汽车技术（德国）有限公司，德国，慕尼黑某路 1 号，汽车软件研发", "")
    assert r4["name"] == "某汽车技术（德国）有限公司" and r4["address"] == "慕尼黑某路 1 号" and r4["biz"] == "汽车软件研发"
    assert official_docx._clean_recipient_entity_name("Qiheng GmbH, Germany, Munich Campus, Automotive R&D") == "Qiheng GmbH"
    assert cn_route.persons("当年累计不满 100 万人（预计约 15 万人）") == 150_000
    assert cn_route.persons("是，不满 1 万人（约 800 人）") == 800
    assert cn_route.persons("不满 100 万人") == 999_999
    assert "源端降敏减害" not in risk_items._measure_adequacy_note("原始图像外流", "原始图像未脱敏直接回传研发云端")
    assert normalize_body("突尼斯电信研究中心单一窗口（CERT）", "TN") == "突尼斯电信研究中心单一窗口（CERT）"
    assert pdf_renderer._risk_zh("unknown") == "待核实" and pdf_renderer._risk_zh("none") == "无显著风险"
    cleaned_proc = clean_admin_process_text("已执行OCR31页；paddleocr 子进程读取 result.json 触发 pydantic 异常")
    assert all(tok not in cleaned_proc.lower() for tok in ("ocr", "json", "pydantic"))

    # 6. Fullwidth CJK punctuation blocked in verify_foreign_blocks & non-adjacent abbr dedup
    iss_fg = verify_foreign_blocks(
        [{"type": "paragraph", "text": "申请主体：启衡智行"}],
        [{"type": "paragraph", "text": "Application Entity（Shanghai）Ltd."}],
        "en",
    )
    assert iss_fg and any("中文括号" in x for x in iss_fg)
    assert _clean_name("符合性声明（DoC）（原件）（Declaration of Conformity (DoC)）") == "符合性声明（DoC）（原件）"

    # 7. Recursive _humanize_schema_dict & malformed notification dedupe_key/link resilience
    cleaned_schema = _humanize_schema_dict({"nested": {"items": ["list[uuid]", "LLM JSON OCR"]}})
    assert "UUID" not in str(cleaned_schema).upper() and "LLM" not in str(cleaned_schema)

    async with session_factory() as s:
        bad_notice = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000001",
            change_id=None,
            dedupe_key="malformed_no_colon",
            link="/laws?doc=not-a-uuid",
            created_at=None,
            read_at=None,
            title="通知",
            body="内容",
        )
        sub_item = await _subscription_item(s, SimpleNamespace(org_id=None, user_id=None), bad_notice)
        assert isinstance(sub_item, dict) and sub_item["related"] == []

        # 8. Multiline clause evidence_quote in graph_edit.create_edge preserves exact substring for active_edge_filters
        from app.ingestion.models import Clause, Document, DocumentVersion
        from app.sources.models import SourceSite

        site = SourceSite(
            country_code="SG", publisher="PDPC", language="en", document_type="LAW",
            entry_url="https://pdpc.example/multiline", strategy="DIRECT", terms_status="pending", enabled=False,
        )
        s.add(site)
        await s.flush()
        doc = Document(
            site_id=site.id, country_code="SG", title="PDPA Multiline", document_type="LAW",
            language="en", source_url="https://pdpc.example/multiline",
        )
        s.add(doc)
        await s.flush()
        ver = DocumentVersion(
            document_id=doc.id, version_no=1, content_hash="f" * 64, archive_path="m",
            mime_type="application/pdf", size_bytes=10, final_url="https://pdpc.example/multiline", state="parsed",
        )
        s.add(ver)
        await s.flush()
        cl = Clause(
            version_id=ver.id, ordinal=1, clause_number="26D", language="en",
            original_text="An organisation must notify the Commission\nas soon as practicable, no later than 3 calendar days.",
            text_hash="a" * 64,
        )
        s.add(cl)
        await s.flush()
        n_law = KgNode(country_code="SG", node_type="INSTRUMENT", node_key="k1", name_zh="个人数据保护法", evidence_clause_id=cl.id, review_status="approved")
        n_ob = KgNode(country_code="SG", node_type="OBLIGATION", node_key="k2", name_zh="3个日历日内泄露通报义务", evidence_clause_id=cl.id, review_status="approved")
        s.add_all([n_law, n_ob])
        await s.flush()
        created_e = await graph_edit.create_edge(
            s,
            source_node_id=n_law.id,
            target_node_id=n_ob.id,
            relation_type="REQUIRES",
            evidence_clause_id=cl.id,
            evidence_quote="notify the Commission as soon as practicable, no later than 3 calendar days.",
        )
        await s.flush()
        from sqlalchemy import select as sa_select

        found_id = await s.scalar(
            sa_select(KgEdge.id)
            .join(Clause, Clause.id == KgEdge.evidence_clause_id)
            .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(KgEdge.id == created_e.id, *active_edge_filters())
        )
        assert found_id == created_e.id


async def test_round11_blind_audit_convergence_guards(client, seed, session_factory):
    """回归门禁：第 11 轮独立红队盲审 7 项边界修复逐一断言。"""
    from types import SimpleNamespace

    from app.analysis import cn_route, doc_applicability, events, official_docx
    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge import whitepaper_graph
    from app.knowledge.history import propose
    from app.knowledge.models import KgEdge, KgNode
    from app.sources.models import SourceSite

    from .conftest import bearer, login

    # 1. cn_route.persons: 日期/期限/车辆条数剔除 + 上限与分句取最大 + 括号精化
    assert cn_route.persons("自当年1月1日起累计不满50万人") == 499_999
    assert cn_route.persons("今年已传输0人，未来3年预计不满50万人") == 499_999
    assert cn_route.persons("今年已传2万人，未来3年累计不满150万人") == 1_499_999
    assert cn_route.persons("涉及测试车50辆，采集遥测日志20万条，涉及驾驶员200人") == 200
    assert (
        cn_route.judge(
            {
                "is_cii": "未收到主管部门认定通知",
                "important_data": "属于一般车辆工况遥测参数，不属于重要数据",
                "sensitive_pi": "涉及公开道路环境参数，但不涉及敏感个人信息",
                "data_volume": "今年已传输0人，未来3年预计不满50万人",
            }
        )["route"]
        == "standard_contract"
    )

    # 2. official_docx._yes_no & cn_route._yes: 否定/倒装否定/转折否定 vs 明确“是，…”
    for neg_s in (
        "未收到主管部门认定通知",
        "未被认定为关键信息基础设施运营者",
        "非关键信息基础设施运营者",
        "重要数据不涉及",
        "仅含一般车辆运行参数，不涉及重要数据",
        "属于一般车辆工况遥测参数，不属于重要数据",
        "车端已完成匿名化处理，不含敏感个人信息",
        "涉及公开道路环境参数，但不涉及敏感个人信息",
    ):
        assert cn_route._yes(neg_s) is False
        assert official_docx._yes_no(neg_s) == ["否"]
    assert cn_route._yes("是，含驾驶员定位轨迹，不涉及人脸生物识别") is True
    assert official_docx._yes_no("是，含驾驶员定位轨迹，不涉及人脸生物识别") == ["是"]

    # 3. official_docx 境外接收方英文逗号后缀（, Inc. / , Co., Ltd.）与法定国名全称解析
    r_inc = official_docx._parse_recipient_info(
        "Acme Mobility, Inc.，德国，慕尼黑某路1号，汽车软件研发", ""
    )
    assert (
        r_inc["name"] == "Acme Mobility, Inc."
        and r_inc["address"] == "慕尼黑某路1号"
        and r_inc["biz"] == "汽车软件研发"
    )
    r_coltd = official_docx._parse_recipient_info(
        "Continental Telematics, Co., Ltd., Republic of Singapore, 80 Bendemeer Rd, Connected Car Cloud",
        "",
    )
    assert (
        r_coltd["name"] == "Continental Telematics, Co., Ltd."
        and r_coltd["address"] == "80 Bendemeer Rd"
        and r_coltd["biz"] == "Connected Car Cloud"
    )
    r_de_full = official_docx._parse_recipient_info(
        "启衡欧洲研发有限公司，德意志联邦共和国，慕尼黑某路1号，汽车软件研发", ""
    )
    assert (
        r_de_full["name"] == "启衡欧洲研发有限公司"
        and r_de_full["address"] == "慕尼黑某路1号"
        and r_de_full["biz"] == "汽车软件研发"
    )
    assert not official_docx._is_country_seg("新加坡纬壹科技城1号")

    # 4. doc_applicability.judge LIA 多场景分句与“正当利益”否定
    lia_labels = {"lawful_basis": "处理的合法性基础"}
    r_lia_na = doc_applicability.judge(
        source="新加坡",
        recipient="德国",
        source_in_library=True,
        values={"data_volume": "5000 人", "lawful_basis": "取得个人同意；不以正当利益为依据"},
        labels=lia_labels,
        grounds={"organized": True},
        cn=None,
        overseas="德国",
        overseas_known=True,
        admission=[],
        function_zh="车联网上云",
    )
    assert r_lia_na["lia"]["state"] == doc_applicability.NA
    r_lia_need = doc_applicability.judge(
        source="新加坡",
        recipient="德国",
        source_in_library=True,
        values={
            "data_volume": "5000 人",
            "lawful_basis": "车载娱乐以取得同意为依据（不以合法利益为依据）；车云入侵检测以合法利益为依据",
        },
        labels=lia_labels,
        grounds={"organized": True},
        cn=None,
        overseas="德国",
        overseas_known=True,
        admission=[],
        function_zh="车联网上云",
    )
    assert r_lia_need["lia"]["state"] == doc_applicability.NEED

    # 5. whitepaper_graph / source_grounds 安全隔离与范围外排除 + 节点/关系人工审批门禁 + events 脱敏
    async with session_factory() as s:
        site = SourceSite(
            country_code="SG",
            publisher="PDPC",
            language="en",
            document_type="LAW",
            entry_url="https://pdpc.example/r11",
            strategy="DIRECT",
            terms_status="pending",
            enabled=False,
        )
        s.add(site)
        await s.flush()
        doc = Document(
            site_id=site.id,
            country_code="SG",
            title="Personal Data Protection Act R11",
            title_zh="个人数据保护法测试版",
            document_type="LAW",
            language="en",
            source_url="https://pdpc.example/r11",
            business_weight="core",
            business_topic="data_protection",
        )
        s.add(doc)
        await s.flush()
        ver = DocumentVersion(
            document_id=doc.id,
            version_no=1,
            content_hash="9" * 64,
            archive_path="r11",
            mime_type="application/pdf",
            size_bytes=10,
            final_url="https://pdpc.example/r11",
            state="parsed",
            legal_status="in_force",
        )
        s.add(ver)
        await s.flush()
        quote_tx = "An organisation shall not transfer personal data outside Singapore without prior approval or comparable protection, or face a fine up to SGD 1,000,000."
        cl = Clause(
            version_id=ver.id,
            ordinal=1,
            clause_number="26",
            language="en",
            original_text=quote_tx,
            text_hash="8" * 64,
        )
        s.add(cl)
        await s.flush()
        n_inst = KgNode(
            country_code="SG",
            node_type="INSTRUMENT",
            node_key="r11:inst",
            name_zh="个人数据保护法测试版",
            document_id=doc.id,
            evidence_clause_id=cl.id,
            review_status="approved",
        )
        n_ob = KgNode(
            country_code="SG",
            node_type="OBLIGATION",
            node_key="r11:ob",
            name_zh="个人数据跨境传输须获批准或具备同等保护水平",
            document_id=doc.id,
            evidence_clause_id=cl.id,
            review_status="approved",
            verify_state="auto",
            attributes={"node_class": "concrete", "claim_tags": {"aspect": "transfer"}},
        )
        n_vague = KgNode(
            country_code="SG",
            node_type="DATA_CATEGORY",
            node_key="r11:vague",
            name_zh="数据",
            document_id=doc.id,
            evidence_clause_id=cl.id,
            review_status="pending_review",
        )
        n_hollow_pen = KgNode(
            country_code="SG",
            node_type="PENALTY",
            node_key="r11:pen",
            name_zh="罚款处罚",
            document_id=doc.id,
            evidence_clause_id=cl.id,
            review_status="pending_review",
        )
        n_dead = KgNode(
            country_code="SG",
            node_type="OBLIGATION",
            node_key="r11:dead",
            name_zh="已停用义务节点",
            document_id=doc.id,
            evidence_clause_id=cl.id,
            review_status="superseded",
        )
        s.add_all([n_inst, n_ob, n_vague, n_hollow_pen, n_dead])
        await s.flush()
        e_dead = KgEdge(
            country_code="SG",
            source_node_id=n_inst.id,
            target_node_id=n_dead.id,
            relation_type="REQUIRES",
            evidence_clause_id=cl.id,
            evidence_quote="An organisation shall not transfer personal data outside Singapore",
            extractor_model="deepseek-chat",
            confidence=0.95,
            judge_verdict="yes",
            is_structural=False,
            review_status="pending_review",
        )
        s.add(e_dead)
        await s.commit()
        doc_id = doc.id
        ob_id = n_ob.id
        vague_id, pen_id, edead_id = str(n_vague.id), str(n_hollow_pen.id), str(e_dead.id)

    admin = bearer(await login(client, "admin", "admin@example.com"))
    assert (
        await client.post(
            f"/api/admin/knowledge/nodes/{vague_id}/review",
            headers=admin,
            json={"decision": "approved"},
        )
    ).status_code == 409
    assert (
        await client.post(
            f"/api/admin/knowledge/nodes/{pen_id}/review",
            headers=admin,
            json={"decision": "approved"},
        )
    ).status_code == 409
    assert (
        await client.post(
            f"/api/admin/knowledge/edges/{edead_id}/review",
            headers=admin,
            json={"decision": "approved"},
        )
    ).status_code == 409

    async with session_factory() as s:
        g1 = await doc_applicability.source_grounds(s, "SG")
        assert g1["approval"] is not None
        doc_row = await s.get(Document, doc_id)
        doc_row.relevance = "out_of_scope"
        await s.commit()
        pools_oos, _, _ = await whitepaper_graph.requirement_pools(s, "SG")
        assert all(n.id != ob_id for lst in pools_oos.values() for n in lst)
        g2 = await doc_applicability.source_grounds(s, "SG")
        assert g2["approval"] is None or "测试版" not in g2["approval"].cite

        doc_row.relevance = "relevant"
        await propose(s, doc_row, {"safety_hold": True, "reason": "r11 quarantine test"})
        await s.commit()
        pools_held, _, _ = await whitepaper_graph.requirement_pools(s, "SG")
        assert all(n.id != ob_id for lst in pools_held.values() for n in lst)

    assert (
        await client.post(
            f"/api/admin/knowledge/nodes/{ob_id}/review",
            headers=admin,
            json={"decision": "approved"},
        )
    ).status_code == 409

    # 6. trace_events maps llm.chat -> model.analyze
    recorded_events: list[tuple[str, dict]] = []

    async def _fake_append(_s, _t, ev_type, payload=None):
        recorded_events.append((ev_type, payload or {}))

    orig_append = events.append_event
    events.append_event = _fake_append
    try:
        await events.trace_events(
            None,
            SimpleNamespace(id="t1", org_id="o1", workflow_version_id="w1", total_tokens=10),
            SimpleNamespace(
                id="tr1",
                phase="analysis",
                agent_node_id="n1",
                status="passed",
                tool_calls_json=[{"tool_name": "llm.chat", "duration_ms": 12}],
                tokens_used=10,
            ),
        )
    finally:
        events.append_event = orig_append
    tool_evs = [p for et, p in recorded_events if et == "tool_called"]
    assert tool_evs and tool_evs[0]["tool_name"] == "model.analyze"


async def test_round12_blind_audit_convergence_guards(client, seed, session_factory):
    """回归门禁：第 12 轮独立红队盲审 10 项跨模块边界修复逐一断言。"""
    import io
    import uuid
    import zipfile

    from app.analysis import cn_route, doc_rules, official_docx, pdf_renderer, report_exporter
    from app.analysis.router import _humanize_any_schema, _humanize_workflow_text
    from app.audit import describe as audit_describe
    from app.core.redaction import EgressContext, egress_context
    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge import graph_edit, reading, whitepaper
    from app.knowledge.history import propose
    from app.knowledge.models import ClauseTranslation, KgNode
    from app.sources.models import SourceSite
    from app.updates import subscriptions as subs

    from .conftest import bearer, login
    from .test_s7_exports_events import fixture_report

    # 1. cn_route._yes & official_docx._yes_no: 肯定主句附带局部/跨项排除 & 先抑后扬转折肯定句
    assert (
        cn_route._yes("含精确位置轨迹约 2 万人，不含生物识别敏感个人信息", "sensitive_pi") is True
    )
    assert cn_route._yes("涉及行车轨迹敏感个人信息约 2 万人，不涉及重要数据", "sensitive_pi") is True
    assert cn_route._yes("含测绘地理信息重要数据，不含敏感个人信息", "important_data") is True
    assert cn_route._yes("此前未被认定，但今年已被认定为关键信息基础设施运营者", "is_cii") is True
    assert (
        cn_route._yes(
            "不含人脸与声纹，但涉及约 2 万名车主的精确位置轨迹敏感个人信息", "sensitive_pi"
        )
        is True
    )
    assert (
        cn_route.judge(
            {
                "is_cii": "此前未被认定，但今年已被认定为关键信息基础设施运营者",
                "important_data": "否",
                "sensitive_pi": "含精确位置轨迹约 2 万人，不含生物识别敏感个人信息",
                "data_volume": "5 万人",
            }
        )["route"]
        == "security_assessment"
    )
    assert official_docx._yes_no(
        "含精确位置轨迹约 2 万人，不含生物识别敏感个人信息", "sensitive_pi"
    ) == ["是"]

    # 2. official_docx._parse_recipient_info: “境外接收方：…”与“名称：…，德国，地址，业务”
    ra = official_docx._parse_recipient_info(
        "境外接收方：Munich Auto Tech GmbH；德国；Parkring 4, Munich；智能网联云研发", ""
    )
    assert (
        ra["name"] == "Munich Auto Tech GmbH"
        and ra["address"] == "Parkring 4, Munich"
        and ra["biz"] == "智能网联云研发"
    )
    rb = official_docx._parse_recipient_info(
        "名称：慕尼黑车联网研发中心 GmbH，德国，慕尼黑工业园 1 号，智能网联云研发", ""
    )
    assert (
        rb["name"] == "慕尼黑车联网研发中心 GmbH"
        and rb["address"] == "慕尼黑工业园 1 号"
        and rb["biz"] == "智能网联云研发"
    )

    # 3. reading.reading_units: 短注记前导行不会吞掉后续小数点切分条文
    from types import SimpleNamespace as _NS

    r_rows = [
        _NS(id=uuid.uuid4(), ordinal=0, clause_number="1", heading="Scope", page_start=1, original_text="Article 1 Scope of the Act."),
        _NS(id=uuid.uuid4(), ordinal=1, clause_number=None, heading=None, page_start=2, original_text="Short editorial note."),
        _NS(id=uuid.uuid4(), ordinal=2, clause_number="4.1", heading=None, page_start=2, original_text="4.1 Sub-clause text one."),
        _NS(id=uuid.uuid4(), ordinal=3, clause_number="4.2", heading=None, page_start=2, original_text="4.2 Sub-clause text two."),
    ]
    u_list = reading.reading_units(r_rows)
    all_u_ids = {rid for u in u_list for rid in u.ids}
    assert {r.id for r in r_rows} == all_u_ids

    # 4. whitepaper.one_name_per_body: original=True 与 verbatim_quote=True 条文原句不展开中文机构名
    async with session_factory() as s:
        kept_blocks = await whitepaper.one_name_per_body(
            s,
            "MA",
            [
                {
                    "type": "paragraph",
                    "text": "原文：Article 45: Prior authorisation of the CNDP is required.",
                    "original": True,
                },
                {
                    "type": "paragraph",
                    "text": "《个人数据保护法》第45条：须经 CNDP 事先授权。",
                    "verbatim_quote": True,
                },
            ],
        )
    assert kept_blocks[0]["text"] == "原文：Article 45: Prior authorisation of the CNDP is required."
    assert "CNDP 事先授权" in kept_blocks[1]["text"]

    # 5. report_exporter & pdf_renderer: COMPLETE / PARTIAL_GAP / PENDING_VERIFICATION / 风险:HIGH 全中文化
    t_rep, rv_rep = await fixture_report(session_factory, seed)
    rv_rep.dimensions_json[0]["status"] = "PARTIAL_GAP"
    rv_rep.dimensions_json[0]["risk_level"] = "PENDING_VERIFICATION"
    rv_rep.dimensions_json[0]["content_markdown"] = "- [C1] 履行告知义务(风险:HIGH；待核对)"
    docx_bytes = report_exporter.build_report_docx(t_rep, rv_rep)
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
        rep_xml = zf.read("word/document.xml").decode("utf-8")
    assert "PARTIAL_GAP" not in rep_xml and "PENDING_VERIFICATION" not in rep_xml
    assert "风险:HIGH" not in rep_xml and "风险：高风险" in rep_xml
    html_str = pdf_renderer.report_html(
        t_rep,
        rv_rep,
        dimension=None,
        context="测试",
        country="新加坡",
        notices=[],
    )
    assert "PENDING_VERIFICATION" not in html_str and "风险:HIGH" not in html_str

    # 6. 复合词与数字紧邻工程黑话清洗 + 英语正常词汇（storage/fragment/democrat/hallmark）零误伤
    s_wf = _humanize_workflow_text(
        "调用 PaddleOCR 与 GraphRAG、vLLM 输出 JSONL 和 UUIDv4，经 PydanticV2 校验"
    )
    for bad_tok in ("OCR", "RAG", "LLM", "JSON", "UUID", "Pydantic"):
        assert bad_tok.lower() not in s_wf.lower()
    s_en = _humanize_workflow_text("keep storage fragment democrat hallmark")
    assert s_en == "keep storage fragment democrat hallmark"
    s_schema = _humanize_any_schema({"ocr_required": "2OCR v2JSON"})
    assert "ocr" not in str(s_schema).lower() and "json" not in str(s_schema).lower()
    s_aud = audit_describe.humanize("将识别方式由“ocr”改为“llm”，使用 PaddleOCR 与 JSONL")
    assert "ocr" not in s_aud.lower() and "llm" not in s_aud.lower() and "json" not in s_aud.lower()

    # 7. GET /api/knowledge/clauses 不暴露 engine="llm"、排除范围外文档，且管理端版本对比可查看隔离文档
    async with session_factory() as s:
        site = SourceSite(
            country_code="SG",
            publisher="PDPC",
            language="en",
            document_type="LAW",
            entry_url="https://pdpc.example/r12",
            strategy="DIRECT",
            terms_status="pending",
            enabled=False,
        )
        s.add(site)
        await s.flush()
        doc_in = Document(
            site_id=site.id,
            country_code="SG",
            title="Vehicle Data Regulation R12",
            title_zh="汽车数据管理条例测试版",
            document_type="LAW",
            language="en",
            source_url="https://pdpc.example/r12-in",
            relevance="relevant",
        )
        doc_oos = Document(
            site_id=site.id,
            country_code="SG",
            title="Traffic Parking Law R12",
            title_zh="停车管理法测试版",
            document_type="LAW",
            language="en",
            source_url="https://pdpc.example/r12-oos",
            relevance="out_of_scope",
        )
        s.add_all([doc_in, doc_oos])
        await s.flush()
        v_in = DocumentVersion(
            document_id=doc_in.id,
            version_no=1,
            content_hash="1" * 64,
            archive_path="r12a",
            mime_type="application/pdf",
            size_bytes=10,
            final_url="https://pdpc.example/r12-in",
            state="parsed",
            legal_status="in_force",
        )
        v_oos = DocumentVersion(
            document_id=doc_oos.id,
            version_no=1,
            content_hash="2" * 64,
            archive_path="r12b",
            mime_type="application/pdf",
            size_bytes=10,
            final_url="https://pdpc.example/r12-oos",
            state="parsed",
            legal_status="in_force",
        )
        s.add_all([v_in, v_oos])
        await s.flush()
        c_in = Clause(
            version_id=v_in.id,
            ordinal=1,
            clause_number="5",
            language="en",
            original_text="Vehicle data transfer abroad requires safeguards.",
            text_hash="3" * 64,
        )
        c_oos = Clause(
            version_id=v_oos.id,
            ordinal=1,
            clause_number="1",
            language="en",
            original_text="Parking fee schedule.",
            text_hash="4" * 64,
        )
        s.add_all([c_in, c_oos])
        await s.flush()
        s.add_all(
            [
                ClauseTranslation(
                    clause_id=c_in.id,
                    translated_text="汽车数据向境外传输须采取安全保障措施。",
                    engine="llm",
                    model_name="deepseek-chat",
                    prompt_version="translate-v12",
                    quality_check_passed=True,
                    review_status="approved",
                    is_current=True,
                ),
                ClauseTranslation(
                    clause_id=c_oos.id,
                    translated_text="停车收费标准。",
                    engine="llm",
                    model_name="deepseek-chat",
                    prompt_version="translate-v12",
                    quality_check_passed=True,
                    review_status="approved",
                    is_current=True,
                ),
            ]
        )
        n_auto = KgNode(
            country_code="SG",
            node_type="OBLIGATION",
            node_key="r12:auto",
            name_zh="汽车数据跨境传输安全保障义务",
            document_id=doc_in.id,
            evidence_clause_id=c_in.id,
            review_status="pending_review",
            verify_state="auto",
        )
        s.add(n_auto)
        await s.commit()
        doc_in_id, doc_oos_id, n_auto_id = doc_in.id, str(doc_oos.id), n_auto.id

    user_hdr = bearer(await login(client, "user", "a-member@example.com"))
    admin_hdr = bearer(await login(client, "admin", "admin@example.com"))
    r_cls = await client.get("/api/knowledge/clauses?country_code=SG", headers=user_hdr)
    assert r_cls.status_code == 200
    assert "llm" not in r_cls.text.lower()
    cls_doc_ids = {row["document_id"] for row in r_cls.json()}
    assert str(doc_in_id) in cls_doc_ids and doc_oos_id not in cls_doc_ids

    # 用户端版本列表对范围外返回 404，管理端版本列表正常返回 200
    assert (
        await client.get(f"/api/knowledge/documents/{doc_oos_id}/versions", headers=user_hdr)
    ).status_code == 404
    assert (
        await client.get(
            f"/api/admin/knowledge/documents/{doc_oos_id}/versions", headers=admin_hdr
        )
    ).status_code == 200

    # 8. doc_rules.core_clauses 在 safety_hold 隔离后立即停止返回；rename_node 清空 verify_state
    async with session_factory() as s:
        assert len(await doc_rules.core_clauses(s, "SG", "source")) >= 1
        renamed = await graph_edit.rename_node(
            s, n_auto_id, name_zh="更新后的汽车数据跨境传输安全义务"
        )
        assert renamed.verify_state is None and renamed.review_status == "pending_review"
        d_in_row = await s.get(Document, doc_in_id)
        await propose(s, d_in_row, {"safety_hold": True, "reason": "r12 hold"})
        await s.commit()
        assert await doc_rules.core_clauses(s, "SG", "source") == []

    # 9. subscriptions.write_pending 开启独立 llm_scope，不继承调用方租户 egress_context
    tok = egress_context.set(EgressContext(org_id="tenant-leak-probe", aliases=("SECRET_CO",)))
    try:
        await subs.write_pending(session_factory)
        assert egress_context.get().org_id == "tenant-leak-probe"
    finally:
        egress_context.reset(tok)


@pytest.mark.asyncio
async def test_round13_blind_audit_convergence_guards(client, seed, session_factory):
    import uuid
    from types import SimpleNamespace

    from app.analysis import cn_route, doc_applicability, doc_rules, official_docx
    from app.analysis.document_templates import sensitive_kinds
    from app.analysis.official_eu_edpb_dpia import _sanitise_edpb_tree, render_edpb_2026_docx
    from app.core.jargon import contains_engineering_jargon, scrub_engineering_jargon
    from app.core.redaction import org_redaction_aliases
    from app.ingestion.router import VersionOut, clean_admin_process_text
    from app.knowledge import graph_edit
    from app.knowledge.models import KgEdge
    from app.knowledge.router import _scrub_quality_issues_zh

    # 1. cn_route 仅/只正向限定与“除...外，不涉及...”识别
    assert cn_route._yes("不涉及车内音视频，仅涉及精准行车轨迹与身份证号（约 2 万人）", "sensitive_pi") is True
    assert cn_route.judge(
        {
            "is_cii": "否",
            "important_data": "否",
            "sensitive_pi": "不涉及车内音视频，仅涉及精准行车轨迹与身份证号（约 2 万人）",
            "data_volume": "约 5 万人",
        }
    )["route"] == "security_assessment"
    assert cn_route._yes("不涉及测绘数据，仅涉及车外敏感区域重要数据", "important_data") is True
    assert cn_route._yes("仅涉及驾驶员生物识别特征（约 2 万人）", "sensitive_pi") is True
    assert cn_route._yes("除驾驶员指纹与声纹（约 2 万人）外，不涉及敏感个人信息", "sensitive_pi") is True
    assert cn_route._yes("仅含一般车辆工况参数，不涉及重要数据", "important_data") is False

    # 2. has_no_personal_data 统一拦截“部分日志不含个人信息但售后涉及 15 万名车主”
    mixed_vals = {
        "data_volume": "车端感知日志不含个人信息，但售后工单涉及 15 万名车主",
        "sensitive_pi": "否",
    }
    assert cn_route.has_no_personal_data(mixed_vals["data_volume"], mixed_vals["sensitive_pi"]) is False
    assert doc_rules.personal_involved(mixed_vals) is True
    app_res = doc_applicability.judge(
        source="摩洛哥",
        recipient="中国",
        source_in_library=False,
        values=mixed_vals,
        labels={"data_volume": "涉及自然人数量"},
        grounds=None,
        cn=None,
        overseas="摩洛哥",
        overseas_known=False,
        admission=[],
        function_zh="远程诊断",
    )
    assert app_res["dpia"]["state"] == doc_applicability.NEED
    assert app_res["tia"]["state"] == doc_applicability.NEED

    # 3. cn_route.persons 支持“自然人以下 / 车主以内”与逗号同位语精确预估
    assert cn_route.persons("100 万自然人以下") == 999_999
    assert cn_route.persons("1 万名车主以内") == 9_999
    assert cn_route.persons("当年累计不满 100 万人，预计约 5 万人") == 50_000
    assert cn_route.persons("今年已传输 0 人，未来 3 年预计不满 50 万人") == 499_999

    # 4. sensitive_kinds 提取非“是”开头及转折正向子句中的敏感信息种类
    assert sensitive_kinds("不涉及座舱生物识别，但涉及精准行车轨迹与身份证号（约 8000 人）") == "精准行车轨迹与身份证号"
    assert sensitive_kinds("涉及精准行车轨迹与驾驶员身份证号（约 8000 人）") == "精准行车轨迹与驾驶员身份证号"
    assert sensitive_kinds("不涉及敏感个人信息") == "不涉及"
    assert sensitive_kinds("是") == ""

    # 5. _parse_recipient_info 区分欧美邮编城市国别、主营业务及括号补充说明
    r_west = official_docx._parse_recipient_info(
        "Chery Europe GmbH, Lyoner Straße 15, 60528 Frankfurt am Main, Germany", ""
    )
    assert r_west["name"] == "Chery Europe GmbH"
    assert r_west["address"] == "Lyoner Straße 15, 60528 Frankfurt am Main"
    assert r_west["biz"] == ""
    r_biz = official_docx._parse_recipient_info(
        "新加坡远景云服务有限公司，负责东南亚车联网云端诊断与存储", ""
    )
    assert r_biz["name"] == "新加坡远景云服务有限公司"
    assert r_biz["address"] == ""
    assert "诊断" in r_biz["biz"]
    r_paren = official_docx._parse_recipient_info(
        "奇衡汽车（德国）有限公司（位于慕尼黑工业园，负责智驾联合研发）", ""
    )
    assert r_paren["name"] == "奇衡汽车（德国）有限公司"
    assert r_paren["address"] == "慕尼黑工业园"
    assert "智驾联合研发" in r_paren["biz"]

    # 6. EDPB Table 6 特殊类别按实际类别勾选 + 最长别名优先替换
    org_stub = SimpleNamespace(name="奇衡汽车", llm_redaction_terms=["奇衡汽车集团股份有限公司"])
    ordered = org_redaction_aliases(org_stub)
    assert ordered[0] == "奇衡汽车集团股份有限公司"
    sanitised = _sanitise_edpb_tree(
        {"controller": {"name": "奇衡汽车集团股份有限公司"}},
        ["奇衡汽车", "奇衡汽车集团股份有限公司"],
    )
    assert sanitised["controller"]["name"] == "[Official English Legal Name of Data Controller]"
    edpb_docx = render_edpb_2026_docx(
        {
            "controller": {"name": "Qiheng Auto GmbH"},
            "data_items": [
                {
                    "item": "Driver heart-rate & medical emergency telemetry",
                    "explanation": "In-cabin physiological health alert monitoring",
                    "is_special": True,
                    "special_type": "health",
                }
            ],
        }
    )
    assert len(edpb_docx) > 10_000

    # 7. 全量复合黑话清洗器拦截前缀/后缀变体且零误伤正常英文单词
    raw_jargon = "引擎 pyocr / fastllm / trtllm / autorag / selfrag / ragflow / orjson / uuidv4 / pydanticv2 异常"
    clean_jargon = scrub_engineering_jargon(raw_jargon)
    assert not contains_engineering_jargon(clean_jargon)
    english_prose = "Article 5 paragraph 2 governs data storage, coverage, fragment integrity, hallmark and democrat."
    assert scrub_engineering_jargon(english_prose) == english_prose
    assert clean_admin_process_text(english_prose) == english_prose
    assert _scrub_quality_issues_zh([english_prose]) == [english_prose]
    v_out = VersionOut(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        version_no=1,
        content_hash="a" * 64,
        size_bytes=100,
        mime_type="application/pdf",
        fetched_at=datetime.now(UTC),
        state="parsed",
        page_count=1,
        char_count=100,
        parse_strategy="pdf_text",
        error=None,
        text_source="native",
        ocr_confidence=None,
        check_note="PaddleOCR 复核与 JSON 校验备注",
        official_number=None,
        version_label=None,
        published_date=None,
        effective_date=None,
        repealed_date=None,
        legal_status="in_force",
        metadata_source=None,
    )
    assert not contains_engineering_jargon(v_out.check_note)

    # 8. merge_nodes 重置迁移边审核状态并校验来源条款有效性
    async with session_factory() as s:
        site = SourceSite(
            country_code="SG",
            publisher="PDPC",
            language="en",
            document_type="law",
            entry_url="https://sso.agc.gov.sg/r13",
        )
        s.add(site)
        await s.flush()
        doc_ok = Document(
            site_id=site.id,
            country_code="SG",
            title="R13 Act",
            title_zh="第13轮测试法",
            source_url="https://sso.agc.gov.sg/r13/1",
            document_type="law",
            language="en",
            relevance="relevant",
        )
        s.add(doc_ok)
        await s.flush()
        ver_ok = DocumentVersion(
            document_id=doc_ok.id,
            version_no=1,
            content_hash="e" * 64,
            archive_path="r13/ok.pdf",
            size_bytes=80,
            mime_type="application/pdf",
            final_url="https://sso.agc.gov.sg/r13/1",
            state="parsed",
            legal_status="in_force",
            review_status="approved",
        )
        s.add(ver_ok)
        await s.flush()
        cl_ok = Clause(
            version_id=ver_ok.id,
            ordinal=1,
            clause_number="Article 9",
            original_text="Data controllers shall notify the Personal Data Protection Commission within 72 hours.",
            text_hash="f" * 64,
            language="en",
        )
        s.add(cl_ok)
        await s.flush()
        n_keep = await graph_edit.create_node(
            s, country_code="SG", node_type="OBLIGATION", name_zh="72小时数据泄露通报义务", evidence_clause_id=cl_ok.id
        )
        n_drop = await graph_edit.create_node(
            s, country_code="SG", node_type="OBLIGATION", name_zh="数据泄露通报义务（重复）", evidence_clause_id=cl_ok.id
        )
        n_reg = await graph_edit.create_node(
            s, country_code="SG", node_type="REGULATOR", name_zh="个人数据保护委员会"
        )
        e_move = await graph_edit.create_edge(
            s,
            source_node_id=n_reg.id,
            target_node_id=n_drop.id,
            relation_type="SUPERVISES",
            evidence_clause_id=cl_ok.id,
            evidence_quote="notify the Personal Data Protection Commission within 72 hours",
        )
        e_move.review_status = "approved"
        e_move.verify_state = "verified"
        await s.flush()
        res_m = await graph_edit.merge_nodes(s, n_keep.id, n_drop.id)
        assert res_m["edges_moved"] == 1
        moved_edge = await s.get(KgEdge, e_move.id)
        assert moved_edge.review_status == "pending_review" and moved_edge.verify_state is None


@pytest.mark.asyncio
async def test_round14_blind_audit_convergence_guards(client, seed, session_factory):
    from datetime import date

    import pypdfium2 as pdfium

    from app.forms import service as forms_service
    from app.knowledge.models import ClauseTranslation, KgEdge, KgNode
    from app.knowledge.router import _resolve_clause_by_title
    from app.updates import subscriptions as subs

    # 1. 外文官方原表双语名称提取支持内部括号、法语重音拉丁字母，渲染 PDF 零汉字外泄
    lb_bi = "启衡智行中东（黎巴嫩）股份公司（Qiheng Smart Mobility Middle East (Lebanon) SAL）"
    ma_bi = "启衡智行摩洛哥公司（Société Qiheng Mobilité Maroc SARL）"
    assert (
        forms_service.latin_part_if_bilingual(lb_bi, "LB")
        == "Qiheng Smart Mobility Middle East (Lebanon) SAL"
    )
    assert (
        forms_service.latin_part_if_bilingual(ma_bi, "MA")
        == "Société Qiheng Mobilité Maroc SARL"
    )
    lb_form = forms_service.get_form("LB_TRA_DOC")
    lb_pdf, _, _ = forms_service.render(
        lb_form,
        {},
        {"company_name": "启衡智行中东（黎巴嫩）股份公司（Qiheng Smart Mobility Middle East SAL）"},
    )
    lb_doc = pdfium.PdfDocument(lb_pdf)
    lb_p1 = lb_doc[0].get_textpage().get_text_range()
    assert "Qiheng Smart Mobility Middle East SAL" in lb_p1
    assert not CJK_RE.search(lb_p1)

    # 2. 法规详情 focus_clause_id 与拆条合并单元：translated_clauses_count 严格 <= clauses_count；
    #    _resolve_clause_by_title 精确匹配第 1 条而不误中第 10 条；
    #    更新推送、驾驶舱与时效报表排除 draft / rejected 版本
    async with session_factory() as s:
        site = SourceSite(
            country_code="SG",
            publisher="PDPC",
            language="en",
            document_type="LAW",
            entry_url="https://sso.agc.gov.sg/r14",
        )
        s.add(site)
        await s.flush()
        doc_split = Document(
            site_id=site.id,
            country_code="SG",
            title="R14 Split Act",
            title_zh="第14轮拆条法",
            source_url="https://sso.agc.gov.sg/r14/split",
            document_type="LAW",
            language="en",
            relevance="relevant",
        )
        doc_draft = Document(
            site_id=site.id,
            country_code="SG",
            title="R14 Draft Bill",
            title_zh="第14轮草案",
            source_url="https://sso.agc.gov.sg/r14/draft",
            document_type="LAW",
            language="en",
            relevance="relevant",
        )
        s.add_all([doc_split, doc_draft])
        await s.flush()
        ver_split = DocumentVersion(
            document_id=doc_split.id,
            version_no=1,
            content_hash="1" * 64,
            archive_path="r14/split.pdf",
            size_bytes=120,
            mime_type="application/pdf",
            final_url="https://sso.agc.gov.sg/r14/split",
            state="parsed",
            legal_status="in_force",
            review_status="approved",
            published_date=date(2026, 3, 1),
        )
        ver_draft = DocumentVersion(
            document_id=doc_draft.id,
            version_no=1,
            content_hash="2" * 64,
            archive_path="r14/draft.pdf",
            size_bytes=120,
            mime_type="application/pdf",
            final_url="https://sso.agc.gov.sg/r14/draft",
            state="parsed",
            legal_status="draft",
            review_status="pending_review",
            published_date=date(2026, 11, 1),
        )
        s.add_all([ver_split, ver_draft])
        await s.flush()
        c_ten = Clause(
            version_id=ver_split.id,
            ordinal=1,
            clause_number="Article 10",
            heading="General",
            page_start=1,
            original_text="Article 10 Data retention shall not exceed statutory necessity.",
            text_hash="3" * 64,
            language="en",
        )
        c_one = Clause(
            version_id=ver_split.id,
            ordinal=2,
            clause_number="Article 1",
            heading="Definitions",
            page_start=1,
            original_text="Article 1 Definitions of personal data and controller.",
            text_hash="4" * 64,
            language="en",
        )
        c_note = Clause(
            version_id=ver_split.id,
            ordinal=3,
            clause_number=None,
            heading=None,
            page_start=1,
            original_text="[40/2020]",
            text_hash="5" * 64,
            language="en",
        )
        s.add_all([c_ten, c_one, c_note])
        await s.flush()
        for cl in (c_ten, c_one, c_note):
            s.add(
                ClauseTranslation(
                    clause_id=cl.id,
                    translated_text="合规译文测试段落。",
                    engine="llm",
                    model_name="deepseek-chat",
                    quality_check_passed=True,
                    review_status="approved",
                    is_current=True,
                )
            )
        # 草案文档上的节点与结构边不得泄漏到管理端图谱
        c_dr = Clause(
            version_id=ver_draft.id,
            ordinal=1,
            clause_number="Article 1",
            original_text="Draft consultation clause.",
            text_hash="6" * 64,
            language="en",
        )
        s.add(c_dr)
        await s.flush()
        n_dr = KgNode(
            country_code="SG",
            node_type="OBLIGATION",
            node_key="r14:draft_obl",
            name_zh="草案未生效义务",
            evidence_clause_id=c_dr.id,
            review_status="pending_review",
        )
        n_inst = KgNode(
            country_code="SG",
            node_type="INSTRUMENT",
            node_key="r14:draft_inst",
            name_zh="第14轮草案",
            document_id=doc_draft.id,
            review_status="pending_review",
        )
        s.add_all([n_dr, n_inst])
        await s.flush()
        s.add(
            KgEdge(
                country_code="SG",
                source_node_id=n_inst.id,
                target_node_id=n_dr.id,
                relation_type="PUBLISHES",
                evidence_clause_id=c_dr.id,
                evidence_quote="",
                extractor_model="structural",
                is_structural=True,
                review_status="pending_review",
            )
        )
        await s.commit()
        assert await _resolve_clause_by_title(s, "SG", "第14轮拆条法", "1") == c_one.id
        assert await subs.law_day(s, doc_draft) is None
        split_doc_id, focus_cid, draft_nid = str(doc_split.id), str(c_one.id), str(n_dr.id)

    user_hdr = bearer(await login(client, "user", "a-member@example.com"))
    admin_hdr = bearer(await login(client, "admin", "admin@example.com"))
    r_det = await client.get(
        f"/api/knowledge/regulations/{split_doc_id}?focus_clause_id={focus_cid}", headers=user_hdr
    )
    assert r_det.status_code == 200
    det_sum = r_det.json()["regulation"]
    assert det_sum["clauses_count"] == 2
    assert det_sum["translated_clauses_count"] == 2
    assert det_sum["translated_clauses_count"] <= det_sum["clauses_count"]

    r_graph = await client.get("/api/admin/knowledge/graph?country_code=SG", headers=admin_hdr)
    assert r_graph.status_code == 200
    gp = r_graph.json()
    assert draft_nid not in {n["id"] for n in gp["nodes"]}
    assert gp["quality_summary"]["linked_clauses_count"] <= gp["quality_summary"]["total_clauses_count"]

    r_ck = await client.get("/api/admin/cockpit/overview", headers=admin_hdr)
    assert r_ck.status_code == 200
    assert r_ck.json()["headline"]["laws_in_force"] == 1
    r_up = await client.get("/api/admin/updates/report", headers=admin_hdr)
    assert r_up.status_code == 200
    assert r_up.json()["intake_30d"]["in_library_documents_total"] == 1


@pytest.mark.asyncio
async def test_round15_blind_audit_convergence_guards(client, seed, session_factory):
    """Round 15 zero-prior blind audit regression guards:
    1) Foreign submission language resolves official form language ('ar' on TN_ATTT_RECEPTION)
       when profile['language'] is '待核实', and mismatched lang_code is rejected with 409;
    2) cn_route.persons & judge resolve '不涉及个人信息' / '无个人信息' / '零人' / '0' to exempt
       without contradicting doc_applicability DPIA=NA;
    3) GET /api/admission/by-task/{task_id} enforces user_shown (blocks 0-step & draft/out_of_scope);
    4) 30d intake timeliness numerator never double-subtracts pending overdue items;
    5) GET /api/knowledge/clauses excludes ambiguous_current conflicting translations;
    6) GET /api/analysis/clauses/{id}/citation-card handles native Chinese ('zh') without 'not_translated'
       and blocks repealed/out_of_scope from usable_as_legal_basis;
    7) Cockpit account_summary excludes self-registered trial sandbox orgs from formal enterprise counts.
    """
    from datetime import UTC, datetime, timedelta

    from app.admission import foreign_letter
    from app.admission.models import AdmissionDraft, AdmissionEntry, AdmissionProject, AdmissionStep
    from app.analysis import cn_route, doc_applicability, pdf_renderer
    from app.analysis.models import AnalysisTask
    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge.models import ClauseTranslation
    from app.sources.models import SourceSite
    from app.updates.models import RegulatoryDiscovery

    from .conftest import bearer, login

    # BiDi isolation in RTL Arabic paragraphs keeps 4G/5G intact as LTR token
    ar_bidi = pdf_renderer._text("للاتصالات المتنقلة 4G/5G وطرفية T-Box")
    assert "\u20664G/5G\u2069" in ar_bidi

    # 1) cn_route + doc_applicability zero-PI text alignment
    for zero_txt in ("不涉及个人信息", "无个人信息", "零人", "0", "无", "不涉及"):
        assert cn_route.persons(zero_txt) == 0
        vals = {
            "is_cii": "否",
            "important_data": "否",
            "sensitive_pi": "不含敏感个人信息",
            "data_volume": zero_txt,
        }
        cn_res = cn_route.judge(vals)
        assert cn_res["route"] == "exempt"
        assert cn_res["missing"] == []
        assert cn_res["docs"]["dpia"]["applies"] is False
        assert "仍要做个人信息保护影响评估" not in cn_res["docs"]["risk_report"]["why"]
        da_res = doc_applicability.judge(
            source="中国",
            recipient="新加坡",
            source_in_library=True,
            values=vals,
            labels={"data_volume": "涉及自然人数量"},
            grounds=None,
            cn=cn_res,
            overseas="新加坡",
            overseas_known=True,
            admission=[],
            function_zh="远程诊断",
        )
        assert da_res["declaration"]["state"] == doc_applicability.NA
        assert da_res["risk_report"]["state"] == doc_applicability.NA
        assert da_res["dpia"]["state"] == doc_applicability.NA
        assert da_res["contract"]["state"] == doc_applicability.NEED

    now = datetime.now(UTC)
    async with session_factory() as s:
        site_tn = SourceSite(
            country_code="TN",
            publisher="ATTT",
            language="fr",
            document_type="LAW",
            entry_url="https://legislation.tn/r15",
        )
        site_cn = SourceSite(
            country_code="CN",
            publisher="CAC",
            language="zh",
            document_type="LAW",
            entry_url="https://www.cac.gov.cn/r15",
        )
        site_sg = SourceSite(
            country_code="SG",
            publisher="PDPC",
            language="en",
            document_type="LAW",
            entry_url="https://sso.agc.gov.sg/r15",
        )
        s.add_all([site_tn, site_cn, site_sg])
        await s.flush()

        # TN French road code clause + TN ATTT entry with '待核实' language -> official form TN_ATTT_RECEPTION ('ar')
        tn_doc = Document(
            site_id=site_tn.id,
            country_code="TN",
            title="Code de la route",
            title_zh="道路法典",
            source_url="https://legislation.tn/code-route",
            document_type="LAW",
            language="fr",
            relevance="relevant",
        )
        s.add(tn_doc)
        await s.flush()
        tn_ver = DocumentVersion(
            document_id=tn_doc.id,
            version_no=1,
            content_hash="c" * 64,
            archive_path="r15/tn_route.pdf",
            size_bytes=100,
            mime_type="application/pdf",
            final_url="https://legislation.tn/code-route",
            state="parsed",
            legal_status="in_force",
            review_status="approved",
        )
        s.add(tn_ver)
        await s.flush()
        tn_clause = Clause(
            version_id=tn_ver.id,
            clause_number="Art. 10",
            original_text="Tout véhicule doit faire l'objet d'une réception par type.",
            language="fr",
            ordinal=1,
            text_hash="d" * 64,
            needs_review=False,
        )
        s.add(tn_clause)
        await s.flush()
        s.add(
            ClauseTranslation(
                clause_id=tn_clause.id,
                translated_text="所有车辆均须办理车辆型式认证。",
                engine="llm",
                model_name="deepseek-chat",
                review_status="approved",
                quality_check_passed=True,
                is_current=True,
            )
        )
        tn_entry = AdmissionEntry(
            country_code="TN",
            name_zh="车辆型式认证",
            category="vehicle_approval",
            authority_zh="陆路运输技术局（ATTT）",
            summary_zh="车辆须办理型式认证。",
            evidence_clause_id=tn_clause.id,
            evidence_quote="réception par type",
            review_status="approved",
            profile={"language": "待核实", "form_codes": ["TN_ATTT_RECEPTION"]},
        )
        s.add(tn_entry)
        await s.flush()
        s.add(
            AdmissionStep(
                entry_id=tn_entry.id,
                seq=1,
                title_zh="提交申请",
                detail_zh="向 ATTT 提交申请表",
                materials=["技术参数表"],
                evidence_quote="réception par type",
                evidence_clause_id=tn_clause.id,
            )
        )

        # Draft + out_of_scope LB doc & 0-step entry (must NOT leak in /api/admission/by-task)
        lb_draft_doc = Document(
            site_id=site_tn.id,
            country_code="TN",
            title="Projet de politique télécom",
            title_zh="电信政策草案",
            source_url="https://example.tn/draft",
            document_type="LAW",
            language="fr",
            relevance="out_of_scope",
        )
        s.add(lb_draft_doc)
        await s.flush()
        lb_draft_ver = DocumentVersion(
            document_id=lb_draft_doc.id,
            version_no=1,
            content_hash="e" * 64,
            archive_path="r15/draft.pdf",
            size_bytes=100,
            mime_type="application/pdf",
            final_url="https://example.tn/draft",
            state="parsed",
            legal_status="draft",
            review_status="approved",
        )
        s.add(lb_draft_ver)
        await s.flush()
        lb_draft_clause = Clause(
            version_id=lb_draft_ver.id,
            clause_number="1",
            original_text="Draft clause text.",
            language="fr",
            ordinal=1,
            text_hash="1" * 64,
            needs_review=False,
        )
        s.add(lb_draft_clause)
        await s.flush()
        bad_entry = AdmissionEntry(
            country_code="TN",
            name_zh="未就绪草案许可",
            category="telecom_approval",
            authority_zh="草案机构",
            summary_zh="草案事项",
            evidence_clause_id=lb_draft_clause.id,
            evidence_quote="Draft clause text.",
            review_status="approved",
        )
        s.add(bad_entry)

        # Native Chinese statute clause (pending_review, no ClauseTranslation row)
        cn_doc = Document(
            site_id=site_cn.id,
            country_code="CN",
            title="中华人民共和国个人信息保护法",
            title_zh="中华人民共和国个人信息保护法",
            source_url="https://www.cac.gov.cn/pipl",
            document_type="act",
            language="zh",
            relevance="relevant",
        )
        s.add(cn_doc)
        await s.flush()
        cn_ver = DocumentVersion(
            document_id=cn_doc.id,
            version_no=1,
            content_hash="2" * 64,
            archive_path="r15/pipl.pdf",
            size_bytes=100,
            mime_type="application/pdf",
            final_url="https://www.cac.gov.cn/pipl",
            state="parsed",
            legal_status="in_force",
            review_status="pending_review",
        )
        s.add(cn_ver)
        await s.flush()
        cn_clause = Clause(
            version_id=cn_ver.id,
            clause_number="第三十八条",
            original_text="个人信息处理者因业务等需要，确需向中华人民共和国境外提供个人信息的，应当具备下列条件之一。",
            language="zh",
            ordinal=1,
            text_hash="3" * 64,
            needs_review=True,
        )
        s.add(cn_clause)

        # Ambiguous current translation clause in SG
        sg_doc = Document(
            site_id=site_sg.id,
            country_code="SG",
            title="Personal Data Protection Act 2012",
            title_zh="个人数据保护法",
            source_url="https://sso.agc.gov.sg/Act/PDPA2012",
            document_type="act",
            language="en",
            relevance="relevant",
        )
        s.add(sg_doc)
        await s.flush()
        sg_ver = DocumentVersion(
            document_id=sg_doc.id,
            version_no=1,
            content_hash="4" * 64,
            archive_path="r15/pdpa.pdf",
            size_bytes=100,
            mime_type="application/pdf",
            final_url="https://sso.agc.gov.sg/Act/PDPA2012",
            state="parsed",
            legal_status="in_force",
            review_status="approved",
            parsed_at=now - timedelta(days=4, hours=23),
        )
        s.add(sg_ver)
        await s.flush()
        sg_amb_clause = Clause(
            version_id=sg_ver.id,
            clause_number="26",
            original_text="Transfer limitation obligation uniquekeywordambig.",
            language="en",
            ordinal=1,
            text_hash="5" * 64,
            needs_review=False,
        )
        s.add(sg_amb_clause)
        await s.flush()
        for txt_zh in ("跨境传输限制义务版本甲", "跨境传输限制义务版本乙"):
            s.add(
                ClauseTranslation(
                    clause_id=sg_amb_clause.id,
                    translated_text=txt_zh,
                    engine="llm",
                    model_name="deepseek-chat",
                    review_status="approved",
                    quality_check_passed=True,
                    is_current=True,
                )
            )

        # RegulatoryDiscovery: 1 on-time completed (1h) + 1 pending overdue (40d) => 50.0% (NOT 0.0%!)
        s.add(
            RegulatoryDiscovery(
                site_id=site_sg.id,
                document_id=sg_doc.id,
                country_code="SG",
                title="On-time Law",
                source_url="https://sso.agc.gov.sg/Act/PDPA2012",
                discovered_at=now - timedelta(days=5),
                last_seen_at=now - timedelta(days=5),
            )
        )
        s.add(
            RegulatoryDiscovery(
                site_id=site_sg.id,
                country_code="SG",
                title="Pending Overdue Law",
                source_url="https://sso.agc.gov.sg/late",
                discovered_at=now - timedelta(days=40),
                last_seen_at=now - timedelta(days=40),
            )
        )

        # Project + mismatched 'fr' foreign draft on Arabic-submission TN ATTT entry
        tn_proj = AdmissionProject(
            org_id=seed["org_a"].id,
            entry_id=tn_entry.id,
            title="突尼斯 · 车辆型式认证",
            applicant="Société Tunisienne Auto SARL",
            vehicle_model="QH-TN-01",
            created_by=seed["a_member"].id,
        )
        s.add(tn_proj)
        await s.flush()
        tn_draft = AdmissionDraft(
            project_id=tn_proj.id,
            org_id=seed["org_a"].id,
            created_by=seed["a_member"].id,
            name_zh="车辆型式认证申请材料",
            blocks=[{"type": "title", "text": "车辆型式认证申请书"}],
            missing=[],
            forms=[],
            foreign_draft={
                "lang_code": "fr",
                "lang_zh": "法文",
                "name_zh": "车辆型式认证申请书（法文正式稿）",
                "title": "Demande",
                "blocks": [{"type": "title", "text": "Demande"}],
            },
        )
        s.add(tn_draft)
        tn_task = AnalysisTask(
            org_id=seed["org_a"].id,
            created_by=seed["a_member"].id,
            title="中国至突尼斯评估",
            country_code="TN",
            vehicle_model="QH-TN-01",
            scenario_type="vehicle_data_export",
            kind="documents",
            facts_json={"source_country": "中国", "recipient_country": "突尼斯"},
        )
        s.add(tn_task)
        await s.flush()

        resolved = await foreign_letter.resolve_submission_language(s, tn_entry)
        assert resolved == ("ar", "阿拉伯文", "Arabic")

        tn_proj_id = str(tn_proj.id)
        tn_draft_id = str(tn_draft.id)
        tn_entry_id = str(tn_entry.id)
        bad_entry_id = str(bad_entry.id)
        tn_task_id = str(tn_task.id)
        cn_cid = str(cn_clause.id)
        lb_draft_cid = str(lb_draft_clause.id)
        await s.commit()

    user_hdr = bearer(await login(client, "user", "a-member@example.com"))
    admin_hdr = bearer(await login(client, "admin", "admin@example.com"))

    # Foreign language mismatch: project reports foreign_lang_code='ar' and has_foreign=False, GET /foreign -> 409
    p_det = (await client.get(f"/api/admission/projects/{tn_proj_id}", headers=user_hdr)).json()
    assert p_det["foreign_lang_code"] == "ar"
    assert p_det["foreign_lang_zh"] == "阿拉伯文"
    assert p_det["drafts"][0]["has_foreign"] is False
    assert (await client.get(f"/api/admission/drafts/{tn_draft_id}/foreign", headers=user_hdr)).status_code == 409

    # /api/admission/by-task filters out draft/out_of_scope/0-step entries
    by_task = (await client.get(f"/api/admission/by-task/{tn_task_id}", headers=user_hdr)).json()
    returned_eids = {e["id"] for e in by_task["entries"]}
    assert tn_entry_id in returned_eids
    assert bad_entry_id not in returned_eids

    # /api/knowledge/clauses excludes ambiguous_current conflicting translations
    amb_search = (
        await client.get("/api/knowledge/clauses?country_code=SG&keyword=uniquekeywordambig", headers=user_hdr)
    ).json()
    assert amb_search == []

    # /api/analysis/clauses/{id}/citation-card handles native zh & blocks draft/out_of_scope
    cn_card = (await client.get(f"/api/analysis/clauses/{cn_cid}/citation-card", headers=user_hdr)).json()
    assert cn_card["is_native_zh"] is True
    assert cn_card["review_status"] == "pending_review"
    assert cn_card["translation_review_status"] == "native_zh"
    assert cn_card["usable_as_legal_basis"] is False

    draft_card = (await client.get(f"/api/analysis/clauses/{lb_draft_cid}/citation-card", headers=user_hdr)).json()
    assert draft_card["usable_as_legal_basis"] is False
    assert draft_card["source_integrity_alerts"]

    # 30d intake timeliness numerator: 1 on-time completed + 1 pending overdue = 50.0%
    rep = (await client.get("/api/admin/updates/report", headers=admin_hdr)).json()
    assert rep["intake_30d"]["tracked"] == 2
    assert rep["intake_30d"]["completed"] == 1
    assert rep["intake_30d"]["overdue_30d"] == 1
    assert rep["intake_30d"]["on_time_rate_pct"] == 50.0

    # Cockpit excludes self-registered trial sandbox orgs from formal enterprise counts
    ck_before = (await client.get("/api/admin/cockpit/overview", headers=admin_hdr)).json()
    reg_res = await client.post(
        "/api/auth/user/register",
        json={
            "email": "r15-trial@visitor.example.com",
            "display_name": "体验访客",
            "org_name": "外部体验车企有限公司",
            "password": "TrialPassword!2026",
        },
    )
    assert reg_res.status_code == 201
    ck_after = (await client.get("/api/admin/cockpit/overview", headers=admin_hdr)).json()
    assert ck_after["headline"]["active_organizations"] == ck_before["headline"]["active_organizations"]
    assert ck_after["account_summary"]["ordinary_viewers"] == ck_before["account_summary"]["ordinary_viewers"]
    assert ck_after["account_summary"]["portal_onboarded"] == ck_before["account_summary"]["portal_onboarded"]
    assert ck_after["account_summary"]["trial_visitors"] == ck_before["account_summary"].get("trial_visitors", 0) + 1


@pytest.mark.asyncio
async def test_round16_blind_audit_convergence_guards(client, seed, session_factory):
    """Round 16 zero-prior blind audit regression guards:
    1) ambiguous_current excluded from doc_rules.core_clauses, admission._basis,
       /api/knowledge/regulations/{id} translated_clauses_count & keyword search & status badge,
       /api/knowledge/overview, and cockpit translated_clauses_total;
    2) repealed/draft excluded from main_law, whitepaper_graph.build D1, _knowledge_overview,
       and non-draft version preferred over draft v2 on latest_parsed / _build_regulation_summary;
    3) active_overseas_countries blocks safety_hold, draft/repealed, and empty whitepapers;
    4) /api/knowledge/overview counts CN native Chinese ('zh') clauses in translations_count;
    5) 72h SLA retains overdue_72h when resolved after due_at;
    6) Cockpit & updates SLA exclude trial sandbox notifications/impacts, align pending_updates
       by (task_id, change_id), and exclude _viewer_private / _doc_project from consulting_chats_total.
    """
    from datetime import UTC, datetime, timedelta

    from app.admission import router as adm_router
    from app.analysis import doc_rules, workflow_engine
    from app.analysis.models import AnalysisTask, ReportVersion
    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge import library_view, whitepaper_graph
    from app.knowledge.models import ClauseTranslation, KnowledgeChangeProposal
    from app.knowledge.router import _OVERVIEW_CACHE
    from app.sources.models import SourceSite
    from app.updates.models import (
        UpdateImpact,
        UserNotification,
        VersionChange,
    )

    from .conftest import bearer, login

    _OVERVIEW_CACHE.clear()
    now = datetime.now(UTC)
    async with session_factory() as s:
        site_sg = SourceSite(
            country_code="SG",
            publisher="PDPC",
            language="en",
            document_type="LAW",
            entry_url="https://sso.agc.gov.sg/r16",
        )
        site_cn = SourceSite(
            country_code="CN",
            publisher="CAC",
            language="zh",
            document_type="LAW",
            entry_url="https://www.cac.gov.cn/r16",
        )
        site_ae = SourceSite(
            country_code="AE",
            publisher="UAE Gov",
            language="ar",
            document_type="LAW",
            entry_url="https://uaelegislation.gov.ae/r16",
        )
        s.add_all([site_sg, site_cn, site_ae])
        await s.flush()

        # SG in-force law v1 + consultation draft v2 on the SAME document + ambiguous_current clause
        sg_law = Document(
            site_id=site_sg.id,
            country_code="SG",
            title="Vehicle Data Protection Act 2024",
            title_zh="新加坡汽车数据保护法",
            source_url="https://sso.agc.gov.sg/r16/vdpa",
            document_type="LAW",
            business_topic="personal_data",
            business_weight="core",
            language="en",
            relevance="relevant",
        )
        # SG repealed law with MORE clauses (must NEVER win main_law or enter Briefing D1)
        sg_rep = Document(
            site_id=site_sg.id,
            country_code="SG",
            title="Repealed Data Act 1998",
            title_zh="已废止旧数据法",
            source_url="https://sso.agc.gov.sg/r16/rep",
            document_type="LAW",
            business_topic="personal_data",
            business_weight="core",
            language="en",
            relevance="relevant",
        )
        # AE safety_hold document (must NOT leak AE into active_overseas_countries)
        ae_doc = Document(
            site_id=site_ae.id,
            country_code="AE",
            title="UAE Draft Law",
            title_zh="阿联酋隔离文件",
            source_url="https://uaelegislation.gov.ae/r16/hold",
            document_type="LAW",
            language="ar",
            relevance="relevant",
        )
        # CN native Chinese statute (no ClauseTranslation rows -> overview translations_count == 1)
        cn_law = Document(
            site_id=site_cn.id,
            country_code="CN",
            title="汽车数据安全管理若干规定（试行）",
            title_zh="汽车数据安全管理若干规定（试行）",
            source_url="https://www.cac.gov.cn/r16/auto",
            document_type="LAW",
            language="zh",
            relevance="relevant",
        )
        s.add_all([sg_law, sg_rep, ae_doc, cn_law])
        await s.flush()

        s.add(
            KnowledgeChangeProposal(
                target_type="documents",
                target_id=ae_doc.id,
                content_hash="a" * 64,
                payload={"safety_hold": True},
                status="pending_review",
            )
        )

        v1_inforce = DocumentVersion(
            document_id=sg_law.id,
            version_no=1,
            content_hash="1" * 64,
            archive_path="r16/vdpa_v1.pdf",
            size_bytes=100,
            mime_type="application/pdf",
            final_url="https://sso.agc.gov.sg/r16/vdpa",
            state="parsed",
            legal_status="in_force",
            review_status="approved",
        )
        v2_draft = DocumentVersion(
            document_id=sg_law.id,
            version_no=2,
            content_hash="2" * 64,
            archive_path="r16/vdpa_v2_draft.pdf",
            size_bytes=100,
            mime_type="application/pdf",
            final_url="https://sso.agc.gov.sg/r16/vdpa-draft",
            state="parsed",
            legal_status="draft",
            review_status="pending_review",
        )
        v_rep = DocumentVersion(
            document_id=sg_rep.id,
            version_no=1,
            content_hash="3" * 64,
            archive_path="r16/rep.pdf",
            size_bytes=100,
            mime_type="application/pdf",
            final_url="https://sso.agc.gov.sg/r16/rep",
            state="parsed",
            legal_status="repealed",
            review_status="approved",
        )
        v_ae = DocumentVersion(
            document_id=ae_doc.id,
            version_no=1,
            content_hash="4" * 64,
            archive_path="r16/ae.pdf",
            size_bytes=100,
            mime_type="application/pdf",
            final_url="https://uaelegislation.gov.ae/r16/hold",
            state="parsed",
            legal_status="in_force",
            review_status="approved",
        )
        v_cn = DocumentVersion(
            document_id=cn_law.id,
            version_no=1,
            content_hash="5" * 64,
            archive_path="r16/cn.pdf",
            size_bytes=100,
            mime_type="application/pdf",
            final_url="https://www.cac.gov.cn/r16/auto",
            state="parsed",
            legal_status="in_force",
            review_status="approved",
        )
        s.add_all([v1_inforce, v2_draft, v_rep, v_ae, v_cn])
        await s.flush()

        c_sg_amb = Clause(
            version_id=v1_inforce.id,
            ordinal=1,
            clause_number="Article 11",
            original_text="Cross-border transfer of important vehicle data abroad requires approval.",
            language="en",
            text_hash="6" * 64,
            needs_review=False,
        )
        c_sg_draft = Clause(
            version_id=v2_draft.id,
            ordinal=1,
            clause_number="Article 1",
            original_text="Consultation draft article.",
            language="en",
            text_hash="7" * 64,
            needs_review=False,
        )
        s.add_all([c_sg_amb, c_sg_draft])
        for idx in range(1, 5):
            s.add(
                Clause(
                    version_id=v_rep.id,
                    ordinal=idx,
                    clause_number=f"Article {idx}",
                    original_text=f"Repealed provision {idx} on personal data.",
                    language="en",
                    text_hash=str(idx) * 64,
                    needs_review=False,
                )
            )
        c_cn = Clause(
            version_id=v_cn.id,
            ordinal=1,
            clause_number="第十一条",
            original_text="重要数据应当依法在境内存储，因业务需要确需向境外提供的应当通过数据出境安全评估。",
            language="zh",
            text_hash="9" * 64,
            needs_review=False,
        )
        s.add(c_cn)
        await s.flush()

        # Two conflicting approved current translations on c_sg_amb
        for conflict_zh in ("冲突译文甲：重要数据向境外传输须获批", "冲突译文乙：重要数据出境仅需事后报备"):
            s.add(
                ClauseTranslation(
                    clause_id=c_sg_amb.id,
                    translated_text=conflict_zh,
                    engine="llm",
                    model_name="deepseek-chat",
                    review_status="approved",
                    quality_check_passed=True,
                    is_current=True,
                )
            )

        # Tasks & UpdateImpact: 1 taskresolved LATE (after 72h), 1 open task with 3 dim rows + viewer private chat
        t_late = AnalysisTask(
            org_id=seed["org_a"].id,
            created_by=seed["a_member"].id,
            title="延迟结案任务",
            country_code="SG",
            vehicle_model="QH-16A",
            scenario_type="connected_vehicle_cloud",
            kind="chat",
            facts_json={},
        )
        t_open = AnalysisTask(
            org_id=seed["org_a"].id,
            created_by=seed["a_member"].id,
            title="多维待处理任务",
            country_code="SG",
            vehicle_model="QH-16B",
            scenario_type="connected_vehicle_cloud",
            kind="chat",
            facts_json={},
        )
        t_priv = AnalysisTask(
            org_id=seed["org_a"].id,
            created_by=seed["a_member"].id,
            title="查看者私密对话",
            country_code="SG",
            vehicle_model="QH-16C",
            scenario_type="connected_vehicle_cloud",
            kind="chat",
            facts_json={"_viewer_private": str(seed["a_member"].id)},
        )
        s.add_all([t_late, t_open, t_priv])
        await s.flush()

        rv1 = ReportVersion(
            org_id=seed["org_a"].id,
            task_id=t_late.id,
            version_no=1,
            change_note="初稿",
            executive_summary="s1",
            dimensions_json=[],
        )
        rv2 = ReportVersion(
            org_id=seed["org_a"].id,
            task_id=t_open.id,
            version_no=1,
            change_note="初稿",
            executive_summary="s2",
            dimensions_json=[],
        )
        s.add_all([rv1, rv2])
        await s.flush()
        vc = VersionChange(
            origin_key="r16-vc-1",
            source_observation_id=None,
            document_id=sg_law.id,
            old_version_id=v1_inforce.id,
            new_version_id=v2_draft.id,
            detected_at=now - timedelta(days=10),
            change_kind="amended",
            summary="修订测试",
            changes_json=[],
            graph_edge_ids=[],
            content_hashes={},
        )
        s.add(vc)
        await s.flush()

        # Late-resolved impact: detected 10d ago, due 7d ago, resolved 1d ago -> STILL counts as overdue_72h!
        s.add(
            UpdateImpact(
                org_id=seed["org_a"].id,
                task_id=t_late.id,
                report_version_id=rv1.id,
                dimension_code="D3",
                change_id=vc.id,
                detected_at=now - timedelta(days=10),
                due_at=now - timedelta(days=7),
                state="resolved",
                resolved_at=now - timedelta(days=1),
            )
        )
        # 3 dimension rows on the SAME (t_open, vc) within 72h -> counts as 1 pending_updates in BOTH cockpit and SLA!
        for dim_c in ("D1", "D3", "D6"):
            s.add(
                UpdateImpact(
                    org_id=seed["org_a"].id,
                    task_id=t_open.id,
                    report_version_id=rv2.id,
                    dimension_code=dim_c,
                    change_id=vc.id,
                    detected_at=now - timedelta(hours=2),
                    due_at=now + timedelta(hours=70),
                    state="open",
                )
            )

        # Direct unit checks inside session
        assert await doc_rules.core_clauses(s, "SG", "recipient") == []
        basis_amb = await adm_router._basis(s, c_sg_amb.id)
        assert basis_amb is not None and basis_amb["zh"] == ""

        overseas_codes = {x["code"] for x in await workflow_engine.active_overseas_countries(s)}
        assert "AE" not in overseas_codes

        lib_all = await library_view.library(s, "SG")
        chosen_main = library_view.main_law(lib_all, {})
        assert chosen_main is not None and chosen_main.document.id == sg_law.id
        assert chosen_main.version.id == v1_inforce.id

        wp_built = await whitepaper_graph.build(s, "SG")
        d1_text = str(wp_built)
        assert "新加坡汽车数据保护法" in d1_text
        assert "已废止旧数据法" not in d1_text

        sg_law_id = str(sg_law.id)
        await s.commit()

    _OVERVIEW_CACHE.clear()
    user_hdr = bearer(await login(client, "user", "a-member@example.com"))
    admin_hdr = bearer(await login(client, "admin", "admin@example.com"))

    # Self-register trial visitor + insert trial notification -> must NOT pollute SLA enterprise_notices_pushed
    reg_res = await client.post(
        "/api/auth/user/register",
        json={
            "email": "r16-trial@visitor.example.com",
            "display_name": "沙箱访客",
            "org_name": "外部沙箱体验公司",
            "password": "TrialPassword!2026",
        },
    )
    assert reg_res.status_code == 201
    trial_oid = reg_res.json()["org_id"]
    async with session_factory() as s:
        import uuid as _uuid

        from app.iam.models import User

        trial_u = await s.scalar(select(User).where(User.email == "r16-trial@visitor.example.com"))
        assert trial_u is not None
        s.add(
            UserNotification(
                org_id=_uuid.UUID(trial_oid),
                user_id=trial_u.id,
                kind="subscription",
                title="沙箱通知",
                body="不应计入正式企业提醒",
                dedupe_key="r16-trial-n1",
            )
        )
        await s.commit()

    # 1) Regulation detail: prefers v1_inforce over v2_draft; ambiguous_current not counted as translated,
    #    keyword search on conflict text returns 0, normal view shows translation_status == 'ambiguous_current'
    reg_det = (await client.get(f"/api/knowledge/regulations/{sg_law_id}", headers=user_hdr)).json()
    assert reg_det["regulation"]["legal_status"] == "in_force"
    assert reg_det["regulation"]["translated_clauses_count"] == 0
    assert reg_det["clauses"][0]["translation_status"] == "ambiguous_current"
    assert reg_det["clauses"][0]["usable_as_legal_basis"] is False
    reg_kw = (
        await client.get(f"/api/knowledge/regulations/{sg_law_id}?keyword=冲突译文甲", headers=user_hdr)
    ).json()
    assert reg_kw["clauses_total"] == 0

    # 2) Knowledge overview: SG excludes repealed doc (documents_count == 1, translations_count == 0);
    #    CN counts native zh clause (documents_count == 1, clauses_count == 1, translations_count == 1)
    ov = {c["country_code"]: c for c in (await client.get("/api/knowledge/overview", headers=user_hdr)).json()}
    assert ov["SG"]["documents_count"] == 1
    assert ov["SG"]["translations_count"] == 0
    assert ov["CN"]["documents_count"] == 1
    assert ov["CN"]["clauses_count"] == 1
    assert ov["CN"]["translations_count"] == 1

    # 3) Cockpit & SLA report alignment
    ck = (await client.get("/api/admin/cockpit/overview", headers=admin_hdr)).json()
    rep = (await client.get("/api/admin/updates/report", headers=admin_hdr)).json()
    assert ck["headline"]["translated_clauses_total"] == 0
    assert ck["headline"]["consulting_chats_total"] == 2  # excludes t_priv (_viewer_private)
    assert ck["operations"]["pending_updates"] == 1
    assert rep["response_72h"]["pending_updates"] == 1
    assert rep["response_72h"]["resolved_updates"] == 1
    assert rep["response_72h"]["overdue_72h"] == 1  # late-resolved impact stays overdue!
    assert rep["response_72h"]["on_time_rate_pct"] == 50.0
    assert rep["linkage"]["enterprise_notices_pushed"] == 0  # excludes trial visitor notification!

    # 4) CWE-1236 spreadsheet formula injection & '===' XML <f> corruption guards
    import io as _io
    import zipfile as _zf

    from app.analysis.document_instances import build_xlsx

    doc_xlsx = build_xlsx(
        [
            {"type": "title", "text": "=== 补充说明 ==="},
            {"type": "paragraph", "text": '=HYPERLINK("https://evil.example","点击")'},
            {"type": "table", "head": ["栏目", "值"], "rows": [["备注", "=== 待核实 ==="], ["电话", "+86-21-68905518"]]},
        ],
        "=== 申报附件 ===",
    )
    with _zf.ZipFile(_io.BytesIO(doc_xlsx)) as zdoc:
        sheet_xml = zdoc.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "<f>" not in sheet_xml and "<f " not in sheet_xml
        shared_xml = zdoc.read("xl/sharedStrings.xml").decode("utf-8") if "xl/sharedStrings.xml" in zdoc.namelist() else ""
        assert "'=HYPERLINK" in (sheet_xml + shared_xml)

    # Trigger an audit record with '=' prefix in display_name via self-registration, then verify export.xlsx & export.csv
    await client.post(
        "/api/auth/user/register",
        json={
            "email": "r17-formula@visitor.example.com",
            "display_name": '=HYPERLINK("http://evil.example","x")',
            "org_name": "=== 测试企业 ===",
            "password": "TrialPassword!2026",
        },
    )
    aud_xlsx = await client.get("/api/admin/audit-logs/export.xlsx?limit=20", headers=admin_hdr)
    assert aud_xlsx.status_code == 200
    with _zf.ZipFile(_io.BytesIO(aud_xlsx.content)) as zaud:
        aud_sheet = zaud.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "<f>" not in aud_sheet and "<f " not in aud_sheet
    aud_csv = (await client.get("/api/admin/audit-logs/export.csv?limit=20", headers=admin_hdr)).content.decode("utf-8-sig")
    assert "'=HYPERLINK" in aud_csv


