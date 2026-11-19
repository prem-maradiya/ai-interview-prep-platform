from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "PrepForge"
    environment: str = "development"  # "production" enables strict startup checks
    database_url: str = "postgresql+asyncpg://prep:prep@localhost:5432/prep"
    # Redis backs the sandbox job queue and shared rate limits. Unset = in-process fallbacks (dev/tests).
    redis_url: str | None = None
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # JWT
    jwt_secret: str = "change-me-in-production-please-32b"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7

    # LLM: "anthropic" uses the Claude API, "mock" is deterministic and needs no key
    llm_provider: str = "mock"
    llm_model: str = "claude-opus-5-5"

    # Sandbox: "docker" runs containers in this process; "queue" sends jobs to the arq worker via Redis
    sandbox_backend: str = "docker"
    sandbox_memory: str = "256m"
    sandbox_cpus: float = 0.5
    sandbox_pids: int = 64
    sandbox_test_timeout_s: float = 2.0
    sandbox_total_timeout_s: float = 20.0
    sandbox_runtime: str | None = None  # e.g. "runsc" for gVisor
    sandbox_max_concurrency: int = 4

    seed_demo_data: bool = True

    # Rate limits: (max requests, window seconds) per client IP (+ account where relevant)
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
        if problems:
            raise RuntimeError("Refusing to start in production: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
