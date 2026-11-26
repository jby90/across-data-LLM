# ruff: noqa: E501
"""Offline regression suite for 2026-11-26 client meeting requirements (zero live DeepSeek calls):
1. Enterprise sub-account editor/viewer RBAC & custom free-added compliance scenarios;
2. Consulting chat clone + rename + freeze archive;
3. Country briefing online editing, human-review anti-drift lock, and historical version rollback;
4. Full-route English Word/PDF/ZIP compliance document & 8-dimension report export (0 CJK leak);
5. 10-expert prompt profile loader & weekly 168h staggered site schedule.
"""

from __future__ import annotations

import io
import re
import uuid
import zipfile
from xml.etree import ElementTree as ET

import pypdfium2 as pdfium

from app.analysis.expert_prompts import load_expert_profiles
from app.analysis.models import AnalysisMessage, AnalysisTask, ReportVersion
from app.iam import service as iam_service
from app.iam.models import Role
from app.knowledge import whitepaper as wp
from app.sources.router import staggered_next_run

from .conftest import PASSWORD, bearer, login

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff01-\uff60]")


def _docx_all_text(raw: bytes) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            if name.startswith("word/") and name.endswith(".xml"):
                root = ET.fromstring(zf.read(name))
                parts.extend(t for t in root.itertext() if t)
    return " ".join(parts)


def _pdf_all_text(raw: bytes) -> str:
    doc = pdfium.PdfDocument(raw)
    try:
        out: list[str] = []
        for idx in range(len(doc)):
            tp = doc[idx].get_textpage()
            out.append(tp.get_text_range())
            tp.close()
        return " ".join(out)
    finally:
        doc.close()


async def test_custom_scenarios_and_chat_clone_freeze(client, seed, session_factory):
    member = bearer(await login(client, "user", "a-member@example.com"))
    async with session_factory() as s:
        await iam_service.create_user(
            s,
            email="v1126@example.com",
            display_name="只读测试员",
            password=PASSWORD,
            role=Role.VIEWER,
            org_id=seed["org_a"].id,
            can_export=False,
        )
    viewer = bearer(await login(client, "user", "v1126@example.com"))

    # 1a. Global null-byte guard returns pure Chinese 422
    r_null = await client.post(
        "/api/analysis/doc-projects",
        headers=member,
        json={
            "source_country": "中国",
            "recipient_country": "新加坡",
            "functions": ["跨境车队碳排监测\x00"],
        },
    )
    assert r_null.status_code == 422

    # 1b. Free-added custom scenarios alongside preset scenarios
    r_create = await client.post(
        "/api/analysis/doc-projects",
        headers=member,
        json={
            "source_country": "中国",
            "recipient_country": "新加坡",
            "functions": ["ota_diagnostics", "跨境车队碳排监测", "海外动力电池健康回传"],
            "business_scenes": ["after_sales", "量产灰度运营阶段"],
            "vehicle_model": "QH-S9",
            "company_name": "测试车企股份有限公司",
        },
    )
    assert r_create.status_code == 201, r_create.text
    proj = r_create.json()
    pid = proj["id"]
    assert proj["route"]["functions"] == ["ota_diagnostics", "跨境车队碳排监测", "海外动力电池健康回传"]
    assert proj["route"]["business_scenes"] == ["after_sales", "量产灰度运营阶段"]

    # Viewer cannot create or clone projects
    assert (
        await client.post(
            "/api/analysis/doc-projects",
            headers=viewer,
            json={"source_country": "中国", "recipient_country": "新加坡", "functions": ["跨境车队碳排监测"]},
        )
    ).status_code == 403

    # 2. Consulting chat clone + rename + freeze archive
    async with session_factory() as s:
        chat = AnalysisTask(
            org_id=seed["org_a"].id,
            created_by=seed["a_member"].id,
            kind="chat",
            title="新加坡车联网合规咨询",
            title_custom=True,
            country_code="SG",
            vehicle_model="QH-S9",
            scenario_type="connected_vehicle_cloud",
            status="completed",
            facts_json={"country_code": "SG"},
        )
        s.add(chat)
        await s.flush()
        s.add(AnalysisMessage(org_id=seed["org_a"].id, task_id=chat.id, seq_no=1, role="user", content="请问新加坡车联网数据出境有什么要求？"))
        s.add(AnalysisMessage(org_id=seed["org_a"].id, task_id=chat.id, seq_no=2, role="assistant", content="依据新加坡《个人数据保护法》第 26 条，须确保境外接收方提供同等保护标准。"))
        s.add(
            ReportVersion(
                org_id=seed["org_a"].id,
                task_id=chat.id,
                version_no=1,
                status="full_draft",
                is_partial_sample=False,
                change_note="初始生成",
                executive_summary="新加坡车联网数据跨境传输须满足同等保护义务与安全保障。",
                dimensions_json=[
                    {
                        "dimension_code": f"D{i}",
                        "dimension_title": f"维度{i}",
                        "status": "ok",
                        "risk_level": "中风险",
                        "content_markdown": "履行新加坡个人数据保护法定要求。",
                        "key_recommendations": ["签署跨境数据传输协议并实施传输加密"],
                        "citations": [
                            {
                                "clause_id": None,
                                "document_title": "新加坡个人数据保护法",
                                "original_title": "Personal Data Protection Act 2012",
                                "clause_number": "第26条",
                                "version_no": 1,
                                "quote_original": "An organisation must not transfer any personal data to a country or territory outside Singapore except in accordance with requirements prescribed under this Act.",
                            }
                        ],
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

    # Viewer cannot clone enterprise chat
    r_v_clone = await client.post(f"/api/analysis/tasks/{chat_id}/clone", headers=viewer)
    assert r_v_clone.status_code == 403

    # Member clones chat, renames snapshot, and archives ("冻结保存")
    r_clone = await client.post(f"/api/analysis/tasks/{chat_id}/clone", headers=member)
    assert r_clone.status_code == 200, r_clone.text
    cloned_id = r_clone.json()["id"]
    assert "（副本）" in r_clone.json()["title"]

    r_ren = await client.patch(f"/api/analysis/tasks/{cloned_id}", headers=member, json={"title": "新加坡合规评估_1126冻结版"})
    assert r_ren.status_code == 200 and r_ren.json()["title"] == "新加坡合规评估_1126冻结版"

    r_arch = await client.delete(f"/api/analysis/tasks/{cloned_id}", headers=member)
    assert r_arch.status_code == 200 and r_arch.json()["archived"] is True

    # 3. Full-route English exports (zero CJK leak, viewer export gate)
    for ep in (
        f"/api/analysis/tasks/{pid}/documents/dpia/export-en.docx?draft=true",
        f"/api/analysis/tasks/{pid}/documents/dpia/export-en.pdf?draft=true",
        f"/api/analysis/doc-projects/{pid}/export.zip?fmt=docx&draft=true&lang=en",
        f"/api/analysis/tasks/{chat_id}/reports/1/export.docx?lang=en",
        f"/api/analysis/tasks/{chat_id}/reports/1/export.pdf?lang=en",
    ):
        assert (await client.get(ep, headers=viewer)).status_code == 403, ep

    r_en_prev = await client.get(f"/api/analysis/tasks/{pid}/documents/dpia/preview-en?draft=true", headers=member)
    assert r_en_prev.status_code == 200
    assert not _CJK_RE.search(str(r_en_prev.json()["blocks"]))

    r_en_docx = await client.get(f"/api/analysis/tasks/{pid}/documents/dpia/export-en.docx?draft=true", headers=member)
    assert r_en_docx.status_code == 200
    txt_docx = _docx_all_text(r_en_docx.content)
    assert "Data Protection Impact Assessment" in txt_docx
    assert not _CJK_RE.search(txt_docx), f"CJK leaked in doc export-en.docx: {_CJK_RE.findall(txt_docx)}"

    r_en_pdf = await client.get(f"/api/analysis/tasks/{pid}/documents/dpia/export-en.pdf?draft=true", headers=member)
    assert r_en_pdf.status_code == 200 and r_en_pdf.content.startswith(b"%PDF")
    txt_pdf = _pdf_all_text(r_en_pdf.content)
    assert not _CJK_RE.search(txt_pdf), f"CJK leaked in doc export-en.pdf: {_CJK_RE.findall(txt_pdf)}"
    from pathlib import Path as _Path

    _proof_dir = _Path(r"C:\Users\zshan\.gemini\antigravity\brain\5f5c95f2-607f-44ff-869e-83d9abd36e24\scratch\pdf_proofs_1126")
    if _proof_dir.exists():
        _doc_p = pdfium.PdfDocument(r_en_pdf.content)
        _doc_p[0].render(scale=1.5).to_pil().save(_proof_dir / "english_dpia_p1.png")
        _doc_p[len(_doc_p) - 1].render(scale=1.5).to_pil().save(_proof_dir / "english_dpia_last.png")
        _doc_p.close()

    r_en_zip = await client.get(f"/api/analysis/doc-projects/{pid}/export.zip?fmt=docx&draft=true&lang=en", headers=member)
    assert r_en_zip.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r_en_zip.content)) as zf:
        docx_names = [n for n in zf.namelist() if n.endswith(".docx") and "_EN" in n]
        assert len(docx_names) >= 3
        for n in docx_names:
            assert not _CJK_RE.search(_docx_all_text(zf.read(n))), n

    r_rep_en_docx = await client.get(f"/api/analysis/tasks/{chat_id}/reports/1/export.docx?lang=en", headers=member)
    assert r_rep_en_docx.status_code == 200
    rep_txt = _docx_all_text(r_rep_en_docx.content)
    assert "Singapore Automotive Cross-Border Data Compliance Assessment" in rep_txt
    assert not _CJK_RE.search(rep_txt)

    r_rep_en_pdf = await client.get(f"/api/analysis/tasks/{chat_id}/reports/1/export.pdf?lang=en", headers=member)
    assert r_rep_en_pdf.status_code == 200 and r_rep_en_pdf.content.startswith(b"%PDF")
    assert not _CJK_RE.search(_pdf_all_text(r_rep_en_pdf.content))


async def test_whitepaper_online_edit_review_lock_and_rollback(client, seed, session_factory):
    admin = bearer(await login(client, "admin", "admin@example.com"))
    user = bearer(await login(client, "user", "a-member@example.com"))

    async with session_factory() as s:
        await wp.save(
            s,
            "SG",
            [
                {"type": "title", "text": "新加坡汽车数据合规洞察简报"},
                {"type": "heading", "text": "一、属地数据法规体系概述"},
                {"type": "paragraph", "text": "初始第一版简报段落内容。"},
            ],
            {"source_hash": "h1", "tokens": 100, "report": {"documents": 2, "clauses": 10}},
        )
        await s.commit()

    r_vers = await client.get("/api/admin/knowledge/whitepapers/SG/versions", headers=admin)
    assert r_vers.status_code == 200
    v1_no = r_vers.json()["version_no"]
    blocks = r_vers.json()["blocks"]

    # Boundary validation: empty blocks -> 422 pure Chinese
    r_bad = await client.patch("/api/admin/knowledge/whitepapers/SG/blocks", headers=admin, json={"blocks": []})
    assert r_bad.status_code == 422

    # Online edit + approve lock -> creates v2
    blocks[-1]["text"] = "经专家人工复核定稿的第二版新加坡简报内容。"
    r_edit = await client.patch(
        "/api/admin/knowledge/whitepapers/SG/blocks",
        headers=admin,
        json={"blocks": blocks, "note": "专家审定第三章措辞", "approve": True},
    )
    assert r_edit.status_code == 200, r_edit.text
    assert r_edit.json()["version_no"] == v1_no + 1
    assert r_edit.json()["review_status"] == "approved"
    assert r_edit.json()["manual_edited"] is True

    # Anti-drift lock blocks full overwrite unless force=true or unlocked
    r_blocked = await client.post("/api/admin/knowledge/whitepapers/SG/update", headers=admin)
    assert r_blocked.status_code == 409
    assert "归档锁定" in r_blocked.json()["detail"]

    # User portal immediately reads v2 edited content
    r_u = await client.get("/api/knowledge/whitepapers/SG", headers=user)
    assert r_u.status_code == 200
    assert any("经专家人工复核定稿的第二版" in str(b.get("text", "")) for b in r_u.json()["blocks"])

    # Rollback to v1 -> creates v3 with v1 text while keeping history intact
    r_rb = await client.post(f"/api/admin/knowledge/whitepapers/SG/versions/{v1_no}/rollback", headers=admin)
    assert r_rb.status_code == 200
    assert r_rb.json()["version_no"] == v1_no + 2

    r_u_after = await client.get("/api/knowledge/whitepapers/SG", headers=user)
    assert any("初始第一版简报段落内容" in str(b.get("text", "")) for b in r_u_after.json()["blocks"])

    # Unlock review lock
    r_unlock = await client.post("/api/admin/knowledge/whitepapers/SG/review", headers=admin, json={"approved": False})
    assert r_unlock.status_code == 200 and r_unlock.json()["review_status"] == "auto_verified"


def test_expert_profiles_and_weekly_stagger_invariants():
    profiles = load_expert_profiles()
    assert len(profiles["experts"]) == 10
    t1 = staggered_next_run(uuid.UUID("00000000-0000-0000-0000-000000000001"), 168)
    t2 = staggered_next_run(uuid.UUID("00000000-0000-0000-0000-000000000999"), 168)
    assert t1 != t2
