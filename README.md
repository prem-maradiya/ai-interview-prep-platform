# PrepForge — Multi-Tenant AI Interview Prep Platform

Organizations (colleges, bootcamps, hiring teams) sign up as **tenants**, invite their members, and give them:
a LeetCode-style judge that runs code in **isolated Docker sandboxes**, **AI mock interviews** with rubric-based
scorecards, AI code reviews, progressive hints, and cohort analytics for mentors.

**Stack:** FastAPI · SQLAlchemy 2 (async) + Alembic · PostgreSQL · Redis + arq · JWT · Docker SDK · Claude API (Anthropic SDK) · React + Vite + Monaco · GitHub Actions

[![CI](https://github.com/prem-maradiya/ai-interview-prep-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/prem-maradiya/ai-interview-prep-platform/actions/workflows/ci.yml)

## Screenshots

| Candidate dashboard: daily plan, streak, progress | Code judge running in a Docker sandbox |
|---|---|
| ![Dashboard with today's plan](docs/screenshots/dashboard.png) | ![Code editor with test results](docs/screenshots/code-judge.png) |
| **Technical mock interview with shared code editor** | **Resume ↔ job description fit report** |
| ![Interview chat beside a code editor](docs/screenshots/interview-with-code.png) | ![Fit report with matched and missing skills](docs/screenshots/resume-fit-report.png) |
| **Mentor view: assignment progress** | **Mentor view: integrity signals on a submission** |
| ![Assignment progress per candidate](docs/screenshots/assignment-progress.png) | ![Integrity signals: similarity, paste, tab switching](docs/screenshots/integrity-signals.png) |

<sub>Screenshots use the built-in offline AI provider (`LLM_PROVIDER=mock`), so interviewer and report text is canned; with `LLM_PROVIDER=anthropic` it comes from Claude.</sub>

## Features

| Area | What's built |
|---|---|
| **Multi-tenancy** | Shared-schema isolation: every tenant row has `tenant_id`, taken from the verified JWT and never from request input. Cross-tenant lookups return 404. Global problem bank shared by all tenants plus org-private problems. |
| **Row-level security** | Defense in depth: **PostgreSQL itself** enforces tenant isolation, so even a query that forgets its `tenant_id` filter can't read or write another tenant's rows. Each transaction sets the tenant from the verified token (`set_config(..., true)`, so nothing leaks across pooled connections) and runs as a non-superuser role; a session with no tenant sees nothing (fails closed). Child tables (test cases, interview messages...) follow their parent row's visibility, and shared global problems are read-only to tenants. Only sign-in, sign-up, seeding and background jobs run in an explicit cross-tenant *system* context. |
| **Auth & RBAC** | Org self-signup, login by org slug, single-use invite codes (optionally email-locked, and emailed to the invitee). **Password reset by email**: single-use links that expire in 30 minutes and sign out every other session. Roles: `owner > admin > mentor > candidate`. 15-minute access JWTs plus **rotating refresh tokens** tracked server-side: re-using an old refresh token revokes the whole session (theft detection). Logout and deactivating a member revoke their tokens. |
| **Social sign-in** | Sign in, join with an invite, or create an organization with **Google, Microsoft or GitHub** (OAuth 2.0 authorization code + PKCE, `state` checked against an HttpOnly cookie). Accounts are matched by email only when the provider has *verified* it, so a Microsoft account showing someone else's address can't take over their account. Users can link and unlink providers and set a password on the Account page. Each button appears only when its keys are configured. |
| **Abuse protection** | Rate limits on login (per IP + account), signup, invite acceptance and refresh, Redis-backed so they hold across API replicas. nginx overwrites `X-Forwarded-For`, so clients can't spoof their IP. Constant-time login for unknown accounts. Production mode refuses to start with a weak JWT secret or without the job queue. |
| **Code judge** | **Python, JavaScript, Java, C++ and Go**. Two problem styles: classic stdin/stdout, and **function-style** (LeetCode-like: you write `twoSum(nums, target)` and the judge calls it). Sample + hidden tests; verdicts: accepted, wrong answer, compile error (with compiler output), runtime error, time limit and memory limit exceeded, and **too slow** (correct, but timed out on a large input). Custom-input runs, per-language drafts and starter templates. Hidden test inputs and outputs are never returned. 21 seeded problems (easy → hard) with company tags. |
| **Function-style problems** | A problem declares a signature such as `pairSum(nums: int[], target: int) -> int[]` (types: int, long, float, bool, string and 1-D/2-D arrays). Starter code for all five languages is generated from it, and a small driver is generated per language that reads the arguments, calls the function and prints the return value as JSON. Results are compared as JSON values (floats within 1e-6, optionally in any order). What the candidate prints is shown separately from what the function returned. Mentors can author these in the problem editor. |
| **Complexity checks** | Hidden stress tests with up to 200,000 elements, stored as compact generator specs (`@gen {...}`) and expanded inside the sandbox, so large inputs never go through the job queue. Their expected answers are computed once, at seed time, by reference solutions. An O(n²) answer that passes every normal test but times out on a large one gets **too slow**, with a hint to look for a faster algorithm. |
| **Docker sandbox** | Runs on a separate **worker** process fed by a Redis job queue (arq). Only the worker has the Docker socket; the API never does. A **warm pool** of pre-started containers (one per language by default) skips container start-up: a run `docker exec`s into one, and the container is destroyed right after, so each one still serves exactly one run. Python runs went from ~980 ms to ~600 ms (median, full stack), and the stdlib bytecode and a precompiled `<bits/stdc++.h>` are baked into the images (C++ runs dropped from ~3.5 s to ~1.1 s). Fresh container per run from a small per-language Alpine image: `--network none`, read-only rootfs, tmpfs, per-language memory and PID limits, `cap-drop ALL`, `no-new-privileges`, uid `nobody`, optional gVisor (`SANDBOX_RUNTIME=runsc`). **Time limits are CPU time, enforced by the kernel (`RLIMIT_CPU`)**, so verdicts are the same on a busy laptop and a fast server; a separate wall-clock limit catches programs that sleep or block. Expected outputs never enter the container. |
| **System design whiteboard** | A shared diagram canvas (services, databases, queues, notes, labelled arrows). In AI system design interviews the interviewer reads a text description of your diagram with each answer (only when it changed) and grades the final design; in peer interviews both people draw and every change syncs live as small operations, so two people editing different shapes never overwrite each other. |
| **Peer mock interviews** | Practise with a teammate: open a room (for anyone in the org, or invite one member), take turns as candidate and interviewer on a different problem each round, then rate each other (problem solving, communication, code quality, hire signal). The candidate's code, chat, test runs and who's online sync live over **WebSockets**; only the candidate can type, so there are never two writers. The interviewer keeps private notes. Rooms work across several API replicas through **Redis pub/sub**, everything is saved as it happens (a reload loses nothing), and mentors see each candidate's peer history. |
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
| **Analytics** | Candidate dashboard: solved by difficulty, acceptance rate, interview score trend, weakest rubric dimensions. Mentor view: cohort leaderboard with XP and levels. Mentors' weekly digest email adds a **cohort summary**: who practised, problems solved, assignment completion and who has gone quiet. |
| **Contests** | Mentors schedule timed contests (up to 12 problems, up to 7 days). ICPC-style scoreboard: problems solved, then penalty time (minutes to each solve plus a penalty per rejected attempt; compile errors are free), ties share a rank, the first solve of each problem is highlighted. Optional **freeze** hides others' last-minute results until the end. While it runs, every submission to its problems counts automatically and candidates can't use hints, AI reviews, discussions or editorials for them. Problems stay hidden until the start. |
| **Discussions & editorials** | Per-problem discussion threads inside the organization (markdown and code blocks, replies, up/down votes, edit, delete, mentor moderation, reply notifications) and a mentor-written **editorial** (with an optional AI first draft). Both unlock once you've solved the problem; you can peek earlier, but that's recorded and shown to mentors as an integrity hint if you solve it afterwards. |
| **Audit log** | Role changes, deactivations, invites (created and accepted), problem and assignment changes, and password / sign-in method changes are recorded with who, what, when and from which IP, in the same transaction as the change. Admins can filter and export it as CSV; nobody can edit it through the API. |
| **Notifications** | In-app bell for new assignments, mentor feedback and replies, badges, and "due within 24 hours" reminders. Deleting an assignment removes its notifications. |
| **Email** | The same notifications by email, plus a **weekly progress digest** (solved problems, streak, reviews due, open assignments; skipped when there's nothing to say). Per-kind opt-ins on the Account page, and one-click unsubscribe (RFC 8058 `List-Unsubscribe`) in every email. Emails are sent only after the transaction commits, in the background with retries. Reminders and digests run in the API with no cron to set up, and are safe with several API replicas (each email is claimed in the database first). Any SMTP provider works; Docker Compose ships **Mailpit**, a local inbox at http://localhost:8025. |
| **XP, levels & badges** | XP from first solves (easy 10 / medium 25 / hard 50), completed interviews and on-time spaced-repetition reviews, always recomputed from real activity so it can't be farmed by re-solving. 10 badges (first solve, polyglot, 7-day streak, all-rounder, 80+ interview score…) with notifications. |
| **Problem lists** | Curated lists (interview warm-up, arrays & hashing, frequently asked at big tech) with per-user progress; mentors can make lists for their org. Filter problems by company or search by title/topic. |
| **Quotas** | Per-tenant, per-plan limits on AI requests (monthly) and code runs (daily), metered in `usage_events`. 429 when exceeded. |

### LLM integration
`LLM_PROVIDER=anthropic` uses Claude (`claude-opus-5-5` by default). Chat turns run at low effort for latency; scorecards use
structured outputs (`messages.parse` with a Pydantic schema), as do fit reports. Interview turns enable server-side refusal
fallbacks and **prompt caching** (each turn resends the growing conversation, so the prefix is cached).
Candidate text (answers, code, resumes, job descriptions) is always sent as tagged user content, never interpolated into
system prompts, and the model is told to treat it as data.
`LLM_PROVIDER=mock` (the default) is a deterministic offline provider, so the whole app runs and tests pass without an API key.

## Run it
For a production server with HTTPS, backups and monitoring, see **[docs/DEPLOY.md](docs/DEPLOY.md)**.


### Option A — Docker Compose (everything)
```bash
cp .env.example .env      # set LLM_PROVIDER=anthropic + ANTHROPIC_API_KEY for real AI
docker compose up -d --build
```
Web: http://localhost:3080 (change with `WEB_PORT`) · API docs: http://localhost:3080/api/docs

Services: `db` (Postgres), `redis`, `backend` (runs `alembic upgrade head`, then the API), `worker` (sandbox runner), `mailpit` (local email inbox), `frontend` (nginx). Only nginx and the Mailpit inbox are published on the host.

Emails the app sends (invites, password resets, reminders, digests) land in Mailpit at http://localhost:8025 (change with `MAIL_UI_PORT`). For real delivery, set `SMTP_*` in `.env` (see `.env.example`).

### Option B — local dev
```bash
cd backend
python -m venv .venv && .venv/Scripts/activate   # Windows (Linux/macOS: source .venv/bin/activate)
pip install -r requirements-dev.txt              # runtime + test tools
set DATABASE_URL=sqlite+aiosqlite:///./dev.db    # or point at Postgres
alembic upgrade head                             # create / update the schema
uvicorn app.main:app --reload                    # SANDBOX_BACKEND=docker (default) runs sandboxes in-process
```
```bash
cd frontend && npm install && npm run dev        # http://localhost:5173, proxies /api -> :8000
```
Sandbox images are built once on first use (the worker builds them at startup). To pre-build: `python -m app.services.sandbox` (optionally followed by language names).

### Tests
```bash
cd backend && .venv/Scripts/python -m pytest -q
```
The suite runs on SQLite by default. To run it on PostgreSQL with row-level security enforced (CI does both), point it at a throwaway database; its schema is rebuilt from the migrations:
```bash
TEST_DATABASE_URL=postgresql+asyncpg://prep:prep@localhost:5432/prep_test python -m pytest -q
```
Covers auth, invites and RBAC, all five judge languages (compile errors, CPU-time and memory limits, fork bombs), **cross-tenant isolation**, hidden-test non-leakage, verdicts, interviews and scorecards,
analytics, quotas, refresh rotation and reuse detection, rate limits, SSE streaming, the queue worker, production config checks,
migrations-vs-models drift, real-Docker sandbox checks (network blocked, read-only FS, non-root, timeouts, warm containers never reused), email (preferences, unsubscribe, password reset, digests, no email on rollback) row-level security (unfiltered queries, cross-tenant writes, pooled-connection leaks), function-style drivers in all five languages (every argument type round-trips, Unicode and escapes included), too-slow verdicts on generated stress tests, the audit log, and peer rooms end to end over a real WebSocket (auth, presence, live code, chat, private notes, role swap, feedback). The Docker tests skip if no daemon is running.

CI (`.github/workflows/ci.yml`) runs the tests on SQLite and on PostgreSQL with RLS, applies migrations to a real PostgreSQL and checks for drift, builds the frontend, and builds the Docker images.

### Schema changes
```bash
cd backend
alembic revision --autogenerate -m "describe change"   # then review the generated file
alembic upgrade head
```

## Architecture
```
React (Vite, Monaco) ──▶ nginx ──/api──▶ FastAPI ──▶ PostgreSQL (row-level security; schema via Alembic)
                                           │   │ │
                                           │   │ └──▶ SMTP (Mailpit locally) - notifications, resets, digests
                                           │   └──▶ Claude API (interviewer [streamed], scorecard, review, hints)
                                           ▼
                                     Redis (job queue + rate limits)
                                           ▼
                                  arq worker ──▶ Docker daemon ──▶ warm pool ──▶ one container per run
```
```
backend/app/
  api/deps.py          JWT auth, role guards, tenant-scoped fetch helper
  api/routes/          auth, org, problems, submissions, interviews, analytics
  db/session.py        sessions + tenant context for row-level security
  services/sandbox.py  Docker executor, warm pool, queue client + grader
  services/email.py    email rendering, after-commit queue, SMTP/console backends
  services/email_jobs.py due-soon reminders + weekly digest (background loop)
  services/functions.py function-style problems: signatures, starter code, per-language drivers
  sandbox_images/testdata.py generated stress inputs + argument encoding (runs inside the sandbox)
  services/rooms.py    WebSocket rooms for peer interviews (Redis pub/sub or in-process)
  services/audit.py    audit log
  api/routes/peer.py   peer interview REST + WebSocket endpoints
  api/routes/contests.py, services/contests.py  contests and ICPC-style scoreboards
  api/routes/discussions.py  discussions, votes, editorials
  services/whiteboard.py  whiteboard validation, operations and text description
  core/observability.py  request IDs, JSON logs, Prometheus metrics, Sentry
  services/ratelimit.py Redis / in-memory rate limiter
  services/integrity.py integrity signals + code-similarity normaliser
  services/assignments.py live assignment progress
  services/csv_export.py CSV export, formula-injection safe
  worker.py            arq worker that runs sandbox jobs
  services/llm.py      Anthropic + mock providers, prompts, scorecard schema
  services/quota.py    per-plan metering
  db/models.py         ORM models
```

## Social sign-in setup

Each provider needs an app registration, which you create in your own account. Use this redirect URL, replacing the host with your `PUBLIC_URL`:

```
http://localhost:3080/api/auth/oauth/<provider>/callback      # <provider> = google | microsoft | github
```

| Provider | Where to create it | Notes |
|---|---|---|
| Google | Google Cloud Console → APIs & Services → Credentials → *Create OAuth client ID* → Web application | Add the redirect URL under *Authorized redirect URIs*. Scopes: `openid email profile`. |
| Microsoft | Azure portal → Microsoft Entra ID → App registrations → *New registration* | Platform *Web*, add the redirect URL, then create a client secret. "Accounts in any organizational directory and personal Microsoft accounts" matches `MICROSOFT_TENANT=common`. |
| GitHub | GitHub → Settings → Developer settings → OAuth Apps → *New OAuth App* | Set *Authorization callback URL* to the redirect URL. |

Then put the keys in `.env` (they are never sent to the browser) and restart: `docker compose up -d`.

```bash
PUBLIC_URL=http://localhost:3080
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

How it behaves:
- **Sign in:** works for anyone already linked, or for an existing member whose *verified* provider email matches.
- **Join with an invite:** creates the account; invites locked to an email need a verified matching email.
- **New organization:** needs a verified email.
- **Social-only accounts:** can add a password later on the Account page.

## Running on any machine

- **Requirements:** Docker (Docker Desktop on Windows/macOS) with about 3.2 GB of free disk for all five language images, and 2 GB of RAM for Docker. Each service has a memory cap in `docker-compose.yml`, so the stack itself stays well under that.
- **Choosing languages:** each language's sandbox image is built once (about 100 MB for Python and 300-600 MB each for JavaScript, C++, Go and Java). To save disk, enable only what you need in `.env`, e.g. `SANDBOX_LANGUAGES=["python","javascript"]`. Old image versions are removed automatically when the runner changes.
- **First start:** the worker builds missing sandbox images (a few minutes, once). Follow it with `docker compose logs -f worker`.
- **Warm pool:** one idle container per enabled language, about 3 MB of RAM each. Set `SANDBOX_WARM_POOL=0` to turn it off, or raise it on a busy server. Idle containers exit on their own after an hour if a worker dies without cleaning up.
- **Windows:** keep the project outside OneDrive (or exclude `node_modules` and `.venv` from sync). Syncing tens of thousands of small files slows builds and Docker file sharing considerably. The repo enforces LF line endings (`.gitattributes`), so files copied into Linux containers behave the same as on Linux and macOS.
- **Freeing space:** `docker builder prune` removes build cache. `docker compose down` stops the stack and keeps your data; add `-v` to delete the database as well.

## Operations
- **Health:** `/api/health` (liveness) and `/api/ready` (database and Redis; 503 when not ready). Docker Compose uses readiness as the API's health check.
- **Logs:** every request gets an ID (returned as `X-Request-ID` and included in each log line). `LOG_FORMAT=json` writes one JSON object per line for log platforms.
- **Metrics:** `/api/metrics` for Prometheus: request rate, latency and status per route, judged runs by language and verdict, sandbox latency, emails, open WebSockets. Reachable only inside the Docker network (or with `METRICS_TOKEN`).
- **Errors:** set `SENTRY_DSN` for Sentry (bodies, cookies, tokens and emails are stripped).
- **Backups:** `./scripts/backup.sh` and `./scripts/restore.sh` (tested end to end, including the row-level security policies and grants).
- **Production:** `docker-compose.prod.yml` adds Caddy with automatic HTTPS and strict settings. Step-by-step in [docs/DEPLOY.md](docs/DEPLOY.md).

## Authoring problems
Mentors create org-private problems on the Problems page, in either style:
- **stdin/stdout:** test input is the program's stdin; expected output is compared line by line.
- **Function:** pick a function name, typed parameters and a return type. Each test's input is one JSON value per line (one per parameter, e.g. `[2,7,11,15]` then `9`) and the expected output is the JSON return value.

For a complexity check, start a hidden test's input with `@gen` and a generator spec, e.g.
`@gen {"lines": [{"ints": 200000, "min": -1000000, "max": 1000000, "seed": 1}]}` (add `"json": true` for function-style problems). Other parts: `{"chars": n, "alphabet": "...", "seed": s}`, `{"value": ...}` for a literal line, and `"distinct"`, `"mod"`/`"residue"` or `"insert": [[index, value]]` to shape the numbers. The full reference is in `backend/app/sandbox_images/testdata.py`.

## Roadmap (next features)
- **Billing:** Stripe plans wired to quotas (after the app is complete).
- **SAML / SCIM** for enterprise tenants.
- **Kubernetes manifests** (Helm chart) for multi-node installs.
- **Problem import/export** (share problem sets between organizations).
