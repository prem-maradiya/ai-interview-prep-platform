"""Per-tenant usage quotas, keyed by the tenant's plan."""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UsageEvent, User

# (limit, window) per plan and kind. None = unlimited.
PLAN_LIMITS: dict[str, dict[str, tuple[int | None, timedelta]]] = {
    "free": {"llm": (200, timedelta(days=30)), "sandbox": (500, timedelta(days=1))},
    "pro": {"llm": (5_000, timedelta(days=30)), "sandbox": (10_000, timedelta(days=1))},
    "enterprise": {"llm": (None, timedelta(days=30)), "sandbox": (None, timedelta(days=1))},
}


async def usage_count(db: AsyncSession, tenant_id: str, kind: str, window: timedelta) -> int:
    since = datetime.now(timezone.utc) - window
    return await db.scalar(
        select(func.count(UsageEvent.id)).where(
            UsageEvent.tenant_id == tenant_id, UsageEvent.kind == kind, UsageEvent.created_at >= since
        )
    ) or 0


async def consume(db: AsyncSession, user: User, kind: str) -> None:
    """Raise 429 if the tenant is over quota, otherwise record one unit of usage (committed by caller)."""
    limit, window = PLAN_LIMITS.get(user.tenant.plan, PLAN_LIMITS["free"])[kind]
    if limit is not None and await usage_count(db, user.tenant_id, kind, window) >= limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Organization {kind} quota exceeded for plan '{user.tenant.plan}'")
    db.add(UsageEvent(tenant_id=user.tenant_id, user_id=user.id, kind=kind))


async def usage_summary(db: AsyncSession, tenant_id: str, plan: str) -> dict:
    out = {}
    for kind, (limit, window) in PLAN_LIMITS.get(plan, PLAN_LIMITS["free"]).items():
        out[kind] = {"used": await usage_count(db, tenant_id, kind, window), "limit": limit, "window_days": window.days}
    return out
