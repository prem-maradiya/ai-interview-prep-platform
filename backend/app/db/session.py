"""Database engine, sessions and the tenant context used by PostgreSQL row-level security.

Every transaction on a session starts by telling PostgreSQL who is asking:

  - a *tenant* session (an authenticated request) sees only that tenant's rows (plus shared
    global problems and lists), enforced by the policies in migration 0008;
  - a *system* session (sign-in, sign-up, seeding, background jobs) may see every tenant;
  - a session with neither sees nothing - forgetting to set the context fails closed.

The settings are transaction-local (`set_config(..., true)`), so nothing leaks between requests
through pooled connections. When `DB_APP_ROLE` is usable, the transaction also switches to that
non-superuser role, because superusers bypass RLS. SQLite (used by most unit tests) has no RLS,
so there the context is simply ignored.
"""
import re
from collections.abc import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

TENANT_KEY = "tenant_id"
SYSTEM_KEY = "rls_system"
_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

# Decided once at startup by `configure_rls` (role existence and membership are checked there).
_rls: dict[str, str | None] = {"role": None}


def _context_sql(session: Session) -> tuple[str, dict]:
    params = {"tenant": session.info.get(TENANT_KEY) or "", "system": "on" if session.info.get(SYSTEM_KEY) else "off"}
    sql = "SELECT set_config('app.tenant_id', :tenant, true), set_config('app.rls_system', :system, true)"
    if _rls["role"]:
        sql += ", set_config('role', :role, true)"
        params["role"] = _rls["role"]
    return sql, params


@event.listens_for(Session, "after_begin")
def _apply_tenant_context(session: Session, transaction, connection) -> None:
    if connection.dialect.name == "postgresql":
        sql, params = _context_sql(session)
        connection.execute(text(sql), params)


async def _reapply(db: AsyncSession) -> None:
    if db.in_transaction() and db.bind.dialect.name == "postgresql":
        sql, params = _context_sql(db.sync_session)
        await db.execute(text(sql), params)


async def set_tenant(db: AsyncSession, tenant_id: str) -> None:
    """Scope this session to one tenant (also applies to a transaction that is already open)."""
    db.info[TENANT_KEY] = tenant_id
    db.info.pop(SYSTEM_KEY, None)
    await _reapply(db)


async def set_system(db: AsyncSession) -> None:
    """Let this session read and write across tenants. Only for code paths that must, e.g. sign-in."""
    db.info[SYSTEM_KEY] = True
    db.info.pop(TENANT_KEY, None)
    await _reapply(db)


def tenant_session(tenant_id: str) -> AsyncSession:
    """A new session scoped to one tenant, e.g. for work that outlives the request (SSE streams)."""
    s = SessionLocal()
    s.info[TENANT_KEY] = tenant_id
    return s


def system_session() -> AsyncSession:
    """A new session in system context, for startup tasks and background jobs."""
    s = SessionLocal()
    s.info[SYSTEM_KEY] = True
    return s


async def configure_rls() -> str:
    """Pick the role transactions run as, and describe whether RLS is enforced. Call once at startup."""
    if engine.dialect.name != "postgresql":
        return "not applicable (not PostgreSQL)"
    role = get_settings().db_app_role
    if role and not _ROLE_RE.match(role):
        raise RuntimeError(f"DB_APP_ROLE {role!r} is not a valid role name")
    async with engine.connect() as conn:
        is_super = await conn.scalar(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"))
        usable = bool(role) and await conn.scalar(text(
            "SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :r) "
            "THEN pg_has_role(current_user, :r, 'MEMBER') ELSE false END"
        ), {"r": role})
    _rls["role"] = role if usable else None
    if usable:
        return f"enforced (transactions run as role {role})"
    if is_super:
        return "NOT enforced: connected as a superuser and DB_APP_ROLE is not available"
    return "enforced (connected as a non-superuser role)"


async def get_db() -> AsyncIterator[AsyncSession]:
    """Request session with no tenant yet: `get_current_user` scopes it from the verified token."""
    async with SessionLocal() as session:
        yield session


async def get_system_db() -> AsyncIterator[AsyncSession]:
    """Request session for public endpoints that look accounts up across tenants (sign-in etc.)."""
    async with system_session() as session:
        yield session
