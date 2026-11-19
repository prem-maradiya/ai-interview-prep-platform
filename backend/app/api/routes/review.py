"""Mentor review tools: comments, member activity, integrity signals, cohort CSV export."""
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DB, ROLE_RANK, CurrentUser, MentorUser, get_scoped
from app.api.routes.analytics import org_overview
from app.db.models import Comment, Interview, Problem, Role, Submission, User
from app.schemas import CommentIn, CommentOut, InterviewSummary, StaffSubmissionOut
from app.services.csv_export import csv_response

router = APIRouter(tags=["review"])

TARGETS = {"interview": Interview, "submission": Submission}


def _is_staff(user: User) -> bool:
    return ROLE_RANK[Role(user.role)] >= ROLE_RANK[Role.mentor]


async def _check_target(db, target_type: str, target_id: str, user: User):
    """The target must be in the user's tenant, and the user must own it or be a mentor+."""
    target = await get_scoped(db, TARGETS[target_type], target_id, user.tenant_id)
    if target.user_id != user.id and not _is_staff(user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{target_type.title()} not found")
    return target


def _out(c: Comment) -> CommentOut:
    return CommentOut(id=c.id, author_id=c.author_id, author_name=c.author.full_name, author_role=c.author.role,
                      body=c.body, created_at=c.created_at)


@router.get("/comments", response_model=list[CommentOut])
async def list_comments(target_type: str, target_id: str, user: CurrentUser, db: DB):
    if target_type not in TARGETS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "target_type must be interview or submission")
    await _check_target(db, target_type, target_id, user)
    rows = (await db.scalars(select(Comment).where(
        Comment.tenant_id == user.tenant_id, Comment.target_type == target_type, Comment.target_id == target_id)
        .order_by(Comment.created_at))).all()
    return [_out(c) for c in rows]


@router.post("/comments", response_model=CommentOut, status_code=201)
async def add_comment(body: CommentIn, user: CurrentUser, db: DB):
    """Mentors leave feedback; the candidate who owns the item can reply."""
    await _check_target(db, body.target_type, body.target_id, user)
    c = Comment(tenant_id=user.tenant_id, author_id=user.id, target_type=body.target_type,
                target_id=body.target_id, body=body.body.strip())
    db.add(c)
    await db.commit()
    c = await db.scalar(select(Comment).where(Comment.id == c.id))  # reload with author
    return _out(c)


@router.delete("/comments/{comment_id}", status_code=204)
async def delete_comment(comment_id: str, user: CurrentUser, db: DB):
    c = await get_scoped(db, Comment, comment_id, user.tenant_id)
    if c.author_id != user.id and ROLE_RANK[Role(user.role)] < ROLE_RANK[Role.admin]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only delete your own comments")
    await db.delete(c)
    await db.commit()


@router.get("/org/members/{member_id}/activity")
async def member_activity(member_id: str, user: MentorUser, db: DB):
    """A member's recent submissions (with integrity signals) and interviews, for mentor review."""
    member = await get_scoped(db, User, member_id, user.tenant_id)
    subs = (await db.scalars(select(Submission).where(Submission.user_id == member.id)
                             .order_by(Submission.created_at.desc()).limit(50))).all()
    titles = dict((await db.execute(select(Problem.id, Problem.title).where(
        Problem.id.in_({s.problem_id for s in subs})))).all()) if subs else {}
    ivs = (await db.scalars(select(Interview).where(Interview.user_id == member.id)
                            .order_by(Interview.created_at.desc()).limit(50))).all()
    return {
        "member": {"id": member.id, "full_name": member.full_name, "email": member.email, "role": member.role},
        "submissions": [{**StaffSubmissionOut.model_validate(s).model_dump(exclude={"code", "results"}),
                         "problem_title": titles.get(s.problem_id, "")} for s in subs],
        "interviews": [InterviewSummary.model_validate(i) for i in ivs],
    }


@router.get("/integrity")
async def integrity_report(user: MentorUser, db: DB, limit: int = 100):
    """Recent submissions in the org that raised integrity signals, newest first."""
    rows = (await db.execute(
        select(Submission, User.full_name, Problem.title)
        .join(User, User.id == Submission.user_id).join(Problem, Problem.id == Submission.problem_id)
        .where(Submission.tenant_id == user.tenant_id, Submission.integrity_flags.is_not(None))
        .order_by(Submission.created_at.desc()).limit(min(limit, 500))
    )).all()
    rank = {"high": 0, "medium": 1, "low": 2}
    return [{
        "submission_id": s.id, "user_id": s.user_id, "full_name": name, "problem_title": title,
        "verdict": s.verdict, "created_at": s.created_at,
        "flags": sorted(s.integrity_flags or [], key=lambda f: rank.get(f.get("severity"), 3)),
    } for s, name, title in rows if s.integrity_flags]


@router.get("/analytics/org/export.csv")
async def export_cohort(user: MentorUser, db: DB):
    data = await org_overview(user, db)
    header = ["Name", "Email", "Role", "Problems solved", "Submissions", "Interviews completed", "Avg interview score"]
    rows = [[r["full_name"], r["email"], r["role"], r["problems_solved"], r["submissions"],
             r["interviews_completed"], r["avg_interview_score"]] for r in data["leaderboard"]]
    return csv_response("cohort-report.csv", header, rows)
