"""The assistant understands before it acts. Every kind of input has somewhere to go (a question about
the law, progress, help, another part of the platform, a correction, small talk, a complaint); a
question is never recorded as a fact; only talk with nothing to do with the platform is declined —
politely, with what the assistant can do; and the history stays so the conversation can be taken up
again days later."""

import asyncio
import io
import json
import zipfile

import pytest

from app.analysis import attachment_jobs, chat_intent, gap_review

from . import chat_fakes
from .conftest import bearer, login
from .test_analysis_p3 import _seed_sg_corpus


async def send(client, auth, tid, text, ids=()):
    r = await client.post(
        f"/api/analysis/tasks/{tid}/messages", headers=auth, json={"content": text, "attachment_ids": list(ids)}
    )
    assert r.status_code == 200, r.text
    return r.json()


def last(d):
    return d["messages"][-1]


def said(d):
    """Everything the assistant said after the user's last message (a reply, then maybe the question as its own message)."""
    out = []
    for m in reversed(d["messages"]):
        if m["role"] == "user":
            break
        out.append(m["content"])
    return "\n".join(reversed(out))


@pytest.fixture
async def ready(client, seed, monkeypatch):
    """A conversation on Tunisia / a vehicle / a function, waiting for the first business fact."""
    chat_fakes.install(monkeypatch)
    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    for answer in ("突尼斯 (TN)", "普通车型", "跨境远程诊断、OTA 升级包分发与故障日志回传"):
        await send(client, auth, tid, answer)
    return auth, tid


# --- the three screenshots ------------------------------------------------------------------

async def test_a_question_about_the_law_is_answered_not_recorded_as_a_fact(client, seed, session_factory, monkeypatch):
    """“摩洛哥传数据出境要满足什么条件”曾被记成“D3 业务事实”。问句不是事实。"""
    await _seed_sg_corpus(session_factory)
    chat_fakes.install(
        monkeypatch,
        law_reply={"paragraphs": [{"text": "新加坡法规要求向境外传输个人数据前确保接收方提供相当水平的保护。", "cites": [1]}]},
    )
    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    await send(client, auth, tid, "新加坡 (SG)")
    d = await send(client, auth, tid, "新加坡传数据出境要满足什么条件")
    assert d["facts"] == {} and d["vehicle_model"] == ""  # 什么都没被记成事实
    text = [m["content"] for m in d["messages"] if m["role"] == "assistant"]
    answered = next(t for t in text if "接收方提供相当水平的保护" in t)
    assert "[1]" in answered and "依据：" in answered  # 有条文依据
    assert "评估这边接着要确认的是「车型/车队」" in answered  # 答完轻轻带一句接着要确认什么，不再硬拼一道题


async def test_a_law_question_the_library_cannot_answer_says_so_and_points_to_the_briefing(client, seed, session_factory, monkeypatch):
    await _seed_sg_corpus(session_factory)
    chat_fakes.install(monkeypatch)  # 模型没有给出任何有条文依据的段落
    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    await send(client, auth, tid, "新加坡 (SG)")
    d = await send(client, auth, tid, "新加坡传数据出境要满足什么条件")
    said = " ".join(m["content"] for m in d["messages"] if m["role"] == "assistant")
    assert "没有找到能直接回答这个问题的条文" in said and "国家合规简报" in said and "数据跨境传输合规路径与前置条件" in said


async def test_not_knowing_the_vehicle_is_met_with_help_not_a_refusal(client, seed, monkeypatch):
    """“不清楚”回答车型曾被回“无关，不作回答”。国家/车型/功能不能“不确定”时，解释并给出办法。"""
    auth, tid = await _fresh_at_vehicle(client, monkeypatch)
    d = await send(client, auth, tid, "不清楚")
    c = said(d)
    assert "写个大概" in c and "不作回答" not in c and "无关" not in c
    assert last(d)["slot"] == "vehicle_model" and d["vehicle_model"] == ""
    d = await send(client, auth, tid, "不确定")  # 第二次仍不确定：记作待定，对话继续
    assert d["vehicle_model"].startswith("待定车型") and last(d)["slot"] == "scenario_type"


async def _fresh_at_vehicle(client, monkeypatch):
    chat_fakes.install(monkeypatch)
    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    await send(client, auth, tid, "突尼斯 (TN)")
    return auth, tid


def _docx(text: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.split("\n"))
    xml = f'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", xml)
    return buf.getvalue()


POLICY = "个人信息保护管理办法\n本制度适用于新加坡（Singapore）境内的车辆数据，遵守 PDPA。收集前应当告知目的并取得同意；保存期限为一年。" * 3


async def test_asking_whether_it_is_still_running_is_answered_from_the_real_state_per_conversation(client, seed, session_factory, monkeypatch):
    """“还在分析么”曾被当成“请分析”；各对话窗口曾都显示“正在分析”。状态在服务端、每个对话自己的。"""
    await _seed_sg_corpus(session_factory)
    chat_fakes.install(monkeypatch)
    gate = asyncio.Event()

    async def slow_review(session, filename, document, country_code, sources=None):
        await gate.wait()
        return await real_review(session, filename, document, country_code, sources)

    real_review = gap_review.review_document
    monkeypatch.setattr(gap_review, "review_document", slow_review)

    async def fake_model(messages, **_):
        from .chat_fakes import _result

        payload = json.loads(messages[1]["content"])
        n = payload["clauses"][0]["n"]
        return _result({"items": [{"requirement": "收集前须告知并取得同意", "cites": [n], "status": "missing", "evidence": "", "gap": "文件未提及。", "suggestion": "补充告知与同意条款。"}]})

    monkeypatch.setattr(gap_review, "call_llm_json", fake_model)
    from app.knowledge import whitepaper as wp

    async def pick(messages, **_):
        from .chat_fakes import _result

        return _result({"keep": [1]})

    monkeypatch.setattr(wp, "call_llm_json", pick)
    auth = bearer(await login(client, "user", "a-member@example.com"))
    a = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    b = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    up = await client.post(f"/api/analysis/tasks/{a}/attachments?filename=制度.docx", headers={**auth, "Content-Type": "application/octet-stream"}, content=_docx(POLICY))
    fid = up.json()["id"]
    d = await send(client, auth, a, "帮我分析一下", [fid])
    assert d["country_code"] == "SG"  # 国家从文件里认出
    rows = (await client.get(f"/api/analysis/tasks/{a}/attachments", headers=auth)).json()
    assert rows[0]["review_state"] == "running" and rows[0]["message_id"]
    other = (await client.get(f"/api/analysis/tasks/{b}/attachments", headers=auth)).json()
    assert other == []  # 另一个对话没有任何对照状态
    d = await send(client, auth, a, "还在分析么")
    c = last(d)["content"]
    assert "还在对照新加坡的要求" in c and "已经" in c and "2–3 分钟" in c and "开始对照" not in c
    gate.set()
    for _ in range(100):
        rows = (await client.get(f"/api/analysis/tasks/{a}/attachments", headers=auth)).json()
        if rows[0]["review_state"] == "done":
            break
        await asyncio.sleep(0.2)
    assert rows[0]["review_state"] == "done" and rows[0]["reviewed"]
    d = (await client.get(f"/api/analysis/tasks/{a}", headers=auth)).json()
    said = [m["content"] for m in d["messages"] if m["role"] == "assistant"]
    assert any("对照新加坡要求完成" in t for t in said)  # 结论由服务端写进对话
    d = await send(client, auth, a, "结果在哪")
    assert "对照已经完成" in last(d)["content"] and "不作回答" not in last(d)["content"]


async def test_a_stale_running_review_counts_as_failed_and_can_be_restarted(client, seed, session_factory, monkeypatch):
    """服务重启后遗留的“运行中”不会永远转圈：超时按失败处理，用户可以说“重新对照”。"""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.analysis import attachments as att
    from app.core.rls import system_scope

    chat_fakes.install(monkeypatch)

    async def no_job(session, task):
        return []

    monkeypatch.setattr(attachment_jobs, "start_review", no_job)  # 只看状态判断，不让后台任务和测试抢时间
    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    up = await client.post(f"/api/analysis/tasks/{tid}/attachments?filename=制度.docx", headers={**auth, "Content-Type": "application/octet-stream"}, content=_docx(POLICY))
    await send(client, auth, tid, "好的", [up.json()["id"]])
    with system_scope("test stale"):
        async with session_factory() as s:
            row = await s.scalar(select(att.TaskAttachment))
            row.review_state, row.review_started_at = "running", datetime.now(UTC) - timedelta(seconds=attachment_jobs.STALE_SECONDS + 5)
            await s.commit()
    rows = (await client.get(f"/api/analysis/tasks/{tid}/attachments", headers=auth)).json()
    assert rows[0]["review_state"] == "failed"
    d = await send(client, auth, tid, "还在分析么")
    assert "没有完成" in last(d)["content"] and "重新对照" in last(d)["content"]


# --- the other intents -----------------------------------------------------------------------

async def test_progress_help_generate_platform_and_file_questions_each_get_an_answer(client, ready):
    auth, tid = ready
    d = await send(client, auth, tid, "现在到哪一步了，还差什么信息")
    assert "目前已收集" in said(d) and "还差" in said(d) and "接下来请回答" not in said(d) and last(d)["slot"] is None
    d = await send(client, auth, tid, "能出报告了吗，我要生成文件")
    assert "要等信息收齐后生成" in said(d) and "目前已收集" in said(d)
    d = await send(client, auth, tid, "摩洛哥的白皮书在哪看")
    assert "国家合规简报" in said(d) and "接下来请回答" not in said(d)
    d = await send(client, auth, tid, "海外准入的材料清单在哪")
    assert "海外属地准入" in last(d)["content"]
    d = await send(client, auth, tid, "把我传的文件分析一下")
    assert "还没有收到文件" in last(d)["content"] and "上传文件" in last(d)["content"]
    assert all(m["content"] for m in d["messages"])


async def test_small_talk_feedback_and_unclear_input_are_answered_politely(client, ready):
    auth, tid = ready
    d = await send(client, auth, tid, "好的，谢谢")
    assert "随时问我" in last(d)["content"] and last(d)["slot"] is None  # 致谢：回一句就够，不追问
    d = await send(client, auth, tid, "你回答的不对，什么质量")
    assert "抱歉" in said(d) and "告诉我想问的具体问题" in said(d)
    d = await send(client, auth, tid, "asdf")
    assert "没完全明白" in said(d) and last(d)["slot"]  # 没看懂：说明后把问题再发一次
    assert d["facts"] == {}


async def test_only_chat_unrelated_to_the_platform_is_declined_and_says_what_it_can_do(client, ready):
    auth, tid = ready
    d = await send(client, auth, tid, "今天天气怎么样")
    c = said(d)
    assert "超出了我的范围" in c and "汽车数据出境合规" in c and "国家合规简报" in c
    assert d["facts"] == {}


async def test_a_message_can_carry_a_fact_and_a_question_and_both_are_handled(client, fresh_chat, monkeypatch):
    """一句话里既说了车型、又问了法规：先记事实，再回应问题，最后接着问。"""
    auth, tid = fresh_chat
    chat_fakes.install(
        monkeypatch,
        rules=[
            (("SU7",), {
                "intents": ["provide_info", "ask_law"],
                "facts": {"vehicle_model": "小米 SU7"},
                "answer_kind": "answer",
                "law_topic": "cross_border",
            })
        ],
    )
    await send(client, auth, tid, "突尼斯 (TN)")
    d = await send(client, auth, tid, "我们是小米 SU7，做路测，对了跨境要什么条件")
    assert d["vehicle_model"] == "小米 SU7"
    said = " ".join(m["content"] for m in d["messages"] if m["role"] == "assistant")
    assert "已记录" in said and ("没有找到" in said or "依据" in said)
    assert last(d)["slot"] == "scenario_type"


@pytest.fixture
async def fresh_chat(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    return auth, (await client.post("/api/analysis/chats", headers=auth)).json()["id"]


async def test_a_correction_overwrites_the_earlier_answer_before_the_report(client, ready, monkeypatch):
    auth, tid = ready
    chat_fakes.install(
        monkeypatch,
        rules=[(("改成理想",), {"intents": ["correct_info"], "corrections": {"vehicle_model": "理想 L9"}})],
    )
    d = await send(client, auth, tid, "刚才说错了，车型改成理想 L9")
    assert d["vehicle_model"] == "理想 L9"
    assert any("已更新" in m["content"] for m in d["messages"] if m["role"] == "assistant")


async def test_a_help_question_gets_a_reply_to_what_was_asked_and_never_the_same_text_twice(client, ready, monkeypatch):
    auth, tid = ready

    async def fake(messages, **_):
        from .chat_fakes import _result

        if "对话理解模块" in messages[0]["content"]:
            return _result({"intents": ["help"]})
        facts = json.loads(messages[1]["content"])
        assert facts["conversation"] and facts["example_asks"] and facts["state"]["progress"]  # 回复撰写拿得到对话历史和状态
        return _result({"reply": "车外视频就是车辆外部摄像头拍到的画面。" if "车外视频" in facts["user_said"] else "我能帮您把评估信息问齐并生成合规文件。"})

    monkeypatch.setattr(chat_intent, "call_llm_json", fake)
    from app.analysis import chat_reply

    monkeypatch.setattr(chat_reply, "call_llm_json", fake)
    d = await send(client, auth, tid, "什么叫车外视频？")
    assert "外部摄像头拍到的画面" in said(d) and "没关系，我来说明" not in said(d) and last(d)["slot"]  # 先答所问，问题单独再发
    d = await send(client, auth, tid, "你能帮我做什么？")
    assert "评估信息问齐" in said(d)


async def test_history_is_kept_and_the_conversation_continues_where_it_stopped(client, ready):
    """过几天回来：对话、已收集的事实都在，接着问下一项，不重来。"""
    auth, tid = ready
    first = (await client.get(f"/api/analysis/tasks/{tid}", headers=auth)).json()
    again = (await client.get(f"/api/analysis/tasks/{tid}", headers=auth)).json()
    assert again["messages"] == first["messages"] and again["facts"] == first["facts"]
    d = await send(client, auth, tid, "不清楚")
    assert len(d["messages"]) > len(first["messages"]) and d["country_code"] == "TN"
    listed = (await client.get("/api/analysis/tasks?limit=200", headers=auth)).json()
    listed = listed if isinstance(listed, list) else listed.get("items", [])
    assert any(t["id"] == tid for t in listed)


# --- the reading itself ----------------------------------------------------------------------

def _ctx(slot="vehicle_model"):
    return chat_intent.IntentCtx(
        stage="collecting", slot=slot, question="车型？", files=[], recent=[],
        slots={"vehicle_model": {"describe": "车型", "filled": False, "value": ""}, "country_code": {"describe": "国家", "filled": False, "value": ""}},
    )


def test_a_question_is_never_a_fact_and_only_verbatim_pieces_survive():
    msg = "摩洛哥传数据出境要满足什么条件"
    only_question = chat_intent.validate(
        {"intents": ["ask_law"], "facts": {"vehicle_model": "摩洛哥传数据"}, "law_topic": "cross_border"}, msg, _ctx()
    )
    assert only_question.facts == {} and only_question.law_topic == "cross_border"
    mixed = chat_intent.validate(
        {
            "intents": ["provide_info", "ask_law", "nonsense"],
            "facts": {"vehicle_model": "小米 SU7", "country_code": "新加坡", "retention_and_blackbox": "x"},
            "answer_kind": "answer",
        },
        "我们是小米 SU7，做路测，对了跨境要什么条件",
        _ctx(),
    )
    assert mixed.intents == ["provide_info", "ask_law"]  # 未知意图丢弃
    assert mixed.facts == {"vehicle_model": "小米 SU7"}  # 不在原话里的、未知槽位的丢弃
    assert chat_intent.validate({"intents": ["bogus"]}, "x", _ctx()) is None
    assert chat_intent.validate("not a dict", "x", _ctx()) is None


def test_rules_read_what_needs_no_model():
    def read(text, slot="vehicle_model"):
        return chat_intent.by_rule(text, _ctx(slot), match_country=lambda t: "TN" if "突尼斯" in t else None, match_scenario=lambda t: None)

    assert read("好的").intents == ["smalltalk"] and read("你好").greeting
    assert read("不清楚").answer_kind == "unknown" and read("没有").answer_kind == "negative"
    assert read("突尼斯 (TN)", "country_code").facts == {"country_code": "突尼斯 (TN)"}
    assert read("还在分析么") is None and read("我们的车续航六百公里") is None  # 这些要模型读


@pytest.mark.parametrize("note", ["x"])
async def test_the_model_is_asked_once_and_a_failure_is_reported_not_turned_into_an_answer(note, monkeypatch):
    calls = {"n": 0}

    async def down(*_a, **_k):
        calls["n"] += 1
        raise RuntimeError("down")

    monkeypatch.setattr(chat_intent, "call_llm_json", down)
    u = await chat_intent.understand("摩洛哥传数据出境要满足什么条件？", _ctx(), match_country=lambda t: None, match_scenario=lambda t: None)
    assert u.ok is False and calls["n"] == 2  # 重试一次后如实报告，不记成事实
    u = await chat_intent.understand("星途 X7", _ctx(), match_country=lambda t: None, match_scenario=lambda t: None)
    assert u.via == "degraded" and u.answer_kind == "answer"


async def test_what_the_user_says_about_an_item_already_filled_is_never_silently_dropped(client, seed, monkeypatch):
    """国家已填后又说“目的国是新加坡”被悄悄丢掉（独立测试集发现）：核心三项问一句，业务事实追加补充。"""
    chat_fakes.install(
        monkeypatch,
        rules=[
            (("目的国是新加坡",), {"intents": ["provide_info"], "facts": {"country_code": "新加坡", "vehicle_model": "星途 EX7"}, "answer_kind": "answer"}),
            (("补充一下，视频只在测试场采集",), {"intents": ["provide_info"], "facts": {"exterior_video_and_gnss": "视频只在测试场采集"}, "answer_kind": "none"}),
        ],
    )
    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    await send(client, auth, tid, "摩洛哥 (MA)")
    d = await send(client, auth, tid, "目的国是新加坡，车型星途 EX7")
    said = " ".join(m["content"] for m in d["messages"] if m["role"] == "assistant")
    assert d["country_code"] == "MA" and d["vehicle_model"] == "星途 EX7"  # 国家没有被悄悄改，车型记下了
    assert "前面选的目标国家是摩洛哥" in said and "改成新加坡" in said  # 并且问了用户
    for _text in ("采集车外视频，车端对人脸做模糊后回传", ):
        await send(client, auth, tid, "新能源 SUV" if False else "L3/L4 自动驾驶公共道路测试与远程监控回传")
    d = await send(client, auth, tid, "采集车外视频，车端对人脸做模糊后回传")
    d = await send(client, auth, tid, "补充一下，视频只在测试场采集")
    assert "用户补充：视频只在测试场采集" in d["facts"]["exterior_video_and_gnss"] and "模糊后回传" in d["facts"]["exterior_video_and_gnss"]


async def test_deleting_a_conversation_archives_it_first_and_it_can_be_restored_or_deleted_for_good(client, seed, session_factory):
    """“删除”先进归档（误删可恢复）；归档里再删除才彻底删除。归档的对话只读；别人的不能动；评估中的不能删。"""
    member = bearer(await login(client, "user", "a-member@example.com"))
    colleague = bearer(await login(client, "user", "a-admin@example.com"))  # 同企业的另一位企业用户
    other_org = bearer(await login(client, "user", "b-admin@example.com"))
    mine = (await client.post("/api/analysis/chats", headers=member)).json()["id"]
    await client.post(f"/api/analysis/tasks/{mine}/messages", headers=member, json={"content": "摩洛哥 (MA)"})
    up = await client.post(f"/api/analysis/tasks/{mine}/attachments?filename=制度.docx", headers={**member, "Content-Type": "application/octet-stream"}, content=_docx(POLICY))
    assert up.status_code == 201

    async def ids(headers, archived=False):
        r = (await client.get(f"/api/analysis/tasks?limit=200&archived={'true' if archived else 'false'}", headers=headers)).json()
        return {t["id"] for t in (r if isinstance(r, list) else r.get("items", []))}

    assert mine in await ids(member)
    assert (await client.delete(f"/api/analysis/tasks/{mine}", headers=other_org)).status_code == 404  # 别的企业：不存在
    theirs = (await client.post("/api/analysis/chats", headers=colleague)).json()["id"]
    # 同一企业的用户都能看、都能管本企业的对话：删除同事的对话同样先进归档
    r = await client.delete(f"/api/analysis/tasks/{theirs}", headers=member)
    assert r.status_code == 200 and r.json()["archived"] is True
    assert theirs in await ids(colleague, archived=True)
    # 第一步：删除 = 归档
    r = await client.delete(f"/api/analysis/tasks/{mine}", headers=member)
    assert r.status_code == 200 and r.json()["archived"] is True and "deleted" not in r.json()
    assert mine not in await ids(member) and mine in await ids(member, archived=True)
    # 归档的对话还在、内容完整，但只读
    d = (await client.get(f"/api/analysis/tasks/{mine}", headers=member)).json()
    assert d["country_code"] == "MA" and len(d["messages"]) >= 3
    assert d["archived"] is True  # 详情告诉页面“已归档”，页面据此不把它放回当前列表
    assert (await client.post(f"/api/analysis/tasks/{mine}/messages", headers=member, json={"content": "继续"})).status_code == 409
    assert (await client.post(f"/api/analysis/tasks/{mine}/attachments?filename=a.txt", headers={**member, "Content-Type": "application/octet-stream"}, content=b"x" * 80)).status_code == 409
    # 没归档就想彻底删：不行
    fresh = (await client.post("/api/analysis/chats", headers=colleague)).json()["id"]
    assert (await client.delete(f"/api/analysis/tasks/{fresh}?permanent=true", headers=colleague)).status_code == 409
    # 恢复：回到列表，聊天记录和文件原样，可以继续
    assert (await client.post(f"/api/analysis/tasks/{mine}/restore", headers=member)).json()["restored"] is True
    assert (await client.get(f"/api/analysis/tasks/{mine}", headers=member)).json()["archived"] is False
    assert mine in await ids(member) and mine not in await ids(member, archived=True)
    assert (await client.get(f"/api/analysis/tasks/{mine}/attachments", headers=member)).json()[0]["filename"] == "制度.docx"
    assert (await client.post(f"/api/analysis/tasks/{mine}/messages", headers=member, json={"content": "不确定"})).status_code == 200
    # 再删除 → 归档 → 在归档里彻底删除（同一企业的用户都可以处理本企业的）
    await client.delete(f"/api/analysis/tasks/{mine}", headers=member)
    r = await client.delete(f"/api/analysis/tasks/{mine}?permanent=true", headers=colleague)
    assert r.status_code == 200 and r.json()["deleted"] is True and r.json()["conversation_code"].startswith("CONV-")
    assert (await client.get(f"/api/analysis/tasks/{mine}", headers=member)).status_code == 404
    assert (await client.post(f"/api/analysis/tasks/{mine}/restore", headers=member)).status_code == 404  # 彻底删了就没了
    # 评估中的不能删
    from sqlalchemy import update

    from app.analysis.models import AnalysisTask
    from app.core.rls import system_scope

    busy = (await client.post("/api/analysis/chats", headers=member)).json()["id"]
    with system_scope("test running"):
        async with session_factory() as s:
            await s.execute(update(AnalysisTask).where(AnalysisTask.id == busy).values(status="running"))
            await s.commit()
    r = await client.delete(f"/api/analysis/tasks/{busy}", headers=member)
    assert r.status_code == 409 and "评估中" in r.json()["detail"]


async def test_while_the_assessment_runs_questions_are_still_answered_and_a_terse_follow_up_keeps_the_country(client, seed, session_factory, monkeypatch):
    """评估运行中不再一概“评估正在进行”：问法规/问平台/致谢照常答；极短的追问（“还有吗”）沿用刚答过的国家；业务信息先放着。"""
    from sqlalchemy import update

    from app.analysis.models import AnalysisTask
    from app.core.rls import system_scope

    await _seed_sg_corpus(session_factory)
    chat_fakes.install(
        monkeypatch,
        rules=[
            (("还有吗",), {"intents": ["ask_law"], "law_topic": "cross_border", "standalone": "还有哪些跨境传输的要求"}),
            (("不申报会怎样",), {"intents": ["ask_law"], "law_topic": "penalty"}),
            (("我们改用云端存储",), {"intents": ["provide_info"], "answer_kind": "answer"}),
        ],
        law_reply={"paragraphs": [{"text": "新加坡法规要求向境外传输个人数据前确保接收方提供相当水平的保护。", "cites": [1]}]},
    )
    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    await send(client, auth, tid, "新加坡 (SG)")
    d = await send(client, auth, tid, "新加坡传数据出境有什么规定")
    assert "接收方提供相当水平的保护" in " ".join(m["content"] for m in d["messages"] if m["role"] == "assistant")
    d = await send(client, auth, tid, "还有吗")  # 国家没再提：沿用上文答过的国家，不再问“哪个国家”
    assert "想问哪个国家" not in last(d)["content"]
    with system_scope("test running"):
        async with session_factory() as s:
            await s.execute(update(AnalysisTask).where(AnalysisTask.id == tid).values(status="running"))
            await s.commit()
    d = await send(client, auth, tid, "不申报会怎样")
    c = last(d)["content"]
    assert "评估正在进行中" in c and ("接收方" in c or "没有找到" in c)  # 先回答问题，再说评估在跑
    d = await send(client, auth, tid, "我们改用云端存储")
    assert "先记在心里" in last(d)["content"] and d["facts"] == {}  # 业务信息不在评估中途改
    d = await send(client, auth, tid, "好的，谢谢")
    assert "评估正在进行中" in last(d)["content"] and "随时问我" in last(d)["content"]


async def test_the_composed_reply_may_not_claim_an_action_and_it_gets_the_facts_of_what_was_recorded(client, seed, monkeypatch):
    """“换摩洛哥再来一次”曾得到“这轮评估就按摩洛哥来”，其实什么都没改。撰写的文字里一旦宣称做过动作就作废，
    换成程序自己的说法；真正记了什么，由程序作为 recorded 交给撰写环节。"""
    from app.analysis import chat_reply

    seen = {}

    async def claims(messages, **_):
        from .chat_fakes import _result

        if "对话理解模块" in messages[0]["content"]:
            return _result({"intents": ["ask_platform"]})
        seen["facts"] = json.loads(messages[1]["content"])
        return _result({"reply": "好的，这轮评估就按摩洛哥来，我已把国家改成摩洛哥。"})

    monkeypatch.setattr(chat_intent, "call_llm_json", claims)
    monkeypatch.setattr(chat_reply, "call_llm_json", claims)
    import types

    monkeypatch.setattr(chat_reply, "get_settings", lambda: types.SimpleNamespace(llm_api_key="x"))
    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    await send(client, auth, tid, "新加坡 (SG)")
    d = await send(client, auth, tid, "新加坡评完了，换摩洛哥再来一次")
    c = last(d)["content"]
    assert "这轮评估就按摩洛哥" not in c and "已把国家改成" not in c  # 动作宣称被丢弃
    assert "新对话" in " ".join(m["content"] for m in d["messages"] if m["role"] == "assistant")  # 用程序的固定说法指路
    assert d["country_code"] == "SG"  # 当前评估的国家没变
    assert "recorded" in seen["facts"] and seen["facts"]["recorded"] == []  # 这一轮什么都没记，撰写环节拿到的事实就是“没记”


async def test_a_half_answer_is_recorded_and_only_the_missing_half_is_asked_and_the_reply_completes_it(client, seed, monkeypatch):
    """“数据传回国内”只答了“传到哪”，没说“靠什么机制”：记下，追问缺的那一半；用户的下一句补到这一项上，不跳去下一项。"""
    chat_fakes.install(
        monkeypatch,
        rules=[
            (("传回国内",), {
                "intents": ["provide_info"], "answer_kind": "answer",
                "facts": {"cross_border_mechanism": "数据传回国内"}, "partial": {"cross_border_mechanism": "靠什么机制出境（标准合同、认证、许可等）"},
            }),
            (("走标准合同",), {"intents": ["provide_info"], "answer_kind": "answer"}),
        ],
    )
    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    for t in ("摩洛哥 (MA)", "星途 X7 纯电 SUV", "L3/L4 自动驾驶公共道路测试与远程监控回传", "采集车外视频，车端模糊后回传"):
        await send(client, auth, tid, t)
    d = await send(client, auth, tid, "数据传回国内")
    assert d["facts"]["cross_border_mechanism"] == "数据传回国内"  # 已答的一半记下了
    assert last(d)["slot"] == "cross_border_mechanism+" and "还差：靠什么机制出境" in last(d)["content"]  # 只追问缺的一半
    d = await send(client, auth, tid, "走标准合同")
    assert "数据传回国内" in d["facts"]["cross_border_mechanism"] and "走标准合同" in d["facts"]["cross_border_mechanism"]  # 补到同一项上
    assert last(d)["slot"] == "consent_and_notice"  # 然后才问下一项


async def test_the_current_question_is_not_tacked_onto_every_reply(client, ready):
    """每条回复末尾都机械地“接下来请回答：……”会让人烦。问进度、致谢、抱怨、问法规、问平台功能之后不再重复发问题；
    求助、没看懂、答非所问、要生成之后才把问题再发一遍。"""
    auth, tid = ready
    n0 = len((await client.get(f"/api/analysis/tasks/{tid}", headers=auth)).json()["messages"])
    d = await send(client, auth, tid, "好的，谢谢")  # 致谢：只回一句，不追问
    assert len(d["messages"]) == n0 + 2 and "接下来请回答" not in last(d)["content"] and last(d)["slot"] is None
    d = await send(client, auth, tid, "现在到哪一步了，还差什么信息")  # 问进度：回答进度就行
    assert "目前已收集" in last(d)["content"] and "接下来请回答" not in last(d)["content"]
    d = await send(client, auth, tid, "我不太懂业务，该怎么答")  # 求助：说明后把问题再发一遍，方便照着回答
    assert last(d)["slot"] and "接下来请回答" not in last(d)["content"]


async def test_a_conversation_gets_a_title_from_the_first_real_message_and_the_user_can_rename_it(client, seed, monkeypatch):
    """新对话一直叫“新对话”不对：第一句有实质内容就按它起名；用户可以改名，改过的名字固定下来、不被自动标题覆盖。"""
    chat_fakes.install(
        monkeypatch,
        rules=[(("OTA",), {"intents": ["provide_info"], "answer_kind": "answer", "facts": {"scenario_type": "OTA"}, "title": "埃及 OTA 数据出境"})],
    )
    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    d = await send(client, auth, tid, "我们是做 OTA 的，客户在埃及")
    assert d["title"] == "埃及 OTA 数据出境"
    r = await client.patch(f"/api/analysis/tasks/{tid}", headers=auth, json={"title": "  埃及项目  记录 "})
    assert r.status_code == 200 and r.json()["title"] == "埃及项目 记录"
    for t in ("埃及 (EG)", "星途 X7 纯电 SUV"):
        d = await send(client, auth, tid, t)
    assert d["title"] == "埃及项目 记录"  # 国家和车型都有了，也不会改回“埃及 · 星途 合规评估”
    assert (await client.patch(f"/api/analysis/tasks/{tid}", headers=auth, json={"title": "   "})).status_code in (422, 400)
    other = bearer(await login(client, "user", "b-admin@example.com"))
    assert (await client.patch(f"/api/analysis/tasks/{tid}", headers=other, json={"title": "x"})).status_code == 404
    member2 = bearer(await login(client, "user", "a-admin@example.com"))  # 同一企业的用户都可以给本企业的对话改名
    assert (await client.patch(f"/api/analysis/tasks/{tid}", headers=member2, json={"title": "埃及项目（管理员改）"})).status_code == 200


def test_a_business_description_mentioning_a_privacy_policy_is_not_a_privacy_question():
    """“车主在App注册时勾选同意隐私政策”是在说业务，不是问平台的数据安全：要交给理解环节整理成业务信息。"""
    from app.analysis.chat_advisor import PRIVACY, _fixed

    assert _fixed("我们的数据会给别人看到吗？") == PRIVACY
    assert _fixed("上传的文件保密吗") == PRIVACY
    desc = "我们在摩洛哥销售的车每分钟把车辆位置、车速和故障码上传到上海的云平台，车主在App注册时勾选同意隐私政策，数据保存两年。"
    assert _fixed(desc) is None
    assert _fixed("我们和供应商签了保密协议，数据只在内部使用") is None
