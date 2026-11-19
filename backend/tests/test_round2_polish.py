"""Tests for Round 2 pre-demo polish:
1. Dynamic overseas country support beyond the initial 5 countries (e.g. JP / DE);
2. Overseas admission foreign-language formal application letter (外文正式稿), programmatic
   reverse verification (zero CJK + exact code/number preservation + DOCX/PDF export cleanliness),
   and viewer export permission enforcement;
3. Ops-admin access to intake-quality references and whitepaper inspection endpoints.
"""

from __future__ import annotations

import io
import json
import re
import zipfile

from app.admission.models import AdmissionEntry, AdmissionStep
from app.analysis.chat import countries_in
from app.analysis.llm import LlmResult
from app.analysis.workflow_engine import is_overseas_country
from app.iam import service as iam_service
from app.iam.models import Role
from app.ingestion.models import Document
from app.knowledge.whitepaper import CountryWhitepaper
from app.sources.models import SourceSite

from .conftest import PASSWORD, bearer, login


async def test_dynamic_overseas_country_in_whitepapers_and_chat(
    client, seed, session_factory
):
    assert is_overseas_country("JP") is True
    assert is_overseas_country("DE") is True
    assert is_overseas_country("CN") is False
    assert set(countries_in("车联网数据计划发往日本和德国")) == {"JP", "DE"}

    async with session_factory() as s:
        site = SourceSite(
            country_code="JP",
            publisher="Digital Agency",
            entry_url="https://laws.e-gov.go.jp",
            language="ja",
            document_type="LAW",
            enabled=True,
            terms_status="approved",
        )
        s.add(site)
        await s.flush()
        doc = Document(
            site_id=site.id,
            country_code="JP",
            title="個人情報の保護に関する法律",
            title_zh="日本个人信息保护法",
            document_type="LAW",
            language="ja",
            source_url="https://laws.e-gov.go.jp/law/415AC0000000057",
        )
        s.add(doc)
        s.add(
            CountryWhitepaper(
                country_code="JP",
                version_no=1,
                source_hash="jp-hash-1",
                blocks=[{"type": "title", "text": "日本汽车数据合规简报"}],
                report={"dimensions": []},
                tokens=100,
            )
        )
        await s.commit()

    u_hdr = bearer(await login(client, "user", "a-admin@example.com"))
    a_hdr = bearer(await login(client, "admin", "admin@example.com"))

    u_list = await client.get("/api/knowledge/whitepapers", headers=u_hdr)
    assert u_list.status_code == 200
    jp_row = next((r for r in u_list.json() if r["country_code"] == "JP"), None)
    assert jp_row is not None and jp_row["country_name_zh"] == "日本" and jp_row["available"] is True

    a_list = await client.get("/api/admin/knowledge/whitepapers", headers=a_hdr)
    assert a_list.status_code == 200
    assert any(r["country_code"] == "JP" and r["version_no"] == 1 for r in a_list.json())

    jp_detail = await client.get("/api/knowledge/whitepapers/JP", headers=u_hdr)
    assert jp_detail.status_code == 200
    assert jp_detail.json()["country_name_zh"] == "日本"
    assert (await client.get("/api/knowledge/whitepapers/CN", headers=u_hdr)).status_code == 404


async def test_admission_foreign_formal_letter_reverse_verification_and_permissions(
    client, seed, session_factory, monkeypatch
):
    async with session_factory() as s:
        entry = AdmissionEntry(
            country_code="SG",
            name_zh="自动驾驶道路试验授权",
            category="av_testing",
            authority_zh="新加坡陆路交通管理局",
            summary_zh="上路试验前须获授权。",
            profile={
                "language": "英语",
                "cited_laws": ["2017 年道路交通（自动驾驶机动车）规则"],
            },
            edited=["profile:cited_laws"],
        )
        s.add(entry)
        await s.flush()
        s.add(
            AdmissionStep(
                entry_id=entry.id,
                seq=1,
                title_zh="提交申请",
                detail_zh="向陆路交通管理局提交申请书与安全方案",
                materials=["安全评估方案"],
                evidence_quote="must be made to the Authority",
            )
        )
        await iam_service.create_user(
            s,
            email="viewer-noexp@example.com",
            display_name="只读无导出",
            password=PASSWORD,
            role=Role.VIEWER,
            org_id=seed["org_a"].id,
            can_export=False,
        )
        await iam_service.create_user(
            s,
            email="viewer-exp@example.com",
            display_name="只读可导出",
            password=PASSWORD,
            role=Role.VIEWER,
            org_id=seed["org_a"].id,
            can_export=True,
        )
        await s.commit()
        entry_id = str(entry.id)

    member_hdr = bearer(await login(client, "user", "a-admin@example.com"))
    v_noexp_hdr = bearer(await login(client, "user", "viewer-noexp@example.com"))
    v_exp_hdr = bearer(await login(client, "user", "viewer-exp@example.com"))

    proj_resp = await client.post(
        "/api/admission/projects",
        json={
            "entry_id": entry_id,
            "applicant": "Qihang Auto (Singapore) Pte. Ltd.",
            "vehicle_model": "QH-E7-2026",
        },
        headers=member_hdr,
    )
    assert proj_resp.status_code == 201
    pid = proj_resp.json()["id"]

    detail = (await client.get(f"/api/admission/projects/{pid}", headers=member_hdr)).json()
    assert detail["foreign_lang_code"] == "en"
    assert detail["foreign_lang_zh"] == "英文"

    draft_resp = await client.post(f"/api/admission/projects/{pid}/drafts", headers=member_hdr)
    assert draft_resp.status_code == 201
    did = draft_resp.json()["id"]

    # Viewer cannot trigger generation
    assert (await client.post(f"/api/admission/drafts/{did}/foreign", headers=v_exp_hdr)).status_code == 403
    assert (await client.get(f"/api/admission/drafts/{did}/foreign", headers=member_hdr)).status_code == 404

    calls: list[dict] = []

    async def fake_call_llm_json(messages, **kwargs):
        user_msg = json.loads(messages[-1]["content"])
        calls.append(user_msg)
        src = user_msg["blocks"]

        def _translate_blk(b, *, leak_bad: bool):
            t = b["type"]
            if t == "title":
                return {"type": "title", "text": "自动驾驶 Application" if leak_bad else "Application for Autonomous Vehicle Road Trial Authorization"}
            if t == "heading":
                return {"type": "heading", "text": "Section Heading"}
            if t == "paragraph":
                return {"type": "paragraph", "text": "To: Land Transport Authority of Singapore. The applicant hereby applies for authorization."}
            if t == "list":
                return {"type": "list", "items": ["Item " + str(i) for i, _ in enumerate(b["items"], 1)]}
            if t == "table":
                head = ["Item" if h else "" for h in b["head"]]
                rows = []
                for r in b["rows"]:
                    new_r = []
                    for c in r:
                        s = str(c)
                        if "QH-E7-2026" in s:
                            new_r.append("WRONG-MODEL" if leak_bad else "QH-E7-2026")
                        elif "Qihang Auto" in s:
                            new_r.append("Qihang Auto (Singapore) Pte. Ltd.")
                        elif "待补充" in s or "待企业填写" in s:
                            new_r.append("[To be completed]")
                        elif re.fullmatch(r"[　\s年月日]+", s):
                            new_r.append("____ / ____ / ________")
                        elif not s.strip():
                            new_r.append("")
                        elif s.strip().isdigit():
                            new_r.append(s.strip())
                        else:
                            new_r.append("Supporting Specification Document")
                    rows.append(new_r)
                return {"type": "table", "head": head, "rows": rows}
            return b

        # First attempt deliberately leaks Chinese and drops model code QH-E7-2026;
        # second attempt fixes both after receiving fix_verification_errors.
        bad = len(calls) == 1
        out_blocks = [_translate_blk(b, leak_bad=bad) for b in src]
        return LlmResult(
            data={"blocks": out_blocks},
            prompt_tokens=200,
            completion_tokens=220,
            duration_ms=50,
            model="test-model",
        )

    monkeypatch.setattr("app.admission.foreign_letter.call_llm_json", fake_call_llm_json)

    gen = await client.post(f"/api/admission/drafts/{did}/foreign", headers=member_hdr)
    assert gen.status_code == 200, gen.text
    assert len(calls) == 2
    assert "fix_verification_errors" in calls[1]
    body = gen.json()
    assert body["verified"] is True
    assert body["lang_zh"] == "英文"
    assert not re.search(r"[\u4e00-\u9fff]", json.dumps(body["blocks"], ensure_ascii=False))
    assert "QH-E7-2026" in json.dumps(body["blocks"], ensure_ascii=False)

    # Viewer can read generated foreign draft, export obeys can_export
    assert (await client.get(f"/api/admission/drafts/{did}/foreign", headers=v_noexp_hdr)).status_code == 200
    assert (await client.get(f"/api/admission/drafts/{did}/foreign/export.docx", headers=v_noexp_hdr)).status_code == 403
    docx_resp = await client.get(f"/api/admission/drafts/{did}/foreign/export.docx", headers=v_exp_hdr)
    assert docx_resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(docx_resp.content)) as zf:
        for xml_name in ("word/document.xml", "word/header1.xml", "word/footer1.xml"):
            xml_text = zf.read(xml_name).decode("utf-8")
            assert not re.search(r"[\u4e00-\u9fff]", xml_text), f"CJK leaked in {xml_name}"
        assert "QH-E7-2026" in zf.read("word/document.xml").decode("utf-8")

    pdf_resp = await client.get(f"/api/admission/drafts/{did}/foreign/export.pdf", headers=v_exp_hdr)
    assert pdf_resp.status_code == 200 and pdf_resp.content.startswith(b"%PDF-")


async def test_ops_admin_can_inspect_quality_references_and_whitepapers_readonly(
    client, seed, session_factory
):
    async with session_factory() as s:
        await iam_service.create_user(
            s,
            email="ops-test@example.com",
            display_name="运维测试",
            password=PASSWORD,
            role=Role.OPS_ADMIN,
            org_id=None,
        )
        await s.commit()

    ops_hdr = bearer(await login(client, "admin", "ops-test@example.com"))
    assert (await client.get("/api/admin/quality-cards", headers=ops_hdr)).status_code == 200
    assert (await client.get("/api/admin/quality-cards/references", headers=ops_hdr)).status_code == 200
    assert (await client.get("/api/admin/knowledge/whitepapers", headers=ops_hdr)).status_code == 200
    # Updating whitepaper requires platform_admin (super_only)
    assert (await client.post("/api/admin/knowledge/whitepapers/SG/update", headers=ops_hdr)).status_code == 403
    # Super-admin-only orgs route remains blocked for ops_admin
    assert (await client.get("/api/admin/orgs", headers=ops_hdr)).status_code == 403
