"""Project validated graph evidence into user-facing compliance topic cards."""

from sqlalchemy import select

from app.ingestion.models import NOT_CITABLE_STATUSES, Clause, Document, DocumentVersion
from app.knowledge.data_classes import class_of
from app.knowledge.graph_evidence import active_edge_filters
from app.knowledge.models import KgEdge, KgNode

TOPICS = {
    "RESTRICTS_CROSS_BORDER": ("transfers", "跨境传输"),
    "REQUIRES_ADMISSION": ("admission", "准入与申报"),
    "PENALIZES": ("penalties", "责任与罚则"),
    "APPLIES_TO_SCENARIO": ("scope", "适用范围"),
    "GOVERNS": ("scope", "适用范围"),
    "REQUIRES": ("obligations", "合规义务"),
    "PROHIBITS": ("restrictions", "限制与禁止"),
    "EXEMPTS": ("exemptions", "例外与豁免"),
    "SUPERVISES": ("regulators", "监管要求"),
}


# 与国家简报同一套方面（claim_tags），顺序即办理时关注的先后
ASPECT_TOPICS = [
    ("transfer", "跨境传输"),
    ("consent", "告知与同意"),
    ("security", "存储与安全"),
    ("admission", "申报与许可"),
    ("governance", "内部管理"),
    ("penalty", "处罚"),
]


async def requirement_topics(session, country_code):
    """The country's compliance topics: every verified requirement of the country page's pools,
    one card each, with its facts and its source as《中文名》第X条."""
    from app.knowledge import whitepaper_graph as wg

    cc = country_code.upper()
    pools, _, _ = await wg.requirement_pools(session, cc)
    refs = wg._Refs(session)
    cards = []
    for aspect, label in ASPECT_TOPICS:
        nodes = pools.get(aspect) or []
        items = await wg._claim_items(refs, nodes, penalty=aspect == "penalty")
        for item in items:
            ref = refs.items[item.ref - 1] if item.ref else None
            if ref is None:
                continue  # 依据条文已不可引用（废止版本等）的不列
            cards.append(
                dict(
                    id=item.node_id,
                    topic=aspect,
                    topic_label=label,
                    title=item.title,
                    facts=[{"label": k, "text": v} for k, v in item.facts],
                    document_id=ref.doc_id,
                    document_title=ref.title,
                    clause_id=ref.clause_id,
                    clause_number=ref.label,
                    source=f"《{ref.title}》{ref.label}",
                    quote=ref.zh or ref.original or "",
                    source_url="",
                    version_no=0,
                )
            )
    return cards


async def topic_cards(session, country_code, document_id=None, clause_id=None):
    stmt = (
        select(KgEdge, Clause, DocumentVersion, Document, KgNode)
        .join(Clause, Clause.id == KgEdge.evidence_clause_id)
        .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(KgNode, KgNode.id == KgEdge.target_node_id)
        .where(
            *active_edge_filters(),
            KgEdge.is_structural.is_(False),
            Document.country_code == country_code.upper(),
            Document.relevance.not_in(("irrelevant", "out_of_scope")),
            DocumentVersion.legal_status.not_in(NOT_CITABLE_STATUSES),
            KgNode.review_status.in_(("approved", "pending_review")),
        )
        .order_by(Document.title, Clause.ordinal, KgEdge.id)
    )
    if document_id:
        stmt = stmt.where(Document.id == document_id)
    if clause_id:
        stmt = stmt.where(Clause.id == clause_id)
    cards = []
    for edge, clause, version, doc, target in (await session.execute(stmt)).all():
        if doc.business_weight == "peripheral":
            continue  # 与车辆数据业务关系较远的文件（谢绝来电登记、机构名单公告等）不列为合规主题
        if target.node_type == "DATA_CATEGORY" and class_of(target) == "unrelated":
            continue  # 只关乎与车辆数据处理无关的数据（如营销电话号码登记），不作为合规主题列出
        topic, label = TOPICS.get(edge.relation_type, ("other", "其他要求"))
        cards.append(
            dict(
                id=str(edge.id),
                topic=topic,
                topic_label=label,
                title=target.name_zh,
                document_id=str(doc.id),
                document_title=doc.title_zh or doc.title,
                clause_id=str(clause.id),
                clause_number=clause.clause_number or str(clause.ordinal),
                version_no=version.version_no,
                source_url=doc.source_url,
                quote=edge.evidence_quote,
                source_label="原文关联要点",
                review_status=edge.review_status,
            )
        )
    return cards
