from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


from app.core.rls import install_rls_hooks  # noqa: E402

install_rls_hooks()


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _clamp_pool_for_low_memory(pool_size: int, max_overflow: int) -> tuple[int, int]:
    import os
    from pathlib import Path

    if "CYSH_DB_POOL_SIZE" in os.environ:
        return pool_size, max_overflow
    try:
        for cg_path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
            p = Path(cg_path)
            if p.exists():
                raw = p.read_text(encoding="utf-8", errors="ignore").strip()
                if raw.isdigit() and int(raw) <= 680 * 1024 * 1024:
                    return min(pool_size, 6), min(max_overflow, 6)
        meminfo = Path("/proc/meminfo")
        if meminfo.exists():
            for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit() and (int(parts[1]) // 1024) <= 2300:
                        return min(pool_size, 6), min(max_overflow, 6)
    except OSError:
        pass
    return pool_size, max_overflow


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        eff_pool, eff_overflow = _clamp_pool_for_low_memory(
            settings.db_pool_size, settings.db_max_overflow
        )
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=eff_pool,
            max_overflow=eff_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_recycle=settings.db_pool_recycle,
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
