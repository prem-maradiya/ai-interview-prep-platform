# Deploying PrepForge

This guide puts PrepForge on a single Linux server with HTTPS. It's a good fit for a bootcamp, a
university course or a company team: one VM with **4 GB of RAM and 2 vCPUs** comfortably runs
the whole stack. For larger installs, see [Scaling out](#scaling-out).

## 1. Server and DNS
1. Create a Linux VM (Ubuntu 24.04 or similar) and open ports **80** and **443**.
2. Point a DNS record (e.g. `prep.example.com`) at its public IP.
3. Install Docker: `curl -fsSL https://get.docker.com | sh`.

## 2. Configure
```bash
git clone https://github.com/prem-maradiya/ai-interview-prep-platform.git /opt/prepforge
cd /opt/prepforge
cp .env.example .env
```
Set at least these in `.env`:

| Variable | What |
|---|---|
| `DOMAIN` | e.g. `prep.example.com` (Caddy gets a Let's Encrypt certificate for it) |
| `JWT_SECRET` | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `POSTGRES_PASSWORD` | another random value |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM` | your email provider (SES, Postmark, SendGrid, Mailgun...) |
| `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY` | real AI interviews (leave `mock` to try it without) |
| `GOOGLE_CLIENT_ID`... | optional social sign-in; use `https://<DOMAIN>/api/auth/oauth/<provider>/callback` as the redirect URI |
| `METRICS_TOKEN`, `SENTRY_DSN` | optional monitoring (below) |

`POSTGRES_PASSWORD` is applied when the database is first created. To change it later, change it
in Postgres too (`ALTER USER prep PASSWORD '...'`).

## 3. Start
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose logs -f worker     # first start builds the sandbox images (a few minutes, once)
```
Open `https://<DOMAIN>` and create your organization. In production mode the API refuses to start
with a weak `JWT_SECRET`, without the job queue, or without an SMTP server, so a misconfiguration
fails loudly instead of running insecurely.

What runs: Caddy (HTTPS, the only public service), the static frontend, the API, the sandbox
worker, PostgreSQL and Redis. Only the worker can reach the Docker socket.

## 4. Backups
```bash
./scripts/backup.sh                                   # backups/prepforge-<time>.sql.gz, keeps 14 days
crontab -e                                            # daily at 02:00:
0 2 * * * cd /opt/prepforge && ./scripts/backup.sh >> backups/backup.log 2>&1
```
Copy backups off the server as well (e.g. `rclone copy backups remote:prepforge`). To restore:
```bash
./scripts/restore.sh backups/prepforge-20261120T020000Z.sql.gz   # asks before replacing data
```
Practise a restore once, on a test server, before you need it.

## 5. Monitoring
- **Health:** `GET /api/health` (process up) and `GET /api/ready` (database and Redis reachable;
  503 otherwise). Docker restarts the API if readiness keeps failing.
- **Logs:** production logs are JSON, one object per line, each with a `request_id`. The same ID is
  returned in the `X-Request-ID` response header, so a user's report leads to the exact log lines:
  `docker compose logs backend | grep <request id>`.
- **Metrics:** `GET /api/metrics` (Prometheus format): request rates, latency and status by route,
  judged runs by language and verdict, sandbox latency, emails sent and failed, open WebSockets.
  Caddy never exposes it publicly. Scrape it from inside the Docker network:
  ```yaml
  scrape_configs:
    - job_name: prepforge
      metrics_path: /api/metrics
      authorization: { credentials: "<METRICS_TOKEN>" }
      static_configs: [{ targets: ["backend:8000"] }]
  ```
- **Errors:** set `SENTRY_DSN` to send exceptions to Sentry. Request bodies, cookies, tokens and
  emails are stripped before sending.

## 6. Upgrades
```bash
cd /opt/prepforge && ./scripts/backup.sh && git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```
Database migrations run automatically when the API starts. Sandbox images rebuild only if the
runner changed.

## Hardening the sandbox
Candidate code already runs with no network, a read-only filesystem, no capabilities, CPU, memory
and process limits, as an unprivileged user, in a fresh container per run. For stronger
isolation, install [gVisor](https://gvisor.dev/docs/user_guide/install/) on the host and set
`SANDBOX_RUNTIME=runsc`. The worker refuses to start if the runtime is configured but missing.

## Scaling out
- **API:** stateless; run several replicas behind the proxy. Rate limits, the job queue and
  peer-interview rooms already go through Redis, and email jobs are safe on many replicas.
- **Worker:** run it on separate hosts (it needs the Docker socket), ideally with gVisor, and raise
  `SANDBOX_MAX_CONCURRENCY` / `SANDBOX_WARM_POOL` to match each host's CPUs.
- **Database:** move to a managed PostgreSQL. If its admin user can't create roles, create a
  non-superuser for the app and set `DB_APP_ROLE=` (empty) so row-level security still applies.
