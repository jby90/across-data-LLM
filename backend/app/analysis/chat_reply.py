# ruff: noqa: E501
"""Writing the assistant's reply to what is not a business fact: asking for help, small talk, a
complaint, something it did not understand, something off topic, a question about another part of
the platform.

One composer, with the whole conversation in view (what was said, what was already explained, what
is on screen, what is still missing), so the reply answers the point that was asked, follows up on
the previous turn and does not say the same thing twice. Anything factual (progress, files, laws)
is computed by the program and handed over as facts; the model only phrases. Replies that carry a
figure or a legal reference that is not in the facts are discarded and a plain fallback is used."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import chat_intent
from app.analysis import document_narrative as nar
from app.analysis.llm import call_llm_json
from app.analysis.models import AnalysisMessage, AnalysisTask
from app.analysis.wording import items_not_questions
from app.core.config import get_settings

SOFT = ("help", "smalltalk", "feedback", "unclear", "off_topic", "ask_platform")
MAX_CHARS = 460

EXAMPLE_ASKS = (
    "我们想把一款 L4 自动驾驶 SUV 拿到新加坡做公共道路测试，需要满足哪些数据合规要求？",
    "摩洛哥传数据出境要满足什么条件？",
    "我们有一份数据管理制度，帮我对照目标国看缺什么（先点「上传文件」）",
    "现在到哪一步了，还差什么？",
)

SYSTEM = (
    "你是汽车数据出境合规平台里的对话助手，正在回应用户的一句话。读 conversation（最近的对话，最后一条是用户刚说的），"
    "结合 state，像一个懂行、耐心的同事那样回答，口语化中文，不用标题和列表，不超过 230 字。\n"
    "先弄清用户这句话在问什么，只答那一点；如果它是接着上一句追问（“那这个呢”“什么意思”），按上一句的内容答。\n"
    "intents 里有：\n"
    "- help：分三种。①问“该问什么/能问什么/你能做什么/从哪开始/不懂业务”：用两三句说清这里能做什么（platform），再给两三句"
    "用户可以直接照着发的话（只能从 example_asks 里选，原样引用，用引号），不要去解释当前这一项的细节；②问某个词是什么意思："
    "用一两句通俗的话说明它在汽车数据业务里指什么，再带一句为什么要问当前这一项；③问当前这一项怎么填、要例子：解释这一项问什么、"
    "为什么要问，借用 example_answers 说怎么答。\n"
    "平台把要填的内容叫“填写项”，回复里一律说“这一项”“填写项”，不用“题”字（不说“本题”“这道题”“题目”）。\n"
    "- provide_info 且 nothing_recorded 为真：用户说了自己业务的情况，但没有对应到现在要问的哪一项（或评估用不上，如车队规模），没有记录。"
    "用一句话说你的理解（例如“车队规模这个评估用不上，我先不记”），不要让用户觉得被忽略或答错了，然后自然地回到当前问题。\n"
    "- smalltalk：礼貌简短回应（用户在致谢/打招呼），不要再长篇介绍。\n"
    "- feedback：用户在抱怨你答得不对。先真诚道歉一句，用自己的话说出你看到的问题（对照 conversation：他问了什么、你答了什么），"
    "然后请他把想问的说具体一点。不要辩解。\n"
    "- unclear：没看懂。说你没明白，并给两三个他可能想做的事（问法规、看进度、传文件对照）。\n"
    "- off_topic：一句话说明这个不在范围内，说能帮什么（scope）。不要说教。\n"
    "- ask_platform：用 pointer 告诉他在哪里，一两句。\n"
    "硬性要求：question_follows 为真时，当前问题会紧接着显示在你这段话下面，不要复述它；为假时不会再显示问题，你不用提它、不要催用户回答（用户可能只是在等结果或只是随口问问），最多在确有帮助时用一句自然的话带一下；已经说过的话（conversation 里 assistant 说过的）"
    "不要整段再说一遍；不得写任何法律规定、法规名称、数字、期限、罚款金额，拿不准就说“评估时会按法规条文核对”；"
    "不要编造平台没有的功能。\n"
    "你不能宣称做过任何动作：不要说“已记录”“已更新”“已按某国评估”“已改成”“已帮你重新生成”（记没记、改没改、生成没生成，都由程序在别处另说，facts 里的 recorded 才是事实）。"
    "想表示会做某事，只能说“你回复……我就……”这种怎么做，不要说已经做了。\n"
    "feedback 的道歉要对得上 conversation：只道歉 conversation 里确实发生过的问题；用户指出你说错了某件事，而 conversation 里你根本没说过那件事时，"
    "不要承认“我说错了”，如实说“我这边没有找到我前面讲过这一点”，请他把原文或想问的发来，你再核对。\n"
    '只输出 JSON：{"reply":""}。'
)


async def recent_conversation(session: AsyncSession, task: AnalysisTask, limit: int = 8, chars: int = 300) -> list[dict]:
    rows = (
        await session.execute(
            select(AnalysisMessage.role, AnalysisMessage.content)
            .where(AnalysisMessage.task_id == task.id)
            .order_by(AnalysisMessage.seq_no.desc())
            .limit(limit)
        )
    ).all()
    return [{"role": r, "text": t[:chars]} for r, t in reversed(rows)]


async def compose(
    session: AsyncSession,
    task: AnalysisTask,
    intents: list[str],
    content: str,
    ctx: chat_intent.IntentCtx,
    *,
    stage: str,
    progress: str,
    pointer: str,
    scope: str,
    capability: str,
    why_asked: str,
    example_answers: list[str],
    nothing_recorded: bool = False,
    recorded: list[str] | None = None,
    question_follows: bool = True,
) -> str | None:
    """The reply text, or None when the model is unavailable or its text failed the checks
    (the caller then uses its plain fallback)."""
    if not get_settings().llm_api_key:
        return None
    conversation = await recent_conversation(session, task)
    facts: dict[str, Any] = {
        "intents": intents,
        "user_said": content,
        "conversation": conversation,
        "state": {"stage": stage, "progress": progress, "files": ctx.files},
        "platform": capability,
        "scope": scope,
        "example_asks": list(EXAMPLE_ASKS),
        "pointer": pointer,
        "current_question": ctx.question,
        "why_asked": why_asked,
        "example_answers": example_answers[:4],
        "nothing_recorded": nothing_recorded,
        "recorded": recorded or [],
        "question_follows": question_follows,
    }
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}]
    try:
        res = await call_llm_json(messages, max_tokens=900, temperature=0.3)
    except Exception:  # noqa: BLE001 - 模型不可用：调用方用固定回复，对话不卡住
        return None
    text = items_not_questions(str((res.data or {}).get("reply") or "").strip())
    if not text or len(text) > MAX_CHARS:
        return None
    if nar._numbers(text) - nar._numbers(json.dumps(facts, ensure_ascii=False)):
        return None  # 出现了事实里没有的数字
    if re.search(r"第\s*\d+\s*条|条文\s*\d|\bclauses?\b|\bcites?\b|\bintents?\b", text, re.I):
        return None
    if re.search(r"已(经)?(帮您|帮你|为您|为你)?(记录|记下|更新|改成|修改|重新生成|生成了|按.{1,6}评估|切换)|这轮评估就按|已把.{0,12}(改|换)成", text):
        return None  # 记没记、改没改由程序另说，撰写文字不许宣称做过动作
    question = ctx.question.strip()
    if question_follows and question and len(question) > 12 and question in text:
        return None  # 把下面马上会显示的问题又念了一遍
    return text
