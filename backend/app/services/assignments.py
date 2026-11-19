"""Assignment progress: computed from submissions and interviews, never stored, so it can't drift.

Only work done after the assignment was created counts, so an assignment always means fresh practice.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Assignment, Interview, Role, Submission, User


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def assignees(db: AsyncSession, a: Assignment) -> list[User]:
    if a.assign_all:
        q = select(User).where(User.tenant_id == a.tenant_id, User.role == Role.candidate, User.is_active.is_(True))
    else:
        ids = [m.user_id for m in a.members]
        if not ids:
            return []
        q = select(User).where(User.tenant_id == a.tenant_id, User.id.in_(ids))
    return list((await db.scalars(q.order_by(User.full_name))).all())


def is_assigned(a: Assignment, user: User) -> bool:
    if a.assign_all:
        return user.role == Role.candidate
    return any(m.user_id == user.id for m in a.members)


async def progress(db: AsyncSession, a: Assignment, users: list[User]) -> list[dict]:
    if not users:
        return []
    ids = [u.id for u in users]
    created = _aware(a.created_at)
    # First acceptance of each assigned problem per user, after the assignment was created.
    first_solved: dict[tuple[str, str], datetime] = {}
    if a.problem_ids:
        rows = (await db.execute(
            select(Submission.user_id, Submission.problem_id, Submission.created_at)
            .where(Submission.tenant_id == a.tenant_id, Submission.user_id.in_(ids),
                   Submission.problem_id.in_(a.problem_ids), Submission.verdict == "accepted",
                   Submission.created_at >= created)
            .order_by(Submission.created_at)
        )).all()
        for uid, pid, at in rows:
            first_solved.setdefault((uid, pid), _aware(at))
    interviews: dict[str, list[datetime]] = {}
    if a.interview_count:
        q = select(Interview.user_id, Interview.completed_at).where(
            Interview.tenant_id == a.tenant_id, Interview.user_id.in_(ids), Interview.status == "completed",
            Interview.created_at >= created)
        if a.interview_kind:
            q = q.where(Interview.kind == a.interview_kind)
        for uid, done_at in (await db.execute(q.order_by(Interview.completed_at))).all():
            interviews.setdefault(uid, []).append(_aware(done_at))

    now, due = datetime.now(timezone.utc), _aware(a.due_at)
    total = len(a.problem_ids) + a.interview_count
    out = []
    for u in users:
        solved = [pid for pid in a.problem_ids if (u.id, pid) in first_solved]
        ivs = interviews.get(u.id, [])[: a.interview_count]
        done = len(solved) + len(ivs)
        completed_at = None
        if total and done == total:
            completed_at = max([first_solved[(u.id, pid)] for pid in solved] + ivs)
        if completed_at:
            status = "late" if completed_at > due else "completed"
        elif now > due:
            status = "overdue"
        else:
            status = "in_progress" if done else "not_started"
        out.append({
            "user_id": u.id, "full_name": u.full_name, "email": u.email, "status": status,
            "done": done, "total": total, "solved_problem_ids": solved, "interviews_done": len(ivs),
            "completed_at": completed_at,
        })
    return out
