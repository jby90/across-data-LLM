"""海外属地准入：事项只留条文支持的环节；项目与台账按企业隔离；材料初稿不编造。"""

from app.admission.generator import verify_entry
from app.admission.models import AdmissionEntry, AdmissionStep

CLAUSE = (
    "An application to use on a road any autonomous motor vehicle must be made to the Authority\n"
    "in the form and manner required by the Authority and be accompanied by supporting documents."
)


def _answer(**over):
    base = {
        "in_scope": True,
        "name_zh": "自动驾驶机动车道路使用申请",
        "category": "av_testing",
        "authority_zh": "",
        "summary_zh": "拟上路的主体须向主管机关申请。",
        "steps": [
            {
                "title_zh": "提出申请",
                "detail_zh": "向主管机关提出",
                "materials": [],
                "quote": "must be made to the Authority in the form and manner required",
            },
            {"title_zh": "编造的一步", "detail_zh": "", "materials": [], "quote": "pay a fee of 500"},
        ],
    }
    return {**base, **over}


def test_a_step_without_a_verbatim_quote_is_dropped_and_whitespace_differences_are_tolerated():
    entry = verify_entry(_answer(), CLAUSE)
    assert [s["title_zh"] for s in entry["steps"]] == ["提出申请"]
    assert "\n" in entry["steps"][0]["quote"] or "Authority" in entry["steps"][0]["quote"]


def test_out_of_scope_or_nameless_answers_give_no_entry():
    assert verify_entry(_answer(in_scope=False), CLAUSE) is None
    assert verify_entry(_answer(name_zh=""), CLAUSE) is None
    assert verify_entry({"in_scope": "true"}, CLAUSE) is None  # 必须是明确的 true
    assert verify_entry(_answer(category="nonsense"), CLAUSE)["category"] == "other"


async def _entry(session_factory, steps=2):
    async with session_factory() as s:
        e = AdmissionEntry(
            country_code="SG",
            name_zh="自动驾驶道路试验授权",
            category="av_testing",
            authority_zh="LTA",
            summary_zh="试验前须获授权。",
            # 用户端只展示有依据法规的事项：测试事项由管理员列一部
            profile={"cited_laws": ["2017 年道路交通（自动驾驶机动车）规则"]},
            edited=["profile:cited_laws"],
        )
        s.add(e)
        await s.flush()
        for i in range(1, steps + 1):
            s.add(
                AdmissionStep(
                    entry_id=e.id,
                    seq=i,
                    title_zh=f"环节{i}",
                    detail_zh="说明",
                    materials=[f"材料{i}"],
                    evidence_quote="quote text",
                )
            )
        hidden = AdmissionEntry(
            country_code="SG", name_zh="无关事项", category="other", review_status="out_of_scope",
            profile={"cited_laws": ["测试法规"]}, edited=["profile:cited_laws"],
        )
        s.add(hidden)
        await s.commit()
        return str(e.id), str(hidden.id)


async def test_project_ledger_is_private_to_the_organisation_and_law_steps_cannot_be_deleted(
    client, seed, session_factory
):
    from .conftest import bearer, login

    entry_id, hidden_id = await _entry(session_factory)
    a = bearer(await login(client, "user", "a-admin@example.com"))
    b = bearer(await login(client, "user", "b-admin@example.com"))

    listed = (await client.get("/api/admission/entries?country=sg", headers=a)).json()
    assert [e["id"] for e in listed] == [entry_id] and listed[0]["step_count"] == 2
    assert (await client.get(f"/api/admission/entries/{hidden_id}", headers=a)).status_code == 404

    made = await client.post(
        "/api/admission/projects",
        json={"entry_id": entry_id, "title": "X1 车型试验授权", "applicant": "甲车企"},
        headers=a,
    )
    assert made.status_code == 201
    pid = made.json()["id"]
    # 平台检查用户填的材料：缺的信息、没生成的申请材料列为问题；改好后复查自动关闭
    again = await client.post("/api/admission/projects", json={"entry_id": entry_id, "title": "X1 车型试验授权"}, headers=a)
    assert again.json() == {"id": pid, "existing": True}  # 同一事项同一业务：打开原来的，不重复建
    detail = (await client.get(f"/api/admission/projects/{pid}", headers=a)).json()
    # 法规列的材料还没分类时，一律当作递交时自带的原件：只列清单，不让上传、不算检查问题
    assert detail["readiness"]["ready"] is False and [x["name"] for x in detail["bring"]] == ["材料1", "材料2"]
    assert "files" not in detail and "materials" not in detail
    checked = (await client.post(f"/api/admission/projects/{pid}/check", headers=a)).json()
    texts = [f["text"] for f in checked["findings"]]
    assert "还没有生成申请材料" in texts and not any("材料1" in t for t in texts)
    issues = (await client.get(f"/api/admission/projects/{pid}", headers=a)).json()["issues"]
    assert {i["kind"] for i in issues} == {"check"} and all(i["state"] == "open" for i in issues)
    # 企业证照不再上传，批文也只手填登记
    import base64 as _b64

    up = await client.post(f"/api/admission/projects/{pid}/files", json={"filename": "m1.pdf", "content_base64": _b64.b64encode(b"x" * 10).decode(), "note": "材料1"}, headers=a)
    assert up.status_code in (404, 405)
    assert (await client.put(f"/api/admission/projects/{pid}/approval", json={"number": ""}, headers=a)).status_code == 422
    ok = await client.put(f"/api/admission/projects/{pid}/approval", json={"number": "LTA-2026-01", "issued_on": "2026-01-01", "expires_on": "2029-01-01"}, headers=a)
    assert ok.status_code == 200 and ok.json()["number"] == "LTA-2026-01" and "file_id" not in ok.json()
    # 按国家 + 业务列出要办的事项
    pl = (await client.get("/api/admission/plan?country=SG&function=autonomous_trial", headers=a)).json()
    assert [e["id"] for e in pl["needed"] + pl["maybe"]] == [entry_id] and pl["needed"][0]["project"]["id"] == pid  # 这一事项已为别的业务准备过：卡上显示那一份的进度
    # 管理员后台看各企业的准备情况（前面刚登记了批文 LTA-2026-01，已取得批文则视为就绪、不再报缺项）
    admin = bearer(await login(client, "admin", "admin@example.com"))
    rows = (await client.get("/api/admin/admission/projects", headers=admin)).json()
    assert rows[0]["entry"] and rows[0]["org"] and rows[0]["ready"] is True and rows[0]["missing"] == [] and rows[0]["summary"] == "已取得批文"
    assert (await client.get("/api/admin/admission/projects", headers=a)).status_code in (401, 403)

    # 乙企业看不到、改不了甲企业的项目
    assert (await client.get(f"/api/admission/projects/{pid}", headers=b)).status_code == 404
    assert (await client.get("/api/admission/projects", headers=b)).json() == []
    assert (await client.post(f"/api/admission/projects/{pid}/check", headers=b)).status_code == 404

    draft = await client.post(f"/api/admission/projects/{pid}/drafts", headers=a)
    assert draft.status_code == 201
    assert any(m.startswith("车型或设备型号") for m in draft.json()["missing"])  # 平台不知道的才问
    blocks = draft.json()["blocks"]
    assert any("待补充" in str(b) for b in blocks)
    # 申请材料末尾附递交清单：本申请材料在前，企业自带的原件在后
    heads = [b.get("text", "") for b in blocks if b.get("type") == "heading"]
    k = next(i for i, b in enumerate(blocks) if b.get("type") == "heading" and b["text"].endswith("递交清单"))
    rows = blocks[k + 1]["rows"]
    assert rows[0][1:] == ["自动驾驶道路试验授权申请材料", "本文件第一部分（申请书）"] and [r[1:] for r in rows][-2:] == [["材料1", "企业递交时自带"], ["材料2", "企业递交时自带"]]
    # 章节序号在各部分（申请书、办理指引）内不重复；待补充事项接在办理指引最后
    split = next(i for i, b in enumerate(blocks) if b.get("type") == "pagebreak")
    for part in (blocks[:split], blocks[split:]):
        nums = [b["text"].split("、")[0] for b in part if b.get("type") == "heading"]
        assert len(nums) == len(set(nums))
    assert heads[-1].endswith("待补充事项")
    assert not any(b.get("text", "") == "六、办理进度" for b in draft.json()["blocks"])  # 递交材料不带内部台账
    did = draft.json()["id"]
    assert (await client.get(f"/api/admission/drafts/{did}", headers=b)).status_code == 404
    docx = await client.get(f"/api/admission/drafts/{did}/export.docx", headers=a)
    assert docx.status_code == 200 and docx.content[:2] == b"PK"
    assert (await client.get(f"/api/admission/drafts/{did}/export.txt", headers=a)).status_code == 404


async def test_only_the_platform_admin_reviews_entries(client, seed, session_factory):
    from .conftest import bearer, login

    entry_id, hidden_id = await _entry(session_factory)
    admin = bearer(await login(client, "admin", "admin@example.com"))
    member = bearer(await login(client, "user", "a-member@example.com"))
    assert len((await client.get("/api/admin/admission/entries", headers=admin)).json()) == 2
    assert (await client.get("/api/admin/admission/entries", headers=member)).status_code in (401, 403)
    shown = await client.patch(
        f"/api/admin/admission/entries/{hidden_id}",
        json={"review_status": "approved"},
        headers=admin,
    )
    assert shown.status_code == 200
    # 没有办理环节的事项资料没齐，用户端不展示
    assert len((await client.get("/api/admission/entries", headers=member)).json()) == 1

    # 合并去重后的事项：管理端能看到“已并入”，撤销后用户端重新单独显示
    import uuid as _uuid

    from app.admission.models import AdmissionEntry

    async with session_factory() as s:
        (await s.get(AdmissionEntry, _uuid.UUID(hidden_id))).merged_into = _uuid.UUID(entry_id)
        await s.commit()
    assert len((await client.get("/api/admission/entries", headers=member)).json()) == 1
    row = next(r for r in (await client.get("/api/admin/admission/entries", headers=admin)).json() if r["id"] == hidden_id)
    assert row["merged_into"] == entry_id
    undone = await client.patch(f"/api/admin/admission/entries/{hidden_id}", json={"unmerge": True}, headers=admin)
    assert undone.status_code == 200 and undone.json()["merged_into"] is None
    assert len((await client.get("/api/admission/entries", headers=member)).json()) == 1  # 没有办理环节的那一项资料没齐，拆开后也不对用户展示


async def _task(session_factory, org_id, country="SG", facts=None):
    from app.analysis.models import AnalysisTask

    async with session_factory() as s:
        t = AnalysisTask(
            org_id=org_id, title="评估任务一", country_code=country, vehicle_model="X9",
            scenario_type="vehicle_data_export", facts_json=facts or {"company_name": "甲车企有限公司"},
        )
        s.add(t)
        await s.commit()
        return str(t.id)


async def test_a_project_started_from_an_assessment_keeps_the_link_prefills_and_asks_what_is_missing(
    client, seed, session_factory
):
    from .conftest import bearer, login

    entry_id, _ = await _entry(session_factory)
    async with session_factory() as s:  # 条文没写受理机构：这一项只能追问得到
        from sqlalchemy import text as sql

        await s.execute(sql("update admission_entries set authority_zh='' where id=:i"), {"i": entry_id})
        await s.commit()
    task_id = await _task(session_factory, seed["org_a"].id)
    a = bearer(await login(client, "user", "a-admin@example.com"))
    b = bearer(await login(client, "user", "b-admin@example.com"))

    # 评估页的入口：该任务所在国家的准入事项，以及已从它发起的项目
    page = (await client.get(f"/api/admission/by-task/{task_id}", headers=a)).json()
    assert [e["id"] for e in page["entries"]] == [entry_id] and page["projects"] == []
    assert (await client.get(f"/api/admission/by-task/{task_id}", headers=b)).status_code == 404

    made = await client.post(
        "/api/admission/projects", json={"entry_id": entry_id, "title": "X9 试验授权", "task_id": task_id}, headers=a
    )
    assert made.status_code == 201
    pid = made.json()["id"]
    detail = (await client.get(f"/api/admission/projects/{pid}", headers=a)).json()
    assert detail["task"]["id"] == task_id and detail["vehicle_model"] == "X9" and detail["applicant"] == "甲车企有限公司"
    assert [p["id"] for p in (await client.get(f"/api/admission/by-task/{task_id}", headers=a)).json()["projects"]] == [pid]

    # 乙企业不能拿甲企业的评估发起项目
    other = await client.post(
        "/api/admission/projects", json={"entry_id": entry_id, "title": "越权", "task_id": task_id}, headers=b
    )
    assert other.status_code == 404

    # 追问：八类文件同一套模板问出来的缺项；已知的（国家、事项、受理机构、申请主体、车型）不再问
    q = (await client.get(f"/api/admission/projects/{pid}/questions", headers=a)).json()
    keys = {x["key"] for x in q["questions"]}
    # 受理机构条文没写时写“递交前向主管部门确认”，不让企业填；只问申请书上申请人的栏目（地址、登记号等平台不知道的）
    assert "authority" not in keys and "company_name" not in keys and "vehicle_model" not in keys
    assert keys <= {"company_address", "legal_rep_name", "company_reg_no", "company_phone", "company_email", "company_tax_no", "contact_email", "legal_rep_title", "local_rep_phone"}
    if keys:
        sample = {"email": "a@b.com", "phone": "+65 6123 4567", "reg_no": "201912345K", "tax_no": "T12345"}
        answers = {k: next((v for w, v in sample.items() if w in k), "已填") for k in keys}  # 每栏按类型校验
        ok = await client.post(f"/api/admission/projects/{pid}/answers", json={"answers": answers}, headers=a)
        assert ok.status_code == 200 and ok.json()["ready"]

    # 回答只收模板真正问的问题；答完后问题消失、初稿不再有待补充
    assert (await client.post(f"/api/admission/projects/{pid}/answers", json={"answers": {"not_a_question": "x"}}, headers=a)).status_code == 422
    assert (await client.post(f"/api/admission/projects/{pid}/answers", json={"answers": {"vehicle_model": "再答"}}, headers=b)).status_code == 404
    draft = (await client.post(f"/api/admission/projects/{pid}/drafts", headers=a)).json()
    assert draft["missing"] == []
    text = str(draft["blocks"])
    assert "X9" in text and "甲车企有限公司" in text and "自动驾驶道路试验授权" in text
    assert not any(b.get("text") == "六、办理进度" for b in draft["blocks"])  # 递交的材料不带内部办理记录
    detail = (await client.get(f"/api/admission/projects/{pid}", headers=a)).json()
    ready = detail["readiness"]
    # 信息齐、已出申请材料：剩下的只有该事项的官方表格（新加坡自动驾驶 → LTA AV 申请表）还没填
    assert [f["code"] for f in detail["forms"]] == ["SG_LTA_AV"]
    assert ready["blocking"] == 1
    assert "条文未写明受理机构，递交前向主管部门确认" in text  # 条文没写受理机构：如实写，不让企业猜
    assert "环节1" in text and "材料2" in text and "quote text" in text  # 环节、材料与条文原话完整列出

    # 评估被删后，项目仍在，只是没有回跳
    from sqlalchemy import text as sql

    async with session_factory() as s:
        await s.execute(sql("delete from analysis_tasks where id=:i"), {"i": task_id})
        await s.commit()
    gone = (await client.get(f"/api/admission/projects/{pid}", headers=a)).json()
    assert gone["task"] is None


async def test_a_project_without_an_assessment_still_works_and_asks_the_same_way(
    client, seed, session_factory
):
    from .conftest import bearer, login

    entry_id, _ = await _entry(session_factory)
    a = bearer(await login(client, "user", "a-admin@example.com"))
    pid = (await client.post("/api/admission/projects", json={"entry_id": entry_id, "title": "独立项目"}, headers=a)).json()["id"]
    q = (await client.get(f"/api/admission/projects/{pid}/questions", headers=a)).json()
    # 申请主体取企业注册时填的公司全称，不再问；平台不知道的（车型）照样问
    assert not any(x["key"] == "company_name" for x in q["questions"])
    assert any(x["key"] == "vehicle_model" for x in q["questions"])


def test_groups_sharing_an_entry_become_one_group():
    from app.admission.dedupe import union_groups

    assert union_groups([[0, 2], [2, 5], [3, 4]], 6) == [[0, 2, 5], [3, 4]]


async def test_an_administrators_change_shows_on_the_user_side_at_once(client, seed, session_factory):
    """管理端改了内容（这里是把准入事项设为范围外），用户端下一次请求就看到，不等缓存过期。"""
    from .conftest import bearer, login

    entry_id, _ = await _entry(session_factory)
    admin = bearer(await login(client, "admin", "admin@example.com"))
    member = bearer(await login(client, "user", "a-member@example.com"))
    from app.knowledge import router as kr

    kr._TOPICS_CACHE["MA"] = (10**12, [{"stale": True}])  # 模拟用户端已有缓存
    before = {e["id"] for e in (await client.get("/api/admission/entries", headers=member)).json()}
    assert entry_id in before
    assert (await client.patch(f"/api/admin/admission/entries/{entry_id}", json={"review_status": "out_of_scope"}, headers=admin)).status_code == 200
    assert "MA" not in kr._TOPICS_CACHE  # 管理端一改，用户端缓存即清
    after = {e["id"] for e in (await client.get("/api/admission/entries", headers=member)).json()}
    assert entry_id not in after


async def test_admin_corrects_an_item_and_rebuild_commands_leave_the_correction_alone(client, seed, session_factory, monkeypatch):
    """准入事项是所有企业共用的基础数据：管理员改名称、受理机构、环节材料、递交清单，立即生效、留痕、
    提醒正在准备的企业；以后再跑补中文名、材料分类、去重，人工改过的栏目不被覆盖。"""
    import types
    import uuid as _uuid

    from sqlalchemy import select

    from app.admission import dedupe as dd
    from app.admission import material_kinds as mk
    from app.admission import material_names as mn

    from .conftest import bearer, login

    entry_id, _ = await _entry(session_factory)
    admin = bearer(await login(client, "admin", "admin@example.com"))
    a = bearer(await login(client, "user", "a-admin@example.com"))
    pid = (await client.post("/api/admission/projects", json={"entry_id": entry_id, "title": "改资料前"}, headers=a)).json()["id"]
    await client.get("/api/notifications", headers=a)  # 记下项目看到的事项现状
    d = (await client.get(f"/api/admin/admission/entries/{entry_id}", headers=admin)).json()
    s1 = d["steps"][0]
    plan = [{"name": p["name"], "kind": p["kind"], "what": p["what"]} for p in d["materials_plan"]]
    plan[0]["what"] = "经公证的复印件"
    plan[1]["kind"] = "none"
    put = await client.put(f"/api/admin/admission/entries/{entry_id}/content", headers=admin, json={
        "name_zh": "自动驾驶道路测试授权", "authority_zh": "陆路交通管理局（LTA）",
        "steps": [{"id": s1["id"], "title_zh": "提交测试申请", "materials": ["材料1", "dossier technique"]}], "materials_plan": plan,
    })
    assert put.status_code == 200
    got = put.json()
    assert set(got["edited"]) - {"profile:cited_laws"} == {"name_zh", "authority_zh", "materials_plan"} and got["steps"][0]["edited"] is True
    assert (await client.put(f"/api/admin/admission/entries/{entry_id}/content", headers=a, json={"name_zh": "xx"})).status_code in (401, 403)
    # 用户端立即看到；递交清单按管理员改的来
    proj = (await client.get(f"/api/admission/projects/{pid}", headers=a)).json()
    assert proj["entry"]["name_zh"] == "自动驾驶道路测试授权" and proj["bring"] == [{"name": "材料1", "what": "经公证的复印件"}, {"name": "dossier technique", "what": ""}]
    # 正在准备的企业收到“准入事项有变化”
    notes = (await client.get("/api/notifications", headers=a)).json()
    assert "自动驾驶道路测试授权" in str(notes) and "有变化" in str(notes) or "更新了办理步骤" in str(notes)
    # 审计：人话动作名，带前后值
    logs = str((await client.get("/api/admin/audit-logs?limit=50", headers=admin)).json())
    assert "修改准入事项“自动驾驶道路测试授权”的资料" in logs and "陆路交通管理局（LTA）" in logs

    # 重新跑命令：补中文名不改人工改过的环节；材料分类 --redo 不改人工改过的递交清单；去重不改人工改过的名称
    async def fake_names(*_a, **_k):
        return types.SimpleNamespace(data={"items": [{"i": 0, "zh": "技术文件"}]}, prompt_tokens=1, completion_tokens=1)

    async def fake_kinds(*_a, **_k):
        return types.SimpleNamespace(data={"items": [{"i": 0, "kind": "original", "what": ""}, {"i": 1, "kind": "original"}]}, prompt_tokens=1, completion_tokens=1)

    async def fake_groups(*_a, **_k):
        return types.SimpleNamespace(data={"groups": [{"items": [0, 1], "name": "模型起的另一个名字"}]}, prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(mn, "call_llm_json", fake_names)
    monkeypatch.setattr(mk, "call_llm_json", fake_kinds)
    monkeypatch.setattr(dd, "call_llm_json", fake_groups)
    async with session_factory() as s:
        other = AdmissionEntry(country_code="SG", name_zh="自动驾驶测试许可", category="av_testing", authority_zh="LTA", summary_zh="")
        s.add(other)
        await s.commit()
        await mn.translate(s)
        await mk.classify(s, redo=True)
        await dd.dedupe(s, "SG", passes=1)
    async with session_factory() as s:
        e = await s.get(AdmissionEntry, _uuid.UUID(entry_id))
        st = await s.get(AdmissionStep, _uuid.UUID(s1["id"]))
        o = (await s.scalars(select(AdmissionEntry).where(AdmissionEntry.name_zh == "自动驾驶测试许可"))).one()
        assert st.materials == ["材料1", "dossier technique"] and st.title_zh == "提交测试申请"
        assert [p["kind"] for p in e.materials_plan] == [plan[0]["kind"], "none"] and e.materials_plan[0]["what"] == "经公证的复印件"
        assert e.name_zh == "自动驾驶道路测试授权" and e.merged_into is None and o.merged_into == e.id  # 人工改过的那一项留下


async def test_every_generated_version_keeps_its_checklist_and_filled_forms(client, seed, session_factory):
    """需求 2.6 留存申报资料：每生成一版都留存，可回看、再下载；递交清单在每一版的正文里，填好的官方表格随版本留存。"""
    from .conftest import bearer, login

    entry_id, _ = await _entry(session_factory)
    a = bearer(await login(client, "user", "a-admin@example.com"))
    pid = (await client.post("/api/admission/projects", json={"entry_id": entry_id, "title": "留存"}, headers=a)).json()["id"]
    first = (await client.post(f"/api/admission/projects/{pid}/drafts", headers=a)).json()
    second = (await client.post(f"/api/admission/projects/{pid}/drafts", headers=a)).json()
    versions = (await client.get(f"/api/admission/projects/{pid}", headers=a)).json()["drafts"]
    assert [v["id"] for v in versions] == [second["id"], first["id"]] and all("forms" in v for v in versions)
    for v in versions:
        body = (await client.get(f"/api/admission/drafts/{v['id']}", headers=a)).json()
        assert any(b.get("type") == "heading" and b["text"].endswith("递交清单") for b in body["blocks"])
        assert (await client.get(f"/api/admission/drafts/{v['id']}/export.pdf", headers=a)).status_code == 200
    assert (await client.get(f"/api/admission/drafts/{first['id']}/forms/NO_SUCH", headers=a)).status_code == 404



async def test_application_follows_the_item_not_the_data_report(client, seed, session_factory):
    """申请材料按事项写：申请人资格要求当地主体时不拿国内公司名代填、要企业补；材料逐项写怎么准备；
    按事项的申请语言写递交要求；办理路线写到向谁递交；条文只列一次。"""
    import uuid as _uuid

    from app.admission.models import AdmissionEntry

    from .conftest import bearer, login

    entry_id, _ = await _entry(session_factory)
    async with session_factory() as s:
        e = await s.get(AdmissionEntry, _uuid.UUID(entry_id))
        e.profile = {"eligibility": "须为在当地商业登记的企业", "language": "英语", "local_test": "待核实", "local_rep": "待核实", "validity": "待核实"}
        await s.commit()
    a = bearer(await login(client, "user", "a-admin@example.com"))
    pid = (await client.post("/api/admission/projects", json={"entry_id": entry_id, "title": "按事项写"}, headers=a)).json()["id"]
    q = (await client.get(f"/api/admission/projects/{pid}/questions", headers=a)).json()["questions"]
    assert any(x["key"] == "applicant" and "当地商业登记" in x["question"] for x in q)  # 不拿国内公司名代填
    d = (await client.post(f"/api/admission/projects/{pid}/drafts", headers=a)).json()
    texts = " ".join(str(b.get("text", "")) + str(b.get("items", "")) + str(b.get("rows", "")) for b in d["blocks"])
    assert d["name_zh"] == "自动驾驶道路试验授权申请材料" and "数据处理方名称" not in texts and "申请人名称" in texts
    assert "以英语提交" in texts and "附中文" not in texts
    assert "向LTA递交" in texts and "所依据的条文同上" not in texts and "编制依据" not in texts
    heads = [b["text"] for b in d["blocks"] if b.get("type") == "heading"]
    # 对外申请书在前（不出现“企业递交时自带”“平台”这类说法），办理指引在后（逐项写怎么准备）
    titles = [b["text"] for b in d["blocks"] if b.get("type") == "title"]
    assert titles[0].startswith("自动驾驶道路试验授权申请书") and titles[1] == "自动驾驶道路试验授权办理指引"
    split = next(i for i, b in enumerate(d["blocks"]) if b.get("type") == "pagebreak")
    letter = " ".join(str(b.get("text", "")) + str(b.get("rows", "")) for b in d["blocks"][:split])
    assert heads[1] == "二、申请事项" and "企业递交时自带" not in letter and "平台" not in letter and "声明" in letter
    assert any(r[0] == "材料1" and r[1].startswith("企业递交时自带") for b in d["blocks"][split:] if b.get("type") == "table" for r in b["rows"])
    detail = (await client.get(f"/api/admission/projects/{pid}", headers=a)).json()
    assert detail["readiness"]["summary"].startswith("还差：") and detail["readiness"]["items"]
