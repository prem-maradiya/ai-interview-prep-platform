"""Outgoing email: notifications, invites, password resets and the weekly digest.

Emails are queued on the database session and sent only after that transaction commits, so a
rolled-back request never emails anyone. Sending happens in the background (with retries) and
never slows down or fails the request that triggered it.

Backends (EMAIL_BACKEND): "console" logs each message (default - works with no setup), "smtp"
sends through any SMTP server (Mailpit locally; SES, Postmark, SendGrid, Gmail... in production),
"memory" keeps messages in a list (tests), "none" drops them.
"""
import asyncio
import html
import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import timedelta
from email.message import EmailMessage
from email.utils import make_msgid

import jwt
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import _encode, decode_token
from app.db.models import User

log = logging.getLogger("prepforge.email")

OUTBOX_KEY = "email_outbox"

# Optional emails a user can turn off (invites and password resets are always sent).
EMAIL_KINDS: dict[str, dict] = {
    "assignment": {"label": "New assignments", "default": True},
    "due_soon": {"label": "Assignment due within 24 hours", "default": True},
    "comment": {"label": "Mentor feedback on your work", "default": True},
    "badge": {"label": "Badges earned", "default": False},
    "digest": {"label": "Weekly progress digest (mentors: includes a cohort summary)", "default": True},
    "peer": {"label": "Peer interview invitations", "default": True},
}


@dataclass
class Email:
    to: str
    subject: str
    text: str
    html: str
    headers: dict[str, str] = field(default_factory=dict)


# ---------- preferences & unsubscribe ----------
def email_prefs(user: User) -> dict[str, bool]:
    saved = user.email_prefs or {}
    return {k: bool(saved.get(k, spec["default"])) for k, spec in EMAIL_KINDS.items()}


def wants(user: User, kind: str) -> bool:
    return user.is_active and kind in EMAIL_KINDS and email_prefs(user)[kind]


def unsubscribe_token(user: User) -> str:
    return _encode({"sub": user.id, "tid": user.tenant_id, "type": "unsubscribe"}, timedelta(days=365))


def read_unsubscribe_token(token: str) -> dict | None:
    try:
        return decode_token(token, "unsubscribe")
    except jwt.PyJWTError:
        return None


def app_url(path: str) -> str:
    return get_settings().public_url.rstrip("/") + path


# ---------- templates ----------
def render(*, to: str, subject: str, heading: str, paragraphs: list[str], button: tuple[str, str] | None = None,
           footer: str = "", unsubscribe_user: User | None = None) -> Email:
    """Build a plain-text + HTML email. All text is escaped; `button` is (label, absolute URL)."""
    name = get_settings().app_name
    headers: dict[str, str] = {}
    unsub = ""
    if unsubscribe_user is not None:
        unsub_url = app_url(f"/unsubscribe?token={unsubscribe_token(unsubscribe_user)}")
        one_click = app_url(f"/api/notifications/unsubscribe?token={unsubscribe_token(unsubscribe_user)}")
        headers = {"List-Unsubscribe": f"<{one_click}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
        unsub = unsub_url
        footer = (footer + " " if footer else "") + "You can change which emails you get in Account settings."

    text_parts = [heading, ""] + paragraphs
    if button:
        text_parts += ["", f"{button[0]}: {button[1]}"]
    if footer:
        text_parts += ["", "--", footer]
    if unsub:
        text_parts += [f"Unsubscribe: {unsub}"]

    e = html.escape
    paras = "".join(f'<p style="margin:0 0 14px;line-height:1.55">{e(p)}</p>' for p in paragraphs)
    cta = ""
    if button:
        cta = (f'<p style="margin:22px 0"><a href="{e(button[1], quote=True)}" style="background:#4f46e5;color:#fff;'
               f'padding:11px 20px;border-radius:8px;text-decoration:none;font-weight:600;display:inline-block">'
               f'{e(button[0])}</a></p>')
    foot = f'<p style="margin:0">{e(footer)}</p>' if footer else ""
    if unsub:
        foot += f'<p style="margin:6px 0 0"><a href="{e(unsub, quote=True)}" style="color:#6b7280">Unsubscribe</a></p>'
    body = f"""<!doctype html><html><body style="margin:0;background:#f4f5f7;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1f2937">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:28px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff;border-radius:12px;border:1px solid #e5e7eb">
<tr><td style="padding:22px 28px 6px;font-weight:700;font-size:15px;color:#4f46e5">{e(name)}</td></tr>
<tr><td style="padding:6px 28px 20px"><h1 style="font-size:20px;margin:0 0 16px">{e(heading)}</h1>{paras}{cta}</td></tr>
<tr><td style="padding:14px 28px 20px;border-top:1px solid #f0f1f3;font-size:12px;color:#6b7280">{foot}</td></tr>
</table></td></tr></table></body></html>"""
    return Email(to=to, subject=subject, text="\n".join(text_parts), html=body, headers=headers)


# ---------- queueing (sent after commit) ----------
def queue_email(db, message: Email) -> None:
    """Send `message` once the session's current transaction commits. Accepts Session or AsyncSession."""
    if get_settings().email_backend == "none" or not message.to:
        return
    session = getattr(db, "sync_session", db)
    if not session.in_transaction():
        session.begin()  # tie the message to a transaction, so a rollback discards it
    session.info.setdefault(OUTBOX_KEY, []).append(message)


@event.listens_for(Session, "after_commit")
def _flush_outbox(session: Session) -> None:
    messages = session.info.pop(OUTBOX_KEY, None)
    if messages:
        for m in messages:
            dispatch(m)


@event.listens_for(Session, "after_soft_rollback")
def _drop_outbox(session: Session, previous_transaction) -> None:
    session.info.pop(OUTBOX_KEY, None)


_pending: set[asyncio.Task] = set()


def dispatch(message: Email) -> None:
    """Send in the background. Without a running event loop (scripts), send right away."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(send(message))
        return
    task = loop.create_task(send(message))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def wait_for_outgoing() -> None:
    """Wait until queued emails have been handed to the backend (tests, graceful shutdown)."""
    while _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


# ---------- backends ----------
sent_messages: list[Email] = []  # "memory" backend


def _to_mime(m: Email) -> EmailMessage:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.email_from
    msg["To"] = m.to
    msg["Subject"] = m.subject
    msg["Message-ID"] = make_msgid(domain=s.email_from.rsplit("@", 1)[-1].rstrip(">") or None)
    for k, v in m.headers.items():
        msg[k] = v
    msg.set_content(m.text)
    msg.add_alternative(m.html, subtype="html")
    return msg


def _smtp_send(m: Email) -> None:
    s = get_settings()
    mime = _to_mime(m)
    if s.smtp_security == "ssl":
        server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_s,
                                  context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_s)
    with server:
        if s.smtp_security == "starttls":
            server.starttls(context=ssl.create_default_context())
        if s.smtp_username:
            server.login(s.smtp_username, s.smtp_password or "")
        server.send_message(mime)


async def send(m: Email, attempts: int = 3) -> bool:
    backend = get_settings().email_backend
    if backend == "memory":
        sent_messages.append(m)
        return True
    if backend == "console":
        log.info("email to %s: %s\n%s", m.to, m.subject, m.text)
        return True
    if backend != "smtp":
        return False
    for attempt in range(1, attempts + 1):
        try:
            await asyncio.to_thread(_smtp_send, m)
            return True
        except (OSError, smtplib.SMTPException) as e:
            if attempt == attempts:
                log.error("could not send email to %s (%s): %s", m.to, m.subject, e)
                return False
            await asyncio.sleep(2 ** attempt)
    return False
