"""Problem discussions and editorials, scoped to the organization.

Both stay hidden until you've solved the problem - or chose to reveal them, which is recorded (and
shown to mentors as an integrity hint if you later solve it). During a running contest they are
locked for its problems. Mentors can always read them, write the editorial and moderate posts.
"""
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import DB, ROLE_RANK, CurrentUser, MentorUser
from app.api.routes.problems import get_visible_problem
from app.api.routes.submissions import problem_prompt
from app.db.models import Discussion, DiscussionVote, Editorial, ProblemReveal, Role, Submission, User
from app.services import quota
from app.services.contests import locked_by_contest
from app.services.llm import LLMError, LLMProvider, get_llm
from app.services.notifications import notify

router = APIRouter(tags=["discussions"])
LLMDep = Annotated[LLMProvider, Depends(get_llm)]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _staff(user: User) -> bool:
    return ROLE_RANK[Role(user.role)] >= ROLE_RANK[Role.mentor]


async def access(db, user: User, problem_id: str) -> dict:
    """Whether this user may see discussions/editorial for the problem, and why (not)."""
    if contest := await locked_by_contest(db, user, problem_id):
        return {"unlocked": False, "reason": "contest", "contest": {"id": contest.id, "title": contest.title}}
    if _staff(user):
        return {"unlocked": True, "reason": "staff"}
    if await db.scalar(select(Submission.id).where(Submission.user_id == user.id, Submission.problem_id == problem_id,
                                                   Submission.verdict == "accepted").limit(1)):
        return {"unlocked": True, "reason": "solved"}
    if await db.scalar(select(ProblemReveal.id).where(ProblemReveal.user_id == user.id,
                                                      ProblemReveal.problem_id == problem_id)):
        return {"unlocked": True, "reason": "revealed"}
    return {"unlocked": False, "reason": "unsolved"}


async def _require_unlocked(db, user: User, problem_id: str) -> None:
    a = await access(db, user, problem_id)
    if not a["unlocked"]:
        msg = "Locked while the contest is running" if a["reason"] == "contest" else "Solve the problem (or reveal) first"
        raise HTTPException(status.HTTP_403_FORBIDDEN, msg)


def _post(d: Discussion, my_votes: dict[str, int], viewer: User) -> dict:
    deleted = d.deleted_at is not None
    return {"id": d.id, "parent_id": d.parent_id, "body": "[deleted]" if deleted else d.body, "deleted": deleted,
            "author": None if deleted else {"id": d.author.id, "name": d.author.full_name, "role": d.author.role},
            "score": d.score, "my_vote": my_votes.get(d.id, 0), "created_at": d.created_at,
            "edited": d.edited_at is not None,
            "can_edit": not deleted and d.user_id == viewer.id,
            "can_delete": not deleted and (d.user_id == viewer.id or _staff(viewer))}


@router.get("/problems/{problem_id}/discussions")
async def list_discussions(problem_id: str, user: CurrentUser, db: DB, sort: Literal["top", "new"] = "top"):
    await get_visible_problem(db, problem_id, user)
    a = await access(db, user, problem_id)
    total = await db.scalar(select(func.count(Discussion.id)).where(
        Discussion.tenant_id == user.tenant_id, Discussion.problem_id == problem_id, Discussion.parent_id.is_(None),
        Discussion.deleted_at.is_(None)))
    if not a["unlocked"]:
        return {**a, "count": total, "items": []}
    posts = (await db.scalars(select(Discussion).where(
        Discussion.tenant_id == user.tenant_id, Discussion.problem_id == problem_id))).all()
    votes = dict((await db.execute(select(DiscussionVote.discussion_id, DiscussionVote.value).where(
        DiscussionVote.user_id == user.id, DiscussionVote.discussion_id.in_([p.id for p in posts] or [""])))).all())
    replies: dict[str, list] = {}
    for p in sorted(posts, key=lambda p: p.created_at):
        if p.parent_id:
            replies.setdefault(p.parent_id, []).append(_post(p, votes, user))
    top = [p for p in posts if p.parent_id is None and (p.deleted_at is None or p.id in replies)]
    key = (lambda p: (-p.score, p.created_at)) if sort == "top" else (lambda p: p.created_at)
    top.sort(key=key, reverse=sort == "new")
    return {**a, "count": total, "items": [{**_post(p, votes, user), "replies": replies.get(p.id, [])} for p in top]}


@router.post("/problems/{problem_id}/reveal")
async def reveal(problem_id: str, user: CurrentUser, db: DB):
    """Unlock discussions and the editorial without solving first. Recorded for mentors."""
    await get_visible_problem(db, problem_id, user)
    if await locked_by_contest(db, user, problem_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Locked while the contest is running")
    if not await db.scalar(select(ProblemReveal.id).where(ProblemReveal.user_id == user.id,
                                                          ProblemReveal.problem_id == problem_id)):
        db.add(ProblemReveal(tenant_id=user.tenant_id, user_id=user.id, problem_id=problem_id))
        await db.commit()
    return await access(db, user, problem_id)


class PostIn(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    parent_id: str | None = None


@router.post("/problems/{problem_id}/discussions", status_code=201)
async def create_post(problem_id: str, body: PostIn, user: CurrentUser, db: DB):
    await get_visible_problem(db, problem_id, user)
    await _require_unlocked(db, user, problem_id)
    parent = None
    if body.parent_id:
        parent = await db.scalar(select(Discussion).where(
            Discussion.id == body.parent_id, Discussion.tenant_id == user.tenant_id, Discussion.problem_id == problem_id))
        if parent is None or parent.deleted_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That post doesn't exist")
        if parent.parent_id:
            parent = await db.get(Discussion, parent.parent_id)  # one level of replies keeps threads readable
    d = Discussion(tenant_id=user.tenant_id, problem_id=problem_id, user_id=user.id, body=body.body.strip(),
                   parent_id=parent.id if parent else None)
    db.add(d)
    if parent and parent.user_id != user.id:
        await notify(db, [parent.user_id], user.tenant_id, kind="discussion",
                     title=f"{user.full_name} replied to your post", body=body.body[:200],
                     link=f"/problems/{problem_id}?tab=discussion")
    await db.commit()
    await db.refresh(d)
    return _post(d, {}, user)


class EditIn(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)


async def _own_post(db, discussion_id: str, user: User) -> Discussion:
    d = await db.scalar(select(Discussion).where(Discussion.id == discussion_id, Discussion.tenant_id == user.tenant_id))
    if d is None or d.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post not found")
    return d


@router.patch("/discussions/{discussion_id}")
async def edit_post(discussion_id: str, body: EditIn, user: CurrentUser, db: DB):
    d = await _own_post(db, discussion_id, user)
    if d.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only edit your own posts")
    d.body, d.edited_at = body.body.strip(), _now()
    await db.commit()
    return _post(d, {}, user)


@router.delete("/discussions/{discussion_id}", status_code=204)
async def delete_post(discussion_id: str, user: CurrentUser, db: DB):
    d = await _own_post(db, discussion_id, user)
    if d.user_id != user.id and not _staff(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only delete your own posts")
    has_replies = await db.scalar(select(Discussion.id).where(Discussion.parent_id == d.id).limit(1))
    if has_replies:
        d.deleted_at = _now()  # keep the thread readable: "[deleted]" with its replies
    else:
        await db.delete(d)
    await db.commit()


class VoteIn(BaseModel):
    value: Literal[-1, 0, 1]


@router.post("/discussions/{discussion_id}/vote")
async def vote(discussion_id: str, body: VoteIn, user: CurrentUser, db: DB):
    d = await _own_post(db, discussion_id, user)
    await _require_unlocked(db, user, d.problem_id)
    if d.user_id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't vote on your own post")
    existing = await db.scalar(select(DiscussionVote).where(DiscussionVote.discussion_id == d.id,
                                                            DiscussionVote.user_id == user.id))
    if existing:
        if body.value == 0:
            await db.delete(existing)
        else:
            existing.value = body.value
    elif body.value:
        db.add(DiscussionVote(discussion_id=d.id, user_id=user.id, value=body.value))
    await db.flush()
    d.score = await db.scalar(select(func.coalesce(func.sum(DiscussionVote.value), 0)).where(
        DiscussionVote.discussion_id == d.id))
    await db.commit()
    return {"id": d.id, "score": d.score, "my_vote": body.value}


# ---------- editorial ----------
@router.get("/problems/{problem_id}/editorial")
async def get_editorial(problem_id: str, user: CurrentUser, db: DB):
    await get_visible_problem(db, problem_id, user)
    a = await access(db, user, problem_id)
    e = await db.scalar(select(Editorial).where(Editorial.tenant_id == user.tenant_id, Editorial.problem_id == problem_id))
    if not a["unlocked"]:
        return {**a, "exists": e is not None, "editorial": None}
    author = await db.get(User, e.author_id) if e and e.author_id else None
    return {**a, "exists": e is not None, "editorial": None if e is None else {
        "body": e.body, "author": author.full_name if author else None, "updated_at": e.updated_at}}


class EditorialIn(BaseModel):
    body: str = Field(min_length=1, max_length=50_000)


@router.put("/problems/{problem_id}/editorial")
async def save_editorial(problem_id: str, body: EditorialIn, user: MentorUser, db: DB):
    await get_visible_problem(db, problem_id, user)
    e = await db.scalar(select(Editorial).where(Editorial.tenant_id == user.tenant_id, Editorial.problem_id == problem_id))
    if e is None:
        e = Editorial(tenant_id=user.tenant_id, problem_id=problem_id, author_id=user.id, body=body.body)
        db.add(e)
    else:
        e.body, e.author_id, e.updated_at = body.body, user.id, _now()
    await db.commit()
    return await get_editorial(problem_id, user, db)


@router.delete("/problems/{problem_id}/editorial", status_code=204)
async def delete_editorial(problem_id: str, user: MentorUser, db: DB):
    e = await db.scalar(select(Editorial).where(Editorial.tenant_id == user.tenant_id, Editorial.problem_id == problem_id))
    if e:
        await db.delete(e)
        await db.commit()


@router.post("/problems/{problem_id}/editorial/draft")
async def draft_editorial(problem_id: str, user: MentorUser, db: DB, llm: LLMDep):
    """An AI-written first draft for mentors to edit (not saved until they save it)."""
    problem = await get_visible_problem(db, problem_id, user)
    await quota.consume(db, user, "llm")
    await db.commit()
    try:
        return {"body": await llm.editorial(problem_prompt(problem))}
    except LLMError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
