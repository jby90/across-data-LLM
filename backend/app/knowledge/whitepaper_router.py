# ruff: noqa: E501
"""White paper endpoints: read and export for users (the portal shows these when a country is
chosen), and an administrator's “update” that writes a new version in the background."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analysis.document_templates import Rendered, build_docx, build_pdf
from app.analysis.report_exporter import content_disposition, named
from app.analysis.workflow_engine import (
    country_name_zh,
    is_overseas_country,
    overseas_countries_map,
)
from app.core.db import get_session
from app.core.rls import system_scope
from app.iam.deps import Principal, require_admin_side, require_super_admin, require_user_side
from app.knowledge import whitepaper as wp

log = logging.getLogger(__name__)
user_router = APIRouter(prefix="/api/knowledge/whitepapers", tags=["whitepaper-user"])
admin_router = APIRouter(prefix="/api/admin/knowledge/whitepapers", tags=["whitepaper-admin"])
UserDep = Annotated[Principal, Depends(require_user_side)]
AdminDep = Annotated[Principal, Depends(require_admin_side)]
# 重新生成简报会花模型费用：只有平台管理员能做
PlatformAdminDep = Annotated[Principal, Depends(require_super_admin)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
_running: set[str] = set()


def _country(code: str) -> str:
    code = code.strip().upper()
    if not is_overseas_country(code):
        raise HTTPException(404, "这个国家的简报还没有生成")
    return code


def _summary(code: str, row: wp.CountryWhitepaper | None) -> dict:
    rep = (row.report or {}) if row and isinstance(row.report, dict) else {}
    rev_status = "approved" if rep.get("review_status") == "approved" else ("auto_verified" if row else None)
    return {
        "country_code": code,
        "country_name_zh": country_name_zh(code),
        "available": row is not None,
        "version_no": row.version_no if row else None,
        "updated_at": row.created_at.isoformat() if row and row.created_at else None,
        "updating": code in _running,
        "review_status": rev_status,
        "reviewed_at": rep.get("reviewed_at"),
        "manual_edited": bool(rep.get("manual_edited")),
        "edit_note": rep.get("edit_note"),
    }


@user_router.get("")
async def list_whitepapers(user: UserDep, session: SessionDep) -> list[dict]:
    """Every country and whether it has a white paper yet (the portal's country list)."""
    countries = await overseas_countries_map(session)
    return [_summary(code, await wp.latest(session, code)) for code in countries]


_page_cache: dict[str, tuple[float, dict]] = {}
_hash_cache: dict[str, str] = {}


async def known_hash(session: AsyncSession, code: str) -> str | None:
    """The fingerprint if the background rebuild has worked it out already, else None: in the
    running server a page request never runs the eight searches itself (right after a start-up that
    would hold up the whole server for every user). Without the keeper (tests, command line) it is
    worked out here."""
    from app.knowledge import cache_sync

    if not cache_sync.keeper_active:
        return await current_hash(session, code)
    return _hash_cache.get(code) or None


async def country_lib_stamp(session: AsyncSession, code: str) -> str:
    """Cheap per-country SQL fingerprint of documents, versions, clauses, translations, and KG edges."""
    from sqlalchemy import text

    row = (
        await session.execute(
            text(
                """
                SELECT
                  (SELECT md5(coalesce(string_agg(d.id::text || d.relevance || coalesce(d.business_weight,'') || coalesce(d.intake_scope::text,''), ',' ORDER BY d.id), ''))
                   FROM documents d WHERE d.country_code = :c),
                  (SELECT md5(coalesce(string_agg(dv.id::text || dv.version_no::text || dv.state || coalesce(dv.legal_status,'') || coalesce(dv.review_status,''), ',' ORDER BY dv.id), ''))
                   FROM document_versions dv JOIN documents d ON d.id = dv.document_id WHERE d.country_code = :c),
                  (SELECT count(c.id)::text || ':' || md5(coalesce(string_agg(c.text_hash, ',' ORDER BY c.id), ''))
                   FROM clauses c JOIN document_versions dv ON dv.id = c.version_id JOIN documents d ON d.id = dv.document_id WHERE d.country_code = :c),
                  (SELECT count(ct.id)::text || ':' || md5(coalesce(string_agg(ct.id::text || ct.review_status || md5(ct.translated_text), ',' ORDER BY ct.id), ''))
                   FROM clause_translations ct JOIN clauses c ON c.id = ct.clause_id JOIN document_versions dv ON dv.id = c.version_id JOIN documents d ON d.id = dv.document_id
                   WHERE d.country_code = :c AND ct.is_current = true AND ct.quality_check_passed = true),
                  (SELECT count(e.id)::text || ':' || md5(coalesce(string_agg(e.id::text || e.review_status || coalesce(e.verify_state,''), ',' ORDER BY e.id), ''))
                   FROM kg_edges e WHERE e.country_code = :c)
                """
            ),
            {"c": code},
        )
    ).one()
    return "|".join(str(x) for x in row)


async def current_hash(session: AsyncSession, code: str) -> str:
    """The fingerprint of the clauses a briefing would be written from now (heavy: eight searches);
    kept until the library changes, so “待更新” is shown without making anyone wait."""
    from app.knowledge.cache_sync import use_cached

    hit = _hash_cache.get(code)
    if use_cached(hit):
        return hit
    latest_wp = await wp.latest(session, code)
    if latest_wp is not None and latest_wp.source_hash and isinstance(latest_wp.report, dict):
        saved_stamp = latest_wp.report.get("lib_stamp")
        if saved_stamp and saved_stamp == await country_lib_stamp(session, code):
            _hash_cache[code] = latest_wp.source_hash
            return latest_wp.source_hash
    _hash_cache[code] = wp.source_hash(await wp.gather(session, code))
    return _hash_cache[code]


async def _page_data(session: AsyncSession, code: str) -> dict:
    """The country page's content (cached half an hour): assembled from the verified graph."""
    import time

    from app.knowledge import country_page_text as cpt
    from app.knowledge import whitepaper_graph as wg
    from app.knowledge.cache_sync import use_cached

    hit = _page_cache.get(code)
    if use_cached(hit):  # 不按时间过期，库或图谱变化时后台重建
        return hit[1]
    _, meta = await wg.build(session, code)
    meta = cpt.apply(meta, await cpt.latest(session, code))
    data = {
        "country_code": code,
        "country_name_zh": country_name_zh(code),
        "documents": meta["documents"],
        "clauses": meta["clauses"],
        "overview": meta["overview"],
        "dimensions": meta["dimensions"],
        "refs": meta["refs"],
    }
    while len(_page_cache) >= 24 and code not in _page_cache:
        _page_cache.pop(next(iter(_page_cache)), None)
    _page_cache[code] = (time.monotonic(), data)
    return data


@user_router.get("/{country_code}")
async def read_whitepaper(country_code: str, user: UserDep, session: SessionDep) -> dict:
    """The white paper as blocks (title, heading, paragraph, list, table): what a page or the
    portal renders. ``outdated`` says the library's clauses have changed since it was written."""
    code = _country(country_code)
    row = await wp.latest(session, code)
    if row is None:
        raise HTTPException(404, "这个国家的简报还没有生成")
    current = await known_hash(session, code)
    return {**_summary(code, row), "blocks": wp.retitle(row.blocks), "outdated": bool(current) and current != row.source_hash}


@user_router.get("/{country_code}/page")
async def country_page(country_code: str, user: UserDep, session: SessionDep) -> dict:
    """What a country page shows: three overview lines, the eight dimensions as entries (one
    sentence and a number each) and, per dimension, the points and the clauses behind them.
    Assembled from the verified knowledge graph; no model call."""
    code = _country(country_code)
    out = await _page_data(session, code)
    # 最后更新：与外层国别卡片同源口径（该国核心相关法规最近一次入库时间，按北京时间日期展示）
    from datetime import timedelta, timezone

    from app.knowledge.library_view import library

    entries = [e for e in await library(session, code) if e.document.business_weight != "peripheral"]
    stamps = [e.version.fetched_at or e.version.parsed_at or e.document.discovered_at for e in entries]
    stamps = [t for t in stamps if t is not None]
    last = max(stamps) if stamps else None
    tz_cn = timezone(timedelta(hours=8))
    has_wp = (await wp.latest(session, code)) is not None
    return {
        **out,
        "updated": last.astimezone(tz_cn).date().isoformat() if last else "",
        "has_whitepaper": has_wp,
    }


async def _export(country_code: str, session: AsyncSession, kind: str) -> Response:
    code = _country(country_code)
    row = await wp.latest(session, code)
    if row is None:
        raise HTTPException(404, "这个国家的简报还没有生成")
    date_tag = row.created_at.date().isoformat() if row.created_at else ""
    blocks = wp.retitle(row.blocks)
    await session.close()
    name = f"{country_name_zh(code)}汽车数据合规洞察简报"
    dl_name = f"{country_name_zh(code)}（{code}）汽车数据合规洞察简报"
    context = name  # 页眉不写“第 N 版”：版本只表示官方法规的修订，不是简报生成次数
    rendered = Rendered(blocks=blocks)
    if kind == "pdf":
        data, media = await asyncio.to_thread(build_pdf, rendered, context), "application/pdf"
    else:
        data = await asyncio.to_thread(build_docx, rendered, context)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return Response(data, media_type=media, headers={"Content-Disposition": content_disposition(named(dl_name, date_tag, ext=kind))})


@user_router.get("/{country_code}/export.pdf")
async def export_pdf(country_code: str, user: UserDep, session: SessionDep) -> Response:
    return await _export(country_code, session, "pdf")


@user_router.get("/{country_code}/export.docx")
async def export_docx(country_code: str, user: UserDep, session: SessionDep) -> Response:
    return await _export(country_code, session, "docx")


async def _generate(factory: async_sessionmaker, code: str) -> None:
    from app.core.redaction import llm_scope
    from app.core.resource_gate import background_job_gate

    try:
        async with background_job_gate():
            with system_scope("whitepaper"), llm_scope(session_factory=factory):
                async with factory() as session:
                    blocks, meta = await wp.write_whitepaper(session, code)
                    await wp.save(session, code, blocks, meta)
                    await session.commit()
    except wp.WhitepaperError as exc:
        log.warning("白皮书生成未完成：%s — %s", code, exc)
    except Exception:  # noqa: BLE001 - 后台任务：失败只记录，用户端仍看到上一版
        log.exception("白皮书生成失败：%s", code)
    finally:
        _running.discard(code)
        from app.knowledge.cache_sync import schedule_rebuild

        schedule_rebuild()


@admin_router.post("/{country_code}/update", status_code=202)
async def update_whitepaper(
    country_code: str, admin: PlatformAdminDep, session: SessionDep, force: bool = False
) -> dict:
    """Write a new version in the background; the version shown stays until it is done."""
    code = _country(country_code)
    if code in _running:
        raise HTTPException(409, "这个国家的简报正在更新")
    cur = await wp.latest(session, code)
    if cur is not None and not force and isinstance(cur.report, dict) and cur.report.get("review_status") == "approved":
        raise HTTPException(
            409,
            "当前简报已完成人工审核归档锁定；为防止全篇重做引发内容漂移，请优先使用「按章节更新」定向合入新规，或先解除归档锁定",
        )
    _running.add(code)
    asyncio.create_task(_generate(async_sessionmaker(session.bind, expire_on_commit=False), code))
    return _summary(code, cur)


class SyncDimensionsIn(BaseModel):
    dimensions: list[str] = Field(..., min_length=1, max_length=8)


async def _sync_dims_job(factory: async_sessionmaker, code: str, dims: list[str]) -> None:
    from app.core.redaction import llm_scope
    from app.core.resource_gate import background_job_gate

    try:
        async with background_job_gate():
            with system_scope("whitepaper-sync"), llm_scope(session_factory=factory):
                async with factory() as session:
                    blocks, meta = await wp.sync_dimensions(session, code, dims)
                    await wp.save(session, code, blocks, meta)
                    await session.commit()
    except wp.WhitepaperError as exc:
        log.warning("白皮书受影响维度增量同步未完成：%s %s — %s", code, dims, exc)
    except Exception:  # noqa: BLE001
        log.exception("白皮书受影响维度增量同步失败：%s %s", code, dims)
    finally:
        _running.discard(code)
        from app.knowledge.cache_sync import schedule_rebuild

        schedule_rebuild()


@admin_router.post("/{country_code}/sync-dimensions", status_code=202)
async def sync_whitepaper_dimensions(
    country_code: str,
    body: SyncDimensionsIn,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> dict:
    """Incrementally regenerate only the specified affected dimensions (D1..D8) of a country briefing
    after a law revision; accessible to both platform_admin and ops_admin."""
    from app.audit.service import set_detail
    from app.iam.models import Role

    code = _country(country_code)
    dim_title_map = {c: wp.display_title(c, t) for c, t in wp.EIGHT_DIMENSIONS_SPEC}
    valid = set(dim_title_map)
    dims = sorted({d.strip().upper() for d in body.dimensions if d and d.strip().upper() in valid})
    if not dims:
        raise HTTPException(422, "请至少选择一个需要更新的简报章节")
    if admin.role != Role.PLATFORM_ADMIN and len(dims) >= len(valid):
        raise HTTPException(403, "全篇重生成须由超级管理员发起，按章节更新最多勾选7个受影响章节")
    if code in _running:
        raise HTTPException(409, "这个国家的简报正在更新")
    _running.add(code)
    dim_names = [dim_title_map[d] for d in dims]
    set_detail(request, summary=f"按受影响章节更新{country_name_zh(code)}合规简报（{'、'.join(dim_names)}）")
    asyncio.create_task(_sync_dims_job(async_sessionmaker(session.bind, expire_on_commit=False), code, dims))
    return {**_summary(code, await wp.latest(session, code)), "synced_dimensions": dims}


@admin_router.get("")
async def admin_list(admin: AdminDep, session: SessionDep) -> list[dict]:
    """Every country with its current version, when it was written, what it cost, and whether the
    library has moved on since (so the administrator sees which ones need an update)."""
    out = []
    countries = await overseas_countries_map(session)
    for code in countries:
        row = await wp.latest(session, code)
        info = _summary(code, row)
        if row is not None:
            info["tokens"] = row.tokens
            info["dropped"] = len(dropped_sentences(row))
            info["documents"] = (row.report or {}).get("documents")
            info["clauses"] = (row.report or {}).get("clauses")
            current = await known_hash(session, code)
            info["outdated"] = None if current is None else current != row.source_hash  # None：后台还在比对
            if info["outdated"]:
                # 生成之后又入库（或出了新版本）的该国法规份数：告诉管理员为什么要重新生成
                from sqlalchemy import text as _sql

                info["new_documents"] = int(await session.scalar(_sql(
                    "select count(distinct d.id) from documents d join document_versions v on v.document_id = d.id "
                    "where d.country_code = :c and d.relevance in ('relevant', 'related') and v.fetched_at > :t"),
                    {"c": code, "t": row.created_at}) or 0)
            info["chars"] = wp.page_text_length(row.blocks)
        out.append(info)
    return out


@admin_router.get("/{country_code}/export.pdf")
async def admin_export_pdf(country_code: str, admin: AdminDep, session: SessionDep) -> Response:
    return await _export(country_code, session, "pdf")


@admin_router.get("/{country_code}/export.docx")
async def admin_export_docx(country_code: str, admin: AdminDep, session: SessionDep) -> Response:
    return await _export(country_code, session, "docx")


def dropped_sentences(row) -> list[dict]:
    """The sentences left out of this edition. Editions written before the sentences were kept
    stored only the reason (“D6: …”): those come back with the reason and no sentence."""
    out = []
    for d in (row.report or {}).get("dropped", []):
        if isinstance(d, dict):
            out.append(d)
        elif isinstance(d, str) and ": " in d and not d.endswith("无条文"):
            code, why = d.split(": ", 1)
            out.append({"dimension": code, "text": "", "why": why, "cites": []})
    return out


@admin_router.get("/{country_code}/dropped")
async def admin_dropped(country_code: str, admin: AdminDep, session: SessionDep) -> dict:
    """What the check left out of the current edition, sentence by sentence, with the reason and
    the clauses the sentence cited."""
    code = _country(country_code)
    row = await wp.latest(session, code)
    if row is None:
        raise HTTPException(404, "这个国家的简报还没有生成")
    titles = {c: wp.display_title(c, t) for c, t in wp.EIGHT_DIMENSIONS_SPEC}
    from app.analysis import document_narrative as nar

    items = []
    for d in dropped_sentences(row):
        why = d.get("why", "")
        if d.get("text") and d.get("cites"):  # 原因按现在的核对规则重新给出，写明是哪个说法对不上
            offered = {i + 1: {"document": c["document"], "clause": c["clause"], "text": c["text"], "original": ""} for i, c in enumerate(d["cites"])}
            allowed = set()
            for c in offered.values():
                allowed |= nar.source_numbers(" ".join([c["text"], c["clause"], c["document"]]))
            ok, new_why = nar._check_paragraph({"text": d["text"], "cites": list(offered)}, offered, allowed)
            why = new_why or why
        items.append({**d, "why": why, "title": d.get("title") or titles.get(d.get("dimension"), d.get("dimension"))})
    return {"country_code": code, "version_no": row.version_no, "created_at": row.created_at.isoformat() if row.created_at else None, "items": items}


def _clean_str(val: object, *, max_len: int = 8000) -> str:
    raw = str(val or "")
    cleaned = "".join(
        ch for ch in raw if (ch >= " " or ch in "\n\t") and ch != "\x7f" and not (0xD800 <= ord(ch) <= 0xDFFF)
    )
    return cleaned.strip()[:max_len]


def _sanitize_wp_blocks(raw_blocks: list[dict]) -> list[dict]:
    if not raw_blocks or len(raw_blocks) > 500:
        raise HTTPException(422, "简报正文内容不能为空且段落块数不超过 500 项")
    out: list[dict] = []
    allowed_types = {"title", "heading", "subheading", "paragraph", "list", "table"}
    for blk in raw_blocks:
        if not isinstance(blk, dict):
            raise HTTPException(422, "简报段落结构格式不正确")
        btype = _clean_str(blk.get("type"), max_len=32)
        if btype not in allowed_types:
            raise HTTPException(422, f"不支持的简报段落类型：{btype or '空'}")
        clean_blk: dict = {"type": btype}
        if btype in ("title", "heading", "subheading", "paragraph"):
            txt = _clean_str(blk.get("text"), max_len=6000)
            if not txt:
                continue
            clean_blk["text"] = txt
        elif btype == "list":
            raw_items = blk.get("items")
            if not isinstance(raw_items, list):
                continue
            items = [_clean_str(it, max_len=3000) for it in raw_items if _clean_str(it, max_len=3000)]
            if not items:
                continue
            clean_blk["items"] = items[:100]
        elif btype == "table":
            head = [_clean_str(h, max_len=200) for h in (blk.get("head") or [])] if isinstance(blk.get("head"), list) else []
            rows = []
            if isinstance(blk.get("rows"), list):
                for r in blk["rows"][:300]:
                    if isinstance(r, list):
                        rows.append([_clean_str(c, max_len=3000) for c in r[:12]])
            clean_blk["head"] = head
            clean_blk["rows"] = rows
            if isinstance(blk.get("widths"), list):
                clean_blk["widths"] = [int(w) for w in blk["widths"][:12] if isinstance(w, (int, float))]
        out.append(clean_blk)
    if not out:
        raise HTTPException(422, "简报有效正文内容不能为空")
    return out


def _invalidate_wp_caches(code: str) -> None:
    _page_cache.pop(code, None)
    _hash_cache.pop(code, None)
    try:
        from app.iam.cockpit_router import _KB_POSTURE_CACHE

        _KB_POSTURE_CACHE.clear()
    except Exception:  # noqa: BLE001
        pass


def _version_item(row: wp.CountryWhitepaper, *, is_latest: bool) -> dict:
    rep = row.report if isinstance(row.report, dict) else {}
    synced = rep.get("synced_dimensions") or []
    note = rep.get("edit_note") or (
        f"按受影响章节更新（{'、'.join(synced)}）" if synced else "法规库自动汇编生成"
    )
    return {
        "version_no": row.version_no,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "chars": wp.page_text_length(row.blocks or []),
        "review_status": "approved" if rep.get("review_status") == "approved" else "auto_verified",
        "reviewed_at": rep.get("reviewed_at"),
        "manual_edited": bool(rep.get("manual_edited")),
        "note": note,
        "is_latest": is_latest,
    }


class WhitepaperEditIn(BaseModel):
    blocks: list[dict] = Field(min_length=1, max_length=500)
    note: str = Field(default="人工在线修订定稿", max_length=200)
    approve: bool = True


class WhitepaperReviewIn(BaseModel):
    approved: bool = True
    note: str = Field(default="", max_length=200)


@admin_router.get("/{country_code}/versions")
async def admin_whitepaper_versions(country_code: str, admin: AdminDep, session: SessionDep) -> dict:
    """读取指定国家合规简报当前正文块及全部历史版本记录（支撑在线修订、人工审核归档与一键回退）。"""
    from sqlalchemy import select

    code = _country(country_code)
    rows = (
        await session.scalars(
            select(wp.CountryWhitepaper)
            .where(wp.CountryWhitepaper.country_code == code)
            .order_by(wp.CountryWhitepaper.version_no.desc())
        )
    ).all()
    if not rows:
        raise HTTPException(404, "这个国家的简报还没有生成")
    cur = rows[0]
    return {
        **_summary(code, cur),
        "blocks": wp.retitle(cur.blocks or []),
        "versions": [_version_item(r, is_latest=(idx == 0)) for idx, r in enumerate(rows)],
    }


@admin_router.get("/{country_code}/versions/{version_no}")
async def admin_whitepaper_version_detail(
    country_code: str, version_no: int, admin: AdminDep, session: SessionDep
) -> dict:
    from sqlalchemy import select

    if version_no < 1 or version_no > 100000:
        raise HTTPException(422, "简报版本号超出有效范围")
    code = _country(country_code)
    row = await session.scalar(
        select(wp.CountryWhitepaper).where(
            wp.CountryWhitepaper.country_code == code,
            wp.CountryWhitepaper.version_no == version_no,
        )
    )
    if row is None:
        raise HTTPException(404, "未找到该历史版本的国家合规简报")
    cur = await wp.latest(session, code)
    return {
        **_version_item(row, is_latest=(cur is not None and cur.version_no == row.version_no)),
        "country_code": code,
        "country_name_zh": country_name_zh(code),
        "blocks": wp.retitle(row.blocks or []),
    }


@admin_router.patch("/{country_code}/blocks")
async def admin_edit_whitepaper_blocks(
    country_code: str,
    body: WhitepaperEditIn,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> dict:
    """管理员在线修改国家合规简报正文并生成新版本快照（防篡改历史链），可同步标记为已人工审核归档。"""
    from datetime import UTC, datetime

    from app.audit.service import set_detail

    code = _country(country_code)
    if code in _running:
        raise HTTPException(409, "当前国家简报正在后台更新中，请稍后再编辑保存")
    cur = await wp.latest(session, code)
    if cur is None:
        raise HTTPException(404, "这个国家的简报还没有生成")

    clean_blocks = _sanitize_wp_blocks(body.blocks)
    clean_note = _clean_str(body.note, max_len=200) or "人工在线修订定稿"
    now_iso = datetime.now(UTC).isoformat()
    prev_rep = dict(cur.report) if isinstance(cur.report, dict) else {}
    stamp = await country_lib_stamp(session, code)
    shash = await current_hash(session, code) if cur.source_hash else cur.source_hash
    new_rep = {
        **prev_rep,
        "manual_edited": True,
        "edit_note": clean_note,
        "edited_by": str(admin.user_id),
        "edited_at": now_iso,
        "lib_stamp": stamp,
    }
    if body.approve:
        new_rep["review_status"] = "approved"
        new_rep["reviewed_by"] = str(admin.user_id)
        new_rep["reviewed_at"] = now_iso

    saved = await wp.save(
        session,
        code,
        clean_blocks,
        {"source_hash": shash, "tokens": cur.tokens or 0, "report": new_rep},
    )
    await session.commit()
    _invalidate_wp_caches(code)
    set_detail(
        request,
        summary=f"在线修订{country_name_zh(code)}合规简报并保存为第 {saved.version_no} 版（{clean_note}）",
        after={"country_code": code, "version_no": saved.version_no, "approved": body.approve, "note": clean_note},
    )
    return _summary(code, saved)


@admin_router.post("/{country_code}/review")
async def admin_review_whitepaper(
    country_code: str,
    body: WhitepaperReviewIn,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> dict:
    """标记国家合规简报为「已人工审核归档」（锁定防全篇覆盖漂移）或解除锁定。"""
    from datetime import UTC, datetime

    from sqlalchemy.orm.attributes import flag_modified

    from app.audit.service import set_detail

    code = _country(country_code)
    cur = await wp.latest(session, code)
    if cur is None:
        raise HTTPException(404, "这个国家的简报还没有生成")
    rep = dict(cur.report) if isinstance(cur.report, dict) else {}
    clean_note = _clean_str(body.note, max_len=200)
    if body.approved:
        rep["review_status"] = "approved"
        rep["reviewed_by"] = str(admin.user_id)
        rep["reviewed_at"] = datetime.now(UTC).isoformat()
        if clean_note:
            rep["review_note"] = clean_note
    else:
        rep["review_status"] = "auto_verified"
        rep.pop("reviewed_at", None)
    cur.report = rep
    flag_modified(cur, "report")
    await session.commit()
    _invalidate_wp_caches(code)
    action_zh = "审核归档锁定" if body.approved else "解除归档锁定"
    set_detail(
        request,
        summary=f"{action_zh}{country_name_zh(code)}合规简报（第 {cur.version_no} 版）",
        after={"country_code": code, "version_no": cur.version_no, "review_status": rep["review_status"]},
    )
    return _summary(code, cur)


@admin_router.post("/{country_code}/versions/{version_no}/rollback")
async def admin_rollback_whitepaper(
    country_code: str,
    version_no: int,
    request: Request,
    admin: AdminDep,
    session: SessionDep,
) -> dict:
    """将国家合规简报一键回退至指定历史版本（追加新版本号保留完整修改轨迹，同步刷新导出件）。"""
    import copy as _copy
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.audit.service import set_detail

    if version_no < 1 or version_no > 100000:
        raise HTTPException(422, "简报版本号超出有效范围")
    code = _country(country_code)
    if code in _running:
        raise HTTPException(409, "当前国家简报正在后台更新中，请稍后再执行回退")
    target = await session.scalar(
        select(wp.CountryWhitepaper).where(
            wp.CountryWhitepaper.country_code == code,
            wp.CountryWhitepaper.version_no == version_no,
        )
    )
    if target is None:
        raise HTTPException(404, "未找到该历史版本的国家合规简报")
    cur = await wp.latest(session, code)
    if cur is not None and cur.version_no == target.version_no:
        return _summary(code, cur)

    date_lbl = target.created_at.astimezone().strftime("%Y-%m-%d %H:%M") if target.created_at else f"第{version_no}版"
    target_rep = dict(target.report) if isinstance(target.report, dict) else {}
    stamp = await country_lib_stamp(session, code)
    shash = await current_hash(session, code)
    new_rep = {
        **target_rep,
        "edit_note": f"回退恢复至第 {version_no} 版（{date_lbl}）",
        "rolled_back_from": version_no,
        "rolled_back_at": datetime.now(UTC).isoformat(),
        "lib_stamp": stamp,
    }
    saved = await wp.save(
        session,
        code,
        _copy.deepcopy(target.blocks or []),
        {"source_hash": shash, "tokens": target.tokens or 0, "report": new_rep},
    )
    await session.commit()
    _invalidate_wp_caches(code)
    set_detail(
        request,
        summary=f"将{country_name_zh(code)}合规简报回退至第 {version_no} 版（生成第 {saved.version_no} 版）",
        after={"country_code": code, "from_version_no": version_no, "new_version_no": saved.version_no},
    )
    return _summary(code, saved)
