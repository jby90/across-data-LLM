import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.iam.models import Role


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Role
    org_id: uuid.UUID | None
    can_export: bool = True


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID | None
    email: str
    display_name: str
    role: Role
    is_active: bool
    signup_source: str = "admin"
    can_export: bool = False

    @model_validator(mode="after")
    def _effective_export_flag(self) -> "UserOut":
        if self.role != Role.VIEWER:
            self.can_export = True
        return self


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    is_active: bool | None = None


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    is_active: bool
    is_demo: bool = False
    llm_redaction_terms: list[str] = Field(default_factory=list)


class RedactionTermsIn(BaseModel):
    terms: list[str] = Field(max_length=100)

    @field_validator("terms")
    @classmethod
    def validate_terms(cls, terms):
        if any(not 2 <= len(v.strip()) <= 200 for v in terms):
            raise ValueError("每个词应为 2–200 字符")
        return list(dict.fromkeys(v.strip() for v in terms))


class UserCreate(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=256)
    role: Role
    org_id: uuid.UUID | None = None
    # 开通企业用户时可直接填公司全称：库里没有这家企业就新建（门户注册走同一个开通函数）
    org_name: str | None = Field(default=None, max_length=200)
    # 普通查看者能否下载（其他角色忽略）
    can_export: bool = False


class UserUpdate(BaseModel):
    """改账号的角色（同一端内换：超级/运维管理员，企业用户/普通查看者）、查看者下载权限、姓名、启停与重置密码。"""

    role: Role | None = None
    can_export: bool | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None
    new_password: str | None = Field(default=None, min_length=12, max_length=256)


class PortalRegisterRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=100)
    org_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=12, max_length=256)


class PortalExchangeRequest(BaseModel):
    ticket: str = Field(min_length=10, max_length=2048)


class AccountProfileOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: Role
    role_zh: str
    org_id: uuid.UUID | None = None
    org_name: str | None = None
    signup_source: str = "admin"
    can_export: bool = False
    is_trial: bool = False


class TrialUserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    org_id: uuid.UUID | None = None
    org_name: str = ""
    is_active: bool = True


class TrialPromoteIn(BaseModel):
    org_id: uuid.UUID
    role: Role = Role.MEMBER
    can_export: bool = False


class AccountProfileUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)


class PasswordChangeRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)
