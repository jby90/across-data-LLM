import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import AUD_ADMIN, AUD_USER, decode_access_token
from app.iam.models import Organization, Role, User

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    role: Role
    org_id: uuid.UUID | None
    can_export: bool = True


def _unauthorized() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录或登录已失效")


# 需求 2.7 四级角色，按类在这一处统一把关，各模块的接口不用逐个改：
# - 管理端写着“平台管理员”的接口，运维管理员也能用；下面这几类只给超级管理员（企业与账号、审计、
#   系统配置、看各企业的准备情况）。
# - 用户端写着“企业用户”的接口，普通查看者也能看；不能新建或修改企业评估/准入项目（放行通知标已读，
#   以及查看者仅自己可见的咨询问答对话），下载（路径里有 /export）要账号开了下载权限。
SUPER_ADMIN_ONLY = (
    "/api/admin/orgs",
    "/api/admin/users",
    "/api/admin/audit-logs",
    "/api/admin/agent-studio",
    "/api/admin/admission/projects",
    "/api/admin/cockpit",
)
VIEWER_MAY_WRITE = re.compile(
    r"^/api/notifications/(read-all|[0-9a-f-]{36}/read)$"
    r"|^/api/analysis/chats$"
    r"|^/api/analysis/tasks/[0-9a-f-]{36}(/messages|/restore)?$"
)  # 订阅是企业的：查看者只看不改；咨询问答仅允许操作查看者自己建的私密对话（在路由内复核归属）
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_super_admin_only(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in SUPER_ADMIN_ONLY)


_DOWNLOAD = re.compile(
    r"/(?:chat-)?export(?:[-./]|$)"
    r"|^/api/admission/drafts/[^/]+/forms/"
    r"|/official-forms/"
    r"|/official/[^/]+/(?:original|blank)"
    r"|/blank(?:[-./]|$)"
)


def is_export(path: str) -> bool:
    """Downloads of generated files: the platform names them …/export.docx and the like; the filled
    official forms kept with an admission draft are the one other download."""
    return bool(_DOWNLOAD.search(path))


def check_role_scope(role: Role, method: str, path: str, can_export: bool) -> None:
    """The class rule above, as one check (also used by the tests that walk every route)."""
    if role == Role.OPS_ADMIN and is_super_admin_only(path):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "这一项只有超级管理员能用")
    if role == Role.VIEWER:
        if method.upper() not in READ_METHODS and not VIEWER_MAY_WRITE.match(path):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "查看账号只能查看，不能新建或修改")
        if is_export(path) and not can_export:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "这个账号没有下载权限，请联系平台开通")


def _expand(allowed: tuple[Role, ...]) -> frozenset[Role]:
    roles = set(allowed)
    if Role.PLATFORM_ADMIN in roles:
        roles.add(Role.OPS_ADMIN)
    if Role.MEMBER in roles:
        roles.add(Role.VIEWER)
    return frozenset(roles)


def principal_for(audience: str, *allowed: Role, super_only: bool = False) -> Callable:
    """生成依赖:校验令牌受众与角色,并复核账号和所属企业仍然有效。

    ``super_only`` 只给超级管理员；否则按上面的类别规则放行运维管理员、普通查看者。"""
    permitted = frozenset(allowed) if super_only else _expand(allowed)

    async def dependency(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
        session: AsyncSession = Depends(get_session),
    ) -> Principal:
        if credentials is None:
            raise _unauthorized()
        try:
            claims = decode_access_token(credentials.credentials, audience)
            role = Role(claims["role"])
            user_id = uuid.UUID(claims["sub"])
        except (jwt.PyJWTError, ValueError, KeyError):
            raise _unauthorized() from None

        from app.core.rls import DatabaseIdentity, bind_identity, bind_principal

        # A signed subject may read only its own identity before role/org revocation checks.
        await bind_identity(session, DatabaseIdentity(scope="identity", user_id=user_id))

        user = await session.get(User, user_id)
        if user is None or not user.is_active or user.role != role.value:
            raise _unauthorized()
        org = None
        if user.org_id is not None:
            org = await session.get(Organization, user.org_id)
            if org is None or not org.is_active:
                raise _unauthorized()
        if allowed and role not in permitted:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权访问")
        can_export = role != Role.VIEWER or bool(user.can_export)
        check_role_scope(role, request.method, request.url.path, can_export)
        from app.core.redaction import bind_egress

        principal = Principal(user_id=user.id, role=role, org_id=user.org_id, can_export=can_export)
        await bind_principal(session, principal)
        bind_egress(session, org)
        return principal

    return dependency


# 管理端:超级管理员和运维管理员(只给超级管理员的几类见 SUPER_ADMIN_ONLY);
# 用户端:企业用户和普通查看者(查看者只读)
require_platform_admin = principal_for(AUD_ADMIN, Role.PLATFORM_ADMIN)
require_admin_side = principal_for(AUD_ADMIN, Role.PLATFORM_ADMIN)
require_super_admin = principal_for(AUD_ADMIN, Role.PLATFORM_ADMIN, super_only=True)
require_user_side = principal_for(AUD_USER, Role.MEMBER)
require_user_editor = principal_for(AUD_USER, Role.MEMBER, super_only=True)


async def require_self_account(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> tuple[Principal, User, Organization | None]:
    """双端已登录账号读取或修改本人姓名与登录密码。"""
    from app.iam.models import ADMIN_ROLES, USER_ROLES

    if credentials is None:
        raise _unauthorized()
    claims = None
    matched_aud = None
    for aud in (AUD_USER, AUD_ADMIN):
        try:
            claims = decode_access_token(credentials.credentials, aud)
            matched_aud = aud
            break
        except jwt.PyJWTError:
            continue
    if claims is None or matched_aud is None:
        raise _unauthorized()
    try:
        role = Role(claims["role"])
        user_id = uuid.UUID(claims["sub"])
    except (ValueError, KeyError):
        raise _unauthorized() from None
    expected_roles = ADMIN_ROLES if matched_aud == AUD_ADMIN else USER_ROLES
    if role not in expected_roles:
        raise _unauthorized()

    from app.core.rls import DatabaseIdentity, bind_identity, bind_principal

    await bind_identity(session, DatabaseIdentity(scope="identity", user_id=user_id))
    user = await session.get(User, user_id)
    if user is None or not user.is_active or user.role != role.value:
        raise _unauthorized()
    org = None
    if user.org_id is not None:
        org = await session.get(Organization, user.org_id)
        if org is None or not org.is_active:
            raise _unauthorized()
    can_export = role != Role.VIEWER or bool(user.can_export)
    principal = Principal(user_id=user.id, role=role, org_id=user.org_id, can_export=can_export)
    await bind_principal(session, principal)
    return principal, user, org
