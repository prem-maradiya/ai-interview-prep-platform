from datetime import timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, or_, select

from app.services import audit
from app.api.deps import DB, ROLE_RANK, CurrentUser, MentorUser, get_scoped
from app.db.models import Assignment, AssignmentMember, Notification, Problem, Role, User
from app.schemas import AssignmentIn
from app.services.assignments import assignees, is_assigned, progress
from app.services.csv_export import csv_response
from app.services.notifications import notify

router = APIRouter(prefix="/assignments", tags=["assignments"])


def _is_staff(user: User) -> bool:
    return ROLE_RANK[Role(user.role)] >= ROLE_RANK[Role.mentor]


async def _problem_titles(db, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    rows = (await db.scalars(select(Problem).where(Problem.id.in_(ids)))).all()
    return {p.id: {"id": p.id, "title": p.title, "difficulty": p.difficulty} for p in rows}


def _base(a: Assignment, problems: dict[str, dict]) -> dict:
    return {
        "id": a.id, "title": a.title, "description": a.description, "due_at": a.due_at, "created_at": a.created_at,
        "problems": [problems[pid] for pid in a.problem_ids if pid in problems],
        "interview_kind": a.interview_kind, "interview_count": a.interview_count, "assign_all": a.assign_all,
    }


@router.post("", status_code=201)
async def create_assignment(body: AssignmentIn, user: MentorUser, db: DB):
    if not body.problem_ids and not body.interview_count:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add at least one problem or interview")
    # Problems must be visible to this tenant (its own or global).
    found = set((await db.scalars(select(Problem.id).where(
        Problem.id.in_(body.problem_ids), or_(Problem.tenant_id == user.tenant_id, Problem.tenant_id.is_(None))))).all())
    if missing := [p for p in body.problem_ids if p not in found]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown problems: {missing}")
    member_ids: list[str] = []
    if not body.assign_all:
        if not body.assignee_ids:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose assignees or assign to all candidates")
        valid = set((await db.scalars(select(User.id).where(
            User.tenant_id == user.tenant_id, User.id.in_(body.assignee_ids)))).all())
        if bad := [u for u in body.assignee_ids if u not in valid]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown members: {bad}")
        member_ids = list(dict.fromkeys(body.assignee_ids))
    due = body.due_at if body.due_at.tzinfo else body.due_at.replace(tzinfo=timezone.utc)
    a = Assignment(
        tenant_id=user.tenant_id, created_by=user.id, title=body.title, description=body.description, due_at=due,
        problem_ids=list(dict.fromkeys(body.problem_ids)), interview_kind=body.interview_kind if body.interview_count else None,
        interview_count=body.interview_count, assign_all=body.assign_all,
        members=[AssignmentMember(user_id=u) for u in member_ids],
    )
    db.add(a)
    await db.flush()
    targets = await assignees(db, a)
    await notify(db, [u.id for u in targets], user.tenant_id, kind="assignment", title=f"New assignment: {a.title}",
                 body=f"From {user.full_name}, due {due.strftime('%b %d')}.", link=f"/assignments/{a.id}")
    audit.record(db, user, "assignment.created", target_type="assignment", target_id=a.id, target_label=a.title,
                 details={"assignees": "all candidates" if a.assign_all else len(member_ids), "due": due.date().isoformat()})
    await db.commit()
    await db.refresh(a)
    return _base(a, await _problem_titles(db, a.problem_ids))


@router.get("")
async def list_assignments(user: CurrentUser, db: DB):
    """Mentors see every assignment with completion counts; candidates see their own with their progress."""
    rows = (await db.scalars(select(Assignment).where(Assignment.tenant_id == user.tenant_id)
                             .order_by(Assignment.due_at))).all()
    staff = _is_staff(user)
    out = []
    for a in rows:
        if not staff and not is_assigned(a, user):
            continue
        item = _base(a, await _problem_titles(db, a.problem_ids))
        if staff:
            prog = await progress(db, a, await assignees(db, a))
            item["assignee_count"] = len(prog)
            item["completed_count"] = sum(p["status"] in ("completed", "late") for p in prog)
        else:
            item["my_progress"] = (await progress(db, a, [user]))[0]
        out.append(item)
    return out


async def _get(db, assignment_id: str, user: User) -> Assignment:
    a = await get_scoped(db, Assignment, assignment_id, user.tenant_id)
    if not _is_staff(user) and not is_assigned(a, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignment not found")
    return a


@router.get("/{assignment_id}")
async def get_assignment(assignment_id: str, user: CurrentUser, db: DB):
    a = await _get(db, assignment_id, user)
    item = _base(a, await _problem_titles(db, a.problem_ids))
    if _is_staff(user):
        item["progress"] = await progress(db, a, await assignees(db, a))
    else:
        item["my_progress"] = (await progress(db, a, [user]))[0]
    return item


@router.delete("/{assignment_id}", status_code=204)
async def delete_assignment(assignment_id: str, user: MentorUser, db: DB):
    a = await get_scoped(db, Assignment, assignment_id, user.tenant_id)
    # Remove its notifications too, so nobody is left with links to a deleted assignment.
    await db.execute(delete(Notification).where(Notification.tenant_id == user.tenant_id,
                                                Notification.link == f"/assignments/{a.id}"))
    audit.record(db, user, "assignment.deleted", target_type="assignment", target_id=a.id, target_label=a.title)
    await db.delete(a)
    await db.commit()


@router.get("/{assignment_id}/export.csv")
async def export_assignment(assignment_id: str, user: MentorUser, db: DB):
    a = await get_scoped(db, Assignment, assignment_id, user.tenant_id)
    problems = await _problem_titles(db, a.problem_ids)
    prog = await progress(db, a, await assignees(db, a))
    header = ["Name", "Email", "Status", "Done", "Total", "Completed at"] + [problems[p]["title"] for p in a.problem_ids if p in problems]
    if a.interview_count:
        header.append(f"{a.interview_kind or 'any'} interviews")
    rows = []
    for p in prog:
        row = [p["full_name"], p["email"], p["status"], p["done"], p["total"],
               p["completed_at"].isoformat() if p["completed_at"] else ""]
        row += ["yes" if pid in p["solved_problem_ids"] else "no" for pid in a.problem_ids if pid in problems]
        if a.interview_count:
            row.append(f"{p['interviews_done']}/{a.interview_count}")
        rows.append(row)
    return csv_response(f"assignment-{a.id[:8]}.csv", header, rows)
