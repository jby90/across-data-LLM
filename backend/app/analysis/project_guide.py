# ruff: noqa: E501
"""Answers in a conversation that belongs to a documents project, with that project in view.

The conversation beside a project (and a consulting chat that created one) knows which project it
is about: the route, the business, the eight documents and what each still lacks, and every item
with its note and current value. “合同怎么填”“还差什么”“这个申报表要我写什么” are answered about
this project — which items feed that document, which are still empty, what form each answer takes —
instead of a general introduction to the assistant. Laws are not quoted here (the law questions go
to the library answer), numbers not in the project or the user's words are refused, and nothing is
claimed to have been filled in.
"""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import risk_items
from app.analysis.document_narrative import _numbers
from app.analysis.llm import call_llm_json
from app.analysis.models import AnalysisTask
from app.analysis.wording import items_not_questions

log = logging.getLogger(__name__)

MAX_REPLY = 900
SYSTEM = (
    "你是“合规文件评估”页面右侧的项目合规顾问。用户正在推进下面这个文件项目（project），你清楚它的跨境流向、业务、适用路径（cn_route）、所需文件状态、每项填写情况以及材料差距诊断（diagnosis）。"
    "根据 project 和最近的对话（recent）回答用户这句话（said），要具体落到这个项目：\n"
    "- 问某份文件怎么填、要准备什么：说明这份文件用到哪些项（用页面上的项名），哪些已填、哪些还空着，每项按 hint 说清怎么写；\n"
    "- 问还差什么、进度如何：按文件或按项说清还缺的，先说最要紧的；文件状态只用 documents 里的 state（未开始 / 填写中 / 待写分析 / 已就绪），"
    "标了 needed 的文件说“按目前情况不需要”并带上 why，不算进还缺的；"
    "有 issues（平台检查出的前后不一致）时说“有 N 处前后不一致”，逐条说是哪一项、和哪里对不上，这些不算缺项、不影响下载成稿；\n"
    "- 问出境合规路径、走安全评估还是标准合同：结合 route 与 cn_route 说明当前判定的合规路径、原因及待确认的关键项；若数据从境外发出，结合 documents 的适用说明讲清所需申报表、标准合同条款或评估报告；\n"
    "- 问现有防护措施、加密脱敏情况：对照 items 中加密措施、去标识化/脱敏措施、访问控制、保存期限等项的当前已填内容与空缺项逐一点评；\n"
    "- 问《隐私政策》或告知文本披露要点：对照 items 中面向个人的告知与《隐私政策》披露、单独同意、个人行使权利渠道等项的填写情况及 diagnosis（如有）说明是否与实际接收国、数据类型和保存期限一致，并提示可点击输入框下方的上传材料按钮上传现有《隐私政策》自动比对；\n"
    "- 问主要风险、风险等级、风险矩阵：按 items 里“主要风险及其可能性、严重程度与应对措施”的值逐项说风险、可能性、严重程度、风险等级和措施，等级照写不改（用“低风险 / 中风险 / 高风险”原词）；有“还缺”的说清还缺什么；\n"
    "- 问申报截止、有效期、什么时候到期：按 deadlines 原样说日期和还剩几天；没填日期的说在项目页顶部“出境路径”下填；deadlines 为空说明不走安全评估，没有这两个期限；\n"
    "- 对你上一条回答不满意：直接按这个项目重新答，不要再道歉一遍；\n"
    "- 说的是自家业务情况：确认收到即可（系统会另外整理进项目）。\n"
    "硬性要求：自然专业的中文，3–8 句，可以分行列出项名；平台把要填的内容叫“填写项”，一律说“这一项”“某项”“填写项”，不用“题”字（不说“本题”“这道题”“题目”）；不写法规条文编号和处罚数额，不编造 project 和用户原话里没有的数字、机构、日期；"
    "不说“已帮你填写/保存”；不要以反问句结尾；不介绍平台的一般功能。\n"
    '只输出 JSON：{"reply":""}'
)
CLAIMED = re.compile(r"已(经)?(帮您|帮你|为您|为你)?(填写|填好|填入|保存|写入)")
# 明确在问怎么填、还差什么（即使同时提到法规，也是在问这个项目）
FILLING = re.compile(r"怎么填|如何填|填什么|填写|要写|怎么写|还差|还缺|缺什么|缺哪|准备什么|上下文|我前面|不一致|对不上|前后矛盾|截止日|到期日|还剩.{0,4}天|倒计时|什么时候到期|"
                     r"出境.{0,4}路径|合规路径|申报路径|防护措施|安全措施|隐私政策.{0,6}(核对|披露|要点|一致)|披露要点|"
                     r"(这个|本)项目.{0,12}(风险|整改)|主要风险|风险矩阵|整改项")
# 这些说法是在问这个项目本身（怎么填、还差什么、某份文件），不是问法规
ABOUT_PROJECT = re.compile(
    r"怎么填|如何填|填什么|填写|要写|怎么写|还差|还缺|缺什么|缺哪|进度|不一致|对不上|完成了|准备什么|需要什么材料|"
    r"出境.{0,4}路径|合规路径|申报路径|防护措施|安全措施|隐私政策.{0,6}(核对|披露|要点|一致)|披露要点|"
    r"申报表|评估报告|合同|整改|准入|DPIA|LIA|TIA|这份|这个文件|哪份|哪一项|这一项|上下文|我前面|刚才说|截止|到期|还剩.{0,4}天|倒计时|有效期|"
    r"(这个|本)项目.{0,12}风险|主要风险|风险矩阵",
    re.I,
)


ROUTE_LABELS = {"company_name": "企业名称", "vehicle_model": "车型", "source_country": "数据发出国", "recipient_country": "数据接收国"}


def _issues(view: dict) -> list[str]:
    """平台检查结果，与项目页题目旁的提示同一套说法：“项名：哪里对不上”。"""
    label_of = {q["key"]: q["label"] for q in view.get("questions", [])} | ROUTE_LABELS
    return [f"{label_of.get(i['key'], i['key'])}：{i['text']}" for i in (view.get("final") or {}).get("issues", [])]


def issues_line(view: dict) -> str:
    found = _issues(view)
    if not found:
        return ""
    rows = "\n".join(f"- {x}" for x in found[:6])
    return f"\n有 {len(found)} 处前后不一致（不影响下载成稿，改好后提示自动消失）：\n{rows}"


def period_text(p: dict | None, over: str = "已过期") -> str:
    """“2027-02-10，还剩 92 天” / “2027-02-10，已过期 3 天”, the same words as the project page."""
    if not p:
        return ""
    d = p["days_left"]
    return f"{p['date']}，" + (f"还剩 {d} 天" if d >= 0 else f"{over} {-d} 天")


def deadline_lines(view: dict) -> list[str]:
    """The security-assessment periods, as the top of the project page says them (empty on other routes)."""
    v = (view.get("cn_route") or {}).get("validity")
    if not v:
        return []
    if v["issued_on"]:
        lines = [f"已取得评估结果（出具日 {v['issued_on']}），不再计申报截止日"]
    elif v["filing"]:
        lines = [f"申报截止日 {period_text(v['filing'], '已超过')}（自评估 {v['assessed_on']} 完成，须在 3 个月内申报）"]
    else:
        lines = ["自评估完成日还没填，填了才能算出申报截止日（自评估完成后 3 个月内须申报）"]
    if v["result"]:
        lines.append(f"评估结果到期日 {period_text(v['result'])}（出具之日起 3 年有效）；{v['extend']}")
    else:
        lines.append(f"拿到评估结果后填出具日，算出到期日（出具之日起 3 年有效）；{v['extend']}")
    return lines


def _project_view(view: dict) -> dict:
    """The part of the project the model needs, small enough to send every time."""
    from app.analysis.doc_projects import STATE_LABELS

    names = {d["code"]: d["name"] for d in view["documents"]}
    appl = view.get("applicability") or {}
    cn = view.get("cn_route") or {}
    return {
        "title": view["title"],
        "route": {k: v for k, v in view["route"].items() if v and k != "library_country"},
        "cn_route": {k: cn.get(k) for k in ("label", "why", "pending") if cn.get(k)} if cn else {},
        "diagnosis": view.get("suggest_diagnosis") or {},
        # 状态与项目页同一套：未开始 / 填写中 / 待写分析 / 已就绪；按目前情况不需要的另外标出
        "documents": [
            {"name": d["name"], "state": STATE_LABELS[d["state"]],
             **({"needed": "按目前情况不需要", "why": appl[d["code"]].get("why", "")} if appl.get(d["code"], {}).get("state") == "na" else {}),
             "missing_count": d.get("official_missing") or len(d["missing"]), "missing": [m["label"] for m in d["missing"]][:15]}
            for d in view["documents"]
        ],
        "final": {"可以下载成稿": view["final"]["ok"], "成稿包里不放": [x["name"] for x in view["final"]["skipped"]]} if view.get("final") else {},
        "issues": _issues(view),
        "deadlines": deadline_lines(view),
        "items": [
            {"label": q["label"], "hint": q.get("hint", ""), "used_in": [names.get(c, c) for c in q["used_in"]],
             "value": risk_items.shown(q["key"], q["value"], "待补充")[:300 if q.get("kind") == "risks" else 80] or "（空）",
             **({"还缺": q["lacking"]} if q.get("lacking") else {})}
            for q in view["questions"]
        ],
    }


async def answer(session: AsyncSession, project: AnalysisTask, said: str, recent: list[dict]) -> str | None:
    """None when the model is unavailable (the caller falls back to its own reply)."""
    from app.analysis.doc_projects import build_overview

    view = _project_view(await build_overview(session, project))
    payload = {"project": view, "recent": recent[-6:], "said": said}
    try:
        res = await call_llm_json(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            max_tokens=2000, temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001 - 模型不可用：交给调用方的兜底回复
        log.warning("项目助手调用模型失败，改用程序回复：%s", exc)
        return None
    text = items_not_questions(str((res.data or {}).get("reply") or "").strip()) if isinstance(res.data, dict) else ""
    if not text or len(text) > MAX_REPLY or CLAIMED.search(text) or re.search(r"第\s*\d+\s*条", text):
        log.warning("项目助手回复不合用（空、过长、自称已填写或写了条号），改用程序回复：%s", text[:200])
        return None
    allowed = _numbers(json.dumps(payload, ensure_ascii=False)) | {str(n) for n in range(1, 61)}  # 列表序号、“还差 N 项”
    if _numbers(text) - allowed:
        log.warning("项目助手回复含资料里没有的数字，改用程序回复：%s", sorted(_numbers(text) - allowed))
        return None
    return text


def risk_lines(view: dict) -> list[str]:
    """The main risks as the risk table on the page says them: each with its grades, level and measure."""
    q = next((x for x in view.get("questions", []) if x.get("kind") == "risks"), None)
    if not q or not str(q.get("value") or "").strip():
        return []
    lines = risk_items.text(q["value"], "待填").split("\n")
    return lines + ([f"还缺：{'；'.join(q['lacking'])}"] if q.get("lacking") else [])


def fallback(view: dict, said: str = "") -> str:
    """A plain reply from the project itself when the model cannot be used."""
    from app.analysis.doc_projects import STATE_LABELS

    if "风险" in said and risk_lines(view):  # 问的是主要风险：按风险表说
        return "这个项目填写的主要风险：\n" + "\n".join(risk_lines(view)) + "\n风险等级按发生可能性与影响严重程度综合判定；风险评估报告和 DPIA 中附主要风险评估与应对清单及风险等级判定与分布表。"
    na = {c for c, a in (view.get("applicability") or {}).items() if a.get("state") == "na"}
    lacking = [d for d in view["documents"] if d["state"] != "ready" and d["code"] not in na]
    skipped = [d["name"] for d in view["documents"] if d["state"] != "ready" and d["code"] in na]
    tail = f"\n按目前情况不需要：{'、'.join(skipped)}。" if skipped else ""
    tail += "".join(f"\n{x}。" for x in deadline_lines(view))
    if not lacking:
        return "这个项目要的文件都已就绪，可以下载成稿。" + tail + issues_line(view)
    lines = []
    for d in lacking:
        n = d.get("official_missing") or len(d["missing"])
        if d["missing"]:
            what = f"，还差 {n} 项（{'、'.join(m['label'] for m in d['missing'][:5])}）"
        else:
            what = f"，官方原表还差 {n} 项" if n else ""
        lines.append(f"{d['name']}：{STATE_LABELS[d['state']]}{what}")
    return "这个项目各份文件的情况：\n" + "\n".join(lines[:8]) + tail + issues_line(view) + "\n每一项下面有填写说明，左侧可以逐项填。"
