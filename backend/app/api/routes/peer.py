"""Peer mock interviews: two members take turns interviewing each other on a coding problem.

Flow: a member opens a room (open to the organization, or for one invited member) -> a teammate
joins -> round 1 (one is the candidate, the other interviews) -> swap -> round 2 -> end -> each
interviewer rates their candidate.

Live parts go over a WebSocket (/api/peer/{id}/ws): the candidate's code, chat, run results and
who's online. Only the current candidate can edit code, so there are never two writers. Everything
is stored as it happens, so a reload or dropped connection loses nothing.
"""
import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

import jwt
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, update

from app.api.deps import DB, ROLE_RANK, CurrentUser
from app.api.routes.problems import to_out, visible_to
from app.api.routes.submissions import _execute, signature_of
from app.core.security import decode_token
from app.db.models import PeerFeedback, PeerMessage, PeerSession, Problem, Role, Submission, User
from app.db.session import tenant_session
from app.schemas import Language
from app.services import quota
from app.services.notifications import notify
from app.services import whiteboard
from app.services.rooms import Connection, get_hub
from app.services.sandbox import Sandbox, get_sandbox, grade

router = APIRouter(prefix="/peer", tags=["peer interviews"])
SandboxDep = Annotated[Sandbox, Depends(get_sandbox)]

OPEN_FOR = timedelta(hours=24)  # open rooms nobody joined disappear from the lobby after this
MAX_CODE = 50_000
MAX_CHAT = 2_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _is_staff(user: User) -> bool:
    return ROLE_RANK[Role(user.role)] >= ROLE_RANK[Role.mentor]


def candidate_id(s: PeerSession, round_: int | None = None) -> str | None:
    r = round_ or s.round
    host_candidate = s.host_first if r == 1 else not s.host_first
    return s.host_id if host_candidate else s.guest_id


def interviewer_id(s: PeerSession, round_: int | None = None) -> str | None:
    r = round_ or s.round
    return s.guest_id if candidate_id(s, r) == s.host_id else s.host_id


def _person(u: User | None) -> dict | None:
    return {"id": u.id, "name": u.full_name} if u else None


def _role(s: PeerSession, user: User) -> str:
    if user.id == candidate_id(s):
        return "candidate"
    if user.id in (s.host_id, s.guest_id):
        return "interviewer"
    return "viewer"


async def _load(db, session_id: str, user: User) -> PeerSession:
    s = await db.scalar(select(PeerSession).where(PeerSession.id == session_id, PeerSession.tenant_id == user.tenant_id))
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Peer interview not found")
    participant = user.id in (s.host_id, s.guest_id)
    can_join = s.status == "open" and s.invited_id in (None, user.id)
    if not (participant or can_join or _is_staff(user)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Peer interview not found")
    return s


def _feedback_out(f: PeerFeedback) -> dict:
    return {"round": f.round, "from_id": f.from_id, "to_id": f.to_id, "problem_solving": f.problem_solving,
            "communication": f.communication, "code_quality": f.code_quality, "hire": f.hire,
            "comments": f.comments, "created_at": f.created_at}


async def _state(db, s: PeerSession, user: User) -> dict:
    """Everything the room page needs, from this viewer's point of view."""
    role = _role(s, user)
    problems = {p.id: p for p in (await db.scalars(select(Problem).where(Problem.id.in_(s.problem_ids)))).all()}
    current = problems.get(s.problem_ids[s.round - 1]) if len(s.problem_ids) >= s.round else None
    invited = await db.get(User, s.invited_id) if s.invited_id else None
    messages = (await db.scalars(select(PeerMessage).where(PeerMessage.session_id == s.id)
                                 .order_by(PeerMessage.created_at).limit(500))).all()
    feedback = (await db.scalars(select(PeerFeedback).where(PeerFeedback.session_id == s.id))).all()
    # You see feedback you gave or received; mentors see all of it.
    visible = [f for f in feedback if _is_staff(user) or user.id in (f.from_id, f.to_id)]
    round_code = (s.code or {}).get(str(s.round)) or {}
    return {
        "id": s.id, "status": s.status, "round": s.round, "my_role": role, "me": user.id,
        "host": _person(s.host), "guest": _person(s.guest), "invited": _person(invited),
        "candidate_id": candidate_id(s), "interviewer_id": interviewer_id(s),
        "problem": to_out(current).model_dump(mode="json") if current else None,
        "problems": [{"round": i + 1, "id": pid, "title": problems[pid].title if pid in problems else "?",
                      "candidate_id": candidate_id(s, i + 1)} for i, pid in enumerate(s.problem_ids)],
        "code": {"language": round_code.get("language", "python"), "code": round_code.get("code")},
        "notes": (s.notes or {}).get(str(s.round), "") if role == "interviewer" else None,
        "board": s.board or {"elements": []},
        "messages": [{"id": m.id, "sender_id": m.sender_id, "round": m.round, "text": m.text,
                      "created_at": m.created_at} for m in messages],
        "feedback": [_feedback_out(f) for f in visible],
        "feedback_given": sorted({f.round for f in feedback if f.from_id == user.id}),
        "online": await get_hub().online(s.id),
        "created_at": s.created_at, "started_at": s.started_at, "ended_at": s.ended_at,
    }


def _summary(s: PeerSession, user: User, feedback: list[PeerFeedback]) -> dict:
    partner = s.guest if s.host_id == user.id else s.host
    mine = [f for f in feedback if f.session_id == s.id and f.to_id == user.id]
    return {"id": s.id, "status": s.status, "round": s.round, "host": _person(s.host), "partner": _person(partner),
            "invited_id": s.invited_id, "created_at": s.created_at, "ended_at": s.ended_at,
            "my_feedback": [_feedback_out(f) for f in mine]}


async def _changed(s: PeerSession) -> None:
    """Tell everyone in the room to refresh their view (roles, round or status changed)."""
    await get_hub().publish(s.id, {"type": "changed", "status": s.status, "round": s.round})


# ---------- lobby ----------
class CreateIn(BaseModel):
    invite_user_id: str | None = None
    difficulty: Literal["easy", "medium", "hard"] | None = None
    host_first: bool = True  # True: the host solves first; False: the host interviews first


async def _pick_problems(db, user: User, difficulty: str | None) -> list[str]:
    q = select(Problem).where(visible_to(user))
    if difficulty:
        q = q.where(Problem.difficulty == difficulty)
    pool = list((await db.scalars(q)).all())
    if len(pool) < 2:
        pool = list((await db.scalars(select(Problem).where(visible_to(user)))).all())
    if len(pool) < 2:
        raise HTTPException(status.HTTP_409_CONFLICT, "Add at least two problems first")
    solved = set((await db.scalars(select(Submission.problem_id).where(
        Submission.user_id == user.id, Submission.verdict == "accepted"))).all())
    fresh = [p for p in pool if p.id not in solved] or pool  # prefer problems the host hasn't solved
    first = random.choice(fresh)
    second = random.choice([p for p in pool if p.id != first.id])
    return [first.id, second.id]


@router.post("", status_code=201)
async def create_session(body: CreateIn, user: CurrentUser, db: DB):
    invited = None
    if body.invite_user_id:
        invited = await db.scalar(select(User).where(User.id == body.invite_user_id, User.tenant_id == user.tenant_id,
                                                     User.is_active.is_(True)))
        if invited is None or invited.id == user.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose another member of your organization")
    s = PeerSession(tenant_id=user.tenant_id, host_id=user.id, invited_id=invited.id if invited else None,
                    host_first=body.host_first, problem_ids=await _pick_problems(db, user, body.difficulty),
                    code={}, notes={})
    db.add(s)
    await db.flush()
    if invited:
        await notify(db, [invited.id], user.tenant_id, kind="peer", title=f"{user.full_name} invited you to a peer interview",
                     body="Join the room to take turns interviewing each other.", link=f"/peer/{s.id}")
    await db.commit()
    return {"id": s.id}


@router.get("")
async def list_sessions(user: CurrentUser, db: DB, user_id: str | None = None):
    """The lobby (open rooms you can join) and your recent peer interviews. Mentors can pass user_id."""
    subject = user
    if user_id and user_id != user.id:
        if not _is_staff(user):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only mentors can view other members' peer interviews")
        subject = await db.scalar(select(User).where(User.id == user_id, User.tenant_id == user.tenant_id))
        if subject is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    open_rooms = [] if subject is not user else (await db.scalars(select(PeerSession).where(
        PeerSession.tenant_id == user.tenant_id, PeerSession.status == "open", PeerSession.host_id != user.id,
        or_(PeerSession.invited_id.is_(None), PeerSession.invited_id == user.id),
        PeerSession.created_at >= _now() - OPEN_FOR).order_by(PeerSession.created_at.desc()).limit(50))).all()
    mine = (await db.scalars(select(PeerSession).where(
        PeerSession.tenant_id == user.tenant_id, or_(PeerSession.host_id == subject.id, PeerSession.guest_id == subject.id),
        PeerSession.status != "cancelled").order_by(PeerSession.created_at.desc()).limit(30))).all()
    feedback = (await db.scalars(select(PeerFeedback).where(
        PeerFeedback.session_id.in_([s.id for s in mine]), PeerFeedback.to_id == subject.id))).all() if mine else []
    return {"open": [_summary(s, user, []) for s in open_rooms], "mine": [_summary(s, subject, feedback) for s in mine]}


@router.get("/members")
async def list_partners(user: CurrentUser, db: DB):
    """Members you can invite (names only, so candidates can use it too)."""
    rows = (await db.scalars(select(User).where(User.tenant_id == user.tenant_id, User.is_active.is_(True),
                                                User.id != user.id).order_by(User.full_name))).all()
    return [{"id": u.id, "name": u.full_name, "role": u.role} for u in rows]


@router.get("/{session_id}")
async def get_session(session_id: str, user: CurrentUser, db: DB):
    return await _state(db, await _load(db, session_id, user), user)


@router.post("/{session_id}/join")
async def join_session(session_id: str, user: CurrentUser, db: DB):
    s = await _load(db, session_id, user)
    if s.host_id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This is your own room")
    # Conditional update: if two people click Join at once, exactly one gets the seat.
    claimed = await db.execute(update(PeerSession).where(
        PeerSession.id == s.id, PeerSession.status == "open",
        or_(PeerSession.invited_id.is_(None), PeerSession.invited_id == user.id),
    ).values(guest_id=user.id, status="active", started_at=_now()).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Someone else already joined this room")
    await notify(db, [s.host_id], user.tenant_id, kind="peer", title=f"{user.full_name} joined your peer interview",
                 link=f"/peer/{s.id}")
    await db.commit()
    await db.refresh(s)
    await _changed(s)
    return await _state(db, s, user)


def _participant(s: PeerSession, user: User) -> None:
    if user.id not in (s.host_id, s.guest_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the two participants can do this")


@router.post("/{session_id}/cancel")
async def cancel_session(session_id: str, user: CurrentUser, db: DB):
    s = await _load(db, session_id, user)
    if s.host_id != user.id or s.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only the host can cancel a room nobody has joined yet")
    s.status = "cancelled"
    await db.commit()
    await _changed(s)
    return {"status": s.status}


@router.post("/{session_id}/swap")
async def swap_roles(session_id: str, user: CurrentUser, db: DB):
    s = await _load(db, session_id, user)
    _participant(s, user)
    if s.status != "active" or s.round != 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Roles can be swapped once, during round 1")
    s.round = 2
    await db.commit()
    await _changed(s)
    return await _state(db, s, user)


@router.post("/{session_id}/end")
async def end_session(session_id: str, user: CurrentUser, db: DB):
    s = await _load(db, session_id, user)
    _participant(s, user)
    if s.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "This peer interview isn't in progress")
    s.status, s.ended_at = "completed", _now()
    await db.commit()
    await _changed(s)
    return await _state(db, s, user)


class RunPeerIn(BaseModel):
    language: Language
    code: str = Field(max_length=MAX_CODE)
    custom_input: str | None = Field(default=None, max_length=10_000)


@router.post("/{session_id}/run")
async def run_peer_code(session_id: str, body: RunPeerIn, user: CurrentUser, db: DB, sandbox: SandboxDep):
    """The candidate runs the sample tests (or custom input); both people see the result."""
    s = await _load(db, session_id, user)
    if s.status != "active" or user.id != candidate_id(s):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the current candidate can run code")
    problem = await db.get(Problem, s.problem_ids[s.round - 1])
    await quota.consume(db, user, "sandbox")
    await db.commit()
    if body.custom_input is not None:
        inputs, expected = [body.custom_input], [None]
    else:
        samples = [t for t in problem.tests if not t.is_hidden]
        inputs, expected = [t.input for t in samples], [t.expected_output for t in samples]
    sig = signature_of(problem)
    raw = await _execute(sandbox, body.language, body.code, inputs, sig)
    result = grade(raw, expected, [False] * len(inputs), inputs, sig)
    await get_hub().publish(s.id, {"type": "run", "round": s.round, "by": user.id, "result": result})
    return result


class FeedbackIn(BaseModel):
    round: int = Field(ge=1, le=2)
    problem_solving: int = Field(ge=1, le=5)
    communication: int = Field(ge=1, le=5)
    code_quality: int = Field(ge=1, le=5)
    hire: Literal["strong_no", "no", "yes", "strong_yes"]
    comments: str = Field(default="", max_length=4000)


@router.post("/{session_id}/feedback", status_code=201)
async def leave_feedback(session_id: str, body: FeedbackIn, user: CurrentUser, db: DB):
    s = await _load(db, session_id, user)
    if user.id != interviewer_id(s, body.round) or s.guest_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only that round's interviewer can rate it")
    finished = s.status == "completed" or (s.status == "active" and body.round < s.round)
    if not finished:
        raise HTTPException(status.HTTP_409_CONFLICT, "Rate a round once it's over")
    if await db.scalar(select(PeerFeedback).where(PeerFeedback.session_id == s.id, PeerFeedback.round == body.round)):
        raise HTTPException(status.HTTP_409_CONFLICT, "You already rated this round")
    to_id = candidate_id(s, body.round)
    db.add(PeerFeedback(session_id=s.id, from_id=user.id, to_id=to_id, **body.model_dump()))
    await notify(db, [to_id], s.tenant_id, kind="peer", title=f"{user.full_name} rated your peer interview",
                 body=body.comments[:300], link=f"/peer/{s.id}")
    await db.commit()
    await _changed(s)
    return await _state(db, s, user)


# ---------- live room ----------
@router.websocket("/{session_id}/ws")
async def room_socket(websocket: WebSocket, session_id: str):
    """First message must be {"type": "auth", "token": <access token>} (tokens never go in URLs)."""
    await websocket.accept()
    try:
        first = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        claims = decode_token(str(first.get("token", "")), "access") if first.get("type") == "auth" else None
    except (asyncio.TimeoutError, jwt.PyJWTError, ValueError, WebSocketDisconnect, TypeError):
        claims = None
    if claims is None:
        await websocket.close(code=4401)
        return
    tenant_id, user_id = claims.get("tid", ""), claims.get("sub", "")
    async with tenant_session(tenant_id) as db:
        user = await db.get(User, user_id)
        s = await db.scalar(select(PeerSession).where(PeerSession.id == session_id, PeerSession.tenant_id == tenant_id))
        allowed = (user is not None and user.is_active and s is not None
                   and (user.id in (s.host_id, s.guest_id) or _is_staff(user)
                        or (s.status == "open" and s.invited_id in (None, user.id))))
        if not allowed:
            await websocket.close(code=4403)
            return
        await websocket.send_text(json.dumps({"type": "state", "state": await _state(db, s, user)}, default=str))

    hub, conn = get_hub(), Connection(websocket, user_id)
    await hub.join(session_id, conn)
    try:
        while True:
            msg = await websocket.receive_json()
            await _handle(msg, session_id, tenant_id, user_id, conn)
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        await hub.leave(session_id, conn)


async def _handle(msg: dict, session_id: str, tenant_id: str, user_id: str, conn: Connection) -> None:
    kind = msg.get("type")
    if kind == "ping":
        await conn.send({"type": "pong"})
        return
    if kind not in ("code", "chat", "notes", "board"):
        return
    async with tenant_session(tenant_id) as db:
        s = await db.get(PeerSession, session_id)
        if s is None or s.status != "active" or user_id not in (s.host_id, s.guest_id):
            return
        key = str(s.round)
        if kind == "code":
            code, language = str(msg.get("code", ""))[:MAX_CODE], str(msg.get("language", "python"))
            if user_id != candidate_id(s) or language not in ("python", "javascript", "java", "cpp", "go"):
                return
            s.code = {**(s.code or {}), key: {"language": language, "code": code}}  # new dict: JSON change is saved
            await db.commit()
            await get_hub().publish(session_id, {"type": "code", "round": s.round, "language": language, "code": code,
                                                 "from": user_id})
        elif kind == "chat":
            text = str(msg.get("text", "")).strip()[:MAX_CHAT]
            if not text:
                return
            m = PeerMessage(session_id=s.id, sender_id=user_id, round=s.round, text=text)
            db.add(m)
            await db.commit()
            await get_hub().publish(session_id, {"type": "chat", "message": {
                "id": m.id, "sender_id": user_id, "round": m.round, "text": text, "created_at": m.created_at}})
        elif kind == "board":
            # Both people can draw. Operations, not whole boards, so simultaneous edits to different
            # shapes don't overwrite each other.
            try:
                s.board = whiteboard.apply_ops(s.board, msg.get("ops"))
            except whiteboard.BoardError as e:
                await conn.send({"type": "error", "detail": f"Whiteboard: {e}"})
                return
            await db.commit()
            await get_hub().publish(session_id, {"type": "board", "ops": msg.get("ops"), "from": user_id})
        elif kind == "notes" and user_id == interviewer_id(s):
            s.notes = {**(s.notes or {}), key: str(msg.get("text", ""))[:10_000]}  # private: not broadcast
            await db.commit()
