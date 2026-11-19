# PrepForge — Multi-Tenant AI Interview Prep Platform

Organizations (colleges, bootcamps, hiring teams) sign up as **tenants**, invite their members, and give them:
a LeetCode-style judge that runs code in **isolated Docker sandboxes**, **AI mock interviews** with rubric-based
scorecards, AI code reviews, progressive hints, and cohort analytics for mentors.

**Stack:** FastAPI · SQLAlchemy 2 (async) · PostgreSQL · JWT · Docker SDK · Claude API (Anthropic SDK) · React + Vite + Monaco

## Features

| Area | What's built |
|---|---|
| **Multi-tenancy** | Shared-schema isolation: every tenant row has `tenant_id`, taken from the verified JWT and never from request input. Cross-tenant lookups return 404. Global problem bank shared by all tenants plus org-private problems. |
| **Auth & RBAC** | Org self-signup, login by org slug, access + refresh JWTs (type-checked), single-use invite codes (optionally email-locked). Roles: `owner > admin > mentor > candidate`. |
| **Code judge** | Python and JavaScript, stdin/stdout problems, sample + hidden tests, verdicts (accepted / wrong answer / runtime error / TLE), custom-input runs, per-language drafts saved in the browser. Hidden test inputs and outputs are never returned. |
| **Docker sandbox** | Fresh container per run: `--network none`, read-only rootfs, 16 MB tmpfs, 256 MB memory, 0.5 CPU, 64 PIDs, `cap-drop ALL`, `no-new-privileges`, uid `nobody`, per-test and wall-clock timeouts, optional gVisor (`SANDBOX_RUNTIME=runsc`). Expected outputs never enter the container. |
| **AI mock interviews** | Behavioral / technical (DSA) / system design, tuned by role, level and focus. The interviewer asks one question per turn and follows up. Ending the interview produces a structured scorecard: 5 rubric dimensions (1–5), overall 0–100, hire signal, strengths, improvements and topics to practice. |
| **AI coaching** | AI code review on any submission (correctness, complexity, style), cached per submission. 3-level progressive hints that never give the full solution. |
| **Analytics** | Candidate dashboard: solved by difficulty, acceptance rate, interview score trend, weakest rubric dimensions. Mentor view: cohort leaderboard. |
| **Quotas** | Per-tenant, per-plan limits on AI requests (monthly) and code runs (daily), metered in `usage_events`. 429 when exceeded. |

### LLM integration
`LLM_PROVIDER=anthropic` uses Claude (`claude-opus-5-5` by default). Chat turns run at low effort for latency; scorecards use
structured outputs (`messages.parse` with a Pydantic schema). Interview turns enable server-side refusal fallbacks.
Candidate text is always sent as user content and never interpolated into system prompts.
`LLM_PROVIDER=mock` (the default) is a deterministic offline provider, so the whole app runs and tests pass without an API key.

## Run it

### Option A — Docker Compose (everything)
```bash
cp .env.example .env      # set LLM_PROVIDER=anthropic + ANTHROPIC_API_KEY for real AI
docker compose up -d --build
```
Web: http://localhost:3080 (change with `WEB_PORT`) · API docs: http://localhost:8010/docs (`API_PORT`)

### Option B — local dev
```bash
cd backend
python -m venv .venv && .venv/Scripts/activate   # Windows (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
set DATABASE_URL=sqlite+aiosqlite:///./dev.db    # or point at Postgres
uvicorn app.main:app --reload
```
```bash
cd frontend && npm install && npm run dev        # http://localhost:5173, proxies /api -> :8000
```
Sandbox images are pulled on first run (`python:3.12-alpine`, `node:22-alpine`); pre-pull them to avoid a slow first submission.

### Tests
```bash
cd backend && .venv/Scripts/python -m pytest -q
```
Covers auth, invites and RBAC, **cross-tenant isolation**, hidden-test non-leakage, verdicts, interviews and scorecards,
analytics, quotas, and real-Docker sandbox checks (network blocked, read-only FS, non-root, timeouts). The Docker tests skip if no daemon is running.

## Architecture
```
React (Vite, Monaco) ──/api──▶ FastAPI ──▶ PostgreSQL   (tenants, users, problems, submissions, interviews, usage)
                                  │
                                  ├──▶ Docker daemon ──▶ throwaway sandbox container per run
                                  └──▶ Claude API (interviewer, scorecard, code review, hints)
```
```
backend/app/
  api/deps.py          JWT auth, role guards, tenant-scoped fetch helper
  api/routes/          auth, org, problems, submissions, interviews, analytics
  services/sandbox.py  Docker executor + grader
  services/llm.py      Anthropic + mock providers, prompts, scorecard schema
  services/quota.py    per-plan metering
  db/models.py         ORM models
```

## Roadmap (next features)
- **Alembic migrations** (tables are created on startup for now) and **Postgres row-level security** as defense in depth.
- **Job queue for the sandbox** (arq/Celery + Redis) on dedicated worker hosts, so the API doesn't need the Docker socket.
- **Resume / JD upload**: tailor interview questions to a candidate's resume and the target job description.
- **Streaming interviewer replies** (SSE) and **voice interviews** (speech-to-text in, TTS out).
- **More languages** (Java, C++, Go), function-signature problems, and complexity checks via large hidden tests.
- **Spaced-repetition practice plans** built from weak rubric dimensions; daily streaks.
- **Peer mock interviews** with shared editor (WebSockets) and mentor review of transcripts.
- **Billing** (Stripe) wired to plans and quotas; SSO (SAML/OIDC) for enterprise tenants; audit log.
