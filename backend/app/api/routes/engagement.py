"""Curated problem lists and achievements (XP, levels, badges)."""
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import or_, select

from app.api.deps import DB, CurrentUser, MentorUser
from app.db.models import Problem, ProblemList, Submission, User
from app.schemas import ProblemListIn
from app.services.achievements import achievements, check_badges

router = APIRouter(tags=["engagement"])


async def _visible_lists(db, user: User) -> list[ProblemList]:
    q = select(ProblemList).where(or_(ProblemList.tenant_id == user.tenant_id, ProblemList.tenant_id.is_(None)))
    return list((await db.scalars(q.order_by(ProblemList.tenant_id.is_(None).desc(), ProblemList.created_at))).all())


async def _resolve(db, user: User, lists: list[ProblemList]) -> dict[str, list[Problem]]:
    """Map each list to its problems: global lists by title (global problems), org lists by id (visible problems)."""
    visible = (await db.scalars(select(Problem).where(
        or_(Problem.tenant_id == user.tenant_id, Problem.tenant_id.is_(None))))).all()
    by_id = {p.id: p for p in visible}
    by_title = {p.title: p for p in visible if p.tenant_id is None}
    out = {}
    for pl in lists:
        if pl.tenant_id is None:
            out[pl.id] = [by_title[t] for t in pl.problem_titles if t in by_title]
        else:
            out[pl.id] = [by_id[i] for i in pl.problem_ids if i in by_id]
    return out


async def _solved(db, user: User) -> set[str]:
    return set((await db.scalars(select(Submission.problem_id).where(
        Submission.user_id == user.id, Submission.verdict == "accepted"))).all())


def _summary(pl: ProblemList, problems: list[Problem], solved: set[str]) -> dict:
    return {"id": pl.id, "slug": pl.slug, "title": pl.title, "description": pl.description,
            "is_global": pl.tenant_id is None, "total": len(problems), "solved": sum(p.id in solved for p in problems),
            "difficulties": {d: sum(p.difficulty == d for p in problems) for d in ("easy", "medium", "hard")}}


@router.get("/lists")
async def list_problem_lists(user: CurrentUser, db: DB):
    lists = await _visible_lists(db, user)
    problems, solved = await _resolve(db, user, lists), await _solved(db, user)
    return [_summary(pl, problems[pl.id], solved) for pl in lists]


@router.get("/lists/{list_id}")
async def get_problem_list(list_id: str, user: CurrentUser, db: DB):
    pl = next((x for x in await _visible_lists(db, user) if x.id == list_id), None)
    if pl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "List not found")
    problems, solved = (await _resolve(db, user, [pl]))[pl.id], await _solved(db, user)
    return {**_summary(pl, problems, solved), "problems": [
        {"id": p.id, "title": p.title, "difficulty": p.difficulty, "tags": p.tags, "companies": p.companies or [],
         "solved": p.id in solved} for p in problems]}


@router.post("/lists", status_code=201)
async def create_problem_list(body: ProblemListIn, user: MentorUser, db: DB):
    found = set((await db.scalars(select(Problem.id).where(
        Problem.id.in_(body.problem_ids), or_(Problem.tenant_id == user.tenant_id, Problem.tenant_id.is_(None))))).all())
    if missing := [p for p in body.problem_ids if p not in found]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown problems: {missing}")
    slug = "-".join(body.title.lower().split())[:80]
    pl = ProblemList(tenant_id=user.tenant_id, slug=slug, title=body.title, description=body.description,
                     problem_ids=list(dict.fromkeys(body.problem_ids)), created_by=user.id)
    db.add(pl)
    await db.commit()
    return await get_problem_list(pl.id, user, db)


@router.delete("/lists/{list_id}", status_code=204)
async def delete_problem_list(list_id: str, user: MentorUser, db: DB):
    pl = await db.scalar(select(ProblemList).where(ProblemList.id == list_id, ProblemList.tenant_id == user.tenant_id))
    if pl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "List not found (global lists can't be deleted)")
    await db.delete(pl)
    await db.commit()


@router.get("/achievements")
async def my_achievements(user: CurrentUser, db: DB):
    """Level, XP, streak and badges. Also awards any badges earned since the last check (e.g. streaks)."""
    await check_badges(db, user)
    await db.commit()
    return await achievements(db, user)
