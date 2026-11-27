# ruff: noqa: E501
"""Regression tests for 2026-11-27 Ultimate Multi-Role Dashboards & Whitepaper Sync Guard:
1. Role-isolated posture endpoints:
   - `/api/admin/cockpit/overview` remains super_admin-only (`403` for `ops_admin`);
   - `/api/admin/knowledge/posture` serves global knowledge & site-patrol posture to BOTH `platform_admin` and `ops_admin` while strictly zeroing/excluding tenant orgs, users, enterprise projects, and audit counts;
   - `/api/knowledge/posture-summary` serves public regulatory coverage counts to `member`, `viewer`, and portal trial accounts (`401` for anonymous).
2. `POST /api/admin/knowledge/whitepapers/{cc}/sync-dimensions` requires explicit `dimensions` (`422` on empty `{}` body).
"""

from __future__ import annotations

from app.iam import service as iam_service
from app.iam.models import Role

from .conftest import PASSWORD, bearer, login


async def test_multi_role_cockpit_posture_isolation_and_sync_guard(client, seed, session_factory):
    super_tok = await login(client, "admin", "admin@example.com")
    async with session_factory() as s:
        await iam_service.create_user(
            s,
            email="ops_1127@example.com",
            display_name="运维管理员1127",
            password=PASSWORD,
            role=Role.OPS_ADMIN,
            org_id=None,
        )
        await iam_service.create_user(
            s,
            email="viewer_1127@example.com",
            display_name="企业查看者1127",
            password=PASSWORD,
            role=Role.VIEWER,
            org_id=seed["org_a"].id,
            can_export=False,
        )
        await s.commit()

    ops_tok = await login(client, "admin", "ops_1127@example.com")
    member_tok = await login(client, "user", "a-member@example.com")
    viewer_tok = await login(client, "user", "viewer_1127@example.com")

    # 1. Super-admin cockpit stays strictly super_admin-only
    assert (await client.get("/api/admin/cockpit/overview", headers=bearer(ops_tok))).status_code == 403
    assert (await client.get("/api/admin/cockpit/overview", headers=bearer(super_tok))).status_code == 200

    # 2. Ops-admin & super-admin both access knowledge-only posture without tenant/audit leakage
    assert (await client.get("/api/admin/knowledge/posture")).status_code == 401
    assert (await client.get("/api/admin/knowledge/posture", headers=bearer(member_tok))).status_code == 401

    r_ops_posture = await client.get("/api/admin/knowledge/posture", headers=bearer(ops_tok))
    assert r_ops_posture.status_code == 200, r_ops_posture.text
    ops_data = r_ops_posture.json()
    assert len(ops_data["kg_node_breakdown"]) == 6
    assert ops_data["headline"]["active_organizations"] == 0
    assert ops_data["headline"]["doc_projects_total"] == 0
    assert ops_data["operations"]["audit_events_7d"] == 0
    assert ops_data["routes"] == []

    # 3. User-side public regulatory capability summary works for both member and viewer
    assert (await client.get("/api/knowledge/posture-summary")).status_code == 401
    for tok in (member_tok, viewer_tok):
        r_sum = await client.get("/api/knowledge/posture-summary", headers=bearer(tok))
        assert r_sum.status_code == 200, r_sum.text
        body = r_sum.json()
        assert isinstance(body["covered_jurisdictions"], int)
        assert isinstance(body["official_forms_total"], int) and body["official_forms_total"] >= 20
        assert isinstance(body["countries"], list)

    # 4. Empty body `{}` on sync-dimensions is rejected with 422 before spawning background task
    r_empty_sync = await client.post(
        "/api/admin/knowledge/whitepapers/SG/sync-dimensions",
        headers=bearer(ops_tok),
        json={},
    )
    assert r_empty_sync.status_code == 422


def test_scenario_filter_blocks_unknown_internal_codes_while_preserving_custom_labels_and_dynamic_fonts():
    from types import SimpleNamespace

    from app.analysis.documents_router import _scenario, task_functions
    from app.analysis.pdf_renderer import report_html

    # Unknown internal snake_case code must never leak into formal compliance documents
    assert _scenario(SimpleNamespace(scenario_type="data_return", facts_json={})) == {"scenario": ""}
    assert task_functions(SimpleNamespace(scenario_type="unknown_internal_slug", facts_json={})) == []

    # Preset code + custom Chinese business scenarios must both render cleanly
    mixed = SimpleNamespace(
        scenario_type="ota_diagnostics",
        facts_json={"_functions": ["ota_diagnostics", "跨境车队碳排监测", "unmapped_slug"]},
    )
    assert task_functions(mixed) == ["ota_diagnostics", "跨境车队碳排监测"]
    scen_txt = _scenario(mixed)["scenario"]
    assert "远程诊断" in scen_txt and "跨境车队碳排监测" in scen_txt and "unmapped_slug" not in scen_txt

    # Standard Chinese/English/Arabic report loads only 3 core @font-face rules; Korean/Thai loads on demand
    html_base = report_html(
        SimpleNamespace(country_code="CN", vehicle_model="QH-S9"),
        SimpleNamespace(consulting=None),
        dimension=None,
        context="核验",
        country="中国",
        notices=[],
        blocks=[{"t": "p", "text": "中文与 English 及 العربية 混排"}],
    )
    assert html_base.count("@font-face") == 3

    html_kr_th = report_html(
        SimpleNamespace(country_code="KR", vehicle_model="QH-S9"),
        SimpleNamespace(consulting=None),
        dimension=None,
        context="核验",
        country="韩国",
        notices=[],
        blocks=[{"t": "p", "text": "한국어 개인정보 · ภาษาไทย ข้อมูล"}],
    )
    assert html_kr_th.count("@font-face") == 5 and "notosanskr" in html_kr_th and "notosansthai" in html_kr_th
