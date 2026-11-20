# ruff: noqa: E501
"""Overseas market admission: the entry library, an organisation's projects, the progress ledger and
the material drafts. Entries are platform-wide and read-only for users; everything a user writes
belongs to their organisation."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admission import engine, filing, foreign_letter, plan, products
from app.admission.material_kinds import plan_of
from app.admission.models import (
    AdmissionDraft,
    AdmissionEntry,
    AdmissionIssue,
    AdmissionProject,
    AdmissionStep,
)
from app.analysis.document_templates import DOC_FACT_PREFIX, Rendered, build_docx, build_pdf
from app.analysis.models import AnalysisTask
from app.analysis.report_exporter import content_disposition, named
from app.audit.service import set_detail
from app.core.db import get_session
from app.iam.deps import Principal, require_platform_admin, require_user_side

user_router = APIRouter(prefix="/api/admission", tags=["admission"])
admin_router = APIRouter(prefix="/api/admin/admission", tags=["admission"])

UserDep = Annotated[Principal, Depends(require_user_side)]
AdminDep = Annotated[Principal, Depends(require_platform_admin)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]

GENERIC_STEPS = ("准备申请材料", "向受理机构提交申请", "取得受理结果")  # 条文没写具体环节时的通用环节
SHOWN = ("pending_review", "approved")


class ProjectIn(BaseModel):
    entry_id: uuid.UUID
    task_id: uuid.UUID | None = None
    function: str = Field(default="", max_length=32)
    title: str = Field(default="", max_length=256)
    applicant: str = Field(default="", max_length=256)
    vehicle_model: str = Field(default="", max_length=256)


class AnswersIn(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict, max_length=20)


class CustomStepIn(BaseModel):
    title_zh: str = Field(min_length=2, max_length=256)


class ReviewIn(BaseModel):
    review_status: Literal["approved", "pending_review", "out_of_scope"] | None = None
    unmerge: bool = False  # 撤销合并：这一项重新单独显示给用户


def _entry_out(e: AdmissionEntry, steps: int | None = None) -> dict:
    out = {
        "id": str(e.id),
        "country_code": e.country_code,
        "name_zh": e.name_zh,
        "category": e.category,
        "authority_zh": e.authority_zh,
        "summary_zh": e.summary_zh,
        "review_status": e.review_status,
        "merged_into": str(e.merged_into) if e.merged_into else None,
        "products": products.products_of(e.category, e.name_zh),
        "products_zh": (e.profile or {}).get("applies_to") or products.label(products.products_of(e.category, e.name_zh)),
    }
    if steps is not None:
        out["step_count"] = steps
    return out


async def _named(session: AsyncSession, outs: list[dict]) -> list[dict]:
    """Show the receiving body by its proper name: an extracted “委员会（Commission）” or “Instance”
    is the law's short form for a body the library knows (merged the same way as on the country page)."""
    from app.knowledge.whitepaper_graph import authority_resolver

    resolvers: dict = {}
    for o in outs:
        cc = o["country_code"]
        if cc not in resolvers:
            resolvers[cc] = await authority_resolver(session, cc)
        o["authority_zh"] = canonical_authority(cc, resolvers[cc](o.get("authority_zh") or ""))
    return outs


_PORTALS_CACHE: list[dict] | None = None
_BASIS_MEMO: dict[str, dict | None] = {}
_LISTED_MEMO: dict[str, bool] = {}
_DETAIL_MEMO: dict[tuple[str, str], dict] = {}


def _portals_list() -> list[dict]:
    global _PORTALS_CACHE
    if _PORTALS_CACHE is None:
        import json as _json
        from pathlib import Path as _Path

        _PORTALS_CACHE = _json.loads((_Path(__file__).with_name("portals.json")).read_text(encoding="utf-8"))["portals"]
    return _PORTALS_CACHE


def canonical_authority(cc: str, text_: str) -> str:
    """One name per body across the site: a text naming a single body that the verified portal
    catalogue knows (by any of its names) is written with the catalogue's standard name."""
    if not text_ or re.search(r"[；;、/]|或|与", text_):
        return text_
    for p in _portals_list():
        if p["country"] == cc and any(a in text_ for a in p["aliases"]):
            return p["authority_zh"]
    return text_


async def _basis(session: AsyncSession, clause_id) -> dict | None:
    if clause_id is None:
        return None
    from app.knowledge.cache_sync import keeper_active, rebuilding, use_cached

    key = str(clause_id)
    s_cache = session.info.setdefault("_adm_basis", {})
    if key in s_cache and not rebuilding.get():
        val = s_cache[key]
        return dict(val) if val is not None else None
    if use_cached(key in _BASIS_MEMO):
        val = _BASIS_MEMO[key]
        return dict(val) if val is not None else None

    row = (
        await session.execute(
            text(
                """select k.id::text, k.clause_number, coalesce(d.title_zh, d.title), d.id::text, d.title, d.document_type, d.instrument_kind
                from clauses k
                join document_versions v on v.id=k.version_id
                join documents d on d.id=v.document_id
                where k.id=:i
                  and d.relevance not in ('irrelevant', 'out_of_scope')
                  and v.legal_status not in ('repealed', 'draft')
                  and v.review_status <> 'rejected'
                  and not exists (
                    select 1 from knowledge_change_proposals p
                    where p.target_type = 'documents' and p.target_id = d.id
                      and p.status in ('pending_review', 'approved')
                      and coalesce((p.payload->>'safety_hold')::boolean, false)
                  )
                  and not exists (
                    select 1 from knowledge_change_proposals p
                    where p.target_type = 'clauses'
                      and p.status in ('pending_review', 'approved')
                      and p.payload->'source_parse_revision'->>'scope' = 'whole_source_page'
                      and (
                        p.target_id = k.id
                        or (
                          p.payload->'source_parse_revision'->>'version_id' = k.version_id::text
                          and (p.payload->'source_parse_revision'->>'page_number')::int >= k.page_start
                          and (p.payload->'source_parse_revision'->>'page_number')::int <= k.page_end
                        )
                      )
                  )"""
            ),
            {"i": clause_id},
        )
    ).first()
    if row is None:
        s_cache[key] = None
        if keeper_active or rebuilding.get():
            _BASIS_MEMO[key] = None
        return None
    from app.knowledge.reading import article_label

    # 依据写成“《中文名》第X条”，与法规浏览、简报、合规文件一致
    label = article_label(row[1] or "")
    from app.admission import names

    name = names.law_name(row[3], row[2])
    zh_rows = list(
        (
            await session.scalars(
                text(
                    "select translated_text from clause_translations "
                    "where clause_id=:i and is_current and quality_check_passed "
                    "and review_status not in ('translation_failed','not_translated','rejected','stale','superseded') "
                    "order by updated_at desc limit 2"
                ),
                {"i": clause_id},
            )
        ).all()
    )
    zh = zh_rows[0] if len(zh_rows) == 1 else ""
    res = {
        "clause_id": row[0], "clause_number": label or row[1], "document": name, "document_id": row[3],
        "original_title": row[4], "source": f"《{name}》{label}",
        # 法律、法令、部令算“依据法规”；办事指南、说明类只作“参考：官方办事说明”
        "is_law": names.is_law(row[3], row[5], row[6]),
        "zh": zh or "",
    }
    s_cache[key] = res
    if keeper_active or rebuilding.get():
        _BASIS_MEMO[key] = res
    return dict(res)


LAW_TYPES = ("LAW", "REGULATION")
_CITED = re.compile(r"(\d{4})\s*年\s*第\s*(\d+)\s*号|No\.?\s*\(?(\d+)\)?\s*(?:of|/)\s*(\d{4})", re.IGNORECASE)


async def _cited_laws(session: AsyncSession, cc: str, cited: list[str]) -> list[dict]:
    """The laws a guide says the item rests on (“1982 年第 121 号法律”), linked to the library when the
    law is collected there, else named as written."""
    from app.ingestion.models import NOT_CITABLE_STATUSES, Document, DocumentVersion
    from app.ingestion.safety import document_available

    valid_ver = (
        select(DocumentVersion.id)
        .where(
            DocumentVersion.document_id == Document.id,
            DocumentVersion.state == "parsed",
            DocumentVersion.legal_status.notin_(NOT_CITABLE_STATUSES),
            DocumentVersion.review_status != "rejected",
        )
        .correlate(Document)
        .exists()
    )
    docs = (
        await session.execute(
            select(Document.id, Document.title, Document.title_zh).where(
                Document.country_code == cc,
                Document.document_type.in_(LAW_TYPES),
                Document.relevance.in_(("relevant", "related")),
                document_available(),
                valid_ver,
            )
        )
    ).all()
    out, seen = [], set()
    for c in cited:
        m = _CITED.search(c)
        hit = None
        if m:
            year, num = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
            pat = [re.compile(rf"(?<!\d){num}(?!\d)"), re.compile(year)]
            cands = [d for d in docs if all(x.search(d[1] or "") or x.search(d[2] or "") for x in pat)]
            hit = min(cands, key=lambda d: len(d[1] or "")) if cands else None
        if hit and str(hit[0]) in seen:
            continue
        if hit:
            seen.add(str(hit[0]))
            from app.admission import names

            # 名称从法规名称表取（“名称（年份+编号）”），和依据法规、推送里的写法一致
            out.append({"source": f"《{names.law_name(str(hit[0]), hit[2] or hit[1])}》", "link": f"/knowledge?country={cc}&doc={hit[0]}"})
        else:
            out.append({"source": c, "link": None})
    return out


async def legal_basis(session: AsyncSession, entry: AdmissionEntry, basis: dict | None) -> tuple[list[dict], dict | None]:
    """(依据法规, 参考：官方办事说明). The item's own clause is the basis when it is a law; when it is an
    authority's guide, the guide is the reference and the laws it names (or an administrator lists)
    are the basis."""
    from app.admission import names
    from app.ingestion.models import Document

    cc = entry.country_code
    card = _basis_card(basis, cc)
    prof = entry.profile or {}
    if "profile:cited_laws" in (entry.edited or []):  # 管理员列的为准
        laws = await _cited_laws(session, cc, prof.get("cited_laws") or [])
    else:
        from app.ingestion.models import NOT_CITABLE_STATUSES, DocumentVersion
        from app.ingestion.safety import document_is_held

        laws = []
        for did in prof.get("law_ids") or []:
            d = await session.get(Document, uuid.UUID(did))
            if (
                d is None
                or d.country_code != cc
                or d.relevance in ("irrelevant", "out_of_scope")
                or await document_is_held(session, d.id)
            ):
                continue
            lver = await session.scalar(
                select(DocumentVersion)
                .where(
                    DocumentVersion.document_id == d.id,
                    DocumentVersion.state == "parsed",
                    DocumentVersion.legal_status != "draft",
                    DocumentVersion.review_status != "rejected",
                )
                .order_by(DocumentVersion.version_no.desc())
                .limit(1)
            )
            if lver is None or lver.legal_status in NOT_CITABLE_STATUSES:
                continue
            laws.append({"source": f"《{names.law_name(did, d.title_zh or d.title)}》", "link": f"/knowledge?country={cc}&doc={did}"})
    if basis and basis.get("is_law"):
        own = card["link"].split("&clause")[0]
        return [card] + [x for x in laws if x["link"] != own], None
    return laws, card


def clean_quote(text_: str | None) -> str:
    """Clause text as shown: the PDF's private bullet glyphs (shown as empty boxes) become “•”."""
    return re.sub(r"[\uE000-\uF8FF]", "•", text_ or "")


ARABIC = re.compile(r"[\u0600-\u06FF]")


def display_authority(text_: str, cc: str = "") -> str:
    """The body as cards show it: Chinese name (English abbreviation); words in Arabic script go to
    the detail only (“中心（المركز）” → “中心”, “الهيئة（电信监管机构）” → “电信监管机构”)."""
    from app.admission import names

    hit = names.body(cc, text_) if cc else None
    if hit is not None:
        return names.normalize_body(hit, cc)
    t = re.sub(r"[（(][^）)]*[\u0600-\u06FF][^）)]*[）)]", "", text_ or "").strip()
    if ARABIC.search(t):
        inner = re.findall(r"[（(]([^）)]*)[）)]", t)
        t = "；".join(x for x in inner if not ARABIC.search(x)) or re.sub(r"[\u0600-\u06FF\s]+", "", t)
    t = re.sub(r"\s*/\s*", "、", t).strip("；、 ")
    # 写的是职位的，换成所属机构（部长 → 部，司长 → 司，法官 → 法院）
    t = re.sub(r"部长(?!会议)", "部", t)
    return names.normalize_body(re.sub(r"司长", "司", re.sub(r"法官", "法院", t)), cc)


def authority_short(detail: dict) -> str:
    """The receiving body for cards and lists: the named body, else the body the law names as
    supervisor, or “条文未写明”. Why it is that body when the clause does not say is in
    ``authority_why``."""
    name = detail.get("authority_zh") or ""
    cc = detail.get("country_code", "")
    shown = display_authority(name, cc) if name and not detail.get("authority_unnamed") else ""
    if not shown and detail.get("authority_hint"):
        shown = display_authority(detail["authority_hint"], cc)
    return shown or "条文未写明"


def authority_why(detail: dict) -> str:
    """One sentence on how the body was found when the clause does not name it (detail page)."""
    note = (detail.get("authority_note") or "").strip()
    if note:
        return f"递交前向受理机构确认（{note.removeprefix('条文未写明受理机构，').rstrip('。')}）"
    unnamed = not (detail.get("authority_zh") or "").strip() or detail.get("authority_unnamed")
    if not unnamed or not detail.get("authority_hint"):
        return ""
    doc = (detail.get("basis") or {}).get("document") or "所依据法规"
    how = detail.get("authority_hint_from")
    if how == "law":
        return f"递交前向受理机构确认（按《{doc}》其他条文写明的受理机构确定）"
    if how == "category":
        return "递交前向受理机构确认（按同类法规写明的受理机构确定）"
    return f"递交前向受理机构确认（按《{doc}》点名的主管机构确定）"


async def _form_version_notes(session: AsyncSession, entry: AdmissionEntry, forms: list[dict]) -> None:
    """A form whose edition is older than the latest law the item rests on may be superseded:
    say so on the form (“2024 年版，早于 2026 年修法……”)."""
    from app.ingestion.models import Document

    years = []
    for did in (entry.profile or {}).get("law_ids") or []:
        d = await session.get(Document, uuid.UUID(did))
        m = re.search(r"(?<!\d)(19|20)\d{2}(?!\d)", f"{d.title_zh or ''} {d.title}") if d is not None and d.document_type in LAW_TYPES else None
        if m:
            years.append(int(m.group(0)))
    latest = max(years, default=0)
    for f in forms:
        m = re.search(r"((19|20)\d{2})", str(f.get("version") or ""))  # “第五版 2024-01”“2024”
        if m and latest and int(m.group(1)) < latest:
            f["version_note"] = f"{m.group(1)} 年版，早于 {latest} 年修法，递交前向受理机构确认是否为现行版本"


def entry_forms(entry: AdmissionEntry) -> list[dict]:
    """The official forms (templates) for this admission item, from the forms catalogue by the
    form's own metadata — no per-country rules: a form the item's text names (“F-112”) first; else
    by what the item is about — a transfer abroad takes the country's export form, a prior
    authorisation the form for authorisation (sensitive data), a declaration the ordinary one."""
    import re as _re

    from app.forms.service import CatalogError, catalog, summary

    try:
        forms = [f for f in catalog().values() if f["country"] == entry.country_code]
    except CatalogError:
        return []
    chosen = (getattr(entry, "profile", None) or {}).get("form_codes")
    if chosen is not None:  # 管理员定了这一项用哪几张官方表格
        by_code = {f["code"]: f for f in forms}
        return [{**{k: summary(by_code[c])[k] for k in ("code", "name_zh", "authority_zh", "version")}, "applies_when": by_code[c].get("applies_when_zh", "")} for c in chosen if c in by_code]
    if not forms:
        return []
    text = f"{entry.name_zh} {entry.summary_zh}"
    flat = _re.sub(r"[\s\-_.]", "", text).upper()

    def code_of(f):
        short = f["name_zh"].split("：", 1)[-1].rpartition("（")[2].rstrip("）")
        return _re.sub(r"[\s\-_.]", "", short.split()[-1] if short else "").upper()

    upper = text.upper()

    def mentions(c: str) -> bool:
        # 表格简称要单独出现，不能是别的词的一部分（“ANCE”不能从“Instance”里认出来）
        return bool(_re.search(r"(?<![A-Z0-9])" + r"[\s\-_.]?".join(map(_re.escape, c)) + r"(?![A-Z0-9])", upper))

    def authority_only(f, c: str) -> bool:
        # 简称就是受理机构的简称（“TRA”“INPDP”）：事项里提到它只说明谁受理，不是点名这份表
        return c in _re.sub(r"[\s\-_.]", "", f.get("authority_zh") or "").upper()

    named = [f for f in forms if (c := code_of(f)) and len(c) >= 3 and c in flat and mentions(c) and not authority_only(f, c)]
    # 由原件读出的表格自带“适用于哪些事项”（matches_zh，对事项名称的表达式），再按表格类别把关
    name_ = entry.name_zh or ""
    about_data = bool(_re.search(r"数据|处理|生物识别|面部识别|视频监控", name_))
    abroad = bool(_re.search(r"跨境|境外|出境|国外|另一国|转移至|传输至", name_))
    follow_up = bool(_re.search(r"经营|安装|运营|转让|续期|控制权|破封|封条", name_)) and not _re.search(r"型式|型号|类型", name_)

    def fits(f) -> bool:
        if f["direction"] == "processing":
            return about_data and not abroad
        # 设备表不给车辆事项、车辆表不给通信设备事项（“型式”两类事项名里都有）
        if f["direction"] == "equipment" and getattr(entry, "category", "") == "vehicle_approval":
            return False
        if f["direction"] == "vehicle" and getattr(entry, "category", "") == "telecom_approval":
            return False
        if f["direction"] in ("equipment", "vehicle"):
            return not follow_up
        return True

    by_rule = [f for f in forms if f.get("matches_zh") and _re.search(f["matches_zh"], name_) and fits(f)]
    forms = [f for f in forms if not f.get("matches_zh")]
    if named:
        picked = named
    # 按事项名称判断它是哪一类（说明里常顺带提到另一类，如“不属于需授权情形时申报”）
    elif _re.search(r"跨境|境外|出境|国外|另一国|转移至|传输至", name := entry.name_zh):
        picked = [f for f in forms if f["direction"] == "export"]
    elif _re.search(r"申报|备案|登记", name) and "处理" in name:
        picked = [f for f in forms if f["direction"] == "processing" and f.get("applies_if") != "sensitive"]
    elif _re.search(r"授权|许可|批准", name) and "处理" in name:
        picked = [f for f in forms if f["direction"] == "processing" and f.get("applies_if") != "not_sensitive"]
    else:
        picked = []
    picked = [*picked, *(f for f in by_rule if f not in picked)]
    return [{**{k: summary(f)[k] for k in ("code", "name_zh", "authority_zh", "version")}, "applies_when": f.get("applies_when_zh", "")} for f in picked]


def entry_kind(entry: AdmissionEntry) -> str:
    """What the item is, from its own name: export (transfer abroad), declaration, authorisation."""
    import re as _re

    name = entry.name_zh or ""
    if _re.search(r"跨境|境外|出境|国外|另一国|转移至|传输至", name):
        return "export"
    if _re.search(r"申报|备案|登记", name):
        return "declaration"
    if _re.search(r"授权|许可|批准|认证", name):
        return "authorisation"
    return ""


def entry_portal(entry: AdmissionEntry) -> dict | None:
    """Where to apply: the authority's official site and the page for this kind of filing, from
    the verified portal catalogue (portals.json) by country and authority name."""
    import json as _json
    from pathlib import Path as _Path

    own = (getattr(entry, "profile", None) or {}).get("portal")
    if own and own.get("site"):  # 管理员填的递交入口
        return {"authority_zh": display_authority(entry.authority_zh or "", entry.country_code), "site": own["site"], "links": own.get("links") or [], "verified": "管理员填写"}
    data = _json.loads((_Path(__file__).with_name("portals.json")).read_text(encoding="utf-8"))
    authority = entry.authority_zh or ""
    for p in data["portals"]:
        if p["country"] != entry.country_code:
            continue
        if not any(a in authority for a in p["aliases"]):  # 受理机构不明的事项不猜入口
            continue
        kind = entry_kind(entry)
        links = [link for link in p["links"] if link["kind"] == kind] or [link for link in p["links"] if link["kind"] == "any"]
        return {"authority_zh": p["authority_zh"], "site": p["site"], "links": links, "verified": p["verified"]}
    return None


async def _authority_hint(session: AsyncSession, entry: AdmissionEntry, document_id: uuid.UUID) -> tuple[str, str]:
    """Who receives an application whose own clause does not name the body: (body, how).
    First what the other admission items of the same law name ("law"), then those of the same
    country's laws of the same business category ("category") — they usually state the body
    outright — and only then the supervisory body the graph finds most often in that law or category
    (named in at least two clauses)."""
    from collections import Counter

    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge.whitepaper_graph import UNNAMED_ROLE, authority_resolver, regulator_of_law

    doc = await session.get(Document, document_id)
    rows = (
        await session.execute(
            select(AdmissionEntry.authority_zh, Document.id)
            .join(Clause, Clause.id == AdmissionEntry.evidence_clause_id)
            .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                AdmissionEntry.country_code == entry.country_code,
                AdmissionEntry.id != entry.id,
                AdmissionEntry.authority_zh != "",
                Document.business_topic == (doc.business_topic if doc else None) if doc and doc.business_topic else Document.id == document_id,
            )
        )
    ).all()
    resolve = await authority_resolver(session, entry.country_code)

    def named(text_: str) -> str:
        full = resolve(text_)
        return "" if UNNAMED_ROLE.search(re.sub(r"[（(][^）)]*[）)]", "", full).strip()) or re.search(r"[/／；;、]|或|与", full) else full

    for how, pick in (("law", [a for a, d in rows if d == document_id]), ("category", [a for a, _ in rows])):
        counts = Counter(n for n in (named(a) for a in pick) if n)
        if counts:
            top, n = counts.most_common(1)[0]
            # 同一部法规里写明的直接用；同类法规的只在明显是同一个机构时才给（至少两项、六成以上）
            if how == "law" or (n >= 2 and n >= 0.6 * sum(counts.values())):
                return top, how
    return await regulator_of_law(session, entry.country_code, document_id, at_least=2)


LANG_ZH = {"fr": "法语", "ar": "阿拉伯语", "en": "英语", "zh": "中文"}


def entry_profile(entry: AdmissionEntry, forms: list[dict]) -> dict:
    """The card's fixed facts: what the law says (item_profile), else “待核实”; the application
    language falls back to the language of the item's official form."""
    from app.admission.item_profile import FIELDS, UNKNOWN
    from app.forms.service import get_form

    out = {f: (entry.profile or {}).get(f) or UNKNOWN for f in FIELDS}
    if getattr(entry, "category", "") not in ("vehicle_approval", "telecom_approval", "av_testing") and out["local_test"] == UNKNOWN:
        out["local_test"] = "不适用"  # 登记类、数据类事项没有检测
    # 栏目里夹着“待核实”的（“待核实：报告由谁出具未明确”）：用户看不出谁核实，统一写“条文未写明”
    out = {f: (v if v == UNKNOWN else v.replace(UNKNOWN, "条文未写明")) for f, v in out.items()}
    if out["language"] == UNKNOWN and forms:
        langs = []
        for f in forms:
            form = get_form(f["code"]) or {}
            src = form.get("src", "") + form.get("file", "")
            lang = form.get("language", "")
            for code in ([lang] + (["ar"] if "_AR" in src.upper() and lang != "ar" else [])):
                if code in LANG_ZH and LANG_ZH[code] not in langs:
                    langs.append(LANG_ZH[code])
        if langs:
            out["language"] = "/".join(langs) + "（按官方表格）"
    for fk, fv in list(out.items()):
        if isinstance(fv, str) and fv and fv != UNKNOWN:
            cleaned = re.sub(r"[（(]见官方程序表\s*\d+[）)]", "", fv)
            cleaned = re.sub(r"^按官方表格[：:]\s*(.+)$", r"\1（按官方表格）", cleaned)
            cleaned = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff（(])", "", cleaned)
            cleaned = re.sub(r"(?<=[）)])[ \t]+(?=[\u4e00-\u9fff])", "", cleaned)
            cleaned = re.sub(r"[ \t]+([：:，。；])", r"\1", cleaned)
            out[fk] = cleaned.strip()
    return out


async def _entry_detail(session: AsyncSession, entry: AdmissionEntry) -> dict:
    import copy as _copy

    from app.knowledge.cache_sync import keeper_active, rebuilding, use_cached

    d_key = (str(entry.id), str(getattr(entry, "updated_at", "") or ""))
    s_cache = session.info.setdefault("_adm_entry_detail", {})
    if d_key in s_cache and not rebuilding.get():
        return _copy.deepcopy(s_cache[d_key])
    if use_cached(d_key in _DETAIL_MEMO):
        return _copy.deepcopy(_DETAIL_MEMO[d_key])

    steps = (
        (
            await session.execute(
                select(AdmissionStep)
                .where(AdmissionStep.entry_id == entry.id)
                .order_by(AdmissionStep.seq)
            )
        )
        .scalars()
        .all()
    )
    out = (await _named(session, [_entry_out(entry, len(steps))]))[0]
    out["evidence_quote"] = clean_quote(entry.evidence_quote)
    out["forms"] = entry_forms(entry)  # 该事项的官方表格模板（有的国家有）
    await _form_version_notes(session, entry, out["forms"])
    out["portal"] = entry_portal(entry)  # 官方办理入口（已核实的才给）
    out["profile"] = entry_profile(entry, out["forms"])  # 固定栏目：本地测试、本地代表、申请语言、有效期
    out["basis"] = await _basis(session, entry.evidence_clause_id)
    out["authority_hint"], out["authority_hint_from"] = "", ""
    from app.knowledge.whitepaper_graph import UNNAMED_ROLE

    unnamed = bool(UNNAMED_ROLE.search(re.sub(r"[（(][^）)]*[）)]", "", out["authority_zh"] or "").strip()))
    out["authority_unnamed"] = unnamed  # 条文只写“主管机关”这类角色，没点名机构
    if (unnamed or not (out["authority_zh"] or "").strip()) and out["basis"]:
        # 条文没写受理部门：给出同一法规/同类法规写明的机构，或所依据法规里点名的监管机构
        out["authority_hint"], out["authority_hint_from"] = await _authority_hint(session, entry, uuid.UUID(out["basis"]["document_id"]))
    from app.admission.dedupe import merged_bases

    # 并入这一项的同一办理事项：它们的条文也是这件事的依据
    extra = [await _basis(session, c) for c in await merged_bases(session, entry.id) if c != entry.evidence_clause_id]
    seen = {out["basis"]["clause_id"]} if out["basis"] else set()
    out["other_bases"] = [b for b in extra if b and not (b["clause_id"] in seen or seen.add(b["clause_id"]))]
    out["steps"] = [
        {
            "id": str(s.id),
            "seq": s.seq,
            "title_zh": s.title_zh,
            "detail_zh": s.detail_zh,
            "materials": s.materials or [],
            "evidence_quote": clean_quote(s.evidence_quote),
            "clause_id": str(s.evidence_clause_id) if s.evidence_clause_id else None,
            "edited": bool(s.edited),
        }
        for s in steps
    ]
    out["edited"] = list(entry.edited or [])
    out["authority_note"] = (entry.profile or {}).get("authority_note", "")
    out["materials_plan"] = plan_of(entry, [m for s in steps for m in (s.materials or [])])
    out["steps_generic"] = not steps
    out["authority_short"] = authority_short(out)
    out["authority_why"] = authority_why(out)
    out["authority_original"] = out["authority_zh"] if ARABIC.search(out["authority_zh"] or "") else ""
    out["authority_roles"] = (entry.profile or {}).get("authority_roles", "")
    out["laws"], out["guide"] = await legal_basis(session, entry, out["basis"])
    out["amount_conflicts"] = await amount_conflicts(session, entry, steps)
    out["user_shown"] = user_shown(out)
    from app.analysis.clause_gate import cut, why_unusable

    zh = (out["basis"] or {}).get("zh", "")
    out["evidence_flag"] = why_unusable(zh, (out["basis"] or {}).get("original_title"), entry.evidence_quote) if zh else "缺中文译文"
    out["evidence_zh"] = "" if out["evidence_flag"] else cut(zh, 600)
    out["conditions"] = list((entry.profile or {}).get("conditions") or [])
    out["cited_laws"] = list((entry.profile or {}).get("cited_laws") or [])
    out["form_codes"] = (entry.profile or {}).get("form_codes")
    forms_ = [f["name_zh"].split("：", 1)[-1] for f in out["forms"]]
    out["route"] = engine.route_steps(out, forms_)
    out["checklist"] = engine.checklist_rows(entry, out["materials_plan"], forms_, "平台生成")
    if not steps:
        # 条文只规定须办理、没写环节：给出通用的三个环节（与办理台账一致），写明按什么准备、向谁提交
        to = out["authority_zh"] or out["authority_hint"] or "该国主管部门"
        out["steps"] = [
            {"id": f"generic-{i}", "seq": i, "title_zh": t, "detail_zh": d, "materials": [], "evidence_quote": q}
            for i, (t, d, q) in enumerate(
                [
                    (GENERIC_STEPS[0], f"按条文要求准备：{entry.summary_zh}", entry.evidence_quote or ""),
                    (GENERIC_STEPS[1], f"向{to}提交申请；条文没写具体方式的，先向其确认受理方式和所需表格。", ""),
                    (GENERIC_STEPS[2], "取得许可、授权或受理回执后再开展相关业务；被要求补正的，修改材料后重新递交。", ""),
                ],
                1,
            )
        ]
    s_cache[d_key] = out
    if keeper_active or rebuilding.get():
        _DETAIL_MEMO[d_key] = out
    return _copy.deepcopy(out)


@user_router.get("/functions")
async def list_functions(user: UserDep) -> list[dict]:
    """准入页“选择业务”的选项：与事项、项目上显示的业务名同一张表。"""
    return [{"key": k, "label": v} for k, v in plan.FUNCTIONS.items()]


async def _admission_countries(session: AsyncSession, *, admin_view: bool = False) -> list[dict[str, str]]:
    """海外属地国家列表：已入库有效法规或已收录有效准入事项的国家（排除中国与国际），按试点顺序排前。"""
    from app.ingestion.models import NOT_CITABLE_STATUSES, Document, DocumentVersion
    from app.ingestion.safety import document_available

    valid_ver = (
        select(DocumentVersion.id)
        .where(
            DocumentVersion.document_id == Document.id,
            DocumentVersion.state == "parsed",
            DocumentVersion.legal_status.notin_(NOT_CITABLE_STATUSES),
            DocumentVersion.review_status != "rejected",
        )
        .correlate(Document)
        .exists()
    )
    doc_stmt = select(Document.country_code).where(
        Document.relevance.notin_(("out_of_scope", "irrelevant")),
        document_available(),
    )
    if not admin_view:
        doc_stmt = doc_stmt.where(valid_ver)
    doc_codes = {
        c.upper()
        for c in (await session.scalars(doc_stmt.distinct())).all()
        if c
    }
    if admin_view:
        entry_codes = {
            c.upper()
            for c in (
                await session.scalars(
                    select(AdmissionEntry.country_code)
                    .where(AdmissionEntry.review_status != "out_of_scope", AdmissionEntry.merged_into.is_(None))
                    .distinct()
                )
            ).all()
            if c
        }
    else:
        cand_entries = (
            await session.scalars(
                select(AdmissionEntry).where(
                    AdmissionEntry.review_status.in_(SHOWN),
                    AdmissionEntry.merged_into.is_(None),
                )
            )
        ).all()
        entry_codes = {
            e.country_code.upper()
            for e in cand_entries
            if e.country_code and await entry_listed(session, e)
        }
    pilot = ("SG", "EG", "TN", "MA", "LB")
    extra = sorted((doc_codes | entry_codes) - set(pilot) - {"CN", "ZZ", "--"})
    return [{"code": c, "name": COUNTRY_ZH.get(c, c)} for c in (*pilot, *extra)]


@user_router.get("/countries")
async def list_admission_countries(user: UserDep, session: SessionDep) -> list[dict[str, str]]:
    return await _admission_countries(session, admin_view=False)


@admin_router.get("/countries")
async def list_admin_admission_countries(admin: AdminDep, session: SessionDep) -> list[dict[str, str]]:
    return await _admission_countries(session, admin_view=True)


@user_router.get("/entries")
async def list_entries(user: UserDep, session: SessionDep, country: str | None = None, product: str = "") -> list[dict]:
    q = (
        select(AdmissionEntry)
        .where(AdmissionEntry.review_status.in_(SHOWN), AdmissionEntry.merged_into.is_(None))
        .order_by(AdmissionEntry.country_code, AdmissionEntry.category, AdmissionEntry.name_zh)
    )
    if country:
        q = q.where(AdmissionEntry.country_code == country.upper())
    counts = dict(
        (
            await session.execute(
                text("select entry_id, count(*) from admission_steps group by entry_id")
            )
        ).all()
    )
    rows = [e for e in (await session.execute(q)).scalars() if products.fits(product, e.category, e.name_zh)]
    outs = await _named(session, [_entry_out(e, counts.get(e.id, 0)) for e in rows])
    for e, o in zip(rows, outs, strict=True):
        b = await _basis(session, e.evidence_clause_id)
        o["basis"] = _basis_card(b, e.country_code)
        o["laws"], o["guide"] = await legal_basis(session, e, b)
        o["user_shown"] = user_shown(o)
        o["authority_zh"] = display_authority(o["authority_zh"], e.country_code)
    return [o for o in outs if o.get("user_shown")]


async def entry_listed(session: AsyncSession, entry: AdmissionEntry) -> bool:
    """用户端会不会列出这一事项（准入页、合规文件 05 共用）：有依据法规、有办理环节。"""
    from app.knowledge.cache_sync import keeper_active, rebuilding, use_cached

    key = f"{entry.id}:{entry.updated_at}"
    s_cache = session.info.setdefault("_adm_entry_listed", {})
    if key in s_cache and not rebuilding.get():
        return s_cache[key]
    if use_cached(key in _LISTED_MEMO):
        return _LISTED_MEMO[key]
    steps = await session.scalar(select(func.count()).select_from(AdmissionStep).where(AdmissionStep.entry_id == entry.id))
    laws, _ = await legal_basis(session, entry, await _basis(session, entry.evidence_clause_id))
    ok = bool(laws) and bool(steps)
    s_cache[key] = ok
    if keeper_active or rebuilding.get():
        _LISTED_MEMO[key] = ok
    return ok


def user_shown(o: dict) -> bool:
    """用户端只列资料齐的事项：有依据法规、有办理环节；人工改过的栏目里的金额与条文原文对不上的，
    改正之前也不展示（管理端标出来）。"""
    return bool(o.get("laws")) and bool(o.get("step_count")) and not o.get("amount_conflicts")


_MONEY = re.compile(r"(\d[\d,，]*(?:\.\d+)?)\s*(万|千)?\s*(?:埃及镑|英镑|EGP|新元|新加坡元|SGD|突尼斯第纳尔|第纳尔|TND|迪拉姆|MAD|黎巴嫩镑|LBP)|(?:EGP|SGD|TND|MAD|LBP)\s*(\d[\d,]*)\s*(千)?")


def _amounts(text_: str) -> dict[int, str]:
    out = {}
    for m in _MONEY.finditer(text_ or ""):
        num, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        try:
            v = float(num.replace(",", "").replace("，", ""))
        except ValueError:
            continue
        out[int(round(v * {"万": 10000, "千": 1000}.get(unit or "", 1)))] = m.group(0).strip()
    return out


def _law_numbers(text_: str) -> set[int]:
    t = (text_ or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    got = {int(round(float(x.replace(",", "")))) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", t) if x.replace(",", "").replace(".", "", 1).isdigit()}
    return got | set(_amounts(t))


async def amount_conflicts(session: AsyncSession, entry: AdmissionEntry, steps: list) -> list[str]:
    """Amounts written into manually edited fields (conditions, checklist notes, steps) that the
    item's own clauses (the item's and its steps' evidence, original and translation) do not contain."""
    from app.ingestion.models import Clause
    from app.knowledge.models import ClauseTranslation

    edited = set(entry.edited or [])
    fields: list[tuple[str, str]] = []
    prof = entry.profile or {}
    if "profile:conditions" in edited:
        fields += [("资格条件", f"{c.get('condition', '')} {c.get('what', '')}") for c in prof.get("conditions") or []]
    if "materials_plan" in edited:
        fields += [("递交清单", f"{x.get('name', '')} {x.get('what', '')}") for x in entry.materials_plan or []]
    fields += [("办理环节", f"{st.title_zh} {st.detail_zh}") for st in steps if st.edited]
    wanted = {v: (label, raw) for label, t in fields for v, raw in _amounts(t).items()}
    if not wanted:
        return []
    ids = {x for x in [entry.evidence_clause_id, *[st.evidence_clause_id for st in steps]] if x}
    if not ids:
        return []
    texts = list((await session.scalars(select(Clause.original_text).where(Clause.id.in_(ids)))).all())
    tr_buckets: dict = {}
    for tr_cid, tr_txt in (
        await session.execute(
            select(ClauseTranslation.clause_id, ClauseTranslation.translated_text).where(
                ClauseTranslation.clause_id.in_(ids),
                ClauseTranslation.is_current.is_(True),
                ClauseTranslation.quality_check_passed.is_(True),
                ClauseTranslation.review_status.notin_(
                    ("translation_failed", "not_translated", "rejected", "stale", "superseded")
                ),
            )
        )
    ).all():
        tr_buckets.setdefault(tr_cid, []).append(tr_txt)
    texts += [v[0] for v in tr_buckets.values() if len(v) == 1 and v[0]]
    known = set().union(*(_law_numbers(t) for t in texts)) if texts else set()
    return [f"{label}写“{raw}”，依据条文里没有这个金额" for v, (label, raw) in wanted.items() if v not in known]


def _basis_card(b: dict | None, cc: str) -> dict | None:
    """The item's legal basis on its card: “《法规》第X条”, linked to the article in the library."""
    if not b:
        return None
    return {"source": b["source"], "link": f"/knowledge?country={cc}&doc={b['document_id']}&clause={b['clause_id']}"}


@user_router.get("/by-task/{task_id}")
async def for_assessment(task_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    """From an assessment: the admission items of its country and the projects already started
    from it (the way from a report to the handling, and back)."""
    task = await _own_task(session, task_id, user)
    # 合规文件项目有发出国和接收国：中国以外、库里有的那一端（或两端）就是要办准入的“海外属地”
    from app.analysis.document_basis import country_of
    from app.forms.router import facts_for_task

    values = facts_for_task(task, "")
    ends = {c for c in (country_of(values.get("source_country")), country_of(values.get("recipient_country"))) if c and c != "CN"}
    countries = ends or {task.country_code}
    entries = (
        await session.scalars(
            select(AdmissionEntry)
            .where(
                AdmissionEntry.country_code.in_(countries),
                AdmissionEntry.review_status.in_(SHOWN),
                AdmissionEntry.merged_into.is_(None),
            )
            .order_by(AdmissionEntry.category, AdmissionEntry.name_zh)
        )
    ).all()
    counts = dict(
        (await session.execute(text("select entry_id, count(*) from admission_steps group by 1"))).all()
    )
    started = (
        await session.scalars(
            select(AdmissionProject)
            .where(AdmissionProject.task_id == task.id, AdmissionProject.org_id == user.org_id)
            .order_by(AdmissionProject.created_at.desc())
        )
    ).all()
    outs = await _named(session, [_entry_out(e, counts.get(e.id, 0)) for e in entries])
    started_entry_ids = {p.entry_id for p in started}
    steps_by_entry: dict[uuid.UUID, list[AdmissionStep]] = {}
    if entries:
        for st in (
            await session.scalars(
                select(AdmissionStep).where(AdmissionStep.entry_id.in_([e.id for e in entries]))
            )
        ).all():
            steps_by_entry.setdefault(st.entry_id, []).append(st)
    visible_outs: list[dict] = []
    for e, o in zip(entries, outs, strict=True):
        b = await _basis(session, e.evidence_clause_id)
        o["basis"] = _basis_card(b, e.country_code)
        o["laws"], o["guide"] = await legal_basis(session, e, b)
        o["amount_conflicts"] = await amount_conflicts(session, e, steps_by_entry.get(e.id, []))
        o["user_shown"] = user_shown(o)
        o["authority_zh"] = display_authority(o["authority_zh"], e.country_code)
        if o["user_shown"] or e.id in started_entry_ids:
            visible_outs.append(o)
    return {
        "country_code": next(iter(sorted(countries))) if len(countries) == 1 else task.country_code,
        "entries": visible_outs,
        "projects": [{"id": str(p.id), "title": p.title, "entry_id": str(p.entry_id)} for p in started],
    }


@user_router.get("/entries/{entry_id}")
async def get_entry(entry_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    entry = await session.get(AdmissionEntry, entry_id)
    if entry is None or entry.review_status not in SHOWN:
        raise HTTPException(404, "准入事项不存在")
    return await _entry_detail(session, entry)


async def _own_task(session: AsyncSession, task_id: uuid.UUID, user: Principal) -> AnalysisTask:
    task = await session.get(AnalysisTask, task_id)
    if task is None or task.org_id != user.org_id:
        raise HTTPException(404, "未找到该合规评估项目")
    return task


async def _project(session: AsyncSession, project_id: uuid.UUID, user: Principal) -> AdmissionProject:
    project = await session.get(AdmissionProject, project_id)
    if project is None or project.org_id != user.org_id:
        raise HTTPException(404, "办理项目不存在")
    return project


async def _user_names(session: AsyncSession, ids) -> dict:
    """user id → 用户名（登录邮箱 @ 前面的部分，全站一致）."""
    from app.iam.models import User

    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: u.email.split("@")[0] for u in (await session.scalars(select(User).where(User.id.in_(ids)))).all()}


async def _known_company(session: AsyncSession, org_id, country: str | None = None) -> str:
    """The enterprise's full name as it gave it last time (a compliance-document project or an
    earlier filing project): it is the same for every project, so it is not asked again.

    With ``country`` (a filing in that country): the name must be able to be the party there. The
    Shanghai parent is not the Egyptian applicant, and another country's local company is not
    either; the most recent name that fits is taken, and "" when none does (the applicant is then
    asked). So what an Egyptian project knows does not change with the projects of other countries."""
    from app.analysis.doc_rules import entity_for
    from app.analysis.document_templates import DOC_FACT_PREFIX

    last = await session.scalar(
        select(AnalysisTask).where(AnalysisTask.org_id == org_id, AnalysisTask.kind == "documents").order_by(AnalysisTask.updated_at.desc()).limit(1)
    )
    name = str(((last.facts_json or {}) if last else {}).get(DOC_FACT_PREFIX + "company_name") or "")
    names = [name] if name and not name.startswith("暂不确定") else []
    prev = (
        await session.scalars(
            select(AdmissionProject.applicant).where(AdmissionProject.org_id == org_id, AdmissionProject.applicant != "").order_by(AdmissionProject.created_at.desc()).limit(30)
        )
    ).all()
    names += list(dict.fromkeys(prev))
    from app.iam.models import Organization  # 都没填过：用企业注册时填的公司全称
    from app.iam.service import strip_trial_org_suffix

    org = await session.get(Organization, org_id)
    if org and org.name:
        clean_org_name = strip_trial_org_suffix(org.name)
        if clean_org_name:
            names.append(clean_org_name)
    if country:
        return next((n for n in names if entity_for(n, country) == n), "")
    return names[0] if names else ""


from app.analysis.document_basis import NAMES as COUNTRY_ZH  # noqa: E402


def _materials(detail: dict) -> list[str]:
    """The materials the law lists for the item (from its steps), each once, in order."""
    seen, out = set(), []
    for st in detail.get("steps") or []:
        for mtl in st.get("materials") or []:
            k = re.sub(r"\s", "", mtl)
            if k and k not in seen:
                seen.add(k)
                out.append(mtl)
    return out


async def _form_states(session: AsyncSession, project: AdmissionProject, detail: dict | None = None, snapshot: bool = False) -> list[dict]:
    """The item's official forms and how far each is filled (the enterprise's working copy, if any).
    ``snapshot``: also the answers and facts the form is filled from (kept with a generated version)."""
    from app.forms import service as fs
    from app.forms.models import FormInstance
    from app.forms.router import facts_for_task

    if detail is None:
        detail = await _entry_detail(session, await session.get(AdmissionEntry, project.entry_id))
    task = await session.get(AnalysisTask, project.task_id) if project.task_id else None
    from app.forms.router import org_profile

    cc_code = detail.get("country_code") or None
    profile = await org_profile(session, project.org_id, cc_code)
    out = []
    for f in detail.get("forms") or []:
        form = fs.get_form(f["code"])
        if form is None:
            continue
        form_cc = str(form.get("country") or cc_code or "")
        q = select(FormInstance).where(FormInstance.org_id == project.org_id, FormInstance.form_code == f["code"])
        q = q.where(FormInstance.task_id == task.id) if task else q.where(FormInstance.task_id.is_(None))
        inst = await session.scalar(q.order_by(FormInstance.updated_at.desc()).limit(1))
        own_facts = {
            ("company_name" if (k := rk.removeprefix(DOC_FACT_PREFIX)) == "applicant" else k): fs.latin_part_if_bilingual(
                str(rv).strip(), form_cc
            )
            for rk, rv in (project.facts or {}).items()
            if str(rv or "").strip()
        }
        raw_base = {
            **profile,
            **own_facts,
            **{
                k: fs.latin_part_if_bilingual(v, form_cc)
                for k, v in {
                    "company_name": project.applicant or own_facts.get("company_name", ""),
                    "vehicle_model": project.vehicle_model or own_facts.get("vehicle_model", ""),
                }.items()
                if v
            },
        }
        facts = (
            {
                **raw_base,
                **{
                    k: fs.latin_part_if_bilingual(str(v).strip(), form_cc)
                    for k, v in facts_for_task(task, f["code"]).items()
                    if str(v).strip()
                },
            }
            if task
            else raw_base
        )
        answers = (inst.answers if inst else {}) or {}
        missing = fs.missing_questions(form, answers, facts) if inst else []
        values = {}
        from_task = {k for k, v in facts_for_task(task, f["code"]).items() if str(v).strip()} if task else set()
        carried = 0
        for _, states in fs.resolve(form, answers, facts):
            for st in states:
                if st.item.get("fact") in ("company_name", "vehicle_model") and st.value:
                    values[st.item["fact"]] = str(st.value)
                if st.source == "assessment" and st.item.get("fact") in from_task:
                    carried += 1  # 关联的合规评估项目里已有、带进这张表的栏目
        out.append({
            "code": f["code"], "name": f["name_zh"].split("：", 1)[-1], "authority_zh": f.get("authority_zh", ""), "version": f.get("version", ""),
            "applies_when": f.get("applies_when", ""), "version_note": f.get("version_note", ""), "instance_id": str(inst.id) if inst else None, "filled": inst is not None,
            "missing": [m.get("label") or m.get("label_zh") or m.get("question") or "" for m in missing], "values": values,
            "carried": f"{carried} 项已从项目 {task.title} 带入" if task and carried else "",
            **({"answers": answers, "facts": facts} if snapshot else {}),
        })
    return out


async def _check(session: AsyncSession, project: AdmissionProject, record: bool) -> list[dict]:
    """Run the platform's check of the item's material; with ``record`` keep the findings as issues
    (new ones added, fixed ones closed)."""
    entry = await session.get(AdmissionEntry, project.entry_id)
    detail = await _entry_detail(session, entry)
    rendered, _, _ = await _render(session, project)
    drafts = (await session.scalars(select(AdmissionDraft).where(AdmissionDraft.project_id == project.id).order_by(AdmissionDraft.created_at.desc()))).all()
    found = plan.findings(
        missing=[(q.key, q.label) for q in rendered.missing],
        forms=await _form_states(session, project, detail),
        drafts=[{"created_at": d.created_at} for d in drafts],
        facts_changed_at=project.updated_at,
        company=project.applicant, vehicle=project.vehicle_model,
    )
    if record:
        from datetime import UTC, datetime

        await session.scalar(
            select(AdmissionProject.id).where(AdmissionProject.id == project.id).with_for_update()
        )
        old = {x.target + ":" + plan.finding_key("", x.question).split(":", 1)[1]: x for x in (await session.scalars(
            select(AdmissionIssue).where(AdmissionIssue.project_id == project.id, AdmissionIssue.kind == "check"))).all()}
        now = datetime.now(UTC)
        now_keys = set()
        for f in found:
            k = f["target"] + ":" + plan.finding_key("", f["text"]).split(":", 1)[1]
            now_keys.add(k)
            if k in old:
                old[k].question = f["text"]
                old[k].updated_at = now
                if old[k].state != "open":
                    old[k].state, old[k].answer = "open", ""
            else:
                session.add(AdmissionIssue(org_id=project.org_id, project_id=project.id, kind="check", target=f["target"], question=f["text"]))
        for k, x in old.items():
            if k not in now_keys and x.state == "open":
                x.state, x.answer, x.updated_at = "closed", "已改正（平台复查通过）", now
        await session.commit()
    return found


def missing_parts(found: list[dict]) -> list[str]:
    """What is still missing, by kind — one wording and one count for the user page, the item card
    and the admin page: information items, official forms, the application material."""
    info = sum(1 for f in found if f["target"].startswith("info:"))
    unfilled = sum(1 for f in found if f["target"].startswith("form:") and "还没有填写" in f["text"])
    incomplete = sum(1 for f in found if f["target"].startswith("form:") and "必填未填" in f["text"])
    mismatch = sum(1 for f in found if f["target"].startswith("form:") and "不一致" in f["text"])
    draft = next((f["text"] for f in found if f["target"] == "draft"), "")
    parts = [
        f"信息 {info} 项" if info else "",
        f"官方表格 {unfilled} 份未填" if unfilled else "",
        f"官方表格 {incomplete} 份未填完" if incomplete else "",
        f"表格与项目不一致 {mismatch} 处" if mismatch else "",
        ("申请材料需重新生成" if "改过" in draft else "申请材料未生成") if draft else "",
    ]
    return [x for x in parts if x]


def check_summary(found: list[dict]) -> str:
    """One wording for user and admin side."""
    parts = missing_parts([f for f in found if f["blocking"]])
    return "还差：" + "、".join(parts) if parts else "材料已备齐，可以下载递交"


async def _readiness(session: AsyncSession, project: AdmissionProject) -> dict:
    """How far the material is. Before the enterprise has done anything it says what is to be
    prepared (not “to fix”); once started, how many blocking findings are left (0 = ready to file)."""
    last = await session.scalar(select(func.max(AdmissionIssue.updated_at)).where(AdmissionIssue.project_id == project.id, AdmissionIssue.kind == "check"))
    if filing.state_of(project) == "approved":
        return {
            "items": [],
            "stale": False,
            "ready": True,
            "started": True,
            "blocking": 0,
            "warnings": 0,
            "checked_at": last.isoformat() if last else None,
            "summary": "已取得批文",
        }
    found = await _check(session, project, record=False)
    blocking = sum(1 for f in found if f["blocking"])
    started = bool(project.facts) or any(f["target"].startswith("form:") and "还没有填写" not in f["text"] for f in found)
    if not started:
        started = bool(await session.scalar(select(func.count()).select_from(AdmissionDraft).where(AdmissionDraft.project_id == project.id)))
    summary = check_summary(found)
    return {
        # 还差的每一处（页面顶部逐条列出，点了跳到那一段）
        "items": [{"target": f["target"], "text": f["text"]} for f in found if f["blocking"]],
        "stale": any(f["target"] == "draft" and "改过" in f["text"] for f in found),
        "ready": started and blocking == 0,
        "started": started,
        "blocking": blocking,
        "warnings": len(found) - blocking,
        "checked_at": last.isoformat() if last else None,
        "summary": summary,
    }


@user_router.get("/plan")
async def admission_plan(
    user: UserDep,
    session: SessionDep,
    country: str | None = None,
    country_code: str | None = None,
    function: str = "",
    functions: str = "",
    task_id: uuid.UUID | None = None,
    product: str = "",
) -> dict:
    """The items a business needs in a country, each with the enterprise's material for it (if
    started). From a compliance-document project: its overseas end and its business."""
    country = country or country_code
    function = function or functions.split(",")[0].strip()
    task = await _own_task(session, task_id, user) if task_id else None
    values: dict[str, str] = {}
    if task is not None:
        from app.analysis.document_basis import country_of
        from app.forms.router import facts_for_task

        values = facts_for_task(task, "")
        ends = [c for c in (country_of(values.get("source_country")), country_of(values.get("recipient_country"))) if c and c != "CN"]
        country = country or (ends[0] if ends else task.country_code)
        function = function or (task.scenario_type if task.scenario_type in plan.FUNCTIONS else "")
    if not country:
        raise HTTPException(422, "请选择国家")
    cc = country.upper()
    entries = (
        await session.scalars(
            select(AdmissionEntry)
            .where(AdmissionEntry.country_code == cc, AdmissionEntry.review_status.in_(SHOWN), AdmissionEntry.merged_into.is_(None))
            .order_by(AdmissionEntry.category, AdmissionEntry.name_zh)
        )
    ).all()
    counts = dict((await session.execute(text("select entry_id, count(*) from admission_steps group by 1"))).all())
    inbound_skip: set = set()
    if task is not None:
        # 带合规项目进来：数据流向已知，接收国自己管“数据传出”的许可只在接收方再转给第三国时才办（与评估端同一规则）
        from app.analysis import doc_rules

        source, recipient = doc_rules.ends(values, task.country_code)
        kept = {e.id for e in doc_rules.inbound_only(list(entries), source, recipient, cc, values)}
        inbound_skip = {e.id for e in entries if e.id not in kept}
    mine = select(AdmissionProject).where(AdmissionProject.org_id == user.org_id)
    mine = mine.where(AdmissionProject.task_id == task.id) if task else mine.where(AdmissionProject.task_id.is_(None), AdmissionProject.function_key == function)
    projects = {p.entry_id: p for p in (await session.scalars(mine)).all()}
    # 两种进入方式（带合规项目、不带项目）同一条规则：企业已经有这个事项的项目，卡上就显示那一份的进度和“继续”，
    # 不让用户再建一个。先取这次进入方式对应的，其次企业里没关联项目的，最后其他项目关联的
    for p in (await session.scalars(
        select(AdmissionProject).where(AdmissionProject.org_id == user.org_id).order_by(AdmissionProject.task_id.is_(None).desc(), AdmissionProject.updated_at.desc())
    )).all():
        projects.setdefault(p.entry_id, p)
    outs = await _named(session, [_entry_out(e, counts.get(e.id, 0)) for e in entries])
    needed, maybe, other = [], [], []
    for e, o in zip(entries, outs, strict=True):
        where = plan.applies(e.category, function, e.functions, e.name_zh)
        detail = await _entry_detail(session, e)
        if not where or not products.fits(product, e.category, e.name_zh):
            # 与所选业务、产品无关的也列出名称（折叠），和事项库的数目对得上
            if detail["user_shown"]:
                other.append({"id": str(e.id), "name_zh": e.name_zh, "summary_zh": e.summary_zh})
            continue
        o["authority_zh"] = authority_short(detail)
        o["portal"] = detail.get("portal")
        o["forms"] = [f["name_zh"].split("：", 1)[-1] for f in detail.get("forms") or []]
        o["profile"] = detail["profile"]
        o["basis"] = _basis_card(detail.get("basis"), cc)
        o["laws"], o["guide"] = detail["laws"], detail["guide"]
        o["authority_why"] = detail["authority_why"]
        if not detail["user_shown"] and e.id not in projects:
            continue  # 资料没补齐的事项不对用户展示（已经在准备的照常显示）
        p = projects.get(e.id)
        o["project"] = {"id": str(p.id), **(await _readiness(session, p)), "filing": filing.filing_out(p), "approval": approval_out(p)} if p else None
        from app.analysis.doc_rules import admission_gate

        gate = admission_gate(e) if where == "needed" else ""
        if where == "needed" and not gate and e.id in inbound_skip:
            gate = f"数据传入{COUNTRY_ZH.get(cc, cc)}时不用办；接收方再向第三国转移时才办"
        o["gate"] = gate
        (needed if where == "needed" and not gate else maybe).append(o)
    return {
        "country_code": cc, "country_zh": COUNTRY_ZH.get(cc, cc), "function": function, "function_zh": plan.FUNCTIONS.get(function, ""),
        "task": {"id": str(task.id), "title": task.title} if task else None,
        "needed": needed, "maybe": maybe, "other": other,
        "functions": [{"key": k, "label": v} for k, v in plan.FUNCTIONS.items()],
        "product": product, "products": [{"key": k, "label": v} for k, v in products.PRODUCTS.items()],
    }


@user_router.post("/projects/{project_id}/check")
async def check_project(project_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    """Check the item's material the way the authority will look at it; findings are recorded."""
    project = await _project(session, project_id, user)
    found = await _check(session, project, record=True)
    return {"findings": found, **(await _readiness(session, project))}


@user_router.post("/projects", status_code=201)
async def create_project(body: ProjectIn, user: UserDep, session: SessionDep) -> dict:
    from sqlalchemy import text as sa_text

    entry = await session.get(AdmissionEntry, body.entry_id)
    if entry is None or entry.review_status not in SHOWN:
        raise HTTPException(404, "准入事项不存在")
    task = await _own_task(session, body.task_id, user) if body.task_id else None
    function = body.function or (task.scenario_type if task and task.scenario_type in plan.FUNCTIONS else "")
    await session.execute(
        sa_text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"adm_proj:{user.org_id}:{entry.id}:{task.id if task else ''}:{function}"},
    )
    same = select(AdmissionProject).where(AdmissionProject.org_id == user.org_id, AdmissionProject.entry_id == entry.id)
    same = same.where(AdmissionProject.task_id == task.id) if task else same.where(AdmissionProject.task_id.is_(None), AdmissionProject.function_key == function)
    if (existing := await session.scalar(same.limit(1))) is not None:  # 准备过的：直接打开，不重复建
        return {"id": str(existing.id), "existing": True}
    title = body.title.strip() or f"{COUNTRY_ZH.get(entry.country_code, entry.country_code)} · {entry.name_zh}"
    project = AdmissionProject(
        org_id=user.org_id,
        entry_id=entry.id,
        task_id=task.id if task else None,
        function_key=function,
        title=title,
        applicant=body.applicant or (str((task.facts_json or {}).get("company_name", "")) if task else "") or await _known_company(session, user.org_id, entry.country_code),
        vehicle_model=body.vehicle_model or ((task.vehicle_model or "") if task else ""),
        created_by=user.user_id,
    )
    from app.updates.notices import entry_hash, entry_snapshot

    project.entry_seen_hash = await entry_hash(session, entry)  # 之后事项有变化才提醒
    project.milestones = {"entry_seen": await entry_snapshot(session, entry)}
    session.add(project)
    await session.flush()
    project_id = project.id
    await session.commit()
    return {"id": str(project_id), "existing": False}


@user_router.get("/projects")
async def list_projects(user: UserDep, session: SessionDep) -> list[dict]:
    projects = (
        (
            await session.execute(
                select(AdmissionProject)
                .where(AdmissionProject.org_id == user.org_id)
                .order_by(AdmissionProject.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not projects:
        return []
    entry_ids = list({p.entry_id for p in projects})
    entries_by_id = {
        e.id: e
        for e in (await session.scalars(select(AdmissionEntry).where(AdmissionEntry.id.in_(entry_ids)))).all()
    }
    merged_ids = [e.merged_into for e in entries_by_id.values() if e.merged_into and e.merged_into not in entries_by_id]
    if merged_ids:
        for e in (await session.scalars(select(AdmissionEntry).where(AdmissionEntry.id.in_(merged_ids)))).all():
            entries_by_id[e.id] = e
    pids = [p.id for p in projects]
    from datetime import UTC

    from sqlalchemy import func as _func

    from app.admission.models import AdmissionProgress

    draft_times = dict(
        (
            await session.execute(
                select(AdmissionDraft.project_id, _func.max(AdmissionDraft.created_at))
                .where(AdmissionDraft.project_id.in_(pids))
                .group_by(AdmissionDraft.project_id)
            )
        ).all()
    )
    prog_times = dict(
        (
            await session.execute(
                select(AdmissionProgress.project_id, _func.max(AdmissionProgress.updated_at))
                .where(AdmissionProgress.project_id.in_(pids))
                .group_by(AdmissionProgress.project_id)
            )
        ).all()
    )
    out = []
    for p in projects:
        entry = entries_by_id.get(p.entry_id) or await session.get(AdmissionEntry, p.entry_id)
        # 合并去重后，项目指向的事项可能已并入另一项：显示合并后的名称
        shown = (entries_by_id.get(entry.merged_into) or await session.get(AdmissionEntry, entry.merged_into)) if entry.merged_into else entry
        ts_candidates = [
            (x if x.tzinfo else x.replace(tzinfo=UTC))
            for x in (p.created_at, p.updated_at, draft_times.get(p.id), prog_times.get(p.id))
            if x is not None
        ]
        effective_updated = max(ts_candidates) if ts_candidates else p.created_at
        out.append(
            {
                "id": str(p.id),
                "title": f"{COUNTRY_ZH.get(entry.country_code, entry.country_code)} · {(shown or entry).name_zh}",
                "status": p.status,
                "country_code": entry.country_code,
                "entry_name": (shown or entry).name_zh,
                "created_at": p.created_at.isoformat(),
                "updated_at": effective_updated.isoformat(),
                "source": await _task_link(session, p),
                "function": p.function_key,
                "function_zh": plan.FUNCTIONS.get(p.function_key, ""),
                "entry_id": str(p.entry_id),
                "approval": approval_out(p),
                "filing": filing.filing_out(p, await _open_authority(session, p.id)),
                **(await _readiness(session, p)),
            }
        )
    return out


@user_router.delete("/projects/{project_id}")
async def delete_project(project_id: uuid.UUID, request: Request, user: UserDep, session: SessionDep) -> dict:
    """Delete one of the enterprise's handling projects with its ledger and drafts (the admission
    item itself, from the law, stays). The deletion is in the audit log."""
    from sqlalchemy import delete as sa_delete

    from app.updates.models import UserNotification

    project = await _project(session, project_id, user)
    set_detail(request, summary=f"删除准入办理项目“{project.title}”")
    await session.execute(
        sa_delete(UserNotification).where(
            UserNotification.org_id == project.org_id,
            UserNotification.link == f"/admission?project={project.id}",
        )
    )
    await session.delete(project)
    await session.commit()
    return {"deleted": str(project_id)}


class ApprovalIn(BaseModel):
    number: str = Field(default="", max_length=120)
    issued_on: str = Field(default="", max_length=10)
    expires_on: str = Field(default="", max_length=10)
    long_term: bool = False  # 批文写明长期有效（如数据处理申报回执）


async def _basis_start(session: AsyncSession, entry: AdmissionEntry | None):
    """When the law an item's own clause comes from took effect: (day, law name, wording), with only
    the year known the 1st of January of that year. An approval cannot be issued before it."""
    from datetime import date

    from app.admission import names
    from app.ingestion.models import Clause, Document, DocumentVersion

    if entry is None or not entry.evidence_clause_id:
        return None
    row = (
        await session.execute(
            select(Document.id, Document.title_zh, Document.title, DocumentVersion.effective_date, DocumentVersion.published_date, DocumentVersion.official_number)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .join(Clause, Clause.version_id == DocumentVersion.id)
            .where(Clause.id == entry.evidence_clause_id)
        )
    ).first()
    if row is None:
        return None
    name = names.law_name(str(row[0]), row[1] or row[2])
    if row[3]:
        return row[3], name, f"施行日期（{row[3].isoformat()}）"
    if row[4]:
        return row[4], name, f"公布日期（{row[4].isoformat()}）"
    m = re.search(r"(?<!\d)(19|20)\d{2}(?!\d)", f"{row[5] or ''} {row[1] or ''} {row[2] or ''}")
    return (date(int(m.group(0)), 1, 1), name, f"公布年份（{m.group(0)} 年）") if m else None


def approval_out(p: AdmissionProject) -> dict | None:
    """The approval the enterprise obtained, with how long it still runs (证照台账)."""
    a = {k: v for k, v in (p.approval or {}).items() if k != "file_id"}  # 以前上传的批文文件不再显示
    if not a.get("number"):
        return None
    from app.core import dates

    until = dates.parse(a.get("expires_on"))
    days = dates.days_left(until) if until else None
    state = "长期有效" if a.get("long_term") else dates.expiry_state(days)
    # 项目状态以批文为准：已取得 / 即将到期 / 需续期（过期了要办续期）
    status = {"已过期": "需续期", "即将到期": "即将到期"}.get(state, "已取得")
    return {**a, "days_left": days, "state": state, "status": status}


@user_router.put("/projects/{project_id}/approval")
async def set_approval(project_id: uuid.UUID, body: ApprovalIn, request: Request, user: UserDep, session: SessionDep) -> dict:
    """Record the approval obtained for this item (number and dates, typed in by the enterprise): it
    goes into the enterprise's ledger of approvals and is reminded before it expires."""
    from datetime import date

    project = await _project(session, project_id, user)
    for d in (body.issued_on, body.expires_on):
        if d:
            try:
                date.fromisoformat(d)
            except ValueError as exc:
                raise HTTPException(422, "日期格式应为 年-月-日") from exc
    if not body.number.strip():
        raise HTTPException(422, "请填写批文或证书编号")
    if body.issued_on and body.expires_on and not body.long_term:
        if date.fromisoformat(body.expires_on) < date.fromisoformat(body.issued_on):
            raise HTTPException(422, "批文有效期届满日期不能早于签发日期")
    if body.issued_on:
        start = await _basis_start(session, await session.get(AdmissionEntry, project.entry_id))
        if start and date.fromisoformat(body.issued_on) < start[0]:
            raise HTTPException(422, f"签发日期早于依据法规《{start[1]}》的{start[2]}，请核对签发日期")
    before = dict(project.approval or {})
    project.approval = body.model_dump(mode="json")
    if (project.milestones or {}).get("state") != "approved":
        # 登记了批文就是“已取得批文”：没走过前面几步（如以前办下来的）也直接记上
        m = dict(project.milestones or {})
        m["state"], m["result_on"] = "approved", body.issued_on or m.get("result_on", "")
        m["history"] = [*m.get("history", []), {"state": "approved", "on": m["result_on"], "at": datetime.now(UTC).isoformat(), "by": str(user.user_id)}][-50:]
        project.milestones = m
    set_detail(request, summary=f"准入办理“{project.title}”登记批文", before=before, after=project.approval)
    await session.commit()
    return approval_out(project) or {}


async def _open_authority(session: AsyncSession, project_id) -> int:
    return int(await session.scalar(
        select(func.count()).select_from(AdmissionIssue).where(
            AdmissionIssue.project_id == project_id, AdmissionIssue.kind == "authority", AdmissionIssue.state == "open"
        )
    ) or 0)


class FilingIn(BaseModel):
    state: Literal["preparing", "submitted", "correcting", "rejected", "renewing"]
    on: str = Field(default="", max_length=10)  # 递交日期 / 收到补正通知的日期 / 结果日期
    reason: str = Field(default="", max_length=500)  # 未通过的原因
    questions: list[str] = Field(default_factory=list, max_length=30)  # 补正中：受理机构提的问题


@user_router.put("/projects/{project_id}/filing")
async def set_filing(project_id: uuid.UUID, body: FilingIn, request: Request, user: UserDep, session: SessionDep) -> dict:
    """The enterprise moves its filing along (requirement 2.6). Going into 补正中 records the
    authority's questions as issues of kind “authority”; 已取得批文 goes through the approval form."""
    project = await _project(session, project_id, user)
    questions = [q.strip()[:4000] for q in body.questions if q.strip()]
    if body.state == "renewing":  # 批文快到期或过期：回到准备中准备续期，原批文留在记录里
        a = approval_out(project)
        if not a or a.get("status") == "已取得":
            raise HTTPException(422, "批文还在有效期内，到期前 90 天再准备续期")
        m = dict(project.milestones or {})
        m["previous_approvals"] = [*m.get("previous_approvals", []), dict(project.approval or {})][-10:]
        m["history"] = [*m.get("history", []), {"state": "preparing", "at": datetime.now(UTC).isoformat(), "by": str(user.user_id), "renewal": True}][-50:]
        m["state"], m["renewal"] = "preparing", True
        m.pop("submitted_draft", None)
        project.milestones, project.approval = m, {}
        set_detail(request, summary=f"准入办理“{project.title}”开始准备续期")
        await session.commit()
        return filing.filing_out(project, 0)
    if body.state == "correcting" and not questions:
        raise HTTPException(422, "请写下受理机构要求补正的问题，至少一条")
    before = filing.state_of(project)
    latest = await session.scalar(select(AdmissionDraft).where(AdmissionDraft.project_id == project.id).order_by(AdmissionDraft.created_at.desc()).limit(1))
    if body.state == "submitted" and latest is None:
        raise HTTPException(422, "还没有生成申请材料，生成后再记“已递交”")
    try:
        project.milestones = filing.move(
            project, body.state, on=body.on, reason=body.reason, by=str(user.user_id),
            open_authority=await _open_authority(session, project.id),
        )
    except filing.FilingError as exc:
        raise HTTPException(422, str(exc)) from None
    if body.state == "submitted":  # 递交的就是最新这一版：之后页面只读显示这一版
        project.milestones = {**project.milestones, "submitted_draft": str(latest.id)}
    for q in questions:
        session.add(AdmissionIssue(
            org_id=user.org_id, project_id=project.id, kind="authority", question=q,
            created_by=user.user_id, updated_by=user.user_id,
        ))
    set_detail(
        request, summary=f"准入办理“{project.title}”申报状态改为“{filing.STATE_ZH[body.state]}”",
        before=filing.STATE_ZH[before], after=filing.STATE_ZH[body.state],
    )
    await session.commit()
    return filing.filing_out(project, await _open_authority(session, project.id))


class LinkIn(BaseModel):
    task_id: uuid.UUID | None = None


@user_router.put("/projects/{project_id}/link")
async def link_task(project_id: uuid.UUID, body: LinkIn, request: Request, user: UserDep, session: SessionDep) -> dict:
    """Tie the filing project to a compliance-assessment project of the enterprise (or untie it):
    the assessment's answers — company, data, recipient — then fill this item's forms and drafts."""
    project = await _project(session, project_id, user)
    task = await _own_task(session, body.task_id, user) if body.task_id else None
    before = str(project.task_id) if project.task_id else ""
    project.task_id = task.id if task else None
    if task is not None and not project.applicant:
        project.applicant = str((task.facts_json or {}).get("doc_company_name") or "")
    set_detail(request, summary=f"准入办理“{project.title}”关联合规评估项目", before=before, after=str(project.task_id or ""))
    await session.commit()
    return {"task": await _task_link(session, project)}


@user_router.get("/projects/{project_id}")
async def get_project(project_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    project = await _project(session, project_id, user)
    entry = await session.get(AdmissionEntry, project.entry_id)
    drafts = (
        (
            await session.execute(
                select(AdmissionDraft)
                .where(AdmissionDraft.project_id == project.id)
                .order_by(AdmissionDraft.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    issues = (
        await session.scalars(select(AdmissionIssue).where(AdmissionIssue.project_id == project.id).order_by(AdmissionIssue.created_at))
    ).all()
    names = await _user_names(session, [x.updated_by or x.created_by for x in issues])
    detail = await _entry_detail(session, entry)
    forms = await _form_states(session, project, detail)
    lang_info = await foreign_letter.resolve_submission_language(
        session, entry, str((detail.get("profile") or {}).get("language") or "")
    )
    at_draft = (project.milestones or {}).get("facts_at_draft") or {}
    own = {k[len(DOC_FACT_PREFIX):] for k in (project.facts or {}) if k.startswith(DOC_FACT_PREFIX)}
    own_labels = {"applicant": "申请人", "company_name": "申请企业名称", "vehicle_model": "车型或设备型号"}

    def label_of(k: str) -> str:
        lab = engine.info_fact(k).get("label", k)
        lab = own_labels.get(k, lab) if lab == k or k in own_labels else lab
        return re.sub(r"[（(][A-Za-z°.\s]+[）)]", "", lab).strip()  # “商业登记号（N°RC）”是别国表格的叫法

    seen_labels: set[str] = set()
    seen_values: set[str] = set()
    group_name = await _known_company(session, project.org_id)
    applicant = (project.applicant or "").strip()

    def label_or_group(k: str, v: str) -> str:
        # 准入项目以当地申请主体为准；和它不同的国内母公司写成“所属集团”
        if applicant and v.strip() == group_name and v.strip() != applicant:
            return "所属集团"
        return label_of(k)
    filled = [
        {
            "key": k, "label": label_or_group(k, v), "value": v, "invalid": engine_check(k, v),
            "editable": k in own,
            "changed": bool(drafts) and k in at_draft and at_draft.get(k) != v,
        }
        for k, v in (await _filled_facts(session, project, entry, detail)).items()
        if not (label_or_group(k, v) in seen_labels or seen_labels.add(label_or_group(k, v)))  # 同名的一项只列一次
        and not (v.strip() in seen_values or seen_values.add(v.strip()))  # 同一个值不放进两栏
    ]
    return {
        "id": str(project.id),
        "filled": filled,
        "title": f"{COUNTRY_ZH.get(entry.country_code, entry.country_code)} · {entry.name_zh}",
        "applicant": project.applicant,
        "vehicle_model": project.vehicle_model,
        "status": project.status,
        "task": await _task_link(session, project),
        "function": project.function_key,
        "function_zh": plan.FUNCTIONS.get(project.function_key, ""),
        "entry": detail,
        "bring": engine.bring_list(plan_of(entry, _materials(detail)), [f["name"] for f in forms]),
        # 页面上的递交清单（与下载件末尾那份同一来源）
        "checklist": engine.checklist_rows(entry, plan_of(entry, _materials(detail)), [f["name"] for f in forms], "平台生成（申请书）"),
        "forms": forms,
        "issues": [_issue_out(x, names) for x in issues],
        "readiness": await _readiness(session, project),
        "approval": approval_out(project),
        "filing": filing.filing_out(project, sum(1 for x in issues if x.kind == "authority" and x.state == "open")),
        "foreign_lang_code": lang_info[0] if lang_info else "",
        "foreign_lang_zh": lang_info[1] if lang_info else "",
        "drafts": [
            {
                "id": str(d.id),
                "name_zh": d.name_zh,
                "created_at": d.created_at.isoformat(),
                "missing": d.missing or [],
                "forms": [{"code": f["code"], "name": f["name"]} for f in d.forms or []],
                "has_foreign": bool((d.foreign_draft or {}).get("blocks"))
                and (not lang_info or (d.foreign_draft or {}).get("lang_code") == lang_info[0]),
                "foreign_lang_code": str((d.foreign_draft or {}).get("lang_code") or ""),
                "foreign_lang_zh": str((d.foreign_draft or {}).get("lang_zh") or ""),
                "foreign_name_zh": str((d.foreign_draft or {}).get("name_zh") or ""),
                "foreign_generated_at": (d.foreign_draft or {}).get("generated_at"),
            }
            for d in drafts
        ],
    }


async def _task_link(session: AsyncSession, project: AdmissionProject) -> dict | None:
    """The assessment this project came from, for the way back to its report."""
    if project.task_id is None:
        return None
    task = await session.get(AnalysisTask, project.task_id)
    if task is None or task.archived_at is not None:
        return None
    # kind：documents = 合规文件项目（回到“生成合规文件”）；其余为咨询对话
    return {"id": str(task.id), "title": getattr(task, "title", "") or "", "kind": task.kind}


EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE = re.compile(r"^[+()\d][\d\s()+\-]{5,}$")


def engine_check(key: str, value: str) -> str:
    """One check per kind of item: an e-mail looks like one, a phone number is digits, a register or
    tax number has digits. Returns what is wrong, or ""."""
    label = engine.info_fact(key).get("label", key)
    k = f"{key} {label}".lower()
    v = value.strip()
    if re.search(r"(详)?见附件|另附|附后", v):
        return f"“{label}”请直接写内容，平台不收附件"
    if ("email" in k or "邮箱" in k) and not EMAIL.match(v):
        return f"“{label}”应是电子邮箱，如 name@company.com"
    if any(w in k for w in ("phone", "tel", "fax", "电话", "传真")) and not PHONE.match(v):
        return f"“{label}”应是电话号码，只写数字、+、空格和短横线"
    if any(w in k for w in ("登记号", "注册号", "税号", "税务登记", "n°rc", "编号")) and not re.search(r"\d", v):
        return f"“{label}”应包含编号数字"
    return ""


async def _filled_facts(session: AsyncSession, project: AdmissionProject, entry: AdmissionEntry, detail: dict) -> dict[str, str]:
    """The information this item's material uses that is already known (filled here, carried from the
    linked assessment or the company's official forms): what “需要的信息” shows as filled."""
    from app.forms.router import org_profile

    task = await session.get(AnalysisTask, project.task_id) if project.task_id else None
    known = {**{k: v for k, v in (await org_profile(session, project.org_id, entry.country_code)).items() if v}, **{k: v for k, v in engine.project_facts(project, entry, task).items() if v}}
    answers = dict(project.facts or {})
    steps = [] if detail.get("steps_generic") else list(detail.get("steps") or [])
    keys = ["company_name", "vehicle_model"]
    for p in plan_of(entry, [m for st in steps for m in st.get("materials") or []]):
        if p.get("kind") == "info":
            keys += [k for k in (p.get("fact") or "").split(",") if k]
    keys += [k[len(DOC_FACT_PREFIX):] for k in answers if k.startswith(DOC_FACT_PREFIX)]
    out = {}
    from app.analysis.doc_rules import entity_for

    for k in dict.fromkeys(keys):
        v = str(answers.get(DOC_FACT_PREFIX + k) or known.get(DOC_FACT_PREFIX + k) or known.get(k) or "").strip()
        if k == "company_name" and not answers.get(DOC_FACT_PREFIX + k):
            v = entity_for(v, entry.country_code)  # 带出来的企业名称只在它能当这个国家的申请主体时才算已知：和申请书缺项判断同一个条件
        if v:
            out[k] = v
    return out


async def _render(session: AsyncSession, project: AdmissionProject):
    """The admission material for a project, from the template engine: (rendered, name, entry)."""
    entry = await session.get(AdmissionEntry, project.entry_id)
    detail = await _entry_detail(session, entry)
    task = await session.get(AnalysisTask, project.task_id) if project.task_id else None
    from app.forms.router import org_profile

    forms = [f["name_zh"].split("：", 1)[-1] for f in detail.get("forms") or []]
    rendered, name = await engine.render_project(
        session, project, entry, detail, task, profile=await org_profile(session, project.org_id, entry.country_code), forms=forms,
    )
    return rendered, name, entry


# 每个填写项按它自己的含义给一个示例；对不上的不给（页面上不放灰字），不拿别的栏的样子凑
# 示例值像真的：按申请所在国家的写法，不重复栏目名、不用 123456 这类占位号码
_LOCAL = {
    "EG": {"phone": "+20 2 2736 4810", "fax": "+20 2 2736 4811", "addr": "开罗新开罗第五区 90 号街 214 号", "reg": "184572", "tax": "512-874-309",
           "dpo": "Ahmed Hassan", "sign": "Mohamed Saleh", "co": "埃及"},
    "SG": {"phone": "+65 6735 2048", "fax": "+65 6735 2049", "addr": "新加坡乌节路 391 号 #10-01", "reg": "201934567K", "tax": "201934567K",
           "dpo": "Tan Wei Ming", "sign": "Lim Kok Heng", "co": "新加坡"},
    "TN": {"phone": "+216 71 902 345", "fax": "+216 71 902 346", "addr": "突尼斯市穆罕默德五世大街 25 号", "reg": "B0123452019", "tax": "1534879/A/M/000",
           "dpo": "Sami Ben Ali", "sign": "Karim Trabelsi", "co": "突尼斯"},
    "MA": {"phone": "+212 522 438 910", "fax": "+212 522 438 911", "addr": "卡萨布兰卡哈桑二世大道 120 号", "reg": "485217", "tax": "40187652",
           "dpo": "Youssef Alaoui", "sign": "Hicham Bennani", "co": "摩洛哥"},
    "LB": {"phone": "+961 1 752 418", "fax": "+961 1 752 419", "addr": "贝鲁特哈姆拉街 45 号", "reg": "1024587", "tax": "3218475",
           "dpo": "Rami Khoury", "sign": "Georges Haddad", "co": "黎巴嫩"},
}
_EXAMPLES = [
    (r"电子邮箱|邮箱|e-?mail", lambda c: "dpo@qh-auto.com"),
    (r"传真", lambda c: c["fax"]),
    (r"电话|手机", lambda c: c["phone"]),
    (r"地址", lambda c: c["addr"]),
    (r"商业登记号|商业登记编号|注册号|登记编号", lambda c: c["reg"]),
    (r"税务登记号|税号|税卡", lambda c: c["tax"]),
    (r"职务|职位", lambda c: "总经理"),
    (r"^数据保护负责人(姓名|名称)?$|数据保护负责人.*(姓名|名称)|DPO", lambda c: c["dpo"]),
    (r"^(签署人|法定代表人)(姓名|名称)?$|(签署人|法定代表人).*(姓名|名称)", lambda c: c["sign"]),
    (r"保障措施有效性证明", lambda c: "已完成数据出境风险自评估，并取得 ISO/IEC 27701 认证"),
    (r"^业务活动说明$", lambda c: f"在{c['co']}进口并销售纯电动轿车，并提供车联网服务"),
    (r"(申请|企业|公司).*(名称|全称)", lambda c: f"启衡汽车销售（{c['co']}）有限公司"),
    (r"车型|型号", lambda c: "QH-E7 纯电动轿车"),
]


def fact_example(label: str, country: str = "") -> str:
    cc = (country or "").upper()
    co = COUNTRY_ZH.get(cc, cc) if cc else "当地"
    local = _LOCAL.get(cc) or {
        "phone": "含国际区号的当地办公电话",
        "fax": "含国际区号的当地传真号码",
        "addr": f"{co}当地注册办公地址",
        "reg": f"{co}当地商业登记编号",
        "tax": f"{co}当地税务登记编号",
        "dpo": "当地数据保护负责人姓名",
        "sign": "法定代表人或授权签署人姓名",
        "co": co,
    }
    for pat, ex in _EXAMPLES:
        if re.search(pat, label or "", re.I):
            return f"如：{ex(local)}"
    return ""


@user_router.get("/projects/{project_id}/questions")
async def project_questions(project_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    """What the material still needs, asked the way every compliance document asks it."""
    project = await _project(session, project_id, user)
    rendered, name, ent = await _render(session, project)
    return {
        "name": name,
        "ready": not rendered.missing,
        "questions": [
            {"key": q.key, "label": q.label, "question": q.question, "why": q.why, "example": fact_example(q.label, ent.country_code if ent else "")}
            for q in rendered.missing
        ],
    }


@user_router.post("/projects/{project_id}/answers")
async def answer_questions(
    project_id: uuid.UUID, body: AnswersIn, user: UserDep, session: SessionDep
) -> dict:
    project = await _project(session, project_id, user)
    entry = await session.get(AdmissionEntry, project.entry_id)
    detail = await _entry_detail(session, entry)
    rendered, _, _ = await _render(session, project)
    own = {k[len(DOC_FACT_PREFIX):] for k in (project.facts or {}) if k.startswith(DOC_FACT_PREFIX)}
    known_filled = set((await _filled_facts(session, project, entry, detail)).keys())
    allowed = {q.key for q in rendered.missing} | own | known_filled  # 还缺的、本项目里自己填过的，以及已带入需覆盖修改的
    clean = engine.clean_answers(body.answers, allowed)
    cleared = [
        k for k, v in body.answers.items()
        if k in own and (DOC_FACT_PREFIX + k) not in clean and str(v or "").strip() == ""
    ]
    if not clean and not cleared:
        raise HTTPException(422, "没有可保存的回答")
    for k, v in clean.items():
        bad = engine_check(k[len(DOC_FACT_PREFIX):], v)
        if bad:
            raise HTTPException(422, bad)
    next_facts = {k: v for k, v in (project.facts or {}).items() if k not in {DOC_FACT_PREFIX + c for c in cleared}}
    project.facts = {**next_facts, **clean}
    if "doc_applicant" in project.facts and str(project.facts["doc_applicant"]).strip():
        project.applicant = str(project.facts["doc_applicant"]).strip()
    if "doc_vehicle_model" in project.facts and str(project.facts["doc_vehicle_model"]).strip():
        project.vehicle_model = str(project.facts["doc_vehicle_model"]).strip()
    await session.flush()
    await _check(session, project, record=True)
    rendered, name, ent = await _render(session, project)
    return {
        "ready": not rendered.missing,
        "questions": [
            {"key": q.key, "label": q.label, "question": q.question, "why": q.why, "example": fact_example(q.label, ent.country_code if ent else "")}
            for q in rendered.missing
        ],
    }


class AdmissionAssistIn(BaseModel):
    message: str = Field(min_length=1, max_length=3000)
    history: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


@user_router.post("/projects/{project_id}/assist")
async def assist_project(
    project_id: uuid.UUID, body: AdmissionAssistIn, user: UserDep, session: SessionDep
) -> dict[str, Any]:
    from app.analysis import copilot_fill
    from app.core.redaction import bind_egress, llm_scope
    from app.iam.models import Organization

    view = await get_project(project_id, user, session)
    q_resp = await project_questions(project_id, user, session)
    project = await _project(session, project_id, user)
    org = await session.get(Organization, project.org_id) if project.org_id else None
    with llm_scope():
        bind_egress(session, org)
        return await copilot_fill.assist_admission_project(view, q_resp["questions"], body.message, body.history)


@user_router.post("/projects/{project_id}/drafts", status_code=201)
async def make_draft(project_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    project = await _project(session, project_id, user)
    rendered, name, entry = await _render(session, project)
    missing = [f"{q.label}（{q.question}）" for q in rendered.missing]
    blocks = rendered.blocks
    if missing:
        blocks = blocks + [
            {"type": "heading", "text": engine.next_heading(blocks) + "待补充事项"},
            {"type": "list", "items": missing},
        ]
    # 这一版连同当时填好的官方表格一起留存（需求 2.6 留存申报资料）：以后表格再改，这一版仍能原样下载
    forms = [
        {"code": f["code"], "name": f["name"], "answers": f["answers"], "facts": f["facts"]}
        for f in await _form_states(session, project, snapshot=True) if f["filled"]
    ]
    draft = AdmissionDraft(
        project_id=project.id,
        org_id=project.org_id,
        name_zh=engine.doc_name(entry),
        blocks=blocks,
        missing=missing,
        forms=forms,
        created_by=user.user_id,
    )
    session.add(draft)
    # 记下这一版用到的信息：之后改过的在“需要的信息”里标出来
    project.milestones = {**(project.milestones or {}), "facts_at_draft": await _filled_facts(session, project, entry, await _entry_detail(session, entry))}
    await session.flush()
    out = {"id": str(draft.id), "name_zh": draft.name_zh, "blocks": blocks, "missing": missing}
    await _check(session, project, record=True)
    return out


async def _draft(session, draft_id: uuid.UUID, user: Principal) -> AdmissionDraft:
    draft = await session.get(AdmissionDraft, draft_id)
    if draft is None or draft.org_id != user.org_id:
        raise HTTPException(404, "材料初稿不存在")
    return draft


@user_router.get("/drafts/{draft_id}")
async def get_draft(draft_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    d = await _draft(session, draft_id, user)
    return {"id": str(d.id), "name_zh": d.name_zh, "blocks": d.blocks, "missing": d.missing or []}


@user_router.get("/drafts/{draft_id}/export.{fmt}")
async def export_draft(draft_id: uuid.UUID, fmt: str, user: UserDep, session: SessionDep) -> Response:
    d = await _draft(session, draft_id, user)
    if fmt not in ("docx", "pdf"):
        raise HTTPException(404, "不支持的导出格式")
    project = await session.get(AdmissionProject, d.project_id)
    proj_title = project.title if project else ""
    blocks = d.blocks
    draft_name = d.name_zh
    if fmt == "docx":
        data = build_docx(Rendered(blocks=blocks), draft_name)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        import asyncio

        await session.close()
        data = await asyncio.to_thread(build_pdf, Rendered(blocks=blocks), draft_name)
        media = "application/pdf"
    return Response(
        data,
        media_type=media,
        headers={"Content-Disposition": content_disposition(named(proj_title, draft_name, ext=fmt))},
    )


@user_router.post("/drafts/{draft_id}/foreign")
async def make_foreign_draft(
    draft_id: uuid.UUID, request: Request, user: UserDep, session: SessionDep
) -> dict:
    """Translate Part 1 (对外申请文书) of a draft into the receiving authority's official language
    and run programmatic reverse verification."""
    d = await _draft(session, draft_id, user)
    project = await session.get(AdmissionProject, d.project_id)
    if project is None:
        raise HTTPException(404, "准入项目不存在")
    if filing.filing_out(project)["state"] != "preparing":
        raise HTTPException(409, "申报材料已递交，当前版本已锁定")
    entry = await session.get(AdmissionEntry, project.entry_id)
    if entry is None:
        raise HTTPException(404, "准入事项不存在")
    from app.core.redaction import bind_egress, llm_scope
    from app.iam.models import Organization

    detail = await _entry_detail(session, entry)
    lang_info = await foreign_letter.resolve_submission_language(
        session, entry, str((detail.get("profile") or {}).get("language") or "")
    )
    if lang_info is None:
        raise HTTPException(422, "该事项支持以中文递交，无需另行生成外文正式稿")
    authority_hint = str(detail.get("authority_original") or detail.get("authority_zh") or "")
    org = await session.get(Organization, project.org_id) if project.org_id else None
    with llm_scope():
        bind_egress(session, org)
        fd = await foreign_letter.generate_foreign_letter(
            session, entry, d, lang_info, authority_hint=authority_hint
        )
    d.foreign_draft = fd
    set_detail(
        request,
        summary=f"生成准入申请书外文正式稿“{fd['name_zh']}”",
        after=fd["lang_zh"],
    )
    await session.commit()
    return {"id": str(d.id), **fd, "missing": d.missing or []}


async def _ensure_foreign_draft_matches_entry(
    session: AsyncSession, d: AdmissionDraft
) -> dict:
    fd = d.foreign_draft or {}
    if not fd.get("blocks"):
        raise HTTPException(404, "尚未生成外文正式稿")
    project = await session.get(AdmissionProject, d.project_id)
    if project is not None:
        entry = await session.get(AdmissionEntry, project.entry_id)
        if entry is not None:
            lang_info = await foreign_letter.resolve_submission_language(session, entry)
            if lang_info and fd.get("lang_code") and fd.get("lang_code") != lang_info[0]:
                raise HTTPException(409, f"外文正式稿语种须为{lang_info[1]}，请重新生成")
    return fd


@user_router.get("/drafts/{draft_id}/foreign")
async def get_foreign_draft(draft_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    d = await _draft(session, draft_id, user)
    fd = await _ensure_foreign_draft_matches_entry(session, d)
    return {"id": str(d.id), **fd, "missing": d.missing or []}


@user_router.get("/drafts/{draft_id}/foreign/export.{fmt}")
async def export_foreign_draft(
    draft_id: uuid.UUID, fmt: str, user: UserDep, session: SessionDep
) -> Response:
    d = await _draft(session, draft_id, user)
    fd = await _ensure_foreign_draft_matches_entry(session, d)
    if fmt not in ("docx", "pdf"):
        raise HTTPException(404, "不支持的导出格式")
    project = await session.get(AdmissionProject, d.project_id)
    proj_title = project.title if project else ""
    ctx_title = str(fd.get("title") or "Application")
    doc_label = str(fd.get("name_zh") or f"申请书（{fd.get('lang_zh', '外文')}正式稿）")
    blocks = fd["blocks"]
    if fmt == "docx":
        data = build_docx(Rendered(blocks=blocks), ctx_title, foreign=True)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        import asyncio

        await session.close()
        data = await asyncio.to_thread(
            build_pdf, Rendered(blocks=blocks), ctx_title, foreign=True
        )
        media = "application/pdf"
    return Response(
        data,
        media_type=media,
        headers={
            "Content-Disposition": content_disposition(
                named(proj_title, doc_label, ext=fmt)
            )
        },
    )


@user_router.get("/drafts/{draft_id}/forms/{code}")
async def export_draft_form(draft_id: uuid.UUID, code: str, user: UserDep, session: SessionDep) -> Response:
    """The official form as it was filled when this version was generated."""
    from starlette.concurrency import run_in_threadpool

    from app.forms import service as fs

    d = await _draft(session, draft_id, user)
    kept = next((f for f in d.forms or [] if f.get("code") == code), None)
    form = fs.get_form(code)
    if kept is None or form is None:
        raise HTTPException(404, "这一版没有这份官方表格")
    data, _report, _too_long = await run_in_threadpool(fs.render, form, kept.get("answers") or {}, kept.get("facts") or {})
    docx = form.get("kind") == "docx"
    project = await session.get(AdmissionProject, d.project_id)
    name = named(project.title if project else "", f"{kept.get('name') or code}（{d.created_at.astimezone().strftime('%Y-%m-%d %H%M')} 版）", ext="docx" if docx else "pdf")
    media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if docx else "application/pdf"
    return Response(data, media_type=media, headers={"Content-Disposition": content_disposition(name)})


@admin_router.get("/projects")
async def admin_projects(admin: AdminDep, session: SessionDep) -> list[dict]:
    """Every enterprise's admission material: which country, item and business, how far the
    material is, what the last check found, when it was last generated. For running the service:
    who uses it and where they get stuck."""
    from app.iam.models import Organization
    from app.iam.service import TRIAL_ORG_MARKER

    valid_orgs_rows = (
        await session.scalars(
            select(Organization).where(
                Organization.is_active.is_(True),
                ~Organization.name.like(f"%{TRIAL_ORG_MARKER}%"),
            )
        )
    ).all()
    orgs = {o.id: o.name for o in valid_orgs_rows}
    if not orgs:
        return []
    rows = (
        await session.scalars(
            select(AdmissionProject)
            .where(AdmissionProject.org_id.in_(orgs.keys()))
            .order_by(AdmissionProject.updated_at.desc())
        )
    ).all()
    names = await _user_names(session, [p.created_by for p in rows])
    out = []
    for p in rows:
        entry = await session.get(AdmissionEntry, p.entry_id)
        if entry is not None and entry.merged_into:  # 事项已并入另一项：名称和用户端一样，取主事项的
            entry = await session.get(AdmissionEntry, entry.merged_into) or entry
        is_approved = filing.state_of(p) == "approved"
        found = [] if is_approved else await _check(session, p, record=False)  # 和用户端同一算法
        last_draft = await session.scalar(select(func.max(AdmissionDraft.created_at)).where(AdmissionDraft.project_id == p.id))
        out.append({
            "id": str(p.id), "org": orgs.get(p.org_id, ""), "by": names.get(p.created_by, ""),
            "country_code": entry.country_code if entry else "", "country_zh": COUNTRY_ZH.get(entry.country_code, entry.country_code) if entry else "",
            "entry": entry.name_zh if entry else "", "function_zh": plan.FUNCTIONS.get(p.function_key, ""),
            "from_compliance": p.task_id is not None, "approval": approval_out(p),
            "filing": {k: v for k, v in filing.filing_out(p).items() if k in ("state", "state_zh", "submitted_on", "result_on", "rounds")},
            "approval_status": (approval_out(p) or {}).get("status", ""),
            "approval_dates": {k: (approval_out(p) or {}).get(k, "") for k in ("issued_on", "expires_on", "long_term")},
            "missing": [] if is_approved else missing_parts([f for f in found if f["blocking"]]),
            "last_draft": last_draft.isoformat() if last_draft else None,
            "created_at": p.created_at.isoformat(), "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            "ready": True if is_approved else not any(f["blocking"] for f in found),
            "summary": "已取得批文" if is_approved else check_summary(found),
        })
    return out


async def _load_admin_entries_with_counts(
    session: AsyncSession, country: str | None = None
) -> tuple[list[tuple[AdmissionEntry, dict]], dict[str, int]]:
    from sqlalchemy import case

    from app.knowledge.whitepaper_graph import UNNAMED_ROLE

    shown_first = case(
        (AdmissionEntry.merged_into.is_not(None), 1),
        (AdmissionEntry.review_status.in_(SHOWN), 0),
        else_=2,
    )
    q = select(AdmissionEntry).order_by(
        shown_first, AdmissionEntry.country_code, AdmissionEntry.name_zh
    )
    if country:
        q = q.where(AdmissionEntry.country_code == country.upper())
    counts = dict(
        (await session.execute(text("select entry_id, count(*) from admission_steps group by 1"))).all()
    )
    rows = list((await session.execute(q)).scalars())
    outs = await _named(session, [_entry_out(e, counts.get(e.id, 0)) for e in rows])
    all_names = dict(
        (await session.execute(select(AdmissionEntry.id, AdmissionEntry.name_zh))).all()
    )
    steps_by_entry: dict[uuid.UUID, list[AdmissionStep]] = {}
    if rows:
        step_rows = (
            await session.scalars(
                select(AdmissionStep).where(AdmissionStep.entry_id.in_([e.id for e in rows]))
            )
        ).all()
        for st in step_rows:
            steps_by_entry.setdefault(st.entry_id, []).append(st)

    status_counts = {"shown": 0, "incomplete": 0, "merged": 0, "out_of_scope": 0, "all": len(outs)}
    paired: list[tuple[AdmissionEntry, dict]] = []
    for e, o in zip(rows, outs, strict=True):
        unnamed = not (o["authority_zh"] or "").strip() or UNNAMED_ROLE.search(
            re.sub(r"[（(][^）)]*[）)]", "", o["authority_zh"] or "").strip()
        )
        if unnamed and e.review_status != "out_of_scope" and e.merged_into is None:
            d = await _entry_detail(session, e)
            o["authority_zh"], o["authority_why"] = authority_short(d), d["authority_why"]
        elif unnamed:
            o["authority_zh"] = "条文未写明"
        else:
            o["authority_zh"] = display_authority(o["authority_zh"], e.country_code)
            o["authority_why"] = authority_why(
                {"authority_note": (e.profile or {}).get("authority_note", ""), "authority_zh": o["authority_zh"]}
            )
        o["laws"], _ = await legal_basis(session, e, await _basis(session, e.evidence_clause_id))
        e_steps = steps_by_entry.get(e.id, [])
        o["amount_conflicts"] = await amount_conflicts(session, e, e_steps)
        o["user_shown"] = user_shown(o)
        o["merged_into_name"] = str(all_names.get(e.merged_into, "")) if e.merged_into else ""
        o["out_reason"] = (e.summary_zh or "").strip()[:80] if e.review_status == "out_of_scope" else ""

        if e.merged_into is not None:
            status_counts["merged"] += 1
        elif e.review_status in SHOWN:
            if o["user_shown"]:
                status_counts["shown"] += 1
            else:
                status_counts["incomplete"] += 1
        elif e.review_status == "out_of_scope":
            status_counts["out_of_scope"] += 1
        paired.append((e, o))
    return paired, status_counts


@admin_router.get("/counts")
async def admin_entry_counts(
    admin: AdminDep, session: SessionDep, country: str | None = None
) -> dict:
    _, status_counts = await _load_admin_entries_with_counts(session, country=country)
    return {"status_counts": status_counts, **status_counts}


@admin_router.get("/entries")
async def admin_entries(
    admin: AdminDep,
    response: Response,
    session: SessionDep,
    country: str | None = None,
    status: str | None = None,
) -> list[dict]:
    import json

    paired, status_counts = await _load_admin_entries_with_counts(session, country=country)
    response.headers["X-Status-Counts"] = json.dumps(status_counts)
    response.headers["Access-Control-Expose-Headers"] = "X-Status-Counts"

    if status == "shown":
        return [o for e, o in paired if e.merged_into is None and e.review_status in SHOWN and bool(o.get("user_shown"))]
    if status == "incomplete":
        return [o for e, o in paired if e.merged_into is None and e.review_status in SHOWN and not bool(o.get("user_shown"))]
    if status == "merged":
        return [o for e, o in paired if e.merged_into is not None]
    if status and status != "all":
        return [o for e, o in paired if e.merged_into is None and e.review_status == status]
    return [o for _, o in paired]


@admin_router.get("/forms")
async def admin_forms(admin: AdminDep, country: str) -> list[dict]:
    """这个国家的官方表格（修改资料时选这一项用哪几张）。"""
    from app.forms.service import CatalogError, catalog

    try:
        return [{"code": f["code"], "name_zh": f["name_zh"].split("：", 1)[-1]} for f in catalog().values() if f["country"] == country.upper()]
    except CatalogError:
        return []


@admin_router.get("/entries/{entry_id}")
async def admin_entry(entry_id: uuid.UUID, admin: AdminDep, session: SessionDep) -> dict:
    entry = await session.get(AdmissionEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "准入事项不存在")
    return await _entry_detail(session, entry)


class StepEdit(BaseModel):
    id: uuid.UUID | None = None  # 空 = 管理员新加的环节
    title_zh: str = Field(min_length=1, max_length=256)
    detail_zh: str | None = Field(default=None, max_length=2000)
    materials: list[str] = Field(default_factory=list, max_length=60)


class LinkEdit(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=8, max_length=600, pattern=r"^https?://")


class ConditionEdit(BaseModel):
    condition: str = Field(default="", max_length=200)
    proof: str = Field(default="", max_length=120)
    what: str = Field(default="", max_length=160)


class PortalEdit(BaseModel):
    site: str = Field(default="", max_length=600)  # 空 = 用平台核实过的入口
    links: list[LinkEdit] = Field(default_factory=list, max_length=10)


# 事项卡上能改的固定栏目（与用户端事项卡、项目页一致）
PROFILE_FIELDS = ("eligibility", "language", "local_test", "local_rep", "validity", "authority_roles")
PROFILE_ZH = {"eligibility": "申请人资格", "language": "申请语言", "local_test": "本地检测", "local_rep": "本地代表", "validity": "有效期", "authority_roles": "受理分工"}


class PlanEdit(BaseModel):
    name: str = Field(min_length=1, max_length=400)
    kind: Literal["info", "form", "application", "original", "none"]
    what: str = Field(default="", max_length=120)


class ContentIn(BaseModel):
    """What an administrator corrects in an admission item (base data every enterprise uses)."""
    name_zh: str | None = Field(default=None, min_length=2, max_length=256)
    authority_zh: str | None = Field(default=None, max_length=256)
    # 条文没写受理机构、由管理员补上时，写明根据什么推断（显示在事项详情里，不当作条文原话）
    authority_note: str | None = Field(default=None, max_length=300)
    summary_zh: str | None = Field(default=None, max_length=4000)
    # 没有 id 的环节新建；steps_full 时按给出的顺序重排、没给出的删除（管理端修改资料页给的是全部环节）
    steps: list[StepEdit] | None = None
    steps_full: bool = False
    materials_plan: list[PlanEdit] | None = None
    # 固定栏目：写“待核实”= 条文未明确（用户端列进“条文未明确”），写“不适用”= 不显示
    profile: dict[str, str] | None = None
    cited_laws: list[str] | None = Field(default=None, max_length=10)  # 依据法规（法律、法令、部令）
    portal: PortalEdit | None = None
    form_codes: list[str] | None = Field(default=None, max_length=10)  # 用哪几张官方表格
    forms_auto: bool = False  # 官方表格改回按事项自动匹配
    applicant: str | None = Field(default=None, max_length=200)
    conditions: list[ConditionEdit] | None = Field(default=None, max_length=12)


@admin_router.put("/entries/{entry_id}/content")
async def edit_entry(
    entry_id: uuid.UUID, body: ContentIn, request: Request, admin: AdminDep, session: SessionDep
) -> dict:
    """Correct what extraction got wrong: name, receiving body, summary, each step's name and
    materials, and how each material is prepared (the filing checklist). Takes effect at once; the
    fields changed are marked so later rebuild commands leave them alone; enterprises preparing this
    item are told on their notice list (the item's content changed)."""
    entry = await session.get(AdmissionEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "准入事项不存在")
    edited = set(entry.edited or [])
    plan_notes: list[str] = []
    before: dict = {}
    after: dict = {}
    for field in ("name_zh", "authority_zh", "summary_zh"):
        value = getattr(body, field)
        if value is None:
            continue
        value = " ".join(value.split()) if field != "summary_zh" else value.strip()
        if value != (getattr(entry, field) or ""):
            before[field], after[field] = getattr(entry, field), value
            setattr(entry, field, value)
            edited.add(field)
    if body.authority_note is not None:
        note = " ".join(body.authority_note.split())
        if note != (entry.profile or {}).get("authority_note", ""):
            before["受理机构依据"], after["受理机构依据"] = (entry.profile or {}).get("authority_note", ""), note
            entry.profile = {**(entry.profile or {}), "authority_note": note}
            edited.add("authority_zh")
    profile = dict(entry.profile or {})
    for k, v in (body.profile or {}).items():
        if k not in PROFILE_FIELDS:
            raise HTTPException(422, "提交了系统不支持修改的事项栏目，请刷新页面后重试")
        v = " ".join(str(v or "").split())[:300] or ("" if k == "authority_roles" else "待核实")
        if v != (profile.get(k) or ("" if k == "authority_roles" else "待核实")):
            before[PROFILE_ZH[k]], after[PROFILE_ZH[k]] = profile.get(k, ""), v
            profile[k] = v
            edited.add(f"profile:{k}")
    if body.conditions is not None:
        conds = [c for c in body.conditions if c.condition.strip() or c.proof.strip()]
        lacking = [c.condition for c in conds if not c.proof.strip()]
        if lacking:
            raise HTTPException(422, f"每条资格条件都要写证明它的原件：“{lacking[0]}”还没写")
        if any(not c.condition.strip() for c in conds):
            raise HTTPException(422, "证明原件要对应一条资格条件")
        new_c = [{"condition": " ".join(c.condition.split()), "proof": " ".join(c.proof.split()), "what": " ".join(c.what.split())} for c in conds]
        applicant = " ".join((body.applicant if body.applicant is not None else profile.get("applicant", "")).split()) or "待核实"
        if new_c != (profile.get("conditions") or []) or applicant != profile.get("applicant"):
            before["申请人资格"] = profile.get("eligibility", "")
            profile["conditions"], profile["applicant"] = new_c, applicant
            profile["eligibility"] = (f"{applicant}；" if applicant != "待核实" else "") + "；".join(c["condition"] for c in new_c) if new_c else applicant
            profile["eligibility_proofs"] = list({c["proof"]: {"name": c["proof"], "what": c["what"]} for c in new_c}.values())
            after["申请人资格"] = profile["eligibility"]
            edited.update({"profile:eligibility", "profile:conditions"})
    if body.cited_laws is not None:
        laws = [" ".join(x.split())[:120] for x in body.cited_laws if x and x.strip()]
        if laws != list(profile.get("cited_laws") or []):
            before["依据法规"], after["依据法规"] = profile.get("cited_laws") or [], laws
            profile["cited_laws"] = laws
            edited.add("profile:cited_laws")
    if body.portal is not None:
        portal = {"site": body.portal.site.strip(), "links": [link.model_dump() for link in body.portal.links]} if body.portal.site.strip() else None
        if portal != profile.get("portal"):
            if portal and not portal["site"].startswith(("http://", "https://")):
                raise HTTPException(422, "官网地址应以 http:// 或 https:// 开头")
            before["在哪递交"], after["在哪递交"] = profile.get("portal") or "平台核实的入口", portal or "平台核实的入口"
            profile["portal"] = portal
            edited.add("profile:portal")
    if body.forms_auto or body.form_codes is not None:
        codes = None if body.forms_auto else list(dict.fromkeys(body.form_codes or []))
        if codes != profile.get("form_codes"):
            from app.forms.service import catalog

            unknown = [c for c in codes or [] if c not in catalog()]
            if unknown:
                raise HTTPException(422, "所选的官方表格编号不存在，请从平台表单目录中重新选择")
            before["官方表格"], after["官方表格"] = profile.get("form_codes") or "自动匹配", codes if codes is not None else "自动匹配"
            profile["form_codes"] = codes
            edited.add("profile:form_codes")
    if profile != (entry.profile or {}):
        entry.profile = profile
    if body.steps is not None:
        from app.admission.models import AdmissionProgress

        rows = {s.id: s for s in (await session.scalars(select(AdmissionStep).where(AdmissionStep.entry_id == entry.id))).all()}
        kept = set()
        for seq, st in enumerate(body.steps, 1):
            materials = [m.strip() for m in st.materials if m and m.strip()]
            title = " ".join(st.title_zh.split())
            if st.id is None:
                session.add(AdmissionStep(entry_id=entry.id, seq=seq, title_zh=title, detail_zh=(st.detail_zh or "").strip(), materials=materials, evidence_quote="", edited=True))
                after[f"新增环节{seq}"] = {"名称": title, "材料": materials}
                continue
            row = rows.get(st.id)
            if row is None:
                raise HTTPException(422, "办理环节不属于这个事项")
            kept.add(row.id)
            detail_ = row.detail_zh if st.detail_zh is None else st.detail_zh.strip()
            if not body.steps_full:
                seq = row.seq
            if title != row.title_zh or materials != (row.materials or []) or seq != row.seq or detail_ != row.detail_zh:
                before[f"环节{row.seq}"] = {"名称": row.title_zh, "材料": row.materials or []}
                after[f"环节{seq}"] = {"名称": title, "材料": materials}
                row.title_zh, row.materials, row.detail_zh, row.seq, row.edited = title, materials, detail_, seq, True
        for sid, row in rows.items():
            if body.steps_full and sid not in kept:
                before[f"删除环节{row.seq}"] = {"名称": row.title_zh, "材料": row.materials or []}
                after[f"删除环节{row.seq}"] = "已删除"
                from sqlalchemy import update

                await session.execute(update(AdmissionProgress).where(AdmissionProgress.step_id == sid).values(step_id=None))
                await session.delete(row)
        if any(k.startswith(("新增环节", "删除环节")) for k in after) or (body.steps_full and any(k.startswith("环节") for k in after)):
            edited.add("steps")
    if body.materials_plan is not None:
        old = {p.get("name"): p for p in entry.materials_plan or []}
        plan = []
        for p in body.materials_plan:
            fact = (old.get(p.name) or {}).get("fact", "") if p.kind == "info" else ""
            if p.name not in old and p.kind not in ("original", "none"):
                raise HTTPException(422, f"新加的“{p.name}”只能是“企业递交时自带”")
            if p.kind == "info" and not fact:
                raise HTTPException(422, f"“{p.name}”没有对应的填写项，不能写进申请材料；请选“企业递交时自带”或“不列入”")
            plan.append({"name": p.name.strip(), "kind": p.kind, "fact": fact, "what": " ".join(p.what.split())})
        if plan != list(entry.materials_plan or []):
            kind_zh = {"info": "写进申请材料", "form": "官方表格", "application": "申请材料本身", "original": "企业递交时自带", "none": "不列入"}
            for p in plan:
                o = old.get(p["name"]) or {}
                if o.get("kind") != p["kind"] or (o.get("what") or "") != p["what"]:
                    plan_notes.append(f"“{p['name']}”改为{kind_zh[p['kind']]}" + (f"（{p['what']}）" if p["what"] else ""))
            before["递交清单"], after["递交清单"] = [f"{x.get('name')}：{kind_zh.get(x.get('kind'), '')}" for x in entry.materials_plan or []], [f"{x['name']}：{kind_zh[x['kind']]}" for x in plan]
            entry.materials_plan = plan
            edited.add("materials_plan")
    if not after:
        return await _entry_detail(session, entry)
    entry.edited = sorted(edited)
    summary = f"修改准入事项“{entry.name_zh}”的资料" + ("；递交清单：" + "；".join(plan_notes[:6]) if plan_notes else "")
    set_detail(request, summary=summary, before=before, after=after)
    await session.commit()
    await session.refresh(entry)
    return await _entry_detail(session, entry)


@admin_router.patch("/entries/{entry_id}")
async def review_entry(
    entry_id: uuid.UUID, body: ReviewIn, admin: AdminDep, session: SessionDep
) -> dict:
    entry = await session.get(AdmissionEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "准入事项不存在")
    if body.review_status:
        entry.review_status = body.review_status
    if body.unmerge:
        entry.merged_into = None
    await session.commit()
    await session.refresh(entry)
    return _entry_out(entry)



# ---------------- 审核问题与申报资料（需求 2.6） ----------------


def _issue_out(x: AdmissionIssue, names: dict) -> dict:
    return {
        "id": str(x.id),
        "kind": x.kind,
        "target": x.target,
        "progress_id": str(x.progress_id) if x.progress_id else None,
        "question": x.question,
        "answer": x.answer,
        "state": x.state,
        "updated_by": names.get(x.updated_by or x.created_by, ""),
        "updated_at": (x.updated_at or x.created_at).isoformat() if (x.updated_at or x.created_at) else None,
    }


class IssueIn(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    progress_id: uuid.UUID | None = None
    # manual = 自己记的；authority = 受理机构要求补正的（补正中时记）
    kind: Literal["manual", "authority"] = "manual"


class IssuePatch(BaseModel):
    answer: str | None = Field(default=None, max_length=8000)
    state: Literal["open", "closed"] | None = None


async def _own_issue(session: AsyncSession, issue_id: uuid.UUID, user: Principal) -> AdmissionIssue:
    row = await session.get(AdmissionIssue, issue_id)
    if row is None or row.org_id != user.org_id:
        raise HTTPException(404, "审核问题不存在")
    return row


@user_router.post("/projects/{project_id}/issues", status_code=201)
async def add_issue(project_id: uuid.UUID, body: IssueIn, request: Request, user: UserDep, session: SessionDep) -> dict:
    from app.admission.models import AdmissionProgress

    project = await _project(session, project_id, user)
    if body.progress_id is not None:
        prog = await session.scalar(
            select(AdmissionProgress.id).where(
                AdmissionProgress.id == body.progress_id,
                AdmissionProgress.project_id == project.id,
            )
        )
        if prog is None:
            raise HTTPException(422, "关联的办理环节不存在或不属于当前项目")
    q_text = body.question.strip()
    if not q_text:
        raise HTTPException(422, "问题描述不能为空")
    row = AdmissionIssue(
        org_id=user.org_id, project_id=project.id, progress_id=body.progress_id, kind=body.kind,
        question=q_text, created_by=user.user_id, updated_by=user.user_id,
    )
    session.add(row)
    set_detail(request, summary=f"准入办理“{project.title}”记录审核问题")
    await session.commit()
    await session.refresh(row)
    return _issue_out(row, await _user_names(session, [user.user_id]))


@user_router.patch("/issues/{issue_id}")
async def update_issue(issue_id: uuid.UUID, body: IssuePatch, request: Request, user: UserDep, session: SessionDep) -> dict:
    row = await _own_issue(session, issue_id, user)
    before = {"state": row.state, "answer": row.answer}
    if body.answer is not None:
        row.answer = body.answer.strip()
    if body.state is not None:
        row.state = body.state
    row.updated_by = user.user_id
    set_detail(request, summary=f"处理准入审核问题：{row.question[:60]}", before=before, after={"state": row.state, "answer": row.answer})
    await session.commit()
    await session.refresh(row)
    return _issue_out(row, await _user_names(session, [user.user_id]))


@user_router.delete("/issues/{issue_id}")
async def delete_issue(issue_id: uuid.UUID, request: Request, user: UserDep, session: SessionDep) -> dict:
    row = await _own_issue(session, issue_id, user)
    set_detail(request, summary="删除准入审核问题")
    await session.delete(row)
    await session.commit()
    return {"deleted": str(issue_id)}
