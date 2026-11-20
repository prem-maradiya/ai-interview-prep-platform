"""Periodic email work, run inside the API process (no cron or extra service needed).

  - "due within 24 hours" reminders for every assignee, even those who haven't opened the app;
  - a weekly progress digest per user (opt-out in Account settings).

Several API replicas may run this loop at the same time. That's safe: a reminder is created with a
unique dedupe key, and a digest is claimed with a conditional UPDATE, so each email goes out once.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Assignment, Interview, ReviewItem, Submission, User
from app.db.session import system_session
from app.services.assignments import is_assigned, progress
from app.services.email import app_url, email_prefs, queue_email, render
from app.services.notifications import generate_all_due_soon
from app.services.practice import build_streak

log = logging.getLogger("prepforge.email")

DIGEST_EVERY = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def digest_summary(db: AsyncSession, user: User) -> dict:
    week_ago = _now() - DIGEST_EVERY
    solved = await db.scalar(select(func.count(func.distinct(Submission.problem_id))).where(
        Submission.user_id == user.id, Submission.verdict == "accepted", Submission.created_at >= week_ago))
    interviews = await db.scalar(select(func.count(Interview.id)).where(
        Interview.user_id == user.id, Interview.status == "completed", Interview.created_at >= week_ago))
    reviews_due = await db.scalar(select(func.count(ReviewItem.id)).where(
        ReviewItem.user_id == user.id, ReviewItem.due_at <= _now()))
    open_assignments = []
    rows = (await db.scalars(select(Assignment).where(Assignment.tenant_id == user.tenant_id, Assignment.due_at > _now())
                             .order_by(Assignment.due_at))).all()
    for a in rows:
        if is_assigned(a, user) and (await progress(db, a, [user]))[0]["status"] in ("not_started", "in_progress"):
            open_assignments.append(a)
    return {"solved": solved or 0, "interviews": interviews or 0, "reviews_due": reviews_due or 0,
            "streak": await build_streak(db, user), "open_assignments": open_assignments}


def digest_email(user: User, s: dict) -> object | None:
    """Nothing happened and nothing is waiting: skip the email rather than send an empty one."""
    if not (s["solved"] or s["interviews"] or s["reviews_due"] or s["open_assignments"]):
        return None
    first = user.full_name.split()[0] if user.full_name.strip() else "there"
    lines = [f"Hi {first}, here's your week in PrepForge."]
    lines.append(f"Problems solved: {s['solved']}. Mock interviews completed: {s['interviews']}. "
                 f"Current streak: {s['streak']} day{'s' if s['streak'] != 1 else ''}.")
    if s["reviews_due"]:
        lines.append(f"{s['reviews_due']} problem{'s are' if s['reviews_due'] != 1 else ' is'} due for review - "
                     "revisiting them now is what makes them stick.")
    for a in s["open_assignments"][:5]:
        lines.append(f"Open assignment: {a.title} (due {a.due_at:%a %d %b, %H:%M} UTC).")
    return render(to=user.email, subject="Your weekly PrepForge digest", heading="Your week in review",
                  paragraphs=lines, button=("Continue practicing", app_url("/")), unsubscribe_user=user)


async def send_digests(db: AsyncSession, limit: int = 200) -> int:
    """Send digests to users whose last one (or sign-up) was over a week ago. Returns how many were sent."""
    cutoff = _now() - DIGEST_EVERY
    candidates = (await db.scalars(select(User).where(
        User.is_active.is_(True), User.created_at <= cutoff,
        or_(User.last_digest_at.is_(None), User.last_digest_at <= cutoff)).limit(limit))).all()
    sent = 0
    for user in candidates:
        if not email_prefs(user)["digest"]:
            continue
        # Claim it: only one replica's UPDATE matches, so only one sends.
        claimed = await db.execute(update(User).where(
            User.id == user.id, or_(User.last_digest_at.is_(None), User.last_digest_at <= cutoff),
        ).values(last_digest_at=_now()).execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            continue
        message = digest_email(user, await digest_summary(db, user))
        if message:
            queue_email(db, message)
            sent += 1
        await db.commit()
    return sent


async def run_once() -> dict:
    async with system_session() as db:
        reminders = await generate_all_due_soon(db)
        await db.commit()
        digests = await send_digests(db)
    return {"reminders": reminders, "digests": digests}


async def email_jobs_loop() -> None:
    interval = get_settings().email_jobs_interval_s
    await asyncio.sleep(5)  # let start-up finish first
    while True:
        try:
            result = await run_once()
            if result["reminders"] or result["digests"]:
                log.info("email jobs: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001  (keep the loop alive; the next run retries)
            log.exception("email jobs failed")
        await asyncio.sleep(interval)
