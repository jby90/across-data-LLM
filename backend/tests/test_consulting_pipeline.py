"""真实流水线 + 假模型：咨询层写入报告版本，失败时证据版报告仍然交付。"""

import json

from app.analysis import pipeline
from app.analysis.llm import LlmResult
from app.core.config import get_settings

from .conftest import bearer, login
from .test_analysis_p3 import FACTS, _fake_llm_factory, _seed_sg_corpus, _wait_done


def consulting_aware(base, *, break_dimensions=False):
    """分析与核验阶段沿用既有假模型；咨询层请求按输入里的真实证据编号作答。"""

    async def fake(messages, **kw):
        payload = json.loads(messages[1]["content"])
        if "risk_counts" in payload:
            return LlmResult(
                data={
                    "verdict": "有条件可行",
                    "headline": "可以推进，但要先补齐传输保护措施",
                    "summary": "已核验的要求里存在需要整改的差距，建议先在 30 天内补齐保护措施。",
                    "must_do_action_ids": [payload["actions"][0]["action_id"]],
                },
                prompt_tokens=10,
                completion_tokens=5,
                duration_ms=1,
                model="fake-model",
            )
        if "evidence" in payload and "phase" not in payload:
            if break_dimensions:
                return LlmResult(
                    data={"conclusion": "太短"},
                    prompt_tokens=1,
                    completion_tokens=1,
                    duration_ms=1,
                    model="fake-model",
                )
            return LlmResult(
                data={
                    "conclusion": "把数据传到境外之前，需要确认接收方能提供可比的保护，目前缺少这方面的安排。",
                    "assessments": [
                        {
                            "requirement": "境外接收方须提供可比的保护",
                            "evidence_ids": [payload["evidence"][0]["evidence_id"]],
                            "business_fact_keys": [next(iter(payload["business_facts"]))],
                            "business_state": "目前只有采集和传输的安排，没有对接收方保护水平的约定",
                            "status": "non_compliant",
                            "likelihood": 4,
                            "impact": 4,
                            "rationale": "没有约定就无法证明接收方的保护水平可比",
                        }
                    ],
                    "actions": [
                        {
                            "category": "organization",
                            "action": "与境外接收方签订数据传输协议并评估保护水平",
                            "owner_role": "法务负责人",
                            "due_in_days": 30,
                            "done_criteria": "协议签署且评估记录归档",
                            "addresses": [1],
                        }
                    ],
                },
                prompt_tokens=10,
                completion_tokens=5,
                duration_ms=1,
                model="fake-model",
            )
        return await base(messages, **kw)

    return fake


async def run_task(client, hdr, monkeypatch, fake):
    monkeypatch.setattr(pipeline, "call_llm_json", fake)
    created = await client.post(
        "/api/analysis/tasks",
        headers=hdr,
        json={"title": "咨询层", "country_code": "SG", "facts": FACTS},
    )
    assert created.status_code == 201, created.text
    return await _wait_done(client, hdr, created.json()["id"])


async def test_pipeline_writes_consulting_layer(client, seed, session_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "consulting_enabled", True)
    await _seed_sg_corpus(session_factory)
    hdr = bearer(await login(client, "user", "a-member@example.com"))
    done = await run_task(
        client, hdr, monkeypatch, consulting_aware(_fake_llm_factory(bad_first=False))
    )

    report = done["report_versions"][-1]
    consulting = report["consulting"]
    assert consulting["status"] in ("ok", "partial"), consulting
    assert consulting["verdict"]["label"] == "有条件可行"
    assert consulting["references"] and consulting["roadmap"]
    # 报告正文与摘要改为咨询版；证据版文字保留在维度的 legacy 字段里，不丢
    assert report["executive_summary"].startswith("有条件可行：")
    with_text = [d for d in report["dimensions"] if "[1]" in d["content_markdown"]]
    assert with_text
    assert "咨询层已生成" in report["change_note"]
    # 咨询层是一次可追踪的步骤，token 计入任务
    traces = done["step_traces"]
    assert any(
        call["tool_name"] == "build_consulting_layer" for t in traces for call in t["tool_calls"]
    )
    assert done["total_tokens"] > 0
    # 用户端看不到模型、用量与校验诊断
    mine = (await client.get(f"/api/analysis/tasks/{done['id']}", headers=hdr)).json()
    visible = mine["report_versions"][-1]["consulting"]
    assert visible["verdict"]["label"] == "有条件可行"
    assert not {"model", "tokens", "validation", "prompt_version"} & visible.keys()


async def test_failed_consulting_keeps_evidence_report(client, seed, session_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "consulting_enabled", True)
    await _seed_sg_corpus(session_factory)
    hdr = bearer(await login(client, "user", "a-member@example.com"))
    done = await run_task(
        client,
        hdr,
        monkeypatch,
        consulting_aware(_fake_llm_factory(bad_first=False), break_dimensions=True),
    )

    assert done["status"] in ("completed", "partial_sample")
    report = done["report_versions"][-1]
    assert report["consulting"]["status"] == "failed"
    # 没通过校验的咨询内容不替换证据版正文
    assert report["executive_summary"].startswith("本报告针对")
    assert all("legacy_content_markdown" not in d for d in report["dimensions"])
    assert "咨询层已生成" not in report["change_note"]
    assert done["findings"]


def declares_no_applicable_source(base, chosen):
    """第一个有候选条款的维度：模型正常作答，但明确说明没有适用的依据。"""

    async def fake(messages, **kw):
        payload = json.loads(messages[1]["content"])
        if payload.get("phase") == "select_evidence" and payload.get("candidate_clauses"):
            code = payload["dimension"]["code"]
            chosen.setdefault("code", code)
            if code == chosen["code"]:
                return LlmResult(
                    data={
                        "findings": [],
                        "summary": "",
                        "recommendations": [],
                        "gaps": "现有条款只针对另一类场景，与当前业务不直接对应",
                    },
                    prompt_tokens=5,
                    completion_tokens=5,
                    duration_ms=1,
                    model="fake-model",
                )
        return await base(messages, **kw)

    return fake


async def test_declared_source_gap_is_a_gap_not_a_technical_failure(
    client, seed, session_factory, monkeypatch
):
    monkeypatch.setattr(get_settings(), "consulting_enabled", True)
    await _seed_sg_corpus(session_factory)
    hdr = bearer(await login(client, "user", "a-member@example.com"))
    chosen: dict = {}
    base = consulting_aware(_fake_llm_factory(bad_first=False))
    done = await run_task(client, hdr, monkeypatch, declares_no_applicable_source(base, chosen))

    assert chosen, "测试前提：至少有一个维度有候选条款"
    report = done["report_versions"][-1]
    dim = next(d for d in report["dimensions"] if d["dimension_code"] == chosen["code"])
    assert dim["status"] == "PARTIAL_GAP" and dim["gap_kind"] == "source_gap"
    assert dim["missing_gap_note"].startswith("已归档法源中没有与该场景直接对应的可核验条款：")
    # 其余维度照常交付，整份报告是部分样稿而不是失败；可以导出
    assert report["status"] == "partial_sample" and done["status"] == "partial_sample"
    assert (
        await client.get(
            f"/api/analysis/tasks/{done['id']}/reports/{report['version_no']}/export.docx",
            headers=hdr,
        )
    ).status_code == 200
    # 咨询层把该维度记为没有依据（报告里不出现这个方面），不编造
    cd = next(d for d in report["consulting"]["dimensions"] if d["code"] == chosen["code"])
    assert cd["status"] == "no_evidence" and cd["assessments"] == []


async def test_disabled_flag_skips_consulting(client, seed, session_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "consulting_enabled", False)
    await _seed_sg_corpus(session_factory)
    hdr = bearer(await login(client, "user", "a-member@example.com"))
    done = await run_task(client, hdr, monkeypatch, _fake_llm_factory(bad_first=False))
    assert done["report_versions"][-1]["consulting"] is None
