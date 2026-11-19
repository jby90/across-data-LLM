"""咨询层：程序校验、降级、编号。使用假模型，不访问网络，不依赖具体国家。"""

import json

import pytest
from pydantic import ValidationError

from app.analysis import consulting as c
from app.analysis.llm import LlmResult, LlmUnavailableError

CFG = {"model": "fake", "temperature": 0.2, "timeout": 5}
FACTS = {
    "data_types": "回传车辆识别码、GPS 轨迹、驾驶员摄像头图像",
    "transfer": "每天批量回传到总部数据中心，传输加密",
}
SCENARIO = {"country": "XX", "scenario": "数据回传"}


def finding(fid, clause, obligation, quote_zh, quote="Original text of the rule."):
    return {
        "finding_id": fid,
        "dimension_code": "D?",
        "business_fact": "业务事实",
        "verification_status": "VERIFIED_SUBSTRING",
        "clause_id": clause,
        "version_id": f"v-{clause}",
        "document_title": f"Act {clause}",
        "clause_number": f"{clause}",
        "version_no": 1,
        "source_url": f"https://example.org/{clause}",
        "compliance_obligation": obligation,
        "basis_quote": quote,
        "basis_zh": quote_zh,
        "risk_level": "MEDIUM",
    }


def dim(code, title, findings):
    return {
        "dimension_code": code,
        "dimension_title": title,
        "status": "COMPLETE" if findings else "PARTIAL_GAP",
        "risk_level": "MEDIUM" if findings else "PENDING_VERIFICATION",
        "content_markdown": "证据版正文" if findings else "",
        "key_recommendations": [],
        "findings": findings,
        "citations": [
            {
                "citation_tag": f"[{code}-C{i}]",
                "clause_id": f["clause_id"],
                "document_title": f["document_title"],
                "clause_number": f["clause_number"],
                "version_no": f["version_no"],
                "quote_original": f["basis_quote"],
                "quote_zh": f["basis_zh"],
                "review_status": "pending",
            }
            for i, f in enumerate(findings, 1)
        ],
        "missing_gap_note": "没有依据" if not findings else None,
    }


def evidence_dims():
    return [
        dim(
            "D1",
            "传输路径",
            [
                finding(
                    "F-01", "a1", "传输前须确认接收方受法律约束", "接收方须受有约束力的义务约束。"
                ),
                finding(
                    "F-02",
                    "a2",
                    "泄露评估后最迟 3 个日历日通知监管机构",
                    "最迟三日内通知监管机构。",
                ),
            ],
        ),
        dim("D2", "存储", []),
    ]


def good_dimension(**over):
    data = {
        "conclusion": "回传之前需要先补上跨境传输的法律文件，目前这一步是空白，是最大的缺口。",
        "assessments": [
            {
                "requirement": "回传前要确认接收方受法律约束",
                "evidence_ids": ["F-01"],
                "business_fact_keys": ["transfer"],
                "business_state": "目前只有传输加密，没有传输协议",
                "status": "non_compliant",
                "likelihood": 5,
                "impact": 4,
                "rationale": "没有协议就无法证明接收方受约束，且每天都在传输",
                "needs_fact": "",
            },
            {
                "requirement": "发生泄露后要在期限内通知监管机构",
                "evidence_ids": ["F-02"],
                "business_fact_keys": [],
                "business_state": "",
                "status": "insufficient_info",
                "rationale": "是否已有应急流程目前不清楚",
                "needs_fact": "是否已有数据泄露应急流程",
            },
        ],
        "actions": [
            {
                "category": "organization",
                "action": "与接收方签署数据传输协议并评估保护水平",
                "owner_role": "法务负责人",
                "due_in_days": 30,
                "done_criteria": "协议签署并保留评估记录",
                "addresses": [1],
            }
        ],
    }
    data.update(over)
    return data


def synth(**over):
    data = {
        "verdict": "有条件可行",
        "headline": "可以做，但先把跨境传输协议签好",
        "summary": "目前最大的缺口是传输协议缺失，其余事项需要补充信息后再判断，先在 30 天内补齐协议。",
        "must_do_action_ids": ["A-01"],
    }
    data.update(over)
    return data


class FakeLlm:
    def __init__(self, dimension_replies, synthesis_replies=None):
        self.dimension_replies = list(dimension_replies)
        self.synthesis_replies = list(synthesis_replies or [synth()])
        self.calls = []

    async def __call__(self, messages, *, model, temperature, timeout):
        payload = json.loads(messages[1]["content"])
        self.calls.append(payload)
        queue = self.synthesis_replies if "risk_counts" in payload else self.dimension_replies
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return LlmResult(
            data=reply, prompt_tokens=10, completion_tokens=5, duration_ms=1, model="fake"
        )


async def run(llm, dims=None):
    return await c.build_consulting(
        dimensions=dims or evidence_dims(),
        facts=FACTS,
        scenario=SCENARIO,
        config=CFG,
        llm=llm,
    )


# ------------------------------------------------------------------ 纯函数


@pytest.mark.parametrize(
    ("lik", "imp", "status", "expected"),
    [
        (5, 4, "non_compliant", (20, "high")),
        (3, 5, "partial", (15, "high")),
        (3, 4, "partial", (12, "medium")),
        (2, 4, "partial", (8, "medium")),
        (2, 3, "partial", (6, "low")),
        (None, None, "insufficient_info", (None, "unknown")),
        (None, None, "compliant", (None, "low")),
        (None, None, "partial", (None, "unknown")),
    ],
)
def test_risk_level_is_computed_by_program(lik, imp, status, expected):
    assert c.risk_level(lik, imp, status) == expected


def test_copy_detection_ignores_short_overlap_and_catches_long_copy():
    source = "接收方须受有约束力的义务约束并提供不低于本法的保护水平，否则不得传输"
    assert not c.copies_source("需要接收方受约束", [source])
    assert c.copies_source("我们认为：" + source[:30] + "，所以要整改", [source])


def test_unsupported_numbers():
    assert c.unsupported_numbers("3 个日历日和 500 人", "最迟 3 个日历日") == ["500"]
    assert c.unsupported_numbers("没有数字", "任意") == []


@pytest.mark.parametrize(
    ("label", "counts", "expected_label", "has_note"),
    [
        ("可行", {"high": 1, "medium": 0, "low": 0, "unknown": 0}, "有条件可行", True),
        ("暂不建议", {"high": 0, "medium": 0, "low": 2, "unknown": 0}, "可行", True),
        ("有条件可行", {"high": 0, "medium": 0, "low": 0, "unknown": 3}, "信息不足", True),
        ("暂不建议", {"high": 2, "medium": 0, "low": 0, "unknown": 0}, "暂不建议", False),
    ],
)
def test_verdict_must_agree_with_risk_counts(label, counts, expected_label, has_note):
    got, note = c.coerce_verdict(label, counts)
    assert got == expected_label
    assert bool(note) is has_note


# ------------------------------------------------------------------ 校验规则


def validate(data):
    ledger = c.build_ledger(evidence_dims()[0])
    return c.validate_dimension(
        c.DimensionResponse.model_validate(data), evidence=ledger, facts=FACTS
    )


def test_valid_dimension_passes():
    assert validate(good_dimension()) == []


def test_unknown_evidence_rejected():
    data = good_dimension()
    data["assessments"][0]["evidence_ids"] = ["F-99"]
    assert any("F-99" in p for p in validate(data))


def test_gap_claim_requires_user_fact():
    data = good_dimension()
    data["assessments"][0]["business_fact_keys"] = []
    assert any("没有引用任何业务事实" in p for p in validate(data))
    data["assessments"][0]["business_fact_keys"] = ["not_provided"]
    assert any("没有提供的业务事实" in p for p in validate(data))


def test_numbers_must_come_from_evidence_or_facts():
    data = good_dimension()
    data["assessments"][0]["rationale"] = "罚款可能高达 500 万元，所以风险很高"
    assert any("500" in p for p in validate(data))
    data["assessments"][0]["rationale"] = "泄露后最迟 3 个日历日要通知，目前做不到"
    assert validate(data) == []


def test_model_may_not_write_its_own_citation_markers():
    data = good_dimension()
    data["conclusion"] = "回传之前需要先补上跨境传输的法律文件 [3]，这是最大的缺口。"
    assert any("角标" in p for p in validate(data))


def test_medium_or_high_gap_needs_an_action():
    data = good_dimension(actions=[])
    assert any("没有对应的建议" in p for p in validate(data))


def test_action_must_target_real_gap():
    data = good_dimension()
    data["actions"][0]["addresses"] = [1, 9]
    assert any("不存在的差距编号" in p for p in validate(data))
    # 已达到的差距同样不需要整改
    data = good_dimension()
    data["assessments"][1].update(status="compliant", business_fact_keys=["transfer"])
    data["assessments"][1]["business_state"] = "已有应急流程"
    data["actions"][0]["addresses"] = [1, 2]
    assert any("不是需要整改的差距" in p for p in validate(data))


def test_insufficient_info_must_say_what_to_ask():
    data = good_dimension()
    data["assessments"][1]["needs_fact"] = ""
    assert any("需要用户补充什么" in p for p in validate(data))


def test_format_slips_are_repaired_but_substance_is_not_relaxed():
    # 信息不足却打了分：忽略多给的分数，不整份打回
    data = good_dimension()
    data["assessments"][1].update(likelihood=3, impact=3)
    parsed = c.DimensionResponse.model_validate(data)
    assert parsed.assessments[1].likelihood is None and parsed.assessments[1].impact is None
    # 信息不足漏写理由：用需要补充的事项补上；非信息不足漏写理由仍然拒绝
    data = good_dimension()
    data["assessments"][1].pop("rationale")
    assert c.DimensionResponse.model_validate(data).assessments[1].rationale
    data["assessments"][0].pop("rationale")
    with pytest.raises(ValidationError):
        c.DimensionResponse.model_validate(data)
    # “结论：”前缀被去掉；建议挂到信息不足的差距时只去掉这条关联
    data = good_dimension()
    data["conclusion"] = "结论：回传之前需要先补上跨境传输的法律文件，目前是空白。"
    data["actions"][0]["addresses"] = [1, 2]
    parsed = c.DimensionResponse.model_validate(data)
    assert parsed.conclusion.startswith("回传之前")
    assert parsed.actions[0].addresses == [1]
    # 建议只挂在信息不足的差距上：这条建议没有可整改的对象，直接丢弃，并因此被"缺建议"挡住
    data["actions"][0]["addresses"] = [2]
    parsed = c.DimensionResponse.model_validate(data)
    assert parsed.actions == []
    ledger = c.build_ledger(evidence_dims()[0])
    problems = c.validate_dimension(parsed, evidence=ledger, facts=FACTS)
    assert any("没有对应的建议" in p for p in problems)


# ------------------------------------------------------------------ 编排


async def test_build_consulting_happy_path():
    llm = FakeLlm([good_dimension()])
    result = await run(llm)

    assert result["status"] == "ok"
    d1, d2 = result["dimensions"]
    assert d1["status"] == "ok" and d2["status"] == "no_evidence"
    assert [a["id"] for a in d1["assessments"]] == ["D1-1", "D1-2"]
    assert d1["assessments"][0]["score"] == 20
    assert d1["assessments"][0]["level"] == "high"
    assert d1["assessments"][1]["level"] == "unknown"
    assert result["risk_counts"] == {"high": 1, "medium": 0, "low": 0, "unknown": 1}
    assert result["roadmap"][0]["id"] == "A-01"
    assert result["roadmap"][0]["addresses"] == ["D1-1"]
    assert result["verdict"]["label"] == "有条件可行"
    assert result["verdict"]["must_do_action_ids"] == ["A-01"]
    # 没有证据的维度不调用模型
    assert sum("risk_counts" not in call for call in llm.calls) == 1
    # 参考文献编号由程序按阅读顺序分配，同一条款只一个号
    assert [r["n"] for r in result["references"]] == [1, 2]
    assert d1["assessments"][0]["ref_numbers"] == [1]
    assert result["top_risks"][0]["ref_numbers"] == [1]
    assert result["tokens"] > 0


async def test_failed_validation_feeds_problems_back_then_gives_up_honestly():
    bad = good_dimension()
    bad["assessments"][0]["business_fact_keys"] = []
    llm = FakeLlm([bad])
    result = await run(llm)

    dim_calls = [call for call in llm.calls if "risk_counts" not in call]
    assert len(dim_calls) == c.MAX_ATTEMPTS
    assert "must_fix" not in dim_calls[0]
    assert any("没有引用任何业务事实" in p for p in dim_calls[1]["must_fix"])
    assert result["dimensions"][0]["status"] == "failed"
    assert result["status"] == "failed"
    assert result["validation"]["rejected"][0]["dimension"] == "D1"
    # 没通过校验的内容不会进入报告
    assert result["dimensions"][0]["assessments"] == []


async def test_retry_can_recover_after_feedback():
    bad = good_dimension()
    bad["assessments"][0]["evidence_ids"] = ["F-99"]
    llm = FakeLlm([bad, good_dimension()])
    result = await run(llm)
    assert result["dimensions"][0]["status"] == "ok"
    assert result["status"] == "ok"


async def test_model_outage_marks_dimension_failed_not_crash():
    llm = FakeLlm([LlmUnavailableError("down", kind="model_unavailable", tokens=7)])
    result = await run(llm)
    assert result["dimensions"][0]["status"] == "failed"
    assert "model_unavailable" not in result["dimensions"][0]["note"]  # 用户看到的是业务说法
    assert "model_unavailable" in str(result["validation"]["rejected"])  # 技术原因留给管理端
    assert result["status"] == "failed"


async def test_synthesis_failure_falls_back_to_program_text():
    llm = FakeLlm(
        [good_dimension()], [synth(summary="共有 99 项高风险，需要立刻处理，请在 30 天内完成。")]
    )
    result = await run(llm)
    assert result["status"] == "partial"
    assert "没有生成" in result["failure_reason"]
    assert "校验" not in result["failure_reason"]  # 技术原因不展示给业务人员
    summary = result["verdict"]["summary"]
    assert "高风险 1 项" in summary  # 程序按统计生成
    assert "99" not in summary


async def test_summary_verdict_is_corrected_by_risk_counts():
    llm = FakeLlm([good_dimension()], [synth(verdict="可行")])
    result = await run(llm)
    assert result["verdict"]["label"] == "有条件可行"
    assert result["validation"]["verdict_note"]


async def test_partial_when_one_dimension_fails():
    dims = evidence_dims()
    dims.append(
        dim("D3", "同意", [finding("F-03", "a3", "收集前须告知目的", "收集前要告知目的。")])
    )
    replies = {"D1": good_dimension()}

    class Switch(FakeLlm):
        async def __call__(self, messages, **kw):
            payload = json.loads(messages[1]["content"])
            if "risk_counts" not in payload and payload["dimension"]["code"] == "D3":
                self.calls.append(payload)
                return LlmResult(
                    data={"conclusion": "x"},
                    prompt_tokens=1,
                    completion_tokens=1,
                    duration_ms=1,
                    model="fake",
                )
            return await super().__call__(messages, **kw)

    result = await run(Switch([replies["D1"]]), dims)
    assert result["status"] == "partial"
    assert "同意" in result["failure_reason"] and "D3" not in result["failure_reason"]
    assert [d["status"] for d in result["dimensions"]] == ["ok", "no_evidence", "failed"]


async def test_single_dimension_export_view_renumbers_references():
    result = await run(FakeLlm([good_dimension()]))
    ledgers = c.build_ledger(evidence_dims()[0])
    one = c.filter_dimension(result, "D1", ledgers)
    assert [d["code"] for d in one["dimensions"]] == ["D1"]
    assert [r["n"] for r in one["references"]] == [1, 2]
    # 原对象不被修改
    assert len(result["dimensions"]) == 2


def action(category, text, addresses, days=30, owner="法务", done="完成"):
    return {
        "category": category,
        "action": text,
        "owner_role": owner,
        "due_in_days": days,
        "done_criteria": done,
        "addresses": addresses,
    }


def test_actions_are_merged_only_when_same_category_and_same_gaps():
    merged = c.merge_similar_actions(
        [
            action("organization", "签署协议", ["D3-1"], days=45, owner="法务", done="签署"),
            action(
                "organization",
                "与接收方签署跨境数据传输协议",
                ["D3-1"],
                days=30,
                owner="法务负责人",
                done="协议签署并归档",
            ),
            action("organization", "签署协议", ["D3-2"]),  # 差距不同：不合并
            action("cloud", "签署协议", ["D3-1"]),  # 类别不同：不合并
        ]
    )
    assert len(merged) == 3
    first = merged[0]
    assert first["action"] == "与接收方签署跨境数据传输协议"  # 保留更详细的写法
    assert first["due_in_days"] == 30  # 取更紧的时限
    assert first["owner_role"] == "法务负责人" and first["done_criteria"] == "协议签署并归档"
    assert first["addresses"] == ["D3-1"]


def test_merge_does_not_mutate_inputs_or_lose_gaps():
    original = [
        action("vehicle", "本地处理", ["D1-1"]),
        action("vehicle", "本地处理摄像头", ["D1-1"]),
    ]
    snapshot = json.loads(json.dumps(original))
    out = c.merge_similar_actions(original)
    assert original == snapshot
    assert {t for a in out for t in a["addresses"]} == {"D1-1"}


def numbered(*acts):
    for n, a in enumerate(acts, 1):
        a["id"], a["priority"] = f"A-{n:02d}", n
    return list(acts)


def test_absorb_duplicates_merges_renumbers_and_remaps():
    acts = numbered(
        action("organization", "重做告知同意", ["D2-2"], days=60, owner="法务", done="上线"),
        action("cloud", "补充访问控制", ["D5-1"]),
        action(
            "organization",
            "改写购车协议与车机告知页并取消默认勾选",
            ["D4-1"],
            days=30,
            owner="法务与产品",
            done="新协议上线并留存同意版本",
        ),
    )
    kept, remap = c.absorb_duplicates(acts, [["A-01", "A-03"]])
    assert [a["id"] for a in kept] == ["A-01", "A-02"]
    assert remap == {"A-01": "A-01", "A-02": "A-02", "A-03": "A-01"}
    first = kept[0]
    assert first["addresses"] == ["D2-2", "D4-1"]  # 两个差距都仍被覆盖
    assert first["due_in_days"] == 30 and first["owner_role"] == "法务与产品"
    assert first["action"] == "改写购车协议与车机告知页并取消默认勾选"
    assert acts[0]["addresses"] == ["D2-2"]  # 输入不被修改


def test_dimension_actions_follow_their_roadmap_entry():
    roadmap = numbered(action("organization", "合并后的建议", ["D1-1", "D2-1"]))
    results = [
        {"actions": [action("organization", "维度一的写法", ["D1-1"])]},
        {"actions": [action("organization", "维度二的写法", ["D2-1"])]},
        # 同名建议跨类别合并后，类别不同但解决同一差距的，也认领合并后的那条
        {"actions": [action("cloud", "类别不同", ["D1-1"])]},
        {"actions": [action("cloud", "解决的差距不相关", ["D9-9"])]},
    ]
    c.link_dimension_actions(results, roadmap)
    assert [r["actions"][0].get("id") for r in results] == ["A-01", "A-01", "A-01", None]


def test_synthesis_duplicate_groups_are_validated():
    ids = {"A-01", "A-02", "A-03"}
    cats = {"A-01": "organization", "A-02": "organization", "A-03": "cloud"}
    counts = {"high": 1, "medium": 0, "low": 0, "unknown": 0}

    def check(groups):
        resp = c.SynthesisResponse.model_validate(
            synth(duplicate_action_groups=groups, must_do_action_ids=["A-01"])
        )
        return c.validate_synthesis(
            resp, action_ids=ids, counts=counts, allowed_numbers={"30"}, action_category=cats
        )

    assert check([["A-01", "A-02"]]) == []
    assert check([["A-01", "A-03"]]) == []  # 同一件事被归入不同类别，也允许成组
    assert any("不存在" in p for p in check([["A-01", "A-09"]]))
    assert any("至少两个" in p for p in check([["A-01"]]))
    assert any("多个分组" in p for p in check([["A-01", "A-02"], ["A-02", "A-03"]]))


async def test_synthesis_merge_is_applied_end_to_end_and_must_do_is_remapped():
    dup = [
        action("organization", "改写告知同意流程", ["D1-1"]),
        action("organization", "重做购车协议和车机的告知与同意", ["D3-1"]),
    ]
    llm = FakeLlm(
        [good_dimension()],
        [synth(must_do_action_ids=["A-02"], duplicate_action_groups=[["A-01", "A-02"]])],
    )
    dims = evidence_dims()
    # 第二个有证据的维度提出了相同类别的另一条建议
    dims[1] = dim("D2", "同意", [finding("F-03", "a3", "收集前须告知目的", "收集前要告知目的。")])
    reply2 = good_dimension()
    reply2["assessments"][0]["evidence_ids"] = ["F-03"]
    reply2["assessments"][1]["evidence_ids"] = ["F-03"]
    reply2["actions"][0]["action"] = dup[1]["action"]

    class TwoDims(FakeLlm):
        async def __call__(self, messages, **kw):
            payload = json.loads(messages[1]["content"])
            if "risk_counts" not in payload and payload["dimension"]["code"] == "D2":
                self.calls.append(payload)
                return LlmResult(
                    data=reply2, prompt_tokens=1, completion_tokens=1, duration_ms=1, model="fake"
                )
            return await super().__call__(messages, **kw)

    llm = TwoDims(
        [good_dimension()],
        [synth(must_do_action_ids=["A-02"], duplicate_action_groups=[["A-01", "A-02"]])],
    )
    result = await c.build_consulting(
        dimensions=dims, facts=FACTS, scenario=SCENARIO, config=CFG, llm=llm
    )
    assert result["status"] == "ok", result["failure_reason"]
    assert len(result["roadmap"]) == 1
    assert result["roadmap"][0]["id"] == "A-01"
    assert set(result["roadmap"][0]["addresses"]) == {"D1-1", "D2-1"}
    assert result["verdict"]["must_do_action_ids"] == ["A-01"]
    assert result["validation"]["merged_action_groups"] == [["A-01", "A-02"]]


def test_markdown_contains_numbered_refs_not_quotes():
    result = {
        "conclusion": "先补协议。",
        "assessments": [
            {
                "id": "D1-1",
                "requirement": "确认接收方受约束",
                "status": "non_compliant",
                "level": "high",
                "rationale": "目前没有协议",
                "needs_fact": "",
                "ref_numbers": [1, 2],
            }
        ],
        "actions": [
            {
                "category": "organization",
                "action": "签协议",
                "owner_role": "法务",
                "due_in_days": 30,
                "done_criteria": "协议签署",
            }
        ],
    }
    md = c.render_dimension_markdown(result)
    assert "确认接收方受约束[1][2]" in md
    assert "组织与流程" in md and "30天内" in md


# ------------------------------------------------------------------ v2：阶段、依赖、短标题、概述


def test_phase_boundaries():
    assert [c.phase_of(d) for d in (1, 30, 31, 60, 61, 365)] == [1, 1, 2, 2, 3, 3]


def test_action_title_falls_back_to_first_clause_and_never_rewrites():
    assert c.short_title("与接收方签署数据传输协议，并评估保护水平") == "与接收方签署数据传输协议"
    assert c.short_title("**先暂停**未脱敏图像回传；再改造流程") == "先暂停未脱敏图像回传"
    long = "对" * 40
    assert c.short_title(long) == "对" * 22 + "…"
    parsed = c.DimensionResponse.model_validate(good_dimension())
    # 动作本身没有逗号且不超长时，短标题就是动作原文（只截取，不改写）
    assert parsed.actions[0].title == "与接收方签署数据传输协议并评估保护水平"


def test_overview_is_checked_like_other_text():
    ledger = c.build_ledger(evidence_dims()[0])
    data = good_dimension(overview="这个国家的框架见 D3 的说明")
    resp = c.DimensionResponse.model_validate(data)
    problems = c.validate_dimension(resp, evidence=ledger, facts=FACTS)
    assert any("概述" in p and "内部编号" in p for p in problems)


def test_finalize_roadmap_orders_by_phase_then_dependency_and_drops_bad_links():
    acts = numbered(
        action("organization", "三", ["x3"], days=90),
        action("organization", "一", ["x1"], days=20),
        action("vehicle", "二", ["x2"], days=20),
    )
    ordered, dropped = c.finalize_roadmap(
        acts,
        [
            ("A-02", "A-03"),  # 同阶段：A-03 先做
            ("A-01", "A-02"),  # 前提更早：合法
            ("A-03", "A-01"),  # 前提安排在更晚的阶段：丢弃
            ("A-02", "A-09"),  # 编号不存在：丢弃
            ("A-03", "A-03"),  # 自指：丢弃
        ],
    )
    assert [a["id"] for a in ordered] == ["A-03", "A-02", "A-01"]
    assert [a["priority"] for a in ordered] == [1, 2, 3]
    assert [a["phase"] for a in ordered] == [1, 1, 3]
    by_id = {a["id"]: a for a in ordered}
    assert by_id["A-02"]["depends_on"] == ["A-03"] and by_id["A-01"]["depends_on"] == ["A-02"]
    assert by_id["A-03"]["depends_on"] == []
    assert sorted(d["reason"] for d in dropped) == sorted(
        ["前提安排在后续阶段", "编号不存在或指向自身", "编号不存在或指向自身"]
    )


def test_finalize_roadmap_breaks_cycles_without_losing_actions():
    acts = numbered(action("cloud", "甲", ["x1"], days=10), action("cloud", "乙", ["x2"], days=10))
    ordered, dropped = c.finalize_roadmap(acts, [("A-01", "A-02"), ("A-02", "A-01")])
    assert {a["id"] for a in ordered} == {"A-01", "A-02"}
    assert [d["reason"] for d in dropped] == ["会形成循环依赖"]


def test_synthesis_dependencies_are_validated():
    ids = {"A-01", "A-02", "A-03"}
    counts = {"high": 1, "medium": 0, "low": 0, "unknown": 0}

    def check(deps, phases):
        resp = c.SynthesisResponse.model_validate(synth(dependencies=deps))
        return c.validate_synthesis(
            resp, action_ids=ids, counts=counts, allowed_numbers={"30"}, action_phase=phases
        )

    staged = {"A-01": 1, "A-02": 2, "A-03": 3}
    assert check([{"action_id": "A-02", "after_action_id": "A-01"}], staged) == []
    assert any(
        "更晚" in p for p in check([{"action_id": "A-01", "after_action_id": "A-03"}], staged)
    )
    assert any(
        "不存在" in p for p in check([{"action_id": "A-01", "after_action_id": "A-09"}], staged)
    )
    assert any(
        "依赖自己" in p for p in check([{"action_id": "A-01", "after_action_id": "A-01"}], staged)
    )
    same = {"A-01": 1, "A-02": 1, "A-03": 1}
    loop = [
        {"action_id": "A-01", "after_action_id": "A-02"},
        {"action_id": "A-02", "after_action_id": "A-01"},
    ]
    assert any("循环" in p for p in check(loop, same))


async def test_dependencies_survive_the_whole_build_and_drive_the_order():
    # D1 的建议 30 天、D3 的建议 60 天；结论页说 D3 的建议要在 D1 之后
    dims = evidence_dims()
    dims[1] = dim("D3", "同意", [finding("F-03", "a3", "收集前须告知目的", "收集前要告知目的。")])
    second = good_dimension()
    second["assessments"][0]["evidence_ids"] = ["F-03"]
    second["assessments"][1]["evidence_ids"] = ["F-03"]
    second["actions"][0].update(
        action="摄像头图像默认只在车内处理，上传前单独征得同意", due_in_days=60, category="vehicle"
    )

    class Two(FakeLlm):
        async def __call__(self, messages, **kw):
            payload = json.loads(messages[1]["content"])
            if "risk_counts" not in payload and payload["dimension"]["code"] == "D3":
                self.calls.append(payload)
                return LlmResult(
                    data=second, prompt_tokens=1, completion_tokens=1, duration_ms=1, model="fake"
                )
            return await super().__call__(messages, **kw)

    llm = Two(
        [good_dimension()], [synth(dependencies=[{"action_id": "A-02", "after_action_id": "A-01"}])]
    )
    result = await c.build_consulting(
        dimensions=dims, facts=FACTS, scenario=SCENARIO, config=CFG, llm=llm
    )
    road = result["roadmap"]
    assert [a["phase"] for a in road] == [1, 2]
    assert road[1]["depends_on"] == [road[0]["id"]]
    assert result["validation"]["dropped_dependencies"] == []
    assert result["roadmap_intro"].startswith("共 2 项整改动作")
    # 咨询层给模型的路线图输入带着类别和短标题，方便它判断先后
    synth_input = next(call for call in llm.calls if "risk_counts" in call)
    assert {"title", "category", "due_in_days"} <= synth_input["actions"][0].keys()


def test_roadmap_dimension_markdown_lists_phases():
    roadmap = [
        {
            **action("organization", "签协议", ["D3-1"], days=30),
            "id": "A-01",
            "phase": 1,
            "title": "签协议",
        },
        {
            **action("vehicle", "车内处理", ["D4-1"], days=60),
            "id": "A-02",
            "phase": 2,
            "title": "车内处理",
        },
    ]
    md = c.render_dimension_markdown(
        {"code": "D8", "conclusion": "整改要分阶段推进", "assessments": [], "actions": []}, roadmap
    )
    assert "整改要分阶段推进" in md
    assert c.PHASE_LABELS[1] in md and c.PHASE_LABELS[2] in md and c.PHASE_LABELS[3] not in md
    assert "- 签协议：" in md and "- 车内处理：" in md


# ------------------------------------------------------------------ 数字校验：认得同一个数的不同写法


@pytest.mark.parametrize(
    ("text", "corpus", "expected"),
    [
        ("罚款上限为 100 万新元", "不得超过 $1 million", []),  # 中英文单位换算
        ("罚款上限为 1000 万新元", "年营业额超过 $10 million", []),
        ("上限是 100万", "up to 1 million", []),  # 无空格
        ("罚款上限为 500 万新元", "不得超过 $1 million", ["500"]),  # 凭空数字仍被拦
        ("可能罚 1000 万", "不得超过 $1 million", ["1000"]),  # 数值对不上
        ("最迟 3 个日历日通知", "最迟 3 个日历日", []),  # 无单位的数字照旧按原样比较
        ("影响 500 人以上", "最迟 3 个日历日", ["500"]),
        ("占营业额的 10%", "该组织年营业额的 10%", []),
        ("约 2 万 4 千元", "3 个日历日", ["2", "4"]),  # 不认“多段拼接”的写法，宁可拦下
    ],
)
def test_unsupported_numbers_understands_unit_conversion_but_not_invention(text, corpus, expected):
    assert c.unsupported_numbers(text, corpus) == expected


def test_scaled_values():
    assert c.scaled_values("$1 million 和 100万 以及 10 million") == {1_000_000, 10_000_000}
    assert c.scaled_values("没有单位 100") == set()
    assert c.scaled_values("1.5 million") == {1_500_000}


def test_overlapping_actions_in_same_category_and_phase_are_merged():
    merged = c.merge_overlapping_actions(
        [
            action(
                "data_architecture", "按类别设保留期", ["D5-1", "D2-3"], days=60, owner="数据治理"
            ),
            action(
                "data_architecture",
                "建立保留期限表并配置到期删除",
                ["D5-1"],
                days=45,
                owner="数据平台负责人",
                done="保留表获批且有删除日志",
            ),
            action("data_architecture", "梳理数据流", ["D3-1"], days=60),  # 差距无交集：不合并
            action("cloud", "云端到期删除", ["D5-1"], days=60),  # 类别不同：不合并
            action(
                "data_architecture", "保留期限的长期方案", ["D5-1"], days=90
            ),  # 阶段不同：不合并
        ]
    )
    assert [a["action"] for a in merged] == [
        "建立保留期限表并配置到期删除",
        "梳理数据流",
        "云端到期删除",
        "保留期限的长期方案",
    ]
    first = merged[0]
    assert first["due_in_days"] == 45  # 取更紧的时限
    assert first["addresses"] == ["D5-1", "D2-3"]  # 差距取并集，一个都不丢
    assert first["owner_role"] == "数据平台负责人"


def test_overlap_merge_never_loses_a_gap_and_does_not_mutate_inputs():
    original = [
        action("vehicle", "甲", ["D1-1", "D1-2"], days=20),
        action("vehicle", "乙乙", ["D1-2", "D1-3"], days=25),
    ]
    snapshot = json.loads(json.dumps(original))
    out = c.merge_overlapping_actions(original)
    assert original == snapshot
    assert len(out) == 1
    assert set(out[0]["addresses"]) == {"D1-1", "D1-2", "D1-3"}


# ------------------------------------------------------------------ 整改时限不是法规数字


@pytest.mark.parametrize(
    ("text", "days"),
    [
        ("先在 30 天内签署协议", {30}),
        ("两件事要在 2 周内完成", {14}),
        ("约 3 个月内上线", {90}),
        ("30天内和 60 天内", {30, 60}),
        ("每 30 天复盘一次", set()),
    ],
)
def test_deadline_days(text, days):
    assert c.deadline_days(text) == days


def test_own_action_deadline_is_not_a_legal_number_but_invented_deadlines_are_still_caught():
    corpus = "最迟 3 个日历日通知"
    ok = c.text_problems("结论", "建议先在 30 天内补齐协议", corpus, [], allowed_days={30})
    assert ok == []
    # 本方面没有写 30 天的建议：这个“30 天”不能凭空出现
    assert c.text_problems("结论", "建议先在 30 天内补齐协议", corpus, [], allowed_days={60})
    # 编造的法定期限仍被拦：7 天不是本方面任何建议的时限，也不在依据里
    bad = c.text_problems("结论", "泄露后必须在 7 天内上报", corpus, [], allowed_days={30})
    assert any("7" in p for p in bad)
    # 依据里确实有的数字照旧可以用
    assert (
        c.text_problems("结论", "泄露评估后最迟 3 个日历日通知", corpus, [], allowed_days=set())
        == []
    )


def test_dimension_conclusion_may_mention_its_own_action_deadline():
    ledger = c.build_ledger(evidence_dims()[0])
    data = good_dimension(
        conclusion="回传之前要先补上跨境传输的法律文件，建议先在 30 天内补齐，**现在这一步是空白**。"
    )
    assert (
        c.validate_dimension(c.DimensionResponse.model_validate(data), evidence=ledger, facts=FACTS)
        == []
    )
    # 结论里写了一个本方面没有任何建议对应的时限：仍然被拦
    data = good_dimension(
        conclusion="回传之前要先补上跨境传输的法律文件，建议先在 45 天内补齐，现在这一步是空白。"
    )
    problems = c.validate_dimension(
        c.DimensionResponse.model_validate(data), evidence=ledger, facts=FACTS
    )
    assert any("45" in p for p in problems)


def test_zero_scores_are_treated_as_unscored_not_rejected():
    from app.analysis.consulting import AssessmentItem

    item = AssessmentItem(
        requirement="已建立传输协议",
        evidence_ids=["F-01"],
        status="compliant",
        likelihood=0,
        impact=0,
        rationale="协议已签署并覆盖全部出境数据流",
    )
    assert item.likelihood is None and item.impact is None


def test_same_title_actions_merge_across_category_and_phase():
    from app.analysis.consulting import link_dimension_actions, merge_same_title

    a = {
        "category": "organization",
        "title": "完成保护水平评估",
        "action": "法务牵头评估",
        "owner_role": "法务",
        "due_in_days": 30,
        "done_criteria": "评估报告归档",
        "addresses": ["D3-1"],
    }
    b = {
        "category": "data_architecture",
        "title": "完成保护水平评估",
        "action": "对每日传输开展保护水平评估并记录差距",
        "owner_role": "数据合规负责人",
        "due_in_days": 45,
        "done_criteria": "评估报告经确认",
        "addresses": ["D1-1"],
    }
    other = {**a, "title": "签署跨境传输协议", "addresses": ["D3-2"]}
    merged = merge_same_title([a, b, other])
    assert [m["title"] for m in merged] == ["完成保护水平评估", "签署跨境传输协议"]
    assert merged[0]["due_in_days"] == 30 and set(merged[0]["addresses"]) == {"D3-1", "D1-1"}
    # 维度里的建议即使类别与合并后的路线图条目不同，也能认领到唯一条目
    merged[0]["id"], merged[0]["priority"] = "A-01", 1
    results = [{"actions": [dict(b)]}]
    link_dimension_actions(results, merged)
    assert results[0]["actions"][0]["id"] == "A-01"


def test_roadmap_dimension_may_mention_phase_boundaries():
    from app.analysis.consulting import DimensionResponse, validate_dimension

    resp = DimensionResponse(
        conclusion="建议 30 天内先补协议，其余在 60 天内补齐，并由负责人跟进落实。"
    )
    assert validate_dimension(resp, evidence={}, facts={}, code="D8") == []
    # 其他方面没有对应建议时，同样的时限仍然要查来源
    assert validate_dimension(resp, evidence={}, facts={}, code="D3") != []


def test_operational_targets_allowed_in_actions_but_not_in_statements():
    from app.analysis.consulting import ActionItem, DimensionResponse, validate_dimension

    action = ActionItem(
        category="organization",
        title="建立撤回响应机制",
        action="收到撤回请求后 24 小时内停止对应数据处理",
        owner_role="法务",
        due_in_days=30,
        done_criteria="撤回请求 24 小时内完成处理并留痕",
        addresses=[1],
    )
    resp = DimensionResponse(
        conclusion="目前没有撤回机制，建议先补上撤回入口并能及时响应用户请求。",
        assessments=[
            {
                "requirement": "用户可随时撤回同意",
                "evidence_ids": ["F-01"],
                "business_fact_keys": ["consent"],
                "business_state": "没有撤回入口",
                "status": "未达到",  # 中文同义写法被接受
                "likelihood": 4,
                "impact": 4,
                "rationale": "没有撤回入口，用户无法行使撤回权。",
            }
        ],
        actions=[action],
    )
    assert resp.assessments[0].status == "non_compliant"
    ev = {
        "F-01": {
            "evidence_id": "F-01",
            "obligation": "用户可撤回同意",
            "quote_zh": "",
            "quote_original": "",
            "clause_number": "16",
        }
    }
    assert validate_dimension(resp, evidence=ev, facts={"consent": "没有撤回入口"}, code="D4") == []
    # 陈述性文字里写“24 小时”仍然要查来源
    resp.conclusion = "法律要求在 24 小时内处理撤回，目前没有撤回机制，需要尽快补上入口。"
    assert any(
        "24" in p
        for p in validate_dimension(resp, evidence=ev, facts={"consent": "没有撤回入口"}, code="D4")
    )


def test_roadmap_dimension_accepts_any_phrasing_of_its_own_durations():
    from app.analysis.consulting import DimensionResponse, validate_dimension

    resp = DimensionResponse(
        conclusion="最紧迫的是任命负责人，前 30 天先把责任落实，再用两个月补齐流程。"
    )
    assert validate_dimension(resp, evidence={}, facts={}, code="D8") == []
    # 与本方面时限无关的天数仍要查来源
    resp = DimensionResponse(
        conclusion="法律规定应在 45 天内完成备案，目前负责人尚未任命，需要尽快补上。"
    )
    assert validate_dimension(resp, evidence={}, facts={}, code="D8") != []
