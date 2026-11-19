import time

from app.audit.service import classify, wants_record
from app.core.security import create_portal_ticket
from app.iam.deps import _DOWNLOAD

from .conftest import PASSWORD, bearer, login


async def test_portal_self_register_defaults_to_readonly_viewer(client, seed):
    res = await client.post(
        "/api/auth/user/register",
        json={
            "email": "trial-visitor@new-oem.example.com",
            "display_name": "张法务",
            "org_name": "新注册整车体验单位有限公司",
            "password": "TrialPassword!2026",
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["role"] == "viewer"
    token = body["access_token"]

    # Duplicate registration rejected with 409
    dup = await client.post(
        "/api/auth/user/register",
        json={
            "email": "trial-visitor@new-oem.example.com",
            "display_name": "张法务",
            "org_name": "新注册整车体验单位有限公司",
            "password": "TrialPassword!2026",
        },
    )
    assert dup.status_code == 409

    # Self account profile reflects viewer + portal + can_export=False
    prof = await client.get("/api/auth/account", headers=bearer(token))
    assert prof.status_code == 200
    pdata = prof.json()
    assert pdata["display_name"] == "张法务"
    assert pdata["org_name"] == "新注册整车体验单位有限公司"
    assert pdata["role"] == "viewer"
    assert pdata["role_zh"] == "普通查看者"
    assert pdata["signup_source"] == "portal"
    assert pdata["can_export"] is False

    # Cannot create evaluation project or export documents
    create_proj = await client.post(
        "/api/analysis/doc-projects",
        headers=bearer(token),
        json={"name": "越权新建项目", "source_country": "CN", "recipient_country": "SG"},
    )
    assert create_proj.status_code == 403

    exp_edpb = await client.get(
        "/api/analysis/tasks/00000000-0000-0000-0000-000000000001/documents/dpia/export-edpb-en.docx",
        headers=bearer(token),
    )
    assert exp_edpb.status_code == 403


async def test_portal_self_register_tenant_isolation_blocks_existing_enterprise_spoofing(
    client, seed
):
    # Enterprise org_a creates a confidential compliance project
    org_a_member_tok = await login(client, "user", "a-member@example.com")
    mk_proj = await client.post(
        "/api/analysis/doc-projects",
        headers=bearer(org_a_member_tok),
        json={
            "source_country": "中国",
            "recipient_country": "新加坡",
            "function": "remote_diag",
            "scenario_text": "签约车企内部机密车载诊断数据跨境评估",
            "vehicle_model": "CONF-X9",
        },
    )
    assert mk_proj.status_code in (200, 201), mk_proj.text
    secret_proj_id = mk_proj.json()["id"]

    # External visitor self-registers using org_a's exact full name
    spoof = await client.post(
        "/api/auth/user/register",
        json={
            "email": f"spoof-visitor-{int(time.time())}@external.example.com",
            "display_name": "外部访客",
            "org_name": seed["org_a"].name,
            "password": "TrialPassword!2026",
        },
    )
    assert spoof.status_code == 201, spoof.text
    sbody = spoof.json()
    spoof_tok = sbody["access_token"]
    assert str(sbody["org_id"]) != str(seed["org_a"].id)

    # Own profile still displays clean org_name without sandbox suffix
    prof = await client.get("/api/auth/account", headers=bearer(spoof_tok))
    assert prof.status_code == 200
    assert prof.json()["org_name"] == seed["org_a"].name
    assert str(prof.json()["org_id"]) != str(seed["org_a"].id)

    # Cannot see org_a's confidential projects in list or detail
    proj_list = await client.get("/api/analysis/doc-projects", headers=bearer(spoof_tok))
    assert proj_list.status_code == 200
    listed_ids = {p["id"] for p in proj_list.json()}
    assert secret_proj_id not in listed_ids

    proj_detail = await client.get(
        f"/api/analysis/doc-projects/{secret_proj_id}", headers=bearer(spoof_tok)
    )
    assert proj_detail.status_code == 404

    # Super admin /api/admin/orgs is not polluted by trial sandbox organizations
    admin_tok = await login(client, "admin", "admin@example.com")
    orgs_res = await client.get("/api/admin/orgs", headers=bearer(admin_tok))
    assert orgs_res.status_code == 200
    orgs_data = orgs_res.json()
    assert not any("（体验空间-" in o["name"] for o in orgs_data)
    assert not any(str(o["id"]) == str(sbody["org_id"]) for o in orgs_data)


async def test_portal_sso_ticket_exchange_tiers_and_security(client, seed):
    # 1. Standard tier -> viewer
    t_std = create_portal_ticket(
        email="sso-std@portal-oem.example.com",
        display_name="王专员",
        org_name="门户单点体验车企有限公司",
        tier="standard",
    )
    r1 = await client.post("/api/auth/portal/exchange", json={"ticket": t_std})
    assert r1.status_code == 200, r1.text
    assert r1.json()["role"] == "viewer"

    # 2. Enterprise tier -> member
    t_ent = create_portal_ticket(
        email="sso-ent@portal-oem.example.com",
        display_name="李总监",
        org_name="门户签约车企集团股份有限公司",
        tier="enterprise",
    )
    r2 = await client.post("/api/auth/portal/exchange", json={"ticket": t_ent})
    assert r2.status_code == 200, r2.text
    assert r2.json()["role"] == "member"

    # 3. Tampered & expired tickets -> 401
    bad_sig = t_std[:-2] + ("aa" if not t_std.endswith("aa") else "bb")
    assert (await client.post("/api/auth/portal/exchange", json={"ticket": bad_sig})).status_code == 401

    expired = create_portal_ticket(
        email="sso-exp@portal-oem.example.com",
        display_name="过期用户",
        org_name="门户单点体验车企有限公司",
        ttl_seconds=-10,
    )
    assert (await client.post("/api/auth/portal/exchange", json={"ticket": expired})).status_code == 401


async def test_self_account_settings_and_admin_account_controls(client, seed):
    # Admin can update own profile name
    admin_tok = await login(client, "admin", "admin@example.com")
    r_adm = await client.patch(
        "/api/auth/account",
        headers=bearer(admin_tok),
        json={"display_name": "平台超级管理员"},
    )
    assert r_adm.status_code == 200
    assert r_adm.json()["display_name"] == "平台超级管理员"

    # Create a dedicated test viewer under org_a, test self password change & admin disable/reset
    create_u = await client.post(
        "/api/admin/users",
        headers=bearer(admin_tok),
        json={
            "email": f"acct-{int(time.time())}@example.com",
            "display_name": "初始姓名",
            "password": PASSWORD,
            "role": "viewer",
            "org_id": str(seed["org_a"].id),
            "can_export": False,
        },
    )
    assert create_u.status_code == 201
    u_id = create_u.json()["id"]
    u_email = create_u.json()["email"]

    u_tok = await login(client, "user", u_email)
    # Viewer can update own display_name & password via /api/auth/*
    upd_self = await client.patch(
        "/api/auth/account",
        headers=bearer(u_tok),
        json={"display_name": "陈查看员"},
    )
    assert upd_self.status_code == 200
    assert upd_self.json()["display_name"] == "陈查看员"

    bad_pw = await client.post(
        "/api/auth/password",
        headers=bearer(u_tok),
        json={"old_password": "WrongOldPassword!123", "new_password": "BrandNewPassword!2026"},
    )
    assert bad_pw.status_code == 400

    ok_pw = await client.post(
        "/api/auth/password",
        headers=bearer(u_tok),
        json={"old_password": PASSWORD, "new_password": "BrandNewPassword!2026"},
    )
    assert ok_pw.status_code == 200

    # Old password rejected, new password works
    assert (
        await client.post("/api/auth/user/login", json={"email": u_email, "password": PASSWORD})
    ).status_code == 401
    assert (
        await client.post(
            "/api/auth/user/login", json={"email": u_email, "password": "BrandNewPassword!2026"}
        )
    ).status_code == 200

    # Admin cannot disable self
    self_me = (await client.get("/api/admin/me", headers=bearer(admin_tok))).json()
    no_self_disable = await client.patch(
        f"/api/admin/users/{self_me['id']}",
        headers=bearer(admin_tok),
        json={"is_active": False},
    )
    assert no_self_disable.status_code == 422

    # Admin disables test user -> login blocked; re-enables + resets password -> login works
    dis = await client.patch(
        f"/api/admin/users/{u_id}",
        headers=bearer(admin_tok),
        json={"is_active": False},
    )
    assert dis.status_code == 200 and dis.json()["is_active"] is False
    assert (
        await client.post(
            "/api/auth/user/login", json={"email": u_email, "password": "BrandNewPassword!2026"}
        )
    ).status_code == 401

    reen = await client.patch(
        f"/api/admin/users/{u_id}",
        headers=bearer(admin_tok),
        json={"is_active": True, "new_password": "AdminResetPassword!2026"},
    )
    assert reen.status_code == 200 and reen.json()["is_active"] is True
    assert (
        await client.post(
            "/api/auth/user/login", json={"email": u_email, "password": "AdminResetPassword!2026"}
        )
    ).status_code == 200


async def test_cockpit_overview_super_admin_only_and_real_counts(client, seed):
    admin_tok = await login(client, "admin", "admin@example.com")
    ops_email = f"ops-{int(time.time())}@example.com"
    mk_ops = await client.post(
        "/api/admin/users",
        headers=bearer(admin_tok),
        json={
            "email": ops_email,
            "display_name": "运维管理员",
            "password": PASSWORD,
            "role": "ops_admin",
            "org_id": None,
            "can_export": False,
        },
    )
    assert mk_ops.status_code == 201
    ops_tok = await login(client, "admin", ops_email)
    user_tok = await login(client, "user", "a-admin@example.com")

    assert (await client.get("/api/admin/cockpit/overview")).status_code == 401
    assert (await client.get("/api/admin/cockpit/overview", headers=bearer(user_tok))).status_code == 401
    assert (await client.get("/api/admin/cockpit/overview", headers=bearer(ops_tok))).status_code == 403

    ok = await client.get("/api/admin/cockpit/overview", headers=bearer(admin_tok))
    assert ok.status_code == 200, ok.text
    data = ok.json()
    assert "headline" in data and isinstance(data["headline"]["laws_in_force"], int)
    assert len(data["kg_node_breakdown"]) == 6
    assert data["account_summary"]["super_admins"] >= 1
    assert isinstance(data["countries"], list)


def test_download_guard_and_audit_form_export_patterns():
    edpb_path = "/api/analysis/tasks/abc-123/documents/dpia/export-edpb-en.docx"
    adm_form_path = "/api/admission/drafts/draft-456/forms/TN_F01"
    assert _DOWNLOAD.search(edpb_path) is not None
    assert _DOWNLOAD.search(adm_form_path) is not None
    assert wants_record("GET", adm_form_path) is True
    cat, action_zh = classify("GET", adm_form_path)
    assert cat == "export" and "官方表格" in action_zh
