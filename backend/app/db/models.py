"""ORM models.

Multi-tenancy model: shared database, shared schema, every tenant-owned row carries
`tenant_id`. All reads/writes go through helpers in `app.api.deps` that scope by the
tenant taken from the verified JWT, never from request input.
"""
import uuid
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Role(StrEnum):
    owner = "owner"
    admin = "admin"
    mentor = "mentor"
    candidate = "candidate"


class Plan(StrEnum):
    free = "free"
    pro = "pro"
    enterprise = "enterprise"


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    plan: Mapped[str] = mapped_column(String(20), default=Plan.free)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120))
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=Role.candidate)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Email opt-ins by kind (see app.services.email.EMAIL_KINDS); a missing key means the default.
    email_prefs: Mapped[dict] = mapped_column(JSON, default=dict)
    last_digest_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tenant: Mapped[Tenant] = relationship(lazy="joined")


class OAuthIdentity(Base):
    """A sign-in identity from an external provider (Google, Microsoft, GitHub) linked to a user.

    Users are per-organization, so the same Google account can be linked to one user in each org.
    `subject` is the provider's stable user id (never the email, which can change).
    """

    __tablename__ = "oauth_identities"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", "subject"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Invite(Base):
    __tablename__ = "invites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default=Role.candidate)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Problem(Base):
    """A coding problem. tenant_id NULL = global problem shared with every tenant."""

    __tablename__ = "problems"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    difficulty: Mapped[str] = mapped_column(String(10), default="easy")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    companies: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")  # where it's been reported
    starter_code: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tests: Mapped[list["TestCase"]] = relationship(
        back_populates="problem", cascade="all, delete-orphan", order_by="TestCase.position", lazy="selectin"
    )


class TestCase(Base):
    __tablename__ = "test_cases"
    __test__ = False  # keep pytest from collecting this class
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), index=True)
    input: Mapped[str] = mapped_column(Text, default="")
    expected_output: Mapped[str] = mapped_column(Text, default="")
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)

    problem: Mapped[Problem] = relationship(back_populates="tests")


class Submission(Base):
    __tablename__ = "submissions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), index=True)
    language: Mapped[str] = mapped_column(String(20))
    code: Mapped[str] = mapped_column(Text)
    verdict: Mapped[str] = mapped_column(String(30))
    passed: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    runtime_ms: Mapped[int] = mapped_column(Integer, default=0)
    results: Mapped[list] = mapped_column(JSON, default=list)
    ai_review: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Editor telemetry sent by the client (paste/tab-switch counts, time spent) and the integrity
    # signals derived from it plus code similarity. Signals are hints for mentors, not verdicts.
    telemetry: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    integrity_flags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class Interview(Base):
    __tablename__ = "interviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # behavioral | technical | system_design
    role_title: Mapped[str] = mapped_column(String(120))
    level: Mapped[str] = mapped_column(String(30))
    focus: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    # Snapshots taken at start, so the interview stays reproducible if the documents change later.
    resume_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_description_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Latest state of the in-interview code editor (technical interviews).
    code: Mapped[str | None] = mapped_column(Text, nullable=True)
    code_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    scorecard: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[list["InterviewMessage"]] = relationship(
        back_populates="interview", cascade="all, delete-orphan", order_by="InterviewMessage.seq", lazy="selectin"
    )

    @property
    def has_resume(self) -> bool:
        return bool(self.resume_text)

    @property
    def has_job_description(self) -> bool:
        return bool(self.job_description_text)


class InterviewMessage(Base):
    __tablename__ = "interview_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    sender: Mapped[str] = mapped_column(String(20))  # interviewer | candidate
    content: Mapped[str] = mapped_column(Text)
    input_mode: Mapped[str] = mapped_column(String(10), default="text", server_default="text")  # text | voice
    code: Mapped[str | None] = mapped_column(Text, nullable=True)  # editor snapshot sent with this answer
    code_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    interview: Mapped[Interview] = relationship(back_populates="messages")


class Document(Base):
    """A candidate's resume or a target job description, stored as extracted plain text.
    Private to its owner."""

    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # resume | job_description
    title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ReviewItem(Base):
    """Spaced-repetition state for one (user, problem): when to revisit it, and how well it's known."""

    __tablename__ = "review_items"
    __table_args__ = (UniqueConstraint("user_id", "problem_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problems.id", ondelete="CASCADE"), index=True)
    ease: Mapped[float] = mapped_column(Float, default=2.5)
    interval_days: Mapped[float] = mapped_column(Float, default=0)
    reps: Mapped[int] = mapped_column(Integer, default=0)  # consecutive successful reviews
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    last_verdict: Mapped[str | None] = mapped_column(String(30), nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Assignment(Base):
    """Work a mentor assigns: problems to solve and/or mock interviews to complete, by a deadline."""

    __tablename__ = "assignments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    problem_ids: Mapped[list] = mapped_column(JSON, default=list)
    interview_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    interview_count: Mapped[int] = mapped_column(Integer, default=0)
    assign_all: Mapped[bool] = mapped_column(Boolean, default=True)  # every candidate, including future ones
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    members: Mapped[list["AssignmentMember"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class AssignmentMember(Base):
    __tablename__ = "assignment_members"
    __table_args__ = (UniqueConstraint("assignment_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class Comment(Base):
    """Mentor feedback (and candidate replies) on an interview or a submission."""

    __tablename__ = "comments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    target_type: Mapped[str] = mapped_column(String(20))  # interview | submission
    target_id: Mapped[str] = mapped_column(String(36), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    author: Mapped[User] = relationship(lazy="joined")


class Notification(Base):
    """In-app notification. `dedupe_key` makes generated reminders idempotent (one per user per key)."""

    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # assignment | due_soon | comment | badge
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(300), default="")  # in-app path, e.g. /assignments/<id>
    dedupe_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class UserBadge(Base):
    __tablename__ = "user_badges"
    __table_args__ = (UniqueConstraint("user_id", "badge"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    badge: Mapped[str] = mapped_column(String(40))
    earned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ProblemList(Base):
    """A curated set of problems, e.g. 'Arrays & hashing essentials'. tenant_id NULL = global list."""

    __tablename__ = "problem_lists"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=True)
    slug: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    problem_titles: Mapped[list] = mapped_column(JSON, default=list)  # for global lists (ids differ per DB)
    problem_ids: Mapped[list] = mapped_column(JSON, default=list)  # for org lists
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RefreshToken(Base):
    """Server-side record of each refresh token (by JWT `jti`).

    Tokens rotate on every refresh. All tokens descending from one login share a `family_id`;
    presenting an already-rotated token means it was stolen or replayed, so the whole family is revoked.
    """

    __tablename__ = "refresh_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # = jti
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UsageEvent(Base):
    """Metering for per-tenant quotas (LLM calls, sandbox runs)."""

    __tablename__ = "usage_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(20), index=True)  # llm | sandbox
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
