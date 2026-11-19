import atexit
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# 必须在导入 app 之前设置测试配置(归档目录强制指向临时目录,防止写入 backend/data)
_TEST_ARCHIVE_DIR = tempfile.mkdtemp(prefix="cyshuhe-archive-")
atexit.register(shutil.rmtree, _TEST_ARCHIVE_DIR, True)
os.environ.setdefault("CYSH_JWT_SECRET", "test-secret-only-for-pytest-" + "x" * 16)
os.environ.setdefault("CYSH_ENV", "dev")
# 测试不调用办公软件转 PDF（慢且依赖本机安装）；真实转换另有一项测试（装了办公软件时才跑）
os.environ.setdefault("CYSH_PDF_ENGINE", "builtin")
os.environ["CYSH_ARCHIVE_DIR"] = _TEST_ARCHIVE_DIR
# 既有流水线测试按证据版报告断言(如 token 总量)；咨询层由 test_consulting*.py 单独开启验证
os.environ.setdefault("CYSH_CONSULTING_ENABLED", "false")
os.environ.setdefault("CYSH_ASSESSMENT_CHAT_ENABLED", "true")  # 旧评估对话的测试仍覆盖它
# 译文语义核验会真实调用模型；既有翻译测试用假翻译函数，由 test_translation_judge.py 单独开启验证
os.environ.setdefault("CYSH_TRANSLATION_JUDGE_ENABLED", "false")
# 入库的文书性质判定同样会调用模型；由 test_legal_nature.py 单独开启验证
os.environ.setdefault("CYSH_LEGAL_NATURE_SCREENING_ENABLED", "false")

import pgserver  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.db import Base, get_session  # noqa: E402
from app.iam import models as _models  # noqa: E402, F401
from app.iam import service  # noqa: E402
from app.iam.models import Role  # noqa: E402
from app.ingestion import models as _ingestion_models  # noqa: E402, F401
from app.main import create_app  # noqa: E402
from app.sources import models as _sources_models  # noqa: E402, F401

PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="session")
def pg_server():
    """嵌入式 PostgreSQL,仅用于测试。"""
    import uuid

    pgdata = os.path.join(tempfile.gettempdir(), f"cyshuhe-pg-{uuid.uuid4().hex[:8]}")
    os.mkdir(pgdata)
    server = pgserver.get_server(pgdata, cleanup_mode="delete")
    yield server
    # Repeated TRUNCATE creates many relation files. Windows can take >60s
    # to finish its shutdown checkpoint; pgserver's default then kills only
    # the postmaster and races still-running children against directory removal.
    # Wait for a confirmed graceful shutdown, with durability settings unchanged.
    import time

    from pgserver._commands import POSTGRES_BIN_PATH

    target = Path(pgdata).resolve()
    assert target.parent == Path(tempfile.gettempdir()).resolve()
    assert target.name.startswith("cyshuhe-pg-")
    pg_ctl_bin = str(POSTGRES_BIN_PATH / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl"))
    user_kw = {"user": server.system_user} if server.system_user else {}
    res = subprocess.run(
        [pg_ctl_bin, "-D", str(target), "-m", "fast", "-w", "-t", "90", "stop"],
        check=False,
        capture_output=True,
        text=True,
        **user_kw,
    )
    if res.returncode != 0 and (target / "postmaster.pid").exists():
        subprocess.run(
            [pg_ctl_bin, "-D", str(target), "-m", "immediate", "-w", "-t", "60", "stop"],
            check=False,
            capture_output=True,
            text=True,
            **user_kw,
        )
    try:
        server.cleanup()
    except PermissionError:
        time.sleep(1.0)
        shutil.rmtree(str(target), ignore_errors=True)


@pytest.fixture(scope="session")
def pg_url(pg_server) -> str:
    uri = pg_server.get_uri()  # postgresql://postgres:@127.0.0.1:port/postgres
    return uri.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest_asyncio.fixture(scope="session")
async def engine(pg_url):
    eng = create_async_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    from app.analysis.events import close_hubs

    await close_hubs()
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    async with engine.begin() as conn:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def isolated_model_audit(session_factory):
    """Real model-transport tests audit only to the isolated test database."""
    from app.core.redaction import llm_scope

    with llm_scope(session_factory=session_factory):
        yield


@pytest_asyncio.fixture
async def client(session_factory):
    app = create_app()

    async def _override():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _legacy_reviewer(s):
    """库里留着的原律所审核员账号（角色已撤销、已停用）：数据行在，不能登录、不在列表里。"""
    from app.core.security import hash_password
    from app.iam.models import User

    u = User(email="reviewer@example.com", display_name="reviewer", password_hash=hash_password(PASSWORD), role="reviewer", org_id=None, is_active=False)
    s.add(u)
    await s.commit()
    await s.refresh(u)
    return u


@pytest_asyncio.fixture
async def seed(session_factory):
    """两家企业加超级管理员，另有一个已撤销角色的旧账号（原律所审核员）。"""
    async with session_factory() as s:
        org_a = await service.create_org(s, "甲车企")
        org_b = await service.create_org(s, "乙车企")
        users = {}
        for key, email, role, org in [
            ("admin", "admin@example.com", Role.PLATFORM_ADMIN, None),
            ("a_admin", "a-admin@example.com", Role.MEMBER, org_a.id),
            ("a_member", "a-member@example.com", Role.MEMBER, org_a.id),
            ("b_admin", "b-admin@example.com", Role.MEMBER, org_b.id),
        ]:
            users[key] = await service.create_user(
                s, email=email, display_name=key, password=PASSWORD, role=role, org_id=org
            )
        users["reviewer"] = await _legacy_reviewer(s)
        return {"org_a": org_a, "org_b": org_b, **users}


async def login(client, side: str, email: str, password: str = PASSWORD) -> str:
    r = await client.post(f"/api/auth/{side}/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _fresh_document_basis():
    """合规文件的依据按国家缓存在进程里；每个测试用自己的数据库，缓存不能从上一个测试带过来。"""
    from app.analysis import documents_router
    from app.knowledge import library_view

    documents_router._COUNTRY_BASIS.clear()
    documents_router._BASIS_CACHE.clear()
    library_view._RELATIONS_CACHE.clear()
    from app.knowledge.cache_sync import clear_user_caches

    clear_user_caches()  # 国家页、合规主题、国家列表等缓存：每个测试从干净状态开始
    yield
