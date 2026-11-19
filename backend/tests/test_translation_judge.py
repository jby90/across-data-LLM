"""译文语义核验：模型只标注，规则裁决。用固定的标注输出验证裁决逻辑，不依赖真实模型。"""

import pytest

from app.knowledge import translation_judge as tj
from app.knowledge.translation_judge import build_verdict

ORIG = (
    "An organisation must notify the Commission of a data breach. "
    "No person may transfer the personal data outside Singapore without consent."
)
GOOD = "组织必须向委员会通报数据泄露。未经同意，任何人不得将个人数据传输至新加坡境外。"


def pair(src, src_type, tgt, tgt_type):
    return {"src": src, "src_type": src_type, "tgt": tgt, "tgt_type": tgt_type}


S1 = "An organisation must notify the Commission of a data breach."
S2 = "No person may transfer the personal data outside Singapore without consent."


def test_matching_modalities_pass():
    data = {
        "pairs": [
            pair(S1, "obligation", "组织必须向委员会通报数据泄露。", "obligation"),
            pair(
                S2, "prohibition", "未经同意，任何人不得将个人数据传输至新加坡境外。", "prohibition"
            ),
        ]
    }
    v = build_verdict(data, ORIG, GOOD)
    assert v.passed and v.pairs == 2


def test_modality_flip_is_critical():
    bad = "组织必须向委员会通报数据泄露。未经同意，任何人可以将个人数据传输至新加坡境外。"
    data = {
        "pairs": [
            pair(
                S2, "prohibition", "未经同意，任何人可以将个人数据传输至新加坡境外。", "permission"
            ),
        ]
    }
    v = build_verdict(data, ORIG, bad)
    assert not v.passed
    assert v.errors[0].category == "negation_modality" and v.errors[0].severity == "critical"
    assert "禁止" in v.errors[0].explanation


def test_lost_prohibition_is_critical():
    bad = "任何人将个人数据传输至新加坡境外。"
    data = {"pairs": [pair(S2, "prohibition", "任何人将个人数据传输至新加坡境外。", "declarative")]}
    assert not build_verdict(data, ORIG, bad).passed


def test_declarative_shall_translated_without_obligation_is_not_an_error():
    orig = "A District Court shall have jurisdiction to try any offence under this Act."
    zh = "地区法院有权审理本法项下的任何罪行。"
    data = {"pairs": [pair(orig, "declarative", zh, "permission")]}
    assert build_verdict(data, orig, zh).passed
    # 原文义务、译文陈述（“应当”被省略）不报：陈述性 shall 译文常省略
    data = {"pairs": [pair(S1, "obligation", "组织向委员会通报数据泄露。", "declarative")]}
    assert build_verdict(data, S1, "组织向委员会通报数据泄露。").passed


def test_missing_sentence_is_flagged_as_omission():
    data = {"pairs": [pair(S1, "obligation", None, None)]}
    v = build_verdict(data, ORIG, "某些无关的译文内容。")
    assert not v.passed and v.errors[0].category == "omission"


def test_short_fragments_are_not_judged_as_omissions():
    orig = "Short title. " + S1
    data = {"pairs": [pair("Short title.", "declarative", None, None)]}
    assert build_verdict(data, orig, "x").passed


def test_garbled_source_is_not_blamed_on_the_translation():
    orig = "المادة :11 تمنح وزارة الصحة العامة ً عن اإلجازة. ً مقطوعا ً للشروط الواردة في هذا القانون وذلك"
    src = "ً عن اإلجازة. ً مقطوعا ً للشروط الواردة في هذا القانون وذلك"
    assert build_verdict({"pairs": [pair(src, "declarative", None, None)]}, orig, "译文").passed


def test_fabricated_spans_are_discarded_not_trusted():
    data = {
        "pairs": [
            # 原文片段在原文里根本没有：模型编造，丢弃
            pair(
                "A sentence the source never contained at all.", "prohibition", "译文", "permission"
            ),
            # 译文片段不在译文里：当作没找到对应内容，按漏译处理
            pair(S1, "obligation", "译文里没有这句话", "obligation"),
        ]
    }
    v = build_verdict(data, ORIG, GOOD)
    assert v.discarded == 1
    assert [e.category for e in v.errors] == ["omission"]


def test_issues_for_retry_name_the_evidence():
    data = {"pairs": [pair(S2, "prohibition", "任何人可以传输。", "permission")]}
    v = build_verdict(data, ORIG, "任何人可以传输。")
    text = "\n".join(v.issues())
    assert "语义对照核验" in text and "禁止" in text and "任何人可以传输" in text


@pytest.mark.asyncio
async def test_judge_returns_none_when_model_unavailable(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "llm_api_key", "")
    assert await tj.judge_translation(ORIG, GOOD, "en") is None


class _Resp:
    status_code = 200

    def __init__(self, content):
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _omission_verdict():
    long_src = "The Central Bank may undertake to provide foreign currency to repay financing."
    data = {"pairs": [pair(long_src, "permission", None, None)]}
    return long_src, build_verdict(data, long_src, "中央银行可承诺提供外汇，用于偿还融资。")


@pytest.mark.asyncio
async def test_a_suspected_omission_is_withdrawn_when_the_sentence_is_found_in_the_translation(
    monkeypatch,
):
    translated = "中央银行可承诺提供外汇，用于偿还融资。"
    src, verdict = _omission_verdict()
    assert not verdict.passed

    async def fake_post(payload, **_):
        return _Resp('{"found":[{"i":0,"tgt":"中央银行可承诺提供外汇，用于偿还融资"}]}')

    monkeypatch.setattr(tj, "post_model", fake_post)
    confirmed = await tj._confirm_omissions(verdict, translated)
    assert confirmed.passed


@pytest.mark.asyncio
async def test_the_omission_stands_when_the_model_finds_nothing_or_invents_a_fragment(monkeypatch):
    translated = "中央银行可承诺提供外汇，用于偿还融资。"
    _, verdict = _omission_verdict()
    for answer in (
        '{"found":[{"i":0,"tgt":null}]}',
        '{"found":[{"i":0,"tgt":"译文里没有的一句话"}]}',
    ):

        async def fake_post(payload, answer=answer, **_):
            return _Resp(answer)

        monkeypatch.setattr(tj, "post_model", fake_post)
        assert not (await tj._confirm_omissions(verdict, translated)).passed


@pytest.mark.asyncio
async def test_a_number_missing_from_the_matched_fragment_keeps_the_omission(monkeypatch):
    src = "The fee shall be 500 dirhams for each application filed with the Authority."
    verdict = build_verdict(
        {"pairs": [pair(src, "obligation", None, None)]}, src, "费用按每份申请缴纳。"
    )

    async def fake_post(payload, **_):
        return _Resp('{"found":[{"i":0,"tgt":"费用按每份申请缴纳"}]}')

    monkeypatch.setattr(tj, "post_model", fake_post)
    assert not (await tj._confirm_omissions(verdict, "费用按每份申请缴纳。")).passed
