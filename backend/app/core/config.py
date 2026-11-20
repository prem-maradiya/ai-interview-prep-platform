from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "PrepForge"
    environment: str = "development"  # "production" enables strict startup checks
    database_url: str = "postgresql+asyncpg://prep:prep@localhost:5432/prep"
    # Non-superuser role that transactions switch to, so PostgreSQL row-level security applies even
    # when DATABASE_URL connects as a superuser (as the docker-compose database does). Created by
    # migration 0008. Empty = connect as a non-superuser role yourself.
    db_app_role: str = "prepforge_app"
    # Redis backs the sandbox job queue and shared rate limits. Unset = in-process fallbacks (dev/tests).
    redis_url: str | None = None
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # JWT
    jwt_secret: str = "change-me-in-production-please-32b"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7

    # Public URL of the web app (what users type in the browser). OAuth redirect URIs are built from it:
    #   {public_url}/api/auth/oauth/{provider}/callback
    public_url: str = "http://localhost:3080"

    # Social sign-in. A provider's button appears only when both its id and secret are set.
    google_client_id: str | None = None
    google_client_secret: str | None = None
    microsoft_client_id: str | None = None
    microsoft_client_secret: str | None = None
    microsoft_tenant: str = "common"  # "common" = personal + work/school accounts
    github_client_id: str | None = None
    github_client_secret: str | None = None

    # LLM: "anthropic" uses the Claude API, "mock" is deterministic and needs no key
    llm_provider: str = "mock"
    llm_model: str = "claude-opus-5-5"

    # Sandbox: "docker" runs containers in this process; "queue" sends jobs to the arq worker via Redis
    sandbox_backend: str = "docker"
    # Languages offered (each needs a ~60-650 MB image, built once on first use / worker start).
    # Trim this to save disk, e.g. SANDBOX_LANGUAGES='["python","javascript"]'.
    sandbox_languages: list[str] = ["python", "javascript", "java", "cpp", "go"]
    sandbox_cpus: float = 1.0
    sandbox_cpu_limit_s: float = 2.0  # per test, CPU time (Java gets x2 for JVM startup)
    sandbox_wall_limit_s: float = 10.0  # per test, catches programs that sleep or block
    sandbox_compile_cpu_s: float = 15.0
    sandbox_compile_wall_s: float = 45.0
    sandbox_runtime: str | None = None  # e.g. "runsc" for gVisor
    sandbox_max_concurrency: int = 4
    sandbox_prebuild_images: bool = True  # worker builds missing images at startup
    # Idle, pre-started containers kept ready per language, so a run skips container start-up
    # (~0.5-1 s). Each is used for exactly one run, then destroyed. 0 disables. An idle
    # container uses ~1-10 MB of RAM.
    sandbox_warm_pool: int = 1

    # Email: "console" logs messages (default, nothing to set up), "smtp" sends them, "none" disables.
    # docker-compose points SMTP at Mailpit, a local inbox you can open at http://localhost:8025.
    email_backend: str = "console"
    email_from: str = "PrepForge <no-reply@prepforge.local>"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_security: str = "starttls"  # starttls | ssl | none
    smtp_timeout_s: float = 15.0
    # Background job in the API that sends "due within 24 hours" reminders and weekly digests.
    # Safe with several API replicas: every email is claimed in the database before it is sent.
    email_jobs_enabled: bool = True
    email_jobs_interval_s: int = 600
    password_reset_minutes: int = 30

    seed_demo_data: bool = True

    # Rate limits: (max requests, window seconds) per client IP (+ account where relevant)
    rl_password_reset: tuple[int, int] = (5, 900)
    rl_login: tuple[int, int] = (10, 300)
    rl_register: tuple[int, int] = (5, 3600)
    rl_invite_accept: tuple[int, int] = (20, 3600)
    rl_refresh: tuple[int, int] = (60, 60)

    def validate_for_production(self) -> None:
        if self.environment != "production":
            return
        problems = []
        if len(self.jwt_secret) < 32 or "change-me" in self.jwt_secret or "dev-only" in self.jwt_secret:
            problems.append("JWT_SECRET must be a random value of at least 32 characters")
        if self.sandbox_backend != "queue":
            problems.append("SANDBOX_BACKEND must be 'queue' so the API never needs the Docker socket")
        if not self.redis_url:
            problems.append("REDIS_URL is required")
        if self.email_backend == "smtp" and not self.smtp_host:
            problems.append("SMTP_HOST is required when EMAIL_BACKEND=smtp")
        if problems:
            raise RuntimeError("Refusing to start in production: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
