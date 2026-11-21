"""标准判定结论范本、跨文书结论实时同步校准、3×3 风险演变矩阵与双端态势接口回归单测（2026-11-21）。"""

from __future__ import annotations

import io
import json
import re
import zipfile

import pytest

from app.analysis import document_narrative, risk_items
from app.iam import service
from app.iam.models import Role

from .conftest import PASSWORD, bearer, login
from .test_document_templates import _fake_paper_llm

BASE = "/api/analysis/doc-projects"


@pytest.fixture(autouse=True)
def _paper_model(monkeypatch):
    monkeypatch.setattr(document_narrative, "call_llm_json", _fake_paper_llm())


def _flatten_blocks_text(blocks: list[dict]) -> str:
    parts: list[str] = []
    for b in blocks:
        if b.get("text"):
            parts.append(str(b["text"]))
        for item in b.get("items") or []:
            parts.append(str(item))
        for h in b.get("head") or []:
            parts.append(str(h))
        for row in b.get("rows") or []:
            for cell in row:
                parts.append(str(cell))
    return "\n".join(parts)


def _docx_all_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml_bytes = zf.read("word/document.xml")
    return re.sub(r"<[^>]+>", "", xml_bytes.decode("utf-8", errors="ignore"))


async def _ensure_extra_roles(session_factory, org_a_id):
    async with session_factory() as s:
        await service.create_user(
            s,
            email="ops-test@example.com",
            display_name="运维管理员",
            password=PASSWORD,
            role=Role.OPS_ADMIN,
            org_id=None,
        )
        await service.create_user(
            s,
            email="a-viewer-test@example.com",
            display_name="甲企查看者",
            password=PASSWORD,
            role=Role.VIEWER,
            org_id=org_a_id,
        )


async def test_verdict_rules_admin_api_validation_and_rbac(client, seed, session_factory):
    await _ensure_extra_roles(session_factory, seed["org_a"].id)
    super_auth = bearer(await login(client, "admin", "admin@example.com"))
    ops_auth = bearer(await login(client, "admin", "ops-test@example.com"))
    member_auth = bearer(await login(client, "user", "a-member@example.com"))
    viewer_auth = bearer(await login(client, "user", "a-viewer-test@example.com"))

    # 用户端角色越权访问管理端结论范本接口一律拦截，零 500
    for u_auth in (member_auth, viewer_auth):
        r_forbid = await client.get("/api/admin/document-templates/verdict-rules", headers=u_auth)
        assert r_forbid.status_code in (401, 403)

    # 超级管理员与运维管理员均可读取 6 类报告的标准判定结论范本
    r_get = await client.get("/api/admin/document-templates/verdict-rules", headers=super_auth)
    assert r_get.status_code == 200, r_get.text
    body = r_get.json()
    assert "【未化解高风险数】" in body["tokens"]
    codes = [it["code"] for it in body["items"]]
    assert codes == ["risk_report", "dpia", "lia", "tia", "remediation", "admission"]

    r_ops = await client.get("/api/admin/document-templates/verdict-rules", headers=ops_auth)
    assert r_ops.status_code == 200

    # 异常输入拦截：空字符、引号、破折号、未知占位符均返回 422 中文提示，零 500
    lia_item = next(it for it in body["items"] if it["code"] == "lia")
    bad_dash = json.loads(json.dumps(lia_item["rules"]))
    bad_dash[0]["template"] += "——违规破折号"
    r_bad1 = await client.put(
        "/api/admin/document-templates/verdict-rules/lia",
        headers=super_auth,
        json={"rules": bad_dash},
    )
    assert r_bad1.status_code == 422
    assert "破折号" in r_bad1.json()["detail"]

    bad_tok = json.loads(json.dumps(lia_item["rules"]))
    bad_tok[0]["template"] += "【不存在的变量】"
    r_bad2 = await client.put(
        "/api/admin/document-templates/verdict-rules/lia",
        headers=super_auth,
        json={"rules": bad_tok},
    )
    assert r_bad2.status_code == 422

    bad_nul = json.loads(json.dumps(lia_item["rules"]))
    bad_nul[0]["label"] = "通过\x00"
    r_bad3 = await client.put(
        "/api/admin/document-templates/verdict-rules/lia",
        headers=super_auth,
        json={"rules": bad_nul},
    )
    assert r_bad3.status_code in (400, 422)

    # 正常修订结论简称与正文范本：版本号递增，且模板详情保留 verdict_rules
    good_rules = json.loads(json.dumps(lia_item["rules"]))
    good_rules[0]["label"] = "经核验可适用"
    r_save = await client.put(
        "/api/admin/document-templates/verdict-rules/lia",
        headers=super_auth,
        json={"rules": good_rules},
    )
    assert r_save.status_code == 200, r_save.text
    saved_ver = r_save.json()["version_no"]
    assert saved_ver > lia_item["version_no"]

    tpl_detail = (await client.get("/api/admin/document-templates/lia", headers=super_auth)).json()
    assert tpl_detail["definition"]["verdict_rules"][0]["label"] == "经核验可适用"


async def test_risk_upgrade_synchronizes_all_six_verdicts_matrix_and_edpb_en(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p_res = await client.post(
        BASE,
        headers=auth,
        json={
            "source_country": "中国",
            "recipient_country": "德国",
            "function": "ota_diagnostics",
            "business_scene": "after_sales",
            "vehicle_model": "华拓 S9",
            "company_name": "华拓汽车有限公司",
        },
    )
    assert p_res.status_code == 201, p_res.text
    p_init = p_res.json()
    pid = p_init["id"]
    supported_keys = {q["key"] for q in p_init["questions"]}

    downgraded_risks = json.dumps(
        [
            {
                "id": "r1",
                "risk": "车载诊断日志含车架号在跨境传输途中被截获风险",
                "init_likelihood": "高",
                "init_severity": "高",
                "likelihood": "低",
                "severity": "中",
                "measures": "车端网关先完成脱敏哈希并启用双向国密传输通道",
                "owner": "车联网安全部",
                "due": "2026-12-01",
                "mitigated": "",
            },
            {
                "id": "r2",
                "risk": "历史调试端口未关闭导致超范围采集风险",
                "init_likelihood": "中",
                "init_severity": "高",
                "likelihood": "低",
                "severity": "低",
                "measures": "已在量产固件中物理封禁调试端口并完成复测",
                "owner": "研发中心",
                "due": "2026-11-15",
                "mitigated": "1",
            },
        ],
        ensure_ascii=False,
    )
    candidate_answers = {
        "credit_code": "91310000123456789X",
        "entity_type": "民营企业",
        "registered_place": "上海市嘉定区安亭镇墨玉南路888号",
        "office_place": "上海市嘉定区安亭镇墨玉南路888号",
        "legal_rep": "张明远",
        "employee_count": "2600人",
        "staff_count": "2600人",
        "dpo": "李知行，数据合规总监，dpo@huatuo-auto.cn",
        "business_overview": "新能源乘用车研发制造及智能网联售后服务",
        "data_system": "车云远程诊断平台与欧盟售后支持系统",
        "is_cii": "否",
        "law_compliance": "近两年严格遵守数据安全法与个人信息保护法，无行政处罚记录",
        "purpose": "用于德国售后维修站故障远程定位与软件补丁质量分析",
        "data_types": "故障码、电池包温控状态参数、去标识化车辆识别代号",
        "important_data": "否",
        "personal_info": "是，约 15 万名车主去标识化车辆故障关联记录",
        "sensitive_pi": "否",
        "data_volume": "累计约 15 万人（不涉及敏感个人信息）",
        "storage_chain": "境内存储于上海嘉定数据中心，经加密专线同步至法兰克福售后节点",
        "recipient": "Huatuo Europe GmbH（德国法兰克福售后技术中心）",
        "onward_provision": "不向其他境外第三方再转移",
        "retention_and_blackbox": "境外最长保存 12 个月，到期自动覆写销毁并留存审计日志",
        "individual_rights": "通过车载中控屏与官方应用提供查阅、复制、更正与删除受理入口",
        "security_capability": "通过 ISO/SAE 21434 与 ISO 27001 认证，配备全流量加密网关与入侵检测",
        "risk_measures": "端侧哈希去标识化、TLS 1.3 双向证书加密传输与境外最小权限控制",
        "issues": "调试端口历史遗留隐患已完成封禁；车架号传输已完成端侧哈希改造",
        "measures": "落实端侧脱敏、双向证书认证、最小权限访问与到期自动清理",
        "owner_and_deadline": "车联网安全部负责，于 2026 年 12 月 1 日前完成常态化巡检",
        "legitimate_interest": "履行跨境车辆质量保修与道路行车安全故障排查所必需",
        "necessity": "仅传输故障时刻状态快照，不采集车外视频与车内语音",
        "impact_and_optout": "对车主权益影响轻微，车主可在车机隐私设置中随时关闭远程诊断上传",
        "cross_border_mechanism": "依法订立个人信息出境标准合同并向所在地省级网信部门备案",
        "supplementary_measures": "端到端 TLS 1.3 加密、字段级假名化与密钥境内独立托管",
        "reevaluation": "每 12 个月或业务与法规发生重大变化时重新开展影响评估",
        "risk_rating": downgraded_risks,
        "risk_conclusion": "经初评与整改复核，高风险已全部压降清零，建议有条件通过并推进标准合同备案。",
    }
    payload_answers = {}
    for q in p_init["questions"]:
        qk = q["key"]
        if qk in candidate_answers:
            payload_answers[qk] = candidate_answers[qk]
        elif not q.get("optional"):
            if q.get("choices"):
                payload_answers[qk] = q["choices"][0]
            else:
                payload_answers[qk] = "已按企业数据安全管理规范落实专人核验与台账留存"
    payload_answers = {k: v for k, v in payload_answers.items() if k in supported_keys}

    ans_res = await client.put(
        f"{BASE}/{pid}/answers",
        headers=auth,
        json={"answers": payload_answers},
    )
    assert ans_res.status_code == 200, ans_res.text
    proj = ans_res.json()

    docs = {d["code"]: d for d in proj["documents"]}
    assert docs["declaration"]["verdict"] is None
    assert docs["contract"]["verdict"] is None
    assert docs["risk_report"]["verdict"]["label"] == "有条件通过"
    assert docs["dpia"]["verdict"]["label"] == "有条件通过"
    assert docs["remediation"]["verdict"]["label"] == "高风险已清零"
    assert docs["admission"]["verdict"] is not None
    assert docs["lia"]["verdict"] is not None
    assert docs["tia"]["verdict"] is not None

    rem_pv = await client.get(f"/api/analysis/tasks/{pid}/documents/remediation/preview", headers=auth)
    assert rem_pv.status_code == 200
    rem_txt = _flatten_blocks_text(rem_pv.json()["blocks"])
    assert "高风险已清零" in rem_txt

    adm_pv = await client.get(f"/api/analysis/tasks/{pid}/documents/admission/preview", headers=auth)
    assert adm_pv.status_code == 200
    adm_txt = _flatten_blocks_text(adm_pv.json()["blocks"])
    assert "属地准入" in adm_txt

    matrix_blocks = risk_items.blocks(downgraded_risks, "")
    matrix_txt = _flatten_blocks_text(matrix_blocks)
    assert "风险 1（原高）" in matrix_txt
    assert "原风险 1（已降级）" in matrix_txt
    assert "原风险 2（已整改）" in matrix_txt

    # 人工将风险 1 调回高风险（高×高），但保留旧的“建议有条件通过”结论文字
    upgraded_risks = json.dumps(
        [
            {
                "id": "r1",
                "risk": "车载诊断日志含车架号在跨境传输途中被截获风险",
                "init_likelihood": "高",
                "init_severity": "高",
                "likelihood": "高",
                "severity": "高",
                "measures": "端侧脱敏方案尚未上线，仍存在明文回传风险",
                "owner": "车联网安全部",
                "due": "2026-12-20",
                "mitigated": "",
            }
        ],
        ensure_ascii=False,
    )
    up_res = await client.put(
        f"{BASE}/{pid}/answers",
        headers=auth,
        json={"answers": {"risk_rating": upgraded_risks}},
    )
    assert up_res.status_code == 200, up_res.text
    up_proj = up_res.json()
    up_docs = {d["code"]: d for d in up_proj["documents"]}

    assert up_proj["project_verdict"]["label"] == "暂缓放行"
    assert up_docs["risk_report"]["verdict"]["label"] == "暂缓放行"
    assert up_docs["dpia"]["verdict"]["label"] == "暂缓放行"
    assert up_docs["lia"]["verdict"]["label"] == "暂缓放行"
    assert up_docs["tia"]["verdict"]["label"] == "暂缓放行"
    assert up_docs["remediation"]["verdict"]["label"] == "尚有 1 项高风险待整改"

    for doc_code in ("risk_report", "dpia"):
        exp_docx = await client.get(
            f"/api/analysis/tasks/{pid}/documents/{doc_code}/export.docx?draft=true",
            headers=auth,
        )
        assert exp_docx.status_code == 200
        docx_text = _docx_all_text(exp_docx.content)
        assert "暂缓放行" in docx_text
        assert "建议有条件通过" not in docx_text

    # 欧盟 EDPB 2026 英文范本：即使旧模型输出曾填 CONDITIONALLY_APPROVED，只要剩余风险含 High 即强制翻转为暂缓与 Art. 36 事先磋商，且零汉字外泄
    from app.analysis.official_eu_edpb_dpia import _CJK_RE, render_edpb_2026_docx

    edpb_docx = render_edpb_2026_docx(
        {
            "controller": {
                "name_and_address": "Huatuo Europe GmbH, Frankfurt, Germany",
                "units": "Connected Vehicle Diagnostics & Privacy Office",
                "contact_or_rep": "Frankfurt Main Office",
                "dpo_info": "dpo@huatuo-eu.example.com",
            },
            "processing_name": "Cross-Border Diagnostic VIN Telemetry",
            "risk_rows": [
                {
                    "risk": "Interception of cross-border VIN telemetry packets",
                    "init_likelihood": "High (L4)",
                    "init_severity": "High (S5)",
                    "init_level": "High (20/25)",
                    "measures": "Edge hashing gateway pending deployment",
                    "res_likelihood": "High (L4)",
                    "res_severity": "High (S5)",
                    "res_level": "High (20/25)",
                    "residual_acceptability": "Unacceptable residual risk.",
                }
            ],
            "residual_risk_conclusion": "All risks are acceptable and conditionally approved.",
            "decision": {
                "outcome": "CONDITIONALLY_APPROVED",
                "justification": "Conditionally approved prior to manual risk upgrade.",
            },
        },
        "2026-11-21",
    )
    edpb_txt = _docx_all_text(edpb_docx)
    assert "article 36" in edpb_txt.lower()
    assert "cannot proceed" in edpb_txt.lower() or "unacceptable" in edpb_txt.lower()
    assert "conditionally approved prior to manual" not in edpb_txt.lower()
    assert _CJK_RE.search(edpb_txt) is None, f"EDPB 英文范本残留汉字: {edpb_txt[:200]}"


async def test_cockpit_and_user_posture_overview_role_separation(client, seed, session_factory):
    await _ensure_extra_roles(session_factory, seed["org_a"].id)
    super_auth = bearer(await login(client, "admin", "admin@example.com"))
    ops_auth = bearer(await login(client, "admin", "ops-test@example.com"))
    member_a = bearer(await login(client, "user", "a-member@example.com"))
    viewer_a = bearer(await login(client, "user", "a-viewer-test@example.com"))
    member_b = bearer(await login(client, "user", "b-admin@example.com"))

    create_res = await client.post(
        BASE,
        headers=member_a,
        json={
            "source_country": "中国",
            "recipient_country": "摩洛哥",
            "function": "ota_diagnostics",
            "company_name": "华拓汽车",
        },
    )
    assert create_res.status_code == 201

    r_super = await client.get("/api/admin/cockpit/overview", headers=super_auth)
    assert r_super.status_code == 200
    c_body = r_super.json()
    assert any(
        rt.get("source_code") == "CN" and rt.get("recipient_code") == "MA"
        for rt in c_body.get("routes", [])
    )

    r_ops = await client.get("/api/admin/cockpit/overview", headers=ops_auth)
    assert r_ops.status_code == 403

    ov_member_a = (await client.get(f"{BASE}/compliance-overview", headers=member_a)).json()
    ov_viewer_a = (await client.get(f"{BASE}/compliance-overview", headers=viewer_a)).json()
    ov_member_b = (await client.get(f"{BASE}/compliance-overview", headers=member_b)).json()

    assert any(
        mr["source_code"] == "CN" and mr["recipient_code"] == "MA"
        for mr in ov_member_a["map_routes"]
    )
    assert len(ov_viewer_a["map_routes"]) == len(ov_member_a["map_routes"])
    assert isinstance(ov_member_a["legal_route_distribution"], list)
    assert isinstance(ov_member_a["deadlines"], list)
    assert any(d["id"] == create_res.json()["id"] for d in ov_member_a["recent_docs"])
    assert create_res.json()["id"] in ov_member_a["recent_progress"]
    assert isinstance(ov_member_a["recent_admissions"], list)
    assert all(d["id"] != create_res.json()["id"] for d in ov_member_b["recent_docs"])
    assert all(mr["recipient_code"] != "MA" for mr in ov_member_b["map_routes"])

    r_viewer_write = await client.post(
        BASE,
        headers=viewer_a,
        json={"source_country": "中国", "recipient_country": "新加坡", "function": "ota_diagnostics"},
    )
    assert r_viewer_write.status_code == 403

    # 验证缓存指纹实时失效：新建第 2 个项目后 compliance-overview 立即感知，零脏缓存
    create_res2 = await client.post(
        BASE,
        headers=member_a,
        json={
            "source_country": "中国",
            "recipient_country": "新加坡",
            "function": "connected_vehicle_cloud",
            "company_name": "华拓汽车",
        },
    )
    assert create_res2.status_code == 201
    ov_member_a_2 = (await client.get(f"{BASE}/compliance-overview", headers=member_a)).json()
    assert len(ov_member_a_2["recent_docs"]) == len(ov_member_a["recent_docs"]) + 1
    assert any(mr["recipient_code"] == "SG" for mr in ov_member_a_2["map_routes"])

    # 验证风险等级轨迹方向标签：上调显示“已上调”，降级显示“已降级”，整改显示“已整改”
    up_blocks = risk_items.blocks(
        json.dumps(
            [
                {
                    "id": "u1",
                    "risk": "实测发现新增敏感生物特征回传",
                    "init_likelihood": "低",
                    "init_severity": "中",
                    "likelihood": "高",
                    "severity": "高",
                    "measures": "待补齐网关脱敏",
                    "mitigated": "",
                }
            ],
            ensure_ascii=False,
        )
    )
    up_txt = _flatten_blocks_text(up_blocks)
    assert "原风险 1（已上调）" in up_txt
    assert "原风险 1（已降级）" not in up_txt
