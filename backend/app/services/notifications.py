"""In-app notifications, mirrored to email for users who opted in.

Event notifications (new assignment, mentor feedback, badge earned) are written when the event
happens. "Due soon" reminders are created two ways: lazily when a user opens their notifications,
and by the background job in `app.services.email_jobs` (so people who aren't in the app get the
email). Both paths share a dedupe key, so each reminder is created - and emailed - once.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Assignment, Notification, User
from app.services.assignments import assignees, is_assigned, progress
from app.services.email import app_url, queue_email, render, wants

DUE_SOON = timedelta(hours=24)


async def notify(db: AsyncSession, user_ids: list[str], tenant_id: str, *, kind: str, title: str,
                 body: str = "", link: str = "", dedupe_key: str | None = None) -> list[str]:
    """Queue notifications (caller commits) and their emails. With a dedupe_key, a user gets it at most once.

    Returns the ids of users who got a new notification.
    """
    if not user_ids:
        return []
    ids = list(dict.fromkeys(user_ids))
    rows = [{"user_id": uid, "tenant_id": tenant_id, "kind": kind, "title": title[:200], "body": body,
             "link": link, "dedupe_key": dedupe_key} for uid in ids]
    if dedupe_key is None:
        db.add_all([Notification(**r) for r in rows])
        created = ids
    else:
        insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
        stmt = insert(Notification).values([{**r, "id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc)}
                                            for r in rows])
        stmt = stmt.on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"]).returning(Notification.user_id)
        created = list((await db.scalars(stmt)).all())  # only rows actually inserted
    await _email(db, created, kind=kind, title=title, body=body, link=link)
    return created


async def _email(db: AsyncSession, user_ids: list[str], *, kind: str, title: str, body: str, link: str) -> None:
    if not user_ids:
        return
    users = (await db.scalars(select(User).where(User.id.in_(user_ids)))).all()
    for u in users:
        if not wants(u, kind):
            continue
        first = u.full_name.split()[0] if u.full_name.strip() else "there"
        queue_email(db, render(
            to=u.email, subject=title, heading=title,
            paragraphs=[f"Hi {first},"] + ([body] if body else []),
            button=("Open in PrepForge", app_url(link or "/")),
            unsubscribe_user=u,
        ))


async def remind_due_soon(db: AsyncSession, a: Assignment, users: list[User]) -> int:
    """Remind each of `users` who hasn't finished `a`. Returns how many new reminders were created."""
    created = 0
    for u, mine in zip(users, await progress(db, a, users)):
        if mine["status"] in ("completed", "late"):
            continue
        created += len(await notify(db, [u.id], a.tenant_id, kind="due_soon", title=f"Due within 24 hours: {a.title}",
                                    body=f"{mine['done']}/{mine['total']} tasks done.", link=f"/assignments/{a.id}",
                                    dedupe_key=f"due_soon:{a.id}"))
    return created


async def due_soon_assignments(db: AsyncSession, tenant_id: str | None = None) -> list[Assignment]:
    now = datetime.now(timezone.utc)
    q = select(Assignment).where(Assignment.due_at > now, Assignment.due_at <= now + DUE_SOON)
    if tenant_id:
        q = q.where(Assignment.tenant_id == tenant_id)
    return list((await db.scalars(q)).all())


async def generate_due_soon(db: AsyncSession, user: User) -> None:
    """Create a one-time reminder for each of the user's assignments due in the next 24h and not done."""
    for a in await due_soon_assignments(db, user.tenant_id):
        if is_assigned(a, user):
            await remind_due_soon(db, a, [user])


async def generate_all_due_soon(db: AsyncSession) -> int:
    """Background job: reminders for every assignee of every assignment due soon (system session)."""
    total = 0
    for a in await due_soon_assignments(db):
        total += await remind_due_soon(db, a, [u for u in await assignees(db, a) if u.is_active])
    return total
