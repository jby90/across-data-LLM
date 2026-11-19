# ruff: noqa: E501
"""对话式信息采集:用户在聊天框里一问一答完成所有信息填写,齐备后启动分析。"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.document_basis import ALIASES, NAMES, country_of
from app.analysis.models import AnalysisMessage, AnalysisTask
from app.analysis.schemas import format_conversation_code
from app.analysis.workflow_engine import (
    COUNTRY_NAMES_ZH,
    REQUIRED_FACTS_SPEC,
    SCENARIO_LABELS,
    active_overseas_countries,
    evaluate_fact_sufficiency,
)

PLACEHOLDER_COUNTRY = "--"
FACT_KEYS = [s["fact_key"] for s in REQUIRED_FACTS_SPEC]
FACT_SPEC = {s["fact_key"]: s for s in REQUIRED_FACTS_SPEC}
# 给用户看的短名称（确认语、进度提示用）；新增事实项时在这里补一条，没补的退回“第 n 项业务情况”
FACT_TITLE = {
    "exterior_video_and_gnss": "车外视频与位置轨迹的采集和匿名化",
    "cross_border_mechanism": "数据传到哪个国家、靠什么机制出境",
    "consent_and_notice": "怎么告知用户并取得同意",
    "retention_and_blackbox": "数据保存多久、黑匣子数据怎么处理",
    "local_admission_and_safety_operator": "是否已向属地部门申报准入、有无安全员",
}
SLOT_LABEL = {
    "country_code": "目标国家",
    "vehicle_model": "车型/车队",
    "scenario_type": "功能",
    **{s["fact_key"]: FACT_TITLE.get(s["fact_key"], f"第 {i + 1} 项业务情况") for i, s in enumerate(REQUIRED_FACTS_SPEC)},
}
GREETING = (
    "你好,我是车研数合合规分析助手。我会通过几个问题了解你的业务场景,"
    "再对照目标国法规，生成八维度合规评估报告，你可以直接在这里导出。"
)
SCENARIO_HINTS = [
    ("autonomous_trial", ("自动驾驶", "路测", "道路测试", "公共道路", "L3", "L4")),
    ("ota_diagnostics", ("OTA", "诊断", "升级")),
    ("cockpit_biometrics", ("座舱", "语音", "生物", "驾驶员状态", "DMS")),
    ("hd_mapping", ('高精地图', '高精度地图', '测绘', '地理信息', '众包地图')),
    ("fleet_mobility", ('车队', '网约车', '出行服务', '物流车', '租车', '共享汽车', '运营车辆')),
    ("sales_service", ('经销商', '销售', '售后', '客户管理', 'CRM', '4S', '维修保养')),
    ("insurance_ubi", ('保险', 'UBI', '驾驶行为', '车险')),
    ("charging_energy", ('充电', '换电', '充电桩', '能源服务', 'V2G', '车网互动')),
    ("connected_vehicle_cloud", ("车联网", "云平台", "遥测", "云端")),
]

StartFn = Callable[[AsyncSession, AnalysisTask], Awaitable[None]]


def _has_country(code: str | None) -> bool:
    c = (code or "").strip().upper()
    return bool(c and c not in ("--", "CN", "ZZ") and c in NAMES)


def next_slot(task: AnalysisTask) -> str | None:
    if not _has_country(task.country_code):
        return "country_code"
    if not (task.vehicle_model or "").strip():
        return "vehicle_model"
    if task.scenario_type not in SCENARIO_LABELS:
        return "scenario_type"
    facts = task.facts_json or {}
    for key in FACT_KEYS:
        if len(str(facts.get(key) or "").strip()) < 4:
            return key
    return None


def question_for(slot: str, countries: dict[str, str] | None = None) -> tuple[str, list[str]]:
    if slot == "country_code":
        src = countries or COUNTRY_NAMES_ZH
        opts = [f"{name} ({code})" for code, name in src.items()]
        return "请问你计划把车辆或数据投放到哪个国家?(可直接点选已收录法规的国家,或输入国家名称)", opts
    if slot == "vehicle_model":
        return "请问受评的车型或车队名称是什么?例如「某品牌 L4 自动驾驶 SUV」。", []
    if slot == "scenario_type":
        return "请选择最接近的功能:", list(SCENARIO_LABELS.values())
    spec = FACT_SPEC[slot]
    text = f"{spec['question_zh']}\n(为什么要问:{spec['why_needed']})"
    return text, list(spec["suggested_options"])


# 用户直接说出的国家名：中文与外文写法（不含机构简称）
COUNTRY_SPOKEN: dict[str, tuple[str, ...]] = {
    "SG": ("Singapore", "Singapour"),
    "EG": ("Egypt", "Egypte", "Égypte"),
    "TN": ("Tunisia", "Tunisie"),
    "MA": ("Morocco", "Maroc"),
    "LB": ("Lebanon", "Liban"),
    "EU": ("European Union", "Europe"),
    "DE": ("Germany", "Deutschland", "Allemagne"),
    "FR": ("France",),
    "IT": ("Italy", "Italia"),
    "ES": ("Spain", "España"),
    "NL": ("Netherlands", "Holland"),
    "GB": ("United Kingdom", "Britain"),
    "JP": ("Japan",),
    "KR": ("Korea",),
    "TH": ("Thailand",),
    "MY": ("Malaysia",),
    "ID": ("Indonesia",),
    "VN": ("Vietnam",),
    "PH": ("Philippines",),
    "IN": ("India",),
    "AE": ("United Arab Emirates", "UAE", "Dubai"),
    "SA": ("Saudi Arabia", "Saudi"),
    "TR": ("Turkey", "Türkiye"),
    "RU": ("Russia",),
    "US": ("United States", "America", "USA"),
    "MX": ("Mexico",),
    "BR": ("Brazil", "Brasil"),
    "AU": ("Australia",),
    "NZ": ("New Zealand",),
}


def match_country(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    upper = raw.upper()
    low = raw.lower()
    overseas_zh = {
        c: n
        for c, n in {**NAMES, **COUNTRY_NAMES_ZH}.items()
        if c not in ("CN", "ZZ", "--") and len(n) >= 2
    }
    zh_hits = {c: n for c, n in overseas_zh.items() if n in raw}
    for alias, code in ALIASES.items():
        if code not in ("CN", "ZZ", "--") and alias in raw and code not in zh_hits:
            zh_hits[code] = alias
    zh_hits = {
        c: n for c, n in zh_hits.items() if not any(n != o and n in o for o in zh_hits.values())
    }
    matches = set(zh_hits.keys())
    for code, words in COUNTRY_SPOKEN.items():
        if any(
            re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", low, re.I)
            for w in words
        ):
            matches.add(code)
    for code in COUNTRY_NAMES_ZH:
        if re.search(rf"(?<![A-Z]){code}(?![A-Z])", upper):
            matches.add(code)
    if not matches:
        other = country_of(raw)
        if other and other not in ("CN", "ZZ", "--"):
            return other
    if len(matches) == 1:
        return next(iter(matches))
    return None


# 文件里提到哪个国家：按名称（中文、英文、当地语言）和该国数据保护机构/法规的简称计数
COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "SG": ("新加坡", "Singapore", "PDPA", "PDPC"),
    "EG": ("埃及", "Egypt", "Égypte", "Egypte"),
    "TN": ("突尼斯", "Tunisia", "Tunisie", "INPDP"),
    "MA": ("摩洛哥", "Morocco", "Maroc", "CNDP"),
    "LB": ("黎巴嫩", "Lebanon", "Liban"),
}


def countries_in(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for code, names in COUNTRY_ALIASES.items():
        n = sum(len(re.findall(re.escape(w), text, re.I if w.isascii() else 0)) for w in names)
        if n:
            out[code] = n
    for code, name in NAMES.items():
        if code in out or code in ("CN", "ZZ", "--") or len(name) < 2:
            continue
        words = (name, *COUNTRY_SPOKEN.get(code, ()))
        n = sum(
            len(
                re.findall(
                    rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])" if w.isascii() else re.escape(w),
                    text,
                    re.I if w.isascii() else 0,
                )
            )
            for w in words
        )
        if n:
            out[code] = n
    return out


def country_from_documents(texts: list[str]) -> str | None:
    """The one country the documents are clearly about: the most mentioned, and at least twice as
    often as the next one. Anything less clear is asked, not guessed."""
    total: dict[str, int] = {}
    for t in texts:
        for code, n in countries_in(t).items():
            total[code] = total.get(code, 0) + n
    ranked = sorted(total.items(), key=lambda kv: -kv[1])
    if not ranked:
        return None
    if len(ranked) == 1 or ranked[0][1] >= 2 * ranked[1][1]:
        return ranked[0][0]
    return None


def _unfilled_slots(task: AnalysisTask) -> set[str]:
    """Extraction may fill blanks, never revise facts already supplied by the user."""
    slots = {key for key in FACT_KEYS if not str((task.facts_json or {}).get(key) or "").strip()}
    if not _has_country(task.country_code):
        slots.add("country_code")
    if not (task.vehicle_model or "").strip():
        slots.add("vehicle_model")
    if task.scenario_type not in SCENARIO_LABELS:
        slots.add("scenario_type")
    return slots


def match_scenario(text: str) -> str | None:
    for key, label in SCENARIO_LABELS.items():
        if text.strip() == label or key in text:
            return key
    for key, words in SCENARIO_HINTS:
        if any(w.lower() in text.lower() for w in words):
            return key
    return None


def apply_text(task: AnalysisTask, slot: str, text: str, min_len: int = 6) -> str | None:
    """把用户原话写入槽位;返回错误提示,成功返回 None。"""
    text = text.strip()
    if slot == "country_code":
        code = match_country(text)
        if code is None:
            return "没有识别出国家,请从下方选项中选择,或直接输入国家名称。"
        task.country_code = code
    elif slot == "vehicle_model":
        if not 2 <= len(text) <= 128:
            return "车型名称请控制在 2~128 个字符内。"
        task.vehicle_model = text
    elif slot == "scenario_type":
        key = match_scenario(text)
        if key is None:
            return "没有识别出业务场景,请从下方选项中选择最接近的一项。"
        task.scenario_type = key
    else:
        if len(text) < min_len:
            return "这条信息有点简略,请再具体描述一下(至少几个字),这样才能对照法规分析。"
        facts = dict(task.facts_json or {})
        facts[slot] = text[:1000]
        task.facts_json = facts
    return None


def _refresh_state(task: AnalysisTask) -> None:
    score, missing = evaluate_fact_sufficiency(task.country_code, task.facts_json or {})
    task.sufficiency_score = score
    task.missing_facts_json = missing
    if _has_country(task.country_code) and (task.vehicle_model or "").strip() and not task.title_custom:
        name = NAMES.get(task.country_code, task.country_code)
        task.title = f"{name} · {task.vehicle_model.strip()} 合规评估"[:256]


async def add_message(
    session: AsyncSession,
    task_id: Any,
    role: str,
    content: str,
    options: list[str] | None = None,
    slot: str | None = None,
) -> AnalysisMessage:
    last = (
        await session.execute(
            select(func.max(AnalysisMessage.seq_no)).where(AnalysisMessage.task_id == task_id)
        )
    ).scalar_one()
    msg = AnalysisMessage(
        id=uuid.uuid4(),
        task_id=task_id,
        seq_no=(last or 0) + 1,
        role=role,
        content=content,
        options_json=options or [],
        slot=slot,
    )
    session.add(msg)
    await session.flush()
    from app.analysis.events import append_event

    task = await session.get(AnalysisTask, task_id)
    await append_event(session, task, "task_changed", {"reason": "message_added"})
    return msg


async def _ask(session: AsyncSession, task: AnalysisTask, slot: str, prefix: str = "") -> None:
    countries = (
        {c["code"]: c["name"] for c in await active_overseas_countries(session)}
        if slot == "country_code"
        else None
    )
    text, options = question_for(slot, countries=countries)
    await add_message(session, task.id, "assistant", (prefix + text).strip(), options, slot)


async def start_chat(session: AsyncSession, task: AnalysisTask) -> None:
    if task.kind == "advice":  # 咨询助手：问候加三个入口，不追问任何信息
        from app.analysis.chat_advisor import greet

        await greet(session, task)
        return
    code = format_conversation_code(task.id, task.created_at)
    greeting = f"{GREETING}\n（本次对话 ID：{code}）"
    unfilled = _unfilled_slots(task)
    prefilled = [s for s in ("country_code", "vehicle_model", "scenario_type") if s not in unfilled]
    if prefilled:  # 引导式入口：这几项已经选好，直接从还缺的开始问
        _refresh_state(task)
        scene = str((task.facts_json or {}).get("business_scene") or "")
        brief = [_brief(s, task) for s in prefilled] + ([f"业务场景:{scene}"] if scene else [])
        greeting += "\n已按你在引导页的选择记录：" + "；".join(brief) + "。"
    await add_message(session, task.id, "assistant", greeting)
    slot = next_slot(task) or "country_code"
    await _ask(session, task, slot)
    await session.commit()


_AFTER_REPORT_HELP = (
    "本次评估的报告已经生成，可以在上方直接查看和导出 Word / PDF。\n"
    "如果业务情况有变化或要补充，请直接在这里说明（例如“车外视频现在不做匿名化了”“数据改存在新加坡云”），"
    "我会更新对应的信息并只重新分析受影响的方面，生成新的报告版本，历史版本保留、可对比。\n"
    "如需评估新的国家或车型，请点击对话列表上方的「+ 新对话」。"
)


def _brief(slot: str, task: AnalysisTask) -> str:
    if slot == "country_code":
        return f"目标国家:{NAMES.get(task.country_code, task.country_code)}"
    if slot == "vehicle_model":
        return f"车型:{task.vehicle_model}"
    if slot == "scenario_type":
        return f"功能:{SCENARIO_LABELS.get(task.scenario_type, task.scenario_type)}"
    value = str((task.facts_json or {}).get(slot) or "")
    return f"{SLOT_LABEL[slot]}:{value[:36]}{'…' if len(value) > 36 else ''}"


def _missing_slots(task: AnalysisTask) -> list[str]:
    out = []
    if not _has_country(task.country_code):
        out.append("country_code")
    if not (task.vehicle_model or "").strip():
        out.append("vehicle_model")
    if task.scenario_type not in SCENARIO_LABELS:
        out.append("scenario_type")
    facts = task.facts_json or {}
    out += [k for k in FACT_KEYS if len(str(facts.get(k) or "").strip()) < 4]
    return out


def _record_fact(task: AnalysisTask, slot: str, text: str, verdict: str) -> None:
    """否定与未确定的短回答也是事实：带上用户原话写入，避免因“太简略”被拒绝。"""
    facts = dict(task.facts_json or {})
    text = text.strip()
    if verdict == "negative":
        facts[slot] = f"否（用户回答：{text}）"[:1000]
    elif verdict == "unknown":
        facts[slot] = f"未确定（用户回答：{text}）"[:1000]
    task.facts_json = facts


async def _link_attachments(session: AsyncSession, task: AnalysisTask, message_id: Any, ids: list[Any]) -> int:
    """The files sent with this message become part of the conversation (no longer in the composer)."""
    from app.analysis.attachments import TaskAttachment

    if not ids:
        return 0
    rows = (
        await session.scalars(
            select(TaskAttachment).where(TaskAttachment.id.in_(ids), TaskAttachment.task_id == task.id, TaskAttachment.message_id.is_(None))
        )
    ).all()
    for r in rows:
        r.message_id = message_id
    await session.flush()
    return len(rows)


async def post_user_message(
    session: AsyncSession,
    task: AnalysisTask,
    content: str,
    start: StartFn,
    question_id: str | None = None,
    attachment_ids: list[Any] | None = None,
) -> None:
    content = content.strip()
    if task.status == "awaiting_user_facts":
        from app.analysis.user_facts import answer_user_facts

        question = task.pending_user_facts_json[0]
        if question_id != question["id"]:
            raise ValueError("追问已变化或已回答，请刷新后回答当前问题")
        await add_message(session, task.id, "user", content, slot=question["fact_key"])
        if await answer_user_facts(
            session, task, {question["fact_key"]: content}, {question["fact_key"]: question_id}
        ):
            await start(session, task)
        return
    if question_id is not None:
        raise ValueError("当前没有该待回答追问，请刷新对话")
    sent = await add_message(session, task.id, "user", content)
    new_files = await _link_attachments(session, task, sent.id, attachment_ids or [])

    from app.core.config import get_settings

    # 旧的评估对话（逐项问齐信息、生成八维度报告）已停用：历史对话里继续说话，由咨询助手回答
    if task.kind == "advice" or (task.kind == "chat" and not get_settings().assessment_chat_enabled):
        from app.analysis.chat_advisor import advise

        await advise(session, task, content, new_files)
        return

    if task.status == "running":
        from app.analysis.chat_flow import converse_while_running

        await converse_while_running(session, task, content, new_files)
        return
    if task.status == "failed":
        await add_message(session, task.id, "assistant", "好的,我重新启动分析。")
        await session.commit()
        await start(session, task)
        return

    from app.analysis.chat_flow import converse

    stage = "completed" if task.status in ("completed", "partial_sample") else "collecting"
    if stage == "collecting" and next_slot(task) is None:
        await session.commit()
        await start(session, task)
        return
    await converse(session, task, content, start, new_files, stage)
