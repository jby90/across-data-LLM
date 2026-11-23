# ruff: noqa: E501
"""命令行工具。

用法:
  python -m app.cli create-platform-admin --email admin@example.com --name 管理员
  python -m app.cli load-sites ../data/seed/source_sites.json   # 导入种子站点(停用、条款待确认)
  python -m app.cli preflight [--country EG] [--report 报告.md]  # 只读预检,不下载文件
  python -m app.cli translate [--country SG] [--only-missing] [--concurrency 4]

密码从环境变量 CYSH_ADMIN_PASSWORD 读取,未设置则交互输入(不回显)。
"""

import argparse
import asyncio
import getpass
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app import models_registry  # noqa: F401  确保所有模型已注册
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.iam import service
from app.iam.models import Role


def _ensure_dev_env() -> None:
    """当未显式传入 CYSH_DATABASE_URL / CYSH_JWT_SECRET 时,自动复用 ~/.cyshuhe-dev 本地开发配置。"""
    dev_dir = Path.home() / ".cyshuhe-dev"
    creds_path = dev_dir / "credentials.json"
    if "CYSH_JWT_SECRET" not in os.environ and creds_path.exists():
        try:
            creds = json.loads(creds_path.read_text(encoding="utf-8"))
            if creds.get("jwt_secret"):
                os.environ["CYSH_JWT_SECRET"] = creds["jwt_secret"]
        except Exception:
            pass
    if "CYSH_JWT_SECRET" not in os.environ:
        os.environ["CYSH_JWT_SECRET"] = "dev-cli-fallback-jwt-secret-at-least-32-chars"

    if "CYSH_DATABASE_URL" not in os.environ and (dev_dir / "pg").exists():
        try:
            import pgserver

            pg = pgserver.get_server(dev_dir / "pg", cleanup_mode=None)
            os.environ["CYSH_DATABASE_URL"] = pg.get_uri().replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
        except Exception:
            pass
    get_settings.cache_clear()


async def _create_platform_admin(email: str, name: str, password: str) -> None:
    async with get_sessionmaker()() as session:
        user = await service.create_user(
            session,
            email=email,
            display_name=name,
            password=password,
            role=Role.PLATFORM_ADMIN,
            org_id=None,
        )
        print(f"已创建平台管理员: {user.email}")


async def load_sites(path: Path, sf=None) -> tuple[int, int]:
    """导入种子站点。已存在的(同国家同入口地址)跳过。返回 (新增数, 跳过数)。

    种子只承载"去哪里取",不承载"可以取":导入后一律是停用、条款待确认,
    必须由人在管理端确认条款后才能启用。
    """
    from sqlalchemy import select

    from app.sources.models import SourceSite
    from app.sources.schemas import SiteCreate

    items = json.loads(path.read_text(encoding="utf-8"))
    created = skipped = 0
    async with (sf or get_sessionmaker())() as session:
        for raw in items:
            body = SiteCreate(
                country_code=raw["country_code"],
                publisher=raw["publisher"],
                language=raw["language"],
                document_type=raw["document_type"],
                entry_url=raw["entry_url"],
                strategy=raw.get("strategy", "DIRECT"),
                config=raw.get("config", {}),
                interval_hours=raw.get("interval_hours", 168),
                terms_url=raw.get("terms_url"),
            )
            exists = (
                await session.execute(
                    select(SourceSite.id).where(
                        SourceSite.country_code == body.country_code,
                        SourceSite.entry_url == body.entry_url,
                    )
                )
            ).first()
            if exists:
                skipped += 1
                continue
            session.add(
                SourceSite(
                    country_code=body.country_code,
                    publisher=body.publisher,
                    language=body.language,
                    document_type=body.document_type,
                    entry_url=body.entry_url,
                    strategy=body.strategy.value,
                    config=body.config.model_dump(),
                    interval_hours=body.interval_hours,
                    terms_url=body.terms_url,
                    terms_note=raw.get("note"),
                )
            )
            created += 1
        await session.commit()
    return created, skipped


async def preflight(country: str | None, sf=None) -> list[dict[str, str]]:
    """对库里的站点做只读预检(只访问各源站的 robots.txt,不下载任何文件)。"""
    from sqlalchemy import select

    from app.ingestion.fetcher import PoliteFetcher
    from app.sources.models import SourceSite

    settings = get_settings()
    async with (sf or get_sessionmaker())() as session:
        stmt = select(SourceSite).order_by(SourceSite.country_code, SourceSite.publisher)
        if country:
            stmt = stmt.where(SourceSite.country_code == country.upper())
        sites = (await session.execute(stmt)).scalars().all()

    rows: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=settings.crawl_timeout_seconds) as client:
        fetcher = PoliteFetcher(
            client,
            user_agent=settings.crawl_user_agent,
            delay_seconds=settings.crawl_delay_seconds,
            max_bytes=settings.crawl_max_bytes,
            allow_resolved_cidrs=settings.crawl_allow_resolved_cidrs,
        )
        for site in sites:
            res = await fetcher.preflight(site.entry_url)
            res["country"] = site.country_code
            res["publisher"] = site.publisher
            res["terms"] = site.terms_status
            rows.append(res)
    return rows


async def run_import_phase1(data_dir: str, country: str) -> None:
    from app.ingestion.archive import ArchiveStore
    from app.ingestion.phase1_import import import_phase1

    settings = get_settings()
    report = await import_phase1(
        Path(data_dir),
        country,
        get_sessionmaker(),
        ArchiveStore(settings.archive_dir),
        _ocr_engine(),
    )
    print(
        f"新增站点 {report.sites_new},新增文档 {report.documents_new},"
        f"新增版本 {report.versions_new},"
        f"已存在 {report.already_present}。解析结果: {dict(report.states)}"
    )
    if report.no_version:
        print(f"一期登记但没有原文的来源 {len(report.no_version)} 个(未导入)。")
    if report.missing_files:
        print(f"原文件缺失 {len(report.missing_files)} 个: {report.missing_files[:5]}")
    if report.hash_mismatch:
        print(f"哈希不一致已拒绝 {len(report.hash_mismatch)} 个: {report.hash_mismatch[:5]}")


def _ocr_engine():
    from app.ingestion.ocr import OcrEngine

    s = get_settings()
    return OcrEngine(s.tesseract_cmd, s.tessdata_dir, s.ocr_dpi, s.paddle_python)


async def _split_oversized_clauses_for_country(
    session, country_code: str | None = None, max_clause_chars: int = 2400
) -> int:
    """Compatibility entry point: translation chunks must never modify source clauses.

    Chunking happens in translator.split_clause_into_chunks in memory only.
    Historical repairs require separate reviewed parse revisions.
    """
    return 0


async def batch_translate(
    country: str | None = None,
    only_missing: bool = True,
    concurrency: int = 4,
    sf=None,
    dry_run: bool = False,
    limit: int | None = None,
    max_attempts: int = 3,
) -> dict[str, int]:
    """Translate every latest parsed clause; explicit limits are samples, never full coverage.

    Dry runs do not write. Successful attempts commit independently, allowing safe resume.
    All source/evidence rows and previous translation attempt contents remain untouched.
    """
    from sqlalchemy import func, or_, select

    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.ingestion.safety import document_available
    from app.knowledge.models import ClauseTranslation, GlossaryTerm
    from app.knowledge.translator import (
        set_translation_concurrency,
        translate_clause,
        verify_translation,
    )

    set_translation_concurrency(concurrency)
    sf = sf or get_sessionmaker()
    async with sf() as session:
        if country:
            target_countries = [country.upper()]
        else:
            db_codes = {
                c.upper()
                for c in (
                    await session.scalars(
                        select(Document.country_code)
                        .where(Document.relevance.notin_(("out_of_scope", "irrelevant")))
                        .distinct()
                    )
                ).all()
                if c and c.upper() not in ("CN", "ZZ", "--")
            }
            pilot = ["SG", "EG", "TN", "MA", "LB"]
            target_countries = [*pilot, *sorted(db_codes - set(pilot))]
        latest = (
            select(
                DocumentVersion.document_id,
                func.max(DocumentVersion.version_no).label("version_no"),
            )
            .where(DocumentVersion.state == "parsed")
            .group_by(DocumentVersion.document_id)
            .subquery()
        )
        rows = (
            await session.execute(
                select(Clause, Document.country_code)
                .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .join(
                    latest,
                    (latest.c.document_id == Document.id)
                    & (latest.c.version_no == DocumentVersion.version_no),
                )
                .where(Document.country_code.in_(target_countries))
                .where(document_available())
                # 与三类业务无关的文档不译：不进检索、报告和数量，翻了只是白耗额度
                .where(Document.relevance.notin_(("out_of_scope", "irrelevant")))
                .order_by(Document.country_code, Document.id, Clause.ordinal, Clause.id)
            )
        ).all()
        current = (
            await session.scalars(
                select(ClauseTranslation).where(
                    ClauseTranslation.is_current.is_(True),
                    ClauseTranslation.review_status.in_(
                        ("machine_translated", "pending_review", "approved")
                    ),
                )
            )
        ).all()
        qualified = set()
        clauses_by_id = {clause.id: clause for clause, _ in rows}
        for tr in current:
            clause = clauses_by_id.get(tr.clause_id)
            if clause and (
                tr.review_status == "approved"
                or (
                    tr.engine == "llm"
                    and tr.quality_check_passed
                    and verify_translation(
                        clause.original_text, tr.translated_text, tr.glossary_hits or []
                    )[0]
                )
            ):
                qualified.add(tr.clause_id)
        from app.knowledge.numeric_integrity import is_numeric_only

        not_required = {c.id for c, _ in rows if is_numeric_only(c.original_text)}
        # 按业务相关章节入库的文件：范围外的条文不译（原文照常显示，已有的译文保留）
        from app.ingestion.intake_scope import out_of_scope, scopes_for

        version_doc = dict(
            (
                await session.execute(
                    select(DocumentVersion.id, DocumentVersion.document_id).where(
                        DocumentVersion.id.in_({c.version_id for c, _ in rows})
                    )
                )
            ).all()
        )
        scopes = await scopes_for(session, set(version_doc.values()))
        outside = {
            c.id
            for c, _ in rows
            if (sc := scopes.get(version_doc.get(c.version_id))) and out_of_scope(sc, c.version_id, c.ordinal)
        }
        not_required |= outside
        pending = [
            (clause.id, cc)
            for clause, cc in rows
            if clause.id not in not_required and (not only_missing or clause.id not in qualified)
        ]
        exhausted = 0
        if only_missing and max_attempts > 0 and pending:
            # 同一版提示词下已失败达到上限的条款不再自动重译：反复请求同一个模型多半得到同一个结果，
            # 只会白耗额度。它们在质量卡里列出原因，修了核验规则或换了提示词版本后自然重新进入
            from app.knowledge.translator import PROMPT_VERSION

            failed_counts = dict(
                (
                    await session.execute(
                        select(ClauseTranslation.clause_id, func.count())
                        .where(
                            ClauseTranslation.clause_id.in_([cid for cid, _ in pending]),
                            ClauseTranslation.engine == "llm",
                            ClauseTranslation.quality_check_passed.is_(False),
                            ClauseTranslation.prompt_version.like(PROMPT_VERSION + "%"),
                        )
                        .group_by(ClauseTranslation.clause_id)
                    )
                ).all()
            )
            kept = [(cid, cc) for cid, cc in pending if failed_counts.get(cid, 0) < max_attempts]
            exhausted = len(pending) - len(kept)
            pending = kept
        total_missing = len(pending)
        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be positive")
            pending = pending[:limit]
        terms = (
            await session.scalars(
                select(GlossaryTerm).where(
                    or_(
                        GlossaryTerm.country_code.in_(target_countries),
                        GlossaryTerm.country_code.is_(None),
                    )
                )
            )
        ).all()
    stats = dict(
        total_eligible=len(rows) - len(not_required),
        not_required=len(not_required),
        total_missing=total_missing,
        total=len(pending),
        skipped_qualified=len(rows) - len(not_required) - total_missing - exhausted,
        retry_exhausted=exhausted,
        limited=int(limit is not None),
        machine_translated=0,
        translation_failed=0,
        not_translated=0,
        errors=0,
        dry_run=int(dry_run),
        superseded=0,
        tokens_in=0,
        tokens_out=0,
    )
    print("[translate] " + json.dumps(stats, ensure_ascii=False), flush=True)
    if dry_run:
        return stats
    sem = asyncio.Semaphore(max(1, concurrency))
    completed = 0

    async def worker(clause_id, cc):
        nonlocal completed
        async with sem:
            async with sf() as session:
                try:
                    clause = await session.get(Clause, clause_id)
                    if clause is None:
                        stats["errors"] += 1
                        return
                    tr = await translate_clause(session, clause, cc, terms)
                    await session.commit()
                    stats[tr.review_status] = stats.get(tr.review_status, 0) + 1
                    if tr.review_status != "approved":
                        stats["tokens_in"] += tr.tokens_in
                        stats["tokens_out"] += tr.tokens_out
                    completed += 1
                    if completed % 20 == 0 or completed == len(pending):
                        print(
                            f"[translate] {completed}/{len(pending)} "
                            + json.dumps(stats, ensure_ascii=False),
                            flush=True,
                        )
                except Exception:
                    await session.rollback()
                    stats["errors"] += 1
                    # Never print request bodies, credentials, or untrusted exception payloads.
                    print(
                        f"[translate] clause {clause_id}: processing failed; safe to retry",
                        flush=True,
                    )

    await asyncio.gather(*(worker(clause_id, cc) for clause_id, cc in pending))
    return stats


async def batch_extract_kg(
    country: str | None = None,
    max_clauses_per_doc: int | None = None,
    concurrency: int = 8,
    sf=None,
) -> dict[str, dict[str, int]]:
    """批量执行 S2 大模型知识图谱抽取(相关性过滤 + 条款抽取 + 类型约束 + 逐字校验 + 语义裁判)。"""
    from app.knowledge.extractor import COUNTRY_NAMES, extract_knowledge_for_country

    sf = sf or get_sessionmaker()
    if country:
        countries = [country.upper()]
    else:
        from sqlalchemy import select

        from app.ingestion.models import Document

        async with sf() as session:
            db_codes = {
                c.upper()
                for c in (
                    await session.scalars(
                        select(Document.country_code)
                        .where(Document.relevance.notin_(("out_of_scope", "irrelevant")))
                        .distinct()
                    )
                ).all()
                if c and c.upper() not in ("CN", "ZZ", "--")
            }
        pilot = list(COUNTRY_NAMES.keys())
        countries = [*pilot, *sorted(db_codes - set(pilot))]
    results: dict[str, dict[str, int]] = {}
    for cc in countries:
        print(f"[S2-KG] 正在抽取国家 {cc} ...", flush=True)
        async with sf() as session:
            res = await extract_knowledge_for_country(
                session,
                cc,
                translate_sample_per_doc=0,
                max_clauses_per_doc=max_clauses_per_doc,
                concurrency=concurrency,
            )
            results[cc] = res
            print(f"[S2-KG] {cc} 完成: {json.dumps(res, ensure_ascii=False)}", flush=True)
        if res.get("status") != "failed":
            # 新抽出的数据类别归入车辆数据种类（国家简报按种类展示；只处理还没有归类的）
            from app.knowledge.data_classes import classify

            async with sf() as session:
                print(f"[S2-KG] {cc} 数据类别归类: {await classify(session, cc)}", flush=True)
            from app.knowledge.claim_tags import tag

            async with sf() as session:
                print(f"[S2-KG] {cc} 要求所属方面: {await tag(session, cc)}", flush=True)
            from app.knowledge.regulator_names import translate as name_regulators

            async with sf() as session:
                print(f"[S2-KG] {cc} 监管机构中文名: {await name_regulators(session, cc)}", flush=True)
    return results


async def reparse(state: str, country: str | None, sf=None) -> dict[str, int]:
    """对已有版本重新解析(例如装好 OCR 后,处理之前标为"需要 OCR"的扫描件)。"""
    from sqlalchemy import select

    from app.ingestion.archive import ArchiveStore
    from app.ingestion.models import Document, DocumentVersion
    from app.ingestion.pipeline import parse_version

    settings = get_settings()
    archive = ArchiveStore(settings.archive_dir)
    ocr = _ocr_engine()
    sf = sf or get_sessionmaker()
    counts: dict[str, int] = {}
    async with sf() as s:
        stmt = (
            select(DocumentVersion, Document)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.state == state)
        )
        if country:
            stmt = stmt.where(Document.country_code == country.upper())
        for version, doc in (await s.execute(stmt)).all():
            await parse_version(s, archive, version, doc.language, ocr)
            await s.commit()
            counts[version.state] = counts.get(version.state, 0) + 1
    return counts


async def upgrade_ocr(
    country: str | None,
    sf=None,
    accept_equal: bool = False,
    max_pages: int | None = None,
) -> list[dict]:
    """用当前 OCR 引擎重新识别库内的扫描件，新结果更好才替换（见 pipeline.upgrade_ocr_version）。"""
    from sqlalchemy import select

    from app.ingestion.archive import ArchiveStore
    from app.ingestion.models import Document, DocumentVersion
    from app.ingestion.pipeline import upgrade_ocr_version

    settings = get_settings()
    archive = ArchiveStore(settings.archive_dir)
    ocr = _ocr_engine()
    sf = sf or get_sessionmaker()
    out: list[dict] = []
    async with sf() as s:
        stmt = (
            select(DocumentVersion, Document)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.text_source == "ocr")
        )
        if country:
            stmt = stmt.where(Document.country_code == country.upper())
        if max_pages:
            stmt = stmt.where(DocumentVersion.page_count <= max_pages)
        for version, doc in (await s.execute(stmt)).all():
            res = await upgrade_ocr_version(
                s, archive, version, doc.language, ocr, accept_equal=accept_equal
            )
            await s.commit()
            out.append({"title": doc.title[:60], "pages": version.page_count, **res})
    return out


def render_report(rows: list[dict[str, str]]) -> str:
    lines = [
        "# 来源预检报告(只读)",
        "",
        f"生成时间:{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}。",
        "只访问了各源站的 `robots.txt`,**没有下载任何法规文件**。",
        "「robots 允许」只说明技术上不被禁止,**不等于**条款允许商用或再分发,仍需法务确认。",
        "",
        "| 国家 | 发布机构 | 地址 | 地址检查 | robots | 说明 | 条款状态 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['country']} | {r['publisher']} | {r['url']} | {r['target']} | "
            f"{r['robots']} | {r['robots_detail']} | {r['terms']} |"
        )
    return "\n".join(lines) + "\n"


async def build_readings(version_id: str, ocr_json: str, sf=None) -> dict[str, int]:
    """For one version, store a cross-checked reading for every clause that two routes settle."""
    from sqlalchemy import select

    from app.ingestion.dual_route import reading_for_clause
    from app.ingestion.models import Clause, ClauseReading

    pages = json.loads(Path(ocr_json).read_text(encoding="utf-8"))
    sf = sf or get_sessionmaker()
    stats = dict(clauses=0, stored=0, unsettled=0, untouched=0)
    async with sf() as session:
        clauses = (
            await session.scalars(
                select(Clause)
                .where(Clause.version_id == uuid.UUID(version_id))
                .order_by(Clause.ordinal)
            )
        ).all()
        for clause in clauses:
            ocr_text = pages.get(str(clause.page_start))
            if not ocr_text:
                continue
            stats["clauses"] += 1
            found = reading_for_clause(clause.original_text, ocr_text)
            if found is None:
                # either nothing was damaged, or the routes do not settle it: keep the clause as is
                stats["unsettled"] += 1
                continue
            text, recon = found
            existing = await session.scalar(
                select(ClauseReading).where(ClauseReading.clause_id == clause.id)
            )
            if existing is None:
                session.add(
                    ClauseReading(
                        clause_id=clause.id,
                        reading_text=text,
                        confirmed_words=recon.confirmed,
                        unresolved_spans=0,
                    )
                )
            else:
                existing.reading_text = text
            stats["stored"] += 1
        await session.commit()
    return stats


async def reparse_version(
    version_id: str, ocr_json: str, *, dry_run: bool = False, allow_shrink: bool = False, sf=None
):
    from app.ingestion.models import DocumentVersion
    from app.ingestion.reparse import reparse_from_pages

    raw = json.loads(Path(ocr_json).read_text(encoding="utf-8"))
    pages = [raw[k] for k in sorted(raw, key=int)]
    sf = sf or get_sessionmaker()
    async with sf() as session:
        version = await session.get(DocumentVersion, uuid.UUID(version_id))
        from app.ingestion.models import Document

        document = await session.get(Document, version.document_id)
        result = await reparse_from_pages(
            session,
            version.id,
            pages,
            document.language,
            note="页面图像重新切分（原文字层阅读顺序错乱）",
            allow_shrink=allow_shrink,
        )
        if dry_run:
            await session.rollback()
            result["dry_run"] = True
        else:
            await session.commit()
        return result


async def run_collect_opinion_urls(urls: list[str]) -> dict:
    from app.ingestion.fetcher import PoliteFetcher
    from app.opinion.collect import collect_urls

    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.crawl_timeout_seconds) as client:
        fetcher = PoliteFetcher(
            client,
            user_agent=settings.crawl_user_agent,
            delay_seconds=settings.crawl_delay_seconds,
            max_bytes=settings.crawl_max_bytes,
            allow_resolved_cidrs=settings.crawl_allow_resolved_cidrs,
        )
        st = await collect_urls(get_sessionmaker(), fetcher, urls)
    return {k: getattr(st, k) for k in ("fetched", "kept", "already", "rejected", "errors")}


async def run_collect_opinion(per_page: int = 40) -> dict:
    from sqlalchemy import select

    from app.ingestion.fetcher import PoliteFetcher
    from app.opinion.collect import collect_source, ensure_sources
    from app.opinion.models import OpinionSource

    settings = get_settings()
    sf = get_sessionmaker()
    async with sf() as session:
        await ensure_sources(session)
        sources = list(
            (await session.scalars(select(OpinionSource).where(OpinionSource.enabled))).all()
        )
    totals = {
        "sources": len(sources),
        "kept": 0,
        "already": 0,
        "rejected": 0,
        "errors": 0,
        "failed": [],
    }
    async with httpx.AsyncClient(timeout=settings.crawl_timeout_seconds) as client:
        fetcher = PoliteFetcher(
            client,
            user_agent=settings.crawl_user_agent,
            delay_seconds=settings.crawl_delay_seconds,
            max_bytes=settings.crawl_max_bytes,
            allow_resolved_cidrs=settings.crawl_allow_resolved_cidrs,
        )
        for source in sources:
            st = await collect_source(sf, fetcher, source, per_page=per_page)
            for k in ("kept", "already", "rejected", "errors"):
                totals[k] += getattr(st, k)
            if st.pages == 0:
                totals["failed"].append(source.entry_url)
            print(
                f"  {source.publisher:<12} 新增 {st.kept:>3} 已有 {st.already:>3} "
                f"不在主题 {st.rejected:>3}",
                flush=True,
            )
    return totals


async def run_collect_standards() -> dict:
    from app.ingestion.fetcher import PoliteFetcher
    from app.standards.collect import collect, collect_industry

    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.crawl_timeout_seconds) as client:
        fetcher = PoliteFetcher(
            client,
            user_agent=settings.crawl_user_agent,
            delay_seconds=settings.crawl_delay_seconds,
            max_bytes=settings.crawl_max_bytes,
            allow_resolved_cidrs=settings.crawl_allow_resolved_cidrs,
        )
        national = await collect(get_sessionmaker(), fetcher)
        industry = await collect_industry(get_sessionmaker(), fetcher)
        return {"national": national, "industry": industry}


async def apply_pages(index_path: str, result_path: str, second_path: str | None = None) -> dict:
    """Write better page readings back, grouped by version; one commit per version.

    ``second_path`` holds a second, independent engine's reading per page ({page: text}). A page
    both engines read the same way is taken as read right; a page the first reading cannot give
    (empty or invented) falls back to the second reading."""
    from app.core import egress_models  # noqa: F401
    from app.core.rls import system_scope
    from app.ingestion.archive import ArchiveStore
    from app.ingestion.models import Document, DocumentVersion
    from app.ingestion.reparse import apply_page_replacements

    index = json.loads(Path(index_path).read_text(encoding="utf-8"))
    result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    second = json.loads(Path(second_path).read_text(encoding="utf-8")) if second_path else {}
    from app.ingestion.page_replace import engines_agree, looks_invented, truncate_loop

    by_version: dict[str, dict[int, str]] = {}
    agreed: dict[str, set[int]] = {}
    for key, meta in index.items():
        page, other = int(meta["page"]), second.get(meta["page"], "")
        # 模型在大表格里偶尔陷入重复循环：循环之前的正文是好的，循环本身去掉
        first = truncate_loop(result.get(key) or "")
        if first and not looks_invented(first):
            by_version.setdefault(meta["version"], {})[page] = first
            if other and engines_agree(first, other):
                agreed.setdefault(meta["version"], set()).add(page)
        elif other:
            by_version.setdefault(meta["version"], {})[page] = other
    sf = get_sessionmaker()
    stats: dict = {
        "versions": len(by_version),
        "replaced": 0,
        "kept_old": 0,
        "errors": 0,
        "detail": [],
    }
    with system_scope("apply-pages"):
        for vid, pages in by_version.items():
            async with sf() as session:
                try:
                    version = await session.get(DocumentVersion, uuid.UUID(vid))
                    document = await session.get(Document, version.document_id)
                    res = await apply_page_replacements(
                        session,
                        version.id,
                        pages,
                        document.language,
                        ArchiveStore(get_settings().archive_dir),
                        corroborated=agreed.get(vid, set()),
                    )
                    await session.commit()
                except Exception as exc:  # noqa: BLE001 - one document must not stop the others
                    await session.rollback()
                    stats["errors"] += 1
                    stats["detail"].append({"version": vid[:8], "error": str(exc)[:100]})
                    continue
            stats["replaced" if res["status"] == "replaced" else "kept_old"] += 1
            stats["detail"].append(
                {
                    "title": document.title[:34],
                    "status": res["status"],
                    "accepted": res["accepted"],
                    "rejected": res["rejected"],
                }
            )
    return stats


async def reparse_columns(country: str | None = None) -> dict:
    """Re-read text-layer documents whose clause numbering fails the quality card."""
    from sqlalchemy import select

    from app.core import egress_models  # noqa: F401
    from app.core.rls import system_scope
    from app.ingestion.archive import ArchiveStore
    from app.ingestion.models import Document, DocumentVersion
    from app.ingestion.quality_card import cards_in
    from app.ingestion.reparse import reparse_from_archive, resplit_from_stored

    settings = get_settings()
    archive = ArchiveStore(settings.archive_dir)
    sf = get_sessionmaker()
    stats: dict = {"checked": 0, "reparsed": 0, "kept_old": 0, "errors": 0, "detail": []}
    with system_scope("reparse-columns"):
        async with sf() as session:
            cards = await cards_in(session, country)
            titles = {c["title"] for c in cards if any("条号连续" in p for p in c["card"].problems)}
            rows = (
                await session.execute(
                    select(
                        DocumentVersion.id,
                        Document.title,
                        Document.language,
                        DocumentVersion.text_source,
                        DocumentVersion.mime_type,
                    )
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(DocumentVersion.state == "parsed")
                    .order_by(DocumentVersion.version_no.desc())
                )
            ).all()
        seen: set[str] = set()
        for vid, title, language, source, mime in rows:
            if title not in titles or title in seen:
                continue
            seen.add(title)
            stats["checked"] += 1
            async with sf() as session:
                try:
                    if source == "text_layer" and "pdf" in (mime or ""):
                        res = await reparse_from_archive(session, archive, vid, language)
                    else:
                        res = await resplit_from_stored(session, vid, language)
                    await session.commit()
                except Exception as exc:  # noqa: BLE001 - one file must not stop the batch
                    await session.rollback()
                    stats["errors"] += 1
                    stats["detail"].append({"title": title[:40], "error": str(exc)[:120]})
                    continue
            stats["reparsed" if res["status"] == "reparsed" else "kept_old"] += 1
            stats["detail"].append(
                {
                    "title": title[:40],
                    "status": res["status"],
                    "before": res["before"],
                    "after": res["after"],
                }
            )
    return stats


async def reverify_translations(country: str | None = None, sf=None) -> dict[str, int]:
    """Re-check every failed current translation under today's rules, without translating again.

    A rule that was too strict (or a bug fixed since) should not leave a good translation marked
    failed, and a model call to redo it would only repeat the cost. A translation is restored only
    when it passes every deterministic check and the semantic check as well.
    """
    from sqlalchemy import select

    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge.models import ClauseTranslation
    from app.knowledge.numeric_integrity import is_numeric_only
    from app.knowledge.translator import _semantic_issues, verify_translation

    sf = sf or get_sessionmaker()
    stats = dict(checked=0, restored=0, still_failing=0, not_required=0)
    async with sf() as session:
        query = (
            select(ClauseTranslation, Clause)
            .join(Clause, Clause.id == ClauseTranslation.clause_id)
            .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                ClauseTranslation.is_current.is_(True),
                ClauseTranslation.quality_check_passed.is_(False),
                ClauseTranslation.engine == "llm",
                ClauseTranslation.translated_text != "",
            )
        )
        if country:
            query = query.where(Document.country_code == country.upper())
        rows = (await session.execute(query)).all()
        from app.ingestion.noise import clean_text, clean_translation

        sem_cache: dict[str, list[str]] = {}
        for tr, clause in rows:
            stats["checked"] += 1
            src = clean_text(clause.original_text)
            if is_numeric_only(src):
                stats["not_required"] += 1
                continue
            zh = clean_translation(tr.translated_text)
            ok, issues, hits = verify_translation(
                src,
                zh,
                tr.glossary_hits or [],
                clause_number=clause.clause_number,
            )
            if ok:
                issues = await _semantic_issues(
                    src, zh, clause.language, sem_cache
                )
                ok = not issues
            if ok:
                tr.translated_text = zh
                tr.quality_check_passed = True
                tr.quality_issues = []
                tr.glossary_hits = hits
                tr.review_status = "machine_translated"
                stats["restored"] += 1
            else:
                tr.quality_issues = issues
                stats["still_failing"] += 1
            if stats["checked"] % 20 == 0:
                await session.commit()
                print("[reverify]", json.dumps(stats, ensure_ascii=False), flush=True)
        await session.commit()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-platform-admin", help="创建平台管理员")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="平台管理员")
    s = sub.add_parser("load-sites", help="导入种子站点(停用、条款待确认)")
    s.add_argument("file")
    r = sub.add_parser("reparse", help="重新解析已有版本(如补做 OCR)")
    r.add_argument("--state", default="needs_ocr")
    r.add_argument("--country")
    u = sub.add_parser("upgrade-ocr", help="用当前 OCR 引擎重新识别库内扫描件，更好才替换")
    u.add_argument("--country")
    u.add_argument("--max-pages", type=int, help="只重新识别页数不超过此值的文件")
    u.add_argument(
        "--accept-equal", action="store_true", help="识别引擎本身改进、页数条号不变时也允许替换"
    )
    i = sub.add_parser("import-phase1", help="从一期迁移已有原文与元数据")
    i.add_argument("--data-dir", required=True, help="一期 docs/phase2/data 目录")
    i.add_argument("--country", required=True)
    pm = sub.add_parser(
        "import-phase1-mysql",
        help="从一期服务器全量镜像备份（mysql_backup.sql + all_documents_backup.tar.gz）幂等吸纳多国法规",
    )
    pm.add_argument("--backup-root", required=True, help="包含 mysql_backup.sql 与 all_documents_backup.tar.gz 的目录")
    pm.add_argument("--ocr-cache-dir", help="预核验逐页识别缓存 JSON 目录")
    pm.add_argument("--supplement-manifest", help="附加官方直采补录清单 manifest.json 路径")
    m = sub.add_parser("import-file", help="导入从官方站点下载的单个 PDF，走同一套解析与识别流程")
    m.add_argument("--country", required=True)
    m.add_argument("--file", required=True)
    m.add_argument("--url", required=True, help="官方来源网址(文件的真实出处)")
    m.add_argument("--title", required=True)
    m.add_argument("--language", required=True)
    m.add_argument("--type", default="REGULATION", help="LAW/REGULATION/GUIDANCE/OTHER")
    m.add_argument("--publisher", required=True)
    m.add_argument("--official-number")
    m.add_argument("--confirmed-official-by", help="仅非名单内站点时填写：由谁确认其为官方来源")
    m.add_argument("--instrument-kind", help="文件性质，如 ADMISSION_GUIDANCE（官方申报表格）")
    m.add_argument("--relevance", choices=("relevant", "related"), help="明确指定纳入范围")
    m.add_argument(
        "--legal-status",
        choices=("unknown", "in_force", "amended", "repealed", "draft"),
        help="现行状态；草案、法案必须填 draft",
    )
    m.add_argument("--published-date", help="YYYY-MM-DD，不确定就不传")
    c = sub.add_parser("import-catalog", help="按国家信源清单批量导入所有已核对的官方文本")
    c.add_argument("--catalog", required=True, help="catalog_<国家码>.json")
    c.add_argument("--dry-run", action="store_true", help="只校验清单与文件，不写库")
    c.add_argument("--concurrency", type=int, default=1, help="同时处理的文件数（扫描件识别很慢）")
    tz = sub.add_parser("catalog-title-zh", help="把信源清单里的中文名补到已入库的文档上（引用时与原名并列）")
    tz.add_argument("--catalog", required=True, action="append", help="catalog_<国家码>.json，可多次给出")
    isc = sub.add_parser("intake-scope", help="长法规按业务相关章节入库：列出各部分是否在范围内；加 --apply 才写入")
    isc.add_argument("--document", required=True, help="文档 id")
    isc.add_argument("--apply", action="store_true", help="写入文档的入库范围（不加则只显示）")
    isc.add_argument("--clear", action="store_true", help="取消按章节入库，恢复整份")
    qc = sub.add_parser("quality-card", help="入库质量卡：逐份文档的可读率、条号连续、译文覆盖")
    qc.add_argument("--country")
    qc.add_argument("--report", help="写成 Markdown 文件")
    f = sub.add_parser("preflight", help="只读预检站点(robots.txt 与地址)")
    f.add_argument("--country")
    f.add_argument("--report", help="把结果写成 Markdown 报告")
    t = sub.add_parser("translate", help="批量执行真实大模型受控法律翻译(S1)")
    t.add_argument("--country", help="指定国家代码(如 SG/EG/TN/MA/LB),不传则翻译全部五国")
    t.add_argument(
        "--only-missing",
        action="store_true",
        default=False,
        help="仅翻译缺少合格 LLM 译文的条款",
    )
    t.add_argument("--dry-run", action="store_true", help="只列出全量目标/缺口，不调用模型、不写库")
    t.add_argument("--limit", type=int, help="显式小样本上限；不传则全量")
    t.add_argument("--concurrency", type=int, default=4, help="并发请求数(默认 4)")
    t.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="同一条款同一版提示词下失败多少次后不再自动重译(0=不限)",
    )
    co = sub.add_parser("collect-opinion", help="采集汽车数据相关的官方通报与官媒资讯")
    co.add_argument("--per-page", type=int, default=40, help="每个栏目页最多读取的相关标题数")
    co.add_argument("--url", action="append", help="直接收录指定的官方文章地址（可重复）")
    cs = sub.add_parser("collect-standards", help="采集智驾、车联网、车载数据相关国家标准的目录")
    cs.add_argument("--prune", action="store_true", help="只按现行范围规则清理库里不合规的条目")
    po = sub.add_parser("prune-opinion", help="按现行主题规则清理库里不在主题内的资讯")
    po.add_argument("--dry-run", action="store_true")
    go = sub.add_parser("grade-opinion", help="给尚未分级的资讯分级（规则表判定，模型只抽事实）")
    go.add_argument("--stored", action="store_true", help="按库里已有的事实重新判级，不调用模型")
    go.add_argument("--limit", type=int)
    go.add_argument("--regrade", action="store_true", help="已有分级的也重新分级")
    go.add_argument(
        "--policy-candidates", action="store_true", help="只重读可能受政策口径影响的条目"
    )
    go.add_argument("--concurrency", type=int, default=4)
    lo = sub.add_parser("label-opinion", help="给全部资讯打参考标签（两个独立模型 + 分歧裁决）")
    lo.add_argument("--out", required=True, help="标签文件（JSONL，可续跑）")
    lo.add_argument("--model-a", default="deepseek-v4-pro")
    lo.add_argument("--model-b", default="deepseek-flash")
    lo.add_argument("--model-judge", default="deepseek-v4-pro")
    lo.add_argument("--concurrency", type=int, default=4)
    ao = sub.add_parser(
        "arbitrate-opinion", help="系统与参考标签不一致的条目，按书面规范做最终裁决"
    )
    ao.add_argument("--labels", required=True)
    ao.add_argument("--model", default="deepseek-v4-pro")
    eo = sub.add_parser("eval-opinion", help="按参考标签评测舆情分级（开发集 / 测试集）")
    eo.add_argument("--labels", required=True)
    eo.add_argument("--report", help="写成 JSON 文件")
    eo.add_argument("--no-arbitration", action="store_true", help="只用参考标签，不采用裁决")
    cr = sub.add_parser("build-readings", help="为文字层缺字的条款生成经双路核对的校读文本")
    cr.add_argument("--version-id", required=True)
    cr.add_argument("--ocr-json", required=True, help="按页的识别结果：{页码: 文字}")
    ts = sub.add_parser("triage-scope", help="按三类业务逐份核对文件范围（不删除，只改相关性）")
    ts.add_argument("--redo", action="store_true", help="已核对过的也重新核对")
    ts.add_argument("--dry-run", action="store_true", help="只看结果，不写库")
    fa = sub.add_parser(
        "fill-attributes", help="从文本抽取生效日、修订日、废止状态（引文须在原文中）"
    )
    fa.add_argument("--country")
    fa.add_argument("--limit", type=int)
    fn = sub.add_parser("fill-node-attributes", help="从证据条款抽取节点本体属性（须有引文）")
    fn.add_argument("--limit", type=int)
    fn.add_argument("--types", default="PENALTY,OBLIGATION")
    fp = sub.add_parser("fill-parties", help="给义务/罚则节点补义务主体与适用条件（须有引文）")
    fp.add_argument("--country")
    fp.add_argument("--limit", type=int, default=None)
    vg = sub.add_parser("verify-graph", help="图谱节点与关系的自动核验（条文逐项对照，两次独立核对）")
    vg.add_argument("--country")
    vg.add_argument("--kind", choices=("all", "nodes", "edges"), default="all")
    vg.add_argument("--limit", type=int, default=None)
    vg.add_argument("--concurrency", type=int, default=6)
    wg = sub.add_parser("whitepaper-generate", help="生成国家白皮书（8 个维度简报，新版本；会调用大模型，先 --dry-run）")
    wg.add_argument("--country", required=True)
    wg.add_argument("--dry-run", action="store_true", help="只数各维度取到的条文并估 token，不调模型")
    pt = sub.add_parser("page-text-generate", help="用模型把国家页的三条概述和各维度结论写短写通顺（每国约 3-4 千 token；先 --dry-run）")
    pt.add_argument("--country", required=True)
    pt.add_argument("--dry-run", action="store_true")
    cd = sub.add_parser("classify-documents", help="给法规定汽车数据跨境业务类别和核心/配套/外围（只读标题）")
    cd.add_argument("--country")
    cd.add_argument("--redo", action="store_true")
    ad = sub.add_parser("admission-dedupe", help="把同一国家里其实是同一办理事项的条目合并（用户端只显示一项）")
    ad.add_argument("--country", required=True)
    ag = sub.add_parser("admission-generate", help="由准入类关系生成准入事项与办理环节（须有逐字引文）")
    ag.add_argument("--country")
    ag.add_argument("--limit", type=int, default=None)
    ag.add_argument("--rescope", action="store_true", help="对范围外事项做二次独立判断并重建")
    cn = sub.add_parser("classify-obligations", help="给义务节点打类别标签（具体/条件/监管职责）")
    cn.add_argument("--limit", type=int)
    ap = sub.add_parser("apply-pages", help="把更好的整页识别结果按页写回（先逐页判断再采用）")
    ap.add_argument("--index", required=True, help="导出页面时的 index.json")
    ap.add_argument("--result", required=True, help="识别结果 result.json")
    ap.add_argument("--second", help="第二个引擎的读数 {页码: 文字}；读出同样的词才互相印证")
    rs = sub.add_parser(
        "reparse-columns",
        help="文字层文件按栏顺序重新读取；只在条号连续率更好时才新增版本",
    )
    rs.add_argument("--country")
    rp = sub.add_parser(
        "reparse-version", help="用页面图像的第二读数重新切分，新增一个版本（旧版本保留）"
    )
    rp.add_argument("--version-id", required=True)
    rp.add_argument("--ocr-json", required=True, help="按页的识别结果：{页码: 文字}")
    rp.add_argument("--dry-run", action="store_true", help="只对比切分结果，不写库")
    rp.add_argument(
        "--allow-shrink", action="store_true", help="允许文字量变少（把文件裁成其中一部法令）"
    )
    rv = sub.add_parser(
        "reverify-translations", help="用当前核验规则重新核验未通过的译文（不调用翻译）"
    )
    rv.add_argument("--country")
    dcl = sub.add_parser("classify-data", help="把图谱里还没有归类的数据类别归入车辆数据种类（国家简报用）")
    dcl.add_argument("--country", required=True)
    ttg = sub.add_parser("tag-terms", help="给术语标与汽车数据跨境业务的关系（核心/相关/较远），术语表按它排序")
    ttg.add_argument("--country", required=True)
    ttg.add_argument("--redo", action="store_true")
    sub.add_parser("admission-materials-zh", help="准入事项里外文的申请材料名补上中文名（原文放括号里）")
    amk = sub.add_parser("admission-material-kinds", help="给准入事项法规列的材料分类：写进申请材料的信息 / 官方表格 / 申请本身 / 递交时自带的原件")
    amk.add_argument("--redo", action="store_true")
    amk.add_argument("--country")
    afd = sub.add_parser("admission-from-docs", help="从指定法规的条文里直接读出准入事项（逐字引文校验；已有事项的条文跳过）")
    afd.add_argument("--documents", nargs="+", required=True)
    afd.add_argument("--dry-run", action="store_true")
    afd.add_argument("--pattern", default=None, help="只读匹配这个正则的条文（条文是“主管机关核准”这类写法时用）")
    aft = sub.add_parser("admission-functions", help="给准入事项标上适用的业务（自动驾驶测试、车联网上云、远程诊断与OTA、座舱与生物识别）")
    aft.add_argument("--redo", action="store_true")
    ipr = sub.add_parser("admission-profile", help="从条文读出准入事项的固定栏目（本地测试、本地代表、申请语言、有效期）")
    ipr.add_argument("--redo", action="store_true")
    ipr.add_argument("--country")
    fa = sub.add_parser("forms-auto", help="从官方表格原件读出可填位置，生成准入官方表格目录（forms_auto.json）")
    fa.add_argument("--only")
    fa.add_argument("--redo", action="store_true")
    fa.add_argument("--reassemble", action="store_true", help="用已保存的模型结果重新组装，不调用模型")
    fa.add_argument("--dry-run", action="store_true", help="只数每份表的可填位置，不调用模型")
    apr = sub.add_parser("admission-prune", help="准入事项里其实是企业内部或合同相对方授权的（不向主管机构申请），设为范围外")
    apr.add_argument("--country", default=None)
    nrg = sub.add_parser("name-regulators", help="给图谱里只有原文名的监管机构补上中文名（国家简报展示与同一机构合并用）")
    nrg.add_argument("--country", default=None)
    nrg.add_argument("--recheck", action="store_true", help="外文原名的机构全部按条文原句重新核对中文名")
    ctg = sub.add_parser("tag-claims", help="给图谱里的义务和处罚标上所属方面与业务相关度（国家页、对比、条文检索用）")
    ctg.add_argument("--country")
    ctg.add_argument("--redo", action="store_true")
    kg = sub.add_parser("extract-kg", help="批量执行 S2 知识图谱结构化抽取与语义裁判")
    kg.add_argument("--country", help="指定国家代码(如 SG/EG/TN/MA/LB),不传则抽取全部五国")
    kg.add_argument(
        "--max-clauses",
        type=int,
        default=None,
        help="显式抽样时每份文档条款上限；不传则抽取全部范围内条款",
    )
    kg.add_argument("--concurrency", type=int, default=8, help="并发请求数(默认 8)")
    args = parser.parse_args()

    _ensure_dev_env()

    if args.command == "create-platform-admin":
        password = os.environ.get("CYSH_ADMIN_PASSWORD") or getpass.getpass("密码(至少12位): ")
        if len(password) < 12:
            print("密码至少 12 位", file=sys.stderr)
            return 2
        asyncio.run(_create_platform_admin(args.email, args.name, password))
    elif args.command == "load-sites":
        created, skipped = asyncio.run(load_sites(Path(args.file)))
        print(f"新增 {created} 个站点,跳过已存在 {skipped} 个。新增站点均为停用、条款待确认。")
    elif args.command == "reparse":
        print("重新解析结果:", asyncio.run(reparse(args.state, args.country)))
    elif args.command == "upgrade-ocr":
        for row in asyncio.run(
            upgrade_ocr(args.country, accept_equal=args.accept_equal, max_pages=args.max_pages)
        ):
            print(row)
    elif args.command == "import-phase1":
        asyncio.run(run_import_phase1(args.data_dir, args.country))
    elif args.command == "import-phase1-mysql":
        from app.ingestion.archive import ArchiveStore
        from app.ingestion.phase1_mysql_import import import_phase1_mysql_backup

        res = asyncio.run(
            import_phase1_mysql_backup(
                Path(args.backup_root),
                get_sessionmaker(),
                ArchiveStore(get_settings().archive_dir),
                ocr_cache_dir=Path(args.ocr_cache_dir) if args.ocr_cache_dir else None,
                supplement_manifest=(
                    Path(args.supplement_manifest) if args.supplement_manifest else None
                ),
                fallback_ocr=_ocr_engine(),
            )
        )
        print(
            "一期镜像吸纳结果:",
            json.dumps(
                {
                    "countries": sorted(res.countries_covered),
                    "countries_count": len(res.countries_covered),
                    "sites_created": res.sites_created,
                    "documents_created": res.documents_created,
                    "versions_created": res.versions_created,
                    "skipped_protected": res.skipped_protected,
                    "skipped_existing_hash": res.skipped_existing_hash,
                    "states": dict(res.states),
                    "errors": res.errors,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    elif args.command == "import-file":
        from datetime import date

        from app.ingestion.archive import ArchiveStore
        from app.ingestion.manual_import import ManualSource, import_manual_file

        result = asyncio.run(
            import_manual_file(
                ManualSource(
                    country_code=args.country,
                    file=Path(args.file),
                    source_url=args.url,
                    title=args.title,
                    language=args.language,
                    document_type=args.type,
                    publisher=args.publisher,
                    official_number=args.official_number,
                    confirmed_official_by=args.confirmed_official_by,
                    instrument_kind=args.instrument_kind,
                    relevance=args.relevance,
                    legal_status=args.legal_status,
                    published_date=(
                        date.fromisoformat(args.published_date) if args.published_date else None
                    ),
                ),
                get_sessionmaker(),
                ArchiveStore(get_settings().archive_dir),
                _ocr_engine(),
            )
        )
        print("导入结果:", result)
    elif args.command == "catalog-title-zh":
        from app.ingestion.source_catalog import apply_title_zh, load_catalog

        async def _title_zh():
            from app.core.db import get_sessionmaker

            for path in args.catalog:
                n = await apply_title_zh(load_catalog(Path(path)), get_sessionmaker())
                print(f"{path}: 补上中文名 {n} 份")

        asyncio.run(_title_zh())
    elif args.command == "import-catalog":
        from app.ingestion.archive import ArchiveStore
        from app.ingestion.source_catalog import (
            import_catalog,
            load_catalog,
            register_entry_pages,
            summarize,
        )

        catalog = load_catalog(args.catalog)
        print(f"国家 {catalog.country}：{len(catalog.items)} 条；按类别与状态：")
        for category, counts in summarize(catalog).items():
            print(" ", category, {k: v for k, v in counts.items() if v})
        for row in asyncio.run(
            import_catalog(
                catalog,
                get_sessionmaker(),
                ArchiveStore(get_settings().archive_dir),
                _ocr_engine(),
                dry_run=args.dry_run,
                concurrency=args.concurrency,
            )
        ):
            print(row)
        pages = asyncio.run(register_entry_pages(catalog, get_sessionmaker(), dry_run=args.dry_run))
        print(
            "入口页登记：",
            {r: sum(1 for p in pages if p["result"] == r) for r in {p["result"] for p in pages}},
        )
        for page in pages:
            if page["result"] == "rejected":
                print(" ", page)
    elif args.command == "intake-scope":
        from app.core.rls import system_scope
        from app.ingestion import intake_scope
        from app.ingestion.models import Document

        async def _isc() -> None:
            with system_scope("intake-scope"):
                async with get_sessionmaker()() as s:
                    doc = await s.get(Document, uuid.UUID(args.document))
                    if doc is None:
                        raise SystemExit("没有这份文档")
                    if args.clear:
                        await intake_scope.save(s, doc, {})
                        await s.commit()
                        print("已恢复整份入库")
                        return
                    plan = await intake_scope.plan(s, doc.id)
                    for p in plan["parts"]:
                        mark = "在内  " if p["in_scope"] else "范围外"
                        print(f"{mark} {p['from']:>4}-{p['to']:<4} {p['clauses']:>3} 条 | {p['title'][:80]} | {p['why']}")
                    print(f"范围内 {plan['in_scope']} / {plan['total']} 条")
                    if args.apply:
                        await intake_scope.save(s, doc, plan)
                        await s.commit()
                        print("已写入")

        asyncio.run(_isc())
    elif args.command == "quality-card":
        from app.ingestion.quality_card import build_cards, render

        text = render(asyncio.run(build_cards(get_sessionmaker(), args.country)))
        if args.report:
            Path(args.report).write_text(text, encoding="utf-8")
            print(f"已写入 {args.report}")
        else:
            print(text)
    elif args.command == "preflight":
        rows = asyncio.run(preflight(args.country))
        report = render_report(rows)
        if args.report:
            Path(args.report).write_text(report, encoding="utf-8")
            print(f"已写入 {args.report}")
        else:
            print(report)
    elif args.command == "translate":
        res = asyncio.run(
            batch_translate(
                country=args.country,
                only_missing=args.only_missing,
                concurrency=args.concurrency,
                dry_run=args.dry_run,
                limit=args.limit,
                max_attempts=args.max_attempts,
            )
        )
        print("批量翻译结果:", json.dumps(res, ensure_ascii=False))
    elif args.command == "collect-opinion":
        if args.url:
            res = asyncio.run(run_collect_opinion_urls(args.url))
        else:
            res = asyncio.run(run_collect_opinion(args.per_page))
        print("舆情采集：", json.dumps(res, ensure_ascii=False))
    elif args.command == "collect-standards":
        if args.prune:
            from app.standards.collect import prune_off_domain

            res = asyncio.run(prune_off_domain(get_sessionmaker()))
        else:
            res = asyncio.run(run_collect_standards())
        print("标准目录采集：", json.dumps(res, ensure_ascii=False))
    elif args.command == "prune-opinion":
        from app.opinion.collect import prune_off_topic

        res = asyncio.run(prune_off_topic(get_sessionmaker(), dry_run=args.dry_run))
        print("舆情清理：", json.dumps(res, ensure_ascii=False))
    elif args.command == "grade-opinion":
        from app.opinion.collect import grade_items, regrade_stored

        if args.stored:
            print(
                "舆情重新判级：",
                json.dumps(asyncio.run(regrade_stored(get_sessionmaker())), ensure_ascii=False),
            )
            return 0

        res = asyncio.run(
            grade_items(
                get_sessionmaker(),
                limit=args.limit,
                regrade=args.regrade,
                policy_candidates=args.policy_candidates,
                concurrency=args.concurrency,
            )
        )
        print("舆情分级：", json.dumps(res, ensure_ascii=False))
    elif args.command == "label-opinion":
        from app.opinion.evaluate import label_all

        res = asyncio.run(
            label_all(
                get_sessionmaker(),
                Path(args.out),
                model_a=args.model_a,
                model_b=args.model_b,
                model_judge=args.model_judge,
                concurrency=args.concurrency,
            )
        )
        print("参考标签：", json.dumps(res, ensure_ascii=False))
    elif args.command == "arbitrate-opinion":
        from app.opinion.evaluate import arbitrate_conflicts

        res = asyncio.run(
            arbitrate_conflicts(get_sessionmaker(), Path(args.labels), model=args.model)
        )
        print("舆情裁决：", json.dumps(res, ensure_ascii=False))
    elif args.command == "eval-opinion":
        from app.opinion.evaluate import evaluate

        res = asyncio.run(
            evaluate(get_sessionmaker(), Path(args.labels), use_arbitration=not args.no_arbitration)
        )
        text = json.dumps(res, ensure_ascii=False, indent=1)
        if args.report:
            Path(args.report).write_text(text, encoding="utf-8")
        print(text)
    elif args.command == "build-readings":
        res = asyncio.run(build_readings(args.version_id, args.ocr_json))
        print("校读文本：", json.dumps(res, ensure_ascii=False))
    elif args.command == "triage-scope":
        from app.ingestion.scope_triage import triage_all

        res = asyncio.run(triage_all(get_sessionmaker(), redo=args.redo, dry_run=args.dry_run))
        for c in res.pop("changes"):
            print(" ", c["from"], "->", c["to"], "|", c["title"], "|", c["why"])
        print("范围核对：", json.dumps(res, ensure_ascii=False))
    elif args.command == "apply-pages":
        done = asyncio.run(apply_pages(args.index, args.result, args.second))
        print("按页写回：", json.dumps(done, ensure_ascii=False))
    elif args.command == "fill-parties":
        from app.knowledge.clause_attributes import fill_parties

        done = asyncio.run(fill_parties(get_sessionmaker(), limit=args.limit, country=args.country))
        print("义务主体与适用条件:", json.dumps(done, ensure_ascii=False))
    elif args.command == "whitepaper-generate":
        from app.core.rls import system_scope
        from app.knowledge import whitepaper as wp

        async def _wp() -> None:
            code = args.country.upper()
            with system_scope("whitepaper-cli"):
                async with get_sessionmaker()() as s:
                    if args.dry_run:
                        per = await wp.gather(s, code)
                        chars = sum(len(str(c.get("quote_zh") or c.get("quote_original") or "")[:700]) + 80 for items in per.values() for c in items)
                        print(code, {k: len(v) for k, v in per.items()}, f"估输入约 {chars / 1.6 / 1000:.0f}k token，输出约 {8 * 1.6:.0f}k token")
                        return
                    blocks, meta = await wp.write_whitepaper(s, code)
                    row = await wp.save(s, code, blocks, meta)
                    await s.commit()
                    print(code, "第", row.version_no, "版", "token", meta["tokens"], "被拒", meta["report"]["dropped"], "字数", wp.page_text_length(blocks))

        asyncio.run(_wp())
    elif args.command == "page-text-generate":
        from app.core.rls import system_scope
        from app.knowledge import country_page_text as cpt
        from app.knowledge import whitepaper_graph as wg

        async def _pt() -> None:
            code = args.country.upper()
            with system_scope("page-text-cli"):
                async with get_sessionmaker()() as s:
                    _, meta = await wg.build(s, code)
                    facts = cpt.facts_of(meta)
                    if args.dry_run:
                        print(code, f"输入约 {len(json.dumps(facts, ensure_ascii=False)) / 1.6 / 1000:.1f}k token，输出约 1k token")
                        return
                    data, tokens, h = await cpt.write(meta)
                    s.add(cpt.CountryPageText(country_code=code, data=data, input_hash=h, tokens=tokens))
                    await s.commit()
                    print(code, "token", tokens, "通过的概述", len(data["overview"]), "/3，通过的维度句", len(data["dimensions"]), "/8")
                    print(json.dumps(data, ensure_ascii=False, indent=1))

        asyncio.run(_pt())
    elif args.command == "verify-graph":
        from app.knowledge.graph_verify_run import verify_graph

        done = asyncio.run(
            verify_graph(
                get_sessionmaker(), country=args.country, kind=args.kind, limit=args.limit,
                concurrency=args.concurrency,
            )
        )
        print("图谱核验：", json.dumps(done, ensure_ascii=False))
    elif args.command == "classify-documents":
        from app.knowledge.business_topic import classify, dumps

        async def _cd() -> None:
            async with get_sessionmaker()() as session:
                print("法规业务分类：", dumps(await classify(session, args.country, redo=args.redo)))

        asyncio.run(_cd())
    elif args.command == "admission-dedupe":
        from app.admission.dedupe import dedupe

        async def _dd() -> None:
            async with get_sessionmaker()() as session:
                print("合并重复事项：", json.dumps(await dedupe(session, args.country), ensure_ascii=False))

        asyncio.run(_dd())
    elif args.command == "admission-generate":
        from app.admission.generator import generate, relink, rescope

        if args.rescope:
            done = asyncio.run(rescope(get_sessionmaker(), country=args.country))
        else:
            # 先把旧事项接到重抽后的新关系上（同一条文、同一引文），只为真正新出现的关系生成事项。
            # 两步放在同一个事件循环里：连接池的连接属于创建它的循环，分两次 asyncio.run 第二步会断连
            async def _relink_then_generate() -> dict:
                sf = get_sessionmaker()
                print("接回已有事项：", json.dumps(await relink(sf, country=args.country), ensure_ascii=False))
                return await generate(sf, country=args.country, limit=args.limit)

            done = asyncio.run(_relink_then_generate())
        print("准入事项：", json.dumps(done, ensure_ascii=False))
    elif args.command == "classify-obligations":
        from app.knowledge.clause_attributes import classify_obligations

        done = asyncio.run(classify_obligations(get_sessionmaker(), limit=args.limit))
        print("义务分类：", json.dumps(done, ensure_ascii=False))
    elif args.command == "fill-node-attributes":
        from app.knowledge.clause_attributes import fill_nodes

        kinds = tuple(args.types.split(","))
        done = asyncio.run(fill_nodes(get_sessionmaker(), node_types=kinds, limit=args.limit))
        print("节点属性：", json.dumps(done, ensure_ascii=False))
    elif args.command == "fill-attributes":
        from app.ingestion.version_attributes import fill_versions

        done = asyncio.run(
            fill_versions(get_sessionmaker(), country=args.country, limit=args.limit)
        )
        print("版本属性：", json.dumps(done, ensure_ascii=False))
    elif args.command == "reparse-columns":
        print(
            "按栏重新读取：",
            json.dumps(asyncio.run(reparse_columns(args.country)), ensure_ascii=False),
        )
    elif args.command == "reparse-version":
        res = asyncio.run(
            reparse_version(
                args.version_id, args.ocr_json, dry_run=args.dry_run, allow_shrink=args.allow_shrink
            )
        )
        print("重新切分：", json.dumps(res, ensure_ascii=False))
    elif args.command == "reverify-translations":
        res = asyncio.run(reverify_translations(args.country))
        print("重新核验结果:", json.dumps(res, ensure_ascii=False))
    elif args.command == "classify-data":
        from app.knowledge.data_classes import classify

        async def _dcl() -> None:
            async with get_sessionmaker()() as session:
                print(await classify(session, args.country.upper()))

        asyncio.run(_dcl())
    elif args.command == "tag-terms":
        from app.knowledge.legal_terms import current_definitions
        from app.knowledge.library_view import library
        from app.knowledge.term_tags import tag as tag_terms

        async def _ttg() -> None:
            async with get_sessionmaker()() as session:
                cc = args.country.upper()
                scope = {e.document.id for e in await library(session, cc)}
                rows = [t for t in await current_definitions(session, cc, None, None) if t.document_id in scope]
                print(await tag_terms(session, rows, redo=args.redo))

        asyncio.run(_ttg())
    elif args.command == "admission-materials-zh":
        from app.admission.material_names import translate as name_materials

        async def _amz() -> None:
            async with get_sessionmaker()() as session:
                print(await name_materials(session))

        asyncio.run(_amz())
    elif args.command == "admission-material-kinds":
        from app.admission.material_kinds import classify as classify_materials

        async def _amk() -> None:
            from app.core.rls import system_scope

            with system_scope("admission-material-kinds"):
                async with get_sessionmaker()() as session:
                    print(await classify_materials(session, redo=args.redo, country=args.country))

        asyncio.run(_amk())
    elif args.command == "admission-from-docs":
        import uuid as _uuid

        from app.admission.from_documents import from_documents

        ids = [_uuid.UUID(x) for x in args.documents]
        if args.dry_run:
            async def _afd_dry() -> None:
                from sqlalchemy import select

                import app.models_registry  # noqa: F401
                from app.admission.from_documents import ADMISSION_WORDS, DUTY_WORDS, NOT_ADMISSION
                from app.core.rls import system_scope
                from app.ingestion.models import Clause, DocumentVersion

                with system_scope("dry"):
                    async with get_sessionmaker()() as s:
                        cl = (await s.scalars(select(Clause).join(DocumentVersion, DocumentVersion.id == Clause.version_id).where(DocumentVersion.document_id.in_(ids)))).all()
                import re as _re

                hit = [c for c in cl if _re.search(args.pattern, c.original_text or "") and len(c.original_text or "") > 60] if args.pattern else [c for c in cl if ADMISSION_WORDS.search(c.original_text or "") and DUTY_WORDS.search(c.original_text or "") and not NOT_ADMISSION.search((c.original_text or "")[:600]) and len(c.original_text or "") > 60]
                print({"clauses": len(cl), "candidates": len(hit), "est_tokens": sum(min(len(c.original_text), 6000) for c in hit) // 2 + 1500 * len(hit)})

            asyncio.run(_afd_dry())
        else:
            print(json.dumps(asyncio.run(from_documents(get_sessionmaker(), ids, pattern=args.pattern)), ensure_ascii=False, indent=1))
    elif args.command == "admission-functions":
        from app.admission.function_tags import tag as tag_functions

        async def _aft() -> None:
            from app.core.rls import system_scope

            with system_scope("admission-functions"):
                async with get_sessionmaker()() as session:
                    print(await tag_functions(session, redo=args.redo))

        asyncio.run(_aft())
    elif args.command == "admission-profile":
        from app.admission.item_profile import fill as fill_profile

        async def _ipr() -> None:
            from app.core.rls import system_scope

            with system_scope("admission-profile"):
                async with get_sessionmaker()() as session:
                    print(await fill_profile(session, redo=args.redo, country=args.country))

        asyncio.run(_ipr())
    elif args.command == "forms-auto":
        from app.forms import build_auto

        if args.dry_run:
            from app.forms.pdf_fill import list_fields

            for spec in build_auto.SPECS:
                data = (build_auto.SRC / spec["src"]).read_bytes()
                f = list_fields(data) or build_auto.printed_spaces(data)
                print(spec["code"], len(f), "est_tokens", len(f) * 160 + 3500 * (len(f) // 45 + 1))
        else:
            print(json.dumps(asyncio.run(build_auto.build(args.only, args.redo, args.reassemble)), ensure_ascii=False, indent=1))
    elif args.command == "admission-prune":
        from app.admission.generator import prune_internal

        print(json.dumps(asyncio.run(prune_internal(get_sessionmaker(), country=args.country)), ensure_ascii=False, indent=1))
    elif args.command == "name-regulators":
        from app.knowledge.regulator_names import translate as name_regulators

        async def _nrg() -> None:
            async with get_sessionmaker()() as session:
                print(await name_regulators(session, args.country, recheck=args.recheck))

        asyncio.run(_nrg())
    elif args.command == "tag-claims":
        from app.knowledge.claim_tags import tag

        async def _ctg() -> None:
            async with get_sessionmaker()() as session:
                print(await tag(session, args.country, redo=args.redo))

        asyncio.run(_ctg())
    elif args.command == "extract-kg":
        res = asyncio.run(
            batch_extract_kg(
                country=args.country,
                max_clauses_per_doc=args.max_clauses,
                concurrency=args.concurrency,
            )
        )
        print("批量知识图谱抽取结果:", json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    from app.core.rls import system_scope

    with system_scope("explicit operator CLI"):
        raise SystemExit(main())
