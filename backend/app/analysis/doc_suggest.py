# ruff: noqa: E501
"""Suggested answers for the “生成合规文件” page, so the user fills as little as possible.

Two sources, both only ever *suggestions* that the user adopts or discards (nothing reaches a document
before that):
- from the user's own words (a pasted business description): each answer carries a verbatim quote of the
  words it came from, which is checked against the text;
- general wording for items that can be drafted from the route and the business alone (purpose,
  necessity, data flow, planned measures): written as plans, never as things the company has already done.
What only the company knows (names, registrations, volumes, durations, assessment conclusions) is never
guessed: without a quote from the user's text it stays blank.
"""

from __future__ import annotations

import asyncio
import json
import re

from app.analysis.document_narrative import _numbers
from app.analysis.llm import call_llm_json

MAX_VALUE_CHARS = 600
# 答案里不许夹带“说明里没有……”这类评语：文件里要写的是事实，不是对材料的批注
REMARK = re.compile(r"未提及|未说明|没有提及|没有说明|未见说明|说明中没有|说明里没有|不详|规模未|未知|尚待|待明确|待确认|待补充|有待")

BATCH = 9

SYSTEM = (
    "你在帮企业起草跨境数据合规文件所需的填写项。只依据【业务概况】和【用户的业务说明】回答，用中文，简洁。\n"
    "每一项只可能是下面两类之一：\n"
    "1. source=text：答案来自用户的业务说明。quote 必须逐字摘抄说明里支撑它的原话（不改字、不省略），value 是据此写出的答案。\n"
    "2. source=general：说明里没有，但 allow_general=true 的项，可以只用【业务概况】里已有的信息拟一版表述。写成计划或建议的口吻（“拟……”“将……”），不得写成企业已经做过的事；不得补充业务概况里没有出现的数据类别、人群、场所、系统；不得出现任何具体数字、日期、公司名、人名、证书号、联系方式。quote 留空。如果写出来只能是“具体待确定”这类空话，就不要输出这一项。\n"
    "allow_general=false 的项，说明里没有依据就不要输出。拿不准的项宁可不输出。\n"
    "同一句话可以同时支撑好几项（如“数据在杭州保存两年，访问需审批”既是保存期限与存放位置，也是保护措施），每一项都要分别输出，不要只放进一项。\n"
    "value 只写说明里有的内容，不要写“说明未提及……”“未说明……”这类评语；说明没有的部分不写，整项都没有依据就不输出。\n"
    "不要输出没有被问到的项。只输出 JSON：{\"answers\":[{\"key\":…,\"value\":…,\"source\":\"text|general\",\"quote\":…}]}"
)


_DEFAULT_LLM = call_llm_json

DIAGNOSE_SYSTEM = (
    "你是资深汽车数据跨境合规律师与企业合规顾问，负责审阅企业从 0 到 0.5 阶段上传或粘贴的现有材料（包括企业内部《数据安全管理制度》《汽车数据分类分级规范》《数据出境内控审批办法》、《隐私政策》/车机告知文本、半成品合规评估底稿、跨境协议草案或业务情况说明），"
    "只依据【业务概况】、【用户的现有材料】、【尚未覆盖的关键信息项名称】及【本流向适用法规核对要点】输出平实、严谨的结构化合规诊断与补齐指导。\n"
    "要求：\n"
    "1. doc_kind：从以下五类中准确选择一类标明材料性质：企业内部数据安全与分类分级制度、隐私政策与用户告知文本、数据出境半成品评估底稿、跨境传输协议或合同草案、业务情况说明；\n"
    "2. summary：用两至三句清晰平实的中文概括材料目前已建立的管理机制、已交代清楚的核心业务链路、数据分类分级规则或告知条款；\n"
    "3. gaps：列出三至五条当前材料在合规成稿层面的关键缺口，或与本流向适用法规要求不一致之处：\n"
    "   - 若材料包含企业内部《数据安全管理制度》《分类分级规范》或《出境内控办法》，重点核查其车载敏感数据分级口径（如车外人脸车牌、精确定位、座舱音视频、驾驶行为）、出境审批触发条件、境外接收方安全审计、密钥管理、应急处置时限及留存销毁规程是否覆盖当前跨境场景，并指出制度规定与目标国/发出国法定要求之间的衔接缺口；\n"
    "   - 若材料包含《隐私政策》、用户告知书或车机隐私弹窗文本，重点核查对外披露内容与实际跨境业务是否一致（境外接收方名称/国家/联系方式、出境字段覆盖度、具体保存期限、向境外提供个人信息的单独同意机制、个人行权与撤回同意渠道）；\n"
    "   - 若为半成品评估底稿或业务说明，聚焦采集边界、出境规模核定、车端脱敏与传输加密、接收方合同义务与再转移限制指出缺口；\n"
    "4. followups：列出二至四条面向企业产品、研发、数据安全或法务团队的具体核实与补齐提问，像顾问陪跑一样指导企业把半成品补齐为正式成稿。\n"
    "用语须通顺直白，严禁出现“最小必要裁剪”“风险缓释”“缓释闭环”“冷备快照”等生造工程黑话；严禁编造原文、业务概况与适用法规要点中没有出现过的任何数字、日期、公司名、条号或认证编号；不写空泛套话。\n"
    "只输出 JSON：{\"doc_kind\":\"…\",\"summary\":\"…\",\"gaps\":[\"…\"],\"followups\":[\"…\"]}"
)


async def diagnose(
    context: dict,
    text: str,
    missing_labels: list[str],
    target_clauses: list[dict] | None = None,
) -> tuple[dict | None, int]:
    """Produce a gap diagnosis & follow-up prompt card for uploaded/pasted enterprise policies or half-finished materials."""
    clean_text = (text or "").strip()
    if not clean_text:
        return None, 0
    if call_llm_json is not _DEFAULT_LLM and not getattr(call_llm_json, "_supports_diagnose", False):
        return None, 0
    clause_payload = [
        {"法规": c.get("law", ""), "条号": c.get("article", ""), "要点": str(c.get("point") or c.get("text") or "")[:180]}
        for c in (target_clauses or [])[:8]
    ]
    messages = [
        {"role": "system", "content": DIAGNOSE_SYSTEM},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "业务概况": context,
                    "用户的现有材料": clean_text,
                    "尚未覆盖的关键信息项名称": missing_labels[:20],
                    "本流向适用法规核对要点": clause_payload,
                },
                ensure_ascii=False,
            ),
        },
    ]
    res = await call_llm_json(messages, max_tokens=2200, temperature=0.1)
    tokens = int(getattr(res, "prompt_tokens", 0) or 0) + int(getattr(res, "completion_tokens", 0) or 0)
    data = res.data if isinstance(res.data, dict) else {}
    allowed_numbers = (
        _numbers(clean_text)
        | _numbers(json.dumps(context, ensure_ascii=False))
        | _numbers(json.dumps(clause_payload, ensure_ascii=False))
    )
    doc_kind = str(data.get("doc_kind") or "").strip()[:32] or "现有业务与制度材料"
    summary = str(data.get("summary") or "").strip()
    if not summary or (_numbers(summary) - allowed_numbers):
        return None, tokens
    gaps = [
        str(g).strip()
        for g in (data.get("gaps") or [])
        if str(g or "").strip() and not (_numbers(str(g)) - allowed_numbers)
    ][:6]
    followups = [
        str(f).strip()
        for f in (data.get("followups") or [])
        if str(f or "").strip() and not (_numbers(str(f)) - allowed_numbers)
    ][:5]
    if not gaps and not followups:
        return None, tokens
    return {"doc_kind": doc_kind, "summary": summary, "gaps": gaps, "followups": followups}, tokens


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


async def _ask(questions: list[dict], context: dict, text: str) -> tuple[list, int]:
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": json.dumps(
                {"业务概况": context, "用户的业务说明": text.strip(), "questions": questions}, ensure_ascii=False
            ),
        },
    ]
    res = await call_llm_json(messages, max_tokens=4000, temperature=0.2)
    tokens = int(getattr(res, "prompt_tokens", 0) or 0) + int(getattr(res, "completion_tokens", 0) or 0)
    data = res.data if isinstance(res.data, dict) else {}
    return list(data.get("answers") or []), tokens


async def draft(questions: list[dict], context: dict, text: str) -> tuple[dict[str, dict], int]:
    """questions: [{key,label,question,allow_general}]. Returns ({key: {text, source, quote}}, tokens).
    Asked in small batches at the same time so no single answer is cut off; a failed batch only
    loses its own items, and all failing is reported."""
    batches = [questions[i : i + BATCH] for i in range(0, len(questions), BATCH)]
    results = await asyncio.gather(*(_ask(b, context, text) for b in batches), return_exceptions=True)
    failed = [r for r in results if isinstance(r, Exception)]
    if batches and len(failed) == len(batches):
        raise RuntimeError(str(failed[0]))
    answers = [a for r in results if not isinstance(r, Exception) for a in r[0]]
    tokens = sum(r[1] for r in results if not isinstance(r, Exception))
    asked = {q["key"]: q for q in questions}
    flat_text = _flat(text)
    allowed_numbers = _numbers(text) | _numbers(json.dumps(context, ensure_ascii=False))
    out: dict[str, dict] = {}
    for item in answers:
        if not isinstance(item, dict):
            continue
        key, value = item.get("key"), str(item.get("value") or "").strip()
        source, quote = item.get("source"), str(item.get("quote") or "").strip()
        q = asked.get(key)
        if q is None or not value or len(value) > MAX_VALUE_CHARS:
            continue
        if source == "text":
            if not quote or not flat_text or _flat(quote) not in flat_text:
                continue  # 摘抄对不上原文，就不能说是来自原文
        elif source == "general":
            if not q.get("allow_general"):
                continue
            quote = ""
        else:
            continue
        if _numbers(value) - allowed_numbers or REMARK.search(value):
            continue  # 答案里不许出现原文和概况里没有的数字，也不许夹带评语
        out[key] = {"text": value, "source": source, "quote": quote}
    return out, tokens
