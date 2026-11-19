from fastapi import APIRouter
from sqlalchemy import distinct, func, or_, select

from app.api.deps import DB, CurrentUser, MentorUser
from app.db.models import Interview, Problem, Submission, User
from app.services.achievements import level_for, xp_for_user

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/me")
async def my_progress(user: CurrentUser, db: DB):
    solved_rows = (await db.execute(
        select(Problem.difficulty, func.count(distinct(Submission.problem_id)))
        .join(Problem, Problem.id == Submission.problem_id)
        .where(Submission.user_id == user.id, Submission.verdict == "accepted")
        .group_by(Problem.difficulty)
    )).all()
    total_problems = await db.scalar(
        select(func.count(Problem.id)).where(or_(Problem.tenant_id == user.tenant_id, Problem.tenant_id.is_(None)))
    )
    attempts = await db.scalar(select(func.count(Submission.id)).where(Submission.user_id == user.id))
    accepted = await db.scalar(select(func.count(Submission.id)).where(Submission.user_id == user.id, Submission.verdict == "accepted"))
    interviews = (await db.scalars(
        select(Interview).where(Interview.user_id == user.id, Interview.status == "completed").order_by(Interview.completed_at)
    )).all()

    # Weakest rubric dimensions across all completed interviews -> what to practice next.
    dim_scores: dict[str, list[int]] = {}
    for iv in interviews:
        for d in (iv.scorecard or {}).get("dimensions", []):
            dim_scores.setdefault(d["name"], []).append(d["score"])
    weakest = sorted(((n, sum(s) / len(s)) for n, s in dim_scores.items()), key=lambda x: x[1])[:3]

    return {
        "solved_by_difficulty": {d: c for d, c in solved_rows},
        "solved_total": sum(c for _, c in solved_rows),
        "total_problems": total_problems,
        "submissions": attempts,
        "acceptance_rate": round(accepted / attempts * 100, 1) if attempts else None,
        "interviews_completed": len(interviews),
        "interview_trend": [
            {"id": iv.id, "kind": iv.kind, "score": iv.overall_score, "date": iv.completed_at} for iv in interviews
        ],
        "focus_areas": [{"dimension": n, "avg_score": round(s, 2)} for n, s in weakest],
    }


@router.get("/org")
async def org_overview(user: MentorUser, db: DB):
    tid = user.tenant_id
    members = (await db.scalars(select(User).where(User.tenant_id == tid))).all()
    solved = dict((await db.execute(
        select(Submission.user_id, func.count(distinct(Submission.problem_id)))
        .where(Submission.tenant_id == tid, Submission.verdict == "accepted").group_by(Submission.user_id)
    )).all())
    subs = dict((await db.execute(
        select(Submission.user_id, func.count(Submission.id)).where(Submission.tenant_id == tid).group_by(Submission.user_id)
    )).all())
    iv_stats = {uid: (n, avg) for uid, n, avg in (await db.execute(
        select(Interview.user_id, func.count(Interview.id), func.avg(Interview.overall_score))
        .where(Interview.tenant_id == tid, Interview.status == "completed").group_by(Interview.user_id)
    )).all()}
    rows = []
    for m in members:
        n, avg = iv_stats.get(m.id, (0, None))
        xp = await xp_for_user(db, m)
        rows.append({
            "user_id": m.id, "full_name": m.full_name, "email": m.email, "role": m.role,
            "xp": xp, "level": level_for(xp)["level"],
            "problems_solved": solved.get(m.id, 0), "submissions": subs.get(m.id, 0),
            "interviews_completed": n, "avg_interview_score": round(avg, 1) if avg is not None else None,
        })
    rows.sort(key=lambda r: (r["xp"], r["problems_solved"]), reverse=True)
    return {
        "member_count": len(members),
        "total_submissions": sum(subs.values()),
        "total_interviews": sum(n for n, _ in iv_stats.values()),
        "leaderboard": rows,
    }
