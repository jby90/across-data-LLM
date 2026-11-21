"""超级管理员专属合规态势看板聚合接口（100% 实时数据库真实统计，零模拟指标）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.workflow_engine import country_name_zh
from app.core.db import get_session
from app.forms import service as forms_service
from app.iam import service as iam_service
from app.iam.deps import Principal, require_super_admin
from app.sources.attention import attention_of
from app.sources.models import SourceSite

router = APIRouter(prefix="/api/admin/cockpit", tags=["cockpit"])


def _safe_pct(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return round(min(num, den) * 100.0 / den, 1)


@router.get("/overview", summary="全球法规底座与平台服务态势概览")
async def cockpit_overview(
    _: Principal = Depends(require_super_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    now = datetime.now(UTC)
    seven_days_ago = now - timedelta(days=7)

    # 1. 各国有效法规数（取每部法规最新非草案、未驳回修订版，统计相关性在范围内且现行有效的法规）
    doc_rows = (
        await session.execute(
            text(
                """
                WITH latest_dv AS (
                    SELECT DISTINCT ON (document_id)
                           id, document_id, legal_status
                    FROM document_versions
                    WHERE state = 'parsed'
                      AND review_status <> 'rejected'
                      AND coalesce(legal_status, 'in_force') <> 'draft'
                    ORDER BY document_id, version_no DESC, parse_revision DESC
                )
                SELECT d.country_code, count(DISTINCT d.id) AS doc_cnt
                FROM documents d
                JOIN latest_dv dv ON dv.document_id = d.id
                WHERE d.relevance IN ('relevant', 'related')
                  AND coalesce(dv.legal_status, 'in_force') NOT IN ('repealed', 'draft')
                  AND NOT EXISTS (
                      SELECT 1 FROM knowledge_change_proposals p
                      WHERE p.target_type = 'documents'
                        AND p.target_id = d.id
                        AND p.status IN ('pending_review', 'approved')
                        AND coalesce((p.payload->>'safety_hold')::boolean, false) = true
                  )
                GROUP BY d.country_code
                """
            )
        )
    ).all()
    docs_by_cc = {r[0]: int(r[1]) for r in doc_rows if r[0]}
    laws_total = sum(docs_by_cc.values())
    laws_archived_total = int(
        (await session.execute(text("SELECT count(*) FROM documents"))).scalar_one() or 0
    )

    # 2. 各国条文数与译文覆盖数（中文原文条文天然可读；外文条文统计已生成/已核验中文译文）
    clause_rows = (
        await session.execute(
            text(
                """
                WITH latest_dv AS (
                    SELECT DISTINCT ON (document_id)
                           id, document_id, legal_status
                    FROM document_versions
                    WHERE state = 'parsed'
                      AND review_status <> 'rejected'
                      AND coalesce(legal_status, 'in_force') <> 'draft'
                    ORDER BY document_id, version_no DESC, parse_revision DESC
                ),
                valid_tr AS (
                    SELECT clause_id,
                           max(review_status) AS review_status
                    FROM clause_translations
                    WHERE is_current = true
                      AND quality_check_passed = true
                      AND review_status IN ('approved', 'machine_translated', 'pending_review')
                    GROUP BY clause_id
                    HAVING count(*) = 1
                )
                SELECT d.country_code,
                       d.language,
                       count(DISTINCT c.id) AS total_clauses,
                       count(DISTINCT c.id) FILTER (
                           WHERE ct.review_status IS NOT NULL
                       ) AS translated_clauses,
                       count(DISTINCT c.id) FILTER (
                           WHERE ct.review_status = 'approved'
                       ) AS approved_clauses
                FROM clauses c
                JOIN latest_dv dv ON dv.id = c.version_id
                JOIN documents d ON d.id = dv.document_id
                LEFT JOIN valid_tr ct ON ct.clause_id = c.id
                WHERE d.relevance IN ('relevant', 'related')
                  AND coalesce(dv.legal_status, 'in_force') NOT IN ('repealed', 'draft')
                  AND NOT EXISTS (
                      SELECT 1 FROM knowledge_change_proposals p
                      WHERE p.target_type = 'documents'
                        AND p.target_id = d.id
                        AND p.status IN ('pending_review', 'approved')
                        AND coalesce((p.payload->>'safety_hold')::boolean, false) = true
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM knowledge_change_proposals p
                      WHERE p.target_type = 'clauses'
                        AND p.status IN ('pending_review', 'approved')
                        AND p.payload->'source_parse_revision'->>'scope' = 'whole_source_page'
                        AND (
                          p.target_id = c.id
                          OR (
                            p.payload->'source_parse_revision'->>'version_id'
                              = c.version_id::text
                            AND (p.payload->'source_parse_revision'->>'page_number')::int
                              >= c.page_start
                            AND (p.payload->'source_parse_revision'->>'page_number')::int
                              <= c.page_end
                          )
                        )
                  )
                GROUP BY d.country_code, d.language
                """
            )
        )
    ).all()
    clauses_by_cc: dict[str, int] = {}
    foreign_clauses_by_cc: dict[str, int] = {}
    translated_by_cc: dict[str, int] = {}
    approved_tr_by_cc: dict[str, int] = {}
    for cc, lang, total_c, trans_c, appr_c in clause_rows:
        if not cc:
            continue
        clauses_by_cc[cc] = clauses_by_cc.get(cc, 0) + int(total_c or 0)
        if not str(lang or "").lower().startswith("zh"):
            foreign_clauses_by_cc[cc] = foreign_clauses_by_cc.get(cc, 0) + int(total_c or 0)
            translated_by_cc[cc] = translated_by_cc.get(cc, 0) + int(trans_c or 0)
            approved_tr_by_cc[cc] = approved_tr_by_cc.get(cc, 0) + int(appr_c or 0)

    # 3. 知识图谱六要素节点与语义关系（与 _build_graph_payload 完全同源过滤）
    from sqlalchemy import func, or_

    from app.ingestion.layout_safety import clause_layout_available
    from app.ingestion.models import NOT_CITABLE_STATUSES, Clause, Document, DocumentVersion
    from app.ingestion.safety import document_available
    from app.knowledge.graph_evidence import active_edge_filters
    from app.knowledge.models import KgEdge, KgNode

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
    node_rows = (
        await session.execute(
            select(KgNode.country_code, KgNode.node_type, func.count(KgNode.id))
            .where(*active_node_conds)
            .group_by(KgNode.country_code, KgNode.node_type)
        )
    ).all()
    kg_nodes_by_cc: dict[str, int] = {}
    kg_by_type: dict[str, int] = {}
    for cc, ntype, cnt in node_rows:
        c = int(cnt or 0)
        if cc:
            kg_nodes_by_cc[cc] = kg_nodes_by_cc.get(cc, 0) + c
        if ntype:
            kg_by_type[str(ntype)] = kg_by_type.get(str(ntype), 0) + c

    valid_node_ids = select(KgNode.id).where(*active_node_conds)
    edge_rows = (
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
    kg_edges_by_cc = {r[0]: int(r[1]) for r in edge_rows if r[0]}

    # 4. 标准目录（现行有效 + 即将实施）
    std_rows = (
        await session.execute(
            text(
                """
                SELECT country_code, count(*)
                FROM standard_entries
                WHERE status IN ('in_force', 'upcoming')
                GROUP BY country_code
                """
            )
        )
    ).all()
    standards_by_cc = {r[0]: int(r[1]) for r in std_rows if r[0]}

    # 5. 国家合规简报（最新可用版本 + 待更新数与 Home.vue 同源）
    wp_rows = (
        await session.execute(
            text(
                """
                SELECT DISTINCT ON (country_code)
                       country_code, created_at,
                       jsonb_array_length(coalesce(blocks, '[]'::jsonb)), source_hash
                FROM country_whitepapers
                ORDER BY country_code, version_no DESC
                """
            )
        )
    ).all()
    wp_by_cc = {
        r[0]: {
            "ready": int(r[2] or 0) > 0,
            "updated_at": r[1].isoformat() if r[1] else None,
            "source_hash": r[3],
        }
        for r in wp_rows
        if r[0]
    }
    whitepapers_outdated = 0
    if wp_by_cc:
        from app.knowledge.whitepaper_router import _hash_cache

        for wp_cc, wp_meta in wp_by_cc.items():
            if wp_cc in ("CN", "ZZ", "INTL") or not wp_meta["ready"]:
                continue
            cur_hash = _hash_cache.get(wp_cc)
            if cur_hash is not None and cur_hash != wp_meta["source_hash"]:
                whitepapers_outdated += 1

    # 6. 海外属地准入事项（与用户端已就绪展示 shown 有效事项口径完全一致）与官方申报表
    from app.admission.models import AdmissionEntry, AdmissionStep
    from app.admission.router import SHOWN, _basis, amount_conflicts, legal_basis

    cand_entries = list(
        (
            await session.scalars(
                select(AdmissionEntry).where(
                    AdmissionEntry.merged_into.is_(None),
                    AdmissionEntry.review_status.in_(SHOWN),
                )
            )
        ).all()
    )
    adm_entries_by_cc: dict[str, int] = {}
    if cand_entries:
        cand_ids = [e.id for e in cand_entries]
        steps_rows = list(
            (
                await session.scalars(
                    select(AdmissionStep).where(AdmissionStep.entry_id.in_(cand_ids))
                )
            ).all()
        )
        steps_by_entry: dict[Any, list[Any]] = {}
        for st in steps_rows:
            steps_by_entry.setdefault(st.entry_id, []).append(st)
        for e in cand_entries:
            e_steps = steps_by_entry.get(e.id, [])
            if not e_steps:
                continue
            laws, _ = await legal_basis(session, e, await _basis(session, e.evidence_clause_id))
            if not laws:
                continue
            if await amount_conflicts(session, e, e_steps):
                continue
            adm_entries_by_cc[e.country_code] = adm_entries_by_cc.get(e.country_code, 0) + 1

    from app.analysis.documents_router import _CN_OFFICIAL

    forms_by_cc: dict[str, int] = {}
    for f in [*_CN_OFFICIAL, *forms_service.catalog().values()]:
        cc = str(f.get("country") or f.get("country_code") or "").upper()
        if cc:
            forms_by_cc[cc] = forms_by_cc.get(cc, 0) + 1

    # 7. 官方采集站点与待处理状态（仅统计已启用的在用监测站点，确保分子分母同源）
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

    # 8. 企业与平台账号、合规评估项目、海外准入项目、72小时修订监测与审计记录
    trial_pat = f"%{iam_service.TRIAL_ORG_MARKER}%"
    active_orgs = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM organizations "
                    "WHERE is_active = true AND name NOT LIKE :trial_pat"
                ),
                {"trial_pat": trial_pat},
            )
        ).scalar_one()
        or 0
    )
    user_role_rows = (
        await session.execute(
            text(
                """
                SELECT u.role, u.signup_source, count(*)
                FROM users u
                LEFT JOIN organizations o ON o.id = u.org_id
                WHERE u.is_active = true
                  AND u.role IN ('platform_admin', 'ops_admin', 'member', 'viewer')
                  AND (u.org_id IS NULL OR (o.is_active = true AND o.name NOT LIKE :trial_pat))
                GROUP BY u.role, u.signup_source
                """
            ),
            {"trial_pat": trial_pat},
        )
    ).all()
    trial_visitors = int(
        (
            await session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM users u
                    JOIN organizations o ON o.id = u.org_id
                    WHERE u.is_active = true
                      AND o.is_active = true
                      AND o.name LIKE :trial_pat
                    """
                ),
                {"trial_pat": trial_pat},
            )
        ).scalar_one()
        or 0
    )
    role_counts = {"platform_admin": 0, "ops_admin": 0, "member": 0, "viewer": 0}
    portal_users = 0
    for r_name, src, cnt in user_role_rows:
        c = int(cnt or 0)
        if r_name in role_counts:
            role_counts[r_name] += c
        if src == "portal" and r_name in ("member", "viewer"):
            portal_users += c

    doc_task_rows = (
        await session.execute(
            text(
                """
                SELECT coalesce(
                           nullif(t.facts_json->>'doc_source_country', ''),
                           nullif(t.facts_json->>'source_country', ''),
                           t.country_code,
                           ''
                       ) AS src_raw,
                       coalesce(
                           nullif(t.facts_json->>'doc_recipient_country', ''),
                           nullif(t.facts_json->>'recipient_country', ''),
                           ''
                       ) AS dst_raw,
                       t.country_code AS task_cc,
                       count(*)
                FROM analysis_tasks t
                JOIN organizations o ON o.id = t.org_id
                WHERE t.kind = 'documents'
                  AND t.archived_at IS NULL
                  AND o.is_active = true
                  AND o.name NOT LIKE :trial_pat
                GROUP BY 1, 2, 3
                ORDER BY count(*) DESC
                """
            ),
            {"trial_pat": trial_pat},
        )
    ).all()
    routes_map: dict[str, int] = {}
    routes_codes: dict[str, tuple[str, str]] = {}
    projects_by_cc: dict[str, int] = {}
    doc_projects_total = 0

    from app.analysis.document_basis import country_of

    matrix_codes = {
        cc
        for cc in (set(docs_by_cc) | set(standards_by_cc)) - {"ZZ", "INTL", "--", ""}
        if docs_by_cc.get(cc, 0) > 0 or standards_by_cc.get(cc, 0) > 0
    }

    def _norm_country_label(val: str) -> str:
        v = str(val or "").strip()
        if not v:
            return ""
        if len(v) == 2 and v.isascii():
            return country_name_zh(v.upper())
        return v

    for src_raw, dst_raw, task_cc, cnt in doc_task_rows:
        c = int(cnt or 0)
        doc_projects_total += c
        t_cc = str(task_cc or "").upper()
        src_cc_all = country_of(src_raw) or ""
        dst_cc_all = country_of(dst_raw) or ""
        bucket_cc = t_cc if t_cc in matrix_codes else ""
        if not bucket_cc:
            fallback_cc = src_cc_all or dst_cc_all or t_cc
            bucket_cc = (
                src_cc_all
                if src_cc_all in matrix_codes
                else (dst_cc_all if dst_cc_all in matrix_codes else fallback_cc)
            )
        if bucket_cc:
            projects_by_cc[bucket_cc] = projects_by_cc.get(bucket_cc, 0) + c
        s_zh = _norm_country_label(src_raw)
        d_zh = _norm_country_label(dst_raw)
        if s_zh or d_zh:
            label = f"{s_zh} 至 {d_zh}" if s_zh and d_zh and s_zh != d_zh else (s_zh or d_zh)
            routes_map[label] = routes_map.get(label, 0) + c
            if label not in routes_codes and (src_cc_all or dst_cc_all):
                routes_codes[label] = (src_cc_all, dst_cc_all)
    routes_summary = [
        {
            "route_label": k,
            "project_count": v,
            "source_code": routes_codes.get(k, ("", ""))[0],
            "recipient_code": routes_codes.get(k, ("", ""))[1],
        }
        for k, v in sorted(routes_map.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    adm_proj_rows = (
        await session.execute(
            text(
                """
                SELECT e.country_code, count(p.id)
                FROM admission_projects p
                JOIN admission_entries e ON e.id = p.entry_id
                JOIN organizations o ON o.id = p.org_id
                WHERE o.is_active = true AND o.name NOT LIKE :trial_pat
                GROUP BY e.country_code
                """
            ),
            {"trial_pat": trial_pat},
        )
    ).all()
    adm_projects_total = 0
    for cc, cnt in adm_proj_rows:
        c = int(cnt or 0)
        adm_projects_total += c
        if cc:
            projects_by_cc[cc] = projects_by_cc.get(cc, 0) + c

    chat_total = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM analysis_tasks t "
                    "JOIN organizations o ON o.id = t.org_id "
                    "WHERE t.kind IN ('advice', 'chat') "
                    "AND t.archived_at IS NULL "
                    "AND coalesce((t.facts_json->>'_project_side')::boolean, false) = false "
                    "AND coalesce(t.facts_json->>'_viewer_private', '') = '' "
                    "AND coalesce(t.facts_json->>'_doc_project', '') = '' "
                    "AND o.is_active = true "
                    "AND o.name NOT LIKE :trial_pat"
                ),
                {"trial_pat": trial_pat},
            )
        ).scalar_one()
        or 0
    )

    pending_updates = int(
        (
            await session.execute(
                text(
                    """
                    SELECT count(*) FROM (
                        SELECT ui.task_id, ui.change_id
                        FROM update_impacts ui
                        LEFT JOIN organizations o ON o.id = ui.org_id
                        WHERE ui.state = 'open'
                          AND (
                            ui.org_id IS NULL
                            OR (o.is_active = true AND o.name NOT LIKE :trial_pat)
                          )
                        GROUP BY ui.task_id, ui.change_id
                    ) g
                    """
                ),
                {"trial_pat": trial_pat},
            )
        ).scalar_one()
        or 0
    )
    quality_unpassed = 0
    try:
        from app.ingestion.quality_card import cards_in

        q_cards = await cards_in(session)
        quality_unpassed = sum(
            1 for r in q_cards if not (r["card"].passed and not r.get("todo"))
        )
    except Exception:  # noqa: BLE001
        quality_unpassed = 0

    audit_7d = int(
        (
            await session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM audit_logs a
                    LEFT JOIN organizations o ON o.id = a.org_id
                    WHERE a.at >= :since
                      AND (a.org_id IS NULL OR (o.is_active = true AND o.name NOT LIKE :trial_pat))
                      AND coalesce(a.actor_email, '') NOT LIKE 'bench_%'
                      AND coalesce(a.actor_email, '') NOT LIKE 'loadtest_%'
                    """
                ),
                {"since": seven_days_ago, "trial_pat": trial_pat},
            )
        ).scalar_one()
        or 0
    )
    opinion_total = int(
        (await session.execute(text("SELECT count(*) FROM opinion_items"))).scalar_one() or 0
    )

    # 9. 组装主权国家与法域矩阵（剔除国际通用标准伪国别 ZZ/INTL 与无范围内法规的空壳代码）
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
        (
            c
            for c in candidate_codes
            if docs_by_cc.get(c, 0) > 0 or standards_by_cc.get(c, 0) > 0
        ),
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
                    100.0
                    if is_zh_native and clauses_by_cc.get(cc, 0) > 0
                    else _safe_pct(f_trans, f_total)
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
                "enterprise_projects_count": projects_by_cc.get(cc, 0),
            }
        )

    total_foreign = sum(foreign_clauses_by_cc.values())
    total_translated = sum(translated_by_cc.values())
    intl_standards_count = sum(
        v for k, v in standards_by_cc.items() if k in ("ZZ", "INTL")
    )
    national_standards_count = sum(
        v for k, v in standards_by_cc.items() if k not in ("ZZ", "INTL")
    )

    covered_count = len([c for c in countries if c["laws_count"] > 0 or c["standards_count"] > 0])
    return {
        "generated_at": now.isoformat(),
        "headline": {
            "covered_jurisdictions": covered_count,
            "laws_in_force": sum(docs_by_cc.values()),
            "laws_total": laws_total,
            "laws_archived_total": laws_archived_total,
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
            "active_organizations": active_orgs,
            "orgs_total": active_orgs,
            "doc_projects_total": doc_projects_total,
            "admission_projects_total": adm_projects_total,
            "consulting_chats_total": chat_total,
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
            "super_admins": role_counts["platform_admin"],
            "ops_admins": role_counts["ops_admin"],
            "enterprise_members": role_counts["member"],
            "ordinary_viewers": role_counts["viewer"],
            "portal_onboarded": portal_users,
            "portal_viewers": portal_users,
            "trial_visitors": trial_visitors,
        },
        "operations": {
            "monitor_sites_total": len(monitor_sites),
            "attention_sites_total": attention_sites_total,
            "pending_updates": pending_updates,
            "quality_unpassed": quality_unpassed,
            "whitepapers_outdated": whitepapers_outdated,
            "audit_events_7d": audit_7d,
        },
        "routes": routes_summary,
        "countries": countries,
    }
