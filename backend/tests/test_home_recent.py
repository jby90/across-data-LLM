"""首页“继续上次的工作”：先出名称，进度另取；进度与项目页同一套数，只给本企业的项目。"""

from .conftest import bearer, login
from .test_doc_projects import BASE, _make


async def test_the_progress_is_the_same_as_the_project_page_and_only_for_its_own_enterprise(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, function="ota_diagnostics")
    rows = (await client.get(BASE, headers=auth)).json()
    row = next(r for r in rows if r["id"] == p["id"])
    assert row["created_at"] and row["conversation_code"] and "progress" not in row  # 列表本身不算进度，名称马上出
    got = (await client.get(f"{BASE}/progress", headers=auth, params={"ids": f"{p['id']},not-an-id"})).json()
    page = (await client.get(f"{BASE}/{p['id']}", headers=auth)).json()
    final = page["final"]
    assert got == {p["id"]: {
        "ready": page["ready_count"],
        "total": 8,
        "missing": len(final["missing"]) + sum(f["missing"] for f in final["forms"]),
        "issues": len(final["issues"]),
    }}
    other = bearer(await login(client, "user", "b-admin@example.com"))
    assert (await client.get(f"{BASE}/progress", headers=other, params={"ids": p["id"]})).json() == {}
