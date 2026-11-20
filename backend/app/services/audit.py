"""Audit log of administrative and security-relevant actions, per organization.

Each event records who did what to which target, with a snapshot of names (so the log still reads
correctly after someone is renamed or deleted). Events are written in the same transaction as the
change itself: if the change rolls back, so does its log entry. Admins and owners can read and
export the log; nobody can edit or delete entries through the API.
"""
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent, User
from app.services.ratelimit import client_ip

# action -> human-readable description, also used for the filter menu in the UI.
ACTIONS = {
    "member.role_changed": "Changed a member's role",
    "member.deactivated": "Deactivated a member",
    "member.reactivated": "Reactivated a member",
    "invite.created": "Created an invite",
    "invite.accepted": "Joined with an invite",
    "problem.created": "Created a problem",
    "problem.updated": "Edited a problem",
    "problem.deleted": "Deleted a problem",
    "assignment.created": "Created an assignment",
    "assignment.deleted": "Deleted an assignment",
    "auth.password_changed": "Changed their password",
    "auth.password_reset": "Reset their password by email",
    "auth.identity_linked": "Linked a sign-in provider",
    "auth.identity_unlinked": "Unlinked a sign-in provider",
}


def record(db: AsyncSession, actor: User | None, action: str, *, tenant_id: str | None = None,
           target_type: str = "", target_id: str = "", target_label: str = "", details: dict | None = None,
           request: Request | None = None) -> None:
    """Add an audit event to the current transaction (the caller commits)."""
    assert action in ACTIONS, action
    db.add(AuditEvent(
        tenant_id=tenant_id or actor.tenant_id, actor_id=actor.id if actor else None,
        actor_name=actor.full_name if actor else "", actor_email=actor.email if actor else "",
        action=action, target_type=target_type, target_id=target_id, target_label=target_label[:200],
        details=details or {}, ip=client_ip(request) if request else None,
    ))
