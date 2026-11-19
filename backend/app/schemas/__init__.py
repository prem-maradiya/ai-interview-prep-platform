from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Language = Literal["python", "javascript"]
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
    starter_code: dict[str, str] = {}
    tests: list[TestCaseIn] = Field(min_length=1)


class ProblemSummary(ORM):
    id: str
    title: str
    difficulty: str
    tags: list[str]
    is_global: bool = False
    solved: bool = False


class ProblemOut(ORM):
    id: str
    title: str
    description: str
    difficulty: str
    tags: list[str]
    starter_code: dict[str, str]
    is_global: bool = False
    sample_tests: list[TestCaseOut] = []
    hidden_test_count: int = 0


# ---------- code execution ----------
class RunIn(BaseModel):
    language: Language
    code: str = Field(max_length=50_000)
    custom_input: str | None = Field(default=None, max_length=10_000)


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


class RunOut(BaseModel):
    verdict: str
    passed: int
    total: int
    runtime_ms: int
    results: list[TestResultOut]


class SubmissionOut(ORM):
    id: str
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


class HintIn(BaseModel):
    language: Language = "python"
    code: str = Field(default="", max_length=50_000)
    level: int = Field(default=1, ge=1, le=3)


class TextOut(BaseModel):
    text: str


# ---------- interviews ----------
class InterviewIn(BaseModel):
    kind: InterviewKind
    role_title: str = Field(default="Software Engineer", max_length=120)
    level: Literal["intern", "junior", "mid", "senior", "staff"] = "junior"
    focus: str = Field(default="", max_length=300)


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class MessageOut(ORM):
    seq: int
    sender: str
    content: str
    created_at: datetime


class InterviewOut(ORM):
    id: str
    kind: str
    role_title: str
    level: str
    focus: str
    status: str
    overall_score: float | None
    scorecard: dict | None
    created_at: datetime
    completed_at: datetime | None
    messages: list[MessageOut] = []


class InterviewSummary(ORM):
    id: str
    kind: str
    role_title: str
    level: str
    status: str
    overall_score: float | None
    created_at: datetime
