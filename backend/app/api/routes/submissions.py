from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import DB, ROLE_RANK, CurrentUser, get_scoped
from app.api.routes.problems import get_visible_problem
from app.db.models import Role, Submission
from app.schemas import HintIn, RunIn, RunOut, SubmissionOut, TextOut
from app.services import quota
from app.services.llm import LLMError, LLMProvider, get_llm
from app.services.sandbox import Sandbox, SandboxError, get_sandbox, grade

router = APIRouter(tags=["submissions"])

SandboxDep = Annotated[Sandbox, Depends(get_sandbox)]
LLMDep = Annotated[LLMProvider, Depends(get_llm)]


def problem_prompt(problem) -> str:
    return f"{problem.title}\n\n{problem.description}"


async def _execute(sandbox: Sandbox, language: str, code: str, inputs: list[str]):
    try:
        return await sandbox.execute(language, code, inputs)
    except SandboxError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))


@router.post("/problems/{problem_id}/run", response_model=RunOut)
async def run_code(problem_id: str, body: RunIn, user: CurrentUser, db: DB, sandbox: SandboxDep):
    """Run against sample tests only (or a custom input). Nothing is persisted."""
    problem = await get_visible_problem(db, problem_id, user)
    await quota.consume(db, user, "sandbox")
    await db.commit()
    if body.custom_input is not None:
        inputs, expected, hidden = [body.custom_input], [None], [False]
    else:
        samples = [t for t in problem.tests if not t.is_hidden]
        inputs, expected, hidden = [t.input for t in samples], [t.expected_output for t in samples], [False] * len(samples)
    raw = await _execute(sandbox, body.language, body.code, inputs)
    return grade(raw, expected, hidden, inputs)


@router.post("/problems/{problem_id}/submit", response_model=SubmissionOut, status_code=201)
async def submit_code(problem_id: str, body: RunIn, user: CurrentUser, db: DB, sandbox: SandboxDep):
    """Judge against all tests (sample + hidden) and record the submission."""
    problem = await get_visible_problem(db, problem_id, user)
    await quota.consume(db, user, "sandbox")
    tests = problem.tests
    inputs = [t.input for t in tests]
    raw = await _execute(sandbox, body.language, body.code, inputs)
    result = grade(raw, [t.expected_output for t in tests], [t.is_hidden for t in tests], inputs)
    sub = Submission(
        tenant_id=user.tenant_id, user_id=user.id, problem_id=problem.id, language=body.language, code=body.code,
        verdict=result["verdict"], passed=result["passed"], total=result["total"],
        runtime_ms=result["runtime_ms"], results=result["results"],
    )
    db.add(sub)
    await db.commit()
    return sub


@router.get("/submissions", response_model=list[SubmissionOut])
async def list_submissions(user: CurrentUser, db: DB, problem_id: str | None = None, user_id: str | None = None, limit: int = 50):
    q = select(Submission).where(Submission.tenant_id == user.tenant_id)
    if user_id and user_id != user.id:
        if ROLE_RANK[Role(user.role)] < ROLE_RANK[Role.mentor]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only mentors can view other members' submissions")
        q = q.where(Submission.user_id == user_id)
    else:
        q = q.where(Submission.user_id == user.id)
    if problem_id:
        q = q.where(Submission.problem_id == problem_id)
    return (await db.scalars(q.order_by(Submission.created_at.desc()).limit(min(limit, 200)))).all()


async def _get_submission(db, submission_id: str, user) -> Submission:
    sub = await get_scoped(db, Submission, submission_id, user.tenant_id)
    if sub.user_id != user.id and ROLE_RANK[Role(user.role)] < ROLE_RANK[Role.mentor]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    return sub


@router.get("/submissions/{submission_id}", response_model=SubmissionOut)
async def get_submission(submission_id: str, user: CurrentUser, db: DB):
    return await _get_submission(db, submission_id, user)


@router.post("/submissions/{submission_id}/review", response_model=SubmissionOut)
async def review_submission(submission_id: str, user: CurrentUser, db: DB, llm: LLMDep):
    """AI code review: correctness, complexity, style, one improvement. Cached on the submission."""
    sub = await _get_submission(db, submission_id, user)
    if sub.ai_review:
        return sub
    problem = await get_visible_problem(db, sub.problem_id, user)
    await quota.consume(db, user, "llm")
    try:
        sub.ai_review = await llm.code_review(problem_prompt(problem), sub.language, sub.code, sub.verdict)
    except LLMError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    await db.commit()
    return sub


@router.post("/problems/{problem_id}/hint", response_model=TextOut)
async def get_hint(problem_id: str, body: HintIn, user: CurrentUser, db: DB, llm: LLMDep):
    """Progressive hints: level 1 nudge -> level 3 pseudocode. Never a full solution."""
    problem = await get_visible_problem(db, problem_id, user)
    await quota.consume(db, user, "llm")
    await db.commit()
    try:
        return TextOut(text=await llm.hint(problem_prompt(problem), body.language, body.code, body.level))
    except LLMError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
