import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DB, AdminUser, CurrentUser, get_scoped
from app.db.models import Invite, Role, User
from app.schemas import InviteIn, InviteOut, MemberOut, MemberUpdateIn, TenantOut
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
    await db.commit()
    return invite


@router.get("/invites", response_model=list[InviteOut])
async def list_invites(user: AdminUser, db: DB):
    return (await db.scalars(select(Invite).where(Invite.tenant_id == user.tenant_id).order_by(Invite.created_at.desc()))).all()
