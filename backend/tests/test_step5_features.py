"""第 8 项收口验证测试：
1. 第 6 国（JP 日本）从入库到法规知识库、合规评估、咨询对话、海外准入、更新订阅与标记新法规全链路动态可见；
2. 成稿包 export.zip 支持 ?fmt=xlsx（打包 Excel）；
3. 合规评估项目列表进度 /doc-projects/progress 快速路径与项目详情统计完全一致，且答案变更后即时刷新；
4. 国家卡片摘要字段与审计记录角色/表名中文化。
"""

import io
import zipfile

import pytest
from openpyxl import load_workbook
from sqlalchemy import update

from app.admission.models import AdmissionEntry, AdmissionStep
from app.analysis import document_narrative
from app.audit import describe
from app.ingestion.models import DocumentVersion
from app.knowledge.models import ClauseTranslation
from app.knowledge.router import _OVERVIEW_CACHE

from .conftest import bearer, login
from .test_doc_projects import BASE, _make
from .test_document_templates import _fake_paper_llm
from .test_s10_updates import setup_source


@pytest.fixture(autouse=True)
def _paper_model(monkeypatch):
    monkeypatch.setattr(document_narrative, "call_llm_json", _fake_paper_llm())


async def test_sixth_country_jp_end_to_end_across_modules(client, seed, session_factory, tmp_path):
    _OVERVIEW_CACHE.clear()
    _state, _site, doc, _v1, c1 = await setup_source(session_factory, tmp_path, country="JP")

    async with session_factory() as s:
        await s.execute(
            update(DocumentVersion)
            .where(DocumentVersion.document_id == doc.id)
            .values(published_date=None, effective_date=None, official_number=None)
        )
        s.add(
            ClauseTranslation(
                clause_id=c1.id,
                translated_text="向日本境外第三国提供个人数据时，个人信息处理事业者须事先取得本人同意。",
                engine="test",
                model_name="test",
                prompt_version="v1",
                quality_check_passed=True,
                quality_issues=[],
                review_status="approved",
                is_current=True,
            )
        )
        entry = AdmissionEntry(
            country_code="JP",
            name_zh="日本个人数据跨境传输备案",
            category="data_filing",
            authority_zh="日本个人信息保护委员会（PPC）",
            summary_zh="向境外提供个人数据前须核验接收方体制。",
            review_status="approved",
            evidence_clause_id=c1.id,
            evidence_quote="Article 28",
            functions=["connected_vehicle_cloud", "ota_diagnostics"],
            profile={"cited_laws": ["日本个人信息保护法"]},
            edited=["profile:cited_laws"],
        )
        s.add(entry)
        await s.flush()
        s.add(
            AdmissionStep(
                entry_id=entry.id,
                seq=1,
                title_zh="提交体制核验说明",
                detail_zh="向个人信息保护委员会提交跨境接收方保护措施说明",
                materials=["接收方保护体制说明"],
                evidence_clause_id=c1.id,
                evidence_quote="Article 28",
            )
        )
        await s.commit()
    _OVERVIEW_CACHE.clear()

    admin = bearer(await login(client, "admin", "admin@example.com"))
    user = bearer(await login(client, "user", "a-member@example.com"))

    # 1) 用户端法规知识库国家卡片摘要（法规数 + 最近更新时间）
    overview = (await client.get("/api/knowledge/overview", headers=user)).json()
    jp_ov = next((c for c in overview if c["country_code"] == "JP"), None)
    assert jp_ov is not None, overview
    assert jp_ov["country_name_zh"] == "日本"
    assert jp_ov["documents_count"] >= 1 and jp_ov["last_updated_at"]

    # 2) 管理端知识库国家卡片摘要（法规数 + 最近更新时间 + 待处理数）
    admin_countries = (await client.get("/api/admin/knowledge/countries", headers=admin)).json()
    jp_adm = next((c for c in admin_countries if c["country_code"] == "JP"), None)
    assert jp_adm is not None, admin_countries
    assert jp_adm["country_name_zh"] == "日本"
    assert jp_adm["documents_count"] >= 1 and jp_adm["last_updated_at"]
    assert isinstance(jp_adm["pending_translations_count"], int)
    assert isinstance(jp_adm["pending_kg_count"], int)

    # 3) 合规评估项目选项与第 6 国建项
    doc_opts = (await client.get(f"{BASE}/options", headers=user)).json()
    assert "日本" in doc_opts["countries"] and "日本" in doc_opts["library_countries"]
    proj = await _make(
        client,
        user,
        source="中国",
        recipient="日本",
        function="connected_vehicle_cloud",
        company_name="测试车企",
    )
    assert "日本" in proj["route"]["library_country"]

    # 4) 咨询助手国家选项与新建第 6 国引导对话
    guided = (await client.get("/api/analysis/chats/guided-options", headers=user)).json()
    assert {"code": "JP", "name": "日本"} in guided["countries"]
    chat_res = await client.post(
        "/api/analysis/chats",
        headers=user,
        json={
            "country_code": "JP",
            "vehicle_model": "测试车型 E7",
            "scenario_type": "connected_vehicle_cloud",
            "business_scene": "mass_operation",
        },
    )
    assert chat_res.status_code == 201, chat_res.text
    chat_data = chat_res.json()
    assert chat_data["country_code"] == "JP" and chat_data["country_name_zh"] == "日本"

    # 5) 海外属地准入（用户端 + 管理端国家列表 + 办理方案）
    adm_user_cs = (await client.get("/api/admission/countries", headers=user)).json()
    assert {"code": "JP", "name": "日本"} in adm_user_cs
    adm_mgr_cs = (await client.get("/api/admin/admission/countries", headers=admin)).json()
    assert {"code": "JP", "name": "日本"} in adm_mgr_cs
    plan = (
        await client.get("/api/admission/plan?country=JP&function=connected_vehicle_cloud", headers=user)
    ).json()
    assert plan["country_zh"] == "日本"
    all_plan_items = (plan.get("needed") or []) + (plan.get("maybe") or [])
    assert any(i["name_zh"] == "日本个人数据跨境传输备案" for i in all_plan_items), plan

    # 6) 订阅第 6 国 + 管理端“标记为新法规”推送通知
    sub = await client.put("/api/notifications/subscription", headers=user, json={"countries": ["JP"]})
    assert sub.status_code == 200 and "JP" in sub.json()["countries"]
    mark = await client.post(
        f"/api/admin/documents/{doc.id}/mark-new",
        headers=admin,
        json={"published_date": "2026-05-01"},
    )
    assert mark.status_code == 200, mark.text
    notes = (await client.get("/api/notifications", headers=user)).json()
    jp_notes = [n for n in notes["items"] if n.get("country_code") == "JP"]
    assert len(jp_notes) == 1 and "日本" in jp_notes[0]["title"]


async def test_export_zip_xlsx_and_progress_fast_path(client, seed):
    user = bearer(await login(client, "user", "a-member@example.com"))
    proj = await _make(
        client,
        user,
        source="摩洛哥",
        recipient="中国",
        function="ota_diagnostics",
        vehicle_model="星途 X7",
        company_name="测试车企",
    )
    pid = proj["id"]

    # 1) 验证 progress 快速路径与 overview 完全一致
    prog1 = (await client.get(f"{BASE}/progress?ids={pid}", headers=user)).json()[pid]
    fin1 = proj["final"]
    assert prog1 == {
        "ready": sum(d["state"] == "ready" for d in proj["documents"]),
        "total": len(proj["documents"]),
        "missing": len(fin1["missing"]) + sum(f["missing"] for f in fin1["forms"]),
        "issues": len(fin1.get("issues") or []),
    }

    # 修改答案后，数据库时间戳变化，progress 立即同步更新
    q_key = proj["questions"][0]["key"]
    updated = (
        await client.put(f"{BASE}/{pid}/answers", headers=user, json={"answers": {q_key: "已补充测试内容"}})
    ).json()
    prog2 = (await client.get(f"{BASE}/progress?ids={pid}", headers=user)).json()[pid]
    fin2 = updated["final"]
    assert prog2["missing"] == len(fin2["missing"]) + sum(f["missing"] for f in fin2["forms"])

    # 2) 验证成稿包 ?fmt=xlsx 返回合法 zip 且内含可解析的 .xlsx 文件
    res = await client.get(f"{BASE}/{pid}/export.zip?fmt=xlsx&draft=true", headers=user)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    xlsx_names = [n for n in zf.namelist() if n.endswith(".xlsx")]
    assert len(xlsx_names) >= 6, zf.namelist()
    wb = load_workbook(io.BytesIO(zf.read(xlsx_names[0])))
    assert len(wb.sheetnames) >= 1

    # 非法格式参数返回 422
    bad = await client.get(f"{BASE}/{pid}/export.zip?fmt=csv&draft=true", headers=user)
    assert bad.status_code == 422


async def test_audit_humanize_and_excel_role_labels(client, seed):
    assert describe.humanize("删除user_subscriptions") == "删除法规订阅"
    assert describe.humanize("角色 由“member”改为“viewer”") == "角色 由“企业用户”改为“普通查看者”"
    assert describe.readable({"合规评估项目《测试》.法定代表人": "张三"}) == "合规评估项目《测试》.法定代表人：张三"

    user = bearer(await login(client, "user", "a-member@example.com"))
    assert (await client.put("/api/notifications/subscription", headers=user, json={"countries": ["SG"]})).status_code == 200
    assert (await client.put("/api/notifications/subscription", headers=user, json={"countries": ["EG"]})).status_code == 200

    admin = bearer(await login(client, "admin", "admin@example.com"))
    logs = (await client.get("/api/admin/audit-logs?limit=20", headers=admin)).json()["items"]
    sub_logs = [r for r in logs if r["action"] == "设置企业的法规订阅"]
    assert sub_logs and all("user_subscriptions" not in (r["summary"] or "") for r in sub_logs)

    xlsx = await client.get("/api/admin/audit-logs/export.xlsx?limit=20", headers=admin)
    assert xlsx.status_code == 200
    ws = load_workbook(io.BytesIO(xlsx.content)).active
    roles_col = [ws.cell(r, 4).value for r in range(2, ws.max_row + 1) if ws.cell(r, 4).value]
    assert roles_col and all(v in {"超级管理员", "运维管理员", "企业用户", "普通查看者", "已停用账号"} for v in roles_col), roles_col
