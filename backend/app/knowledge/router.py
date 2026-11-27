# ruff: noqa: E501
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.iam.deps import Principal, require_admin_side, require_platform_admin, require_user_side
from app.ingestion.models import NOT_CITABLE_STATUSES, Clause, Document, DocumentVersion
from app.ingestion.safety import document_available, document_is_held
from app.knowledge.countries import country_catalog
from app.knowledge.extractor import (
    extract_knowledge_for_country,
    seed_glossary_and_review_points,
)
from app.knowledge.graph_evidence import (
    active_edge_filters,
    evidence_attributes,
    ontology_attributes,
)
from app.knowledge.models import (
    ClauseTranslation,
    GlossaryTerm,
    KgEdge,
    KgNode,
    NodeType,
    RelationType,
    TranslationReviewStatus,
)
from app.knowledge.public_view import PublicRegulationDetail, PublicRegulationSummary
from app.knowledge.schemas import (
    ClauseTranslationOut,
    EdgeCreateIn,
    EdgeRelationIn,
    EdgeReviewIn,
    GlossaryTermCreate,
    GlossaryTermOut,
    GlossaryTermReviewIn,
    GraphPayloadOut,
    KgEdgeOut,
    KgNodeOut,
    NodeCreateIn,
    NodeEditIn,
    PublicClauseTranslationOut,
    RegulationClauseItemOut,
    RegulationDetailOut,
    RegulationSummaryOut,
    RestoreIn,
    TranslationReviewIn,
)
from app.knowledge.translator import (
    translate_clause,
    translate_document_latest_version,
    verify_translation,
)
from app.knowledge.user_relations import ReaderRelations, reader_relations
from app.sources.models import SourceSite

AdminSideDep = Annotated[Principal, Depends(require_admin_side)]
PlatformAdminDep = Annotated[Principal, Depends(require_platform_admin)]
UserDep = Annotated[Principal, Depends(require_user_side)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]

# 具体数值类属性：以条款自身抽取（含原句）为准
OWN_CLAUSE_WINS = {"penalty_amount", "retention_period"}

admin_router = APIRouter(prefix="/api/admin/knowledge", tags=["knowledge-admin"])
user_router = APIRouter(prefix="/api/knowledge", tags=["knowledge-user"])


@admin_router.get("/parsed-graph-revisions")
async def parsed_graph_revisions(
    _: PlatformAdminDep,
    session: SessionDep,
    country_code: str = Query(default="SG", pattern="^[A-Za-z]{2}$"),
) -> list[dict]:
    """Page-based graph proposals are for review, never active report/legal evidence."""
    from app.knowledge.history import payload_hash
    from app.knowledge.models import KnowledgeAuditRecord

    rows = (
        await session.scalars(
            select(KnowledgeAuditRecord)
            .join(Clause, Clause.id == KnowledgeAuditRecord.target_id)
            .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                KnowledgeAuditRecord.action == "kg_parsed_source_result",
                Document.country_code == country_code.upper(),
            )
            .order_by(KnowledgeAuditRecord.created_at.desc())
        )
    ).all()
    return [
        dict(
            id=str(row.id),
            clause_id=str(row.target_id),
            content_hash=row.content_hash,
            payload=row.payload,
        )
        for row in rows
        if payload_hash(row.payload) == row.content_hash
    ]


def _status_badge(review_status: str) -> tuple[bool, str]:
    if review_status == TranslationReviewStatus.APPROVED.value:
        return True, "已复核（可作法律依据）"
    if review_status == "native_zh":
        return False, "中文原文（免译）"
    if review_status == "ambiguous_current":
        return False, "多现行译文待消歧（不可作法律依据）"
    if review_status == TranslationReviewStatus.REJECTED.value:
        return False, "已驳回（待重译）"
    if review_status == TranslationReviewStatus.STALE.value:
        return False, "法规已修订，旧译文待重核"
    if review_status == TranslationReviewStatus.TRANSLATION_FAILED.value:
        return False, "待人工复核"
    if review_status == TranslationReviewStatus.NOT_TRANSLATED.value:
        return False, "暂无译文"
    if review_status == TranslationReviewStatus.SUPERSEDED.value:
        return False, "旧版已归档"
    return False, "系统译文（已质检）"


def _clean_glossary_hits(raw_hits: list | None) -> list:
    if not raw_hits:
        return []
    from app.knowledge.legal_terms import is_stopword_term

    cleaned: list = []
    for h in raw_hits:
        if not isinstance(h, dict):
            continue
        src = str(h.get("source_term") or h.get("en") or "").strip()
        tgt = str(h.get("target_zh") or h.get("zh") or "").strip()
        if is_stopword_term(src, tgt):
            continue
        if tgt and tgt.isascii() and tgt == tgt.lower():
            continue
        cleaned.append(h)
    return cleaned


async def _build_translation_outs(
    session: AsyncSession,
    rows: list[tuple[ClauseTranslation, Clause, Document]],
) -> list[ClauseTranslationOut]:
    from app.knowledge.models import KnowledgeAuditRecord

    contexts, parse_revisions = {}, {}
    if rows:
        for record in (
            await session.scalars(
                select(KnowledgeAuditRecord)
                .where(
                    KnowledgeAuditRecord.target_id.in_([clause.id for _, clause, _ in rows]),
                    KnowledgeAuditRecord.action.in_(
                        ("translation_context_revision", "source_parse_revision")
                    ),
                )
                .order_by(KnowledgeAuditRecord.created_at.desc())
            )
        ).all():
            if record.action == "source_parse_revision":
                parse_revisions.setdefault(
                    record.target_id,
                    dict(
                        id=str(record.id),
                        review_status=record.payload["review_status"],
                        content_kind=record.payload["content_kind"],
                        active=False,
                        usable_as_legal_basis=False,
                    ),
                )
                continue
            contexts.setdefault(
                record.target_id,
                dict(
                    id=str(record.id),
                    quality_check_passed=record.payload["quality_check_passed"],
                    review_status=record.payload["review_status"],
                    standalone_translation=False,
                ),
            )
    ambiguous_clause_ids: set[uuid.UUID] = set()
    if rows:
        cids = list({clause.id for _, clause, _ in rows})
        ambiguous_clause_ids = set(
            (
                await session.scalars(
                    select(ClauseTranslation.clause_id)
                    .where(
                        ClauseTranslation.clause_id.in_(cids),
                        ClauseTranslation.is_current.is_(True),
                        ClauseTranslation.quality_check_passed.is_(True),
                        ClauseTranslation.review_status.in_(
                            ("machine_translated", "pending_review", "approved")
                        ),
                    )
                    .group_by(ClauseTranslation.clause_id)
                    .having(func.count(ClauseTranslation.id) > 1)
                )
            ).all()
        )
    engine_zh_map = {
        "llm": "智能翻译引擎",
        "glossary_assisted": "术语辅助翻译",
        "rule": "术语直译",
        "manual": "人工译定",
    }
    out: list[ClauseTranslationOut] = []
    for tr, clause, doc in rows:
        usable, badge = _status_badge(tr.review_status)
        if clause.id in ambiguous_clause_ids:
            usable = False
            badge = "多现行译文待消歧（不可作法律依据）"
        else:
            usable = usable and tr.is_current and tr.quality_check_passed
        out.append(
            ClauseTranslationOut(
                source_context_revision=contexts.get(clause.id),
                source_parse_revision=parse_revisions.get(clause.id),
                is_current=tr.is_current,
                id=tr.id,
                clause_id=clause.id,
                document_id=doc.id,
                country_code=doc.country_code,
                document_title=doc.title,
                clause_number=clause.clause_number,
                heading=clause.heading,
                section_path=clause.section_path,
                original_text=clause.original_text,
                translated_text=tr.translated_text,
                engine=engine_zh_map.get(tr.engine, _scrub_quality_issues_zh([tr.engine])[0] if tr.engine else ""),
                model_name=tr.model_name,
                prompt_version=tr.prompt_version,
                tokens_in=int(tr.tokens_in or 0),
                tokens_out=int(tr.tokens_out or 0),
                duration_ms=int(tr.duration_ms or 0),
                glossary_hits=_clean_glossary_hits(tr.glossary_hits),
                unmatched_terms=tr.unmatched_terms or [],
                quality_check_passed=tr.quality_check_passed,
                quality_issues=_scrub_quality_issues_zh(tr.quality_issues),
                review_status=tr.review_status,
                status=tr.review_status,
                usable_as_legal_basis=usable,
                status_badge=badge,
                reviewer_note=tr.reviewer_note,
                reviewed_at=tr.reviewed_at,
            )
        )
    return out


def _scrub_quality_issues_zh(issues: list | None) -> list[str]:
    import re

    from app.core.jargon import scrub_engineering_jargon

    if not issues:
        return []
    cleaned: list[str] = []
    for raw in issues:
        s = str(raw or "").strip()
        if not s:
            continue
        if s.lower().startswith("llm unavailable"):
            s = "智能翻译服务暂不可用或尚未配置密钥，暂未更新译文"
        s = re.sub(r"LLM\s*服务", "智能翻译服务", s, flags=re.I)
        s = scrub_engineering_jargon(
            s,
            ocr_zh="版面文字识别",
            llm_zh="智能翻译模型",
            rag_zh="法条检索增强",
            json_zh="结构化数据",
            uuid_zh="唯一标识",
            pydantic_zh="字段校验",
            cjk_zh="中文汉字",
        )
        cleaned.append(s)
    return cleaned


_DOC_TYPE_ZH: dict[str, str] = {
    "act": "法律",
    "regulation": "行政法规与规章",
    "subsidiary": "附属立法与通告",
    "guidance": "监管指南与框架",
    "guideline": "监管指南与框架",
    "admission": "准入申报材料",
}


# 官方发布的准入申请须知与表格：不是立法，但车企做准入要用，在用户端单独归为一类，不混进监管指南
ADMISSION_KIND = "ADMISSION_GUIDANCE"


def public_doc_type(doc_type: str | None, instrument_kind: str | None) -> str:
    return "ADMISSION" if instrument_kind == ADMISSION_KIND else (doc_type or "")


def _doc_type_zh(doc_type: str | None, title: str = "") -> str:
    raw = (doc_type or "").lower().strip()
    if raw == "admission":
        return _DOC_TYPE_ZH["admission"]
    t_low = title.lower()
    if "guideline" in t_low or "guide" in t_low or "framework" in t_low:
        return "监管指南与框架"
    if "rules" in t_low or "regulations" in t_low or "notification" in t_low or "order" in t_low:
        return "附属条例与实施规则"
    return _DOC_TYPE_ZH.get(raw, "法律")


def _compute_eight_attributes(
    country_code: str,
    title: str | None = None,
    rel_types: set[str] | None = None,
    data_names: list[str] | None = None,
    obl_names: list[str] | None = None,
) -> dict[str, str]:
    """No country/title templates: unknown dimensions must remain explicitly unknown.

    Data/obligation names alone do not establish industry, retention, penalties or admission rules.
    Evidence-backed attributes are assembled from actual current graph edges by the caller.
    """
    return {
        key: "条文未明确"
        for key in (
            "applicable_industry",
            "data_scope",
            "cross_border_condition",
            "retention_period",
            "authorization_requirement",
            "penalty_amount",
            "violation_level",
            "admission_checklist",
        )
    }


def _active_kg_node_filters(*, include_out_of_scope_structural: bool = False):
    from app.ingestion.layout_safety import clause_layout_available

    doc_rel_cond = (
        Document.relevance != "irrelevant"
        if include_out_of_scope_structural
        else Document.relevance.in_(("relevant", "related"))
    )
    return (
        KgNode.review_status.notin_(("superseded", "stale", "rejected")),
        func.coalesce(KgNode.verify_state, "") != "retired",
        or_(
            KgNode.document_id.is_(None),
            KgNode.document_id.in_(
                select(Document.id).where(
                    document_available(),
                    doc_rel_cond,
                )
            ),
        ),
        or_(
            KgNode.evidence_clause_id.is_(None),
            KgNode.evidence_clause_id.in_(
                select(Clause.id)
                .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    document_available(),
                    clause_layout_available(),
                    doc_rel_cond,
                    DocumentVersion.legal_status.notin_(NOT_CITABLE_STATUSES),
                    DocumentVersion.review_status != "rejected",
                )
            ),
        ),
    )


async def _build_graph_payload(
    session: AsyncSession,
    country_code: str,
    only_approved: bool = False,
    user_view: bool = False,
) -> GraphPayloadOut:
    cc = country_code.upper()
    node_stmt = (
        select(KgNode)
        .where(
            KgNode.country_code == cc,
            *_active_kg_node_filters(include_out_of_scope_structural=not user_view),
        )
        .order_by(KgNode.node_type)
    )
    if only_approved:
        node_stmt = node_stmt.where(KgNode.review_status == "approved")
    elif user_view:
        node_stmt = node_stmt.where(KgNode.review_status.in_(("approved", "pending_review")))
    nodes = list((await session.execute(node_stmt)).scalars().all())
    node_by_id = {n.id: n for n in nodes}

    edge_stmt = (
        select(KgEdge)
        .where(
            KgEdge.country_code == cc,
            func.coalesce(KgEdge.verify_state, "") != "retired",
            *active_edge_filters(),
        )
        .order_by(KgEdge.relation_type)
    )
    if only_approved:
        edge_stmt = edge_stmt.where(KgEdge.review_status == "approved")
    elif user_view:
        edge_stmt = edge_stmt.where(KgEdge.review_status.in_(("approved", "pending_review")))
    edges = list((await session.execute(edge_stmt)).scalars().all())

    superseded_edges_count = int(
        (
            await session.execute(
                select(func.count(KgEdge.id)).where(
                    KgEdge.country_code == cc,
                    KgEdge.review_status == "superseded",
                )
            )
        ).scalar_one()
        or 0
    )

    all_clause_ids = {e.evidence_clause_id for e in edges} | {
        n.evidence_clause_id for n in nodes if n.evidence_clause_id
    }
    clause_ids = {e.evidence_clause_id for e in edges}
    clause_map: dict[uuid.UUID, tuple[str | None, str | None, str]] = {}
    clause_provenance: dict[uuid.UUID, dict] = {}
    own_clauses: dict[uuid.UUID, tuple[str | None, str | None, str]] = {}
    if all_clause_ids:
        c_rows = (
            await session.execute(
                select(
                    Clause.id,
                    Clause.clause_number,
                    func.coalesce(func.nullif(func.btrim(Document.title_zh), ""), Document.title),
                    Clause.original_text,
                    DocumentVersion.id,
                    DocumentVersion.content_hash,
                    Document.source_url,
                )
                .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(Clause.id.in_(all_clause_ids))
            )
        ).all()
        for cid, cnum, dtitle, orig_txt, version_id, content_hash, source_url in c_rows:
            info = (cnum, dtitle, orig_txt or "")
            if cid in clause_ids:
                clause_map[cid] = info
                clause_provenance[cid] = {
                    "evidence_version_id": version_id,
                    "evidence_content_hash": content_hash,
                    "official_source_url": source_url,
                }
            own_clauses[cid] = info

    node_counts = {t.value: 0 for t in NodeType}
    for n in nodes:
        node_counts[n.node_type] = node_counts.get(n.node_type, 0) + 1

    inst_doc_ids = {n.document_id for n in nodes if n.document_id}
    inst_titles: dict[uuid.UUID, str] = {}
    inst_relevances: dict[uuid.UUID, str] = {}
    if inst_doc_ids:
        for did, dtzh, drel in (
            await session.execute(
                select(
                    Document.id,
                    func.coalesce(func.nullif(func.btrim(Document.title_zh), ""), Document.title),
                    Document.relevance,
                ).where(Document.id.in_(inst_doc_ids))
            )
        ).all():
            if dtzh:
                inst_titles[did] = dtzh
            if drel:
                inst_relevances[did] = str(drel)

    from app.knowledge.graph_verify import clean_verify_note, verify_edge, verify_node

    node_verbatim_edge_ids: set[uuid.UUID] = set()
    for e in edges:
        src = node_by_id.get(e.source_node_id)
        dst = node_by_id.get(e.target_node_id)
        if not src or not dst:
            continue
        _, _, orig_txt = clause_map.get(e.evidence_clause_id, (None, None, ""))
        if e.evidence_quote and (not orig_txt or e.evidence_quote in orig_txt):
            node_verbatim_edge_ids.add(src.id)
            node_verbatim_edge_ids.add(dst.id)

    node_verification: dict[uuid.UUID, tuple[str, str]] = {}
    for n in nodes:
        node_verification[n.id] = verify_node(
            n,
            has_current_clause=bool(n.evidence_clause_id and n.evidence_clause_id in own_clauses),
            has_verbatim_edge=(n.id in node_verbatim_edge_ids),
        )

    edge_counts = {r.value: 0 for r in RelationType}
    edge_outs: list[KgEdgeOut] = []
    connected_node_ids: set[uuid.UUID] = set()
    node_rels: dict[uuid.UUID, set[str]] = {}
    node_data_names: dict[uuid.UUID, list[str]] = {}
    node_obl_names: dict[uuid.UUID, list[str]] = {}
    incident_edges: dict[uuid.UUID, list[KgEdge]] = {}
    quote_mismatch_count = 0

    for e in edges:
        src = node_by_id.get(e.source_node_id)
        dst = node_by_id.get(e.target_node_id)
        if not src or not dst:
            continue
        incident_edges.setdefault(src.id, []).append(e)
        if dst.id != src.id:
            incident_edges.setdefault(dst.id, []).append(e)
        connected_node_ids.add(src.id)
        connected_node_ids.add(dst.id)
        node_rels.setdefault(src.id, set()).add(e.relation_type)
        node_rels.setdefault(dst.id, set()).add(e.relation_type)
        if dst.node_type == NodeType.DATA_CATEGORY.value:
            node_data_names.setdefault(src.id, []).append(dst.name_zh)
        if dst.node_type == NodeType.OBLIGATION.value:
            node_obl_names.setdefault(src.id, []).append(dst.name_zh)

        edge_counts[e.relation_type] = edge_counts.get(e.relation_type, 0) + 1
        cnum, dtitle, orig_txt = clause_map.get(e.evidence_clause_id, (None, None, ""))
        if not e.evidence_quote or (orig_txt and e.evidence_quote not in orig_txt):
            quote_mismatch_count += 1
        v_status, v_reason = verify_edge(
            e,
            src_node=src,
            dst_node=dst,
            clause_text=orig_txt,
            src_status=node_verification.get(src.id, ("auto_verified", ""))[0],
            dst_status=node_verification.get(dst.id, ("auto_verified", ""))[0],
        )
        src_display = inst_titles.get(src.document_id) if src.node_type == "INSTRUMENT" and src.document_id else None
        dst_display = inst_titles.get(dst.document_id) if dst.node_type == "INSTRUMENT" and dst.document_id else None
        edge_outs.append(
            KgEdgeOut(
                **clause_provenance.get(e.evidence_clause_id, {}),
                id=e.id,
                country_code=e.country_code,
                source_node_id=e.source_node_id,
                source_name_zh=src_display or src.name_zh,
                source_node_type=src.node_type,
                target_node_id=e.target_node_id,
                target_name_zh=dst_display or dst.name_zh,
                target_node_type=dst.node_type,
                relation_type=e.relation_type,
                evidence_clause_id=e.evidence_clause_id,
                evidence_quote=e.evidence_quote,
                clause_number=cnum,
                document_title=dtitle,
                extractor_model=e.extractor_model,
                confidence=float(getattr(e, "confidence", 0.85)),
                judge_verdict=str(getattr(e, "judge_verdict", "yes")),
                judge_reason=clean_verify_note(getattr(e, "judge_reason", None)) or None,
                is_structural=bool(getattr(e, "is_structural", False)),
                review_status=e.review_status,
                verification_status=v_status,
                verification_reason=clean_verify_note(v_reason),
                usable_as_legal_basis=(
                    e.review_status == "approved"
                    and not e.is_structural
                    and e.judge_verdict == "yes"
                ),
            )
        )

    # 统计本国有效范围内条款总数、未挂接义务/关系的条款数、OCR/阿语需人工核对条款数
    from app.ingestion.layout_safety import clause_layout_available

    valid_ver_rows = (
        await session.scalars(
            select(DocumentVersion)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Document.country_code == cc,
                document_available(),
                _in_scope(),
                DocumentVersion.state == "parsed",
                DocumentVersion.legal_status.notin_(NOT_CITABLE_STATUSES),
                DocumentVersion.review_status != "rejected",
            )
            .order_by(DocumentVersion.document_id, DocumentVersion.version_no.desc())
        )
    ).all()
    latest_valid_by_doc: dict = {}
    for vv in valid_ver_rows:
        latest_valid_by_doc.setdefault(vv.document_id, vv)
    latest_valid_vids = [vv.id for vv in latest_valid_by_doc.values()]
    valid_clause_ids_set = (
        set(
            (
                await session.scalars(
                    select(Clause.id).where(
                        Clause.version_id.in_(latest_valid_vids),
                        clause_layout_available(),
                    )
                )
            ).all()
        )
        if latest_valid_vids
        else set()
    )
    total_clauses = len(valid_clause_ids_set)
    linked_clause_ids = {
        e.evidence_clause_id
        for e in edge_outs
        if e.evidence_clause_id is not None and e.evidence_clause_id in valid_clause_ids_set
    }
    ocr_or_rtl_clauses = (
        int(
            (
                await session.execute(
                    select(func.count(Clause.id))
                    .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(
                        Clause.version_id.in_(latest_valid_vids),
                        clause_layout_available(),
                        or_(
                            DocumentVersion.text_source == "ocr",
                            Document.language == "ar",
                        ),
                    )
                )
            ).scalar_one()
            or 0
        )
        if latest_valid_vids
        else 0
    )
    isolated_ids = [str(n.id) for n in nodes if n.id not in connected_node_ids]
    pending_edges = sum(1 for e in edge_outs if e.review_status == "pending_review")
    approved_edges = sum(1 for e in edge_outs if e.review_status == "approved")
    stale_or_rejected = sum(1 for e in edge_outs if e.review_status in ("stale", "rejected"))
    structural_edges = sum(1 for e in edge_outs if e.is_structural)
    semantic_edges = sum(1 for e in edge_outs if not e.is_structural)
    semantic_unjudged = sum(
        1 for e in edge_outs if not e.is_structural and e.judge_verdict != "yes"
    )
    low_confidence = sum(
        1
        for e in edge_outs
        if not e.is_structural and (e.confidence < 0.75 or e.judge_verdict == "partial")
    )

    node_outs: list[KgNodeOut] = []
    for n in nodes:
        eight_attrs = _compute_eight_attributes(
            cc,
            title=n.name_orig or n.name_zh,
            rel_types=node_rels.get(n.id),
            data_names=node_data_names.get(n.id),
            obl_names=node_obl_names.get(n.id),
        )
        actual, provenance = evidence_attributes(
            incident_edges.get(n.id, ()), node_by_id, clause_map, node_id=n.id
        )
        own, own_provenance = ontology_attributes(
            n.attributes, own_clauses.get(n.evidence_clause_id)
        )
        for key, value in own.items():
            # 处罚金额、留存期限是条款里的具体数字：边的目标名只是概括，条款抽取带数字与
            # 原句，以它为准；其余属性保留边推出的关联（带复核状态），条款抽取的补在没有的地方
            if key in OWN_CLAUSE_WINS or key not in actual:
                actual[key] = value
                provenance[key] = own_provenance[key]
        merged_attrs = {
            **(n.attributes or {}),
            **eight_attrs,
            **actual,
            "attribute_evidence": provenance,
        }
        if n.document_id and n.document_id in inst_relevances:
            merged_attrs["scope"] = (
                "out_of_scope"
                if inst_relevances[n.document_id] in ("out_of_scope", "irrelevant")
                else "in_scope"
            )
        if "verify_note" in merged_attrs and isinstance(merged_attrs["verify_note"], str):
            merged_attrs["verify_note"] = clean_verify_note(merged_attrs["verify_note"])
        nout = KgNodeOut.model_validate(n)
        if n.node_type == "INSTRUMENT" and n.document_id and n.document_id in inst_titles:
            nout.name_zh = inst_titles[n.document_id]
        if nout.summary and ("待人工核对" in nout.summary or "机器抽取" in nout.summary):
            nout.summary = str((n.attributes or {}).get("model_definition_proposal") or "") or None
        nout.attributes = merged_attrs
        nout.verification_status, nout.verification_reason = node_verification.get(
            n.id, ("auto_verified", "")
        )
        node_outs.append(nout)

    doc_scope_rows = (
        await session.execute(
            select(Document.relevance, func.count(Document.id))
            .where(Document.country_code == cc)
            .group_by(Document.relevance)
        )
    ).all()
    doc_scope = {str(r): int(c) for r, c in doc_scope_rows}
    docs_total = sum(doc_scope.values())
    docs_out_of_scope = doc_scope.get("out_of_scope", 0) + doc_scope.get("irrelevant", 0)
    source_held_count = int(
        await session.scalar(
            select(func.count(Document.id)).where(
                Document.country_code == cc,
                ~document_available(),
            )
        )
        or 0
    )
    from app.core.config import get_settings
    from app.ingestion.archive import ArchiveStore
    from app.ingestion.binding_status import source_binding_statuses

    binding_statuses = (
        await source_binding_statuses(session, ArchiveStore(get_settings().archive_dir), cc)
        if source_held_count and not user_view
        else []
    )
    replaced_sources = sum(x["status"] == "replacement_available" for x in binding_statuses)

    return GraphPayloadOut(
        country_code=cc,
        node_counts_by_type=node_counts,
        edge_counts_by_relation=edge_counts,
        quality_summary={
            "documents_total": docs_total,
            "documents_core": doc_scope.get("relevant", 0),
            "documents_related": doc_scope.get("related", 0),
            "documents_out_of_scope": docs_out_of_scope,
            "documents_extracted": max(0, docs_total - docs_out_of_scope - source_held_count),
            "source_integrity_held_documents": source_held_count,
            "source_integrity_unresolved_documents": source_held_count - replaced_sources,
            "source_integrity_replaced_documents": replaced_sources,
            "source_integrity_records": binding_statuses,
            "structural_edges": structural_edges,
            "semantic_edges": semantic_edges,
            "semantic_unjudged": semantic_unjudged,
            "low_confidence": low_confidence,
            "quote_mismatch": quote_mismatch_count,
            "superseded_edges_count": superseded_edges_count,
            "isolated_nodes_count": len(isolated_ids),
            "isolated_node_ids": isolated_ids,
            "total_clauses_count": total_clauses,
            "linked_clauses_count": len(linked_clause_ids),
            "unlinked_clauses_count": max(0, total_clauses - len(linked_clause_ids)),
            "pending_review_edges_count": pending_edges,
            "approved_edges_count": approved_edges,
            "auto_verified_edges_count": sum(
                1 for e in edge_outs if e.verification_status == "auto_verified"
            ),
            "needs_confirmation_edges_count": sum(
                1 for e in edge_outs if e.verification_status == "needs_confirmation"
            ),
            "approved_nodes_count": sum(
                1 for n in node_outs if n.verification_status == "approved"
            ),
            "auto_verified_nodes_count": sum(
                1 for n in node_outs if n.verification_status == "auto_verified"
            ),
            "needs_confirmation_nodes_count": sum(
                1 for n in node_outs if n.verification_status == "needs_confirmation"
            ),
            "stale_or_rejected_edges_count": stale_or_rejected,
            "quote_mismatch_edges_count": quote_mismatch_count,
            "ocr_or_rtl_clauses_count": ocr_or_rtl_clauses,
        },
        nodes=node_outs,
        edges=edge_outs,
    )


# ==================== 管理端接口 ====================


@admin_router.post("/extract")
async def trigger_country_extraction(
    _: PlatformAdminDep,
    session: SessionDep,
    country_code: str = Query(default="SG", min_length=2, max_length=2),
    translate_sample_per_doc: int = Query(default=0, ge=0, le=50),
    max_clauses_per_doc: int | None = Query(default=None, ge=1, le=500),
) -> dict:
    """一键初始化术语库/解读要点,并对指定国家执行六要素+十二关系知识图谱抽取与核心条款翻译。"""
    res = await extract_knowledge_for_country(
        session,
        country_code.upper(),
        translate_sample_per_doc=translate_sample_per_doc,
        max_clauses_per_doc=max_clauses_per_doc,
    )
    if isinstance(res, dict) and res.get("reason"):
        import re as _re

        reason_map = {
            "country_extraction_already_running": "该国知识图谱正在后台梳理中，请稍候刷新",
            "document_relevance_unavailable": "相关法规范围尚未核定完成，已保留既有图谱结果",
            "no_parsed_documents": "当前国家暂无已解析完成的法规条文",
        }
        raw_r = str(res["reason"])
        if raw_r in reason_map:
            res = {**res, "reason": reason_map[raw_r]}
        elif _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Timeout)", raw_r):
            res = {**res, "reason": "智能分析服务暂不可用或连接超时，已保留既有图谱结果"}
    return res


@admin_router.get("/glossary", response_model=list[GlossaryTermOut])
async def list_glossary_terms(
    _: AdminSideDep,
    session: SessionDep,
    country_code: str | None = None,
    review_status: str | None = None,
) -> list[GlossaryTerm]:
    await seed_glossary_and_review_points(session)
    stmt = select(GlossaryTerm).order_by(
        GlossaryTerm.priority_level,
        GlossaryTerm.country_code.nulls_last(),
        GlossaryTerm.concept_code,
    )
    if country_code:
        cc = country_code.upper()
        stmt = stmt.where(or_(GlossaryTerm.country_code == cc, GlossaryTerm.country_code.is_(None)))
    if review_status:
        stmt = stmt.where(GlossaryTerm.review_status == review_status)
    return list((await session.execute(stmt)).scalars().all())


@admin_router.post("/glossary", response_model=GlossaryTermOut, status_code=status.HTTP_201_CREATED)
async def create_glossary_term(
    body: GlossaryTermCreate,
    user: AdminSideDep,
    session: SessionDep,
) -> GlossaryTerm:
    import re

    from sqlalchemy import text as sa_text
    from sqlalchemy.exc import IntegrityError

    from app.knowledge.legal_terms import is_stopword_term

    src_clean = body.source_term.strip()
    zh_clean = body.target_zh.strip()
    lang_clean = body.language.lower().strip()
    cc_clean = body.country_code.upper().strip() if body.country_code and body.country_code.strip() else None
    if not src_clean or not zh_clean:
        raise HTTPException(status_code=422, detail="外文原词和标准中文译名均不能为空")
    if is_stopword_term(src_clean, zh_clean):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="不能将常见英文介词、冠词或无效同词映射收录为专业法律术语",
        )
    if body.evidence_clause_id is not None:
        clause_exists = await session.scalar(select(Clause.id).where(Clause.id == body.evidence_clause_id))
        if clause_exists is None:
            raise HTTPException(status_code=422, detail="关联的依据条款不存在，请重新选择")
    await session.execute(
        sa_text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"glossary:{cc_clean or ''}:{lang_clean}:{src_clean.lower()}"},
    )
    dup_stmt = select(GlossaryTerm.id).where(
        GlossaryTerm.language == lang_clean,
        func.lower(GlossaryTerm.source_term) == src_clean.lower(),
    )
    dup_stmt = dup_stmt.where(
        GlossaryTerm.country_code == cc_clean if cc_clean else GlossaryTerm.country_code.is_(None)
    )
    if await session.scalar(dup_stmt.limit(1)) is not None:
        raise HTTPException(status_code=409, detail="该语种与国别下已存在相同的专业术语词条")
    raw_code = (body.concept_code or "").strip()
    if not raw_code:
        slug = re.sub(r"[^a-z0-9]+", "_", src_clean.lower()).strip("_")[:48]
        raw_code = slug or f"term_{uuid.uuid4().hex[:8]}"
    term = GlossaryTerm(
        concept_code=raw_code,
        country_code=cc_clean,
        language=lang_clean,
        source_term=src_clean,
        target_zh=zh_clean,
        priority_level=body.priority_level,
        statutory_definition=body.statutory_definition,
        source_reference=body.source_reference,
        evidence_clause_id=body.evidence_clause_id,
        review_status="approved",
        reviewed_by=user.user_id,
        reviewed_at=datetime.now(UTC),
    )
    session.add(term)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="该专业术语已存在或关联条款无效") from None
    await session.refresh(term)
    return term


@admin_router.post("/glossary/{term_id}/review", response_model=GlossaryTermOut)
async def review_glossary_term(
    term_id: uuid.UUID,
    body: GlossaryTermReviewIn,
    user: AdminSideDep,
    session: SessionDep,
) -> GlossaryTerm:
    from app.knowledge.legal_terms import is_stopword_term

    term = await session.get(GlossaryTerm, term_id)
    if term is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="术语不存在")
    next_target = body.target_zh.strip() if body.target_zh else term.target_zh
    if body.decision == "approved" and is_stopword_term(term.source_term, next_target):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="常见英文介词、冠词或无效同词映射不可审核通过为强制法律术语",
        )
    term.review_status = body.decision
    if body.target_zh:
        term.target_zh = next_target
    if body.statutory_definition is not None:
        term.statutory_definition = body.statutory_definition
    term.reviewed_by = user.user_id
    term.reviewed_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(term)
    return term


_SUBSTANTIVE_CLAUSE_REGEX = (
    r"[\u4e00-\u9fff]{2,}"
    r"|([A-Za-z\u00C0-\u024F]{2,}[^A-Za-z\u00C0-\u024F]+[A-Za-z\u00C0-\u024F]{2,})"
    r"|([\u0600-\u06FF]{2,}[^\u0600-\u06FF]+[\u0600-\u06FF]{2,})"
)


@admin_router.get("/translations", response_model=list[ClauseTranslationOut])
async def list_translations(
    response: Response,
    _: AdminSideDep,
    session: SessionDep,
    country_code: str | None = None,
    review_status: str | None = None,
    document_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=2147483647),
    include_history: bool = False,
    include_short: bool = Query(default=True),
) -> list[ClauseTranslationOut]:
    from sqlalchemy import case

    # 审核队列：只列每份法规当前版本的条款（同一法规的旧版本不重复出现），范围外的不列；
    # 先核心法规、再配套、最后关系较远的文件（同级按业务类别），同一份法规按条文顺序
    latest = (
        select(DocumentVersion.document_id, func.max(DocumentVersion.version_no).label("v"))
        .where(DocumentVersion.state == "parsed")
        .group_by(DocumentVersion.document_id)
        .subquery()
    )
    weight = case(
        (Document.business_weight == "core", 0), (Document.business_weight == "supporting", 1),
        (Document.business_weight == "peripheral", 3), else_=2,
    )
    from app.knowledge.business_topic import TOPICS

    # 同为核心法规的，个人数据保护与跨境传输在前，再网络安全、车辆准入、车联网通信
    topic = case(*[(Document.business_topic == k, i) for i, (k, _) in enumerate(TOPICS)], else_=len(TOPICS))
    stmt = (
        select(ClauseTranslation, Clause, Document)
        .join(Clause, Clause.id == ClauseTranslation.clause_id)
        .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(latest, (latest.c.document_id == Document.id) & (latest.c.v == DocumentVersion.version_no))
        .where(
            document_available(),
            Document.relevance.notin_(("irrelevant", "out_of_scope")),
            DocumentVersion.review_status != "rejected",
            DocumentVersion.legal_status.notin_(NOT_CITABLE_STATUSES),
        )
        .order_by(weight, topic, Document.title, Clause.ordinal, ClauseTranslation.id)
    )
    if country_code:
        stmt = stmt.where(Document.country_code == country_code.upper())
    if review_status:
        stmt = stmt.where(ClauseTranslation.review_status == review_status)
    elif not include_history:
        if not include_short:
            stmt = stmt.where(
                ClauseTranslation.quality_check_passed.is_(True),
                ClauseTranslation.review_status.in_(
                    ("approved", "machine_translated", "pending_review")
                ),
            )
        else:
            stmt = stmt.where(
                ClauseTranslation.review_status != TranslationReviewStatus.SUPERSEDED.value
            )
    if document_id:
        stmt = stmt.where(Document.id == document_id)
    if not include_history and review_status != "superseded":
        stmt = stmt.where(ClauseTranslation.is_current.is_(True))
    if not include_short:
        stmt = stmt.where(
            or_(
                Document.country_code == "CN",
                Document.language == "zh",
                Clause.original_text.op("~")(r"[\u4e00-\u9fff]{2,}"),
                (
                    (func.length(func.btrim(Clause.original_text)) >= 16)
                    & Clause.original_text.op("~")(_SUBSTANTIVE_CLAUSE_REGEX)
                ),
            )
        )
    total = await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    response.headers["X-Total-Count"] = str(total)
    rows = list((await session.execute(stmt.offset(offset).limit(limit))).all())
    return await _build_translation_outs(session, rows)


@admin_router.post("/clauses/{clause_id}/translate", response_model=ClauseTranslationOut)
async def trigger_clause_translate(
    clause_id: uuid.UUID,
    _: PlatformAdminDep,
    session: SessionDep,
) -> ClauseTranslationOut:
    row = (
        await session.execute(
            select(Clause, Document)
            .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(Clause.id == clause_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="条款不存在")
    clause, doc = row
    if await document_is_held(session, doc.id):
        raise HTTPException(409, "原文绑定异常已隔离，请先核查来源后再翻译")
    tr = await translate_clause(session, clause, doc.country_code)
    await session.commit()
    outs = await _build_translation_outs(session, [(tr, clause, doc)])
    return outs[0]


@admin_router.post("/documents/{document_id}/translate")
async def trigger_document_translate(
    document_id: uuid.UUID,
    _: PlatformAdminDep,
    session: SessionDep,
    limit_clauses: int | None = Query(default=None, ge=1, le=500),
) -> dict:
    if await document_is_held(session, document_id):
        raise HTTPException(409, "原文绑定异常已隔离，请先核查来源后再翻译")
    trs = await translate_document_latest_version(session, document_id, limit_clauses=limit_clauses)
    return {
        "document_id": str(document_id),
        "attempted_count": len(trs),
        "translated_count": sum(tr.quality_check_passed for tr in trs),
        "failed_count": sum(not tr.quality_check_passed for tr in trs),
        "limited": limit_clauses is not None,
    }


@admin_router.post("/translations/{translation_id}/review", response_model=ClauseTranslationOut)
async def review_clause_translation(
    translation_id: uuid.UUID,
    body: TranslationReviewIn,
    user: AdminSideDep,
    session: SessionDep,
) -> ClauseTranslationOut:
    row = (
        await session.execute(
            select(ClauseTranslation, Clause, Document)
            .join(Clause, Clause.id == ClauseTranslation.clause_id)
            .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(ClauseTranslation.id == translation_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="翻译记录不存在")
    tr, clause, doc = row

    if body.decision == "approved" and await document_is_held(session, doc.id):
        raise HTTPException(409, "来源绑定异常尚未解决，不可审批为法律依据")
    from app.ingestion.models import ClauseReading
    from app.ingestion.noise import clean_text
    from app.knowledge.translator import unwrap_layout_lines

    proposed_text = (body.translated_text or tr.translated_text).strip()
    rd_txt = await session.scalar(
        select(ClauseReading.reading_text).where(ClauseReading.clause_id == clause.id)
    )
    cleaned_src = unwrap_layout_lines(clean_text(rd_txt or clause.original_text))
    passed, issues, checked_hits = verify_translation(
        cleaned_src, proposed_text, tr.glossary_hits or []
    )
    if not passed and cleaned_src != clause.original_text:
        passed_raw, issues_raw, hits_raw = verify_translation(
            clause.original_text, proposed_text, tr.glossary_hits or []
        )
        if passed_raw:
            passed, issues, checked_hits = passed_raw, issues_raw, hits_raw
    if body.decision == "approved" and (not passed or tr.review_status in ("superseded", "stale")):
        clean_issues = _scrub_quality_issues_zh((issues or [])[:3])
        suffix = f"（{'；'.join(clean_issues)}）" if clean_issues else "（历史或已失效版本请另建修订）"
        raise HTTPException(
            status_code=409,
            detail=f"该译文不能审批通过，请先修正校验问题{suffix}",
        )
    return await _apply_translation_review(session, tr, clause, doc, body, proposed_text, passed, issues, checked_hits, user.user_id)


async def _apply_translation_review(session, tr, clause, doc, body, proposed_text, passed, issues, checked_hits, actor_id):
    from app.admission.router import _BASIS_MEMO, _DETAIL_MEMO
    from app.knowledge.history import snapshot

    await session.execute(select(Clause.id).where(Clause.id == clause.id).with_for_update())
    snapshot(session, tr, "before_human_review", actor_id)
    effective_passed = False if body.decision == "rejected" else passed
    if proposed_text != tr.translated_text:
        # Corrections are new revisions. Approval of the old text is never inherited.
        old = tr
        if old.is_current:
            siblings = (
                await session.scalars(
                    select(ClauseTranslation).where(
                        ClauseTranslation.clause_id == old.clause_id,
                        ClauseTranslation.is_current.is_(True),
                    )
                )
            ).all()
            for sib in siblings:
                sib.is_current = False
                if sib.review_status not in ("approved", "rejected"):
                    sib.review_status = "superseded"
        tr = ClauseTranslation(
            clause_id=old.clause_id,
            translated_text=proposed_text,
            engine="human_revision",
            model_name=old.model_name,
            prompt_version="human-edit",
            glossary_hits=checked_hits,
            unmatched_terms=old.unmatched_terms,
            quality_check_passed=effective_passed,
            quality_issues=issues,
            is_current=True,
        )
        session.add(tr)
        await session.flush()
    tr.quality_check_passed = effective_passed
    tr.quality_issues = issues
    tr.glossary_hits = checked_hits
    tr.review_status = body.decision
    tr.reviewer_id = actor_id
    tr.reviewer_note = body.reviewer_note
    tr.reviewed_at = datetime.now(UTC)
    tr.updated_at = datetime.now(UTC)
    snapshot(session, tr, "after_human_review", actor_id)
    await session.commit()
    _BASIS_MEMO.pop(str(clause.id), None)
    _DETAIL_MEMO.clear()
    outs = await _build_translation_outs(session, [(tr, clause, doc)])
    return outs[0]


async def _country_catalog(session: AsyncSession, codes, *, include_sites=False):
    sites = (await session.execute(select(SourceSite.country_code, SourceSite.config))).all()
    discovered = set(codes)
    if include_sites:
        discovered.update(code for code, _ in sites)
    return country_catalog(discovered, sites)


async def _count_needs_confirmation_edges(
    session: AsyncSession, country_codes: list[str]
) -> dict[str, int]:
    """Compute exact needs_confirmation_edges_count per country aligned 100% with _build_graph_payload."""
    if not country_codes:
        return {}
    from app.knowledge.graph_verify import verify_edge, verify_node

    nodes = list(
        (
            await session.scalars(
                select(KgNode).where(
                    KgNode.country_code.in_(country_codes),
                    *_active_kg_node_filters(include_out_of_scope_structural=True),
                )
            )
        ).all()
    )
    node_by_id = {n.id: n for n in nodes}
    edges = list(
        (
            await session.scalars(
                select(KgEdge).where(
                    KgEdge.country_code.in_(country_codes),
                    *active_edge_filters(),
                )
            )
        ).all()
    )
    clause_ids = {e.evidence_clause_id for e in edges if e.evidence_clause_id}
    own_clause_ids = {n.evidence_clause_id for n in nodes if n.evidence_clause_id}
    clause_texts: dict[uuid.UUID, str] = {}
    if clause_ids:
        for cid, otxt in (
            await session.execute(
                select(Clause.id, Clause.original_text).where(Clause.id.in_(clause_ids))
            )
        ).all():
            clause_texts[cid] = otxt or ""
    present_own: set[uuid.UUID] = set()
    if own_clause_ids:
        present_own = set(
            (
                await session.scalars(
                    select(Clause.id)
                    .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
                    .where(Clause.id.in_(own_clause_ids), DocumentVersion.state == "parsed")
                )
            ).all()
        )
    verbatim_node_ids: set[uuid.UUID] = set()
    for e in edges:
        src = node_by_id.get(e.source_node_id)
        dst = node_by_id.get(e.target_node_id)
        if not src or not dst or src.country_code != e.country_code or dst.country_code != e.country_code:
            continue
        orig_txt = clause_texts.get(e.evidence_clause_id, "")
        if e.evidence_quote and (not orig_txt or e.evidence_quote in orig_txt):
            verbatim_node_ids.add(src.id)
            verbatim_node_ids.add(dst.id)
    node_status: dict[uuid.UUID, str] = {
        n.id: verify_node(
            n,
            has_current_clause=bool(n.evidence_clause_id and n.evidence_clause_id in present_own),
            has_verbatim_edge=(n.id in verbatim_node_ids),
        )[0]
        for n in nodes
    }
    counts: dict[str, int] = {}
    for e in edges:
        src = node_by_id.get(e.source_node_id)
        dst = node_by_id.get(e.target_node_id)
        if not src or not dst or src.country_code != e.country_code or dst.country_code != e.country_code:
            continue
        v_status, _ = verify_edge(
            e,
            src_node=src,
            dst_node=dst,
            clause_text=clause_texts.get(e.evidence_clause_id, ""),
            src_status=node_status.get(src.id, "auto_verified"),
            dst_status=node_status.get(dst.id, "auto_verified"),
        )
        if v_status == "needs_confirmation":
            counts[e.country_code] = counts.get(e.country_code, 0) + 1
    return counts


@admin_router.get("/countries")
async def get_admin_knowledge_countries(_: AdminSideDep, session: SessionDep) -> list[dict]:
    """Read-only discovery; empty out_of_scope-only shell countries (e.g. DE) are excluded."""
    codes = (
        await session.scalars(select(Document.country_code).union(select(KgNode.country_code)))
    ).all()
    catalog = await _country_catalog(session, codes, include_sites=True)
    doc_stats = {
        cc: (int(cnt or 0), upd.isoformat() if upd else None)
        for cc, cnt, upd in (
            await session.execute(
                select(
                    Document.country_code,
                    func.count(func.distinct(Document.id)),
                    func.max(
                        func.coalesce(
                            DocumentVersion.parsed_at,
                            DocumentVersion.fetched_at,
                            Document.last_checked_at,
                            Document.discovered_at,
                        )
                    ),
                )
                .select_from(Document)
                .outerjoin(
                    DocumentVersion,
                    (DocumentVersion.document_id == Document.id)
                    & (DocumentVersion.state == "parsed"),
                )
                .where(_in_scope())
                .group_by(Document.country_code)
            )
        ).all()
    }
    active_catalog = [
        c
        for c in catalog
        if c["country_code"] not in ("ZZ", "INTL")
        and doc_stats.get(c["country_code"], (0, None))[0] > 0
    ]
    latest = (
        select(DocumentVersion.document_id, func.max(DocumentVersion.version_no).label("v"))
        .where(DocumentVersion.state == "parsed")
        .group_by(DocumentVersion.document_id)
        .subquery()
    )
    pending_tr = dict(
        (
            await session.execute(
                select(Document.country_code, func.count(ClauseTranslation.id))
                .select_from(ClauseTranslation)
                .join(Clause, Clause.id == ClauseTranslation.clause_id)
                .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .join(
                    latest,
                    (latest.c.document_id == Document.id)
                    & (latest.c.v == DocumentVersion.version_no),
                )
                .where(
                    _in_scope(),
                    Document.country_code != "CN",
                    func.coalesce(Document.language, "") != "zh",
                    ClauseTranslation.is_current.is_(True),
                    ClauseTranslation.review_status.notin_(("superseded", "approved")),
                    ~(
                        ClauseTranslation.quality_check_passed.is_(True)
                        & (ClauseTranslation.review_status == "machine_translated")
                    ),
                    func.length(Clause.original_text) >= 24,
                )
                .group_by(Document.country_code)
            )
        ).all()
    )
    pending_kg = await _count_needs_confirmation_edges(
        session, [c["country_code"] for c in active_catalog]
    )
    return [
        dict(
            **c,
            documents_count=doc_stats.get(c["country_code"], (0, None))[0],
            last_updated_at=doc_stats.get(c["country_code"], (0, None))[1],
            pending_translations_count=0
            if c["country_code"] == "CN"
            else int(pending_tr.get(c["country_code"], 0) or 0),
            pending_kg_count=int(pending_kg.get(c["country_code"], 0) or 0),
        )
        for c in active_catalog
    ]


_GRAPH_JSON_CACHE: dict[tuple[str, bool], str] = {}


@admin_router.get("/graph", response_model=GraphPayloadOut)
async def get_admin_knowledge_graph(
    _: PlatformAdminDep,
    session: SessionDep,
    country_code: str = Query(default="SG", min_length=2, max_length=2),
    only_approved: bool = False,
) -> Response:
    from app.knowledge.cache_sync import use_cached

    key = (country_code.upper(), bool(only_approved))
    hit = _GRAPH_JSON_CACHE.get(key)
    if use_cached(hit):
        return Response(content=hit, media_type="application/json")
    payload = await _build_graph_payload(session, country_code, only_approved=only_approved)
    raw = payload.model_dump_json()
    if use_cached(True):
        _GRAPH_JSON_CACHE[key] = raw
    return Response(content=raw, media_type="application/json")


@admin_router.get("/graph/validation")
async def get_graph_validation(
    _: PlatformAdminDep,
    session: SessionDep,
    country_code: str = Query(default="SG", min_length=2, max_length=2),
) -> list[dict]:
    from app.knowledge.graph_validation import validate_graph

    return await validate_graph(session, country_code.upper())


@admin_router.get("/history/{target_id}")
async def knowledge_history(
    target_id: uuid.UUID,
    _: AdminSideDep,
    session: SessionDep,
    offset: int = Query(default=0, ge=0, le=2147483647),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    from app.knowledge.history import record_payload
    from app.knowledge.models import KnowledgeAuditRecord, KnowledgeChangeProposal

    records = (
        await session.scalars(
            select(KnowledgeAuditRecord)
            .where(KnowledgeAuditRecord.target_id == target_id)
            .order_by(KnowledgeAuditRecord.created_at.desc(), KnowledgeAuditRecord.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    proposals = (
        await session.scalars(
            select(KnowledgeChangeProposal)
            .where(KnowledgeChangeProposal.target_id == target_id)
            .order_by(KnowledgeChangeProposal.created_at.desc(), KnowledgeChangeProposal.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return {
        "history": [record_payload(row) for row in records],
        "proposals": [record_payload(row) for row in proposals],
        "offset": offset,
        "limit": limit,
    }


@admin_router.get("/graph-runs")
async def graph_runs(
    _: PlatformAdminDep,
    session: SessionDep,
    country_code: str = Query(default="SG", min_length=2, max_length=2),
    offset: int = Query(default=0, ge=0, le=2147483647),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict]:
    from app.knowledge.history import record_payload
    from app.knowledge.models import KnowledgeAuditRecord

    country_id = uuid.uuid5(uuid.NAMESPACE_URL, "cyshuhe:kg:" + country_code.upper())
    rows = (
        await session.scalars(
            select(KnowledgeAuditRecord)
            .where(
                KnowledgeAuditRecord.target_id == country_id,
                KnowledgeAuditRecord.action == "graph_rebuild_result",
            )
            .order_by(KnowledgeAuditRecord.created_at.desc(), KnowledgeAuditRecord.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return [record_payload(row) for row in rows]


@admin_router.post("/edges/{edge_id}/review", response_model=KgEdgeOut)
async def review_knowledge_edge(
    edge_id: uuid.UUID,
    body: EdgeReviewIn,
    user: PlatformAdminDep,
    session: SessionDep,
) -> KgEdgeOut:
    edge = await session.get(KgEdge, edge_id)
    if edge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="图谱关系边不存在")
    from app.ingestion.safety import held_citation_ids
    from app.knowledge.extractor import (
        ALLOWED_RELATION_ENDPOINT_TYPES,
        validate_entity_specificity_and_penalty_fidelity,
    )
    from app.knowledge.graph_verify import clean_verify_note, verify_edge
    from app.knowledge.history import snapshot

    src = await session.get(KgNode, edge.source_node_id)
    dst = await session.get(KgNode, edge.target_node_id)
    clause = await session.get(Clause, edge.evidence_clause_id)
    ver = await session.get(DocumentVersion, clause.version_id) if clause is not None else None
    doc = await session.get(Document, ver.document_id) if ver is not None else None

    if body.decision == "approved":
        if await held_citation_ids(session, [edge.evidence_clause_id]):
            raise HTTPException(409, "原文绑定异常已隔离，不能批准为法律依据")
        if (
            edge.review_status in ("superseded", "stale")
            or edge.extractor_model == "kg-rule-extractor-v1"
            or not clause
            or not edge.evidence_quote
            or edge.evidence_quote not in clause.original_text
            or (not edge.is_structural and edge.judge_verdict not in ("yes", "title_inferred"))
            or (edge.is_structural and edge.judge_verdict not in ("structural", "title_inferred"))
        ):
            raise HTTPException(status_code=409, detail="关系证据/裁判未通过或已失效；请先建立有效修订")
        if (
            ver is None
            or ver.legal_status in NOT_CITABLE_STATUSES
            or ver.review_status == "rejected"
            or (doc is not None and doc.relevance in ("irrelevant", "out_of_scope"))
        ):
            raise HTTPException(409, "依据条款所属法规已废止、属草案或不在收录范围内，不能批准为法律依据")
        if (
            src is None
            or dst is None
            or src.review_status in ("rejected", "superseded", "stale")
            or dst.review_status in ("rejected", "superseded", "stale")
        ):
            raise HTTPException(409, "该关系关联的起点或终点节点已停用或已驳回，不能批准为法律依据")
        allowed_pairs = ALLOWED_RELATION_ENDPOINT_TYPES.get(RelationType(edge.relation_type), set())
        if (NodeType(src.node_type), NodeType(dst.node_type)) not in allowed_pairs:
            raise HTTPException(409, "该关系类型的起点或终点要素类别不匹配，不能批准为法律依据")
        if not edge.is_structural:
            ok_spec, _, spec_reason = validate_entity_specificity_and_penalty_fidelity(
                relation_type=RelationType(edge.relation_type),
                source_type=NodeType(src.node_type),
                source_name=src.name_zh,
                target_type=NodeType(dst.node_type),
                target_name=dst.name_zh,
                evidence_quote=edge.evidence_quote or "",
            )
            if not ok_spec:
                raise HTTPException(409, spec_reason)
    snapshot(session, edge, "before_human_review", user.user_id)
    edge.review_status = body.decision
    if body.decision == "approved" and edge.verify_state == "retired":
        edge.verify_state = None
        edge.verify_note = None
    edge.reviewed_by = user.user_id
    edge.reviewed_at = datetime.now(UTC)
    snapshot(session, edge, "after_human_review", user.user_id)
    await session.commit()
    v_status, v_reason = (
        verify_edge(
            edge,
            src_node=src,
            dst_node=dst,
            clause_text=(clause.original_text if clause else ""),
        )
        if src and dst
        else ("approved" if edge.review_status == "approved" else "retired", "")
    )
    return KgEdgeOut(
        id=edge.id,
        country_code=edge.country_code,
        source_node_id=edge.source_node_id,
        source_name_zh=src.name_zh if src else "",
        source_node_type=src.node_type if src else "",
        target_node_id=edge.target_node_id,
        target_name_zh=dst.name_zh if dst else "",
        target_node_type=dst.node_type if dst else "",
        relation_type=edge.relation_type,
        evidence_clause_id=edge.evidence_clause_id,
        evidence_quote=edge.evidence_quote,
        clause_number=clause.clause_number if clause else None,
        document_title=None,
        extractor_model=edge.extractor_model,
        confidence=float(getattr(edge, "confidence", 0.85)),
        judge_verdict=str(getattr(edge, "judge_verdict", "yes")),
        judge_reason=clean_verify_note(getattr(edge, "judge_reason", None)) or None,
        is_structural=bool(getattr(edge, "is_structural", False)),
        review_status=edge.review_status,
        verification_status=v_status,
        verification_reason=clean_verify_note(v_reason),
        usable_as_legal_basis=(
            edge.review_status == "approved"
            and not edge.is_structural
            and edge.judge_verdict in ("yes", "title_inferred")
        ),
    )


@admin_router.post("/nodes/{node_id}/review", response_model=KgNodeOut)
async def review_knowledge_node(
    node_id: uuid.UUID,
    body: EdgeReviewIn,
    user: PlatformAdminDep,
    session: SessionDep,
) -> KgNodeOut:
    node = await session.get(KgNode, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="图谱节点不存在")
    from app.ingestion.safety import document_is_held, held_citation_ids
    from app.knowledge.extractor import (
        _GENERIC_HOLLOW_PENALTY_NAMES,
        _PENALTY_QUANTIFIER_IN_TARGET_RE,
        _QUOTE_NUMERIC_PENALTY_RE,
        _ULTRA_VAGUE_ENTITY_NAMES_ZH,
    )
    from app.knowledge.graph_verify import verify_node
    from app.knowledge.history import snapshot

    if body.decision == "approved":
        if node.review_status in ("superseded", "stale"):
            raise HTTPException(status_code=409, detail="节点已停用，不能确认保留")
        if node.document_id is not None:
            if await document_is_held(session, node.document_id):
                raise HTTPException(409, "原文绑定异常已隔离，不能确认保留")
            node_doc = await session.get(Document, node.document_id)
            if node_doc is not None and node_doc.relevance in ("irrelevant", "out_of_scope"):
                raise HTTPException(409, "所属法规不在收录范围内，不能确认保留")
        cl_obj = None
        if node.evidence_clause_id is not None:
            if await held_citation_ids(session, [node.evidence_clause_id]):
                raise HTTPException(409, "原文绑定异常已隔离，不能确认保留")
            cl_obj = await session.get(Clause, node.evidence_clause_id)
            if cl_obj is None:
                raise HTTPException(409, "依据条款不存在，不能确认保留")
            cl_ver = await session.get(DocumentVersion, cl_obj.version_id)
            cl_doc = await session.get(Document, cl_ver.document_id) if cl_ver is not None else None
            if (
                cl_ver is None
                or cl_ver.legal_status in NOT_CITABLE_STATUSES
                or cl_ver.review_status == "rejected"
                or (cl_doc is not None and cl_doc.relevance in ("irrelevant", "out_of_scope"))
            ):
                raise HTTPException(409, "依据条款所属法规已废止、属草案或不在收录范围内，不能确认保留")
        clean_nm = "".join((node.name_zh or "").split())
        if node.node_type in ("DATA_CATEGORY", "OBLIGATION", "PENALTY") and (
            len(clean_nm) <= 1 or clean_nm in _ULTRA_VAGUE_ENTITY_NAMES_ZH
        ):
            raise HTTPException(
                409, f"节点名称“{node.name_zh}”颗粒度过于宽泛空洞，请先修改补充限定语后再确认"
            )
        if node.node_type == "PENALTY":
            if clean_nm in _GENERIC_HOLLOW_PENALTY_NAMES:
                raise HTTPException(
                    409, f"罚则名称“{node.name_zh}”过于宽泛，请先补充具体罚金数额、比例或监禁期限"
                )
            pen_quotes = list(
                (
                    await session.scalars(
                        select(KgEdge.evidence_quote).where(
                            KgEdge.target_node_id == node.id,
                            KgEdge.relation_type == "PENALIZES",
                            KgEdge.review_status.notin_(("superseded", "stale", "rejected")),
                        )
                    )
                ).all()
            )
            if cl_obj is not None and cl_obj.original_text:
                pen_quotes.append(cl_obj.original_text)
            if any(_QUOTE_NUMERIC_PENALTY_RE.search(q or "") for q in pen_quotes) and not _PENALTY_QUANTIFIER_IN_TARGET_RE.search(node.name_zh or ""):
                raise HTTPException(
                    409,
                    f"依据条文载有明确罚金数额、比例或监禁期限，罚则名称“{node.name_zh}”不得省略量化标准",
                )
    snapshot(session, node, "before_human_review", user.user_id)
    reviewed_ts = datetime.now(UTC)
    node.review_status = body.decision
    if body.decision == "approved" and node.verify_state == "retired":
        node.verify_state = None
        node.verify_note = None
    node.attributes = {
        **(node.attributes or {}),
        "human_review": {
            "decision": body.decision,
            "by": str(user.user_id),
            "at": reviewed_ts.isoformat(timespec="seconds"),
        },
    }
    snapshot(session, node, "after_human_review", user.user_id)
    if body.decision == "rejected":
        incident = (
            await session.scalars(
                select(KgEdge).where(
                    or_(KgEdge.source_node_id == node.id, KgEdge.target_node_id == node.id),
                    *active_edge_filters(),
                )
            )
        ).all()
        for e in incident:
            snapshot(session, e, "before_node_reject_cascade", user.user_id)
            e.review_status = "stale"
            snapshot(session, e, "after_node_reject_cascade", user.user_id)
    await session.commit()
    await session.refresh(node)
    nout = KgNodeOut.model_validate(node)
    nout.verification_status, nout.verification_reason = verify_node(node)
    return nout


# ==================== 图谱人工编辑（改名、新增、改关系、停用、恢复） ====================


async def _edited(session: AsyncSession, call):
    from app.knowledge.graph_edit import GraphEditError

    try:
        result = await call
    except GraphEditError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return result


def _node_brief(node: KgNode) -> dict:
    return {
        "id": str(node.id),
        "country_code": node.country_code,
        "node_type": node.node_type,
        "name_zh": node.name_zh,
        "attributes": node.attributes or {},
        "review_status": node.review_status,
    }


def _edge_brief(edge: KgEdge) -> dict:
    return {
        "id": str(edge.id),
        "relation_type": edge.relation_type,
        "source_node_id": str(edge.source_node_id),
        "target_node_id": str(edge.target_node_id),
        "review_status": edge.review_status,
    }


@admin_router.get("/clauses")
async def search_admin_knowledge_clauses(
    _: AdminSideDep,
    session: SessionDep,
    country_code: str | None = Query(default=None, max_length=8),
    q: str | None = Query(default=None, max_length=120),
    document_id: uuid.UUID | None = None,
    limit: int = Query(default=30, ge=1, le=100),
) -> list[dict]:
    """Search latest parsed in-scope clauses by country and keyword for GraphEditPanel."""
    latest = (
        select(DocumentVersion.document_id, func.max(DocumentVersion.version_no).label("v"))
        .where(DocumentVersion.state == "parsed")
        .group_by(DocumentVersion.document_id)
        .subquery()
    )
    stmt = (
        select(Clause, Document.id, Document.title, Document.title_zh, Document.country_code)
        .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(
            latest,
            (latest.c.document_id == Document.id)
            & (latest.c.v == DocumentVersion.version_no),
        )
        .where(_in_scope(), document_available())
        .order_by(Document.title, Clause.ordinal)
    )
    if country_code and country_code.strip():
        from app.knowledge.countries import resolve_country_code

        stmt = stmt.where(Document.country_code == resolve_country_code(country_code))
    if document_id is not None:
        stmt = stmt.where(Document.id == document_id)
    if q and q.strip():
        for tok in q.strip().split()[:5]:
            pat = f"%{tok}%"
            stmt = stmt.where(
                or_(
                    Document.title.ilike(pat),
                    Document.title_zh.ilike(pat),
                    Clause.clause_number.ilike(pat),
                    Clause.heading.ilike(pat),
                    Clause.original_text.ilike(pat),
                )
            )
    rows = (await session.execute(stmt.limit(limit))).all()
    return [
        {
            "id": str(clause.id),
            "country_code": cc,
            "document_id": str(doc_id),
            "document_title": (title_zh or "").strip() or title,
            "clause_number": clause.clause_number,
            "heading": clause.heading,
            "original_text": clause.original_text,
        }
        for clause, doc_id, title, title_zh, cc in rows
    ]


@admin_router.get("/clauses/{clause_id}")
async def get_knowledge_clause(
    clause_id: uuid.UUID, _: AdminSideDep, session: SessionDep
) -> dict:
    """The clause a hand-made relation will cite: number, document, original text."""
    row = (
        await session.execute(
            select(Clause, Document.title, Document.country_code)
            .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(Clause.id == clause_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="条款不存在")
    clause, title, country = row
    return {
        "id": str(clause.id),
        "country_code": country,
        "document_title": title,
        "clause_number": clause.clause_number,
        "original_text": clause.original_text,
    }


@admin_router.patch("/nodes/{node_id}")
async def edit_knowledge_node(
    node_id: uuid.UUID, body: NodeEditIn, user: PlatformAdminDep, session: SessionDep
) -> dict:
    from app.knowledge import graph_edit

    node = await _edited(
        session,
        graph_edit.rename_node(
            session,
            node_id,
            name_zh=body.name_zh,
            summary=body.summary,
            attributes=body.attributes,
            actor_id=user.user_id,
        ),
    )
    return _node_brief(node)


@admin_router.post("/nodes", status_code=status.HTTP_201_CREATED)
async def create_knowledge_node(
    body: NodeCreateIn, user: PlatformAdminDep, session: SessionDep
) -> dict:
    from app.knowledge import graph_edit

    node = await _edited(
        session,
        graph_edit.create_node(
            session,
            country_code=body.country_code,
            node_type=body.node_type,
            name_zh=body.name_zh,
            name_orig=body.name_orig,
            summary=body.summary,
            evidence_clause_id=body.evidence_clause_id,
            actor_id=user.user_id,
        ),
    )
    return _node_brief(node)


@admin_router.delete("/nodes/{node_id}")
async def retire_knowledge_node(
    node_id: uuid.UUID, user: PlatformAdminDep, session: SessionDep
) -> dict:
    from app.knowledge import graph_edit

    node, edges = await _edited(
        session, graph_edit.retire_node(session, node_id, actor_id=user.user_id)
    )
    return {**_node_brief(node), "retired_edges": edges}


@admin_router.post("/edges", status_code=status.HTTP_201_CREATED)
async def create_knowledge_edge(
    body: EdgeCreateIn, user: PlatformAdminDep, session: SessionDep
) -> dict:
    from app.knowledge import graph_edit

    edge = await _edited(
        session,
        graph_edit.create_edge(
            session,
            source_node_id=body.source_node_id,
            target_node_id=body.target_node_id,
            relation_type=body.relation_type,
            evidence_clause_id=body.evidence_clause_id,
            evidence_quote=body.evidence_quote,
            actor_id=user.user_id,
        ),
    )
    return _edge_brief(edge)


@admin_router.patch("/edges/{edge_id}")
async def edit_knowledge_edge(
    edge_id: uuid.UUID, body: EdgeRelationIn, user: PlatformAdminDep, session: SessionDep
) -> dict:
    from app.knowledge import graph_edit

    edge = await _edited(
        session,
        graph_edit.change_edge_relation(
            session, edge_id, relation_type=body.relation_type, actor_id=user.user_id
        ),
    )
    return _edge_brief(edge)


@admin_router.delete("/edges/{edge_id}")
async def retire_knowledge_edge(
    edge_id: uuid.UUID, user: PlatformAdminDep, session: SessionDep
) -> dict:
    from app.knowledge import graph_edit

    edge = await _edited(session, graph_edit.retire_edge(session, edge_id, actor_id=user.user_id))
    return _edge_brief(edge)


@admin_router.post("/restore")
async def restore_knowledge_record(
    body: RestoreIn, user: PlatformAdminDep, session: SessionDep
) -> dict:
    from app.knowledge import graph_edit

    record = await _edited(
        session, graph_edit.restore_from_history(session, body.audit_id, actor_id=user.user_id)
    )
    return _node_brief(record) if isinstance(record, KgNode) else _edge_brief(record)


class BatchNodeItemIn(BaseModel):
    country_code: str = Field(min_length=1, max_length=32)
    node_type: str = Field(min_length=2, max_length=32)
    name_zh: str = Field(min_length=1, max_length=255)
    name_orig: str | None = None
    summary: str | None = None
    evidence_clause_id: uuid.UUID | None = None
    document_title: str | None = None
    clause_number: str | None = None


class BatchEdgeItemIn(BaseModel):
    country_code: str | None = None
    source_node_id: uuid.UUID | None = None
    target_node_id: uuid.UUID | None = None
    source_node_name: str | None = None
    target_node_name: str | None = None
    relation_type: str = Field(min_length=2, max_length=40)
    evidence_clause_id: uuid.UUID | None = None
    document_title: str | None = None
    clause_number: str | None = None
    evidence_quote: str = Field(min_length=8, max_length=2000)


class BatchImportIn(BaseModel):
    nodes: list[Any] = Field(default_factory=list)
    edges: list[Any] = Field(default_factory=list)
    csv_text: str | None = None
    csv_kind: str = Field(default="nodes", max_length=32)


def _parse_uuid_field(raw_val: Any, field_label: str, row_prefix: str) -> uuid.UUID | None:
    s = str(raw_val or "").strip()
    if not s:
        return None
    try:
        return uuid.UUID(s)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{row_prefix}“{field_label}”格式不正确，请核对编号后重试") from exc


def _validate_batch_node_dict(raw: Any, idx: int, *, from_csv: bool = False) -> tuple[BatchNodeItemIn | None, dict | None]:
    loc_key = "row" if from_csv else "index"
    prefix = f"第 {idx} 行"
    if not isinstance(raw, dict):
        return None, {loc_key: idx, "kind": "节点", "error": f"{prefix}节点数据格式不正确，请按标准模板填写"}
    cc = str(raw.get("country_code") or raw.get("国家或地区") or "").strip()
    ntype = str(raw.get("node_type") or raw.get("节点类别") or "").strip()
    name_zh = str(raw.get("name_zh") or raw.get("中文名称") or "").strip()
    name_orig = str(raw.get("name_orig") or raw.get("原文名称") or "").strip() or None
    summary = str(raw.get("summary") or raw.get("简要说明") or "").strip() or None
    doc_title = str(raw.get("document_title") or raw.get("依据法规名称") or "").strip() or None
    clause_no = str(raw.get("clause_number") or raw.get("依据条号") or "").strip() or None
    if not cc or len(cc) > 32:
        return None, {loc_key: idx, "kind": "节点", "name_zh": name_zh, "error": f"{prefix}“国家或地区”不能为空或格式超长，请填写有效国别"}
    if len(ntype) < 2 or len(ntype) > 32:
        return None, {loc_key: idx, "kind": "节点", "name_zh": name_zh, "error": f"{prefix}“节点类别”不能为空，请填写标准要素类别（如“合规义务”“数据类型”）"}
    if not name_zh:
        return None, {loc_key: idx, "kind": "节点", "error": f"{prefix}“中文名称”不能为空，请填写要素中文名称"}
    if len(name_zh) > 255:
        return None, {loc_key: idx, "kind": "节点", "name_zh": name_zh[:40], "error": f"{prefix}“中文名称”超出长度限制（最多 255 字）"}
    try:
        cid = _parse_uuid_field(raw.get("evidence_clause_id"), "依据条款编号", prefix)
    except ValueError as exc:
        return None, {loc_key: idx, "kind": "节点", "name_zh": name_zh, "error": str(exc)}
    from pydantic import ValidationError as PydanticValidationError

    try:
        return (
            BatchNodeItemIn(
                country_code=cc,
                node_type=ntype,
                name_zh=name_zh,
                name_orig=name_orig,
                summary=summary,
                evidence_clause_id=cid,
                document_title=doc_title,
                clause_number=clause_no,
            ),
            None,
        )
    except PydanticValidationError:
        return None, {loc_key: idx, "kind": "节点", "name_zh": name_zh[:40], "error": f"{prefix}节点字段内容格式不合法，请检查填写内容"}


def _validate_batch_edge_dict(raw: Any, idx: int, *, from_csv: bool = False) -> tuple[BatchEdgeItemIn | None, dict | None]:
    loc_key = "row" if from_csv else "index"
    prefix = f"第 {idx} 行"
    if not isinstance(raw, dict):
        return None, {loc_key: idx, "kind": "关联关系", "error": f"{prefix}关联关系数据格式不正确，请按标准模板填写"}
    cc = str(raw.get("country_code") or raw.get("国家或地区") or "").strip() or None
    src_name = str(raw.get("source_node_name") or raw.get("起点节点名称") or "").strip() or None
    tgt_name = str(raw.get("target_node_name") or raw.get("终点节点名称") or "").strip() or None
    rtype = str(raw.get("relation_type") or raw.get("关联关系") or "").strip()
    doc_title = str(raw.get("document_title") or raw.get("依据法规名称") or "").strip() or None
    clause_no = str(raw.get("clause_number") or raw.get("依据条号") or "").strip() or None
    quote = str(raw.get("evidence_quote") or raw.get("依据条文原句") or "").strip()
    if len(rtype) < 2 or len(rtype) > 40:
        return None, {loc_key: idx, "kind": "关联关系", "relation_type": rtype, "error": f"{prefix}“关联关系”不能为空，请填写标准关系类型（如“强制要求”“涵盖数据”）"}
    if len(quote) < 8:
        return None, {loc_key: idx, "kind": "关联关系", "relation_type": rtype, "error": f"{prefix}“原文佐证引句”不能为空或过短（至少 8 个字符），请填写对应条文原句"}
    if len(quote) > 2000:
        return None, {loc_key: idx, "kind": "关联关系", "relation_type": rtype, "error": f"{prefix}“原文佐证引句”过长（请控制在 2000 字以内）"}
    try:
        src_id = _parse_uuid_field(raw.get("source_node_id"), "起点节点编号", prefix)
        tgt_id = _parse_uuid_field(raw.get("target_node_id"), "终点节点编号", prefix)
        cid = _parse_uuid_field(raw.get("evidence_clause_id"), "依据条款编号", prefix)
    except ValueError as exc:
        return None, {loc_key: idx, "kind": "关联关系", "relation_type": rtype, "error": str(exc)}
    from pydantic import ValidationError as PydanticValidationError

    try:
        return (
            BatchEdgeItemIn(
                country_code=cc,
                source_node_id=src_id,
                target_node_id=tgt_id,
                source_node_name=src_name,
                target_node_name=tgt_name,
                relation_type=rtype,
                evidence_clause_id=cid,
                document_title=doc_title,
                clause_number=clause_no,
                evidence_quote=quote,
            ),
            None,
        )
    except PydanticValidationError:
        return None, {loc_key: idx, "kind": "关联关系", "relation_type": rtype[:40], "error": f"{prefix}关联关系字段内容格式不合法，请检查填写内容"}


_NODE_TYPE_ZH_MAP = {
    "国家/地区": "COUNTRY",
    "国家或地区": "COUNTRY",
    "国家": "COUNTRY",
    "监管机构": "REGULATOR",
    "法规文件": "INSTRUMENT",
    "合规义务": "OBLIGATION",
    "数据类型": "DATA_CATEGORY",
    "违规罚则": "PENALTY",
}
def _build_relation_zh_map() -> dict[str, str]:
    from app.knowledge.graph_edit import RELATIONS

    return {
        "涵盖数据": "INVOLVES_DATA",
        "涉及数据": "INVOLVES_DATA",
        "规范": "GOVERNS",
        "管辖": "GOVERNS",
        "规定": "REQUIRES",
        "规定了": "REQUIRES",
        "要求": "REQUIRES",
        "豁免于": "EXEMPTS",
        "监管": "SUPERVISES",
        "处罚": "IMPOSES_PENALTY",
        "处罚为": "IMPOSES_PENALTY",
        "罚则": "PENALIZES",
        "限制出境": "RESTRICTS_TRANSFER",
        "限制跨境传输": "RESTRICTS_CROSS_BORDER",
        "适用准入": "REQUIRES_ADMISSION",
        **{zh: code for code, zh in RELATIONS.items()},
    }


_RELATION_ZH_MAP = _build_relation_zh_map()


def _resolve_cc_input(raw: str) -> str:
    from app.knowledge.countries import resolve_country_code

    return resolve_country_code(raw)


async def _resolve_clause_by_title(
    session: AsyncSession, country_code: str, doc_title: str | None, clause_no: str | None
) -> uuid.UUID | None:
    import re as _re

    from app.ingestion.layout_safety import clause_layout_available
    from app.knowledge.reading import article_label

    dt_val = (doc_title or "").strip().strip("《》")
    cn_val = (clause_no or "").strip()
    if not dt_val or not cn_val:
        return None
    doc_stmt = select(Document.id).where(
        document_available(),
        _in_scope(),
        or_(Document.title_zh.ilike(f"%{dt_val}%"), Document.title.ilike(f"%{dt_val}%")),
    )
    if country_code:
        doc_stmt = doc_stmt.where(Document.country_code == country_code.upper())
    doc_ids = list((await session.scalars(doc_stmt)).all())
    if not doc_ids:
        return None
    ver_rows = (
        await session.scalars(
            select(DocumentVersion)
            .where(
                DocumentVersion.document_id.in_(doc_ids),
                DocumentVersion.state == "parsed",
                DocumentVersion.legal_status.notin_(NOT_CITABLE_STATUSES),
                DocumentVersion.review_status != "rejected",
            )
            .order_by(DocumentVersion.document_id, DocumentVersion.version_no.desc())
        )
    ).all()
    latest_vids: list[uuid.UUID] = []
    seen_docs: set = set()
    for vr in ver_rows:
        if vr.document_id not in seen_docs:
            seen_docs.add(vr.document_id)
            latest_vids.append(vr.id)
    if not latest_vids:
        return None
    c_rows = (
        await session.execute(
            select(Clause.id, Clause.clause_number)
            .where(
                Clause.version_id.in_(latest_vids),
                clause_layout_available(),
                Clause.clause_number.is_not(None),
            )
            .order_by(Clause.ordinal.asc())
        )
    ).all()
    cn_low = cn_val.lower()
    cn_label = article_label(cn_val).lower() if article_label(cn_val) else ""
    for cid, cnum in c_rows:
        raw_num = (cnum or "").strip()
        if raw_num.lower() == cn_low or (cn_label and article_label(raw_num).lower() == cn_label):
            return cid
    digits_m = _re.search(r"\d+(?:[.-]\d+)*", cn_val)
    if digits_m:
        tok_pat = _re.compile(rf"(?<!\d){_re.escape(digits_m.group(0))}(?!\d)", _re.I)
        for cid, cnum in c_rows:
            if tok_pat.search(cnum or ""):
                return cid
    return None


async def _resolve_node_by_name(
    session: AsyncSession, country_code: str | None, name_zh: str | None
) -> uuid.UUID | None:
    nm = (name_zh or "").strip()
    if not nm:
        return None
    stmt = (
        select(KgNode.id)
        .where(KgNode.name_zh == nm, KgNode.review_status.notin_(("rejected", "superseded", "stale")))
        .order_by((KgNode.review_status == "approved").desc(), KgNode.id)
        .limit(1)
    )
    if country_code:
        stmt = stmt.where(KgNode.country_code == country_code.upper())
    return await session.scalar(stmt)


@admin_router.get("/batch-template")
async def get_kg_batch_template(
    _: AdminSideDep,
    country_code: str | None = Query(default=None, max_length=32),
) -> dict:
    from app.analysis.workflow_engine import country_name_zh
    from app.knowledge import graph_edit

    resolved_cc = _resolve_cc_input(country_code) if country_code else ""
    sample_country = country_name_zh(resolved_cc) if resolved_cc else "新加坡"
    return {
        "node_types": list(graph_edit.NODE_TYPES),
        "relation_types": list(graph_edit.RELATIONS),
        "needs_evidence_node_types": list(graph_edit.NEEDS_EVIDENCE),
        "json_example": {
            "nodes": [
                {
                    "country_code": sample_country,
                    "node_type": "数据类型",
                    "name_zh": "车载电池热管理遥测参数",
                    "name_orig": "Battery Thermal Telemetry Parameters",
                    "summary": "用于云端热失控预警的电池包温度与电压采样参数",
                    "document_title": "",
                    "clause_number": "",
                }
            ],
            "edges": [],
        },
        "csv_headers": {
            "nodes": "国家或地区,节点类别,中文名称,原文名称,简要说明,依据法规名称,依据条号",
            "edges": "国家或地区,起点节点名称,终点节点名称,关联关系,依据法规名称,依据条号,依据条文原句",
        },
    }


@admin_router.post("/batch-import")
async def batch_import_knowledge_graph(
    body: BatchImportIn,
    request: Request,
    user: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    import csv
    import io

    from app.audit.service import set_detail
    from app.knowledge import graph_edit

    node_items: list[tuple[int, BatchNodeItemIn]] = []
    edge_items: list[tuple[int, BatchEdgeItemIn]] = []
    errors: list[dict] = []

    for idx, raw_node in enumerate(body.nodes or [], start=1):
        parsed_n, err_n = _validate_batch_node_dict(raw_node, idx, from_csv=False)
        if err_n is not None:
            errors.append(err_n)
        elif parsed_n is not None:
            node_items.append((idx, parsed_n))

    for idx, raw_edge in enumerate(body.edges or [], start=1):
        parsed_e, err_e = _validate_batch_edge_dict(raw_edge, idx, from_csv=False)
        if err_e is not None:
            errors.append(err_e)
        elif parsed_e is not None:
            edge_items.append((idx, parsed_e))

    if body.csv_text and body.csv_text.strip():
        reader = csv.DictReader(io.StringIO(body.csv_text.strip()))
        for idx, row in enumerate(reader, start=2):
            if body.csv_kind == "edges":
                parsed_e, err_e = _validate_batch_edge_dict(row, idx, from_csv=True)
                if err_e is not None:
                    errors.append(err_e)
                elif parsed_e is not None:
                    edge_items.append((idx, parsed_e))
            else:
                parsed_n, err_n = _validate_batch_node_dict(row, idx, from_csv=True)
                if err_n is not None:
                    errors.append(err_n)
                elif parsed_n is not None:
                    node_items.append((idx, parsed_n))

    created_nodes: list[dict] = []
    created_edges: list[dict] = []

    for idx, item in node_items:
        try:
            cc = _resolve_cc_input(item.country_code)
            ntype = _NODE_TYPE_ZH_MAP.get(item.node_type.strip(), item.node_type.strip().upper())
            cid = item.evidence_clause_id
            if cid is None and item.document_title and item.clause_number:
                cid = await _resolve_clause_by_title(session, cc, item.document_title, item.clause_number)
                if cid is None:
                    raise graph_edit.GraphEditError(
                        f"未在已入库条文中匹配到《{item.document_title}》{item.clause_number}"
                    )
            async with session.begin_nested():
                node = await graph_edit.create_node(
                    session,
                    country_code=cc,
                    node_type=ntype,
                    name_zh=item.name_zh,
                    name_orig=item.name_orig,
                    summary=item.summary,
                    evidence_clause_id=cid,
                    actor_id=user.user_id,
                )
            created_nodes.append(_node_brief(node))
        except graph_edit.GraphEditError as exc:
            errors.append({"index": idx, "kind": "节点", "name_zh": item.name_zh, "error": str(exc)})

    for idx, item in edge_items:
        try:
            cc = _resolve_cc_input(item.country_code or "")
            rtype = _RELATION_ZH_MAP.get(item.relation_type.strip(), item.relation_type.strip().upper())
            src_id = item.source_node_id or await _resolve_node_by_name(session, cc or None, item.source_node_name)
            tgt_id = item.target_node_id or await _resolve_node_by_name(session, cc or None, item.target_node_name)
            if src_id is None or tgt_id is None:
                raise graph_edit.GraphEditError("未匹配到对应的起点或终点节点名称，请确认节点已入库")
            cid = item.evidence_clause_id
            if cid is None and item.document_title and item.clause_number:
                cid = await _resolve_clause_by_title(session, cc, item.document_title, item.clause_number)
            if cid is None:
                raise graph_edit.GraphEditError("请提供有效的依据法规名称与依据条号")
            async with session.begin_nested():
                edge = await graph_edit.create_edge(
                    session,
                    source_node_id=src_id,
                    target_node_id=tgt_id,
                    relation_type=rtype,
                    evidence_clause_id=cid,
                    evidence_quote=item.evidence_quote,
                    actor_id=user.user_id,
                )
            created_edges.append(_edge_brief(edge))
        except graph_edit.GraphEditError as exc:
            errors.append({"index": idx, "kind": "关联关系", "relation_type": item.relation_type, "error": str(exc)})

    if created_nodes or created_edges:
        await session.commit()
        from app.knowledge.cache_sync import after_admin_change

        after_admin_change()

    set_detail(
        request,
        summary=f"知识图谱批量校验导入：新增节点 {len(created_nodes)} 个、关系 {len(created_edges)} 条（拦截异常 {len(errors)} 条）",
    )
    return {
        "created_nodes_count": len(created_nodes),
        "created_edges_count": len(created_edges),
        "created_nodes": created_nodes,
        "created_edges": created_edges,
        "errors": errors,
    }


# ==================== 用户端法规知识库浏览接口 ====================


def _in_scope():
    """用户可见的范围：与检索分析、用户端目录同一条规则——被判定为范围外的文件不计入、不展示。"""
    return Document.relevance.notin_(("irrelevant", "out_of_scope"))


_OVERVIEW_CACHE: dict[str, tuple[float, list[dict]]] = {}


@user_router.get("/overview")
async def get_knowledge_overview(
    _: UserDep,
    session: SessionDep,
) -> list[dict]:
    """The country list of the library with its counts (cached half an hour; cleared whenever the
    administrators change something, see cache_sync): the country picker must open at once."""
    import time

    # 不按时间过期：库或图谱有变化时由 cache_sync 在后台重建，重建期间沿用上一份，不让人等
    from app.knowledge.cache_sync import use_cached

    hit = _OVERVIEW_CACHE.get("all")
    if use_cached(hit):
        return hit[1]
    data = await _knowledge_overview(session)
    _OVERVIEW_CACHE["all"] = (time.monotonic(), data)
    return data


async def _knowledge_overview(session: AsyncSession) -> list[dict]:
    """Batch statistics for existing visible countries plus the original pilot labels.

    Documents and articles are counted from the user's library (same rules as library_view), with a
    constant number of queries regardless of country count; newly discovered available countries
    whose versions are not parsed yet are still surfaced."""
    from app.ingestion.layout_safety import clause_layout_available
    from app.knowledge.library_view import _Light
    from app.knowledge.reading import reading_units

    doc_filter = (
        document_available(),
        _in_scope(),
        or_(Document.business_weight.is_(None), Document.business_weight != "peripheral"),
    )
    all_docs = list(
        (await session.scalars(select(Document).where(*doc_filter).order_by(Document.title))).all()
    )
    versions = list(
        (
            await session.scalars(
                select(DocumentVersion)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    *doc_filter,
                    DocumentVersion.state == "parsed",
                    DocumentVersion.review_status != "rejected",
                )
                .order_by(
                    DocumentVersion.document_id,
                    (DocumentVersion.legal_status == "draft").asc(),
                    DocumentVersion.version_no.desc(),
                )
            )
        ).all()
    )
    latest_by_doc: dict = {}
    for v in versions:
        latest_by_doc.setdefault(v.document_id, v)
    citable_ver_ids = [
        v.id
        for v in latest_by_doc.values()
        if (v.legal_status or "in_force") not in NOT_CITABLE_STATUSES
    ]
    clause_rows = (
        (
            await session.execute(
                select(
                    Clause.version_id,
                    Clause.id,
                    Clause.ordinal,
                    Clause.clause_number,
                    Clause.heading,
                    Clause.page_start,
                    func.left(Clause.original_text, 80),
                    func.length(Clause.original_text),
                    Clause.language,
                    Clause.needs_review,
                ).where(Clause.version_id.in_(citable_ver_ids))
            )
        ).all()
        if citable_ver_ids
        else []
    )
    rows_by_ver: dict = {}
    clause_meta: dict = {}
    for vid, cid, ordinal, number, heading, page, head, length, clang, c_nr in clause_rows:
        text = head if (length or 0) <= 80 else head + " " * ((length or 0) - 80)
        rows_by_ver.setdefault(vid, []).append(_Light(cid, ordinal, number, heading, page, text))
        clause_meta[cid] = (str(clang or ""), bool(c_nr))
    tr_status_rows = (
        (
            await session.execute(
                select(ClauseTranslation.clause_id, ClauseTranslation.review_status)
                .join(Clause, Clause.id == ClauseTranslation.clause_id)
                .where(
                    Clause.version_id.in_(citable_ver_ids),
                    ClauseTranslation.is_current.is_(True),
                    ClauseTranslation.quality_check_passed.is_(True),
                    ClauseTranslation.review_status.notin_(
                        ("superseded", "not_translated", "translation_failed", "rejected", "stale")
                    ),
                )
            )
        ).all()
        if citable_ver_ids
        else []
    )
    tr_buckets: dict[uuid.UUID, list[str]] = {}
    for cid, rstatus in tr_status_rows:
        tr_buckets.setdefault(cid, []).append(rstatus)
    translated_cids: set = {cid for cid, rs in tr_buckets.items() if len(rs) == 1}
    approved_cids: set = {cid for cid, rs in tr_buckets.items() if rs == ["approved"]}
    discovered_docs: dict[str, int] = {}
    has_parsed_cc: set[str] = set()
    parsed_docs: dict[str, int] = {}
    clauses: dict[str, int] = {}
    translations: dict[str, tuple[int, int]] = {}
    last_updated: dict[str, datetime] = {}
    for d in all_docs:
        cc = d.country_code
        discovered_docs[cc] = discovered_docs.get(cc, 0) + 1
        v = latest_by_doc.get(d.id)
        if v is not None:
            has_parsed_cc.add(cc)
        ts = (v.fetched_at or v.parsed_at) if v is not None else (d.discovered_at or d.last_checked_at)
        if ts is not None and (cc not in last_updated or ts > last_updated[cc]):
            last_updated[cc] = ts
        if (
            v is not None
            and (v.legal_status or "in_force") not in NOT_CITABLE_STATUSES
            and rows_by_ver.get(v.id)
        ):
            from app.knowledge.library_view import usable_unit_list

            doc_is_zh = str(d.language or "").lower().startswith("zh")
            ver_cids = {r.id for r in rows_by_ver[v.id]}
            ver_tr_cids = ver_cids & translated_cids
            ver_zh_cids = {
                cid
                for cid in ver_cids
                if clause_meta.get(cid, ("", False))[0].lower().startswith("zh")
            }
            units = usable_unit_list(
                reading_units(rows_by_ver[v.id]),
                is_native_zh=doc_is_zh,
                intake_scope=d.intake_scope,
                version_id=v.id,
                translated_cids=ver_tr_cids,
                zh_cids=ver_zh_cids,
            )
            if not units:
                continue
            parsed_docs[cc] = parsed_docs.get(cc, 0) + 1
            clauses[cc] = clauses.get(cc, 0) + len(units)
            prev_t, prev_a = translations.get(cc, (0, 0))
            u_trans = sum(
                1
                for u in units
                if doc_is_zh or any(c.id in ver_tr_cids or c.id in ver_zh_cids for c in u.rows)
            )
            u_appr = sum(
                1
                for u in units
                if (
                    (doc_is_zh or all(c.id in ver_zh_cids for c in u.rows))
                    and v.review_status == "approved"
                    and not any(clause_meta.get(c.id, ("", True))[1] for c in u.rows)
                )
                or any(c.id in approved_cids for c in u.rows)
            )
            translations[cc] = (prev_t + u_trans, prev_a + u_appr)
    active_node_conds = (
        KgNode.review_status.notin_(("superseded", "stale", "rejected")),
        func.coalesce(KgNode.verify_state, "") != "retired",
        or_(
            KgNode.document_id.is_(None),
            KgNode.document_id.in_(
                select(Document.id).where(
                    document_available(),
                    Document.relevance.in_(("relevant", "related")),
                )
            ),
        ),
        or_(
            KgNode.evidence_clause_id.is_(None),
            KgNode.evidence_clause_id.in_(
                select(Clause.id)
                .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    document_available(),
                    clause_layout_available(),
                    Document.relevance.in_(("relevant", "related")),
                    DocumentVersion.legal_status.notin_(NOT_CITABLE_STATUSES),
                    DocumentVersion.review_status != "rejected",
                )
            ),
        ),
    )
    valid_node_ids = select(KgNode.id).where(*active_node_conds)
    nodes = dict(
        (
            await session.execute(
                select(KgNode.country_code, func.count(KgNode.id))
                .where(*active_node_conds)
                .group_by(KgNode.country_code)
            )
        ).all()
    )
    edges = dict(
        (
            await session.execute(
                select(KgEdge.country_code, func.count(KgEdge.id))
                .where(
                    *active_edge_filters(),
                    func.coalesce(KgEdge.verify_state, "") != "retired",
                    KgEdge.source_node_id.in_(valid_node_ids),
                    KgEdge.target_node_id.in_(valid_node_ids),
                )
                .group_by(KgEdge.country_code)
            )
        ).all()
    )
    catalog = await _country_catalog(session, discovered_docs.keys())
    return [
        dict(
            **country,
            documents_count=(
                parsed_docs.get(country["country_code"], 0)
                if country["country_code"] in has_parsed_cc
                else discovered_docs.get(country["country_code"], 0)
            ),
            clauses_count=clauses.get(country["country_code"], 0),
            translations_count=translations.get(country["country_code"], (0, 0))[0],
            approved_translations_count=translations.get(country["country_code"], (0, 0))[1],
            kg_nodes_count=nodes.get(country["country_code"], 0),
            kg_edges_count=edges.get(country["country_code"], 0),
            last_updated_at=(
                last_updated[country["country_code"]].isoformat()
                if country["country_code"] in last_updated
                else None
            ),
        )
        for country in catalog
    ]


@user_router.get("/clauses", response_model=list[PublicClauseTranslationOut])
async def search_knowledge_clauses(
    _: UserDep,
    session: SessionDep,
    country_code: str = Query(default="SG", min_length=2, max_length=2),
    keyword: str | None = None,
    only_approved: bool = False,
    include_repealed: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[PublicClauseTranslationOut]:
    """用户端检索法规条款与对照译文(未复核内容显式带机器翻译待核对标识)。

    默认不返回已废止、草案状态的版本（include_repealed=true 可看）。关键词按空白拆成多个词：
    先要求每个词都出现（原文、译文或标题中），一条都没有时放宽为任一词出现，并按命中词数排序。"""
    from sqlalchemy.orm import aliased

    from app.ingestion.layout_safety import clause_layout_available

    ct_conflict = aliased(ClauseTranslation)
    no_ambiguous_current = (
        ~select(ct_conflict.id)
        .where(
            ct_conflict.clause_id == ClauseTranslation.clause_id,
            ct_conflict.id != ClauseTranslation.id,
            ct_conflict.is_current.is_(True),
            ct_conflict.quality_check_passed.is_(True),
            ct_conflict.review_status.in_(("machine_translated", "pending_review", "approved")),
        )
        .correlate(ClauseTranslation)
        .exists()
    )
    latest_ver_sq = (
        select(func.max(DocumentVersion.version_no))
        .where(
            DocumentVersion.document_id == Document.id,
            DocumentVersion.state == "parsed",
            DocumentVersion.review_status != "rejected",
            *(
                ()
                if include_repealed
                else (DocumentVersion.legal_status.notin_(NOT_CITABLE_STATUSES),)
            ),
        )
        .correlate(Document)
        .scalar_subquery()
    )
    base = (
        select(ClauseTranslation, Clause, Document)
        .join(Clause, Clause.id == ClauseTranslation.clause_id)
        .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            Document.country_code == country_code.upper(),
            Document.relevance.notin_(("irrelevant", "out_of_scope")),
            document_available(),
            clause_layout_available(),
            DocumentVersion.state == "parsed",
            DocumentVersion.review_status != "rejected",
            DocumentVersion.version_no == latest_ver_sq,
            ClauseTranslation.is_current.is_(True),
            ClauseTranslation.quality_check_passed.is_(True),
            ClauseTranslation.review_status.notin_(
                ("superseded", "not_translated", "translation_failed", "rejected", "stale")
            ),
            no_ambiguous_current,
        )
    )
    if not include_repealed:
        base = base.where(DocumentVersion.legal_status.notin_(NOT_CITABLE_STATUSES))
    if only_approved:
        base = base.where(ClauseTranslation.review_status == "approved")

    def hit(token: str):
        kw = f"%{token}%"
        return or_(
            Clause.original_text.ilike(kw),
            ClauseTranslation.translated_text.ilike(kw),
            Document.title.ilike(kw),
        )

    def _to_public(items: list[ClauseTranslationOut]) -> list[PublicClauseTranslationOut]:
        return [PublicClauseTranslationOut(**it.model_dump(exclude={"engine", "model_name", "prompt_version", "tokens_in", "tokens_out", "duration_ms", "source_context_revision", "source_parse_revision"})) for it in items]

    tokens = list((keyword or "").split())[:6]
    if not tokens:
        rows = list(
            (
                await session.execute(base.order_by(Document.title, Clause.ordinal).limit(limit))
            ).all()
        )
        return _to_public(await _build_translation_outs(session, rows))
    ordered = base.order_by(Document.title, Clause.ordinal)
    rows = list(
        (await session.execute(ordered.where(*(hit(t) for t in tokens)).limit(limit))).all()
    )
    if not rows and len(tokens) > 1:
        pool = list(
            (await session.execute(ordered.where(or_(*(hit(t) for t in tokens))).limit(400))).all()
        )

        def matched(row) -> int:
            blob = " ".join(
                (row[1].original_text, row[0].translated_text or "", row[2].title)
            ).lower()
            return sum(1 for t in tokens if t.lower() in blob)

        rows = sorted(pool, key=lambda r: -matched(r))[:limit]
    return _to_public(await _build_translation_outs(session, rows))


@user_router.get("/graph", response_model=GraphPayloadOut)
async def get_user_knowledge_graph(
    _: UserDep,
    session: SessionDep,
    country_code: str = Query(default="SG", min_length=2, max_length=2),
    only_approved: bool = False,
) -> GraphPayloadOut:
    raise HTTPException(410, "请使用法规库的合规主题浏览；关系图谱位于管理端")


@user_router.get("/topics")
async def get_user_topics(
    _: UserDep,
    session: SessionDep,
    country_code: str = Query(pattern="^[A-Za-z]{2}$"),
    document_id: uuid.UUID | None = None,
):
    from app.knowledge.business_view import topic_cards

    if document_id:
        items = await topic_cards(session, country_code, document_id)
    else:
        items = await _topics_cached(session, country_code.upper())
    return {"items": items, "total": len(items)}


_TOPICS_CACHE: dict[str, tuple[float, list[dict]]] = {}


async def _topics_cached(session: AsyncSession, cc: str) -> list[dict]:
    """The compliance topics of a country (half an hour; warmed at start-up with the country pages)."""
    import time

    from app.knowledge.business_view import requirement_topics
    from app.knowledge.cache_sync import use_cached

    hit = _TOPICS_CACHE.get(cc)
    if use_cached(hit):
        return hit[1]
    items = await requirement_topics(session, cc)
    _TOPICS_CACHE[cc] = (time.monotonic(), items)
    return items


@user_router.get("/glossary", response_model=list[GlossaryTermOut])
async def get_user_glossary(
    _: UserDep,
    session: SessionDep,
    country_code: str = Query(default="SG", min_length=2, max_length=2),
) -> list[GlossaryTerm]:
    cc = country_code.upper()
    stmt = (
        select(GlossaryTerm)
        .where(
            or_(GlossaryTerm.country_code == cc, GlossaryTerm.country_code.is_(None)),
            GlossaryTerm.review_status == "approved",
        )
        .order_by(GlossaryTerm.priority_level, GlossaryTerm.concept_code)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _version_unit_counts(
    session: AsyncSession,
    version_id: uuid.UUID,
    *,
    is_native_zh: bool | None = None,
    intake_scope: dict | None = None,
) -> tuple[int, int]:
    from app.knowledge.library_view import (
        light_rows,
        qualified_translation_ids,
        usable_unit_list,
    )
    from app.knowledge.reading import reading_units

    if is_native_zh is None or intake_scope is None:
        ver_obj = await session.get(DocumentVersion, version_id)
        doc_obj = await session.get(Document, ver_obj.document_id) if ver_obj else None
        if is_native_zh is None:
            is_native_zh = str((doc_obj.language if doc_obj else "") or "").lower().startswith("zh")
        if intake_scope is None and doc_obj is not None:
            intake_scope = doc_obj.intake_scope

    rows_map = await light_rows(session, [version_id])
    rows = rows_map.get(version_id) or []
    if not rows:
        return 0, 0
    tr_by_ver, zh_by_ver = await qualified_translation_ids(session, [version_id])
    translated_cids = tr_by_ver.get(version_id, set())
    zh_cids = zh_by_ver.get(version_id, set())
    units = usable_unit_list(
        reading_units(rows),
        is_native_zh=bool(is_native_zh),
        intake_scope=intake_scope,
        version_id=version_id,
        translated_cids=translated_cids,
        zh_cids=zh_cids,
    )
    if is_native_zh:
        return len(units), len(units)
    translated_units = sum(
        1 for u in units if any(c.id in translated_cids or c.id in zh_cids for c in u.rows)
    )
    return len(units), translated_units


async def _build_regulation_summary(
    session: AsyncSession, doc: Document
) -> tuple[RegulationSummaryOut, DocumentVersion | None]:
    if await document_is_held(session, doc.id):
        raise HTTPException(409, "原文绑定异常已隔离，请管理员核实正式来源")
    versions = list(
        (
            await session.execute(
                select(DocumentVersion)
                .where(
                    DocumentVersion.document_id == doc.id,
                    DocumentVersion.review_status != "rejected",
                )
                .order_by(DocumentVersion.version_no.desc())
            )
        )
        .scalars()
        .all()
    )
    from app.analysis.workflow_engine import country_name_zh

    latest = (
        next(
            (v for v in versions if v.state == "parsed" and v.legal_status != "draft"),
            None,
        )
        or next((v for v in versions if v.state == "parsed"), None)
        or (versions[0] if versions else None)
    )
    site = await session.get(SourceSite, doc.site_id)
    publisher = site.publisher if site else f"{country_name_zh(doc.country_code)}官方发布机构"

    clauses_count = 0
    translated_count = 0
    edges_count = 0
    rel_types: set[str] = set()
    data_names: list[str] = []
    obl_names: list[str] = []

    if latest is not None:
        clauses_count, translated_count = await _version_unit_counts(
            session, latest.id, is_native_zh=str(doc.language or "").lower().startswith("zh")
        )

    inst_node = (
        await session.execute(
            select(KgNode)
            .where(KgNode.document_id == doc.id, KgNode.review_status != "superseded")
            .limit(1)
        )
    ).scalar_one_or_none()
    if inst_node is not None:
        linked_edges = list(
            (
                await session.execute(
                    select(KgEdge).where(
                        *active_edge_filters(),
                        or_(
                            KgEdge.source_node_id == inst_node.id,
                            KgEdge.target_node_id == inst_node.id,
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        edges_count = len(linked_edges)
        target_ids = {e.target_node_id for e in linked_edges}
        if target_ids:
            tnodes = list(
                (await session.execute(select(KgNode).where(KgNode.id.in_(target_ids))))
                .scalars()
                .all()
            )
            for tn in tnodes:
                if tn.node_type == NodeType.DATA_CATEGORY.value:
                    data_names.append(tn.name_zh)
                elif tn.node_type == NodeType.OBLIGATION.value:
                    obl_names.append(tn.name_zh)
        for e in linked_edges:
            rel_types.add(e.relation_type)

    eight_attrs = _compute_eight_attributes(
        doc.country_code,
        title=doc.title,
        rel_types=rel_types,
        data_names=data_names,
        obl_names=obl_names,
    )
    if inst_node is not None and linked_edges:
        source_rows = (
            await session.execute(
                select(Clause.id, Clause.clause_number, Clause.original_text).where(
                    Clause.id.in_([e.evidence_clause_id for e in linked_edges])
                )
            )
        ).all()
        node_ids = {e.source_node_id for e in linked_edges} | {
            e.target_node_id for e in linked_edges
        }
        all_nodes = (await session.scalars(select(KgNode).where(KgNode.id.in_(node_ids)))).all()
        actual, _ = evidence_attributes(
            linked_edges,
            {n.id: n for n in all_nodes},
            {cid: (number, doc.title, original) for cid, number, original in source_rows},
            node_id=inst_node.id,
        )
        eight_attrs.update(actual)
    summary = RegulationSummaryOut(
        id=doc.id,
        country_code=doc.country_code,
        title=doc.title,
        title_zh=doc.title_zh,
        document_type=public_doc_type(doc.document_type, doc.instrument_kind),
        document_type_zh=_doc_type_zh(
            public_doc_type(doc.document_type, doc.instrument_kind), doc.title
        ),
        language=doc.language,
        source_url=doc.source_url,
        publisher=publisher,
        version_no=latest.version_no if latest else 1,
        legal_status=latest.legal_status if latest else "in_force",
        fetched_at=latest.fetched_at if latest else doc.discovered_at,
        sha256_hex=latest.content_hash if latest else None,
        clauses_count=clauses_count,
        translated_clauses_count=translated_count,
        edges_count=edges_count,
        eight_attributes=eight_attrs,
        version_count=len(versions) or 1,
    )
    return summary, latest


_REGULATIONS_LIST_CACHE: dict[str, list[RegulationSummaryOut]] = {}


async def _build_all_user_regulations(session: AsyncSession) -> dict[str, list[RegulationSummaryOut]]:
    """Vectorized 3-query build of RegulationSummaryOut across all countries in the library."""
    from collections import defaultdict

    from app.analysis.workflow_engine import country_name_zh
    from app.knowledge.library_view import library, main_law, relation_counts

    all_entries = await library(session, None, include_peripheral=True)
    all_vids = [e.version.id for e in all_entries]
    relations = await relation_counts(session, all_vids) if all_vids else {}
    site_ids = {e.document.site_id for e in all_entries if e.document.site_id}
    sites = (
        {
            s.id: s.publisher
            for s in (await session.scalars(select(SourceSite).where(SourceSite.id.in_(site_ids)))).all()
        }
        if site_ids
        else {}
    )
    by_cc: dict[str, list] = defaultdict(list)
    for e in all_entries:
        by_cc[e.document.country_code].append(e)

    out_by_cc: dict[str, list[RegulationSummaryOut]] = {}
    all_flat: list[RegulationSummaryOut] = []
    for cc, entries in sorted(by_cc.items()):
        main = main_law(entries, relations)
        cc_list: list[RegulationSummaryOut] = []
        for e in entries:
            doc, v = e.document, e.version
            kind = public_doc_type(doc.document_type, doc.instrument_kind)
            item = RegulationSummaryOut(
                id=doc.id,
                country_code=doc.country_code,
                title=doc.title,
                title_zh=doc.title_zh,
                business_topic=doc.business_topic,
                business_weight=doc.business_weight,
                document_type=kind,
                document_type_zh=_doc_type_zh(kind, doc.title),
                language=doc.language,
                source_url=doc.source_url,
                publisher=sites.get(doc.site_id) or f"{country_name_zh(doc.country_code)}官方发布机构",
                version_no=v.version_no,
                legal_status=v.legal_status,
                fetched_at=v.fetched_at,
                sha256_hex=None,
                clauses_count=e.articles,
                translated_clauses_count=0,
                edges_count=0,
                eight_attributes={},
                version_count=1,
                relations_count=relations.get(v.id, 0),
                primary=main is not None and main.document.id == doc.id,
            )
            cc_list.append(item)
            all_flat.append(item)
        out_by_cc[cc] = cc_list
    _REGULATIONS_LIST_CACHE.clear()
    _REGULATIONS_LIST_CACHE.update(out_by_cc)
    _REGULATIONS_LIST_CACHE["ALL"] = all_flat
    return _REGULATIONS_LIST_CACHE


@user_router.get("/regulations", response_model=list[PublicRegulationSummary])
async def list_user_regulations(
    _: UserDep,
    session: SessionDep,
    country_code: str = Query(default="SG", min_length=2, max_length=3),
    keyword: str | None = None,
    legal_status: str | None = Query(default=None, max_length=32),
) -> list[RegulationSummaryOut]:
    """按国家（或 country_code=ALL 一次批量获取全库各国）返回法规文件目录。"""
    from app.knowledge.cache_sync import use_cached
    from app.knowledge.library_view import library, main_law, relation_counts

    cc_norm = country_code.strip().upper()
    status_filter = (legal_status or "").strip().lower()
    terms = [t for t in (keyword or "").strip().lower().split() if t]
    unfiltered = (not status_filter or status_filter == "all") and not terms

    if cc_norm == "ALL":
        if use_cached("ALL" in _REGULATIONS_LIST_CACHE):
            base_all = _REGULATIONS_LIST_CACHE["ALL"]
        else:
            base_all = (await _build_all_user_regulations(session))["ALL"]
        if unfiltered:
            return base_all
        items = base_all
        if status_filter and status_filter != "all":
            items = [r for r in items if (r.legal_status or "in_force").lower() == status_filter]
        if terms:
            items = [
                r
                for r in items
                if all(t in f"{r.title or ''} {r.title_zh or ''} {r.business_topic or ''} {r.country_code or ''}".lower() for t in terms)
            ]
        return items

    if len(cc_norm) != 2:
        raise HTTPException(status_code=422, detail="国家或地区代码格式不正确")

    if unfiltered and use_cached(cc_norm in _REGULATIONS_LIST_CACHE):
        return _REGULATIONS_LIST_CACHE[cc_norm]

    # 外围文件（与车辆数据业务关系较远）也返回、带上标记：目录里不列，按名称搜索时能找到
    entries = await library(session, cc_norm, include_peripheral=True)
    relations = await relation_counts(session, [e.version.id for e in entries])
    main = main_law(entries, relations)
    status_filter = (legal_status or "").strip().lower()
    if status_filter and status_filter != "all":
        entries = [
            e for e in entries if (e.version.legal_status or "in_force").lower() == status_filter
        ]
    terms = [t for t in (keyword or "").strip().lower().split() if t]
    if terms:
        def _haystack(entry) -> str:
            d = entry.document
            return " ".join(
                [d.title or "", d.title_zh or "", d.business_topic or "", d.country_code or ""]
            ).lower()

        entries = [e for e in entries if all(t in _haystack(e) for t in terms)]
    sites = (
        {
            s.id: s.publisher
            for s in (
                await session.scalars(
                    select(SourceSite).where(
                        SourceSite.id.in_({e.document.site_id for e in entries})
                    )
                )
            ).all()
        }
        if entries
        else {}
    )
    from app.analysis.workflow_engine import country_name_zh

    results: list[RegulationSummaryOut] = []
    for e in entries:
        doc, v = e.document, e.version
        kind = public_doc_type(doc.document_type, doc.instrument_kind)
        results.append(
            RegulationSummaryOut(
                id=doc.id,
                country_code=doc.country_code,
                title=doc.title,
                title_zh=doc.title_zh,
                business_topic=doc.business_topic,
                business_weight=doc.business_weight,
                document_type=kind,
                document_type_zh=_doc_type_zh(kind, doc.title),
                language=doc.language,
                source_url=doc.source_url,
                publisher=sites.get(doc.site_id) or f"{country_name_zh(doc.country_code)}官方发布机构",
                version_no=v.version_no,
                legal_status=v.legal_status,
                fetched_at=v.fetched_at,
                sha256_hex=None,
                clauses_count=e.articles,
                translated_clauses_count=0,
                edges_count=0,
                eight_attributes={},
                version_count=1,
                relations_count=relations.get(v.id, 0),
                primary=main is not None and main.document.id == doc.id,
            )
        )
    return results


@user_router.get("/regulations/{document_id}/relations", response_model=ReaderRelations)
async def get_reader_relations(
    document_id: uuid.UUID,
    _: UserDep,
    session: SessionDep,
    version_id: uuid.UUID | None = None,
    offset: int = Query(default=0, ge=0, le=2147483647),
    limit: int = Query(default=60, ge=1, le=120),
):
    return await reader_relations(session, document_id, version_id, offset, limit)


@user_router.get("/regulations/{document_id}", response_model=PublicRegulationDetail)
async def get_user_regulation_detail(
    document_id: uuid.UUID,
    _: UserDep,
    session: SessionDep,
    clause_offset: int = Query(default=0, ge=0, le=2147483647),
    clause_limit: int = Query(default=12, ge=1, le=100),
    keyword: str = Query(default="", max_length=200),
    focus_clause_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
) -> RegulationDetailOut:
    """Paginated original/Chinese reading; exact source navigation without graph diagnostics."""
    doc = await session.get(Document, document_id)
    if doc is None or getattr(doc, "relevance", "") == "irrelevant":
        raise HTTPException(404, "法规文件不存在")
    summary, latest = await _build_regulation_summary(session, doc)
    if focus_clause_id:
        focus = await session.get(Clause, focus_clause_id)
        focused_version = await session.get(DocumentVersion, focus.version_id) if focus else None
        if (
            not focused_version
            or focused_version.document_id != doc.id
            or focused_version.review_status == "rejected"
        ):
            raise HTTPException(404, "条款不属于该法规")
        version_id = focused_version.id
    if version_id:
        selected = await session.get(DocumentVersion, version_id)
        if (
            not selected
            or selected.document_id != doc.id
            or selected.state != "parsed"
            or selected.review_status == "rejected"
        ):
            raise HTTPException(404, "归档版本不存在或尚未完成解析")
        latest = selected
        summary.version_no, summary.legal_status = selected.version_no, selected.legal_status
        summary.clauses_count, summary.translated_clauses_count = await _version_unit_counts(
            session, selected.id
        )
    versions = list(
        (
            await session.scalars(
                select(DocumentVersion)
                .where(
                    DocumentVersion.document_id == doc.id,
                    DocumentVersion.review_status != "rejected",
                )
                .order_by(DocumentVersion.version_no.desc())
            )
        ).all()
    )
    clauses_out, total = [], 0
    if latest:
        from app.ingestion.display_text import for_reading
        from app.ingestion.intake_scope import note_for as scope_note_for
        from app.ingestion.safety import held_citation_ids
        from app.knowledge.library_view import qualified_translation_ids, usable_unit_list
        from app.knowledge.numeric_integrity import is_numeric_only
        from app.knowledge.reading import joined, reading_units
        from app.knowledge.user_relations import reader_relations

        rows = list(
            (await session.scalars(select(Clause).where(Clause.version_id == latest.id))).all()
        )
        all_units = reading_units(rows)
        tr_by_ver, zh_by_ver = await qualified_translation_ids(session, [latest.id])
        units = usable_unit_list(
            all_units,
            is_native_zh=str(doc.language or "").lower().startswith("zh"),
            intake_scope=doc.intake_scope,
            version_id=latest.id,
            translated_cids=tr_by_ver.get(latest.id, set()),
            zh_cids=zh_by_ver.get(latest.id, set()),
        )
        if focus_clause_id and not any(focus_clause_id in u.ids for u in units):
            focused_u = next((u for u in all_units if focus_clause_id in u.ids), None)
            if focused_u is not None:
                units = sorted([*units, focused_u], key=lambda u: u.head.ordinal)
        first_numbered = next((u.head.ordinal for u in units if u.head.clause_number), None)
        if first_numbered is None and units:
            first_numbered = units[0].head.ordinal + 1  # 全文都没有条号：只有第一段算“前言”
        position = {u.head.id: k + 1 for k, u in enumerate(units)}
        if keyword.strip():
            from sqlalchemy.orm import aliased

            ct_ambig = aliased(ClauseTranslation)
            no_ambig_tr = (
                ~select(ct_ambig.id)
                .where(
                    ct_ambig.clause_id == ClauseTranslation.clause_id,
                    ct_ambig.id != ClauseTranslation.id,
                    ct_ambig.is_current.is_(True),
                    ct_ambig.quality_check_passed.is_(True),
                    ct_ambig.review_status.in_(
                        ("approved", "machine_translated", "pending_review")
                    ),
                )
                .correlate(ClauseTranslation)
                .exists()
            )
            terms = [t for t in keyword.strip().split() if t][:6]
            term_conds = []
            for term in terms:
                pattern = f"%{term}%"
                translated_matches = select(ClauseTranslation.clause_id).where(
                    ClauseTranslation.is_current.is_(True),
                    ClauseTranslation.quality_check_passed.is_(True),
                    ClauseTranslation.review_status.in_(
                        ("approved", "machine_translated", "pending_review")
                    ),
                    no_ambig_tr,
                    ClauseTranslation.translated_text.ilike(pattern),
                )
                term_conds.append(
                    or_(
                        Clause.original_text.ilike(pattern),
                        Clause.heading.ilike(pattern),
                        Clause.clause_number.ilike(pattern),
                        Clause.id.in_(translated_matches),
                    )
                )
            hits = set(
                (
                    await session.scalars(
                        select(Clause.id).where(Clause.version_id == latest.id, *term_conds)
                    )
                ).all()
            )
            units = [u for u in units if hits & set(u.ids)]
        total = len(units)
        if focus_clause_id:
            at = next((k for k, u in enumerate(units) if focus_clause_id in u.ids), 0)
            clause_offset = (at // clause_limit) * clause_limit
        page_units = units[clause_offset : clause_offset + clause_limit]
        ids = [cid for u in page_units for cid in u.ids]
        held = set(await held_citation_ids(session, ids)) if ids else set()
        translations = (
            list(
                (
                    await session.scalars(
                        select(ClauseTranslation).where(
                            ClauseTranslation.clause_id.in_(ids),
                            ClauseTranslation.is_current.is_(True),
                            ClauseTranslation.quality_check_passed.is_(True),
                            ClauseTranslation.review_status.in_(
                                ("approved", "machine_translated", "pending_review")
                            ),
                        )
                    )
                ).all()
            )
            if ids
            else []
        )
        by_clause = {}
        for tr in translations:
            by_clause.setdefault(tr.clause_id, []).append(tr)

        def usable(c):
            candidates = by_clause.get(c.id, [])
            return candidates[0] if len(candidates) == 1 else None

        for unit in page_units:
            head = unit.head
            parts = [(c, usable(c)) for c in unit.rows]
            done = [tr for _, tr in parts if tr]
            has_ambiguous = any(len(by_clause.get(c.id, [])) > 1 for c in unit.rows)
            is_zh_unit = str(head.language or doc.language or "").lower().startswith("zh")
            if is_zh_unit and not done:
                zh = None
                state = (
                    "approved"
                    if latest.review_status == "approved"
                    and not any(c.needs_review or c.id in held for c in unit.rows)
                    else "native_zh"
                )
            elif not done and has_ambiguous:
                zh, state = None, "ambiguous_current"
            elif not done:
                zh, state = None, "not_translated"
            else:
                # 一条由几段组成：每段用自己的中文；纯编号段保留原编号
                zh = for_reading(
                    joined(
                        [
                            tr.translated_text
                            if tr
                            else (
                                c.original_text.strip()
                                if is_numeric_only(c.original_text)
                                else "〔此段暂无中文〕"
                            )
                            for c, tr in parts
                        ]
                    )
                )
                states = {tr.review_status for tr in done}
                state = (
                    "approved"
                    if states == {"approved"} and len(done) == len(parts)
                    else (
                        "machine_translated"
                        if "machine_translated" in states
                        else done[0].review_status
                    )
                )
            clauses_out.append(
                RegulationClauseItemOut(
                    clause_id=head.id,
                    part_ids=unit.ids,
                    position=position[head.id],
                    preamble=not head.clause_number
                    and (first_numbered is None or head.ordinal < first_numbered),
                    ordinal=head.ordinal,
                    scope_note=scope_note_for(doc.intake_scope, latest.id, head.ordinal),
                    clause_number=head.clause_number,
                    heading=head.heading,
                    section_path=head.section_path,
                    original_text=for_reading(joined([c.original_text for c in unit.rows])),
                    translated_text=zh,
                    translation_status=state,
                    status_badge=_status_badge(state)[1],
                    usable_as_legal_basis=bool(
                        (
                            is_zh_unit
                            or (
                                done
                                and len(done) == len(parts)
                                and all(tr.review_status == "approved" for tr in done)
                            )
                        )
                        and latest.review_status == "approved"
                        and latest.legal_status not in NOT_CITABLE_STATUSES
                        and doc.relevance not in ("irrelevant", "out_of_scope")
                        and not any(c.needs_review or c.id in held for c in unit.rows)
                    ),
                    glossary_hits=[],
                    unmatched_terms=[],
                    linked_edges=[],
                )
            )
        summary.relations_count = (await reader_relations(session, doc.id, latest.id, 0, 1)).total
    return RegulationDetailOut(
        regulation=summary,
        version_id=latest.id if latest else None,
        clauses_total=total,
        clause_offset=clause_offset,
        versions=[
            dict(
                version_no=v.version_no,
                legal_status=v.legal_status,
                fetched_at=v.fetched_at.isoformat() if v.fetched_at else None,
                version_label=v.version_label,
                published_date=v.published_date.isoformat() if v.published_date else None,
            )
            for v in versions
        ],
        clauses=clauses_out,
        mini_graph=GraphPayloadOut(
            country_code=doc.country_code,
            node_counts_by_type={},
            edge_counts_by_relation={},
            nodes=[],
            edges=[],
        ),
    )


_STD_KIND_ZH = {"mandatory": "强制性", "recommended": "推荐性", "guidance": "指导性"}
_STD_STATUS_ZH = {"in_force": "现行", "upcoming": "即将实施"}
_STD_COUNTRY_FALLBACK = {
    "ZZ": "国际通用标准",
    "INTL": "国际通用标准",
    "ISO": "国际通用标准",
    "UN": "联合国法规标准",
    "EU": "欧盟",
    "CN": "中国",
}


@user_router.get("/standards")
async def list_public_standards(
    _: UserDep,
    session: SessionDep,
    country_code: str | None = Query(None, max_length=8),
    kind: str | None = Query(None, max_length=24),
    q: str | None = Query(None, max_length=80),
    limit: int = Query(1000, ge=1, le=1000),
    offset: int = Query(0, ge=0, le=2147483647),
) -> dict:
    """User-portal read-only automotive data & cybersecurity technical standards directory (in_force + upcoming)."""
    from app.analysis.workflow_engine import country_name_zh
    from app.standards.models import StandardEntry

    def _std_country_zh(code: str) -> str:
        return _STD_COUNTRY_FALLBACK.get(code) or country_name_zh(code)

    active_statuses = ("in_force", "upcoming")
    base_where = [StandardEntry.status.in_(active_statuses)]
    if country_code and country_code.strip():
        cc = country_code.strip().upper()
        base_where.append(StandardEntry.country_code == cc)
    if kind and kind.strip():
        base_where.append(StandardEntry.kind == kind.strip())
    if q and q.strip():
        for tok in q.strip().split()[:5]:
            base_where.append(
                or_(
                    StandardEntry.title.ilike(f"%{tok}%"),
                    StandardEntry.number.ilike(f"%{tok}%"),
                    StandardEntry.publisher.ilike(f"%{tok}%"),
                )
            )

    total = int(
        (await session.scalar(select(func.count(StandardEntry.id)).where(*base_where))) or 0
    )
    rows = (
        await session.scalars(
            select(StandardEntry)
            .where(*base_where)
            .order_by(StandardEntry.published_on.desc().nulls_last(), StandardEntry.number)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    country_counts_rows = (
        await session.execute(
            select(StandardEntry.country_code, func.count(StandardEntry.id))
            .where(StandardEntry.status.in_(active_statuses))
            .group_by(StandardEntry.country_code)
            .order_by(func.count(StandardEntry.id).desc())
        )
    ).all()
    countries_summary = [
        {
            "country_code": cc,
            "country_name_zh": _std_country_zh(cc),
            "count": int(cnt or 0),
        }
        for cc, cnt in country_counts_rows
    ]
    return {
        "total": total,
        "countries": countries_summary,
        "items": [
            {
                "id": str(r.id),
                "country_code": r.country_code,
                "country_name_zh": _std_country_zh(r.country_code),
                "number": r.number,
                "title": r.title,
                "kind_code": r.kind,
                "kind": _STD_KIND_ZH.get(r.kind, r.kind),
                "status_code": r.status,
                "status": _STD_STATUS_ZH.get(r.status, r.status),
                "published_on": r.published_on.isoformat() if r.published_on else None,
                "effective_on": r.effective_on.isoformat() if r.effective_on else None,
                "topics": list(r.topics or []),
                "detail_url": r.detail_url,
                "publisher": r.publisher,
            }
            for r in rows
        ],
    }


@admin_router.get("/posture", summary="全球法规知识底座与采集巡检态势概览（运维与超管共用底座视图，不含租户与审计数据）")
async def admin_knowledge_posture(
    _: AdminSideDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Return global regulatory, translation, knowledge-graph, briefing, official-form, and source-patrol
    posture metrics without touching tenant organizations, accounts, enterprise projects, or audit logs."""
    from sqlalchemy import text as sql_text

    from app.analysis.documents_router import _CN_OFFICIAL
    from app.analysis.workflow_engine import country_name_zh
    from app.forms import service as forms_service
    from app.iam.cockpit_router import _kb_posture, _safe_pct
    from app.sources.attention import attention_of

    now = datetime.now(UTC)
    kb = await _kb_posture(session)
    docs_by_cc = kb["docs_by_cc"]
    clauses_by_cc = kb["clauses_by_cc"]
    foreign_clauses_by_cc = kb["foreign_clauses_by_cc"]
    translated_by_cc = kb["translated_by_cc"]
    approved_tr_by_cc = kb["approved_tr_by_cc"]
    kg_nodes_by_cc = kb["kg_nodes_by_cc"]
    kg_by_type = kb["kg_by_type"]
    kg_edges_by_cc = kb["kg_edges_by_cc"]
    standards_by_cc = kb["standards_by_cc"]
    wp_by_cc = kb["wp_by_cc"]
    whitepapers_outdated = kb["whitepapers_outdated"]
    adm_entries_by_cc = kb["adm_entries_by_cc"]
    quality_unpassed = kb["quality_unpassed"]

    forms_by_cc: dict[str, int] = {}
    for f in [*_CN_OFFICIAL, *forms_service.catalog().values()]:
        cc = str(f.get("country") or f.get("country_code") or "").upper()
        if cc:
            forms_by_cc[cc] = forms_by_cc.get(cc, 0) + 1

    site_rows = await session.execute(select(SourceSite).where(SourceSite.enabled.is_(True)))
    monitor_sites = list(site_rows.scalars().all())
    sites_total_by_cc: dict[str, int] = {}
    sites_attention_by_cc: dict[str, int] = {}
    attention_sites_total = 0
    for s in monitor_sites:
        cc = s.country_code
        sites_total_by_cc[cc] = sites_total_by_cc.get(cc, 0) + 1
        att = attention_of(
            enabled=s.enabled,
            terms_status=s.terms_status,
            consecutive_failures=s.consecutive_failures,
            last_run_at=s.last_run_at,
            interval_hours=s.interval_hours,
            now=now,
        )
        if s.enabled and att:
            sites_attention_by_cc[cc] = sites_attention_by_cc.get(cc, 0) + 1
            attention_sites_total += 1

    pending_updates = int(
        (
            await session.execute(
                sql_text("SELECT count(DISTINCT change_id) FROM update_impacts WHERE state = 'open'")
            )
        ).scalar_one()
        or 0
    )
    opinion_total = int(
        (await session.execute(sql_text("SELECT count(*) FROM opinion_items"))).scalar_one() or 0
    )

    non_sovereign = {"ZZ", "INTL", "--", ""}
    candidate_codes = (
        set(docs_by_cc)
        | set(clauses_by_cc)
        | set(kg_nodes_by_cc)
        | set(standards_by_cc)
        | set(wp_by_cc)
        | set(adm_entries_by_cc)
        | set(forms_by_cc)
    ) - non_sovereign
    all_codes = sorted(
        (c for c in candidate_codes if docs_by_cc.get(c, 0) > 0 or standards_by_cc.get(c, 0) > 0),
        key=lambda c: (
            0 if c in ("CN", "SG", "EU", "DE", "MA", "TN", "EG") else 1,
            -docs_by_cc.get(c, 0),
            -clauses_by_cc.get(c, 0),
            c,
        ),
    )

    countries: list[dict[str, Any]] = []
    for cc in all_codes:
        f_total = foreign_clauses_by_cc.get(cc, 0)
        f_trans = translated_by_cc.get(cc, 0)
        is_zh_native = cc == "CN" and f_total == 0
        wp_info = wp_by_cc.get(cc, {"ready": False, "updated_at": None})
        countries.append(
            {
                "country_code": cc,
                "country_name_zh": country_name_zh(cc),
                "is_origin_country": cc == "CN",
                "laws_count": docs_by_cc.get(cc, 0),
                "clauses_count": clauses_by_cc.get(cc, 0),
                "is_zh_native": is_zh_native,
                "foreign_clauses_count": f_total,
                "translated_clauses_count": f_trans,
                "approved_clauses_count": approved_tr_by_cc.get(cc, 0),
                "translation_pct": (
                    100.0 if is_zh_native and clauses_by_cc.get(cc, 0) > 0 else _safe_pct(f_trans, f_total)
                ),
                "standards_count": standards_by_cc.get(cc, 0),
                "kg_nodes_count": kg_nodes_by_cc.get(cc, 0),
                "kg_edges_count": kg_edges_by_cc.get(cc, 0),
                "briefing_ready": bool(wp_info["ready"]),
                "briefing_updated_at": wp_info["updated_at"],
                "admission_entries_count": adm_entries_by_cc.get(cc, 0),
                "official_forms_count": forms_by_cc.get(cc, 0),
                "monitor_sites_count": sites_total_by_cc.get(cc, 0),
                "attention_sites_count": sites_attention_by_cc.get(cc, 0),
                "enterprise_projects_count": 0,
            }
        )

    total_foreign = sum(foreign_clauses_by_cc.values())
    total_translated = sum(translated_by_cc.values())
    intl_standards_count = sum(v for k, v in standards_by_cc.items() if k in ("ZZ", "INTL"))
    national_standards_count = sum(v for k, v in standards_by_cc.items() if k not in ("ZZ", "INTL"))
    covered_count = len([c for c in countries if c["laws_count"] > 0 or c["standards_count"] > 0])

    return {
        "generated_at": now.isoformat(),
        "headline": {
            "covered_jurisdictions": covered_count,
            "laws_in_force": sum(docs_by_cc.values()),
            "laws_total": kb["laws_total"],
            "laws_archived_total": kb["laws_archived_total"],
            "clauses_total": sum(clauses_by_cc.values()),
            "foreign_clauses_total": total_foreign,
            "translated_clauses_total": total_translated,
            "translation_coverage_pct": _safe_pct(total_translated, total_foreign),
            "standards_active": sum(standards_by_cc.values()),
            "intl_standards_count": intl_standards_count,
            "national_standards_count": national_standards_count,
            "standards_cn": standards_by_cc.get("CN", national_standards_count),
            "standards_intl": intl_standards_count,
            "standards_international": intl_standards_count,
            "kg_nodes_total": sum(kg_nodes_by_cc.values()),
            "kg_edges_total": sum(kg_edges_by_cc.values()),
            "briefings_ready": sum(
                1 for k, v in wp_by_cc.items() if k not in ("CN", "ZZ", "INTL") and v["ready"]
            ),
            "whitepapers_outdated": whitepapers_outdated,
            "admission_entries_total": sum(adm_entries_by_cc.values()),
            "official_forms_total": sum(forms_by_cc.values()),
            "official_forms_eu": forms_by_cc.get("EU", 0),
            "active_organizations": 0,
            "doc_projects_total": 0,
            "admission_projects_total": 0,
            "consulting_chats_total": 0,
            "opinion_items_total": opinion_total,
        },
        "kg_node_breakdown": [
            {"label": "国家与地区", "count": kg_by_type.get("COUNTRY", 0)},
            {"label": "监管机构", "count": kg_by_type.get("REGULATOR", 0)},
            {"label": "法规文件", "count": kg_by_type.get("INSTRUMENT", 0)},
            {"label": "数据类型", "count": kg_by_type.get("DATA_CATEGORY", 0)},
            {"label": "合规义务", "count": kg_by_type.get("OBLIGATION", 0)},
            {"label": "违规罚则", "count": kg_by_type.get("PENALTY", 0)},
        ],
        "account_summary": {
            "super_admins": 0,
            "ops_admins": 0,
            "enterprise_members": 0,
            "ordinary_viewers": 0,
            "portal_onboarded": 0,
            "trial_visitors": 0,
        },
        "operations": {
            "monitor_sites_total": len(monitor_sites),
            "attention_sites_total": attention_sites_total,
            "pending_updates": pending_updates,
            "quality_unpassed": quality_unpassed,
            "whitepapers_outdated": whitepapers_outdated,
            "audit_events_7d": 0,
        },
        "routes": [],
        "countries": countries,
    }


@user_router.get("/posture-summary", summary="用户端全球法规与准入底座能力概览（纯公共法规知识计数，零跨租户数据）")
async def user_knowledge_posture_summary(
    _: UserDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Public knowledge-base coverage summary for enterprise users, read-only viewers, and portal trial visitors."""
    from app.analysis.documents_router import _CN_OFFICIAL
    from app.analysis.workflow_engine import country_name_zh
    from app.forms import service as forms_service
    from app.iam.cockpit_router import _kb_posture, _safe_pct

    kb = await _kb_posture(session)
    docs_by_cc = kb["docs_by_cc"]
    clauses_by_cc = kb["clauses_by_cc"]
    foreign_clauses_by_cc = kb["foreign_clauses_by_cc"]
    translated_by_cc = kb["translated_by_cc"]
    standards_by_cc = kb["standards_by_cc"]
    wp_by_cc = kb["wp_by_cc"]
    adm_entries_by_cc = kb["adm_entries_by_cc"]

    forms_by_cc: dict[str, int] = {}
    for f in [*_CN_OFFICIAL, *forms_service.catalog().values()]:
        cc = str(f.get("country") or f.get("country_code") or "").upper()
        if cc:
            forms_by_cc[cc] = forms_by_cc.get(cc, 0) + 1

    non_sovereign = {"ZZ", "INTL", "--", ""}
    candidate_codes = (
        set(docs_by_cc)
        | set(clauses_by_cc)
        | set(standards_by_cc)
        | set(wp_by_cc)
        | set(adm_entries_by_cc)
        | set(forms_by_cc)
    ) - non_sovereign
    all_codes = sorted(
        (c for c in candidate_codes if docs_by_cc.get(c, 0) > 0 or standards_by_cc.get(c, 0) > 0),
        key=lambda c: (-docs_by_cc.get(c, 0), -clauses_by_cc.get(c, 0), c),
    )

    countries = [
        {
            "country_code": cc,
            "country_name_zh": country_name_zh(cc),
            "laws_count": docs_by_cc.get(cc, 0),
            "clauses_count": clauses_by_cc.get(cc, 0),
            "standards_count": standards_by_cc.get(cc, 0),
            "briefing_ready": bool(wp_by_cc.get(cc, {}).get("ready")),
            "admission_entries_count": adm_entries_by_cc.get(cc, 0),
            "official_forms_count": forms_by_cc.get(cc, 0),
        }
        for cc in all_codes
    ]
    total_foreign = sum(foreign_clauses_by_cc.values())
    total_translated = sum(translated_by_cc.values())
    intl_std = sum(v for k, v in standards_by_cc.items() if k in ("ZZ", "INTL"))
    cn_std = standards_by_cc.get("CN", sum(v for k, v in standards_by_cc.items() if k not in ("ZZ", "INTL")))

    return {
        "covered_jurisdictions": len(countries),
        "laws_in_force": sum(docs_by_cc.values()),
        "clauses_total": sum(clauses_by_cc.values()),
        "translation_coverage_pct": _safe_pct(total_translated, total_foreign) or 100.0,
        "standards_active": sum(standards_by_cc.values()),
        "standards_cn": cn_std,
        "standards_intl": intl_std,
        "kg_nodes_total": sum(kb["kg_nodes_by_cc"].values()),
        "kg_edges_total": sum(kb["kg_edges_by_cc"].values()),
        "briefings_ready": sum(
            1 for k, v in wp_by_cc.items() if k not in ("CN", "ZZ", "INTL") and v["ready"]
        ),
        "admission_entries_total": sum(adm_entries_by_cc.values()),
        "official_forms_total": sum(forms_by_cc.values()),
        "countries": countries,
    }
