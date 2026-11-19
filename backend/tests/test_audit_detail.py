"""审计写明“做了什么”：每个写接口和下载都有人话的动作名；改了哪个项目的哪一栏、前后值由统一的写库监听记下；
密码、文件内容不记；长值截断并写明字数；摘要写不出也不挡正常保存。"""

import time

import pytest

from app.analysis import document_narrative
from app.audit import describe
from app.audit.service import classify

from .conftest import PASSWORD, bearer, login
from .test_doc_projects import BASE, _fake_paper_llm, _make


@pytest.fixture(autouse=True)
def _paper_model(monkeypatch):
    monkeypatch.setattr(document_narrative, "call_llm_json", _fake_paper_llm())


def test_every_write_route_and_download_has_a_readable_action_name():
    from app.main import create_app

    missing = []
    for path, ops in create_app().openapi()["paths"].items():
        for method in ops:
            m = method.upper()
            if m in ("POST", "PUT", "PATCH", "DELETE") or "/export" in path:
                if (m, path) not in describe.ROUTE_ACTIONS:
                    missing.append(f"{m} {path}")
    assert missing == [], "这些接口还没有动作名：" + "、".join(missing)
    assert classify("PUT", "/api/forms/instances/5b0c/answers") == ("change", "填写官方表格")
    assert classify("GET", "/api/analysis/doc-projects/x/export.zip") == ("export", "下载成稿包")


def test_values_are_cut_and_files_are_not_kept():
    long = "字" * 450
    assert describe.short(long).endswith("…（共 450 字）") and len(describe.short(long)) < 230
    assert describe.short(b"%PDF-1.7") == "已上传（文件内容不记录）"
    assert describe._secret("users", "password_hash") and describe._secret("task_attachments", "text")
    assert not describe._secret("analysis_tasks", "tokens_in")


async def _records(client):
    admin = bearer(await login(client, "admin", "admin@example.com"))
    return (await client.get("/api/admin/audit-logs?limit=200", headers=admin)).json()["items"]


async def test_saving_answers_records_the_project_the_item_and_before_and_after(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, function="ota_diagnostics", company_name="某车企")
    q = next(x for x in p["questions"] if x["key"] == "legal_rep")
    assert (await client.put(f"{BASE}/{p['id']}/answers", headers=auth, json={"answers": {"legal_rep": "张三"}})).status_code == 200
    assert (await client.put(f"{BASE}/{p['id']}/answers", headers=auth, json={"answers": {"legal_rep": "李四" * 120}})).status_code == 200
    rows = [r for r in await _records(client) if r["action"] == "填写合规评估信息"]
    assert len(rows) == 2
    last = rows[0]
    assert f"合规评估项目《{p['title']}》" in last["summary"]
    assert q["label"] in last["summary"] and "由“张三”改为" in last["summary"]
    key = next(k for k in last["after"] if k.endswith(q["label"]))
    assert last["before"][key] == "张三" and last["after"][key].endswith("（共 240 字）")
    first = rows[1]
    assert f"{q['label']} 填为“张三”" in first["summary"]


async def test_new_accounts_keep_no_password_and_deletions_name_what_went(client, seed):
    admin = bearer(await login(client, "admin", "admin@example.com"))
    r = await client.post("/api/admin/users", headers=admin, json={
        "email": "audit-new@example.com", "display_name": "x", "password": PASSWORD, "role": "member", "org_name": "审计测试有限公司"})
    assert r.status_code == 201
    rec = next(x for x in await _records(client) if x["action"] == "创建账号")
    dump = str(rec)
    assert "audit-new@example.com" in dump and "审计测试有限公司" in dump
    assert PASSWORD not in dump and "argon" not in dump.lower() and "$2" not in dump
    auth = bearer(await login(client, "user", "a-member@example.com"))
    chat = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    await client.patch(f"/api/analysis/tasks/{chat}", headers=auth, json={"title": "审计改名测试"})
    await client.delete(f"/api/analysis/tasks/{chat}", headers=auth)
    rows = {x["action"]: x for x in await _records(client)}
    assert "名称 由" in rows["重命名对话"]["summary"] and "审计改名测试" in rows["重命名对话"]["summary"]
    assert "对话《审计改名测试》" in rows["删除对话"]["summary"]


async def test_a_failing_summary_never_blocks_the_save(client, seed, monkeypatch):
    async def broken(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(describe, "describe", broken)
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, function="ota_diagnostics")
    assert (await client.put(f"{BASE}/{p['id']}/answers", headers=auth, json={"answers": {"legal_rep": "王五"}})).status_code == 200
    rec = next(x for x in await _records(client) if x["action"] == "填写合规评估信息")
    assert rec["actor_email"] == "a-member@example.com"  # 谁、何时、做了什么照常记下


async def test_listener_cost_per_save_is_small(client, seed, monkeypatch):
    """一次保存里监听本身花的时间（写进验收说明的数从这里来）。"""
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, function="ota_diagnostics")
    spent: list[float] = []
    real_end = describe.end

    def end(tokens):
        spent.append(describe.spent_seconds())
        real_end(tokens)

    monkeypatch.setattr(describe, "end", end)
    totals = []
    for i in range(10):
        t = time.perf_counter()
        await client.put(f"{BASE}/{p['id']}/answers", headers=auth, json={"answers": {"legal_rep": f"测{i}"}})
        totals.append(time.perf_counter() - t)
    per_save = sorted(spent[-10:])[5]
    print(f"[audit-cost] 一次保存共 {sorted(totals)[5] * 1000:.1f} ms，其中写库监听 {per_save * 1000:.2f} ms")
    assert per_save < 0.02
