"""管理端改了企业项目所依赖的内容，企业用户在“更新通知”里收到提醒，点了能直接打开对应页面：
- 简报出新版：只提醒在该国有项目的企业，且只提醒项目开始以后写成的新版；
- 准入事项有变化（步骤、材料、是否还在清单上）：提醒正在准备这一事项的企业；同一变化只提醒一次。"""

from sqlalchemy import select

from app.admission.models import AdmissionStep
from app.knowledge.whitepaper import CountryWhitepaper

from .conftest import bearer, login
from .test_admission import _entry


async def _notices(client, headers):
    return (await client.get("/api/notifications", headers=headers)).json()


async def test_briefing_edition_and_admission_change_reach_only_the_enterprises_concerned(client, seed, session_factory):
    entry_id, _ = await _entry(session_factory)
    a = bearer(await login(client, "user", "a-member@example.com"))
    a2 = bearer(await login(client, "user", "a-admin@example.com"))
    b = bearer(await login(client, "user", "b-admin@example.com"))
    r = await client.post("/api/admission/projects", json={"entry_id": entry_id, "title": "X1 车型试验授权"}, headers=a)
    assert r.status_code == 201, r.text
    project_id = r.json()["id"]
    assert (await _notices(client, a))["total"] == 0  # 刚建项目：没有变化，不提醒

    # 管理端更新了这一事项的办理步骤
    async with session_factory() as s:
        step = await s.scalar(select(AdmissionStep).where(AdmissionStep.entry_id == entry_id, AdmissionStep.seq == 1))
        step.materials = ["材料1", "新增：本地代表授权书"]
        s.add(CountryWhitepaper(country_code="SG", version_no=1, blocks=[], source_hash="x", report={}))
        await s.commit()

    got = await _notices(client, a)
    kinds = {n["kind"]: n for n in got["items"]}
    assert set(kinds) == {"admission", "whitepaper"} and got["unread"] == 2
    adm = kinds["admission"]
    assert adm["title"] == "准入事项有变化：自动驾驶道路试验授权"
    assert "X1 车型试验授权" in adm["body"] and adm["link"] == f"/admission?project={project_id}"
    wp = kinds["whitepaper"]
    assert wp["title"] == "新加坡合规简报已更新" and wp["link"] == "/knowledge?country=SG"
    # 同企业的同事也收到；别的企业（在新加坡没有项目）收不到
    assert (await _notices(client, a2))["total"] == 2
    assert (await _notices(client, b))["total"] == 0
    # 同一变化只提醒一次
    assert (await _notices(client, a))["total"] == 2
    # 点“打开”记为已读
    assert (await client.post(f"/api/notifications/{adm['id']}/read", headers=a)).status_code == 200
    assert (await _notices(client, a))["unread"] == 1


async def test_opening_the_notice_list_clears_the_count_and_a_newer_edition_replaces_the_unseen_one(client, seed, session_factory):
    entry_id, _ = await _entry(session_factory)
    a = bearer(await login(client, "user", "a-member@example.com"))
    assert (await client.post("/api/admission/projects", json={"entry_id": entry_id, "title": "X2"}, headers=a)).status_code == 201
    async with session_factory() as s:
        s.add(CountryWhitepaper(country_code="SG", version_no=1, blocks=[], source_hash="x", report={}))
        await s.commit()
    assert (await _notices(client, a))["unread"] == 1
    async with session_factory() as s:  # 没看之前又出了一版：只留新的那条
        s.add(CountryWhitepaper(country_code="SG", version_no=2, blocks=[], source_hash="y", report={}))
        await s.commit()
    got = await _notices(client, a)
    assert got["unread"] == 1 and got["total"] == 1
    seen = (await client.post("/api/notifications/read-all", headers=a)).json()["fresh"]
    assert seen == [got["items"][0]["id"]]
    assert (await _notices(client, a))["unread"] == 0
