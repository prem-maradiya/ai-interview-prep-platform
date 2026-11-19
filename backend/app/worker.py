"""Sandbox worker. Run with:  arq app.worker.WorkerSettings

Only this process needs access to the Docker daemon. Deploy it on separate hosts from the API
(ideally with SANDBOX_RUNTIME=runsc for gVisor) and scale it independently.
"""
from dataclasses import asdict

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.services.sandbox import DockerSandbox

settings = get_settings()


async def run_code(ctx: dict, language: str, code: str, inputs: list[str]) -> list[dict]:
    raw = await ctx["sandbox"].execute(language, code, inputs)
    return [asdict(r) for r in raw]


async def startup(ctx: dict) -> None:
    sandbox = DockerSandbox()
    sandbox.check_runtime()
    ctx["sandbox"] = sandbox


class WorkerSettings:
    functions = [run_code]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(settings.redis_url or "redis://localhost:6379")
    max_jobs = settings.sandbox_max_concurrency
    job_timeout = int(settings.sandbox_total_timeout_s) + 30
    keep_result = 120
    poll_delay = 0.05  # seconds between queue checks (default 0.5s adds visible latency to every run)
    max_tries = 1  # never re-run untrusted code automatically
