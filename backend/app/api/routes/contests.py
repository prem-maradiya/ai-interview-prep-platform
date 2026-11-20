from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app.api.deps import DB, CurrentUser, MentorUser, get_scoped
from app.db.models import Contest, Problem, Role, Submission, User
from app.services import audit
from app.services.contests import STAFF, aware, freeze_at, is_frozen, scoreboard
from app.services.contests import status as contest_status
from app.services.notifications import notify

router = APIRouter(prefix="/contests", tags=["contests"])

MAX_LENGTH = timedelta(days=7)


class ContestIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=5000)
    problem_ids: list[str] = Field(min_length=1, max_length=12)
    starts_at: datetime
    ends_at: datetime
    freeze_minutes: int = Field(default=0, ge=0, le=24 * 60)
    penalty_minutes: int = Field(default=20, ge=0, le=120)


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _problems(db, c: Contest) -> list[Problem]:
    found = {p.id: p for p in (await db.scalars(select(Problem).where(Problem.id.in_(c.problem_ids)))).all()}
    return [found[pid] for pid in c.problem_ids if pid in found]


async def _out(db, c: Contest, user: User, *, detail: bool = False) -> dict:
    st = contest_status(c)
    item = {
        "id": c.id, "title": c.title, "description": c.description, "status": st,
        "starts_at": aware(c.starts_at), "ends_at": aware(c.ends_at), "freeze_minutes": c.freeze_minutes,
        "freeze_at": freeze_at(c), "frozen": is_frozen(c), "penalty_minutes": c.penalty_minutes,
        "problem_count": len(c.problem_ids),
    }
    if detail:
        # Candidates see the problems only once it starts; staff can always see them.
        show = st != "upcoming" or user.role in STAFF
        mine = {}
        if show:
            for pid, verdict in (await db.execute(select(Submission.problem_id, Submission.verdict).where(
                    Submission.contest_id == c.id, Submission.user_id == user.id))).all():
                mine[pid] = "solved" if verdict == "accepted" or mine.get(pid) == "solved" else "attempted"
        item["problems"] = [{"id": p.id, "label": chr(65 + i), "title": p.title, "difficulty": p.difficulty,
                             "my_status": mine.get(p.id)} for i, p in enumerate(await _problems(db, c))] if show else []
    return item


@router.post("", status_code=201)
async def create_contest(body: ContestIn, user: MentorUser, db: DB):
    starts, ends = _utc(body.starts_at), _utc(body.ends_at)
    if ends <= starts:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The contest must end after it starts")
    if ends - starts > MAX_LENGTH:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A contest can last at most 7 days")
    if ends <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That time window is already over")
    if body.freeze_minutes * 60 >= (ends - starts).total_seconds():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The freeze must be shorter than the contest")
    ids = list(dict.fromkeys(body.problem_ids))
    visible = set((await db.scalars(select(Problem.id).where(
        Problem.id.in_(ids), or_(Problem.tenant_id == user.tenant_id, Problem.tenant_id.is_(None))))).all())
    if missing := [p for p in ids if p not in visible]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown problems: {missing}")
    c = Contest(tenant_id=user.tenant_id, created_by=user.id, title=body.title, description=body.description,
                problem_ids=ids, starts_at=starts, ends_at=ends, freeze_minutes=body.freeze_minutes,
                penalty_minutes=body.penalty_minutes)
    db.add(c)
    await db.flush()
    candidates = (await db.scalars(select(User.id).where(
        User.tenant_id == user.tenant_id, User.role == Role.candidate, User.is_active.is_(True)))).all()
    await notify(db, list(candidates), user.tenant_id, kind="contest", title=f"New contest: {c.title}",
                 body=f"{len(ids)} problems, starts {starts:%a %d %b %H:%M} UTC, lasts "
                      f"{int((ends - starts).total_seconds() // 60)} minutes.", link=f"/contests/{c.id}")
    audit.record(db, user, "contest.created", target_type="contest", target_id=c.id, target_label=c.title,
                 details={"problems": len(ids), "starts": starts.isoformat(timespec="minutes")})
    await db.commit()
    return await _out(db, c, user, detail=True)


@router.get("")
async def list_contests(user: CurrentUser, db: DB):
    rows = (await db.scalars(select(Contest).where(Contest.tenant_id == user.tenant_id)
                             .order_by(Contest.starts_at.desc()).limit(50))).all()
    return [await _out(db, c, user) for c in rows]


@router.get("/{contest_id}")
async def get_contest(contest_id: str, user: CurrentUser, db: DB):
    return await _out(db, await get_scoped(db, Contest, contest_id, user.tenant_id), user, detail=True)


@router.get("/{contest_id}/scoreboard")
async def get_scoreboard(contest_id: str, user: CurrentUser, db: DB):
    c = await get_scoped(db, Contest, contest_id, user.tenant_id)
    if contest_status(c) == "upcoming" and user.role not in STAFF:
        return {"status": "upcoming", "frozen": False, "rows": [], "problems": []}
    board = await scoreboard(db, c, user)
    board["problems"] = [{"id": p.id, "label": chr(65 + i), "title": p.title} for i, p in enumerate(await _problems(db, c))]
    return board


@router.delete("/{contest_id}", status_code=204)
async def delete_contest(contest_id: str, user: MentorUser, db: DB):
    c = await get_scoped(db, Contest, contest_id, user.tenant_id)
    audit.record(db, user, "contest.deleted", target_type="contest", target_id=c.id, target_label=c.title)
    await db.delete(c)  # its submissions stay (contest_id is cleared)
    await db.commit()
