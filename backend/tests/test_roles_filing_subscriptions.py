"""第 7.1 项：四级角色（需求 2.7）、准入申报状态（需求 2.6）、订阅推送（需求 2.3）。"""

import re

import pytest
from sqlalchemy import select

from app.iam import service
from app.iam.deps import check_role_scope, is_export, is_super_admin_only
from app.iam.models import Role
from app.updates.models import ChangeAdvice, UserNotification

from .conftest import PASSWORD, bearer, login
from .test_admission import _entry
from .test_s10_updates import add_report, change_source, setup_source


async def _account(sf, email, role, org_id=None, can_export=False):
    async with sf() as s:
        return await service.create_user(
            s, email=email, display_name=email.split("@")[0], password=PASSWORD, role=role, org_id=org_id, can_export=can_export
        )


def _routes(app):
    spec = app.openapi()
    return [(m.upper(), p) for p, ops in spec["paths"].items() for m in ops]


def test_every_user_side_write_and_download_is_held_for_viewers():
    """按类把关：用户端每个写接口查看者都不能用（标已读、订阅设置除外），每个下载接口要开下载权限。"""
    from fastapi import HTTPException

    from app.main import create_app

    routes = _routes(create_app())
    user_side = [(m, p) for m, p in routes if not p.startswith(("/api/admin", "/api/auth", "/api/health"))]
    writes = [(m, p) for m, p in user_side if m not in ("GET", "HEAD")]
    assert len(writes) > 30
    allowed = []
    for m, p in writes:
        path = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", p)
        try:
            check_role_scope(Role.VIEWER, m, path, True)
            allowed.append((m, p))
        except HTTPException as exc:
            assert exc.status_code == 403
    assert sorted(allowed) == sorted(
        [
            ("POST", "/api/notifications/read-all"),
            ("POST", "/api/notifications/{notification_id}/read"),
            # 普通查看者个人咨询问答（仅限操作自己新建的私密对话，企业共享对话在路由内返回 403）
            ("POST", "/api/analysis/chats"),
            ("POST", "/api/analysis/tasks/{task_id}/messages"),
            ("PATCH", "/api/analysis/tasks/{task_id}"),
            ("DELETE", "/api/analysis/tasks/{task_id}"),
            ("POST", "/api/analysis/tasks/{task_id}/restore"),
        ]
    )
    downloads = [p for m, p in user_side if m == "GET" and is_export(re.sub(r"\{[^}]+\}", "x", p))]
    assert "/api/admission/drafts/{draft_id}/forms/{code}" in downloads and len(downloads) >= 10
    for p in downloads:
        with pytest.raises(HTTPException):
            check_role_scope(Role.VIEWER, "GET", re.sub(r"\{[^}]+\}", "x", p), False)
        check_role_scope(Role.VIEWER, "GET", re.sub(r"\{[^}]+\}", "x", p), True)
        check_role_scope(Role.MEMBER, "GET", re.sub(r"\{[^}]+\}", "x", p), False)
    # 管理端：企业与账号、审计、系统配置、企业准备情况只给超级管理员
    admin_side = {p for _, p in routes if p.startswith("/api/admin")}
    super_only = {p for p in admin_side if is_super_admin_only(p)}
    assert {"/api/admin/orgs", "/api/admin/users", "/api/admin/admission/projects"} <= super_only
    assert not any(p.startswith(("/api/admin/knowledge", "/api/admin/sites", "/api/admin/documents")) for p in super_only)


async def test_ops_admin_and_viewer_accounts(client, seed, session_factory):
    sf = session_factory
    admin = bearer(await login(client, "admin", "admin@example.com"))
    # 超级管理员在“企业与账号”开运维管理员和查看者
    r = await client.post("/api/admin/users", headers=admin, json={"email": "ops@example.com", "display_name": "ops", "password": PASSWORD, "role": "ops_admin"})
    assert r.status_code == 201, r.text
    r = await client.post("/api/admin/users", headers=admin, json={"email": "view@example.com", "display_name": "view", "password": PASSWORD, "role": "viewer", "org_id": str(seed["org_a"].id)})
    assert r.status_code == 201 and r.json()["can_export"] is False
    viewer_id = r.json()["id"]
    assert (await client.post("/api/admin/users", headers=admin, json={"email": "x@example.com", "display_name": "x", "password": PASSWORD, "role": "viewer"})).status_code == 422

    ops = bearer(await login(client, "admin", "ops@example.com"))
    assert (await client.get("/api/admin/me", headers=ops)).json()["role"] == "ops_admin"
    assert (await client.get("/api/admin/admission/entries", headers=ops)).status_code == 200
    assert (await client.get("/api/admin/knowledge/glossary", headers=ops)).status_code == 200
    for path in ("/api/admin/orgs", "/api/admin/users", "/api/admin/admission/projects", "/api/admin/audit-logs"):
        assert (await client.get(path, headers=ops)).status_code == 403, path
    with pytest.raises(AssertionError):
        await login(client, "user", "ops@example.com")  # 管理端账号不能登用户端

    entry_id, _ = await _entry(sf)
    a = bearer(await login(client, "user", "a-member@example.com"))
    pid = (await client.post("/api/admission/projects", json={"entry_id": entry_id, "title": "X1"}, headers=a)).json()["id"]
    draft = (await client.post(f"/api/admission/projects/{pid}/drafts", headers=a)).json()

    v = bearer(await login(client, "user", "view@example.com"))
    me = (await client.get("/api/user/me", headers=v)).json()
    assert me["role"] == "viewer" and me["can_export"] is False
    assert [p["id"] for p in (await client.get("/api/admission/projects", headers=v)).json()] == [pid]
    assert (await client.get(f"/api/admission/projects/{pid}", headers=v)).status_code == 200
    r = await client.post("/api/admission/projects", json={"entry_id": entry_id, "title": "X2"}, headers=v)
    assert r.status_code == 403 and "只能查看" in r.json()["detail"]
    assert (await client.put(f"/api/admission/projects/{pid}/filing", json={"state": "submitted"}, headers=v)).status_code == 403
    assert (await client.get(f"/api/admission/drafts/{draft['id']}/export.docx", headers=v)).status_code == 403
    assert (await client.put("/api/notifications/subscription", json={"countries": ["SG"]}, headers=v)).status_code == 403
    # 打开下载权限
    r = await client.patch(f"/api/admin/users/{viewer_id}", headers=admin, json={"can_export": True})
    assert r.json()["can_export"] is True
    assert (await client.get(f"/api/admission/drafts/{draft['id']}/export.docx", headers=v)).status_code == 200
    # 只能同一端内换角色；企业用户改成查看者后不能再改东西
    assert (await client.patch(f"/api/admin/users/{viewer_id}", headers=admin, json={"role": "ops_admin"})).status_code == 422
    member_id = str(seed["a_admin"].id)
    assert (await client.patch(f"/api/admin/users/{member_id}", headers=admin, json={"role": "viewer"})).json()["role"] == "viewer"
    assert (await client.post("/api/admission/projects", json={"entry_id": entry_id, "title": "X3"}, headers=bearer(await login(client, "user", "a-admin@example.com")))).status_code == 403
    users = (await client.get("/api/admin/users", headers=admin)).json()
    assert {u["role"] for u in users} >= {"platform_admin", "ops_admin", "viewer", "member"}


async def test_filing_status_follows_the_enterprise(client, seed, session_factory):
    entry_id, _ = await _entry(session_factory)
    a = bearer(await login(client, "user", "a-member@example.com"))
    pid = (await client.post("/api/admission/projects", json={"entry_id": entry_id, "title": "X1"}, headers=a)).json()["id"]
    detail = (await client.get(f"/api/admission/projects/{pid}", headers=a)).json()
    assert detail["filing"]["state"] == "preparing" and detail["filing"]["next"] == ["submitted"]
    put = lambda body: client.put(f"/api/admission/projects/{pid}/filing", json=body, headers=a)  # noqa: E731
    assert (await put({"state": "correcting", "questions": ["x"]})).status_code == 422  # 没递交不能补正
    r = await put({"state": "submitted", "on": "2026-11-01"})
    assert r.status_code == 422 and "生成" in r.json()["detail"]  # 没生成申请材料不能记已递交
    draft = (await client.post(f"/api/admission/projects/{pid}/drafts", headers=a)).json()
    assert (await put({"state": "submitted", "on": "2999-01-01"})).status_code == 422  # 递交日期不能晚于今天
    r = await put({"state": "submitted", "on": "2026-11-01"})
    assert r.status_code == 200 and r.json()["submitted_on"] == "2026-11-01" and r.json()["submitted_draft"] == draft["id"]
    assert (await put({"state": "correcting"})).status_code == 422  # 补正要写受理机构的问题
    r = await put({"state": "correcting", "questions": ["请补充数据处理者的当地代表授权书", "车型参数表缺少传感器清单"]})
    assert r.json()["state_zh"] == "补正中" and r.json()["open_questions"] == 2
    issues = (await client.get(f"/api/admission/projects/{pid}", headers=a)).json()["issues"]
    authority = [i for i in issues if i["kind"] == "authority"]
    assert len(authority) == 2
    r = await put({"state": "submitted"})
    assert r.status_code == 422 and "2 条" in r.json()["detail"]  # 问题没处理完不能再次递交
    for i in authority:
        await client.patch(f"/api/admission/issues/{i['id']}", json={"state": "closed", "answer": "已补"}, headers=a)
    r = await put({"state": "submitted"})
    assert r.status_code == 200 and r.json()["rounds"] == 1
    # 已取得批文走批文登记
    r = await client.put(f"/api/admission/projects/{pid}/approval", json={"number": "LTA-2026-001", "issued_on": "2026-11-05", "expires_on": "2027-11-04"}, headers=a)
    assert r.status_code == 200
    detail = (await client.get(f"/api/admission/projects/{pid}", headers=a)).json()
    assert detail["filing"]["state"] == "approved" and detail["filing"]["result_on"] == "2026-11-05"
    listed = (await client.get("/api/admission/projects", headers=a)).json()
    assert listed[0]["filing"]["state_zh"] == "已取得批文"
    admin = bearer(await login(client, "admin", "admin@example.com"))
    usage = (await client.get("/api/admin/admission/projects", headers=admin)).json()
    assert usage[0]["filing"]["state_zh"] == "已取得批文" and usage[0]["filing"]["rounds"] == 1
    # 未通过 → 重新准备
    pid2 = (await client.post("/api/admission/projects", json={"entry_id": entry_id, "title": "X2", "function": "other"}, headers=a)).json()["id"]
    await client.post(f"/api/admission/projects/{pid2}/drafts", headers=a)
    await client.put(f"/api/admission/projects/{pid2}/filing", json={"state": "submitted"}, headers=a)
    assert (await client.put(f"/api/admission/projects/{pid2}/filing", json={"state": "rejected"}, headers=a)).status_code == 422
    r = await client.put(f"/api/admission/projects/{pid2}/filing", json={"state": "rejected", "reason": "数据出境评估未通过"}, headers=a)
    assert r.json()["reason"] == "数据出境评估未通过" and r.json()["next"] == ["preparing"]


async def test_subscription_pushes_one_notice_per_change_with_comparison(session_factory, tmp_path, seed, client):
    from app.updates.service import monitor_once

    sf = session_factory
    state, site_id, doc, v1, c1 = await setup_source(sf, tmp_path, country="SG")
    await add_report(sf, seed, doc, v1, c1)  # 甲车企有引用这部法规的评估报告
    a = bearer(await login(client, "user", "a-member@example.com"))
    b = bearer(await login(client, "user", "b-admin@example.com"))
    sub = (await client.get("/api/notifications/subscription", headers=a)).json()
    assert sub["summary"] == "" and "ADMISSION" not in {o["value"] for o in sub["options"]["doc_types"]}
    r = await client.put("/api/notifications/subscription", json={"countries": ["SG", "ZZZ"], "doc_types": [], "topics": []}, headers=a)
    assert r.json()["countries"] == ["SG"] and r.json()["summary"].startswith("新加坡")
    await client.put("/api/notifications/subscription", json={"countries": ["EG"]}, headers=b)  # 乙订的是埃及

    await change_source(sf, tmp_path, state, site_id, text=" The organisation must notify the Commission within 3 days.")
    await monitor_once(sf)
    # 没有修订法令号和施行日期的文本变化不当修订推送
    assert [i["kind"] for i in (await client.get("/api/notifications", headers=a)).json()["items"]] == ["source_change"]
    from datetime import date

    from sqlalchemy import update

    from app.ingestion.models import DocumentVersion

    async with sf() as s:
        newest = (await s.scalars(select(DocumentVersion).order_by(DocumentVersion.version_no.desc()))).first()
        await s.execute(update(DocumentVersion).where(DocumentVersion.id == newest.id).values(official_number="Amendment Act 2026", effective_date=date(2026, 12, 1)))
        await s.commit()
    first = (await client.get("/api/notifications", headers=a)).json()
    kinds = [i["kind"] for i in first["items"]]
    assert kinds == ["subscription"], kinds  # 影响报告的那条并进订阅通知，同一件事只一条
    n = first["items"][0]
    assert n["revised"] and "修订" in n["title"] and "Amendment Act 2026" in n["body"] and "2026-12-01" in n["body"]
    assert n["advice"]["state"] == "pending" and n["advice"]["pairs"]
    pair = n["advice"]["pairs"][0]
    assert "3 days" in pair["after"]["text"] and pair["after"]["text"] != pair["before"]["text"]
    assert len(n["related"]) == 1 and n["related"][0]["cites"] and n["related"][0]["notice_id"]
    again = (await client.get("/api/notifications", headers=a)).json()
    assert again["total"] == 1
    assert (await client.get("/api/notifications", headers=b)).json()["total"] == 0
    async with sf() as s:
        assert len((await s.scalars(select(ChangeAdvice))).all()) == 1
        rows = (await s.scalars(select(UserNotification).where(UserNotification.kind == "subscription"))).all()
        # 订阅是企业的：甲车企每个账号各一条，同一件事；乙车企（订的是埃及）没有
        assert len({r.dedupe_key for r in rows}) == 1 and len({r.org_id for r in rows}) == 1 and len(rows) >= 1

    # 影响说明由模型写（这里替身），写好后同一条通知里显示
    from app.updates import subscriptions as subs

    class Fake:
        data = {"impact": "通报期限缩短为 3 天。", "actions": ["把数据泄露事件通报流程里的通报期限改为 3 天"]}
        model, total_tokens = "fake", 10

    async def fake_call(*args, **kwargs):
        return Fake()

    import app.analysis.llm as llm

    llm_call, llm.call_llm_json = llm.call_llm_json, fake_call
    try:
        assert await subs.write_pending(sf) == 10
    finally:
        llm.call_llm_json = llm_call
    n = (await client.get("/api/notifications", headers=a)).json()["items"][0]
    assert n["advice"]["state"] == "ready" and n["advice"]["actions"][0].startswith("把")
    # 取消订阅：不再推新的
    r = await client.put("/api/notifications/subscription", json={}, headers=a)
    assert r.json()["summary"] == ""


def test_admission_notice_says_what_changed_and_skips_rewording():
    from app.updates.notices import entry_changes

    old = {"name": "进口商登记簿注册", "authority": "埃及进出口监管总局（GOEIC）", "listed": True,
           "steps": ["提交申请"], "materials": ["银行保函原件"], "bring": ["银行保函原件"]}
    same = {**old, "authority": "埃及进出口监管总局 (GOEIC)"}  # 只是括号、空格不同
    assert entry_changes(old, same) == []
    new = {**old, "steps": ["提交申请", "缴纳保证金"], "bring": ["银行保函原件", "商业登记摘录"]}
    lines = entry_changes(old, new)
    assert lines == ["法定办理环节（增补「缴纳保证金」）", "递交核验原件（增补「商业登记摘录」）"]
    moved = {**old, "authority": "埃及工业发展局"}
    assert entry_changes(old, moved) == ["法定受理机构调整（由「埃及进出口监管总局（GOEIC）」更新为「埃及工业发展局」）"]


async def test_first_collected_law_without_date_or_same_content_is_not_news(session_factory, tmp_path, seed):
    """存量法规首次入库（没有公布、施行日期）不当变化推送；重新入库内容没变的也不推。"""
    import hashlib
    import uuid as _uuid
    from datetime import UTC, date, datetime

    from app.ingestion.models import Document, DocumentVersion
    from app.updates import subscriptions as subs

    _state, site, *_ = await setup_source(session_factory, tmp_path, country="SG")
    async with session_factory() as s:
        def doc(title):
            return Document(id=_uuid.uuid4(), site_id=site, country_code="SG", source_url=f"https://x/{_uuid.uuid4()}", title=title, title_zh=title,
                            language="en", document_type="LAW", relevance="relevant", discovered_at=datetime.now(UTC))
        a, b, c = doc("旧法"), doc("新法"), doc("新法副本")
        s.add_all([a, b, c])
        await s.flush()
        h = hashlib.sha256(b"same").hexdigest()
        for d, day, hh in ((a, None, hashlib.sha256(b"a").hexdigest()), (b, date(2026, 10, 1), h), (c, date(2026, 10, 1), h)):
            s.add(DocumentVersion(id=_uuid.uuid4(), document_id=d.id, version_no=1, content_hash=hh, archive_path="x", size_bytes=1, mime_type="text/plain",
                                  final_url=d.source_url, state="parsed", published_date=day))
        await s.flush()
        assert await subs.law_day(s, a) is None
        assert await subs.law_day(s, b) is not None
        assert await subs.same_as_existing(s, b) and await subs.same_as_existing(s, c)
        await s.rollback()


async def test_cited_clause_counts_as_updated_only_when_its_wording_changed(session_factory, tmp_path, seed):
    """合规文件“引用的条文有更新”：只在被引用那条条文的原文真的变了才算；写明是哪部法规哪一条。"""
    from app.analysis.document_narrative import DocumentNarrative, cited_changed
    from app.analysis.models import AnalysisTask
    from app.ingestion.models import Clause, DocumentVersion

    state, site_id, doc, v1, c1 = await setup_source(session_factory, tmp_path, country="SG")
    await add_report(session_factory, seed, doc, v1, c1)
    async with session_factory() as s:
        task = (await s.scalars(select(AnalysisTask).where(AnalysisTask.title == "Synthetic update test"))).first()
        paper = DocumentNarrative(org_id=task.org_id, task_id=task.id, code="x", inputs_hash="h", blocks=[], report={"cited_clause_ids": [str(c1.id)]})
        s.add(paper)
        await s.flush()
        assert await cited_changed(s, paper) == []  # 只有一版：没有更新
        v2 = DocumentVersion(document_id=doc.id, version_no=2, content_hash="v2" * 32, archive_path="x", size_bytes=1, mime_type="text/plain",
                             final_url=v1.final_url, state="parsed")
        s.add(v2)
        await s.flush()
        same = Clause(version_id=v2.id, ordinal=1, clause_number=c1.clause_number, original_text=c1.original_text, text_hash=c1.text_hash, language=c1.language)
        s.add(same)
        await s.flush()
        assert await cited_changed(s, paper) == []  # 新版同一条原文没变：不算
        same.text_hash, same.original_text = "changed" * 9 + "x", c1.original_text + " Amended."
        await s.flush()
        got = await cited_changed(s, paper)
        assert len(got) == 1 and got[0].startswith("《") and "条" in got[0], got
        await s.rollback()


async def test_mark_first_collected_law_as_new_pushes_once(session_factory, tmp_path, seed, client):
    """没有公布/施行日期、首次入库的法规不推送；管理员标记为新法规后推送一次，重复标不再推；
    查看者、企业用户没有这个入口；动作走审计。"""
    from sqlalchemy import update

    from app.audit.models import AuditLog
    from app.ingestion.models import Document, DocumentVersion

    _state, _site, doc, v1, _c1 = await setup_source(session_factory, tmp_path, country="SG")
    async with session_factory() as s:
        await s.execute(update(DocumentVersion).where(DocumentVersion.document_id == doc.id).values(published_date=None, effective_date=None, official_number=None))
        await s.commit()
    admin = bearer(await login(client, "admin", "admin@example.com"))
    a = bearer(await login(client, "user", "a-member@example.com"))
    assert (await client.put("/api/notifications/subscription", json={"countries": ["SG"]}, headers=a)).status_code == 200
    assert (await client.get("/api/notifications", headers=a)).json()["total"] == 0  # 没有日期：不推
    detail = (await client.get(f"/api/admin/documents/{doc.id}", headers=admin)).json()
    assert detail["has_date"] is False and detail["news_marked_at"] is None

    # 企业用户、查看者进不了管理端的这个入口
    assert (await client.post(f"/api/admin/documents/{doc.id}/mark-new", json={}, headers=a)).status_code in (401, 403)
    sup = await client.post("/api/admin/users", headers=admin, json={"email": "view2@example.com", "display_name": "v", "password": PASSWORD, "role": "viewer", "org_id": str(seed["org_a"].id)})
    assert sup.status_code == 201
    v = bearer(await login(client, "user", "view2@example.com"))
    assert (await client.post(f"/api/admin/documents/{doc.id}/mark-new", json={}, headers=v)).status_code in (401, 403)

    # 公布日期不能晚于今天；标一次
    assert (await client.post(f"/api/admin/documents/{doc.id}/mark-new", json={"published_date": "2999-01-01"}, headers=admin)).status_code == 422
    r = await client.post(f"/api/admin/documents/{doc.id}/mark-new", json={"published_date": "2026-03-01"}, headers=admin)
    assert r.status_code == 200, r.text
    assert (await client.post(f"/api/admin/documents/{doc.id}/mark-new", json={}, headers=admin)).status_code == 409  # 同一部只能标一次
    async with session_factory() as s:
        ver = (await s.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc.id).order_by(DocumentVersion.version_no.desc()))).first()
        assert str(ver.published_date) == "2026-03-01"  # 填了公布日期就写进去
        assert (await s.get(Document, doc.id)).news_marked_at is not None
        log = (await s.scalars(select(AuditLog).where(AuditLog.path.like("%mark-new%"), AuditLog.status == 200))).first()
        assert log is not None and log.action == "标记为新法规" and "标记为新法规" in log.summary

    first = (await client.get("/api/notifications", headers=a)).json()
    kinds = [i["kind"] for i in first["items"]]
    assert kinds.count("subscription") == 1, kinds
    n = next(i for i in first["items"] if i["kind"] == "subscription")
    assert not n["revised"] and n["advice"]["state"] in ("pending", "ready")
    again = (await client.get("/api/notifications", headers=a)).json()
    assert [i["kind"] for i in again["items"]].count("subscription") == 1  # 再读也不会多推
    detail = (await client.get(f"/api/admin/documents/{doc.id}", headers=admin)).json()
    assert detail["news_marked_at"] and detail["has_date"] is True


async def test_known_company_follows_the_filing_country_and_viewer_sees_every_project(client, seed, session_factory):
    """申请企业名称带入按申请国家算：上海母公司、别国的当地公司都不当埃及项目的申请人；别国项目的增删不改变埃及项目缺几项；
    页面“已带出”和缺项计数是同一个判断；普通查看者看到的项目和企业用户完全一样。"""
    from app.admission.models import AdmissionEntry, AdmissionStep
    from app.iam.models import Organization

    sf = session_factory
    async with sf() as s:
        org = await s.get(Organization, seed["org_a"].id)
        org.name = "启衡智行汽车科技（上海）有限公司"
        eg = AdmissionEntry(country_code="EG", name_zh="个人数据处理许可证", category="data_filing", authority_zh="埃及个人数据保护中心（PDPC）", summary_zh="须取得许可。",
                            profile={"cited_laws": ["2020 年第 151 号法律"]}, edited=["profile:cited_laws"])
        s.add(eg)
        await s.flush()
        s.add(AdmissionStep(entry_id=eg.id, seq=1, title_zh="提交申请", detail_zh="说明", materials=["商业登记副本"], evidence_quote="q"))
        eg_id = str(eg.id)
        await s.commit()
    sg_id, _ = await _entry(sf)
    admin = bearer(await login(client, "admin", "admin@example.com"))
    a = bearer(await login(client, "user", "a-member@example.com"))
    await client.post("/api/admin/users", headers=admin, json={"email": "view3@example.com", "display_name": "v", "password": PASSWORD, "role": "viewer", "org_id": str(seed["org_a"].id)})
    v = bearer(await login(client, "user", "view3@example.com"))

    async def state(pid):
        qs = (await client.get(f"/api/admission/projects/{pid}/questions", headers=a)).json()["questions"]
        proj = (await client.get(f"/api/admission/projects/{pid}", headers=a)).json()
        info = [i for i in proj["readiness"]["items"] if i["target"].startswith("info:")]
        return [q["key"] for q in qs], len(info), proj

    first = (await client.post("/api/admission/projects", json={"entry_id": eg_id}, headers=a)).json()["id"]
    keys, info, proj = await state(first)
    assert proj["applicant"] == ""  # 上海母公司不当埃及申请人，新建时也不写进去
    assert "applicant" in keys and info == len(keys)  # 缺项计数和问题清单同一个数
    assert "company_name" not in {f["key"] for f in proj["filled"]}  # 没有“申请企业名称已带出，却又要填申请人”的矛盾

    # 别国项目带着别国的当地公司：埃及项目的缺项不变
    other = (await client.post("/api/admission/projects", json={"entry_id": sg_id, "applicant": "某某汽车（新加坡）有限公司"}, headers=a)).json()["id"]
    assert (await state(first))[0] == keys
    await client.delete(f"/api/admission/projects/{other}", headers=a)
    assert (await state(first))[0] == keys

    # 埃及的当地公司给过一次：下一个埃及项目带出，不再问
    from app.admission.plan import FUNCTIONS

    f1, f2 = list(FUNCTIONS)[:2]  # 同一事项、同一业务只一个项目：换两个业务各建一个
    mine = (await client.post("/api/admission/projects", json={"entry_id": eg_id, "function": f1, "applicant": "某某汽车（埃及）有限公司"}, headers=a)).json()["id"]
    second = (await client.post("/api/admission/projects", json={"entry_id": eg_id, "function": f2}, headers=a)).json()["id"]
    assert len({first, mine, second}) == 3
    keys2, info2, proj2 = await state(second)
    assert proj2["applicant"] == "某某汽车（埃及）有限公司" and "applicant" not in keys2 and info2 == len(keys2)

    # 普通查看者看到企业里的全部项目，和企业用户一样
    mine_ids = sorted(p["id"] for p in (await client.get("/api/admission/projects", headers=a)).json())
    seen = (await client.get("/api/admission/projects", headers=v)).json()
    assert sorted(p["id"] for p in seen) == mine_ids and len(mine_ids) == 3 and {first, mine, second} == set(mine_ids)
