# ruff: noqa: E501
"""Which kind of vehicle-relevant data a data category of the graph is.

The graph keeps every data category a law names — “personal data”, “vehicle location”, but also
“Singapore telephone number” from do-not-call rules or “court records”. A country briefing about
automotive data should lead with the kinds of data a vehicle business handles, so each category
is put into one class of a fixed list by the model (names only, a few thousand tokens per country)
and the class is stored on the node. Classes, not categories, are what the briefing shows.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.llm import LlmUnavailableError, call_llm_json
from app.knowledge.models import KgNode

PROMPT_VERSION = "data-class-v1"
CLASSES: list[tuple[str, str]] = [
    ("personal", "个人数据"),
    ("sensitive", "敏感个人数据"),
    ("biometric", "生物识别数据"),
    ("location", "位置与行驶轨迹"),
    ("image", "车内外图像、音视频"),
    ("vehicle", "车辆运行与诊断数据"),
    ("important", "重要数据与关键基础设施数据"),
    ("security", "网络与系统安全数据"),
    ("other", "其他与车辆业务相关的数据"),
    ("unrelated", "与车辆数据处理无直接关系"),
]
LABEL = dict(CLASSES)
SHOWN = [c for c, _ in CLASSES if c != "unrelated"]
BATCH = 20

SYSTEM = (
    "你在为汽车数据合规平台整理外国法规里出现的“数据类别”。每个类别归入下面一类（只填代码）：\n"
    + "\n".join(f"{c}：{label}" for c, label in CLASSES)
    + "\n判断标准：车企在研发、生产、车联网服务、自动驾驶测试、售后诊断中会处理到的数据，归入相应的类；"
    "法律概念上的“个人数据”“敏感数据”按其本义归类；只在与车辆业务无关的场景出现的（如营销电话号码登记、"
    "法院记录、医疗处方、选举资料）填 unrelated。"
    '输出 JSON：{"items":[{"i":序号,"class":"代码"}]}，序号与输入一致。'
)


def class_of(node: KgNode) -> str | None:
    value = (node.attributes or {}).get("data_class") or {}
    return value.get("class") if isinstance(value, dict) else None


async def classify(session: AsyncSession, country_code: str) -> dict:
    """Classify the country's live data categories that have no class yet. Returns counts."""
    nodes = [
        n
        for n in (
            await session.scalars(
                select(KgNode).where(
                    KgNode.country_code == country_code.upper(),
                    KgNode.node_type == "DATA_CATEGORY",
                    KgNode.review_status.notin_(("stale", "rejected", "superseded")),
                )
            )
        ).all()
        if not class_of(n)
    ]
    done = tokens = 0
    for k in range(0, len(nodes), BATCH):
        batch = nodes[k : k + BATCH]
        listing = "\n".join(
            f"{i}. {n.name_zh}"
            + (f"（{n.name_orig}）" if n.name_orig and n.name_orig != n.name_zh else "")
            for i, n in enumerate(batch)
        )
        try:
            res = await call_llm_json(
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": listing}],
                max_tokens=4000,
                temperature=0.0,
            )
        except LlmUnavailableError:
            continue
        tokens += int(res.prompt_tokens or 0) + int(res.completion_tokens or 0)
        for item in (res.data or {}).get("items", []):
            i, code = item.get("i"), item.get("class")
            i = int(i) if isinstance(i, str) and i.isdigit() else i
            if isinstance(i, int) and 0 <= i < len(batch) and code in LABEL:
                node = batch[i]
                node.attributes = {
                    **(node.attributes or {}),
                    "data_class": {"class": code, "prompt": PROMPT_VERSION},
                }
                done += 1
        await session.commit()
    return {"nodes": len(nodes), "classified": done, "tokens": tokens}
