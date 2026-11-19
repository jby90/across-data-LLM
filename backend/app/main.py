import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app import models_registry  # noqa: F401
from app.analysis.router import (
    admin_router as analysis_admin_router,
)
from app.analysis.router import (
    user_router as analysis_user_router,
)
from app.core.config import get_settings
from app.iam.router import admin_router, auth_router, user_router
from app.ingestion.router import router as documents_router
from app.knowledge.router import (
    admin_router as knowledge_admin_router,
)
from app.knowledge.router import (
    user_router as knowledge_user_router,
)
from app.sources.router import router as sites_router
from app.updates.router import admin_router as updates_router
from app.updates.router import user_router as notifications_router


@asynccontextmanager
async def _lifespan(_app):
    if get_settings().env == "prod":
        from app.core.db import get_engine
        from app.core.rls import validate_runtime_role

        await validate_runtime_role(get_engine())
    import asyncio
    import os

    from app.forms import service as forms_service
    from app.knowledge.cache_sync import keeper

    # 预先同步加载官方申报原表目录与栏位尺寸缓存，避免与启动后多线程 PDFium 预热竞争
    forms_service.catalog()

    # 用户端常用视图：启动时建好，库或图谱变化时后台重建（cache_sync）
    warm = asyncio.create_task(keeper()) if "PYTEST_CURRENT_TEST" not in os.environ else None
    yield
    if warm is not None:
        warm.cancel()
    from app.analysis.events import close_hubs

    await close_hubs()


def create_app() -> FastAPI:
    from app.audit.describe import install as install_audit_listener

    install_audit_listener()  # 审计：所有写库在同一处记下改了什么
    settings = get_settings()
    app = FastAPI(
        title="车研数合 API",
        version="0.1.0",
        lifespan=_lifespan,
        # 生产环境不暴露交互式文档
        docs_url="/api/docs" if settings.env == "dev" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.env == "dev" else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
    )

    @app.middleware("http")
    async def isolate_model_egress_context(request, call_next):
        from app.audit import describe as audit_describe
        from app.audit.service import record_request, wants_record
        from app.core.redaction import llm_scope
        from app.core.rls import database_scope

        audit_tokens = audit_describe.begin()
        try:
            with llm_scope(), database_scope():
                response = await call_next(request)
            if wants_record(request.method, request.url.path):
                await record_request(request, response.status_code)
        finally:
            audit_describe.end(audit_tokens)
        from app.knowledge.cache_sync import (
            after_admin_change,
            changes_admin_data,
            clear_user_caches,
        )

        if changes_admin_data(request.method, request.url.path, response.status_code):
            # 管理端改了内容：后台重建用户端视图；测试里直接清空，下一次请求即重算
            clear_user_caches() if "PYTEST_CURRENT_TEST" in os.environ else after_admin_change()
        return response

    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    @app.get("/api/health", tags=["system"])
    async def health(request: Request):
        import asyncio

        from sqlalchemy import text

        from app.core.db import get_session, get_sessionmaker
        from app.core.rls import system_scope

        async def _probe() -> None:
            override = request.app.dependency_overrides.get(get_session)
            with system_scope("healthcheck"):
                if override is not None:
                    gen = override()
                    try:
                        session = await anext(gen)
                        await session.execute(text("SELECT 1"))
                    finally:
                        try:
                            await gen.aclose()
                        except Exception:  # noqa: BLE001
                            pass
                else:
                    async with get_sessionmaker()() as session:
                        await session.execute(text("SELECT 1"))

        try:
            await asyncio.wait_for(_probe(), timeout=3.0)
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=503, content={"status": "degraded"})
        return {"status": "ok"}

    _FIELD_LABELS_ZH = {
        "title": "标题",
        "name": "名称",
        "name_zh": "中文名称",
        "email": "邮箱地址",
        "password": "密码",
        "old_password": "原密码",
        "new_password": "新密码",
        "display_name": "显示名称",
        "role": "角色类型",
        "org_id": "企业标识",
        "user_id": "用户标识",
        "task_id": "任务标识",
        "project_id": "项目标识",
        "document_id": "法规文档标识",
        "version_id": "版本标识",
        "clause_id": "条款标识",
        "focus_clause_id": "定位条款标识",
        "instance_id": "表单实例标识",
        "entry_id": "准入事项标识",
        "draft_id": "申报材料标识",
        "issue_id": "审核问题标识",
        "progress_id": "办理环节标识",
        "attachment_id": "附件标识",
        "target_id": "目标标识",
        "site_id": "站点标识",
        "run_id": "运行记录标识",
        "country": "国家或地区代码",
        "country_code": "国家或地区代码",
        "source_country": "数据发出方国家或地区",
        "recipient_country": "数据接收方国家或地区",
        "limit": "单页条数",
        "clause_limit": "条款每页条数",
        "offset": "分页偏移量",
        "clause_offset": "条款分页偏移量",
        "page": "页码",
        "include_archived": "是否包含归档项",
        "draft": "初稿标识",
        "annex": "附页续表标识",
        "inline": "在线预览标识",
        "fmt": "导出文件格式",
        "kind": "类型参数",
        "category": "分类筛选条件",
        "state": "状态值",
        "dimension": "评估维度",
        "published_date": "发布日期",
        "effective_date": "生效日期",
        "issued_on": "签发日期",
        "expires_on": "到期日期",
        "result_issued_on": "结果出具日期",
        "on": "日期",
        "number": "文号或编号",
        "question": "问题描述",
        "answer": "回复内容",
        "answers": "填报内容",
        "message": "消息内容",
        "content": "正文内容",
        "keyword": "检索关键词",
        "q": "检索词",
        "base": "基准版本号",
        "target": "对比版本号",
        "block": "修订段落序号",
        "filename": "文件名称",
    }

    _BANNED_VALIDATION_JARGON = re.compile(
        r"input should be|field required|pydantic|uuid|json|llm|ocr|rag|value error",
        re.IGNORECASE,
    )

    def _loc_label_zh(loc: tuple | list) -> str:
        parts = [str(p) for p in loc if p not in ("body", "query", "path", "header", "cookie")]
        if not parts:
            return "请求参数"
        leaf = parts[-1]
        if leaf.isdigit() and len(parts) >= 2:
            leaf = parts[-2]
        if leaf in _FIELD_LABELS_ZH:
            return _FIELD_LABELS_ZH[leaf]
        if leaf.endswith("_id") or leaf == "id":
            return "资源标识"
        if re.fullmatch(r"[A-Za-z0-9_.-]+", leaf):
            return f"参数“{leaf}”"
        return f"“{leaf}”"

    def _translate_one_validation_error(err: dict) -> str:
        etype = str(err.get("type") or "")
        loc = err.get("loc") or ()
        label = _loc_label_zh(loc)
        ctx = err.get("ctx") or {}
        raw_msg = str(err.get("msg") or "").strip()

        # 自定义校验器抛出的中文业务提示原样保留（剥离英文前缀并拦截黑话）
        if etype in ("value_error", "assertion_error") or raw_msg.startswith("Value error,"):
            cleaned = re.sub(r"^(?:Value error|Assertion failed),\s*", "", raw_msg).strip()
            has_cjk = bool(re.search(r"[\u4e00-\u9fff]", cleaned))
            if cleaned and has_cjk and not _BANNED_VALIDATION_JARGON.search(cleaned):
                return cleaned

        if etype == "json_invalid":
            return "提交的请求体结构格式不合法，请检查填写格式后重试"
        if etype in ("model_type", "model_attributes_type", "dict_type"):
            return f"{label}格式不正确，请提交有效的键值对象"
        if etype == "list_type":
            return f"{label}格式不正确，请提交列表格式"
        if etype == "missing":
            if list(loc) == ["body"]:
                return "请求体不能为空，请补充必填内容"
            return f"缺少必填项：{label}"
        if etype.startswith("uuid"):
            return f"{label}格式无效，请确认所访问的资源链接是否正确"
        if etype.startswith(("int_", "integer")):
            return f"{label}必须填写有效的整数"
        if etype.startswith(("float_", "decimal_")):
            return f"{label}必须填写有效的数值"
        if etype.startswith("bool"):
            return f"{label}取值无效，请传入合法的布尔开关值（是/否）"
        if etype == "string_type":
            return f"{label}必须为文本内容"
        if etype == "string_too_short":
            min_len = ctx.get("min_length")
            if min_len == 1 or min_len is None:
                return f"{label}不能为空"
            return f"{label}长度不足，至少需要 {min_len} 个字符"
        if etype == "string_too_long":
            max_len = ctx.get("max_length")
            return f"{label}长度超出限制" + (f"（最多 {max_len} 个字符）" if max_len else "")
        if etype == "too_short":
            min_len = ctx.get("min_length")
            return f"{label}数量不足" + (f"（至少需要 {min_len} 项）" if min_len else "")
        if etype == "too_long":
            max_len = ctx.get("max_length")
            return f"{label}数量超出上限" + (f"（最多 {max_len} 项）" if max_len else "")
        if etype == "string_pattern_mismatch":
            return f"{label}格式不符合规范要求"
        if etype in ("enum", "literal_error"):
            return f"{label}不在系统支持的取值范围内"
        if etype == "greater_than":
            return f"{label}必须大于 {ctx['gt']}" if "gt" in ctx else f"{label}数值过小"
        if etype == "greater_than_equal":
            return f"{label}不能小于 {ctx['ge']}" if "ge" in ctx else f"{label}数值过小"
        if etype == "less_than":
            return f"{label}必须小于 {ctx['lt']}" if "lt" in ctx else f"{label}数值超出上限"
        if etype == "less_than_equal":
            return f"{label}不能超过 {ctx['le']}" if "le" in ctx else f"{label}数值超出上限"
        if etype.startswith(("date", "time")):
            return f"{label}日期或时间格式无效，请使用标准日期格式（如 YYYY-MM-DD）"
        if etype.startswith("url"):
            return f"{label}网址格式无效，请填写以 http:// 或 https:// 开头的完整地址"
        if etype == "extra_forbidden":
            return f"包含未定义的提交项：{label}"
        return f"{label}填写格式有误，请检查后重试"

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request, exc: RequestValidationError):
        if request.url.path.startswith("/api/admin/knowledge/batch-import"):
            msg = "批量导入提交的数据格式有误，请检查结构化字段或表格列名称后重试"
            return JSONResponse(status_code=422, content={"detail": msg})
        seen: list[str] = []
        for err in exc.errors():
            item = _translate_one_validation_error(err)
            if item and item not in seen:
                seen.append(item)
        detail_msg = "；".join(seen[:3]) if seen else "请求参数格式有误，请检查后重试"
        return JSONResponse(status_code=422, content={"detail": detail_msg})

    app.include_router(auth_router)
    app.include_router(admin_router)
    from app.iam.cockpit_router import router as cockpit_router

    app.include_router(cockpit_router)
    app.include_router(user_router)
    app.include_router(sites_router)
    app.include_router(documents_router)
    app.include_router(knowledge_admin_router)
    app.include_router(knowledge_user_router)
    from app.knowledge.compare_router import router as knowledge_compare_router

    app.include_router(knowledge_compare_router)
    from app.knowledge.compare_router import admin_router as knowledge_compare_admin_router

    app.include_router(knowledge_compare_admin_router)
    from app.knowledge import whitepaper_router

    app.include_router(whitepaper_router.user_router)
    app.include_router(whitepaper_router.admin_router)
    from app.analysis import attachments_router

    app.include_router(attachments_router.router)
    from app.knowledge.term_routes import admin as term_admin
    from app.knowledge.term_routes import user as term_user

    app.include_router(term_admin)
    app.include_router(term_user)
    app.include_router(analysis_admin_router)
    app.include_router(analysis_user_router)
    from app.analysis import documents_router as compliance_documents

    app.include_router(compliance_documents.user_router)
    from app.analysis import doc_projects

    app.include_router(doc_projects.router)
    app.include_router(compliance_documents.admin_router)
    from app.opinion.router import router as opinion_router

    app.include_router(opinion_router)
    from app.standards.router import router as standards_router

    app.include_router(standards_router)
    from app.audit import router as audit_router

    app.include_router(audit_router.router)
    from app.forms import router as forms_router

    app.include_router(forms_router.router)
    from app.admission import router as admission

    app.include_router(admission.user_router)
    app.include_router(admission.admin_router)
    app.include_router(notifications_router)
    app.include_router(updates_router)

    return app


app = create_app()
