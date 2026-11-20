import os
import subprocess
import sys
import tempfile
import time

# SQLite by default. Set TEST_DATABASE_URL to a throwaway PostgreSQL database to run the whole suite
# with row-level security enforced (CI does both). Its schema is rebuilt from the migrations.
TEST_PG = os.environ.get("TEST_DATABASE_URL", "")
_db = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DATABASE_URL"] = TEST_PG or f"sqlite+aiosqlite:///{_db}"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["EMAIL_BACKEND"] = "memory"
os.environ["EMAIL_JOBS_ENABLED"] = "false"

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from sqlalchemy import text  # noqa: E402

from app.db.session import Base, configure_rls, engine, system_session  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed_global_problems  # noqa: E402
from app.services.sandbox import RawResult, Sandbox, get_sandbox  # noqa: E402


class LocalPythonSandbox(Sandbox):
    """Test double: runs Python with the local interpreter (trusted test code only)."""

    async def execute(self, language, code, inputs):
        out = []
        for i in inputs:
            s = time.perf_counter()
            try:
                r = subprocess.run([sys.executable, "-c", code], input=i.encode(), capture_output=True, timeout=5)
                st = "ok" if r.returncode == 0 else "runtime_error"
                out.append(RawResult(st, r.stdout.decode(), r.stderr.decode(), int((time.perf_counter() - s) * 1000)))
            except subprocess.TimeoutExpired:
                out.append(RawResult("timeout", "", "", 5000))
        return out


app.dependency_overrides[get_sandbox] = lambda: LocalPythonSandbox()


_pg_ready = False


async def _reset_postgres() -> None:
    """First use: rebuild the schema with the migrations (policies included). Then: empty every table."""
    global _pg_ready
    if not _pg_ready:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True,
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        await configure_rls()
        _pg_ready = True
    async with engine.begin() as conn:  # the owner connection; TRUNCATE isn't subject to RLS
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest_asyncio.fixture(autouse=True)
async def fresh_db():
    from app.services.ratelimit import get_limiter

    get_limiter().reset()
    from app.services import email

    await email.wait_for_outgoing()  # finish the previous test's sends before clearing
    email.sent_messages.clear()
    if TEST_PG:
        await _reset_postgres()
    else:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    async with system_session() as db:
        await seed_global_problems(db)
    yield


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def register(client, slug="acme", email="owner@acme.io"):
    r = await client.post("/api/auth/register-org", json={
        "org_name": slug.title(), "org_slug": slug, "full_name": "Owner", "email": email, "password": "password123",
    })
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def reg():
    return register
