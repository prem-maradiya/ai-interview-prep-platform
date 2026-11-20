import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import DB, ROLE_RANK, CurrentUser, get_scoped
from app.api.routes.documents import own_document
from app.db.models import Interview, InterviewMessage, Role
from app.db.session import tenant_session
from app.schemas import (
    InterviewIn, InterviewOut, InterviewRunIn, InterviewSummary, MessageIn, MessageOut, RunOut,
)
from app.services import whiteboard
from app.services import quota
from app.services.achievements import check_badges
from app.services.delivery import delivery_metrics
from app.services.llm import InterviewContext, LLMError, LLMProvider, Turn, get_llm
from app.services.sandbox import Sandbox, SandboxError, get_sandbox, grade

router = APIRouter(prefix="/interviews", tags=["interviews"])
LLMDep = Annotated[LLMProvider, Depends(get_llm)]
SandboxDep = Annotated[Sandbox, Depends(get_sandbox)]

MAX_CANDIDATE_TURNS = 20


def _ctx(iv: Interview) -> InterviewContext:
    return InterviewContext(kind=iv.kind, role_title=iv.role_title, level=iv.level, focus=iv.focus,
                            resume=iv.resume_text or "", job_description=iv.job_description_text or "")


def _history(iv: Interview) -> list[Turn]:
    return [Turn(m.sender, m.content, m.code, m.code_language, m.board_text) for m in iv.messages]


async def _own_interview(db, interview_id: str, user) -> Interview:
    iv = await get_scoped(db, Interview, interview_id, user.tenant_id)
    if iv.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview not found")
    return iv


async def _ask(llm: LLMProvider, iv: Interview) -> str:
    try:
        return await llm.interviewer_reply(_ctx(iv), _history(iv))
    except LLMError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))


def _check_can_answer(iv: Interview) -> None:
    if iv.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "Interview already completed")
    if sum(m.sender == "candidate" for m in iv.messages) >= MAX_CANDIDATE_TURNS:
        raise HTTPException(status.HTTP_409_CONFLICT, "Turn limit reached - end the interview to get feedback")


def _add_answer(iv: Interview, body: MessageIn) -> InterviewMessage:
    # Code is only attached when it changed since the last snapshot, to keep prompts small.
    code = body.code if body.code and body.code.strip() and body.code != iv.code else None
    answer = InterviewMessage(seq=len(iv.messages), sender="candidate", content=body.content,
                              input_mode=body.input_mode, code=code, code_language=body.code_language if code else None)
    if code:
        iv.code, iv.code_language = code, body.code_language
    if body.board is not None:
        try:
            board = whiteboard.clean_board(body.board)
        except whiteboard.BoardError as e:
            raise HTTPException(422, f"Whiteboard: {e}")
        if board != (iv.board or {"elements": []}):  # like code: only sent to the interviewer when it changed
            iv.board = board
            answer.board_text = whiteboard.describe(board) or "(the whiteboard is now empty)"
    iv.messages.append(answer)
    return answer


@router.post("", response_model=InterviewOut, status_code=201)
async def start_interview(body: InterviewIn, user: CurrentUser, db: DB, llm: LLMDep):
    """Start an interview, optionally tailored to one of your resumes and/or a job description."""
    resume = await own_document(db, body.resume_id, user, "resume") if body.resume_id else None
    jd = await own_document(db, body.job_description_id, user, "job_description") if body.job_description_id else None
    await quota.consume(db, user, "llm")
    iv = Interview(
        tenant_id=user.tenant_id, user_id=user.id, kind=body.kind, role_title=body.role_title, level=body.level,
        focus=body.focus, resume_text=resume.text if resume else None, job_description_text=jd.text if jd else None,
    )
    iv.messages = []
    first = await _ask(llm, iv)
    iv.messages.append(InterviewMessage(seq=0, sender="interviewer", content=first))
    db.add(iv)
    await db.commit()
    await db.refresh(iv)
    return iv


@router.get("", response_model=list[InterviewSummary])
async def list_interviews(user: CurrentUser, db: DB):
    q = select(Interview).where(Interview.tenant_id == user.tenant_id, Interview.user_id == user.id)
    return (await db.scalars(q.order_by(Interview.created_at.desc()))).all()


@router.get("/{interview_id}", response_model=InterviewOut)
async def get_interview(interview_id: str, user: CurrentUser, db: DB):
    """Owners see their interview; mentors+ can read any interview in their org (to review and comment).
    Only the owner can answer, run code or end it."""
    iv = await get_scoped(db, Interview, interview_id, user.tenant_id)
    if iv.user_id != user.id and ROLE_RANK[Role(user.role)] < ROLE_RANK[Role.mentor]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview not found")
    return iv


@router.post("/{interview_id}/messages", response_model=list[MessageOut])
async def send_message(interview_id: str, body: MessageIn, user: CurrentUser, db: DB, llm: LLMDep):
    """Send the candidate's answer (optionally with the editor's code); returns it plus the interviewer's reply."""
    iv = await _own_interview(db, interview_id, user)
    _check_can_answer(iv)
    await quota.consume(db, user, "llm")
    answer = _add_answer(iv, body)
    reply = InterviewMessage(seq=answer.seq + 1, sender="interviewer", content=await _ask(llm, iv))
    iv.messages.append(reply)
    await db.commit()
    return [answer, reply]


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/{interview_id}/messages/stream")
async def send_message_stream(interview_id: str, body: MessageIn, user: CurrentUser, db: DB, llm: LLMDep):
    """Same as /messages, but streams the interviewer's reply as Server-Sent Events.

    Events: `candidate` (saved answer), `delta` ({text}) repeated, then `done` (saved reply) or `error`.
    """
    iv = await _own_interview(db, interview_id, user)
    _check_can_answer(iv)
    await quota.consume(db, user, "llm")
    answer = _add_answer(iv, body)
    await db.commit()
    # Snapshot everything the stream needs: the request's DB session is not used after this point.
    ctx, history, iv_id, seq, tenant_id = _ctx(iv), _history(iv), iv.id, answer.seq, user.tenant_id
    answer_out = MessageOut.model_validate(answer).model_dump(mode="json")

    async def events():
        yield _sse("candidate", answer_out)
        parts: list[str] = []
        try:
            async for chunk in llm.interviewer_reply_stream(ctx, history):
                parts.append(chunk)
                yield _sse("delta", {"text": chunk})
        except LLMError as e:
            yield _sse("error", {"detail": str(e)})
            return
        async with tenant_session(tenant_id) as session:
            reply = InterviewMessage(interview_id=iv_id, seq=seq + 1, sender="interviewer", content="".join(parts).strip())
            session.add(reply)
            await session.commit()
            await session.refresh(reply)
            yield _sse("done", MessageOut.model_validate(reply).model_dump(mode="json"))

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{interview_id}/run", response_model=RunOut)
async def run_interview_code(interview_id: str, body: InterviewRunIn, user: CurrentUser, db: DB, sandbox: SandboxDep):
    """Run the interview editor's code on custom input (same sandbox as the judge)."""
    iv = await _own_interview(db, interview_id, user)
    if iv.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "Interview already completed")
    await quota.consume(db, user, "sandbox")
    await db.commit()
    try:
        raw = await sandbox.execute(body.language, body.code, [body.stdin])
    except SandboxError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))
    return grade(raw, [None], [False], [body.stdin])


@router.post("/{interview_id}/end", response_model=InterviewOut)
async def end_interview(interview_id: str, user: CurrentUser, db: DB, llm: LLMDep):
    """Finish the interview: rubric scorecard from the LLM, plus speaking metrics for voice answers."""
    iv = await _own_interview(db, interview_id, user)
    if iv.status == "completed":
        return iv
    if not any(m.sender == "candidate" for m in iv.messages):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Answer at least one question before ending")
    await quota.consume(db, user, "llm")
    try:
        card = await llm.scorecard(_ctx(iv), _history(iv))
    except LLMError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    scorecard = card.model_dump()
    delivery = delivery_metrics([m.content for m in iv.messages if m.sender == "candidate" and m.input_mode == "voice"])
    if delivery:
        scorecard["delivery"] = delivery
    iv.scorecard = scorecard
    iv.overall_score = card.overall_score
    iv.status = "completed"
    iv.completed_at = datetime.now(timezone.utc)
    await db.flush()
    await check_badges(db, user)
    await db.commit()
    await db.refresh(iv)
    return iv
