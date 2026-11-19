# ruff: noqa: E501
"""Audit trail: every login, every change and every export is written once, never edited.

One HTTP middleware covers all endpoints, so a new endpoint is audited without extra code. An
endpoint may add detail (a readable summary, the content before and after a change) through
``set_detail``; without it the record still carries who, when, from where, what route and the
outcome."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from typing import Any

import jwt
from fastapi import Request

logger = logging.getLogger(__name__)

_SECRET_KEYS = re.compile(r"pass|secret|token|key|ticket", re.IGNORECASE)


def classify(method: str, path: str) -> tuple[str, str]:
    """(category, action). category: login / export / delete / change. The action is the route's
    readable name (``describe.ROUTE_ACTIONS``), never an address."""
    from app.audit.describe import action_name

    if re.search(r"^/api/auth/(user|admin)/login$", path):
        return "login", "登录"
    if path in ("/api/auth/portal/exchange", "/api/auth/admin/exchange"):
        return "login", action_name(method, path)
    if (
        "/export" in path
        or "/chat-export" in path
        or path.startswith("/api/admin/audit-logs")
        or re.search(r"^/api/admission/drafts/[^/]+/forms/|/official(?:-forms)?/[^/]+/(?:original|blank)|/blank(?:[-./]|$)", path)
    ):
        return "export", action_name("GET" if method == "GET" else method, path)
    return ("delete" if method == "DELETE" else "change"), action_name(method, path)


def display_action(category: str, action: str, method: str, path: str) -> str:
    """Older records named some actions by their address or only by module (“新增：合规评估”);
    the list shows the readable name worked out from the same method and address. The stored
    record itself is never changed."""
    if category == "login" or not path:
        return action
    if (
        "/api/" in (action or "")
        or "：" in (action or "")
        or action == "导出文件"
        or "EDPB" in (action or "")
        or "维度" in (action or "")
    ):
        return classify(method, path)[1]
    return action


def wants_record(method: str, path: str) -> bool:
    if not path.startswith("/api/") or path == "/api/health":
        return False
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        return not path.endswith("/auth/refresh")
    return method == "GET" and (
        "/export" in path
        or "/chat-export" in path
        or path.startswith("/api/admin/audit-logs/export")
        or bool(re.search(r"^/api/admission/drafts/[^/]+/forms/", path))
    )


def client_ip(request: Request) -> str:
    """The address the connection came from. A reverse proxy's X-Forwarded-For is honoured only
    when the platform is configured to sit behind one (trusted_proxies), never from arbitrary
    clients."""
    from app.core.config import get_settings

    peer = request.client.host if request.client else ""
    trusted = getattr(get_settings(), "trusted_proxies", None) or []
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded and peer in trusted:
        return forwarded.split(",")[0].strip()[:64]
    return peer[:64]


def set_detail(request: Request, *, summary: str = "", before: Any = None, after: Any = None) -> None:
    """Let an endpoint describe its change: a readable summary and the content before/after."""
    request.state.audit_detail = {"summary": summary, "before": before, "after": after}


def redact(value: Any, depth: int = 0) -> Any:
    """Detail payloads never carry credentials: keys that look secret are masked."""
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        return {
            k: ("***" if _SECRET_KEYS.search(str(k)) else redact(v, depth + 1))
            for k, v in list(value.items())[:60]
        }
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in list(value)[:60]]
    if isinstance(value, str) and len(value) > 4000:
        return value[:4000] + "…"
    return value


def _actor_claims(request: Request) -> tuple[uuid.UUID | None, str]:
    from app.core.security import AUD_ADMIN, AUD_USER, decode_access_token

    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None, ""
    token = header[7:].strip()
    for audience in (AUD_USER, AUD_ADMIN):
        try:
            claims = decode_access_token(token, audience)
            return uuid.UUID(claims["sub"]), str(claims.get("role", ""))
        except (jwt.PyJWTError, ValueError, KeyError):
            continue
    return None, ""


def _hash(row: dict) -> str:
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


async def record_request(request: Request, status_code: int) -> None:
    """Write the audit row for a finished request. Never raises into the request: a failure is
    logged loudly (an audit gap must be visible), but the user's action has already happened."""
    from sqlalchemy import select

    from app.audit.models import AuditLog
    from app.core.db import get_session
    from app.core.rls import system_scope
    from app.iam.models import User

    try:
        if getattr(request.state, "audit_skip", False):
            return
        method, path = request.method, request.url.path
        category, action = classify(method, path)
        actor_id, role = _actor_claims(request)
        detail = getattr(request.state, "audit_detail", None) or {}
        email = getattr(request.state, "audit_email", "") or ""
        # 与请求使用同一个会话来源（测试里被替换为测试库），身份固定为系统写入
        provider = request.app.dependency_overrides.get(get_session, get_session)
        with system_scope("audit-record"):
            async for session in provider():
                name, org_id = "", None
                if actor_id is not None or email:
                    # 登录请求还没有令牌：按提交的邮箱补全姓名、角色、企业（失败的登录同样如此）
                    cond = User.id == actor_id if actor_id is not None else User.email == email
                    user = (await session.execute(select(User).where(cond))).scalar_one_or_none()
                    if user is not None:
                        actor_id = user.id if category == "login" and status_code < 400 else actor_id
                        name, email, org_id, role = user.display_name, user.email, user.org_id, user.role
                summary = detail.get("summary") or ""
                before, after = detail.get("before"), detail.get("after")
                if category != "login":
                    try:
                        from app.audit.describe import collected, describe

                        target, changes, auto_before, auto_after = await describe(
                            session, method, path, collected() if status_code < 400 else []
                        )
                        # 接口自己写了的摘要与前后值优先；统一监听补上对象名和改了哪几栏
                        subject = target or changes
                        if subject.lstrip("删除新增").startswith("合规评估项目") and "对话" in action:
                            action = action.replace("对话", "合规评估项目")  # 同一接口也管合规评估项目
                        head = summary or target
                        summary = (head + "：" + changes if head and changes else head or changes)[:1000]
                        if before is None and after is None:
                            before, after = auto_before, auto_after
                    except Exception:  # noqa: BLE001  摘要写不出也照常记下谁、何时、做了什么
                        logger.exception("审计：整理内容摘要失败")
                        await session.rollback()
                if category == "login":
                    summary = summary or (
                        f"{'成功' if status_code < 400 else '失败'}登录：{email or '未知账号'}"
                    )
                elif not summary.strip():
                    from app.audit.describe import fallback_summary

                    summary = fallback_summary(category, action, method, path, status_code)
                row = {
                    "actor_id": actor_id,
                    "actor_name": name,
                    "actor_email": email,
                    "actor_role": role,
                    "org_id": org_id,
                    "ip": client_ip(request),
                    "category": category,
                    "action": action,
                    "method": method,
                    "path": path[:512],
                    "status": status_code,
                    "summary": summary,
                    "before": redact(before),
                    "after": redact(after),
                }
                session.add(AuditLog(**row, content_hash=_hash(row)))
                await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("审计记录写入失败：%s %s", request.method, request.url.path)
