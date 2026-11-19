"""对话里上传企业现有文件：读文字、与目标国要求对照；“已涵盖”必须有文件原文摘录，找不到原文就按未涵盖。"""

import io
import json
import zipfile

import pytest

from app.analysis import attachments as att
from app.analysis import gap_review
from app.analysis.llm import LlmResult

from .conftest import bearer, login
from .test_analysis_p3 import _seed_sg_corpus

POLICY = (
    "个人信息保护管理办法\n\n第一条 本公司收集车辆数据前，应当向车主明确告知收集目的并取得单独同意。\n\n"
    "第二条 个人信息的保存期限为一年，到期后自动删除。\n\n第三条 数据传输采用加密方式。" + "补充说明。" * 20
)


def _docx(text: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.split("\n"))
    xml = f'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("word/document.xml", date_time=(2026, 1, 1, 0, 0, 0)), xml)  # 固定时间：同样的内容每次生成的字节相同
    return buf.getvalue()


def test_text_word_and_pdf_are_read_and_a_scan_or_junk_is_refused_with_a_reason():
    assert att.extract("a.txt", POLICY.encode())[2] is True
    assert "单独同意" in att.extract("b.docx", _docx(POLICY))[0]
    with pytest.raises(att.UploadError, match="PDF、Word"):
        att.extract("c.exe", b"x")
    with pytest.raises(att.UploadError, match="打不开"):
        att.extract("d.pdf", b"not a pdf")
    with pytest.raises(att.UploadError, match="Word"):
        att.extract("e.docx", b"not a zip")
    text, _, readable, note = att.extract("tiny.txt", b"too short")
    assert readable is False and "几乎没有读到文字" in note


def test_the_passages_chosen_for_a_dimension_are_the_ones_that_talk_about_it():
    document = "公司简介：我们是一家汽车公司。\n\n" + "\n\n".join(f"无关段落{i}，讲食堂和班车。" * 3 for i in range(12)) + "\n\n保存期限为一年，到期后删除个人数据并销毁备份。"
    clauses = [{"quote_zh": "个人数据的保存期限不得超过实现目的所必需的时间，到期应当删除或匿名化。"}]
    chosen = gap_review.passages_for(document, clauses, "数据存储、留存、销毁")
    assert any("保存期限为一年" in p for p in chosen)


def _fake_review_model():
    async def fake(messages, **_kw):
        payload = json.loads(messages[1]["content"])
        if "candidates" in payload:
            return LlmResult(data={"keep": [1]}, prompt_tokens=1, completion_tokens=1, duration_ms=1, model="fake")
        n = payload["clauses"][0]["n"]
        items = [
            {"requirement": "收集前须告知目的并取得同意", "cites": [n], "status": "covered", "evidence": "应当向车主明确告知收集目的并取得单独同意", "gap": "", "suggestion": ""},
            {"requirement": "保存期限须与目的相称", "cites": [n], "status": "covered", "evidence": "文件里根本没有的一句话", "gap": "", "suggestion": "补充保存期限依据。"},
            {"requirement": "应指定数据保护负责人", "cites": [n], "status": "missing", "evidence": "", "gap": "文件未提及负责人。", "suggestion": "指定负责人并公示联系方式。"},
            {"requirement": "没有条文依据的要求", "cites": [999], "status": "missing", "evidence": "", "gap": "", "suggestion": ""},
        ]
        return LlmResult(data={"items": items}, prompt_tokens=10, completion_tokens=5, duration_ms=1, model="fake")

    return fake


async def test_covered_needs_a_real_quotation_and_the_counts_are_the_programs_own(session_factory, monkeypatch):
    from app.core.rls import system_scope
    from app.knowledge import whitepaper as wp

    await _seed_sg_corpus(session_factory)
    monkeypatch.setattr(gap_review, "call_llm_json", _fake_review_model())
    monkeypatch.setattr(wp, "call_llm_json", _fake_review_model())
    with system_scope("test review"):
        async with session_factory() as s:
            review = await gap_review.review_document(s, "制度.docx", POLICY, "SG")
    text = json.dumps(review.blocks, ensure_ascii=False)
    assert "“应当向车主明确告知收集目的并取得单独同意”" in text  # 真实摘录原样给出
    assert "文件里根本没有的一句话" not in text  # 编造的摘录不出现
    assert "找不到，按未涵盖处理" in text  # 且被降为未涵盖，并说明原因
    assert "没有条文依据的要求" not in text  # 没有条文依据的要求整条丢弃
    # 假模型对每个有条文的维度给出 3 条有效项（1 条有真摘录、1 条编造摘录被降级、1 条本来就缺）；
    # 种子库里个别维度没有条文，则该维度如实写“暂无法对照”，不凭空给项
    c = review.counts
    assert c["covered"] >= 3 and c["missing"] == 2 * c["covered"] and c["partial"] == 0
    assert f"已涵盖 {c['covered']} 项" in text
    assert c["covered"] == 5 or "本库目前没有收录" in text  # 五个对照维度都有条文时没有“暂无法对照”的维度
    assert any(b["type"] == "table" and b["head"][0] == "编号" for b in review.blocks)


async def _wait_reviews(client, auth, base, state="done", expect=None):
    import asyncio

    for _ in range(150):
        rows = (await client.get(base, headers=auth)).json()
        readable = [r for r in rows if r["readable"]]
        if readable and all(r["review_state"] == state for r in readable) and (expect is None or len(readable) == expect):
            return rows
        await asyncio.sleep(0.2)
    raise AssertionError(f"reviews did not reach {state}: {[r['review_state'] for r in rows]}")


async def test_upload_review_and_export_through_the_api(client, seed, session_factory, monkeypatch):
    from app.knowledge import whitepaper as wp

    from . import chat_fakes

    await _seed_sg_corpus(session_factory)
    chat_fakes.install(monkeypatch)
    monkeypatch.setattr(gap_review, "call_llm_json", _fake_review_model())
    monkeypatch.setattr(wp, "call_llm_json", _fake_review_model())
    auth = bearer(await login(client, "user", "a-member@example.com"))
    task = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    await client.post(f"/api/analysis/tasks/{task}/messages", headers=auth, json={"content": "新加坡 (SG)"})
    base = f"/api/analysis/tasks/{task}/attachments"
    put = lambda name, body: client.post(f"{base}?filename={name}", headers={**auth, "Content-Type": "application/octet-stream"}, content=body)  # noqa: E731
    say = lambda text, ids: client.post(f"/api/analysis/tasks/{task}/messages", headers=auth, json={"content": text, "attachment_ids": ids})  # noqa: E731
    assert (await put("x.exe", b"abc")).status_code == 422
    assert (await put("empty.txt", b"")).status_code == 422
    assert (await put("scan.pdf", b"%PDF-1.4 broken")).status_code == 422
    r = await put("制度.docx", _docx(POLICY))
    assert r.status_code == 201 and r.json()["readable"] and r.json()["chars"] > 100
    aid = r.json()["id"]
    assert (await put("制度.docx", _docx(POLICY))).json()["id"] == aid  # 同一份文件不重复放
    assert len((await client.get(base, headers=auth)).json()) == 1
    assert (await put("big.txt", b"x" * (att.MAX_BYTES + 1))).status_code == 413
    row = (await client.get(base, headers=auth)).json()[0]
    assert row["message_id"] is None and row["review_state"] is None  # 还在输入框里，没发出、没开始对照
    assert (await client.get(f"{base}/{aid}/review", headers=auth)).status_code == 404
    # 发送：文件随消息进对话，对照在服务端后台开始，完成后结论写进对话
    assert (await say("帮我对照一下", [aid])).status_code == 200
    rows = await _wait_reviews(client, auth, base)
    assert rows[0]["reviewed"] and rows[0]["summary"]["covered"] >= 1 and rows[0]["summary"]["gaps"]
    body = (await client.get(f"{base}/{aid}/review", headers=auth)).json()
    assert body["blocks"]
    assert (await client.post(f"{base}/review-pending", headers=auth)).json() == {"started": []}  # 已对照过：不重复调用模型
    pdf = await client.get(f"{base}/{aid}/review/export.pdf", headers=auth)
    docx = await client.get(f"{base}/{aid}/review/export.docx", headers=auth)
    assert pdf.content[:5] == b"%PDF-" and docx.content[:2] == b"PK"
    chat = (await client.get(f"/api/analysis/tasks/{task}", headers=auth)).json()
    said = " ".join(m["content"] for m in chat["messages"] if m["role"] == "assistant")
    assert "对照新加坡要求完成" in said and "最突出的缺口" in said and "已读取" not in said
    assert chat["messages"][-1]["slot"] is None  # 结论之后不再把用户引回旧的八维度评估问卷
    # 再传一份：两份合起来对照一次，依据标明出自哪份；删掉一份后合并结果失效
    second = await put("补充规定.txt", ("本公司车辆数据在境内存储，出境前须经法务审批，并记录接收方名称、传输目的和传输范围，保存审批记录以备检查。" * 2).encode())
    assert second.status_code == 201
    assert (await say("再一起对照一下", [second.json()["id"]])).status_code == 200
    rows = await _wait_reviews(client, auth, base, expect=2)
    assert all(r["in_merged"] for r in rows)
    merged = (await client.get(f"{base}/merged-review", headers=auth)).json()
    assert merged["files"] == ["制度.docx", "补充规定.txt"] and merged["summary"]["covered"] >= 1
    assert "——《制度.docx》" in json.dumps(merged["blocks"], ensure_ascii=False)
    assert (await client.post(f"{base}/review-pending", headers=auth)).json() == {"started": []}
    assert (await client.get(f"{base}/merged-review/export.pdf", headers=auth)).content[:5] == b"%PDF-"
    assert (await client.get(f"{base}/{aid}/review", headers=auth)).json()["blocks"] == body["blocks"]  # 单份对照不受影响
    assert (await client.delete(f"{base}/{second.json()['id']}", headers=auth)).status_code == 200
    assert (await client.get(f"{base}/merged-review", headers=auth)).status_code == 404
    other = bearer(await login(client, "user", "b-admin@example.com"))
    assert (await client.get(base, headers=other)).status_code == 404  # 别的企业看不到
    assert (await client.delete(f"{base}/{aid}", headers=auth)).json() == {"removed": True}


async def test_a_failed_review_is_written_into_the_conversation_and_can_be_restarted(client, seed, session_factory, monkeypatch):
    from app.analysis.document_narrative import NarrativeError
    from app.knowledge import whitepaper as wp

    from . import chat_fakes

    await _seed_sg_corpus(session_factory)
    chat_fakes.install(monkeypatch)
    monkeypatch.setattr(wp, "call_llm_json", _fake_review_model())

    async def broken(*_a, **_k):
        raise NarrativeError("模型输出被截断")

    monkeypatch.setattr(gap_review, "call_llm_json", broken)
    auth = bearer(await login(client, "user", "a-member@example.com"))
    task = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    await client.post(f"/api/analysis/tasks/{task}/messages", headers=auth, json={"content": "新加坡 (SG)"})
    base = f"/api/analysis/tasks/{task}/attachments"
    aid = (await client.post(f"{base}?filename=制度.docx", headers={**auth, "Content-Type": "application/octet-stream"}, content=_docx(POLICY))).json()["id"]
    await client.post(f"/api/analysis/tasks/{task}/messages", headers=auth, json={"content": "帮我对照一下", "attachment_ids": [aid]})
    rows = await _wait_reviews(client, auth, base, state="failed")
    assert "截断" in rows[0]["review_error"]
    chat = (await client.get(f"/api/analysis/tasks/{task}", headers=auth)).json()
    assert any("文件对照没有完成" in m["content"] for m in chat["messages"] if m["role"] == "assistant")
    # 失败不会自动重来；用户说“重新对照”（或点重试）才重新开始
    monkeypatch.setattr(gap_review, "call_llm_json", _fake_review_model())
    await client.post(f"/api/analysis/tasks/{task}/messages", headers=auth, json={"content": "重新对照"})
    rows = await _wait_reviews(client, auth, base)
    assert rows[0]["reviewed"]


async def test_a_file_that_names_the_country_settles_the_country_and_a_short_command_is_not_refused(client, seed, monkeypatch):
    """用户在新对话里先传文件再说“帮我分析下呢”：国家从文件里认，不当成求助、也不当成无关内容；
    文件随这条消息进入对话，不再留在输入框。"""
    from app.analysis import attachment_jobs

    from . import chat_fakes
    from .test_analysis_p3 import _last_assistant

    chat_fakes.install(monkeypatch)

    async def no_job(session, task):
        return []

    monkeypatch.setattr(attachment_jobs, "start_review", no_job)  # 这里只看国家怎么定，不跑对照
    auth = bearer(await login(client, "user", "a-member@example.com"))

    async def put(tid, name, body):
        r = await client.post(f"/api/analysis/tasks/{tid}/attachments?filename={name}", headers={**auth, "Content-Type": "application/octet-stream"}, content=body)
        assert r.status_code == 201
        return r.json()["id"]

    async def send(tid, text, ids):
        r = await client.post(f"/api/analysis/tasks/{tid}/messages", headers=auth, json={"content": text, "attachment_ids": ids})
        assert r.status_code == 200, r.text
        return r.json()

    named = POLICY + "\n本制度适用于摩洛哥（Maroc）境内的车辆数据，并遵守 CNDP 的要求。摩洛哥法规要求另行申报。" * 3
    t1 = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    f1 = await put(t1, "制度.docx", _docx(named))
    d = await send(t1, "帮我分析下呢", [f1])
    said = " ".join(m["content"] for m in d["messages"] if m["role"] == "assistant")
    assert d["country_code"] == "MA" and "目标国家先记为摩洛哥" in said and "没关系，我来说明" not in said and "超出了我的范围" not in said
    rows = (await client.get(f"/api/analysis/tasks/{t1}/attachments", headers=auth)).json()
    sent = next(m for m in d["messages"] if m["role"] == "user" and m["content"] == "帮我分析下呢")
    assert rows[0]["message_id"] == sent["id"]  # 文件属于这条消息
    # 文件里没有国家线索：如实说没看出来并问国家，不给“怎么用”的说明
    t2 = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    f2 = await put(t2, "通用.docx", _docx(POLICY.replace("新加坡（Singapore）", "本公司").replace("PDPA", "公司制度")))
    d2 = await send(t2, "帮我分析下呢", [f2])
    texts = " ".join(m["content"] for m in d2["messages"] if m["role"] == "assistant")
    assert d2["country_code"] == "--" and "没能从内容里看出针对哪个国家" in texts and "没关系，我来说明" not in texts
    assert _last_assistant(d2)["slot"] == "country_code"
    assert len((await client.get(f"/api/analysis/tasks/{t2}/attachments", headers=auth)).json()) == 1
    # 别的对话的文件不能挂到这条消息上
    other = await put(t1, "另一份.docx", _docx(POLICY + "另一份。" * 30))
    await send(t2, "再看看", [other])
    assert next(r for r in (await client.get(f"/api/analysis/tasks/{t1}/attachments", headers=auth)).json() if r["id"] == other)["message_id"] is None
    from app.analysis.chat import country_from_documents

    assert country_from_documents(["摩洛哥 摩洛哥 突尼斯 突尼斯"]) is None
    assert country_from_documents(["Maroc CNDP 摩洛哥 突尼斯"]) == "MA"
