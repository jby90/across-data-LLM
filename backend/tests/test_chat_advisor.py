"""The consulting assistant: opens with one line, answers, guides, compares files and — when asked —
creates the documents project from the user's own words; it never chases the user for information."""

from types import SimpleNamespace

import pytest

from app.analysis import chat_docs

from . import chat_fakes
from .conftest import bearer, login


async def send(client, auth, tid, content):
    r = await client.post(f"/api/analysis/tasks/{tid}/messages", headers=auth, json={"content": content})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
async def advice(client, seed, monkeypatch):
    chat_fakes.install(monkeypatch)
    auth = bearer(await login(client, "user", "a-member@example.com"))
    r = await client.post("/api/analysis/chats?advice=true", headers=auth)
    assert r.status_code == 201, r.text
    return auth, r.json()


def _route_model(monkeypatch, reading):
    async def fake(messages, **kw):
        return SimpleNamespace(data=reading, prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(chat_docs, "call_llm_json", fake)


async def test_a_new_consulting_chat_opens_with_one_line_and_no_buttons(client, advice):
    auth, d = advice
    msgs = d["messages"]
    assert len(msgs) == 1 and msgs[0]["options"] == [] and len(msgs[0]["content"]) <= 40
    listed = (await client.get("/api/analysis/tasks", headers=auth)).json()
    assert d["id"] in {t["id"] for t in listed}  # 和旧对话一起出现在咨询对话列表里
    docs = (await client.get("/api/analysis/tasks?kind=documents", headers=auth)).json()
    assert d["id"] not in {t["id"] for t in docs}


async def test_what_the_platform_does_with_data_is_a_fixed_statement(client, advice):
    auth, d = advice
    for q in ("你们会保密吗", "我上传的数据会不会外泄？"):
        out = await send(client, auth, d["id"], q)
        assert "只保存在你所在企业的空间里" in out["messages"][-1]["content"] and "审计记录" in out["messages"][-1]["content"]


async def test_a_reference_with_nothing_to_refer_to_is_asked_about_not_guessed(client, advice):
    auth, d = advice
    out = await send(client, auth, d["id"], "这个跟我有什么关系")
    assert "是指什么" in out["messages"][-1]["content"]


async def test_business_talk_is_never_chased_with_questions_and_no_assessment_starts(client, advice):
    auth, d = advice
    out = await send(client, auth, d["id"], "我们在埃及有一支测试车队，数据传回中国")
    last = out["messages"][-1]
    assert out["status"] == "collecting_facts" and last["options"] == []
    assert "请问" not in last["content"] and "请选择" not in last["content"] and "帮我生成文件" in last["content"]
    assert [m["role"] for m in out["messages"]].count("assistant") == 2  # 问候 + 这一条回复，没有追问


async def test_a_law_question_is_answered_from_the_library_and_nothing_is_tacked_on(client, advice):
    auth, d = advice
    out = await send(client, auth, d["id"], "摩洛哥传数据出境有什么规定")
    last = out["messages"][-1]["content"]
    assert last and "接着要确认" not in last and "请回答" not in last


async def test_unrelated_talk_is_declined_politely_with_a_way_back(client, advice):
    auth, d = advice
    out = await send(client, auth, d["id"], "今天天气怎么样")
    last = out["messages"][-1]["content"]
    assert "只做汽车数据出境合规" in last and "合规文件" in last


async def test_a_data_handling_question_in_any_wording_gets_the_fixed_statement_and_help_gets_the_intro(client, seed, monkeypatch):
    chat_fakes.install(monkeypatch, rules=[(("拿去做训练",), {"intents": ["ask_privacy"]}), (("到底能帮我做什么",), {"intents": ["help"]})])
    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = (await client.post("/api/analysis/chats?advice=true", headers=auth)).json()["id"]
    out = await send(client, auth, tid, "我们传上去的资料会被你们拿去做训练吗")  # 说法不在关键词里，由理解层判断
    assert "只保存在你所在企业的空间里" in out["messages"][-1]["content"]
    out = await send(client, auth, tid, "我对这块不太懂，你到底能帮我做什么")
    last = out["messages"][-1]
    assert "8 类合规文件" in last["content"] and last["options"] == []


async def test_asking_for_the_documents_creates_the_project_from_the_users_own_words(client, advice, monkeypatch):
    auth, d = advice
    tid = d["id"]
    await send(client, auth, tid, "我们在埃及有一支测试车队，数据传回国内总部")
    _route_model(monkeypatch, {"source_country": "埃及", "recipient_country": "中国", "function": "autonomous_trial", "business": "测试车队数据回传",
                               "vehicle": "测试车队", "evidence": {"source": "在埃及有一支测试车队", "recipient": "数据传回国内总部"}})
    out = await send(client, auth, tid, "帮我生成文件")
    last = out["messages"][-1]["content"]
    projects = (await client.get("/api/analysis/doc-projects", headers=auth)).json()
    assert len(projects) == 1 and projects[0]["source_country"] == "埃及" and projects[0]["recipient_country"] == "中国"
    assert f"(/documents?project={projects[0]['id']})" in last and "？" not in last


async def test_a_country_not_in_the_users_words_is_never_taken_and_one_plain_question_asks_for_the_route(client, advice, monkeypatch):
    auth, d = advice
    tid = d["id"]
    # 模型说了“新加坡”，但用户从没提过：不能拿来建项目
    _route_model(monkeypatch, {"source_country": "埃及", "recipient_country": "新加坡", "function": "", "business": "", "vehicle": "",
                               "evidence": {"source": "埃及", "recipient": "新加坡"}})
    out = await send(client, auth, tid, "我们在埃及做车联网，帮我生成文件")
    assert "从哪个国家发出、传到哪个国家" in out["messages"][-1]["content"]
    assert (await client.get("/api/analysis/doc-projects", headers=auth)).json() == []
    _route_model(monkeypatch, {"source_country": "埃及", "recipient_country": "德国", "function": "connected_vehicle_cloud", "business": "", "vehicle": "",
                               "evidence": {"source": "在埃及做车联网", "recipient": "传到德国"}})
    out = await send(client, auth, tid, "数据传到德国的云")  # 回答了这一个问题：直接建好
    projects = (await client.get("/api/analysis/doc-projects", headers=auth)).json()
    assert len(projects) == 1 and projects[0]["recipient_country"] == "德国"
    assert "打开这个项目" in out["messages"][-1]["content"]


async def test_a_city_the_user_wrote_counts_for_its_country_but_a_place_the_user_never_wrote_does_not(client, advice, monkeypatch):
    auth, d = advice
    tid = d["id"]
    await send(client, auth, tid, "在突尼斯跑的卡车，司机图像传回武汉的研发中心")
    reading = {"source_country": "突尼斯", "recipient_country": "中国", "function": "cockpit_biometrics", "business": "", "vehicle": "",
               "evidence": {"source": "在突尼斯跑的卡车", "recipient": "传回武汉的研发中心"}}
    _route_model(monkeypatch, {**reading, "place": {"source": "突尼斯", "recipient": "上海"}})
    out = await send(client, auth, tid, "帮我生成文件")
    assert "从哪个国家发出、传到哪个国家" in out["messages"][-1]["content"]  # “上海”不在原话里：不认
    _route_model(monkeypatch, {**reading, "place": {"source": "突尼斯", "recipient": "武汉"}})
    await send(client, auth, tid, "帮我生成文件")
    projects = (await client.get("/api/analysis/doc-projects", headers=auth)).json()
    assert len(projects) == 1 and projects[0]["recipient_country"] == "中国"


async def test_after_the_project_is_created_what_the_user_says_in_the_chat_fills_it(client, advice, monkeypatch):
    from app.analysis import doc_projects

    auth, d = advice
    tid = d["id"]
    await send(client, auth, tid, "我们在埃及有一支测试车队，数据传回国内总部")
    _route_model(monkeypatch, {"source_country": "埃及", "recipient_country": "中国", "function": "autonomous_trial", "business": "", "vehicle": "",
                               "evidence": {"source": "在埃及有一支测试车队", "recipient": "数据传回国内总部"}})
    fed: list[tuple[str, str]] = []
    monkeypatch.setattr(doc_projects, "begin_suggest", lambda session, pid, text: fed.append((str(pid), text)))
    await send(client, auth, tid, "帮我生成文件")
    pid = (await client.get("/api/analysis/doc-projects", headers=auth)).json()[0]["id"]
    out = await send(client, auth, tid, "补充一下，数据在总部服务器保存一年，访问要审批")
    assert fed[-1] == (pid, "补充一下，数据在总部服务器保存一年，访问要审批")  # 接着说的业务情况交给项目预填
    assert f"(/documents?project={pid})" in out["messages"][-1]["content"]


async def test_someone_only_asking_for_advice_is_offered_the_documents_once_and_nothing_is_created(client, advice):
    auth, d = advice
    tid = d["id"]
    out = await send(client, auth, tid, "我们在埃及有一支测试车队，数据传回中国")
    assert "帮我生成文件" in out["messages"][-1]["content"]
    out = await send(client, auth, tid, "车上装的是激光雷达和摄像头")
    assert "帮我生成文件" not in out["messages"][-1]["content"]  # 只提一次，不机械重复
    assert (await client.get("/api/analysis/doc-projects", headers=auth)).json() == []  # 只是咨询：不自动建项目
