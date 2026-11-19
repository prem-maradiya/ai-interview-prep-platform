# PrepForge — Multi-Tenant AI Interview Prep Platform

Organizations (colleges, bootcamps, hiring teams) sign up as **tenants**, invite their members, and give them:
a LeetCode-style judge that runs code in **isolated Docker sandboxes**, **AI mock interviews** with rubric-based
scorecards, AI code reviews, progressive hints, and cohort analytics for mentors.

**Stack:** FastAPI · SQLAlchemy 2 (async) + Alembic · PostgreSQL · Redis + arq · JWT · Docker SDK · Claude API (Anthropic SDK) · React + Vite + Monaco · GitHub Actions

## Features

| Area | What's built |
|---|---|
| **Multi-tenancy** | Shared-schema isolation: every tenant row has `tenant_id`, taken from the verified JWT and never from request input. Cross-tenant lookups return 404. Global problem bank shared by all tenants plus org-private problems. |
| **Auth & RBAC** | Org self-signup, login by org slug, single-use invite codes (optionally email-locked). Roles: `owner > admin > mentor > candidate`. 15-minute access JWTs plus **rotating refresh tokens** tracked server-side: re-using an old refresh token revokes the whole session (theft detection). Logout and deactivating a member revoke their tokens. |
| **Abuse protection** | Rate limits on login (per IP + account), signup, invite acceptance and refresh, Redis-backed so they hold across API replicas. nginx overwrites `X-Forwarded-For`, so clients can't spoof their IP. Constant-time login for unknown accounts. Production mode refuses to start with a weak JWT secret or without the job queue. |
| **Code judge** | Python and JavaScript, stdin/stdout problems, sample + hidden tests, verdicts (accepted / wrong answer / runtime error / TLE), custom-input runs, per-language drafts saved in the browser. Hidden test inputs and outputs are never returned. |
| **Docker sandbox** | Runs on a separate **worker** process fed by a Redis job queue (arq). Only the worker has the Docker socket; the API never does. Fresh container per run: `--network none`, read-only rootfs, 16 MB tmpfs, 256 MB memory, 0.5 CPU, 64 PIDs, `cap-drop ALL`, `no-new-privileges`, uid `nobody`, per-test and wall-clock timeouts, optional gVisor (`SANDBOX_RUNTIME=runsc`). Expected outputs never enter the container. |
| **AI mock interviews** | Behavioral / technical (DSA) / system design, tuned by role, level and focus. The interviewer asks one question per turn, follows up, and its replies **stream word by word** over Server-Sent Events. Ending the interview produces a structured scorecard: 5 rubric dimensions (1–5), overall 0–100, hire signal, strengths, improvements and topics to practice. |
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
Web: http://localhost:3080 (change with `WEB_PORT`) · API docs: http://localhost:3080/api/docs

Services: `db` (Postgres), `redis`, `backend` (runs `alembic upgrade head`, then the API), `worker` (sandbox runner), `frontend` (nginx). Only nginx is published on the host.

### Option B — local dev
```bash
cd backend
python -m venv .venv && .venv/Scripts/activate   # Windows (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
set DATABASE_URL=sqlite+aiosqlite:///./dev.db    # or point at Postgres
alembic upgrade head                             # create / update the schema
uvicorn app.main:app --reload                    # SANDBOX_BACKEND=docker (default) runs sandboxes in-process
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
analytics, quotas, refresh rotation and reuse detection, rate limits, SSE streaming, the queue worker, production config checks,
migrations-vs-models drift, and real-Docker sandbox checks (network blocked, read-only FS, non-root, timeouts). The Docker tests skip if no daemon is running.

CI (`.github/workflows/ci.yml`) runs the tests, applies migrations to a real PostgreSQL and checks for drift, builds the frontend, and builds the Docker images.

### Schema changes
```bash
cd backend
alembic revision --autogenerate -m "describe change"   # then review the generated file
alembic upgrade head
```

## Architecture
```
React (Vite, Monaco) ──▶ nginx ──/api──▶ FastAPI ──▶ PostgreSQL (tenants, users, problems, submissions, interviews,
                                           │   │                    usage, refresh tokens; schema via Alembic)
                                           │   └──▶ Claude API (interviewer [streamed], scorecard, review, hints)
                                           ▼
                                     Redis (job queue + rate limits)
                                           ▼
                                  arq worker ──▶ Docker daemon ──▶ throwaway sandbox container per run
```
```
backend/app/
  api/deps.py          JWT auth, role guards, tenant-scoped fetch helper
  api/routes/          auth, org, problems, submissions, interviews, analytics
  services/sandbox.py  Docker executor, queue client + grader
  services/ratelimit.py Redis / in-memory rate limiter
  worker.py            arq worker that runs sandbox jobs
  services/llm.py      Anthropic + mock providers, prompts, scorecard schema
  services/quota.py    per-plan metering
  db/models.py         ORM models
```

## Roadmap (next features)
- **Resume / JD upload**: tailor interview questions to a candidate's resume and the target job description.
- **Voice interviews** (speech-to-text in, TTS out) and a **shared code editor** inside technical interviews.
- **Postgres row-level security** as defense in depth; a warm pool of sandbox containers to cut run latency.
- **More languages** (Java, C++, Go), function-signature problems, and complexity checks via large hidden tests.
- **Spaced-repetition practice plans** built from weak rubric dimensions; daily streaks.
- **Peer mock interviews** with shared editor (WebSockets) and mentor review of transcripts.
- **Billing** (Stripe) wired to plans and quotas; SSO (SAML/OIDC) for enterprise tenants; audit log.
