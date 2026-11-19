# ruff: noqa: E501
"""Legal terminology endpoints: source-derived reading aids and exceptional ambiguities."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.iam.deps import Principal, require_admin_side, require_platform_admin, require_user_side
from app.knowledge.legal_terms import (
    CATALOG,
    current_definitions,
    rebuild_library,
    register_critical_ambiguity,
    register_reference,
    term_payload,
)
from app.knowledge.models import ReviewPoint

admin = APIRouter(
    prefix="/api/admin/knowledge/terminology",
    tags=["terminology-admin"],
    dependencies=[Depends(require_admin_side)],
)
user = APIRouter(
    prefix="/api/knowledge/terminology",
    tags=["terminology"],
    dependencies=[Depends(require_user_side)],
)
Session = Annotated[AsyncSession, Depends(get_session)]
Admin = Annotated[Principal, Depends(require_admin_side)]
PlatformAdmin = Annotated[Principal, Depends(require_platform_admin)]
User = Annotated[Principal, Depends(require_user_side)]


@user.get("")
@admin.get("")
async def definitions(
    session: Session,
    country_code: str = Query(pattern="^[A-Za-z]{2}$"),
    document_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
    q: str = Query(default="", max_length=150),
):
    rows = await current_definitions(session, country_code, document_id, version_id)
    from app.knowledge.legal_terms import PRIORITY

    if document_id is None:
        # 国家的术语表：只列用户法规库范围内的文件里的定义，且要有中文（没有中文译名也没有中文释义的
        # 只在阅读该文件时作为原文释义出现）
        from app.knowledge.library_view import library

        entries = await library(session, country_code)
        scope = {e.document.id for e in entries}
        doc_of = {e.document.id: e.document for e in entries}
        rows = [
            t
            for t in rows
            if t.document_id in scope
            and t.business_close != "not_term"  # 误识别成定义的句子不列
            and (
                (t.definition_zh or "").strip()
                or (t.target_zh or "").strip().casefold() != (t.source_term or "").strip().casefold()
            )
        ]

    # 国家术语表：先列车企做数据出境一定要懂的核心概念，再列相关的，关系较远的放最后；同级里法律定义在前
    # 同级里：通用核心概念（个人数据、数据控制者……）在前，再按所在法规——个人数据保护类、核心法规在前
    from app.knowledge.business_topic import TOPICS
    from app.knowledge.term_tags import LEVELS

    catalog = {code for code, _, _ in CATALOG}
    topic_rank = {k: i for i, (k, _) in enumerate(TOPICS)}
    weight_rank = {"core": 0, "supporting": 1}

    def rank(t):
        if document_id is not None:
            return (0, PRIORITY[t.source_kind], t.source_term.casefold(), str(t.id))
        doc = doc_of.get(t.document_id)
        return (
            LEVELS.get(t.business_close or "", 1),
            t.concept_code not in catalog,
            topic_rank.get(getattr(doc, "business_topic", None) or "", 9),
            weight_rank.get(getattr(doc, "business_weight", None) or "", 2),
            PRIORITY[t.source_kind],
            t.source_term.casefold(),
            str(t.id),
        )

    rows.sort(key=rank)
    items = [term_payload(t) for t in rows]
    import re as _re

    _lead_noise_zh = _re.compile(
        r"^\s*(?:[-•–—]|\.\d+|\d+\.)\s*(?:[A-Za-zÀ-ÿ'’\-\s]{2,50}\s*:\s*)?"
    )
    _lead_noise_orig = _re.compile(r"^\s*(?:[-•–—]|\.\d+|\d+\.)\s*")
    _tail_bleed_zh = _re.compile(r"\n(?:\d+\s*参见|主管机关就第).*$", _re.S)
    for item in items:
        raw_src = str(item.get("source_term") or "").strip()
        clean_src = _re.sub(r"^(?:ou|or)\s+", "", raw_src, flags=_re.I)
        clean_src = _re.sub(r"^[.\-•–—0-9٠-٩]+\s*", "", clean_src)
        clean_src = _re.sub(r"(?<=[A-Za-z])\d+$", "", clean_src)
        clean_src = _re.sub(r"[,;，；:：]+$", "", clean_src).strip()
        if clean_src:
            item["source_term"] = clean_src

        target = _re.sub(r"[,;，；:：]+$", "", str(item.get("target_zh") or "").strip()).strip()
        if _re.match(r"^(?:ou|or)\s+", raw_src, flags=_re.I) and target.startswith("或") and len(target) >= 3:
            target = target[1:].strip()

        zh = str(item.get("definition_zh") or "")
        zh = _lead_noise_zh.sub("", zh)
        zh = _tail_bleed_zh.sub("", zh).strip()
        zh = _re.sub(r"(?<=个人数据)\d{1,3}(?=保护法)", "", zh)
        if target and zh.startswith("或" + target):
            zh = zh[1:]

        if target == "该机构" and "国家个人数据保护" in zh:
            target = "国家个人数据保护局（L’Instance）"
            zh = _re.sub(r"^该机构(?=：|:)", "国家个人数据保护局（L’Instance）", zh)
        elif target in ("主管机关/被许可方", "国家电信监管局/被许可方") and "国家电信监管局" in zh:
            target = "主管机关（NTRA）"
            zh = "主管机关：指埃及国家电信监管局（NTRA）。"
        elif target == "主管机关" and "资讯通信媒体发展局" in zh:
            target = "资讯通信媒体发展局（Authority）"
        elif target == "委员会" and "个人数据保护委员会" in zh:
            target = "个人数据保护委员会（Commission）"
        item["target_zh"] = target

        if orig_def := item.get("definition_original"):
            item["definition_original"] = _lead_noise_orig.sub("", str(orig_def)).strip()

        # 中文释义是译文里的原样摘录，从术语本身开始，前引号落在摘录之外：显示时补回，读起来不缺半个引号
        from app.knowledge.legal_terms import NEXT_ITEM_ZH

        if nxt := NEXT_ITEM_ZH.search(zh):  # 旧摘录里连带了下一项的，只显示本项
            zh = zh[: nxt.start() + 1]
        if target and zh.startswith(target + "”"):
            zh = "“" + zh
        item["definition_zh"] = zh
        orig_cur = str(item.get("definition_original") or "").strip()
        if orig_cur and zh:
            norm_z = _re.sub(r"[\s“”\"'‘’（）()【】\[\]，,。.；;：:、·\-—_]", "", zh).lower()
            norm_o = _re.sub(r"[\s“”\"'‘’（）()【】\[\]，,。.；;：:、·\-—_]", "", orig_cur).lower()
            if norm_z == norm_o or (
                str(item.get("language") or "").lower() == "zh"
                and not _re.search(r"[A-Za-zÀ-ÿ\u0600-\u06FF]", orig_cur)
            ):
                item["definition_original"] = ""
    if rows:
        # 出处统一写成“《法规中文名》第X条”，消除“· 四 · v1”等内部切分标签外露
        from app.ingestion.models import Document
        from app.knowledge.reading import article_label

        doc_meta = {
            did: (title_val, dtype_val)
            for did, title_val, dtype_val in (
                await session.execute(
                    select(
                        Document.id,
                        func.coalesce(Document.title_zh, Document.title),
                        Document.document_type,
                    ).where(Document.id.in_({t.document_id for t in rows if t.document_id}))
                )
            ).all()
        }
        for t, item in zip(rows, items, strict=True):
            parts = (t.source_reference or "").split(" · ")
            raw_num = parts[-2].strip() if len(parts) >= 3 else ""
            if _re.fullmatch(r"(?:v\d+(?:\.\d+)*|(?:ch|sec|part|s|p)[-_]?\d+)", raw_num, flags=_re.I):
                raw_num = ""
            number = article_label(raw_num) if raw_num else ""
            if number and _re.fullmatch(r"[一二三四五六七八九十百零〇]+", number):
                number = f"第{number}条"
            elif number and not number.startswith("第") and _re.fullmatch(r"\d+\.\d+", number):
                dtype_cur = (doc_meta.get(t.document_id, ("", ""))[1] or "").upper()
                if dtype_cur in ("LAW", "ACT", "REGULATION"):
                    number = f"第{number.split('.')[0]}条"
                else:
                    number = f"第{number}节"
            if t.document_id in doc_meta:
                item["source_reference"] = f"《{doc_meta[t.document_id][0]}》{number}"
            elif item.get("source_reference"):
                item["source_reference"] = _re.sub(r"\s*·\s*v\d+(?:\.\d+)*$", "", str(item["source_reference"]))
    present = {t.concept_code for t in rows}
    lexical_notes = {
        "personal_data": "指与已识别或可识别自然人相关的各类信息；具体外延与豁免情形以所涉法域现行法规为准。",
        "data_controller": "指单独或共同决定个人数据处理目的与方式的组织或自然人；具体权责以所涉法域现行法规为准。",
        "data_processor": "指受数据控制者委托、代表其处理个人数据的组织或自然人；具体义务以所涉法域现行法规为准。",
        "data_subject": "指其个人数据受到收集、存储、使用或传输等处理的已识别或可识别自然人。",
        "sensitive_data": "指一旦泄露或不当使用易对自然人权益造成重大影响的特定类别个人数据；法定目录依属地法规确定。",
        "processing": "指对个人数据开展的收集、记录、存储、使用、加工、传输、提供或删除等一项或多项操作。",
        "consent": "指数据主体在知情前提下就其个人数据处理作出的自愿、明确意思表示；单独同意或书面形式要求依属地法规确定。",
        "data_intermediary": "指依书面合同代表另一组织处理个人数据的外部数据处理机构（不含该组织内部雇员）。",
        "data_protection_officer": "指组织依法指定负责监督数据保护合规实施并与监管机构及个人沟通的专责人员。",
        "cross_border_transfer": "指将境内收集或产生的个人数据或业务数据向境外接收方传输、提供或开放远程访问的活动。",
    }
    for code, target, aliases in CATALOG:
        if code in present:
            continue
        items.append(
            dict(
                id=code,
                concept_code=code,
                country_code=None,
                language="multi",
                source_term=" / ".join(aliases.get("en", [])),
                target_zh=target,
                aliases=[a for lang in aliases.values() for a in lang],
                definition_original=None,
                definition_zh=lexical_notes.get(code, f"跨法域通用规范译名（{target}），具体法定范围按所适用法规条文认定。"),
                source_url=None,
                source_reference="通用译名",
                source_kind="terminology_only",
                source_label="通用译名",
                document_id=None,
                version_id=None,
                verification_status="lexical_only",
            )
        )
    if q.strip():
        items = [
            t
            for t in items
            if q.strip().casefold() in " ".join(str(v or "") for v in t.values()).casefold()
        ]
    return {"items": items, "total": len(items)}


class RebuildIn(BaseModel):
    country_code: str | None = Field(default=None, pattern="^[A-Za-z]{2}$")
    document_id: uuid.UUID | None = None


@admin.post("/rebuild")
async def rebuild(body: RebuildIn, _: PlatformAdmin, session: Session):
    result = await rebuild_library(session, body.country_code, body.document_id)
    await session.commit()
    return result


class ReferenceIn(BaseModel):
    clause_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    document_title: str | None = Field(default=None, max_length=500)
    clause_number: str | None = Field(default=None, max_length=120)
    source_term: str = Field(min_length=2, max_length=100)
    target_zh: str = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=12, max_length=6000)
    source_kind: Literal[
        "law_definition", "regulator_guidance", "official_parallel", "international_reference"
    ]
    country_code: str | None = Field(default=None, pattern="^[A-Za-z]{2}$")
    applies_to_document_id: uuid.UUID | None = None


@admin.post("/references")
async def reference(body: ReferenceIn, admin_user: Admin, session: Session):
    from sqlalchemy import or_

    from app.ingestion.models import Clause, Document, DocumentVersion

    resolved_clause_id = body.clause_id
    if resolved_clause_id is None:
        stmt = (
            select(Clause.id)
            .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.state == "parsed")
            .order_by(DocumentVersion.version_no.desc(), Clause.ordinal.asc())
        )
        if body.document_id is not None:
            stmt = stmt.where(Document.id == body.document_id)
        elif body.document_title and body.document_title.strip():
            dt_val = body.document_title.strip().strip("《》")
            stmt = stmt.where(
                or_(Document.title_zh.ilike(f"%{dt_val}%"), Document.title.ilike(f"%{dt_val}%"))
            )
        elif body.country_code:
            stmt = stmt.where(Document.country_code == body.country_code.upper())
        if body.clause_number and body.clause_number.strip():
            stmt = stmt.where(Clause.clause_number.ilike(f"%{body.clause_number.strip()}%"))
        else:
            snippet = body.quote.strip()[:40]
            stmt = stmt.where(Clause.original_text.ilike(f"%{snippet}%"))
        resolved_clause_id = await session.scalar(stmt.limit(1))
        if resolved_clause_id is None:
            raise HTTPException(400, "未匹配到对应的已归档法规条款，请确认所选法规与条号")

    payload = {
        "clause_id": resolved_clause_id,
        "source_term": body.source_term,
        "target_zh": body.target_zh,
        "quote": body.quote,
        "source_kind": body.source_kind,
        "country_code": body.country_code,
        "applies_to_document_id": body.applies_to_document_id,
    }
    try:
        row = await register_reference(session, **payload, actor_id=admin_user.user_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await session.commit()
    return term_payload(row)


class AmbiguityIn(BaseModel):
    clause_id: uuid.UUID
    source_term: str = Field(min_length=2, max_length=100)
    question: str = Field(min_length=5, max_length=1500)
    impact: str = Field(min_length=5, max_length=1500)


@admin.post("/ambiguities")
async def ambiguity(body: AmbiguityIn, _: Admin, session: Session):
    try:
        row = await register_critical_ambiguity(session, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await session.commit()
    return {"id": str(row.id), "status": row.status}


@admin.get("/ambiguities")
async def ambiguities(_: Admin, session: Session):
    rows = (
        await session.scalars(
            select(ReviewPoint)
            .where(ReviewPoint.point_code.like("TERM-%"))
            .order_by(ReviewPoint.created_at.desc())
        )
    ).all()
    return [
        dict(
            id=str(r.id),
            country_code=r.country_code,
            title=r.title,
            question=r.question,
            impact=r.impact_on_conclusion,
            status=r.status,
        )
        for r in rows
    ]
