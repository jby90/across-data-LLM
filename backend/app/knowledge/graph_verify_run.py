# ruff: noqa: E501
"""Run the automatic verification over the graph and record the result on each node and relation.

What is written: ``verify_state`` ("auto" verified, "human_needed" left for a person, "retired"
stopped after two independent readings both disagreed with the clause), the reason in
``verify_note`` and the time. The review status is not touched except for a retirement, which
follows the rule used for the full-graph check of obligations: the item becomes ``stale`` with a
reason that begins "两次独立核对", a history snapshot is kept, and it can be restored. A statement
a person has already approved or rejected is never looked at again."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import text

from app.knowledge import graph_auto_verify as graph_verify
from app.knowledge.history import snapshot
from app.knowledge.models import KgEdge, KgNode

logger = logging.getLogger(__name__)

LATEST = (
    "(select max(version_no) from document_versions x "
    "where x.document_id=v.document_id and x.state='parsed')"
)
NODE_SQL = f"""select n.id, n.node_type, n.name_zh, n.name_orig, n.summary,
  n.attributes->'ontology'->'obligated_party'->>'value',
  n.attributes->'ontology'->'applicable_condition'->>'value',
  c.original_text, (v.version_no = {LATEST})
  from kg_nodes n left join clauses c on c.id=n.evidence_clause_id
  left join document_versions v on v.id=c.version_id
  where n.review_status='pending_review' and n.verify_state is null
    and n.node_type in ({{types}})
    and (cast(:c as text) is null or n.country_code=cast(:c as text))
  order by n.id"""
EDGE_SQL = f"""select e.id, e.relation_type, s.node_type, s.name_zh, t.node_type, t.name_zh,
  e.evidence_quote, c.original_text, (v.version_no = {LATEST})
  from kg_edges e join kg_nodes s on s.id=e.source_node_id join kg_nodes t on t.id=e.target_node_id
  join clauses c on c.id=e.evidence_clause_id join document_versions v on v.id=c.version_id
  where e.review_status='pending_review' and e.verify_state is null
    and (cast(:c as text) is null or e.country_code=cast(:c as text))
  order by e.id"""
# 国家、法规文件、监管机构、数据类别不靠某一条条款成立：只核验它们的关系，不单独核验节点
CLAIM_TYPES = "'OBLIGATION','PENALTY'"
NODE_KEYS = ("id", "node_type", "name_zh", "name_orig", "summary", "party", "condition", "clause", "clause_current")
EDGE_KEYS = (
    "id", "relation_type", "source_type", "source_name", "target_type", "target_name",
    "evidence_quote", "clause", "clause_current",
)


async def verify_graph(
    sf, *, country: str | None = None, kind: str = "all", limit: int | None = None, concurrency: int = 6
) -> dict:
    """``kind``: nodes, edges or all. Safe to repeat: an item already verified is skipped."""
    stats = {"checked": 0, "auto": 0, "retired": 0, "human_needed": 0, "failed": 0}
    items: list[tuple[str, dict]] = []
    async with sf() as s:
        if kind in ("all", "nodes"):
            for row in (await s.execute(text(NODE_SQL.replace("{types}", CLAIM_TYPES)), {"c": country})).all():
                items.append(("node", dict(zip(NODE_KEYS, row, strict=True))))
        if kind in ("all", "edges"):
            for row in (await s.execute(text(EDGE_SQL), {"c": country})).all():
                items.append(("edge", dict(zip(EDGE_KEYS, row, strict=True))))
    if limit:
        items = items[:limit]
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(kind_: str, row: dict) -> None:
        async with gate:
            result = await graph_verify.verify_statement("node" if kind_ == "node" else "edge", row)
        try:
            await _write(sf, kind_, row["id"], result)
        except Exception:  # noqa: BLE001
            stats["failed"] += 1
            logger.exception("图谱核验结果写入失败：%s %s", kind_, row["id"])
            return
        stats["checked"] += 1
        stats[{"auto": "auto", "retire": "retired"}.get(result["verdict"], "human_needed")] += 1

    await asyncio.gather(*(one(k, r) for k, r in items))
    return stats


async def _write(sf, kind: str, item_id, result: dict) -> None:
    model = KgNode if kind == "node" else KgEdge
    state = {"auto": "auto", "retire": "retired"}.get(result["verdict"], "human_needed")
    async with sf() as s:
        obj = await s.get(model, item_id)
        if obj is None or obj.review_status != "pending_review" or obj.verify_state is not None:
            return  # 这期间被人处理过或已核验：不覆盖
        if state == "retired":
            snapshot(s, obj, "before_auto_verification_retire")
        obj.verify_state = state
        obj.verify_note = (result["note"] or "")[:600] or None
        obj.verified_at = datetime.now(UTC)
        if state == "retired":
            obj.review_status = "stale"
            if kind == "node":
                obj.attributes = {
                    **(obj.attributes or {}),
                    "stale_reason": "两次独立核对均判定与条款原文不符：" + (obj.verify_note or "")[:150],
                }
        await s.commit()
