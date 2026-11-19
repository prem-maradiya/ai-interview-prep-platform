from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, get_scoped
from app.db.models import Interview, InterviewMessage
from app.schemas import InterviewIn, InterviewOut, InterviewSummary, MessageIn, MessageOut
from app.services import quota
from app.services.llm import InterviewContext, LLMError, LLMProvider, Turn, get_llm

router = APIRouter(prefix="/interviews", tags=["interviews"])
LLMDep = Annotated[LLMProvider, Depends(get_llm)]

MAX_CANDIDATE_TURNS = 20


def _ctx(iv: Interview) -> InterviewContext:
    return InterviewContext(kind=iv.kind, role_title=iv.role_title, level=iv.level, focus=iv.focus)


def _history(iv: Interview) -> list[Turn]:
    return [Turn(m.sender, m.content) for m in iv.messages]


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


@router.post("", response_model=InterviewOut, status_code=201)
async def start_interview(body: InterviewIn, user: CurrentUser, db: DB, llm: LLMDep):
    await quota.consume(db, user, "llm")
    iv = Interview(tenant_id=user.tenant_id, user_id=user.id, **body.model_dump())
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
    return await _own_interview(db, interview_id, user)


@router.post("/{interview_id}/messages", response_model=list[MessageOut])
async def send_message(interview_id: str, body: MessageIn, user: CurrentUser, db: DB, llm: LLMDep):
    """Send the candidate's answer; returns the candidate message and the interviewer's reply."""
    iv = await _own_interview(db, interview_id, user)
    if iv.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "Interview already completed")
    if sum(m.sender == "candidate" for m in iv.messages) >= MAX_CANDIDATE_TURNS:
        raise HTTPException(status.HTTP_409_CONFLICT, "Turn limit reached - end the interview to get feedback")
    await quota.consume(db, user, "llm")
    seq = len(iv.messages)
    answer = InterviewMessage(seq=seq, sender="candidate", content=body.content)
    iv.messages.append(answer)
    reply = InterviewMessage(seq=seq + 1, sender="interviewer", content=await _ask(llm, iv))
    iv.messages.append(reply)
    await db.commit()
    return [answer, reply]


@router.post("/{interview_id}/end", response_model=InterviewOut)
async def end_interview(interview_id: str, user: CurrentUser, db: DB, llm: LLMDep):
    """Finish the interview and generate a rubric-based scorecard."""
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
    iv.scorecard = card.model_dump()
    iv.overall_score = card.overall_score
    iv.status = "completed"
    iv.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(iv)
    return iv
