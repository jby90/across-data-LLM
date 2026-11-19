# ruff: noqa: E501
"""Answering a question about the law inside the conversation, from the library only.

The knowledge base does the work (the same retrieval the reports use, restricted to the country's
in-scope, current clauses); the model only phrases what the retrieved clauses say. Every statement
carries the number of the clause it rests on, figures must come from those clauses, and when the
library has nothing on the point the answer says so instead of guessing."""

from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import clause_gate
from app.analysis import document_narrative as nar
from app.analysis.document_basis import NAMES as COUNTRY_NAMES_ZH
from app.analysis.llm import call_llm_json
from app.knowledge import whitepaper as wp

# which of the eight aspects a question belongs to decides where retrieval looks first
ASPECT = {
    "cross_border": "D3",
    "consent": "D4",
    "retention": "D5",
    "penalty": "D6",
    "admission": "D7",
    "data_category": "D2",
    "general": "D1",
}
ASPECT_TITLE = {
    "D1": "属地数据法规体系",
    "D2": "汽车核心数据定义与分类",
    "D3": "数据跨境传输合规路径与前置条件",
    "D4": "用户授权、告知、同意的合规要求",
    "D5": "数据存储、留存、销毁时效与规范",
    "D6": "违规行为分级、处罚标准、监管追责机制",
    "D7": "属地市场准入认证、申报材料、流程节点要求",
}
SYSTEM = (
    "你是汽车数据合规研究员，回答车企合规人员提出的一个法规问题。只能依据 clauses 里编号的条文作答："
    "不得补充条文以外的法律规定、数字、日期、机构名称，也不得凭对该国的印象写。\n"
    "写 1–2 段，每段不超过 200 字，先直接回答问题，再说关键条件；每个涉及法律要求的论断，在 cites 里写出依据条文的编号 n"
    "（clauses 里的序号，不是法规的条号）。条文没有直接回答的部分，明确写“目前收录的该国法规未涉及这一点”，不要猜。\n"
    "平实口语，不用标题和列表；提到法规时使用 clauses 里给出的中文名称（“名称（年份+编号）”）加条号，如“《个人数据保护法（第09-08号法律）》第43条”；不得出现“条文6”“clauses”“cites”这类编号或字段名。"
    '只输出 JSON：{"paragraphs":[{"text":"","cites":[1]}]}。'
)


async def answer(session: AsyncSession, country: str, question: str, law_topic: str, entities: list[str]) -> str | None:
    """The answer text with its sources, or None when nothing usable was found or checked."""
    from app.analysis.tools import ToolBox

    country_zh = COUNTRY_NAMES_ZH.get(country, country)
    if not country or not country_zh:
        return None
    await clause_gate.load_title_names(session)
    aspect = ASPECT.get(law_topic, "D1") if law_topic != "none" else "D1"
    toolbox = ToolBox(session, country, cache_scope=None)
    skip = await wp.out_of_scope_documents(session, country)
    query = " ".join([question, *entities])
    found: list[dict] = []
    # 依据与准入页、国家简报统一用同一套条文映射与法规名（“名称（年份+编号）”）
    from sqlalchemy import select

    from app.ingestion.display_text import for_reading
    from app.ingestion.models import Document
    from app.knowledge.library_view import article_of
    from app.knowledge.reading import article_label

    names: dict[str, tuple[str, str]] = {}
    units: dict = {}
    for dim in dict.fromkeys([aspect, "D1"]):
        hits = await toolbox.search_effective_clauses(dim, query=query, top_k=8)
        missing = {c.document_id for c in hits if str(c.document_id) not in names}
        if missing:
            for doc_id, orig_title, zh in (
                await session.execute(select(Document.id, Document.title, Document.title_zh).where(Document.id.in_(missing)))
            ).all():
                names[str(doc_id)] = (zh or orig_title or "", orig_title or "")
        for c in hits:
            if not c.text.strip() or str(c.document_id) in skip:
                continue
            # 被切开存放的长条文（如 4.2）按它所属的整条标号
            whole = await article_of(session, c.clause_id, units)
            label = (whole[0].clause_number or c.label) if whole else c.label
            doc_zh, orig_t = names.get(str(c.document_id), (c.document_title, c.document_title))
            unified_title = clause_gate.law_name(doc_zh or c.document_title, str(c.document_id))
            item = {
                "document_title": unified_title,
                "original_title": orig_t,
                "clause_number": article_label(label),
                "quote_original": for_reading(c.text),
                "quote_zh": for_reading(c.translated_text or ""),
            }
            if clause_gate.usable(item):
                found.append(item)
    pool = wp._dedupe(found)
    offered_list = nar._offered(pool, None)[:7]
    if not offered_list:
        return None
    offered = {c["n"]: c for c in offered_list}
    allowed: set[str] = set()
    allowed |= nar.offered_numbers(pool, offered_list)
    allowed |= nar._numbers(question)
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps({"country": country_zh, "question": question, "clauses": offered_list}, ensure_ascii=False)},
    ]
    try:
        res = await call_llm_json(messages, max_tokens=4500, temperature=0.1)
    except Exception:  # noqa: BLE001 - 模型不可用：由调用方给指路，不编造
        return None
    refs: dict = {}
    paragraphs: list[str] = []
    for item in (res.data or {}).get("paragraphs") or []:
        ok, _why = nar._check_paragraph(item, offered, allowed)
        if ok is None or not ok["cites"]:
            continue  # 没有条文依据的段落不要
        paragraphs.append(ok["text"] + nar._cite_line(ok["cites"], offered, refs))
    if not paragraphs:
        return None
    sources = [
        f"[{n}] 《{doc}》{nar._clause_label(clause)}".strip() for (doc, clause), (n, _t) in refs.items()
    ]
    title = ASPECT_TITLE.get(aspect, "")
    tail = f"\n\n依据：{'；'.join(sources)}。完整的{('「' + title + '」') if title else ''}要求在「法规知识库→国家合规简报」里，每条都能点开看原文。"
    return "\n\n".join(paragraphs) + tail
