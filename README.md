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
| **Resume & job fit** | Upload a resume or job description (PDF/TXT/MD, text extracted server-side, file discarded) or paste it. Private to the owner, even within the org. A **fit report** gives a match score, matched/missing skills, truthful resume edits and likely questions. |
| **Tailored interviews** | Start any interview from a resume and/or job description: questions target your actual projects and the role's requirements, and the scorecard is calibrated to the job. Documents are snapshotted at start. |
| **Code in interviews** | Technical interviews have a Monaco editor beside the chat. Your code is sent with each answer (only when it changed) so the interviewer can react to it, and you can run it in the sandbox on custom input. |
| **Voice mode** | Speak answers (browser Speech Recognition) and hear questions read aloud (speech synthesis), with no server cost. Spoken answers get **delivery feedback** in the scorecard: words per answer, filler words ("um", "basically"…) and tips. |
| **Daily study plan** | Spaced repetition (simplified SM-2) schedules every attempted problem: solved ones come back after 1, 3, then ~8+ days; failed ones tomorrow. "Today's plan" shows due reviews, new problems ranked by your weak tags and level, a suggested interview for your weakest rubric area, and your streak. |
| **Assignments** | Mentors assign problems and/or mock interviews (a kind and count) with a deadline, to all candidates or selected ones. Progress is computed live from real work done after the assignment was created: not started / in progress / completed / late / overdue, with a per-candidate progress table. |
| **Mentor review** | Mentors open any candidate's interview (read-only) or submission and leave feedback in a thread the candidate can reply to. A member page shows each candidate's recent interviews and submissions. |
| **Integrity signals** | The editor reports paste volume, tab switches and time spent with each submission. The server flags paste-heavy code, frequent tab switching, very fast solves of medium/hard problems, and code that matches another member's accepted solution **after renaming variables and removing comments**. Signals are shown only to mentors, labelled as hints, not proof. |
| **CSV exports** | Cohort report and per-assignment progress as CSV. Cells that spreadsheets would execute as formulas (`=`, `+`, `-`, `@`) are neutralised (CSV-injection safe). |
| **AI coaching** | AI code review on any submission (correctness, complexity, style), cached per submission. 3-level progressive hints that never give the full solution. |
| **Analytics** | Candidate dashboard: solved by difficulty, acceptance rate, interview score trend, weakest rubric dimensions. Mentor view: cohort leaderboard. |
| **Quotas** | Per-tenant, per-plan limits on AI requests (monthly) and code runs (daily), metered in `usage_events`. 429 when exceeded. |

### LLM integration
`LLM_PROVIDER=anthropic` uses Claude (`claude-opus-5-5` by default). Chat turns run at low effort for latency; scorecards use
structured outputs (`messages.parse` with a Pydantic schema), as do fit reports. Interview turns enable server-side refusal
fallbacks and **prompt caching** (each turn resends the growing conversation, so the prefix is cached).
Candidate text (answers, code, resumes, job descriptions) is always sent as tagged user content, never interpolated into
system prompts, and the model is told to treat it as data.
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
  services/integrity.py integrity signals + code-similarity normaliser
  services/assignments.py live assignment progress
  services/csv_export.py CSV export, formula-injection safe
  worker.py            arq worker that runs sandbox jobs
  services/llm.py      Anthropic + mock providers, prompts, scorecard schema
  services/quota.py    per-plan metering
  db/models.py         ORM models
```

## Roadmap (next features)
- **Notifications:** email or in-app alerts for new assignments, approaching deadlines and mentor feedback.
- **Sandbox:** CPU-time (not wall-clock) limits so a busy host can't cause false TLEs; a warm container pool to cut latency.
- **Postgres row-level security** as defense in depth.
- **More languages** (Java, C++, Go), function-signature problems, and complexity checks via large hidden tests.
- **Peer mock interviews** with a shared editor (WebSockets).
- **Billing** (Stripe) wired to plans and quotas; SSO (SAML/OIDC) for enterprise tenants; audit log.
