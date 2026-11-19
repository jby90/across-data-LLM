"""Tests for the 6 quality & productization upgrades (2026-11-16):
1. Official PDF form overflow summary + automatic Continuation Annex (allow_annex / ?annex=true);
2. Admin template outline & preview studio compatibility;
3. Out-of-library third-country 5-point statutory verification checklist + single-PDF import guidance;
4. Document-type-differentiated prose profiles, multi-variant exemplars, and bilateral jurisdiction conflict protocol;
5. Core narrative field information-density precheck & scenario-tailored draft template;
6. Pre-submission manual sign-off checklist in project overview, standalone export, and formal export.zip?draft=false.
"""

from __future__ import annotations

import io
import zipfile

import pypdfium2 as pdfium

from app.analysis import document_narrative as nar
from app.analysis import project_delivery_aids as aids
from app.forms import service

from .conftest import bearer, login
from .test_analysis_p3 import _seed_sg_corpus

BASE = "/api/analysis/doc-projects"


def test_official_form_overflow_annex_preserves_default_guard_and_appends_continuation_sheet():
    f = service.get_form("MA_F118")
    long_mode = "Canal VPN IPsec chiffré de bout en bout avec gestion dédiée des clés HSM et journalisation intégrale " * 5
    answers = {
        "ctrl_type": "morale",
        "dest_kind": ["siege"],
        "consent": "oui",
        "file_name": "Données télématiques",
        "file_mode": long_mode[:380],
    }
    facts = {"company_name": "示例汽车科技有限公司", "recipient": "Siège Chine"}

    # Default mode still reports overflow without silently truncating
    _, _, too_long_default = service.render(f, answers, facts, allow_annex=False)
    assert [t["id"] for t in too_long_default] == ["file_mode"]

    # Annex mode places summary + reference in box and appends formal continuation page(s)
    pdf_bytes, report_annex, too_long_annex = service.render(f, answers, facts, allow_annex=True)
    assert not too_long_annex and not report_annex.overflow
    doc = pdfium.PdfDocument(pdf_bytes)
    assert len(doc) > 9  # 9 official CNDP pages + >=1 continuation annex page
    p4_txt = doc[3].get_textpage().get_text_range()
    annex_txt = "\n".join(doc[i].get_textpage().get_text_range() for i in range(9, len(doc)))
    doc.close()
    assert "Voir Annexe #1" in p4_txt or "Ann. #1" in p4_txt
    assert "官方申报表附页续表" in annex_txt
    assert "journalisation intégrale" in annex_txt


async def test_official_form_api_supports_annex_query_while_keeping_default_409(client, seed, session_factory):
    from app.analysis.models import AnalysisTask

    async with session_factory() as s:
        t = AnalysisTask(
            org_id=seed["org_a"].id,
            title="摩洛哥出境测试",
            country_code="MA",
            vehicle_model="X9",
            scenario_type="ota_diagnostics",
            facts_json={"company_name": "示例汽车科技有限公司", "doc_recipient": "Siège Chine"},
        )
        s.add(t)
        await s.commit()
        tid = str(t.id)

    auth = bearer(await login(client, "user", "a-admin@example.com"))
    iid = (await client.post("/api/forms/instances", json={"form_code": "MA_F118", "task_id": tid}, headers=auth)).json()["id"]
    long_addr = "Zone Industrielle Technopark Casablanca Route Nationale Numéro Un Bâtiment Central " * 4
    saved = await client.put(f"/api/forms/instances/{iid}/answers", json={"answers": {"ctrl_adresse": long_addr[:320]}}, headers=auth)
    assert saved.status_code == 200
    items = {i["id"]: i for sec in saved.json()["sections"] for i in sec["items"]}
    assert items["ctrl_adresse"]["store_chars"] == 400

    blocked = await client.get(f"/api/forms/instances/{iid}/export.pdf", headers=auth)
    assert blocked.status_code == 409

    ok = await client.get(f"/api/forms/instances/{iid}/export.pdf?annex=true", headers=auth)
    assert ok.status_code == 200 and ok.content[:5] == b"%PDF-"
    doc = pdfium.PdfDocument(ok.content)
    assert len(doc) >= 10
    doc.close()


def test_narrative_doctype_styles_exemplar_variants_and_bilateral_conflict_rules():
    sec_rcp = nar.Section(key="recipient_law", heading="三、接收国法律与实践评估", brief="评估接收国", facts=["recipient"])
    assert nar._resolve_doc_style({"doc_code": "tia", "source_country": "中国"}, sec_rcp) == "tia"
    assert nar._resolve_doc_style({"doc_code": "lia", "source_country": "摩洛哥"}, sec_rcp) == "lia"
    assert nar._resolve_doc_style({"doc_code": "risk_report", "source_country": "中国"}, sec_rcp) == "cn_official"
    assert nar._resolve_doc_style({"doc_code": "dpia", "source_country": "突尼斯"}, sec_rcp) == "dpia"

    # Exemplars and role guides must contain zero stray Arabic digits that could pollute number checks
    for role, variants in nar._EXEMPLAR_VARIANTS.items():
        assert len(variants) >= 2
        for v in variants:
            assert not nar._numbers(v), f"Unexpected digits in exemplar for {role}"
    assert "双边法域管辖冲突" in nar._EXEMPLAR_ROLE_GUIDES["recipient_and_remedy"]
    assert "排比八股句式" in nar.SYSTEM


async def test_density_precheck_third_country_checklist_and_signoff_package(client, seed, session_factory, monkeypatch):
    from .test_document_templates import _fake_paper_llm

    monkeypatch.setattr(nar, "call_llm_json", _fake_paper_llm())
    await _seed_sg_corpus(session_factory)
    auth = bearer(await login(client, "user", "a-member@example.com"))

    # 1. In-library route (CN -> SG): zero third-country checklist
    p_sg = (
        await client.post(
            BASE,
            headers=auth,
            json={"source_country": "中国", "recipient_country": "新加坡", "function": "ota_diagnostics", "vehicle_model": "星途 X7", "company_name": "华拓汽车有限公司"},
        )
    ).json()
    assert p_sg["third_country_checklists"] == []
    assert p_sg["signoff_summary"]["check_items_count"] == 10

    # 2. Out-of-library third-country route (CN -> DE): 5-point verification checklist + density precheck + formal package
    p_de = (
        await client.post(
            BASE,
            headers=auth,
            json={"source_country": "中国", "recipient_country": "德国", "function": "ota_diagnostics", "vehicle_model": "星途 X7", "company_name": "华拓汽车有限公司"},
        )
    ).json()
    pid = p_de["id"]
    assert len(p_de["third_country_checklists"]) == 1
    assert p_de["third_country_checklists"][0]["country"] == "德国"
    assert len(p_de["third_country_checklists"][0]["items"]) == 5

    # Fill a terse 3-char answer -> triggers density_hint without marking question missing
    updated = (await client.put(f"{BASE}/{pid}/answers", headers=auth, json={"answers": {"risk_measures": "已加密"}})).json()
    rm_q = next(q for q in updated["questions"] if q["key"] == "risk_measures")
    assert rm_q["density_hint"] is not None
    assert "加密" not in rm_q["density_hint"]["missing_facets"] or len(rm_q["density_hint"]["missing_facets"]) >= 2
    assert "【请按本企业实际技术与管理配置核对确认】" in rm_q["density_hint"]["draft_template"]
    assert updated["thin_count"] >= 1

    # Adopting comprehensive multi-facet text clears density_hint for risk_measures
    rich_rm = aids.evaluate_field_density("risk_measures", rm_q["density_hint"]["draft_template"])
    assert rich_rm is None

    # Preview & export standalone sign-off checklist and third-country checklist
    pv_sign = await client.get(f"{BASE}/{pid}/delivery-aid/signoff/preview", headers=auth)
    assert pv_sign.status_code == 200 and any("人工核签" in str(b.get("text", "")) for b in pv_sign.json()["blocks"])
    dl_tc = await client.get(f"{BASE}/{pid}/delivery-aid/third-country/export.docx", headers=auth)
    assert dl_tc.status_code == 200 and dl_tc.content[:2] == b"PK"

    # Complete project under exempt route so we can verify export.zip?draft=false includes 00_ signoff & 09_ third-country
    fill_all = {q["key"]: f"本企业已核实事项：{q['label']}符合远程故障诊断与安全加密留存管理规范。" for q in updated["questions"]}
    fill_all.update({
        "is_cii": "否",
        "important_data": "否",
        "sensitive_pi": "否",
        "data_volume": "今年已传输 0 人；未来三年预计累计不超过 3000 人（履行跨境购车与远程售后维修合同所必需）",
        "lawful_basis": "履行合同所必需：为海外购车用户提供售后远程故障诊断与软件修复服务所必需",
        "issues": "无",
        "risk_rating": '[{"id":"r1","risk":"跨境链路截获","likelihood":"低","severity":"中","measures":"双向证书鉴权加密通道与日志审计"}]',
    })
    ready_ov = (await client.put(f"{BASE}/{pid}/answers", headers=auth, json={"answers": fill_all})).json()
    for d in ready_ov["documents"]:
        if ready_ov["applicability"][d["code"]]["state"] != "na" and d["narrative"]:
            await client.post(f"{BASE}/{pid}/documents/{d['code']}/paper", headers=auth)
    import asyncio
    for _ in range(50):
        cur = (await client.get(f"{BASE}/{pid}", headers=auth)).json()
        if cur["final"]["ok"]:
            break
        await asyncio.sleep(0.1)
    assert cur["final"]["ok"] is True

    final_zip = await client.get(f"{BASE}/{pid}/export.zip?draft=false&fmt=docx", headers=auth)
    assert final_zip.status_code == 200
    znames = zipfile.ZipFile(io.BytesIO(final_zip.content)).namelist()
    assert any(n.startswith("00_") and "正式申报递交前人工核签清单" in n for n in znames)
    assert any(n.startswith("09_") and "库外法域属地法律待核查清单" in n for n in znames)
