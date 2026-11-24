# ruff: noqa: E501
"""Keep the user side fast and in step with the library.

The user side keeps a few views that are expensive to build (country list, country page,
compliance topics, relation counts of the regulations, the basis clauses of the compliance
documents). They never expire by time — a time limit only means the first user after it waits
several seconds. Instead:

- a keeper task, started with the app, looks every minute at a cheap fingerprint of the library
  and the graph; when it has changed (a crawl, a batch extraction, a translation run) it rebuilds
  the views in the background;
- an administrator's change through the admin API (an approved correction, a graph edit, an
  admission item set out of scope) starts the same rebuild at once;
- while a rebuild runs, users keep getting the previous views, so nobody waits; each view is
  replaced as soon as its new version is ready.

``rebuilding`` is set inside a rebuild so the cached getters recompute instead of returning
what they have.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging

log = logging.getLogger(__name__)
rebuilding: contextvars.ContextVar[bool] = contextvars.ContextVar("rebuilding", default=False)
_task: asyncio.Task | None = None
_last_mark: str | None = None
_pending = False
keeper_active = False  # 服务运行中由后台维护视图；测试和命令行里没有它，按需当场计算
KEEPER_SECONDS = 60


def use_cached(hit) -> bool:
    """Whether a getter may return its cached value (when keeper runs, except while rebuilding)."""
    return bool(hit) and keeper_active and not rebuilding.get()


def cockpit_router_warmed() -> bool:
    from app.iam import cockpit_router

    return bool(cockpit_router._KB_POSTURE_CACHE)


def clear_user_caches() -> None:
    """Drop every cached view (tests start from a clean state with this)."""
    from app.admission import router as adm_router
    from app.analysis import (
        benchmarks,
        clause_gate,
        doc_applicability,
        doc_projects,
        doc_rules,
        doc_sections,
        document_templates,
        documents_router,
        evaluation,
        question_basis,
    )
    from app.iam import cockpit_router
    from app.ingestion.quality_card import clear_cards_cache
    from app.knowledge import library_view, router, whitepaper, whitepaper_graph, whitepaper_router

    clear_cards_cache()
    cockpit_router._KB_POSTURE_CACHE.clear()
    benchmarks._BENCH_CACHE.clear()
    evaluation._LATEST_EVAL_CACHE.clear()
    clause_gate.clear_title_names()
    doc_applicability._SOURCE_GROUNDS_CACHE.clear()
    whitepaper_router._page_cache.clear()
    whitepaper_router._hash_cache.clear()
    whitepaper._PINNED_TOPICS_CACHE.clear()
    whitepaper_graph._MENTIONS.clear()
    whitepaper_graph._CLAUSES.clear()
    whitepaper_graph._PRIMARY_CLAUSE.clear()
    whitepaper_graph._KEYS.clear()
    router._TOPICS_CACHE.clear()
    router._OVERVIEW_CACHE.clear()
    router._GRAPH_JSON_CACHE.clear()
    library_view._RELATIONS_CACHE.clear()
    documents_router._BASIS_CACHE.clear()
    documents_router._COUNTRY_BASIS.clear()
    doc_rules._LATEST_CLAUSES_CACHE.clear()
    doc_rules._TRANSFER_CLAUSE_CACHE.clear()
    doc_rules._PINNED_CACHE.clear()
    doc_rules.ADEQUACY_SEEN.clear()
    doc_sections._RECEIVING_LAW_CACHE.clear()
    question_basis._CN_ARTICLES_CACHE.clear()
    adm_router._BASIS_MEMO.clear()
    adm_router._LISTED_MEMO.clear()
    adm_router._DETAIL_MEMO.clear()
    doc_projects._PROGRESS_CACHE.clear()
    doc_projects._OVERVIEW_PROJECT_CACHE.clear()
    doc_projects._COMPLIANCE_OVERVIEW_CACHE.clear()
    document_templates._DEFAULT_TEMPLATES_ENSURED = False


async def mark(session) -> str:
    """Fingerprint of what the views are built from: documents and their scope fields, versions and
    their status, current translations, graph nodes (review state and Chinese name) and edges."""
    from sqlalchemy import text

    row = (
        await session.execute(
            text(
                "select (select md5(coalesce(string_agg(id::text || relevance || coalesce(business_weight, ''), ',' order by id), '')) from documents),"
                " (select count(*)::text || md5(coalesce(string_agg(legal_status || state, ',' order by id), '')) from document_versions),"
                " (select count(*)::text || coalesce(max(updated_at)::text, '') from clause_translations where is_current),"
                " (select count(*)::text || md5(coalesce(string_agg(review_status || coalesce(verify_state, '') || name_zh, ',' order by id), '')) from kg_nodes),"
                " (select count(*)::text || md5(coalesce(string_agg(review_status || coalesce(verify_state, ''), ',' order by id), '')) from kg_edges),"
                " (select count(*)::text || md5(coalesce(string_agg(review_status || coalesce(merged_into::text, ''), ',' order by id), '')) from admission_entries),"
                " (select count(*)::text || md5(coalesce(string_agg(status || coalesce(target_type, ''), ',' order by id), '')) from knowledge_change_proposals),"
                " (select count(*)::text || md5(coalesce(string_agg(status, ',' order by id), '')) from standard_entries),"
                " (select count(*)::text || coalesce(max(version_no)::text, '') from country_whitepapers)"
            )
        )
    ).one()
    return "|".join(str(x) for x in row)


def _rebuild_in_thread() -> str:
    """The rebuild itself, in a worker thread with its own event loop and database connections:
    building the views is heavy on the CPU (ranking thousands of clauses), and in the server's own
    loop it would hold up every user request while it runs."""

    async def run() -> str:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.analysis.documents_router import warm_basis
        from app.core.config import get_settings
        from app.core.rls import system_scope

        engine = create_async_engine(get_settings().database_url, pool_pre_ping=True, pool_size=2, max_overflow=0)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            with system_scope("view-rebuild"):
                async with factory() as session:
                    current = await mark(session)
            token = rebuilding.set(True)
            try:
                # 先在启动 3 秒内建好驾驶舱首屏缓存（此时 known_hash 不阻塞），随后后台算完简报指纹再刷新一次
                if not cockpit_router_warmed():
                    with system_scope("view-rebuild-cockpit-fast"):
                        async with factory() as session:
                            from app.iam import cockpit_router

                            rebuilding.reset(token)
                            try:
                                await cockpit_router._kb_posture(session)
                            finally:
                                token = rebuilding.set(True)
                            session.expunge_all()
                await warm_basis(factory)
                with system_scope("view-rebuild-admin"):
                    async with factory() as session:
                        from sqlalchemy import text as sa_text

                        from app.analysis.benchmarks import run_retrieval_benchmarks
                        from app.analysis.evaluation import latest_evaluation
                        from app.iam import cockpit_router
                        from app.ingestion.ref_check import scan
                        from app.knowledge import router as k_router

                        await run_retrieval_benchmarks(session)
                        session.expunge_all()
                        await latest_evaluation(session)
                        session.expunge_all()
                        await scan(session)
                        session.expunge_all()

                        hot_kg_rows = (
                            await session.execute(
                                sa_text(
                                    "select distinct country_code from analysis_tasks "
                                    "where archived_at is null and country_code is not null limit 8"
                                )
                            )
                        ).scalars().all()
                        for _cc in sorted({*(c.upper() for c in hot_kg_rows if c), "CN"})[:8]:
                            _gp = await k_router._build_graph_payload(session, _cc, only_approved=False)
                            while len(k_router._GRAPH_JSON_CACHE) >= 16 and (_cc, False) not in k_router._GRAPH_JSON_CACHE:
                                k_router._GRAPH_JSON_CACHE.pop(next(iter(k_router._GRAPH_JSON_CACHE)), None)
                            k_router._GRAPH_JSON_CACHE[(_cc, False)] = _gp.model_dump_json()
                            session.expunge_all()
                        await cockpit_router._kb_posture(session)
                        session.expunge_all()
            finally:
                rebuilding.reset(token)
            return current
        finally:
            await engine.dispose()
            import gc

            from app.core.security import trim_native_heap

            gc.collect()
            trim_native_heap()

    return asyncio.run(run())


async def _rebuild() -> None:
    global _last_mark, _pending
    while True:
        _pending = False
        try:
            _last_mark = await asyncio.to_thread(_rebuild_in_thread)
        except Exception:  # noqa: BLE001
            log.warning("后台视图重建失败", exc_info=True)
            return
        if not _pending:  # 重建期间又有改动：再来一遍
            return


def schedule_rebuild() -> None:
    """Rebuild the views in the background (one at a time; a change during a rebuild queues another)."""
    global _task, _pending
    if not keeper_active:
        return
    if _task is not None and not _task.done():
        _pending = True
        return
    _task = asyncio.get_running_loop().create_task(_rebuild())


async def keeper() -> None:
    """Build the views at start-up, then rebuild them whenever the library or the graph changes."""
    global _last_mark, keeper_active
    from app.core.db import get_sessionmaker
    from app.core.rls import system_scope

    keeper_active = True
    schedule_rebuild()
    while True:
        await asyncio.sleep(KEEPER_SECONDS)
        try:
            with system_scope("view-keeper"):
                async with get_sessionmaker()() as session:
                    current = await mark(session)
            if _last_mark is not None and current != _last_mark:
                schedule_rebuild()
        except Exception:  # noqa: BLE001 - 检查失败不影响使用，下一分钟再看
            log.warning("视图检查失败", exc_info=True)


def changes_admin_data(method: str, path: str, status: int) -> bool:
    return method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/admin/") and status < 400


def after_admin_change() -> None:
    from app.admission import router as adm_router
    from app.analysis import doc_applicability, doc_projects
    from app.iam import cockpit_router
    from app.ingestion.quality_card import clear_cards_cache
    from app.knowledge import router, whitepaper_graph

    clear_cards_cache()
    cockpit_router._KB_POSTURE_CACHE.clear()
    doc_projects._COMPLIANCE_OVERVIEW_CACHE.clear()
    doc_applicability._SOURCE_GROUNDS_CACHE.clear()
    whitepaper_graph._MENTIONS.clear()
    whitepaper_graph._CLAUSES.clear()
    whitepaper_graph._PRIMARY_CLAUSE.clear()
    whitepaper_graph._KEYS.clear()
    router._GRAPH_JSON_CACHE.clear()
    adm_router._BASIS_MEMO.clear()
    adm_router._LISTED_MEMO.clear()
    adm_router._DETAIL_MEMO.clear()
    schedule_rebuild()
