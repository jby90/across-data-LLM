"""第三阶段模块收口核验测试：
1. 中国网信办官方原件在线修改、修改留痕、无损套打导出与一键恢复自动生成版；
2. 非欧盟流向与欧盟流向整包导出中欧盟标准英文范本范围控制，以及路径待补说明件格式一致；
3. 合规工作台概览跨账号未读通知严格隔离（同企业不同成员互不串扰）及零工程黑话；
4. 官方申报表目录与详情零 OCR 调试批注、普通查看者无导出权限严格拦截欧盟英文范本下载。
"""

import io
import json
import xml.etree.ElementTree as ET
import zipfile
from types import SimpleNamespace

import pytest

from app.analysis import document_narrative, official_eu_edpb_dpia
from app.iam import service
from app.iam.models import Role
from app.updates.models import UserNotification

from .conftest import PASSWORD, bearer, login
from .test_document_templates import _fake_paper_llm

BASE = "/api/analysis/doc-projects"
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@pytest.fixture(autouse=True)
def _paper_model(monkeypatch):
    monkeypatch.setattr(document_narrative, "call_llm_json", _fake_paper_llm())

    async def _fake_edpb_llm(*_args, **_kwargs):
        return SimpleNamespace(
            data={
                "controller": {
                    "name_and_address": "Merlion Mobility Pte. Ltd., Singapore",
                    "units": "Connected Vehicle R&D Division",
                    "contact_or_rep": "Singapore Headquarters",
                    "dpo_info": "Data Protection Officer, dpo@example.com",
                },
                "processing_name": "Connected Vehicle Remote Diagnostics",
            },
            prompt_tokens=10,
            completion_tokens=20,
        )

    monkeypatch.setattr(official_eu_edpb_dpia, "call_llm_json", _fake_edpb_llm)


def _docx_xml_root(raw: bytes) -> ET.Element:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        return ET.fromstring(zf.read("word/document.xml"))


def _docx_plain_text(raw: bytes) -> str:
    root = _docx_xml_root(raw)
    return "".join(t.text or "" for t in root.iter(f"{W_NS}t"))


async def test_cn_official_docx_online_edit_preserves_cac_tables_and_supports_reset(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    created = await client.post(
        BASE,
        headers=auth,
        json={
            "source_country": "中国",
            "recipient_country": "新加坡",
            "function": "ota_diagnostics",
            "company_name": "华星汽车有限公司",
            "vehicle_model": "HX7",
        },
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]

    # 1. 基线官方套打导出：核对表格总数与样式结构
    base_res = await client.get(f"/api/analysis/tasks/{pid}/documents/declaration/export.docx?draft=true", headers=auth)
    assert base_res.status_code == 200
    base_root = _docx_xml_root(base_res.content)
    base_table_count = len(list(base_root.iter(f"{W_NS}tbl")))
    assert base_table_count >= 1

    # 2. 开启初稿在线修订，定位首个表格单元格或正文段落进行修改
    init_res = await client.post(f"/api/analysis/tasks/{pid}/documents/declaration/saved?draft=true", headers=auth)
    assert init_res.status_code == 200, init_res.text
    blocks = init_res.json()["blocks"]
    assert len(blocks) >= 3

    target_payload = None
    for idx, blk in enumerate(blocks):
        if blk.get("type") == "table" and blk.get("rows"):
            rows = blk["rows"]
            for r_i, r in enumerate(rows):
                if len(r) >= 2:
                    target_payload = {"block": idx, "row": r_i, "col": 1, "text": "华星汽车法务部在线核定修订值"}
                    break
        if target_payload:
            break
    if not target_payload:
        p_idx = next(i for i, b in enumerate(blocks) if b.get("type") == "paragraph")
        target_payload = {"block": p_idx, "text": "华星汽车法务部在线核定修订值"}

    patch_res = await client.patch(
        f"/api/analysis/tasks/{pid}/documents/declaration/saved",
        headers=auth,
        json=target_payload,
    )
    assert patch_res.status_code == 200, patch_res.text
    patched = patch_res.json()
    assert patched["exists"] is True
    assert patched["edit_count"] == 1
    assert patched["edits"][0]["after"] == "华星汽车法务部在线核定修订值"

    # 3. 再次导出官方原表：确认修改文字已落入官方原表，且网信办官方表格总数零退化
    edited_res = await client.get(f"/api/analysis/tasks/{pid}/documents/declaration/export.docx?draft=true", headers=auth)
    assert edited_res.status_code == 200
    edited_root = _docx_xml_root(edited_res.content)
    assert len(list(edited_root.iter(f"{W_NS}tbl"))) == base_table_count
    assert "华星汽车法务部在线核定修订值" in _docx_plain_text(edited_res.content)

    # 4. 恢复自动生成版后，在线修订副本清除，导出恢复原值
    del_res = await client.delete(f"/api/analysis/tasks/{pid}/documents/declaration/saved", headers=auth)
    assert del_res.status_code == 200, del_res.text
    assert del_res.json()["exists"] is False

    saved_check = await client.get(f"/api/analysis/tasks/{pid}/documents/declaration/saved", headers=auth)
    assert saved_check.status_code == 200 and saved_check.json()["exists"] is False
    restored_res = await client.get(f"/api/analysis/tasks/{pid}/documents/declaration/export.docx?draft=true", headers=auth)
    assert "华星汽车法务部在线核定修订值" not in _docx_plain_text(restored_res.content)


async def test_zip_edpb_scope_and_route_note_format_consistency(client, seed, session_factory):
    auth = bearer(await login(client, "user", "a-member@example.com"))

    # 1. 中国发出但尚未填处理规模（路径待补）：导出 xlsx 压缩包时，路径待补说明件也必须是 .xlsx 而非混入 .docx
    cn_proj = await client.post(
        BASE,
        headers=auth,
        json={"source_country": "中国", "recipient_country": "新加坡", "function": "ota_diagnostics"},
    )
    assert cn_proj.status_code == 201
    cn_id = cn_proj.json()["id"]
    xlsx_zip = await client.get(f"{BASE}/{cn_id}/export.zip?fmt=xlsx&draft=true", headers=auth)
    assert xlsx_zip.status_code == 200
    xlsx_names = zipfile.ZipFile(io.BytesIO(xlsx_zip.content)).namelist()
    route_files = [n for n in xlsx_names if "出境路径判断待补" in n]
    assert route_files and all(n.endswith(".xlsx") for n in route_files)
    assert not any(n.endswith(".docx") for n in xlsx_names)

    # 2. 非欧盟流向（新加坡 -> 埃及）：has_edpb_en 为 False，整包不含欧盟英文范本，直接请求英文范本返回清晰 400
    non_eu = await client.post(
        BASE,
        headers=auth,
        json={"source_country": "新加坡", "recipient_country": "埃及", "function": "ota_diagnostics", "company_name": "狮城车联"},
    )
    assert non_eu.status_code == 201
    non_eu_data = non_eu.json()
    non_eu_id = non_eu_data["id"]
    non_eu_dpia = next(d for d in non_eu_data["documents"] if d["code"] == "dpia")
    assert non_eu_dpia["has_edpb_en"] is False

    fill_all = {q["key"]: "测试完整业务事实与安全技术措施说明" for q in non_eu_data["questions"]}
    fill_all["risk_rating"] = json.dumps(
        [{"id": "r1", "risk": "跨境链路风险", "likelihood": "低", "severity": "低", "measures": "全链路双向认证加密"}]
    )
    ans_res = await client.put(f"{BASE}/{non_eu_id}/answers", headers=auth, json={"answers": fill_all})
    assert ans_res.status_code == 200
    ans_dpia = next(d for d in ans_res.json()["documents"] if d["code"] == "dpia")
    assert ans_dpia["has_edpb_en"] is False

    draft_zip = await client.get(f"{BASE}/{non_eu_id}/export.zip?draft=true", headers=auth)
    assert draft_zip.status_code == 200
    non_eu_names = zipfile.ZipFile(io.BytesIO(draft_zip.content)).namelist()
    assert not any("欧盟标准英文范本" in n or "EDPB" in n for n in non_eu_names)

    non_eu_edpb = await client.get(f"/api/analysis/tasks/{non_eu_id}/documents/dpia/preview-edpb-en?draft=true", headers=auth)
    assert non_eu_edpb.status_code == 400
    assert "当前跨境流向不涉及欧盟或欧洲经济区" in non_eu_edpb.json()["detail"]

    # 3. 涉欧流向（新加坡 -> 德国）：has_edpb_en 为 True，preview-edpb-en 返回 200 且含欧盟标准英文范本，无导出权限 viewer 拦截 403
    eu_proj = await client.post(
        BASE,
        headers=auth,
        json={"source_country": "新加坡", "recipient_country": "德国", "function": "ota_diagnostics", "company_name": "狮城欧陆车联"},
    )
    assert eu_proj.status_code == 201
    eu_data = eu_proj.json()
    eu_id = eu_proj.json()["id"]
    eu_dpia = next(d for d in eu_data["documents"] if d["code"] == "dpia")
    assert eu_dpia["has_edpb_en"] is True

    single_edpb = await client.get(f"/api/analysis/tasks/{eu_id}/documents/dpia/preview-edpb-en?draft=true", headers=auth)
    assert single_edpb.status_code == 200
    assert "欧盟标准英文范本" in single_edpb.json()["name"]

    async with session_factory() as s:
        await service.create_user(
            s,
            email="noexport-viewer@example.com",
            display_name="体验查看者",
            password=PASSWORD,
            role=Role.VIEWER,
            org_id=seed["org_a"].id,
            can_export=False,
        )
    viewer_auth = bearer(await login(client, "user", "noexport-viewer@example.com"))
    denied = await client.get(f"/api/analysis/tasks/{eu_id}/documents/dpia/export-edpb-en.docx?draft=true", headers=viewer_auth)
    assert denied.status_code == 403


async def test_compliance_overview_isolates_unread_notifications_per_user(client, seed, session_factory):
    async with session_factory() as s:
        # 仅给 a_admin 写入未读通知，同企业 a_member 无未读通知
        s.add(
            UserNotification(
                user_id=seed["a_admin"].id,
                org_id=seed["org_a"].id,
                kind="whitepaper",
                title="新加坡国家合规简报已发布新版本",
                body="新增车载数据跨境传输备案说明",
                read_at=None,
            )
        )
        await s.commit()

    auth_admin = bearer(await login(client, "user", "a-admin@example.com"))
    auth_member = bearer(await login(client, "user", "a-member@example.com"))

    ov_admin = (await client.get(f"{BASE}/compliance-overview", headers=auth_admin)).json()
    ov_member = (await client.get(f"{BASE}/compliance-overview", headers=auth_member)).json()

    admin_law_todos = [t for t in ov_admin["todos"] if t["kind"] == "law_update"]
    member_law_todos = [t for t in ov_member["todos"] if t["kind"] == "law_update"]
    assert len(admin_law_todos) == 1
    assert "新加坡国家合规简报已发布新版本" in admin_law_todos[0]["title"]
    assert len(member_law_todos) == 0
    raw_dump = json.dumps(ov_admin, ensure_ascii=False)
    assert "待复诊" not in raw_dump


async def test_forms_catalog_and_official_templates_zero_ocr_jargon(client, seed):
    from app.forms import service as forms_service

    user_auth = bearer(await login(client, "user", "a-member@example.com"))
    admin_auth = bearer(await login(client, "admin", "admin@example.com"))

    cat = await client.get("/api/forms/catalog", headers=user_auth)
    assert cat.status_code == 200
    items = cat.json()
    assert len(items) >= 20
    cat_dump = json.dumps(items, ensure_ascii=False)
    assert "OCR" not in cat_dump and "乱码" not in cat_dump

    for item in items:
        spec = forms_service.get_form(item["code"])
        s_dump = json.dumps(spec, ensure_ascii=False, default=str)
        assert "OCR" not in s_dump and "乱码" not in s_dump, f"Leak in {item['code']}"

    for sample_code in ("MA_F118", "TN_INPDP_DECL"):
        inst = await client.post("/api/forms/instances", headers=user_auth, json={"form_code": sample_code})
        assert inst.status_code in (200, 201)
        i_dump = json.dumps(inst.json(), ensure_ascii=False)
        assert "OCR" not in i_dump and "乱码" not in i_dump

    tpls = await client.get("/api/admin/document-templates/official", headers=admin_auth)
    assert tpls.status_code == 200
    t_dump = json.dumps(tpls.json(), ensure_ascii=False)
    assert "OCR" not in t_dump and "乱码" not in t_dump
