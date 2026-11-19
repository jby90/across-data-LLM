"""第 5 条：题目旁的依据卡片（填写说明、所依据的法条、示例）；第 6 条：带入的值标出来源。"""

import re

from app.analysis import doc_assist
from app.analysis import doc_field_guide as G
from app.analysis import question_basis as QB
from app.analysis.document_templates import DOC_FACT_PREFIX, resolve_facts
from app.analysis.document_templates_default import DEFAULT_TEMPLATES

from .conftest import bearer, login
from .test_doc_projects import BASE, _make

PROJECT_KEYS = {"source_country", "recipient_country", "vehicle_model", "scenario", "company_name", "country_name"}
QUESTION_KEYS = {f["key"] for _, _, d in DEFAULT_TEMPLATES for f in d.get("facts", [])} - PROJECT_KEYS
CN_NUMBER = re.compile(r"^[一二三四五六七八九十]+$")


def test_every_question_has_a_note_and_an_example_and_the_note_carries_no_example():
    assert QUESTION_KEYS <= set(G.HINTS) and QUESTION_KEYS <= set(G.EXAMPLES)
    for key, hint in G.HINTS.items():
        assert not re.search(r"如“|例如|比如", hint), key  # 例子只放在示例栏，说明里不再重复
        assert G.EXAMPLES[key] not in hint


def test_the_rules_name_only_real_questions_and_the_four_chinese_provisions():
    assert set(QB.CN) <= QUESTION_KEYS and set(QB.ASPECTS) <= QUESTION_KEYS and set(QB.ADMISSION_KEYS) <= QUESTION_KEYS
    for key, items in QB.CN.items():
        for law, number, point in items:
            assert law in (QB.SA, QB.SC, QB.CP, QB.AUTO) and CN_NUMBER.match(number) and point, key
    assert all(kind in QB.KINDS for rules in QB.ASPECTS.values() for kind, _ in rules)
    assert not re.search(QB.ASPECTS["data_volume"][0][1], "跨境传输个人数据须获明确授权")  # “个人数据”里的“人数”不算人数


def test_a_chinese_article_the_library_does_not_hold_is_not_shown():
    held = {(QB.SA, "九"): {"document_id": "d", "clause_id": "c9"}}
    cards = QB._cn_card("retention_and_blackbox", held)
    assert cards == [{"law": "《数据出境安全评估办法》", "article": "第九条", "point": QB.CN["retention_and_blackbox"][0][2], "country": "CN", "document_id": "d", "clause_id": "c9"}]
    assert QB._cn_card("purpose", held) == []  # 第五条、第四条都不在库里：不凭记忆写
    assert QB.link(cards[0]) == "/knowledge?country=CN&doc=d&clause=c9"


def test_a_fact_that_borrows_a_default_keeps_what_the_user_wrote():
    definition = {"facts": [{"key": "scenario_summary", "from": "scenario"}, {"key": "vehicle_model"}]}
    base = {"scenario": "车联网上云（售后）", "vehicle_model": "X7"}
    assert resolve_facts(definition, base, {})["scenario_summary"] == "车联网上云（售后）"
    got = resolve_facts(definition, base, {DOC_FACT_PREFIX + "scenario_summary": "故障码经专线回传上海", DOC_FACT_PREFIX + "vehicle_model": "别的"})
    assert got["scenario_summary"] == "故障码经专线回传上海" and got["vehicle_model"] == "X7"


def test_the_assistant_may_cite_only_the_cards_articles():
    basis = ["《数据出境安全评估办法》第九条：法律文件须约定保存地点"]
    assert doc_assist._cites_ok("依据《数据出境安全评估办法》第九条，要写明保存地点。", basis)
    assert not doc_assist._cites_ok("依据《个人信息保护法》第三十八条。", basis)
    assert not doc_assist._cites_ok("见第十条。", basis)
    assert doc_assist._cites_ok("这一项问的是保存多久。", [])


async def test_each_question_has_its_card_and_an_article_missing_from_the_library_reads_none(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    p = await _make(client, auth, source="中国", recipient="埃及", function="ota_diagnostics")
    for q in p["questions"]:
        assert q["hint"] and q["example"] and q["used_for"] and isinstance(q["basis"], list)
    assert all(not q["basis"] for q in p["questions"])  # 测试库里没有法规：一律“暂无对应条文”，不编条号


async def test_carried_values_say_where_they_came_from_until_the_user_changes_them(client, seed):
    auth = bearer(await login(client, "user", "a-member@example.com"))
    first = await _make(client, auth, source="中国", recipient="新加坡", function="ota_diagnostics", company_name="某车企")
    await client.put(f"{BASE}/{first['id']}/answers", headers=auth, json={"answers": {"credit_code": "91330000TEST"}})
    second = await _make(client, auth, source="中国", recipient="埃及", function="ota_diagnostics")
    qs = {q["key"]: q for q in second["questions"]}
    assert qs["credit_code"]["carried"] == {"kind": "project", "text": f"已从项目 {first['title']} 带入", "project_id": first["id"]}
    assert second["route"]["company_name_carried"]["project_id"] == first["id"]
    assert qs["purpose"]["carried"] is None  # 8 份文件共用的题不算带入
    assert qs["scenario_summary"]["carried"]["kind"] == "route" and "业务场景" in qs["scenario_summary"]["carried"]["text"]
    after = (await client.put(f"{BASE}/{second['id']}/answers", headers=auth, json={"answers": {"credit_code": "91330000NEW", "scenario_summary": "故障码经专线回传上海"}})).json()
    qs = {q["key"]: q for q in after["questions"]}
    assert qs["credit_code"]["carried"] is None and qs["scenario_summary"]["carried"] is None
    assert qs["scenario_summary"]["value"] == "故障码经专线回传上海"  # 自己写的不再被业务场景盖掉
