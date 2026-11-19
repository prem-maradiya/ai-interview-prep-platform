from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, CurrentUser
from app.core.security import create_token_pair, decode_token, hash_password, verify_password
from app.db.models import Invite, Role, Tenant, User
from app.schemas import AcceptInviteIn, LoginIn, RefreshIn, RegisterOrgIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _tokens(user: User) -> dict:
    return create_token_pair(user.id, user.tenant_id, user.role)


@router.post("/register-org", response_model=TokenOut, status_code=201)
async def register_org(body: RegisterOrgIn, db: DB):
    """Create a new tenant (organization) and its owner account."""
    if await db.scalar(select(Tenant).where(Tenant.slug == body.org_slug)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Organization slug already taken")
    tenant = Tenant(name=body.org_name, slug=body.org_slug)
    db.add(tenant)
    await db.flush()
    user = User(
        tenant_id=tenant.id, email=body.email.lower(), full_name=body.full_name,
        hashed_password=hash_password(body.password), role=Role.owner,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Organization slug already taken")
    return _tokens(user)


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, db: DB):
    user = await db.scalar(
        select(User).join(Tenant).where(Tenant.slug == body.org_slug, User.email == body.email.lower())
    )
    if user is None or not verify_password(body.password, user.hashed_password) or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid organization, email or password")
    return _tokens(user)


@router.post("/refresh", response_model=TokenOut)
async def refresh(body: RefreshIn, db: DB):
    try:
        payload = decode_token(body.refresh_token, "refresh")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    user = await db.get(User, payload["sub"])
    if user is None or not user.is_active or user.tenant_id != payload.get("tid"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    return _tokens(user)  # role is re-read from DB, so role changes take effect on refresh


@router.post("/accept-invite", response_model=TokenOut, status_code=201)
async def accept_invite(body: AcceptInviteIn, db: DB):
    invite = await db.scalar(select(Invite).where(Invite.code == body.code))
    now = datetime.now(timezone.utc)
    expires = invite.expires_at.replace(tzinfo=invite.expires_at.tzinfo or timezone.utc) if invite else None
    if invite is None or invite.used_at is not None or expires < now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invite is invalid or expired")
    if invite.email and invite.email.lower() != body.email.lower():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This invite was issued for a different email")
    if await db.scalar(select(User).where(User.tenant_id == invite.tenant_id, User.email == body.email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists in the organization")
    user = User(
        tenant_id=invite.tenant_id, email=body.email.lower(), full_name=body.full_name,
        hashed_password=hash_password(body.password), role=invite.role,
    )
    invite.used_at = now
    db.add(user)
    await db.commit()
    return _tokens(user)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return user
