"""审计：登录、修改、导出都留痕（含 IP）；只有平台管理员能查；记录不可改、不可删；可按条件检索并导出 Excel。"""

import io

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.audit.service import classify, redact, wants_record

from .conftest import PASSWORD, bearer, login


def test_requests_are_classified_and_only_changes_exports_and_logins_are_recorded():
    assert classify("POST", "/api/auth/user/login") == ("login", "登录")
    assert classify("GET", "/api/analysis/tasks/x/documents/y/export.docx")[0] == "export"
    assert classify("DELETE", "/api/admission/issues/x")[0] == "delete"
    assert classify("PATCH", "/api/admission/issues/x") == ("change", "处理补正意见")
    assert classify("POST", "/api/some/new/endpoint") == ("change", "新增：其他")  # 不显示地址
    assert wants_record("PATCH", "/api/user/x") and not wants_record("GET", "/api/knowledge/clauses")
    assert wants_record("GET", "/api/analysis/tasks/x/documents/y/export.pdf")
    assert not wants_record("GET", "/api/health") and not wants_record("POST", "/health")


def test_credentials_are_masked_in_detail_payloads():
    out = redact({"title": "x", "password": "p", "nested": {"api_key": "k", "ok": 1}, "list": [{"token": "t"}]})
    assert out == {"title": "x", "password": "***", "nested": {"api_key": "***", "ok": 1}, "list": [{"token": "***"}]}


async def test_login_and_change_and_export_leave_records_with_ip_and_only_platform_admin_reads_them(
    client, seed, session_factory
):
    from .test_admission import _entry

    bad = await client.post("/api/auth/user/login", json={"email": "a-member@example.com", "password": "wrong-password"})
    assert bad.status_code == 401
    entry_id, _ = await _entry(session_factory)
    a = bearer(await login(client, "user", "a-admin@example.com"))
    pid = (await client.post("/api/admission/projects", json={"entry_id": entry_id, "title": "审计测试项目"}, headers=a)).json()["id"]
    issue = (await client.post(f"/api/admission/projects/{pid}/issues", json={"question": "补充测试计划"}, headers=a)).json()["id"]
    assert (await client.patch(f"/api/admission/issues/{issue}", json={"answer": "已补充", "state": "closed"}, headers=a)).status_code == 200
    draft = (await client.post(f"/api/admission/projects/{pid}/drafts", headers=a)).json()["id"]
    assert (await client.get(f"/api/admission/drafts/{draft}/export.docx", headers=a)).status_code == 200

    admin = bearer(await login(client, "admin", "admin@example.com"))
    got = (await client.get("/api/admin/audit-logs?limit=100", headers=admin)).json()
    assert got["total"] >= 5
    actions = {r["action"] for r in got["items"]}
    assert {"登录", "处理补正意见", "下载准入材料"} <= actions
    failed = next(r for r in got["items"] if r["category"] == "login" and r["status"] == 401)
    assert "a-member@example.com" in failed["summary"] and failed["actor_name"] == "a_member"
    ok = next(r for r in got["items"] if r["category"] == "login" and r["status"] == 200)
    assert ok["actor_email"] and ok["actor_role"] and ok["actor_name"]
    change = next(r for r in got["items"] if r["action"] == "处理补正意见")
    assert change["actor_email"] == "a-admin@example.com" and change["ip"]
    assert change["before"]["state"] == "open" and change["after"]["state"] == "closed" and change["after"]["answer"] == "已补充"
    assert len(change["content_hash"]) == 64

    # 检索：按人员、类型、关键字、时间
    by_actor = (await client.get("/api/admin/audit-logs?actor=a-admin", headers=admin)).json()
    assert by_actor["total"] >= 3 and all("a-admin" in r["actor_email"] for r in by_actor["items"])
    only_export = (await client.get("/api/admin/audit-logs?category=export", headers=admin)).json()
    assert only_export["total"] >= 1 and all(r["category"] == "export" for r in only_export["items"])
    assert (await client.get("/api/admin/audit-logs?q=审核问题", headers=admin)).json()["total"] >= 1
    assert (await client.get("/api/admin/audit-logs?start=2999-01-01T00:00:00", headers=admin)).json()["total"] == 0
    assert (await client.get("/api/admin/audit-logs?category=nonsense", headers=admin)).status_code == 422

    # 非平台管理员看不到
    for _who in ("a-admin@example.com", "a-member@example.com"):
        assert (await client.get("/api/admin/audit-logs", headers=a)).status_code in (401, 403)
    legacy = await client.post("/api/auth/admin/login", json={"email": "reviewer@example.com", "password": PASSWORD})
    assert legacy.status_code == 401  # 原律所审核员账号不能再登录

    # Excel 导出：有表头与数据，且导出这件事本身也被记录
    xlsx = await client.get("/api/admin/audit-logs/export.xlsx?category=change", headers=admin)
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    from openpyxl import load_workbook

    ws = load_workbook(io.BytesIO(xlsx.content)).active
    assert [c.value for c in ws[1]][:3] == ["时间", "人员", "邮箱"] and ws.max_row >= 2
    again = (await client.get("/api/admin/audit-logs?category=export&q=审计", headers=admin)).json()
    assert again["total"] >= 1


async def test_a_record_can_be_neither_changed_nor_deleted_even_with_full_database_access(
    client, seed, session_factory
):
    from app.audit.ddl import IMMUTABLE_DDL

    await client.post("/api/auth/user/login", json={"email": "nobody@example.com", "password": "x" * 12})
    async with session_factory() as s:
        for statement in IMMUTABLE_DDL:  # 测试库由模型建表，这里补上与迁移相同的不可改规则
            await s.execute(text(statement))
        await s.commit()
        try:
            for sql in (
                "update audit_logs set summary='篡改'",
                "delete from audit_logs",
                "truncate audit_logs",
            ):
                with pytest.raises(DBAPIError, match="不可修改或删除"):
                    await s.execute(text(sql))
                await s.rollback()
            assert (await s.execute(text("select count(*) from audit_logs"))).scalar_one() >= 1
        finally:
            # 共享测试库：规则只在本测试内生效，否则后面的测试清表（TRUNCATE）会被它挡住
            for sql in (
                "DROP TRIGGER IF EXISTS audit_logs_no_change ON audit_logs",
                "DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs",
            ):
                await s.execute(text(sql))
            await s.commit()
