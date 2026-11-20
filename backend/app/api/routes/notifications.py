from datetime import datetime, timezone

from fastapi import APIRouter, Body, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select, update

from app.api.deps import DB, CurrentUser, SystemDB
from app.core.config import get_settings
from app.db.models import Notification, User
from app.services.email import EMAIL_KINDS, email_prefs, read_unsubscribe_token
from app.services.notifications import generate_due_soon

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _out(n: Notification) -> dict:
    return {"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "link": n.link,
            "read": n.read_at is not None, "created_at": n.created_at}


@router.get("")
async def list_notifications(user: CurrentUser, db: DB, unread_only: bool = False, limit: int = 30):
    await generate_due_soon(db, user)
    await db.commit()
    q = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    rows = (await db.scalars(q.order_by(Notification.created_at.desc()).limit(min(limit, 100)))).all()
    unread = await db.scalar(select(func.count(Notification.id)).where(
        Notification.user_id == user.id, Notification.read_at.is_(None)))
    return {"unread_count": unread, "items": [_out(n) for n in rows]}


@router.post("/{notification_id}/read", status_code=204)
async def mark_read(notification_id: str, user: CurrentUser, db: DB):
    n = await db.scalar(select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id))
    if n is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    n.read_at = n.read_at or datetime.now(timezone.utc)
    await db.commit()


@router.post("/read-all", status_code=204)
async def mark_all_read(user: CurrentUser, db: DB):
    await db.execute(update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
                     .values(read_at=datetime.now(timezone.utc)))
    await db.commit()


# ---------- email preferences ----------
class EmailPrefsIn(BaseModel):
    assignment: bool | None = None
    due_soon: bool | None = None
    comment: bool | None = None
    badge: bool | None = None
    digest: bool | None = None


def _prefs_out(user: User) -> dict:
    prefs = email_prefs(user)
    return {
        "email": user.email,
        "delivery_enabled": get_settings().email_backend != "none",
        "options": [{"kind": k, "label": spec["label"], "enabled": prefs[k]} for k, spec in EMAIL_KINDS.items()],
    }


@router.get("/email-preferences")
async def get_email_preferences(user: CurrentUser):
    return _prefs_out(user)


@router.put("/email-preferences")
async def update_email_preferences(body: EmailPrefsIn, user: CurrentUser, db: DB):
    changes = body.model_dump(exclude_none=True)
    user.email_prefs = {**email_prefs(user), **changes}  # new dict so the JSON column is marked dirty
    await db.commit()
    return _prefs_out(user)


class UnsubscribeIn(BaseModel):
    token: str = ""


@router.post("/unsubscribe")
async def unsubscribe(db: SystemDB, token: str = Query(""), body: UnsubscribeIn | None = Body(None)):
    """Turn off every optional email. Linked from each email; also handles RFC 8058 one-click POSTs."""
    claims = read_unsubscribe_token(token or (body.token if body else ""))
    user = await db.get(User, claims["sub"]) if claims else None
    if user is None or user.tenant_id != claims.get("tid"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This unsubscribe link is invalid or has expired")
    user.email_prefs = {k: False for k in EMAIL_KINDS}
    await db.commit()
    return {"email": user.email, "unsubscribed": True}
