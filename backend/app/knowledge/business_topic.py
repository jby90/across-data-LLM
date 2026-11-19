# ruff: noqa: E501
"""Regulations organised by the business they serve — automotive cross-border data.

For a carmaker moving vehicle data across borders only a few kinds of law matter: personal data
protection and cross-border transfer, cybersecurity and critical infrastructure, vehicle approval,
autonomous driving and road traffic, connected-vehicle telecoms and radio equipment, and the
penalties attached to them. Each regulation of the library gets one topic and a weight:

- core: the law itself or its main implementing rules for that topic (PDPA, its regulations on
  breach notification, the autonomous-vehicle rules, the data protection law of Morocco…);
- supporting: guidance, amendments, appeals, procedure — useful when reading the core;
- peripheral: in the library for completeness but not about this business (the do-not-call
  register, lists of statutory bodies or law-enforcement agencies, commencement notices only).

The user's directory groups by topic with core first and peripheral folded away; the briefing lists
core regulations; citations in the documents and the chat leave peripheral ones out. The model reads
titles only (a few thousand tokens per country); the result is stored on the document.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.llm import LlmUnavailableError, call_llm_json
from app.ingestion.models import Document

TOPICS: list[tuple[str, str]] = [
    ("personal_data", "个人数据保护与跨境传输"),
    ("cybersecurity", "网络安全与关键信息基础设施"),
    ("vehicle", "车辆准入、自动驾驶与道路交通"),
    ("telecom", "车联网通信与无线电设备"),
    ("other", "其他相关法规"),
]
TOPIC_ZH = dict(TOPICS)
WEIGHTS = ("core", "supporting", "peripheral")
BATCH = 30

SYSTEM = (
    "你是汽车数据跨境合规顾问，在整理一个国家的法规库。读者是要把车辆数据（车联网、自动驾驶测试、远程诊断、座舱数据等）"
    "传出或传入这个国家的车企。给每份文件定一个业务类别和重要程度。\n"
    "类别（topic）：" + "；".join(f"{k}={v}" for k, v in TOPICS) + "。\n"
    "重要程度（weight）：\n"
    "- core：该类别的法律本身或主要实施条例，车企做合规一定要看的（如个人数据保护法、数据泄露通知条例、自动驾驶车辆规则、关键信息基础设施条例、跨境传输许可办法、官方申报表）；\n"
    "- supporting：配套、指南、修订、申诉与执法程序、生效公告以外的程序性规定，读核心法时有用；\n"
    "- peripheral：与车辆数据业务基本无关，只是为了完整收录（如营销电话谢绝来电登记、法定机构名单公告、订明执法机构或医护机构名单、"
    "行政机构任命公告、只写生效日期的生效公告、法院改革、与车辆数据无关的消费者权益细则）。\n"
    "注意：生物识别、人脸识别、视频监控的决议与说明，关系到车内摄像头、驾驶员监测，至少算 supporting；"
    "数据泄露通知、跨境传输、数据保护影响评估、云服务数据存放的规定算 core；车辆型式认证、无线电设备型式批准算 core。\n"
    '输出 JSON：{"items":[{"i":序号,"topic":"类别代码","weight":"core|supporting|peripheral"}]}，序号与输入一致。'
)


async def classify(session: AsyncSession, country_code: str | None = None, *, redo: bool = False) -> dict:
    stmt = select(Document).where(Document.relevance.notin_(("irrelevant", "out_of_scope")))
    if country_code:
        stmt = stmt.where(Document.country_code == country_code.upper())
    if not redo:
        stmt = stmt.where(Document.business_topic.is_(None))
    docs = list((await session.scalars(stmt.order_by(Document.country_code, Document.title))).all())
    stats = {"documents": len(docs), "classified": 0, "tokens": 0}
    by_country: dict[str, list[Document]] = {}
    for d in docs:
        by_country.setdefault(d.country_code, []).append(d)
    for cc, items in by_country.items():
        for k in range(0, len(items), BATCH):
            batch = items[k : k + BATCH]
            listing = "\n".join(
                f"{i}. [{d.document_type}] {d.title_zh or ''}｜{d.title}"[:240] for i, d in enumerate(batch)
            )
            try:
                res = await call_llm_json(
                    [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"国家：{cc}\n{listing}"}],
                    max_tokens=8000, temperature=0.0,
                )
            except LlmUnavailableError:
                continue
            stats["tokens"] += int(res.prompt_tokens or 0) + int(res.completion_tokens or 0)
            for item in (res.data or {}).get("items", []) if isinstance(res.data, dict) else []:
                i = item.get("i")
                i = int(i) if isinstance(i, str) and i.isdigit() else i
                topic, weight = item.get("topic"), item.get("weight")
                if isinstance(i, int) and 0 <= i < len(batch) and topic in TOPIC_ZH and weight in WEIGHTS:
                    batch[i].business_topic, batch[i].business_weight = topic, weight
                    stats["classified"] += 1
            await session.commit()
    return stats


def dumps(stats: dict) -> str:
    return json.dumps(stats, ensure_ascii=False)
