import uuid
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import and_, not_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.iam.deps import Principal, require_platform_admin
from app.ingestion.models import Document
from app.sources.models import CrawlRun, SourceSite, TermsStatus
from app.sources.permissions import terms_permit_crawl
from app.sources.schemas import RunOut, SiteCreate, SiteOut, SitePatch, TermsDecision

router = APIRouter(prefix="/api/admin/sites", tags=["sources"])

_SHARED_CDN_HOSTS = frozenset({"isomer-user-content.by.gov.sg"})


def _norm_host(url: str | None) -> str:
    if not url:
        return ""
    host = (urlparse(url).hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    for prefix in ("digital.", "cms.", "assets."):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    if host in _SHARED_CDN_HOSTS:
        return ""
    return host


async def site_document_filter(session: AsyncSession, site_id: uuid.UUID):
    """Build a SQLAlchemy filter matching documents directly under `site_id` or, when `site_id` is
    a portal or deduplicated single-file import with zero direct rows, the matching publisher/domain
    or URL documents in the same country so `/admin/sites` counts and `/admin/documents?site_id=...`
    drill-down agree 1-to-1."""
    site = await session.get(SourceSite, site_id)
    if site is None:
        return Document.site_id == site_id
    direct_cnt = len(
        (
            await session.scalars(select(Document.id).where(Document.site_id == site_id).limit(1))
        ).all()
    )
    if direct_cnt > 0:
        return Document.site_id == site_id
    is_single_import = site.strategy == "DIRECT" and not site.enabled
    if is_single_import:
        return and_(
            Document.country_code == site.country_code,
            or_(Document.site_id == site_id, Document.source_url == site.entry_url),
        )
    host = _norm_host(site.entry_url)
    sibling_sites = (
        await session.scalars(
            select(SourceSite).where(SourceSite.country_code == site.country_code)
        )
    ).all()
    matched_site_ids = {
        st.id
        for st in sibling_sites
        if st.id == site.id
        or st.publisher == site.publisher
        or (host and _norm_host(st.entry_url) == host)
    }
    conds = [Document.site_id.in_(matched_site_ids)]
    if host:
        conds.append(Document.source_url.ilike(f"%{host}%"))
    return and_(Document.country_code == site.country_code, or_(*conds))


async def _get_site(session: AsyncSession, site_id: uuid.UUID) -> SourceSite:
    site = await session.get(SourceSite, site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "站点不存在")
    return site


async def _attach_doc_summaries(session: AsyncSession, sites: list[SourceSite]) -> list[SiteOut]:
    if not sites:
        return []
    countries = {s.country_code for s in sites if s.country_code}
    all_country_sites = (
        list(
            (
                await session.scalars(
                    select(SourceSite).where(SourceSite.country_code.in_(countries))
                )
            ).all()
        )
        if countries
        else list(sites)
    )
    site_map = {st.id: st for st in all_country_sites}
    rows = (
        await session.execute(
            select(
                Document.id,
                Document.country_code,
                Document.site_id,
                Document.source_url,
                Document.title,
                Document.title_zh,
                Document.document_type,
                Document.discovered_at,
            )
            .where(Document.country_code.in_(countries))
            .order_by(Document.discovered_at.desc(), Document.id)
        )
    ).all()

    by_site: dict[uuid.UUID, list[tuple]] = {}
    by_url: dict[tuple[str, str], list[tuple]] = {}
    by_pub: dict[tuple[str, str], list[tuple]] = {}
    by_host: dict[tuple[str, str], list[tuple]] = {}

    for row in rows:
        _did, cc, sid, src_url, _title, _title_zh, _dtype, _disc = row
        by_site.setdefault(sid, []).append(row)
        if src_url:
            by_url.setdefault((cc, src_url), []).append(row)
        owner_site = site_map.get(sid)
        if owner_site and owner_site.publisher:
            by_pub.setdefault((cc, owner_site.publisher), []).append(row)
        for h in {_norm_host(src_url), _norm_host(owner_site.entry_url if owner_site else None)}:
            if h:
                by_host.setdefault((cc, h), []).append(row)

    def _summarize(doc_rows: list[tuple], default_dtype: str | None):
        seen_ids = set()
        titles: list[str] = []
        dtypes: list[str] = []
        latest_dt: datetime | None = None
        for did, _cc, _sid, _url, title, title_zh, doc_type, discovered_at in doc_rows:
            if did in seen_ids:
                continue
            seen_ids.add(did)
            display_title = (title_zh or title or "").strip() or title
            if display_title and len(titles) < 3 and display_title not in titles:
                titles.append(display_title)
            norm_dtype = str(doc_type or "").strip().upper()
            if norm_dtype and norm_dtype != "OTHER" and norm_dtype not in dtypes:
                dtypes.append(norm_dtype)
            if discovered_at and (latest_dt is None or discovered_at > latest_dt):
                latest_dt = discovered_at
        norm_default = str(default_dtype or "").strip().upper()
        if not dtypes and norm_default:
            dtypes = [norm_default]
        return len(seen_ids), titles, dtypes, latest_dt

    out: list[SiteOut] = []
    for s in sites:
        item = SiteOut.model_validate(s)
        matched = list(by_site.get(s.id, []))
        if not matched:
            is_single_import = s.strategy == "DIRECT" and not s.enabled
            if is_single_import:
                matched = list(by_url.get((s.country_code, s.entry_url), []))
            else:
                host = _norm_host(s.entry_url)
                matched = list(by_pub.get((s.country_code, s.publisher), []))
                if host:
                    matched = matched + list(by_host.get((s.country_code, host), []))
        cnt, s_titles, s_dtypes, s_latest = _summarize(matched, s.document_type)
        item.document_count = cnt
        item.sample_titles = s_titles
        item.doc_types = s_dtypes
        item.latest_discovered_at = s_latest
        out.append(item)
    return out


@router.get("", response_model=list[SiteOut], summary="站点列表")
async def list_sites(
    country_code: str | None = None,
    scope: Literal["monitor", "imports", "all"] = "monitor",
    _: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
):
    """monitor：入口页与已启用的采集站点（要定期看的）；imports：人工导入单份文件时留下的记录
    （一份文件一条，不会被定期采集）；all：全部。"""
    stmt = select(SourceSite).order_by(
        SourceSite.country_code, SourceSite.publisher, SourceSite.entry_url
    )
    single_file = and_(SourceSite.strategy == "DIRECT", SourceSite.enabled.is_(False))
    if scope == "monitor":
        stmt = stmt.where(not_(single_file))
    elif scope == "imports":
        stmt = stmt.where(single_file)
    if country_code:
        stmt = stmt.where(SourceSite.country_code == country_code.upper())
    sites = list((await session.execute(stmt)).scalars().all())
    return await _attach_doc_summaries(session, sites)


@router.post("", response_model=SiteOut, status_code=201, summary="新增站点(默认停用、条款待确认)")
async def create_site(
    body: SiteCreate,
    _: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
):
    site = SourceSite(
        country_code=body.country_code,
        publisher=body.publisher.strip(),
        language=body.language,
        document_type=body.document_type,
        entry_url=body.entry_url,
        strategy=body.strategy.value,
        config=body.config.model_dump(),
        interval_hours=body.interval_hours,
        terms_url=body.terms_url,
    )
    session.add(site)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "该国家下已存在相同入口地址的站点") from None
    return (await _attach_doc_summaries(session, [site]))[0]


@router.get("/{site_id}", response_model=SiteOut, summary="站点详情")
async def get_site(
    site_id: uuid.UUID,
    _: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
):
    site = await _get_site(session, site_id)
    return (await _attach_doc_summaries(session, [site]))[0]


@router.patch("/{site_id}", response_model=SiteOut, summary="修改站点(入口地址不可改,需新建)")
async def patch_site(
    site_id: uuid.UUID,
    body: SitePatch,
    _: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
):
    site = await _get_site(session, site_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("enabled") and not terms_permit_crawl(site):
        raise HTTPException(status.HTTP_409_CONFLICT, "来源使用条款未确认,不能启用")
    if "config" in data and body.config is not None:
        site.config = body.config.model_dump()
    for key in ("publisher", "interval_hours", "terms_url"):
        if key in data and data[key] is not None:
            setattr(site, key, data[key])
    if "enabled" in data and data["enabled"] is not None:
        site.enabled = data["enabled"]
        if site.enabled and site.next_run_at is None:
            site.next_run_at = datetime.now(UTC)
    await session.commit()
    return (await _attach_doc_summaries(session, [site]))[0]


@router.post("/{site_id}/terms", response_model=SiteOut, summary="确认/否决来源使用条款")
async def decide_terms(
    site_id: uuid.UUID,
    body: TermsDecision,
    p: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
):
    site = await _get_site(session, site_id)
    from app.knowledge.history import snapshot

    snapshot(session, site, "before_source_terms_decision", p.user_id)
    site.terms_status = body.status
    site.terms_approval_scope = body.approval_scope
    site.terms_note = body.note.strip()
    site.terms_decided_by = p.user_id
    site.terms_decided_at = datetime.now(UTC)
    snapshot(session, site, "after_source_terms_decision", p.user_id)
    if body.status != TermsStatus.APPROVED.value:
        site.enabled = False  # 条款被否决:立即停用
    await session.commit()
    return (await _attach_doc_summaries(session, [site]))[0]


@router.post("/{site_id}/run", status_code=202, summary="请求立即抓取(由 worker 执行)")
async def run_now(
    site_id: uuid.UUID,
    _: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
):
    site = await _get_site(session, site_id)
    if not terms_permit_crawl(site):
        raise HTTPException(status.HTTP_409_CONFLICT, "来源使用条款未确认,不能抓取")
    if not site.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "站点已停用")
    site.run_requested = True
    site.next_run_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.get("/{site_id}/runs", response_model=list[RunOut], summary="运行记录")
async def list_runs(
    site_id: uuid.UUID,
    _: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
):
    await _get_site(session, site_id)
    stmt = (
        select(CrawlRun)
        .where(CrawlRun.site_id == site_id)
        .order_by(CrawlRun.started_at.desc())
        .limit(50)
    )
    return (await session.execute(stmt)).scalars().all()


@router.delete("/{site_id}", status_code=status.HTTP_200_OK, summary="删除站点及关联抓取记录")
async def delete_site(
    site_id: uuid.UUID,
    _: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    site = await _get_site(session, site_id)
    has_docs = await session.scalar(select(Document.id).where(Document.site_id == site_id).limit(1))
    if has_docs is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "该站点下已有入库归档法规与采集存证记录，为保障法规溯源链完整，请停用站点而非直接删除",
        )
    await session.delete(site)
    await session.commit()
    return {"deleted_site_id": str(site_id), "country_code": site.country_code}
