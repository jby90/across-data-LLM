"""第 7 条：主要风险按行填写（风险、可能性、严重程度、应对措施、责任人、完成时限）——
风险等级按矩阵算；风险评估报告和 DPIA 出风险清单与矩阵；缺档位或措施算缺项；以前写的整段文字保留成一行、不替用户定档；
其他用到这题的地方照常出字；措施一键生成整改项，只追加不覆盖、标出来源、不重复生成。"""

import io
import json
import zipfile

from app.analysis import official_docx as od
from app.analysis import risk_items
from app.analysis.document_templates import DRAFT_BLANK, render
from app.analysis.document_templates_default import DEFAULT_TEMPLATES

from .conftest import bearer, login
from .test_doc_projects import BASE, _make

ROWS = [
    {"id": "a", "risk": "接收方再转移", "likelihood": "低", "severity": "高", "measures": "合同禁止再转移", "owner": "法务部", "due": "2027-03-31"},
    {"id": "b", "risk": "境外存储泄露", "likelihood": "中", "severity": "高", "measures": "加密存储"},
    {"id": "c", "risk": "越权访问", "likelihood": "低", "severity": "低", "measures": "分级授权"},
]
FULL = json.dumps(ROWS, ensure_ascii=False)
DEF = {code: d for code, _, d in DEFAULT_TEMPLATES}


def test_levels_follow_the_matrix_of_the_grading_table():
    got = {(r["likelihood"], r["severity"]): risk_items.level(r) for r in (
        {"likelihood": a, "severity": b} for a in risk_items.LEVELS for b in risk_items.LEVELS)}
    assert got[("低", "低")] == "低风险" and got[("低", "高")] == "中风险" and got[("中", "中")] == "中风险"
    assert got[("中", "高")] == "高风险" and got[("高", "低")] == "中风险" and got[("高", "高")] == "高风险"
    assert risk_items.level({"likelihood": "", "severity": "高"}) == ""


def test_an_earlier_paragraph_is_kept_whole_as_one_row_and_nothing_is_guessed():
    old = "接收方再转移数据：可能性低，严重程度高"
    rows = risk_items.parse(old)
    assert len(rows) == 1 and rows[0]["risk"] == old and not rows[0]["likelihood"] and not rows[0]["severity"] and not rows[0]["measures"]
    assert risk_items.lacking(old) == ["风险 1：可能性、严重程度、应对措施"]
    assert risk_items.parse("暂不确定（待补充）") == [] and not risk_items.lacking("暂不确定（待补充）")


def test_every_risk_needs_its_grades_and_at_least_one_measure():
    rows = [dict(r) for r in ROWS]
    rows[1]["measures"] = ""
    rows[2]["severity"] = "很高"  # 不是低/中/高：当作没选
    assert risk_items.lacking(json.dumps(rows, ensure_ascii=False)) == ["风险 2：应对措施", "风险 3：严重程度"]
    assert risk_items.lacking(FULL) == []


def test_other_uses_read_plain_text_one_risk_per_line():
    text = risk_items.text(FULL)
    lines = text.split("\n")
    assert len(lines) == 3 and "{" not in text and "[" not in text
    assert lines[0] == "风险 1：接收方再转移。可能性低，严重程度高，风险等级为中风险。应对措施：合同禁止再转移（责任人：法务部；完成时限：2027-03-31）。"
    assert risk_items.shown("purpose", FULL) == FULL  # 别的题原样


def test_the_risk_report_shows_the_register_and_the_matrix_and_an_incomplete_risk_is_missing():
    d = DEF["risk_report"]
    out = render(d, {"risk_rating": FULL}, [], draft=True)
    tables = [b for b in out.blocks if b["type"] == "table"]
    register = next(t for t in tables if t["head"][0] == "编号")
    grid = next(t for t in tables if t["head"][0] == "可能性＼严重程度")
    assert [r[4] for r in register["rows"]] == ["中风险", "高风险", "低风险"]
    assert [r[0] for r in grid["rows"]] == ["可能性高", "可能性中", "可能性低"]
    assert grid["rows"][1][3] == "高风险：风险 2" and grid["rows"][2][3] == "中风险：风险 1" and grid["rows"][2][1] == "低风险：风险 3"
    assert "risk_rating" not in {q.key for q in out.missing}
    half = render(d, {"risk_rating": "接收方再转移数据"}, [], draft=True)
    assert "risk_rating" in {q.key for q in half.missing}  # 整段文字：还没拆分、没定档，算缺项
    assert any(DRAFT_BLANK in str(b) for b in half.blocks)


def test_the_dpia_matrix_comes_with_its_narrative_and_the_question_is_shared():
    assert any(p.get("risks") and "risk_rating" in p["facts"] for p in DEF["dpia"]["narrative"])
    labels = {DEF[c]["facts"][[f["key"] for f in DEF[c]["facts"]].index("risk_rating")]["label"] for c in ("risk_report", "dpia")}
    assert len(labels) == 1  # 两份文件共用一题，题名一致


def _xml(data: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8")


def test_the_official_templates_hold_the_tables_under_their_own_risk_heading_and_keep_the_data_list():
    for fill, prompt in ((od.cn_self_assessment, "对照《数据出境安全评估办法》第五条规定事项"),
                         (od.cn_pia, "对照《个人信息出境标准合同办法》第五条规定事项")):
        r = fill({"risk_rating": FULL, "risk_findings": "发现 1 项已整改", "data_types": "位置、视频"}, blank=DRAFT_BLANK)
        xml = _xml(r.data)
        assert prompt in r.filled
        at = xml.index(prompt)
        assert xml.index("发现 1 项已整改", at) < xml.index("主要风险评估与应对清单", at) < xml.index("风险等级判定与分布表", at)
        assert "接收方再转移" in xml and "高风险：风险 2" in xml and "[{" not in xml
    xml = _xml(od.cn_self_assessment({"risk_rating": FULL, "data_types": "位置、视频"}).data)
    first_table = xml[xml.index("<w:tbl>"):]
    assert "位置" in first_table[: first_table.index("</w:tbl>")]  # 模板自带的数据清单表仍填在原表里


async def test_measures_become_remediation_items_added_after_what_is_there(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, source="中国", recipient="埃及", scenario_text="车辆运行数据回传海外研发中心")
    pid = p["id"]
    half = [dict(r) for r in ROWS]
    half[2]["measures"] = ""
    v = (await client.put(f"{BASE}/{pid}/answers", headers=auth, json={"answers": {
        "risk_rating": json.dumps(half, ensure_ascii=False), "issues": "隐私政策没写接收方", "measures": "无"}})).json()
    q = next(x for x in v["questions"] if x["key"] == "risk_rating")
    assert q["kind"] == "risks" and q["lacking"] == ["风险 3：应对措施"] and q["remediate"]["pending"] == 2
    assert any(m["key"] == "risk_rating" for d in v["documents"] if d["code"] in ("risk_report", "dpia") for m in d["missing"])
    r = await client.post(f"{BASE}/{pid}/risks/remediate", headers=auth)
    assert r.status_code == 200, r.text
    v = r.json()
    qs = {x["key"]: x for x in v["questions"]}
    issues, measures = qs["issues"]["value"].split("\n"), qs["measures"]["value"].split("\n")
    assert issues[0] == "隐私政策没写接收方" and len(issues) == 3  # 已有的保留，新条目在后面
    assert issues[1].startswith("风险 1：接收方再转移（中风险")
    assert measures[0].startswith("针对风险 1：合同禁止再转移（责任人：法务部；完成时限：2027-03-31）") and len(measures) == 2  # “无”只是占位，被替换
    assert qs["issues"]["carried"]["text"] == "其中 2 条已从主要风险带入" and qs["issues"]["carried"]["kind"] == "risk"
    assert qs["risk_rating"]["remediate"] == {"pending": 0, "done": 2}
    assert (await client.post(f"{BASE}/{pid}/risks/remediate", headers=auth)).status_code == 409  # 不重复生成
    # 补上第 3 项的措施再生成：只加第 3 项，标注累计
    half[2]["measures"] = "分级授权"
    await client.put(f"{BASE}/{pid}/answers", headers=auth, json={"answers": {"risk_rating": json.dumps(half, ensure_ascii=False)}})
    v = (await client.post(f"{BASE}/{pid}/risks/remediate", headers=auth)).json()
    qs = {x["key"]: x for x in v["questions"]}
    assert len(qs["issues"]["value"].split("\n")) == 4 and qs["issues"]["carried"]["text"] == "其中 3 条已从主要风险带入"
    # 用户改了整改问题：不再标“带入”
    v = (await client.put(f"{BASE}/{pid}/answers", headers=auth, json={"answers": {"issues": "全部重写"}})).json()
    assert next(x for x in v["questions"] if x["key"] == "issues")["carried"] is None
    # 风险评估报告初稿里有风险评估清单与等级判定分布表；别的文件（合同）用到的保护措施照常出字
    doc = await client.get(f"/api/analysis/tasks/{pid}/documents/risk_report/export.docx?draft=true", headers=auth)
    assert doc.status_code == 200 and "风险等级判定与分布表" in _xml(doc.content) and "分级授权" in _xml(doc.content)


def test_asking_about_this_projects_risks_goes_to_the_project_not_the_library():
    from app.analysis.chat_advisor import _about_project

    assert _about_project("这个项目的主要风险有哪些，各是什么等级？", ["ask_law"])
    assert _about_project("风险矩阵里哪个是高风险", ["ask_law"])
    assert not _about_project("埃及法规对数据出境风险等级有什么要求？", ["ask_law"])  # 问法规的仍然查法规库


def test_without_the_model_the_assistant_answers_risks_from_the_table():
    from app.analysis.project_guide import fallback

    view = {"questions": [{"key": "risk_rating", "kind": "risks", "value": FULL, "lacking": []}], "documents": [], "applicability": {}}
    got = fallback(view, "主要风险有哪些？")
    assert "风险 2：境外存储泄露" in got and "高风险" in got and "[{" not in got


def test_risk_evolution_tracks_before_and_after_and_excludes_controllable_risks_from_formal_report():
    initial = risk_items.merge_history("", json.dumps([
        {"id": "r1", "risk": "车端上传原始车架号与高精度轨迹", "likelihood": "高", "severity": "高", "measures": "待部署车端脱敏"},
        {"id": "r2", "risk": "跨境传输链路遭中间人截获", "likelihood": "中", "severity": "高", "measures": "待启用双向证书"},
        {"id": "r3", "risk": "本地临时诊断缓存超期滞留", "likelihood": "中", "severity": "中", "measures": "待设自动清理"},
    ], ensure_ascii=False))
    # 车企完成整改后：r1 降为低风险，r2 降为低风险，r3 作为已可控风险勾选剔除
    after = risk_items.merge_history(initial, json.dumps([
        {"id": "r1", "risk": "车端上传原始车架号与高精度轨迹", "likelihood": "低", "severity": "中", "measures": "车端边缘动态脱敏与网格泛化", "owner": "车联网安全组", "due": "2026-10-30"},
        {"id": "r2", "risk": "跨境传输链路遭中间人截获", "likelihood": "低", "severity": "中", "measures": "启用 TLS 1.3 双向证书与国密网关", "owner": "云基础设施组", "due": "2026-10-30"},
        {"id": "r3", "risk": "本地临时诊断缓存超期滞留", "likelihood": "低", "severity": "低", "measures": "已上线 24 小时自动覆写任务并验收闭环", "mitigated": "1"},
    ], ensure_ascii=False))
    s = risk_items.summary(after)
    assert s["init_counts"] == {"低风险": 0, "中风险": 1, "高风险": 2}
    assert s["current_counts"] == {"低风险": 2, "中风险": 0, "高风险": 0}
    assert s["downgraded_count"] == 2 and s["excluded_count"] == 1 and s["has_change"] is True

    # 正式报告口径（text / blocks）已剔除 r3，并标注 r1、r2 由高风险降为低风险
    formal_txt = risk_items.text(after)
    assert "本地临时诊断缓存超期滞留" not in formal_txt
    assert "初评高风险，整改后降为低风险" in formal_txt
    formal_blocks = risk_items.blocks(after)
    reg = next(b for b in formal_blocks if b["type"] == "table" and b["head"][0] == "编号")
    assert len(reg["rows"]) == 2 and [row[4] for row in reg["rows"]] == ["低风险", "低风险"]

    # 内部整改跟踪台账（evolution_blocks）完整保留全部 3 项前后变化
    evo_blocks = risk_items.evolution_blocks(after)
    evo_tbl = next(b for b in evo_blocks if b["type"] == "table")
    assert len(evo_tbl["rows"]) == 3
    assert "由高风险降级" in evo_tbl["rows"][0][4]
    assert "已可控闭环" in evo_tbl["rows"][2][4]


def test_official_self_assessment_cover_date_fonts_and_declaration_structured_persons():
    sa = od.cn_self_assessment({
        "company_name": "启衡智行汽车科技（上海）有限公司",
        "self_assessed_on": "2026-11-10",
        "purpose": "履行与中国境内购车车主签订的远程运维服务协议",
        "risk_rating": FULL,
    })
    xml = _xml(sa.data)
    assert "数据处理者名称：启衡智行汽车科技（上海）有限公司（盖章）" in xml
    assert "2026 年 11 月 10 日" in xml
    assert "仿宋_GB2312" not in xml
    assert "必要性；</w:t>" not in xml
    assert "中国境内购车车主。</w:t>" in xml or "中国境内购车车主签订的远程运维服务协议。" in xml

    decl = od.cn_declaration({
        "company_name": "启衡智行汽车科技（上海）有限公司",
        "entity_nature": "企业",
        "legal_rep": "周明远；董事长兼总经理；中国；居民身份证；31011519780516281X；021-68905501；zhou.my@qiheng-auto.cn",
        "security_org": "数据安全与跨境合规委员会；16人",
        "recipient": "Qiheng Pte. Ltd.；新加坡纬壹科技城1号；汽车远程诊断服务",
        "recipient_contact": "Marcus Tan；亚太区合规总监；+65-6812-9088 dpo@qiheng.sg",
    })
    dxml = _xml(decl.data)
    assert "31011519780516281X" in dxml
    assert "zhou.my@qiheng-auto.cn" in dxml
    assert "16人" in dxml
    assert "新加坡纬壹科技城1号" in dxml
    assert "Marcus Tan" in dxml


