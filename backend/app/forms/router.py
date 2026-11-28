# ruff: noqa: E501
"""Official forms for organisations: pick a form, see what is already known from the assessment,
answer what is missing, download the publisher's own file with the values written in."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.document_templates import DOC_FACT_PREFIX, task_values
from app.analysis.models import AnalysisTask
from app.analysis.report_exporter import content_disposition
from app.audit.service import set_detail
from app.core.db import get_session
from app.forms import f118_matrix, service
from app.forms.models import FormInstance
from app.iam.deps import Principal, require_user_side

router = APIRouter(prefix="/api/forms", tags=["official-forms"])
UserDep = Annotated[Principal, Depends(require_user_side)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class CreateIn(BaseModel):
    form_code: str = Field(min_length=2, max_length=64)
    task_id: uuid.UUID | None = None


class AnswersIn(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict, max_length=300)


async def _task(session: AsyncSession, task_id: uuid.UUID, user: Principal) -> AnalysisTask:
    from app.analysis.chat_advisor import viewer_owner

    task = await session.get(AnalysisTask, task_id)
    if task is None or task.org_id != user.org_id:
        raise HTTPException(404, "未找到该合规评估项目")
    owner = viewer_owner(task)
    if owner is not None and owner != str(user.user_id):
        raise HTTPException(404, "未找到该合规评估项目")
    return task


async def _instance(
    session: AsyncSession, instance_id: uuid.UUID, user: Principal, *, for_update: bool = False
) -> FormInstance:
    if for_update:
        row = await session.scalar(
            select(FormInstance)
            .where(FormInstance.id == instance_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    else:
        row = await session.get(FormInstance, instance_id)
    if row is None or row.org_id != user.org_id:
        raise HTTPException(404, "表单不存在")
    return row


async def _admission_country_facts(session: AsyncSession, org_id, country: str | None) -> dict[str, str]:
    """Company & contact facts already entered on an AdmissionProject of the same country."""
    if not country:
        return {}
    from app.admission.models import AdmissionEntry, AdmissionProject

    projs = (
        await session.scalars(
            select(AdmissionProject)
            .join(AdmissionEntry, AdmissionEntry.id == AdmissionProject.entry_id)
            .where(AdmissionProject.org_id == org_id, AdmissionEntry.country_code == country.upper())
            .order_by(AdmissionProject.updated_at.desc())
        )
    ).all()
    out: dict[str, str] = {}
    for p in projs:
        pf = p.facts or {}
        if p.applicant and "company_name" not in out:
            out["company_name"] = p.applicant.strip()
        if p.vehicle_model and "vehicle_model" not in out:
            out["vehicle_model"] = p.vehicle_model.strip()
        for raw_k, raw_v in pf.items():
            v = str(raw_v or "").strip()
            if not v:
                continue
            k = raw_k.removeprefix(DOC_FACT_PREFIX)
            target_k = "company_name" if k == "applicant" else k
            out.setdefault(target_k, v)
    return out


def _latin_part_if_bilingual(val: str, country: str | None) -> str:
    """When a foreign form carries a bilingual '中文名称（Latin Legal Name）' string, keep only the Latin legal name."""
    return service.latin_part_if_bilingual(val, country)


async def org_profile(session: AsyncSession, org_id, country: str | None = None) -> dict[str, str]:
    """Company facts the enterprise already gave in an official form of the same country, plus its
    local applicant facts on an admission project there — scoped by country so another country's
    subsidiary or foreign-language fields never leak across jurisdictions."""
    rows = (await session.execute(
        select(FormInstance.form_code, FormInstance.answers).where(FormInstance.org_id == org_id).order_by(FormInstance.updated_at.desc())
    )).all()
    cc = country.upper() if country else None
    scoped = [
        (c, a or {})
        for c, a in rows
        if not cc or (service.get_form(c) or {}).get("country") == cc
    ]
    out = service.profile_values(scoped)
    if cc:
        for k, v in (await _admission_country_facts(session, org_id, cc)).items():
            out.setdefault(k, v)
    if not out.get("company_name"):
        from app.admission.router import _known_company

        name = await _known_company(session, org_id, country)
        if name:
            out["company_name"] = name
    if cc and cc != "CN":
        for k in list(out):
            out[k] = _latin_part_if_bilingual(out[k], cc)
    return out


async def _facts(session: AsyncSession, row: FormInstance) -> dict[str, str]:
    """What the linked assessment already knows, under the keys the forms use; company facts given
    in other forms of the same country fill what the assessment does not hold."""
    form = service.get_form(row.form_code) or {}
    profile = await org_profile(session, row.org_id, form.get("country"))
    task = await session.get(AnalysisTask, row.task_id) if row.task_id else None
    if task is None:
        return profile
    facts = facts_for_task(task, row.form_code)
    return {**profile, **{k: v for k, v in facts.items() if str(v).strip()}}


async def _origins(session: AsyncSession, row: FormInstance) -> dict[str, dict[str, str]]:
    """For each fact the form fills without the user typing it here, where it comes from and where
    to change it: the linked project, another official form of the enterprise in the same country,
    or the company name given for an earlier project — the same order ``_facts`` takes them in."""
    form_meta = service.get_form(row.form_code) or {}
    cc = form_meta.get("country")
    rows_all = (await session.execute(
        select(FormInstance.id, FormInstance.form_code, FormInstance.answers)
        .where(FormInstance.org_id == row.org_id).order_by(FormInstance.updated_at.desc())
    )).all()
    rows = [
        (iid, c, a)
        for iid, c, a in rows_all
        if not cc or (service.get_form(c) or {}).get("country") == cc
    ]
    index: dict[str, int] = {}
    profile = service.profile_values([(c, a or {}) for _, c, a in rows], index)
    out: dict[str, dict[str, str]] = {}
    for key, n in index.items():
        iid, code, _ = rows[n]
        if iid != row.id:
            out[key] = {"text": f"已从《{(service.get_form(code) or {}).get('name_zh', code)}》带入", "to": f"/forms/{iid}"}
    if "company_name" in profile and "company_name" not in index:  # 取自以往项目里填的公司全称
        last = await session.scalar(
            select(AnalysisTask).where(AnalysisTask.org_id == row.org_id, AnalysisTask.kind == "documents").order_by(AnalysisTask.updated_at.desc()).limit(1)
        )
        if last is not None and str((last.facts_json or {}).get(DOC_FACT_PREFIX + "company_name") or "") == profile["company_name"]:
            out["company_name"] = {"text": f"已从项目 {last.title} 带入", "to": f"/documents?project={last.id}"}
        else:
            out["company_name"] = {"text": "已从以往的准入办理带入", "to": "/admission"}
    task = await session.get(AnalysisTask, row.task_id) if row.task_id else None
    if task is not None:
        for key, value in facts_for_task(task, row.form_code).items():
            if str(value).strip():
                out[key] = {"text": f"已从项目 {task.title} 带入", "to": f"/documents?project={task.id}"}
    return out


def facts_for_task(task: AnalysisTask, form_code: str) -> dict[str, str]:
    """The project's facts under the keys the form uses (no instance needed)."""
    from app.analysis.risk_items import shown

    # 按行填的题（主要风险）以文字带入表格
    known = {(k := key.removeprefix(DOC_FACT_PREFIX)): shown(k, str(v)) for key, v in (task.facts_json or {}).items()}
    # 评估自带的字段（国家、车型）打底；评估里回答过的事实（含公司名称）优先，不被空值盖掉
    facts = {**task_values(task), **{k: v for k, v in known.items() if v.strip()}}
    facts["function_key"] = getattr(task, "scenario_type", "") or ""  # 车辆数据用途：官方表格上的文件名称、用途用表格语言写
    # 在表格所在国处理数据的一方：表格所在国是数据发出国时是企业自己，是接收国时是接收方
    form = service.get_form(form_code) or {}
    from app.analysis.doc_rules import ADEQUACY_SEEN
    from app.analysis.document_basis import country_of

    seen = ADEQUACY_SEEN.get((country_of(facts.get("source_country")), country_of(facts.get("recipient_country"))))
    if seen is not None:  # 接收国是否在发出国的充分保护名单里（决定官方表格上的传输依据）
        facts["recipient_adequate"] = "是" if seen["listed"] else "否"
    from app.analysis.doc_rules import entity_for

    if form.get("country"):  # 表格所在国的一方必须是该国境内的主体：别国公司名不填进表格
        facts["company_name"] = _latin_part_if_bilingual(
            entity_for(facts.get("company_name"), form["country"]),
            form["country"],
        )
        if form["country"] != "CN":
            for fk_key, fk_val in list(facts.items()):
                if isinstance(fk_val, str) and fk_key not in ("source_country", "recipient_country", "recipient_adequate"):
                    facts[fk_key] = _latin_part_if_bilingual(fk_val, form["country"])
    if form.get("country") and country_of(facts.get("source_country")) == form["country"]:
        facts["ma_processor"] = facts.get("company_name", "")
    elif form.get("country") and country_of(facts.get("recipient_country")) == form["country"]:
        facts["ma_processor"] = facts.get("recipient", "")
    return facts


def _form(row: FormInstance) -> dict[str, Any]:
    form = service.get_form(row.form_code)
    if form is None:
        raise HTTPException(404, "这份官方表单已不在目录中")
    return form


async def _shown(session: AsyncSession, row: FormInstance, form: dict[str, Any]) -> dict[str, Any]:
    return _view(row, form, await _facts(session, row), await _origins(session, row))


def _view(row: FormInstance, form: dict[str, Any], facts: dict[str, str], origins: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    sections = []
    for section, states in service.resolve(form, row.answers or {}, facts):
        sections.append(
            {
                "id": section["id"],
                "title_zh": section["title_zh"],
                "title_fr": section.get("title_fr", ""),
                "items": [
                    {
                        "id": st.item["id"],
                        "label_zh": st.item["label_zh"],
                        "label_fr": st.item["label_fr"],
                        "kind": st.item["kind"],
                        "options": list((st.item["options"] or {}).keys()) or None,
                        "matrix": f118_matrix.public(st.item["matrix"]) if st.item["kind"] == "matrix" else None,
                        "option_labels": st.item.get("option_labels_zh") or {},
                        "required": st.item["required"],
                        "help_zh": st.item["help_zh"],
                        "question_zh": st.item["question_zh"],
                        "max_chars": st.item.get("max_chars"),
                        "store_chars": st.item.get("store_chars") or st.item.get("max_chars"),
                        "inline_after": st.item.get("inline_after") or None,  # 这一栏排在哪道题的右边（如“选其他时的说明”）
                        "value": st.value,
                        "source": st.source,
                        # 带入的值：从哪来、点了去哪改
                        "carried": (origins or {}).get(st.item.get("fact") or "") if st.source == "assessment" else None,
                    }
                    for st in states
                ],
            }
        )
    missing = service.missing_questions(form, row.answers or {}, facts)
    return {
        "id": str(row.id),
        "task_id": str(row.task_id) if row.task_id else None,
        "form": service.summary(form),
        "sections": sections,
        "missing": missing,
        "ready": not missing,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/catalog")
async def list_catalog(user: UserDep, country: str | None = None, direction: str | None = None) -> list[dict]:
    out = []
    for form in service.catalog().values():
        if country and form["country"] != country.upper():
            continue
        if direction and form["direction"] != direction:
            continue
        out.append(service.summary(form))
    return sorted(out, key=lambda f: (f["country"], f["code"]))


@router.get("/preview/{form_code}")
async def preview_form(
    form_code: str,
    user: UserDep,
    session: SessionDep,
    task_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> dict:
    """Read-only preview of an official form before creating a FormInstance (writes zero DB rows or audit logs)."""
    form = service.get_form(form_code)
    if form is None:
        raise HTTPException(404, "没有这份官方表单")
    task = await _task(session, task_id, user) if task_id else None
    q_existing = (
        select(FormInstance)
        .where(FormInstance.org_id == user.org_id, FormInstance.form_code == form["code"])
        .order_by(FormInstance.created_at.desc())
        .limit(1)
    )
    existing = await session.scalar(
        q_existing.where(FormInstance.task_id == task.id) if task is not None else q_existing.where(FormInstance.task_id.is_(None))
    )
    if existing is not None:
        return await _shown(session, existing, form)
    virtual = FormInstance(
        id=uuid.UUID(int=0),
        org_id=user.org_id,
        task_id=task.id if task else None,
        form_code=form["code"],
        form_version=form["version"],
        answers={},
        created_by=user.user_id,
    )
    facts = await _facts(session, virtual)
    if project_id is not None:
        from app.admission.models import AdmissionProject

        proj = await session.get(AdmissionProject, project_id)
        if proj is not None and proj.org_id == user.org_id:
            if proj.applicant:
                facts["company_name"] = _latin_part_if_bilingual(proj.applicant.strip(), form.get("country"))
            if proj.vehicle_model:
                facts["vehicle_model"] = _latin_part_if_bilingual(proj.vehicle_model.strip(), form.get("country"))
            for rk, rv in (proj.facts or {}).items():
                if str(rv or "").strip():
                    k = rk.removeprefix(DOC_FACT_PREFIX)
                    target_k = "company_name" if k == "applicant" else k
                    facts[target_k] = _latin_part_if_bilingual(str(rv).strip(), form.get("country"))
    view = _view(virtual, form, facts, await _origins(session, virtual))
    view["id"] = f"preview:{form['code']}"
    return view


@router.post("/instances", status_code=201)
async def create_instance(body: CreateIn, request: Request, user: UserDep, session: SessionDep) -> dict:
    from sqlalchemy import text as sa_text

    form = service.get_form(body.form_code)
    if form is None:
        raise HTTPException(404, "没有这份官方表单")
    task = await _task(session, body.task_id, user) if body.task_id else None
    await session.execute(
        sa_text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"form_inst:{user.org_id}:{task.id if task else ''}:{form['code']}"},
    )
    q_existing = (
        select(FormInstance)
        .where(FormInstance.org_id == user.org_id, FormInstance.form_code == form["code"])
        .order_by(FormInstance.created_at.desc())
        .limit(1)
    )
    if task is not None:
        # 同一个项目的同一份官方表只有一份：再点“填写”回到已填的那份，不另起一份空表
        existing = await session.scalar(q_existing.where(FormInstance.task_id == task.id))
        if existing is not None:
            request.state.audit_skip = True
            return await _shown(session, existing, form)
    else:
        # 未关联具体项目时，若同一 (org_id, None, form_code) 已存在 answers 为空的未填实例，直接复用防止插入空壳行
        existing = await session.scalar(q_existing.where(FormInstance.task_id.is_(None)))
        if existing is not None and not (existing.answers or {}):
            request.state.audit_skip = True
            return await _shown(session, existing, form)
    row = FormInstance(
        org_id=user.org_id,
        task_id=task.id if task else None,
        form_code=form["code"],
        form_version=form["version"],
        answers={},
        created_by=user.user_id,
    )
    session.add(row)
    await session.flush()
    out = await _shown(session, row, form)
    await session.commit()
    return out


@router.get("/instances")
async def list_instances(user: UserDep, session: SessionDep, task_id: uuid.UUID | None = None) -> list[dict]:
    stmt = (
        select(FormInstance)
        .where(FormInstance.org_id == user.org_id)
        .order_by(FormInstance.created_at.desc())
    )
    if task_id:
        stmt = stmt.where(FormInstance.task_id == task_id)
    out = []
    for row in (await session.scalars(stmt)).all():
        form = service.get_form(row.form_code)
        if form is None:
            continue
        missing = service.missing_questions(form, row.answers or {}, await _facts(session, row))
        out.append(
            {
                "id": str(row.id),
                "form": service.summary(form),
                "task_id": str(row.task_id) if row.task_id else None,
                "task_title": (task.title if (task := await session.get(AnalysisTask, row.task_id)) else "") if row.task_id else "",
                "missing": len(missing),
                "ready": not missing,
                "updated_at": row.updated_at.isoformat(),
            }
        )
    return out


@router.get("/instances/{instance_id}")
async def get_instance(instance_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    row = await _instance(session, instance_id, user)
    return await _shown(session, row, _form(row))


@router.put("/instances/{instance_id}/answers")
async def save_answers(
    instance_id: uuid.UUID, body: AnswersIn, request: Request, user: UserDep, session: SessionDep
) -> dict:
    row = await _instance(session, instance_id, user, for_update=True)
    form = _form(row)
    clean = service.clean_answers(form, body.answers)
    # 企业把已填的内容删空后保存：这一栏回到未填（原先空值被丢掉，删不掉已保存的内容）
    known = {i["id"] for s in form["sections"] for i in s["items"]}
    cleared = [k for k, v in body.answers.items() if k in known and k not in clean and (v is None or v == [] or not str(v).strip())]
    if not clean and not cleared:
        raise HTTPException(422, "没有可保存的内容")
    before = dict(row.answers or {})
    row.answers = {**{k: v for k, v in before.items() if k not in cleared}, **clean}
    clean = {**clean, **{k: None for k in cleared}}  # 审计里记下清空了哪几栏
    await session.commit()
    await session.refresh(row)
    set_detail(
        request,
        summary=f"填写官方表单“{form['name_zh']}”（{len(clean)} 项）",
        before={k: before.get(k) for k in clean},
        after=clean,
    )
    return await _shown(session, row, form)


class FormAssistIn(BaseModel):
    message: str = Field(min_length=1, max_length=3000)
    history: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


@router.post("/instances/{instance_id}/assist")
async def assist_instance(
    instance_id: str, body: FormAssistIn, user: UserDep, session: SessionDep
) -> dict[str, Any]:
    from app.analysis import copilot_fill
    from app.core.redaction import bind_egress, llm_scope
    from app.iam.models import Organization

    if instance_id.startswith("preview:"):
        shown = await preview_form(instance_id[8:], user, session)
        org = await session.get(Organization, user.org_id) if user.org_id else None
    else:
        try:
            iid = uuid.UUID(instance_id)
        except ValueError as exc:
            raise HTTPException(404, "表单不存在") from exc
        row = await _instance(session, iid, user)
        form = _form(row)
        shown = await _shown(session, row, form)
        org = await session.get(Organization, row.org_id) if row.org_id else None
    with llm_scope():
        bind_egress(session, org)
        return await copilot_fill.assist_official_form(shown, body.message, body.history)


@router.delete("/instances/{instance_id}")
async def delete_instance(instance_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    """删除一份填写中的官方表（只删本企业的）；所属项目和项目里的信息不受影响。"""
    row = await session.get(FormInstance, instance_id)
    if row is None or row.org_id != user.org_id:
        raise HTTPException(404, "没有这份表单")
    await session.delete(row)
    await session.commit()
    return {"deleted": True, "id": str(instance_id)}


async def _export_instance_file(
    instance_id: uuid.UUID,
    request: Request,
    user: Principal,
    session: AsyncSession,
    *,
    annex: bool = False,
    want_docx: bool = False,
    foreign_only: bool = False,
) -> Response:
    import asyncio
    from functools import partial

    from starlette.concurrency import run_in_threadpool

    from app.analysis.models import AnalysisTask
    from app.analysis.report_exporter import named

    row = await _instance(session, instance_id, user)
    form = _form(row)
    facts = await _facts(session, row)
    task = await session.get(AnalysisTask, row.task_id) if row.task_id else None
    task_title = task.title if task else ""
    answers = dict(row.answers or {})
    is_foreign_form = str(form.get("country") or "").upper() not in ("", "CN")
    await session.close()

    # Redline 0.2 & Redline 5.1: 默认原样写入企业填报字面值（契约见 tests/test_official_forms_api.py:108）；
    # 显式传 foreign_only=true 时对中文填报值执行东道国语种转译；外文原表附页/表头/页眉页脚在 service.py 中恒定零中文。
    data, _report, too_long = await run_in_threadpool(
        partial(
            service.render,
            form,
            answers,
            facts,
            allow_annex=annex,
            foreign_only=bool(foreign_only and is_foreign_form),
            foreign_annex=is_foreign_form,
        )
    )
    if too_long:
        names = "；".join(
            t["label"] + (f"（建议不超过 {t['max_chars']} 字）" if t["max_chars"] else "") for t in too_long
        )
        raise HTTPException(409, "以下内容放不进官方表格栏位，请修改后再导出：" + names)
    set_detail(request, summary=f"导出官方表单“{form['name_zh']}”" + ("（含附页续表）" if annex else ""))
    country, _, title = form["name_zh"].partition("：")
    label_part = title or form["name_zh"]
    is_docx = form.get("kind") == "docx"
    if want_docx:
        if not is_docx:
            raise HTTPException(404, "该官方表格仅提供 PDF 格式")
        name = named(task_title or country, label_part, ext="docx")
        return Response(
            data,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": content_disposition(name)},
        )
    if is_docx:
        from app.analysis import official_docx as od
        from app.analysis.document_templates import Rendered, _render_pdf
        from app.analysis.docx_pdf import PdfEngineUnavailable, docx_to_pdf, engine

        is_foreign_form = str(form.get("country") or "").upper() not in ("", "CN")
        pdf_header = (
            str(form.get("name_orig") or form["code"].replace("_", " "))
            if is_foreign_form
            else label_part
        )
        if engine() != "builtin":
            try:
                data = await asyncio.to_thread(docx_to_pdf, data)
            except PdfEngineUnavailable:
                blocks = await asyncio.to_thread(od.docx_preview_blocks, data, include_pagebreaks=True)
                data = await asyncio.to_thread(
                    _render_pdf, Rendered(blocks=blocks), pdf_header, foreign=is_foreign_form
                )
        else:
            blocks = await asyncio.to_thread(od.docx_preview_blocks, data, include_pagebreaks=True)
            data = await asyncio.to_thread(
                _render_pdf, Rendered(blocks=blocks), pdf_header, foreign=is_foreign_form
            )
    name = named(task_title or country, label_part, ext="pdf")
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": content_disposition(name)})


@router.get("/instances/{instance_id}/export.pdf")
async def export_pdf(
    instance_id: uuid.UUID,
    request: Request,
    user: UserDep,
    session: SessionDep,
    annex: bool = False,
    fmt: str | None = None,
    foreign_only: bool = False,
) -> Response:
    return await _export_instance_file(
        instance_id,
        request,
        user,
        session,
        annex=annex,
        want_docx=(fmt == "docx"),
        foreign_only=foreign_only,
    )


@router.get("/instances/{instance_id}/export.docx")
async def export_docx(
    instance_id: uuid.UUID,
    request: Request,
    user: UserDep,
    session: SessionDep,
    annex: bool = False,
    foreign_only: bool = False,
) -> Response:
    return await _export_instance_file(
        instance_id,
        request,
        user,
        session,
        annex=annex,
        want_docx=True,
        foreign_only=foreign_only,
    )
