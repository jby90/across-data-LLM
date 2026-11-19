"""Country identity and previously supplied facts survive untrusted slot extraction."""

import json
import types

import pytest

from app.analysis import chat, chat_intent, chat_reply
from app.analysis.llm import LlmResult
from app.analysis.workflow_engine import COUNTRY_NAMES_ZH, REQUIRED_FACTS_SPEC

from .conftest import bearer, login


async def send(client, auth, tid, content):
    result = await client.post(
        f"/api/analysis/tasks/{tid}/messages", headers=auth, json={"content": content}
    )
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.parametrize("country", COUNTRY_NAMES_ZH)
async def test_later_country_mention_cannot_overwrite_confirmed_identity(
    client, seed, monkeypatch, country
):
    other = next(code for code in COUNTRY_NAMES_ZH if code != country)
    other_name = COUNTRY_NAMES_ZH[other]
    current = "consent_and_notice"
    original_fact = "采集视频并对人脸车牌模糊处理"
    answer = (
        f"已任命{other_name}数据保护联系人，并公示邮箱，"
        "车机激活时提供隐私告知和明示同意，车型修改为其他车型，开展远程诊断业务。"
    )
    payloads = []

    async def fake(messages, **_):
        payloads.append(json.loads(messages[1]["content"]))
        return LlmResult(
            data={
                "intents": ["provide_info"],
                "facts": {
                    "country_code": other_name,
                    "vehicle_model": "其他车型",
                    "scenario_type": "远程诊断",
                    "exterior_video_and_gnss": "明示同意",
                    current: answer,
                },
                "answer_kind": "answer",
            },
            prompt_tokens=1,
            completion_tokens=1,
            duration_ms=1,
            model="fake",
        )

    monkeypatch.setattr(chat_intent, "call_llm_json", fake)
    monkeypatch.setattr(chat_reply, "get_settings", lambda: types.SimpleNamespace(llm_api_key="fake"))
    auth = bearer(await login(client, "user", "a-member@example.com"))
    created = (await client.post("/api/analysis/chats", headers=auth)).json()
    tid = created["id"]
    for text in (
        f"{COUNTRY_NAMES_ZH[country]} ({country})",
        "原有车型",
        "L3/L4 自动驾驶公共道路测试与远程监控回传",
        original_fact,
        "传输到境外研发中心，保障机制尚未确定",
    ):
        before = await send(client, auth, tid, text)
    assert before["messages"][-1]["slot"] == current
    earlier_calls = len(payloads)
    after = await send(client, auth, tid, answer)
    assert after["country_code"] == country
    assert after["title"] == f"{COUNTRY_NAMES_ZH[country]} · 原有车型 合规评估"
    assert after["vehicle_model"] == "原有车型"
    assert after["scenario_type"] == before["scenario_type"]
    assert after["facts"]["exterior_video_and_gnss"] == original_fact
    assert after["facts"][current] == answer
    assert after["messages"][:-3] == before["messages"]
    assert "目标国家:" not in after["messages"][-2]["content"]
    # 模型即使把已确认的槽位也“读”了出来，程序也只采纳还空着的槽位和当前这一项：
    # 已确认的国家、车型、功能、事实不会被后面的话悄悄改掉（要改须用户明确更正）
    assert len(payloads) > earlier_calls


async def test_current_answer_is_saved_even_when_model_only_fills_another_blank(
    client, seed, monkeypatch
):
    async def fake(messages, **_):
        return LlmResult(
            data={"intents": ["provide_info"], "facts": {"cross_border_mechanism": "境外研发中心"}, "answer_kind": "answer"},
            prompt_tokens=1,
            completion_tokens=1,
            duration_ms=1,
            model="fake",
        )

    monkeypatch.setattr(chat_intent, "call_llm_json", fake)
    monkeypatch.setattr(chat_reply, "get_settings", lambda: types.SimpleNamespace(llm_api_key="fake"))
    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    for answer in ("突尼斯 (TN)", "普通车型", "跨境远程诊断、OTA 升级包分发与故障日志回传"):
        await send(client, auth, tid, answer)
    answer = "车辆采集外部摄像头视频和轨迹，在车端对人脸和车牌脱敏后发送到境外研发中心用于研发诊断和故障分析。"
    detail = await send(client, auth, tid, answer)
    assert detail["facts"]["exterior_video_and_gnss"] == answer
    assert detail["facts"]["cross_border_mechanism"] == "境外研发中心"
    assert detail["messages"][-1]["slot"] == "consent_and_notice"


@pytest.mark.parametrize("answer", ["突尼斯和新加坡", "TN → SG", "EG, MA", "未知国家"])
async def test_ambiguous_country_needs_explicit_selection(client, seed, monkeypatch, answer):
    from . import chat_fakes

    chat_fakes.install(monkeypatch)  # 模型即使读成“回答”，含糊的国家也不会被选中
    auth = bearer(await login(client, "user", "a-member@example.com"))
    tid = (await client.post("/api/analysis/chats", headers=auth)).json()["id"]
    result = await send(client, auth, tid, answer)
    assert result["country_code"] == chat.PLACEHOLDER_COUNTRY
    assert result["messages"][-1]["slot"] == "country_code"
    assert result["status"] == "collecting_facts"


def test_country_matching_follows_configuration_and_does_not_pick_first(monkeypatch):
    monkeypatch.setattr(chat, "COUNTRY_NAMES_ZH", {"AA": "甲国", "BB": "乙国"})
    assert chat.match_country("甲国 (AA)") == "AA"
    assert chat.match_country("甲国") == "AA"
    assert chat.match_country("乙国 (bb)") == "BB"
    assert chat.match_country("从甲国传输到乙国") is None
    assert chat.match_country("AABB") is None


def test_dynamic_non_pilot_countries_match_cleanly():
    assert chat.match_country("德国") == "DE"
    assert chat.match_country("Germany") == "DE"
    assert chat.match_country("日本 (JP)") == "JP"
    assert chat.match_country("阿联酋") == "AE"
    assert chat.match_country("UAE") == "AE"
    assert chat.match_country("巴西") == "BR"
    assert chat.match_country("泰国") == "TH"
    assert chat.match_country("德国和日本") is None


def test_initial_questionnaire_has_no_country_specific_legal_presumptions():
    for spec in REQUIRED_FACTS_SPEC:
        text = json.dumps(spec, ensure_ascii=False)
        assert all(name not in text for name in COUNTRY_NAMES_ZH.values())
        assert not any(term in text for term in ("PDPA", "LTA", "INPDP", "NTRA"))
