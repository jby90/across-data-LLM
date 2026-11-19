import asyncio
import base64
import hashlib
import hmac
import json
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

_hasher = PasswordHasher()
# 限制并发 Argon2id 哈希线程数（每次分配 64 MiB 内存块），防止多客户端突发并发登录导致小内存主机抖动
_ARGON2_SEM = threading.BoundedSemaphore(2)
_ALGORITHM = "HS256"
_ISSUER = "cyshuhe"

# 令牌受众:用户端与管理端令牌互不通用
AUD_USER = "user"
AUD_ADMIN = "admin"


def hash_password(password: str) -> str:
    with _ARGON2_SEM:
        return _hasher.hash(password)


async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


def verify_password(password: str, password_hash: str) -> bool:
    with _ARGON2_SEM:
        try:
            return _hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False


async def verify_password_async(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(verify_password, password, password_hash)


# 用于在用户不存在时消耗等量时间,避免通过响应时间枚举账号
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


def burn_password_check(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


async def burn_password_check_async(password: str) -> None:
    await asyncio.to_thread(burn_password_check, password)


def _portal_key() -> bytes:
    settings = get_settings()
    if settings.portal_sso_secret:
        return settings.portal_sso_secret.encode("utf-8")
    return hashlib.sha256((settings.jwt_secret + ":cyshuhe-portal-sso").encode("utf-8")).digest()


def create_portal_ticket(
    *,
    email: str,
    display_name: str,
    org_name: str,
    tier: str = "standard",
    ttl_seconds: int = 300,
) -> str:
    """签发门户单点登录票据（供门户联调与集成测试调用）。"""
    payload = {
        "email": email.strip().lower(),
        "display_name": display_name.strip(),
        "org_name": org_name.strip(),
        "tier": tier,
        "exp": int(time.time()) + ttl_seconds,
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).rstrip(b"=")
    sig = base64.urlsafe_b64encode(
        hmac.new(_portal_key(), body, hashlib.sha256).digest()
    ).rstrip(b"=")
    return f"{body.decode('ascii')}.{sig.decode('ascii')}"


def verify_portal_ticket(ticket: str) -> dict[str, Any]:
    """校验门户单点登录签名票据并返回载荷。"""
    parts = (ticket or "").strip().split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("门户登录凭证格式无效")
    body_b = parts[0].encode("ascii")
    expected_sig = base64.urlsafe_b64encode(
        hmac.new(_portal_key(), body_b, hashlib.sha256).digest()
    ).rstrip(b"=")
    if not hmac.compare_digest(expected_sig, parts[1].encode("ascii")):
        raise ValueError("门户登录凭证签名无效")
    pad = "=" * (-len(parts[0]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(parts[0] + pad).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("门户登录凭证内容无效") from exc
    if not isinstance(data, dict) or int(data.get("exp") or 0) < int(time.time()):
        raise ValueError("门户登录凭证已过期，请从门户重新进入")
    return data


def create_access_token(
    *, user_id: uuid.UUID, audience: str, role: str, org_id: uuid.UUID | None
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "iss": _ISSUER,
        "aud": audience,
        "sub": str(user_id),
        "role": role,
        "org": str(org_id) if org_id else None,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_access_token(token: str, audience: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        get_settings().jwt_secret,
        algorithms=[_ALGORITHM],
        audience=audience,
        issuer=_ISSUER,
        options={"require": ["exp", "sub", "aud", "iss"]},
    )
