# ruff: noqa: E501
"""The assistant beside the form: talks about the one question the user is filling in.

It explains that question (what the words mean, why it is asked, an example), and when the user says
their answer in their own words (“我们没有这个”“数据存一年”) it proposes the text for the box — the
user still adopts it; nothing is written by the assistant. What the platform does with data is the
fixed, checked statement; anything unrelated is declined in one sentence that leads back to the
question. Numbers, laws and claims of having done something that are not in the question or in what
the user said are refused, so the assistant cannot make them up.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.analysis.chat_advisor import PRIVACY, PRIVACY_ASK
from app.analysis.chat_intent import _NEGATIVE_SHORT, _UNKNOWN_SHORT
from app.analysis.document_narrative import _numbers
from app.analysis.llm import call_llm_json
from app.analysis.wording import items_not_questions

MAX_REPLY = 360
SYSTEM = (
    "你是合规文件填写页上的助手，用户正在填【当前这一项】。只围绕这一项回答，口语化中文，2–4 句，不用标题和列表。\n"
    "kind 只能是：\n"
    "- explain：用户在问这一项（什么意思、为什么要问、怎么填、举个例子、依据哪条法规）。用通俗的话解释词义和为什么要问；要举例时用【当前这一项】的“示例”（标明“例如”，不是用户的事实）；"
    "问依据时按【当前这一项】的“依据”回答，原样写法规名和条号，没有给出依据就说这一项暂无对应条文。\n"
    "- answer：用户在说自己的实际情况（含“没有/不涉及/不清楚”）。reply 用一句话复述你的理解；proposal 写成可以直接放进这一项的文字，只能用用户说过的内容，"
    "不得添加用户没说的措施、数字、机构、日期；用户说没有，就写“无”或这一项选项里的否定项。\n"
    "- off_topic：和这一项、和填合规文件都无关。reply 一句话委婉说明帮不上，再把话题带回这一项。\n"
    "用户提到“上一题”“上一项”“刚才那个”时，只按【上一项】回答；没有给出上一项就说你不确定指哪一项，不要猜。\n"
    "平台把要填的内容叫“填写项”，回复里一律说“这一项”“上一项”“填写项”，不用“题”字（不说“本题”“这道题”“题目”）。\n"
    "硬性要求：除“依据”里给出的法规名和条号外，不写别的法规名称、条文编号和处罚数额；示例不是用户的情况，不得写进 proposal；不宣称已经填写或保存（用户自己点“采用”）；不要反问用户与这一项无关的问题。\n"
    '只输出 JSON：{"kind":"explain|answer|off_topic","reply":"","proposal":""}'
)
CLAIMED = re.compile(r"已(经)?(帮您|帮你|为您|为你)?(填写|填好|填入|保存|记录|更新|写入)")


@dataclass
class Reply:
    text: str
    proposal: str = ""


def _basis_lines(question: dict) -> list[str]:
    """The articles on the question's card, as the assistant may cite them (same as the card)."""
    return [f"{c['law']}{c['article']}：{c.get('point', '')}" for c in question.get("basis") or []]


def _cites_ok(reply: str, basis: list[str]) -> bool:
    """Only the card's own laws and articles may be named in a reply."""
    laws = re.findall(r"《[^》]+》", reply)
    if any(not any(law in b for b in basis) for law in laws):
        return False
    for m in re.finditer(r"(《[^》]+》)?\s*(第[一二三四五六七八九十百零\d]+条)", reply):
        if not any(m.group(2) in b and (not m.group(1) or m.group(1) in b) for b in basis):
            return False
    return True


def _negative(question: dict) -> str:
    for c in question.get("choices") or []:
        if c in ("否", "无", "不含", "没有"):
            return c
    return "无"


async def answer(question: dict, message: str, context: dict, previous: dict | None = None) -> Reply:
    """``question``: {key,label,question,why,choices,value}; ``context``: route and business."""
    text = message.strip()
    if PRIVACY_ASK.search(text):
        return Reply(PRIVACY)
    short = text.strip("。.!！?？ ").lower()
    if short in _NEGATIVE_SHORT or short in ("我们没有", "我们没有这个", "没有这个", "我们不涉及", "不涉及这个"):
        value = _negative(question)
        return Reply(f"明白，那这一项可以写“{value}”。确认无误的话点“采用”。", value)
    if short in _UNKNOWN_SHORT:
        return Reply("没关系，暂时不确定可以先空着，或点“暂不确定”，文件里会标“待补充”，不影响先下载初稿。")
    keep = ("label", "question", "hint", "used_for", "choices", "value")
    basis = _basis_lines(question)
    current = {**{k: question.get(k) for k in keep}, "示例": question.get("example", ""), "依据": basis}
    payload = {"当前这一项": current, "上一项": {k: previous.get(k) for k in keep} if previous else None, "业务概况": context, "用户说": text}
    try:
        res = await call_llm_json(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            max_tokens=1200, temperature=0.2,
        )
    except Exception:  # noqa: BLE001 - 模型不可用：给这一项自带的说明，不让页面卡住
        why = f"为什么要问：{question['why']}" if question.get("why") else ""
        return Reply(f"这一项问的是：{question['question']}{why}。可以直接在框里写；暂时不确定就点“暂不确定”。")
    data = res.data if isinstance(res.data, dict) else {}
    kind = data.get("kind")
    reply = items_not_questions(str(data.get("reply") or "").strip())
    proposal = str(data.get("proposal") or "").strip() if kind == "answer" else ""
    allowed = _numbers(json.dumps(payload, ensure_ascii=False))
    if not reply or len(reply) > MAX_REPLY or CLAIMED.search(reply) or not _cites_ok(reply, basis):
        reply = f"这一项问的是：{question['question']}" + (f"为什么要问：{question['why']}" if question.get("why") else "")
    if _numbers(reply) - allowed:
        reply = f"这一项问的是：{question['question']}"
    if proposal and (_numbers(proposal) - _numbers(text) or len(proposal) > 1000):
        proposal = ""  # 代填的文字里出现了用户没说过的数字：不给
    example = str(question.get("example") or "").strip()
    if proposal and example and (proposal.strip() == example or example in proposal):
        proposal = ""  # 示例不是用户的情况，不能当答案
    if kind == "answer" and proposal and "采用" not in reply:
        reply += "确认无误的话点“采用”。"
    return Reply(reply, proposal)
