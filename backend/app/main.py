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

    import json as _json
    import logging as _logging
    import math as _math

    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from sqlalchemy.exc import DBAPIError as _SADBAPIError
    from sqlalchemy.exc import IntegrityError as _SAIntegrityError
    from starlette.exceptions import HTTPException as _StarletteHTTPException

    _SURROGATE_OR_NUL_RE = re.compile(r"[\x00\ud800-\udfff]")
    _RAW_BODY_SUSPECT_RE = re.compile(
        rb"\x00|\\u0000|\\ud[89a-f][0-9a-f]{2}|\b(?:NaN|Infinity|-Infinity)\b",
        re.IGNORECASE,
    )

    def _has_illegal_json_val(val: object) -> bool:
        stack: list[object] = [val]
        while stack:
            cur = stack.pop()
            if isinstance(cur, str):
                if _SURROGATE_OR_NUL_RE.search(cur):
                    return True
            elif isinstance(cur, float):
                if _math.isnan(cur) or _math.isinf(cur):
                    return True
            elif isinstance(cur, dict):
                for k, v in cur.items():
                    stack.append(k)
                    stack.append(v)
            elif isinstance(cur, list):
                stack.extend(cur)
        return False

    _NUL_ERR = {"detail": "请求参数包含非法空字符、控制字符或无效数值，请修改后重试"}
    _UTF8_ERR = {"detail": "提交的请求体字符编码无效，请使用标准 UTF-8 编码重试"}

    @app.middleware("http")
    async def isolate_model_egress_context(request, call_next):
        raw_qs: bytes = request.scope.get("query_string") or b""
        if (
            b"\x00" in raw_qs
            or b"%00" in raw_qs.lower()
            or bool(_SURROGATE_OR_NUL_RE.search(request.url.path))
        ):
            return JSONResponse(status_code=422, content=_NUL_ERR)
        media_type = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        is_binary_upload = (
            media_type.startswith("multipart/") or media_type == "application/octet-stream"
        )
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and not is_binary_upload:
            raw_body = await request.body()
            if raw_body:
                try:
                    decoded_body = raw_body.decode("utf-8")
                except UnicodeDecodeError:
                    return JSONResponse(status_code=422, content=_UTF8_ERR)
                if b"\x00" in raw_body or _SURROGATE_OR_NUL_RE.search(decoded_body):
                    return JSONResponse(status_code=422, content=_NUL_ERR)
                if _RAW_BODY_SUSPECT_RE.search(raw_body):
                    try:
                        parsed_json = _json.loads(raw_body)
                    except Exception:  # noqa: BLE001
                        parsed_json = None
                    if _has_illegal_json_val(parsed_json):
                        return JSONResponse(status_code=422, content=_NUL_ERR)

        from app.audit import describe as audit_describe
        from app.audit.service import record_request, wants_record
        from app.core.redaction import llm_scope
        from app.core.rls import database_scope

        audit_tokens = audit_describe.begin()
        try:
            try:
                with llm_scope(), database_scope():
                    response = await call_next(request)
            except Exception:  # noqa: BLE001
                _logging.getLogger(__name__).error(
                    "未预期服务端异常已兜底拦截: %s %s",
                    request.method,
                    request.url.path,
                    exc_info=True,
                )
                response = JSONResponse(
                    status_code=500,
                    content={"detail": "服务处理请求时遇到异常，请稍后重试"},
                )
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
        "version_no": "版本号",
        "from_version": "起始版本号",
        "to_version": "目标版本号",
        "block": "修订段落序号",
        "filename": "文件名称",
        "llm_redaction_terms": "脱敏词条列表",
        "ocr_required": "扫描识别标识",
        "ocr_confidence": "识别置信度",
        "terms": "词条列表",
        "recipients": "收件人列表",
        "definition": "模板结构定义",
        "entry_url": "采集入口网址",
        "terms_url": "使用条款网址",
        "publisher": "发布机构",
        "source_term": "外文原词",
        "target_zh": "标准中文译名",
        "vehicle_model": "车型标识",
        "scenario_type": "业务场景类型",
        "ticket": "登录凭证",
        "decision": "审核结论",
        "translated_text": "译文正文",
        "profile": "事项配置",
    }

    from app.core.jargon import contains_engineering_jargon, scrub_engineering_jargon

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
        if re.search(r"[\u4e00-\u9fff]", leaf) and not contains_engineering_jargon(leaf):
            return f"“{leaf}”"
        return "该提交字段"

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
            if cleaned and has_cjk and not contains_engineering_jargon(cleaned):
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
            return "提交了系统未定义的扩展字段，请检查请求参数"
        return f"{label}填写格式有误，请检查后重试"

    _DEFAULT_HTTP_ERRORS_ZH = {
        400: "请求内容格式不正确，请检查提交参数后重试",
        401: "未登录或登录已失效",
        403: "当前账号无权执行该操作",
        404: "请求的接口或资源不存在",
        405: "当前接口不支持该请求方式",
        413: "提交的数据体积超出限制",
        422: "请求参数格式有误，请检查后重试",
        429: "操作过于频繁，请稍后再试",
    }
    _ENGLISH_HTTP_DEFAULTS = frozenset(
        {
            "Not Found",
            "Method Not Allowed",
            "Bad Request",
            "Unauthorized",
            "Forbidden",
            "There was an error parsing the body",
            "Malformed JSON",
        }
    )

    def _scrub_http_detail_zh(text: str) -> str:
        return scrub_engineering_jargon(
            text,
            ocr_zh="扫描文字识别",
            llm_zh="智能分析",
            rag_zh="条文检索",
            json_zh="结构化数据",
            uuid_zh="编号",
            pydantic_zh="格式核验",
            cjk_zh="中文汉字",
        )

    _RAW_ENGINE_DETAIL_RE = re.compile(
        r"\d+\s+validation\s+errors?\s+for\b|"
        r"\[type=[a-z0-9_]+,\s*input_value=|"
        r"errors\.pydantic\.dev|"
        r"Traceback\s*\(most recent call last\)|"
        r"invalid literal for int\(\)|"
        r"could not convert string to float",
        re.IGNORECASE,
    )

    @app.exception_handler(_StarletteHTTPException)
    async def handle_http_exception(_request, exc: _StarletteHTTPException):
        detail = exc.detail
        headers = getattr(exc, "headers", None)
        if (
            isinstance(detail, str)
            and detail not in _ENGLISH_HTTP_DEFAULTS
            and not _RAW_ENGINE_DETAIL_RE.search(detail)
        ):
            if re.search(r"[\u4e00-\u9fff]", detail):
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": _scrub_http_detail_zh(detail)},
                    headers=headers,
                )
        msg = _DEFAULT_HTTP_ERRORS_ZH.get(exc.status_code, "请求无法完成，请稍后重试")
        return JSONResponse(status_code=exc.status_code, content={"detail": msg}, headers=headers)

    @app.exception_handler(_SAIntegrityError)
    async def handle_db_integrity_error(request, exc: _SAIntegrityError):
        _logging.getLogger(__name__).warning(
            "数据库约束冲突已拦截: %s %s", request.method, request.url.path, exc_info=True
        )
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", "") or ""
        if sqlstate == "23503":
            return JSONResponse(
                status_code=409,
                content={"detail": "关联的资源不存在或仍被业务记录引用，请刷新页面后重试"},
            )
        return JSONResponse(
            status_code=409,
            content={"detail": "数据状态已发生变更或存在重复提交，请刷新后重试"},
        )

    @app.exception_handler(_SADBAPIError)
    async def handle_db_data_error(request, exc: _SADBAPIError):
        from sqlalchemy.exc import DataError as _SADataError

        orig = getattr(exc, "orig", None)
        orig_cls = type(orig).__name__ if orig is not None else ""
        orig_msg = str(orig or exc).lower()
        immutable_tokens = (
            "immutable",
            "append_only",
            "no_change",
            "禁止修改",
            "禁止删除",
            "严禁修改",
            "严禁删除",
            "不可变审计存证",
            "不可修改",
            "不可删除",
            "不可单独删除",
        )
        protected_audit_tables = (
            "audit_logs",
            "llm_call_audit",
            "source_fetch_observations",
            "version_changes",
            "knowledge_audit_records",
            "document_edits",
        )
        if any(k in orig_msg for k in immutable_tokens) or (
            orig_cls == "InsufficientPrivilegeError"
            and any(tbl in orig_msg for tbl in protected_audit_tables)
        ):
            return JSONResponse(
                status_code=409,
                content={"detail": "审计或存证记录受防篡改保护，禁止修改或删除"},
            )
        if (
            isinstance(exc, _SADataError)
            or orig_cls in ("DataError", "OverflowError", "InvalidDatetimeFormatError")
            or any(
                k in orig_msg
                for k in (
                    "out of range",
                    "value out of int",
                    "invalid input",
                    "character not in repertoire",
                    "null byte",
                    "numeric field overflow",
                    "date/time field value out of range",
                )
            )
        ):
            _logging.getLogger(__name__).warning(
                "数据库参数边界异常已拦截: %s %s", request.method, request.url.path, exc_info=True
            )
            return JSONResponse(
                status_code=422,
                content={"detail": "提交的数据或时间范围超出有效界限，请修改后重试"},
            )
        _logging.getLogger(__name__).error(
            "数据库运行期异常已拦截: %s %s (%s)",
            request.method,
            request.url.path,
            orig_cls or type(exc).__name__,
            exc_info=True,
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "数据库服务繁忙或暂时不可用，请稍后重试"},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(request, exc: Exception):
        _logging.getLogger(__name__).error(
            "未捕获异常已兜底转换: %s %s (%s)",
            request.method,
            request.url.path,
            type(exc).__name__,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "服务处理请求时遇到异常，请稍后重试"},
        )

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
