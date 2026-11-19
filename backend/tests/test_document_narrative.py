"""论述部分（DPIA、LIA、TIA）：引用由程序打印、编造的数字被拒、数据流图与风险等级由程序生成。"""

import pytest

from app.analysis import document_narrative as nar
from app.analysis.document_narrative import NarrativeError, Section, write_section
from app.analysis.llm import LlmResult

CITATIONS = [
    {"clause_id": "c1", "document_title": "某数据保护法", "clause_number": "26", "quote_zh": "传输须保障同等保护，保存期限不超过 5 年。"},
    {"clause_id": "c2", "document_title": "某网络安全法", "clause_number": "9", "quote_zh": "应采取加密措施。"},
]


REFS: dict = {}


@pytest.fixture(autouse=True)
def _fresh_refs():
    REFS.clear()


def _model(data):
    async def fake(messages, **_kw):
        return LlmResult(data=data, prompt_tokens=10, completion_tokens=5, duration_ms=1, model="fake")

    return fake


async def _write(monkeypatch, data, **section):
    monkeypatch.setattr(nar, "call_llm_json", _model(data))
    sec = Section(key="k", heading="一、测试", brief="论证", facts=["a"], **section)
    return await write_section(sec, {"a": "事实甲"}, {"a": "甲"}, CITATIONS, {}, REFS)


async def test_the_citation_line_is_printed_by_the_program_from_the_clause_numbers(monkeypatch):
    blocks, _, dropped = await _write(monkeypatch, {"paragraphs": [{"text": "须保障同等保护。", "cites": [1, 2]}]})
    text = blocks[1]["text"]
    assert text == "须保障同等保护。[1][2]"  # 引用只是编号，法规名称与条款在文末的表里，由程序按条文本身填写
    assert [(k, v[0]) for k, v in REFS.items()] == [(("某数据保护法", "26"), 1), (("某网络安全法", "9"), 2)]
    assert not dropped


async def test_internal_numbering_or_field_names_in_the_text_are_refused(monkeypatch):
    blocks, _, dropped = await _write(
        monkeypatch,
        {"paragraphs": [{"text": "依据条文6，应当加密。", "cites": [1]}, {"text": "facts 未提供。", "cites": []}, {"text": "正文正常。", "cites": []}]},
    )
    assert [b["text"] for b in blocks if b["type"] == "paragraph"] == ["正文正常。"]
    assert len([d for d in dropped if "内部编号" in d]) == 2


async def test_a_statement_citing_a_clause_that_was_not_offered_is_dropped(monkeypatch):
    blocks, _, dropped = await _write(
        monkeypatch,
        {"paragraphs": [{"text": "某法另有规定。", "cites": [7]}, {"text": "有依据的一段。", "cites": [1]}]},
    )
    assert [b["text"][:6] for b in blocks if b["type"] == "paragraph"] == ["有依据的一段"]
    assert any("没有提供给它的条文" in d for d in dropped)


async def test_a_figure_that_is_in_neither_the_facts_nor_a_cited_clause_is_refused(monkeypatch):
    # “5 年”在条文里，可以引用；“99 万人”哪里都没有，整段被拒
    blocks, _, dropped = await _write(
        monkeypatch,
        {"paragraphs": [{"text": "保存不超过 5 年。", "cites": [1]}, {"text": "涉及 99 万人。", "cites": [1]}]},
    )
    texts = [b["text"] for b in blocks if b["type"] == "paragraph"]
    assert len(texts) == 1 and "5 年" in texts[0]
    assert any("99" in d for d in dropped)


async def test_nothing_that_passes_the_checks_is_an_error_not_an_empty_section(monkeypatch):
    with pytest.raises(NarrativeError):
        await _write(monkeypatch, {"paragraphs": [{"text": "涉及 99 万人。", "cites": []}]})


async def test_the_flow_table_and_the_risk_levels_are_made_by_the_program(monkeypatch):
    import json

    data = {
        "paragraphs": [{"text": "按流程叙述。", "cites": []}],
        "flow": [{"stage": "采集", "data": "视频", "where_who": "车端", "safeguard": "脱敏"}, {"stage": "存储", "data": "视频", "where_who": "境内", "safeguard": "加密"}],
        # 模型就算给了等级也不用：风险表只来自企业逐行填的风险
        "risks": [{"risk": "模型自己想的", "likelihood": "高", "severity": "高", "measure": "", "cites": []}],
    }
    rows = [
        {"id": "a", "risk": "被识别", "likelihood": "中", "severity": "高", "measures": "脱敏"},
        {"id": "b", "risk": "越权访问", "likelihood": "低", "severity": "低", "measures": "分级授权"},
    ]
    monkeypatch.setattr(nar, "call_llm_json", _model(data))
    sec = Section(key="k", heading="一、测试", brief="论证", facts=["risk_rating"], flow=True, risks=True)
    blocks, _, _ = await write_section(sec, {"risk_rating": json.dumps(rows, ensure_ascii=False)}, {"risk_rating": "风险"}, CITATIONS, {}, REFS)
    arrows = [b["text"] for b in blocks if b["type"] == "paragraph" and "→" in b["text"]]
    assert arrows == ["采集 → 存储"]
    tables = [b for b in blocks if b["type"] == "table"]
    flow, register, grid = tables[0], tables[1], tables[2]
    assert flow["head"][0] == "环节" and len(flow["rows"]) == 2
    assert {r[1]: r[4] for r in register["rows"]} == {"被识别": "高风险", "越权访问": "低风险"}  # 等级按矩阵算
    assert "模型自己想的" not in json.dumps(blocks, ensure_ascii=False)
    assert grid["head"][0] == "可能性＼严重程度" and "风险 1" in grid["rows"][1][3]  # 可能性中 × 严重程度高


def test_the_same_facts_and_clauses_give_the_same_hash_and_a_changed_fact_a_new_one():
    d = {"title": "T", "narrative": [{"key": "a"}]}
    h1 = nar.inputs_hash(d, {"x": "1"}, CITATIONS)
    assert h1 == nar.inputs_hash(d, {"x": "1"}, CITATIONS)
    assert h1 != nar.inputs_hash(d, {"x": "2"}, CITATIONS)
    assert h1 != nar.inputs_hash(d, {"x": "1"}, CITATIONS[:1])


def test_a_figure_in_another_notation_is_the_same_figure_and_a_different_figure_is_not():
    clause = nar._numbers("罚金 20.000 至 200.000 DH，期限 3 个月，第 09-08 号法")
    assert nar._numbers("罚金2万至20万迪拉姆") <= clause | {"2", "20", "3"}
    assert "20000" in nar._numbers("2万") and "200000" in nar._numbers("20万") and "20000" in nar._numbers("20,000")
    assert "990000" in nar._numbers("99万人") and "990000" not in clause
    assert nar._numbers("1.5 倍") == {"1.5"}


def test_digits_split_by_a_line_break_in_a_clause_never_break_the_checker():
    # 条文里数字被换行拆开（“0\n750.000”）：各自当作独立的数，不报错
    assert nar._numbers("罚款 0\n750.000 里拉") == {"0", "750000"}
    assert nar._numbers("20 000 DH") == {"20000"}
