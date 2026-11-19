from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select, update

from app.api.deps import DB, CurrentUser
from app.db.models import Notification
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
