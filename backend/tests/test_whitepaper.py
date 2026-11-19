"""国家白皮书：8 个维度的简报，条文引用由程序列出，通不过核对的段落不出现，用户端可读可导出，管理员可更新。"""

import json

import pytest

from app.analysis.llm import LlmResult
from app.knowledge import whitepaper as wp

from .conftest import bearer, login
from .test_analysis_p3 import _seed_sg_corpus


def _fake_model(bad_number: bool = False):
    async def fake(messages, **_kw):
        payload = json.loads(messages[1]["content"])
        if "candidates" in payload:  # 挑选条文
            return LlmResult(data={"keep": [1, 2]}, prompt_tokens=5, completion_tokens=2, duration_ms=1, model="fake")
        offered = [c["n"] for c in payload["clauses"]][:1]
        text, point = "该国法规对此有明确规定。", "要点一。"
        if bad_number:
            text, point = "该国对此处罚 99999 万元。", "罚款 88888 万元。"
        data = {
            "paragraphs": [{"text": text, "cites": offered}],
            "points": [{"text": point, "cites": offered}],
        }
        return LlmResult(data=data, prompt_tokens=10, completion_tokens=5, duration_ms=1, model="fake")

    return fake


async def test_the_briefing_has_all_eight_dimensions_and_a_clause_table_made_by_the_program(
    session_factory, monkeypatch
):
    from app.core.rls import system_scope

    await _seed_sg_corpus(session_factory)
    monkeypatch.setattr(wp, "call_llm_json", _fake_model())
    with system_scope("test whitepaper"):
        async with session_factory() as s:
            blocks, meta = await wp.write_whitepaper(s, "SG")
    headings = [b["text"] for b in blocks if b["type"] == "heading"]
    assert len([h for h in headings if h[:2] in {f"{n}、" for n in "一二三四五六七八"}]) == 8
    assert blocks[0]["type"] == "title" and "新加坡" in blocks[0]["text"]
    assert any(b["type"] == "table" and b["head"][0] == "编号" for b in blocks)  # 条文表
    assert "[1]" in json.dumps(blocks, ensure_ascii=False)
    assert meta["tokens"] > 0 and meta["source_hash"]


async def test_a_dimension_with_nothing_that_passes_the_checks_stops_the_briefing(
    session_factory, monkeypatch
):
    from app.core.rls import system_scope

    await _seed_sg_corpus(session_factory)
    monkeypatch.setattr(wp, "call_llm_json", _fake_model(bad_number=True))
    with system_scope("test whitepaper"), pytest.raises(wp.WhitepaperError):
        async with session_factory() as s:
            await wp.write_whitepaper(s, "SG")


async def test_users_read_and_export_and_only_administrators_update(client, seed, session_factory, monkeypatch):
    from app.core.rls import system_scope

    await _seed_sg_corpus(session_factory)
    monkeypatch.setattr(wp, "call_llm_json", _fake_model())
    user = bearer(await login(client, "user", "a-member@example.com"))
    _admin = bearer(await login(client, "admin", "admin@example.com"))
    listing = (await client.get("/api/knowledge/whitepapers", headers=user)).json()
    assert {r["country_code"] for r in listing} == {"SG", "EG", "TN", "MA", "LB"} and not any(r["available"] for r in listing)
    assert (await client.get("/api/knowledge/whitepapers/SG", headers=user)).status_code == 404  # 还没生成
    assert (await client.get("/api/knowledge/whitepapers/CN", headers=user)).status_code == 404  # 没有的国家
    assert (await client.post("/api/admin/knowledge/whitepapers/SG/update", headers=user)).status_code in (401, 403)

    with system_scope("test whitepaper"):
        async with session_factory() as s:
            blocks, meta = await wp.write_whitepaper(s, "SG")
            await wp.save(s, "SG", blocks, meta)
            await s.commit()
    got = (await client.get("/api/knowledge/whitepapers/sg", headers=user)).json()
    assert got["available"] and got["version_no"] == 1 and got["blocks"] and got["outdated"] is False
    pdf = await client.get("/api/knowledge/whitepapers/SG/export.pdf", headers=user)
    docx = await client.get("/api/knowledge/whitepapers/SG/export.docx", headers=user)
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" and docx.status_code == 200 and docx.content[:2] == b"PK"
    # 再保存是新版本，旧版本不覆盖
    with system_scope("test whitepaper"):
        async with session_factory() as s:
            row = await wp.save(s, "SG", blocks, meta)
            await s.commit()
    assert row.version_no == 2


def test_the_same_clause_published_under_several_documents_is_shown_once():
    a = {"clause_number": "(96)", "quote_zh": "第(96)条 向经济和贸易部提交的申报必须包括以下信息：1 处理目的。2 个人数据及其来源。"}
    b = {"clause_number": "96", "quote_zh": "第96条 根据前一条向经济和贸易部提交的申报必须包括以下信息：1 – 处理目的。2 – 个人数据及其来源。"}
    c = {"clause_number": "97", "quote_zh": "第97条 另一条。"}
    assert wp._dedupe([a, b, c]) == [a, c]


def test_the_choice_menu_shows_the_part_of_a_long_clause_that_matters_for_the_dimension():
    long_clause = "第96条 申报必须包括：" + "；".join(f"{i}、事项{i}" for i in range(1, 40)) + "；40、必要时以任何形式将个人数据传输至另一国家。"
    glimpse = wp._glimpse("D3", long_clause)
    assert "另一国家" in glimpse and glimpse.startswith("第96条")
    assert "另一国家" not in wp._glimpse("D6", long_clause)  # 不同维度关注点不同


async def test_a_dimension_that_loses_most_of_its_text_to_the_checks_is_rewritten_once_with_the_reasons(
    session_factory, monkeypatch
):
    from app.core.rls import system_scope

    await _seed_sg_corpus(session_factory)
    calls = {"n": 0, "feedback": ""}

    async def fake(messages, **_kw):
        payload = json.loads(messages[1]["content"])
        if "candidates" in payload:
            return LlmResult(data={"keep": [1, 2]}, prompt_tokens=1, completion_tokens=1, duration_ms=1, model="fake")
        calls["n"] += 1
        if len(messages) > 2:  # 第二次：带着被拒原因
            calls["feedback"] = messages[-1]["content"]
            good = "该国法规对此有明确规定，" + "具体要求如下。" * 60
            return LlmResult(data={"paragraphs": [{"text": good, "cites": [1]}], "points": []}, prompt_tokens=1, completion_tokens=1, duration_ms=1, model="fake")
        bad = {"paragraphs": [{"text": "依据第 17 条规定。", "cites": [17]}], "points": []}  # 把法规条号当成了序号
        return LlmResult(data=bad, prompt_tokens=1, completion_tokens=1, duration_ms=1, model="fake")

    monkeypatch.setattr(wp, "call_llm_json", fake)
    with system_scope("test whitepaper"):
        async with session_factory() as s:
            blocks, meta = await wp.write_whitepaper(s, "SG")
    # 种子库只有部分维度有条文：有条文的维度各调用 2 次（第一次被拒、第二次带原因重写），没有的如实说明
    written = sum(1 for b in blocks if b["type"] == "paragraph" and "具体要求如下" in b["text"])
    assert written >= 3 and calls["n"] == 2 * written
    assert "没有提供给它的条文" in calls["feedback"] and "不是法规的条号" in calls["feedback"]


async def test_the_administrator_sees_every_country_with_version_cost_and_whether_it_is_outdated(client, seed, session_factory, monkeypatch):
    from app.core.rls import system_scope

    await _seed_sg_corpus(session_factory)
    monkeypatch.setattr(wp, "call_llm_json", _fake_model())
    admin = bearer(await login(client, "admin", "admin@example.com"))
    user = bearer(await login(client, "user", "a-member@example.com"))
    rows = (await client.get("/api/admin/knowledge/whitepapers", headers=admin)).json()
    assert {r["country_code"] for r in rows} == {"SG", "EG", "TN", "MA", "LB"} and not any(r["available"] for r in rows)
    assert (await client.get("/api/admin/knowledge/whitepapers", headers=user)).status_code in (401, 403)
    with system_scope("test whitepaper"):
        async with session_factory() as s:
            blocks, meta = await wp.write_whitepaper(s, "SG")
            await wp.save(s, "SG", blocks, meta)
            await s.commit()
    sg = next(r for r in (await client.get("/api/admin/knowledge/whitepapers", headers=admin)).json() if r["country_code"] == "SG")
    assert sg["available"] and sg["version_no"] == 1 and sg["tokens"] > 0 and sg["chars"] > 0 and sg["outdated"] is False
    pdf = await client.get("/api/admin/knowledge/whitepapers/SG/export.pdf", headers=admin)
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    assert (await client.get("/api/admin/knowledge/whitepapers/SG/export.pdf", headers=user)).status_code in (401, 403)


async def test_the_country_page_gives_overview_dimension_entries_and_clause_sources_without_a_model(client, seed, session_factory):
    """门户国家页用的结构：三条概述、8 个维度入口（结论句+数）、要点与出处；不调模型，随图谱变化。"""
    await _seed_sg_corpus(session_factory)
    auth = bearer(await login(client, "user", "a-member@example.com"))
    r = await client.get("/api/knowledge/whitepapers/SG/page", headers=auth)
    assert r.status_code == 200, r.text
    page = r.json()
    assert [o["label"] for o in page["overview"]] == ["法律体系", "监管核心", "风险警示"]
    assert [d["code"] for d in page["dimensions"]] == [f"D{i}" for i in range(1, 9)]
    for d in page["dimensions"]:
        assert d["headline"] and d["intro"] and "下表" not in d["intro"]
        assert d["total"] >= len(d["items"])
    ns = {ref["n"] for ref in page["refs"]}
    for d in page["dimensions"]:
        for it in d["items"]:
            assert it["title"] and isinstance(it["facts"], list)
            assert it["ref"] is None or it["ref"] in ns  # 每个出处都能在条文表里找到
    for ref in page["refs"]:
        assert ref["source"].startswith("《") and ref["doc_id"] and ref["clause_id"]
    # 简报的法规份数、条数与法规浏览同一口径
    listed = [x for x in (await client.get("/api/knowledge/regulations?country_code=SG", headers=auth)).json() if x["business_weight"] != "peripheral"]
    assert page["documents"] == len(listed) and page["clauses"] == sum(x["clauses_count"] for x in listed)
    overview = next(c for c in (await client.get("/api/knowledge/overview", headers=auth)).json() if c["country_code"] == "SG")
    assert overview["documents_count"] == len(listed) and overview["clauses_count"] == page["clauses"]
    d1 = next(d for d in page["dimensions"] if d["code"] == "D1")
    assert {it["doc_id"] for it in d1["items"] if it["doc_id"]} <= {x["id"] for x in listed}
    assert (await client.get("/api/knowledge/whitepapers/ZZ/page", headers=auth)).status_code == 404


def test_model_lines_for_the_country_page_keep_only_what_the_facts_allow():
    from app.knowledge import country_page_text as cpt

    meta = {
        "overview": [{"label": lbl, "text": f"{lbl}原句 49 项"} for lbl in cpt.LABELS],
        "dimensions": [{"code": "D3", "title": "跨境", "total": 24, "unit": "项要求", "headline": "例如：甲", "items": [{"title": "跨境须确保充分保护"}]}],
    }
    facts = cpt.facts_of(meta)
    got = cpt.check(
        {
            "overview": [
                {"label": "法律体系", "text": "以个人数据保护法为核心，共 49 项。"},  # 数字来自事实
                {"label": "监管核心", "text": "监管机构处罚上限 99999 万元。"},  # 编造数字
                {"label": "风险警示", "text": "本库收录的处罚规定较多。"},  # 系统口吻
            ],
            "dimensions": {"D3": "先看接收国是否提供充分保护。", "D9": "不存在的维度", "D1": "x" * 80},
        },
        facts,
    )
    assert list(got["overview"]) == ["法律体系"]
    assert got["dimensions"] == {"D3": "先看接收国是否提供充分保护。"}


async def test_the_page_uses_model_lines_only_while_the_facts_are_unchanged(client, seed, session_factory):
    from app.core.rls import system_scope
    from app.knowledge import country_page_text as cpt
    from app.knowledge import whitepaper_graph as wg

    await _seed_sg_corpus(session_factory)
    with system_scope("test page text"):
        async with session_factory() as s:
            _, meta = await wg.build(s, "SG")
            facts = cpt.facts_of(meta)
            s.add(cpt.CountryPageText(country_code="SG", data={"overview": {"法律体系": "模型写的短句。"}, "dimensions": {"D3": "模型的维度句。"}}, input_hash=cpt.input_hash(facts)))
            await s.commit()
    from app.knowledge import whitepaper_router

    whitepaper_router._page_cache.clear()
    auth = bearer(await login(client, "user", "a-member@example.com"))
    page = (await client.get("/api/knowledge/whitepapers/SG/page", headers=auth)).json()
    assert page["overview"][0]["text"] == "模型写的短句。" and page["overview"][1]["text"] != ""
    assert next(d for d in page["dimensions"] if d["code"] == "D3")["headline"] == "模型的维度句。"
    with system_scope("test page text"):
        async with session_factory() as s:
            row = await cpt.latest(s, "SG")
            row.input_hash = "stale"
            await s.commit()
    from app.knowledge import whitepaper_router

    whitepaper_router._page_cache.clear()
    page = (await client.get("/api/knowledge/whitepapers/SG/page", headers=auth)).json()
    assert page["overview"][0]["text"] != "模型写的短句。"  # 事实变了，退回程序自己的句子


async def test_documents_far_from_the_vehicle_data_business_are_not_counted_or_listed_but_can_be_found_by_name(client, seed, session_factory):
    """外围文件（谢绝来电登记、机构名单公告等）：不计入份数、不进目录和简报，按名称搜索时带“外围”标记返回。"""
    from sqlalchemy import select

    from app.ingestion.models import Document

    await _seed_sg_corpus(session_factory)
    auth = bearer(await login(client, "user", "a-member@example.com"))
    before = next(c for c in (await client.get("/api/knowledge/overview", headers=auth)).json() if c["country_code"] == "SG")["documents_count"]
    async with session_factory() as s:
        doc = (await s.scalars(select(Document).where(Document.country_code == "SG"))).first()
        doc.business_topic, doc.business_weight = "personal_data", "peripheral"
        await s.commit()
        doc_id = str(doc.id)
    from app.knowledge.cache_sync import clear_user_caches

    clear_user_caches()  # 运行中由后台每分钟比对指纹后重建；测试里直接清掉
    after = next(c for c in (await client.get("/api/knowledge/overview", headers=auth)).json() if c["country_code"] == "SG")["documents_count"]
    assert after == before - 1
    listed = {x["id"]: x for x in (await client.get("/api/knowledge/regulations?country_code=SG", headers=auth)).json()}
    assert listed[doc_id]["business_weight"] == "peripheral"  # 列表里带标记，页面默认不列、搜索时出现


async def test_the_country_page_routes_requirements_by_their_tagged_aspect_and_leaves_out_unrelated_ones(client, seed, session_factory):
    """要求按图谱标的“方面”归入各方面；标为与车辆数据业务无关的（如驾驶资格罚金）不进国家页；没有标签的按名称关键词。"""
    from sqlalchemy import select

    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge import whitepaper_router
    from app.knowledge.models import KgNode

    await _seed_sg_corpus(session_factory)
    async with session_factory() as s:
        doc = (await s.scalars(select(Document).where(Document.country_code == "SG"))).first()
        clause = (
            await s.scalars(select(Clause).join(DocumentVersion, Clause.version_id == DocumentVersion.id).where(DocumentVersion.document_id == doc.id))
        ).first()

        def node(key, kind, name, tag=None, onto=None):
            attrs = {"node_class": "concrete", "ontology": onto or {}}
            if tag:
                attrs["claim_tag"] = {"aspect": tag[0], "close": tag[1], "prompt": "test"}
            return KgNode(
                country_code="SG", node_key=key, node_type=kind, name_zh=name, attributes=attrs,
                document_id=doc.id, evidence_clause_id=clause.id, verify_state="auto",
            )

        s.add_all([
            node("a", "OBLIGATION", "车辆位置数据出海前须经主管机关批准", ("transfer", "vehicle")),
            node("b", "PENALTY", "无证驾驶罚金", ("penalty", "unrelated"), {"penalty_amount": {"value": "5000 新元"}}),
            node("c", "OBLIGATION", "处理前告知数据主体处理目的"),  # 没有标签：按关键词归入告知同意
            node("d", "OBLIGATION", "通知主管机关断开服务", ("other", "data")),  # 名称里有“通知”，但标签说不是告知同意
        ])
        await s.commit()
    whitepaper_router._page_cache.clear()
    auth = bearer(await login(client, "user", "a-member@example.com"))
    page = (await client.get("/api/knowledge/whitepapers/SG/page", headers=auth)).json()
    titles = {d["code"]: [it["title"] for it in d["items"]] for d in page["dimensions"]}
    assert "车辆位置数据出海前须经主管机关批准" in titles["D3"]
    assert "处理前告知数据主体处理目的" in titles["D4"]
    assert "通知主管机关断开服务" not in titles["D4"]
    assert not any("无证驾驶罚金" in t for code in titles for t in titles[code])
    assert "无证驾驶" not in page["overview"][2]["text"]


async def test_compliance_topics_use_the_country_page_aspects_and_numbers(client, seed, session_factory):
    """合规主题与国家简报同一套方面、同一份要求：每个方面的项数与简报对应方面一致，卡片写《中文名》第X条。"""
    from sqlalchemy import select

    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge import router as kr
    from app.knowledge import whitepaper_router
    from app.knowledge.models import KgNode

    await _seed_sg_corpus(session_factory)
    async with session_factory() as s:
        doc = (await s.scalars(select(Document).where(Document.country_code == "SG"))).first()
        clause = (
            await s.scalars(select(Clause).join(DocumentVersion, Clause.version_id == DocumentVersion.id).where(DocumentVersion.document_id == doc.id))
        ).first()
        for key, name, tag in (("t1", "跨境前须确保同等保护", ("transfer", "data")), ("t2", "收集前告知目的", ("consent", "data")), ("t3", "无证驾驶罚金", ("penalty", "unrelated"))):
            s.add(KgNode(country_code="SG", node_key=key, node_type="OBLIGATION", name_zh=name, document_id=doc.id, evidence_clause_id=clause.id,
                         verify_state="auto", attributes={"node_class": "concrete", "ontology": {}, "claim_tag": {"aspect": tag[0], "close": tag[1]}}))
        await s.commit()
    whitepaper_router._page_cache.clear()
    kr._TOPICS_CACHE.clear()
    auth = bearer(await login(client, "user", "a-member@example.com"))
    topics = (await client.get("/api/knowledge/topics?country_code=SG", headers=auth)).json()["items"]
    page = (await client.get("/api/knowledge/whitepapers/SG/page", headers=auth)).json()
    totals = {d["code"]: d["total"] for d in page["dimensions"]}
    by = {}
    for t in topics:
        by[t["topic"]] = by.get(t["topic"], 0) + 1
    assert by.get("transfer", 0) == totals["D3"] and by.get("consent", 0) == totals["D4"]
    assert not any(t["title"] == "无证驾驶罚金" for t in topics)
    card = next(t for t in topics if t["title"] == "跨境前须确保同等保护")
    assert card["topic_label"] == "跨境传输" and card["source"].startswith("《") and "第" in card["source"]


def test_short_names_of_a_body_become_its_one_proper_name():
    from app.knowledge.whitepaper import authority_forms, foreign_name_dropped, unify_authorities

    tn = authority_forms(["突尼斯国家个人数据保护局"], "突尼斯")
    assert unify_authorities("建议向国家个人数据保护机构书面确认。", tn) == "建议向突尼斯国家个人数据保护局书面确认。"
    assert unify_authorities("向国家个人数据保护机构（Instance nationale de protection）申报", tn) == "向突尼斯国家个人数据保护局申报"
    assert unify_authorities("突尼斯国家个人数据保护局（INPDP）", tn) == "突尼斯国家个人数据保护局（INPDP）"  # 已是全称，不重复
    sg = authority_forms(["新加坡个人数据保护委员会 PDPC"], "新加坡")
    assert unify_authorities("事件后向委员会通报", sg) == "事件后向新加坡个人数据保护委员会（PDPC）通报"
    assert unify_authorities("新加坡个人数据保护委员会（PDPC）（简称委员会）负责实施", sg) == "新加坡个人数据保护委员会（PDPC）负责实施"
    assert unify_authorities("向上诉委员会提出", sg) == "向上诉委员会提出"  # 别的委员会不动
    assert foreign_name_dropped("电信监管机构（الجهاز）") == "电信监管机构"
    assert foreign_name_dropped("黎巴嫩电信监管局（TRA）") == "黎巴嫩电信监管局（TRA）"
    lb = authority_forms(["黎巴嫩电信监管局（TRA）"], "黎巴嫩")
    assert unify_authorities("向TRA（黎巴嫩电信监管局）申请电信设备批准。", lb) == "向黎巴嫩电信监管局（TRA）申请电信设备批准。"
    from app.admission.names import normalize_body

    assert normalize_body("TRA（黎巴嫩电信监管局）", "LB") == "黎巴嫩电信监管局（TRA）"


def test_turnover_point_names_amending_clause_and_narrative_checks_connective():
    from app.analysis import document_narrative as nar
    from app.ingestion.noise import clean_translation
    from app.knowledge.whitepaper import turnover_point

    base = {"document_title": "个人数据保护法（2012年）", "clause_number": "48J", "quote_zh": "(1) (a) 违反第3部的任何条文；"}
    amend = {
        "document_title": "个人数据保护（修订）法（2020年）",
        "clause_number": "24",
        "quote_zh": (
            "第48J条现予修订：\n"
            "(3) 根据第(1)款第(a)项施加的罚款不得超过：\n"
            "(a) 对于由组织实施的违反行为的情形——$1百万或组织在新加坡的年度营业额的10%（以较高者为准）。"
        ),
    }
    refs: dict = {}
    out = turnover_point([{"type": "list", "items": []}], [amend], refs, base=base)
    assert "《个人数据保护（修订）法（2020年）》第24条修改了《个人数据保护法（2012年）》第48J条" in out[0]["items"][0]

    offered_all = {1: {"text": "向境外传输个人数据，须同时满足下列全部条件：（一）取得同意；（二）取得授权。", "clause": "第43条", "document": "某法"}}
    ok, reason = nar._check_paragraph({"text": "企业可任选其一满足上述条件。", "cites": [1]}, offered_all, set())
    assert ok is None and "同时满足" in reason

    offered_any = {1: {"text": "符合下列情形之一的，可以向境外提供个人信息：（一）取得认证；（二）订立标准合同。", "clause": "第38条", "document": "某法"}}
    ok2, reason2 = nar._check_paragraph({"text": "企业须同时满足认证与标准合同两项条件。", "cites": [1]}, offered_any, set())
    assert ok2 is None and "任选其一" in reason2

    assert (
        clean_translation("控制者须向国家个人数据保护委员会（الهيئة الوطنية لحماية المعطيات ذات الطابع الشخصي بالمملكة）事先申报。")
        == "控制者须向国家个人数据保护委员会事先申报。"
    )
    from app.analysis.clause_gate import why_unusable

    assert why_unusable("机器可读区域应使用至少8点的 OCR-B 字体。", "", "Schriftart OCR-B") == ""
    assert why_unusable("第3条（OCR不清）进口商须登记。", "", "") == "识别不清"



def test_layout_residue_in_a_clause_is_cleaned():
    from app.ingestion.noise import structure

    assert structure("同意的撤回的同意 16。——（1）在向组织发出合理通知后，个人可随时撤回").startswith("第16条 （1）")
    assert structure("本法4的适用。——（1）第3部分、第4部分不向下列主体施加任何义务").startswith("第4条 （1）")
    assert structure("个人数据保护委员会\n5.—(1) 资讯通信媒体发展局被指定为个人数据保护委员会。\n[22/2016]\n(2) 个人数据保护委员会负责本法的实施。\n[22/2016]").count("[22/2016]") == 0
    assert structure("第 3 条——关于申报\n第 26 条——当申报符合规定时，CNDP 签发回执") .startswith("第 26 条")
    assert structure("第74条：\n视频记录必须予以销毁。\n第 VI 章 该机构国家个人数据保护机构") .endswith("予以销毁。")
    assert structure("5. 新闻组织仅为其新闻活动收集、使用或披露关于个人的个人数据。") .startswith("5. 新闻组织")  # 普通条文不动


def test_superseded_documents_are_not_the_law_as_it_stands():
    from app.analysis.clause_gate import superseded_titles

    gone = superseded_titles([
        "2012年个人数据保护法（2012年颁布原文）", "2012年个人数据保护法（2020年修订版，现行合订本）", "转移限制义务第19章历史指南",
        "2014年个人数据保护条例", "2021年个人数据保护条例", "2021年个人数据保护（修订）条例",
    ])
    assert gone == {"2012年个人数据保护法（2012年颁布原文）", "转移限制义务第19章历史指南", "2014年个人数据保护条例"}


def test_advice_to_ask_a_body_must_name_a_body_the_cited_clauses_name():
    from app.knowledge.whitepaper import advice_checked

    offered = {1: {"text": "新加坡网络安全局（CSA）负责关键信息基础设施的监管。", "authority": "新加坡网络安全局（CSA）"}}
    data = {"paragraphs": [{"text": "该法由CSA监督。车联网还涉及网络安全等事项，建议就这些事项向新加坡个人数据保护委员会（PDPC）书面确认适用要求。", "cites": [1]}], "points": []}
    assert "书面确认" not in advice_checked("D3", data, offered, False)["paragraphs"][0]["text"]
    ok = {"paragraphs": [{"text": "建议就监管要求向新加坡网络安全局（CSA）书面确认。", "cites": [1]}], "points": []}
    assert advice_checked("D3", ok, offered, False)["paragraphs"]
    assert advice_checked("D1", ok, offered, False)["paragraphs"] == []  # 第一、二方面不写这类话
    adm = {"paragraphs": [{"text": "申报要先递交材料；其他准入手续建议向主管机构书面确认。", "cites": [1]}], "points": []}
    assert "书面确认" not in advice_checked("D8", adm, offered, True)["paragraphs"][0]["text"]


def test_material_plan_has_one_row_per_material():
    from app.admission.material_kinds import tidy_plan

    plan = [
        {"name": "数据安全技术证书和认可文件", "kind": "original"}, {"name": "颁发机构信息", "kind": "original"}, {"name": "有效期", "kind": "original"},
        {"name": "数据安全技术证书和认可文件（写明颁发机构、取得日期、有效期）", "kind": "original"},
        {"name": "数据保护负责人指定信息", "kind": "info"}, {"name": "数据保护负责人指定信息", "kind": "original"},
    ]
    assert [p["name"] for p in tidy_plan(plan)] == ["数据安全技术证书和认可文件", "数据保护负责人指定信息"]
    assert tidy_plan(plan)[1]["kind"] == "info"
