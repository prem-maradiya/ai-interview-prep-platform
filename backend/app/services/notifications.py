"""In-app notifications.

Event notifications (new assignment, mentor feedback, badge earned) are written when the event
happens. "Due soon" reminders are generated lazily when a user checks notifications, so the app
needs no scheduler or cron job - it works the same on a laptop and in production.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Assignment, Notification, User
from app.services.assignments import is_assigned, progress

DUE_SOON = timedelta(hours=24)


async def notify(db: AsyncSession, user_ids: list[str], tenant_id: str, *, kind: str, title: str,
                 body: str = "", link: str = "", dedupe_key: str | None = None) -> None:
    """Queue notifications (caller commits). With a dedupe_key, a user gets it at most once."""
    if not user_ids:
        return
    rows = [{"user_id": uid, "tenant_id": tenant_id, "kind": kind, "title": title[:200], "body": body,
             "link": link, "dedupe_key": dedupe_key} for uid in dict.fromkeys(user_ids)]
    if dedupe_key is None:
        db.add_all([Notification(**r) for r in rows])
        return
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    stmt = insert(Notification).values([{**r, "id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc)}
                                        for r in rows])
    await db.execute(stmt.on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"]))


async def generate_due_soon(db: AsyncSession, user: User) -> None:
    """Create a one-time reminder for each of the user's assignments due in the next 24h and not done."""
    now = datetime.now(timezone.utc)
    rows = (await db.scalars(select(Assignment).where(
        Assignment.tenant_id == user.tenant_id, Assignment.due_at > now, Assignment.due_at <= now + DUE_SOON))).all()
    for a in rows:
        if not is_assigned(a, user):
            continue
        mine = (await progress(db, a, [user]))[0]
        if mine["status"] in ("completed", "late"):
            continue
        await notify(db, [user.id], user.tenant_id, kind="due_soon", title=f"Due within 24 hours: {a.title}",
                     body=f"{mine['done']}/{mine['total']} tasks done.", link=f"/assignments/{a.id}",
                     dedupe_key=f"due_soon:{a.id}")
