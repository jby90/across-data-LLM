# ruff: noqa: E501
"""Which aspect of automotive data compliance a requirement of the graph is about, and how close
it is to a vehicle-data business.

Obligations and penalties were routed to the briefing's aspects by keywords in their names, which
put “notify the regulator before cutting service” under consent and traffic-ticket fines at the
top of the penalties. Each live OBLIGATION / PENALTY node is instead put into one aspect of a fixed
list and one closeness level by the model (name, condition and data scope only — a few hundred
thousand tokens for all five countries, done once; new nodes are tagged when they appear). The tag
is stored on the node and read by the country page, its comparison across countries and the
graph tier of clause retrieval, so all three agree on what a requirement is about.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.llm import LlmUnavailableError, call_llm_json
from app.knowledge.models import KgNode

PROMPT_VERSION = "claim-tag-v2"
ASPECTS: list[tuple[str, str]] = [
    ("transfer", "数据跨境传输：向境外提供或传输数据的条件、批准、合同、充分保护、接收方义务"),
    ("consent", "告知、同意与数据主体权利：告知内容、取得同意、撤回、查阅更正删除等权利的响应"),
    ("security", "存储与安全：本地存储、保存期限、删除销毁、加密与访问控制、安全事件和泄露通知"),
    ("admission", "申报、登记、许可与认证：向主管机构申报或登记、取得许可/授权、设备或车辆认证"),
    ("governance", "内部管理：数据保护负责人、处理记录、影响评估、制度与培训、配合监管检查"),
    ("penalty", "处罚与责任：罚款、监禁、吊销、赔偿等法律后果"),
    ("other", "其他"),
]
ASPECT_ZH = {
    "transfer": "跨境传输",
    "consent": "告知与同意",
    "security": "存储与安全",
    "admission": "申报与许可",
    "governance": "内部管理",
    "penalty": "处罚",
    "other": "其他",
}
CLOSENESS: list[tuple[str, str]] = [
    ("vehicle", "直接涉及车辆、车联网、自动驾驶、车载设备、位置轨迹或车内外影像"),
    ("data", "一般的个人数据或网络数据处理要求，车企处理数据时同样适用"),
    ("unrelated", "与数据处理和车辆数据业务无关（如交通违章、驾驶资格、消费信贷、资费、营销电话）"),
]
ASPECT_CODES = {c for c, _ in ASPECTS}
CLOSE_CODES = {c for c, _ in CLOSENESS}
CLOSE_RANK = {"vehicle": 0, "data": 1, "unrelated": 3}
BATCH = 30
CONCURRENCY = 6
DEAD = ("stale", "rejected", "superseded")

SYSTEM = (
    "你在为汽车数据跨境合规平台整理外国法规里的“要求”（义务或处罚）。每条要求给出两个代码。\n"
    "aspect（这条要求讲的是哪一方面，只选最主要的一个）：\n"
    + "\n".join(f"{c}：{label}" for c, label in ASPECTS)
    + "\nclose（与车企数据业务的关系）：\n"
    + "\n".join(f"{c}：{label}" for c, label in CLOSENESS)
    + "\n判断要点：车辆、车载终端、无线电或通信设备的型式认证、准入许可属于 vehicle（车企出海必须办理）；"
    "只要求“通知”主管机关某项经营变动、断开服务等，不是 consent；"
    "罚则写的是违反数据保护规定的后果，aspect 是 penalty、close 按被罚行为判断；"
    "车企作为数据处理者也会遇到的一般数据保护要求填 data。"
    '只输出 JSON：{"items":[{"i":序号,"aspect":"代码","close":"代码"}]}，序号与输入一致。'
)


def tag_of(node: KgNode) -> dict:
    value = (node.attributes or {}).get("claim_tag") or {}
    return value if isinstance(value, dict) else {}


def aspect_of(node: KgNode) -> str | None:
    return tag_of(node).get("aspect")


def closeness_of(node: KgNode) -> str | None:
    return tag_of(node).get("close")


def _onto(node: KgNode, key: str) -> str:
    val = ((node.attributes or {}).get("ontology") or {}).get(key) or {}
    return str(val.get("value") or "").strip() if isinstance(val, dict) else ""


def _line(i: int, node: KgNode) -> str:
    extra = "；".join(
        x[:80]
        for x in (_onto(node, "applicable_condition"), _onto(node, "data_scope"), _onto(node, "penalty_amount"))
        if x
    )
    kind = "处罚" if node.node_type == "PENALTY" else "义务"
    return f"{i}. [{kind}] {node.name_zh}" + (f"（{extra}）" if extra else "")


async def _ask(batch: list[KgNode], gate: asyncio.Semaphore) -> tuple[dict[int, tuple[str, str]], int]:
    listing = "\n".join(_line(i, n) for i, n in enumerate(batch))
    async with gate:
        for _ in range(2):
            try:
                res = await call_llm_json(
                    [{"role": "system", "content": SYSTEM}, {"role": "user", "content": listing}],
                    max_tokens=8000,
                    temperature=0.0,
                )
                break
            except LlmUnavailableError:
                res = None
        if res is None:
            return {}, 0
    tokens = int(res.prompt_tokens or 0) + int(res.completion_tokens or 0)
    out: dict[int, tuple[str, str]] = {}
    for item in (res.data or {}).get("items", []) if isinstance(res.data, dict) else []:
        i = item.get("i")
        i = int(i) if isinstance(i, str) and i.isdigit() else i
        aspect, close = item.get("aspect"), item.get("close")
        if isinstance(i, int) and 0 <= i < len(batch) and aspect in ASPECT_CODES and close in CLOSE_CODES:
            out[i] = (aspect, close)
    return out, tokens


async def tag(session: AsyncSession, country_code: str | None = None, *, redo: bool = False) -> dict:
    """Tag the live obligations and penalties that have no tag of the current prompt yet (all of
    them with ``redo``)."""
    q = select(KgNode).where(
        KgNode.node_type.in_(("OBLIGATION", "PENALTY")), KgNode.review_status.notin_(DEAD)
    )
    if country_code:
        q = q.where(KgNode.country_code == country_code.upper())
    # 没有标签的、或按旧提示标的（提示改过之后）重标
    nodes = [n for n in (await session.scalars(q)).all() if redo or tag_of(n).get("prompt") != PROMPT_VERSION]
    batches = [nodes[k : k + BATCH] for k in range(0, len(nodes), BATCH)]
    gate = asyncio.Semaphore(CONCURRENCY)
    answers = await asyncio.gather(*(_ask(b, gate) for b in batches))
    done = tokens = 0
    for batch, (got, used) in zip(batches, answers, strict=True):
        tokens += used
        for i, (aspect, close) in got.items():
            node = batch[i]
            node.attributes = {
                **(node.attributes or {}),
                "claim_tag": {"aspect": aspect, "close": close, "prompt": PROMPT_VERSION},
            }
            done += 1
    await session.commit()
    return {"nodes": len(nodes), "tagged": done, "tokens": tokens}
