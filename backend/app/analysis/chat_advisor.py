# ruff: noqa: E501
"""The consulting assistant: a conversation that answers, guides, compares files and — when asked —
produces the compliance documents, without chasing the user for information.

New conversations (kind "advice") work like the consulting window of any good website: a one-line
greeting and the input box, nothing to click first. What the user says decides what happens:

- a question about the law is answered from the current clauses in the library (with references);
- “帮我把文件做出来” creates a documents project from what the user has said (route and business read
  from their own words), starts reading the same words for suggested answers, and links to it; if the
  route is not in what was said, one plain question asks for it — the only question the assistant asks;
- a file sent with a message is compared with the target country's requirements, with one reply;
- what the platform does with data is a fixed, checked statement, never improvised by the model;
- a reference to “this/that” with nothing to refer to is asked about instead of guessed;
- anything unrelated is declined politely with a way back.
No reply ends with a question tacked on, and there are no buttons: an action the user may want next
is a link inside the sentence that mentions it.
"""

from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import chat as C
from app.analysis import chat_flow as F
from app.analysis import chat_reply as reply
from app.analysis.chat_intent import understand
from app.analysis.models import AnalysisMessage, AnalysisTask
from app.analysis.workflow_engine import is_overseas_country

log = logging.getLogger(__name__)

ADVICE_SLOT = "advice"  # 旧版对话里带入口按钮的消息（新对话不再出按钮）
OPT_INTRO, OPT_GENERATE, OPT_UPLOAD = "新手，带我了解", "直接生成文件", "上传现有制度文件"
GREETING = "你好，我是车研数合的合规顾问，有什么可以帮你？"
INTRO = (
    "可以问我某个国家对汽车数据出境有什么要求，我会引用该国现行法规的原文，并注明出自哪条；"
    "把现有的制度文件或《隐私政策》发给我，我对照目标国的要求指出缺什么、怎么补；"
    "说说数据从哪国发往哪国、做什么业务，我按出境路径从 8 类合规文件中筛选并建立所需初稿，有官方原表的按官方原表填写，没有的参照欧盟标准（含英文官方原表）。"
)
UPLOAD = "点输入框旁的「上传文件」选好文件（PDF、Word、文本都可以），再说一句要对照哪个国家，我就开始对照。"
# 平台怎么处理数据：只写核实过的事实（企业隔离、发给大模型前先替换敏感信息、每次调用有审计记录）
PRIVACY = (
    "关于数据：你在这里填写的内容和上传的文件，只保存在你所在企业的空间里，其他企业看不到。"
    "为了理解和撰写内容，系统会调用大模型；发送之前，企业名称、手机号、邮箱、证件号、车架号、车牌等会先替换成代号，每次调用都有审计记录。"
)
QUESTION = re.compile(r"[？?]|吗|么|呢|是否|会不会|能不能|有没有|怎么|如何|谁")
PRIVACY_ASK = re.compile(r"保密|隐私|泄露|泄漏|外泄|安全吗|(给|让|被).{0,3}(别人|他人|外人|第三方)|别人.{0,3}(看|知道)|谁(能|可以)?看|数据.{0,6}(去哪|存哪|存在哪|传到哪|给谁|被谁)|你们.{0,6}(会|能)看")
# 开头的“那/那么/所以/那这”是承接语气，不是指代：跳过它再看后面指的是什么（“那这个会影响我吗”问的是“这个”）
DANGLING = re.compile(r"^\s*(?:那么|所以|那(?=这|那|它|上面|刚才))?\s*(这个|那个|这些|那些|这|它|第[一二三四五六七八九十\d]+个|上面(?:那个|的)?|刚才(?:那个|的)?)")
CLARIFY = "你说的“{w}”是指什么呢？可以把想问的内容直接发给我，比如某个国家的某条规定，或者上传一份文件，我就能接着答。"
UNAVAILABLE = "我这边暂时没能理解这句话（服务繁忙），请稍后再发一次。"
ADV_CAPABILITY = (
    "这里是汽车数据出境合规顾问：回答某国法规怎么规定（只引用库里现行条文，带条文编号）；"
    "对照用户上传的制度文件或《隐私政策》，指出缺什么、怎么补；用户说清数据从哪国发往哪国和业务后，按出境路径筛选并建立所需合规文件初稿；"
    "各国法规简报在左侧「法规知识库」。"
)
ADV_SCOPE = "我能帮的是汽车数据出境合规：问法规、对照制度文件、生成合规文件、读各国的国家合规简报。"
OFF_TOPIC = "这个我帮不上，我只做汽车数据出境合规。需要的话可以问我某个国家的出境要求，或者说说你们的业务，我把合规文件做出来。"
SMALLTALK = "好的。有想问的随时说。"
NEED_ROUTE = "可以。要做这几份文件，还需要知道数据从哪个国家发出、传到哪个国家，比如“车在埃及运行，数据传回中国”。"
NOTED = "已记下你说的业务情况。你还可以继续补充涉及的数据类别（如车外视频、定位轨迹、远程诊断日志）、预计涉及人数规模及现有加密脱敏措施，便于进一步锁定申报路径；需要的话随时说一声“帮我生成文件”，我就按出境路径建立所需合规文件的初稿，也可以直接问目标国法规。"
AWAIT_ROUTE = "_await_route"  # 用户要生成文件、但还没说清流向：下一句话先读流向
DOC_PROJECT = "_doc_project"  # 这次对话里建好的文件项目
VIEWER_PRIVATE_KEY = "_viewer_private"  # 普通查看者的个人咨询对话：仅本人可见，不建/不改企业项目
VIEWER_NO_PROJECT = (
    "当前是普通查看者账号，这里可以咨询各国法规要求，不能新建合规评估项目或生成 8 类合规文件。"
    "如需建立项目或生成文件，请联系本企业的企业用户操作。"
)


def viewer_owner(task: AnalysisTask) -> str | None:
    raw = (task.facts_json or {}).get(VIEWER_PRIVATE_KEY)
    return str(raw) if raw else None


async def greet(session: AsyncSession, task: AnalysisTask) -> None:
    await C.add_message(session, task.id, "assistant", GREETING)
    await session.commit()


async def _say(session: AsyncSession, task: AnalysisTask, text: str) -> None:
    await C.add_message(session, task.id, "assistant", text)
    await session.commit()


def _fixed(content: str) -> str | None:
    """Replies that never need the model: what the platform does with data, and the entry buttons of
    conversations started before the buttons were removed."""
    text = content.strip()
    if text == OPT_INTRO:
        return INTRO
    if text == OPT_UPLOAD:
        return UPLOAD
    # 只有简短的问句才直接答（“数据会给别人看吗”）；业务描述里出现“隐私政策”“保密协议”这类词，
    # 要交给理解环节整理成业务信息，不能被当成在问平台的数据安全
    if PRIVACY_ASK.search(text) and len(text) <= 50 and QUESTION.search(text):
        return PRIVACY
    return None


def _dangling(content: str, recent: list[dict]) -> str | None:
    """A reference to “this/that” with nothing it could mean: only the greeting or fixed statements so far."""
    fixed = (GREETING, INTRO, UPLOAD, PRIVACY)
    said = [m for m in recent if m["role"] == "assistant" and not any(m["text"].startswith(o[:16]) for o in fixed)]
    m = DANGLING.match(content)
    if m and not said and len(content) <= 40:
        return m.group(1)
    return None


async def _user_words(session: AsyncSession, task: AnalysisTask) -> str:
    rows = (
        await session.scalars(
            select(AnalysisMessage.content)
            .where(AnalysisMessage.task_id == task.id, AnalysisMessage.role == "user")
            .order_by(AnalysisMessage.seq_no)
        )
    ).all()
    return "\n".join(r for r in rows if r and r not in (OPT_INTRO, OPT_GENERATE, OPT_UPLOAD))


def _link(label: str, path: str) -> str:
    return f"[{label}]({path})"


async def _make_documents(session: AsyncSession, task: AnalysisTask) -> str:
    """Create the documents project from what the user said; one question if the route is missing."""
    if viewer_owner(task) is not None:
        return VIEWER_NO_PROJECT
    from app.analysis import chat_docs, doc_projects

    said = await _user_words(session, task)
    try:
        route = await chat_docs.read_route(said)
    except Exception:  # noqa: BLE001 - 模型不可用：如实说，不建空项目
        log.warning("读取流向失败", exc_info=True)
        return UNAVAILABLE
    facts = dict(task.facts_json or {})
    if not (route.source and route.recipient):
        facts[AWAIT_ROUTE] = True
        task.facts_json = facts
        return NEED_ROUTE
    body = doc_projects.CreateIn(
        source_country=route.source, recipient_country=route.recipient,
        function=route.function or None,
        scenario_text="" if route.function else (route.business or said.strip().split("\n")[-1])[:200],
        vehicle_model=route.vehicle,
    )
    project = await doc_projects.new_project(session, task.org_id, task.created_by, body)
    project.facts_json = {**(project.facts_json or {}), doc_projects.CHAT_KEY: str(task.id)}
    if not is_overseas_country(task.country_code) and is_overseas_country(project.country_code):
        task.country_code = project.country_code  # 之后问法规默认按项目的国家答
    facts.pop(AWAIT_ROUTE, None)
    facts[DOC_PROJECT] = str(project.id)
    task.facts_json = facts
    await session.commit()
    doc_projects.begin_suggest(session, project.id, said)
    what = doc_projects.SHORT_FUNCTION.get(route.function or "") or route.business
    return (
        f"好的，已按你说的建好：数据从{route.source}发往{route.recipient}" + (f"，{what}" if what else "") + "。"
        "已按当前出境路径筛选所需合规文件，初稿现在就能下载；我正从你刚才的描述里整理能填进文件的信息，每一项都附原文，你确认后才会写进去。"
        + _link("打开这个项目", f"/documents?project={project.id}") + "。流向或业务不对的话，在项目里直接改。"
    )


async def advise(session: AsyncSession, task: AnalysisTask, content: str, new_files: int) -> None:
    text = content.strip()
    fixed = _fixed(text)
    if fixed is not None:
        await _say(session, task, fixed)
        return
    if text == OPT_GENERATE:  # 旧版对话里的按钮
        await _say(session, task, await _make_documents(session, task))
        return
    ctx = await F._context(session, task, None, "advice", text)
    project = await _linked_project(session, task)
    ref = _dangling(text, ctx.recent) if project is None else None  # 有项目时“这个”多半指项目里的东西
    if ref is not None:
        await _say(session, task, CLARIFY.format(w=ref.strip()))
        return
    u = await understand(text, ctx, match_country=C.match_country, match_scenario=C.match_scenario)
    if not u.ok:
        await _say(session, task, UNAVAILABLE)
        return
    if u.title and task.title == "新对话" and not task.title_custom:
        task.title = u.title
    intents = list(u.intents)
    if "ask_privacy" in intents:
        await _say(session, task, PRIVACY)
        return
    waiting = bool((task.facts_json or {}).get(AWAIT_ROUTE))
    if "generate" in intents or (waiting and {"provide_info", "correct_info"} & set(intents)):
        existing = await _linked_project(session, task)
        if existing is not None:
            await _say(session, task, "这次对话的文件项目已经建好了，所需合规文件的状态和待补信息都在项目里：" + _link("打开项目", f"/documents?project={existing.id}")
                       + "。接着说业务情况，我会继续整理进去；要换流向，请在项目里修改。")
            return
        await _say(session, task, await _make_documents(session, task))
        return
    notes = await F._files_step(session, task, text, new_files, u)
    if notes and set(intents) <= {"provide_info", "correct_info", "file_request", "smalltalk", "ask_status"}:
        await _say(session, task, "\n".join(notes))  # 传了文件：只回一条，说清楚在做什么
        return
    if project is not None and not notes and _about_project(text, intents):
        await _say(session, task, await _guide(session, project, text, ctx))
        return
    if "help" in intents and not notes and set(intents) <= {"help", "unclear"}:
        await _say(session, task, INTRO)
        return
    parts = list(notes)
    soft = [i for i in intents if i in reply.SOFT and i != "off_topic"]
    for intent in intents:
        if intent == "ask_law":
            parts.append(await F._law_text(session, task, u, text, ctx))
        elif intent == "ask_status":
            parts.append(await _status(session, task, u))
        elif intent == "file_request":
            got = await F._respond(session, task, intent, u, text, ctx, None, "advice")
            if got:
                parts.append(got)
        elif intent in ("provide_info", "correct_info") and not notes:
            parts.append(await _feed_project(session, task, text) or await _noted(session, task, text, ctx, u))
        elif intent == "off_topic":
            parts.append(OFF_TOPIC)
    if soft and not parts:
        composed = await reply.compose(
            session, task, soft, text, ctx, stage="advice", progress="", pointer=F._platform_reply(text),
            scope=ADV_SCOPE, capability=ADV_CAPABILITY, why_asked="", example_answers=[], question_follows=False,
        )
        parts.append(composed or _soft_fallback(soft, u))
    await _say(session, task, "\n".join(dict.fromkeys(p for p in parts if p)) or UNAVAILABLE)


def _about_project(text: str, intents: list[str]) -> bool:
    """In a conversation that has a documents project: a request for help, a complaint about the last
    answer, a progress question, or anything about filling in the documents is answered about that
    project. Business facts go into the project (_feed_project); law questions to the library."""
    from app.analysis import project_guide

    kinds = set(intents)
    if kinds <= {"help", "unclear", "feedback", "ask_status", "smalltalk"} and kinds != {"smalltalk"}:
        return True
    if "ask_law" in kinds:
        return bool(project_guide.FILLING.search(text))
    return bool(project_guide.ABOUT_PROJECT.search(text)) and not kinds <= {"provide_info", "correct_info"}


async def _guide(session: AsyncSession, project: AnalysisTask, text: str, ctx) -> str:
    from app.analysis import project_guide
    from app.analysis.doc_projects import build_overview

    got = await project_guide.answer(session, project, text, ctx.recent)
    if got:
        return got
    return project_guide.fallback(await build_overview(session, project), text)


async def _noted(session: AsyncSession, task: AnalysisTask, text: str, ctx, u) -> str:
    """The user is telling us about the business without a project: many only want advice. Mention that
    the documents can be made from this once in the conversation, then just respond like a person —
    no repeated offer, nothing created unless asked."""
    if viewer_owner(task) is not None:
        return "明白了。有想了解的法规问题可以接着问。"
    if not any(m["role"] == "assistant" and "帮我生成文件" in m["text"] for m in ctx.recent):
        return NOTED
    composed = await reply.compose(
        session, task, ["provide_info"], text, ctx, stage="advice", progress="", pointer="", scope=ADV_SCOPE,
        capability=ADV_CAPABILITY, why_asked="", example_answers=[], nothing_recorded=True, question_follows=False,
    )
    return composed or "明白了。有想了解的地方可以接着问。"


async def _linked_project(session: AsyncSession, task: AnalysisTask) -> AnalysisTask | None:
    if viewer_owner(task) is not None:
        return None
    pid = (task.facts_json or {}).get(DOC_PROJECT)
    if not pid:
        return None
    project = await session.get(AnalysisTask, uuid.UUID(pid))
    if project is None or project.archived_at is not None or project.kind != "documents":
        return None
    return project


async def _feed_project(session: AsyncSession, task: AnalysisTask, text: str) -> str | None:
    """After a documents project was created in this conversation, what the user goes on to say about
    the business is read into that project too (as suggestions to confirm there), so talking here
    keeps filling the documents."""
    if viewer_owner(task) is not None:
        return None
    from app.analysis import doc_projects

    project = await _linked_project(session, task)
    if project is None:
        return None
    if session.info.get("_copilot_mode"):
        try:
            view = await doc_projects.build_overview(session, project)
            missing_items = [m["label"] for m in (view.get("final") or {}).get("missing", []) if m.get("label")]
            cn_pending = (view.get("cn_route") or {}).get("pending") or []
            if cn_pending:
                next_hint = f"接下来还可以顺手说一下「{'」「'.join(cn_pending[:2])}」，就能进一步锁定本次出境申报路径。"
            elif missing_items:
                next_hint = f"当前项目还差 {len(missing_items)} 项关键信息（如「{'」「'.join(missing_items[:3])}」），你也可以继续口述或上传材料由我代为提取。"
            else:
                next_hint = "目前本项目所需填写项已基本齐备，核对填入后即可生成论述并导出成稿。"
        except Exception:  # noqa: BLE001
            next_hint = "你还可以继续补充其他业务或防护细节，我会持续为你整理。"
        return "收到，已从你刚才的说明里提取出对应填写项，核对气泡下方的卡片后点击一键填入即可写入左侧项目。" + next_hint
    doc_projects.begin_suggest(session, project.id, text)
    return "收到，这些情况我正在整理进文件项目，识别出能填的内容会列在项目的“请核对”里，你确认后才写进文件：" + _link("打开项目", f"/documents?project={project.id}") + "。"


async def _status(session: AsyncSession, task: AnalysisTask, u) -> str:
    files = [f for f in await F.jobs.sent_files(session, task) if f.readable]
    if files:
        return await F._status_text(session, task, u, "advice")
    project = (task.facts_json or {}).get(DOC_PROJECT)
    if project:
        return "这次对话里建的文件项目在这里：" + _link("打开项目", f"/documents?project={project}") + "，每份文件的状态和还差的信息都在项目里。"
    return "目前没有在进行的事情。"


def _soft_fallback(soft: list[str], u) -> str:
    if "ask_platform" in soft:
        return "这个功能在平台的其他页面里，左侧菜单可以找到：法规知识库、海外属地准入、官方表格、更新通知。"
    if "feedback" in soft:
        return "抱歉，刚才的回复没有答到点上。你直接告诉我想问的具体问题，我重新回答。"
    if "smalltalk" in soft:
        return GREETING if u.greeting else SMALLTALK
    if "unclear" in soft:
        return "我没完全明白你的意思。可以问某个国家的法规，把制度文件发给我对照，或者说说业务让我把合规文件做出来。"
    return ADV_SCOPE
