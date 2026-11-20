import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DB, AdminUser, CurrentUser, get_scoped
from app.api.routes.auth import revoke_all_for_user
from app.db.models import Invite, Role, User
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
async def update_member(member_id: str, body: MemberUpdateIn, user: AdminUser, db: DB):
    member = await get_scoped(db, User, member_id, user.tenant_id)
    if member.role == Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The owner account cannot be modified")
    if member.id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot modify your own account here")
    if body.role == Role.admin and user.role != Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can grant admin")
    if body.role is not None:
        member.role = body.role
    if body.is_active is not None:
        member.is_active = body.is_active
        if not body.is_active:
            await revoke_all_for_user(db, member.id)  # sign them out everywhere
    await db.commit()
    return member


@router.post("/invites", response_model=InviteOut, status_code=201)
async def create_invite(body: InviteIn, user: AdminUser, db: DB):
    if body.role == Role.admin and user.role != Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can invite admins")
    invite = Invite(
        tenant_id=user.tenant_id, code=secrets.token_urlsafe(24), email=body.email, role=body.role,
        expires_at=datetime.now(timezone.utc) + timedelta(days=body.days_valid),
    )
    db.add(invite)
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
