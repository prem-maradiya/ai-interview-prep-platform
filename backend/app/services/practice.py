"""Spaced repetition for coding problems and the personalised daily study plan.

Scheduling is a simplified SM-2: each (user, problem) has an ease factor and an interval.
Solving it on time grows the interval (1 day, 3 days, then x ease); failing shrinks ease and
brings the problem back tomorrow. Everything here is deterministic - no LLM calls.
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Interview, Problem, ReviewItem, Submission, User
from app.services.llm import RUBRICS

MIN_EASE, MAX_EASE = 1.3, 3.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def schedule(item: ReviewItem, accepted: bool, now: datetime) -> None:
    """Update one review item after a judged submission."""
    early = now < _aware(item.due_at)
    if accepted:
        if early and item.reps > 0:
            return  # re-solving a problem you already know, before it's due, doesn't change the schedule
        item.reps += 1
        item.interval_days = 1 if item.reps == 1 else 3 if item.reps == 2 else round(item.interval_days * item.ease, 1)
        item.ease = min(MAX_EASE, item.ease + 0.1)
    else:
        if item.reps > 0:
            item.lapses += 1  # forgot something it had learned
        item.reps = 0
        item.interval_days = 1
        item.ease = max(MIN_EASE, item.ease - 0.2)
    item.due_at = now + timedelta(days=item.interval_days)


async def record_attempt(db: AsyncSession, user: User, problem_id: str, verdict: str) -> ReviewItem:
    now = _now()
    item = await db.scalar(select(ReviewItem).where(ReviewItem.user_id == user.id, ReviewItem.problem_id == problem_id))
    if item is None:
        item = ReviewItem(tenant_id=user.tenant_id, user_id=user.id, problem_id=problem_id, ease=2.5,
                          interval_days=0, reps=0, lapses=0, due_at=now)
        db.add(item)
    schedule(item, verdict == "accepted", now)
    item.last_verdict = verdict
    item.updated_at = now
    return item


def streak_from_dates(days: set[date], today: date) -> int:
    """Consecutive active days ending today (or yesterday, so the streak survives until you practice today)."""
    start = today if today in days else today - timedelta(days=1)
    n = 0
    while start - timedelta(days=n) in days:
        n += 1
    return n


async def build_plan(db: AsyncSession, user: User) -> dict:
    now = _now()
    since = now - timedelta(days=90)

    activity = (await db.scalars(select(Submission.created_at).where(
        Submission.user_id == user.id, Submission.created_at >= since))).all()
    activity += (await db.scalars(select(Interview.created_at).where(
        Interview.user_id == user.id, Interview.created_at >= since))).all()
    days = {_aware(d).date() for d in activity}

    problems = {p.id: p for p in (await db.scalars(select(Problem).where(
        or_(Problem.tenant_id == user.tenant_id, Problem.tenant_id.is_(None))))).all()}
    items = (await db.scalars(select(ReviewItem).where(ReviewItem.user_id == user.id))).all()
    items = [i for i in items if i.problem_id in problems]

    due = sorted((i for i in items if _aware(i.due_at) <= now), key=lambda i: i.due_at)
    due_reviews = [{
        "problem_id": i.problem_id, "title": problems[i.problem_id].title, "difficulty": problems[i.problem_id].difficulty,
        "reason": "Not solved yet - try again" if i.reps == 0 else f"Review to keep it fresh (interval {i.interval_days:g} days)",
        "overdue_days": max(0, (now - _aware(i.due_at)).days),
    } for i in due[:5]]

    # Weak tags: tags of problems you've failed or lapsed on.
    weak_tags = Counter(t for i in items if i.reps == 0 or i.lapses for t in problems[i.problem_id].tags)
    solved_by_diff = Counter(problems[i.problem_id].difficulty for i in items if i.reps > 0)
    target_diff = "easy" if solved_by_diff["easy"] < 2 else "medium" if solved_by_diff["medium"] < 3 else "hard"
    # Anything with a submission counts as attempted, including history from before review tracking existed.
    submitted = set((await db.scalars(select(Submission.problem_id).where(Submission.user_id == user.id))).all())
    attempted = {i.problem_id for i in items} | submitted

    def rank(p: Problem):
        tag_hits = sum(weak_tags[t] for t in p.tags)
        return (-tag_hits, p.difficulty != target_diff, p.created_at)

    new_problems = []
    for p in sorted((p for p in problems.values() if p.id not in attempted), key=rank)[:3]:
        hits = [t for t in p.tags if weak_tags[t]]
        reason = f"Practices {', '.join(hits)}, where you've struggled" if hits else (
            f"Next step: {p.difficulty} problem" if p.difficulty == target_diff else "Broaden your coverage")
        new_problems.append({"problem_id": p.id, "title": p.title, "difficulty": p.difficulty, "reason": reason})

    # Interview suggestion: weakest rubric dimension across completed interviews.
    completed = (await db.scalars(select(Interview).where(
        Interview.user_id == user.id, Interview.status == "completed"))).all()
    dims: dict[str, list[int]] = {}
    for iv in completed:
        for d in (iv.scorecard or {}).get("dimensions", []):
            dims.setdefault(d["name"], []).append(d["score"])
    if dims:
        weakest, scores = min(dims.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))
        kind = next((k for k, names in RUBRICS.items() if weakest in names), "behavioral")
        suggested_interview = {"kind": kind, "focus": weakest,
                               "reason": f"Your weakest area is {weakest} (avg {sum(scores) / len(scores):.1f}/5)"}
    else:
        suggested_interview = {"kind": "behavioral", "focus": "",
                               "reason": "Start with a behavioral interview to get a baseline scorecard"}

    upcoming = sorted(_aware(i.due_at) for i in items if _aware(i.due_at) > now)
    return {
        "streak_days": streak_from_dates(days, now.date()),
        "practiced_today": now.date() in days,
        "due_reviews": due_reviews,
        "due_count": len(due),
        "new_problems": new_problems,
        "suggested_interview": suggested_interview,
        "learned_count": sum(1 for i in items if i.reps > 0),
        "next_review_at": upcoming[0] if upcoming else None,
    }
