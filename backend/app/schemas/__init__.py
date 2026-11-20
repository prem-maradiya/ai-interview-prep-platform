from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Language = Literal["python", "javascript", "java", "cpp", "go"]
InterviewKind = Literal["behavioral", "technical", "system_design"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- auth ----------
class RegisterOrgIn(BaseModel):
    org_name: str = Field(min_length=2, max_length=120)
    org_slug: str = Field(min_length=2, max_length=60, pattern=r"^[a-z0-9-]+$")
    full_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    org_slug: str
    email: EmailStr
    password: str


class AcceptInviteIn(BaseModel):
    code: str
    full_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class RefreshIn(BaseModel):
    refresh_token: str


class PasswordResetRequestIn(BaseModel):
    org_slug: str = Field(max_length=60)
    email: EmailStr


class PasswordResetConfirmIn(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TenantOut(ORM):
    id: str
    name: str
    slug: str
    plan: str


class UserOut(ORM):
    id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    tenant: TenantOut


class MemberOut(ORM):
    id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime


class MemberUpdateIn(BaseModel):
    role: Literal["admin", "mentor", "candidate"] | None = None
    is_active: bool | None = None


class InviteIn(BaseModel):
    email: EmailStr | None = None
    role: Literal["admin", "mentor", "candidate"] = "candidate"
    days_valid: int = Field(default=7, ge=1, le=30)


class InviteOut(ORM):
    id: str
    code: str
    email: str | None
    role: str
    expires_at: datetime
    used_at: datetime | None


# ---------- problems ----------
class TestCaseIn(BaseModel):
    input: str = ""
    expected_output: str
    is_hidden: bool = False


class TestCaseOut(ORM):
    id: str
    input: str
    expected_output: str
    is_hidden: bool


class ProblemIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str
    difficulty: Literal["easy", "medium", "hard"] = "easy"
    tags: list[str] = []
    companies: list[str] = Field(default=[], max_length=20)
    starter_code: dict[str, str] = {}
    # Function-style problem: {function, params: [{name, type}], returns, compare}. Omit for stdin/stdout.
    signature: dict | None = None
    tests: list[TestCaseIn] = Field(min_length=1)


class ProblemSummary(ORM):
    id: str
    title: str
    difficulty: str
    tags: list[str]
    companies: list[str] = []
    is_global: bool = False
    solved: bool = False
    style: str = "stdio"  # "stdio" | "function"


class ProblemOut(ORM):
    id: str
    title: str
    description: str
    difficulty: str
    tags: list[str]
    companies: list[str] = []
    starter_code: dict[str, str]
    signature: dict | None = None
    is_global: bool = False
    sample_tests: list[TestCaseOut] = []
    hidden_test_count: int = 0
    # Set while a running contest includes this problem (hints, reviews and discussions are off for candidates).
    contest: dict | None = None


# ---------- code execution ----------
class RunIn(BaseModel):
    language: Language
    code: str = Field(max_length=50_000)
    custom_input: str | None = Field(default=None, max_length=10_000)
    telemetry: "Telemetry | None" = None  # used on submit only


class TestResultOut(BaseModel):
    index: int
    status: str
    passed: bool
    hidden: bool
    time_ms: int
    input: str | None = None
    expected: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    large: bool = False  # a generated complexity-check input
    printed: str | None = None  # function-style problems: what the code printed (stdout holds the return value)


class RunOut(BaseModel):
    verdict: str
    passed: int
    total: int
    runtime_ms: int
    results: list[TestResultOut]


class SubmissionOut(ORM):
    id: str
    user_id: str
    problem_id: str
    language: str
    code: str
    verdict: str
    passed: int
    total: int
    runtime_ms: int
    results: list[dict]
    ai_review: str | None
    created_at: datetime


class StaffSubmissionOut(SubmissionOut):
    """What mentors see: the submission plus integrity signals (candidates don't see these)."""
    telemetry: dict | None = None
    integrity_flags: list[dict] | None = None


class HintIn(BaseModel):
    language: Language = "python"
    code: str = Field(default="", max_length=50_000)
    level: int = Field(default=1, ge=1, le=3)


class TextOut(BaseModel):
    text: str


# ---------- assignments, comments, integrity ----------
class AssignmentIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=5000)
    due_at: datetime
    problem_ids: list[str] = Field(default=[], max_length=50)
    interview_kind: InterviewKind | None = None
    interview_count: int = Field(default=0, ge=0, le=10)
    assign_all: bool = True
    assignee_ids: list[str] = Field(default=[], max_length=500)


class CommentIn(BaseModel):
    target_type: Literal["interview", "submission"]
    target_id: str
    body: str = Field(min_length=1, max_length=4000)


class CommentOut(BaseModel):
    id: str
    author_id: str
    author_name: str
    author_role: str
    body: str
    created_at: datetime


class Telemetry(BaseModel):
    """Editor activity the client reports with a submission. All optional; the server derives signals from it."""
    paste_chars: int = Field(default=0, ge=0, le=10_000_000)
    paste_events: int = Field(default=0, ge=0, le=100_000)
    tab_switches: int = Field(default=0, ge=0, le=100_000)
    time_spent_s: float | None = Field(default=None, ge=0, le=10_000_000)


class ProblemListIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=2000)
    problem_ids: list[str] = Field(min_length=1, max_length=100)


# ---------- documents ----------
DocumentKind = Literal["resume", "job_description"]


class DocumentIn(BaseModel):
    kind: DocumentKind
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=30, max_length=30_000)


class DocumentSummary(BaseModel):
    id: str
    kind: str
    title: str
    chars: int
    preview: str
    created_at: datetime


class DocumentOut(ORM):
    id: str
    kind: str
    title: str
    text: str
    created_at: datetime


class FitIn(BaseModel):
    resume_id: str
    job_description_id: str


# ---------- interviews ----------
class InterviewIn(BaseModel):
    kind: InterviewKind
    role_title: str = Field(default="Software Engineer", max_length=120)
    level: Literal["intern", "junior", "mid", "senior", "staff"] = "junior"
    focus: str = Field(default="", max_length=300)
    resume_id: str | None = None
    job_description_id: str | None = None


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    input_mode: Literal["text", "voice"] = "text"
    code: str | None = Field(default=None, max_length=50_000)
    code_language: Language | None = None
    board: dict | None = None  # system design whiteboard: {"elements": [...]}


class InterviewRunIn(BaseModel):
    language: Language
    code: str = Field(max_length=50_000)
    stdin: str = Field(default="", max_length=10_000)


class MessageOut(ORM):
    seq: int
    sender: str
    content: str
    input_mode: str = "text"
    code: str | None = None
    code_language: str | None = None
    board_text: str | None = None
    created_at: datetime


class InterviewOut(ORM):
    id: str
    user_id: str
    kind: str
    role_title: str
    level: str
    focus: str
    status: str
    overall_score: float | None
    scorecard: dict | None
    created_at: datetime
    completed_at: datetime | None
    has_resume: bool = False
    has_job_description: bool = False
    code: str | None = None
    code_language: str | None = None
    board: dict | None = None
    messages: list[MessageOut] = []


class InterviewSummary(ORM):
    id: str
    kind: str
    role_title: str
    level: str
    status: str
    overall_score: float | None
    created_at: datetime
