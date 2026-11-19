"""Sandbox worker. Run with:  arq app.worker.WorkerSettings

Only this process needs access to the Docker daemon. Deploy it on separate hosts from the API
(ideally with SANDBOX_RUNTIME=runsc for gVisor) and scale it independently.
"""
import logging
from dataclasses import asdict

import anyio
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.services.sandbox import DockerSandbox, enabled_languages

settings = get_settings()
log = logging.getLogger("prepforge.worker")

# arq only configures its own logger; give ours a handler so image builds are visible in `docker compose logs`.
_root = logging.getLogger("prepforge")
if not _root.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s: %(message)s", "%H:%M:%S"))
    _root.addHandler(_handler)
    _root.setLevel(logging.INFO)


async def run_code(ctx: dict, language: str, code: str, inputs: list[str]) -> list[dict]:
    raw = await ctx["sandbox"].execute(language, code, inputs)
    return [asdict(r) for r in raw]


async def startup(ctx: dict) -> None:
    sandbox = DockerSandbox()
    sandbox.check_runtime()
    if settings.sandbox_prebuild_images:
        # Build (once) the images for every enabled language, so the first submission isn't slow.
        for tag in await anyio.to_thread.run_sync(sandbox.ensure_images):
            log.info("sandbox image ready: %s", tag)
    log.info("languages enabled: %s", ", ".join(enabled_languages()))
    ctx["sandbox"] = sandbox


class WorkerSettings:
    functions = [run_code]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(settings.redis_url or "redis://localhost:6379")
    max_jobs = settings.sandbox_max_concurrency
    # Compile + up to ~50 tests at the per-test wall limit; the container has its own hard cap too.
    job_timeout = int(settings.sandbox_compile_wall_s + 50 * settings.sandbox_wall_limit_s)
    keep_result = 120
    poll_delay = 0.05  # seconds between queue checks (default 0.5s adds visible latency to every run)
    max_tries = 1  # never re-run untrusted code automatically
