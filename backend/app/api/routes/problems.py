from fastapi import APIRouter, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DB, CurrentUser, MentorUser
from app.db.models import Problem, Submission, TestCase, User
from app.schemas import ProblemIn, ProblemOut, ProblemSummary, TestCaseOut

router = APIRouter(prefix="/problems", tags=["problems"])


def visible_to(user: User):
    """Global problems plus the user's own tenant's problems."""
    return or_(Problem.tenant_id == user.tenant_id, Problem.tenant_id.is_(None))


async def get_visible_problem(db: AsyncSession, problem_id: str, user: User) -> Problem:
    problem = await db.scalar(select(Problem).where(Problem.id == problem_id, visible_to(user)))
    if problem is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Problem not found")
    return problem


def to_out(p: Problem) -> ProblemOut:
    return ProblemOut(
        id=p.id, title=p.title, description=p.description, difficulty=p.difficulty, tags=p.tags,
        starter_code=p.starter_code, is_global=p.tenant_id is None,
        sample_tests=[TestCaseOut.model_validate(t) for t in p.tests if not t.is_hidden],
        hidden_test_count=sum(t.is_hidden for t in p.tests),
    )


@router.get("", response_model=list[ProblemSummary])
async def list_problems(user: CurrentUser, db: DB, difficulty: str | None = None, tag: str | None = None):
    q = select(Problem).where(visible_to(user)).order_by(Problem.created_at)
    if difficulty:
        q = q.where(Problem.difficulty == difficulty)
    problems = (await db.scalars(q)).all()
    if tag:
        problems = [p for p in problems if tag in p.tags]
    solved = set(
        (await db.scalars(
            select(Submission.problem_id).where(Submission.user_id == user.id, Submission.verdict == "accepted")
        )).all()
    )
    return [
        ProblemSummary(id=p.id, title=p.title, difficulty=p.difficulty, tags=p.tags,
                       is_global=p.tenant_id is None, solved=p.id in solved)
        for p in problems
    ]


@router.get("/{problem_id}", response_model=ProblemOut)
async def get_problem(problem_id: str, user: CurrentUser, db: DB):
    return to_out(await get_visible_problem(db, problem_id, user))


@router.get("/{problem_id}/tests", response_model=list[TestCaseOut])
async def get_all_tests(problem_id: str, user: MentorUser, db: DB):
    """Mentors+ can see hidden tests of their own tenant's problems."""
    problem = await get_visible_problem(db, problem_id, user)
    if problem.tenant_id != user.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Hidden tests of global problems are not exposed")
    return problem.tests


def _apply(problem: Problem, body: ProblemIn) -> None:
    problem.title = body.title
    problem.description = body.description
    problem.difficulty = body.difficulty
    problem.tags = body.tags
    problem.starter_code = body.starter_code
    problem.tests = [TestCase(position=i, **t.model_dump()) for i, t in enumerate(body.tests)]


@router.post("", response_model=ProblemOut, status_code=201)
async def create_problem(body: ProblemIn, user: MentorUser, db: DB):
    problem = Problem(tenant_id=user.tenant_id, created_by=user.id, title=body.title, description=body.description)
    _apply(problem, body)
    db.add(problem)
    await db.commit()
    await db.refresh(problem)
    return to_out(problem)


async def _get_owned(db: AsyncSession, problem_id: str, user: User) -> Problem:
    problem = await get_visible_problem(db, problem_id, user)
    if problem.tenant_id != user.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Global problems are read-only")
    return problem


@router.put("/{problem_id}", response_model=ProblemOut)
async def update_problem(problem_id: str, body: ProblemIn, user: MentorUser, db: DB):
    problem = await _get_owned(db, problem_id, user)
    _apply(problem, body)
    await db.commit()
    await db.refresh(problem)
    return to_out(problem)


@router.delete("/{problem_id}", status_code=204)
async def delete_problem(problem_id: str, user: MentorUser, db: DB):
    await db.delete(await _get_owned(db, problem_id, user))
    await db.commit()
