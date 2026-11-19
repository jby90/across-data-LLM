"""咨询版报告的导出与接口：法条只在参考文献出现；旧报告不受影响。合成数据，隔离测试库。"""

import io
import json
import zipfile

from sqlalchemy import select

from app.analysis import consulting as c
from app.analysis.llm import LlmResult
from app.analysis.models import AnalysisTask, ReportVersion

from .conftest import bearer, login
from .test_consulting import CFG, FACTS, SCENARIO, dim, finding

QUOTE_1 = "ORIGINAL_QUOTE_MARKER_ONE the recipient must be bound"
QUOTE_3 = "ORIGINAL_QUOTE_MARKER_THREE consent before collection"


def evidence_dims():
    dims = [
        dim(
            "D1",
            "传输路径",
            [
                finding(
                    "F-01",
                    "a1",
                    "传输前须确认接收方受法律约束",
                    "ZH_QUOTE_MARKER_ONE 接收方须受约束。",
                    QUOTE_1,
                )
            ],
        ),
        dim(
            "D3",
            "同意",
            [
                finding(
                    "F-03",
                    "a3",
                    "收集前须告知目的并取得同意",
                    "ZH_QUOTE_MARKER_THREE 收集前须同意。",
                    QUOTE_3,
                )
            ],
        ),
    ]
    out = []
    for i in range(1, 9):
        code = f"D{i}"
        out.append(
            next((d for d in dims if d["dimension_code"] == code), None)
            or dim(code, f"维度{i}", [])
        )
    return out


REPLIES = {
    "D1": {
        "conclusion": "回传之前要先补上跨境传输的法律文件，**现在这一步是空白**。",
        "assessments": [
            {
                "requirement": "回传前确认接收方受法律约束",
                "evidence_ids": ["F-01"],
                "business_fact_keys": ["transfer"],
                "business_state": "目前只有传输加密，没有传输协议",
                "status": "non_compliant",
                "likelihood": 5,
                "impact": 4,
                "rationale": "没有协议就无法证明接收方受约束",
            }
        ],
        "actions": [
            {
                "category": "organization",
                "action": "与接收方签署数据传输协议",
                "owner_role": "法务负责人",
                "due_in_days": 30,
                "done_criteria": "协议签署并留存评估记录",
                "addresses": [1],
            }
        ],
    },
    "D3": {
        "conclusion": "摄像头图像的告知和同意需要单独设计，现在还没有。",
        "assessments": [
            {
                "requirement": "收集前告知目的并取得同意",
                "evidence_ids": ["F-03"],
                "business_fact_keys": ["data_types"],
                "business_state": "驾驶员摄像头图像被一并回传",
                "status": "partial",
                "likelihood": 3,
                "impact": 4,
                "rationale": "图像能识别个人，同意范围需要单独说明",
            }
        ],
        "actions": [
            {
                "category": "vehicle",
                "action": "摄像头图像默认只在车内处理，上传前单独征得同意",
                "owner_role": "产品负责人",
                "due_in_days": 60,
                "done_criteria": "新版车机流程通过评审",
                "addresses": [1],
            }
        ],
    },
}
SYNTH = {
    "verdict": "有条件可行",
    "headline": "可以做，但先把传输协议和同意流程补上",
    "summary": "最大缺口是传输协议缺失，其次是摄像头图像的同意，建议先在 30 天内补齐协议。",
    "must_do_action_ids": ["A-01"],
    # 同意流程改造要等传输协议先落地（演示用依赖）
    "dependencies": [{"action_id": "A-02", "after_action_id": "A-01"}],
}


async def fake_llm(messages, *, model, temperature, timeout):
    payload = json.loads(messages[1]["content"])
    data = SYNTH if "risk_counts" in payload else REPLIES[payload["dimension"]["code"]]
    return LlmResult(data=data, prompt_tokens=1, completion_tokens=1, duration_ms=1, model="fake")


async def make_report(session_factory, seed, *, with_consulting=True, edited=False):
    dims = evidence_dims()
    consulting = None
    if with_consulting:
        consulting = await c.build_consulting(
            dimensions=dims, facts=FACTS, scenario=SCENARIO, config=CFG, llm=fake_llm
        )
        assert consulting["status"] == "ok", consulting
        for d in dims:
            cd = next(x for x in consulting["dimensions"] if x["code"] == d["dimension_code"])
            if cd["status"] == "ok":
                d["content_markdown"] = c.render_dimension_markdown(cd)
        if edited:
            consulting["edited"] = {"dimensions": ["D1"], "executive_summary": False}
            dims[0]["content_markdown"] = "USER_EDITED_D1_TEXT 这是用户自己改的内容"
    async with session_factory() as session:
        task = AnalysisTask(
            org_id=seed["org_a"].id,
            created_by=seed["a_member"].id,
            title="咨询版导出测试",
            country_code="XX",
            vehicle_model="synthetic",
            scenario_type="synthetic",
            status="completed",
            facts_json=FACTS,
            findings_json=[f for d in dims for f in d["findings"]],
        )
        session.add(task)
        await session.flush()
        rv = ReportVersion(
            task_id=task.id,
            version_no=1,
            status="full_draft",
            is_partial_sample=False,
            change_note="synthetic",
            executive_summary=c.render_summary_text(consulting) if consulting else "LEGACY_SUMMARY",
            dimensions_json=dims,
            consulting_json=consulting,
        )
        session.add(rv)
        await session.commit()
        return task, rv


def docx_text(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return z.read("word/document.xml").decode()


async def get_docx(client, task, headers, query=""):
    r = await client.get(
        f"/api/analysis/tasks/{task.id}/reports/1/export.docx{query}", headers=headers
    )
    assert r.status_code == 200, r.text
    return docx_text(r.content)


async def user_headers(client):
    return bearer(await login(client, "user", "a-member@example.com"))


async def test_consulting_docx_leads_with_verdict_and_keeps_law_in_references(
    client, seed, session_factory
):
    task, _ = await make_report(session_factory, seed)
    text = await get_docx(client, task, await user_headers(client))

    assert "有条件可行" in text and "可以做，但先把传输协议和同意流程补上" in text
    needles = (
        "先做这几件事",
        "风险排序",
        "第一阶段：30 天内",
        "第二阶段：31–60 天",
        "附录：差距登记表",
        "参考文献",
        "[1]",
    )
    for needle in needles:
        assert needle in text, f"导出里缺少：{needle}"
    # 原文引文不出现在正文和矩阵里；法条只作为参考文献的条目、要点和来源出现
    assert "ORIGINAL_QUOTE_MARKER" not in text
    assert "ZH_QUOTE_MARKER" not in text
    assert "合规分析判定矩阵" not in text
    # 没有依据的方面不在报告里出现：有什么依据就写什么，不列“暂无”“待核查”条目
    assert "暂时还不能下结论" not in text
    assert "没有经过核验的依据" not in text
    assert "维度2" not in text and "维度4" not in text and "维度7" not in text
    assert "传输路径" in text and "同意" in text  # 有依据的方面照常成文
    # 三层顺序：决策页 → 各方面与路线图 → 附录登记表 → 参考文献
    order = [
        text.index(n) for n in ("先做这几件事", "第一阶段：30 天内", "附录：差距登记表", "参考文献")
    ]
    assert order == sorted(order)
    assert "Act a1" in text.split("参考文献", 1)[1]


async def test_findings_are_labeled_lines_and_register_lives_in_appendix(
    client, seed, session_factory
):
    task, _ = await make_report(session_factory, seed)
    text = await get_docx(client, task, await user_headers(client))
    body, appendix = text.split("附录：差距登记表", 1)
    # 正文：每条发现是带标签的几行，不是五列表
    for label in ("差距与原因：", "建议："):
        assert label in body, f"发现里缺少：{label}"
    # 去重：“你们的现状”只在附录登记表里逐条写，正文发现里不再重复
    assert "你们的现状" not in body
    assert "达到情况" not in body and "判断理由" not in body
    # 附录：逐项登记表
    for head in ("法律要求", "你们的现状", "达到情况", "判断理由"):
        assert head in appendix
    assert "目前只有传输加密，没有传输协议" in appendix and "目前只有传输加密" not in body


async def test_roadmap_is_the_last_topic_and_shows_dependencies(client, seed, session_factory):
    task, _ = await make_report(session_factory, seed)
    headers = await user_headers(client)
    text = await get_docx(client, task, headers)
    # 第八个方面（这里标题是“维度8”）本身就是路线图，不再另有“整改路线图”这一节标题
    assert "<w:t xml:space='preserve'>整改路线图</w:t>" not in text
    assert text.index("维度8") < text.index("第一阶段：30 天内") < text.index("第二阶段：31–60 天")
    r = await client.get(f"/api/analysis/tasks/{task.id}", headers=headers)
    road = r.json()["report_versions"][0]["consulting"]["roadmap"]
    assert [a["phase"] for a in road] == [1, 2]
    assert road[1]["depends_on"] == [road[0]["id"]]
    # “先完成”这一列写的是前提事项的短标题
    assert road[0]["title"] in text.split("第二阶段：31–60 天", 1)[1]


async def test_single_dimension_export_renumbers_references(client, seed, session_factory):
    task, _ = await make_report(session_factory, seed)
    text = await get_docx(client, task, await user_headers(client), "?dimension=D3")
    assert "摄像头图像的告知和同意" in text
    assert "回传之前要先补上" not in text  # 其他方面不在文件里
    refs = text.split("参考文献", 1)[1]
    assert "[1]" in refs and "[2]" not in refs and "Act a3" in refs and "Act a1" not in refs
    assert "第一阶段" not in text and "先做这几件事" not in text
    # 单独导出一个方面：附录只登记这个方面
    assert "附录：差距登记表" in text and "目前只有传输加密" not in text


async def test_pdf_and_zip_work_for_consulting_reports(client, seed, session_factory):
    task, _ = await make_report(session_factory, seed)
    headers = await user_headers(client)
    pdf = await client.get(f"/api/analysis/tasks/{task.id}/reports/1/export.pdf", headers=headers)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    bundle = await client.get(
        f"/api/analysis/tasks/{task.id}/reports/1/export.zip", headers=headers
    )
    assert bundle.status_code == 200
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as z:
        # 一份完整报告 + 每个有依据的方面一份（夹具里 D1、D3、D8 有依据，其余方面法规里没有，不出空壳）
        assert len(z.namelist()) == 4
        assert all("ORIGINAL_QUOTE_MARKER" not in docx_text(z.read(n)) for n in z.namelist())


async def test_edited_dimension_shows_user_text(client, seed, session_factory):
    task, _ = await make_report(session_factory, seed, edited=True)
    text = await get_docx(client, task, await user_headers(client))
    assert "USER_EDITED_D1_TEXT" in text
    assert "回传之前要先补上" not in text  # 被用户改写的维度不再显示原生成内容
    assert "摄像头图像的告知和同意" in text  # 没改的维度保持结构化内容


async def test_legacy_report_without_consulting_keeps_original_layout(
    client, seed, session_factory
):
    task, _ = await make_report(session_factory, seed, with_consulting=False)
    text = await get_docx(client, task, await user_headers(client))
    assert "合规分析判定矩阵" in text and "LEGACY_SUMMARY" in text
    assert "参考文献" not in text


async def test_report_has_no_review_notices_internal_codes_and_bolds_key_text(
    client, seed, session_factory
):
    task, _ = await make_report(session_factory, seed)
    headers = await user_headers(client)
    text = await get_docx(client, task, headers)
    with zipfile.ZipFile(
        io.BytesIO(
            (
                await client.get(
                    f"/api/analysis/tasks/{task.id}/reports/1/export.docx", headers=headers
                )
            ).content
        )
    ) as z:
        header = z.read("word/header1.xml").decode()

    # 现阶段不显示“待核对/机翻/复核”类提示（页眉、正文、参考文献都没有）
    for banned in (
        "待核对",
        "机翻",
        "机器生成草稿",
        "不等于法律审批",
        "引用复核概览",
        "引用策略",
        "人工复核",
    ):
        assert banned not in text, f"正文不应出现：{banned}"
        assert banned not in header, f"页眉不应出现：{banned}"
    # 不出现维度/建议内部编号；用名称
    import re

    assert not re.search(r"(?<![A-Za-z0-9])(D[1-8]|A-\d+|F-\d+)(?![A-Za-z0-9])", text)
    assert "传输路径" in text
    # 加粗：Word 里是真正的粗体片段，且没有残留的 ** 标记
    assert "**" not in text
    bold_runs = re.findall(r"<w:b/><w:bCs/>.*?<w:t[^>]*>([^<]*)</w:t>", text)
    assert "现在这一步是空白" in bold_runs


async def test_single_dimension_export_names_the_topic_not_the_code(client, seed, session_factory):
    task, _ = await make_report(session_factory, seed)
    headers = await user_headers(client)
    r = await client.get(
        f"/api/analysis/tasks/{task.id}/reports/1/export.docx?dimension=D3", headers=headers
    )
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        header = z.read("word/header1.xml").decode()
        body = z.read("word/document.xml").decode()
    # 会话编号（CONV-日期-8位十六进制）取自随机任务编号，约 3% 的概率里恰好含“D3”：
    # 去掉编号再判断页眉有没有把维度代码当标题写出来
    import re

    labels = re.sub(r"CONV-\d{8}-[0-9A-F]{8}", "", header)
    assert "同意" in header and "D3" not in labels
    assert "：同意" in body or "同意" in body


async def test_pdf_escapes_markup_but_renders_bold(client, seed, session_factory):
    from app.analysis.pdf_renderer import _rich

    assert _rich("普通**重点**普通") == "普通<b>重点</b>普通"
    assert _rich("<script>x</script> **<img src=x>**") == (
        "&lt;script&gt;x&lt;/script&gt; <b>&lt;img src=x&gt;</b>"
    )
    assert _rich("没配对 ** 的标记") == "没配对  的标记"


async def test_saving_unchanged_text_does_not_mark_dimensions_as_user_edited(
    client, seed, session_factory
):
    from app.analysis.rich_text import strip_bold

    task, rv = await make_report(session_factory, seed)
    headers = await user_headers(client)
    plain = {d["dimension_code"]: strip_bold(d["content_markdown"]) for d in rv.dimensions_json}
    # 页面把去掉加粗标记的原文原样带回来：不算修订，加粗保留
    r = await client.post(
        f"/api/analysis/tasks/{task.id}/reports/edit",
        headers=headers,
        json={
            "change_note": "x",
            "executive_summary": strip_bold(rv.executive_summary),
            "dimension_updates": plain,
        },
    )
    assert r.status_code == 200, r.text
    edited = r.json()["consulting"].get("edited") or {"dimensions": [], "executive_summary": False}
    assert edited["dimensions"] == [] and edited["executive_summary"] is False
    assert any("**" in d["content_markdown"] for d in r.json()["dimensions"])
    # 真改了一章：只有这一章被登记
    plain["D1"] = "用户自己改写的内容"
    r = await client.post(
        f"/api/analysis/tasks/{task.id}/reports/edit",
        headers=headers,
        json={"change_note": "y", "dimension_updates": plain},
    )
    assert r.json()["consulting"]["edited"]["dimensions"] == ["D1"]


async def test_api_exposes_consulting_without_internal_diagnostics(client, seed, session_factory):
    task, _ = await make_report(session_factory, seed)
    r = await client.get(f"/api/analysis/tasks/{task.id}", headers=await user_headers(client))
    assert r.status_code == 200, r.text
    report = r.json()["report_versions"][0]
    consulting = report["consulting"]
    assert consulting["verdict"]["label"] == "有条件可行"
    assert consulting["references"] and consulting["roadmap"]
    for hidden in ("model", "tokens", "prompt_version", "validation"):
        assert hidden not in consulting


def test_reference_point_is_a_short_entry_not_a_restated_clause():
    from app.analysis.consulting_export import POINT_LIMIT, reference_point

    assert reference_point([]) == ""
    assert (
        reference_point(["组织须确保接收方受约束。"]) == "组织须确保接收方受约束。"
    )  # 短的原样保留
    # 句子边界在后半段：按边界截断，不留半句
    sentence = "组织向新加坡境外传输个人数据前，必须先确认接收方受法律约束并提供相当的保护标准，"
    long = sentence + "并且" * 80
    out = reference_point([long, "第二条不应出现"])
    assert out == sentence and len(out) <= POINT_LIMIT and "第二条" not in out
    # 第一个完整短句不到 12 字：不在这么短的地方硬切，整段截断后加省略号
    early = "不得传输，" + "并且" * 80
    assert reference_point([early]).endswith("…")
    # 句号优先于逗号：前半句有句号时在句号处截断
    mixed = (
        "组织须确保接收方受约束并提供相当保护。接收方应当在合同里写明可传输的国家，" + "并且" * 60
    )
    assert reference_point([mixed]) == "组织须确保接收方受约束并提供相当保护。"
    assert (
        reference_point(["（a）任何法律；\n（b）依照 paragraph (2) 的合同；" * 10]).count("\n") == 0
    )
    assert reference_point(["没有标点" * 40]).endswith("…")  # 找不到句子边界时才加省略号


async def test_exported_references_are_entries_not_clause_text(client, seed, session_factory):
    task, _ = await make_report(session_factory, seed)
    text = await get_docx(client, task, await user_headers(client))
    refs = text.split("参考文献", 1)[1]
    # 条目里没有被搬进来的整条义务；而页面上的完整原文不受影响
    assert refs.count("要点：") >= 1
    assert len(refs) < len(text) * 0.6


async def test_reference_entries_use_the_consulting_one_liner_not_the_clause_restatement(
    client, seed, session_factory
):
    task, rv = await make_report(session_factory, seed)
    refs = rv.consulting_json["references"]
    # 每条参考文献都有咨询层写的一句话；它来自对应发现的“要求”，不是证据里整条义务的转述
    assert refs and all(r["summary"] for r in refs)
    requirement_texts = {
        a["requirement"].replace("**", "")
        for d in rv.consulting_json["dimensions"]
        for a in d["assessments"]
    }
    assert all(r["summary"] in requirement_texts for r in refs)
    text = await get_docx(client, task, await user_headers(client))
    refs_part = text.split("参考文献", 1)[1]
    assert "回传前确认接收方受法律约束" in refs_part  # 用的是咨询层的一句话
    # 单章导出：参考文献也只带本章的，一句话说明来自本章的发现
    one = await get_docx(client, task, await user_headers(client), "?dimension=D3")
    assert "收集前告知目的并取得同意" in one.split("参考文献", 1)[1]


def test_reference_summary_falls_back_to_clause_text_for_old_reports():
    from app.analysis.consulting_export import consulting_blocks

    consulting = {
        "verdict": {
            "label": "有条件可行",
            "headline": "x",
            "summary": "y",
            "must_do_action_ids": [],
        },
        "dimensions": [],
        "top_risks": [],
        "roadmap": [],
        "references": [
            {
                "n": 1,
                "document_title": "Act",
                "clause_number": "26",
                "version_no": 1,
                "source_url": "",
                "points": ["旧报告只有条款义务的转述，没有一句话说明。"],
            }
        ],
    }
    from types import SimpleNamespace

    blocks = consulting_blocks(
        country="X",
        task=SimpleNamespace(title="t", vehicle_model="v"),
        rv=SimpleNamespace(version_no=1, dimensions_json=[], executive_summary=""),
        consulting=consulting,
        dimension=None,
        notices=[],
    )
    ref = next(b for b in blocks if b["t"] == "ref")
    assert any("旧报告只有条款义务的转述" in s for s in ref["sub"])


async def test_when_no_dimension_has_evidence_one_sentence_explains_instead_of_an_empty_report(
    client, seed, session_factory
):
    task, _ = await make_report(session_factory, seed)
    async with session_factory() as session:
        rv = (
            await session.execute(select(ReportVersion).where(ReportVersion.task_id == task.id))
        ).scalar_one()
        consulting = dict(rv.consulting_json)
        consulting["dimensions"] = [
            {
                **d,
                "status": "no_evidence",
                "note": "已归档法源中未检索到相关条款",
                "conclusion": "",
                "overview": "",
                "assessments": [],
                "actions": [],
            }
            for d in consulting["dimensions"]
        ]
        consulting["top_risks"] = []
        consulting["roadmap"] = []
        rv.consulting_json = consulting
        await session.commit()
    text = await get_docx(client, task, await user_headers(client))
    assert "目前法规库里还没有与这个场景直接对应的依据" in text
    assert "暂时还不能下结论" not in text
    assert "已归档法源中未检索到相关条款" not in text


async def test_separate_reports_exist_only_for_dimensions_the_laws_support(
    client, seed, session_factory
):
    task, _ = await make_report(session_factory, seed)
    async with session_factory() as session:
        rv = (
            await session.execute(select(ReportVersion).where(ReportVersion.task_id == task.id))
        ).scalar_one()
        # 落地方面本身就是整改路线图，始终单独成文；其余方面看有没有依据
        supported = {
            d["code"]
            for d in rv.consulting_json["dimensions"]
            if d["status"] != "no_evidence" or d["code"] == "D8"
        }
    headers = await user_headers(client)
    bundle = await client.get(
        f"/api/analysis/tasks/{task.id}/reports/1/export.zip", headers=headers
    )
    assert bundle.status_code == 200
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as z:
        names = z.namelist()
    assert len(names) == 1 + len(supported)
    unsupported = [f"D{i}" for i in range(1, 9) if f"D{i}" not in supported]
    assert unsupported, "夹具里应有没依据的方面"
    gone = await client.get(
        f"/api/analysis/tasks/{task.id}/reports/1/export.docx?dimension={unsupported[0]}",
        headers=headers,
    )
    assert gone.status_code == 404 and "没有这个方面的依据" in gone.text
    kept = await client.get(
        f"/api/analysis/tasks/{task.id}/reports/1/export.docx?dimension={sorted(supported)[0]}",
        headers=headers,
    )
    assert kept.status_code == 200


async def test_an_older_report_whose_roadmap_lacks_title_and_phase_still_exports_and_loads(
    client, seed, session_factory
):
    task, _ = await make_report(session_factory, seed)
    async with session_factory() as session:
        rv = (
            await session.execute(select(ReportVersion).where(ReportVersion.task_id == task.id))
        ).scalar_one()
        consulting = dict(rv.consulting_json)
        consulting["roadmap"] = [
            {k: v for k, v in x.items() if k not in ("title", "phase", "depends_on")}
            for x in consulting["roadmap"]
        ]
        assert consulting["roadmap"], "夹具里应有路线图"
        rv.consulting_json = consulting
        await session.commit()
    headers = await user_headers(client)
    for ext in ("docx", "pdf", "zip"):
        r = await client.get(
            f"/api/analysis/tasks/{task.id}/reports/1/export.{ext}", headers=headers
        )
        assert r.status_code == 200, (ext, r.text[:200])
    detail = (await client.get(f"/api/analysis/tasks/{task.id}", headers=headers)).json()
    roadmap = detail["report_versions"][-1]["consulting"]["roadmap"]
    assert all(x["title"] and x["phase"] in (1, 2, 3) and x["depends_on"] == [] for x in roadmap)


async def test_a_dimension_whose_opinion_failed_to_generate_gets_no_separate_report(
    client, seed, session_factory
):
    task, _ = await make_report(session_factory, seed)
    async with session_factory() as session:
        rv = (
            await session.execute(select(ReportVersion).where(ReportVersion.task_id == task.id))
        ).scalar_one()
        consulting = dict(rv.consulting_json)
        consulting["dimensions"] = [
            {**d, "status": "failed"} if d["code"] == "D1" else d for d in consulting["dimensions"]
        ]
        rv.consulting_json = consulting
        await session.commit()
    r = await client.get(
        f"/api/analysis/tasks/{task.id}/reports/1/export.docx?dimension=D1",
        headers=await user_headers(client),
    )
    assert r.status_code == 404
