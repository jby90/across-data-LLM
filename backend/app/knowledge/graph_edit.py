"""Hand edits to the knowledge graph: rename, add, re-label, retire, and restore from history.

Every change keeps the graph's guarantees: an edge still cites a clause and a quote that is really
in that clause; nothing is deleted (a retired node or edge is marked superseded); each change is
written to the append-only audit trail before and after, so any version can be restored; an edited
record goes back to ``pending_review`` because the sentence a reviewer approved is no longer the
one in the graph.
"""

from __future__ import annotations

import re
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

NODE_RESTORABLE = (
    "name_zh",
    "name_orig",
    "summary",
    "attributes",
    "review_status",
    "verify_state",
    "verify_note",
)
EDGE_RESTORABLE = ("relation_type", "review_status", "verify_state", "verify_note")
EDITABLE_ATTR_KEYS = (
    "applicable_industry",
    "data_scope",
    "cross_border_condition",
    "retention_period",
    "authorization_requirement",
    "penalty_amount",
    "violation_level",
    "admission_checklist",
    "obligated_party",
    "applicable_condition",
)


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
    from app.ingestion.models import NOT_CITABLE_STATUSES
    from app.ingestion.safety import held_citation_ids

    clause = await session.get(Clause, clause_id)
    if clause is None:
        raise GraphEditError("依据条款不存在")
    if await held_citation_ids(session, [clause_id]):
        raise GraphEditError("依据条款所属原文处于异常隔离状态，不能作为图谱依据")
    row = (
        await session.execute(
            select(
                Document.country_code,
                Document.relevance,
                DocumentVersion.legal_status,
                DocumentVersion.review_status,
            )
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(DocumentVersion.id == clause.version_id)
        )
    ).first()
    if row is None:
        raise GraphEditError("依据条款所属法规版本不存在")
    country, rel, l_status, rev_status = row
    if (
        l_status in NOT_CITABLE_STATUSES
        or rev_status == "rejected"
        or (rel or "") in ("irrelevant", "out_of_scope")
    ):
        raise GraphEditError("依据条款所属法规已废止、属草案或不在收录范围内，不能作为图谱依据")
    return clause, country or ""


async def rename_node(
    session: AsyncSession,
    node_id: uuid.UUID,
    *,
    name_zh: str,
    summary: str | None = None,
    attributes: dict[str, str] | None = None,
    actor_id: uuid.UUID | None = None,
) -> KgNode:
    name = _flat(name_zh)
    if not name or len(name) > 255:
        raise GraphEditError("名称不能为空，且不超过 255 个字符")
    node = await _node(session, node_id)
    from app.knowledge.extractor import (
        _GENERIC_HOLLOW_PENALTY_NAMES,
        _ULTRA_VAGUE_ENTITY_NAMES_ZH,
    )

    clean_nm = name.replace(" ", "")
    if node.node_type in ("DATA_CATEGORY", "OBLIGATION", "PENALTY") and (
        len(clean_nm) <= 1 or clean_nm in _ULTRA_VAGUE_ENTITY_NAMES_ZH
    ):
        raise GraphEditError(f"节点名称“{name}”过于宽泛空洞，请补充具体的业务或法律限定语")
    if node.node_type == "PENALTY":
        from app.knowledge.extractor import (
            _PENALTY_QUANTIFIER_IN_TARGET_RE,
            _QUOTE_NUMERIC_PENALTY_RE,
        )

        if clean_nm in _GENERIC_HOLLOW_PENALTY_NAMES:
            raise GraphEditError(f"罚则名称“{name}”过于宽泛，请载明具体罚金数额、比例或监禁期限")
        pen_quotes = list(
            (
                await session.scalars(
                    select(KgEdge.evidence_quote).where(
                        KgEdge.target_node_id == node.id,
                        KgEdge.relation_type == "PENALIZES",
                        KgEdge.review_status.notin_(EDGE_INACTIVE),
                    )
                )
            ).all()
        )
        if node.evidence_clause_id is not None:
            cl_obj = await session.get(Clause, node.evidence_clause_id)
            if cl_obj is not None and cl_obj.original_text:
                pen_quotes.append(cl_obj.original_text)
        has_num = any(_QUOTE_NUMERIC_PENALTY_RE.search(q or "") for q in pen_quotes)
        if has_num and not _PENALTY_QUANTIFIER_IN_TARGET_RE.search(name):
            raise GraphEditError(
                f"依据条文载有明确罚金数额、比例或监禁期限，罚则名称“{name}”不得省略量化标准"
            )
    snapshot(session, node, "before_manual_edit", actor_id)
    node.name_zh = name
    if summary is not None:
        node.summary = summary.strip() or None
    if attributes is not None:
        merged_attrs = dict(node.attributes or {})
        for attr_key, raw_val in attributes.items():
            if attr_key not in EDITABLE_ATTR_KEYS:
                raise GraphEditError("不支持修改该要素属性项")
            cleaned_val = _flat(str(raw_val or ""))
            if len(cleaned_val) > 500:
                raise GraphEditError("单项属性内容请控制在 500 个字符以内")
            if cleaned_val:
                merged_attrs[attr_key] = cleaned_val
            else:
                merged_attrs.pop(attr_key, None)
        node.attributes = merged_attrs
    node.review_status = "pending_review"
    node.verify_state = None
    node.verify_note = None
    node.verified_at = None
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
    from sqlalchemy import text as sa_text

    country = country_code.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise GraphEditError("无法识别该国家或地区，请填写标准两字母国家代码或受支持的中文名称")
    from app.knowledge.extractor import (
        _GENERIC_HOLLOW_PENALTY_NAMES,
        _PENALTY_QUANTIFIER_IN_TARGET_RE,
        _QUOTE_NUMERIC_PENALTY_RE,
        _ULTRA_VAGUE_ENTITY_NAMES_ZH,
    )

    name = _flat(name_zh)
    if node_type not in NODE_TYPES:
        raise GraphEditError("节点类型不在六类要素之内")
    if not name or len(name) > 255:
        raise GraphEditError("名称不能为空，且不超过 255 个字符")
    clean_nm = name.replace(" ", "")
    if node_type in ("DATA_CATEGORY", "OBLIGATION", "PENALTY") and (
        len(clean_nm) <= 1 or clean_nm in _ULTRA_VAGUE_ENTITY_NAMES_ZH
    ):
        raise GraphEditError(f"节点名称“{name}”过于宽泛空洞，请补充具体的业务或法律限定语")
    if node_type == "PENALTY" and clean_nm in _GENERIC_HOLLOW_PENALTY_NAMES:
        raise GraphEditError(f"罚则名称“{name}”过于宽泛，请载明具体罚金数额、比例或监禁期限")
    document_id = None
    if node_type in NEEDS_EVIDENCE and evidence_clause_id is None:
        raise GraphEditError("合规义务、违规罚则必须指定依据条款")
    if evidence_clause_id is not None:
        clause, clause_country = await _clause_country(session, evidence_clause_id)
        if clause_country != country:
            raise GraphEditError("依据条款不属于该国家")
        if (
            node_type == "PENALTY"
            and _QUOTE_NUMERIC_PENALTY_RE.search(clause.original_text or "")
            and not _PENALTY_QUANTIFIER_IN_TARGET_RE.search(name)
        ):
            raise GraphEditError(
                f"依据条文载有明确罚金数额、比例或监禁期限，罚则名称“{name}”不得省略量化标准"
            )
        document_id = await session.scalar(
            select(DocumentVersion.document_id).where(DocumentVersion.id == clause.version_id)
        )
    await session.execute(
        sa_text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"kg_node:{country}:{node_type}:{name}"},
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
    from app.knowledge.extractor import (
        ALLOWED_RELATION_ENDPOINT_TYPES,
        align_verbatim_quote,
        validate_entity_specificity_and_penalty_fidelity,
    )
    from app.knowledge.models import NodeType, RelationType

    if source.country_code != target.country_code:
        raise GraphEditError("关系的两端必须属于同一个国家")
    allowed_pairs = ALLOWED_RELATION_ENDPOINT_TYPES.get(RelationType(relation_type), set())
    if (NodeType(source.node_type), NodeType(target.node_type)) not in allowed_pairs:
        raise GraphEditError(
            f"关系类型“{relation_type}”不允许连接“{source.node_type} → {target.node_type}”"
        )
    clause, clause_country = await _clause_country(session, evidence_clause_id)
    if clause_country != source.country_code:
        raise GraphEditError("依据条款不属于该国家")
    quote_flat = _flat(evidence_quote)
    from sqlalchemy import text as sa_text

    if len(quote_flat) < MIN_QUOTE:
        raise GraphEditError("依据原句太短，请粘贴条款中的完整句子")
    aligned = align_verbatim_quote(clause.original_text, evidence_quote)
    if aligned is None and quote_flat not in _flat(clause.original_text):
        raise GraphEditError("依据原句不在所选条款的原文里；请直接从条款原文复制")
    if aligned is None:
        m_ws = re.search(
            r"\s+".join(re.escape(w) for w in quote_flat.split() if w),
            clause.original_text,
        )
        quote = m_ws.group(0) if m_ws else quote_flat
    else:
        quote = aligned
    ok_spec, _, spec_reason = validate_entity_specificity_and_penalty_fidelity(
        relation_type=RelationType(relation_type),
        source_type=NodeType(source.node_type),
        source_name=source.name_zh,
        target_type=NodeType(target.node_type),
        target_name=target.name_zh,
        evidence_quote=quote,
    )
    if not ok_spec:
        raise GraphEditError(spec_reason)
    await session.execute(
        sa_text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"kg_edge:{source.id}:{target.id}:{relation_type}"},
    )
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
    from app.knowledge.extractor import (
        ALLOWED_RELATION_ENDPOINT_TYPES,
        validate_entity_specificity_and_penalty_fidelity,
    )
    from app.knowledge.models import NodeType, RelationType

    edge = await _edge(session, edge_id)
    if edge.relation_type == relation_type:
        raise GraphEditError("关系类型没有变化")
    src_n = await _node(session, edge.source_node_id)
    tgt_n = await _node(session, edge.target_node_id)
    allowed_pairs = ALLOWED_RELATION_ENDPOINT_TYPES.get(RelationType(relation_type), set())
    if (NodeType(src_n.node_type), NodeType(tgt_n.node_type)) not in allowed_pairs:
        raise GraphEditError(
            f"关系类型“{relation_type}”不允许连接“{src_n.node_type} → {tgt_n.node_type}”"
        )
    ok_spec, _, spec_reason = validate_entity_specificity_and_penalty_fidelity(
        relation_type=RelationType(relation_type),
        source_type=NodeType(src_n.node_type),
        source_name=src_n.name_zh,
        target_type=NodeType(tgt_n.node_type),
        target_name=tgt_n.name_zh,
        evidence_quote=edge.evidence_quote or "",
    )
    if not ok_spec:
        raise GraphEditError(spec_reason)
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
    edge.verify_state = None
    edge.verify_note = None
    edge.verified_at = None
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
    from app.knowledge.extractor import (
        ALLOWED_RELATION_ENDPOINT_TYPES,
        validate_entity_specificity_and_penalty_fidelity,
    )
    from app.knowledge.models import NodeType, RelationType

    if audit.target_type == "kg_edges":
        next_status = str(audit.payload.get("review_status", record.review_status))
        next_rel = str(audit.payload.get("relation_type", record.relation_type))
        if next_status not in EDGE_INACTIVE:
            await _clause_country(session, record.evidence_clause_id)
            src_n = await session.get(KgNode, record.source_node_id)
            tgt_n = await session.get(KgNode, record.target_node_id)
            ends_inactive = (
                src_n is None
                or tgt_n is None
                or src_n.review_status in INACTIVE
                or tgt_n.review_status in INACTIVE
            )
            if ends_inactive:
                raise GraphEditError("该关系关联的起点或终点节点已停用，请先恢复对应节点")
            allowed_pairs = ALLOWED_RELATION_ENDPOINT_TYPES.get(RelationType(next_rel), set())
            if (NodeType(src_n.node_type), NodeType(tgt_n.node_type)) not in allowed_pairs:
                raise GraphEditError(
                    f"关系类型“{next_rel}”不允许连接“{src_n.node_type} → {tgt_n.node_type}”"
                )
            ok_spec, _, spec_reason = validate_entity_specificity_and_penalty_fidelity(
                relation_type=RelationType(next_rel),
                source_type=NodeType(src_n.node_type),
                source_name=src_n.name_zh,
                target_type=NodeType(tgt_n.node_type),
                target_name=tgt_n.name_zh,
                evidence_quote=record.evidence_quote or "",
            )
            if not ok_spec:
                raise GraphEditError(spec_reason)
            twin_edge = await session.scalar(
                select(KgEdge.id).where(
                    KgEdge.source_node_id == record.source_node_id,
                    KgEdge.target_node_id == record.target_node_id,
                    KgEdge.relation_type == next_rel,
                    KgEdge.id != record.id,
                    KgEdge.review_status.notin_(EDGE_INACTIVE),
                )
            )
            if twin_edge is not None:
                raise GraphEditError("已存在相同的有效关联关系，无法重复恢复")
    elif audit.target_type == "kg_nodes":
        from app.knowledge.extractor import (
            _GENERIC_HOLLOW_PENALTY_NAMES,
            _PENALTY_QUANTIFIER_IN_TARGET_RE,
            _QUOTE_NUMERIC_PENALTY_RE,
            _ULTRA_VAGUE_ENTITY_NAMES_ZH,
        )

        next_status = str(audit.payload.get("review_status", record.review_status))
        next_name = _flat(str(audit.payload.get("name_zh", record.name_zh)))
        if next_status not in INACTIVE:
            if record.evidence_clause_id is not None:
                await _clause_country(session, record.evidence_clause_id)
            elif record.document_id is not None:
                from app.ingestion.safety import document_is_held

                if await document_is_held(session, record.document_id):
                    raise GraphEditError("所属法规处于异常隔离状态，无法恢复为有效节点")
                ndoc = await session.get(Document, record.document_id)
                if ndoc is not None and ndoc.relevance in ("irrelevant", "out_of_scope"):
                    raise GraphEditError("所属法规不在收录范围内，无法恢复为有效节点")
            clean_nm = next_name.replace(" ", "")
            if record.node_type in ("DATA_CATEGORY", "OBLIGATION", "PENALTY") and (
                len(clean_nm) <= 1 or clean_nm in _ULTRA_VAGUE_ENTITY_NAMES_ZH
            ):
                raise GraphEditError(f"节点名称“{next_name}”过于宽泛空洞，无法恢复为有效节点")
            if record.node_type == "PENALTY":
                if clean_nm in _GENERIC_HOLLOW_PENALTY_NAMES:
                    raise GraphEditError(f"罚则名称“{next_name}”过于宽泛，无法恢复为有效节点")
                pen_quotes = list(
                    (
                        await session.scalars(
                            select(KgEdge.evidence_quote).where(
                                KgEdge.target_node_id == record.id,
                                KgEdge.relation_type == "PENALIZES",
                                KgEdge.review_status.notin_(EDGE_INACTIVE),
                            )
                        )
                    ).all()
                )
                if record.evidence_clause_id is not None:
                    cl_obj = await session.get(Clause, record.evidence_clause_id)
                    if cl_obj is not None and cl_obj.original_text:
                        pen_quotes.append(cl_obj.original_text)
                has_pen_num = any(_QUOTE_NUMERIC_PENALTY_RE.search(q or "") for q in pen_quotes)
                if has_pen_num and not _PENALTY_QUANTIFIER_IN_TARGET_RE.search(next_name):
                    raise GraphEditError(
                        f"依据条文载有明确罚金数额、比例或监禁期限，罚则名称“{next_name}”不得省略量化标准"
                    )
            twin_node = await session.scalar(
                select(KgNode.id).where(
                    KgNode.country_code == record.country_code,
                    KgNode.node_type == record.node_type,
                    KgNode.name_zh == next_name,
                    KgNode.id != record.id,
                    KgNode.review_status.notin_(INACTIVE),
                )
            )
            if twin_node is not None:
                raise GraphEditError("该国家已有同类型同名称的有效节点，无法重复恢复")
    snapshot(session, record, "before_restore", actor_id)
    for field in fields:
        if field in audit.payload:
            setattr(record, field, audit.payload[field])
    restored_status = str(getattr(record, "review_status", ""))
    if (
        restored_status not in INACTIVE
        and "verify_state" not in audit.payload
        and getattr(record, "verify_state", None) == "retired"
    ):
        record.verify_state = None
        record.verify_note = None
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
    from app.knowledge.extractor import validate_entity_specificity_and_penalty_fidelity
    from app.knowledge.models import NodeType, RelationType

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
    touched_edges: list[KgEdge] = []
    for edge in edges:
        new_source = keep.id if edge.source_node_id == drop.id else edge.source_node_id
        new_target = keep.id if edge.target_node_id == drop.id else edge.target_node_id
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
            snapshot(session, edge, "before_node_merge", actor_id)
            edge.review_status = "superseded"
            retired += 1
            touched_edges.append(edge)
            continue
        if edge.evidence_clause_id is not None:
            await _clause_country(session, edge.evidence_clause_id)
            clause = await session.get(Clause, edge.evidence_clause_id)
            quote_clean = (edge.evidence_quote or "").strip()
            if (
                clause is not None
                and quote_clean
                and quote_clean not in (clause.original_text or "")
            ):
                raise GraphEditError("待迁移关系的引文不在关联条文原文中")
        src_obj = keep if new_source == keep.id else await session.get(KgNode, new_source)
        tgt_obj = keep if new_target == keep.id else await session.get(KgNode, new_target)
        if src_obj is not None and tgt_obj is not None:
            ok_spec, _, spec_reason = validate_entity_specificity_and_penalty_fidelity(
                relation_type=RelationType(edge.relation_type),
                source_type=NodeType(src_obj.node_type),
                source_name=src_obj.name_zh,
                target_type=NodeType(tgt_obj.node_type),
                target_name=tgt_obj.name_zh,
                evidence_quote=edge.evidence_quote or "",
            )
            if not ok_spec:
                raise GraphEditError(spec_reason)
        snapshot(session, edge, "before_node_merge", actor_id)
        edge.source_node_id, edge.target_node_id = new_source, new_target
        edge.review_status = "pending_review"
        edge.reviewed_by = None
        edge.reviewed_at = None
        edge.verify_state = None
        edge.verify_note = None
        edge.verified_at = None
        moved += 1
        touched_edges.append(edge)
    snapshot(session, drop, "before_node_merge", actor_id)
    drop.review_status = "superseded"
    drop.attributes = {**(drop.attributes or {}), "merged_into": str(keep.id)}
    await session.flush()
    for edge in touched_edges:
        snapshot(session, edge, "after_node_merge", actor_id)
    snapshot(session, drop, "after_node_merge", actor_id)
    return {"edges_moved": moved, "edges_retired": retired}
