# ruff: noqa: E501
"""Generate the standard compliance documents for an assessment; manage their templates."""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import risk_items
from app.analysis.chat import SCENARIO_LABELS
from app.analysis.document_basis import NAMES, country_of
from app.analysis.document_instances import (
    DocumentEdit,
    DocumentInstance,
    EditError,
    apply_edit,
    build_xlsx,
)
from app.analysis.document_narrative import (
    DocumentNarrative,
    NarrativeError,
    facts_hash,
    write_paper,
)
from app.analysis.document_narrative import inputs_hash as narrative_hash
from app.analysis.document_narrative import stored as narrative_stored
from app.analysis.document_templates import (
    DOC_FACT_PREFIX,
    DRAFT_BLANK,
    MAX_ANSWER_CHARS,
    DocumentTemplate,
    Rendered,
    TemplateError,
    build_docx,
    build_pdf,
    ensure_default_templates,
    latest_template,
    render,
    resolve_facts,
    save_template,
    task_values,
)
from app.analysis.document_templates_default import DEFAULT_TEMPLATES
from app.analysis.models import AnalysisTask
from app.analysis.report_exporter import content_disposition, named
from app.audit.service import set_detail
from app.core.db import get_session
from app.iam.deps import Principal, require_platform_admin, require_user_side
from app.iam.models import User

logger = logging.getLogger(__name__)
user_router = APIRouter(prefix="/api/analysis", tags=["compliance-documents"])
admin_router = APIRouter(prefix="/api/admin/document-templates", tags=["compliance-documents"])

UserDep = Annotated[Principal, Depends(require_user_side)]
PlatformAdminDep = Annotated[Principal, Depends(require_platform_admin)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class AnswersIn(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict, max_length=20)


class TemplateIn(BaseModel):
    name_zh: str = Field(min_length=2, max_length=128)
    definition: dict[str, Any]


async def _task(session: AsyncSession, task_id: uuid.UUID, user: Principal) -> AnalysisTask:
    task = await session.get(AnalysisTask, task_id)
    if task is None or task.org_id != user.org_id:
        raise HTTPException(404, "未找到该合规评估项目")
    return task


async def _resolve_citation_to_current(session: AsyncSession, citation: dict) -> dict:
    """Resolve a report citation to the document's latest parsed version when the document was
    subsequently re-cut, refreshing the clause ID, text, and current Chinese translation."""
    from app.ingestion.layout_safety import layout_held_clause_ids
    from app.ingestion.models import NOT_CITABLE_STATUSES, Clause, Document, DocumentVersion
    from app.ingestion.safety import document_is_held
    from app.knowledge.graph_validation import _latest_counterpart, _plain
    from app.knowledge.models import ClauseTranslation

    raw_cid = citation.get("clause_id")
    if not raw_cid:
        return citation
    try:
        cid = uuid.UUID(str(raw_cid))
    except (ValueError, TypeError):
        return citation
    cl = await session.get(Clause, cid)
    doc_id: uuid.UUID | None = None
    if cl is not None:
        dv = await session.get(DocumentVersion, cl.version_id)
        doc_id = dv.document_id if dv else None
    elif citation.get("document_id"):
        try:
            doc_id = uuid.UUID(str(citation["document_id"]))
        except (ValueError, TypeError):
            doc_id = None
    if doc_id is None:
        return citation
    doc_obj = await session.get(Document, doc_id)
    if (
        doc_obj is None
        or doc_obj.relevance in ("irrelevant", "out_of_scope")
        or await document_is_held(session, doc_id)
    ):
        return {}
    latest_dv = await session.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == doc_id,
            DocumentVersion.state == "parsed",
            DocumentVersion.legal_status != "draft",
            DocumentVersion.review_status != "rejected",
        )
        .order_by(DocumentVersion.version_no.desc())
        .limit(1)
    )
    if latest_dv is None or latest_dv.legal_status in NOT_CITABLE_STATUSES:
        return {}
    target_cl = cl if (cl is not None and cl.version_id == latest_dv.id) else None
    if target_cl is None and cl is not None:
        new_cid = await _latest_counterpart(session, cl.id)
        if new_cid is not None:
            cand_cl = await session.get(Clause, new_cid)
            if cand_cl is not None and cand_cl.version_id == latest_dv.id:
                target_cl = cand_cl
    if target_cl is None:
        quote = str(citation.get("quote_original") or (cl.original_text if cl else "") or "")
        needle = _plain(quote[:120])
        if len(needle) >= 20:
            cands = (
                await session.scalars(select(Clause).where(Clause.version_id == latest_dv.id))
            ).all()
            target_cl = next((c for c in cands if needle in _plain(c.original_text)), None)
    if target_cl is None:
        return {}
    if await layout_held_clause_ids(session, [target_cl.id]):
        return {}
    updated = dict(citation)
    rebound = target_cl.id != cid
    updated["clause_id"] = str(target_cl.id)
    updated["version_id"] = str(latest_dv.id)
    updated["version_no"] = latest_dv.version_no
    if target_cl.clause_number:
        updated["clause_number"] = target_cl.clause_number
    if rebound or not updated.get("quote_original"):
        updated["quote_original"] = target_cl.original_text
    zh = str(updated.get("quote_zh") or "").strip()
    is_zh_cl = str(target_cl.language or "").lower().startswith("zh")
    tr_rows = list(
        (
            await session.scalars(
                select(ClauseTranslation)
                .where(
                    ClauseTranslation.clause_id == target_cl.id,
                    ClauseTranslation.is_current.is_(True),
                    ClauseTranslation.quality_check_passed.is_(True),
                    ClauseTranslation.review_status.notin_(
                        ("translation_failed", "not_translated", "rejected", "stale", "superseded")
                    ),
                )
                .limit(2)
            )
        ).all()
    )
    if len(tr_rows) > 1:
        return {}
    if rebound or zh in ("", "(未提供译文)", "（未提供译文）"):
        tr = tr_rows[0] if len(tr_rows) == 1 else None
        if tr and tr.translated_text:
            updated["quote_zh"] = tr.translated_text
        elif is_zh_cl:
            updated["quote_zh"] = target_cl.original_text
    return updated


async def _citations(
    session: AsyncSession, task: AnalysisTask, country_code: str | None = None, side: str = ""
) -> list[dict]:
    """The legal basis of a compliance document: the destination country's own clauses, searched
    directly. It does not come from the assessment report (the white-paper content stays out of
    the evidence documents); the assessment only contributes the business facts used as the query.
    The search is the same keyword-and-index ranking the assessment uses, with no model call."""
    country = country_code or task.country_code
    cache_key = (country, side)
    from app.knowledge.cache_sync import use_cached

    if task.kind == "documents":
        hit = _COUNTRY_BASIS.get(cache_key)
        if use_cached(hit):
            return [dict(c) for c in hit[1]]
    if task.kind != "documents":
        return await _citations_search(session, task, country, side)
    from app.knowledge.cache_sync import rebuilding

    if rebuilding.get():  # 后台重建在自己的线程里跑，不碰服务进程里的锁
        out = await _citations_search(session, task, country, side)
        _COUNTRY_BASIS[cache_key] = (time.monotonic(), out)
        return [dict(c) for c in out]
    async with _BASIS_LOCKS.setdefault(cache_key, asyncio.Lock()):
        hit = _COUNTRY_BASIS.get(cache_key)
        if use_cached(hit):
            return [dict(c) for c in hit[1]]
        out = await _citations_search(session, task, country, side)
        _COUNTRY_BASIS[cache_key] = (time.monotonic(), out)
        return [dict(c) for c in out]


# 合规文件的依据要围绕“个人数据/汽车数据的处理与出境”：每个方面带一句主题词（中文，索引里含条文译文，
# 各语种的条文都能命中），避免方面名太宽泛时把电子交易、消费者保护这类只沾边的条文排到前面。
# 准入（D7）本来就要覆盖型式认证、无线电等非数据法规，不加主题词
DOC_TOPIC = {
    "D1": "个人数据 保护 处理 适用范围 数据控制者 责任人",
    "D2": "个人数据 敏感数据 生物识别 位置 图像 定义 类别",
    "D3": "个人数据 向境外 传输 跨境 充分保护 授权 例外 转移",
    "D4": "个人数据 同意 告知 数据主体 权利 查阅 更正 删除",
    "D5": "个人数据 保存期限 存储 销毁 删除 安全措施",
    "D6": "违反 个人数据 处罚 罚款 监禁 制裁",
    "D8": "个人数据 安全措施 技术 组织 义务 整改",
}


async def _citations_search(session: AsyncSession, task: AnalysisTask, country: str, side: str) -> list[dict]:
    from app.analysis import clause_gate
    from app.analysis.doc_rules import label_ok, real_label
    from app.analysis.retrieval import task_query
    from app.analysis.tools import ToolBox
    from app.analysis.workflow_engine import EIGHT_DIMENSIONS_SPEC
    from app.ingestion.models import Document

    toolbox = ToolBox(session, country, cache_scope=None)
    out: list[dict] = []
    seen: set[str] = set()
    # 外文法规名旁边并列中文名（信源清单里登记的），中文读者一眼知道引的是哪部法
    zh_titles = dict(
        (await session.execute(select(Document.id, Document.title_zh).where(Document.country_code == country, Document.title_zh.is_not(None)))).all()
    )
    for code, _ in EIGHT_DIMENSIONS_SPEC:
        # 文件项目没有评估事实，依据按国家、方面和主题词检索（结果只随法规库变，所以可以缓存）
        query = DOC_TOPIC.get(code) if task.kind == "documents" else task_query(task.facts_json or {}, code)
        hits = await toolbox.search_effective_clauses(code, top_k=8 if task.kind != "documents" else 12, query=query)
        if task.kind == "documents":
            # 只沾边的法规（采集时判为 related，如消费者保护）不作合规凭证的依据；准入方面例外
            hits = [c for c in hits if code == "D7" or getattr(c, "relevance", "relevant") != "related"][:8]
        for c in hits:
            if c.clause_id in seen or not c.text.strip() or PAGE_LABEL.match(str(c.label or "")) or not label_ok(str(c.label or ""), c.text):
                continue  # “第1页”这类是整页文字（敕令、目录），不是条文
            seen.add(c.clause_id)
            resolved = await _resolve_citation_to_current(
                session,
                {
                    "clause_id": c.clause_id,
                    "document_id": c.document_id,
                    "document_title": c.document_title,
                    "clause_number": c.label,
                    "quote_original": c.text,
                    "quote_zh": c.translated_text,
                    "dimension_code": code,
                },
            )
            for k in ("quote_original", "quote_zh"):
                resolved[k] = for_reading(str(resolved.get(k) or ""))
            # 依据写法与法规浏览一致：中文名、第X条；原名另存，依据表里放在中文名下一行
            zh = zh_titles.get(_as_uuid(resolved.get("document_id")))
            if zh and zh != resolved.get("document_title"):
                resolved["original_title"] = str(resolved.get("document_title") or "")
                resolved["document_title"] = zh
            resolved["document_title"] = clause_gate.law_name(resolved.get("document_title"), resolved.get("document_id"))  # 同一法规一个中文名，不带“××版”
            label = real_label(str(resolved.get("clause_number") or ""), str(resolved.get("quote_original") or ""), str(resolved.get("quote_zh") or ""), str(zh_titles.get(_as_uuid(resolved.get("document_id"))) or resolved.get("document_title") or ""))
            if not label:
                continue  # 条号读不出来的不作依据（不拿切块序号、页码当条号）
            resolved["clause_number"] = label
            resolved["quote_zh"] = clause_gate.chinese_text(resolved.get("quote_zh"), resolved.get("quote_original"))
            if not clause_gate.usable({**resolved, "document_title": zh_titles.get(_as_uuid(resolved.get("document_id"))) or resolved.get("document_title")}):
                continue  # 缺译文、乱码、个人联系方式、序言签署、官方表格：不作依据
            out.append({**resolved, "dimension_code": code, "side": side})
    return out


# 公报扫描件里，一条的末尾有时连着下一页的目录，条文中间夹着每页的页眉：引用时去掉（app.ingestion.display_text），
# 只动引用的显示，不改库里的条文
from app.ingestion.display_text import cut_at_contents, for_reading  # noqa: E402, F401


def _as_uuid(value) -> uuid.UUID | None:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


log = logging.getLogger(__name__)
PAGE_LABEL = re.compile(r"^\s*第\s*\d+\s*页\s*$")
BASIS_TTL_SECONDS = 6 * 3600
_COUNTRY_BASIS: dict[tuple, tuple[float, list[dict]]] = {}
_BASIS_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_BASIS_LOCKS: dict[tuple, asyncio.Lock] = {}


async def library_countries(session: AsyncSession) -> frozenset[str]:
    """The countries whose regulations are in the library now — read from the library itself (one
    small query), so a country added later is quoted without any change here."""
    from app.ingestion.models import Document

    return frozenset(c for c in (await session.scalars(select(Document.country_code).distinct())).all() if c)


async def warm_basis(factory=None) -> None:
    """After start-up, load what users open first so nobody waits: the country list of the library,
    then per country the relation counts of its regulations, its country page and compliance
    topics, and last the basis clauses of the compliance documents for each country as receiving and
    as sending end (about eight seconds each)."""
    import time as _time

    from app.core.db import get_sessionmaker
    from app.core.rls import system_scope

    try:
        with system_scope("basis-warm"):
            async with (factory or get_sessionmaker())() as session:
                from app.knowledge import whitepaper_router
                from app.knowledge.library_view import library, relation_counts
                from app.knowledge.router import (
                    _OVERVIEW_CACHE,
                    _knowledge_overview,
                    _topics_cached,
                )
                from app.knowledge.whitepaper_router import _page_data

                _OVERVIEW_CACHE["all"] = (_time.monotonic(), await _knowledge_overview(session))
                codes = sorted(await library_countries(session))
                from app.analysis.workflow_engine import overseas_countries_map

                overseas = set(await overseas_countries_map(session))
                for code in codes:
                    # 法规浏览列出全部文件（含关系较远的，按名称可搜到）：与它用同一组版本，命中同一份缓存
                    await relation_counts(session, [e.version.id for e in await library(session, code, include_peripheral=True)])
                    await relation_counts(session, [e.version.id for e in await library(session, code)])
                    if code in overseas:  # 有国家简报的国家（中国等作为数据发出国入库，但没有简报页）
                        await _page_data(session, code)
                        await _topics_cached(session, code)
                        await whitepaper_router.current_hash(session, code)  # 简报是否待更新
                # 每国作为数据接收方、发出方的依据条文都先算好：任意流向（含用户新建的）打开项目、
                # 查看更新通知时只是组合现成的结果
                from app.analysis import clause_gate as _cg
                from app.analysis import doc_rules as _dr
                from app.analysis import doc_sections as _ds
                from app.analysis import question_basis as _qb
                from app.knowledge import whitepaper as _wp

                await _cg.load_title_names(session)
                await _qb._cn_articles(session)
                for code in codes:
                    probe = AnalysisTask(kind="documents", country_code=code, facts_json={})
                    for side in ("recipient", "source"):
                        await _citations(session, probe, side=side)
                    await _dr._latest_clauses(session, code)
                    await _dr.transfer_clause(session, code)
                    await _ds.receiving_law(session, code)
                    if code != "CN":
                        await _wp.pinned_topics(session, code)
    except Exception:  # noqa: BLE001 - 预热失败不影响使用，只是首次会慢
        log.warning("预热失败", exc_info=True)


async def _both_ends(
    session: AsyncSession, task: AnalysisTask, values: dict[str, str]
) -> list[dict]:
    """The assessed country's clauses, plus the other end's when the user named another country
    whose law is in the library (each clause marks which side of the flow it is on).

    For a documents project the basis depends only on the route (not on answers), so it is kept for a
    while: reading a country's whole library for the first time takes seconds, and every page
    refresh and every one of the eight documents would otherwise pay that again."""
    if task.kind != "documents":
        return await _both_ends_uncached(session, task, values)
    key = (
        task.country_code,
        country_of(values.get("source_country")),
        country_of(values.get("recipient_country")),
    )
    from app.knowledge.cache_sync import use_cached

    hit = _BASIS_CACHE.get(key)
    if use_cached(hit):
        return list(hit[1])
    from app.knowledge.cache_sync import rebuilding

    if rebuilding.get():  # 同上：重建线程不碰服务进程里的锁
        out = await _both_ends_uncached(session, task, values)
        _BASIS_CACHE[key] = (time.monotonic(), out)
        return list(out)
    lock = _BASIS_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _BASIS_CACHE.get(key)
        if use_cached(hit):
            return list(hit[1])
        out = await _both_ends_uncached(session, task, values)
        _BASIS_CACHE[key] = (time.monotonic(), out)
        return list(out)


async def _both_ends_uncached(
    session: AsyncSession, task: AnalysisTask, values: dict[str, str]
) -> list[dict]:
    others = {
        c
        for c in (country_of(values.get("source_country")), country_of(values.get("recipient_country")))
        if c and c != task.country_code
    }
    recipient = country_of(values.get("recipient_country"))
    own_side = "recipient" if recipient in (None, task.country_code) else "source"
    out = await _citations(session, task, side=own_side)
    for code in sorted(others):
        out += await _citations(
            session, task, country_code=code, side="recipient" if code == recipient else "source"
        )
    return out


def _official_forms_for(code: str, values: dict[str, str]) -> list[dict]:
    """Official forms to fill from the same facts, at either end of the flow: the sending end's
    transfer form and the receiving end's declaration of processing (the form page takes the facts
    over from the project)."""
    if code != "declaration":
        return []
    from app.analysis.document_basis import official_forms
    from app.forms.service import summary

    sensitive = values.get("sensitive_pi", "")
    out = []
    for side in ("source",):  # 申报表 = 发出国的出境申报
        forms = official_forms(country_of(values.get(f"{side}_country")), side, sensitive)
        pair = {f["code"] for f in forms if f.get("applies_if")}
        # 同一国家按是否涉及敏感数据二选一的两张表：说明写成“X 与 Y 二选一”，表名取自目录，不写死某国
        def short_of(f):
            return f["name_zh"].split("：", 1)[-1].rpartition("（")[2].rstrip("）") or f["code"]

        either = ""
        if len(pair) > 1:
            sens = next((short_of(f) for f in forms if f.get("applies_if") == "sensitive"), "")
            plain = next((short_of(f) for f in forms if f.get("applies_if") == "not_sensitive"), "")
            either = f"{plain} 与 {sens} 二选一：涉及敏感个人信息时用 {sens}，否则用 {plain}；回答“是否含敏感个人信息”后只留一张。"
        for f in forms:
            item = {k: summary(f)[k] for k in ("code", "name_zh", "authority_zh", "country")}
            name = item["name_zh"].split("：", 1)[-1]
            title, _, short = name.rpartition("（")
            item.update(
                side=side,
                side_label="数据发出方" if side == "source" else "数据接收方",
                short=short.rstrip("）") or item["code"],
                title=title or name,
                # 同一国家一般申报与敏感数据事先授权两张表：还没答“是否含敏感个人信息”时都列出，写明二选一
                note=either if f["code"] in pair else "",
            )
            out.append(item)
    return out


async def _inputs(session: AsyncSession, task: AnalysisTask, code: str):
    await ensure_default_templates(session)
    from app.analysis import clause_gate as _cg

    await _cg.load_title_names(session)  # 同一部法在文件里只有一个名字
    template = await latest_template(session, code)
    if template is None:
        raise HTTPException(404, "没有这种合规文件")
    values = resolve_facts(
        template.definition, {**task_values(task), **_scenario(task)}, task.facts_json or {}
    )
    from app.analysis import doc_rules

    if not values.get("company_name") and task.org_id:  # 企业账号注册时填的公司全称（不拿别的项目里写的子公司名代填）
        from app.iam.models import Organization
        from app.iam.service import strip_trial_org_suffix

        org = await session.get(Organization, task.org_id)
        values["company_name"] = strip_trial_org_suffix(org.name or "") if org else ""
    # 发出方（准入是申请人）必须是该国境内的主体或企业指定的当地代表：别国的公司名不代填
    src0, rcp0 = doc_rules.ends(values, task.country_code)
    party_country = doc_rules.overseas(src0, rcp0) if code == "admission" else src0
    values["company_name"] = doc_rules.entity_for(values.get("company_name"), party_country)
    if code == "admission" and not values["company_name"] and party_country and party_country == rcp0:
        raw_rcp = str((task.facts_json or {}).get(DOC_FACT_PREFIX + "recipient") or "")
        local_party = re.split(r"[；;\n]+", raw_rcp)[0].strip() if raw_rcp else ""
        values["company_name"] = doc_rules.entity_for(local_party, party_country)
    # 方向：文件里的“国家”是数据发出国（主依据的法律所在国）；准入取业务开展地（海外一端）
    source, recipient = doc_rules.ends(values, task.country_code)
    if code == "admission":
        target = doc_rules.overseas(source, recipient)
        if source and recipient and source != "CN" and recipient != "CN" and source != recipient:
            src_nm = NAMES.get(source, source)
            rcp_nm = NAMES.get(recipient, recipient)
            if target == source and recipient not in ("SG", "MA", "EG", "TN", "LB", "CN"):
                eu_note = "欧盟 GDPR 及" if recipient in {"EU", "FR", "DE", "IT", "ES", "NL", "BE", "SE", "PL", "AT", "IE", "PT", "CZ", "HU", "NO"} else ""
                values["country_name"] = f"{src_nm}（实车运营与出境审批法域）及{rcp_nm}（接收侧适用{eu_note}属地合规要求）"
            else:
                values["country_name"] = f"{src_nm}（数据发出端）与{rcp_nm}（数据接收端，主办公准入法域：{NAMES.get(target or '', target or '')}）"
        else:
            values["country_name"] = NAMES.get(target or "", values.get("country_name", ""))
    elif source:
        values["country_name"] = NAMES.get(source, values.get("country_name", ""))
    if code == "admission" and task.kind == "documents":
        await _fill_admission(session, task, values)
    citations = await _both_ends(session, task, values)
    if source:  # 出境路径的条文、中国出境主依据、发出国的车企核心法规：每张依据表都从它们开始
        core = await doc_rules.pinned(session, values, task.country_code, code)
        ids = {c["clause_id"] for c in core}
        citations = core + [c for c in citations if str(c.get("clause_id")) not in ids]
    if code == "tia" and source and recipient and source != recipient:
        # TIA 第三节接收国四项要素所依据的条文进入可引用的条文，正文引用它们（表里的和正文里的是同一批）
        from app.analysis import doc_sections

        table_clauses: list[dict] = []
        await doc_sections.receiving_law(session, recipient, table_clauses)
        taken = {x["clause_id"] for x in table_clauses if x.get("clause_id")}
        citations = table_clauses + [c for c in citations if not c.get("clause_id") or str(c.get("clause_id")) not in taken]
    return template, values, doc_rules.applicable(citations, values, code, task.country_code)


async def _fill_admission(session: AsyncSession, task: AnalysisTask, values: dict[str, str]) -> None:
    """The admission matters and who handles them are already in the platform's admission library, so
    the user is not asked for them (what the user typed still wins). Market admission is about the
    country where the vehicles run — where the data is generated, the sending end — so that end is
    looked up first, then the receiving end."""
    from app.admission.models import AdmissionEntry
    from app.admission.router import SHOWN, entry_listed

    if values.get("entry_type") and values.get("authority"):
        return
    rows: list = []
    for code in dict.fromkeys(c for c in (country_of(values.get("source_country")), country_of(values.get("recipient_country")), task.country_code) if c):
        cand_rows = (
            await session.scalars(
                select(AdmissionEntry)
                .where(
                    AdmissionEntry.country_code == code,
                    AdmissionEntry.review_status.in_(SHOWN),
                    AdmissionEntry.merged_into.is_(None),
                )
                .order_by(AdmissionEntry.category, AdmissionEntry.name_zh)
            )
        ).all()
        rows = [r for r in cand_rows if await entry_listed(session, r)]
        if rows:
            break
    # 只列这项业务要办的（与合规评估页“要办 N 项”和准入页同一条规则）；判断不出业务时列全部
    from app.admission import plan

    needed = [r for r in rows if plan.applies(getattr(r, "category", ""), task.scenario_type or "", getattr(r, "functions", None), r.name_zh) == "needed"]
    rows = needed or rows
    if not rows:
        return
    if not values.get("entry_type"):
        values["entry_type"] = "、".join(r.name_zh for r in rows)[:900]
    if not values.get("authority"):
        # 同一机构在库里有多种写法（全称、简称、外文名）：被别的写法包含的去掉，再取出现最多的几个
        # 条文只写“中心”“主管机构”这类角色的，按库里认得的机构全称写；仍认不出的不列
        from app.knowledge.whitepaper_graph import UNNAMED_ROLE, authority_resolver

        resolve = await authority_resolver(session, rows[0].country_code)
        counts: dict[str, int] = {}
        for r in rows:
            name = resolve(r.authority_zh or "")
            if name and not UNNAMED_ROLE.search(re.sub(r"[（(][^）)]*[）)]", "", name).strip()):
                counts[name] = counts.get(name, 0) + 1
        names = [n for n in counts if not any(n != o and n in o for o in counts)]
        names.sort(key=lambda n: -counts[n])
        values["authority"] = "、".join(names[:3])


def _find_section_bounds(paper: list[dict], heading_prefixes: tuple[str, ...] | str) -> tuple[int, int] | None:
    prefixes = (heading_prefixes,) if isinstance(heading_prefixes, str) else heading_prefixes
    start = next(
        (
            i
            for i, b in enumerate(paper)
            if b.get("type") == "heading" and any(str(b.get("text") or "").startswith(p) for p in prefixes)
        ),
        None,
    )
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(paper)) if paper[i].get("type") == "heading"), len(paper))
    return start, end


def _insert_at_section_top(
    paper: list[dict], heading_prefixes: tuple[str, ...] | str, extra: list[dict], marker_text: str
) -> list[dict]:
    if not extra or any(marker_text in str(b.get("text") or "") for b in paper):
        return paper
    bounds = _find_section_bounds(paper, heading_prefixes)
    if bounds is None:
        return paper
    start, _ = bounds
    return [*paper[: start + 1], *copy.deepcopy(extra), *paper[start + 1 :]]


def _insert_at_section_end(paper: list[dict], heading_prefix: str, extra: list[dict], marker_text: str) -> list[dict]:
    if not extra or any(marker_text in str(b.get("text") or "") for b in paper):
        return paper
    bounds = _find_section_bounds(paper, heading_prefix)
    if bounds is None:
        return paper
    _, end = bounds
    return [*paper[:end], *copy.deepcopy(extra), *paper[end:]]


def _promote_flow_and_field_tables(
    paper: list[dict], heading_prefixes: tuple[str, ...] | str, special_note: list[dict]
) -> list[dict]:
    """Place Data Items table -> Special-Category Determination Note -> Flow table -> Analytical Prose."""
    bounds = _find_section_bounds(paper, heading_prefixes)
    if bounds is None:
        return paper
    start, end = bounds
    inner = paper[start + 1 : end]
    field_tbls: list[dict] = []
    flow_blks: list[dict] = []
    prose_blks: list[dict] = []
    for b in inner:
        txt = str(b.get("text") or "").strip()
        if b.get("type") == "table" and (b.get("head") or [])[:2] == ["序号", "数据项名称"]:
            field_tbls.append(b)
        elif b.get("type") == "table" or txt.startswith("数据处理流程图（") or (" → " in txt and not txt.endswith("。")):
            flow_blks.append(b)
        elif txt.startswith("特殊类别与敏感个人数据边界判定说明："):
            continue
        else:
            prose_blks.append(b)
    note_copy = copy.deepcopy(special_note) if special_note else []
    rebuilt = [paper[start], *field_tbls, *note_copy, *flow_blks, *prose_blks]
    return [*paper[:start], *rebuilt, *paper[end:]]


def _refresh_and_promote_dpia_risk_blocks(paper: list[dict], values: dict[str, str], draft: bool) -> list[dict]:
    """Refresh risk blocks and place tables + quantitative closure verdict ABOVE analytical prose in Section 4."""
    raw = values.get("risk_rating", "")
    parsed = risk_items.parse(raw)
    bounds = _find_section_bounds(paper, ("4.", "五、"))
    if bounds is None:
        return paper
    start, end = bounds
    inner = paper[start + 1 : end]
    risk_start = next(
        (
            i
            for i, b in enumerate(inner)
            if b.get("type") == "paragraph"
            and any(
                str(b.get("text") or "").startswith(pfx)
                for pfx in ("主要风险评估与应对清单：", "风险等级判定与分布表", "剩余风险可接受性结论：", "风险整改前后变化说明：")
            )
        ),
        None,
    )
    prose_only = inner[:risk_start] if risk_start is not None else inner
    fresh_risks = risk_items.blocks(raw, DRAFT_BLANK if draft else "") if parsed else (inner[risk_start:] if risk_start is not None else [])
    return [*paper[: start + 1], *fresh_risks, *prose_only, *paper[end:]]


_NON_CN_DECL_NAME = "跨境数据传输合规申报概览表"
_NON_CN_DECL_ROW_LABELS = {
    "是否关键信息基础设施运营者": "是否属关键基础设施或特定受监管主体",
    "是否含重要数据": "是否涉及受管制特定业务数据",
    "单位注册号": "企业商业登记号或识别码",
}


def _effective_doc_name(code: str, default_name: str, values: dict[str, str]) -> str:
    if code == "declaration":
        src = str(values.get("source_country") or "").strip()
        if src and country_of(src) != "CN":
            return _NON_CN_DECL_NAME
    return default_name


def _localize_non_cn_declaration_blocks(blocks: list[dict], values: dict[str, str]) -> list[dict]:
    src = str(values.get("source_country") or "").strip()
    if not src or country_of(src) == "CN":
        return blocks
    out: list[dict] = []
    for blk in blocks:
        b = copy.deepcopy(blk)
        if b.get("type") == "title":
            b["text"] = _NON_CN_DECL_NAME
        elif b.get("type") == "table" and isinstance(b.get("rows"), list):
            for r in b["rows"]:
                if isinstance(r, list) and r and r[0] in _NON_CN_DECL_ROW_LABELS:
                    r[0] = _NON_CN_DECL_ROW_LABELS[r[0]]
        out.append(b)
    return out


def _localize_non_cn_risk_blocks(blocks: list[dict], values: dict[str, str]) -> list[dict]:
    src = str(values.get("source_country") or "").strip()
    if not src or country_of(src) == "CN":
        return blocks
    out: list[dict] = []
    for blk in blocks:
        b = copy.deepcopy(blk)
        if b.get("type") in ("heading", "subheading") and isinstance(b.get("text"), str):
            b["text"] = b["text"].replace("境内全链路技术防护", "发出国本地与跨境全链路技术防护")
        elif b.get("type") == "table" and isinstance(b.get("rows"), list):
            for r in b["rows"]:
                if isinstance(r, list) and r and r[0] in _NON_CN_DECL_ROW_LABELS:
                    r[0] = _NON_CN_DECL_ROW_LABELS[r[0]]
        out.append(b)
    return out


async def _prepare(
    session: AsyncSession,
    task: AnalysisTask,
    code: str,
    draft: bool = False,
    write_draft: bool = False,
    clean_names: bool = False,
):
    """The document as it stands. ``write_draft`` (downloading a draft): a document with argued
    sections gets them written now from what is known, the missing facts marked 【待补充】, instead
    of an outline."""
    from app.analysis import doc_rules, doc_sections

    template, values, citations = await _inputs(session, task, code)
    paper = None
    if template.definition.get("narrative"):
        from app.analysis.document_narrative import usable

        done, outdated = await usable(session, task, code, template.definition, values, citations)
        paper = copy.deepcopy(done.blocks) if done else None
        if write_draft:  # 下载初稿：按现在的依据取（依据、规则没变就是存着的那一份，变了就重写）
            paper = None
        if paper is None and write_draft:
            paper = await _write_paper(session, task, code, template, values, citations, None)

    pre_probe = render(template.definition, values, citations, None, paper, draft, {}, "")
    from app.analysis import verdict_rules

    merged_probe_vals = {**doc_sections.answered(task), **{k: v for k, v in values.items() if v}}
    route_info = (
        await doc_rules.route(session, merged_probe_vals, task.country_code)
        if code in verdict_rules.VERDICT_DOC_CODES
        else {}
    )
    v_info = verdict_rules.compute_doc_verdict(
        code,
        merged_probe_vals,
        route_info,
        task_country_code=task.country_code,
        missing_count=len(pre_probe.missing),
        definition=template.definition,
    )
    if code == "risk_report" and values.get("risk_conclusion") and v_info:
        values = {**values, "risk_conclusion": verdict_rules.reconcile_conclusion_text(values["risk_conclusion"], v_info)}
    if paper and v_info:
        paper = verdict_rules.reconcile_paper_blocks(paper, code, v_info)
    slots = await doc_sections.slots(
        session, task, code, values, missing_count=len(pre_probe.missing), definition=template.definition
    )

    if paper:
        if code == "dpia":
            paper = _promote_flow_and_field_tables(paper, ("1.", "二、"), slots.get("dpia_special_data_note", []))
            paper = _insert_at_section_top(
                paper,
                ("2.", "三、"),
                [*slots.get("dpia_legal_basis", []), *slots.get("safeguard_effectiveness", [])],
                "分业务场景处理目的与合法性基础映射表",
            )
            paper = _insert_at_section_top(
                paper, ("3.", "四、"), slots.get("necessity_comparison", []), "必要性与最小侵害替代方案排他比选表"
            )
            paper = _refresh_and_promote_dpia_risk_blocks(paper, values, draft)
            paper = _insert_at_section_top(
                paper, ("5.", "六、"), slots.get("dpia_stakeholders", []), "利益相关方参与、数据保护官审查意见及权利保障核验表"
            )
            paper = _insert_at_section_top(
                paper, ("6.", "七、"), slots.get("verdict_decision", []), "结论性研判结果与放行条件核定表"
            )
        elif code == "lia":
            paper = _insert_at_section_top(
                paper, "二、", slots.get("necessity_comparison", []), "必要性与最小侵害替代方案排他比选表"
            )
            paper = _insert_at_section_top(
                paper, "三、", slots.get("lia_balancing", []), "企业正当利益与数据主体基本权利五维权衡测试表"
            )
            paper = _insert_at_section_top(
                paper, "四、", slots.get("verdict_decision", []), "结论性研判结果与放行条件核定表"
            )
        elif code == "tia":
            paper = _insert_at_section_top(
                paper, "一、", slots.get("tia_six_steps", []), "跨境传输影响评估（TIA）六步法核查总览表"
            )
            _, rcp = doc_rules.ends(values, task.country_code)
            table = await doc_sections.receiving_law(session, rcp)
            if table:
                paper = _insert_at_section_top(
                    paper,
                    "三、",
                    [{"type": "table", "head": ["事项", f"{NAMES.get(rcp or '', '接收国')}的规定"], "rows": table}],
                    f"{NAMES.get(rcp or '', '接收国')}的规定",
                )
            paper = _insert_at_section_top(
                paper, "四、", slots.get("safeguard_effectiveness", []), "合规原则与安全保护措施适当性及有效性核验表"
            )
            paper = _insert_at_section_top(
                paper, "六、", slots.get("verdict_decision", []), "结论性研判结果与放行条件核定表"
            )
        elif code == "risk_report":
            paper = _promote_flow_and_field_tables(paper, "三、", [])
            paper = _insert_at_section_top(
                paper, "三、", slots.get("necessity_comparison", []), "必要性与最小侵害替代方案排他比选表"
            )

    paper_ref_tail: list[dict] = []
    if code == "risk_report" and paper:
        ref_idx = next(
            (
                i
                for i, b in enumerate(paper)
                if b.get("type") == "heading"
                and str(b.get("text") or "").strip() in ("参考条文", "所依据的条文")
            ),
            None,
        )
        if ref_idx is not None:
            paper_ref_tail = copy.deepcopy(paper[ref_idx:])
            paper = paper[:ref_idx]

    rendered = render(template.definition, values, citations, None, paper, draft, slots, _note(task))
    if code == "risk_report" and paper_ref_tail:
        # 将正文[1]..[n]引注对应的《参考条文》置于第八章之后、附录之前，并移除第八章末尾无编号的重复依据表
        app_idx = next(
            (
                i
                for i, b in enumerate(rendered.blocks)
                if b.get("type") == "heading" and str(b.get("text") or "").startswith("附录")
            ),
            len(rendered.blocks),
        )
        if (
            app_idx > 0
            and rendered.blocks[app_idx - 1].get("type") == "table"
            and rendered.blocks[app_idx - 1].get("head") == ["依据", "条款", "条文内容"]
        ):
            rendered.blocks.pop(app_idx - 1)
            app_idx -= 1
        paper_ref_tail[0]["text"] = "参考条文"
        rendered.blocks[app_idx:app_idx] = paper_ref_tail
    if code == "declaration":
        rendered.blocks = _localize_non_cn_declaration_blocks(rendered.blocks, values)
        if str(values.get("source_country") or "").strip() and country_of(values.get("source_country")) != "CN":
            for mq in (*rendered.missing, *rendered.optional_open):
                if mq.label in _NON_CN_DECL_ROW_LABELS:
                    mq.label = _NON_CN_DECL_ROW_LABELS[mq.label]
    elif code == "risk_report":
        rendered.blocks = _localize_non_cn_risk_blocks(rendered.blocks, values)
        if str(values.get("source_country") or "").strip() and country_of(values.get("source_country")) != "CN":
            for mq in (*rendered.missing, *rendered.optional_open):
                if mq.label in _NON_CN_DECL_ROW_LABELS:
                    mq.label = _NON_CN_DECL_ROW_LABELS[mq.label]
    if write_draft or clean_names:  # 导出的初稿或成稿：同一个机构只用一个名字（法规库机构字典的全称），法规名按流向所属国统一
        from app.analysis import clause_gate
        from app.knowledge.whitepaper import one_name_per_body

        await clause_gate.load_title_names(session)
        ends = tuple(sorted({c for c in doc_rules.ends(values, task.country_code) if c}))
        for cc in ends:
            rendered.blocks = await one_name_per_body(session, cc, rendered.blocks, law_countries=ends)
    return template, rendered


def _note(task: AnalysisTask) -> str:
    """The line under the title: which project, made when."""
    from datetime import date

    title = (getattr(task, "title", "") or "").strip()
    return "　".join(x for x in (f"项目：{title}" if title else "", f"编制日期：{date.today().isoformat()}") if x)


async def _write_paper(
    session: AsyncSession,
    task: AnalysisTask,
    code: str,
    template,
    values: dict,
    citations: list[dict],
    user_id,
    *,
    raise_on_error: bool = False,
) -> list[dict] | None:
    """Write and store the argued sections (DPIA, LIA, TIA, risk_report) from what is known; None when the model
    could not produce sections that pass the checks (unless raise_on_error=True)."""
    from app.analysis import doc_rules, doc_sections, risk_items

    all_facts = {**doc_sections.answered(task), **{k: v for k, v in values.items() if v}}
    route = await doc_rules.route(session, all_facts, task.country_code)
    receiving = await doc_sections.receiving_law(session, doc_rules.ends(all_facts, task.country_code)[1]) if code == "tia" else []
    extra_keys = ("recipient", "purpose", "data_types", "data_volume", "storage_chain", "onward_provision", "risk_conclusion")
    project_facts = {
        k: re.sub(r"《[^》]+》第\s*[0-9一二三四五六七八九十百]+\s*条", "相关法规条款", risk_items.shown(k, str(all_facts[k]), ""))
        for k in extra_keys
        if str(all_facts.get(k) or "").strip()
    }
    context = {
        "doc_code": code,
        "country": values.get("country_name", ""),
        "vehicle": values.get("vehicle_model", ""),
        "scenario": values.get("scenario", ""),
        "source_country": values.get("source_country", ""),
        "recipient_country": values.get("recipient_country", ""),
        "route": re.sub(r"《[^》]+》第\s*[0-9一二三四五六七八九十百]+\s*条", "相关法定条款", route.get("text", "")),
        **({"project_facts": project_facts} if project_facts else {}),
        **({"recipient_law": {k: v for k, v in receiving}} if receiving else {}),
    }
    if code == "lia":
        lb = await doc_rules.legit_basis(session, doc_rules.ends(all_facts, task.country_code)[0])
        context["legit_basis"] = (
            f"发出国法律在处理依据条款里列明了合法利益（{lb['cite']}）" if lb["state"] == "有"
            else f"发出国法律的处理依据条款（{lb['cite']}）没有列合法利益" if lb["state"] == "无"
            else "发出国现行已收录条文中未单独列明合法利益事由，建议以数据主体明确同意或履行合同必需作为主依据，以本合法利益评估作为内部相称性论证支撑"
        )
    import asyncio as _aio
    import hashlib as _h
    import json as _j

    # 依据、接收国要素这些随库变化的上下文也进摘要：库里补了法规、规则变了就按新的重写
    digest = narrative_hash(template.definition, {**values, "_rules": "r11", "_context": _h.sha256(_j.dumps(context, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]}, citations)
    have = await narrative_stored(session, task.id, code, digest)
    if have is not None:
        return have.blocks
    from sqlalchemy.exc import IntegrityError as _SAIntegrityError

    lock_key = (id(_aio.get_running_loop()), str(task.id), code)
    if len(_PAPER_WRITE_LOCKS) > 256:
        _PAPER_WRITE_LOCKS.clear()
    lock = _PAPER_WRITE_LOCKS.setdefault(lock_key, _aio.Lock())
    async with lock:
        have = await narrative_stored(session, task.id, code, digest)
        if have is not None:
            return have.blocks
        try:
            paper = await write_paper(template.definition, values, citations, context)
        except NarrativeError:
            logger.warning("论述没有生成成功：%s %s", task.id, code, exc_info=True)
            if raise_on_error:
                raise
            return None
        session.add(
            DocumentNarrative(
                org_id=task.org_id, task_id=task.id, code=code, inputs_hash=digest, blocks=paper.blocks,
                report={"dropped": paper.dropped, "facts_hash": facts_hash(template.definition, values), "cited_clause_ids": paper.cited},
                tokens=paper.tokens, created_by=user_id,
            )
        )
        try:
            await session.commit()
        except _SAIntegrityError:
            await session.rollback()
            existing = await narrative_stored(session, task.id, code, digest)
            if existing is not None:
                return existing.blocks
            return paper.blocks
        if task.kind == "documents":
            from app.analysis import doc_projects as dp

            ov = await dp.build_overview(session, task)
            await dp._sync_project_status(session, task, ov["final"])
        return paper.blocks


_PAPER_WRITE_LOCKS: dict[tuple[int, str, str], object] = {}


def _scenario(task: AnalysisTask) -> dict[str, str]:
    label = SCENARIO_LABELS.get(task.scenario_type, "")
    scene = str((getattr(task, "facts_json", None) or {}).get("business_scene") or "")
    return {"scenario": f"{label}（{scene}）" if label and scene else label}


@user_router.get("/document-templates")
async def list_templates(user: UserDep, session: SessionDep) -> list[dict]:
    await ensure_default_templates(session)
    rows = (
        await session.scalars(
            select(DocumentTemplate)
            .where(DocumentTemplate.status == "published")
            .order_by(DocumentTemplate.code, DocumentTemplate.version_no.desc())
        )
    ).all()
    latest: dict[str, DocumentTemplate] = {}
    for row in rows:
        latest.setdefault(row.code, row)
    # 按需求 3.2 的顺序列出；自行新增的模板排在后面
    order = {code: i for i, (code, _, _) in enumerate(DEFAULT_TEMPLATES)}
    ranked = sorted(latest.values(), key=lambda r: (order.get(r.code, len(order)), r.code))
    return [{"code": r.code, "name": r.name_zh} for r in ranked]


@user_router.get("/tasks/{task_id}/documents/{code}")
async def document_status(
    task_id: uuid.UUID, code: str, user: UserDep, session: SessionDep
) -> dict:
    """Whether the document can be produced now, and if not, what still has to be asked."""
    task = await _task(session, task_id, user)
    template, rendered = await _prepare(session, task, code)
    values = resolve_facts(
        template.definition, {**task_values(task), **_scenario(task)}, task.facts_json or {}
    )
    return {
        "code": code,
        "name": _effective_doc_name(code, template.name_zh, values),
        "ready": not rendered.missing and not rendered.needs_paper,
        "needs_paper": bool(rendered.needs_paper and not rendered.missing),
        "questions": [
            {
                "key": q.key,
                "label": q.label,
                "question": q.question,
                "why": q.why,
                "short": q.short,
            }
            for q in rendered.missing
        ],
        "official_forms": _official_forms_for(code, values),
        "optional_questions": [
            {"key": q.key, "label": q.label, "question": q.question, "why": q.why, "short": q.short}
            for q in rendered.optional_open
        ],
    }


@user_router.post("/tasks/{task_id}/documents/{code}/answers")
async def answer_document_questions(
    task_id: uuid.UUID, code: str, body: AnswersIn, user: UserDep, session: SessionDep
) -> dict:
    """Store the user's own words for the questions this document asked."""
    task = await _task(session, task_id, user)
    template, rendered = await _prepare(session, task, code)
    asked = {q.key for q in rendered.missing}
    facts = dict(task.facts_json or {})
    for key, value in body.answers.items():
        text = (value or "").strip()
        if key not in asked:
            raise HTTPException(409, "该问题已回答或不属于这份文件")
        if len(text) < 2:
            raise HTTPException(422, "回答太简短，请具体说明")
        limit = risk_items.MAX_CHARS if key in risk_items.LONG_KEYS else MAX_ANSWER_CHARS
        if len(text) > limit:
            raise HTTPException(422, f"回答请控制在 {limit} 字以内")
        if key in risk_items.STRUCTURED and text.startswith("["):
            prev_raw = str(facts.get(DOC_FACT_PREFIX + key) or "")
            text = risk_items.merge_history(prev_raw, text)  # 按行填的风险：保留初评等级、去掉空行、统一格式
        elif key in risk_items.STRUCTURED and not risk_items.is_unsure(text):
            # 一段文字补不齐档位和措施，存进去这一题也永远算缺项：请按行填
            raise HTTPException(422, "“主要风险”请逐项填写：每项风险写内容、选可能性和严重程度（低/中/高）、写至少一项应对措施")
        facts[DOC_FACT_PREFIX + key] = text
    task.facts_json = facts
    await session.commit()
    return await document_status(task_id, code, user, session)


async def _instance(session: AsyncSession, task: AnalysisTask, code: str, *, for_update: bool = False):
    stmt = (
        select(DocumentInstance)
        .where(DocumentInstance.task_id == task.id, DocumentInstance.code == code)
        .order_by(DocumentInstance.created_at.desc())
        .limit(1)
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(stmt)


async def _export_source(session: AsyncSession, task: AnalysisTask, code: str, draft: bool = False):
    """Blocks to export and the file's name: the saved (possibly edited) document when there is
    one, otherwise a fresh rendering that must be complete (or, as a draft, with what is still
    unanswered marked “待补充” without blocking HTTP GET on synchronous LLM generation)."""
    template, rendered = await _prepare(session, task, code, draft=draft, write_draft=False, clean_names=True)
    _, eff_values, _ = await _inputs(session, task, code)
    eff_name = _effective_doc_name(code, template.name_zh, eff_values)
    saved = await _instance(session, task, code)
    if saved is not None:
        blocks, name = saved.blocks, (eff_name if code == "declaration" and eff_name != template.name_zh else saved.name_zh)
    else:
        if rendered.missing and not draft:
            raise HTTPException(
                409, "还有信息需要补充：" + "、".join(q.label for q in rendered.missing)
            )
        if rendered.needs_paper and not draft:
            raise HTTPException(409, "论述部分还没有生成，请先点击“生成论述”")
        blocks, name = rendered.blocks, eff_name
    # 页眉只写文件名：这是企业要递交的文件，平台的对话编号属于内部信息，不印在文件上（Word 与 PDF 相同）
    context = name
    return blocks, name, context


@user_router.post("/tasks/{task_id}/documents/{code}/paper")
async def write_document_paper(
    task_id: uuid.UUID, code: str, user: UserDep, session: SessionDep
) -> dict:
    """Write the argumentative part of a document (DPIA, LIA, TIA) from the facts and the target
    country's clauses. Every figure and every cited clause is checked; what fails is not shown."""
    task = await _task(session, task_id, user)
    template, values, citations = await _inputs(session, task, code)
    if not template.definition.get("narrative"):
        raise HTTPException(409, "这种文件没有需要生成的论述部分")
    probe = render(template.definition, values, citations, None, [])
    if probe.missing:
        raise HTTPException(409, "还有信息需要补充：" + "、".join(q.label for q in probe.missing))
    if await _write_paper(session, task, code, template, values, citations, user.user_id) is None:
        raise HTTPException(502, "论述没有生成成功，可以补充信息后重试")
    return await document_status(task_id, code, user, session)


async def _official_cn_base(
    session: AsyncSession, task: AnalysisTask, code: str, draft: bool
) -> tuple[bytes, str] | None:
    """Build the filled official Chinese CAC Word .docx prior to any online user edits."""
    from app.analysis import doc_rules, doc_sections, verdict_rules
    from app.analysis import official_docx as od
    from app.analysis.document_basis import CN
    from app.analysis.document_narrative import usable

    if code not in ("declaration", "risk_report", "dpia", "contract"):
        return None
    template, in_values, cits = await _inputs(session, task, code)
    if country_of(in_values.get("source_country")) != CN:
        return None
    if not draft:
        probe = render(template.definition, in_values, [], None, [])
        if probe.missing:
            raise HTTPException(409, "还有信息需要补充：" + "、".join(q.label for q in probe.missing))
    paper_blocks: list[dict] | None = None
    if template.definition.get("narrative"):
        found, _ = await usable(session, task, code, template.definition, in_values, cits)
        if not draft and found is None:
            raise HTTPException(409, "论述部分还没有生成，请先点击“生成论述”")
        if found is not None:
            from app.analysis import clause_gate
            from app.knowledge.whitepaper import one_name_per_body

            await clause_gate.load_title_names(session)
            ends = tuple(sorted({c for c in doc_rules.ends(in_values, task.country_code) if c}))
            cleaned_pb = copy.deepcopy(found.blocks)
            for cc in ends:
                cleaned_pb = await one_name_per_body(session, cc, cleaned_pb, law_countries=ends)
            paper_blocks = cleaned_pb
    # 一个项目里的题只答一次：官方表格上有、本文件没单列的栏目（如“数据出境涉及业务”取企业写的跨境传输场景简述），
    # 用企业在本项目别处的回答；本文件自己的值优先
    answered = {
        k.removeprefix(DOC_FACT_PREFIX): str(v).strip() for k, v in (task.facts_json or {}).items()
        if k.startswith(DOC_FACT_PREFIX) and isinstance(v, str) and v.strip() and not v.startswith("暂不确定")
    }
    values = {**answered, **{k: v for k, v in in_values.items() if v}}
    if code == "declaration":
        result = await asyncio.to_thread(od.cn_declaration, values)
        return result.data, template.name_zh
    if code == "contract":
        result = await asyncio.to_thread(
            od.cn_standard_contract, values, DRAFT_BLANK if draft else ""
        )
        return result.data, "个人信息出境标准合同"

    pre_probe = render(template.definition, in_values, cits, None, paper_blocks, draft, {}, "")
    if code == "dpia":  # DPIA = 标准合同备案指南附件 5 个人信息保护影响评估报告；同一项目为风险评估报告回答过的信息一并用上
        _, other, _ = await _inputs(session, task, "risk_report")
        merged_pia_vals = {**other, **values}
        cn_route_info = await doc_rules.route(session, merged_pia_vals, task.country_code)
        cn_v = verdict_rules.compute_doc_verdict(
            code,
            merged_pia_vals,
            cn_route_info,
            task_country_code=task.country_code,
            missing_count=len(pre_probe.missing),
            definition=template.definition,
        )
        if merged_pia_vals.get("risk_conclusion") and cn_v:
            merged_pia_vals = {
                **merged_pia_vals,
                "risk_conclusion": verdict_rules.reconcile_conclusion_text(merged_pia_vals["risk_conclusion"], cn_v),
            }
        if paper_blocks and cn_v:
            paper_blocks = verdict_rules.reconcile_paper_blocks(paper_blocks, code, cn_v)
        cn_slots = await doc_sections.slots(
            session, task, code, merged_pia_vals, missing_count=len(pre_probe.missing), definition=template.definition
        )
        pia_extra = {
            "（2）个人信息处理者和境外接收方处理个人信息的目的": cn_slots.get("necessity_comparison", []),
            "4.遵守个人信息保护相关法律法规的情况": cn_slots.get("safeguard_effectiveness", []),
            "综合影响评估情况和相应整改情况": cn_slots.get("verdict_decision", []),
        }
        result = await asyncio.to_thread(
            od.cn_pia, merged_pia_vals, DRAFT_BLANK if draft else "", paper_blocks, pia_extra
        )
        return result.data, "个人信息保护影响评估报告（出境版）"
    cn_route_info = await doc_rules.route(session, values, task.country_code)
    cn_v = verdict_rules.compute_doc_verdict(
        code,
        values,
        cn_route_info,
        task_country_code=task.country_code,
        missing_count=len(pre_probe.missing),
        definition=template.definition,
    )
    if values.get("risk_conclusion") and cn_v:
        values = {
            **values,
            "risk_conclusion": verdict_rules.reconcile_conclusion_text(values["risk_conclusion"], cn_v),
        }
    if paper_blocks and cn_v:
        paper_blocks = verdict_rules.reconcile_paper_blocks(paper_blocks, code, cn_v)
    cn_slots = await doc_sections.slots(
        session, task, code, values, missing_count=len(pre_probe.missing), definition=template.definition
    )
    sa_extra = {
        "4.拟出境数据在境内存储的系统平台": cn_slots.get("necessity_comparison", []),
        "3.数据安全保障措施有效性证明": cn_slots.get("safeguard_effectiveness", []),
        "综合风险自评估情况和相应整改情况": cn_slots.get("verdict_decision", []),
    }
    result = await asyncio.to_thread(
        od.cn_self_assessment, values, DRAFT_BLANK if draft else "", paper_blocks, sa_extra
    )  # 风险评估报告 = 指南附件 4 数据出境风险自评估报告
    return result.data, "数据出境风险自评估报告"


async def official_docx(session: AsyncSession, task: AnalysisTask, code: str, draft: bool) -> tuple[bytes, str] | None:
    """The document as the official Word template itself, filled — and if the user has revised it
    online, with every chronological DocumentEdit applied losslessly onto the official CAC Word XML."""
    from app.analysis import official_docx as od

    base = await _official_cn_base(session, task, code, draft)
    if base is None:
        return None
    inst = await _instance(session, task, code)
    if inst is None:
        return base
    chronological = list(
        (
            await session.scalars(
                select(DocumentEdit)
                .where(DocumentEdit.instance_id == inst.id)
                .order_by(DocumentEdit.at.asc())
                .limit(500)
            )
        ).all()
    )
    if not chronological:
        return base
    pairs = [(e.before, e.after) for e in chronological]
    patched = await asyncio.to_thread(od.apply_edits_to_official_docx, base[0], pairs, base[1])
    return patched, base[1]


@user_router.get("/tasks/{task_id}/documents/{code}/preview")
async def preview_document(
    task_id: uuid.UUID, code: str, user: UserDep, session: SessionDep
) -> dict:
    """Online read-only preview of any compliance document (official Word template or standard EU/platform layout)
    without blocking on synchronous LLM generation."""
    from app.analysis import official_docx as od

    task = await _task(session, task_id, user)
    saved = await _instance(session, task, code)
    if saved is not None:
        return {"name": saved.name_zh, "kind": "edited", "blocks": saved.blocks}
    official = await official_docx(session, task, code, draft=True)
    if official is not None:
        return {
            "name": official[1],
            "kind": "official_docx",
            "blocks": await asyncio.to_thread(od.docx_preview_blocks, official[0]),
        }
    template, rendered = await _prepare(session, task, code, draft=True, write_draft=False, clean_names=True)
    _, eff_values, _ = await _inputs(session, task, code)
    return {"name": _effective_doc_name(code, template.name_zh, eff_values), "kind": "standard", "blocks": rendered.blocks}


@user_router.get("/tasks/{task_id}/documents/{code}/export.docx")
async def export_document(
    task_id: uuid.UUID, code: str, user: UserDep, session: SessionDep, draft: bool = False
) -> Response:
    task = await _task(session, task_id, user)
    official = await official_docx(session, task, code, draft)
    if official is not None:
        return Response(
            official[0],
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": content_disposition(named(task.title, official[1], ext="docx"))},
        )
    blocks, name, context = await _export_source(session, task, code, draft)
    tstyle = "edpb" if code == "dpia" else "formal_black"
    docx_bytes = await asyncio.to_thread(build_docx, Rendered(blocks=blocks), context, table_style=tstyle)
    return Response(
        docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": content_disposition(
                named(task.title, name, ext="docx")
            )
        },
    )


@user_router.get("/tasks/{task_id}/documents/{code}/export.xlsx")
async def export_document_xlsx(
    task_id: uuid.UUID, code: str, user: UserDep, session: SessionDep, draft: bool = False
) -> Response:
    from app.analysis import official_docx as od

    task = await _task(session, task_id, user)
    official = await official_docx(session, task, code, draft)
    if official is not None:
        blocks = await asyncio.to_thread(od.docx_preview_blocks, official[0])
        xlsx_bytes = await asyncio.to_thread(build_xlsx, blocks, official[1])
        return Response(
            xlsx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": content_disposition(named(task.title, official[1], ext="xlsx"))},
        )
    blocks, name, _ = await _export_source(session, task, code, draft)
    xlsx_bytes = await asyncio.to_thread(build_xlsx, blocks, name)
    return Response(
        xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": content_disposition(
                named(task.title, name, ext="xlsx")
            )
        },
    )


@user_router.get("/tasks/{task_id}/documents/{code}/export.pdf")
async def export_document_pdf(
    task_id: uuid.UUID, code: str, user: UserDep, session: SessionDep, draft: bool = False
) -> Response:
    task = await _task(session, task_id, user)
    task_title = task.title
    import asyncio

    official = await official_docx(session, task, code, draft)
    if official is not None:
        # 有官方 Word 模板的文件：先释放数据库连接，再将填好的官方 Word 原件转成 PDF（无办公软件时平滑降级为内置排版）
        from app.analysis import official_docx as od
        from app.analysis.docx_pdf import PdfEngineUnavailable, docx_to_pdf, engine

        await session.close()
        pdf_bytes: bytes | None = None
        if engine() != "builtin":
            try:
                pdf_bytes = await asyncio.to_thread(docx_to_pdf, official[0])
            except PdfEngineUnavailable:
                pdf_bytes = None
        if pdf_bytes is None:
            fb_blocks = await asyncio.to_thread(od.docx_preview_blocks, official[0], include_pagebreaks=True)
            pdf_bytes = await asyncio.to_thread(
                build_pdf, Rendered(blocks=fb_blocks), official[1], table_style="formal_black"
            )
        return Response(
            pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": content_disposition(named(task_title, official[1], ext="pdf"))},
        )
    blocks, name, context = await _export_source(session, task, code, draft)
    await session.close()
    tstyle = "edpb" if code == "dpia" else "formal_black"
    return Response(
        await asyncio.to_thread(build_pdf, Rendered(blocks=blocks), context, table_style=tstyle),
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(named(task_title, name, ext="pdf"))
        },
    )


async def edpb_en_docx(
    session: AsyncSession,
    task: AnalysisTask,
    draft: bool,
    *,
    actor_user_id: uuid.UUID | None = None,
    read_only_viewer: bool = False,
) -> tuple[bytes, str]:
    """Generate or fetch cached English EDPB 2026 official DPIA Word form for EU/EEA-related non-China routes."""
    from app.analysis.doc_projects import involves_eu_eea
    from app.analysis.document_basis import CN
    from app.analysis.official_eu_edpb_dpia import build_edpb_2026_en

    template, in_values, cits = await _inputs(session, task, "dpia")
    src_val = in_values.get("source_country")
    rcp_val = in_values.get("recipient_country")
    if not involves_eu_eea(src_val, rcp_val):
        raise HTTPException(400, "当前跨境流向不涉及欧盟或欧洲经济区，无需生成欧盟标准英文范本")
    if country_of(src_val) == CN:
        raise HTTPException(409, "数据从中国发出的项目适用国家网信办《个人信息保护影响评估报告》官方模板，不使用欧盟标准英文范本")
    if not draft:
        probe = render(template.definition, in_values, [], None, [])
        if probe.missing:
            raise HTTPException(409, "还有信息需要补充：" + "、".join(q.label for q in probe.missing))
    _, risk_vals, _ = await _inputs(session, task, "risk_report")
    answered = {
        k.removeprefix(DOC_FACT_PREFIX): str(v).strip()
        for k, v in (task.facts_json or {}).items()
        if k.startswith(DOC_FACT_PREFIX) and isinstance(v, str) and v.strip() and not v.startswith("暂不确定")
    }
    merged = {**risk_vals, **answered, **{k: v for k, v in in_values.items() if v}}
    try:
        return await build_edpb_2026_en(
            session,
            task,
            merged,
            cits,
            draft=draft,
            actor_user_id=actor_user_id,
            read_only_viewer=read_only_viewer,
        )
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@user_router.get("/tasks/{task_id}/documents/dpia/export-edpb-en.docx")
async def export_edpb_en_docx(
    task_id: uuid.UUID, user: UserDep, session: SessionDep, draft: bool = False
) -> Response:
    from app.iam.models import Role

    task = await _task(session, task_id, user)
    data, label = await edpb_en_docx(
        session,
        task,
        draft,
        actor_user_id=user.user_id,
        read_only_viewer=(user.role == Role.VIEWER),
    )
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": content_disposition(named(task.title, label, ext="docx"))},
    )


@user_router.get("/tasks/{task_id}/documents/dpia/export-edpb-en.pdf")
async def export_edpb_en_pdf(
    task_id: uuid.UUID, user: UserDep, session: SessionDep, draft: bool = False
) -> Response:
    import asyncio

    from app.analysis import official_docx as od
    from app.analysis.docx_pdf import PdfEngineUnavailable, docx_to_pdf, engine
    from app.iam.models import Role

    task = await _task(session, task_id, user)
    task_title = task.title
    data, label = await edpb_en_docx(
        session,
        task,
        draft,
        actor_user_id=user.user_id,
        read_only_viewer=(user.role == Role.VIEWER),
    )
    await session.close()
    pdf_bytes: bytes | None = None
    if engine() != "builtin":
        try:
            pdf_bytes = await asyncio.to_thread(docx_to_pdf, data)
        except PdfEngineUnavailable:
            pdf_bytes = None
    if pdf_bytes is None:
        fb_blocks = await asyncio.to_thread(od.docx_preview_blocks, data, include_pagebreaks=True)
        pdf_bytes = await asyncio.to_thread(
            build_pdf,
            Rendered(blocks=fb_blocks),
            "EDPB DPIA Template 2026 (Official EN)",
            foreign=True,
            table_style="edpb",
        )
    return Response(
        pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(named(task_title, label, ext="pdf"))},
    )


@user_router.get("/tasks/{task_id}/documents/dpia/preview-edpb-en")
async def preview_edpb_en(
    task_id: uuid.UUID, user: UserDep, session: SessionDep, draft: bool = True
) -> dict:
    from app.analysis import official_docx as od
    from app.iam.models import Role

    task = await _task(session, task_id, user)
    data, _label = await edpb_en_docx(
        session,
        task,
        draft,
        actor_user_id=user.user_id,
        read_only_viewer=(user.role == Role.VIEWER),
    )
    return {
        "name": "数据保护影响评估（欧盟标准英文范本）",
        "kind": "official_docx",
        "blocks": await asyncio.to_thread(od.docx_preview_blocks, data),
    }


class EditIn(BaseModel):
    block: int = Field(ge=0)
    text: str
    item: int | None = Field(None, ge=0)
    row: int | None = Field(None, ge=0)
    col: int | None = Field(None, ge=0)


def _instance_view(inst: DocumentInstance, edits: list[DocumentEdit]) -> dict:
    return {
        "id": str(inst.id),
        "name": inst.name_zh,
        "blocks": inst.blocks,
        "edit_count": inst.edit_count,
        "updated_at": inst.updated_at.isoformat() if inst.updated_at else None,
        "edits": [
            {
                "user_name": e.user_name,
                "place": e.place,
                "before": e.before,
                "after": e.after,
                "at": e.at.isoformat() if e.at else None,
            }
            for e in edits
        ],
    }


async def _edits(session: AsyncSession, inst: DocumentInstance) -> list[DocumentEdit]:
    return list(
        (
            await session.scalars(
                select(DocumentEdit)
                .where(DocumentEdit.instance_id == inst.id)
                .order_by(DocumentEdit.at.desc())
                .limit(200)
            )
        ).all()
    )


@user_router.get("/tasks/{task_id}/documents/{code}/saved")
async def saved_document(task_id: uuid.UUID, code: str, user: UserDep, session: SessionDep) -> dict:
    """The saved copy of this document (for editing) and its edit log, if there is one."""
    task = await _task(session, task_id, user)
    inst = await _instance(session, task, code)
    if inst is None:
        return {"exists": False}
    return {"exists": True, **_instance_view(inst, await _edits(session, inst))}


@user_router.post("/tasks/{task_id}/documents/{code}/saved")
async def save_document(
    task_id: uuid.UUID, code: str, user: UserDep, session: SessionDep, draft: bool = False
) -> dict:
    """Keep a copy of the document so its wording can be adjusted online by hand."""
    from app.analysis import official_docx as od

    task = await _task(session, task_id, user)
    await session.scalar(select(AnalysisTask.id).where(AnalysisTask.id == task.id).with_for_update())
    inst = await _instance(session, task, code, for_update=True)
    if inst is None:
        cn_base = await _official_cn_base(session, task, code, draft=draft)
        if cn_base is not None:
            template = await latest_template(session, code)
            blocks = await asyncio.to_thread(od.docx_preview_blocks, cn_base[0])
            name_zh = cn_base[1]
            ver_no = template.version_no if template else 1
        else:
            template, rendered = await _prepare(
                session, task, code, draft=draft, write_draft=False, clean_names=True
            )
            if not draft and rendered.missing:
                raise HTTPException(
                    409, "还有信息需要补充：" + "、".join(q.label for q in rendered.missing)
                )
            if not draft and rendered.needs_paper:
                raise HTTPException(409, "论述部分还没有生成，请先点击“生成论述”")
            blocks = rendered.blocks
            _, eff_values, _ = await _inputs(session, task, code)
            name_zh = _effective_doc_name(code, template.name_zh, eff_values)
            ver_no = template.version_no
        inst = DocumentInstance(
            task_id=task.id,
            org_id=task.org_id,
            code=code,
            name_zh=name_zh,
            template_version=ver_no,
            blocks=blocks,
            created_by=user.user_id,
        )
        session.add(inst)
        await session.commit()
        await session.refresh(inst)
    return {"exists": True, **_instance_view(inst, await _edits(session, inst))}


@user_router.delete("/tasks/{task_id}/documents/{code}/saved")
async def reset_saved_document(
    task_id: uuid.UUID, code: str, request: Request, user: UserDep, session: SessionDep
) -> dict:
    """Discard manual online edits for this document and restore the auto-generated version."""
    from sqlalchemy import delete as sa_delete

    task = await _task(session, task_id, user)
    await session.scalar(select(AnalysisTask.id).where(AnalysisTask.id == task.id).with_for_update())
    inst = await _instance(session, task, code)
    if inst is not None:
        doc_name = inst.name_zh
        await session.execute(
            sa_delete(DocumentInstance).where(
                DocumentInstance.task_id == task.id, DocumentInstance.code == code
            )
        )
        await session.commit()
        set_detail(request, summary=f"恢复《{doc_name}》为自动生成版")
    return {"exists": False}


_ROLE_ONLY_LABELS = frozenset({
    "超级管理员",
    "平台管理员",
    "运维管理员",
    "企业用户",
    "普通查看者",
    "org-admin",
    "member",
    "admin",
    "ops",
    "viewer",
    "platform_admin",
    "ops_admin",
})


def _user_label(account: User | None) -> str:
    if account is None:
        return ""
    dn = (account.display_name or "").strip()
    if dn and dn not in _ROLE_ONLY_LABELS and dn.lower() not in _ROLE_ONLY_LABELS:
        return dn[:128]
    return (account.email.split("@")[0] if account.email else "")[:128]


@user_router.patch("/tasks/{task_id}/documents/{code}/saved")
async def edit_document(
    task_id: uuid.UUID, code: str, body: EditIn, request: Request, user: UserDep, session: SessionDep
) -> dict:
    """Change one piece of text and log who changed what, from what, to what."""
    task = await _task(session, task_id, user)
    await session.scalar(select(AnalysisTask.id).where(AnalysisTask.id == task.id).with_for_update())
    inst = await _instance(session, task, code, for_update=True)
    if inst is None:
        raise HTTPException(404, "请先保存一份文件副本再修改")
    blocks = copy.deepcopy(inst.blocks)
    try:
        place, before, after = apply_edit(
            blocks, body.text, block=body.block, item=body.item, row=body.row, col=body.col
        )
    except EditError as exc:
        raise HTTPException(422, str(exc)) from exc
    if before == after:
        return {"exists": True, **_instance_view(inst, await _edits(session, inst))}
    account = await session.get(User, user.user_id)
    inst.blocks = blocks
    inst.edit_count += 1
    session.add(
        DocumentEdit(
            instance_id=inst.id,
            user_id=user.user_id,
            user_name=_user_label(account),
            place=place,
            before=before,
            after=after,
        )
    )
    await session.commit()
    await session.refresh(inst)
    set_detail(request, summary=f"修改“{inst.name_zh}”：{place}", before=before, after=after)
    return {"exists": True, **_instance_view(inst, await _edits(session, inst))}


# 中国的官方原表是指南 Word 原件里的附件（不在 forms.json 的表格目录里），在这里登记
_CN_OFFICIAL = [
    {"code": "CN_CAC_ANNEX3", "country": "CN", "name_zh": "数据出境安全评估申报表", "authority_zh": "国家互联网信息办公室",
     "version": "《数据出境安全评估申报指南（第三版）》附件 3，2025-06-27", "file": "CN_CAC_security_assessment_guide_v3_2025-06-27.docx",
     "source_url": "https://www.cac.gov.cn/", "used_for": "数据从中国发出：汽车数据出境评估申报表"},
    {"code": "CN_CAC_ANNEX4", "country": "CN", "name_zh": "数据出境风险自评估报告", "authority_zh": "国家互联网信息办公室",
     "version": "《数据出境安全评估申报指南（第三版）》附件 4，2025-06-27", "file": "CN_CAC_security_assessment_guide_v3_2025-06-27.docx",
     "source_url": "https://www.cac.gov.cn/", "used_for": "数据从中国发出：汽车数据跨境风险评估报告"},
    {"code": "CN_CAC_SC", "country": "CN", "name_zh": "个人信息出境标准合同", "authority_zh": "国家互联网信息办公室",
     "version": "《个人信息出境标准合同办法》附件，2023-02", "file": "CN_CAC_standard_contract_2023-02.pdf",
     "source_url": "https://www.cac.gov.cn/2023-02/24/c_1678884830036813.htm", "used_for": "数据从中国发出：汽车数据跨境传输合规合同（范本全文）"},
    {"code": "CN_CAC_SC_ANNEX5", "country": "CN", "name_zh": "个人信息保护影响评估报告（出境版）", "authority_zh": "国家互联网信息办公室",
     "version": "《个人信息出境标准合同备案指南（第二版）》附件 5，2024-03-22", "file": "CN_CAC_standard_contract_filing_guide_v2_2024-03-22.docx",
     "source_url": "https://www.cac.gov.cn/", "used_for": "数据从中国发出：数据保护影响评估（DPIA）"},
    {"code": "EU_EDPB_DPIA_2026", "country": "EU", "name_zh": "数据保护影响评估（DPIA）标准范本", "authority_zh": "欧洲数据保护委员会（EDPB）",
     "version": "EDPB DPIA Template 2026 v1.0", "file": "EU_EDPB_DPIA_template_2026_v1_en.docx",
     "source_url": "https://www.edpb.europa.eu/", "used_for": "发出国无专用官方报告原表时：数据保护影响评估（DPIA）中英文双出（中文正式报告 + 欧盟标准英文范本代填）"},
]
_USED_FOR = {
    "export": "数据从该国发出：汽车数据出境评估申报表（即这张官方表）",
    "processing": "海外属地准入：该国数据处理申报/事先授权事项的官方表格",
    "equipment": "海外属地准入：该国通信设备型式认证/入网事项的官方表格",
    "vehicle": "海外属地准入：该国车辆型式认证/注册/上路事项的官方表格",
}


@admin_router.get("/official")
@admin_router.get("/official-forms")
async def admin_official_templates(_: PlatformAdminDep) -> list[dict]:
    """The official templates and forms the platform fills, as issued: country, issuing body,
    edition, source and where each is used. No official template for a route → the EU standard."""
    from app.analysis.document_basis import NAMES
    from app.forms.service import CatalogError, catalog

    try:
        forms = list(catalog().values())
    except CatalogError as exc:
        raise HTTPException(409, str(exc)) from exc
    rows = list(_CN_OFFICIAL) + [
        {
            "code": f["code"], "country": f["country"], "name_zh": f["name_zh"].split("：", 1)[-1], "authority_zh": f["authority_zh"],
            "version": f["version"], "file": f["file"], "source_url": f["source_url"], "retrieved": f.get("retrieved", ""),
            "used_for": _USED_FOR.get(f["direction"], f["direction"]),
        }
        for f in forms
    ]
    for r in rows:
        r["country_zh"] = NAMES.get(r["country"], r["country"])
    return rows


@admin_router.get("/official/{code}/original")
@admin_router.get("/official-forms/{code}/original")
async def admin_official_original(code: str, _: PlatformAdminDep) -> Response:
    """The archived original file of an official template (trailing 0-char blank page removed if present)."""
    from app.analysis.official_docx import OFFICIAL_DIR

    row = next((r for r in await admin_official_templates(_) if r["code"] == code and r["file"]), None)
    if row is None:
        raise HTTPException(404, "没有这份官方表格的原件")
    path = OFFICIAL_DIR / row["file"]
    raw_bytes = path.read_bytes()
    if path.suffix == ".pdf" and code == "TN_ANCE_CRYPTO":
        from app.forms.pdf_fill import strip_trailing_blank_pdf_pages

        raw_bytes = strip_trailing_blank_pdf_pages(raw_bytes)
    media = "application/pdf" if path.suffix == ".pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return Response(raw_bytes, media_type=media, headers={"Content-Disposition": content_disposition(named(row["country_zh"], row["name_zh"], ext=path.suffix.lstrip(".")))})


@admin_router.get("")
async def admin_list(_: PlatformAdminDep, session: SessionDep) -> list[dict]:
    await ensure_default_templates(session)
    rows = (
        await session.scalars(
            select(DocumentTemplate).order_by(DocumentTemplate.code, DocumentTemplate.version_no)
        )
    ).all()
    return [
        {
            "code": r.code,
            "version_no": r.version_no,
            "name": r.name_zh,
            "status": r.status,
            "customized": r.created_by is not None,
            "updated_at": r.created_at.isoformat() if r.created_by is not None and r.created_at else None,
        }
        for r in rows
    ]


@admin_router.get("/{code}/versions")
async def admin_list_versions(code: str, _: PlatformAdminDep, session: SessionDep) -> list[dict]:
    """Read-only version history list for a single compliance document template."""
    await ensure_default_templates(session)
    rows = (
        await session.scalars(
            select(DocumentTemplate)
            .where(DocumentTemplate.code == code)
            .order_by(DocumentTemplate.version_no.desc())
        )
    ).all()
    if not rows:
        raise HTTPException(404, "没有这种合规文件")
    max_ver = rows[0].version_no
    return [
        {
            "code": r.code,
            "version_no": r.version_no,
            "name": r.name_zh,
            "status": r.status,
            "is_current": r.version_no == max_ver,
            "customized": r.created_by is not None,
            "updated_at": r.created_at.isoformat() if r.created_by is not None and r.created_at else None,
            "sections_count": len((r.definition or {}).get("sections") or []),
            "facts_count": len((r.definition or {}).get("facts") or []),
            "definition": r.definition,
        }
        for r in rows
    ]


class VerdictRulesIn(BaseModel):
    rules: list[dict[str, Any]] = Field(min_length=1, max_length=12)


@admin_router.get("/verdict-rules")
async def admin_get_verdict_rules(_: PlatformAdminDep, session: SessionDep) -> dict:
    """List standard verdict templates across the 6 assessment and tracking documents."""
    from app.analysis import verdict_rules

    await ensure_default_templates(session)
    items: list[dict[str, Any]] = []
    for code in verdict_rules.VERDICT_DOC_CODES:
        row = await latest_template(session, code)
        defn = row.definition if row else {}
        items.append(
            {
                "code": code,
                "name": row.name_zh if row else verdict_rules.VERDICT_DOC_NAMES.get(code, code),
                "version_no": row.version_no if row else 1,
                "customized": bool(defn and isinstance(defn.get("verdict_rules"), list)),
                "rules": verdict_rules.get_effective_rules(code, defn),
            }
        )
    return {"tokens": list(verdict_rules.AVAILABLE_TOKENS), "items": items}


@admin_router.put("/verdict-rules/{code}")
async def admin_save_verdict_rules(
    code: str, body: VerdictRulesIn, request: Request, user: PlatformAdminDep, session: SessionDep
) -> dict:
    """Save deterministic verdict rule templates onto a new template version."""
    from app.analysis import verdict_rules

    await ensure_default_templates(session)
    row = await latest_template(session, code)
    if row is None:
        raise HTTPException(404, "没有这种合规文件")
    try:
        cleaned = verdict_rules.validate_verdict_rules(code, body.rules)
        new_def = copy.deepcopy(row.definition or {})
        new_def["verdict_rules"] = cleaned
        saved = await save_template(
            session, code, row.name_zh, new_def, uuid.UUID(str(user.user_id))
        )
    except TemplateError as exc:
        raise HTTPException(422, str(exc)) from exc
    await session.commit()
    from app.analysis import doc_projects as _dp

    _dp._OVERVIEW_PROJECT_CACHE.clear()
    set_detail(request, summary=f"更新标准判定结论范本：{row.name_zh}（第 {saved.version_no} 版）")
    return {"code": saved.code, "version_no": saved.version_no, "rules": cleaned}


@admin_router.get("/{code}")
async def admin_get(code: str, _: PlatformAdminDep, session: SessionDep) -> dict:
    await ensure_default_templates(session)
    row = await latest_template(session, code)
    if row is None:
        raise HTTPException(404, "没有这种合规文件")
    return {
        "code": row.code,
        "version_no": row.version_no,
        "name": row.name_zh,
        "definition": row.definition,
    }


@admin_router.put("/{code}")
async def admin_save(
    code: str, body: TemplateIn, user: PlatformAdminDep, session: SessionDep
) -> dict:
    """Saving creates the next version; documents already made keep the version they used."""
    try:
        row = await save_template(
            session, code, body.name_zh, body.definition, uuid.UUID(str(user.user_id))
        )
    except TemplateError as exc:
        raise HTTPException(422, str(exc)) from exc
    await session.commit()
    from app.analysis import doc_projects as _dp

    _dp._OVERVIEW_PROJECT_CACHE.clear()
    return {"code": row.code, "version_no": row.version_no}
