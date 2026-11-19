# ruff: noqa: E501
import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import AUD_ADMIN, AUD_USER, hash_password_async
from app.iam import service
from app.iam.deps import (
    Principal,
    principal_for,
    require_self_account,
    require_super_admin,
)
from app.iam.models import ADMIN_ROLES, ROLE_ZH, USER_ROLES, Organization, Role, User
from app.iam.schemas import (
    AccountProfileOut,
    AccountProfileUpdate,
    LoginRequest,
    OrgCreate,
    OrgOut,
    OrgUpdate,
    PasswordChangeRequest,
    PortalExchangeRequest,
    PortalRegisterRequest,
    RedactionTermsIn,
    TokenResponse,
    UserCreate,
    UserOut,
    UserUpdate,
)

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
admin_router = APIRouter(prefix="/api/admin", tags=["admin"])
user_router = APIRouter(prefix="/api/user", tags=["user"])

_LOGIN_FAILED = HTTPException(status.HTTP_401_UNAUTHORIZED, "账号或密码错误")


def _token_out(user: User, token: str) -> TokenResponse:
    role = Role(user.role)
    return TokenResponse(
        access_token=token,
        role=role,
        org_id=user.org_id,
        can_export=role != Role.VIEWER or bool(user.can_export),
    )


async def _login(body: LoginRequest, session: AsyncSession, audience: str) -> TokenResponse:
    try:
        user, token = await service.authenticate(
            session, email=body.email, password=body.password, audience=audience
        )
    except service.AuthError:
        raise _LOGIN_FAILED from None
    return _token_out(user, token)


@auth_router.post("/user/login", response_model=TokenResponse, summary="用户端登录")
async def user_login(
    body: LoginRequest, request: Request, session: AsyncSession = Depends(get_session)
):
    request.state.audit_email = body.email.strip().lower()
    return await _login(body, session, AUD_USER)


@auth_router.post("/admin/login", response_model=TokenResponse, summary="管理端登录")
async def admin_login(
    body: LoginRequest, request: Request, session: AsyncSession = Depends(get_session)
):
    request.state.audit_email = body.email.strip().lower()
    return await _login(body, session, AUD_ADMIN)


@auth_router.post("/user/register", response_model=TokenResponse, status_code=201, summary="普通用户自助注册体验账号")
async def user_register(
    body: PortalRegisterRequest, request: Request, session: AsyncSession = Depends(get_session)
):
    request.state.audit_email = body.email.strip().lower()
    try:
        user, token = await service.register_portal_user(
            session,
            email=body.email,
            display_name=body.display_name,
            org_name=body.org_name,
            password=body.password,
        )
    except service.ConflictError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    except service.RuleError as e:
        raise HTTPException(422, str(e)) from None
    return _token_out(user, token)


@auth_router.post("/portal/exchange", response_model=TokenResponse, summary="门户单点票据换取登录令牌")
async def portal_exchange(
    body: PortalExchangeRequest, request: Request, session: AsyncSession = Depends(get_session)
):
    try:
        user, token = await service.exchange_portal_ticket(session, ticket=body.ticket)
    except service.AuthError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e) or "门户登录凭证无效") from None
    except service.RuleError as e:
        raise HTTPException(422, str(e)) from None
    request.state.audit_email = user.email
    return _token_out(user, token)


@auth_router.post("/admin/exchange", response_model=TokenResponse, summary="管理端单点票据换取登录令牌")
async def admin_exchange(
    body: PortalExchangeRequest, request: Request, session: AsyncSession = Depends(get_session)
):
    try:
        user, token = await service.exchange_admin_ticket(session, ticket=body.ticket)
    except service.AuthError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e) or "管理端登录凭证无效") from None
    request.state.audit_email = user.email
    return _token_out(user, token)


def _profile_out(user: User, org: Organization | None) -> AccountProfileOut:
    role = Role(user.role)
    return AccountProfileOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=role,
        role_zh=ROLE_ZH.get(role, user.role),
        org_id=user.org_id,
        org_name=service.strip_trial_org_suffix(org.name) if org is not None else None,
        signup_source=user.signup_source,
        can_export=role != Role.VIEWER or bool(user.can_export),
    )


@auth_router.get("/account", response_model=AccountProfileOut, summary="读取当前本人账号信息")
async def get_self_account(ctx=Depends(require_self_account)):
    _, user, org = ctx
    return _profile_out(user, org)


@auth_router.patch("/account", response_model=AccountProfileOut, summary="修改当前本人姓名")
async def update_self_account(
    body: AccountProfileUpdate,
    ctx=Depends(require_self_account),
    session: AsyncSession = Depends(get_session),
):
    _, user, org = ctx
    clean_name = body.display_name.strip()
    if not clean_name:
        raise HTTPException(422, "姓名不能为空")
    user.display_name = clean_name
    await session.commit()
    return _profile_out(user, org)


@auth_router.post("/password", summary="修改当前本人登录密码")
async def change_self_password(
    body: PasswordChangeRequest,
    ctx=Depends(require_self_account),
    session: AsyncSession = Depends(get_session),
):
    _, user, _ = ctx
    try:
        await service.change_own_password(
            session,
            user=user,
            old_password=body.old_password,
            new_password=body.new_password,
        )
    except service.AuthError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e) or "当前密码不正确") from None
    except service.RuleError as e:
        raise HTTPException(422, str(e)) from None
    return {"ok": True}


# ---- 管理端 ----


@admin_router.get("/me", response_model=UserOut, summary="当前管理端账号")
async def admin_me(
    p: Principal = Depends(principal_for(AUD_ADMIN, Role.PLATFORM_ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await session.get(User, p.user_id)


@admin_router.post("/orgs", response_model=OrgOut, status_code=201, summary="创建企业")
async def create_org(
    body: OrgCreate,
    _: Principal = Depends(require_super_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await service.create_org(session, body.name)
    except service.ConflictError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None


@admin_router.get("/orgs", response_model=list[OrgOut], summary="企业列表")
async def list_orgs(
    include_inactive: bool = False,
    _: Principal = Depends(require_super_admin),
    session: AsyncSession = Depends(get_session),
):
    # 默认仅列在用签约企业；门户个人体验沙箱空间始终不列入签约企业列表
    stmt = select(Organization).where(~Organization.name.like(f"%{service.TRIAL_ORG_MARKER}%"))
    if not include_inactive:
        stmt = stmt.where(Organization.is_active.is_(True))
    stmt = stmt.order_by(Organization.is_active.desc(), Organization.name)
    return (await session.execute(stmt)).scalars().all()


@admin_router.patch("/orgs/{org_id}", response_model=OrgOut, summary="修改企业名称或启停状态")
async def update_org(
    org_id: uuid.UUID,
    body: OrgUpdate,
    request: Request,
    _: Principal = Depends(require_super_admin),
    session: AsyncSession = Depends(get_session),
):
    from app.audit.service import set_detail

    org = await session.get(Organization, org_id)
    if org is None or service.is_trial_org_name(org.name):
        raise HTTPException(404, "企业不存在")
    before = {"name": org.name, "is_active": org.is_active}
    if body.name is not None:
        clean = service.full_name(body.name)
        if len(clean) < 2:
            raise HTTPException(422, "请填写公司全称")
        dup = (
            await session.execute(
                select(Organization.id).where(Organization.name == clean, Organization.id != org.id)
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "企业名称已存在")
        org.name = clean
    if body.is_active is not None:
        org.is_active = body.is_active
    after = {"name": org.name, "is_active": org.is_active}
    set_detail(request, summary=f"修改企业“{org.name}”信息或启用状态", before=before, after=after)
    await session.commit()
    return org


async def _save_redaction_terms(session, org_id, terms, actor_id):
    from app.knowledge.models import KnowledgeAuditRecord

    org = await session.get(Organization, org_id)
    if org is None:
        raise HTTPException(404, "企业不存在")
    payload = {"before": org.llm_redaction_terms or [], "after": terms}
    session.add(
        KnowledgeAuditRecord(
            target_type="organization_redaction",
            target_id=org.id,
            action="redaction_terms_changed",
            actor_id=actor_id,
            payload=payload,
            content_hash=hashlib.sha256(
                json.dumps(payload, ensure_ascii=False).encode()
            ).hexdigest(),
        )
    )
    org.llm_redaction_terms = terms
    await session.commit()
    return org


@admin_router.patch("/orgs/{org_id}/redaction", response_model=OrgOut)
async def set_org_redaction(
    org_id: uuid.UUID,
    body: RedactionTermsIn,
    principal: Principal = Depends(require_super_admin),
    session: AsyncSession = Depends(get_session),
):
    return await _save_redaction_terms(session, org_id, body.terms, principal.user_id)



@admin_router.post("/users", response_model=UserOut, status_code=201, summary="创建账号")
async def create_user(
    body: UserCreate,
    _: Principal = Depends(require_super_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await service.create_user(session, **body.model_dump())
    except service.ConflictError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    except service.RuleError as e:
        raise HTTPException(422, str(e)) from None


@admin_router.patch("/users/{user_id}", response_model=UserOut, summary="修改账号角色、下载权限、启停或重置密码")
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    principal: Principal = Depends(require_super_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, user_id)
    if user is None or user.role not in {r.value for r in Role}:
        raise HTTPException(404, "账号不存在")
    if body.role is not None:
        same_side = ADMIN_ROLES if Role(user.role) in ADMIN_ROLES else USER_ROLES
        if body.role not in same_side:
            raise HTTPException(422, "管理端账号和企业账号不能互换")
        if user.id == principal.user_id and body.role != Role(user.role):
            raise HTTPException(422, "不能改自己的角色")
        user.role = body.role.value
    if body.display_name is not None:
        clean = body.display_name.strip()
        if not clean:
            raise HTTPException(422, "姓名不能为空")
        user.display_name = clean
    if body.is_active is not None:
        if user.id == principal.user_id and not body.is_active:
            raise HTTPException(422, "不能停用当前登录的管理员账号")
        user.is_active = body.is_active
    if body.new_password is not None:
        user.password_hash = await hash_password_async(body.new_password)
    if body.can_export is not None:
        user.can_export = body.can_export
    if user.role != Role.VIEWER.value:
        user.can_export = False
    await session.commit()
    return user


@admin_router.get("/users", response_model=list[UserOut], summary="账号列表")
async def list_users(
    org_id: uuid.UUID | None = None,
    include_inactive_orgs: bool = False,
    _: Principal = Depends(require_super_admin),
    session: AsyncSession = Depends(get_session),
):
    # 只列现行的两类账号；默认不列已停用企业的账号（旧数据行留在库里供审计）
    stmt = select(User).outerjoin(Organization, Organization.id == User.org_id).where(
        User.role.in_([r.value for r in Role]),
        or_(User.org_id.is_(None), ~Organization.name.like(f"%{service.TRIAL_ORG_MARKER}%")),
    )
    if not include_inactive_orgs:
        stmt = stmt.where(or_(User.org_id.is_(None), Organization.is_active.is_(True)))
    stmt = stmt.order_by(User.created_at)
    if org_id is not None:
        stmt = stmt.where(User.org_id == org_id)
    return (await session.execute(stmt)).scalars().all()


# ---- 用户端 ----


@user_router.get("/me", response_model=UserOut, summary="当前用户端账号")
async def user_me(
    p: Principal = Depends(principal_for(AUD_USER, Role.MEMBER)),
    session: AsyncSession = Depends(get_session),
):
    return await session.get(User, p.user_id)
