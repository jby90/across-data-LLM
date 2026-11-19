import jwt
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import AUD_USER, create_access_token
from app.iam.models import Organization, User

from .conftest import PASSWORD, bearer, login


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_user_login_ok_and_me(client, seed):
    token = await login(client, "user", "a-admin@example.com")
    r = await client.get("/api/user/me", headers=bearer(token))
    assert r.status_code == 200
    assert r.json()["role"] == "member"
    assert r.json()["org_id"] == str(seed["org_a"].id)


async def test_email_case_insensitive(client, seed):
    r = await client.post(
        "/api/auth/user/login", json={"email": "A-Admin@Example.com", "password": PASSWORD}
    )
    assert r.status_code == 200


async def test_wrong_password_and_unknown_user_same_response(client, seed):
    bad_pw = await client.post(
        "/api/auth/user/login",
        json={"email": "a-admin@example.com", "password": "wrong-password-1"},
    )
    unknown = await client.post(
        "/api/auth/user/login", json={"email": "nobody@example.com", "password": "wrong-password-1"}
    )
    assert bad_pw.status_code == unknown.status_code == 401
    assert bad_pw.json() == unknown.json()


async def test_side_separation_on_login(client, seed):
    # 用户端账号不能走管理端登录,反之亦然
    r1 = await client.post(
        "/api/auth/admin/login", json={"email": "a-admin@example.com", "password": PASSWORD}
    )
    r2 = await client.post(
        "/api/auth/user/login", json={"email": "admin@example.com", "password": PASSWORD}
    )
    assert r1.status_code == 401 and r2.status_code == 401


async def test_token_audience_not_interchangeable(client, seed):
    user_token = await login(client, "user", "a-admin@example.com")
    admin_token = await login(client, "admin", "admin@example.com")
    assert (await client.get("/api/admin/orgs", headers=bearer(user_token))).status_code == 401
    assert (await client.get("/api/user/me", headers=bearer(admin_token))).status_code == 401


async def test_requires_token(client, seed):
    assert (await client.get("/api/admin/orgs")).status_code == 401
    assert (await client.get("/api/user/me")).status_code == 401


async def test_tampered_and_expired_token_rejected(client, seed):
    good = await login(client, "user", "a-admin@example.com")
    assert (await client.get("/api/user/me", headers=bearer(good + "x"))).status_code == 401
    expired = jwt.encode(
        {
            "iss": "cyshuhe",
            "aud": AUD_USER,
            "sub": str(seed["a_admin"].id),
            "role": "member",
            "exp": 1,
        },
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    assert (await client.get("/api/user/me", headers=bearer(expired))).status_code == 401


async def test_role_claim_must_match_db(client, seed, session_factory):
    # 令牌里声称的角色与库里不一致时拒绝；合并前的“企业管理员”令牌同样失效（需重新登录）
    for role in ("org_admin", "platform_admin"):
        token = create_access_token(
            user_id=seed["a_member"].id, audience=AUD_USER, role=role, org_id=seed["org_a"].id
        )
        assert (await client.get("/api/user/me", headers=bearer(token))).status_code == 401


async def test_user_side_has_no_member_management(client, seed):
    # 只有一种企业用户：用户端不设成员管理，也不在用户端改脱敏词（由超级管理员在“企业”页设置）
    token = bearer(await login(client, "user", "a-admin@example.com"))
    assert (await client.get("/api/user/members", headers=token)).status_code in (404, 405)
    assert (
        await client.post(
            "/api/user/members",
            headers=token,
            json={"email": "new@example.com", "display_name": "x", "password": PASSWORD},
        )
    ).status_code in (404, 405)
    assert (
        await client.patch("/api/user/organization/redaction", headers=token, json={"terms": ["Ab"]})
    ).status_code in (404, 405)


async def test_retired_reviewer_account_cannot_log_in(client, seed):
    # 律所审核员角色已撤销：旧账号留在库里，但两端都登录不了
    for side in ("admin", "user"):
        r = await client.post(f"/api/auth/{side}/login", json={"email": "reviewer@example.com", "password": PASSWORD})
        assert r.status_code == 401


async def test_account_list_hides_stopped_reviewer_and_has_two_kinds(client, seed, session_factory):
    token = bearer(await login(client, "admin", "admin@example.com"))
    users = (await client.get("/api/admin/users", headers=token)).json()
    assert {u["role"] for u in users} == {"platform_admin", "member"}
    assert "reviewer@example.com" not in {u["email"] for u in users}
    # 停用的企业不列，它的账号也不列；数据行留在库里
    async with session_factory() as s:
        org = await s.get(Organization, seed["org_b"].id)
        org.is_active = False
        await s.commit()
    orgs = (await client.get("/api/admin/orgs", headers=token)).json()
    assert str(seed["org_b"].id) not in {o["id"] for o in orgs}
    users = (await client.get("/api/admin/users", headers=token)).json()
    assert "b-admin@example.com" not in {u["email"] for u in users}


async def test_opening_an_account_by_company_full_name(client, seed):
    # 填公司全称开通：没有这家企业就新建；半角括号、多余空格视为同一家
    token = bearer(await login(client, "admin", "admin@example.com"))
    first = await client.post(
        "/api/admin/users",
        headers=token,
        json={
            "email": "c1@example.com",
            "display_name": "c1",
            "password": PASSWORD,
            "role": "member",
            "org_name": " 丙汽车（上海）有限公司 ",
        },
    )
    assert first.status_code == 201
    second = await client.post(
        "/api/admin/users",
        headers=token,
        json={
            "email": "c2@example.com",
            "display_name": "c2",
            "password": PASSWORD,
            "role": "member",
            "org_name": "丙汽车(上海)有限公司",
        },
    )
    assert second.status_code == 201 and second.json()["org_id"] == first.json()["org_id"]
    orgs = (await client.get("/api/admin/orgs", headers=token)).json()
    names = [o["name"] for o in orgs if o["id"] == first.json()["org_id"]]
    assert names == ["丙汽车（上海）有限公司"]
    # 管理端账号不能挂企业；企业用户既没选企业也没填全称时拒绝
    bad = await client.post(
        "/api/admin/users",
        headers=token,
        json={
            "email": "c3@example.com",
            "display_name": "c3",
            "password": PASSWORD,
            "role": "platform_admin",
            "org_name": "丁公司",
        },
    )
    assert bad.status_code == 422


async def test_platform_admin_create_org_and_user(client, seed):
    token = await login(client, "admin", "admin@example.com")
    r = await client.post("/api/admin/orgs", headers=bearer(token), json={"name": "丙车企"})
    assert r.status_code == 201
    org_id = r.json()["id"]
    dup = await client.post("/api/admin/orgs", headers=bearer(token), json={"name": "丙车企"})
    assert dup.status_code == 409
    u = await client.post(
        "/api/admin/users",
        headers=bearer(token),
        json={
            "email": "c-admin@example.com",
            "display_name": "丙管理员",
            "password": PASSWORD,
            "role": "member",
            "org_id": org_id,
        },
    )
    assert u.status_code == 201


async def test_role_org_rules_enforced(client, seed):
    token = await login(client, "admin", "admin@example.com")
    no_org = await client.post(
        "/api/admin/users",
        headers=bearer(token),
        json={
            "email": "x1@example.com",
            "display_name": "x",
            "password": PASSWORD,
            "role": "member",
        },
    )
    admin_with_org = await client.post(
        "/api/admin/users",
        headers=bearer(token),
        json={
            "email": "x2@example.com",
            "display_name": "x",
            "password": PASSWORD,
            "role": "platform_admin",
            "org_id": str(seed["org_a"].id),
        },
    )
    assert no_org.status_code == 422 and admin_with_org.status_code == 422


async def test_short_password_rejected(client, seed):
    token = await login(client, "admin", "admin@example.com")
    r = await client.post(
        "/api/admin/users",
        headers=bearer(token),
        json={
            "email": "short@example.com",
            "display_name": "x",
            "password": "short",
            "role": "platform_admin",
        },
    )
    assert r.status_code == 422


async def test_disabled_user_and_org_blocked(client, seed, session_factory):
    token = await login(client, "user", "a-member@example.com")
    async with session_factory() as s:
        user = (
            await s.execute(select(User).where(User.email == "a-member@example.com"))
        ).scalar_one()
        user.is_active = False
        await s.commit()
    # 已发出的令牌立即失效
    assert (await client.get("/api/user/me", headers=bearer(token))).status_code == 401
    r = await client.post(
        "/api/auth/user/login", json={"email": "a-member@example.com", "password": PASSWORD}
    )
    assert r.status_code == 401

    admin_token = await login(client, "user", "a-admin@example.com")
    async with session_factory() as s:
        org = await s.get(type(seed["org_a"]), seed["org_a"].id)
        org.is_active = False
        await s.commit()
    assert (await client.get("/api/user/me", headers=bearer(admin_token))).status_code == 401


async def test_password_not_stored_plain(session_factory, seed):
    async with session_factory() as s:
        user = (await s.execute(select(User).where(User.email == "admin@example.com"))).scalar_one()
        assert PASSWORD not in user.password_hash
        assert user.password_hash.startswith("$argon2")
