# ruff: noqa: E501
from __future__ import annotations

import copy
import re
import uuid
from datetime import UTC, datetime
from time import perf_counter
from types import SimpleNamespace
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analysis.benchmarks import run_retrieval_benchmarks
from app.analysis.chat import PLACEHOLDER_COUNTRY, post_user_message, start_chat
from app.analysis.chat_advisor import VIEWER_PRIVATE_KEY, viewer_owner
from app.analysis.consulting import upgrade_legacy
from app.analysis.events import append_event, stream_events, validate_reader
from app.analysis.export_cache import cached_pdf
from app.analysis.models import (
    AgentWorkflowConfig,
    AnalysisEvent,
    AnalysisMessage,
    AnalysisStepTrace,
    AnalysisTask,
    ReportVersion,
)
from app.analysis.pipeline import launch_pipeline
from app.analysis.public_view import PHASES, PublicTaskDetail, PublicTaskSummary
from app.analysis.report_exporter import (
    build_report_docx,
    build_report_pdf,
    build_report_zip,
    content_disposition,
    export_filename,
    exportable_dimensions,
)
from app.analysis.rich_text import same_text
from app.analysis.schemas import (
    AgentWorkflowConfigOut,
    AnalysisFindingOut,
    AnalysisStepTraceOut,
    AnalysisTaskCreateIn,
    AnalysisTaskDetailOut,
    AnalysisTaskSummaryOut,
    BenchmarkSummaryOut,
    ChatMessageIn,
    ChatMessageOut,
    DimensionDiffItemOut,
    FollowUpQuestionOut,
    GuidedStartIn,
    ReportDiffOut,
    ReportDimensionOut,
    ReportEditIn,
    ReportVersionOut,
    StaleImpactAlertOut,
    ToolCallTraceOut,
    UpdateFactsIn,
    WorkflowDslImportIn,
    WorkflowEdgeOut,
    WorkflowNodeOut,
    WorkflowVersionSummaryOut,
    compute_active_node,
    format_conversation_code,
)
from app.analysis.workflow_engine import (
    BUSINESS_SCENES,
    SCENARIO_LABELS,
    WORKFLOW_KEY,
    active_overseas_countries,
    country_name_zh,
    ensure_workflow_config,
    evaluate_fact_sufficiency,
    export_workflow_dsl,
    validate_and_import_workflow_dsl,
)
from app.analysis.workflow_runtime import (
    PARAMETERS,
    RUNTIME_SCHEMA,
    activate_version,
    execution_settings,
    pin_active_version,
    require_executable,
)
from app.core.db import get_session
from app.iam.deps import (
    Principal,
    require_platform_admin,
    require_user_side,
)
from app.iam.models import Organization, Role, User
from app.ingestion.models import Clause, Document, DocumentVersion
from app.ingestion.safety import report_held_citation_ids
from app.knowledge.models import ClauseTranslation
from app.updates.service import report_updates

AdminDep = Annotated[Principal, Depends(require_platform_admin)]  # 评估流程诊断只给平台管理员
PlatformAdminDep = Annotated[Principal, Depends(require_platform_admin)]
UserDep = Annotated[Principal, Depends(require_user_side)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]

admin_router = APIRouter(prefix="/api/admin/agent-studio", tags=["admin-agent-studio"])
user_router = APIRouter(prefix="/api/analysis", tags=["user-compliance-analysis"])


STALE_RUNNING_SECONDS = 600


async def _start(session: AsyncSession, task: AnalysisTask) -> None:
    """把任务置为运行中并在后台启动流水线,请求立即返回。"""
    task = await session.scalar(
        select(AnalysisTask)
        .where(AnalysisTask.id == task.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task.status in {"running", "awaiting_user_facts"}:
        raise HTTPException(409, "任务正在执行或待补充情况，不能重复启动或跳过追问")
    try:
        if (task.analysis_checkpoint_json or {}).get("resume_dimensions"):
            cfg = await session.get(AgentWorkflowConfig, task.workflow_version_id)
            if cfg is None:
                raise ValueError("断点的执行版本不存在")
            require_executable(cfg)
        else:
            await pin_active_version(session, task)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    task.status = "running"
    await append_event(session, task, "phase_started", {"node_id": "node_start", "phase": "start"})
    await session.commit()
    await session.refresh(task)  # updated_at 由数据库更新,需刷新后才能同步读取
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    launch_pipeline(factory, task.id)


async def _expire_stale(session: AsyncSession, task: AnalysisTask) -> None:
    """服务重启等原因导致长时间无进展的运行中任务,标记为失败,避免界面一直转圈。"""
    if task.status != "running" or task.updated_at is None:
        return
    age = (datetime.now(UTC) - task.updated_at).total_seconds()
    if age > STALE_RUNNING_SECONDS:
        task.status = "failed"
        await session.commit()
        await session.refresh(task)


_WORKFLOW_TEXT_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("JSON-Schema-Gateway-v1", "结构化契约网关-v1"),
    ("Effective Law & KG RAG Retriever", "现行有效法规与知识图谱混合检索"),
    ("Start · Scenario & Facts Input", "业务场景与初始事实输入"),
    ("Fact Collector & Sufficiency Guard", "事实完备性采集与追问守卫"),
    ("8-Dimension Compliance Matrix Analyst", "八维度合规矩阵对标分析"),
    ("Programmatic Citation & Substring Gate", "法规引文子串与数字程序化门禁"),
    ("8-Dimension Report Compiler & Exporter", "八维度评估报告汇编与导出"),
    ("72h Legal Amendment Impact Watcher", "法规修订 72 小时影响联动监测"),
    ("无 LLM 调用", "纯规则确定性执行，不调用生成式模型"),
    ("不调用大模型", "纯规则确定性校验"),
    ("外文 OCR/翻译复核资格", "外文扫描件文字与译文复核资格"),
    ("OCR 候选不启用", "扫描件待复核候选默认不启用"),
    ("显式声明JSON模式", "显式声明固定结构化输出模式"),
    ("严格只返回 JSON", "严格按固定结构化格式返回"),
    ("只返回 JSON", "仅返回固定结构化结果"),
    ("JSON DSL", "标准工作流配置包"),
    ("list[uuid]", "标识列表"),
)


def _humanize_workflow_text(val: str | None) -> str | None:
    if not val:
        return val
    out = val
    for src, dst in _WORKFLOW_TEXT_REPLACEMENTS:
        out = out.replace(src, dst)
    out = re.sub(r"\bOCR\b", "扫描件文字识别", out)
    out = re.sub(r"\bRAG\b", "检索增强", out)
    out = re.sub(r"\bLLM\b", "生成式模型", out)
    out = re.sub(r"\bJSON\b", "结构化数据", out)
    out = re.sub(r"\buuid\b", "资源标识", out, flags=re.IGNORECASE)
    return out


def _humanize_schema_dict(schema: dict | None) -> dict:
    if not isinstance(schema, dict):
        return {}
    return {k: (_humanize_workflow_text(v) if isinstance(v, str) else v) for k, v in schema.items()}


def _humanize_node_for_ui(raw_node: dict) -> dict:
    n = copy.deepcopy(raw_node)
    for field_key in ("title", "subtitle", "category_label", "lock_reason", "model_provider", "system_prompt", "responsibility_desc"):
        if isinstance(n.get(field_key), str):
            n[field_key] = _humanize_workflow_text(n[field_key])
    if n.get("node_id") not in ("node_dimension_analyst", "node_mcp_retriever") and isinstance(n.get("model_name"), str):
        n["model_name"] = _humanize_workflow_text(n["model_name"])
    if isinstance(n.get("mcp_tools"), list):
        cleaned_tools = []
        for tool in n["mcp_tools"]:
            if isinstance(tool, dict):
                t2 = dict(tool)
                if isinstance(t2.get("description"), str):
                    t2["description"] = _humanize_workflow_text(t2["description"])
                if isinstance(t2.get("display_name"), str):
                    t2["display_name"] = _humanize_workflow_text(t2["display_name"])
                t2["input_schema"] = _humanize_schema_dict(t2.get("input_schema"))
                t2["output_schema"] = _humanize_schema_dict(t2.get("output_schema"))
                cleaned_tools.append(t2)
            else:
                cleaned_tools.append(tool)
        n["mcp_tools"] = cleaned_tools
    return n


def _to_workflow_out(cfg: AgentWorkflowConfig) -> AgentWorkflowConfigOut:
    return AgentWorkflowConfigOut(
        id=cfg.id,
        workflow_key=cfg.workflow_key,
        version_no=cfg.version_no,
        name=_humanize_workflow_text(cfg.name) or cfg.name,
        description=_humanize_workflow_text(cfg.description) or "",
        dsl_version=cfg.dsl_version,
        is_active=cfg.is_active,
        readonly_locked=True,
        runtime_schema_version=cfg.runtime_schema_version,
        executable=cfg.runtime_schema_version == RUNTIME_SCHEMA,
        nodes=[
            WorkflowNodeOut(
                **_humanize_node_for_ui(n),
                runtime_settings=execution_settings(n)
                if cfg.runtime_schema_version == RUNTIME_SCHEMA
                else {},
                configurable_fields=sorted(
                    PARAMETERS.get(n["node_id"], set())
                    | (
                        {"model_name", "system_prompt"}
                        if n["node_id"] in ("node_dimension_analyst", "node_mcp_retriever")
                        else set()
                    )
                )
                if cfg.runtime_schema_version == RUNTIME_SCHEMA
                else [],
            )
            for n in (cfg.nodes_json or [])
        ],
        edges=[WorkflowEdgeOut(**e) for e in (cfg.edges_json or [])],
        change_note=_humanize_workflow_text(cfg.change_note),
        created_at=cfg.created_at,
    )


async def _load_org_and_creator(session: AsyncSession, t: AnalysisTask) -> tuple[str, str, str]:
    org_name = "系统示例"
    if t.org_id is not None:
        org = await session.get(Organization, t.org_id)
        if org is not None:
            org_name = org.name
    creator_email = ""
    creator_name = ""
    if t.created_by is not None:
        u = await session.get(User, t.created_by)
        if u is not None:
            creator_email = u.email
            creator_name = u.email.split("@")[0]  # 用户名 = 邮箱 @ 前部分，全站一致
    return org_name, creator_email, creator_name


def _build_documents_project_fact_rows(t: AnalysisTask) -> list[dict[str, Any]]:
    """Map ``doc_*`` workspace facts onto the eight-dimension (D1–D8) business fact collection table."""
    from app.analysis import risk_items

    f = t.facts_json or {}

    def _g(*keys: str) -> str:
        for k in keys:
            raw = f.get(f"doc_{k}") if f"doc_{k}" in f else f.get(k)
            val = str(raw or "").strip()
            if val and not val.startswith("暂不确定"):
                return val
        return ""

    def _join(*items: tuple[str, str], limit: int = 360) -> str:
        parts: list[str] = []
        for label, val in items:
            if not val:
                continue
            parts.append(f"{label}：{val}" if label else val)
        text = "；".join(parts).strip(" ；;。")
        return (text[: limit - 1] + "…") if len(text) > limit else text

    src_c, dst_c = _g("source_country"), _g("recipient_country")
    route_str = f"{src_c} → {dst_c}" if (src_c and dst_c) else (src_c or dst_c)
    risk_raw = _g("risk_rating")
    risk_txt = risk_items.shown("risk_rating", risk_raw) if risk_raw else ""

    specs = [
        (
            "doc_d1_scope",
            "D1",
            "跨境业务流向、处理主体与业务场景目的",
            "核定适用发出国与接收国数据保护法律体系及管辖机构。",
            _join(
                ("申报主体", _g("company_name")),
                ("跨境流向", route_str),
                ("涉及车型", t.vehicle_model or _g("vehicle_model")),
                ("处理目的", _g("purpose", "scenario")),
            ),
        ),
        (
            "doc_d2_data",
            "D2",
            "出境汽车数据字段类别、敏感信息识别与自然人规模",
            "判定是否触及重要数据、敏感个人信息或车外视觉脱敏阈值。",
            _join(
                ("数据类型", _g("data_types", "exterior_video_and_gnss")),
                ("涉及规模", _g("data_volume")),
                ("敏感信息情况", _g("sensitive_pi")),
                ("重要数据认定", _g("important_data")),
            ),
        ),
        (
            "doc_d3_transfer",
            "D3",
            "境外接收方主体信息、传输链路与再向第三方提供约束",
            "确定法定跨境合规路径、标准合同或事先授权义务。",
            _join(
                ("境外接收方", _g("recipient")),
                ("传输链路", _g("storage_chain", "cross_border_mechanism")),
                ("再转移管控", _g("onward_provision")),
            ),
        ),
        (
            "doc_d4_consent",
            "D4",
            "面向自然人的隐私告知、同意取得方式及权利响应渠道",
            "核验单独同意、透明告知、DPO 联络及查阅更正删除机制。",
            _join(
                ("告知与同意", _g("consent_and_notice")),
                ("主体权利保障", _g("individual_rights")),
                ("数据安全负责人/DPO", _g("security_officer")),
            ),
        ),
        (
            "doc_d5_retention",
            "D5",
            "境内外存储位置、保存期限及到期删除与匿名化策略",
            "对照发出国本地化留存规范与出境后最小保存期限要求。",
            _join(
                ("保存期限与存放位置", _g("retention_and_blackbox")),
                ("存储与加密链路", _g("storage_chain")),
            ),
        ),
        (
            "doc_d6_liability",
            "D6",
            "遵守法律法规情况、准据法救济约定及安全事件应急预案",
            "明确监管调查配合、违约连带责任承担及安全事件通报时限。",
            _join(
                ("守法合规情况", _g("law_compliance")),
                ("管辖法律与救济", _g("governing_law")),
                ("安全事件预案", _g("incident_plan")),
            ),
        ),
        (
            "doc_d7_security",
            "D7",
            "技术与管理安全保障措施及合规认证/有效性证明",
            "支撑跨境申报材料、属地准入核验及安全保护能力论证。",
            _join(
                ("技术保护措施", _g("risk_measures")),
                ("内控管理制度", _g("security_mgmt")),
                ("有效性证明", _g("effectiveness_proof")),
            ),
        ),
        (
            "doc_d8_remediation",
            "D8",
            "合规风险研判分级、整改措施责任人及落实核验情况",
            "闭环跟踪高/中风险压降情况与出境申报前整改验收记录。",
            _join(
                ("整改核验结论", _g("verification")),
                ("整改措施与责任人", _g("measures")),
                ("风险评估台账", risk_txt),
            ),
        ),
    ]
    rows: list[dict[str, Any]] = []
    for fkey, dim, q_zh, why, val in specs:
        ok = len(val) >= 4
        rows.append(
            {
                "fact_key": fkey,
                "dimension_code": dim,
                "question_zh": q_zh,
                "why_needed": why,
                "suggested_options": [],
                "current_value": val if ok else None,
                "is_fulfilled": ok,
            }
        )
    return rows


def _assessment_chat() -> bool:
    from app.core.config import get_settings

    return get_settings().assessment_chat_enabled


def _enrich_trace_snapshots(
    t: AnalysisTask,
    tr: AnalysisStepTrace,
    rvs: list[ReportVersion],
    evaluated_missing: list[dict],
) -> tuple[dict, dict]:
    """确保历史任务与新任务的每个阶段都有可展开查看的完整输入/输出产出快照。"""
    inp = dict(tr.input_snapshot_json or {})
    out = dict(tr.output_snapshot_json or {})
    if tr.phase == "fact_check":
        inp.setdefault("country_code", t.country_code)
        inp.setdefault("vehicle_model", t.vehicle_model)
        inp.setdefault("scenario_type", t.scenario_type)
        out.setdefault("collected_facts", dict(t.facts_json or {}))
        if "fact_check_items" not in out:
            out["fact_check_items"] = [
                {
                    "fact_key": m["fact_key"],
                    "dimension_code": m["dimension_code"],
                    "question_zh": m["question_zh"],
                    "value": m.get("current_value") or "",
                    "is_fulfilled": bool(m.get("is_fulfilled")),
                }
                for m in evaluated_missing
            ]
    elif tr.phase in ("verification", "re_verification"):
        findings_list = list(t.findings_json or [])
        kept = int(out.get("kept_sentences") or len(findings_list))
        dropped = int(out.get("dropped_sentences") or 0)
        out.setdefault("verified_passed_count", kept)
        out.setdefault("verified_failed_count", dropped)
        raw_items = out.get("verification_items")
        if isinstance(raw_items, list) and raw_items:
            for item in raw_items:
                if isinstance(item, dict):
                    item.setdefault("passed", True)
                    item.setdefault("dimension_code", item.get("dimension_title") or "合规核验")
                    item.setdefault("obligation", item.get("claim") or item.get("obligation_summary") or "")
                    item.setdefault("quote", item.get("evidence_quote") or "")
        elif findings_list:
            out["verification_items"] = [
                {
                    "dimension_code": f.get("dimension_code") or f.get("dimension_title") or "合规核验",
                    "obligation": f.get("obligation_summary") or f.get("claim") or "",
                    "citation_label": f.get("citation_label") or f.get("law_title") or "",
                    "quote": f.get("evidence_quote") or f.get("quote") or "",
                    "passed": f.get("verified", True) is not False,
                }
                for f in findings_list[:20]
                if isinstance(f, dict)
            ]
    elif tr.phase == "compilation" and rvs:
        rv = rvs[0]
        inp.setdefault("country_code", t.country_code)
        inp.setdefault("vehicle_model", t.vehicle_model)
        inp.setdefault("verified_findings_count", len(t.findings_json or []))
        out.setdefault("report_status", rv.status)
        out.setdefault("is_partial_sample", rv.is_partial_sample)
        out.setdefault("executive_summary", rv.executive_summary)
        out.setdefault("verified_findings_count", len(t.findings_json or []))
        if "dimensions_deliverables" not in out:
            out["dimensions_deliverables"] = [
                {
                    "dimension_code": d.get("dimension_code", ""),
                    "dimension_title": d.get("dimension_title", ""),
                    "status": d.get("status", ""),
                    "risk_level": d.get("risk_level", ""),
                    "citations_count": len(d.get("citations") or []),
                    "content_markdown": d.get("content_markdown", ""),
                    "key_recommendations": list(d.get("key_recommendations") or []),
                    "missing_gap_note": d.get("missing_gap_note"),
                }
                for d in (rv.dimensions_json or [])
            ]
    elif tr.phase == "update_linkage" and rvs:
        rv = rvs[0]
        out.setdefault("stale_impacts_count", len(rv.stale_impacts_json or []))
        out.setdefault("stale_impacts", list(rv.stale_impacts_json or []))
    return inp, out


async def _live_project(session: AsyncSession, t: AnalysisTask) -> str | None:
    """The documents project a consulting chat belongs to, while that project is still in the list."""
    pid = (t.facts_json or {}).get("_doc_project") if t.kind == "advice" else None
    if not pid:
        return None
    project = await session.get(AnalysisTask, uuid.UUID(pid))
    return pid if project is not None and project.archived_at is None else None


def _visible_to_user(t: AnalysisTask, user: Principal) -> bool:
    """普通查看者建的咨询对话只归本人可见；其余企业对话对本企业所有账号可见。"""
    owner = viewer_owner(t)
    return owner is None or owner == str(user.user_id)


async def _to_task_summaries(
    session: AsyncSession, tasks: list[AnalysisTask], viewer_id: uuid.UUID | None = None
) -> list[AnalysisTaskSummaryOut]:
    if not tasks:
        return []
    for t in tasks:
        await _expire_stale(session, t)
    task_ids = [t.id for t in tasks]

    latest_rv: dict[uuid.UUID, tuple[int, bool]] = {}
    for tid, vno, is_part in (
        await session.execute(
            select(ReportVersion.task_id, ReportVersion.version_no, ReportVersion.is_partial_sample)
            .where(ReportVersion.task_id.in_(task_ids))
            .order_by(ReportVersion.task_id, ReportVersion.version_no.desc())
        )
    ).all():
        latest_rv.setdefault(tid, (vno, is_part))

    trace_counts: dict[uuid.UUID, int] = {}
    last_trace: dict[uuid.UUID, SimpleNamespace] = {}
    for tid, phase, st, is_bt in (
        await session.execute(
            select(
                AnalysisStepTrace.task_id,
                AnalysisStepTrace.phase,
                AnalysisStepTrace.status,
                AnalysisStepTrace.is_backtrack,
            )
            .where(AnalysisStepTrace.task_id.in_(task_ids))
            .order_by(AnalysisStepTrace.task_id, AnalysisStepTrace.seq_no.asc())
        )
    ).all():
        trace_counts[tid] = trace_counts.get(tid, 0) + 1
        last_trace[tid] = SimpleNamespace(phase=phase, status=st, is_backtrack=is_bt)

    msg_counts = {
        tid: int(cnt or 0)
        for tid, cnt in (
            await session.execute(
                select(AnalysisMessage.task_id, func.count(AnalysisMessage.id))
                .where(AnalysisMessage.task_id.in_(task_ids))
                .group_by(AnalysisMessage.task_id)
            )
        ).all()
    }

    org_ids = list({t.org_id for t in tasks if t.org_id is not None})
    orgs_by_id = (
        {
            oid: name
            for oid, name in (
                await session.execute(select(Organization.id, Organization.name).where(Organization.id.in_(org_ids)))
            ).all()
        }
        if org_ids
        else {}
    )

    user_ids = list({t.created_by for t in tasks if t.created_by is not None})
    users_by_id = (
        {
            uid: email
            for uid, email in (
                await session.execute(select(User.id, User.email).where(User.id.in_(user_ids)))
            ).all()
        }
        if user_ids
        else {}
    )

    proj_uuids: list[uuid.UUID] = []
    for t in tasks:
        raw_pid = (t.facts_json or {}).get("_doc_project") if t.kind == "advice" else None
        if raw_pid:
            try:
                proj_uuids.append(uuid.UUID(str(raw_pid)))
            except ValueError:
                pass
    live_projects = (
        {
            str(pid)
            for pid in (
                await session.scalars(
                    select(AnalysisTask.id).where(AnalysisTask.id.in_(proj_uuids), AnalysisTask.archived_at.is_(None))
                )
            ).all()
        }
        if proj_uuids
        else set()
    )

    out: list[AnalysisTaskSummaryOut] = []
    for t in tasks:
        org_name = orgs_by_id.get(t.org_id, "系统示例") if t.org_id is not None else "系统示例"
        creator_email = users_by_id.get(t.created_by, "") if t.created_by is not None else ""
        creator_name = creator_email.split("@")[0] if creator_email else ""
        tr_list = [last_trace[t.id]] if t.id in last_trace else []
        active_node_id, active_node_title = compute_active_node(t.status, tr_list)
        rv_info = latest_rv.get(t.id)
        latest_ver = rv_info[0] if rv_info else 0
        is_partial = rv_info[1] if rv_info else (t.status == "partial_sample")
        missing_unfulfilled = sum(
            1 for m in (t.missing_facts_json or []) if not m.get("is_fulfilled", False)
        )
        if t.status == "awaiting_user_facts":
            missing_unfulfilled += len(t.pending_user_facts_json or [])
        owner = viewer_owner(t)
        raw_pid = (t.facts_json or {}).get("_doc_project") if t.kind == "advice" else None
        project_id = str(raw_pid) if raw_pid and str(raw_pid) in live_projects else None
        out.append(
            AnalysisTaskSummaryOut(
                project_id=project_id,
                viewer_own=bool(owner is not None and viewer_id is not None and owner == str(viewer_id)),
                id=t.id,
                conversation_code=format_conversation_code(t.id, t.created_at),
                org_id=t.org_id,
                org_name=org_name,
                creator_email=creator_email,
                creator_name=creator_name,
                title=t.title,
                country_code=t.country_code,
                country_name_zh=country_name_zh(t.country_code),
                vehicle_model=t.vehicle_model,
                scenario_type=t.scenario_type,
                scenario_label=SCENARIO_LABELS.get(t.scenario_type, t.scenario_type),
                status=t.status,
                sufficiency_score=t.sufficiency_score,
                missing_facts_count=missing_unfulfilled,
                messages_count=msg_counts.get(t.id, 0),
                steps_count=trace_counts.get(t.id, 0),
                current_active_node_id=active_node_id,
                current_active_node_title=active_node_title,
                findings_count=len(t.findings_json or []),
                latest_report_version=latest_ver,
                is_partial_sample=is_partial,
                total_tokens=t.total_tokens,
                total_duration_ms=t.total_duration_ms,
                mcp_calls_count=t.mcp_calls_count,
                backtrack_count=t.backtrack_count,
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
        )
    return out


async def _to_task_summary(
    session: AsyncSession, t: AnalysisTask, viewer_id: uuid.UUID | None = None
) -> AnalysisTaskSummaryOut:
    return (await _to_task_summaries(session, [t], viewer_id=viewer_id))[0]


async def _to_task_detail(
    session: AsyncSession, t: AnalysisTask, viewer_id: uuid.UUID | None = None
) -> AnalysisTaskDetailOut:
    await _expire_stale(session, t)
    # Only real, committed phase starts drive the user-facing activity label.
    # No prompts, tools, internal node names or model reasoning leave this projection.
    activity = None
    if t.status == "running":
        event = await session.scalar(
            select(AnalysisEvent)
            .where(AnalysisEvent.task_id == t.id, AnalysisEvent.event_type == "phase_started")
            .order_by(AnalysisEvent.id.desc())
            .limit(1)
        )
        if event is not None:
            activity = {
                "label": PHASES.get(event.payload.get("phase"), "正在评估业务场景"),
                "state": "running",
                "started_at": event.created_at.isoformat(),
            }
    traces = list(
        (
            await session.execute(
                select(AnalysisStepTrace)
                .where(AnalysisStepTrace.task_id == t.id)
                .order_by(AnalysisStepTrace.seq_no.asc())
            )
        )
        .scalars()
        .all()
    )
    rvs = list(
        (
            await session.execute(
                select(ReportVersion)
                .where(ReportVersion.task_id == t.id)
                .order_by(ReportVersion.version_no.desc())
            )
        )
        .scalars()
        .all()
    )

    if t.kind == "documents":
        evaluated_missing = _build_documents_project_fact_rows(t)
    else:
        _, evaluated_missing = evaluate_fact_sufficiency(t.country_code, t.facts_json or {})

    trace_outs = []
    for tr in traces:
        inp_snap, out_snap = _enrich_trace_snapshots(t, tr, rvs, evaluated_missing)
        trace_outs.append(
            AnalysisStepTraceOut(
                workflow_version_id=tr.workflow_version_id,
                id=tr.id,
                seq_no=tr.seq_no,
                agent_node_id=tr.agent_node_id,
                agent_name=tr.agent_name,
                phase=tr.phase,
                status=tr.status,
                business_summary=tr.business_summary,
                is_backtrack=tr.is_backtrack,
                backtrack_reason=tr.backtrack_reason,
                backtrack_resolution=tr.backtrack_resolution,
                tool_calls=[ToolCallTraceOut(**tc) for tc in (tr.tool_calls_json or [])],
                input_snapshot=inp_snap,
                output_snapshot=out_snap,
                tokens_used=tr.tokens_used,
                duration_ms=tr.duration_ms,
                created_at=tr.created_at,
            )
        )

    rv_outs = [
        ReportVersionOut(
            regulatory_updates=await report_updates(session, rv.id),
            citation_policy=rv.citation_policy or "legacy_unrecorded",
            source_integrity_alerts=[
                f"原文绑定或页级解析待核对，引用 {cid} 已隔离，需核查后重新分析"
                for cid in await report_held_citation_ids(session, rv)
            ],
            id=rv.id,
            task_id=rv.task_id,
            version_no=rv.version_no,
            status=rv.status,
            is_partial_sample=rv.is_partial_sample,
            change_note=rv.change_note,
            executive_summary=rv.executive_summary,
            dimensions=[ReportDimensionOut(**d) for d in (rv.dimensions_json or [])],
            stale_impacts=[StaleImpactAlertOut(**s) for s in (rv.stale_impacts_json or [])],
            consulting=upgrade_legacy(rv.consulting_json),
            created_at=rv.created_at,
        )
        for rv in rvs
    ]

    msgs = list(
        (
            await session.execute(
                select(AnalysisMessage)
                .where(AnalysisMessage.task_id == t.id)
                .order_by(AnalysisMessage.seq_no.asc())
            )
        )
        .scalars()
        .all()
    )
    org_name, creator_email, creator_name = await _load_org_and_creator(session, t)
    active_node_id, active_node_title = compute_active_node(t.status, traces)
    owner = viewer_owner(t)
    return AnalysisTaskDetailOut(
        activity=activity,
        archived=t.archived_at is not None,
        viewer_own=bool(owner is not None and viewer_id is not None and owner == str(viewer_id)),
        messages=[
            ChatMessageOut(
                id=m.id,
                seq_no=m.seq_no,
                role=m.role,
                content=m.content,
                options=list(m.options_json or []),
                slot=m.slot,
                created_at=m.created_at,
            )
            for m in msgs
        ],
        pending_user_facts=t.pending_user_facts_json or [],
        fact_followup_rounds=t.fact_followup_rounds,
        id=t.id,
        conversation_code=format_conversation_code(t.id, t.created_at),
        org_id=t.org_id,
        org_name=org_name,
        creator_email=creator_email,
        creator_name=creator_name,
        current_active_node_id=active_node_id,
        current_active_node_title=active_node_title,
        title=t.title,
        country_code=t.country_code,
        country_name_zh=country_name_zh(t.country_code),
        vehicle_model=t.vehicle_model,
        scenario_type=t.scenario_type,
        scenario_label=SCENARIO_LABELS.get(t.scenario_type, t.scenario_type),
        status=t.status,
        sufficiency_score=t.sufficiency_score,
        facts=t.facts_json or {},
        missing_facts=[FollowUpQuestionOut(**m) for m in evaluated_missing],
        findings=[AnalysisFindingOut(**f) for f in (t.findings_json or [])],
        step_traces=trace_outs,
        report_versions=rv_outs,
        total_tokens=t.total_tokens,
        total_duration_ms=t.total_duration_ms,
        mcp_calls_count=t.mcp_calls_count,
        backtrack_count=t.backtrack_count,
        workflow_version_id=t.workflow_version_id,
        workflow_version_no=t.workflow_version_no,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


# ==================== 管理端 Dify 风格只读多智能体工作台接口 ====================


@admin_router.get("/citation-policy")
async def get_citation_policy(_: AdminDep):
    from app.core.config import get_settings

    policy = get_settings().citation_policy
    return {
        "policy": policy,
        "label": "允许机器内容进入草稿，逐条待核对"
        if policy == "allow_machine_with_notice"
        else "仅使用原文及译文均已复核的引用",
        "readonly": True,
    }


@admin_router.get("/workflow", response_model=AgentWorkflowConfigOut)
async def get_admin_workflow(
    _: AdminDep,
    session: SessionDep,
    version_no: int | None = Query(default=None),
) -> AgentWorkflowConfigOut:
    """查看当前激活(或指定版本)的多智能体编排拓扑、节点详情与上下游连接(只读锁定态)。"""
    await ensure_workflow_config(session)
    if version_no is not None:
        cfg = (
            await session.execute(
                select(AgentWorkflowConfig).where(
                    AgentWorkflowConfig.workflow_key == WORKFLOW_KEY,
                    AgentWorkflowConfig.version_no == version_no,
                )
            )
        ).scalar_one_or_none()
        if cfg is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="指定工作流版本不存在"
            )
        return _to_workflow_out(cfg)

    cfg = await ensure_workflow_config(session)
    return _to_workflow_out(cfg)


@admin_router.get("/workflow/versions", response_model=list[WorkflowVersionSummaryOut])
async def list_workflow_versions(
    _: AdminDep,
    session: SessionDep,
) -> list[WorkflowVersionSummaryOut]:
    await ensure_workflow_config(session)
    cfgs = list(
        (
            await session.execute(
                select(AgentWorkflowConfig)
                .where(AgentWorkflowConfig.workflow_key == WORKFLOW_KEY)
                .order_by(AgentWorkflowConfig.version_no.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        WorkflowVersionSummaryOut(
            id=c.id,
            version_no=c.version_no,
            name=c.name,
            dsl_version=c.dsl_version,
            is_active=c.is_active,
            readonly_locked=True,
            executable=c.runtime_schema_version == RUNTIME_SCHEMA,
            nodes_count=len(c.nodes_json or []),
            edges_count=len(c.edges_json or []),
            change_note=_humanize_workflow_text(c.change_note),
            created_at=c.created_at,
        )
        for c in cfgs
    ]


@admin_router.post(
    "/workflow/versions/{version}/activate",
    response_model=AgentWorkflowConfigOut,
)
async def activate_workflow_version(
    version: str,
    user: PlatformAdminDep,
    session: SessionDep,
) -> AgentWorkflowConfigOut:
    try:
        target = await activate_version(session, version, user.user_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _to_workflow_out(target)


@admin_router.get("/workflow/export")
async def export_admin_workflow_dsl(
    _: AdminDep,
    session: SessionDep,
    version_no: int | None = Query(default=None),
) -> dict:
    """导出多智能体编排配置包(JSON DSL),供长久维护、跨环境迁移与版本备份。"""
    await ensure_workflow_config(session)
    if version_no is not None:
        cfg = (
            await session.execute(
                select(AgentWorkflowConfig).where(
                    AgentWorkflowConfig.workflow_key == WORKFLOW_KEY,
                    AgentWorkflowConfig.version_no == version_no,
                )
            )
        ).scalar_one_or_none()
        if cfg is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作流版本不存在")
    else:
        cfg = await ensure_workflow_config(session)
    return export_workflow_dsl(cfg)


@admin_router.post(
    "/workflow/import",
    response_model=AgentWorkflowConfigOut,
    status_code=status.HTTP_201_CREATED,
)
async def import_admin_workflow_dsl(
    body: WorkflowDslImportIn,
    user: PlatformAdminDep,
    session: SessionDep,
) -> AgentWorkflowConfigOut:
    """导入并校验多智能体编排配置包(JSON DSL),生成新版本并保持生产只读安全锁。"""
    await ensure_workflow_config(session)
    try:
        new_cfg = await validate_and_import_workflow_dsl(
            session=session,
            dsl_payload=body.dsl_payload,
            change_note=body.change_note,
            user_id=user.user_id,
            activate_immediately=body.activate_immediately,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_workflow_out(new_cfg)


@admin_router.get("/tasks", response_model=list[AnalysisTaskSummaryOut])
async def list_admin_analysis_tasks(
    _: AdminDep,
    session: SessionDep,
    status_filter: str | None = Query(default=None, alias="status"),
    country_code: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    exclude_empty: bool = Query(default=False),
) -> list[AnalysisTaskSummaryOut]:
    from app.iam.service import TRIAL_ORG_MARKER

    await ensure_workflow_config(session)
    stmt = (
        select(AnalysisTask)
        .join(Organization, Organization.id == AnalysisTask.org_id)
        .where(
            AnalysisTask.org_id.is_not(None),
            Organization.is_active.is_(True),
            ~Organization.name.like(f"%{TRIAL_ORG_MARKER}%"),
        )
    )
    if not include_archived:
        stmt = stmt.where(AnalysisTask.archived_at.is_(None))
    if status_filter:
        stmt = stmt.where(AnalysisTask.status == status_filter)
    from app.analysis.document_basis import country_of

    cc_filter = country_code.upper().strip() if country_code else ""
    stmt = stmt.order_by(AnalysisTask.updated_at.desc(), AnalysisTask.created_at.desc())
    tasks = [
        t
        for t in (await session.execute(stmt)).scalars().all()
        if viewer_owner(t) is None
        and not bool((t.facts_json or {}).get("_project_side"))
        and not (t.facts_json or {}).get("_doc_project")
        and (
            not cc_filter
            or (t.country_code or "").upper() == cc_filter
            or (
                t.kind == "documents"
                and (
                    country_of((t.facts_json or {}).get("doc_source_country")) == cc_filter
                    or country_of((t.facts_json or {}).get("doc_recipient_country")) == cc_filter
                )
            )
        )
    ]
    out = await _to_task_summaries(session, tasks)
    if exclude_empty:
        out = [
            item
            for item in out
            if not (
                item.country_code in ("--", "")
                and item.steps_count == 0
                and item.latest_report_version == 0
                and item.messages_count <= 1
            )
        ]
    if keyword and keyword.strip():
        kw = keyword.strip().lower()
        out = [
            item
            for item in out
            if kw in item.conversation_code.lower()
            or kw in str(item.id).lower()
            or kw in item.title.lower()
            or kw in item.vehicle_model.lower()
            or kw in item.org_name.lower()
            or kw in item.creator_email.lower()
            or kw in item.creator_name.lower()
        ]
    return out


@admin_router.get("/tasks/{task_id}", response_model=AnalysisTaskDetailOut)
async def get_admin_analysis_task_detail(
    task_id: uuid.UUID,
    _: AdminDep,
    session: SessionDep,
) -> AnalysisTaskDetailOut:
    task = await session.get(AnalysisTask, task_id)
    if task is None or viewer_owner(task) is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该合规评估项目")
    return await _to_task_detail(session, task)


@admin_router.delete("/tasks/{task_id}")
async def delete_admin_analysis_task(
    task_id: uuid.UUID,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    """平台管理员删除某条历史对话产生的工作流记录。"""
    task = await session.get(AnalysisTask, task_id)
    if task is None or viewer_owner(task) is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对话工作流记录不存在")
    code = format_conversation_code(task.id, task.created_at)
    await session.delete(task)
    await session.commit()
    return {"deleted": True, "id": str(task_id), "conversation_code": code}


@admin_router.post("/tasks/{task_id}/rerun", response_model=AnalysisTaskDetailOut)
async def rerun_admin_analysis_task(
    task_id: uuid.UUID,
    _: PlatformAdminDep,
    session: SessionDep,
) -> AnalysisTaskDetailOut:
    """平台管理员重新触发某条已齐备事实的对话工作流。"""
    task = await session.get(AnalysisTask, task_id)
    if task is None or viewer_owner(task) is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对话工作流记录不存在")
    if task.status == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该对话工作流正在实时运行中",
        )
    if task.sufficiency_score < 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该对话尚未完成八维度必填情况采集，无法启动合规评估流程",
        )
    await _start(session, task)
    return await _to_task_detail(session, task)


@admin_router.get("/benchmarks", response_model=BenchmarkSummaryOut)
async def get_admin_benchmarks(
    _: AdminDep,
    session: SessionDep,
) -> BenchmarkSummaryOut:
    await ensure_workflow_config(session)
    res = await run_retrieval_benchmarks(session)
    return BenchmarkSummaryOut(**res)


# ==================== 业务用户端多智能体合规分析与八维度报告接口 ====================


@admin_router.get("/evaluations/suite")
async def get_evaluation_suite(_: AdminDep):
    from app.analysis.evaluation import suite

    return suite()


@admin_router.get("/evaluations/latest")
async def get_latest_evaluation(_: AdminDep, session: SessionDep):
    from app.analysis.evaluation import latest_evaluation

    return await latest_evaluation(session)


@admin_router.post("/evaluations/{batch_id}/cases/{question_id}")
async def execute_evaluation_case(
    batch_id: uuid.UUID,
    question_id: str,
    user: PlatformAdminDep,
    session: SessionDep,
):
    from app.analysis.evaluation import evaluate_case

    try:
        return await evaluate_case(session, question_id, batch_id, user.user_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@user_router.get("/tasks", response_model=list[PublicTaskSummary])
async def list_user_analysis_tasks(
    user: UserDep,
    session: SessionDep,
    country_code: str | None = None,
    archived: bool = False,
    kind: Literal["chat", "documents"] = "chat",
    standalone_only: bool = False,
) -> list[AnalysisTaskSummaryOut]:
    await ensure_workflow_config(session)
    stmt = select(AnalysisTask).where(AnalysisTask.org_id == user.org_id)
    stmt = stmt.where(AnalysisTask.archived_at.is_not(None) if archived else AnalysisTask.archived_at.is_(None))
    stmt = stmt.where(AnalysisTask.kind.in_(("chat", "advice")) if kind == "chat" else AnalysisTask.kind == kind)
    if country_code:
        stmt = stmt.where(AnalysisTask.country_code == country_code.upper())
    stmt = stmt.order_by(AnalysisTask.created_at.asc())
    tasks = [
        t
        for t in (await session.execute(stmt)).scalars().all()
        if _visible_to_user(t, user)
        and not (standalone_only and kind == "chat" and bool((t.facts_json or {}).get("_project_side")))
    ]
    return await _to_task_summaries(session, tasks, viewer_id=user.user_id)


@user_router.post(
    "/tasks",
    response_model=PublicTaskDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_user_analysis_task(
    body: AnalysisTaskCreateIn,
    user: UserDep,
    session: SessionDep,
) -> AnalysisTaskDetailOut:
    await ensure_workflow_config(session)
    cc = body.country_code.upper()
    score, missing = evaluate_fact_sufficiency(cc, body.facts or {})
    task = AnalysisTask(
        org_id=user.org_id,
        created_by=user.user_id,
        title=body.title.strip(),
        country_code=cc,
        vehicle_model=body.vehicle_model.strip(),
        scenario_type=body.scenario_type.strip(),
        status="ready_to_run" if score >= 100 else "collecting_facts",
        facts_json=body.facts or {},
        missing_facts_json=missing,
        sufficiency_score=score,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)

    if score >= 100 and body.auto_run_if_sufficient:
        await _start(session, task)

    return await _to_task_detail(session, task)


@user_router.get("/tasks/{task_id}", response_model=PublicTaskDetail)
async def get_user_analysis_task_detail(
    task_id: uuid.UUID,
    user: UserDep,
    session: SessionDep,
) -> AnalysisTaskDetailOut:
    task = await session.get(AnalysisTask, task_id)
    if task is None or task.org_id != user.org_id or not _visible_to_user(task, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该合规评估项目")
    return await _to_task_detail(session, task, viewer_id=user.user_id)


async def _own_task(
    session: AsyncSession, task_id: uuid.UUID, user: Principal, *, write: bool = False
) -> AnalysisTask:
    """A conversation of the user's company: every user of the company sees the company's
    conversations and may manage them (deleting first moves to the archive, and is audited); 404 for
    another company's or another viewer's private consulting chat; 403 if a viewer tries to modify a
    company shared conversation."""
    task = await session.get(AnalysisTask, task_id)
    if task is None or task.org_id != user.org_id or not _visible_to_user(task, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "对话不存在")
    if write and user.role == Role.VIEWER and viewer_owner(task) != str(user.user_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "查看账号只能查看本企业的共享对话，不能修改；如需提问请新建咨询对话")
    return task


class BatchTasksIn(BaseModel):
    kind: Literal["chat", "documents"] = "documents"
    ids: list[uuid.UUID] | None = None


async def _batch_candidates(
    session: AsyncSession,
    user: Principal,
    *,
    kind: Literal["chat", "documents"],
    archived: bool,
    ids: list[uuid.UUID] | None = None,
) -> list[AnalysisTask]:
    stmt = select(AnalysisTask).where(AnalysisTask.org_id == user.org_id)
    stmt = stmt.where(AnalysisTask.archived_at.is_not(None) if archived else AnalysisTask.archived_at.is_(None))
    stmt = stmt.where(AnalysisTask.kind.in_(("chat", "advice")) if kind == "chat" else AnalysisTask.kind == "documents")
    if ids is not None:
        if not ids:
            return []
        stmt = stmt.where(AnalysisTask.id.in_(ids))
    stmt = stmt.order_by(AnalysisTask.created_at.asc())
    rows = (await session.execute(stmt)).scalars().all()
    out: list[AnalysisTask] = []
    for t in rows:
        if not _visible_to_user(t, user):
            continue
        if user.role == Role.VIEWER and viewer_owner(t) != str(user.user_id):
            continue
        if kind == "chat" and ids is None:
            facts = t.facts_json or {}
            if facts.get("_project_side"):
                continue
        out.append(t)
    return out


@user_router.post("/tasks/batch-archive")
async def batch_archive_analysis_tasks(
    body: BatchTasksIn, request: Request, user: UserDep, session: SessionDep
) -> dict:
    """一键归档当前列表中的合规评估项目或咨询对话（运行中的评估自动跳过；项目关联的侧栏对话同步归档）。"""
    from app.audit.service import set_detail

    tasks = await _batch_candidates(session, user, kind=body.kind, archived=False, ids=body.ids)
    now = datetime.now(UTC)
    done: list[AnalysisTask] = []
    skipped_running = 0
    for t in tasks:
        if t.status == "running":
            skipped_running += 1
            continue
        t.archived_at = now
        t.archived_by = user.user_id
        side_chat = await _project_side_chat(session, t)
        if side_chat is not None and side_chat.archived_at is None:
            side_chat.archived_at, side_chat.archived_by = now, user.user_id
        done.append(t)
    await session.commit()
    target_zh = "合规评估项目" if body.kind == "documents" else "咨询对话"
    set_detail(
        request,
        summary=f"批量归档 {len(done)} 个{target_zh}",
        after={"kind": body.kind, "count": len(done), "titles": [t.title for t in done[:15]]},
    )
    return {"archived_count": len(done), "skipped_running": skipped_running, "ids": [str(t.id) for t in done]}


@user_router.post("/tasks/batch-restore")
async def batch_restore_analysis_tasks(
    body: BatchTasksIn, request: Request, user: UserDep, session: SessionDep
) -> dict:
    """一键恢复归档中的合规评估项目或咨询对话（项目关联的侧栏对话同步恢复）。"""
    from app.audit.service import set_detail

    tasks = await _batch_candidates(session, user, kind=body.kind, archived=True, ids=body.ids)
    for t in tasks:
        t.archived_at = None
        t.archived_by = None
        side_chat = await _project_side_chat(session, t)
        if side_chat is not None:
            side_chat.archived_at = side_chat.archived_by = None
    await session.commit()
    target_zh = "合规评估项目" if body.kind == "documents" else "咨询对话"
    set_detail(
        request,
        summary=f"批量恢复 {len(tasks)} 个{target_zh}",
        after={"kind": body.kind, "count": len(tasks), "titles": [t.title for t in tasks[:15]]},
    )
    return {"restored_count": len(tasks), "ids": [str(t.id) for t in tasks]}


@user_router.post("/tasks/batch-delete")
async def batch_delete_analysis_tasks(
    body: BatchTasksIn, request: Request, user: UserDep, session: SessionDep
) -> dict:
    """一键彻底清空归档中的合规评估项目或咨询对话（仅限已归档记录，项目侧栏对话同步删除）。"""
    from app.audit.service import set_detail

    tasks = await _batch_candidates(session, user, kind=body.kind, archived=True, ids=body.ids)
    deleted_ids: list[str] = []
    titles: list[str] = []
    for t in tasks:
        if t.status == "running":
            continue
        deleted_ids.append(str(t.id))
        titles.append(t.title)
        side_chat = await _project_side_chat(session, t)
        if side_chat is not None:
            await session.delete(side_chat)
        await session.delete(t)
    await session.commit()
    target_zh = "合规评估项目" if body.kind == "documents" else "咨询对话"
    set_detail(
        request,
        summary=f"批量彻底删除 {len(deleted_ids)} 个已归档{target_zh}",
        before={"kind": body.kind, "count": len(deleted_ids), "titles": titles[:15]},
    )
    return {"deleted_count": len(deleted_ids), "ids": deleted_ids}


@user_router.delete("/tasks/{task_id}")
async def delete_own_analysis_task(
    task_id: uuid.UUID, user: UserDep, session: SessionDep, permanent: bool = False
) -> dict:
    """用户“删除”对话：先进归档（可恢复）。已在归档里的再删除（permanent=true）才是彻底删除，
    连同消息、上传的文件、对照结果和评估记录一起，无法恢复。两步都会进审计日志。
    同一企业的用户都能管理本企业的对话；评估运行中的不能删。"""
    task = await _own_task(session, task_id, user, write=True)
    if task.status == "running":
        raise HTTPException(status.HTTP_409_CONFLICT, "这个对话正在评估中，等评估完成后再删除")
    code = format_conversation_code(task.id, task.created_at)
    side_chat = await _project_side_chat(session, task)  # 文件项目旁边的对话跟着项目一起归档、删除
    if task.archived_at is None:
        if permanent:
            raise HTTPException(status.HTTP_409_CONFLICT, "对话要先归档（删除）后，才能在归档里彻底删除")
        task.archived_at = datetime.now(UTC)
        task.archived_by = user.user_id
        if side_chat is not None and side_chat.archived_at is None:
            side_chat.archived_at, side_chat.archived_by = task.archived_at, user.user_id
        await session.commit()
        return {"archived": True, "id": str(task_id), "conversation_code": code}
    if not permanent:
        return {"archived": True, "id": str(task_id), "conversation_code": code}
    if side_chat is not None:
        await session.delete(side_chat)
    await session.delete(task)
    await session.commit()
    return {"deleted": True, "id": str(task_id), "conversation_code": code}


class RenameIn(BaseModel):
    title: str | None = Field(default=None, max_length=60)


@user_router.patch("/tasks/{task_id}")
async def rename_own_analysis_task(
    task_id: uuid.UUID,
    user: UserDep,
    session: SessionDep,
    body: RenameIn | None = Body(default=None),
) -> dict:
    """给对话改名。用户起的名字固定下来，不会再被自动标题覆盖；权限核验优先于空标题校验。"""
    task = await _own_task(session, task_id, user, write=True)
    title = " ".join((body.title if body and body.title is not None else "").split())
    if not title:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "名字不能是空的")
    task.title = title
    task.title_custom = True
    await session.commit()
    return {"id": str(task_id), "title": task.title}


@user_router.post("/tasks/{task_id}/restore")
async def restore_own_analysis_task(task_id: uuid.UUID, user: UserDep, session: SessionDep) -> dict:
    """把归档里的对话恢复到对话列表，聊天记录、文件、对照结果和报告都原样在。"""
    task = await _own_task(session, task_id, user, write=True)
    if task.archived_at is None:
        return {"restored": True, "id": str(task_id)}
    task.archived_at = None
    task.archived_by = None
    side_chat = await _project_side_chat(session, task)
    if side_chat is not None:
        side_chat.archived_at = side_chat.archived_by = None
    await session.commit()
    return {"restored": True, "id": str(task_id), "conversation_code": format_conversation_code(task.id, task.created_at)}


async def _project_side_chat(session: AsyncSession, task: AnalysisTask) -> AnalysisTask | None:
    """The conversation opened beside a documents project (not a consulting chat that went on to create
    the project — that one stays the user's own conversation)."""
    if task.kind != "documents":
        return None
    chat_id = (task.facts_json or {}).get("_chat")
    chat = await session.get(AnalysisTask, uuid.UUID(chat_id)) if chat_id else None
    if chat is None or chat.org_id != task.org_id:
        return None
    facts = chat.facts_json or {}
    side = facts.get("_project_side") or (chat.title_custom and (chat.title or "").endswith(" · 对话"))
    return chat if side and facts.get("_doc_project") == str(task.id) else None


@user_router.post("/tasks/{task_id}/facts", response_model=PublicTaskDetail)
async def update_user_task_facts(
    task_id: uuid.UUID,
    body: UpdateFactsIn,
    user: UserDep,
    session: SessionDep,
) -> AnalysisTaskDetailOut:
    """补充追问事实；当事实完备度达到 100% 时自动启动多智能体协同生成八维度报告。"""
    task = await session.scalar(
        select(AnalysisTask).where(AnalysisTask.id == task_id).with_for_update()
    )
    if task is None or task.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该合规评估项目")

    if task.status == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="分析正在运行中,请等待完成后再补充事实"
        )
    if task.status == "awaiting_user_facts":
        from app.analysis.user_facts import answer_user_facts

        try:
            resume = await answer_user_facts(session, task, body.facts, body.question_ids)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if resume and body.auto_run_if_sufficient:
            await _start(session, task)
        return await _to_task_detail(session, task)
    if body.question_ids:
        raise HTTPException(409, "该追问已完成，请勿重复提交旧回答")
    merged = dict(task.facts_json or {})
    for k, v in (body.facts or {}).items():
        if v is not None and str(v).strip():
            merged[k] = str(v).strip()

    score, missing = evaluate_fact_sufficiency(task.country_code, merged)
    task.facts_json = merged
    task.missing_facts_json = missing
    task.sufficiency_score = score
    await session.commit()
    await session.refresh(task)

    if score >= 100 and body.auto_run_if_sufficient:
        await _start(session, task)

    return await _to_task_detail(session, task)


@user_router.post("/tasks/{task_id}/run", response_model=PublicTaskDetail)
async def run_user_analysis_task(
    task_id: uuid.UUID,
    user: UserDep,
    session: SessionDep,
) -> AnalysisTaskDetailOut:
    task = await session.get(AnalysisTask, task_id)
    if task is None or task.org_id != user.org_id or not _visible_to_user(task, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该合规评估项目")
    if task.sufficiency_score < 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必填业务事实尚未补充完整，请先完成追问补充后再启动多智能体分析",
        )
    if task.status == "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="分析正在运行中")
    await _start(session, task)
    return await _to_task_detail(session, task)


@user_router.post("/tasks/{task_id}/reports/edit", response_model=ReportVersionOut)
async def edit_user_report_version(
    task_id: uuid.UUID,
    body: ReportEditIn,
    user: UserDep,
    session: SessionDep,
) -> ReportVersionOut:
    """在线编辑报告章节并保存为新的不可变版本(保留部分样稿标识)。"""
    task = await session.get(AnalysisTask, task_id)
    if task is None or task.org_id != user.org_id or not _visible_to_user(task, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该合规评估项目")

    rvs = list(
        (
            await session.execute(
                select(ReportVersion)
                .where(ReportVersion.task_id == task.id)
                .order_by(ReportVersion.version_no.desc())
            )
        )
        .scalars()
        .all()
    )
    if not rvs:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前任务尚无报告版本")

    latest = rvs[0]
    next_ver = latest.version_no + 1
    new_dims: list[dict] = []
    for d in latest.dimensions_json or []:
        copy_d = dict(d)
        code = copy_d.get("dimension_code", "")
        if code in (body.dimension_updates or {}):
            submitted = body.dimension_updates[code]
            # 文字没变（页面把去掉加粗标记的原文带了回来）就保留原文，不丢加粗
            if not same_text(submitted, copy_d.get("content_markdown")):
                copy_d["content_markdown"] = submitted
        new_dims.append(copy_d)

    carried = copy.deepcopy(latest.consulting_json) if latest.consulting_json else None
    if carried is not None:
        # 结构化的咨询内容原样保留；人工改过的维度/摘要登记下来，展示与导出时以用户文字为准
        edited = carried.setdefault("edited", {"dimensions": [], "executive_summary": False})
        old_text = {
            d.get("dimension_code"): d.get("content_markdown", "")
            for d in latest.dimensions_json or []
        }
        for code, text in (body.dimension_updates or {}).items():
            # 提交了但文字没变（只是把原文原样带回来）不算用户修订
            if same_text(text, old_text.get(code)):
                continue
            if code not in edited["dimensions"]:
                edited["dimensions"].append(code)
        summary_changed = bool(
            (body.executive_summary or "").strip()
            and not same_text(body.executive_summary, latest.executive_summary)
        )
        if summary_changed:
            edited["executive_summary"] = True
    new_rv = ReportVersion(
        citation_policy=latest.citation_policy,
        task_id=task.id,
        version_no=next_ver,
        status="analysis_failed"
        if latest.status == "analysis_failed"
        else "partial_sample"
        if latest.is_partial_sample
        else "edited",
        is_partial_sample=latest.is_partial_sample,
        change_note=body.change_note.strip() or f"人工修订版本 v{next_ver}",
        executive_summary=(
            body.executive_summary.strip()
            if (body.executive_summary or "").strip()
            and not same_text(body.executive_summary, latest.executive_summary)
            else latest.executive_summary
        ),
        dimensions_json=new_dims,
        stale_impacts_json=latest.stale_impacts_json or [],
        consulting_json=carried,
        created_by=user.user_id,
    )
    session.add(new_rv)
    await session.flush()
    from app.updates.service import link_report, reconcile_impacts

    await link_report(session, task, new_rv)
    await reconcile_impacts(session, task_ids=[task.id])
    await session.commit()
    await session.refresh(new_rv)

    return ReportVersionOut(
        regulatory_updates=await report_updates(session, new_rv.id),
        citation_policy=new_rv.citation_policy or "legacy_unrecorded",
        id=new_rv.id,
        task_id=new_rv.task_id,
        version_no=new_rv.version_no,
        status=new_rv.status,
        is_partial_sample=new_rv.is_partial_sample,
        change_note=new_rv.change_note,
        executive_summary=new_rv.executive_summary,
        dimensions=[ReportDimensionOut(**d) for d in (new_rv.dimensions_json or [])],
        stale_impacts=[StaleImpactAlertOut(**s) for s in (new_rv.stale_impacts_json or [])],
        consulting=upgrade_legacy(new_rv.consulting_json),
        created_at=new_rv.created_at,
    )


@user_router.get("/tasks/{task_id}/reports/diff", response_model=ReportDiffOut)
async def diff_user_report_versions(
    task_id: uuid.UUID,
    user: UserDep,
    session: SessionDep,
    from_version: int = Query(default=1, ge=1),
    to_version: int = Query(default=2, ge=1),
) -> ReportDiffOut:
    task = await session.get(AnalysisTask, task_id)
    if task is None or task.org_id != user.org_id or not _visible_to_user(task, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该合规评估项目")

    rv_from = (
        await session.execute(
            select(ReportVersion).where(
                ReportVersion.task_id == task.id,
                ReportVersion.version_no == from_version,
            )
        )
    ).scalar_one_or_none()
    rv_to = (
        await session.execute(
            select(ReportVersion).where(
                ReportVersion.task_id == task.id,
                ReportVersion.version_no == to_version,
            )
        )
    ).scalar_one_or_none()
    if rv_from is None or rv_to is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对比的报告版本不存在")

    from_map = {d["dimension_code"]: d for d in (rv_from.dimensions_json or [])}
    to_map = {d["dimension_code"]: d for d in (rv_to.dimensions_json or [])}

    diffs: list[DimensionDiffItemOut] = []
    for code, to_d in to_map.items():
        from_d = from_map.get(code, {})
        f_md = str(from_d.get("content_markdown", ""))
        t_md = str(to_d.get("content_markdown", ""))
        diffs.append(
            DimensionDiffItemOut(
                dimension_code=code,
                dimension_title=str(to_d.get("dimension_title", code)),
                changed=(f_md != t_md),
                from_markdown=f_md,
                to_markdown=t_md,
            )
        )

    return ReportDiffOut(
        task_id=task.id,
        from_version=from_version,
        to_version=to_version,
        summary_changed=(rv_from.executive_summary != rv_to.executive_summary),
        from_summary=rv_from.executive_summary,
        to_summary=rv_to.executive_summary,
        dimension_diffs=diffs,
    )


@user_router.get("/tasks/{task_id}/reports/{version_no}/export.{ext}")
async def export_user_report(
    task_id: uuid.UUID,
    version_no: int,
    ext: Literal["docx", "pdf", "zip"],
    user: UserDep,
    session: SessionDep,
    dimension: Literal["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"] | None = None,
) -> Response:
    task = await session.get(AnalysisTask, task_id)
    if task is None or task.org_id != user.org_id or not _visible_to_user(task, user):
        raise HTTPException(404, "未找到该合规评估项目")
    rv = await session.scalar(
        select(ReportVersion).where(
            ReportVersion.task_id == task.id, ReportVersion.version_no == version_no
        )
    )
    if rv is None:
        raise HTTPException(404, "报告版本不存在")
    if rv.status == "analysis_failed":
        raise HTTPException(409, "报告分析技术失败，不能导出为部分样稿，请重试分析")
    if await report_held_citation_ids(session, rv):
        raise HTTPException(409, "报告引用了来源绑定或页级解析待核对的条款，请核查后重新分析再导出")
    if ext == "zip" and dimension:
        raise HTTPException(400, "ZIP 包含整份与八个维度，请勿同时指定单个维度")
    codes = {d["dimension_code"] for d in rv.dimensions_json or []}
    if dimension and dimension not in codes:
        raise HTTPException(404, "该报告版本没有此维度")
    if dimension and dimension not in exportable_dimensions(rv):
        raise HTTPException(404, "该国法规中没有这个方面的依据，不单独成文")
    if ext == "zip" and not {f"D{i}" for i in range(1, 9)} <= codes:
        raise HTTPException(409, "历史报告未保存全部八个维度，不能伪造拆分文件")
    export_view = SimpleNamespace(
        id=rv.id,
        version_no=rv.version_no,
        created_at=rv.created_at,
        is_partial_sample=rv.is_partial_sample,
        dimensions_json=rv.dimensions_json,
        executive_summary=rv.executive_summary,
        citation_policy=rv.citation_policy,
        consulting_json=upgrade_legacy(rv.consulting_json),
        regulatory_updates=await report_updates(session, rv.id),
    )
    from starlette.concurrency import run_in_threadpool

    # Native PDF shaping must not block chat progress or SSE heartbeats.
    started = perf_counter()
    cache_hit = False
    if ext == "zip":
        data = await run_in_threadpool(build_report_zip, task, export_view)
    elif ext == "docx":
        data = await run_in_threadpool(build_report_docx, task, export_view, dimension)
    else:
        data, cache_hit = await run_in_threadpool(
            cached_pdf, task, export_view, dimension, build_report_pdf
        )
    media = {
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
        "zip": "application/zip",
    }
    return Response(
        data,
        media_type=media[ext],
        headers={
            "Content-Disposition": content_disposition(export_filename(task, rv, ext, dimension)),
            "Access-Control-Expose-Headers": "Content-Disposition",
            "Cache-Control": "private, no-store",
            "Server-Timing": f"export;dur={(perf_counter() - started) * 1000:.1f}",
            "X-Export-Cache": "hit" if cache_hit else "miss",
        },
    )


@user_router.get("/tasks/{task_id}/chat-export.{fmt}")
async def export_chat_transcript(
    task_id: uuid.UUID,
    fmt: Literal["docx", "pdf"],
    request: Request,
    user: UserDep,
    session: SessionDep,
) -> Response:
    """将问答式法律咨询会话导出为正式《合规咨询问答纪要》（支持 Word / PDF）。"""
    import asyncio
    from datetime import timedelta, timezone

    from app.analysis.document_templates import Rendered, build_docx, build_pdf
    from app.analysis.report_exporter import named
    from app.analysis.schemas import format_conversation_code
    from app.analysis.workflow_engine import SCENARIO_LABELS, country_name_zh
    from app.audit.service import set_detail

    if not user.can_export:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "这个账号没有下载权限，请联系平台开通")
    task = await session.get(AnalysisTask, task_id)
    if task is None or task.org_id != user.org_id or not _visible_to_user(task, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该合规咨询会话")
    msgs = list(
        (
            await session.scalars(
                select(AnalysisMessage)
                .where(AnalysisMessage.task_id == task.id, AnalysisMessage.role.in_(("user", "assistant")))
                .order_by(AnalysisMessage.seq_no.asc())
            )
        ).all()
    )
    if not msgs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="当前咨询对话暂无问答记录可导出")

    tz_cn = timezone(timedelta(hours=8))
    task_title = (task.title or "合规咨询会话").strip()
    conv_code = format_conversation_code(task.id, task.created_at)
    cc_label = country_name_zh(task.country_code) if task.country_code and task.country_code != PLACEHOLDER_COUNTRY else "跨国综合合规咨询"
    scen_label = SCENARIO_LABELS.get(task.scenario_type, task.scenario_type) or "智能合规问答咨询"
    created_str = task.created_at.astimezone(tz_cn).strftime("%Y-%m-%d %H:%M") if task.created_at else ""

    meta_rows = [
        ["咨询主题", task_title],
        ["会话编号", conv_code],
        ["涉及法域", cc_label],
        ["业务场景", scen_label],
    ]
    if (task.vehicle_model or "").strip():
        meta_rows.append(["关联车型", task.vehicle_model.strip()])
    if created_str:
        meta_rows.append(["创建时间", created_str])

    blocks: list[dict[str, Any]] = [
        {"type": "title", "text": "合规咨询问答纪要"},
        {"type": "heading", "text": "一、会话基本信息"},
        {"type": "table", "head": ["项目", "内容"], "widths": [24, 76], "rows": meta_rows},
        {"type": "heading", "text": "二、咨询问答记录"},
    ]
    q_idx = 0
    for m in msgs:
        content = (m.content or "").strip()
        if not content:
            continue
        ts_str = f"（{m.created_at.astimezone(tz_cn).strftime('%Y-%m-%d %H:%M')}）" if m.created_at else ""
        if m.role == "user":
            q_idx += 1
            blocks.append({"type": "subheading", "text": f"提问 {q_idx}{ts_str}"})
            blocks.append({"type": "paragraph", "text": content})
        else:
            head_txt = f"顾问解答 {q_idx}{ts_str}" if q_idx else f"合规顾问说明{ts_str}"
            blocks.append({"type": "subheading", "text": head_txt})
            para_buf: list[str] = []
            for raw_ln in content.splitlines():
                ln = raw_ln.strip()
                if not ln:
                    if para_buf:
                        blocks.append({"type": "paragraph", "text": "\n".join(para_buf)})
                        para_buf = []
                    continue
                m_md_h = re.match(r"^#{1,6}\s+(.+)$", ln)
                if m_md_h:
                    if para_buf:
                        blocks.append({"type": "paragraph", "text": "\n".join(para_buf)})
                        para_buf = []
                    h_clean = re.sub(r"^\*\*(.+)\*\*$", r"\1", m_md_h.group(1).strip())
                    blocks.append({"type": "subheading", "text": h_clean})
                elif re.match(r"^[-*]\s+.+$", ln):
                    para_buf.append("• " + re.sub(r"^[-*]\s+", "", ln))
                elif re.fullmatch(r"-{3,}|\*{3,}", ln):
                    continue
                else:
                    para_buf.append(ln)
            if para_buf:
                blocks.append({"type": "paragraph", "text": "\n".join(para_buf)})

    blocks.append({"type": "heading", "text": "三、使用说明"})
    blocks.append(
        {
            "type": "paragraph",
            "text": "本纪要根据车研数合智能合规咨询会话记录整理生成，供企业内部合规研判与留档参考；涉及正式监管申报或对外法律承诺时，请结合主管机关最新官方文本核定。",
        }
    )

    await session.close()
    rendered = Rendered(blocks=blocks)
    ctx_header = f"{conv_code} | 合规咨询问答纪要"
    if fmt == "pdf":
        data = await asyncio.to_thread(build_pdf, rendered, ctx_header)
        media = "application/pdf"
    else:
        data = await asyncio.to_thread(build_docx, rendered, ctx_header)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    set_detail(request, summary=f"导出合规咨询问答纪要“{task_title}”")
    fname = named(task_title, "合规咨询问答纪要", ext=fmt)
    return Response(data, media_type=media, headers={"Content-Disposition": content_disposition(fname)})


def _event_cursor(last_event_id):
    try:
        value = int(last_event_id or "0")
        if not 0 <= value <= 9223372036854775807:
            raise ValueError
        return value
    except ValueError as exc:
        raise HTTPException(400, "Last-Event-ID 不合法") from exc


@user_router.get("/tasks/{task_id}/events")
async def user_task_events(
    task_id: uuid.UUID,
    request: Request,
    user: UserDep,
    session: SessionDep,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    cursor = _event_cursor(last_event_id)
    await validate_reader(session, user, task_id)
    engine = session.bind
    await session.rollback()  # No request transaction/connection held by a long-lived stream.
    return StreamingResponse(
        stream_events(request, engine, user, cursor, task_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@admin_router.get("/events")
async def admin_task_events(
    request: Request,
    admin: AdminDep,
    session: SessionDep,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    cursor = _event_cursor(last_event_id)
    await validate_reader(session, admin)
    if last_event_id is None:
        cursor = await session.scalar(select(func.max(AnalysisEvent.id))) or 0
    engine = session.bind
    await session.rollback()
    return StreamingResponse(
        stream_events(request, engine, admin, cursor),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@user_router.get("/clauses/{clause_id}/citation-card")
async def get_clause_citation_card(
    clause_id: uuid.UUID,
    _: UserDep,
    session: SessionDep,
) -> dict:
    """点击报告引用角标或分析矩阵条款时，返回原文、译文、版本及 1 跳关联图谱。"""
    row = (
        await session.execute(
            select(Clause, DocumentVersion, Document)
            .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(Clause.id == clause_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="引用条款不存在")

    clause, ver, doc = row
    from app.ingestion.safety import document_is_held

    if await document_is_held(session, doc.id):
        raise HTTPException(409, "此引用的原文绑定异常已隔离，请管理员核实来源后重做分析")
    from app.ingestion.layout_safety import layout_held_clause_ids

    layout_pending = bool(await layout_held_clause_ids(session, [clause.id]))
    translation_rows = list(
        (
            await session.execute(
                select(ClauseTranslation)
                .where(
                    ClauseTranslation.clause_id == clause.id,
                    ClauseTranslation.is_current.is_(True),
                    ClauseTranslation.review_status.in_(
                        ("machine_translated", "pending_review", "approved")
                    ),
                    ClauseTranslation.quality_check_passed.is_(True),
                )
                .order_by(ClauseTranslation.updated_at.desc())
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    tr = translation_rows[0] if len(translation_rows) == 1 else None
    translation_ambiguous = len(translation_rows) > 1

    valid_tr_text = (
        tr.translated_text
        if (
            tr is not None
            and tr.review_status not in ("not_translated", "translation_failed", "superseded")
            and (tr.translated_text or "").strip()
        )
        else ""
    )
    from app.knowledge.business_view import topic_cards
    from app.knowledge.legal_terms import current_definitions, term_payload

    return {
        "clause_id": str(clause.id),
        "document_id": str(doc.id),
        "document_title": doc.title,
        "country_code": doc.country_code,
        "clause_number": clause.clause_number or f"#{clause.ordinal}",
        "heading": clause.heading,
        "section_path": clause.section_path,
        "version_no": ver.version_no,
        "source_url": doc.source_url,
        "original_text": clause.original_text,
        "translated_text": valid_tr_text,
        "review_status": "approved"
        if (
            not layout_pending
            and ver.review_status == "approved"
            and not clause.needs_review
            and ((tr and tr.review_status == "approved") or clause.language.startswith("zh"))
        )
        else "pending_review"
        if tr
        else "not_translated",
        "source_integrity_alerts": (
            ["本页存在待核对解析修订；以下仅保留旧原文和译文，禁止作新的法律依据引用。"]
            if layout_pending
            else []
        )
        + (
            ["同一条款存在多个现行译文，尚未消歧；保留原文，不任意选取一个译文。"]
            if translation_ambiguous
            else []
        ),
        "source_review_status": "approved"
        if ver.review_status == "approved" and not clause.needs_review
        else "pending_review",
        "translation_review_status": tr.review_status
        if tr
        else "ambiguous_current"
        if translation_ambiguous
        else "not_translated",
        "usable_as_legal_basis": bool(
            not layout_pending
            and ver.review_status == "approved"
            and not clause.needs_review
            and ((tr and tr.review_status == "approved") or clause.language.startswith("zh"))
        ),
        "related_requirements": await topic_cards(session, doc.country_code, clause_id=clause.id),
        "terms": [
            term_payload(t)
            for t in await current_definitions(session, doc.country_code, doc.id, ver.id)
        ],
    }


@user_router.get("/chats/guided-options")
async def guided_options(user: UserDep, session: SessionDep) -> dict[str, list[dict[str, str]]]:
    """引导式入口的可选项：国家与业务场景取自评估流程本身，前端不另写一份。"""
    return {
        "countries": await active_overseas_countries(session),
        "functions": [{"key": k, "label": v} for k, v in SCENARIO_LABELS.items()],
        "scenes": [{"key": k, "label": v} for k, v in BUSINESS_SCENES.items()],
    }


@user_router.post("/chats", response_model=PublicTaskDetail, status_code=201)
async def create_chat(
    user: UserDep, session: SessionDep, body: GuidedStartIn | None = None, advice: bool = False
) -> AnalysisTaskDetailOut:
    """新建一次对话式评估:助手先问候,并开始逐项提问。

    带引导信息（国家、车型、场景）时这些槽位已填好，后面的追问与自由对话完全共用同一流程。"""
    if body is not None and not (
        body.country_code or body.vehicle_model or body.scenario_type or body.business_scene
    ):
        body = None  # 空请求体 = 直接对话
    if user.role == Role.VIEWER and body is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "查看账号只能新建咨询问答对话，不能发起合规评估任务")
    if body is not None:
        allowed_codes = {c["code"] for c in await active_overseas_countries(session)}
        cc = (body.country_code or "").upper()
        if cc not in allowed_codes:
            raise HTTPException(422, "目标国家不在支持范围内")
        body.country_code = cc
        if body.scenario_type not in SCENARIO_LABELS:
            raise HTTPException(422, "功能不在支持范围内")
        if body.business_scene not in BUSINESS_SCENES:
            raise HTTPException(422, "业务场景不在支持范围内")
        if len((body.vehicle_model or "").strip()) < 2:
            raise HTTPException(422, "请填写车型或车队名称")
    initial_facts: dict = {"business_scene": BUSINESS_SCENES[body.business_scene]} if body else {}
    if user.role == Role.VIEWER:
        initial_facts[VIEWER_PRIVATE_KEY] = str(user.user_id)
    task = AnalysisTask(
        org_id=user.org_id,
        created_by=user.user_id,
        title="新对话",
        kind="advice" if user.role == Role.VIEWER or (advice and not body) or not _assessment_chat() else "chat",  # advice=true：咨询助手，只答、只引导，不追问；否则是逐项收集信息的评估对话
        country_code=body.country_code if body else PLACEHOLDER_COUNTRY,
        vehicle_model=body.vehicle_model.strip() if body else "",
        scenario_type=body.scenario_type if body else "",
        status="collecting_facts",
        facts_json=initial_facts,
        missing_facts_json=[],
        sufficiency_score=0,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    await start_chat(session, task)
    await session.refresh(task)
    return await _to_task_detail(session, task, viewer_id=user.user_id)


@user_router.post("/tasks/{task_id}/messages", response_model=PublicTaskDetail)
async def post_chat_message(
    task_id: uuid.UUID,
    body: ChatMessageIn,
    user: UserDep,
    session: SessionDep,
    copilot: bool = False,
) -> AnalysisTaskDetailOut:
    """用户在聊天框发送一条消息:助手记录信息、继续追问,齐备后自动启动分析。"""
    session.info["_copilot_mode"] = bool(copilot)
    task = await session.scalar(
        select(AnalysisTask).where(AnalysisTask.id == task_id).with_for_update()
    )
    if task is None or task.org_id != user.org_id or not _visible_to_user(task, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对话不存在")
    if user.role == Role.VIEWER:
        if viewer_owner(task) != str(user.user_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "查看账号只能查看本企业的共享对话，不能在其中发言；请新建咨询对话提问")
        if body.attachment_ids:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "查看账号只能进行文字咨询问答，不能上传文件")
    if task.archived_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "这个对话在归档里，恢复后才能继续")
    try:
        await post_user_message(
            session, task, body.content, _start, str(body.question_id) if body.question_id else None,
            attachment_ids=body.attachment_ids,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await session.refresh(task)
    return await _to_task_detail(session, task, viewer_id=user.user_id)


@user_router.get("/benchmarks", response_model=BenchmarkSummaryOut)
async def get_user_benchmarks(
    _: UserDep,
    session: SessionDep,
) -> BenchmarkSummaryOut:
    await ensure_workflow_config(session)
    res = await run_retrieval_benchmarks(session)
    return BenchmarkSummaryOut(**res)
