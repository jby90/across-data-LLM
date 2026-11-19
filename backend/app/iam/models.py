import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Role(enum.StrEnum):
    # 管理端
    PLATFORM_ADMIN = "platform_admin"  # 超级管理员：全部，含企业与账号、审计、系统配置
    OPS_ADMIN = "ops_admin"  # 运维管理员：法规、采集、译文术语、图谱、简报、准入事项等基础数据维护
    # 用户端（需求 2.7 的“业务编辑”“普通查看者”）
    MEMBER = "member"  # 企业用户：本企业的评估、准入项目能建、能改、能下载
    VIEWER = "viewer"  # 普通查看者：本企业只读；能否下载看账号的 can_export


ADMIN_ROLES = frozenset({Role.PLATFORM_ADMIN, Role.OPS_ADMIN})
USER_ROLES = frozenset({Role.MEMBER, Role.VIEWER})
ROLE_ZH = {
    Role.PLATFORM_ADMIN: "超级管理员",
    Role.OPS_ADMIN: "运维管理员",
    Role.MEMBER: "企业用户",
    Role.VIEWER: "普通查看者",
}


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    llm_redaction_terms: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # 种子数据里的演示企业，只用于演示；真实企业由门户注册或管理端开通，名称取注册时填的公司全称
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # 管理端角色不属于任何企业;用户端角色必须属于某个企业
        CheckConstraint(
            "(role IN ('platform_admin','ops_admin','reviewer') AND org_id IS NULL) OR "
            "(role IN ('member','viewer') AND org_id IS NOT NULL)",
            name="ck_users_role_org",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), index=True, nullable=True
    )
    # 邮箱统一小写存储,全局唯一
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32))
    # 账号来源：admin = 超级管理员开通，portal = 门户注册
    signup_source: Mapped[str] = mapped_column(String(16), default="admin", server_default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # 普通查看者能否下载评估文件、申报材料、简报（其他角色不看这一栏）
    can_export: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
