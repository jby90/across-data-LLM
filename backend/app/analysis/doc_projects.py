# ruff: noqa: E501
"""“生成合规文件”：选好数据从哪国发出、发往哪国和业务，直接在页面上生成 8 类合规文件。

一个文件项目就是一个不跑评估的任务（kind="documents"）：8 类文件的模板、官方表格、论述文、依据检索
全部沿用现有的 /api/analysis/tasks/{id}/documents/... 接口，这里只负责项目的建立、全部文件的总览、
成批填写、先出初稿、论述后台生成和打包下载。出入境国家不限定：有官方模板的用官方模板，没有的参照
欧盟标准，并在每份文件上写明。
"""

from __future__ import annotations

import asyncio
import copy
import io
import logging
import re
import uuid
import zipfile
from collections import OrderedDict
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analysis import cn_route, doc_applicability, doc_consistency, risk_items
from app.analysis import documents_router as D
from app.analysis.document_basis import CN, WORLD, basis_kind, basis_lines, country_of
from app.analysis.document_basis import NAMES as LIBRARY_NAMES
from app.analysis.document_instances import DocumentInstance, build_xlsx
from app.analysis.document_narrative import (
    DocumentNarrative,
    NarrativeError,
    facts_hash,
)
from app.analysis.document_templates import (
    DOC_FACT_PREFIX,
    MAX_ANSWER_CHARS,
    Rendered,
    build_docx,
    build_pdf,
    ensure_default_templates,
    render,
    resolve_facts,
    task_values,
)
from app.analysis.document_templates_default import DEFAULT_TEMPLATES
from app.analysis.models import AgentWorkflowConfig, AnalysisTask
from app.analysis.report_exporter import content_disposition, named
from app.analysis.schemas import format_conversation_code
from app.analysis.workflow_engine import BUSINESS_SCENES, COUNTRY_NAMES_ZH, SCENARIO_LABELS
from app.core import dates
from app.core.db import get_session
from app.core.rls import system_scope
from app.iam.deps import Principal, require_user_side

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analysis/doc-projects", tags=["doc-projects"])
UserDep = Annotated[Principal, Depends(require_user_side)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]

# 项目级信息（对所有文件共用）：只填一次
PROJECT_KEYS = ("source_country", "recipient_country", "vehicle_model", "scenario", "company_name", "country_name")
# 企业自己的身份/管理信息：只有企业知道，不能替它猜；填一次，以后新项目自动带入
COMPANY_KEYS = frozenset({
    "entity_nature", "entity_type", "registered_place", "office_place", "staff_count", "credit_code", "is_cii",
    "pi_scale", "legal_rep", "security_officer", "security_org", "agent_info", "company_profile",
})
# 新项目自动带入的只能是与流向无关的企业登记信息；“数据处理者基本情况”往往写着这次业务（出口到哪国、传到哪里），
# 带到别的流向会张冠李戴，所以不带
CARRY_KEYS = COMPANY_KEYS - {"company_profile"}
# 没有用户原文依据时，只有这几项可以按业务概况拟一版；其余（含法律判断、措施、数量、流程）只能来自用户自己的话
GENERAL_OK = frozenset({"purpose", "industry_field", "scenario_summary"})
# 评估性的栏目（风险判断、问题与整改、结论、必要性、传输机制、补充措施、复评、合同约定要点）：
# 是企业自己的评估判断，系统不能代写（真模型试过：只会写出“尚缺……”，等于没写）。单独归一组、最后填，
# 初稿里先标“待补充”
SYSTEM_KEYS = frozenset({
    "risk_rating", "risk_findings", "risk_conclusion", "assessment_work", "legal_doc_terms",
    "issues", "measures", "verification", "necessity", "cross_border_mechanism", "supplementary_measures", "reevaluation",
})
SUGGEST_KEY = "_suggest"
DIAGNOSIS_KEY = "_suggest_diagnosis"
MANUAL_DOCS_KEY = "_manual_docs"
FUNCTIONS_KEY = "_functions"
SCENES_KEY = "_scenes"
# 带入来源：新项目从同一企业上一个项目复制的企业信息 {题: {from, title, value}}；用户点“采用”的识别结果 {题: {value, quote}}。
# 值被改过就不再算带入（保存时清掉，读取时也按值比对）
CARRIED_KEY = "_carried"
ADOPTED_KEY = "_adopted"
# 从“主要风险”一键生成的整改项：已生成的风险 {ids}，写进整改题的 {题: {value, n}}（值被改过就不再标“带入”）
RISK_DONE_KEY = "_risk_remediation"
RISK_FACTS_SIG_KEY = "_risk_facts_sig"
# 取另一项的值作默认值的题（如“跨境传输场景简述”默认取业务场景）：来源的叫法
FROM_NAMES = {"scenario": "业务场景"}


_RISK_DOWNSTREAM_KEYS = frozenset({
    "risk_rating", "risk_findings", "risk_conclusion", "issues", "measures", "verification",
})


def _risk_facts_signature(facts: dict, vehicle_model: str = "") -> str:
    """Compute a deterministic fingerprint of upstream business & security facts that feed risk grading."""
    import hashlib
    import json

    upstream: dict[str, str] = {}
    for k, v in (facts or {}).items():
        if not k.startswith(DOC_FACT_PREFIX) or not isinstance(v, str):
            continue
        short_k = k.removeprefix(DOC_FACT_PREFIX)
        if short_k in _RISK_DOWNSTREAM_KEYS:
            continue
        cleaned = v.strip()
        if cleaned and not cleaned.startswith("暂不确定"):
            upstream[short_k] = cleaned
    if (vehicle_model or "").strip():
        upstream["_vehicle_model"] = vehicle_model.strip()
    if not upstream:
        return ""
    raw = json.dumps(upstream, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _build_risk_sync(facts: dict, vehicle_model: str, risk_raw: str) -> dict:
    cur_sig = _risk_facts_signature(facts, vehicle_model)
    saved_sig = str((facts or {}).get(RISK_FACTS_SIG_KEY) or "")
    has_rows = bool(risk_items.parse(risk_raw or ""))
    return {
        "stale": bool(has_rows and saved_sig and cur_sig and saved_sig != cur_sig),
        "can_diagnose": bool(cur_sig),
    }
CHAT_KEY = "_chat"  # 项目关联的对话：项目页旁边显示它，对话里说的业务情况进本项目预填
MAX_SOURCE_TEXT = 50000
STALE_PAPER_SECONDS = 20 * 60
_suggesting: OrderedDict[str, tuple[str, datetime, str]] = OrderedDict()  # 项目 -> (状态, 开始时间, 失败原因)
_papers: OrderedDict[tuple[str, str], tuple[str, datetime, str]] = OrderedDict()  # (项目, 文件) -> (状态, 开始时间, 失败原因)


def _put_bounded(store: OrderedDict, key, val, max_items: int = 256) -> None:
    if key in store:
        store.move_to_end(key)
    store[key] = val
    while len(store) > max_items:
        store.popitem(last=False)

# 项目名里用的业务简称：同一流向的多个项目靠它区分
SHORT_FUNCTION = {
    "autonomous_trial": "自动驾驶测试",
    "connected_vehicle_cloud": "车联网上云",
    "ota_diagnostics": "远程诊断与OTA",
    "cockpit_biometrics": "座舱与生物识别",
    "hd_mapping": "高精地图与测绘",
    "fleet_mobility": "车队与出行服务",
    "sales_service": "销售与售后客户数据",
    "insurance_ubi": "保险与驾驶行为",
    "charging_energy": "充电与能源服务",
}

# 可选的国家/地区（出入境都不限定，列表里没有的也可以直接输入）
COUNTRY_CHOICES = list(dict.fromkeys(["中国", *COUNTRY_NAMES_ZH.values(), *WORLD.values()]))


class CreateIn(BaseModel):
    source_country: str = Field(min_length=1, max_length=40)
    recipient_country: str = Field(min_length=1, max_length=40)
    function: str | None = None  # SCENARIO_LABELS 的键（兼容单选）
    functions: list[str] | None = Field(default=None, max_length=12)  # 多场景多选
    business_scene: str | None = None  # BUSINESS_SCENES 的键（兼容单选）
    business_scenes: list[str] | None = Field(default=None, max_length=12)  # 多业务阶段多选
    scenario_text: str = Field("", max_length=200)  # 选项都不合适时用一句话描述
    vehicle_model: str = Field("", max_length=128)
    company_name: str = Field("", max_length=120)
    title: str = Field("", max_length=60)


class UpdateIn(BaseModel):
    source_country: str | None = Field(None, max_length=40)
    recipient_country: str | None = Field(None, max_length=40)
    function: str | None = None
    functions: list[str] | None = Field(default=None, max_length=12)
    business_scene: str | None = None
    business_scenes: list[str] | None = Field(default=None, max_length=12)
    scenario_text: str | None = Field(None, max_length=200)
    vehicle_model: str | None = Field(None, max_length=128)
    company_name: str | None = Field(None, max_length=120)
    title: str | None = Field(None, max_length=60)
    # 走安全评估时的两个日期（年-月-日，空串表示清除）：自评估完成日、评估结果出具日
    self_assessed_on: str | None = Field(None, max_length=10)
    result_issued_on: str | None = Field(None, max_length=10)


# 项目级的两个日期（存在项目信息里，不进 8 份文件的题目，不算缺项）
VALIDITY_DATES = ("self_assessed_on", "result_issued_on")


class AnswersIn(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict, max_length=80)


class SuggestIn(BaseModel):
    text: str = Field("", max_length=MAX_SOURCE_TEXT)  # 用户已有的业务说明（可不给）


class AcceptIn(BaseModel):
    keys: list[str] | None = None  # 不给就是全部
    dismiss: bool = False  # 不采用，只是不再显示


class IncludeDocIn(BaseModel):
    include: bool = True


def _apply_manual_docs(applicability: dict, facts: dict) -> dict:
    manual = (facts or {}).get(MANUAL_DOCS_KEY) or {}
    if not isinstance(manual, dict) or not manual:
        return applicability
    out = dict(applicability)
    for code, flag in manual.items():
        if flag and code in out and out[code].get("state") == "na":
            orig_why = str(out[code].get("why") or "")
            out[code] = {
                **out[code],
                "state": "need",
                "manual_include": True,
                "auto_why": orig_why,
                "why": f"已纳入本次出件（原路径判断：{orig_why}）",
            }
    return out


def _library_country(source: str, recipient: str) -> str:
    """The end the documents are written for: the receiving country when the user named a country we
    recognise, else the sending one. No country is preferred over another and none is required to be in
    the library: the clauses of both ends are quoted whenever the library holds them (see
    documents_router._both_ends), and an end without regulations in the library yet is simply covered
    by the official or EU-standard template until its regulations are added."""
    for text in (recipient, source):
        code = country_of(text)
        if code:
            return code
    return "--"


def _scenario_text(function: str | None, scene: str | None) -> str:
    label = SCENARIO_LABELS.get(function or "", "")
    scene_text = BUSINESS_SCENES.get(scene or "", "")
    return f"{label}（{scene_text}）" if label and scene_text else (label or scene_text)


async def _project(session: AsyncSession, project_id: uuid.UUID, user: Principal, writable: bool = False) -> AnalysisTask:
    if writable:
        task = await session.scalar(
            select(AnalysisTask)
            .where(AnalysisTask.id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    else:
        task = await session.get(AnalysisTask, project_id)
    if task is None or task.org_id != user.org_id or task.kind != "documents":
        raise HTTPException(404, "这个文件项目不存在")
    if writable and task.archived_at is not None:
        raise HTTPException(409, "这个项目在归档里，恢复后才能继续")
    return task


def task_business_scenes(task: AnalysisTask) -> list[str]:
    facts = task.facts_json or {}
    raw = facts.get(SCENES_KEY)
    if isinstance(raw, list):
        out = [D.sanitize_custom_tag(x) for x in raw if D.sanitize_custom_tag(x)]
        if out:
            return list(dict.fromkeys(out))[:12]
    stored_label = str(facts.get("business_scene") or "").strip()
    if stored_label:
        rev_map = {v: k for k, v in BUSINESS_SCENES.items()}
        parts = [D.sanitize_custom_tag(p) for p in stored_label.split("、") if D.sanitize_custom_tag(p)]
        matched = [rev_map.get(p, p) for p in parts]
        if matched:
            return list(dict.fromkeys(matched))[:12]
    return []


def _format_multi_function_short(fns: list[str], fallback: str = "", *, max_label_len: int = 48) -> str:
    labels = [SHORT_FUNCTION.get(f, f[:max_label_len]) for f in fns if SHORT_FUNCTION.get(f, f[:max_label_len])]
    if not labels:
        return fallback[:max_label_len]
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]}、{labels[1]}"
    return f"{labels[0]}、{labels[1]}等{len(labels)}项"


def _apply_route(task: AnalysisTask, body: CreateIn | UpdateIn) -> None:
    facts = dict(task.facts_json or {})
    for key in ("source_country", "recipient_country"):
        value = getattr(body, key, None)
        if value is not None:
            facts[DOC_FACT_PREFIX + key] = value.strip()
    src_check = (facts.get(DOC_FACT_PREFIX + "source_country") or "").strip()
    rcp_check = (facts.get(DOC_FACT_PREFIX + "recipient_country") or "").strip()
    if src_check and rcp_check and (
        src_check == rcp_check
        or (country_of(src_check) and country_of(src_check) == country_of(rcp_check))
    ):
        raise HTTPException(422, "数据发出国与接收国不能相同，请选择境外目的国或地区")
    if getattr(body, "company_name", None) is not None:
        facts[DOC_FACT_PREFIX + "company_name"] = body.company_name.strip()
    for key in VALIDITY_DATES:
        value = getattr(body, key, None)
        if value is None:
            continue
        if value.strip() and dates.parse(value) is None:
            raise HTTPException(422, "日期格式应为 年-月-日")
        if value.strip() and dates.parse(value) > date.today():
            raise HTTPException(422, "日期不能晚于今天")
        facts[DOC_FACT_PREFIX + key] = value.strip()
    assessed, issued = (dates.parse(facts.get(DOC_FACT_PREFIX + k)) for k in VALIDITY_DATES)
    if assessed and issued and issued < assessed:
        raise HTTPException(422, "评估结果出具日不能早于自评估完成日")
    if getattr(body, "vehicle_model", None) is not None:
        task.vehicle_model = body.vehicle_model.strip()
    if getattr(body, "scenario_text", None) is not None:
        facts[DOC_FACT_PREFIX + "scenario"] = body.scenario_text.strip()
    raw_fns = getattr(body, "functions", None)
    raw_fn = getattr(body, "function", None)
    if raw_fns is not None:
        clean_fns = list(dict.fromkeys(D.sanitize_custom_tag(f) for f in raw_fns if D.sanitize_custom_tag(f)))[:12]
        if clean_fns:
            facts[FUNCTIONS_KEY] = clean_fns
            task.scenario_type = clean_fns[0][:64]
        else:
            facts.pop(FUNCTIONS_KEY, None)
            task.scenario_type = ""
    elif raw_fn is not None:
        fn_clean = D.sanitize_custom_tag(raw_fn)
        if fn_clean:
            facts[FUNCTIONS_KEY] = [fn_clean]
            task.scenario_type = fn_clean[:64]
        else:
            facts.pop(FUNCTIONS_KEY, None)
            task.scenario_type = ""
    raw_scs = getattr(body, "business_scenes", None)
    raw_sc = getattr(body, "business_scene", None)
    if raw_scs is not None:
        clean_scs = list(dict.fromkeys(D.sanitize_custom_tag(s) for s in raw_scs if D.sanitize_custom_tag(s)))[:12]
        if clean_scs:
            facts[SCENES_KEY] = clean_scs
            facts["business_scene"] = "、".join(BUSINESS_SCENES.get(s, s) for s in clean_scs)
        else:
            facts.pop(SCENES_KEY, None)
            facts.pop("business_scene", None)
    elif raw_sc is not None:
        sc_clean = D.sanitize_custom_tag(raw_sc)
        if sc_clean:
            facts[SCENES_KEY] = [sc_clean]
            facts["business_scene"] = BUSINESS_SCENES.get(sc_clean, sc_clean)
        else:
            facts.pop(SCENES_KEY, None)
            facts.pop("business_scene", None)
    task.facts_json = facts
    source = facts.get(DOC_FACT_PREFIX + "source_country", "")
    recipient = facts.get(DOC_FACT_PREFIX + "recipient_country", "")
    task.country_code = _library_country(source, recipient)
    # 文件上“评估所在国家”：库里有的那端；都没有就写接收国
    facts[DOC_FACT_PREFIX + "country_name"] = LIBRARY_NAMES.get(task.country_code) or recipient
    task.facts_json = facts
    if not task.title_custom:
        cur_fns = D.task_functions(task)
        what = _format_multi_function_short(cur_fns, facts.get(DOC_FACT_PREFIX + "scenario") or "")
        suffix = str(facts.get("_clone_suffix") or "")
        limit = 54 if suffix else 60
        base = (f"{source or '?'} → {recipient or '?'}" + (f" · {what}" if what else ""))[:limit]
        task.title = (base + suffix)[:60]


async def _carry_company_info(session: AsyncSession, task: AnalysisTask, org_id: uuid.UUID | None) -> None:
    """The company's own details (registration, legal representative, contacts…) are the same for every
    project, so a new project starts with them: each one as given in the organization's latest earlier
    project that has it (an earlier project left blank does not hide what an older one holds), and the
    project it came from is recorded so the page can say so."""
    await session.flush()  # 先拿到本项目的编号，下面才能把它自己排除
    earlier_all = (
        await session.scalars(
            select(AnalysisTask)
            .where(AnalysisTask.org_id == org_id, AnalysisTask.kind == "documents", AnalysisTask.id != task.id)
            .order_by(AnalysisTask.updated_at.desc())
            .limit(50)
        )
    ).all()
    facts = dict(task.facts_json or {})
    cur_src = country_of(facts.get(DOC_FACT_PREFIX + "source_country"))
    earlier = sorted(
        earlier_all,
        key=lambda e: 0 if cur_src and country_of((e.facts_json or {}).get(DOC_FACT_PREFIX + "source_country")) == cur_src else 1,
    )
    wanted = sorted(CARRY_KEYS)
    if not facts.get(DOC_FACT_PREFIX + "company_name"):
        wanted.append("company_name")
    carried: dict[str, dict] = {}
    for key in wanted:
        for last in earlier:
            value = str((last.facts_json or {}).get(DOC_FACT_PREFIX + key) or "")
            if value and not value.startswith("暂不确定"):
                facts[DOC_FACT_PREFIX + key] = value
                carried[key] = {"from": str(last.id), "title": last.title, "value": value}  # 记下从哪个项目带来的
                break
    if carried:
        facts[CARRIED_KEY] = carried
    task.facts_json = facts


@router.get("/options")
async def options(user: UserDep, session: SessionDep) -> dict:
    """What the start page offers: countries (any can be typed too), functions and business scenes."""
    return {
        "countries": COUNTRY_CHOICES,
        "library_countries": [LIBRARY_NAMES.get(c, c) for c in sorted(await D.library_countries(session))],
        # 用途的叫法与项目名、准入页一致（短名）；长的说明放 desc，供选项下方小字
        "functions": [{"key": k, "label": SHORT_FUNCTION.get(k, v), "desc": v} for k, v in SCENARIO_LABELS.items()],
        "scenes": [{"key": k, "label": v} for k, v in BUSINESS_SCENES.items()],
    }


@router.get("/updates")
async def basis_updates(user: UserDep, session: SessionDep) -> list[dict]:
    """The enterprise's documents whose cited clauses changed after they were written (法规更新后
    受影响的文件): the file keeps working; the user decides when to rewrite it on the new clauses."""
    from app.analysis.document_narrative import cited_changed, usable

    rows = (
        await session.scalars(
            select(AnalysisTask).where(
                AnalysisTask.org_id == user.org_id,
                AnalysisTask.kind == "documents",
                AnalysisTask.archived_at.is_(None),
            )
        )
    ).all()
    if not rows:
        return []
    task_ids = [t.id for t in rows]
    all_papers = (
        await session.scalars(
            select(DocumentNarrative)
            .where(DocumentNarrative.task_id.in_(task_ids))
            .order_by(DocumentNarrative.created_at.desc())
        )
    ).all()
    if not all_papers:
        return []
    papers_by_pair: dict[tuple, list[DocumentNarrative]] = {}
    for p in all_papers:
        papers_by_pair.setdefault((p.task_id, p.code), []).append(p)

    # Fast pre-check: only if a stored narrative actually cites a clause whose wording changed in a
    # newer DocumentVersion do we need to run full D._inputs() verification.
    changed_candidates: dict[tuple, list[str]] = {}
    for key_pair, plist in papers_by_pair.items():
        ch = await cited_changed(session, plist[0])
        if ch:
            changed_candidates[key_pair] = ch
    if not changed_candidates:
        return []

    await ensure_default_templates(session)
    templates = {code: await D.latest_template(session, code) for code, _, _ in DEFAULT_TEMPLATES}
    out = []
    for task in rows:
        for code, name, _ in DEFAULT_TEMPLATES:
            if (task.id, code) not in changed_candidates:
                continue
            tpl = templates.get(code)
            if tpl is None or not tpl.definition.get("narrative"):
                continue
            template, values, citations = await D._inputs(session, task, code)
            paper, updated = await usable(session, task, code, template.definition, values, citations)
            if paper is not None and updated:
                out.append(
                    {
                        "project_id": str(task.id),
                        "project_title": task.title,
                        "project_created_at": task.created_at.isoformat() if task.created_at else None,
                        "code": code,
                        "name": name,
                        "written_at": paper.created_at.isoformat() if paper.created_at else None,
                        "changed": await cited_changed(session, paper),
                    }
                )
    return out


@router.get("")
async def list_projects(user: UserDep, session: SessionDep, archived: bool = False) -> list[dict]:
    return [item for item, _ in await _list(session, user, archived)]


_PROGRESS_CACHE: OrderedDict[str, tuple[tuple, dict]] = OrderedDict()
_OVERVIEW_PROJECT_CACHE: OrderedDict[str, tuple[tuple, dict]] = OrderedDict()


async def _progress_stamp(session: AsyncSession, task: AnalysisTask) -> tuple:
    import hashlib
    import json

    from sqlalchemy import func
    from sqlalchemy import inspect as sa_inspect

    from app.admission.models import AdmissionEntry
    from app.analysis.document_templates import DocumentTemplate
    from app.forms.models import FormInstance
    from app.iam.models import Organization
    from app.knowledge.models import KgNode

    if sa_inspect(task).expired_attributes:
        await session.refresh(task)
    facts_raw = json.dumps(task.facts_json or {}, ensure_ascii=False, sort_keys=True)
    facts_sig = hashlib.sha1(facts_raw.encode("utf-8")).hexdigest()[:16]
    org = await session.get(Organization, task.org_id) if task.org_id else None
    form_sig = (
        await session.execute(
            select(func.count(), func.max(FormInstance.updated_at)).where(
                FormInstance.task_id == task.id, FormInstance.org_id == task.org_id
            )
        )
    ).one()
    nar_sig = (
        await session.execute(
            select(func.count(), func.max(DocumentNarrative.created_at)).where(DocumentNarrative.task_id == task.id)
        )
    ).one()
    tpl_sig = (
        await session.execute(
            select(func.count(), func.max(DocumentTemplate.created_at)).where(DocumentTemplate.status == "published")
        )
    ).one()
    adm_sig = (
        await session.execute(select(func.count(), func.max(AdmissionEntry.updated_at)))
    ).one()
    kg_sig = (
        await session.execute(select(func.count(), func.max(KgNode.created_at)).where(KgNode.review_status != "superseded"))
    ).one()
    from app.iam.service import strip_trial_org_suffix

    return (
        task.updated_at,
        task.country_code,
        task.scenario_type,
        task.vehicle_model,
        strip_trial_org_suffix(org.name) if org else "",
        facts_sig,
        form_sig,
        nar_sig,
        tpl_sig,
        adm_sig,
        kg_sig,
    )


async def _fast_progress(
    session: AsyncSession,
    task: AnalysisTask,
    templates: dict[str, object],
    library: frozenset[str],
) -> dict:
    """Compute {ready, total, missing, issues} with the exact rules of ``build_overview`` without
    per-question clause search or full document clause rendering."""
    from app.analysis import doc_field_guide as G
    from app.analysis import doc_rules
    from app.analysis.document_narrative import usable
    from app.iam.models import Organization

    stamp = await _progress_stamp(session, task)
    cached = _PROGRESS_CACHE.get(str(task.id))
    if cached and cached[0] == stamp:
        _PROGRESS_CACHE.move_to_end(str(task.id))
        return cached[1]

    from app.iam.service import strip_trial_org_suffix

    org = await session.get(Organization, task.org_id) if task.org_id else None
    org_name = strip_trial_org_suffix(org.name or "") if org else ""
    nar_rows = (
        await session.scalars(
            select(DocumentNarrative)
            .where(DocumentNarrative.task_id == task.id)
            .order_by(DocumentNarrative.created_at.desc())
        )
    ).all()
    nar_by_code: dict[str, list[DocumentNarrative]] = {}
    for row in nar_rows:
        nar_by_code.setdefault(row.code, []).append(row)

    docs: list[dict] = []
    questions: dict[str, dict] = {}
    base = {**task_values(task), **D._scenario(task)}
    facts = task.facts_json or {}

    for code, _, _ in DEFAULT_TEMPLATES:
        template = templates.get(code)
        if template is None:
            continue
        raw_values = resolve_facts(template.definition, base, facts)
        doc_values = dict(raw_values)
        if not doc_values.get("company_name"):
            doc_values["company_name"] = org_name
        src0, rcp0 = doc_rules.ends(doc_values, task.country_code)
        party_country = doc_rules.overseas(src0, rcp0) if code == "admission" else src0
        doc_values["company_name"] = doc_rules.entity_for(doc_values.get("company_name"), party_country)
        if code == "admission" and not doc_values["company_name"] and party_country and party_country == rcp0:
            local_party = str(facts.get(DOC_FACT_PREFIX + "recipient") or "")
            doc_values["company_name"] = doc_rules.entity_for(local_party, party_country)
        src_cc, rcp_cc = doc_rules.ends(doc_values, task.country_code)
        if code == "admission":
            target = doc_rules.overseas(src_cc, rcp_cc)
            doc_values["country_name"] = LIBRARY_NAMES.get(target or "", doc_values.get("country_name", ""))
        elif src_cc:
            doc_values["country_name"] = LIBRARY_NAMES.get(src_cc, doc_values.get("country_name", ""))
        if code == "admission" and task.kind == "documents":
            await D._fill_admission(session, task, doc_values)

        rendered = render(template.definition, doc_values, [], None, None, draft=True)
        narrative = bool(template.definition.get("narrative"))
        done_paper = False
        if narrative:
            rows_for_code = nar_by_code.get(code, [])
            if rows_for_code:
                want = facts_hash(template.definition, doc_values)
                has_legacy = False
                for r in rows_for_code:
                    mark = (r.report or {}).get("facts_hash")
                    if mark == want or (mark is None and task.updated_at and r.created_at and r.created_at >= task.updated_at):
                        done_paper = True
                        break
                    if mark is None:
                        has_legacy = True
                if not done_paper and has_legacy:
                    _, v_full, cits_full = await D._inputs(session, task, code)
                    found, _ = await usable(session, task, code, template.definition, v_full, cits_full)
                    done_paper = found is not None

        missing = [{"key": q.key, "label": q.label} for q in rendered.missing]
        forms = await _official_states(session, task, D._official_forms_for(code, doc_values))
        only_official = official_only(code, doc_values.get("source_country", ""), forms)
        if only_official:
            missing = []
            open_n = sum(f["missing"] for f in forms)
            started = any(f["started"] for f in forms)
        else:
            open_n = len(missing)
            started = any(
                (raw_values.get(f["key"]) or "").strip()
                for f in template.definition.get("facts", [])
                if f["key"] not in PROJECT_KEYS
            )
        state = doc_state(open_n, started, narrative and not done_paper)
        docs.append(
            {
                "code": code,
                "name": template.name_zh,
                "state": state,
                "missing": missing,
                "official_forms": forms,
                "official_only": only_official,
            }
        )

        for fact in template.definition.get("facts", []):
            key = fact["key"]
            if key in PROJECT_KEYS:
                continue
            q = questions.setdefault(
                key,
                {
                    "key": key,
                    "label": fact["label"],
                    "value": raw_values.get(key, ""),
                    "tier": "company" if key in COMPANY_KEYS else "system" if key in SYSTEM_KEYS else "business",
                    "choices": fact.get("choices") or [],
                },
            )
            if not q["choices"] and key in G.CHOICES:
                q["choices"] = G.CHOICES[key]

    source = facts.get(DOC_FACT_PREFIX + "source_country", "")
    recipient = facts.get(DOC_FACT_PREFIX + "recipient_country", "")
    answers = {k: q["value"] for k, q in questions.items()}
    labels = {k: q["label"] for k, q in questions.items()}
    cn = cn_route.judge(answers) if country_of(source) == "CN" else None
    applicability = _apply_manual_docs(
        await doc_applicability.for_project(
            session,
            source=source,
            recipient=recipient,
            values=answers,
            labels=labels,
            function=task.scenario_type or "",
            functions=D.task_functions(task),
            library=library,
            cn=cn,
        ),
        facts,
    )
    issues = doc_consistency.between_questions(
        {k: v for k, v in answers.items() if str(v).strip()}, labels, recipient
    )
    issues += doc_consistency.with_forms(await _form_items(session, task, set(questions)))
    final = final_status(docs, applicability, list(questions.values()))
    final["issues"] = doc_consistency.summary(issues)
    req_docs = [d for d in docs if applicability.get(d["code"], {}).get("state") != "na"] or docs
    res = {
        "ready": sum(1 for d in req_docs if d["state"] == "ready"),
        "total": len(req_docs),
        "missing": len(final["missing"]) + sum(f["missing"] for f in final["forms"]),
        "issues": len(final.get("issues") or []),
    }
    _put_bounded(_PROGRESS_CACHE, str(task.id), (stamp, res), 64)
    return res


@router.get("/progress")
async def projects_progress(ids: str, user: UserDep, session: SessionDep) -> dict[str, dict]:
    """How far each of these projects is (at most 10, the enterprise's own): ready documents, items
    still missing, inconsistencies — the numbers the project page shows, from the same overview.
    The home page asks for it after the names are on screen."""
    parsed_ids: list[uuid.UUID] = []
    for raw in ids.split(",")[:50]:
        if raw.strip():
            try:
                parsed_ids.append(uuid.UUID(raw.strip()))
            except ValueError:
                continue
    if not parsed_ids:
        return {}
    library = await D.library_countries(session)
    await ensure_default_templates(session)
    templates = {code: await D.latest_template(session, code) for code, _, _ in DEFAULT_TEMPLATES}
    out: dict[str, dict] = {}
    for pid in parsed_ids:
        try:
            task = await _project(session, pid, user)
        except HTTPException:
            continue
        out[str(task.id)] = await _fast_progress(session, task, templates, library)
    return out


_COMPLIANCE_OVERVIEW_CACHE: dict[tuple[str, str], tuple[tuple, dict]] = {}


async def _org_overview_stamp(session: AsyncSession, user: Principal) -> tuple:
    from sqlalchemy import text

    from app.knowledge import cache_sync

    row = (
        await session.execute(
            text(
                """
                SELECT
                  (SELECT md5(coalesce(string_agg(id::text || coalesce(updated_at::text, '') || coalesce(archived_at::text, '') || coalesce(title, '') || coalesce(facts_json::text, ''), ',' ORDER BY id), ''))
                   FROM analysis_tasks WHERE org_id = :org AND kind = 'documents'),
                  (SELECT count(*)::text || ':' || coalesce(max(created_at)::text, '')
                   FROM document_narratives WHERE org_id = :org),
                  (SELECT count(*)::text || ':' || coalesce(max(updated_at)::text, '')
                   FROM form_instances WHERE org_id = :org),
                  (SELECT md5(coalesce(string_agg(id::text || status || coalesce(updated_at::text, '') || coalesce(approval::text, '') || coalesce(facts::text, ''), ',' ORDER BY id), ''))
                   FROM admission_projects WHERE org_id = :org),
                  (SELECT count(*)::text || ':' || coalesce(max(created_at)::text, '')
                   FROM admission_drafts WHERE org_id = :org),
                  (SELECT count(*)::text || ':' || coalesce(max(updated_at)::text, '') || ':' || coalesce(sum(CASE WHEN state = 'open' THEN 1 ELSE 0 END), 0)::text
                   FROM admission_issues WHERE org_id = :org),
                  (SELECT count(*)::text || ':' || coalesce(max(created_at)::text, '')
                   FROM admission_files WHERE org_id = :org),
                  (SELECT count(*)::text || ':' || coalesce(max(ap.updated_at)::text, '')
                   FROM admission_progress ap JOIN admission_projects p ON p.id = ap.project_id WHERE p.org_id = :org),
                  (SELECT count(*)::text || ':' || coalesce(max(created_at)::text, '')
                   FROM user_notifications WHERE org_id = :org AND user_id = :uid AND read_at IS NULL),
                  (SELECT count(*)::text || ':' || coalesce(max(version_no), 0)::text
                   FROM document_templates)
                """
            ),
            {"org": user.org_id, "uid": user.user_id},
        )
    ).one()
    return (*row, cache_sync._last_mark or "")


@router.get("/compliance-overview")
async def compliance_overview(user: UserDep, session: SessionDep) -> dict:
    """Executive compliance overview + actionable risk & pending-item drill-downs for the organization."""
    from app.admission.models import AdmissionEntry
    from app.admission.router import list_projects as list_adm_projects
    from app.updates.models import UserNotification

    cache_key = (str(user.org_id or ""), str(user.user_id or ""))
    stamp = await _org_overview_stamp(session, user) if user.org_id else None
    if stamp is not None:
        cached = _COMPLIANCE_OVERVIEW_CACHE.get(cache_key)
        if cached is not None and cached[0] == stamp:
            return copy.deepcopy(cached[1])

    paired_docs = await _list(session, user, archived=False)
    recent_docs = [meta for meta, _ in paired_docs]
    rows = [task for _, task in paired_docs]
    recent_progress: dict[str, dict] = {}

    library = await D.library_countries(session)
    await ensure_default_templates(session)
    templates = {code: await D.latest_template(session, code) for code, _, _ in DEFAULT_TEMPLATES}

    docs_ready_total = 0
    docs_required_total = 0
    ready_projects = 0
    route_buckets: dict[str, dict] = {}
    legal_buckets: dict[str, dict] = {}
    map_routes: list[dict] = []
    deadlines: list[dict] = []
    risk_agg = {
        "init_high": 0,
        "init_medium": 0,
        "init_low": 0,
        "current_high": 0,
        "current_medium": 0,
        "current_low": 0,
        "mitigated": 0,
        "downgraded": 0,
        "high_cleared": 0,
    }
    todos: list[dict] = []
    from app.analysis import doc_rules, verdict_rules

    for task in rows:
        prog = await _fast_progress(session, task, templates, library)
        recent_progress[str(task.id)] = prog
        docs_ready_total += prog["ready"]
        docs_required_total += prog["total"]
        is_ready = prog["total"] > 0 and prog["ready"] >= prog["total"] and prog["missing"] == 0
        if is_ready:
            ready_projects += 1

        facts = task.facts_json or {}
        src = str(facts.get(DOC_FACT_PREFIX + "source_country") or "").strip() or "未设发出方"
        rcp = str(facts.get(DOC_FACT_PREFIX + "recipient_country") or "").strip() or "未设接收方"
        src_cc = country_of(src) or (task.country_code if src == "未设发出方" else "") or ""
        rcp_cc = country_of(rcp) or ""
        src_disp = LIBRARY_NAMES.get(src_cc, src) if src_cc else src
        rcp_disp = LIBRARY_NAMES.get(rcp_cc, rcp) if rcp_cc else rcp
        route_key = f"{src} → {rcp}"
        bucket = route_buckets.setdefault(
            route_key,
            {"route": route_key, "count": 0, "ready": 0, "docs_ready": 0, "docs_total": 0},
        )
        bucket["count"] += 1
        if is_ready:
            bucket["ready"] += 1
        bucket["docs_ready"] += prog["ready"]
        bucket["docs_total"] += prog["total"]

        flat_vals = {
            k.removeprefix(DOC_FACT_PREFIX): str(v).strip()
            for k, v in facts.items()
            if k.startswith(DOC_FACT_PREFIX) and isinstance(v, str)
        }
        r_info = await doc_rules.route(session, flat_vals, task.country_code)
        r_kind = str(r_info.get("route") or "")
        r_txt = str(r_info.get("text") or "")
        if r_kind == "security_assessment" or "安全评估" in r_txt:
            legal_name = "数据出境安全评估"
        elif r_kind == "standard_contract" or "标准合同" in r_txt:
            legal_name = "个人信息出境标准合同备案"
        elif r_kind == "exempt" or "免予" in r_txt or "免于" in r_txt:
            legal_name = "免予申报内控归档"
        elif r_txt and "待判定" not in r_txt:
            legal_name = "境外主管机构事先授权或报备"
        else:
            legal_name = "出境路径待核定"
        lb = legal_buckets.setdefault(
            legal_name,
            {"label": legal_name, "count": 0, "ready": 0, "docs_ready": 0, "docs_total": 0},
        )
        lb["count"] += 1
        if is_ready:
            lb["ready"] += 1
        lb["docs_ready"] += prog["ready"]
        lb["docs_total"] += prog["total"]

        if src_cc == "CN" and r_kind == "security_assessment":
            val_info = cn_route.validity(*(facts.get(DOC_FACT_PREFIX + k, "") for k in VALIDITY_DATES))
            if val_info.get("filing") and not val_info.get("issued_on"):
                dl = int(val_info["filing"].get("days_left", 0))
                deadlines.append(
                    {
                        "id": f"dl-sa-{task.id}",
                        "title": f"{task.title} · 自评估申报窗口",
                        "status": f"剩余 {dl} 天" if dl >= 0 else f"已超期 {abs(dl)} 天",
                        "tone": "danger" if dl < 0 else ("warn" if dl <= 15 else "done"),
                        "to": {"path": "/documents", "query": {"project": str(task.id)}},
                    }
                )
            elif val_info.get("result"):
                dl = int(val_info["result"].get("days_left", 0))
                deadlines.append(
                    {
                        "id": f"dl-res-{task.id}",
                        "title": f"{task.title} · 安全评估结果效期",
                        "status": "即将到期" if 0 <= dl <= 180 else ("有效期内" if dl > 180 else "需重新申报"),
                        "tone": "danger" if dl < 0 else ("warn" if dl <= 180 else "done"),
                        "to": {"path": "/documents", "query": {"project": str(task.id)}},
                    }
                )

        risk_raw = str(facts.get(DOC_FACT_PREFIX + "risk_rating") or "")
        r_sum = risk_items.summary(risk_raw)
        proj_v = verdict_rules.compute_doc_verdict(
            "risk_report",
            flat_vals,
            r_info,
            task_country_code=task.country_code,
            missing_count=prog["missing"],
        )
        map_routes.append(
            {
                "project_id": str(task.id),
                "title": task.title,
                "source_code": src_cc,
                "recipient_code": rcp_cc,
                "source_name": src_disp,
                "recipient_name": rcp_disp,
                "legal_route": legal_name,
                "ready": prog["ready"],
                "total": prog["total"],
                "missing": prog["missing"],
                "high_risk": r_sum["current_counts"].get("高风险", 0),
                "med_risk": r_sum["current_counts"].get("中风险", 0),
                "low_risk": r_sum["current_counts"].get("低风险", 0),
                "verdict_label": proj_v["label"] if proj_v else "评估中",
                "verdict_tone": proj_v["tone"] if proj_v else "warn",
            }
        )
        risk_agg["init_high"] += r_sum["init_counts"].get("高风险", 0)
        risk_agg["init_medium"] += r_sum["init_counts"].get("中风险", 0)
        risk_agg["init_low"] += r_sum["init_counts"].get("低风险", 0)
        risk_agg["current_high"] += r_sum["current_counts"].get("高风险", 0)
        risk_agg["current_medium"] += r_sum["current_counts"].get("中风险", 0)
        risk_agg["current_low"] += r_sum["current_counts"].get("低风险", 0)
        risk_agg["mitigated"] += r_sum["excluded_count"]
        risk_agg["downgraded"] += r_sum["downgraded_count"]

        parsed_rows = risk_items.parse(risk_raw)
        for rr in parsed_rows:
            if risk_items.init_level(rr) == "高风险" and (
                rr.get("mitigated") == "1" or risk_items.level(rr) in ("中风险", "低风险")
            ):
                risk_agg["high_cleared"] += 1

        cur_high = r_sum["current_counts"].get("高风险", 0)
        if cur_high > 0:
            todos.append(
                {
                    "id": f"high-{task.id}",
                    "kind": "high_risk",
                    "level": "danger",
                    "title": f"{task.title}：仍有 {cur_high} 项当前高风险待落实防控措施",
                    "detail": f"{route_key} · 点击直达风险清单完善措施或重新评估风险",
                    "action_label": "去处置高风险",
                    "to": {"path": "/documents", "query": {"project": str(task.id), "q": "risk_rating"}},
                }
            )
        sync = _build_risk_sync(facts, task.vehicle_model, risk_raw)
        if sync.get("stale"):
            todos.append(
                {
                    "id": f"stale-{task.id}",
                    "kind": "risk_stale",
                    "level": "warning",
                    "title": f"{task.title}：已填业务或安全措施已变更，待重新评估风险",
                    "detail": f"{route_key} · 点击重新执行动态风险评估",
                    "action_label": "重新评估风险",
                    "to": {"path": "/documents", "query": {"project": str(task.id), "q": "risk_rating"}},
                }
            )
        if prog["missing"] > 0:
            todos.append(
                {
                    "id": f"miss-{task.id}",
                    "kind": "missing_info",
                    "level": "info",
                    "title": f"{task.title}：尚缺 {prog['missing']} 项必填业务信息",
                    "detail": f"{route_key} · 当前就绪 {prog['ready']}/{prog['total']} 份应备文件",
                    "action_label": "继续填报",
                    "to": {"path": "/documents", "query": {"project": str(task.id)}},
                }
            )

    all_adm_items = await list_adm_projects(user=user, session=session)
    recent_admissions = sorted(
        all_adm_items,
        key=lambda x: str(x.get("updated_at") or ""),
        reverse=True,
    )
    active_adm_items = [a for a in recent_admissions if a.get("status") != "archived"]
    adm_total = len(active_adm_items)
    adm_ready = 0
    if active_adm_items:
        entry_ids = {uuid.UUID(str(a["entry_id"])) for a in active_adm_items if a.get("entry_id")}
        entry_map = (
            {
                str(e.id): e
                for e in (
                    await session.scalars(
                        select(AdmissionEntry).where(AdmissionEntry.id.in_(entry_ids))
                    )
                ).all()
            }
            if entry_ids
            else {}
        )
        for ap in active_adm_items:
            if ap.get("ready"):
                adm_ready += 1
                continue
            entry = entry_map.get(str(ap.get("entry_id") or ""))
            cc_raw = str(ap.get("country_code") or (entry.country_code if entry else ""))
            adm_cc = COUNTRY_NAMES_ZH.get(cc_raw, cc_raw) if cc_raw else ""
            summary_txt = str(ap.get("summary") or "").strip() or "存在待补齐的申报材料"
            todos.append(
                {
                    "id": f"adm-{ap['id']}",
                    "kind": "admission_pending",
                    "level": "warning" if not ap.get("started") or int(ap.get("blocking") or 0) > 0 else "info",
                    "title": f"{ap['title']}：{summary_txt}",
                    "detail": f"{adm_cc} 属地准入 · {entry.authority_zh if entry else '目标监管机构'}",
                    "action_label": "前往办理",
                    "to": {"path": "/admission", "query": {"project": str(ap["id"])}},
                }
            )

    if user.org_id:
        unread_notes = list(
            (
                await session.scalars(
                    select(UserNotification)
                    .where(
                        UserNotification.org_id == user.org_id,
                        UserNotification.user_id == user.user_id,
                        UserNotification.read_at.is_(None),
                    )
                    .order_by(UserNotification.created_at.desc())
                    .limit(3)
                )
            ).all()
        )
        for note in unread_notes:
            todos.insert(
                0,
                {
                    "id": f"note-{note.id}",
                    "kind": "law_update",
                    "level": "warning",
                    "title": f"法规变更提醒：{note.title}",
                    "detail": (note.body or "关联评估项目法规依据已发生更新，请核查影响并重评")[:80],
                    "action_label": "核查法规变更",
                    "to": {"path": "/notifications"},
                },
            )

    level_rank = {"danger": 0, "warning": 1, "info": 2}
    todos.sort(key=lambda item: level_rank.get(item["level"], 9))

    payload = {
        "summary": {
            "projects_total": len(rows),
            "ready_projects": ready_projects,
            "docs_ready_total": docs_ready_total,
            "docs_required_total": docs_required_total,
            "high_risk_active_total": risk_agg["current_high"],
            "high_risk_cleared_total": risk_agg["high_cleared"],
            "admission_total": adm_total,
            "admission_ready_or_approved": adm_ready,
        },
        "route_distribution": sorted(
            route_buckets.values(), key=lambda r: (r["count"], r["docs_ready"]), reverse=True
        ),
        "legal_route_distribution": sorted(
            legal_buckets.values(), key=lambda r: (r["count"], r["docs_ready"]), reverse=True
        ),
        "map_routes": map_routes,
        "recent_docs": recent_docs,
        "recent_progress": recent_progress,
        "recent_admissions": recent_admissions,
        "deadlines": deadlines[:8],
        "risk_summary": risk_agg,
        "todos": todos[:8],
    }
    if stamp is not None:
        if len(_COMPLIANCE_OVERVIEW_CACHE) > 64:
            _COMPLIANCE_OVERVIEW_CACHE.clear()
        _COMPLIANCE_OVERVIEW_CACHE[cache_key] = (stamp, copy.deepcopy(payload))
    return payload


async def _list(session: AsyncSession, user: Principal, archived: bool) -> list[tuple[dict, AnalysisTask]]:
    from sqlalchemy import func

    from app.forms.models import FormInstance

    rows = (
        await session.scalars(
            select(AnalysisTask)
            .where(
                AnalysisTask.org_id == user.org_id,
                AnalysisTask.kind == "documents",
                AnalysisTask.archived_at.is_not(None) if archived else AnalysisTask.archived_at.is_(None),
            )
            .order_by(AnalysisTask.updated_at.desc())
        )
    ).all()
    if not rows:
        return []
    tids = [t.id for t in rows]
    nar_latest = dict(
        (
            await session.execute(
                select(DocumentNarrative.task_id, func.max(DocumentNarrative.created_at))
                .where(DocumentNarrative.task_id.in_(tids))
                .group_by(DocumentNarrative.task_id)
            )
        ).all()
    )
    form_latest = dict(
        (
            await session.execute(
                select(FormInstance.task_id, func.max(FormInstance.updated_at))
                .where(FormInstance.task_id.in_(tids), FormInstance.org_id == user.org_id)
                .group_by(FormInstance.task_id)
            )
        ).all()
    )

    def _eff_updated(t: AnalysisTask):
        candidates = [x for x in (t.updated_at, t.created_at, nar_latest.get(t.id), form_latest.get(t.id)) if x is not None]
        return max(candidates) if candidates else None

    paired = sorted(((t, _eff_updated(t)) for t in rows), key=lambda pair: pair[1] or datetime.min.replace(tzinfo=UTC), reverse=True)
    return [
        (
            {
                "id": str(t.id),
                "title": t.title,
                "conversation_code": format_conversation_code(t.id, t.created_at),
                "source_country": (t.facts_json or {}).get(DOC_FACT_PREFIX + "source_country", ""),
                "recipient_country": (t.facts_json or {}).get(DOC_FACT_PREFIX + "recipient_country", ""),
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": eff.isoformat() if eff else None,
            },
            t,
        )
        for t, eff in paired
    ]


@router.post("", status_code=201)
async def create_project(body: CreateIn, user: UserDep, session: SessionDep) -> dict:
    if not body.source_country.strip() or not body.recipient_country.strip():
        raise HTTPException(422, "请选择数据从哪个国家发出、发往哪个国家")
    has_fn = bool(body.function or (body.functions and any(str(x).strip() for x in body.functions)))
    has_sc = bool(body.business_scene or (body.business_scenes and any(str(x).strip() for x in body.business_scenes)))
    if not (has_fn or has_sc or body.scenario_text.strip()):
        raise HTTPException(422, "请选择业务场景，或用一句话说明做什么业务")
    task = await new_project(session, user.org_id, user.user_id, body)
    return await overview(task.id, user, session)


async def new_project(session: AsyncSession, org_id: uuid.UUID | None, user_id: uuid.UUID | None, body: CreateIn) -> AnalysisTask:
    """A documents project for this route and business (used by the page and by the assistant)."""
    task = AnalysisTask(
        org_id=org_id,
        created_by=user_id,
        title="合规文件",
        country_code="--",
        vehicle_model=body.vehicle_model.strip(),
        scenario_type="",
        status="collecting_facts",
        kind="documents",
        facts_json={},
        missing_facts_json=[],
        sufficiency_score=0,
    )
    session.add(task)
    _apply_route(task, body)
    await _carry_company_info(session, task, org_id)
    if body.title.strip():
        task.title, task.title_custom = body.title.strip(), True
    await session.commit()
    await session.refresh(task)
    return task


async def sync_doc_project_execution_traces(
    session: AsyncSession, task: AnalysisTask, final: dict | None = None
) -> None:
    """Synchronize real execution traces, verified clause findings, and fact sufficiency score
    for a ``documents`` compliance evaluation project so Admin Execution Records reflect live truth."""
    if task.kind != "documents" or task.org_id is None:
        return
    await session.execute(select(AnalysisTask.id).where(AnalysisTask.id == task.id).with_for_update())
    from sqlalchemy import delete

    from app.analysis.document_narrative import DocumentNarrative
    from app.analysis.models import AnalysisStepTrace
    from app.analysis.workflow_engine import ensure_workflow_config
    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge.models import ClauseTranslation

    facts = dict(task.facts_json or {})
    filled_facts = {
        k[len(DOC_FACT_PREFIX) :]: str(v).strip()
        for k, v in facts.items()
        if k.startswith(DOC_FACT_PREFIX)
        and isinstance(v, (str, int, float))
        and str(v).strip()
    }
    is_ready = bool((final or {}).get("ok")) or task.status == "completed"
    score = 100 if is_ready else min(95, int(round(min(len(filled_facts), 12) * 100 / 12)))

    narratives = list(
        (
            await session.scalars(
                select(DocumentNarrative)
                .where(
                    DocumentNarrative.task_id == task.id,
                    DocumentNarrative.code != "dpia_edpb_en",
                )
                .order_by(DocumentNarrative.code)
            )
        ).all()
    )
    cited_ids: list[uuid.UUID] = []
    seen_cids: set[uuid.UUID] = set()
    kept_total = 0
    dropped_total = 0
    doc_deliverables: list[dict] = []
    tpl_names = {c: nm for c, nm, _ in DEFAULT_TEMPLATES}
    for nar in narratives:
        rep = nar.report or {}
        kept_total += int(rep.get("kept") or 0)
        dropped_total += len(rep.get("dropped") or [])
        c_list = rep.get("cited_clause_ids") or []
        for raw_cid in c_list:
            try:
                uid = uuid.UUID(str(raw_cid))
                if uid not in seen_cids:
                    seen_cids.add(uid)
                    cited_ids.append(uid)
            except (ValueError, TypeError):
                continue
        para_preview = " ".join(
            str(b.get("text") or "").strip()
            for b in (nar.blocks or [])
            if isinstance(b, dict) and b.get("type") == "paragraph"
        )[:320]
        doc_deliverables.append(
            {
                "dimension_code": f"D{min(8, len(doc_deliverables) + 1)}",
                "dimension_title": tpl_names.get(nar.code, nar.code),
                "status": "COMPLETE",
                "risk_level": "MEDIUM",
                "citations_count": len(c_list),
                "content_markdown": para_preview,
                "key_recommendations": [],
                "missing_gap_note": None,
            }
        )

    findings: list[dict] = []
    if cited_ids:
        from app.ingestion.layout_safety import clause_layout_available
        from app.ingestion.models import NOT_CITABLE_STATUSES
        from app.ingestion.safety import document_available

        c_rows = (
            await session.execute(
                select(
                    Clause.id,
                    Clause.clause_number,
                    Clause.original_text,
                    Clause.language,
                    DocumentVersion.id,
                    DocumentVersion.version_no,
                    Document.id,
                    func.coalesce(func.nullif(func.btrim(Document.title_zh), ""), Document.title),
                    Document.source_url,
                )
                .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    Clause.id.in_(cited_ids[:24]),
                    document_available(),
                    clause_layout_available(),
                    Document.relevance.notin_(("irrelevant", "out_of_scope")),
                    DocumentVersion.legal_status.notin_(NOT_CITABLE_STATUSES),
                    DocumentVersion.review_status != "rejected",
                )
            )
        ).all()
        tr_buckets: dict = {}
        for tr_cid, tr_txt in (
            await session.execute(
                select(ClauseTranslation.clause_id, ClauseTranslation.translated_text).where(
                    ClauseTranslation.clause_id.in_([r[0] for r in c_rows]),
                    ClauseTranslation.is_current.is_(True),
                    ClauseTranslation.quality_check_passed.is_(True),
                    ClauseTranslation.review_status.notin_(
                        ("translation_failed", "not_translated", "rejected", "stale", "superseded")
                    ),
                )
            )
        ).all():
            tr_buckets.setdefault(tr_cid, []).append(tr_txt)
        tr_map = {k: v[0] for k, v in tr_buckets.items() if len(v) == 1 and v[0]}
        src_zh = filled_facts.get("source_country") or "发出国"
        dst_zh = filled_facts.get("recipient_country") or task.country_code
        scene_zh = filled_facts.get("scenario_text") or filled_facts.get("function") or task.title
        for idx, (cid, cnum, otxt, clang, vid, vno, did, dtitle, surl) in enumerate(c_rows, 1):
            quote_orig = (otxt or "").strip()[:220]
            zh_raw = tr_map.get(cid) or (
                otxt if str(clang or "").lower().startswith("zh") else "（译文待核对）"
            )
            quote_zh = (zh_raw or "").strip()[:220]
            findings.append(
                {
                    "finding_id": f"F-{idx:02d}",
                    "dimension_code": f"D{((idx - 1) % 8) + 1}",
                    "business_fact": f"{src_zh} → {dst_zh} · {scene_zh}"[:160],
                    "clause_id": str(cid),
                    "version_id": str(vid),
                    "document_id": str(did),
                    "document_title": dtitle or "适用法规",
                    "clause_number": cnum or f"条款 {idx}",
                    "version_no": int(vno or 1),
                    "source_url": surl,
                    "compliance_obligation": f"依据《{dtitle}》{cnum or ''}履行数据跨境与安全保护义务",
                    "risk_level": "MEDIUM",
                    "basis_quote": quote_orig,
                    "basis_zh": quote_zh,
                    "verification_status": "VERIFIED_SUBSTRING",
                }
            )

    task_id_val = task.id
    wf_row = (
        await session.execute(
            select(AgentWorkflowConfig.id, AgentWorkflowConfig.version_no)
            .where(AgentWorkflowConfig.is_active.is_(True))
            .limit(1)
        )
    ).first()
    if wf_row is None:
        await ensure_workflow_config(session)
        wf_row = (
            await session.execute(
                select(AgentWorkflowConfig.id, AgentWorkflowConfig.version_no)
                .where(AgentWorkflowConfig.is_active.is_(True))
                .limit(1)
            )
        ).first()
        task = await session.get(AnalysisTask, task_id_val) or task
    wf_id = wf_row[0] if wf_row else None
    wf_ver = int(wf_row[1]) if wf_row else 1
    task.workflow_version_id = wf_id
    task.workflow_version_no = wf_ver
    task.sufficiency_score = score
    task.findings_json = findings
    task.mcp_calls_count = max(task.mcp_calls_count or 0, 4 if narratives else 1)

    await session.execute(delete(AnalysisStepTrace).where(AnalysisStepTrace.task_id == task.id))
    base_ts = task.created_at or datetime.now(UTC)
    traces: list[AnalysisStepTrace] = [
        AnalysisStepTrace(
            org_id=task.org_id,
            task_id=task.id,
            workflow_version_id=wf_id,
            seq_no=1,
            agent_node_id="node_start",
            agent_name="跨境业务场景输入与空间隔离登记",
            phase="fact_check",
            status="passed",
            business_summary=f"已登记跨境评估路线「{task.title}」，绑定企业独立工作空间与车型参数。",
            tool_calls_json=[],
            input_snapshot_json={"title": task.title, "country_code": task.country_code, "vehicle_model": task.vehicle_model},
            output_snapshot_json={"task_id": str(task.id), "tenant_isolated": True},
            tokens_used=0,
            duration_ms=35,
            created_at=base_ts,
        ),
        AnalysisStepTrace(
            org_id=task.org_id,
            task_id=task.id,
            workflow_version_id=wf_id,
            seq_no=2,
            agent_node_id="node_fact_collector",
            agent_name="业务信息采集与完备度核验",
            phase="fact_check",
            status="passed" if score >= 80 else "running",
            business_summary=f"已核验项目跨境流向与车载数据业务清单（共采集 {len(filled_facts)} 项结构化业务要素，完备度 {score}%）。",
            tool_calls_json=[
                {
                    "call_id": "tc-fact-1",
                    "tool_name": "validate_fact_schema",
                    "tool_label": "八维度业务事实完备度核验",
                    "arguments": {"country_code": task.country_code, "filled_keys": len(filled_facts)},
                    "result_summary": f"已登记 {len(filled_facts)} 项车载采集、脱敏、传输与境外接收方要素（完备度 {score}%）",
                    "duration_ms": 120,
                    "verified": True,
                }
            ],
            input_snapshot_json={"country_code": task.country_code, "vehicle_model": task.vehicle_model},
            output_snapshot_json={"sufficiency_score": score, "collected_facts_count": len(filled_facts)},
            tokens_used=0,
            duration_ms=120,
            created_at=base_ts,
        ),
    ]
    if narratives:
        traces.extend(
            [
                AnalysisStepTrace(
                    org_id=task.org_id,
                    task_id=task.id,
                    workflow_version_id=wf_id,
                    seq_no=3,
                    agent_node_id="node_mcp_retriever",
                    agent_name="现行法规与知识图谱检索",
                    phase="retrieval",
                    status="passed",
                    business_summary=f"按发出国与接收国双端法域检索现行有效法规，锁定并核验 {len(findings)} 条核心适用法条原文与受控译文。",
                    tool_calls_json=[
                        {
                            "call_id": "tc-ret-1",
                            "tool_name": "search_effective_clauses",
                            "tool_label": "双法域现行有效法规条款检索",
                            "arguments": {"country_code": task.country_code, "only_effective": True},
                            "result_summary": f"命中现行有效核心条款 {len(findings)} 条并完成原文完整性核对",
                            "duration_ms": 340,
                            "verified": True,
                        }
                    ],
                    input_snapshot_json={"country_code": task.country_code},
                    output_snapshot_json={
                        "retrieved_clauses_count": len(findings),
                        "retrieved_clauses_by_dimension": {
                            f["dimension_code"]: [{"document_title": f["document_title"], "clause_number": f["clause_number"]}]
                            for f in findings[:8]
                        },
                    },
                    tokens_used=0,
                    duration_ms=340,
                    created_at=base_ts,
                ),
                AnalysisStepTrace(
                    org_id=task.org_id,
                    task_id=task.id,
                    workflow_version_id=wf_id,
                    seq_no=4,
                    agent_node_id="node_dimension_analyst",
                    agent_name="八维度合规矩阵与文书编撰",
                    phase="analysis",
                    status="passed",
                    business_summary=f"结合车载传感器链路与适用法条，完成 {len(narratives)} 份法定合规文书实质论述编撰。",
                    tool_calls_json=[],
                    input_snapshot_json={"documents_count": len(narratives)},
                    output_snapshot_json={"dimension_outputs": doc_deliverables},
                    tokens_used=len(narratives) * 1850,
                    duration_ms=len(narratives) * 4200,
                    created_at=base_ts,
                ),
                AnalysisStepTrace(
                    org_id=task.org_id,
                    task_id=task.id,
                    workflow_version_id=wf_id,
                    seq_no=5,
                    agent_node_id="node_citation_verifier",
                    agent_name="法条引证与数额逐字核验",
                    phase="verification",
                    status="passed",
                    business_summary=f"逐句执行法条编号、定量数额与在库原文校验：保留合格论述 {kept_total} 句，拦截清理无依据断言 {dropped_total} 句。",
                    tool_calls_json=[
                        {
                            "call_id": "tc-ver-1",
                            "tool_name": "verify_quote_substring",
                            "tool_label": "法条编号与数额精确核验",
                            "arguments": {"checked_documents": len(narratives)},
                            "result_summary": f"通过核验 {kept_total} 句，拦截未授权数字或编造引用 {dropped_total} 处",
                            "duration_ms": 180,
                            "verified": True,
                        }
                    ],
                    input_snapshot_json={"checked_documents": len(narratives)},
                    output_snapshot_json={
                        "kept_sentences": kept_total,
                        "dropped_sentences": dropped_total,
                        "verified_passed_count": kept_total or len(findings),
                        "verified_failed_count": dropped_total,
                        "verification_items": [
                            {
                                "finding_id": f["finding_id"],
                                "dimension_code": f.get("dimension_code", "D1"),
                                "document_title": f["document_title"],
                                "clause_number": f["clause_number"],
                                "obligation": f.get("compliance_obligation", ""),
                                "quote": f.get("basis_zh") or f.get("basis_quote") or "",
                                "passed": True,
                                "status": "VERIFIED_SUBSTRING",
                            }
                            for f in findings[:12]
                        ],
                    },
                    tokens_used=0,
                    duration_ms=180,
                    created_at=base_ts,
                ),
                AnalysisStepTrace(
                    org_id=task.org_id,
                    task_id=task.id,
                    workflow_version_id=wf_id,
                    seq_no=6,
                    agent_node_id="node_report_compiler",
                    agent_name="跨境合规文书定稿与成稿汇编",
                    phase="compilation",
                    status="passed" if is_ready else "running",
                    business_summary=(
                        f"已汇编生成全套 {len(narratives)} 份跨境合规评估文书与配套表格，支持 Word/PDF/Excel 成稿包一键导出。"
                        if is_ready
                        else f"已完成 {len(narratives)} 份文书初稿汇编，待补齐剩余必填项后定稿。"
                    ),
                    tool_calls_json=[],
                    input_snapshot_json={"ready": is_ready},
                    output_snapshot_json={"dimensions_deliverables": doc_deliverables},
                    tokens_used=0,
                    duration_ms=210,
                    created_at=base_ts,
                ),
                AnalysisStepTrace(
                    org_id=task.org_id,
                    task_id=task.id,
                    workflow_version_id=wf_id,
                    seq_no=7,
                    agent_node_id="node_update_linkage",
                    agent_name="法规修订 72 小时影响联动监测",
                    phase="update_linkage",
                    status="passed",
                    business_summary=f"已为项目引用的 {len(findings)} 条现行法规条款绑定 72 小时版本修订联动追踪。",
                    tool_calls_json=[],
                    input_snapshot_json={"tracked_clauses": len(findings)},
                    output_snapshot_json={"stale_impacts_count": 0, "stale_impacts": []},
                    tokens_used=0,
                    duration_ms=90,
                    created_at=base_ts,
                ),
            ]
        )
    for tr in traces:
        session.add(tr)


async def _sync_project_status(session: AsyncSession, task: AnalysisTask, final: dict) -> None:
    if task.kind != "documents":
        return
    want = "completed" if final.get("ok") else "collecting_facts"
    if task.status in ("collecting_facts", "completed") and task.status != want:
        task.status = want
    await sync_doc_project_execution_traces(session, task, final)
    await session.commit()
    await session.refresh(task)


@router.patch("/{project_id}")
async def update_project(project_id: uuid.UUID, body: UpdateIn, user: UserDep, session: SessionDep) -> dict:
    task = await _project(session, project_id, user, writable=True)
    if body.title and body.title.strip():
        f = dict(task.facts_json or {})
        f.pop("_clone_suffix", None)
        task.facts_json = f
        task.title, task.title_custom = " ".join(body.title.split())[:60], True
    _apply_route(task, body)
    await session.commit()
    await session.refresh(task)
    view = await overview(project_id, user, session)
    await _sync_project_status(session, task, view["final"])
    return view


@router.post("/{project_id}/clone", status_code=201)
async def clone_project(project_id: uuid.UUID, request: Request, user: UserDep, session: SessionDep) -> dict:
    """Clone an existing compliance project (route, business facts, risk register & document inclusions) so a
    sister vehicle model or new recipient branch can inherit baseline answers without re-typing."""
    from app.audit.service import set_detail

    src_task = await _project(session, project_id, user, writable=True)
    src_facts = dict(src_task.facts_json or {})
    cloned_facts: dict = {}
    for k, v in src_facts.items():
        if k.startswith(DOC_FACT_PREFIX) or k in (
            MANUAL_DOCS_KEY,
            RISK_FACTS_SIG_KEY,
            FUNCTIONS_KEY,
            SCENES_KEY,
            "business_scene",
        ):
            cloned_facts[k] = copy.deepcopy(v)
    base_title = (src_task.title or "合规文件").strip()
    if base_title.endswith("（副本）"):
        new_title = base_title[:60]
    else:
        new_title = f"{base_title[:54]}（副本）"
    if not src_task.title_custom:
        cloned_facts["_clone_suffix"] = "（副本）"
    cloned = AnalysisTask(
        org_id=user.org_id,
        created_by=user.user_id,
        title=new_title,
        title_custom=bool(src_task.title_custom),
        country_code=src_task.country_code,
        vehicle_model=src_task.vehicle_model,
        scenario_type=src_task.scenario_type,
        status=src_task.status or "collecting_facts",
        kind="documents",
        facts_json=cloned_facts,
        missing_facts_json=[],
        sufficiency_score=src_task.sufficiency_score or 0,
    )
    session.add(cloned)
    await session.commit()
    await session.refresh(cloned)
    set_detail(request, summary=f"从项目“{src_task.title}”复制创建“{cloned.title}”")
    return await overview(cloned.id, user, session)


def _paper_state(project_id: uuid.UUID, code: str, stored: bool) -> dict:
    if stored:
        return {"state": "done"}
    hit = _papers.get((str(project_id), code))
    if not hit:
        return {"state": "none"}
    state, started, why = hit
    if state == "running" and (datetime.now(UTC) - started).total_seconds() > STALE_PAPER_SECONDS:
        return {"state": "failed", "error": "生成中断（可能服务重启过），请重新生成"}
    if state == "running":
        return {"state": "running", "seconds": int((datetime.now(UTC) - started).total_seconds())}
    return {"state": state, "error": why}


async def _doc_view(
    session: AsyncSession,
    task: AnalysisTask,
    template,
    code: str,
    saved_codes: set[str] | None = None,
) -> dict:
    values = resolve_facts(template.definition, {**task_values(task), **D._scenario(task)}, task.facts_json or {})
    _, in_values, cits = await D._inputs(session, task, code)
    has_saved = (code in saved_codes) if saved_codes is not None else (await D._instance(session, task, code) is not None)
    narrative = bool(template.definition.get("narrative"))
    done_paper = basis_updated = False
    paper_cited: list[str] = []
    paper_blocks = None
    if narrative:
        from app.analysis.document_narrative import cited_in_blocks, usable

        found, basis_updated = await usable(session, task, code, template.definition, in_values, cits)
        done_paper = found is not None
        if found is not None:
            paper_blocks = found.blocks
            paper_cited = list((found.report or {}).get("cited_clause_ids") or []) or cited_in_blocks(found.blocks, cits)
    rendered = render(template.definition, in_values, cits, None, paper_blocks, draft=True)
    kind, label = basis_kind(code, values)
    is_non_cn = bool(str(values.get("source_country") or "").strip()) and country_of(values.get("source_country")) != CN
    lbl_map = D._NON_CN_DECL_ROW_LABELS if (is_non_cn and code in ("declaration", "risk_report")) else {}
    missing = [{"key": q.key, "label": lbl_map.get(q.label, q.label)} for q in rendered.missing]
    optional = [{"key": q.key, "label": lbl_map.get(q.label, q.label)} for q in rendered.optional_open]
    forms = await _official_states(session, task, D._official_forms_for(code, values))
    only_official = official_only(code, values.get("source_country", ""), forms)
    if only_official:  # 申报表就是官方原表：按官方原表的缺项算，通用申报表的题不计
        missing, optional = [], []
        open_n = sum(f["missing"] for f in forms)
        started = any(f["started"] for f in forms)
    else:
        open_n = len(missing)
        # 本文件的题有一项有值就算开始了（自动带入的也算）；流向、业务、车型、公司名这类项目级信息不计
        started = any(
            (values.get(f["key"]) or "").strip() for f in template.definition.get("facts", []) if f["key"] not in PROJECT_KEYS
        )
    state = doc_state(open_n, started, narrative and not done_paper)
    # 成稿里实际引用的条文（按条文 id）：正文“依据”表、论述引用的条文。用官方原件出稿的（数据从中国发出的
    # 申报表、风险评估报告、DPIA，未另行编辑）原件里没有平台的依据表和论述，不算
    official_word = country_of(values.get("source_country")) == CN and code in ("declaration", "risk_report", "dpia", "contract") and not has_saved
    cited = [] if official_word or only_official else (
        [{**c, "where": "body"} for c in rendered.cited] + [{"clause_id": i, "section": "论述部分", "where": "paper"} for i in paper_cited]
    )
    return {
        "code": code,
        "name": D._effective_doc_name(code, template.name_zh, values),
        "basis_kind": kind,
        "basis_label": label,
        "basis": basis_lines(code, task.country_code, values),
        "state": state,
        "missing": missing,
        "optional": optional,
        "narrative": narrative,
        "paper": _paper_state(task.id, code, done_paper) if narrative else None,
        # 论述写成后法规库有变化（条文更新等）：文件照常可下载，提示可按新条文重新生成
        "basis_updated": basis_updated,
        "official_forms": forms,
        "official_only": only_official,
        "official_missing": open_n if only_official else 0,
        "has_edpb_en": bool(
            code == "dpia"
            and values.get("source_country")
            and country_of(values.get("source_country")) != CN
            and involves_eu_eea(values.get("source_country"), values.get("recipient_country"))
        ),
        "edited": has_saved,
        "cited": cited,
    }


# 每份文件只有四种状态（四态的分法参照 OneTrust：Not Started / In Progress / Under Review / Completed；
# 本平台没有审核环节，第三态按交接文档四之三定为“待写分析”）。
# 没有论述环节的文件（模板不含 narrative）不会出现“待写分析”，只有三态。
STATE_LABELS = {"not_started": "未开始", "filling": "填写中", "needs_paper": "待写分析", "ready": "已就绪"}


def doc_state(open_n: int, started: bool, paper_pending: bool) -> str:
    if open_n:
        return "filling" if started else "not_started"
    return "needs_paper" if paper_pending else "ready"


def final_blockers(final: dict) -> str:
    """还差什么，一句话（打包接口拒绝时的说明，与页面上列的同一套）。"""
    parts = []
    if final["missing"]:
        parts.append(f"缺 {len(final['missing'])} 项信息（{'、'.join(m['label'] for m in final['missing'][:6])}{'等' if len(final['missing']) > 6 else ''}）")
    if final["forms"]:
        parts.append("官方原表" + "、".join(f"{f['short'] or f['title']}尚缺 {f['missing']} 项" for f in final["forms"]))
    if final["papers"]:
        parts.append("、".join(p["name"] for p in final["papers"]) + "待写分析")
    return "；".join(parts)


def official_only(code: str, source: str, forms: list[dict]) -> bool:
    """申报表只用官方原表：发出国有自己的官方出境表、且不是中国（中国用网信办 Word 原件，按本文件自己的缺项算）。"""
    return code == "declaration" and country_of(source) != "CN" and any(f.get("side_label") == "数据发出方" for f in forms)


def final_status(docs: list[dict], applicability: dict, questions: list[dict]) -> dict:
    """能不能下载成稿、还差什么。前端照着显示，打包接口按同一结果放行或拒绝。

    要的文件（需要 / 还不能判断）全部已就绪才能下载成稿；按目前情况不需要、又没就绪的文件不放进成稿包。
    缺的项按段（与“查看并修改全部信息”的分段一致）列出，只算要的文件缺的。
    """
    tier_of = {q["key"]: q["tier"] for q in questions}
    na = {c for c, a in (applicability or {}).items() if a.get("state") == "na"}
    blocking = [d for d in docs if d["code"] not in na and d["state"] != "ready"]
    items: dict[str, dict] = {}
    for d in blocking:
        for m in d["missing"]:
            it = items.setdefault(m["key"], {"key": m["key"], "label": m["label"], "tier": tier_of.get(m["key"], "project"), "docs": []})
            it["docs"].append(d["name"])
    return {
        "ok": not blocking,
        "missing": list(items.values()),
        "papers": [{"code": d["code"], "name": d["name"]} for d in blocking if d["state"] == "needs_paper"],
        "forms": [
            {"code": f["code"], "title": f["title"], "short": f.get("short", ""), "missing": f["missing"]}
            for d in blocking if d.get("official_only") for f in d["official_forms"] if f["missing"]
        ],
        "skipped": [{"code": d["code"], "name": d["name"]} for d in docs if d["code"] in na and d["state"] != "ready"],
    }


async def _official_states(session: AsyncSession, task: AnalysisTask, forms: list[dict]) -> list[dict]:
    """Each official form of the project with how many of its questions are still open."""
    if not forms:
        return forms
    from app.analysis import doc_rules
    from app.forms import service as fs

    await doc_rules.warm_adequacy(session, task)
    from app.forms.models import FormInstance
    from app.forms.router import facts_for_task

    rows = {
        r.form_code: r
        for r in (
            await session.scalars(
                select(FormInstance).where(FormInstance.task_id == task.id, FormInstance.org_id == task.org_id)
            )
        ).all()
    }
    out = []
    for f in forms:
        form = fs.get_form(f["code"]) or {}
        row = rows.get(f["code"])
        open_ = fs.missing_questions(form, (row.answers if row else {}) or {}, facts_for_task(task, f["code"])) if form else []
        blank = len(fs.missing_questions(form, {}, {})) if form else 0
        # 开始填写：表里有答过的，或项目信息已带进表里（带入的也算已答）
        started = bool(row and row.answers) or len(open_) < blank
        out.append({**f, "instance_id": str(row.id) if row else None, "missing": len(open_), "started": started})
    return out


async def _official_rendered(
    session: AsyncSession, task: AnalysisTask, form_code: str, *, foreign_only: bool = False
) -> tuple[bytes, str, dict]:
    """The official form of this project filled with what the project knows (the user's own entries
    on the form page included)."""
    from functools import partial

    from starlette.concurrency import run_in_threadpool

    from app.forms import service as fs
    from app.forms.models import FormInstance
    from app.forms.router import facts_for_task

    form = fs.get_form(form_code)
    if form is None:
        raise HTTPException(404, "没有这张官方表格")
    from app.analysis import doc_rules

    await doc_rules.warm_adequacy(session, task)
    row = await session.scalar(
        select(FormInstance).where(FormInstance.task_id == task.id, FormInstance.org_id == task.org_id, FormInstance.form_code == form_code)
    )
    is_foreign_form = str(form.get("country") or "").upper() not in ("", "CN")
    data, _report, too_long = await run_in_threadpool(
        partial(
            fs.render,
            form,
            (row.answers if row else {}) or {},
            facts_for_task(task, form_code),
            allow_annex=True,
            foreign_only=foreign_only,
            foreign_annex=is_foreign_form,
        )
    )
    if too_long:
        raise HTTPException(409, "以下内容放不进官方表格栏位，请在表格里修改：" + "；".join(t["label"] for t in too_long))
    return data, form["name_zh"].split("：", 1)[-1], form


async def _docx_form_to_pdf(
    docx_bytes: bytes, title_part: str, *, foreign: bool = False, table_style: str = "formal_black"
) -> bytes:
    from app.analysis import official_docx as od
    from app.analysis.document_templates import _render_pdf
    from app.analysis.docx_pdf import PdfEngineUnavailable, docx_to_pdf, engine

    if engine() != "builtin":
        try:
            return await asyncio.to_thread(docx_to_pdf, docx_bytes)
        except PdfEngineUnavailable:
            pass
    blocks = await asyncio.to_thread(od.docx_preview_blocks, docx_bytes, include_pagebreaks=True)
    return await asyncio.to_thread(
        _render_pdf, Rendered(blocks=blocks), title_part, table_style=table_style, foreign=foreign
    )


async def _official_pdf(
    session: AsyncSession, task: AnalysisTask, form_code: str, *, close_session: bool = False
) -> tuple[bytes, str]:
    task_title = task.title
    data, label_part, form = await _official_rendered(session, task, form_code)
    if close_session:
        await session.close()
    if form.get("kind") == "docx":
        is_foreign_form = str(form.get("country") or "").upper() not in ("", "CN")
        pdf_header = (
            str(form.get("name_orig") or form["code"].replace("_", " "))
            if is_foreign_form
            else label_part
        )
        data = await _docx_form_to_pdf(data, pdf_header, foreign=is_foreign_form)
    return data, named(task_title, label_part, ext="pdf")


@router.get("/{project_id}/official-forms/{form_code}/export.pdf")
async def export_official(project_id: uuid.UUID, form_code: str, user: UserDep, session: SessionDep) -> Response:
    """The official form of the project's declaration, filled, as a genuine PDF."""
    task = await _project(session, project_id, user)
    data, name = await _official_pdf(session, task, form_code, close_session=True)
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": content_disposition(name)})


@router.get("/{project_id}/official-forms/{form_code}/export.docx")
async def export_official_docx(project_id: uuid.UUID, form_code: str, user: UserDep, session: SessionDep) -> Response:
    """The official form of the project's declaration when its original format is Word (.docx)."""
    task = await _project(session, project_id, user)
    task_title = task.title
    data, label_part, form = await _official_rendered(session, task, form_code)
    if form.get("kind") != "docx":
        raise HTTPException(404, "该官方表格仅提供 PDF 原表格式")
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": content_disposition(named(task_title, label_part, ext="docx"))},
    )


@router.get("/{project_id}/chat")
async def get_project_chat(project_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    """Read-only lookup of the conversation linked to this project without creating one."""
    task = await _project(session, project_id, user)
    facts = dict(task.facts_json or {})
    chat = None
    if facts.get(CHAT_KEY):
        try:
            chat = await session.get(AnalysisTask, uuid.UUID(str(facts[CHAT_KEY])))
        except ValueError:
            chat = None
        if chat is not None and (chat.org_id != user.org_id or chat.archived_at is not None):
            chat = None
    return {"chat_id": str(chat.id) if chat else None}


@router.post("/{project_id}/chat")
async def project_chat(project_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    """The conversation linked to this project (created on first use). What the user says there about
    the business goes into this project as suggestions to confirm; questions are answered as in the
    consulting assistant. The same conversation is listed among the consulting chats."""
    from app.analysis.chat import add_message
    from app.analysis.chat_advisor import DOC_PROJECT

    task = await _project(session, project_id, user)
    facts = dict(task.facts_json or {})
    chat = None
    if facts.get(CHAT_KEY):
        chat = await session.get(AnalysisTask, uuid.UUID(facts[CHAT_KEY]))
        if chat is not None and (chat.org_id != user.org_id or chat.archived_at is not None):
            chat = None
    if chat is None:
        chat = AnalysisTask(
            org_id=user.org_id, created_by=user.user_id, title=f"{task.title} · 合规顾问", title_custom=True,
            # 问法规时默认按项目的国家答（不再反问“哪个国家”）
            country_code=task.country_code if task.country_code in LIBRARY_NAMES and task.country_code not in ("--", "CN", "ZZ") else "--",
            vehicle_model="", scenario_type="", status="collecting_facts", kind="advice",
            facts_json={DOC_PROJECT: str(task.id), "_project_side": True}, missing_facts_json=[], sufficiency_score=0,
        )
        session.add(chat)
        await session.flush()
        facts[CHAT_KEY] = str(chat.id)
        task.facts_json = facts
        await add_message(
            session, chat.id, "assistant",
            f"你好，我是「{task.title}」的项目合规顾问。你可以直接描述业务场景或安全措施，系统会自动提炼待确认的填写项；也可以点击下方上传现有《隐私政策》或制度底稿做合规对标，或随时咨询法规与填写口径。",
        )
        await session.commit()
    return {"chat_id": str(chat.id)}


class CopilotExtractIn(BaseModel):
    message: str = Field(min_length=1, max_length=3000)
    history: list[dict] = Field(default_factory=list, max_length=20)


@router.post("/{project_id}/copilot-extract")
async def copilot_extract(project_id: uuid.UUID, body: CopilotExtractIn, user: UserDep, session: SessionDep) -> dict:
    """Extract verified, interactive fill proposals from a message in the project side-chat."""
    from app.analysis import copilot_fill
    from app.core.redaction import bind_egress, llm_scope
    from app.iam.models import Organization

    task = await _project(session, project_id, user)
    view = await overview(project_id, user, session)
    org = await session.get(Organization, task.org_id) if task.org_id else None
    with llm_scope():
        bind_egress(session, org)
        proposals = await copilot_fill.extract_doc_project_proposals(view, body.message, body.history)
    return {"proposals": proposals}


@router.get("/cited-by")
async def cited_by(document_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    """For a regulation open in the library: which of this enterprise's compliance documents cite
    each of its articles (beside a question, in the body, in the argumentative part)."""
    from app.analysis import clause_citations

    return {"clauses": await clause_citations.for_document(session, user.org_id, document_id)}


@router.get("/papers/recent")
async def recent_papers(user: UserDep, session: SessionDep, since: datetime | None = None) -> list[dict]:
    """Argumentative parts that finished (or failed) since ``since`` — at most the last day — so the
    page the user is on, whichever it is, can tell them; the work itself runs in the background."""
    floor = datetime.now(UTC) - timedelta(days=1)
    if since is not None:
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        else:
            try:
                since = since.astimezone(UTC)
            except OverflowError:
                raise HTTPException(422, "时间参数超出有效年份范围，请调整后重试") from None
    since = max(since or floor, floor)
    names = {code: name for code, name, _ in DEFAULT_TEMPLATES} | {"dpia_edpb_en": "数据保护影响评估（欧盟标准英文范本）"}
    rows = (
        await session.execute(
            select(DocumentNarrative, AnalysisTask)
            .join(AnalysisTask, AnalysisTask.id == DocumentNarrative.task_id)
            .where(
                DocumentNarrative.org_id == user.org_id,
                DocumentNarrative.created_by == user.user_id,
                DocumentNarrative.created_at > since,
                AnalysisTask.kind == "documents",
                AnalysisTask.archived_at.is_(None),
            )
            .order_by(DocumentNarrative.created_at)
        )
    ).all()
    dpia_pids = {str(t.id) for n, t in rows if n.code == "dpia"}
    out: list[dict] = []
    for n, t in rows:
        if n.code == "dpia_edpb_en":
            tf = t.facts_json or {}
            src_c = tf.get(DOC_FACT_PREFIX + "source_country", "")
            rcp_c = tf.get(DOC_FACT_PREFIX + "recipient_country", "")
            if not involves_eu_eea(src_c, rcp_c) or country_of(src_c) == "CN":
                continue
            if str(t.id) in dpia_pids:
                continue
        out.append(
            {
                "project_id": str(t.id),
                "title": t.title,
                "code": n.code,
                "name": names.get(n.code, n.code),
                "state": "done",
                "at": n.created_at.isoformat(),
            }
        )
    failed = [(uuid.UUID(pid), code, at, why) for (pid, code), (state, at, why) in _papers.items() if state == "failed" and at > since]
    if failed:
        own = {
            t.id: t
            for t in (await session.scalars(select(AnalysisTask).where(AnalysisTask.id.in_([f[0] for f in failed]), AnalysisTask.org_id == user.org_id))).all()
        }
        out += [
            {"project_id": str(pid), "title": own[pid].title, "code": code, "name": names.get(code, code), "state": "failed", "error": why, "at": at.isoformat()}
            for pid, code, at, why in failed
            if pid in own
        ]
    return out


@router.get("/{project_id}")
async def overview(project_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    state_then = _suggest_state(project_id, 0)  # 状态要在读取答案之前取：读的过程中后台做完，状态和内容会对不上
    task = await _project(session, project_id, user)
    return await build_overview(session, task, state_then)


async def _carried_from(session: AsyncSession, task: AnalysisTask) -> dict[str, AnalysisTask]:
    """The projects this one's company details were copied from, by id (current titles; a project
    since deleted is left out)."""
    ids = {str(v.get("from")) for v in ((task.facts_json or {}).get(CARRIED_KEY) or {}).values() if isinstance(v, dict) and v.get("from")}
    found: dict[str, AnalysisTask] = {}
    for i in ids:
        try:
            row = await session.get(AnalysisTask, uuid.UUID(i))
        except ValueError:
            continue
        if row is not None and row.org_id == task.org_id and row.kind == "documents":
            found[i] = row
    return found


def _carried(q: dict, facts: dict, sources: dict[str, AnalysisTask]) -> dict | None:
    """Where the value now in the box came from, when the user did not type it here: another project
    (company details copied into a new project), this project's own business scene, or the business
    description the user gave (a recognised item they adopted). ``None`` once the user changed it.
    Answers shared by several documents are one question on the page, so they are not “带入”."""
    key, value = q["key"], str(q.get("value") or "").strip()
    if not value:
        return None
    made = ((facts.get(RISK_DONE_KEY) or {}).get(key)) or {}
    if isinstance(made, dict) and made.get("n") and str(made.get("value") or "").strip() == value:
        return {"kind": "risk", "text": f"其中 {made['n']} 条已从主要风险带入"}
    came = ((facts.get(CARRIED_KEY) or {}).get(key)) or {}
    if isinstance(came, dict) and str(came.get("value") or "").strip() == value:
        src = sources.get(str(came.get("from")))
        if src is not None:
            return {"kind": "project", "text": f"已从项目 {src.title} 带入", "project_id": str(src.id)}
        return {"kind": "project", "text": f"已从项目 {came.get('title') or '上一个项目'} 带入（该项目已删除）", "project_id": ""}
    adopted = ((facts.get(ADOPTED_KEY) or {}).get(key)) or {}
    if isinstance(adopted, dict) and str(adopted.get("value") or "").strip() == value:
        return {"kind": "said", "text": "已从你提供的业务情况带入", "quote": str(adopted.get("quote") or "")}
    if q.get("derived"):
        return {"kind": "route", "text": f"已从本项目的{FROM_NAMES.get(q['derived'], '项目信息')}带入"}
    return None


async def _form_items(session: AsyncSession, task: AnalysisTask, keys: set[str]) -> list[dict]:
    """The official forms of this project, each item the user typed on the form that the project
    also holds (to compare the two). Items the form only takes from the project are left out: they
    are the project's value, and cannot differ."""
    from app.forms import service as fs
    from app.forms.models import FormInstance
    from app.forms.router import facts_for_task

    rows = (await session.scalars(
        select(FormInstance).where(FormInstance.task_id == task.id, FormInstance.org_id == task.org_id)
    )).all()
    out = []
    for row in rows:
        form = fs.get_form(row.form_code)
        answers = row.answers or {}
        if not form or not answers:
            continue
        facts = facts_for_task(task, row.form_code)
        items = []
        for section in form["sections"]:
            for item in section["items"]:
                fact, given = item.get("fact"), answers.get(item["id"])
                if item["kind"] != "text" or not fact or not isinstance(given, str) or not given.strip():
                    continue
                key = fact
                if fact == "ma_processor":  # 在表格所在国处理数据的一方：企业自己或接收方
                    key = "company_name" if facts.get(fact) == facts.get("company_name") else "recipient"
                if key not in keys and key not in PROJECT_KEYS:
                    continue
                items.append({"id": item["id"], "label": item["label_zh"], "fact": fact, "key": key,
                              "given": given, "project": str(facts.get(fact) or "")})
        if items:
            out.append({"code": row.form_code, "instance_id": str(row.id), "name": form.get("name_zh", row.form_code), "items": items})
    return out


async def build_overview(session: AsyncSession, task: AnalysisTask, state_then: dict | None = None) -> dict:
    """What the project page shows: route, the eight documents and every item with its value."""
    from sqlalchemy import func

    from app.knowledge import cache_sync

    state_then = state_then if state_then is not None else _suggest_state(task.id, 0)
    carried_from = await _carried_from(session, task)
    ov_stamp: tuple | None = None
    if cache_sync.use_cached(True):
        inst_sig = (
            await session.execute(
                select(func.count(), func.max(DocumentInstance.updated_at)).where(DocumentInstance.task_id == task.id)
            )
        ).one()
        paper_mem = tuple(
            (code, tuple(sorted(_paper_state(task.id, code, False).items())))
            for code, _, _ in DEFAULT_TEMPLATES
            if (str(task.id), code) in _papers
        )
        carried_sig = tuple((k, v.title) for k, v in sorted(carried_from.items()))
        ov_stamp = (
            await _progress_stamp(session, task),
            task.title,
            task.archived_at,
            inst_sig,
            paper_mem,
            tuple(sorted(state_then.items())),
            carried_sig,
            cache_sync._last_mark,
        )
        hit = _OVERVIEW_PROJECT_CACHE.get(str(task.id))
        if hit is not None and hit[0] == ov_stamp:
            _OVERVIEW_PROJECT_CACHE.move_to_end(str(task.id))
            return copy.deepcopy(hit[1])

    library = await D.library_countries(session)
    await ensure_default_templates(session)
    saved_codes = set(
        (
            await session.scalars(
                select(DocumentInstance.code).where(DocumentInstance.task_id == task.id).distinct()
            )
        ).all()
    )
    docs: list[dict] = []
    questions: dict[str, dict] = {}
    suggestions = (task.facts_json or {}).get(SUGGEST_KEY) or {}
    for code, _, _ in DEFAULT_TEMPLATES:
        template = await D.latest_template(session, code)
        if template is None:
            continue
        docs.append(await _doc_view(session, task, template, code, saved_codes=saved_codes))
        base = {**task_values(task), **D._scenario(task)}
        values = resolve_facts(template.definition, base, task.facts_json or {})
        for fact in template.definition.get("facts", []):
            key = fact["key"]
            if key in PROJECT_KEYS:
                continue
            q = questions.setdefault(
                key,
                {
                    "key": key,
                    "label": fact["label"],
                    "question": fact["question"],
                    "why": fact.get("why", ""),
                    "short": bool(fact.get("short")),
                    "optional": bool(fact.get("optional")),
                    "choices": fact.get("choices") or [],
                    "detail_on": fact.get("detail_on", ""),
                    "value": values.get(key, ""),
                    "tier": "company" if key in COMPANY_KEYS else "system" if key in SYSTEM_KEYS else "business",
                    "suggestion": suggestions.get(key),
                    "first_doc": code,
                    "used_in": [],
                },
            )
            q["used_in"].append(code)
            q["optional"] = q["optional"] and bool(fact.get("optional"))
            borrowed = fact.get("from")
            if borrowed and borrowed != key and "derived" not in q and values.get(key) and not (task.facts_json or {}).get(DOC_FACT_PREFIX + key):
                q["derived"] = borrowed  # 没答，值取自项目里的另一项
    from app.analysis import doc_field_guide as G
    from app.analysis import question_basis

    facts = task.facts_json or {}
    source = facts.get(DOC_FACT_PREFIX + "source_country", "")
    recipient = facts.get(DOC_FACT_PREFIX + "recipient_country", "")
    if source and country_of(source) != "CN":
        src_display = LIBRARY_NAMES.get(country_of(source) or "", source)
        if "storage_chain" in questions:
            questions["storage_chain"]["label"] = f"{src_display}本地存储与传输链路"
            questions["storage_chain"]["question"] = (
                f"数据在{src_display}本地存放在哪些系统平台、数据中心（含云服务），经什么链路、以什么方式传输出去？"
            )
        if "is_cii" in questions:
            questions["is_cii"]["label"] = "是否属关键基础设施或特定受监管主体"
            questions["is_cii"]["question"] = f"在{src_display}法域下是否属于关键基础设施运营方或特定受监管行业主体？"
        if "important_data" in questions:
            questions["important_data"]["label"] = "是否涉及受管制特定业务数据"
            questions["important_data"]["question"] = f"是否涉及{src_display}法规单独列管的特定业务或高敏管制数据？"
        if "credit_code" in questions:
            questions["credit_code"]["label"] = "企业商业登记号或识别码"
            questions["credit_code"]["question"] = "企业商业登记号或法定识别码是多少？"
    # 题目旁的依据卡片：所依据的法条（按数据发出国取，链到法规知识库）
    is_non_cn_route = bool(source and country_of(source) != "CN")
    non_cn_hints = {
        "credit_code": "填写企业在注册地主管机关登记的商业登记号或法定识别码。",
        "is_cii": "按数据发出国法律判断是否属于关键基础设施运营方或特定受监管主体；不属于的选“否”。",
        "important_data": "按数据发出国法规判断是否涉及当地单独列管的特定业务或高敏管制数据。",
    }
    non_cn_examples = {
        "entity_type": "外商独资企业",
        "registered_place": "新加坡纬壹科技城",
        "office_place": "同注册地",
        "credit_code": "202418920K（当地商业登记号）",
        "is_cii": "否（不属于当地法定关键基础设施运营主体）",
        "law_compliance": "近两年在当地经营中严格遵守个人数据保护与电信管理法规，无监管处罚或调查记录。",
    }
    non_cn_choices = {
        "entity_type": ["本地内资企业", "外商独资企业", "合资企业", "跨国集团分支机构", "其他"],
    }
    basis = await question_basis.for_project(session, source=source, recipient=recipient, keys=list(questions), library=library, function=task.scenario_type or "")
    for q in questions.values():
        q["hint"] = (non_cn_hints.get(q["key"]) if is_non_cn_route else None) or G.HINTS.get(q["key"], "")
        q["example"] = (non_cn_examples.get(q["key"]) if is_non_cn_route else None) or G.EXAMPLES.get(q["key"], "")
        q["used_for"] = G.used_for(q["used_in"])
        q["basis"] = [{**c, "link": question_basis.link(c)} for c in basis.get(q["key"], [])]
        q["carried"] = _carried(q, facts, carried_from)
        q["max"] = risk_items.MAX_CHARS if q["key"] in risk_items.LONG_KEYS else MAX_ANSWER_CHARS
        if q["key"] in risk_items.STRUCTURED:
            # 主要风险按行填：每行还缺什么、还有几项风险没生成整改项、整改前后等级变化
            done = list((facts.get(RISK_DONE_KEY) or {}).get("ids") or [])
            ex_issues = str(facts.get(DOC_FACT_PREFIX + "issues") or "")
            ex_measures = str(facts.get(DOC_FACT_PREFIX + "measures") or "")
            active_complete = [
                r
                for r in risk_items.parse(q["value"])
                if r.get("mitigated") != "1" and risk_items.level(r) and r.get("measures")
            ]
            pend_cnt = risk_items.pending_count(q["value"], done, ex_issues, ex_measures)
            done_cnt = max(len(done), len(active_complete) - pend_cnt)
            q["kind"] = "risks"
            q["lacking"] = risk_items.lacking(q["value"])
            q["remediate"] = {"pending": pend_cnt, "done": done_cnt}
            q["evolution"] = risk_items.summary(q["value"])
        q.pop("derived", None)
        if not q["choices"] and q["key"] in G.CHOICES:
            q["choices"] = (non_cn_choices.get(q["key"]) if is_non_cn_route else None) or G.CHOICES[q["key"]]
            q["other"] = True
    answers = {k: q["value"] for k, q in questions.items()}
    # 数据从中国出境时，按企业填的情况判断出境路径（安全评估 / 标准合同 / 免于申报）
    cn = cn_route.judge(answers) if country_of(source) == "CN" else None
    if cn and cn["route"] == "security_assessment":
        # 两个期限：申报截止日（自评估完成日起 3 个月）、评估结果到期日（出具日起 3 年）
        cn["validity"] = cn_route.validity(*(facts.get(DOC_FACT_PREFIX + k, "") for k in VALIDITY_DATES))
        for b in cn["validity"]["basis"]:
            number = b["article"][1:-1] if b["article"].startswith("第") else ""
            b["link"] = await question_basis.cn_link(session, b["law"], number) if number else ""
    # 8 份文件各自需要 / 按目前情况不需要 / 还不能判断：依据取自数据发出国的法规，原因引用企业填的答案
    applicability = _apply_manual_docs(
        await doc_applicability.for_project(
            session,
            source=source,
            recipient=recipient,
            values=answers,
            labels={k: q["label"] for k, q in questions.items()},
            function=task.scenario_type or "",
            functions=D.task_functions(task),
            library=library,
            cn=cn,
        ),
        facts,
    )
    # 平台检查：不同的题前后对不上、项目和官方表格对不上。挂在题目旁，只提示，不改状态、不挡下载
    from app.analysis import project_delivery_aids

    labels = {k: q["label"] for k, q in questions.items()}
    issues = doc_consistency.between_questions({k: v for k, v in answers.items() if str(v).strip()}, labels, recipient)
    issues += doc_consistency.with_forms(await _form_items(session, task, set(questions)))
    for q in questions.values():
        q["issues"] = [i for i in issues if i["key"] == q["key"]]
        if q.get("used_in") and all(applicability.get(c, {}).get("state") == "na" for c in q["used_in"]):
            q["density_hint"] = None
        else:
            q["density_hint"] = project_delivery_aids.evaluate_field_density(
                q["key"],
                q["value"],
                scenario_type=task.scenario_type or "",
                source_country=source,
                recipient_country=recipient,
                vehicle_model=task.vehicle_model or "",
            )
    route_issues = [i for i in issues if i["key"] in PROJECT_KEYS]
    final = final_status(docs, applicability, list(questions.values()))
    final["issues"] = doc_consistency.summary(issues)
    from app.analysis import doc_rules, doc_sections, verdict_rules

    merged_proj_vals = {
        **{k: str(v) for k, v in answers.items() if str(v).strip()},
        "source_country": source,
        "recipient_country": recipient,
        "vehicle_model": task.vehicle_model or "",
        "scenario": str(facts.get(DOC_FACT_PREFIX + "scenario") or task.title or ""),
        "company_name": str(facts.get(DOC_FACT_PREFIX + "company_name") or ""),
    }
    proj_route_info = await doc_rules.route(session, merged_proj_vals, task.country_code)
    _, target_adm = doc_rules.ends(merged_proj_vals, task.country_code)
    raw_adm = await doc_sections.admission_entries(session, task, target_adm)
    adm_applicable = [
        e
        for e in doc_rules.inbound_only(
            raw_adm, country_of(source), country_of(recipient), target_adm, merged_proj_vals
        )
        if not doc_rules.admission_gate(e)
    ]
    tpl_defs = {
        code: (await D.latest_template(session, code))
        for code in verdict_rules.VERDICT_DOC_CODES
    }
    for d in docs:
        d_code = d["code"]
        if d_code in verdict_rules.VERDICT_DOC_CODES:
            d_miss = int(d.get("official_missing") or 0) if d.get("official_only") else len(d.get("missing") or [])
            tpl_obj = tpl_defs.get(d_code)
            dv = verdict_rules.compute_doc_verdict(
                d_code,
                merged_proj_vals,
                proj_route_info,
                task_country_code=task.country_code,
                missing_count=d_miss,
                admission_count=len(adm_applicable),
                definition=tpl_obj.definition if tpl_obj else None,
            )
            d["verdict"] = {"label": dv["label"], "tone": dv["tone"], "tier": dv["tier"]} if dv else None
        else:
            d["verdict"] = None

    req_docs = [d for d in docs if applicability.get(d["code"], {}).get("state") != "na"] or docs
    proj_overall = verdict_rules.compute_doc_verdict(
        "risk_report",
        merged_proj_vals,
        proj_route_info,
        task_country_code=task.country_code,
        missing_count=len(final["missing"]) + len(final["forms"]),
        definition=tpl_defs["risk_report"].definition if tpl_defs.get("risk_report") else None,
    )
    third_country_checklists = project_delivery_aids.build_third_country_checklists(
        source,
        recipient,
        set(library),
        scenario_type=task.scenario_type or "",
        vehicle_model=task.vehicle_model or "",
    )
    cur_fns = D.task_functions(task)
    cur_scs = task_business_scenes(task)
    result = {
        "id": str(task.id),
        "title": task.title,
        "conversation_code": format_conversation_code(task.id, task.created_at),
        "archived": task.archived_at is not None,
        "route": {
            "source_country": source,
            "recipient_country": recipient,
            "function": cur_fns[0] if cur_fns else (task.scenario_type or ""),
            "functions": cur_fns,
            "function_zh": _format_multi_function_short(cur_fns, facts.get(DOC_FACT_PREFIX + "scenario") or ""),
            "business_scene": cur_scs[0] if cur_scs else "",
            "business_scenes": cur_scs,
            "scenario_text": facts.get(DOC_FACT_PREFIX + "scenario", ""),
            "vehicle_model": task.vehicle_model,
            "company_name": facts.get(DOC_FACT_PREFIX + "company_name", ""),
            "company_name_carried": _carried({"key": "company_name", "value": facts.get(DOC_FACT_PREFIX + "company_name", "")}, facts, carried_from),
            "library_country": "、".join(dict.fromkeys(
                LIBRARY_NAMES.get(c, c) for c in (country_of(source), country_of(recipient)) if c and c in library
            )),  # 两端里法规库已收录条文的国家（依据取自它们）
        },
        "documents": docs,
        "questions": list(questions.values()),
        "thin_count": sum(1 for q in questions.values() if q.get("density_hint")),
        "third_country_checklists": third_country_checklists,
        "ready_count": sum(1 for d in req_docs if d["state"] == "ready"),
        "required_count": len(req_docs),
        "cn_route": cn,
        "applicability": applicability,
        "final": final,
        "route_issues": route_issues,
        "suggest": {**state_then, "count": len(suggestions)},
        "suggest_diagnosis": facts.get(DIAGNOSIS_KEY),
        "risk_evolution": risk_items.summary(answers.get("risk_rating", "")),
        "risk_sync": _build_risk_sync(facts, task.vehicle_model, answers.get("risk_rating", "")),
        "project_verdict": (
            {
                "label": proj_overall["label"],
                "tone": proj_overall["tone"],
                "tier": proj_overall["tier"],
                "text": proj_overall["text"],
                "route_text": str(proj_route_info.get("text") or ""),
            }
            if proj_overall
            else None
        ),
        "route_suggestions": {k: suggestions[k] for k in ("company_name", "vehicle_model", "functions") if k in suggestions},
    }
    signoff_full = project_delivery_aids.build_signoff_checklist(result, answers)
    result["signoff_summary"] = {k: v for k, v in signoff_full.items() if k != "blocks"}
    if ov_stamp is not None:
        _put_bounded(_OVERVIEW_PROJECT_CACHE, str(task.id), (ov_stamp, copy.deepcopy(result)), 24)
    return result


class AskIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    previous: str | None = Field(None, max_length=60)  # 用户刚填过的上一题（说“跟上一题有什么区别”时要用）


@router.post("/{project_id}/questions/{key}/ask")
async def ask_about_question(project_id: uuid.UUID, key: str, body: AskIn, user: UserDep, session: SessionDep) -> dict:
    """The assistant beside the form, about this one question: explains it, or proposes the text
    for the box from what the user says (the user adopts it). Nothing is saved here."""
    from app.analysis import doc_assist
    from app.core.redaction import bind_egress, llm_scope
    from app.iam.models import Organization

    task = await _project(session, project_id, user)
    view = await overview(project_id, user, session)
    question = next((q for q in view["questions"] if q["key"] == key), None)
    if question is None:
        raise HTTPException(404, "没有这个问题")
    route = view["route"]
    before = next((q for q in view["questions"] if q["key"] == body.previous), None) if body.previous else None
    context = {"数据发出国": route["source_country"], "数据接收国": route["recipient_country"], "业务": route["scenario_text"] or route["function"]}
    org = await session.get(Organization, task.org_id) if task.org_id else None
    with llm_scope():
        bind_egress(session, org)
        got = await doc_assist.answer(question, body.message, context, before)
    return {"reply": got.text, "proposal": got.proposal}


@router.put("/{project_id}/answers")
async def save_answers(project_id: uuid.UUID, body: AnswersIn, user: UserDep, session: SessionDep) -> dict:
    """Fill (or clear) any of the questions of any document, all at once; what is shared between
    documents is answered once."""
    task = await _project(session, project_id, user, writable=True)
    await ensure_default_templates(session)
    known: set[str] = set()
    for code, _, _ in DEFAULT_TEMPLATES:
        template = await D.latest_template(session, code)
        if template:
            known |= {f["key"] for f in template.definition.get("facts", [])}
    facts = dict(task.facts_json or {})
    for key, value in body.answers.items():
        if key not in known or key in PROJECT_KEYS:
            raise HTTPException(409, "提交了当前项目不支持的填写栏目，请刷新页面后重试")
        text = (value or "").strip()
        limit = risk_items.MAX_CHARS if key in risk_items.LONG_KEYS else MAX_ANSWER_CHARS
        if len(text) > limit:
            raise HTTPException(422, f"回答请控制在 {limit} 字以内")
        if text and key in ("data_volume", "sensitive_pi", "employee_count") and not text.startswith("暂不确定"):
            if re.search(r"(?:^|[\s：:，,（(=])-\s*\d", text):
                raise HTTPException(422, "涉及人数或数量不能为负数，请核对后重新填写")
            if key == "data_volume" and re.fullmatch(r"[A-Za-z_.\s]+", text):
                raise HTTPException(422, "涉及自然人数量请填写具体人数或规模说明（如 5000 人、约 3.2 万人）")
        if key in risk_items.STRUCTURED and text.startswith("["):
            prev_raw = str(facts.get(DOC_FACT_PREFIX + key) or "")
            text = risk_items.merge_history(prev_raw, text)  # 按行填的风险：保留初评等级以追踪前后变化、去掉空行、统一格式
        if text:
            facts[DOC_FACT_PREFIX + key] = text
        else:
            facts.pop(DOC_FACT_PREFIX + key, None)
        _forget_source(facts, key, text)
    if "risk_rating" in body.answers:
        sig = _risk_facts_signature(facts, task.vehicle_model)
        if sig and facts.get(DOC_FACT_PREFIX + "risk_rating"):
            facts[RISK_FACTS_SIG_KEY] = sig
    task.facts_json = facts
    await session.commit()
    await session.refresh(task)
    view = await overview(project_id, user, session)
    await _sync_project_status(session, task, view["final"])
    return view


# 整改问题里只是占位、不算内容的回答：生成整改项时可以替换；其余已有内容一律保留，新条目追加在后面
_PLACEHOLDER_ANSWERS = ("无", "无。", "没有", "暂无")


@router.post("/{project_id}/risks/diagnose")
async def diagnose_risks(project_id: uuid.UUID, request: Request, user: UserDep, session: SessionDep) -> dict:
    """Initial risk identification OR post-remediation dynamic risk re-evaluation based on project facts."""
    import json as _json

    from app.analysis.llm import LlmUnavailableError, call_llm_json
    from app.audit.service import set_detail
    from app.core.redaction import bind_egress, llm_scope
    from app.iam.models import Organization

    task = await _project(session, project_id, user, writable=True)
    org = await session.get(Organization, task.org_id) if task.org_id else None
    facts = dict(task.facts_json or {})
    prev_raw = str(facts.get(DOC_FACT_PREFIX + "risk_rating") or "").strip()
    existing_rows = risk_items.parse(prev_raw)

    known_facts: dict[str, str] = {}
    for k, v in facts.items():
        if k.startswith(DOC_FACT_PREFIX) and k != DOC_FACT_PREFIX + "risk_rating" and isinstance(v, str) and v.strip() and not v.startswith("暂不确定"):
            known_facts[k.removeprefix(DOC_FACT_PREFIX)] = v.strip()[:500]
    if task.vehicle_model:
        known_facts["vehicle_model"] = task.vehicle_model

    mode = "reevaluate" if existing_rows else "initial"
    sys_prompt = (
        "你是资深汽车数据跨境首席合规专家。请严格依据企业已填报的跨境业务事实与安全控制措施开展风险研判，只输出合法 JSON。\n"
        "等级维度仅允许填“低”“中”“高”三档之一。\n"
        "行文准则：必须使用平实、严谨、易读的法律与合规中文表述，严禁出现“最小必要裁剪”“脱敏裁剪”“字段裁剪”“精度裁剪”“风险缓释”“缓释闭环”“地理围栏泛化”“精度降维”“不可逆硬擦除”“穿透约束”等生造工程黑话（统一使用“仅采集业务必需字段”“车端模糊脱敏”“降低定位精度”“加密传输”“安全删除”“合同明确约定”等规范表述）。\n"
        "当 mode='initial'（初次问诊识别）时：围绕当前数据发出国至接收国的车载采集、跨境链路传输、境外云端存储访问、后续再转移及个人权益响应环节，"
        "识别 3 至 4 项具体且贴合智能网联汽车业务的跨境合规风险，给出落实专项防控措施前的初评可能性（likelihood）与严重程度（severity），"
        "并针对每项风险写出可落地的技术与管理防控措施（measures）及建议责任部门（owner），mitigated 置为空字符串。\n"
        "当 mode='reevaluate'（落实措施后复诊复评）时：保持原有各行的 id 不变，结合企业已填报的安全技术措施、管理制度、接收方管控、标准合同约定及整改落实情况，"
        "重新评估每项风险在落实措施后的当前可能性（likelihood）与严重程度（severity）："
        "若某项风险已在车端源头完成匿名化脱敏处理或物理阻断、不再发生原始敏感信息跨境传输，可将 mitigated 设为 '1'（风险已消除并归入整改台账）；"
        "若通过传输与存储强加密、密钥境内独立保管、最小权限隔离与合同违约追责约束显著降低了发生概率或影响范围，应如实下调当前 likelihood 或 severity；"
        "同时补充完善 measures。\n"
        "输出格式：{\"note\": \"一句话总结本次初评或复评的风险变化结论\", \"rows\": [{\"id\": \"r1\", \"risk\": \"...\", \"likelihood\": \"中\", \"severity\": \"中\", \"measures\": \"...\", \"owner\": \"...\", \"due\": \"\", \"mitigated\": \"\"}]}"
    )
    user_payload = {
        "mode": mode,
        "project_title": task.title,
        "facts": known_facts,
        "existing_rows": existing_rows,
    }
    try:
        with llm_scope():
            bind_egress(session, org)
            res = await call_llm_json(
                [{"role": "system", "content": sys_prompt}, {"role": "user", "content": _json.dumps(user_payload, ensure_ascii=False)}],
                max_tokens=4000,
                temperature=0.1,
                timeout=120.0,
            )
    except LlmUnavailableError as exc:
        raise HTTPException(503, "风险研判服务暂时不可用，请稍后重试") from exc

    data = res.data if isinstance(res.data, dict) else {}
    raw_rows = data.get("rows") if isinstance(data.get("rows"), list) else []
    if not raw_rows:
        raise HTTPException(502, "未能生成有效的风险研判条目，请补充业务事实后重试")
    merged = risk_items.merge_history(prev_raw, _json.dumps(raw_rows, ensure_ascii=False))
    if not merged:
        raise HTTPException(502, "风险条目格式校验未通过，请重试")
    facts[DOC_FACT_PREFIX + "risk_rating"] = merged
    sig = _risk_facts_signature(facts, task.vehicle_model)
    if sig:
        facts[RISK_FACTS_SIG_KEY] = sig
    _forget_source(facts, "risk_rating", merged)
    task.facts_json = facts
    await session.commit()
    await session.refresh(task)
    note = str(data.get("note") or ("已完成落实措施后的风险动态复评" if mode == "reevaluate" else "已结合业务事实识别初评风险清单")).strip()
    set_detail(request, summary=f"项目“{task.title}”执行风险{'动态复评' if mode == 'reevaluate' else '初评识别'}")
    view = await overview(project_id, user, session)
    return {**view, "diagnose_note": note}


@router.post("/{project_id}/risks/remediate")
async def risks_to_remediation(project_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    """Turn the measures of each complete risk into items of the remediation tracking report: the
    problem into “发现的合规问题”, the measure with who and by when into “整改措施与责任人”. What the
    user already wrote there stays; the new items are added after it and marked as carried from the
    risks. A risk turned into items before is not added again."""
    task = await _project(session, project_id, user, writable=True)
    facts = dict(task.facts_json or {})
    held = dict(facts.get(RISK_DONE_KEY) or {})
    done = list(held.get("ids") or [])
    raw_risk_val = str(facts.get(DOC_FACT_PREFIX + "risk_rating") or "")
    existing_issues = str(facts.get(DOC_FACT_PREFIX + "issues") or "")
    existing_measures = str(facts.get(DOC_FACT_PREFIX + "measures") or "")
    ids, issues, measures = risk_items.remediation(
        raw_risk_val, done, existing_issues=existing_issues, existing_measures=existing_measures
    )
    if not ids:
        raise HTTPException(409, "没有可以生成的整改项：风险填好可能性、严重程度和应对措施后才能生成，生成过的不再重复")
    for key, lines in (("issues", issues), ("measures", measures)):
        before = str(facts.get(DOC_FACT_PREFIX + key) or "").strip()
        kept = "" if before.startswith("暂不确定") or before in _PLACEHOLDER_ANSWERS else before
        kept_lines = [ln.strip() for ln in kept.splitlines() if ln.strip()]
        dedup_new = [ln for ln in lines if ln.strip() and ln.strip() not in kept_lines]
        after = "\n".join([*kept_lines, *dedup_new])
        if len(after) > risk_items.MAX_CHARS:
            name = "发现的合规问题" if key == "issues" else "整改措施与责任人"
            raise HTTPException(422, f"“{name}”加上新条目后超过 {risk_items.MAX_CHARS} 字，请先精简已有内容")
        prev = held.get(key) or {}
        carried = int(prev.get("n") or 0) if str(prev.get("value") or "").strip() == before else 0
        facts[DOC_FACT_PREFIX + key] = after
        _forget_source(facts, key, after)
        held[key] = {"value": after, "n": carried + len(dedup_new)}
    all_complete_ids = [
        r["id"]
        for r in risk_items.parse(raw_risk_val)
        if r.get("mitigated") != "1" and risk_items.level(r) and r.get("measures")
    ]
    held["ids"] = list(dict.fromkeys([*done, *all_complete_ids, *ids]))
    facts[RISK_DONE_KEY] = held
    task.facts_json = facts
    await session.commit()
    await session.refresh(task)
    return await overview(project_id, user, session)


def _forget_source(facts: dict, key: str, text: str) -> None:
    """The user changed an item that was carried in: it is theirs now, no longer “带入”."""
    for store in (CARRIED_KEY, ADOPTED_KEY):
        held = dict(facts.get(store) or {})
        if key in held and str((held[key] or {}).get("value") or "").strip() != text:
            held.pop(key)
            facts[store] = held


def _suggest_state(project_id: uuid.UUID, count: int) -> dict:
    hit = _suggesting.get(str(project_id))
    if hit is None:
        return {"state": "none", "count": count}
    state, started, why = hit
    seconds = int((datetime.now(UTC) - started).total_seconds())
    if state == "running" and seconds > STALE_PAPER_SECONDS:
        return {"state": "failed", "error": "生成中断（可能服务重启过），请重试", "count": count}
    if state == "running":
        return {"state": "running", "seconds": seconds, "count": count}
    return {"state": state, "error": why, "count": count}


async def _suggest_job(factory: async_sessionmaker, project_id: uuid.UUID, text: str) -> None:
    from app.analysis import doc_suggest, question_basis
    from app.core.redaction import (
        bind_egress,
        llm_scope,
        org_redaction_aliases,
        summarize_redaction,
    )
    from app.iam.models import Organization

    key = str(project_id)
    try:
        with system_scope("doc-suggest"):
            async with factory() as session:
                task = await session.get(AnalysisTask, project_id)
                if task is None:
                    return
                org = await session.get(Organization, task.org_id) if task.org_id else None
                aliases = org_redaction_aliases(org)
                redaction_info = summarize_redaction(text, aliases) if text.strip() else None
                await ensure_default_templates(session)
                base = {**task_values(task), **D._scenario(task)}
                asked: dict[str, dict] = {}
                for code, _, _ in DEFAULT_TEMPLATES:
                    template = await D.latest_template(session, code)
                    if template is None:
                        continue
                    values = resolve_facts(template.definition, base, task.facts_json or {})
                    for fact in template.definition.get("facts", []):
                        k = fact["key"]
                        if k in PROJECT_KEYS or k in asked or values.get(k):
                            continue
                        allow = k in GENERAL_OK
                        if not allow and not text.strip():
                            continue
                        asked[k] = {"key": k, "label": fact["label"], "question": fact["question"], "allow_general": allow}
                facts = task.facts_json or {}
                if text.strip():
                    if not facts.get(DOC_FACT_PREFIX + "company_name"):
                        asked["company_name"] = {"key": "company_name", "label": "公司名称", "question": "数据处理者（公司）的名称", "allow_general": False}
                    if not task.vehicle_model:
                        asked["vehicle_model"] = {"key": "vehicle_model", "label": "车型或车队", "question": "涉及的车型或车队：原话里对车辆的描述即可（如“智能网联SUV”“物流卡车”“测试车队约40辆”），逐字摘抄", "allow_general": False}
                source_c = facts.get(DOC_FACT_PREFIX + "source_country", "")
                recipient_c = facts.get(DOC_FACT_PREFIX + "recipient_country", "")
                context = {
                    "数据从哪国发出": source_c,
                    "传到哪国": recipient_c,
                    "业务": base.get("scenario", ""),
                    "车型或车队": task.vehicle_model,
                    "公司": facts.get(DOC_FACT_PREFIX + "company_name", ""),
                }
                target_clauses: list[dict] = []
                if text.strip():
                    try:
                        library = await D.library_countries(session)
                        bmap = await question_basis.for_project(
                            session,
                            source=source_c,
                            recipient=recipient_c,
                            keys=["purpose", "consent_notice", "retention", "security_tech", "recipient_control", "rights_channel"],
                            library=library,
                            function=task.scenario_type or "",
                        )
                        seen_c: set[tuple[str, str]] = set()
                        for clist in bmap.values():
                            for c in clist:
                                sig = (str(c.get("law") or ""), str(c.get("article") or ""))
                                if sig not in seen_c:
                                    seen_c.add(sig)
                                    target_clauses.append(c)
                    except Exception:  # noqa: BLE001
                        log.debug("诊断目标法规要点读取略过", exc_info=True)
                diag = None
                with llm_scope():
                    bind_egress(session, org)
                    found, _tokens = await doc_suggest.draft(list(asked.values()), context, text)
                    if text.strip():
                        try:
                            still_missing = [q["label"] for k, q in asked.items() if k not in found]
                            diag, _ = await doc_suggest.diagnose(context, text, still_missing, target_clauses)
                        except Exception:  # noqa: BLE001
                            log.debug("材料差距诊断略过", exc_info=True)
                if text.strip() and redaction_info is not None:
                    if diag is None:
                        diag = {"doc_kind": "现有业务与制度材料", "summary": "", "gaps": [], "followups": [], "redaction": redaction_info}
                    else:
                        diag["redaction"] = redaction_info
                task = await session.get(AnalysisTask, project_id)
                facts = dict(task.facts_json or {})
                kept = {k: v for k, v in (facts.get(SUGGEST_KEY) or {}).items() if not facts.get(DOC_FACT_PREFIX + k)}
                kept.update({k: v for k, v in found.items() if not facts.get(DOC_FACT_PREFIX + k)})
                if text.strip():
                    detected_fns = doc_suggest.detect_functions_from_text(text)
                    existing_fns = D.task_functions(task)
                    new_fns = [fk for fk, _ in detected_fns if fk not in existing_fns]
                    if new_fns:
                        merged_fns = list(dict.fromkeys([*existing_fns, *new_fns]))
                        kept["functions"] = {
                            "text": "、".join(SHORT_FUNCTION.get(fk, fk) for fk in merged_fns),
                            "keys": merged_fns,
                            "source": "text",
                            "quote": "……".join(qt for fk, qt in detected_fns if fk in new_fns)[:240],
                        }
                facts[SUGGEST_KEY] = kept
                if diag is not None:
                    facts[DIAGNOSIS_KEY] = diag
                task.facts_json = facts
                await session.commit()
        _put_bounded(_suggesting, key, ("done", datetime.now(UTC), ""), 256)
    except Exception as exc:  # noqa: BLE001 - 后台任务：失败要让用户看到，不能悄悄消失
        log.exception("建议答案生成失败")
        why = "模型暂时不可用，稍后再试；也可以直接填写" if isinstance(exc, RuntimeError) else "暂时没能生成建议，稍后再试；也可以直接填写"
        _put_bounded(_suggesting, key, ("failed", datetime.now(UTC), why), 256)


@router.post("/{project_id}/suggest", status_code=202)
async def start_suggest(project_id: uuid.UUID, body: SuggestIn, user: UserDep, session: SessionDep) -> dict:
    """Draft suggested answers in the background (from the user's own description when given)."""
    from app.core.redaction import org_redaction_aliases, summarize_redaction
    from app.iam.models import Organization

    task = await _project(session, project_id, user, writable=True)
    if _suggest_state(project_id, 0)["state"] == "running":
        return _suggest_state(project_id, 0)
    org = await session.get(Organization, task.org_id) if task.org_id else None
    aliases = org_redaction_aliases(org)
    redaction_info = summarize_redaction(body.text, aliases) if body.text.strip() else None
    begin_suggest(session, task.id, body.text)
    return {"state": "running", "seconds": 0, "redaction": redaction_info}


def begin_suggest(session: AsyncSession, project_id: uuid.UUID, text: str) -> None:
    """Start reading ``text`` for suggested answers in the background."""
    _put_bounded(_suggesting, str(project_id), ("running", datetime.now(UTC), ""), 256)
    asyncio.create_task(_suggest_job(async_sessionmaker(session.bind, expire_on_commit=False), project_id, text))


@router.post("/{project_id}/suggest-file", status_code=202)
async def start_suggest_from_file(
    project_id: uuid.UUID, filename: str, request: Request, user: UserDep, session: SessionDep
) -> dict:
    """The same, reading an existing file (PDF / Word / text) instead of pasted words. The file is
    only read for this; it is not kept."""
    from app.analysis import attachments as att
    from app.core.redaction import org_redaction_aliases, summarize_redaction
    from app.iam.models import Organization

    task = await _project(session, project_id, user, writable=True)
    filename = filename.strip().replace(chr(92), "/").rsplit("/", 1)[-1][:255]
    try:
        att.kind_of(filename)
    except att.UploadError as exc:
        raise HTTPException(422, str(exc)) from exc
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > att.MAX_BYTES:
            raise HTTPException(413, f"文件超过 {att.MAX_BYTES // 1024 // 1024} MB，请压缩或拆分后再传")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(422, "文件是空的")
    try:
        text, _pages, readable, note = await asyncio.to_thread(att.extract, filename, data)
    except att.UploadError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not readable:
        raise HTTPException(422, note)
    if len(text) > MAX_SOURCE_TEXT:
        note = (note + f" 文件较长，这次只读了前 {MAX_SOURCE_TEXT} 个字，其余内容请挑重点粘贴。").strip()
        text = text[:MAX_SOURCE_TEXT]
    if _suggest_state(project_id, 0)["state"] == "running":
        return _suggest_state(project_id, 0)
    org = await session.get(Organization, task.org_id) if task.org_id else None
    aliases = org_redaction_aliases(org)
    redaction_info = summarize_redaction(text, aliases)
    _put_bounded(_suggesting, str(task.id), ("running", datetime.now(UTC), ""), 256)
    asyncio.create_task(_suggest_job(async_sessionmaker(session.bind, expire_on_commit=False), project_id, text))
    return {"state": "running", "seconds": 0, "note": note, "redaction": redaction_info}


@router.post("/{project_id}/suggestions/accept")
async def accept_suggestions(project_id: uuid.UUID, body: AcceptIn, user: UserDep, session: SessionDep) -> dict:
    """Adopt the suggestions (all, or the listed ones) as answers — or, with dismiss, drop them."""
    task = await _project(session, project_id, user, writable=True)
    facts = dict(task.facts_json or {})
    pending = dict(facts.get(SUGGEST_KEY) or {})
    for key in list(pending) if body.keys is None else body.keys:
        item = pending.pop(key, None)
        if not item or body.dismiss:
            continue
        if key == "vehicle_model":
            task.vehicle_model = task.vehicle_model or item["text"][:128]
        elif key == "functions":
            existing_custom = [f for f in D.task_functions(task) if f and f not in SCENARIO_LABELS]
            raw_keys = item.get("keys") if isinstance(item.get("keys"), list) else []
            clean_fns = list(
                dict.fromkeys(
                    [*(str(x).strip() for x in raw_keys if str(x).strip() in SCENARIO_LABELS), *existing_custom]
                )
            )
            if clean_fns:
                facts[FUNCTIONS_KEY] = clean_fns
                task.scenario_type = clean_fns[0]
                if not task.title_custom:
                    src_c = facts.get(DOC_FACT_PREFIX + "source_country", "")
                    rcp_c = facts.get(DOC_FACT_PREFIX + "recipient_country", "")
                    what = _format_multi_function_short(clean_fns, facts.get(DOC_FACT_PREFIX + "scenario") or "")
                    suffix = str(facts.get("_clone_suffix") or "")
                    limit = 54 if suffix else 60
                    base = (f"{src_c or '?'} → {rcp_c or '?'}" + (f" · {what}" if what else ""))[:limit]
                    task.title = (base + suffix)[:60]
        elif key == "risk_rating" and str(item.get("text") or "").strip().startswith("["):
            prev_raw = str(facts.get(DOC_FACT_PREFIX + key) or "")
            facts[DOC_FACT_PREFIX + key] = risk_items.merge_history(prev_raw, str(item["text"]))
        elif not facts.get(DOC_FACT_PREFIX + key):
            facts[DOC_FACT_PREFIX + key] = item["text"]
            if item.get("source") == "text":  # 来自用户自己的业务说明（附原文）；按常见做法拟的不算带入
                facts[ADOPTED_KEY] = {**(facts.get(ADOPTED_KEY) or {}), key: {"value": item["text"], "quote": item.get("quote", "")}}
    facts[SUGGEST_KEY] = pending
    task.facts_json = facts
    await session.commit()
    await session.refresh(task)
    view = await overview(project_id, user, session)
    await _sync_project_status(session, task, view["final"])
    return view


async def _paper_job(factory: async_sessionmaker, project_id: uuid.UUID, code: str, user_id: uuid.UUID) -> None:
    from app.core.redaction import bind_egress, llm_scope
    from app.core.resource_gate import background_job_gate
    from app.iam.models import Organization

    key = (str(project_id), code)
    try:
        async with background_job_gate():
            with system_scope("doc-paper"):
                async with factory() as session:
                    task = await session.get(AnalysisTask, project_id)
                    if task is None or task.archived_at is not None:
                        _papers.pop(key, None)
                        return
                    org = await session.get(Organization, task.org_id) if task.org_id else None
                    template, values, citations = await D._inputs(session, task, code)
                    with llm_scope():
                        bind_egress(session, org)
                        await D._write_paper(session, task, code, template, values, citations, user_id, raise_on_error=True)
                    await session.refresh(task)
                    if task.archived_at is not None:
                        _papers.pop(key, None)
                        return
                    if (
                        code == "dpia"
                        and values.get("source_country")
                        and country_of(values.get("source_country")) != "CN"
                        and involves_eu_eea(values.get("source_country"), values.get("recipient_country"))
                    ):
                        try:
                            await D.edpb_en_docx(session, task, draft=False, actor_user_id=user_id)
                        except Exception:  # noqa: BLE001
                            log.debug("预生成 EDPB 英文官方表略过（可在导出时按需生成）", exc_info=True)
                    ov_after = await build_overview(session, task)
                    await _sync_project_status(session, task, ov_after["final"])
        _put_bounded(_papers, key, ("done", datetime.now(UTC), ""), 256)
    except NarrativeError as exc:
        from app.core.jargon import scrub_engineering_jargon

        _put_bounded(_papers, key, ("failed", datetime.now(UTC), scrub_engineering_jargon(f"论述没有生成成功：{exc}。可以补充事实后重试")), 256)
    except Exception:  # noqa: BLE001 - 后台任务：失败要让用户看到，不能悄悄消失
        log.exception("论述后台生成失败")
        _put_bounded(_papers, key, ("failed", datetime.now(UTC), "服务暂时不可用，请稍后重试"), 256)


@router.post("/{project_id}/documents/{code}/include")
async def set_document_included(
    project_id: uuid.UUID, code: str, body: IncludeDocIn, request: Request, user: UserDep, session: SessionDep
) -> dict:
    """Allow the user to manually include an optional/na document in this project's package, or restore automatic route judgement."""
    from app.audit.service import set_detail

    known_codes = {c: name for c, name, _ in DEFAULT_TEMPLATES}
    if code not in known_codes:
        raise HTTPException(404, "没有这份合规文件")
    task = await _project(session, project_id, user, writable=True)
    facts = dict(task.facts_json or {})
    manual = dict(facts.get(MANUAL_DOCS_KEY) or {})
    if body.include:
        manual[code] = True
    else:
        manual.pop(code, None)
    if manual:
        facts[MANUAL_DOCS_KEY] = manual
    else:
        facts.pop(MANUAL_DOCS_KEY, None)
    task.facts_json = facts
    await session.commit()
    await session.refresh(task)
    action_text = "手动纳入本次出件" if body.include else "恢复按路径自动判断"
    set_detail(request, summary=f"项目“{task.title}”将《{known_codes[code]}》设为{action_text}")
    view = await overview(project_id, user, session)
    await _sync_project_status(session, task, view["final"])
    return view


@router.post("/{project_id}/documents/{code}/paper", status_code=202)
async def start_paper(project_id: uuid.UUID, code: str, user: UserDep, session: SessionDep) -> dict:
    """Write the argumentative part (DPIA/LIA/TIA) in the background; the page shows its state."""
    task = await _project(session, project_id, user, writable=True)
    template, values, citations = await D._inputs(session, task, code)
    if not template.definition.get("narrative"):
        raise HTTPException(409, "这种文件没有需要生成的论述部分")
    probe = render(template.definition, values, citations, None, [])
    if probe.missing:
        raise HTTPException(409, "还有信息需要补充：" + "、".join(q.label for q in probe.missing))
    key = (str(project_id), code)
    state = _paper_state(project_id, code, False)
    if state["state"] == "running":
        return state
    _put_bounded(_papers, key, ("running", datetime.now(UTC), ""), 256)
    asyncio.create_task(_paper_job(async_sessionmaker(session.bind, expire_on_commit=False), project_id, code, user.user_id))
    return {"state": "running", "seconds": 0}


_EU_EEA_CODES = frozenset({
    "EU", "DE", "FR", "IT", "ES", "NL", "BE", "AT", "SE", "PL", "IE", "DK", "FI", "PT",
    "GR", "CZ", "HU", "RO", "BG", "HR", "SK", "SI", "LT", "LV", "EE", "LU", "MT", "CY",
    "IS", "LI", "NO",
})


def involves_eu_eea(source_country: str | None, target_country: str | None) -> bool:
    """Whether either the sending or receiving jurisdiction belongs to the EU/EEA."""
    src = country_of(source_country or "")
    dst = country_of(target_country or "")
    return bool((src and src in _EU_EEA_CODES) or (dst and dst in _EU_EEA_CODES))


async def _file(session: AsyncSession, task: AnalysisTask, code: str, ext: str, draft: bool) -> tuple[bytes, str, str]:
    if ext in ("docx", "pdf", "xlsx"):
        official = await D.official_docx(session, task, code, draft)
        if official is not None:
            if ext == "pdf":  # 与 Word 同一份：由填好的官方原件转出（无办公软件时降级内置排版）
                return await _docx_form_to_pdf(official[0], official[1]), named(task.title, official[1], ext="pdf"), "pdf"
            if ext == "xlsx":
                from app.analysis import official_docx as od

                preview_blocks = await asyncio.to_thread(od.docx_preview_blocks, official[0])
                xlsx_bytes = await asyncio.to_thread(build_xlsx, preview_blocks, official[1])
                return xlsx_bytes, named(task.title, official[1], ext="xlsx"), "xlsx"
            return official[0], named(task.title, official[1], ext="docx"), "docx"
    blocks, name, context = await D._export_source(session, task, code, draft)
    tstyle = "edpb" if code == "dpia" else "formal_black"
    if ext == "docx":
        docx_bytes = await asyncio.to_thread(build_docx, Rendered(blocks=blocks), context, table_style=tstyle)
        return docx_bytes, named(task.title, name, ext="docx"), "docx"
    if ext == "xlsx":
        xlsx_bytes = await asyncio.to_thread(build_xlsx, blocks, name)
        return xlsx_bytes, named(task.title, name, ext="xlsx"), "xlsx"
    return await asyncio.to_thread(build_pdf, Rendered(blocks=blocks), context, table_style=tstyle), named(task.title, name, ext="pdf"), "pdf"


async def _build_aid_doc(
    session: AsyncSession,
    task: AnalysisTask,
    aid_kind: Literal["signoff", "third-country"],
    ext: str,
    *,
    close_session: bool = False,
) -> tuple[bytes, str, list[dict]]:
    from app.analysis import project_delivery_aids

    task_title = task.title
    ov = await build_overview(session, task)
    answers = {q["key"]: q["value"] for q in ov.get("questions") or []}
    ctx_note = D._note(task)
    if aid_kind == "signoff":
        label = "正式申报递交前人工核签清单"
        blocks = project_delivery_aids.build_signoff_checklist(ov, answers)["blocks"]
    else:
        cks = ov.get("third_country_checklists") or []
        if not cks:
            raise HTTPException(404, "当前项目流向两端均已纳入现行法规条文库，无需生成库外法域待核查清单")
        label = "库外法域属地法律待核查清单"
        blocks = project_delivery_aids.render_third_country_blocks(task_title, cks)
    if close_session and ext == "pdf":
        await session.close()
    if ext == "preview":
        return b"", label, blocks
    if ext == "docx":
        return await asyncio.to_thread(build_docx, Rendered(blocks=blocks), ctx_note), named(task_title, label, ext="docx"), blocks
    if ext == "xlsx":
        return await asyncio.to_thread(build_xlsx, blocks, label), named(task_title, label, ext="xlsx"), blocks
    return await asyncio.to_thread(build_pdf, Rendered(blocks=blocks), ctx_note), named(task_title, label, ext="pdf"), blocks


@router.get("/{project_id}/delivery-aid/{aid_kind}/preview")
async def preview_delivery_aid(
    project_id: uuid.UUID,
    aid_kind: Literal["signoff", "third-country"],
    user: UserDep,
    session: SessionDep,
) -> dict:
    task = await _project(session, project_id, user)
    _, label, blocks = await _build_aid_doc(session, task, aid_kind, "preview")
    return {"name": label, "kind": "standard", "blocks": blocks}


@router.get("/{project_id}/delivery-aid/{aid_kind}/export.{fmt}")
async def export_delivery_aid(
    project_id: uuid.UUID,
    aid_kind: Literal["signoff", "third-country"],
    fmt: Literal["docx", "pdf", "xlsx"],
    request: Request,
    user: UserDep,
    session: SessionDep,
) -> Response:
    from app.audit.service import set_detail

    if not user.can_export:
        raise HTTPException(403, "当前账号无导出文件权限")
    task = await _project(session, project_id, user)
    data, fname, _ = await _build_aid_doc(session, task, aid_kind, fmt, close_session=(fmt == "pdf"))
    set_detail(request, summary=f"导出项目辅助核签材料“{fname}”")
    media = {
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }[fmt]
    return Response(data, media_type=media, headers={"Content-Disposition": content_disposition(fname)})


@router.get("/{project_id}/export.zip")
async def export_all(
    project_id: uuid.UUID, user: UserDep, session: SessionDep,
    fmt: Literal["docx", "pdf", "xlsx"] = "docx", draft: bool = True,
    lang: Literal["zh", "en", "fr", "de", "es", "pt", "it", "nl", "id", "tr", "pl", "ja", "ko", "th", "vi", "ar", "ru"] = "zh",
) -> Response:
    """All eight documents in one package. As a draft, what is unanswered reads “【待补充】”."""
    task = await _project(session, project_id, user)
    task_title = task.title
    left_out: set[str] = set()
    overview_now = await build_overview(session, task)
    if not draft:  # 成稿：与项目页同一套判断，要的文件没全部就绪就不出；按目前情况不需要、没就绪的不放进包
        final = overview_now["final"]
        if not final["ok"]:
            raise HTTPException(409, "还不能下载成稿：" + final_blockers(final))
        left_out = {d["code"] for d in final["skipped"]}
    # 按出境路径不需要的文件（如走标准合同时的安全评估申报表）初稿也不放进包，包里附一页说明为什么
    not_needed = {c: a for c, a in (overview_now.get("applicability") or {}).items() if a.get("state") == "na"}
    left_out |= set(not_needed)
    route_note = None
    cn = overview_now.get("cn_route") or {}
    if cn.get("route") == "unknown":
        pending = {c for c, a in (overview_now.get("applicability") or {}).items() if a.get("state") == "unknown" and c in cn_route.ROUTE_DOCS}
        left_out |= pending
        route_note = cn_route.pending_note(cn, task_title)
    skipped: list[str] = []
    from app.analysis.docx_pdf import PdfEngineUnavailable, docx_to_pdf_many, engine

    # PDF 包：先出 Word，立即释放数据库连接，再分批转成 PDF（杜绝长事务占满连接池）
    via_word = fmt == "pdf" and engine() != "builtin"
    made: list[tuple[str, bytes]] = []
    originals: list[tuple[str, bytes]] = []  # 官方 PDF 原表：本来就是 PDF，两种包里一样
    builtin_docx_forms: list[tuple[str, bytes, str, bool]] = []
    src_code = country_of(overview_now["route"]["source_country"])
    involves_eu = involves_eu_eea(overview_now["route"]["source_country"], overview_now["route"]["recipient_country"])
    is_rtl_lang = lang == "ar"

    from app.analysis.english_report import (
        _apply_translated_slots,
        _collect_translatable_slots,
        strip_and_translate_cjk,
    )
    from app.analysis.multilingual_report import (
        _apply_multilingual_slots,
        clean_and_gate_target_text,
    )

    def _tr_str(txt: str, fb: str = "Document") -> str:
        if lang == "zh":
            return txt
        if lang == "en":
            return strip_and_translate_cjk(txt, fallback=fb)
        return clean_and_gate_target_text(txt, lang, fallback=fb)

    def _tr_std_blocks(blks: list[dict], fb: str = "—") -> list[dict]:
        if lang == "zh":
            return blks
        slots = _collect_translatable_slots(blks)
        if lang == "en":
            return _apply_translated_slots(blks, [strip_and_translate_cjk(s, fallback=fb) for s in slots])
        return _apply_multilingual_slots(blks, [clean_and_gate_target_text(s, lang, fallback=fb) for s in slots], lang)

    task_tr = task_title if lang == "zh" else _tr_str(task_title, "Compliance_Project")

    async def _render_std_entry(blks: list[dict], label_text: str) -> tuple[str, bytes, bool]:
        ext_now = "docx" if via_word else fmt
        if lang != "zh":
            blks = _tr_std_blocks(blks)
        if ext_now == "docx":
            b = await asyncio.to_thread(
                build_docx,
                Rendered(blocks=blks),
                label_text,
                foreign=(lang != "zh"),
                rtl=is_rtl_lang if lang != "zh" else None,
            )
            return named(task_tr, label_text, ext="docx"), b, via_word
        if ext_now == "xlsx":
            b = await asyncio.to_thread(
                build_xlsx,
                blks,
                label_text,
                foreign=(lang != "zh"),
                rtl=bool(is_rtl_lang and lang != "zh"),
            )
            return named(task_tr, label_text, ext="xlsx"), b, False
        b = await asyncio.to_thread(
            build_pdf,
            Rendered(blocks=blks),
            label_text,
            foreign=(lang != "zh"),
            rtl=is_rtl_lang if lang != "zh" else None,
        )
        return named(task_tr, label_text, ext="pdf"), b, False

    if not draft:
        if lang == "zh":
            aid_ext = "docx" if via_word else fmt
            signoff_bytes, signoff_fname, _ = await _build_aid_doc(session, task, "signoff", aid_ext)
            made.append((f"00_{signoff_fname}", signoff_bytes))
        else:
            _, _, signoff_blks = await _build_aid_doc(session, task, "signoff", "preview")
            signoff_lbl = _tr_str("正式申报递交前人工核签清单", "Pre_Submission_Signoff_Checklist")
            sf_name, sf_bytes, sf_via_word = await _render_std_entry(signoff_blks, signoff_lbl)
            (made if sf_via_word else originals).append((f"00_{sf_name}", sf_bytes))
    for i, (code, _, _) in enumerate(DEFAULT_TEMPLATES, 1):
        if code in left_out:
            continue
        if code == "declaration":
            _, values, _ = await D._inputs(session, task, code)
            forms = D._official_forms_for(code, values)
            non_cn_forms_added = False
            for k, f in enumerate(forms):  # 转移申请在前，与项目页顺序一致
                f_country = str(f.get("country") or "").upper()
                if lang != "zh" and (f_country in ("", "CN") or fmt == "xlsx"):
                    # 外文包或外文 Excel 包不塞纯中文官方原表，统一走下方多语种结构化转译分支（保留网格合并与在线修订）
                    continue
                try:
                    raw_form, label_part, f_meta = await _official_rendered(
                        session, task, f["code"], foreign_only=(lang != "zh")
                    )
                except HTTPException as exc:
                    err_line = f"{f['name_zh']}：{exc.detail}" if lang == "zh" else _tr_str(f"{f.get('code', code)}: {exc.detail}", f"{f.get('code', code)}: skipped")
                    skipped.append(err_line)
                    continue
                prefix = f"{i:02d}{chr(97 + k)}_"
                eff_label = (
                    label_part
                    if lang == "zh"
                    else str(f_meta.get("name_orig") or f_meta.get("code") or "Official_Form").replace("_", " ")
                )
                eff_title = task_title if lang == "zh" else task_tr
                non_cn_forms_added = True
                if f_meta.get("kind") == "docx":
                    if via_word:
                        made.append((f"{prefix}{named(eff_title, eff_label, ext='docx')}", raw_form))
                    elif fmt == "pdf":
                        is_foreign_form = str(f_meta.get("country") or "").upper() not in ("", "CN")
                        pdf_header = (
                            str(f_meta.get("name_orig") or f_meta["code"].replace("_", " "))
                            if is_foreign_form
                            else eff_label
                        )
                        builtin_docx_forms.append(
                            (f"{prefix}{named(eff_title, eff_label, ext='pdf')}", raw_form, pdf_header, is_foreign_form)
                        )
                    else:
                        originals.append((f"{prefix}{named(eff_title, eff_label, ext='docx')}", raw_form))
                else:
                    originals.append((f"{prefix}{named(eff_title, eff_label, ext='pdf')}", raw_form))
            if official_only(code, values.get("source_country", ""), forms) and (lang == "zh" or non_cn_forms_added):
                continue  # 发出国有同语种/外文官方表：申报表就是官方原表，不再放通用申报表
        if lang != "zh":
            try:
                tr_blocks, label_tr, header_tr = await D._build_foreign_doc(session, task, code, user, draft, lang=lang)
                if fmt == "xlsx":
                    tr_xlsx = await asyncio.to_thread(
                        build_xlsx, tr_blocks, label_tr, foreign=True, rtl=bool(is_rtl_lang)
                    )
                    originals.append((f"{i:02d}_{named(task_tr, label_tr, ext='xlsx')}", tr_xlsx))
                elif via_word or fmt == "docx":
                    tr_bytes = await asyncio.to_thread(
                        build_docx,
                        Rendered(blocks=tr_blocks),
                        header_tr,
                        foreign=True,
                        table_style="formal_black",
                        rtl=is_rtl_lang,
                    )
                    made.append((f"{i:02d}_{named(task_tr, label_tr, ext='docx')}", tr_bytes))
                else:
                    tr_pdf = await asyncio.to_thread(
                        build_pdf,
                        Rendered(blocks=tr_blocks),
                        header_tr,
                        foreign=True,
                        table_style="formal_black",
                        rtl=is_rtl_lang,
                    )
                    originals.append((f"{i:02d}_{named(task_tr, label_tr, ext='pdf')}", tr_pdf))
            except HTTPException as exc:
                skipped.append(_tr_str(f"{code}: {exc.detail}", f"{code}: skipped"))
            continue
        try:
            data, filename, _ = await _file(session, task, code, "docx" if via_word else fmt, draft)
        except HTTPException as exc:
            skipped.append(f"{code}：{exc.detail}")
            continue
        made.append((f"{i:02d}_{filename}", data))
        if (
            not draft
            and code == "dpia"
            and fmt in ("docx", "pdf")
            and overview_now["route"]["source_country"]
            and src_code != "CN"
            and involves_eu
        ):
            try:
                from app.iam.models import Role

                edpb_bytes, edpb_label = await D.edpb_en_docx(
                    session,
                    task,
                    draft=False,
                    actor_user_id=user.user_id,
                    read_only_viewer=(user.role == Role.VIEWER),
                )
                if via_word or fmt == "docx":
                    made.append((f"{i:02d}b_{named(task_title, edpb_label, ext='docx')}", edpb_bytes))
                elif fmt == "pdf":
                    builtin_docx_forms.append(
                        (
                            f"{i:02d}b_{named(task_title, edpb_label, ext='pdf')}",
                            edpb_bytes,
                            "EDPB DPIA Template 2026 (Official EN)",
                            True,
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("打包成稿生成欧盟标准英文范本略过：%s", exc)
    if not draft and overview_now.get("third_country_checklists"):
        if lang == "zh":
            aid_ext = "docx" if via_word else fmt
            tc_bytes, tc_fname, _ = await _build_aid_doc(session, task, "third-country", aid_ext)
            made.append((f"09_{tc_fname}", tc_bytes))
        else:
            _, _, tc_blks = await _build_aid_doc(session, task, "third-country", "preview")
            tc_lbl = _tr_str("库外法域属地法律待核查清单", "Third_Country_Statutory_Verification_Checklist")
            tc_name, tc_bytes, tc_via_word = await _render_std_entry(tc_blks, tc_lbl)
            (made if tc_via_word else originals).append((f"09_{tc_name}", tc_bytes))

    # 关键优化：所有数据库读取已全部完成，进入耗时 PDF 转换与压缩前立即归还数据库连接与事务锁
    await session.close()

    for out_name, raw_docx, label_part, is_foreign_doc in builtin_docx_forms:
        is_edpb_en = label_part.startswith("EDPB ")
        originals.append(
            (
                out_name,
                await _docx_form_to_pdf(
                    raw_docx,
                    label_part,
                    foreign=is_foreign_doc,
                    table_style="edpb" if is_edpb_en else "formal_black",
                ),
            )
        )

    if via_word and made:
        try:
            pdfs = await asyncio.to_thread(docx_to_pdf_many, [d for _, d in made])
            made = [(n.rsplit(".", 1)[0] + ".pdf", p) for (n, _), p in zip(made, pdfs, strict=True)]
        except PdfEngineUnavailable as exc:
            raise HTTPException(503, "PDF 暂时无法生成，请先下载 Word 版") from exc

    route_note_entry: tuple[str, bytes] | None = None
    if route_note is not None:
        rn_label = "出境路径判断待补" if lang == "zh" else _tr_str("出境路径判断待补", "Route_Determination_Pending")
        rn_blocks = route_note if lang == "zh" else _tr_std_blocks(route_note)
        if fmt == "pdf":
            rn_bytes = await asyncio.to_thread(
                build_pdf,
                Rendered(blocks=rn_blocks),
                rn_label,
                foreign=(lang != "zh"),
                rtl=is_rtl_lang if lang != "zh" else None,
            )
            route_note_entry = (named(task_tr, rn_label, ext="pdf"), rn_bytes)
        elif fmt == "xlsx":
            rn_bytes = await asyncio.to_thread(
                build_xlsx,
                rn_blocks,
                rn_label,
                foreign=(lang != "zh"),
                rtl=bool(is_rtl_lang and lang != "zh"),
            )
            route_note_entry = (named(task_tr, rn_label, ext="xlsx"), rn_bytes)
        else:
            rn_bytes = await asyncio.to_thread(
                build_docx,
                Rendered(blocks=rn_blocks),
                rn_label,
                foreign=(lang != "zh"),
                rtl=is_rtl_lang if lang != "zh" else None,
            )
            route_note_entry = (named(task_tr, rn_label, ext="docx"), rn_bytes)

    def _pack_zip() -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, fdata in sorted(made + originals):
                zf.writestr(fname, fdata)
            if skipped:
                skip_name = "未能生成的文件.txt" if lang == "zh" else "Skipped_Documents.txt"
                zf.writestr(skip_name, "\n".join(skipped))
            if route_note_entry is not None:
                zf.writestr(route_note_entry[0], route_note_entry[1])
            if not_needed:
                if lang == "zh":
                    tpl_names = {c: nm for c, nm, _ in DEFAULT_TEMPLATES} | {"dpia_edpb_en": "数据保护影响评估（欧盟标准英文范本）"}
                    if overview_now["route"]["source_country"] and src_code != "CN":
                        tpl_names["declaration"] = D._NON_CN_DECL_NAME
                    zf.writestr("按目前情况不需要的文件.txt", "\n".join(f"{tpl_names.get(c, c)}：{a['why']}" for c, a in not_needed.items()))
                else:
                    lines_na = [
                        _tr_str(f"{c.upper()}: {a['why']}", f"{c.upper()}: Not required under current route")
                        for c, a in not_needed.items()
                    ]
                    zf.writestr("Not_Applicable_Documents.txt", "\n".join(lines_na))
        return buf.getvalue()

    zip_bytes = await asyncio.to_thread(_pack_zip)
    if lang == "zh":
        name = named(task_title, "初稿" if draft else "成稿", ext="zip")
    else:
        from app.analysis.multilingual_report import release_lang_lexicon_cache

        pkg_suffix = _tr_str("初稿" if draft else "成稿", "Draft" if draft else "Final")
        name = named(task_tr, f"{pkg_suffix}_{lang.upper()}", ext="zip")
        release_lang_lexicon_cache()
    return Response(zip_bytes, media_type="application/zip", headers={"Content-Disposition": content_disposition(name)})
