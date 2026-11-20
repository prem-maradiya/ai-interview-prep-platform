"""Timed contests with ICPC-style scoring.

- Rank by problems solved, then by penalty time (lower is better).
- Penalty for a solved problem = minutes from the start to its first accepted submission, plus
  `penalty_minutes` for every rejected attempt before it. Compile errors don't count against you,
  and attempts after the first acceptance are ignored. Unsolved problems add no penalty.
- With `freeze_minutes`, the scoreboard freezes that long before the end: other people's results
  from then on show as "pending" until the contest ends. You always see your own, and staff see
  everything.
- While a contest runs, candidates can't get hints, AI reviews, discussions or the editorial for
  its problems - and every submission to them counts for the contest automatically.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Contest, Role, Submission, User

STAFF = {Role.mentor, Role.admin, Role.owner}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite drops tzinfo


def status(c: Contest, now: datetime | None = None) -> str:
    now = now or _now()
    if now < aware(c.starts_at):
        return "upcoming"
    return "running" if now < aware(c.ends_at) else "ended"


def freeze_at(c: Contest) -> datetime | None:
    return aware(c.ends_at) - timedelta(minutes=c.freeze_minutes) if c.freeze_minutes else None


def is_frozen(c: Contest, now: datetime | None = None) -> bool:
    now = now or _now()
    f = freeze_at(c)
    return f is not None and f <= now < aware(c.ends_at)


async def running_contest_for(db: AsyncSession, user: User, problem_id: str) -> Contest | None:
    """The running contest (in the user's organization) that includes this problem, if any."""
    now = _now()
    rows = (await db.scalars(select(Contest).where(
        Contest.tenant_id == user.tenant_id, Contest.starts_at <= now, Contest.ends_at > now)
        .order_by(Contest.ends_at))).all()
    return next((c for c in rows if problem_id in (c.problem_ids or []) and status(c, now) == "running"), None)


async def locked_by_contest(db: AsyncSession, user: User, problem_id: str) -> Contest | None:
    """Candidates can't use help (hints, reviews, discussions, editorial) on running contest problems."""
    if user.role in STAFF:
        return None
    return await running_contest_for(db, user, problem_id)


@dataclass
class Cell:
    attempts: int = 0  # rejected attempts before the first acceptance (or all, if unsolved)
    solved_minute: int | None = None
    pending: int = 0  # attempts hidden by the freeze
    first_solve: bool = False  # first person to solve this problem


@dataclass
class Row:
    user_id: str
    name: str
    cells: dict[str, Cell] = field(default_factory=dict)
    solved: int = 0
    penalty: int = 0
    last_solve: int = 0


COUNTS_AS_WRONG = {"wrong_answer", "runtime_error", "time_limit_exceeded", "memory_limit_exceeded", "too_slow"}


async def scoreboard(db: AsyncSession, c: Contest, viewer: User) -> dict:
    now = _now()
    st = status(c, now)
    hide_after = freeze_at(c) if (c.freeze_minutes and st == "running" and viewer.role not in STAFF) else None
    start = aware(c.starts_at)
    subs = (await db.execute(
        select(Submission.user_id, Submission.problem_id, Submission.verdict, Submission.created_at)
        .where(Submission.contest_id == c.id).order_by(Submission.created_at))).all()
    users = {u.id: u for u in (await db.scalars(select(User).where(
        User.id.in_({s.user_id for s in subs} or {""})))).all()}

    rows: dict[str, Row] = {}
    first_solver: dict[str, tuple[datetime, str]] = {}
    for uid, pid, verdict, created in subs:
        user = users.get(uid)
        if user is None or user.role in STAFF:  # staff submissions are unofficial
            continue
        row = rows.setdefault(uid, Row(user_id=uid, name=user.full_name))
        cell = row.cells.setdefault(pid, Cell())
        if cell.solved_minute is not None:
            continue  # later attempts don't matter
        created = aware(created)
        if hide_after and created >= hide_after and uid != viewer.id:
            cell.pending += 1
            continue
        if verdict == "accepted":
            cell.solved_minute = int((created - start).total_seconds() // 60)
            if pid not in first_solver or created < first_solver[pid][0]:
                first_solver[pid] = (created, uid)
        elif verdict in COUNTS_AS_WRONG:
            cell.attempts += 1

    for pid, (_, uid) in first_solver.items():
        rows[uid].cells[pid].first_solve = True
    for row in rows.values():
        for cell in row.cells.values():
            if cell.solved_minute is not None:
                row.solved += 1
                row.penalty += cell.solved_minute + c.penalty_minutes * cell.attempts
                row.last_solve = max(row.last_solve, cell.solved_minute)

    ordered = sorted(rows.values(), key=lambda r: (-r.solved, r.penalty, r.last_solve, r.name.lower()))
    out, rank, prev = [], 0, None
    for i, r in enumerate(ordered, 1):
        key = (r.solved, r.penalty)
        if key != prev:
            rank, prev = i, key  # ties share a rank
        out.append({
            "rank": rank, "user_id": r.user_id, "name": r.name, "solved": r.solved, "penalty": r.penalty,
            "problems": {pid: {"attempts": cell.attempts, "solved_minute": cell.solved_minute, "pending": cell.pending,
                               "first_solve": cell.first_solve} for pid, cell in r.cells.items()},
        })
    return {"status": st, "frozen": bool(hide_after) and now >= hide_after, "rows": out}
