"""Social sign-in (Google, Microsoft, GitHub) and linked accounts.

Flow (authorization code + PKCE):
  1. Browser opens /auth/oauth/{provider}/start?mode=...  We create `state` and a PKCE verifier,
     keep them in a short-lived signed HttpOnly cookie, and redirect to the provider.
  2. The provider redirects back to /auth/oauth/{provider}/callback?code&state. The state must match
     the cookie (CSRF protection), then we exchange the code (with the PKCE verifier) for a profile.
  3. Depending on `mode` we sign in, join an org with an invite, create a new org, or link the
     provider to the signed-in account. Then we redirect to the app with our own tokens in the URL
     *fragment* (never sent to any server or written to access logs).

Modes:
  login  (org_slug)            existing identity, or an existing member with the same *verified* email
  invite (invite code)         join an org; email-locked invites need a verified matching email
  signup (org_name, org_slug)  create a new org owned by this account (needs a verified email)
  link   (link ticket)         attach the provider to the signed-in user (from account settings)
"""
import hmac
import re
import secrets
from datetime import timedelta
from typing import Annotated
from urllib.parse import quote

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, CurrentUser
from app.api.routes.auth import _aware, _now, issue_tokens
from app.core.config import get_settings
from app.core.security import _encode, decode_token, hash_password, verify_password
from app.db.models import Invite, OAuthIdentity, Role, Tenant, User
from app.services.oauth import OAuthError, OAuthProvider, Profile, get_oauth_providers, pkce_pair, redirect_uri
from app.services.ratelimit import enforce

router = APIRouter(prefix="/auth", tags=["auth"])
Providers = Annotated[dict[str, OAuthProvider], Depends(get_oauth_providers)]

TX_COOKIE = "pf_oauth_tx"
TX_TTL = timedelta(minutes=10)
NO_PASSWORD = "!"  # hashed_password for social-only accounts: bcrypt never matches it
SLUG_RE = re.compile(r"^[a-z0-9-]{2,60}$")


class FlowError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message


def _app_redirect(fragment: str) -> RedirectResponse:
    resp = RedirectResponse(f"{get_settings().public_url.rstrip('/')}/oauth/complete#{fragment}", status_code=302)
    resp.delete_cookie(TX_COOKIE, path="/api/auth/oauth")
    return resp


def _error(message: str) -> RedirectResponse:
    return _app_redirect(f"error={quote(message)}")


def _provider(providers: dict[str, OAuthProvider], name: str) -> OAuthProvider:
    p = providers.get(name)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Sign-in with '{name}' is not enabled")
    return p


@router.get("/providers")
async def list_providers(providers: Providers):
    """Which social sign-in buttons to show."""
    return [{"name": p.name, "label": p.label} for p in providers.values()]


# ---------- start ----------
@router.get("/oauth/{provider}/start")
async def oauth_start(
    provider: str, request: Request, providers: Providers,
    mode: Annotated[str, Query(pattern="^(login|invite|signup|link)$")] = "login",
    org_slug: str = "", org_name: str = "", invite: str = "", ticket: str = "",
):
    p = _provider(providers, provider)
    await enforce(request, "oauth", (30, 300))
    tx = {"type": "oauth_tx", "p": provider, "mode": mode}
    if mode in ("login", "signup"):
        if not SLUG_RE.match(org_slug):
            return _error("Enter your organization's short name (lowercase letters, numbers and dashes).")
        tx["org_slug"] = org_slug
    if mode == "signup":
        if not 2 <= len(org_name.strip()) <= 120:
            return _error("Enter an organization name (2-120 characters).")
        tx["org_name"] = org_name.strip()
    if mode == "invite":
        if not invite:
            return _error("Enter your invite code first.")
        tx["invite"] = invite
    if mode == "link":
        try:
            tx["uid"] = decode_token(ticket, "link_ticket")["sub"]
        except jwt.PyJWTError:
            return _error("That link request expired. Open your account settings and try again.")
    state = secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair()
    tx.update(st=state, pv=verifier)
    resp = RedirectResponse(p.authorization_url(state, challenge, redirect_uri(provider)), status_code=302)
    resp.set_cookie(
        TX_COOKIE, _encode(tx, TX_TTL), max_age=int(TX_TTL.total_seconds()), path="/api/auth/oauth",
        httponly=True, samesite="lax",  # lax: the cookie must survive the top-level redirect back from the provider
        secure=get_settings().public_url.startswith("https://"),
    )
    return resp


# ---------- callback ----------
@router.get("/oauth/{provider}/callback")
async def oauth_callback(provider: str, request: Request, db: DB, providers: Providers,
                         code: str = "", state: str = "", error: str = ""):
    p = _provider(providers, provider)
    if error:
        return _error("Sign-in was cancelled." if error == "access_denied" else f"{p.label} reported an error: {error}")
    try:
        tx = decode_token(request.cookies.get(TX_COOKIE, ""), "oauth_tx")
    except jwt.PyJWTError:
        return _error("Your sign-in session expired. Please try again.")
    if tx.get("p") != provider or not state or not hmac.compare_digest(tx.get("st", ""), state):
        return _error("Sign-in could not be verified. Please try again.")
    if not code:
        return _error("Sign-in was cancelled.")
    try:
        profile = await p.profile(await p.exchange(code, tx["pv"], redirect_uri(provider)))
        handler = {"login": _login, "invite": _join_with_invite, "signup": _create_org, "link": _link}[tx["mode"]]
        user, fragment_extra = await handler(db, provider, profile, tx)
    except OAuthError as e:
        return _error(str(e))
    except FlowError as e:
        await db.rollback()
        return _error(e.message)
    tokens = await issue_tokens(db, user)
    await db.commit()
    return _app_redirect(f"access_token={tokens['access_token']}&refresh_token={tokens['refresh_token']}{fragment_extra}")


async def _identity(db, tenant_id: str, provider: str, subject: str) -> OAuthIdentity | None:
    return await db.scalar(select(OAuthIdentity).where(
        OAuthIdentity.tenant_id == tenant_id, OAuthIdentity.provider == provider, OAuthIdentity.subject == subject))


def _add_identity(db, user: User, provider: str, profile: Profile) -> None:
    db.add(OAuthIdentity(tenant_id=user.tenant_id, user_id=user.id, provider=provider, subject=profile.subject,
                         email=profile.email, last_login_at=_now()))


def _check_active(user: User) -> User:
    if not user.is_active:
        raise FlowError("This account has been deactivated. Contact your organization's admin.")
    return user


async def _login(db, provider: str, profile: Profile, tx: dict):
    tenant = await db.scalar(select(Tenant).where(Tenant.slug == tx["org_slug"]))
    if tenant is None:
        raise FlowError(f"No organization called '{tx['org_slug']}'. Check the name, or create a new organization.")
    ident = await _identity(db, tenant.id, provider, profile.subject)
    if ident:
        ident.last_login_at = _now()
        return _check_active(await db.get(User, ident.user_id)), ""
    if profile.email and profile.email_verified:
        user = await db.scalar(select(User).where(User.tenant_id == tenant.id, User.email == profile.email))
        if user:
            _add_identity(db, user, provider, profile)  # first social sign-in: link by verified email
            return _check_active(user), ""
    raise FlowError(f"There's no account for this {provider.title()} login in '{tenant.slug}'. "
                    "Ask your organization for an invite, or sign in with your password and link it in Account settings.")


async def _join_with_invite(db, provider: str, profile: Profile, tx: dict):
    invite = await db.scalar(select(Invite).where(Invite.code == tx["invite"]))
    if invite is None or invite.used_at is not None or _aware(invite.expires_at) < _now():
        raise FlowError("This invite is invalid or has expired.")
    if invite.email and not (profile.email_verified and profile.email == invite.email.lower()):
        raise FlowError("This invite is for a different email address. Sign in with that account, or use a password.")
    if not profile.email:
        raise FlowError("Your account doesn't share an email address. Use a password to join instead.")
    if await _identity(db, invite.tenant_id, provider, profile.subject):
        raise FlowError("This login already belongs to a member of the organization. Sign in instead.")
    existing = await db.scalar(select(User).where(User.tenant_id == invite.tenant_id, User.email == profile.email))
    if existing:
        raise FlowError("An account with this email already exists in the organization. Sign in instead.")
    user = User(tenant_id=invite.tenant_id, email=profile.email, full_name=(profile.name or profile.email.split("@")[0])[:120],
                hashed_password=NO_PASSWORD, role=invite.role)
    db.add(user)
    await db.flush()
    _add_identity(db, user, provider, profile)
    invite.used_at = _now()
    return user, ""


async def _create_org(db, provider: str, profile: Profile, tx: dict):
    if not (profile.email and profile.email_verified):
        raise FlowError("We need a verified email address to create an organization. Try another sign-in method.")
    if await db.scalar(select(Tenant).where(Tenant.slug == tx["org_slug"])):
        raise FlowError("That organization short name is already taken.")
    tenant = Tenant(name=tx["org_name"], slug=tx["org_slug"])
    db.add(tenant)
    try:
        await db.flush()
    except IntegrityError:
        raise FlowError("That organization short name is already taken.")
    user = User(tenant_id=tenant.id, email=profile.email, full_name=(profile.name or profile.email.split("@")[0])[:120],
                hashed_password=NO_PASSWORD, role=Role.owner)
    db.add(user)
    await db.flush()
    _add_identity(db, user, provider, profile)
    return user, ""


async def _link(db, provider: str, profile: Profile, tx: dict):
    user = await db.get(User, tx.get("uid", ""))
    if user is None:
        raise FlowError("Your session ended. Sign in and try again.")
    _check_active(user)
    ident = await _identity(db, user.tenant_id, provider, profile.subject)
    if ident and ident.user_id != user.id:
        raise FlowError(f"This {provider.title()} login is already linked to another member of your organization.")
    if ident is None:
        if await db.scalar(select(OAuthIdentity).where(OAuthIdentity.user_id == user.id, OAuthIdentity.provider == provider)):
            raise FlowError(f"You already have a {provider.title()} login linked. Unlink it first.")
        _add_identity(db, user, provider, profile)
    return user, "&linked=" + provider


# ---------- linked accounts & password ----------
@router.post("/oauth/link-ticket")
async def link_ticket(user: CurrentUser):
    """Short-lived ticket so a full-page redirect (which can't carry the Authorization header) can link a provider."""
    return {"ticket": _encode({"sub": user.id, "type": "link_ticket"}, timedelta(minutes=5))}


@router.get("/identities")
async def list_identities(user: CurrentUser, db: DB):
    rows = (await db.scalars(select(OAuthIdentity).where(OAuthIdentity.user_id == user.id)
                             .order_by(OAuthIdentity.created_at))).all()
    return {
        "has_password": user.hashed_password != NO_PASSWORD,
        "identities": [{"id": i.id, "provider": i.provider, "email": i.email, "created_at": i.created_at,
                        "last_login_at": i.last_login_at} for i in rows],
    }


@router.delete("/identities/{identity_id}", status_code=204)
async def unlink_identity(identity_id: str, user: CurrentUser, db: DB):
    ident = await db.scalar(select(OAuthIdentity).where(OAuthIdentity.id == identity_id, OAuthIdentity.user_id == user.id))
    if ident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Linked account not found")
    others = await db.scalar(select(func.count(OAuthIdentity.id)).where(
        OAuthIdentity.user_id == user.id, OAuthIdentity.id != ident.id))
    if user.hashed_password == NO_PASSWORD and not others:
        raise HTTPException(status.HTTP_409_CONFLICT, "Set a password first, or you'd have no way to sign in")
    await db.delete(ident)
    await db.commit()


class PasswordIn(BaseModel):
    current_password: str | None = None
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/password", status_code=204)
async def set_password(body: PasswordIn, user: CurrentUser, db: DB, request: Request):
    """Change your password, or add one to a social-only account."""
    await enforce(request, "password", (10, 300), user.id)
    if user.hashed_password != NO_PASSWORD and not verify_password(body.current_password or "", user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.hashed_password = hash_password(body.new_password)
    await db.commit()
