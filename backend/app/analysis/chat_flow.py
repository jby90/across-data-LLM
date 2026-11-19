# ruff: noqa: E501
"""The conversation flow: read what the user said (chat_intent), then do what it calls for.

Every kind of input has somewhere to go. Facts are recorded only when the user stated or corrected
them; questions about the law are answered from the library; questions about progress are answered
from the real state (collection, file review running in the background, the report); help is
answered to the point that was asked; and only talk that has nothing to do with the platform is
politely declined. Nothing is answered with a bare refusal, and nothing is guessed."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import attachment_jobs as jobs
from app.analysis import chat as C
from app.analysis import chat_law
from app.analysis import chat_reply as reply
from app.analysis.chat_intent import IntentCtx, Understanding, link_entities, understand
from app.analysis.document_basis import NAMES as _ALL_COUNTRY_NAMES
from app.analysis.models import AnalysisMessage, AnalysisTask
from app.analysis.workflow_engine import (
    COUNTRY_NAMES_ZH,
    EIGHT_DIMENSIONS_SPEC,
    SCENARIO_LABELS,
    country_name_zh,
    is_overseas_country,
    overseas_countries_map,
)

CAPABILITY = (
    "这个平台帮车企做汽车数据出境合规：在对话里把评估要用的信息问齐（目标国家、车型、功能、业务场景，"
    "再加采集了哪些数据、传到哪里、怎么取得用户同意、保存多久这几项），然后生成合规评估报告和 8 类合规文件"
    "（申报表、风险评估报告、合同、DPIA/LIA/TIA、准入材料清单等，有官方原表的按官方原表填写）；"
    "也可以上传公司现有的制度文件，对照目标国要求看缺什么；各国法规简报在左侧「法规知识库」。"
)
SCOPE_LINE = "我能帮的是汽车数据出境合规：问法规、把评估信息问齐并生成报告和合规文件、上传制度文件对照、读各国的国家合规简报。"
UNAVAILABLE = "我这边暂时没能理解这句话（服务繁忙），请稍后再发一次；也可以直接点选下面的选项继续。"
_SLOT_WHY = {
    "country_code": "先确定目标国家，才能找到适用的法规",
    "vehicle_model": "车型或车队用来界定评估的对象",
    "scenario_type": "不同功能涉及的数据活动不同，适用的要求也不同",
}
PLATFORM_POINTERS = (
    ("白皮书|简报|国家概况|国家合规", "各国的合规洞察简报在左侧「法规知识库」的“国家合规简报”里，选国家就能读，也可以下载 PDF / Word。"),
    ("表格|表单|F118|申报表", "官方申报表在左侧「官方表格」里，可以在线填写并导出官方原表。"),
    ("准入|备案|认证|许可|材料清单", "各国的市场准入事项、办理环节和材料清单在左侧「海外属地准入」里，可以按事项建项目、生成材料清单。"),
    ("更新|变更|通知|订阅", "法规更新和影响提示在左侧「更新通知」里。"),
    ("法规|条文|条款|术语", "法规原文、译文和术语释义在左侧「法规知识库」里查。"),
    ("新建|另一个国家|换个国家|别的国家|其他国家|新的评估|重新开始|从头", "要换国家或业务，点对话列表上方的「+ 新对话」重新开始；要生成合规文件，在上方「生成合规文件」里新建项目。当前这个对话会原样保留，随时可以回来接着做。"),
)
LAW_POINTER = {
    "cross_border": "数据跨境传输合规路径与前置条件",
    "consent": "用户授权、告知、同意的合规要求",
    "retention": "数据存储、留存、销毁时效与规范",
    "penalty": "违规行为分级、处罚标准、监管追责机制",
    "admission": "属地市场准入认证、申报材料、流程节点要求",
    "data_category": "汽车核心数据定义与分类",
    "general": "属地数据法规体系",
}
# --- state the model is shown ------------------------------------------------------------

def _slot_catalog(task: AnalysisTask, countries: dict[str, str] | None = None) -> dict[str, dict]:
    facts = task.facts_json or {}
    cmap = countries or COUNTRY_NAMES_ZH
    has_c = is_overseas_country(task.country_code)
    out: dict[str, dict] = {
        "country_code": {
            "describe": "目标国家（" + "/".join(cmap.values()) + "）",
            "filled": has_c,
            "value": country_name_zh(task.country_code) if has_c else "",
        },
        "vehicle_model": {"describe": "受评车型或车队名称", "filled": bool((task.vehicle_model or "").strip()), "value": (task.vehicle_model or "")[:40]},
        "scenario_type": {
            "describe": "功能：" + "/".join(SCENARIO_LABELS.values()),
            "filled": task.scenario_type in SCENARIO_LABELS,
            "value": SCENARIO_LABELS.get(task.scenario_type, ""),
        },
    }
    for key, spec in C.FACT_SPEC.items():
        value = str(facts.get(key) or "").strip()
        out[key] = {"describe": spec["question_zh"], "filled": len(value) >= 4, "value": value[:40]}
    return out


def _file_state(f) -> str:
    if not f.readable:
        return "unreadable"
    return {"running": "reviewing", "done": "reviewed", "failed": "failed"}.get(jobs.effective_state(f) or "", "not_started")


async def _context(session: AsyncSession, task: AnalysisTask, slot: str | None, stage: str, content: str) -> IntentCtx:
    recent = await reply.recent_conversation(session, task, limit=8, chars=300)
    files = [{"name": f.filename, "state": _file_state(f)} for f in await jobs.sent_files(session, task)]
    users = await session.scalar(select(func.count(AnalysisMessage.id)).where(AnalysisMessage.task_id == task.id, AnalysisMessage.role == "user"))
    avail = await overseas_countries_map(session)
    countries = [task.country_code] if is_overseas_country(task.country_code) else (list(C.countries_in(content)) or list(avail))
    question = C.question_for(slot, countries=avail)[0].split("\n")[0] if slot else ""
    return IntentCtx(
        stage=stage, slot=slot, question=question, slots=_slot_catalog(task, avail), files=files, recent=recent,
        entities=await link_entities(session, content, countries), first_message=(users or 0) <= 1,
    )


# --- small replies ---------------------------------------------------------------------------

def _platform_reply(content: str) -> str:
    # 提到国家且带“再/重新/换/另一/下一”之类：想的是另开一份评估（不论怎么措辞），指到新建评估
    if C.countries_in(content) or C.match_country(content):
        if re.search(r"再|重新|换|另|下一个|别的|其他", content):
            return PLATFORM_POINTERS[-1][1]
    for words, text in PLATFORM_POINTERS:
        if re.search(words, content):
            return text
    return "这个功能在平台的其他页面里，左侧菜单可以找到：法规知识库、海外属地准入、官方表格、更新通知。"


def _help_fallback(slot: str | None, repeated: bool) -> str:
    why = (C.FACT_SPEC.get(slot or "") or {}).get("why_needed") or _SLOT_WHY.get(slot or "", "")
    if repeated:
        tail = f"这一项问的原因：{why}。" if why else ""
        return f"刚才已经说明过怎么用了。{tail}拿不准的可以直接回答“不确定”，我会如实记下，不会替你编。"
    asks = "你可以直接这样问：" + "；".join(f"“{a}”" for a in reply.EXAMPLE_ASKS[:3]) + "。"
    return "没关系，我来说明。" + SCOPE_LINE + asks + "拿不准的可以直接回答“不确定”，我会如实记下，不会替你编。"


def _slot_title(slot: str) -> str:
    return C.SLOT_LABEL.get(slot, slot)


def progress_text(task: AnalysisTask) -> str:
    missing = C._missing_slots(task)
    total = 3 + len(C.FACT_KEYS)
    head = f"目前已收集 {total - len(missing)}/{total} 项信息"
    if not missing:
        return head + "，信息已齐，正在生成。"
    shown = "；".join(_slot_title(m) for m in missing[:6]) + ("等" if len(missing) > 6 else "")
    return f"{head}，还差 {len(missing)} 项：{shown}。信息收齐后，我会自动生成评估报告和 8 类合规文件。"


def _cannot_be_unknown(task: AnalysisTask, slot: str, last_assistant: str) -> tuple[bool, str]:
    """(recorded, note): country, vehicle and function are what the assessment is about, so “不清楚”
    is met with help — not a refusal. A vehicle still undecided after that is recorded as pending."""
    if slot == "vehicle_model":
        if "写个大概" in last_assistant:
            task.vehicle_model = "待定车型（用户暂不确定）"
            return True, ""
        return False, "车型还没定也没关系，写个大概就行，比如“某品牌纯电 SUV”“L4 自动驾驶测试车队”。实在没定，再回我一次“不确定”，我先记作待定。"
    if slot == "country_code":
        return False, "目前支持" + "、".join(COUNTRY_NAMES_ZH.values()) + "等已收录法规的国家。你的车辆数据主要会去哪个？还没定的话，可以先告诉我做什么业务，我帮你对应。"
    return False, "没关系，选最接近的一项就行，选项在下面。"


def _apply(task: AnalysisTask, slot: str | None, content: str, u: Understanding, last_assistant: str) -> tuple[list[str], list[str]]:
    """Record what the user stated or corrected. Returns (slots recorded, notes for the reply)."""
    applied: list[str] = []
    notes: list[str] = []
    unfilled = C._unfilled_slots(task)
    for key, snippet in u.facts.items():
        if key in unfilled or key == slot:
            if key in C.FACT_SPEC and len(snippet) < 6 and len(content) >= 6:
                snippet = content[:1000]  # “60天”太短：留下用户整句话，才有上下文
            err = C.apply_text(task, key, snippet, min_len=2)
            if err is None:
                applied.append(key)
            elif key == slot:
                notes.append(err)
    # 已经填过的项，用户又说了：业务事实把补充追加在后面；国家/车型/功能与已填的不同则问一句，绝不悄悄丢掉
    for key, snippet in u.facts.items():
        if key in applied or key in u.corrections or key == slot or key in unfilled:
            continue
        if key in C.FACT_SPEC:
            if u.answer_kind == "answer":
                continue  # 这句话是在回答当前问题：夹在长回答里的词不能追加到别的已确认事实上
            old = str((task.facts_json or {}).get(key) or "").strip()
            if old and snippet not in old:
                facts = dict(task.facts_json or {})
                facts[key] = f"{old}（用户补充：{snippet}）"[:1000]
                task.facts_json = facts
                applied.append(key)
        elif key == "country_code" and C.match_country(snippet) not in (None, task.country_code):
            mc = C.match_country(snippet)
            notes.append(f"你前面选的目标国家是{country_name_zh(task.country_code)}，这次提到{snippet}。要改的话回复“改成{mc and country_name_zh(mc)}”；如果是想另外评估一个国家，点「+ 新对话」，当前这份会保留。")
        elif key == "vehicle_model" and snippet.strip() != (task.vehicle_model or "").strip():
            notes.append(f"你前面说的车型是“{task.vehicle_model}”，这次提到“{snippet}”。要改的话回复“车型改成{snippet}”。")
        elif key == "scenario_type" and C.match_scenario(snippet) not in (None, task.scenario_type):
            notes.append("你前面选的功能和这次说的不一样。要改的话回复“功能改成……”；想另外评估一个场景，点「+ 新对话」。")
    for key, snippet in u.corrections.items():
        if key in applied:
            continue
        if key in C.FACT_SPEC and len(snippet) < 6 and len(content) >= 6:
            snippet = content[:1000]
        old = str((task.facts_json or {}).get(key) or "").strip()
        if key in C.FACT_SPEC and old and old not in snippet:
            snippet = f"{old}（用户后来更正：{snippet}）"[:1000]  # 保留原话和更正，都留下依据
        err = C.apply_text(task, key, snippet, min_len=2)
        if err is None:
            applied.append(key)
        else:
            notes.append(err)
    if slot and slot not in applied and "provide_info" in u.intents:
        kind = u.answer_kind
        if kind in ("negative", "unknown"):
            if slot in C.FACT_SPEC:
                C._record_fact(task, slot, content, kind)
                applied.append(slot)
            else:
                done, note = _cannot_be_unknown(task, slot, last_assistant)
                if done:
                    applied.append(slot)
                else:
                    notes.append(note)
        elif kind == "answer":
            err = C.apply_text(task, slot, content, min_len=2)  # 模型已确认这是在回答这一项：“就SCC吧”也是有效回答
            if err is None:
                applied.append(slot)
            else:
                notes.append(err)
    elif slot in applied and set(u.facts) == {slot} and slot in C.FACT_SPEC and len(content) <= 1000 and not u.corrections:
        facts = dict(task.facts_json or {})
        facts[slot] = content  # 只抽出当前问题一个槽位：整段话都属于这个问题，保留原话
        task.facts_json = facts
    return applied, notes


# --- what each intent gets -----------------------------------------------------------------

def _mmss(seconds: int) -> str:
    return f"{seconds // 60} 分 {seconds % 60} 秒" if seconds >= 60 else f"{seconds} 秒"


async def _latest_conclusion(session: AsyncSession, files: list) -> str | None:
    from app.analysis.attachments import AttachmentReview

    row = await session.scalar(
        select(AttachmentReview).where(AttachmentReview.attachment_id.in_([f.id for f in files])).order_by(AttachmentReview.created_at.desc()).limit(1)
    )
    if row is None:
        return None
    s = row.summary or {}
    text = f"对照已经完成：已涵盖 {s.get('covered', 0)} 项，部分涵盖 {s.get('partial', 0)} 项，未涵盖 {s.get('missing', 0)} 项。"
    if s.get("gaps"):
        text += "最突出的缺口：" + "；".join(f"{i}. {g}" for i, g in enumerate(s["gaps"], 1)) + "。"
    return text + "逐项差距和建议，点上面文件旁的“查看对照”。"


async def _status_text(session: AsyncSession, task: AnalysisTask, u: Understanding, stage: str) -> str:
    """Progress from the facts of this conversation, not from the wording of the question."""
    files = [f for f in await jobs.sent_files(session, task) if f.readable]
    running = [f for f in files if jobs.effective_state(f) == "running"]
    failed = [f for f in files if jobs.effective_state(f) == "failed"]
    country = country_name_zh(task.country_code) if is_overseas_country(task.country_code) else "目标国家"
    topic = u.topic
    review_line = ""
    if running:
        names = "、".join(f"《{f.filename}》" for f in running[:3])
        secs = max(jobs.elapsed_seconds(f) for f in running)
        review_line = f"{names}还在对照{country}的要求，已经 {_mmss(secs)}，一般 2–3 分钟（文件多的话更久），完成后我会把结论直接发在这里。"
    elif failed:
        why = failed[0].review_error or "服务暂时不可用"
        review_line = f"上一次文件对照没有完成（{why}）。说一声“重新对照”，或点文件旁的“重试”就会重新开始。"
    if topic == "files" or (topic == "none" and (running or failed)):
        if review_line:
            return review_line
        if not files:
            return "还没有收到文件。点输入框旁的「上传文件」选好文件，再发一句话，我就开始对照。"
        done = await _latest_conclusion(session, files)
        if done:
            return done
        return "文件已收到，还没开始对照" + ("" if is_overseas_country(task.country_code) else "：我还不确定针对哪个国家，告诉我一下就马上开始") + "。"
    if topic == "report" or stage == "completed":
        if stage == "completed":
            return "评估报告已经生成，在对话上方可以查看，也能导出 Word / PDF；8 类合规文件在报告页的“合规文件”里。"
        return "评估报告还没生成。" + progress_text(task)
    return (review_line + "\n" if review_line else "") + progress_text(task)


def _last_law_country(ctx: IntentCtx) -> str | None:
    """The country of the law answer the assistant gave most recently (its sources name the country)."""
    for m in reversed(ctx.recent):
        if m["role"] != "assistant":
            continue
        if "依据：" in m["text"] or "库里收录的条文" in m["text"] or "目前收录的该国法规" in m["text"] or "库里没有检索到" in m["text"] or "没有找到能直接回答" in m["text"]:
            hit = C.countries_in(m["text"])
            if hit:
                return max(hit, key=hit.get)
            for code, name in _ALL_COUNTRY_NAMES.items():
                if code not in ("CN", "ZZ", "--") and len(name) >= 2 and name in m["text"]:
                    return code
    return None


async def _law_text(session: AsyncSession, task: AnalysisTask, u: Understanding, content: str, ctx: IntentCtx) -> str:
    avail = await overseas_countries_map(session)
    by_name = {name: c for c, name in {**_ALL_COUNTRY_NAMES, **avail}.items() if c not in ("CN", "ZZ", "--")}
    code = (
        C.match_country(content)
        or by_name.get(u.law_country)
        or (task.country_code if is_overseas_country(task.country_code) else None)
    )
    if code is None:
        code = _last_law_country(ctx)  # 上文刚答过某国的法规：省略国家的追问（“还有吗”）沿用它
    question = u.standalone or content  # 省略句用结合上文改写后的完整问题去检索
    if code is None:
        return "你想问哪个国家的规定？目前支持" + "、".join(avail.values()) + "，说一下国家我就按该国法规回答。"
    got = await chat_law.answer(session, code, question, u.law_topic, ctx.entities)
    if got:
        return got
    where = LAW_POINTER.get(u.law_topic, "属地数据法规体系")
    return (
        f"目前收录的法规里没有找到能直接回答这个问题的条文，我不凭印象回答。{country_name_zh(code)}的相关要求可以先看「法规知识库→国家合规简报」里的“{where}”，"
        "每条都带条文出处；评估报告里也会逐项对照法条。"
    )


async def _respond(session: AsyncSession, task: AnalysisTask, intent: str, u: Understanding, content: str, ctx: IntentCtx, slot: str | None, stage: str) -> str | None:
    if intent == "ask_privacy":
        from app.analysis.chat_advisor import PRIVACY

        return PRIVACY
    if intent == "ask_status":
        return await _status_text(session, task, u, stage)
    if intent == "generate":
        if stage == "completed":
            return "评估报告已经生成，在对话上方可以查看和导出；8 类合规文件在报告页的“合规文件”里。"
        return "报告和 8 类合规文件要等信息收齐后生成。" + progress_text(task)
    if intent == "ask_law":
        return await _law_text(session, task, u, content, ctx)
    if intent == "file_request":
        files = await jobs.sent_files(session, task)
        return None if files else "还没有收到文件。点输入框旁的「上传文件」选好文件，再发一句话（比如“帮我对照一下”），我就开始对照。"
    return None


async def _files_step(session: AsyncSession, task: AnalysisTask, content: str, new_files: int, u: Understanding) -> list[str]:
    """Settle the country from the message or the documents, and start the comparison when there is
    something to compare. A failed run is restarted only when the user asks for it."""
    files = [f for f in await jobs.sent_files(session, task) if f.readable]
    if not files:
        return []
    asked = new_files > 0 or "file_request" in u.intents
    notes: list[str] = []
    if not is_overseas_country(task.country_code) and (asked or any(jobs.effective_state(f) is None for f in files)):
        code = C.match_country(content) or C.country_from_documents([f.text for f in files])
        if code:
            task.country_code = code
            cname = country_name_zh(code)
            notes.append(f"读了你的文件，内容主要涉及{cname}，目标国家先记为{cname}，不对请告诉我。")
        elif asked:
            notes.append("文件我读了，但没能从内容里看出针对哪个国家。请告诉我要对照哪个国家，选好后马上开始。")
    if any(jobs.effective_state(f) == "failed" for f in files) and not asked:
        return notes
    started = await jobs.start_review(session, task)
    if started:
        names = "、".join(f"《{f.filename}》" for f in started[:3]) + ("等" if len(started) > 3 else "")
        notes.append(f"开始对照{names}和{country_name_zh(task.country_code)}的要求，约需 2–3 分钟，完成后我把结论发在这里。")
    return notes


async def _nothing_recorded(session: AsyncSession, task: AnalysisTask, content: str, u: Understanding, ctx: IntentCtx, slot: str | None, stage: str) -> str:
    """The user said something about their business that does not fit any open item (or fits none we
    can use): say so kindly and carry on, with the conversation in view."""
    _, options = C.question_for(slot) if slot else ("", [])
    why = (C.FACT_SPEC.get(slot or "") or {}).get("why_needed") or _SLOT_WHY.get(slot or "", "")
    text = await reply.compose(
        session, task, ["provide_info"], content, ctx, stage=stage, progress=progress_text(task), pointer=_platform_reply(content),
        scope=SCOPE_LINE, capability=CAPABILITY, why_asked=why, example_answers=options, nothing_recorded=True,
    )
    return text or "这句话里的内容我暂时归不到评估要问的哪一项，先不记录，不影响后面。"


def _soft_fallback(intent: str, u: Understanding, content: str, stage: str, slot: str | None, repeated: bool) -> str:
    if intent == "help":
        return _help_fallback(slot, repeated)
    if intent == "ask_platform":
        return _platform_reply(content)  # 含“换国家重新评估”：指到「+ 新对话」，当前评估不改
    if intent == "smalltalk":
        if stage == "completed":
            return "不客气。" + C._AFTER_REPORT_HELP
        return ("你好！" + SCOPE_LINE) if u.greeting else "不客气，有不清楚的随时问我。"
    if intent == "feedback":
        return "抱歉，刚才的回复没有答到点上。你直接告诉我想问的具体问题，我重新回答；想从头开始的话，点对话列表上方的「+ 新对话」。"
    if intent == "off_topic":
        return "这个问题超出了我的范围。" + SCOPE_LINE
    return "我没完全明白你的意思。你可以直接回答上面的问题，也可以问某国的法规、看进度，或上传文件让我对照。"


async def _soft_reply(session: AsyncSession, task: AnalysisTask, soft: list[str], u: Understanding, content: str, ctx: IntentCtx, slot: str | None, stage: str, recorded: list[str] | None = None) -> str:
    _, options = C.question_for(slot) if slot else ("", [])
    why = (C.FACT_SPEC.get(slot or "") or {}).get("why_needed") or _SLOT_WHY.get(slot or "", "")
    text = await reply.compose(
        session, task, soft, content, ctx, stage=stage, progress=progress_text(task), pointer=_platform_reply(content),
        scope=SCOPE_LINE, capability=CAPABILITY, why_asked=why, example_answers=options, recorded=recorded,
        question_follows=bool({"help", "unclear"} & set(soft)),
    )
    if text:
        return text
    repeated = any("没关系，我来说明" in m["text"] or "刚才已经说明" in m["text"] for m in ctx.recent if m["role"] == "assistant")
    return " ".join(_soft_fallback(i, u, content, stage, slot, repeated) for i in soft)


# --- the flow --------------------------------------------------------------------------------

async def _pending_half(session: AsyncSession, task: AnalysisTask) -> tuple[str, str] | None:
    """(item, the question about its missing half) when the assistant's last message asked for it."""
    row = (
        await session.execute(
            select(AnalysisMessage.slot, AnalysisMessage.content)
            .where(AnalysisMessage.task_id == task.id, AnalysisMessage.role == "assistant")
            .order_by(AnalysisMessage.seq_no.desc())
            .limit(1)
        )
    ).first()
    if row and row[0] and row[0].endswith("+") and row[0][:-1] in C.FACT_SPEC:
        return row[0][:-1], row[1]
    return None


async def converse(session: AsyncSession, task: AnalysisTask, content: str, start: Any, new_files: int, stage: str) -> None:
    """One user message in the collecting stage ("collecting") or after the report ("completed")."""
    half = await _pending_half(session, task) if stage == "collecting" else None
    half_key = half[0] if half else None
    slot = half_key or (C.next_slot(task) if stage == "collecting" else None)
    ctx = await _context(session, task, slot, stage, content)
    if half:
        ctx.question = half[1]  # 当前在补的是这一项还差的那一半
    u = await understand(content, ctx, match_country=C.match_country, match_scenario=C.match_scenario)
    last_assistant = " ".join(m["text"] for m in ctx.recent[-4:] if m["role"] == "assistant")  # 先解释、再单独发问题：两条都要看
    if u.ok and u.title and task.title == "新对话" and not task.title_custom:
        task.title = u.title  # 第一句有实质内容：对话不再叫“新对话”（用户随时可以改名）
    if not u.ok:
        await C.add_message(session, task.id, "assistant", UNAVAILABLE)
        if slot:
            await C._ask(session, task, slot)
        await session.commit()
        return

    applied: list[str] = []
    notes: list[str] = []
    if {"provide_info", "correct_info"} & set(u.intents):
        if stage == "collecting" and half_key and "provide_info" in u.intents and not u.corrections:
            old = str((task.facts_json or {}).get(half_key) or "").strip()
            facts = dict(task.facts_json or {})
            facts[half_key] = f"{old}（补充：{content.strip()}）"[:1000]
            task.facts_json = facts
            applied = [half_key]
        elif stage == "collecting":
            applied, more = _apply(task, slot, content, u, last_assistant)
            notes += more
        else:
            await _after_report_info(session, task, content, u, start)
            return
    notes += await _files_step(session, task, content, new_files, u)
    soft = [i for i in u.intents if i in reply.SOFT]
    for intent in u.intents:
        if intent in ("provide_info", "correct_info") or intent in reply.SOFT:
            continue
        text = await _respond(session, task, intent, u, content, ctx, slot, stage)
        if text:
            notes.append(text)
    if soft:
        notes.append(await _soft_reply(session, task, soft, u, content, ctx, slot, stage, [C._brief(s, task) for s in applied]))

    if stage != "collecting":
        await C.add_message(session, task.id, "assistant", "\n".join(notes) if notes else C._AFTER_REPORT_HELP)
        await session.commit()
        return

    nothing = False
    if not applied and not notes:
        notes.append(await _nothing_recorded(session, task, content, u, ctx, slot, stage))
        nothing = True
    text = "\n".join(dict.fromkeys(n for n in notes if n))
    C._refresh_state(task)
    if not applied:
        # 什么时候把当前问题再发一遍：用户求助/要生成/没看懂/这句话没对应任何一项、或还差国家才能对照文件；
        # 问进度、致谢、抱怨、问法规、问平台功能、无关闲聊之后不催，让用户自己接着说
        reask = nothing or bool({"help", "generate", "unclear", "provide_info", "correct_info"} & set(u.intents)) or any("没能从内容里看出" in n for n in notes)  # 没记下来的回答（没认出国家、太含糊……）也要再问
        if "ask_law" in u.intents and not reask and slot:
            text += "\n" + f"评估这边接着要确认的是「{_slot_title(slot)}」，方便时告诉我。"
        await C.add_message(session, task.id, "assistant", text)
        if reask and slot:
            if half:
                await C.add_message(session, task.id, "assistant", half[1], [], f"{half_key}+")
            else:
                await C._ask(session, task, slot)
        await session.commit()
        return
    corrected = bool(u.corrections) and set(applied) <= set(u.corrections)
    ack = ("已更新：" if corrected else "已记录：") + "；".join(C._brief(s, task) for s in applied)
    follow = C.next_slot(task)
    half_now = next((k for k in applied if k in u.partial and k in C.FACT_SPEC and not half_key), None)
    if half_now is not None:
        await C.add_message(session, task.id, "assistant", ack + (("\n" + text) if text else ""))
        missing = u.partial[half_now].rstrip("。；;，, ")
        await C.add_message(session, task.id, "assistant", f"这一项我先记下了，还差：{missing}。你说一下就行，不确定的话回复“不确定”。", [], f"{half_now}+")
        task.status = "collecting_facts"
        await session.commit()
        return
    if follow is not None:
        await C.add_message(session, task.id, "assistant", ack + (("\n" + text) if text else ""))
        await C._ask(session, task, follow)
        task.status = "collecting_facts"
        await session.commit()
        return
    await C.add_message(session, task.id, "assistant", f"{ack}\n\n信息已齐备，正在查找适用法规并生成合规报告。")
    task.status = "ready_to_run"
    await session.commit()
    await start(session, task)


RUNNING_NOTE = "评估正在进行中，通常需要几分钟，进度实时显示在下方；完成后报告会出现在这里。"


async def converse_while_running(session: AsyncSession, task: AnalysisTask, content: str, new_files: int) -> None:
    """The assessment is running. The user can still ask about the law, the platform, the progress or
    say thanks; only a statement about the business (which would change what is being assessed) is held
    back until the report is out."""
    ctx = await _context(session, task, None, "running", content)
    u = await understand(content, ctx, match_country=C.match_country, match_scenario=C.match_scenario)
    parts: list[str] = []
    if u.ok:
        for intent in u.intents:
            if intent in ("provide_info", "correct_info", "generate"):
                continue
            if intent in reply.SOFT:
                continue
            text = await _respond(session, task, intent, u, content, ctx, None, "running")
            if text:
                parts.append(text)
        soft = [i for i in u.intents if i in reply.SOFT]
        if soft:
            parts.append(await _soft_reply(session, task, soft, u, content, ctx, None, "running"))
        if {"provide_info", "correct_info"} & set(u.intents):
            parts.append("这条业务信息我先记在心里，评估跑完后，你说“把……改成……”，我会只重算受影响的部分。")
    parts = [p for p in dict.fromkeys(parts) if p]
    parts.append(RUNNING_NOTE if not parts else "（" + RUNNING_NOTE + "）")
    await C.add_message(session, task.id, "assistant", "\n".join(parts))
    await session.commit()


async def _after_report_info(session: AsyncSession, task: AnalysisTask, content: str, u: Understanding, start: Any) -> None:
    """After the report: a change to a business fact updates it and re-runs only what it touches.
    Country, vehicle and function are what the assessment is about; changing them is a new assessment."""
    given = {**u.facts, **u.corrections}
    changed = {k: (content if len(given) == 1 and len(content) <= 1000 else v) for k, v in given.items() if k in C.FACT_SPEC and len(v) >= 6}
    about = [k for k in given if k in ("country_code", "vehicle_model", "scenario_type")]
    if not changed:
        text = "换国家、车型或功能属于新的评估，请点对话列表上方的「+ 新对话」；当前这份报告和对话会原样保留。" if about else C._AFTER_REPORT_HELP
        await C.add_message(session, task.id, "assistant", text)
        await session.commit()
        return
    facts = dict(task.facts_json or {})
    facts.update({k: v[:1000] for k, v in changed.items()})
    task.facts_json = facts
    affected = sorted({C.FACT_SPEC[k]["dimension_code"] for k in changed})
    checkpoint = dict(task.analysis_checkpoint_json or {})
    if checkpoint.get("results"):
        checkpoint["resume_dimensions"] = affected
        task.analysis_checkpoint_json = checkpoint
        scope = f"只重新分析受影响的方面（{'、'.join(affected)}），其余方面沿用已核验的结果"
    else:
        task.analysis_checkpoint_json = {}
        scope = "这份报告没有保留中间结果，需要整体重新分析"
    C._refresh_state(task)
    titles = dict(EIGHT_DIMENSIONS_SPEC)
    labels = f"{len(changed)} 项业务信息（涉及：{'、'.join(titles.get(c, c) for c in affected)}）"
    await C.add_message(session, task.id, "assistant", f"已更新{labels}。{scope}，完成后会生成新的报告版本，原版本保留。")
    await session.commit()
    await start(session, task)
