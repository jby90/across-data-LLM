"""“生成合规文件”：选出入境国和业务，直接在页面完成 8 类文件；先出初稿，缺的标“待补充”；论述后台生成；
打包下载；项目不出现在咨询对话列表里。"""

import asyncio
import io
import zipfile

import pytest

from app.analysis import document_narrative

from .conftest import bearer, login
from .test_analysis_p3 import _seed_sg_corpus
from .test_document_templates import _fake_paper_llm

BASE = "/api/analysis/doc-projects"


@pytest.fixture(autouse=True)
def _paper_model(monkeypatch):
    monkeypatch.setattr(document_narrative, "call_llm_json", _fake_paper_llm())


async def _make(client, auth, source="摩洛哥", recipient="中国", **extra):
    r = await client.post(BASE, headers=auth, json={"source_country": source, "recipient_country": recipient, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def test_a_project_shows_all_eight_documents_with_what_each_stands_on_and_how_much_is_missing(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, function="ota_diagnostics", business_scene="after_sales", vehicle_model="星途 X7", company_name="某车企")
    assert p["title"] == "摩洛哥 → 中国 · 远程诊断与OTA"
    docs = {d["code"]: d for d in p["documents"]}
    assert list(docs) == ["declaration", "risk_report", "contract", "remediation", "admission", "dpia", "lia", "tia"]
    assert docs["contract"]["basis_kind"] == "eu"  # 摩洛哥→中国：发出端不是中国，合同参照欧盟标准
    assert docs["declaration"]["basis_kind"] == "official" and "摩洛哥" in docs["declaration"]["basis_label"]  # 有摩洛哥官方表格
    assert docs["dpia"]["basis_kind"] == "eu" and docs["dpia"]["narrative"] is True
    assert all(d["state"] in ("not_started", "filling", "needs_paper", "ready") for d in docs.values())
    keys = [q["key"] for q in p["questions"]]
    assert len(keys) == len(set(keys)) and not {"source_country", "recipient_country", "company_name"} & set(keys)  # 项目级信息不重复问
    # 中国发出：合同、申报表、风险评估报告都按中国官方模板
    cn = await _make(client, auth, source="中国", recipient="新加坡", function="ota_diagnostics")
    kinds = {d["code"]: d["basis_kind"] for d in cn["documents"]}
    assert kinds["contract"] == kinds["declaration"] == kinds["risk_report"] == "official"
    # 两端都不在库里：照样能建，文件参照欧盟标准，不限定国家
    far = await _make(client, auth, source="日本", recipient="越南", scenario_text="车联网云平台")
    assert (await client.post(BASE, headers=auth, json={"source_country": "日本", "recipient_country": "越南"})).status_code == 422  # 没选业务场景
    assert far["route"]["library_country"] == "" and {d["basis_kind"] for d in far["documents"] if d["code"] == "contract"} == {"eu"}


async def test_answers_are_given_once_for_all_documents_and_a_draft_is_downloadable_before_everything_is_answered(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, function="ota_diagnostics", vehicle_model="星途 X7", company_name="某车企")
    pid = p["id"]
    before = sum(len(d["missing"]) for d in p["documents"])
    # 几份文件都还缺的那一项（按路径不出的章节里的项不算）
    missing_in = {}
    for d in p["documents"]:
        for m in d["missing"]:
            missing_in.setdefault(m["key"], []).append(d["code"])
    shared = next(q for q in p["questions"] if len(missing_in.get(q["key"], [])) > 1)
    r = await client.put(f"{BASE}/{pid}/answers", headers=auth, json={"answers": {shared["key"]: "已填的答案"}})
    assert r.status_code == 200
    after = sum(len(d["missing"]) for d in r.json()["documents"])
    assert after < before and next(q for q in r.json()["questions"] if q["key"] == shared["key"])["value"] == "已填的答案"  # 一处回答，几份文件都少一项
    assert (await client.put(f"{BASE}/{pid}/answers", headers=auth, json={"answers": {"no_such_key": "x"}})).status_code == 409
    assert (await client.put(f"{BASE}/{pid}/answers", headers=auth, json={"answers": {shared["key"]: ""}})).status_code == 200  # 清空
    # 没填完：成稿不能下，初稿能下（缺的标“待补充”）
    base = f"/api/analysis/tasks/{pid}/documents/declaration/export.docx"
    assert (await client.get(base, headers=auth)).status_code == 409
    d = await client.get(base + "?draft=true", headers=auth)
    assert d.status_code == 200 and d.content[:2] == b"PK" and "【待补充】".encode() in _docx_text(d.content)
    z = await client.get(f"{BASE}/{pid}/export.zip", headers=auth)
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    # 发出国摩洛哥有官方表格：申报表一栏放官方原表（PDF 原件，转移申请在前），其余 7 份是 Word
    assert [n for n in names if n.startswith("01")] and all(n.startswith("01") and n.endswith(".pdf") and "CNDP" in n for n in names if n.startswith("01"))
    assert names[0].startswith("01a_") and "F118" in names[0]
    assert len([n for n in names if not n.startswith("01")]) == 7 and all(n.endswith(".docx") for n in names if not n.startswith("01"))


def _docx_text(data: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read("word/document.xml")


async def test_the_argumentative_documents_are_written_in_the_background_and_the_state_is_visible(client, seed, session_factory):
    await _seed_sg_corpus(session_factory)
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, source="中国", recipient="新加坡", function="ota_diagnostics", vehicle_model="星途 X7", company_name="某车企")
    pid = p["id"]
    dpia = next(d for d in p["documents"] if d["code"] == "dpia")
    # 缺信息时不能生成论述，并说明缺什么
    r = await client.post(f"{BASE}/{pid}/documents/dpia/paper", headers=auth)
    assert r.status_code == 409 and "还有信息需要补充" in r.json()["detail"], (r.text, dpia["missing"], dpia["state"])
    answers = {m["key"]: f"回答{i}：这是用于测试的具体说明。" for i, m in enumerate(dpia["missing"])}
    if "risk_rating" in answers:  # 主要风险按行填：一段文字保留成一行、仍算缺项
        answers["risk_rating"] = '[{"id": "a", "risk": "被识别", "likelihood": "中", "severity": "高", "measures": "脱敏"}]'
    await client.put(f"{BASE}/{pid}/answers", headers=auth, json={"answers": answers})
    r = await client.post(f"{BASE}/{pid}/documents/dpia/paper", headers=auth)
    assert r.status_code == 202 and r.json()["state"] == "running", r.text
    for _ in range(100):
        cur = (await client.get(f"{BASE}/{pid}", headers=auth)).json()
        doc = next(d for d in cur["documents"] if d["code"] == "dpia")
        if doc["paper"]["state"] == "done":
            break
        await asyncio.sleep(0.2)
    assert doc["paper"]["state"] == "done" and doc["state"] == "ready"
    full = await client.get(f"/api/analysis/tasks/{pid}/documents/dpia/export.docx", headers=auth)
    assert full.status_code == 200  # 信息齐了、论述生成了：成稿可以下
    # 用户可能早已离开文件页：任何页面都能查到“刚刚完成了哪些论述”，用来提醒一次
    recent = (await client.get(f"{BASE}/papers/recent", headers=auth)).json()
    assert [(r["project_id"], r["code"], r["state"]) for r in recent] == [(pid, "dpia", "done")]
    later = (await client.get(f"{BASE}/papers/recent", headers=auth, params={"since": recent[0]["at"]})).json()
    assert later == []  # 提醒过的不再提醒
    other = bearer(await login(client, "user", "b-admin@example.com"))
    assert (await client.get(f"{BASE}/papers/recent", headers=other)).json() == []  # 别的企业看不到


async def test_projects_are_separate_from_chats_and_can_be_renamed_archived_and_restored(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    other = bearer(await login(client, "user", "b-admin@example.com"))
    p = await _make(client, auth, scenario_text="数据回传总部做分析")
    pid = p["id"]
    chats = (await client.get("/api/analysis/tasks?limit=200", headers=auth)).json()
    assert all(t["id"] != pid for t in (chats if isinstance(chats, list) else chats.get("items", [])))  # 不混进咨询对话列表
    assert any(x["id"] == pid for x in (await client.get(BASE, headers=auth)).json())
    assert (await client.get(f"{BASE}/{pid}", headers=other)).status_code == 404  # 别的企业看不到
    r = await client.patch(f"{BASE}/{pid}", headers=auth, json={"title": "埃及项目文件", "recipient_country": "新加坡"})
    assert r.json()["title"] == "埃及项目文件" and r.json()["route"]["recipient_country"] == "新加坡"
    # 删除=归档（沿用对话的接口），归档后只读，恢复后继续
    assert (await client.delete(f"/api/analysis/tasks/{pid}", headers=auth)).json()["archived"] is True
    assert all(x["id"] != pid for x in (await client.get(BASE, headers=auth)).json())
    assert any(x["id"] == pid for x in (await client.get(f"{BASE}?archived=true", headers=auth)).json())
    assert (await client.put(f"{BASE}/{pid}/answers", headers=auth, json={"answers": {}})).status_code == 409
    assert (await client.post(f"/api/analysis/tasks/{pid}/restore", headers=auth)).json()["restored"] is True
    assert (await client.put(f"{BASE}/{pid}/answers", headers=auth, json={"answers": {}})).status_code == 200


# ---- 少填：从用户自己的话里预填 ----
TEXT = "我们公司叫远行出行，车队的故障日志每天通过加密通道回传北京运维中心，数据只保存在北京的服务器，访问需要审批。"


def _fake_suggest_llm(answers):
    from types import SimpleNamespace

    async def fake(messages, **kw):
        return SimpleNamespace(data={"answers": answers}, prompt_tokens=1, completion_tokens=1)

    return fake


async def _wait_suggest(client, auth, pid):
    for _ in range(100):
        cur = (await client.get(f"{BASE}/{pid}", headers=auth)).json()
        if cur["suggest"]["state"] != "running":
            return cur
        await asyncio.sleep(0.1)
    raise AssertionError("建议一直没出来")


async def test_suggested_answers_need_a_real_quote_and_reach_the_documents_only_when_adopted(client, seed, monkeypatch):
    from app.analysis import doc_suggest

    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, function="ota_diagnostics")
    pid = p["id"]
    keys = {q["key"]: q for q in p["questions"]}
    assert "purpose" in keys and keys["entity_nature"]["tier"] == "company" and keys["purpose"]["tier"] == "business"
    good = {"key": "retention_and_blackbox", "value": "保存在北京的服务器，访问需审批。", "source": "text", "quote": "数据只保存在北京的服务器，访问需要审批"}
    answers = [
        good,
        {"key": "company_name", "value": "远行出行", "source": "text", "quote": "我们公司叫远行出行"},
        {"key": "risk_measures", "value": "已做了加密", "source": "text", "quote": "原文里根本没有这句话"},  # 摘抄对不上原文
        {"key": "data_volume", "value": "约 500 万条", "source": "text", "quote": "访问需要审批"},  # 数字不在原文里
        {"key": "recipient", "value": "北京运维中心", "source": "general", "quote": ""},  # 这一项不许凭空拟
        {"key": "purpose", "value": "说明里未提及目的", "source": "text", "quote": "访问需要审批"},  # 夹带评语
    ]
    monkeypatch.setattr(doc_suggest, "call_llm_json", _fake_suggest_llm(answers))
    assert (await client.post(f"{BASE}/{pid}/suggest", headers=auth, json={"text": TEXT})).status_code == 202
    cur = await _wait_suggest(client, auth, pid)
    got = {q["key"]: q["suggestion"] for q in cur["questions"] if q["suggestion"]}
    assert set(got) == {"retention_and_blackbox"} and set(cur["route_suggestions"]) == {"company_name"}, cur["suggest"]
    assert all(q["value"] == "" for q in cur["questions"] if q["key"] == "retention_and_blackbox")  # 没采用前不进文件
    r = await client.post(f"{BASE}/{pid}/suggestions/accept", headers=auth, json={"keys": None})
    cur = r.json()
    assert next(q for q in cur["questions"] if q["key"] == "retention_and_blackbox")["value"].startswith("保存在北京")
    assert cur["route"]["company_name"] == "远行出行" and not cur["route_suggestions"] and cur["suggest"]["count"] == 0
    # 不要：丢掉建议，不改答案
    monkeypatch.setattr(doc_suggest, "call_llm_json", _fake_suggest_llm([{**good, "key": "storage_chain", "value": "北京服务器"}]))
    await client.post(f"{BASE}/{pid}/suggest", headers=auth, json={"text": TEXT})
    cur = await _wait_suggest(client, auth, pid)
    cur = await _wait_suggest(client, auth, pid)
    cur = (await client.post(f"{BASE}/{pid}/suggestions/accept", headers=auth, json={"keys": ["storage_chain"], "dismiss": True})).json()
    assert next(q for q in cur["questions"] if q["key"] == "storage_chain")["value"] == ""


async def test_without_the_users_words_only_the_plain_items_get_a_general_draft_and_all_models_failing_is_reported(client, seed, monkeypatch):
    from app.analysis import doc_suggest

    auth = bearer(await login(client, "user", "a-member@example.com"))
    pid = (await _make(client, auth, function="ota_diagnostics"))["id"]
    asked: list[list[str]] = []

    async def fake(messages, **kw):
        import json as _j
        from types import SimpleNamespace

        asked.append([q["key"] for q in _j.loads(messages[1]["content"])["questions"]])
        return SimpleNamespace(data={"answers": [{"key": "purpose", "value": "拟用于远程诊断。", "source": "general", "quote": ""}]}, prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(doc_suggest, "call_llm_json", fake)
    await client.post(f"{BASE}/{pid}/suggest", headers=auth, json={"text": ""})
    cur = await _wait_suggest(client, auth, pid)
    assert [k for b in asked for k in b] == ["purpose", "industry_field", "scenario_summary"][: len([k for b in asked for k in b])] or set(k for b in asked for k in b) <= {"purpose", "industry_field", "scenario_summary"}
    assert cur["suggest"]["state"] == "done"

    async def broken(messages, **kw):
        from app.analysis.llm import LlmUnavailableError

        raise LlmUnavailableError("down")

    monkeypatch.setattr(doc_suggest, "call_llm_json", broken)
    await client.post(f"{BASE}/{pid}/suggest", headers=auth, json={"text": TEXT})
    cur = await _wait_suggest(client, auth, pid)
    assert cur["suggest"]["state"] == "failed" and "稍后再试" in cur["suggest"]["error"]


async def test_a_new_project_starts_with_the_company_details_already_given_before(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    first = await _make(client, auth, function="ota_diagnostics", company_name="某车企")
    await client.put(f"{BASE}/{first['id']}/answers", headers=auth, json={"answers": {"legal_rep": "张三，总经理", "entity_nature": "暂不确定（待补充）", "purpose": "诊断"}})
    second = await _make(client, auth, source="突尼斯", recipient="中国", function="autonomous_trial")
    qs = {q["key"]: q["value"] for q in second["questions"]}
    assert qs["legal_rep"] == "张三，总经理"  # 公司信息自动带入
    assert qs["entity_nature"] == "" and qs["purpose"] == ""  # “暂不确定”和业务细节不带
    assert second["route"]["company_name"] == "某车企"


async def test_what_describes_one_flow_is_not_carried_into_a_project_for_another(client, seed):
    """“数据处理者基本情况”常写着这次的流向（如“出口新加坡”），不能带到中国→埃及的新项目里。"""
    auth = bearer(await login(client, "user", "a-member@example.com"))
    first = await _make(client, auth, source="中国", recipient="新加坡", function="ota_diagnostics")
    await client.put(f"{BASE}/{first['id']}/answers", headers=auth, json={"answers": {"company_profile": "整车企业，出口新加坡智能网联SUV", "recipient_contact": "新加坡经销商张经理", "credit_code": "91330000TEST"}})
    second = await _make(client, auth, source="中国", recipient="埃及", function="ota_diagnostics")
    qs = {q["key"]: q["value"] for q in second["questions"]}
    assert qs["credit_code"] == "91330000TEST" and qs["company_profile"] == "" and qs["recipient_contact"] == ""


async def test_yes_no_questions_are_buttons_and_admission_matters_come_from_the_library_not_the_user(client, seed, session_factory):
    from sqlalchemy import select

    from app.admission.models import AdmissionEntry

    async with session_factory() as s:
        s.add_all([
            AdmissionEntry(country_code="MA", name_zh="个人数据跨境传输授权", category="data_filing", authority_zh="国家个人数据保护委员会（CNDP）", summary_zh="x", review_status="pending_review",
                           # 用户端只展示有依据法规、有办理环节的事项（与准入页同一口径）
                           profile={"cited_laws": ["09-08号法"]}, edited=["profile:cited_laws"]),
            AdmissionEntry(country_code="MA", name_zh="范围外的事项", category="other", authority_zh="某部门", summary_zh="x", review_status="out_of_scope"),
        ])
        await s.flush()
        from app.admission.models import AdmissionStep

        first = (await s.scalars(select(AdmissionEntry).where(AdmissionEntry.name_zh == "个人数据跨境传输授权"))).first()
        s.add(AdmissionStep(entry_id=first.id, seq=1, title_zh="提交授权申请", detail_zh="向 CNDP 提交", materials=[], evidence_quote="q"))
        await s.commit()
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, function="ota_diagnostics")
    qs = {q["key"]: q for q in p["questions"]}
    assert qs["sensitive_pi"]["choices"] == ["是", "否"] and qs["purpose"]["choices"] == []
    assert "entry_type" not in {m["key"] for d in p["documents"] if d["code"] == "admission" for m in d["missing"]}  # 不再向用户要
    z = await client.get(f"/api/analysis/tasks/{p['id']}/documents/admission/export.docx?draft=true", headers=auth)
    text = _docx_text(z.content).decode("utf-8")
    # 受理机构按库里认得的全称写（“摩洛哥国家个人数据保护监管委员会（CNDP）”）
    assert "个人数据跨境传输授权" in text and "（CNDP）" in text and "范围外的事项" not in text


def test_a_long_clause_is_cut_at_the_end_of_a_sentence_and_marked():
    from app.analysis.document_templates import _quote

    clause = "第一句话说明适用范围。" * 20 + "最后一句没有写完的话"
    cut = _quote({"quote_zh": clause}, limit=100)
    assert cut.endswith("。 ……") and len(cut) <= 106 and "没有写完" not in cut
    assert _quote({"quote_zh": "很短的条文。"}) == "很短的条文。"


async def test_the_companys_own_judgements_are_grouped_apart_and_never_drafted_by_the_system(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, function="ota_diagnostics")
    tiers = {q["key"]: q["tier"] for q in p["questions"]}
    assert tiers["risk_conclusion"] == tiers["issues"] == tiers["necessity"] == "system"
    assert tiers["legal_rep"] == "company" and tiers["data_types"] == "business"


async def test_any_country_is_recognised_on_either_end_and_the_basis_comes_from_whatever_the_library_holds(client, seed, session_factory):
    """出入境国家不限定：任何一端都按用户说的国家认出来；依据取自法规库里已收录条文的那一端（库里有什么由数据决定，
    新国家入库后自动用上，不改代码）。"""
    from app.analysis.models import AnalysisTask

    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, source="中国", recipient="日本", function="ota_diagnostics")
    async with session_factory() as s:
        task = await s.get(AnalysisTask, p["id"])
        assert task.country_code == "JP"  # 文件按接收端写；中国一端的条文另外并列
    jp_to_cn = await _make(client, auth, source="日本", recipient="中国", function="ota_diagnostics")
    async with session_factory() as s:
        assert (await s.get(AnalysisTask, jp_to_cn["id"])).country_code == "CN"  # 不限定国家：哪端都认得出来


async def test_a_country_whose_regulations_enter_the_library_is_quoted_without_code_change(client, seed, session_factory):
    await _seed_sg_corpus(session_factory)  # 库里只有新加坡的条文
    auth = bearer(await login(client, "user", "a-member@example.com"))
    sg = await _make(client, auth, source="中国", recipient="新加坡", function="ota_diagnostics")
    assert sg["route"]["library_country"] == "新加坡"
    de = await _make(client, auth, source="中国", recipient="德国", function="ota_diagnostics")
    assert de["route"]["library_country"] == ""  # 德国的条文还没入库：文件照样出，依据按官方/欧盟标准模板
    assert len(de["documents"]) == 8
    options = (await client.get(f"{BASE}/options", headers=auth)).json()
    assert "新加坡" in options["library_countries"] and "德国" in options["countries"]


async def test_the_assistant_beside_the_form_talks_about_one_question_and_only_proposes(client, seed, monkeypatch):
    from types import SimpleNamespace

    from app.analysis import doc_assist

    auth = bearer(await login(client, "user", "a-member@example.com"))
    pid = (await _make(client, auth, source="中国", recipient="摩洛哥", function="ota_diagnostics"))["id"]
    ask = lambda key, msg: client.post(f"{BASE}/{pid}/questions/{key}/ask", headers=auth, json={"message": msg})  # noqa: E731
    r = (await ask("sensitive_pi", "我们没有这个")).json()
    assert r["proposal"] == "否" and "采用" in r["reply"]  # 选择题：给出选项里的否定项
    r = (await ask("onward_provision", "不涉及")).json()
    assert r["proposal"] == "无"
    r = (await ask("purpose", "你们会保密吗")).json()
    assert "只保存在你所在企业的空间里" in r["reply"] and r["proposal"] == ""
    replies = iter([
        {"kind": "answer", "reply": "明白，数据存一年。", "proposal": "保存 1 年，存放在卡萨布兰卡的服务器"},
        {"kind": "answer", "reply": "已为您填写好了。", "proposal": "保存 3 年"},  # 宣称做过动作、出现用户没说过的数字
        {"kind": "off_topic", "reply": "这个我帮不上，我们接着看这一题。", "proposal": "不该出现"},
    ])

    async def fake(messages, **kw):
        return SimpleNamespace(data=next(replies), prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(doc_assist, "call_llm_json", fake)
    r = (await ask("retention_and_blackbox", "数据在卡萨布兰卡的服务器上存 1 年")).json()
    assert r["proposal"].startswith("保存 1 年") and "采用" in r["reply"]
    r = (await ask("retention_and_blackbox", "大概存一阵子")).json()
    assert r["proposal"] == "" and "已为您" not in r["reply"]
    r = (await ask("retention_and_blackbox", "推荐个餐厅")).json()
    assert r["proposal"] == "" and "帮不上" in r["reply"]
    cur = (await client.get(f"{BASE}/{pid}", headers=auth)).json()
    assert all(q["value"] == "" for q in cur["questions"] if q["key"] in ("sensitive_pi", "retention_and_blackbox"))  # 助手不替用户保存
    assert (await ask("no_such_key", "什么意思")).status_code == 404


async def test_a_foreign_regulation_is_cited_with_its_chinese_title_beside_the_original(client, seed, session_factory):
    from sqlalchemy import update

    from app.ingestion.models import Document

    await _seed_sg_corpus(session_factory)
    async with session_factory() as s:
        await s.execute(update(Document).where(Document.country_code == "SG").values(title_zh="新加坡个人数据保护法"))
        await s.commit()
    auth = bearer(await login(client, "user", "a-member@example.com"))
    # 依据取数据发出国的法律（新加坡发出）；中国出境的申报表/自评估报告是官方原件，不含依据表
    p = await _make(client, auth, source="新加坡", recipient="埃及", function="ota_diagnostics")
    d = await client.get(f"/api/analysis/tasks/{p['id']}/documents/risk_report/export.docx?draft=true", headers=auth)
    # 依据写《中文名》，原名在下一行（与法规浏览一致）
    assert "《新加坡个人数据保护法》".encode() in _docx_text(d.content)


async def test_the_project_has_its_own_chat_and_what_is_said_there_goes_into_the_project(client, seed, monkeypatch):
    from app.analysis import doc_projects

    from . import chat_fakes

    chat_fakes.install(monkeypatch)
    fed: list[tuple[str, str]] = []
    monkeypatch.setattr(doc_projects, "begin_suggest", lambda session, pid, text: fed.append((str(pid), text)))
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, source="中国", recipient="突尼斯", function="ota_diagnostics")
    first = (await client.post(f"{BASE}/{p['id']}/chat", headers=auth)).json()["chat_id"]
    assert (await client.post(f"{BASE}/{p['id']}/chat", headers=auth)).json()["chat_id"] == first  # 同一个项目始终是同一条对话
    chat = (await client.get(f"/api/analysis/tasks/{first}", headers=auth)).json()
    assert chat["country_code"] == "TN"  # 问法规默认按项目的国家
    listed = {t["id"] for t in (await client.get("/api/analysis/tasks", headers=auth)).json()}
    assert first in listed  # 咨询页里也能看到、接着聊
    r = await client.post(f"/api/analysis/tasks/{first}/messages", headers=auth, json={"content": "我们的车辆位置每天同步到苏州云平台，保存18个月"})
    assert r.status_code == 200 and fed == [(p["id"], "我们的车辆位置每天同步到苏州云平台，保存18个月")]


async def test_data_leaving_china_gets_the_declaration_on_the_official_cac_template_itself(client, seed):
    """申报表直接用网信办指南附件 3 的官方 Word 原件填写：表格、勾选框、说明原样，只填入内容。"""
    import io
    import zipfile
    from xml.etree import ElementTree

    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, source="中国", recipient="埃及", function="ota_diagnostics", company_name="华拓汽车有限公司")
    await client.put(f"{BASE}/{p['id']}/answers", headers=auth, json={"answers": {"sensitive_pi": "是", "is_cii": "否", "entity_nature": "企业"}})
    r = await client.get(f"/api/analysis/tasks/{p['id']}/documents/declaration/export.docx?draft=true", headers=auth)
    import urllib.parse

    assert r.status_code == 200 and "模板" not in urllib.parse.unquote(r.headers["content-disposition"])  # 文件名去掉“模板”二字
    z = zipfile.ZipFile(io.BytesIO(r.content))
    xml = z.read("word/document.xml").decode("utf-8")
    ElementTree.fromstring(xml)
    assert "数据出境安全评估申报表" in xml and "数据出境安全评估申报表（模板）" not in xml and "本单位承诺" in xml  # 官方原文在且去掉“（模板）”
    assert "华拓汽车有限公司" in xml and 'w:char="F0FE"' in xml  # 填进了名称，打了勾
    assert "附件4" not in xml and "数据出境风险自评估报告" not in xml  # 只截出附件 3
    assert "word/header1.xml" in z.namelist()  # 官方文件的页眉页脚、样式都保留
    other = await _make(client, auth, source="埃及", recipient="中国", function="ota_diagnostics")
    r = await client.get(f"/api/analysis/tasks/{other['id']}/documents/declaration/export.docx?draft=true", headers=auth)
    assert "本单位承诺" not in zipfile.ZipFile(io.BytesIO(r.content)).read("word/document.xml").decode("utf-8")  # 不是从中国出境：不用网信办原件


async def test_data_leaving_china_gets_the_risk_self_assessment_on_the_official_cac_template(client, seed):
    import io
    import zipfile

    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, source="中国", recipient="摩洛哥", function="ota_diagnostics", company_name="华拓汽车有限公司")
    await client.put(f"{BASE}/{p['id']}/answers", headers=auth, json={"answers": {"data_types": "故障日志、车辆位置", "storage_chain": "存储于杭州数据中心"}})
    r = await client.get(f"/api/analysis/tasks/{p['id']}/documents/risk_report/export.docx?draft=true", headers=auth)
    import re as _re
    import urllib.parse

    assert "模板" not in urllib.parse.unquote(r.headers["content-disposition"])
    raw = zipfile.ZipFile(io.BytesIO(r.content)).read("word/document.xml").decode("utf-8")
    xml = "".join(_re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", raw))  # 官方文件里一句话常被拆成好几段，按文字比
    assert "数据出境风险自评估报告" in xml and "数据出境风险自评估报告（模板）" not in xml and "一、自评估工作情况" in xml and "三、出境活动的风险自评估情况及结论" in xml  # 官方章节原样且去掉“（模板）”
    assert "存储于杭州数据中心" in xml and "车辆位置" in xml and "华拓汽车有限公司" in xml  # 各节下填入内容、数据项清单、封面名称
    assert "【待补充】" in xml and "延长评估结果有效期申请表" not in xml  # 初稿标出待补充；只截出附件 4
    final = await client.get(f"/api/analysis/tasks/{p['id']}/documents/risk_report/export.docx", headers=auth)
    assert final.status_code == 409  # 没填完不能出成稿


async def test_asking_how_to_fill_a_document_in_the_project_chat_is_answered_about_this_project(client, seed, monkeypatch):
    """项目旁边的对话知道是哪个项目：问“合同怎么填”按这个项目答（用到哪些项、还缺哪些），不给通用介绍。"""
    from types import SimpleNamespace

    from app.analysis import project_guide

    from . import chat_fakes

    chat_fakes.install(monkeypatch, rules=[(("怎么填",), {"intents": ["help"]})])
    seen: dict = {}

    async def fake(messages, **_kw):
        import json

        seen.update(json.loads(messages[1]["content"]))
        return SimpleNamespace(data={"reply": "传输合同用到传输目的、传输的数据类型、境外接收方等项，目前境外接收方还空着，按“名称、所在国家或地区、地址、主营业务”写。"}, prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(project_guide, "call_llm_json", fake)
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, source="中国", recipient="新加坡", function="ota_diagnostics")
    chat = (await client.post(f"{BASE}/{p['id']}/chat", headers=auth)).json()["chat_id"]
    out = (await client.post(f"/api/analysis/tasks/{chat}/messages", headers=auth, json={"content": "合同怎么填"})).json()
    last = out["messages"][-1]["content"]
    assert "境外接收方" in last and "8 类合规文件的初稿建好" not in last
    assert seen["project"]["route"]["source_country"] == "中国" and seen["project"]["route"]["recipient_country"] == "新加坡"
    recipient = next(it for it in seen["project"]["items"] if it["label"] == "境外接收方")
    assert recipient["value"] == "（空）" and any("合同" in d for d in recipient["used_in"])  # 带着每一项的填写情况和用途
    listed = (await client.get("/api/analysis/tasks", headers=auth)).json()
    assert next(t for t in listed if t["id"] == chat)["project_id"] == p["id"]  # 咨询列表里能看出这条对话属于哪个项目


async def test_each_item_says_how_to_fill_it_and_which_documents_use_it(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, source="中国", recipient="新加坡", function="ota_diagnostics")
    view = (await client.get(f"{BASE}/{p['id']}", headers=auth)).json()
    qs = {q["key"]: q for q in view["questions"]}
    assert qs["recipient"]["hint"] and "申报表" in qs["recipient"]["used_for"] and "传输合同" in qs["recipient"]["used_for"]
    assert qs["entity_nature"]["choices"][:2] == ["企业", "事业单位"] and qs["entity_nature"]["other"] is True  # 官方表上的勾选项
    assert all(q["hint"] for q in view["questions"])  # 每一项都有填写说明


async def test_the_chat_beside_a_project_goes_with_the_project_when_it_is_archived_restored_or_deleted(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, source="中国", recipient="埃及", function="ota_diagnostics")
    chat = (await client.post(f"{BASE}/{p['id']}/chat", headers=auth)).json()["chat_id"]
    listed = lambda r: {t["id"] for t in r.json()}  # noqa: E731
    await client.delete(f"/api/analysis/tasks/{p['id']}", headers=auth)
    assert chat not in listed(await client.get("/api/analysis/tasks", headers=auth))  # 项目归档，旁边的对话也归档
    await client.post(f"/api/analysis/tasks/{p['id']}/restore", headers=auth)
    assert chat in listed(await client.get("/api/analysis/tasks", headers=auth))
    await client.delete(f"/api/analysis/tasks/{p['id']}", headers=auth)
    await client.delete(f"/api/analysis/tasks/{p['id']}?permanent=true", headers=auth)
    assert (await client.get(f"/api/analysis/tasks/{chat}", headers=auth)).status_code == 404  # 彻底删除时一起删


async def test_every_project_says_for_each_of_the_eight_documents_whether_it_is_needed(client, seed):
    """适用性判断不限出境国家：8 份文件每份都有“需要 / 按目前情况不需要 / 还不能判断”和原因；
    法规库里没有发出国的要求时按通用要求判“需要”；答了能判断的题，结论跟着变。"""
    auth = bearer(await login(client, "user", "a-member@example.com"))
    for source, recipient in (("中国", "埃及"), ("摩洛哥", "中国"), ("新加坡", "中国"), ("日本", "越南")):
        p = await _make(client, auth, source=source, recipient=recipient, function="connected_vehicle_cloud")
        appl = p["applicability"]
        assert set(appl) == {d["code"] for d in p["documents"]}
        assert all(a["state"] in ("need", "na", "unknown") and a["why"] for a in appl.values())
        if source != "中国":
            assert appl["declaration"]["state"] == "need"  # 测试库里没有发出国的要求：按通用要求，需要
            assert "按各国通行的要求" in appl["declaration"]["why"]
    cn = await _make(client, auth, source="中国", recipient="埃及", function="connected_vehicle_cloud")
    assert cn["applicability"]["declaration"]["state"] == "unknown"
    assert cn["applicability"]["declaration"]["missing_keys"]
    r = await client.put(f"{BASE}/{cn['id']}/answers", headers=auth, json={"answers": {
        "is_cii": "否", "important_data": "否", "sensitive_pi": "否", "data_volume": "约 3 万人", "lawful_basis": "取得同意", "issues": "无",
    }})
    assert r.status_code == 200, r.text
    v = (await client.get(f"{BASE}/{cn['id']}", headers=auth)).json()["applicability"]
    assert v["declaration"]["state"] == "na" and "约 30,000 人" in v["declaration"]["why"]
    assert v["lia"]["state"] == "na" and v["remediation"]["state"] == "na"
    assert v["contract"]["state"] == "need" and v["risk_report"]["state"] == "na" and v["dpia"]["state"] == "need"


async def test_each_document_has_one_of_four_states_and_the_final_package_waits_only_for_the_needed_ones(client, seed):
    """四态：未开始 / 填写中 / 待生成论述 / 已就绪；没有论述环节的文件不出现“待生成论述”。
    成稿：要的文件（需要 / 还不能判断）没全部就绪，后端拒绝打包；缺项只算要的文件缺的，按段列出。"""
    from app.analysis import project_guide

    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, source="中国", recipient="埃及", function="connected_vehicle_cloud")
    docs = {d["code"]: d for d in p["documents"]}
    assert docs["dpia"]["state"] == "not_started"
    assert all(d["state"] != "needs_paper" for d in docs.values() if not d["narrative"])
    only_dpia = next(q for q in p["questions"] if q["used_in"] == ["dpia"] and not q["optional"])
    v = (await client.put(f"{BASE}/{p['id']}/answers", headers=auth, json={"answers": {only_dpia["key"]: "已填"}})).json()
    assert next(d for d in v["documents"] if d["code"] == "dpia")["state"] == "filling"
    final = v["final"]
    assert final["ok"] is False and final["missing"]
    need = {d["code"] for d in v["documents"] if v["applicability"][d["code"]]["state"] != "na"}
    for m in final["missing"]:
        assert m["tier"] in ("business", "company", "system", "project")
        assert any(m["key"] in [x["key"] for x in d["missing"]] for d in v["documents"] if d["code"] in need)
    r = await client.get(f"{BASE}/{p['id']}/export.zip?draft=false", headers=auth)
    assert r.status_code == 409 and "还不能下载成稿" in r.json()["detail"]
    # 答到申报表、LIA、整改报告按目前情况不需要：它们没就绪也不拦成稿，只在“不放进成稿包”里列出
    v = (await client.put(f"{BASE}/{p['id']}/answers", headers=auth, json={"answers": {
        "is_cii": "否", "important_data": "否", "sensitive_pi": "否", "data_volume": "约 3 万人", "lawful_basis": "取得同意", "issues": "无",
    }})).json()
    skipped = {d["code"] for d in v["final"]["skipped"]}
    assert {"declaration", "lia", "remediation"} <= skipped
    na_only = {q["key"] for q in v["questions"] if set(q["used_in"]) <= skipped}
    assert not na_only & {m["key"] for m in v["final"]["missing"]}
    text = project_guide.fallback(v)
    assert "按目前情况不需要" in text and ("未开始" in text or "填写中" in text)
    view = project_guide._project_view(v)
    assert {d["state"] for d in view["documents"]} <= {"未开始", "填写中", "待写分析", "已就绪"}


def test_final_status_lists_what_blocks_and_leaves_out_unneeded_unfinished_documents():
    from app.analysis.doc_projects import doc_state, final_status

    assert [doc_state(3, False, False), doc_state(3, True, True), doc_state(0, True, True), doc_state(0, False, False)] == [
        "not_started", "filling", "needs_paper", "ready"]
    q = [{"key": "a", "tier": "business"}, {"key": "b", "tier": "company"}]
    docs = [
        {"code": "x", "name": "X", "state": "filling", "missing": [{"key": "a", "label": "甲"}, {"key": "vehicle_model", "label": "车型"}]},
        {"code": "y", "name": "Y", "state": "filling", "missing": [{"key": "b", "label": "乙"}]},
        {"code": "z", "name": "Z", "state": "needs_paper", "missing": []},
        {"code": "w", "name": "W", "state": "ready", "missing": []},
    ]
    f = final_status(docs, {"y": {"state": "na"}, "x": {"state": "unknown"}}, q)
    assert f["ok"] is False and [(m["key"], m["tier"]) for m in f["missing"]] == [("a", "business"), ("vehicle_model", "project")]
    assert f["papers"] == [{"code": "z", "name": "Z"}] and f["skipped"] == [{"code": "y", "name": "Y"}]
    docs[0]["state"] = docs[2]["state"] = "ready"
    f = final_status(docs, {"y": {"state": "na"}}, q)
    assert f["ok"] is True and f["skipped"] == [{"code": "y", "name": "Y"}]
