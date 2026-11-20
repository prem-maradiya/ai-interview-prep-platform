import secrets
from datetime import datetime, timedelta, timezone

import csv
import io

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import DB, AdminUser, CurrentUser, get_scoped
from app.api.routes.auth import revoke_all_for_user
from app.db.models import AuditEvent, Invite, Role, User
from app.services import audit
from app.services.csv_export import safe_cell
from app.schemas import InviteIn, InviteOut, MemberOut, MemberUpdateIn, TenantOut
from app.services.email import app_url, queue_email, render
from app.services.quota import usage_summary

router = APIRouter(prefix="/org", tags=["organization"])


@router.get("")
async def get_org(user: CurrentUser, db: DB):
    return {"tenant": TenantOut.model_validate(user.tenant), "usage": await usage_summary(db, user.tenant_id, user.tenant.plan)}


@router.get("/members", response_model=list[MemberOut])
async def list_members(user: AdminUser, db: DB):
    return (await db.scalars(select(User).where(User.tenant_id == user.tenant_id).order_by(User.created_at))).all()


@router.patch("/members/{member_id}", response_model=MemberOut)
async def update_member(member_id: str, body: MemberUpdateIn, user: AdminUser, db: DB, request: Request):
    member = await get_scoped(db, User, member_id, user.tenant_id)
    if member.role == Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The owner account cannot be modified")
    if member.id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot modify your own account here")
    if body.role == Role.admin and user.role != Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can grant admin")
    target = dict(target_type="user", target_id=member.id, target_label=f"{member.full_name} <{member.email}>",
                  request=request)
    if body.role is not None and body.role != member.role:
        audit.record(db, user, "member.role_changed", details={"from": member.role, "to": body.role}, **target)
        member.role = body.role
    if body.is_active is not None and body.is_active != member.is_active:
        member.is_active = body.is_active
        audit.record(db, user, "member.reactivated" if body.is_active else "member.deactivated", **target)
        if not body.is_active:
            await revoke_all_for_user(db, member.id)  # sign them out everywhere
    await db.commit()
    return member


@router.post("/invites", response_model=InviteOut, status_code=201)
async def create_invite(body: InviteIn, user: AdminUser, db: DB, request: Request):
    if body.role == Role.admin and user.role != Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can invite admins")
    invite = Invite(
        tenant_id=user.tenant_id, code=secrets.token_urlsafe(24), email=body.email, role=body.role,
        expires_at=datetime.now(timezone.utc) + timedelta(days=body.days_valid),
    )
    db.add(invite)
    await db.flush()
    audit.record(db, user, "invite.created", target_type="invite", target_id=invite.id,
                 target_label=invite.email or "(open invite)", details={"role": invite.role, "days_valid": body.days_valid},
                 request=request)
    if invite.email:
        org = user.tenant
        queue_email(db, render(
            to=invite.email, subject=f"{user.full_name} invited you to {org.name} on PrepForge",
            heading=f"Join {org.name} on PrepForge",
            paragraphs=[f"{user.full_name} invited you to join {org.name} as a {invite.role}.",
                        "PrepForge is where your team practices coding problems and mock interviews together.",
                        f"Your invite code is {invite.code} - it expires in {body.days_valid} days."],
            button=("Accept invite", app_url(f"/?invite={invite.code}")),
            footer="If you weren't expecting this, you can ignore this email.",
        ))
    await db.commit()
    return invite


@router.get("/invites", response_model=list[InviteOut])
async def list_invites(user: AdminUser, db: DB):
    return (await db.scalars(select(Invite).where(Invite.tenant_id == user.tenant_id).order_by(Invite.created_at.desc()))).all()


# ---------- audit log ----------
def _event_out(e: AuditEvent) -> dict:
    return {"id": e.id, "action": e.action, "description": audit.ACTIONS.get(e.action, e.action),
            "actor_id": e.actor_id, "actor_name": e.actor_name, "actor_email": e.actor_email,
            "target_type": e.target_type, "target_id": e.target_id, "target_label": e.target_label,
            "details": e.details or {}, "ip": e.ip, "created_at": e.created_at}


def _audit_query(user: User, action: str | None, actor_id: str | None):
    q = select(AuditEvent).where(AuditEvent.tenant_id == user.tenant_id)
    if action:
        q = q.where(AuditEvent.action == action)
    if actor_id:
        q = q.where(AuditEvent.actor_id == actor_id)
    return q.order_by(AuditEvent.created_at.desc(), AuditEvent.id)


@router.get("/audit")
async def list_audit_events(user: AdminUser, db: DB, action: str | None = None, actor_id: str | None = None,
                            limit: int = 50, offset: int = 0):
    """Newest first. Admins and owners only."""
    rows = (await db.scalars(_audit_query(user, action, actor_id).limit(min(max(limit, 1), 200)).offset(max(offset, 0)))).all()
    return {"actions": audit.ACTIONS, "items": [_event_out(e) for e in rows]}


@router.get("/audit.csv")
async def export_audit_events(user: AdminUser, db: DB, action: str | None = None):
    rows = (await db.scalars(_audit_query(user, action, None).limit(10_000))).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time (UTC)", "actor", "actor email", "action", "target", "details", "ip"])
    for e in rows:
        w.writerow([safe_cell(v) for v in (
            e.created_at.strftime("%Y-%m-%d %H:%M:%S"), e.actor_name, e.actor_email, audit.ACTIONS.get(e.action, e.action),
            e.target_label, "; ".join(f"{k}={v}" for k, v in (e.details or {}).items()), e.ip or "")])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'})
