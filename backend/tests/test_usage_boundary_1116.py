"""真实业务输入边界与管理端企业维护回归测试。"""

from __future__ import annotations

import pytest

from app.admission.models import AdmissionEntry, AdmissionStep
from app.analysis import document_narrative
from app.analysis.cn_route import judge, persons
from app.analysis.report_exporter import content_disposition
from app.audit import describe

from .conftest import bearer, login
from .test_document_templates import _fake_paper_llm


@pytest.fixture(autouse=True)
def _paper_model(monkeypatch):
    monkeypatch.setattr(document_narrative, "call_llm_json", _fake_paper_llm())


def test_cn_route_negative_persons_never_treated_as_exempt():
    assert persons("-100") is None
    assert persons("-50000 人") is None
    assert persons("预计：-10 人") is None
    assert persons("5000 人") == 5000
    base = {"is_cii": "否", "important_data": "否", "sensitive_pi": "否"}
    assert judge({**base, "data_volume": "-100"})["route"] == "unknown"
    assert judge({**base, "data_volume": "abc"})["route"] == "unknown"
    assert judge({**base, "data_volume": "5000 人"})["route"] == "exempt"


def test_content_disposition_ascii_fallback_strips_trailing_underscores():
    cd1 = content_disposition("privacy_notice_morocco_draft_制度对照审查.pdf")
    assert 'filename="privacy_notice_morocco_draft.pdf"' in cd1
    cd2 = content_disposition("审计记录.xlsx")
    assert 'filename="export.xlsx"' in cd2
    cd3 = content_disposition("审计记录_20261116_0830.xlsx")
    assert 'filename="20261116_0830.xlsx"' in cd3


def test_audit_readable_translates_approval_fields_to_chinese():
    rendered = describe.readable(
        {
            "number": "CNDP-2026-8899",
            "issued_on": "2026-11-16",
            "expires_on": "2027-11-16",
            "long_term": False,
        }
    )
    assert "批文编号：CNDP-2026-8899" in rendered
    assert "签发日期：2026-11-16" in rendered
    assert "有效期至：2027-11-16" in rendered
    assert "长期有效：否" in rendered
    assert "expires_on" not in rendered
    assert "issued_on" not in rendered


async def test_doc_project_rejects_same_country_and_invalid_counts_and_localizes_non_cn_storage_label(
    client, seed
):
    auth = bearer(await login(client, "user", "a-member@example.com"))

    same = await client.post(
        "/api/analysis/doc-projects",
        headers=auth,
        json={
            "source_country": "中国",
            "recipient_country": "中国",
            "function": "connected_vehicle_cloud",
        },
    )
    assert same.status_code == 422
    assert "不能相同" in same.json()["detail"]

    cn_proj = await client.post(
        "/api/analysis/doc-projects",
        headers=auth,
        json={
            "source_country": "中国",
            "recipient_country": "新加坡",
            "function": "connected_vehicle_cloud",
        },
    )
    assert cn_proj.status_code == 201
    pid = cn_proj.json()["id"]

    neg = await client.put(
        f"/api/analysis/doc-projects/{pid}/answers",
        headers=auth,
        json={"answers": {"data_volume": "-100"}},
    )
    assert neg.status_code == 422

    alpha = await client.put(
        f"/api/analysis/doc-projects/{pid}/answers",
        headers=auth,
        json={"answers": {"data_volume": "abc"}},
    )
    assert alpha.status_code == 422

    valid = await client.put(
        f"/api/analysis/doc-projects/{pid}/answers",
        headers=auth,
        json={
            "answers": {
                "is_cii": "否",
                "important_data": "否",
                "sensitive_pi": "否",
                "data_volume": "约 5 万人",
            }
        },
    )
    assert valid.status_code == 200
    assert valid.json()["cn_route"]["route"] == "exempt"

    tn_proj = await client.post(
        "/api/analysis/doc-projects",
        headers=auth,
        json={
            "source_country": "突尼斯",
            "recipient_country": "埃及",
            "function": "connected_vehicle_cloud",
        },
    )
    assert tn_proj.status_code == 201
    q_labels = {q["key"]: q["label"] for q in tn_proj.json()["questions"]}
    assert q_labels["storage_chain"] == "突尼斯本地存储与传输链路"

    # Admin execution trace enrichment includes verified_passed_count / verified_failed_count
    from app.analysis.models import AnalysisStepTrace, AnalysisTask
    from app.analysis.router import _enrich_trace_snapshots

    dummy_task = AnalysisTask(
        country_code="SG",
        vehicle_model="S7",
        scenario_type="connected_vehicle_cloud",
        findings_json=[{"dimension_code": "D1", "obligation_summary": "履行事先告知义务"}],
    )
    dummy_trace = AnalysisStepTrace(
        phase="verification",
        input_snapshot_json={},
        output_snapshot_json={"kept_sentences": 18, "dropped_sentences": 1},
    )
    _, enriched_out = _enrich_trace_snapshots(dummy_task, dummy_trace, [], [])
    assert enriched_out["verified_passed_count"] == 18
    assert enriched_out["verified_failed_count"] == 1
    assert enriched_out["verification_items"][0]["passed"] is True


async def test_admission_approval_rejects_expiry_before_issue_date(
    client, seed, session_factory
):
    async with session_factory() as s:
        e = AdmissionEntry(
            country_code="SG",
            name_zh="自动驾驶道路测试许可",
            category="av_testing",
            authority_zh="新加坡陆路交通管理局（LTA）",
            summary_zh="道路测试前应取得授权。",
            profile={"cited_laws": ["2017 年道路交通（自动驾驶机动车）规则"]},
            edited=["profile:cited_laws"],
        )
        s.add(e)
        await s.flush()
        s.add(
            AdmissionStep(
                entry_id=e.id,
                seq=1,
                title_zh="提交申请",
                detail_zh="向主管机关递交申请",
                materials=["申请书"],
                evidence_quote="application",
            )
        )
        await s.commit()
        entry_id = str(e.id)

    auth = bearer(await login(client, "user", "a-admin@example.com"))
    created = await client.post(
        "/api/admission/projects",
        headers=auth,
        json={"entry_id": entry_id, "title": "新加坡测试项目"},
    )
    assert created.status_code == 201
    proj_id = created.json()["id"]

    bad = await client.put(
        f"/api/admission/projects/{proj_id}/approval",
        headers=auth,
        json={
            "number": "LTA-AV-2026-01",
            "issued_on": "2026-11-16",
            "expires_on": "2020-01-01",
            "long_term": False,
            "note": "",
        },
    )
    assert bad.status_code == 422
    assert "不能早于签发日期" in bad.json()["detail"]

    good = await client.put(
        f"/api/admission/projects/{proj_id}/approval",
        headers=auth,
        json={
            "number": "LTA-AV-2026-01",
            "issued_on": "2026-11-16",
            "expires_on": "2027-11-16",
            "long_term": False,
            "note": "",
        },
    )
    assert good.status_code == 200


async def test_admin_org_rename_and_deactivate_reactivate_lifecycle(client, seed):
    admin_auth = bearer(await login(client, "admin", "admin@example.com"))
    created = await client.post(
        "/api/admin/orgs",
        headers=admin_auth,
        json={"name": "星瀚智能网联汽车有限公司"},
    )
    assert created.status_code == 201
    org_id = created.json()["id"]

    renamed = await client.patch(
        f"/api/admin/orgs/{org_id}",
        headers=admin_auth,
        json={"name": "星瀚智能网联汽车（上海）有限公司"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "星瀚智能网联汽车（上海）有限公司"

    deactivated = await client.patch(
        f"/api/admin/orgs/{org_id}",
        headers=admin_auth,
        json={"is_active": False},
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False

    active_list = await client.get("/api/admin/orgs", headers=admin_auth)
    assert org_id not in {o["id"] for o in active_list.json()}

    all_list = await client.get(
        "/api/admin/orgs?include_inactive=true", headers=admin_auth
    )
    assert org_id in {o["id"] for o in all_list.json()}

    reactivated = await client.patch(
        f"/api/admin/orgs/{org_id}",
        headers=admin_auth,
        json={"is_active": True},
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["is_active"] is True


def test_docx_exports_continue_page_numbers_across_sections_and_enable_update_fields():
    import io
    import zipfile
    from xml.etree import ElementTree as ET

    from app.analysis import official_docx as od
    from app.analysis.document_templates import Rendered, build_docx

    # 1. Multi-section CAC Annex 5 PIA: only sect[0] has w:start="1", subsequent sections continue numbering,
    #    and every footer*.xml has a native dirty PAGE field with updateFields enabled in settings.xml.
    pia_res = od.cn_pia(
        {
            "company_name": "启衡智行汽车科技（上海）有限公司",
            "purpose": "跨境座舱语音适配",
            "data_types": "假名化文本",
        },
        blank="【待补充】",
    )
    with zipfile.ZipFile(io.BytesIO(pia_res.data)) as zf:
        doc_root = ET.fromstring(zf.read("word/document.xml"))
        sects = list(doc_root.iter(od.W + "sectPr"))
        assert len(sects) >= 1
        pg0 = sects[0].find(od.W + "pgNumType")
        assert pg0 is not None and pg0.get(od.W + "start") == "1"
        for later in sects[1:]:
            pg = later.find(od.W + "pgNumType")
            if pg is not None:
                assert pg.get(od.W + "start") is None
        assert b"updateFields" in zf.read("word/settings.xml")
        ftrs = [n for n in zf.namelist() if n.startswith("word/footer") and n.endswith(".xml")]
        assert ftrs
        for fn in ftrs:
            raw_f = zf.read(fn)
            assert b"PAGE" in raw_f and b'dirty="true"' in raw_f and b"txbx" not in raw_f

    # Multi-section body helper test: sect[0] starts at 1, sect[1..2] strip w:start
    multi_body = ET.fromstring(
        f'<w:body xmlns:w="{od.W_NS}">'
        '<w:p><w:pPr><w:sectPr><w:pgNumType w:start="1"/></w:sectPr></w:pPr></w:p>'
        '<w:p><w:pPr><w:sectPr><w:pgNumType w:start="1"/></w:sectPr></w:pPr></w:p>'
        '<w:sectPr><w:pgNumType w:start="5"/></w:sectPr>'
        "</w:body>"
    )
    od._reset_sect_page_start(multi_body)
    m_sects = list(multi_body.iter(od.W + "sectPr"))
    assert m_sects[0].find(od.W + "pgNumType").get(od.W + "start") == "1"
    assert m_sects[1].find(od.W + "pgNumType").get(od.W + "start") is None
    assert m_sects[2].find(od.W + "pgNumType").get(od.W + "start") is None

    # 2. Official declaration preview grid uses no_head=True instead of fake "栏目 1..N" headers
    decl_res = od.cn_declaration({"company_name": "启衡智行汽车科技（上海）有限公司"})
    blocks = od.docx_preview_blocks(decl_res.data)
    tbls = [b for b in blocks if b["type"] == "table"]
    assert tbls and tbls[0].get("no_head") is True
    assert not any("栏目 " in str(h) for t in tbls for h in t.get("head", []))

    # 3. Standard report_exporter docx sets dirty="true" on PAGE/NUMPAGES and writes word/settings.xml
    std_docx = build_docx(
        Rendered(blocks=[{"type": "title", "text": "测试报告"}, {"type": "paragraph", "text": "正文段落。"}]),
        "测试报告",
    )
    with zipfile.ZipFile(io.BytesIO(std_docx)) as zf:
        assert "word/settings.xml" in zf.namelist()
        assert b"updateFields" in zf.read("word/settings.xml")
        ftr = zf.read("word/footer1.xml")
        assert b"PAGE" in ftr and b"NUMPAGES" in ftr and b"dirty='true'" in ftr

