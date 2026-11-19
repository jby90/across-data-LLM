"""对话联动填报（方案甲）与官方原表去“模板”二字回归测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.analysis import copilot_fill, document_basis, documents_router
from app.forms import service as form_service

from .conftest import bearer, login
from .test_admission import _entry
from .test_doc_projects import BASE, _make


def _llm_res(data: dict):
    return SimpleNamespace(data=data, prompt_tokens=10, completion_tokens=10)


def test_no_template_word_in_cn_official_names_or_ma_f113():
    for meta in documents_router._CN_OFFICIAL:
        assert "模板" not in meta["name_zh"], meta["code"]
    for lines in document_basis._FIXED.values():
        for text in lines:
            assert "官方模板" not in text and "（模板）" not in text
    for code in ("declaration", "risk_report", "contract", "dpia", "lia", "tia"):
        for text in document_basis.basis_lines(code, "EG", {"source_country": "中国", "recipient_country": "埃及"}):
            assert "官方模板" not in text and "（模板）" not in text
    f113 = form_service.get_form("MA_F113")
    assert f113 is not None
    dumped = json.dumps(f113, ensure_ascii=False)
    assert "模板" not in dumped


async def test_doc_project_copilot_extract_apply_and_undo(client, seed, monkeypatch):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, source_country="中国", recipient_country="摩洛哥", function="ota_diagnostics")

    # 纯提问不触发提取
    r_q = await client.post(f"{BASE}/{p['id']}/copilot-extract", headers=auth, json={"message": "合同怎么填？"})
    assert r_q.status_code == 200 and r_q.json()["proposals"] == []

    async def fake_llm(messages, **_kw):
        payload = json.loads(messages[-1]["content"])
        keys = {f["key"] for f in payload["可填栏目"]}
        out = []
        if "company_name" in keys:
            out.append({"key": "company_name", "value": "启航新能源汽车有限公司", "quote": "启航新能源汽车有限公司"})
        if "sensitive_pi" in keys:
            out.append({"key": "sensitive_pi", "value": "是，涉及精确位置约 2 万人", "quote": "涉及精确位置约 2 万人"})
        if "retention_and_blackbox" in keys:
            # 故意混入原文没有的数字 999，验证防幻觉拦截
            out.append({"key": "retention_and_blackbox", "value": "保存 999 年", "quote": "保存"})
        if "purpose" in keys:
            # 故意给不存在的 quote，验证原话摘抄校验拦截
            out.append({"key": "purpose", "value": "远程故障诊断", "quote": "这段话用户根本没说过"})
        if "risk_rating" in keys:
            out.append({
                "key": "risk_rating",
                "value": "",
                "quote": "跨境传输链路可能被截获，可能性低、严重程度中，采用 TLS 1.3 加密",
                "risk_row": {
                    "risk": "跨境传输链路可能被截获",
                    "likelihood": "低",
                    "severity": "中",
                    "measures": "采用 TLS 1.3 加密",
                    "owner": "",
                    "due": "",
                },
            })
        return _llm_res({"proposals": out})

    monkeypatch.setattr(copilot_fill, "call_llm_json", fake_llm)
    msg = (
        "我们公司叫启航新能源汽车有限公司，这次出境涉及精确位置约 2 万人；"
        "风险主要是跨境传输链路可能被截获，可能性低、严重程度中，采用 TLS 1.3 加密。"
    )
    r = await client.post(f"{BASE}/{p['id']}/copilot-extract", headers=auth, json={"message": msg, "history": []})
    assert r.status_code == 200
    props = {x["key"]: x for x in r.json()["proposals"]}
    assert set(props) == {"company_name", "sensitive_pi", "risk_rating"}
    assert props["company_name"]["target"] == "route"
    assert props["sensitive_pi"]["value"] == "是，涉及精确位置约 2 万人"
    assert "TLS 1.3" in props["risk_rating"]["display_value"]

    # 模拟前端“一键填入”与“撤回本次填入”
    assert (await client.patch(f"{BASE}/{p['id']}", headers=auth, json={"company_name": props["company_name"]["value"]})).status_code == 200
    applied = (
        await client.put(
            f"{BASE}/{p['id']}/answers",
            headers=auth,
            json={"answers": {"sensitive_pi": props["sensitive_pi"]["value"], "risk_rating": props["risk_rating"]["value"]}},
        )
    ).json()
    assert applied["route"]["company_name"] == "启航新能源汽车有限公司"
    q_map = {q["key"]: q["value"] for q in applied["questions"]}
    assert q_map["sensitive_pi"] == "是，涉及精确位置约 2 万人"
    assert q_map["risk_rating"].startswith("[")

    # 撤回：恢复 previous_value
    assert (await client.patch(f"{BASE}/{p['id']}", headers=auth, json={"company_name": props["company_name"]["previous_value"]})).status_code == 200
    undone = (
        await client.put(
            f"{BASE}/{p['id']}/answers",
            headers=auth,
            json={"answers": {"sensitive_pi": props["sensitive_pi"]["previous_value"], "risk_rating": props["risk_rating"]["previous_value"]}},
        )
    ).json()
    q_after = {q["key"]: q["value"] for q in undone["questions"]}
    assert q_after["sensitive_pi"] == "" and q_after["risk_rating"] == ""


async def test_official_form_assist_translates_and_supports_undo(client, seed, monkeypatch):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    inst = (await client.post("/api/forms/instances", headers=auth, json={"form_code": "MA_F118"})).json()
    first_text = next(
        it for sec in inst["sections"] for it in sec["items"] if it["kind"] == "text"
    )

    async def fake_llm(messages, **_kw):
        sys_prompt = messages[0]["content"]
        if "只输出 JSON：{\"reply\"" in sys_prompt:
            return _llm_res({"reply": "已为你把企业名称译为法语，请核对下方卡片后点一键填入。"})
        payload = json.loads(messages[-1]["content"])
        ids = {x["id"] for x in payload["本批栏位列表"]}
        if first_text["id"] in ids:
            return _llm_res({
                "proposals": [
                    {
                        "key": first_text["id"],
                        "value": "Qihang Auto Maroc SARL",
                        "note_zh": "启航汽车摩洛哥有限责任公司",
                        "quote": "启航汽车摩洛哥有限责任公司",
                    }
                ]
            })
        return _llm_res({"proposals": []})

    monkeypatch.setattr(copilot_fill, "call_llm_json", fake_llm)
    r = await client.post(
        f"/api/forms/instances/{inst['id']}/assist",
        headers=auth,
        json={"message": "申请企业是启航汽车摩洛哥有限责任公司", "history": []},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["proposals"]) == 1
    p = body["proposals"][0]
    assert p["key"] == first_text["id"]
    assert p["value"] == "Qihang Auto Maroc SARL"
    assert "中文核对：" in p["note"]

    # 一键填入
    saved = (await client.put(f"/api/forms/instances/{inst['id']}/answers", headers=auth, json={"answers": {p["key"]: p["value"]}})).json()
    item_now = next(it for sec in saved["sections"] for it in sec["items"] if it["id"] == p["key"])
    assert item_now["value"] == "Qihang Auto Maroc SARL" and item_now["source"] == "answer"

    # 撤回本次填入（传 previous_value="" 清空）
    reverted = (await client.put(f"/api/forms/instances/{inst['id']}/answers", headers=auth, json={"answers": {p["key"]: p["previous_value"]}})).json()
    item_reverted = next(it for sec in reverted["sections"] for it in sec["items"] if it["id"] == p["key"])
    assert item_reverted["source"] != "answer"


async def test_admission_assist_and_undo_clear(client, seed, session_factory, monkeypatch):
    entry_id, _ = await _entry(session_factory)
    auth = bearer(await login(client, "user", "a-member@example.com"))
    proj = (await client.post("/api/admission/projects", headers=auth, json={"entry_id": entry_id, "title": "测试准入"})).json()
    qs = (await client.get(f"/api/admission/projects/{proj['id']}/questions", headers=auth)).json()["questions"]
    assert len(qs) >= 1
    target_q = qs[0]

    async def fake_llm(_messages, **_kw):
        return _llm_res({
            "reply": "已识别出申报信息，请核对下方卡片后点一键填入。",
            "proposals": [
                {"key": target_q["key"], "value": "启航汽车销售有限公司", "quote": "启航汽车销售有限公司"},
            ],
        })

    monkeypatch.setattr(copilot_fill, "call_llm_json", fake_llm)
    r = await client.post(
        f"/api/admission/projects/{proj['id']}/assist",
        headers=auth,
        json={"message": "我们的当地主体是启航汽车销售有限公司", "history": []},
    )
    assert r.status_code == 200
    props = r.json()["proposals"]
    assert len(props) == 1 and props[0]["key"] == target_q["key"] and props[0]["previous_value"] == ""

    # 一键填入
    r_save = await client.post(
        f"/api/admission/projects/{proj['id']}/answers",
        headers=auth,
        json={"answers": {target_q["key"]: props[0]["value"]}},
    )
    assert r_save.status_code == 200
    remaining_keys = {q["key"] for q in r_save.json()["questions"]}
    assert target_q["key"] not in remaining_keys

    # 撤回本次填入：传空字符串清空刚填的项，回到未填问题列表
    r_undo = await client.post(
        f"/api/admission/projects/{proj['id']}/answers",
        headers=auth,
        json={"answers": {target_q["key"]: props[0]["previous_value"]}},
    )
    assert r_undo.status_code == 200
    restored_keys = {q["key"] for q in r_undo.json()["questions"]}
    assert target_q["key"] in restored_keys
