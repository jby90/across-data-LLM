"""Hand edits to the knowledge graph: rename, add, re-label, retire, and restore from history.

Every change keeps the graph's guarantees: an edge still cites a clause and a quote that is really
in that clause; nothing is deleted (a retired node or edge is marked superseded); each change is
written to the append-only audit trail before and after, so any version can be restored; an edited
record goes back to ``pending_review`` because the sentence a reviewer approved is no longer the
one in the graph.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.models import Clause, Document, DocumentVersion
from app.knowledge.history import snapshot
from app.knowledge.models import KgEdge, KgNode, KnowledgeAuditRecord

RELATIONS: dict[str, str] = {
    "PUBLISHES": "发布",
    "AMENDS": "修订",
    "REPEALS": "废止",
    "GOVERNS": "约束",
    "PROHIBITS": "禁止",
    "REQUIRES": "强制要求",
    "EXEMPTS": "豁免",
    "SUPERVISES": "监管",
    "PENALIZES": "处罚",
    "APPLIES_TO_SCENARIO": "适用场景",
    "RESTRICTS_CROSS_BORDER": "数据出境限制",
    "REQUIRES_ADMISSION": "准入要求",
}
RELATION_SYNONYMS: dict[str, str] = {
    "INVOLVES_DATA": "GOVERNS",
    "COVERS_DATA": "GOVERNS",
    "PRESCRIBES": "REQUIRES",
    "REGULATES": "SUPERVISES",
    "IMPOSES_PENALTY": "PENALIZES",
    "EXEMPT_UNDER": "EXEMPTS",
    "RESTRICTS_TRANSFER": "RESTRICTS_CROSS_BORDER",
}
NODE_TYPES = ("COUNTRY", "REGULATOR", "INSTRUMENT", "DATA_CATEGORY", "OBLIGATION", "PENALTY")
NEEDS_EVIDENCE = ("OBLIGATION", "PENALTY")
INACTIVE = ("superseded", "stale", "rejected")
EDGE_INACTIVE = ("superseded", "stale", "rejected")
MIN_QUOTE = 8

NODE_RESTORABLE = ("name_zh", "name_orig", "summary", "attributes", "review_status")
EDGE_RESTORABLE = ("relation_type", "review_status")


class GraphEditError(ValueError):
    """The edit would break a guarantee of the graph; the message says which."""


def _flat(text: str) -> str:
    return " ".join((text or "").split())


async def _node(session: AsyncSession, node_id: uuid.UUID) -> KgNode:
    node = await session.get(KgNode, node_id)
    if node is None:
        raise GraphEditError("节点不存在")
    if node.review_status in INACTIVE:
        raise GraphEditError("该节点已停用，不能修改")
    return node


async def _edge(session: AsyncSession, edge_id: uuid.UUID) -> KgEdge:
    edge = await session.get(KgEdge, edge_id)
    if edge is None:
        raise GraphEditError("关系不存在")
    if edge.review_status in ("superseded", "stale"):
        raise GraphEditError("该关系已停用，不能修改")
    return edge


def _mark_edited(node: KgNode, actor_id: uuid.UUID | None, action: str) -> None:
    node.attributes = {
        **(node.attributes or {}),
        "manual_edit": {
            "action": action,
            "by": str(actor_id) if actor_id else None,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    }


async def _clause_country(session: AsyncSession, clause_id: uuid.UUID) -> tuple[Clause, str]:
    clause = await session.get(Clause, clause_id)
    if clause is None:
        raise GraphEditError("依据条款不存在")
    country = await session.scalar(
        select(Document.country_code)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(DocumentVersion.id == clause.version_id)
    )
    return clause, country or ""


async def rename_node(
    session: AsyncSession,
    node_id: uuid.UUID,
    *,
    name_zh: str,
    summary: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> KgNode:
    name = _flat(name_zh)
    if not name or len(name) > 255:
        raise GraphEditError("名称不能为空，且不超过 255 个字符")
    node = await _node(session, node_id)
    snapshot(session, node, "before_manual_edit", actor_id)
    node.name_zh = name
    if summary is not None:
        node.summary = summary.strip() or None
    node.review_status = "pending_review"
    _mark_edited(node, actor_id, "rename")
    await session.flush()
    snapshot(session, node, "after_manual_edit", actor_id)
    return node


async def create_node(
    session: AsyncSession,
    *,
    country_code: str,
    node_type: str,
    name_zh: str,
    name_orig: str | None = None,
    summary: str | None = None,
    evidence_clause_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
) -> KgNode:
    country = country_code.upper()
    name = _flat(name_zh)
    if node_type not in NODE_TYPES:
        raise GraphEditError("节点类型不在六类要素之内")
    if not name or len(name) > 255:
        raise GraphEditError("名称不能为空，且不超过 255 个字符")
    document_id = None
    if node_type in NEEDS_EVIDENCE and evidence_clause_id is None:
        raise GraphEditError("合规义务、违规罚则必须指定依据条款")
    if evidence_clause_id is not None:
        clause, clause_country = await _clause_country(session, evidence_clause_id)
        if clause_country != country:
            raise GraphEditError("依据条款不属于该国家")
        document_id = await session.scalar(
            select(DocumentVersion.document_id).where(DocumentVersion.id == clause.version_id)
        )
    twin = await session.scalar(
        select(KgNode).where(
            KgNode.country_code == country,
            KgNode.node_type == node_type,
            KgNode.name_zh == name,
            KgNode.review_status.notin_(INACTIVE),
        )
    )
    if twin is not None:
        raise GraphEditError("该国家已有同类型同名称的节点")
    node = KgNode(
        country_code=country,
        node_type=node_type,
        node_key=f"manual:{uuid.uuid4().hex[:16]}",
        name_zh=name,
        name_orig=(name_orig or "").strip() or None,
        summary=(summary or "").strip() or None,
        attributes={},
        document_id=document_id,
        evidence_clause_id=evidence_clause_id,
        review_status="pending_review",
    )
    _mark_edited(node, actor_id, "create")
    session.add(node)
    await session.flush()
    snapshot(session, node, "after_manual_create", actor_id)
    return node


async def create_edge(
    session: AsyncSession,
    *,
    source_node_id: uuid.UUID,
    target_node_id: uuid.UUID,
    relation_type: str,
    evidence_clause_id: uuid.UUID,
    evidence_quote: str,
    actor_id: uuid.UUID | None = None,
) -> KgEdge:
    relation_type = RELATION_SYNONYMS.get(relation_type, relation_type)
    if relation_type not in RELATIONS:
        raise GraphEditError("关系类型不在十二类关系之内")
    if source_node_id == target_node_id:
        raise GraphEditError("关系的两端不能是同一个节点")
    source = await _node(session, source_node_id)
    target = await _node(session, target_node_id)
    if source.country_code != target.country_code:
        raise GraphEditError("关系的两端必须属于同一个国家")
    clause, clause_country = await _clause_country(session, evidence_clause_id)
    if clause_country != source.country_code:
        raise GraphEditError("依据条款不属于该国家")
    quote = _flat(evidence_quote)
    if len(quote) < MIN_QUOTE:
        raise GraphEditError("依据原句太短，请粘贴条款中的完整句子")
    if quote not in _flat(clause.original_text):
        raise GraphEditError("依据原句不在所选条款的原文里；请直接从条款原文复制")
    twin = await session.scalar(
        select(KgEdge).where(
            KgEdge.source_node_id == source.id,
            KgEdge.target_node_id == target.id,
            KgEdge.relation_type == relation_type,
            KgEdge.review_status.notin_(EDGE_INACTIVE),
        )
    )
    if twin is not None:
        raise GraphEditError("已有同样的关系")
    edge = KgEdge(
        country_code=source.country_code,
        source_node_id=source.id,
        target_node_id=target.id,
        relation_type=relation_type,
        evidence_clause_id=clause.id,
        evidence_quote=quote,
        extractor_model="manual-edit",
        confidence=1.0,
        judge_verdict="yes",
        judge_reason="人工新增，依据原句已校验在条款原文中",
        is_structural=False,
        review_status="pending_review",
    )
    session.add(edge)
    await session.flush()
    snapshot(session, edge, "after_manual_create", actor_id)
    return edge


async def change_edge_relation(
    session: AsyncSession,
    edge_id: uuid.UUID,
    *,
    relation_type: str,
    actor_id: uuid.UUID | None = None,
) -> KgEdge:
    relation_type = RELATION_SYNONYMS.get(relation_type, relation_type)
    if relation_type not in RELATIONS:
        raise GraphEditError("关系类型不在十二类关系之内")
    edge = await _edge(session, edge_id)
    if edge.relation_type == relation_type:
        raise GraphEditError("关系类型没有变化")
    twin = await session.scalar(
        select(KgEdge).where(
            KgEdge.source_node_id == edge.source_node_id,
            KgEdge.target_node_id == edge.target_node_id,
            KgEdge.relation_type == relation_type,
            KgEdge.id != edge.id,
            KgEdge.review_status.notin_(EDGE_INACTIVE),
        )
    )
    if twin is not None:
        raise GraphEditError("两端之间已有该类型的关系")
    snapshot(session, edge, "before_manual_edit", actor_id)
    edge.relation_type = relation_type
    edge.review_status = "pending_review"
    edge.reviewed_by = None
    edge.reviewed_at = None
    await session.flush()
    snapshot(session, edge, "after_manual_edit", actor_id)
    return edge


async def retire_edge(
    session: AsyncSession, edge_id: uuid.UUID, *, actor_id: uuid.UUID | None = None
) -> KgEdge:
    edge = await _edge(session, edge_id)
    snapshot(session, edge, "before_manual_retire", actor_id)
    edge.review_status = "superseded"
    await session.flush()
    snapshot(session, edge, "after_manual_retire", actor_id)
    return edge


async def retire_node(
    session: AsyncSession, node_id: uuid.UUID, *, actor_id: uuid.UUID | None = None
) -> tuple[KgNode, int]:
    """Retire a node and the relations attached to it (a relation cannot outlive an end)."""
    node = await _node(session, node_id)
    attached = (
        await session.scalars(
            select(KgEdge).where(
                (KgEdge.source_node_id == node.id) | (KgEdge.target_node_id == node.id),
                KgEdge.review_status.notin_(("superseded", "rejected")),
            )
        )
    ).all()
    for edge in attached:
        snapshot(session, edge, "before_manual_retire", actor_id)
        edge.review_status = "superseded"
    snapshot(session, node, "before_manual_retire", actor_id)
    node.review_status = "superseded"
    _mark_edited(node, actor_id, "retire")
    await session.flush()
    snapshot(session, node, "after_manual_retire", actor_id)
    return node, len(attached)


async def restore_from_history(
    session: AsyncSession, audit_id: uuid.UUID, *, actor_id: uuid.UUID | None = None
) -> KgNode | KgEdge:
    """Put a node or edge back to the state a history entry recorded.

    Only the editable fields are restored (name, summary, attributes, status for a node;
    relation and status for an edge); evidence and ends are never rewritten from history."""
    audit = await session.get(KnowledgeAuditRecord, audit_id)
    if audit is None:
        raise GraphEditError("历史记录不存在")
    if audit.target_type == "kg_nodes":
        record, fields = await session.get(KgNode, audit.target_id), NODE_RESTORABLE
    elif audit.target_type == "kg_edges":
        record, fields = await session.get(KgEdge, audit.target_id), EDGE_RESTORABLE
    else:
        raise GraphEditError("这类历史记录不能恢复")
    if record is None:
        raise GraphEditError("对应的节点或关系已不存在")
    snapshot(session, record, "before_restore", actor_id)
    for field in fields:
        if field in audit.payload:
            setattr(record, field, audit.payload[field])
    await session.flush()
    snapshot(session, record, "after_restore", actor_id)
    return record


async def merge_nodes(
    session: AsyncSession,
    keep_id: uuid.UUID,
    drop_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
) -> dict:
    """Fold a duplicate node into the one that is kept.

    Same country and type are required. Every live relation of the duplicate moves to the kept
    node, except one the kept node already has (same other end, same relation), which is retired
    with the duplicate. The duplicate is then retired (never deleted). Everything is snapshotted."""
    if keep_id == drop_id:
        raise GraphEditError("保留的节点和被合并的节点不能是同一个")
    keep = await _node(session, keep_id)
    drop = await _node(session, drop_id)
    if (keep.country_code, keep.node_type) != (drop.country_code, drop.node_type):
        raise GraphEditError("只能合并同一国家、同一类型的节点")
    edges = (
        await session.scalars(
            select(KgEdge).where(
                (KgEdge.source_node_id == drop.id) | (KgEdge.target_node_id == drop.id),
                KgEdge.review_status.notin_(("superseded", "rejected", "stale")),
            )
        )
    ).all()
    moved = retired = 0
    for edge in edges:
        new_source = keep.id if edge.source_node_id == drop.id else edge.source_node_id
        new_target = keep.id if edge.target_node_id == drop.id else edge.target_node_id
        snapshot(session, edge, "before_node_merge", actor_id)
        twin = await session.scalar(
            select(KgEdge.id).where(
                KgEdge.source_node_id == new_source,
                KgEdge.target_node_id == new_target,
                KgEdge.relation_type == edge.relation_type,
                KgEdge.review_status.notin_(EDGE_INACTIVE),
                KgEdge.id != edge.id,
            )
        )
        if new_source == new_target or twin is not None:
            edge.review_status = "superseded"
            retired += 1
        else:
            edge.source_node_id, edge.target_node_id = new_source, new_target
            moved += 1
    snapshot(session, drop, "before_node_merge", actor_id)
    drop.review_status = "superseded"
    drop.attributes = {**(drop.attributes or {}), "merged_into": str(keep.id)}
    await session.flush()
    snapshot(session, drop, "after_node_merge", actor_id)
    return {"edges_moved": moved, "edges_retired": retired}
