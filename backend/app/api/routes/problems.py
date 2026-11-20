from fastapi import APIRouter, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DB, CurrentUser, MentorUser
from app.db.models import Problem, Submission, TestCase, User
from app.schemas import ProblemIn, ProblemOut, ProblemSummary, TestCaseOut
from app.services import audit, functions

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
        companies=p.companies or [], starter_code=p.starter_code, signature=p.signature, is_global=p.tenant_id is None,
        sample_tests=[TestCaseOut.model_validate(t) for t in p.tests if not t.is_hidden],
        hidden_test_count=sum(t.is_hidden for t in p.tests),
    )


@router.get("", response_model=list[ProblemSummary])
async def list_problems(user: CurrentUser, db: DB, difficulty: str | None = None, tag: str | None = None,
                        company: str | None = None):
    q = select(Problem).where(visible_to(user)).order_by(Problem.created_at)
    if difficulty:
        q = q.where(Problem.difficulty == difficulty)
    problems = (await db.scalars(q)).all()
    if tag:
        problems = [p for p in problems if tag in p.tags]
    if company:
        problems = [p for p in problems if company.lower() in (c.lower() for c in (p.companies or []))]
    solved = set(
        (await db.scalars(
            select(Submission.problem_id).where(Submission.user_id == user.id, Submission.verdict == "accepted")
        )).all()
    )
    return [
        ProblemSummary(id=p.id, title=p.title, difficulty=p.difficulty, tags=p.tags, companies=p.companies or [],
                       is_global=p.tenant_id is None, solved=p.id in solved,
                       style="function" if p.signature else "stdio")
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
    signature = None
    if body.signature is not None:
        try:
            sig = functions.Signature(**body.signature)
            for n, t in enumerate(body.tests, 1):
                try:
                    functions.check_test(sig, t.input, t.expected_output)
                except ValueError as e:
                    raise ValueError(f"Test {n}: {e}")
        except (ValueError, TypeError) as e:  # pydantic's ValidationError is a ValueError
            raise HTTPException(422, f"Invalid function signature or tests: {e}")
        signature = sig.model_dump()
    problem.title = body.title
    problem.description = body.description
    problem.difficulty = body.difficulty
    problem.tags = body.tags
    problem.companies = body.companies
    problem.signature = signature
    # Function problems get generated starter code unless the author wrote their own.
    problem.starter_code = body.starter_code or (functions.starter_code(sig) if signature else {})
    problem.tests = [TestCase(position=i, **t.model_dump()) for i, t in enumerate(body.tests)]


@router.post("", response_model=ProblemOut, status_code=201)
async def create_problem(body: ProblemIn, user: MentorUser, db: DB):
    problem = Problem(tenant_id=user.tenant_id, created_by=user.id, title=body.title, description=body.description)
    _apply(problem, body)
    db.add(problem)
    await db.flush()
    audit.record(db, user, "problem.created", target_type="problem", target_id=problem.id, target_label=problem.title)
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
    audit.record(db, user, "problem.updated", target_type="problem", target_id=problem.id, target_label=problem.title)
    await db.commit()
    await db.refresh(problem)
    return to_out(problem)


@router.delete("/{problem_id}", status_code=204)
async def delete_problem(problem_id: str, user: MentorUser, db: DB):
    problem = await _get_owned(db, problem_id, user)
    audit.record(db, user, "problem.deleted", target_type="problem", target_id=problem.id, target_label=problem.title)
    await db.delete(problem)
    await db.commit()
