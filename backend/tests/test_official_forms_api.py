"""官方表单接口：从评估带入已知信息、问缺项、只收官方选项、导出官方原件、企业隔离、超长不放行。"""

import pypdfium2 as pdfium

from .conftest import bearer, login


async def _task(session_factory, org_id, country="MA"):
    from app.analysis.models import AnalysisTask

    async with session_factory() as s:
        t = AnalysisTask(
            org_id=org_id,
            title="评估任务",
            country_code=country,
            vehicle_model="X9",
            scenario_type="vehicle_data_export",
            facts_json={"company_name": "示例汽车科技有限公司", "doc_recipient": "Siège Chine", "data_types": "车辆位置轨迹"},
        )
        s.add(t)
        await s.commit()
        return str(t.id)


async def test_catalogue_lists_forms_by_country_and_direction(client, seed):
    a = bearer(await login(client, "user", "a-member@example.com"))
    allf = (await client.get("/api/forms/catalog", headers=a)).json()
    codes = {f["code"] for f in allf}
    assert {"MA_F112", "MA_F118", "MA_F211", "TN_INPDP_TRANSFERT"} <= codes  # 逐格核过的数据保护表
    # 由原件读出的准入官方表格：五国都有
    assert {"EG_NTRA_TA", "LB_TRA_TA", "LB_TRA_DOC", "MA_F214", "MA_F113", "MA_ANRT_AGREMENT", "TN_INPDP_DECL", "TN_INPDP_BIO",
            "TN_INPDP_COMM", "TN_CERT_HOMOLOGATION", "SG_LTA_R01", "SG_LTA_AV", "SG_IMDA_SDOC"} <= codes
    assert next(f for f in allf if f["code"] == "MA_F211")["direction"] == "processing"  # 在摩洛哥处理数据的申报：接收端要填
    ma = next(f for f in allf if f["code"] == "MA_F118")
    assert ma["direction"] == "export" and ma["authority_zh"] and ma["prerequisite_zh"]
    assert ma["source_url"].startswith("https://www.cndp.ma")
    tn = (await client.get("/api/forms/catalog?country=TN&direction=export", headers=a)).json()
    assert [f["code"] for f in tn] == ["TN_INPDP_TRANSFERT"] and tn[0]["source_url"].startswith("https://www.mjs.tn")
    sg = (await client.get("/api/forms/catalog?country=SG", headers=a)).json()
    assert {f["code"] for f in sg} == {"SG_LTA_R01", "SG_LTA_AV", "SG_IMDA_SDOC"} and all(f["country"] == "SG" for f in sg)
    assert next(f for f in sg if f["code"] == "SG_IMDA_SDOC")["kind"] == "docx"  # Word 原件按 Word 交付
    assert (await client.get("/api/forms/catalog?country=ma&direction=import", headers=a)).json() == []


async def test_a_form_started_from_an_assessment_is_prefilled_and_asks_only_what_is_missing(
    client, seed, session_factory
):
    task_id = await _task(session_factory, seed["org_a"].id)
    a = bearer(await login(client, "user", "a-admin@example.com"))
    b = bearer(await login(client, "user", "b-admin@example.com"))
    assert (await client.post("/api/forms/instances", json={"form_code": "NOPE"}, headers=a)).status_code == 404
    assert (await client.post("/api/forms/instances", json={"form_code": "MA_F118", "task_id": task_id}, headers=b)).status_code == 404

    made = await client.post("/api/forms/instances", json={"form_code": "MA_F118", "task_id": task_id}, headers=a)
    assert made.status_code == 201
    view = made.json()
    items = {i["id"]: i for s in view["sections"] for i in s["items"]}
    assert items["ctrl_nom"]["value"] == "示例汽车科技有限公司" and items["ctrl_nom"]["source"] == "assessment"
    assert items["dest_nom"]["value"] == "Siège Chine" and items["file_description"]["value"] == "车辆位置轨迹"
    asked = {m["id"] for m in view["missing"]}
    assert "file_mode" in asked and "ctrl_nom" not in asked and not view["ready"]
    iid = view["id"]
    again = await client.post("/api/forms/instances", json={"form_code": "MA_F118", "task_id": task_id}, headers=a)
    assert again.json()["id"] == iid  # 同一项目再点“填写”回到同一份，不另起空表

    # 企业隔离
    assert (await client.get(f"/api/forms/instances/{iid}", headers=b)).status_code == 404
    assert (await client.get("/api/forms/instances", headers=b)).json() == []
    assert [x["id"] for x in (await client.get(f"/api/forms/instances?task_id={task_id}", headers=a)).json()] == [iid]

    # 回答：只收官方有的栏目，选项必须是官方选项
    assert (await client.put(f"/api/forms/instances/{iid}/answers", json={"answers": {"nope": "x"}}, headers=a)).status_code == 422
    saved = await client.put(
        f"/api/forms/instances/{iid}/answers",
        json={"answers": {"file_mode": "加密专线", "consent": "oui", "ctrl_type": "bogus", "dest_kind": ["siege"]}},
        headers=a,
    )
    assert saved.status_code == 200
    items = {i["id"]: i for s in saved.json()["sections"] for i in s["items"]}
    assert items["file_mode"]["value"] == "加密专线" and items["consent"]["value"] == "oui"
    # 平台推得出的不再问：车企是法人（责任人类型由平台填）
    assert items["ctrl_type"]["source"] == "assessment" and items["ctrl_type"]["value"] == "morale" and items["dest_kind"]["value"] == ["siege"]
    assert (await client.put(f"/api/forms/instances/{iid}/answers", json={"answers": {"file_mode": "x"}}, headers=b)).status_code == 404
    # 删空后保存：这一栏回到未填（不是保持原值）；多选全部取消也一样
    cleared = await client.put(f"/api/forms/instances/{iid}/answers", json={"answers": {"file_mode": "  ", "dest_kind": []}}, headers=a)
    assert cleared.status_code == 200
    items = {i["id"]: i for s in cleared.json()["sections"] for i in s["items"]}
    assert items["file_mode"]["source"] == "missing" and items["dest_kind"]["source"] == "missing" and items["consent"]["value"] == "oui"


async def test_export_is_the_official_file_filled_and_too_long_text_blocks_it_with_the_field_named(
    client, seed, session_factory
):
    task_id = await _task(session_factory, seed["org_a"].id)
    a = bearer(await login(client, "user", "a-admin@example.com"))
    b = bearer(await login(client, "user", "b-admin@example.com"))
    iid = (await client.post("/api/forms/instances", json={"form_code": "MA_F118", "task_id": task_id}, headers=a)).json()["id"]
    await client.put(
        f"/api/forms/instances/{iid}/answers",
        json={"answers": {"ctrl_type": "morale", "consent": "oui", "file_name": "Données télématiques", "dest_kind": ["siege"]}},
        headers=a,
    )
    ok = await client.get(f"/api/forms/instances/{iid}/export.pdf", headers=a)
    assert ok.status_code == 200 and ok.content[:5] == b"%PDF-"
    pdf = pdfium.PdfDocument(ok.content)
    assert len(pdf) == 9
    text = (pdf[1].get_textpage().get_text_bounded() or "").replace(chr(0xA0), " ")
    assert "示例汽车科技有限公司" in text and "✓" in text
    assert (await client.get(f"/api/forms/instances/{iid}/export.pdf", headers=b)).status_code == 404

    await client.put(f"/api/forms/instances/{iid}/answers", json={"answers": {"ctrl_adresse": "x" * 390}}, headers=a)
    blocked = await client.get(f"/api/forms/instances/{iid}/export.pdf", headers=a)
    assert blocked.status_code == 409 and "责任人：地址" in blocked.json()["detail"]


async def test_filling_and_exporting_leave_audit_records(client, seed, session_factory):
    task_id = await _task(session_factory, seed["org_a"].id)
    a = bearer(await login(client, "user", "a-admin@example.com"))
    iid = (await client.post("/api/forms/instances", json={"form_code": "MA_F118", "task_id": task_id}, headers=a)).json()["id"]
    await client.put(f"/api/forms/instances/{iid}/answers", json={"answers": {"file_mode": "加密专线"}}, headers=a)
    await client.get(f"/api/forms/instances/{iid}/export.pdf", headers=a)
    admin = bearer(await login(client, "admin", "admin@example.com"))
    logs = (await client.get("/api/admin/audit-logs?q=官方表单", headers=admin)).json()["items"]
    assert {r["category"] for r in logs} >= {"change", "export"}


async def test_a_form_being_filled_can_be_deleted_by_its_own_company_only(client, seed, session_factory):
    task_id = await _task(session_factory, seed["org_a"].id)
    a = bearer(await login(client, "user", "a-admin@example.com"))
    b = bearer(await login(client, "user", "b-admin@example.com"))
    iid = (await client.post("/api/forms/instances", json={"form_code": "MA_F118", "task_id": task_id}, headers=a)).json()["id"]
    assert (await client.delete(f"/api/forms/instances/{iid}", headers=b)).status_code == 404
    assert (await client.delete(f"/api/forms/instances/{iid}", headers=a)).status_code == 200
    assert (await client.get(f"/api/forms/instances/{iid}", headers=a)).status_code == 404


async def test_a_carried_field_says_which_project_it_came_from_and_links_back(client, seed, session_factory):
    """第 6 条：带入的栏目写“已从〈项目名〉带入”，点了回到项目去改。"""
    task_id = await _task(session_factory, seed["org_a"].id)
    a = bearer(await login(client, "user", "a-admin@example.com"))
    view = (await client.post("/api/forms/instances", json={"form_code": "MA_F118", "task_id": task_id}, headers=a)).json()
    items = {i["id"]: i for s in view["sections"] for i in s["items"]}
    assert items["ctrl_nom"]["carried"] == {"text": "已从项目 评估任务 带入", "to": f"/documents?project={task_id}"}
    assert all(i["carried"] is None for i in items.values() if i["source"] != "assessment")

    # 同一企业未关联具体项目时重复创建未填官方表，直接复用已有空实例而非不断插入空壳行
    empty_1 = (await client.post("/api/forms/instances", json={"form_code": "MA_F118"}, headers=a)).json()
    empty_2 = (await client.post("/api/forms/instances", json={"form_code": "MA_F118"}, headers=a)).json()
    assert empty_1["id"] == empty_2["id"]
