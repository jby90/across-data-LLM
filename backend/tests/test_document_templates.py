"""Standard compliance documents: templates are versioned data; nothing is invented or skipped."""

import copy
import io
import zipfile

import pytest

from app.analysis.document_templates import (
    TemplateError,
    build_docx,
    render,
    resolve_facts,
    validate_definition,
)
from app.analysis.document_templates_default import DEFAULT_TEMPLATES

from .conftest import bearer, login

CODES = [code for code, _, _ in DEFAULT_TEMPLATES]


def _fake_paper_llm():
    """A model that writes a short, valid paper: no figures of its own, citations only to offered clauses."""

    async def fake(messages, **_kw):
        import json as _json

        from app.analysis.llm import LlmResult

        payload = _json.loads(messages[1]["content"])
        offered = [c["n"] for c in payload["clauses"]][:1]
        data = {"paragraphs": [{"text": "依据企业提供的事实，该处理有明确目的并采取了相应措施。", "cites": offered}]}
        if "flow" in payload["schema"]:
            data["flow"] = [{"stage": "采集", "data": "车端数据", "where_who": "车端", "safeguard": "脱敏"}, {"stage": "存储", "data": "数据", "where_who": "境内", "safeguard": "加密"}]
        if "risks" in payload["schema"]:
            data["risks"] = [{"risk": "被识别", "likelihood": "中", "severity": "高", "measure": "脱敏", "cites": []}]
        return LlmResult(data=data, prompt_tokens=10, completion_tokens=5, duration_ms=1, model="fake")

    return fake


@pytest.fixture(autouse=True)
def _paper_model(monkeypatch):
    from app.analysis import document_narrative

    monkeypatch.setattr(document_narrative, "call_llm_json", _fake_paper_llm())


def _definition(code):
    return next(d for c, _, d in DEFAULT_TEMPLATES if c == code)


@pytest.mark.parametrize("code,_name,definition", DEFAULT_TEMPLATES)
def test_every_starting_template_is_well_formed(code, _name, definition):
    validate_definition(definition)
    assert code in (
        "declaration",
        "risk_report",
        "contract",
        "remediation",
        "admission",
        "dpia",
        "lia",
        "tia",
    )


def test_a_template_may_not_use_a_fact_it_does_not_declare():
    bad = copy.deepcopy(DEFAULT_TEMPLATES[0][2])
    bad["sections"][0]["blocks"].append({"type": "text", "text": "见 {{undeclared_fact}}"})
    with pytest.raises(TemplateError):
        validate_definition(bad)


def test_the_assessment_value_wins_and_the_documents_own_answer_fills_the_rest():
    definition = DEFAULT_TEMPLATES[0][2]
    values = resolve_facts(
        definition,
        {"country_name": "摩洛哥", "purpose": "忽略我"},
        {"doc_purpose": "道路测试与改进自动驾驶功能", "doc_company_name": "某汽车公司"},
    )
    assert values["country_name"] == "摩洛哥"
    assert values["purpose"] == "忽略我"  # 评估里已有的事实不被覆盖
    assert values["company_name"] == "某汽车公司"


def test_missing_facts_are_asked_in_template_order_and_never_filled_in():
    definition = DEFAULT_TEMPLATES[0][2]
    values = resolve_facts(definition, {"country_name": "埃及"}, {})
    rendered = render(definition, values, [])
    asked = [q.key for q in rendered.missing]
    assert asked[:3] == ["source_country", "recipient_country", "vehicle_model"]  # 按模板里声明的顺序
    assert "country_name" not in asked
    assert all(q.question for q in rendered.missing)  # 每个缺项都有可回答的问题


def test_a_complete_document_has_no_questions_and_carries_the_legal_basis():
    definition = DEFAULT_TEMPLATES[2][2]
    values = {f["key"]: f"值-{f['key']}" for f in definition["facts"]}
    citations = [
        {
            "dimension_code": "D3",
            "document_title": "某数据保护法",
            "clause_number": "26",
            "quote_zh": "跨境传输须保障同等保护。",
        }
    ]
    rendered = render(definition, values, citations)
    assert rendered.missing == []
    rows = [r for b in rendered.blocks if b["type"] == "table" for r in b["rows"]]
    assert ["《某数据保护法》", "26", "跨境传输须保障同等保护。"] in rows  # 依据表写《法规名》，与法规浏览一致


def test_citations_follow_the_templates_topics_and_never_show_a_placeholder():
    # 用一份自带“依据表”的小模板：只取 D3（跨境传输）方面的条文，别的方面不进表
    definition = {
        "title": "测",
        "facts": [{"key": "a", "label": "甲", "question": "问", "why": ""}],
        "sections": [{"heading": "依据", "blocks": [{"type": "citations", "dimensions": ["D3"], "limit": 8}]}],
    }
    values = {"a": "值"}
    citations = [
        {
            "dimension_code": "D3",
            "document_title": "A法",
            "clause_number": "1",
            "quote_zh": "(未提供译文)",
            "quote_original": "Transfers abroad require safeguards.",
        },
        {"dimension_code": "D5", "document_title": "B法", "clause_number": "2", "quote_zh": "存储"},
    ]
    rows = [
        r
        for b in render(definition, values, citations).blocks
        if b["type"] == "table"
        for r in b["rows"]
    ]
    assert ["《A法》", "1", "Transfers abroad require safeguards."] in rows
    assert not any(r[0] == "B法" for r in rows)


def test_the_word_file_is_a_real_package_with_the_filled_text():
    definition = _definition("lia")
    values = {f["key"]: f"内容{f['key']}" for f in definition["facts"]}
    paper = [{"type": "heading", "text": "一、目的测试"}, {"type": "paragraph", "text": "内容purpose"}]
    data = build_docx(render(definition, values, [], None, paper), "对话 | 合法利益评估")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        body = zf.read("word/document.xml").decode("utf-8")
    assert "合法利益评估" in body and "内容purpose" in body and "{{" not in body


async def _task(client, auth, facts=None):
    r = await client.post(
        "/api/analysis/tasks",
        headers=auth,
        json={
            "title": "文件测试",
            "country_code": "MA",
            "vehicle_model": "某车型",
            "scenario_type": "ota_diagnostics",
            "facts": facts or {},
            "auto_run_if_sufficient": False,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.parametrize("code", CODES)
async def test_the_user_is_asked_for_what_is_missing_then_gets_the_document(client, seed, code):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = await _task(client, auth)
    state = (await client.get(f"/api/analysis/tasks/{tid}/documents/{code}", headers=auth)).json()
    if code == "admission":  # 05 的内容全部来自准入事项库，平台已知的不再问；只看能否出件
        assert (await client.get(f"/api/analysis/tasks/{tid}/documents/{code}/export.docx?draft=true", headers=auth)).status_code == 200
        return
    assert not state["ready"] and state["questions"]
    blocked = await client.get(
        f"/api/analysis/tasks/{tid}/documents/{code}/export.docx", headers=auth
    )
    assert blocked.status_code == 409  # 缺信息不出文件
    while not state["ready"]:
        if not state["questions"]:
            assert state["needs_paper"]  # 信息答齐后，论述部分要先生成
            blocked = await client.get(f"/api/analysis/tasks/{tid}/documents/{code}/export.docx", headers=auth)
            assert blocked.status_code == 409
            state = (await client.post(f"/api/analysis/tasks/{tid}/documents/{code}/paper", headers=auth)).json()
            continue
        q = state["questions"][0]
        said = f"用户对“{q['label']}”的回答"
        if q["key"] == "risk_rating":  # 主要风险按行填：一段文字不算填完
            plain = await client.post(f"/api/analysis/tasks/{tid}/documents/{code}/answers", headers=auth, json={"answers": {q["key"]: said}})
            assert plain.status_code == 422 and "逐项填写" in plain.json()["detail"]
            said = '[{"risk": "被识别", "likelihood": "中", "severity": "高", "measures": "脱敏"}]'
        reply = await client.post(
            f"/api/analysis/tasks/{tid}/documents/{code}/answers",
            headers=auth,
            json={"answers": {q["key"]: said}},
        )
        assert reply.status_code == 200, reply.text
        state = reply.json()
    done = await client.get(f"/api/analysis/tasks/{tid}/documents/{code}/export.docx", headers=auth)
    assert done.status_code == 200 and done.content[:2] == b"PK"


async def test_answers_that_are_empty_too_long_or_not_asked_are_refused(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = await _task(client, auth)
    url = f"/api/analysis/tasks/{tid}/documents/dpia/answers"
    question = (await client.get(f"/api/analysis/tasks/{tid}/documents/dpia", headers=auth)).json()[
        "questions"
    ][0]["key"]
    assert (
        await client.post(url, headers=auth, json={"answers": {question: " "}})
    ).status_code == 422
    assert (
        await client.post(url, headers=auth, json={"answers": {question: "字" * 1001}})
    ).status_code == 422
    assert (
        await client.post(url, headers=auth, json={"answers": {"nope": "回答"}})
    ).status_code == 409


async def test_another_organisation_cannot_reach_the_document(client, seed):
    owner = bearer(await login(client, "user", "a-member@example.com"))
    tid = await _task(client, owner)
    other = bearer(await login(client, "user", "b-admin@example.com"))
    r = await client.get(f"/api/analysis/tasks/{tid}/documents/dpia", headers=other)
    assert r.status_code == 404


async def test_editing_a_template_adds_a_version_and_bad_templates_are_refused(client, seed):
    admin = bearer(await login(client, "admin", "admin@example.com"))
    current = (await client.get("/api/admin/document-templates/lia", headers=admin)).json()
    assert current["version_no"] == 1
    edited = copy.deepcopy(current["definition"])
    edited["narrative"][0]["brief"] += " 补充说明。"
    ok = await client.put(
        "/api/admin/document-templates/lia",
        headers=admin,
        json={"name_zh": current["name"], "definition": edited},
    )
    assert ok.status_code == 200 and ok.json()["version_no"] == 2
    bad = copy.deepcopy(edited)
    bad["sections"][0]["blocks"].append({"type": "text", "text": "{{missing_fact}}"})
    refused = await client.put(
        "/api/admin/document-templates/lia",
        headers=admin,
        json={"name_zh": current["name"], "definition": bad},
    )
    assert refused.status_code == 422
    member = bearer(await login(client, "user", "a-member@example.com"))
    assert (await client.get("/api/admin/document-templates/lia", headers=member)).status_code in (
        401,
        403,
    )


async def _complete(client, auth, tid, code):
    state = (await client.get(f"/api/analysis/tasks/{tid}/documents/{code}", headers=auth)).json()
    while not state["ready"]:
        if not state["questions"]:
            assert state["needs_paper"]
            state = (await client.post(f"/api/analysis/tasks/{tid}/documents/{code}/paper", headers=auth)).json()
            continue
        q = state["questions"][0]
        said = f"对“{q['label']}”的回答"
        if q["key"] == "risk_rating":  # 主要风险按行填
            said = '[{"risk": "被识别", "likelihood": "中", "severity": "高", "measures": "脱敏"}]'
        state = (
            await client.post(
                f"/api/analysis/tasks/{tid}/documents/{code}/answers",
                headers=auth,
                json={"answers": {q["key"]: said}},
            )
        ).json()


async def test_a_finished_document_can_be_edited_and_every_change_is_logged(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = await _task(client, auth)
    base = f"/api/analysis/tasks/{tid}/documents/dpia"
    assert (await client.get(f"{base}/saved", headers=auth)).json() == {"exists": False}
    assert (await client.post(f"{base}/saved", headers=auth)).status_code == 409  # 信息不全不保存
    await _complete(client, auth, tid, "dpia")
    saved = (await client.post(f"{base}/saved", headers=auth)).json()
    assert saved["exists"] and saved["edit_count"] == 0
    first_table = next(i for i, b in enumerate(saved["blocks"]) if b["type"] == "table")
    edit = {"block": first_table, "row": 0, "col": 1, "text": "改后的内容"}
    after = (await client.patch(f"{base}/saved", headers=auth, json=edit)).json()
    assert after["edit_count"] == 1
    log = after["edits"][0]
    assert log["after"] == "改后的内容" and log["before"] and log["user_name"]
    # 再保存不会覆盖修改；导出用修改后的文字
    again = (await client.post(f"{base}/saved", headers=auth)).json()
    assert again["blocks"][first_table]["rows"][0][1] == "改后的内容"
    docx = await client.get(f"{base}/export.docx", headers=auth)
    assert docx.status_code == 200 and "改后的内容" in _docx_text(docx.content)
    xlsx = await client.get(f"{base}/export.xlsx", headers=auth)
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    pdf = await client.get(f"{base}/export.pdf", headers=auth)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read("word/document.xml").decode("utf-8")


async def test_bad_edits_are_refused_and_other_organisations_cannot_touch_the_copy(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = await _task(client, auth)
    base = f"/api/analysis/tasks/{tid}/documents/dpia"
    assert (
        await client.patch(f"{base}/saved", headers=auth, json={"block": 0, "text": "x"})
    ).status_code == 404  # 还没有副本
    await _complete(client, auth, tid, "dpia")
    await client.post(f"{base}/saved", headers=auth)
    for bad in (
        {"block": 999, "text": "内容"},
        {"block": 0, "text": "   "},
        {"block": 0, "text": "字" * 4001},
        {"block": 0, "item": 0, "text": "标题没有条目"},
    ):
        assert (await client.patch(f"{base}/saved", headers=auth, json=bad)).status_code == 422
    other = bearer(await login(client, "user", "b-admin@example.com"))
    assert (await client.get(f"{base}/saved", headers=other)).status_code == 404


def test_the_workbook_keeps_titles_tables_and_lists():
    from openpyxl import load_workbook

    from app.analysis.document_instances import build_xlsx

    data = build_xlsx(
        [
            {"type": "title", "text": "申报表"},
            {"type": "heading", "text": "一、基本情况"},
            {"type": "table", "head": ["事项", "内容"], "rows": [["公司", "某公司"]]},
            {"type": "list", "items": ["第一条"]},
        ],
        "申报表",
    )
    cells = [c.value for row in load_workbook(io.BytesIO(data)).active.iter_rows() for c in row]
    assert "申报表" in cells and "某公司" in cells and "· 第一条" in cells


def test_an_unknown_scenario_code_is_never_written_into_a_document():
    from types import SimpleNamespace

    from app.analysis.documents_router import _scenario

    assert _scenario(SimpleNamespace(scenario_type="data_return")) == {"scenario": ""}
    assert _scenario(SimpleNamespace(scenario_type=None)) == {"scenario": ""}
    assert _scenario(SimpleNamespace(scenario_type="ota_diagnostics"))["scenario"].startswith(
        "跨境"
    )


def test_a_full_stop_in_the_answer_is_not_doubled_by_the_templates_own():
    definition = {
        "title": "测",
        "facts": [{"key": "a", "label": "甲", "question": "问", "why": ""}],
        "sections": [{"heading": "一", "blocks": [{"type": "text", "text": "内容：{{a}}。"}]}],
    }
    out = render(definition, {"a": "已完成。"}, [])
    assert [b["text"] for b in out.blocks if b["type"] == "paragraph"] == ["内容：已完成。"]


async def test_citations_from_older_document_version_resolve_to_latest_version(session_factory):
    from app.analysis.documents_router import _resolve_citation_to_current
    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge.models import ClauseTranslation
    from app.sources.models import SourceSite

    body_v1 = "Article 16: The authority shall supervise personal data processing."
    body_v2 = "Article 16: The authority shall supervise personal data processing and transfers."
    async with session_factory() as s:
        site = SourceSite(
            country_code="MA",
            publisher="CNDP",
            language="en",
            document_type="LAW",
            entry_url="https://cndp.example/law",
            strategy="DIRECT",
            terms_status="pending",
            enabled=False,
        )
        s.add(site)
        await s.flush()
        doc = Document(
            site_id=site.id,
            country_code="MA",
            title="Law 09-08",
            document_type="LAW",
            language="en",
            source_url="https://cndp.example/law",
        )
        s.add(doc)
        await s.flush()
        v1 = DocumentVersion(
            document_id=doc.id,
            version_no=1,
            content_hash="1" * 64,
            archive_path="v1",
            mime_type="application/pdf",
            size_bytes=1,
            final_url="https://cndp.example/law",
            state="parsed",
        )
        v2 = DocumentVersion(
            document_id=doc.id,
            version_no=2,
            content_hash="2" * 64,
            archive_path="v2",
            mime_type="application/pdf",
            size_bytes=1,
            final_url="https://cndp.example/law",
            state="parsed",
        )
        s.add_all([v1, v2])
        await s.flush()
        c1 = Clause(
            version_id=v1.id,
            ordinal=1,
            clause_number="16",
            language="en",
            original_text=body_v1,
            text_hash="a" * 64,
        )
        c2 = Clause(
            version_id=v2.id,
            ordinal=1,
            clause_number="16",
            language="en",
            original_text=body_v2,
            text_hash="b" * 64,
        )
        s.add_all([c1, c2])
        await s.flush()
        tr2 = ClauseTranslation(
            clause_id=c2.id,
            translated_text="第16条：主管机构应当监督个人数据处理及跨境传输。",
            engine="llm",
            model_name="deepseek-chat",
            prompt_version="v1",
            quality_check_passed=True,
            review_status="machine_translated",
            is_current=True,
        )
        s.add(tr2)
        await s.commit()

        resolved = await _resolve_citation_to_current(
            s,
            {
                "clause_id": str(c1.id),
                "document_id": str(doc.id),
                "document_title": "Law 09-08",
                "clause_number": "16",
                "quote_original": body_v1,
                "quote_zh": "(未提供译文)",
            },
        )
        assert resolved["clause_id"] == str(c2.id)
        assert resolved["version_no"] == 2
        assert resolved["quote_original"] == body_v2
        assert "监督个人数据处理及跨境传输" in resolved["quote_zh"]


def test_the_basis_lines_depend_on_the_ends_named_see_test_document_basis():
    from app.analysis.document_basis import basis_lines

    assert any("SCC" in line for line in basis_lines("contract", "EG"))
    assert any("东盟" in line for line in basis_lines("contract", "SG"))
    assert not any("东盟" in line for line in basis_lines("contract", "EG"))

async def test_the_declaration_is_the_sending_countrys_official_export_form_and_local_filings_belong_to_admission(client, seed):
    """申报表 = 发出国的出境申报：发出国有官方原表就用（摩洛哥 F118），没有官方表时不挂任何表格；
    在某国处理数据要办的申报/事先授权（F211/F112）不放在申报表，而是该国属地准入事项的官方表格。"""
    from types import SimpleNamespace

    from app.admission.router import entry_forms

    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = await _task(client, auth, {"doc_source_country": "摩洛哥", "doc_recipient_country": "中国"})
    state = (await client.get(f"/api/analysis/tasks/{tid}/documents/declaration", headers=auth)).json()
    assert [f["code"] for f in state["official_forms"]] == ["MA_F118"]
    other = await _task(client, auth, {"doc_source_country": "中国", "doc_recipient_country": "摩洛哥", "doc_sensitive_pi": "是"})
    state = (await client.get(f"/api/analysis/tasks/{other}/documents/declaration", headers=auth)).json()
    assert state["official_forms"] == []  # 中国发出：申报表是网信办 Word 原件，不挂摩洛哥的处理申报
    none = (await client.get(f"/api/analysis/tasks/{tid}/documents/dpia", headers=auth)).json()
    assert none["official_forms"] == []  # 只有申报表挂官方表格

    def entry(name, summary=""):
        return SimpleNamespace(country_code="MA", name_zh=name, summary_zh=summary)

    # 11.09 接入 CNDP 简化表后：普通表在前，简化表随后，并写明何时用它代替普通表
    declared = entry_forms(entry("个人数据处理事先申报", "不属于需授权情形时须事先申报"))
    assert [f["code"] for f in declared] == ["MA_F211", "MA_F214"]
    assert "代替普通申报 F211" in declared[1]["applies_when"]
    authorised = entry_forms(entry("个人数据处理事先授权"))
    assert [f["code"] for f in authorised] == ["MA_F112", "MA_F113"]
    assert "代替 F112" in authorised[1]["applies_when"]
    assert [f["code"] for f in entry_forms(entry("个人数据跨境传输授权"))] == ["MA_F118"]
    assert [f["code"] for f in entry_forms(entry("无线电设施与终端设备批准"))] == ["MA_ANRT_AGREMENT"]


def test_data_leaving_china_gets_the_official_contract_text_word_for_word_with_known_facts_filled():
    definition = _definition("contract")
    values = {f["key"]: f"值-{f['key']}" for f in definition["facts"]}
    values.update(source_country="中国", recipient_country="新加坡", company_name="示例汽车科技有限公司", recipient="Example Pte Ltd")
    cn = render(definition, values, [])
    heads = [b["text"] for b in cn.blocks if b["type"] == "heading"]
    assert heads[0].startswith("个人信息出境标准合同") and "第一条 定义" in heads and "附录一 个人信息出境说明" in heads
    assert "第四条 境外接收方所在国家或者地区个人信息保护政策和法规对合同履行的影响" in heads
    text = chr(10).join(b.get("text", "") for b in cn.blocks)
    assert "个人信息处理者：示例汽车科技有限公司" in text and "境外接收方：Example Pte Ltd" in text
    assert "（一）处理目的：值-purpose" in text
    assert "责任与免责条款" not in text  # 欧盟结构的章节不出现
    other = render(definition, {**values, "source_country": "埃及"}, [])
    assert "第一条 定义" not in [b["text"] for b in other.blocks if b["type"] == "heading"]
    assert any("责任与免责条款" in b.get("text", "") for b in other.blocks)


def test_characters_word_cannot_open_are_removed_from_every_run():
    """原文提取里偶尔混入 U+FFFE、控制符：写进去整份 Word 就打不开，必须在写出时去掉。"""
    import io
    import zipfile
    from xml.etree import ElementTree

    from app.analysis.document_templates import Rendered, build_docx

    dirty = "第 D\ufffe126-EUS-2020 号\x0b条\x00款\uffff"
    data = build_docx(Rendered(blocks=[{"type": "paragraph", "text": dirty}, {"type": "list", "items": [dirty]}]), "ctx")
    xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml")
    ElementTree.fromstring(xml)  # 不再抛“not well-formed”
    assert "D126-EUS-2020 号条款" in xml.decode("utf-8")


def test_a_gazette_table_of_contents_glued_to_a_clause_is_cut_from_the_quote():
    from app.analysis.documents_router import cut_at_contents

    text = "第(2)条\n本决定在官方公报上公布，自公布次日起施行。\n部长 某某\nI\nE\n官方公报 第 244 期\n目录\n页码 主题\n个人数据的收集"
    assert cut_at_contents(text).split("\n") == ["第(2)条", "本决定在官方公报上公布，自公布次日起施行。", "部长 某某"]
    ar = "المادة (2)\nيُنشر هذا القرار\nالفهرس\nالصفحة"
    assert cut_at_contents(ar) == "المادة (2)\nيُنشر هذا القرار"
    assert cut_at_contents("目录\n第一条 适用范围") == "目录\n第一条 适用范围"  # 开头就是目录：不是粘连，不动


def test_gazette_running_headers_are_dropped_from_quotes_and_reading_but_sentences_mentioning_the_gazette_stay():
    from app.ingestion.display_text import for_reading

    ar = "المادة 2\nالوقائع المصرية  العدد ٢٤٤ تابع فى أول نوفمبر سنة ٢٠٢٥\nيلتزم المتحكم بما يلي\nالعدد٨\nينشر هذا القرار في الوقائع المصرية"
    assert for_reading(ar) == "المادة 2\nيلتزم المتحكم بما يلي\nينشر هذا القرار في الوقائع المصرية"
    zh = "第2条\n官方公报 - 第 61 期 — 2004/7/30\n控制者应当\n本法在官方公报上公布，并作为国家法律之一执行。"
    assert for_reading(zh) == "第2条\n控制者应当\n本法在官方公报上公布，并作为国家法律之一执行。"
    assert for_reading("Journal officiel de la République tunisienne — N° 61\nArticle 3") == "Article 3"


def test_the_pdf_of_a_document_is_its_word_file_converted(monkeypatch):
    """PDF 就是同一份 Word 转出来的：文字一字不差、顺序相同（官方原件的勾选框也一样）。本机没有办公软件时跳过。"""
    import io
    import re
    import zipfile
    from xml.etree import ElementTree as ET

    import pypdfium2 as pdfium

    from app.analysis import docx_pdf
    from app.analysis import official_docx as od

    monkeypatch.delenv("CYSH_PDF_ENGINE", raising=False)
    if docx_pdf.engine() == "builtin":
        pytest.skip("本机没有可把 Word 转成 PDF 的办公软件")
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    filled = od.cn_declaration({"company_name": "甲车企有限公司", "entity_nature": "企业"}).data
    xml = zipfile.ZipFile(io.BytesIO(filled)).read("word/document.xml")
    word = re.sub(r"\s+", "", "".join(t.text or "" for t in ET.fromstring(xml).iter(w + "t")))
    pdf = pdfium.PdfDocument(docx_pdf.docx_to_pdf(filled))
    text = "".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
    # 勾选框在 PDF 里是符号字体的私用区字符，页码是数字：都不算正文，两边去掉后逐字相同
    def norm(t):
        return re.sub(r"[\s\d-☐☑□]+", "", t)

    assert norm(text) == norm(word)
    assert "甲车企有限公司" in text


def test_a_paragraph_with_a_stray_english_word_is_not_used_but_names_from_the_facts_are_fine():
    from app.analysis.document_narrative import _check_paragraph

    names = {"nile", "auto", "services"}
    bad = {"text": "安全评估 likewise 将法律文件列为重点。", "cites": []}
    ok = {"text": "接收方 Nile Auto Services 采用 TLS 加密，factsheet 不算。".replace("，factsheet 不算", ""), "cites": []}
    assert _check_paragraph(bad, {}, set(), names)[0] is None
    assert _check_paragraph(ok, {}, set(), names)[0] is not None
    assert _check_paragraph({"text": "依据 facts 写成。", "cites": []}, {}, set(), names)[0] is None


def test_the_official_pia_does_not_repeat_a_fact_already_written_in_the_same_box():
    import io
    import re
    import zipfile

    from app.analysis.official_docx import cn_pia

    r = cn_pia({"purpose": "远程诊断", "lawful_basis": "取得同意", "data_subjects": "车主约 3 万人", "data_volume": "约 3 万人"}, blank="【待补充】")
    text = re.sub(r"<[^>]+>", "", zipfile.ZipFile(io.BytesIO(r.data)).read("word/document.xml").decode("utf-8"))
    assert text.count("约 3 万人") == 1 and "处理的合法性基础：取得同意" in text


async def test_a_section_with_a_paragraph_that_fails_the_checks_is_written_again(monkeypatch):
    """引了没给的条文的段落不用；整节再写一次，不留下接不上的半截论述。"""
    from types import SimpleNamespace

    from app.analysis import document_narrative as N

    calls = []

    async def fake(messages, **_):
        calls.append(messages)
        if len(calls) == 1:
            return SimpleNamespace(data={"paragraphs": [{"text": "依据第一条开头的一段。", "cites": [9]}, {"text": "该机制还受限制。", "cites": []}]}, prompt_tokens=10, completion_tokens=5)
        return SimpleNamespace(data={"paragraphs": [{"text": "完整的一段。", "cites": []}, {"text": "第二段。", "cites": []}]}, prompt_tokens=10, completion_tokens=5)

    monkeypatch.setattr(N, "call_llm_json", fake)
    sec = N.Section(key="tool", heading="二、机制", brief="说明机制", facts=[])
    blocks, tokens, dropped = await N.write_section(sec, {}, {}, [], {}, {})
    assert len(calls) == 2 and "未通过核对" in calls[1][-1]["content"]
    assert [b["text"] for b in blocks[1:]] == ["完整的一段。", "第二段。"] and dropped == [] and tokens == 30
