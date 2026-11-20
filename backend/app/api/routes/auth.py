import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, SystemDB
from app.core.config import get_settings
from app.core.security import (
    DUMMY_HASH, _encode, create_access_token, create_refresh_token, decode_token, hash_password, verify_password,
)
from app.db.models import Invite, RefreshToken, Role, Tenant, User
from app.schemas import (
    AcceptInviteIn, LoginIn, PasswordResetConfirmIn, PasswordResetRequestIn, RefreshIn, RegisterOrgIn, TokenOut, UserOut,
)
from app.services.email import app_url, queue_email, render
from app.services.ratelimit import enforce

router = APIRouter(prefix="/auth", tags=["auth"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite drops tzinfo


async def issue_tokens(db: AsyncSession, user: User, family_id: str | None = None) -> dict:
    """Create an access token plus a tracked refresh token. Caller commits."""
    refresh, jti, expires_at = create_refresh_token(user.id, user.tenant_id)
    db.add(RefreshToken(id=jti, user_id=user.id, family_id=family_id or str(uuid.uuid4()), expires_at=expires_at))
    return {"access_token": create_access_token(user.id, user.tenant_id, user.role), "refresh_token": refresh,
            "token_type": "bearer"}


async def revoke_family(db: AsyncSession, family_id: str) -> None:
    await db.execute(update(RefreshToken).where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
                     .values(revoked_at=_now()))


async def revoke_all_for_user(db: AsyncSession, user_id: str) -> None:
    await db.execute(update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
                     .values(revoked_at=_now()))


@router.post("/register-org", response_model=TokenOut, status_code=201)
async def register_org(body: RegisterOrgIn, db: SystemDB, request: Request):
    """Create a new tenant (organization) and its owner account."""
    await enforce(request, "register", get_settings().rl_register)
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
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Organization slug already taken")
    tokens = await issue_tokens(db, user)
    await db.commit()
    return tokens


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, db: SystemDB, request: Request):
    await enforce(request, "login", get_settings().rl_login, body.org_slug, body.email)
    user = await db.scalar(
        select(User).join(Tenant).where(Tenant.slug == body.org_slug, User.email == body.email.lower())
    )
    # Always run bcrypt so response time doesn't reveal whether the account exists.
    password_ok = verify_password(body.password, user.hashed_password if user else DUMMY_HASH)
    if user is None or not password_ok or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid organization, email or password")
    tokens = await issue_tokens(db, user)
    await db.commit()
    return tokens


@router.post("/refresh", response_model=TokenOut)
async def refresh(body: RefreshIn, db: SystemDB, request: Request):
    """Rotate the refresh token. Re-using an old one revokes the whole login session (theft detection)."""
    await enforce(request, "refresh", get_settings().rl_refresh)
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    try:
        payload = decode_token(body.refresh_token, "refresh")
    except jwt.PyJWTError:
        raise invalid
    record = await db.get(RefreshToken, payload.get("jti", ""))
    if record is None or record.user_id != payload["sub"] or _aware(record.expires_at) < _now():
        raise invalid
    if record.revoked_at is not None:
        await revoke_family(db, record.family_id)
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token reuse detected; please sign in again")
    user = await db.get(User, record.user_id)
    if user is None or not user.is_active or user.tenant_id != payload.get("tid"):
        raise invalid
    record.revoked_at = _now()
    tokens = await issue_tokens(db, user, family_id=record.family_id)  # role is re-read from the DB
    await db.commit()
    return tokens


@router.post("/logout", status_code=204)
async def logout(body: RefreshIn, db: SystemDB):
    """Revoke this login session's refresh tokens. Always 204, even for unknown/expired tokens."""
    try:
        payload = jwt.decode(body.refresh_token, get_settings().jwt_secret, algorithms=[get_settings().jwt_algorithm],
                             options={"verify_exp": False})
    except jwt.PyJWTError:
        return Response(status_code=204)
    record = await db.get(RefreshToken, payload.get("jti", ""))
    if record is not None:
        await revoke_family(db, record.family_id)
        await db.commit()
    return Response(status_code=204)


@router.post("/accept-invite", response_model=TokenOut, status_code=201)
async def accept_invite(body: AcceptInviteIn, db: SystemDB, request: Request):
    await enforce(request, "invite", get_settings().rl_invite_accept)
    invite = await db.scalar(select(Invite).where(Invite.code == body.code))
    if invite is None or invite.used_at is not None or _aware(invite.expires_at) < _now():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invite is invalid or expired")
    if invite.email and invite.email.lower() != body.email.lower():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This invite was issued for a different email")
    if await db.scalar(select(User).where(User.tenant_id == invite.tenant_id, User.email == body.email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists in the organization")
    user = User(
        tenant_id=invite.tenant_id, email=body.email.lower(), full_name=body.full_name,
        hashed_password=hash_password(body.password), role=invite.role,
    )
    invite.used_at = _now()
    db.add(user)
    await db.flush()
    tokens = await issue_tokens(db, user)
    await db.commit()
    return tokens


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return user


# ---------- password reset ----------
def _password_fingerprint(user: User) -> str:
    """Changes whenever the password does, so a reset link works once and dies after any password change."""
    key = get_settings().jwt_secret.encode()
    return hmac.new(key, user.hashed_password.encode(), hashlib.sha256).hexdigest()[:24]


@router.post("/password-reset/request", status_code=202)
async def request_password_reset(body: PasswordResetRequestIn, db: SystemDB, request: Request):
    """Email a reset link. Always 202, so the response doesn't reveal whether the account exists."""
    await enforce(request, "pwreset", get_settings().rl_password_reset, body.org_slug, body.email)
    user = await db.scalar(
        select(User).join(Tenant).where(Tenant.slug == body.org_slug, User.email == body.email.lower()))
    if user is not None and user.is_active:
        minutes = get_settings().password_reset_minutes
        token = _encode({"sub": user.id, "tid": user.tenant_id, "type": "password_reset",
                         "pw": _password_fingerprint(user)}, timedelta(minutes=minutes))
        queue_email(db, render(
            to=user.email, subject="Reset your PrepForge password", heading="Reset your password",
            paragraphs=[f"Someone (hopefully you) asked to reset the password for {user.email} in "
                        f"{user.tenant.name}.", f"The link works once and expires in {minutes} minutes."],
            button=("Choose a new password", app_url(f"/reset-password?token={token}")),
            footer="If you didn't ask for this, ignore this email - your password won't change.",
        ))
        await db.commit()
    return {"detail": "If that account exists, we've emailed a reset link."}


@router.post("/password-reset/confirm", status_code=204)
async def confirm_password_reset(body: PasswordResetConfirmIn, db: SystemDB, request: Request):
    await enforce(request, "pwreset-confirm", get_settings().rl_login)
    invalid = HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired")
    try:
        claims = decode_token(body.token, "password_reset")
    except jwt.PyJWTError:
        raise invalid
    user = await db.get(User, claims.get("sub", ""))
    if (user is None or not user.is_active or user.tenant_id != claims.get("tid")
            or not hmac.compare_digest(claims.get("pw", ""), _password_fingerprint(user))):
        raise invalid
    user.hashed_password = hash_password(body.password)
    await revoke_all_for_user(db, user.id)  # sign out every existing session
    await db.commit()
    return Response(status_code=204)
