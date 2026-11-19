"""XP, levels and badges.

XP is derived from real activity every time (never stored), so it can't drift or be gamed by
re-solving: only the first accepted solve of each problem counts. Badges are awarded once and
announced with a notification.
"""
from dataclasses import dataclass

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Interview, Problem, ReviewItem, Submission, User, UserBadge
from app.services.notifications import notify

XP_SOLVE = {"easy": 10, "medium": 25, "hard": 50}
XP_INTERVIEW = 20  # plus overall_score / 10
XP_REVIEW = 5  # each successful spaced-repetition review beyond the first solve
LEVELS = [0, 50, 150, 300, 500, 800, 1200, 1700, 2300, 3000]  # XP needed for level 1..10


@dataclass(frozen=True)
class Badge:
    key: str
    name: str
    description: str
    icon: str


BADGES = [
    Badge("first_solve", "First blood", "Solve your first problem", "🩸"),
    Badge("ten_solves", "Problem solver", "Solve 10 different problems", "🧩"),
    Badge("hard_solve", "Heavy lifter", "Solve a hard problem", "🏋️"),
    Badge("polyglot", "Polyglot", "Get accepted in 3 different languages", "🌐"),
    Badge("streak_3", "On a roll", "Practice 3 days in a row", "🔥"),
    Badge("streak_7", "Unstoppable", "Practice 7 days in a row", "⚡"),
    Badge("first_interview", "Brave face", "Complete a mock interview", "🎤"),
    Badge("all_rounder", "All-rounder", "Complete behavioral, technical and system design interviews", "🎯"),
    Badge("high_score", "Hire signal", "Score 80+ in a mock interview", "🏆"),
    Badge("reviewer", "Long-term memory", "Pass 5 spaced-repetition reviews", "🧠"),
]
BADGE_BY_KEY = {b.key: b for b in BADGES}


def level_for(xp: int) -> dict:
    level = sum(1 for t in LEVELS if xp >= t)
    nxt = LEVELS[level] if level < len(LEVELS) else None
    return {"level": level, "xp": xp, "current_level_xp": LEVELS[level - 1], "next_level_xp": nxt}


async def _stats(db: AsyncSession, user: User) -> dict:
    solved = (await db.execute(
        select(Problem.difficulty, func.count(distinct(Submission.problem_id)))
        .join(Problem, Problem.id == Submission.problem_id)
        .where(Submission.user_id == user.id, Submission.verdict == "accepted").group_by(Problem.difficulty)
    )).all()
    languages = await db.scalar(select(func.count(distinct(Submission.language))).where(
        Submission.user_id == user.id, Submission.verdict == "accepted"))
    interviews = (await db.execute(select(Interview.kind, Interview.overall_score).where(
        Interview.user_id == user.id, Interview.status == "completed"))).all()
    reviews = await db.scalar(select(func.coalesce(func.sum(ReviewItem.reps - 1), 0)).where(
        ReviewItem.user_id == user.id, ReviewItem.reps > 1))
    from app.services.practice import build_streak  # local import avoids a circular import

    return {
        "solved": {d: n for d, n in solved}, "languages": languages or 0, "interviews": interviews,
        "reviews": int(reviews or 0), "streak": await build_streak(db, user),
    }


def _xp(stats: dict) -> int:
    xp = sum(XP_SOLVE.get(d, 10) * n for d, n in stats["solved"].items())
    xp += sum(XP_INTERVIEW + int((score or 0) // 10) for _, score in stats["interviews"])
    return xp + XP_REVIEW * stats["reviews"]


def _earned(stats: dict) -> set[str]:
    total = sum(stats["solved"].values())
    kinds = {k for k, _ in stats["interviews"]}
    rules = {
        "first_solve": total >= 1,
        "ten_solves": total >= 10,
        "hard_solve": stats["solved"].get("hard", 0) >= 1,
        "polyglot": stats["languages"] >= 3,
        "streak_3": stats["streak"] >= 3,
        "streak_7": stats["streak"] >= 7,
        "first_interview": len(stats["interviews"]) >= 1,
        "all_rounder": {"behavioral", "technical", "system_design"} <= kinds,
        "high_score": any((s or 0) >= 80 for _, s in stats["interviews"]),
        "reviewer": stats["reviews"] >= 5,
    }
    return {k for k, ok in rules.items() if ok}


async def check_badges(db: AsyncSession, user: User) -> list[str]:
    """Award any newly earned badges (caller commits). Returns the new badge keys."""
    stats = await _stats(db, user)
    have = set((await db.scalars(select(UserBadge.badge).where(UserBadge.user_id == user.id))).all())
    new = sorted(_earned(stats) - have, key=lambda k: [b.key for b in BADGES].index(k))
    for key in new:
        b = BADGE_BY_KEY[key]
        db.add(UserBadge(tenant_id=user.tenant_id, user_id=user.id, badge=key))
        await notify(db, [user.id], user.tenant_id, kind="badge", title=f"Badge earned: {b.icon} {b.name}",
                     body=b.description, link="/achievements", dedupe_key=f"badge:{key}")
    return new


async def achievements(db: AsyncSession, user: User) -> dict:
    stats = await _stats(db, user)
    earned = {b.badge: b.earned_at for b in (await db.scalars(select(UserBadge).where(UserBadge.user_id == user.id))).all()}
    return {
        **level_for(_xp(stats)),
        "streak_days": stats["streak"],
        "badges": [{"key": b.key, "name": b.name, "description": b.description, "icon": b.icon,
                    "earned": b.key in earned, "earned_at": earned.get(b.key)} for b in BADGES],
    }


async def xp_for_user(db: AsyncSession, user: User) -> int:
    return _xp(await _stats(db, user))
