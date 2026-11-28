# ruff: noqa: E501
import re
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    AUD_ADMIN,
    AUD_USER,
    burn_password_check_async,
    create_access_token,
    hash_password_async,
    verify_password_async,
    verify_portal_ticket,
)
from app.iam.models import ADMIN_ROLES, USER_ROLES, Organization, Role, User

TRIAL_ORG_MARKER = "（体验空间-"
_TRIAL_ORG_SUFFIX_RE = re.compile(r"（体验空间-[0-9a-fA-F]{4,32}）$")


def strip_trial_org_suffix(name: str | None) -> str:
    """还原门户自助注册个人体验组织对外展示给本人的申报单位全称。"""
    if not name:
        return ""
    return _TRIAL_ORG_SUFFIX_RE.sub("", name).strip()


def is_trial_org_name(name: str | None) -> bool:
    return bool(name and (TRIAL_ORG_MARKER in name or "(体验空间-" in name))


class AuthError(Exception):
    """登录失败。对外统一提示,不区分账号不存在/密码错误/已停用。"""


class ConflictError(Exception):
    pass


class RuleError(Exception):
    pass


async def authenticate(
    session: AsyncSession, *, email: str, password: str, audience: str
) -> tuple[User, str]:
    from app.core.rls import DatabaseIdentity, bind_identity

    # Authentication can look up exactly one normalized email, not other tenants' rows.
    await bind_identity(session, DatabaseIdentity(scope="auth", auth_email=email.lower()))
    allowed = ADMIN_ROLES if audience == AUD_ADMIN else USER_ROLES
    user = (
        await session.execute(select(User).where(User.email == email.lower()))
    ).scalar_one_or_none()
    if user is None:
        await burn_password_check_async(password)
        raise AuthError
    if not await verify_password_async(password, user.password_hash):
        raise AuthError
    if not user.is_active or user.role not in {r.value for r in allowed}:  # 已撤销的旧角色（如原律所审核员）不能登录
        raise AuthError
    if user.org_id is not None:
        org = await session.get(Organization, user.org_id)
        if org is None or not org.is_active:
            raise AuthError
    token = create_access_token(
        user_id=user.id, audience=audience, role=user.role, org_id=user.org_id
    )
    return user, token


def full_name(name: str) -> str:
    """One way of writing a company's full name: spaces collapsed, brackets in the full-width form
    Chinese registrations use, so “某某（上海）有限公司” and “某某(上海)有限公司” are one company."""
    text = re.sub(r"\s+", " ", (name or "").strip())
    return text.replace("(", "（").replace(")", "）")


async def company_by_full_name(session: AsyncSession, name: str) -> Organization:
    """The company with this full name, created when there is none yet (not committed here)."""
    clean = full_name(name)
    if len(clean) < 2:
        raise RuleError("请填写公司全称")
    if is_trial_org_name(clean):
        raise RuleError("企业名称不可包含系统体验空间保留标识")
    org = (await session.execute(select(Organization).where(Organization.name == clean))).scalar_one_or_none()
    if org is None:
        try:
            async with session.begin_nested():
                cand = Organization(name=clean, is_active=True, is_demo=False)
                session.add(cand)
                await session.flush()
            org = cand
        except IntegrityError:
            org = (
                await session.execute(select(Organization).where(Organization.name == clean))
            ).scalar_one()
    return org


async def create_org(session: AsyncSession, name: str) -> Organization:
    clean = full_name(name)
    if len(clean) < 2:
        raise RuleError("请填写公司全称（至少 2 个非空字符）")
    if is_trial_org_name(clean):
        raise RuleError("企业名称不可包含系统体验空间保留标识")
    org = Organization(name=clean)
    session.add(org)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ConflictError("企业名称已存在") from None
    return org


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    display_name: str,
    password: str,
    role: Role,
    org_id: uuid.UUID | None,
    org_name: str | None = None,
    signup_source: str = "admin",
    can_export: bool = False,
) -> User:
    """Open an account. An enterprise user may be given the company's full name instead of an
    existing company: the company is found by that name or created. Portal registration and the
    administrator's manual opening go through here alike, so the company is always named by the
    full name given when the account was opened."""
    from pydantic import EmailStr, TypeAdapter
    from pydantic import ValidationError as PydanticValidationError

    norm_email = (email or "").strip().lower()
    try:
        norm_email = str(TypeAdapter(EmailStr).validate_python(norm_email)).lower()
    except PydanticValidationError as exc:
        raise RuleError("邮箱地址格式无效，请使用合法的企业或个人邮箱域名") from exc

    if role in ADMIN_ROLES and (org_id is not None or (org_name or "").strip()):
        raise RuleError("管理端角色不能属于企业")
    if role in USER_ROLES and org_id is None and (org_name or "").strip():
        org_id = (await company_by_full_name(session, org_name)).id
    if role in USER_ROLES:
        if org_id is None:
            raise RuleError("用户端角色必须指定所属企业")
        org_obj = await session.get(Organization, org_id)
        if org_obj is None:
            raise RuleError("所属企业不存在")
        if is_trial_org_name(org_obj.name) and (
            signup_source == "admin" or role != Role.VIEWER or bool(can_export)
        ):
            raise RuleError("个人体验空间受隔离保护，不支持后台新增账号或开通正式成员权限")
    user = User(
        email=norm_email,
        display_name=display_name.strip(),
        password_hash=await hash_password_async(password),
        role=role.value,
        org_id=org_id,
        signup_source=signup_source,
        can_export=bool(can_export) and role == Role.VIEWER,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ConflictError("邮箱已被注册") from None
    return user


async def register_portal_user(
    session: AsyncSession,
    *,
    email: str,
    display_name: str,
    org_name: str,
    password: str,
) -> tuple[User, str]:
    """普通用户自助注册体验账号：新建独立个人体验空间组织实现跨租户物理隔离，默认赋予无导出权限的普通查看者角色。"""
    from app.core.rls import DatabaseIdentity, bind_identity

    norm_org = full_name(org_name)
    if is_trial_org_name(org_name) or is_trial_org_name(norm_org):
        raise RuleError("单位全称不可包含系统体验空间保留标识")
    clean = strip_trial_org_suffix(norm_org)
    if len(clean) < 2:
        raise RuleError("请填写公司全称")

    await bind_identity(session, DatabaseIdentity(scope="system"))
    norm_email = email.strip().lower()
    existing = (
        await session.execute(select(User.id).where(User.email == norm_email))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("邮箱已被注册")

    sandbox_name = f"{clean[:175]}{TRIAL_ORG_MARKER}{uuid.uuid4().hex[:8]}）"
    org = Organization(name=sandbox_name, is_demo=True, is_active=True)
    session.add(org)
    await session.flush()
    if not org.is_active:
        raise RuleError("该单位账号已停用，请联系超级管理员")
    user = await create_user(
        session,
        email=norm_email,
        display_name=display_name,
        password=password,
        role=Role.VIEWER,
        org_id=org.id,
        signup_source="portal",
        can_export=False,
    )
    token = create_access_token(
        user_id=user.id, audience=AUD_USER, role=user.role, org_id=user.org_id
    )
    return user, token


async def exchange_portal_ticket(
    session: AsyncSession, *, ticket: str
) -> tuple[User, str]:
    """门户单点登录签名票据兑换平台访问令牌。"""
    from app.core.rls import DatabaseIdentity, bind_identity

    try:
        data = verify_portal_ticket(ticket)
    except ValueError as exc:
        raise AuthError(str(exc)) from exc
    from pydantic import EmailStr, TypeAdapter
    from pydantic import ValidationError as PydanticValidationError

    email = str(data.get("email") or "").strip().lower()
    display_name = str(data.get("display_name") or email.split("@")[0]).strip()
    org_name = str(data.get("org_name") or "").strip()
    tier = str(data.get("tier") or "standard").strip().lower()
    target_role = Role.MEMBER if tier == "enterprise" else Role.VIEWER
    if not email or "@" not in email or len(org_name) < 2:
        raise RuleError("门户票据缺少有效的邮箱或单位全称")
    norm_org = full_name(org_name)
    if is_trial_org_name(org_name) or is_trial_org_name(norm_org):
        raise RuleError("单位全称不可包含系统体验空间保留标识")
    clean_org = strip_trial_org_suffix(norm_org)
    if len(clean_org) < 2:
        raise RuleError("请填写公司全称")
    try:
        TypeAdapter(EmailStr).validate_python(email)
    except PydanticValidationError:
        raise RuleError("门户票据中的邮箱地址格式无效") from None

    await bind_identity(session, DatabaseIdentity(scope="system"))
    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is not None:
        if not user.is_active or user.role not in {r.value for r in USER_ROLES}:
            raise AuthError("该账号无法从用户端登录")
        org = await session.get(Organization, user.org_id) if user.org_id else None
        if org is None or not org.is_active:
            raise AuthError("所属单位已停用")
        if tier == "enterprise":
            if is_trial_org_name(org.name):
                ent_org = await company_by_full_name(session, clean_org)
                if not ent_org.is_active:
                    raise AuthError("所属单位已停用")
                user.org_id = ent_org.id
            user.role = Role.MEMBER.value
            user.can_export = False
        if display_name and user.display_name != display_name:
            user.display_name = display_name[:100]
        await session.commit()
    else:
        if tier == "enterprise":
            org = await company_by_full_name(session, clean_org)
            if not org.is_active:
                raise AuthError("所属单位已停用")
        else:
            # 非企业级普通单点票据严格建立物理隔离的独立体验沙箱，绝不混入正式签约车企租户
            sandbox_name = f"{clean_org[:175]}{TRIAL_ORG_MARKER}{uuid.uuid4().hex[:8]}）"
            org = Organization(name=sandbox_name, is_demo=True, is_active=True)
            session.add(org)
            await session.flush()
        try:
            user = await create_user(
                session,
                email=email,
                display_name=display_name[:100],
                password=secrets.token_urlsafe(24),
                role=target_role,
                org_id=org.id,
                signup_source="portal",
                can_export=False,
            )
        except ConflictError:
            await bind_identity(session, DatabaseIdentity(scope="system"))
            user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if user is None or not user.is_active or user.role not in {r.value for r in USER_ROLES}:
                raise AuthError("该账号无法从用户端登录") from None
            org = await session.get(Organization, user.org_id) if user.org_id else None
            if org is None or not org.is_active:
                raise AuthError("所属单位已停用") from None
    token = create_access_token(
        user_id=user.id, audience=AUD_USER, role=user.role, org_id=user.org_id
    )
    return user, token


async def exchange_admin_ticket(
    session: AsyncSession, *, ticket: str
) -> tuple[User, str]:
    """管理端单点登录签名票据兑换平台管理访问令牌（仅限已启用的管理端账号）。"""
    from app.core.rls import DatabaseIdentity, bind_identity

    try:
        data = verify_portal_ticket(ticket)
    except ValueError as exc:
        raise AuthError(str(exc)) from exc
    email = str(data.get("email") or "").strip().lower()
    tier = str(data.get("tier") or "").strip().lower()
    if tier != "admin" or not email or "@" not in email:
        raise AuthError("管理端单点登录凭证无效")

    await bind_identity(session, DatabaseIdentity(scope="system"))
    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None or not user.is_active or user.role not in {r.value for r in ADMIN_ROLES}:
        raise AuthError("该账号无权登录管理端")
    token = create_access_token(
        user_id=user.id, audience=AUD_ADMIN, role=user.role, org_id=user.org_id
    )
    return user, token


async def change_own_password(
    session: AsyncSession, *, user: User, old_password: str, new_password: str
) -> None:
    if not await verify_password_async(old_password, user.password_hash):
        raise AuthError("当前密码不正确")
    if old_password == new_password:
        raise RuleError("新密码不能与当前密码相同")
    user.password_hash = await hash_password_async(new_password)
    await session.commit()
