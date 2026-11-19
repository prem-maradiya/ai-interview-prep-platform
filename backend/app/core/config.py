from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "PrepForge"
    database_url: str = "postgresql+asyncpg://prep:prep@localhost:5432/prep"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # JWT
    jwt_secret: str = "change-me-in-production-please-32b"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 7

    # LLM: "anthropic" uses the Claude API, "mock" is deterministic and needs no key
    llm_provider: str = "mock"
    llm_model: str = "claude-opus-5-5"

    # Sandbox
    sandbox_enabled: bool = True
    sandbox_memory: str = "256m"
    sandbox_cpus: float = 0.5
    sandbox_pids: int = 64
    sandbox_test_timeout_s: float = 2.0
    sandbox_total_timeout_s: float = 20.0
    sandbox_runtime: str | None = None  # e.g. "runsc" for gVisor
    sandbox_max_concurrency: int = 4

    seed_demo_data: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
