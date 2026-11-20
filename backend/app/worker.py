"""Sandbox worker. Run with:  arq app.worker.WorkerSettings

Only this process needs access to the Docker daemon. Deploy it on separate hosts from the API
(ideally with SANDBOX_RUNTIME=runsc for gVisor) and scale it independently.
"""
import logging
from dataclasses import asdict

import anyio
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.logs import setup_logging
from app.services.sandbox import DockerSandbox, enabled_languages

settings = get_settings()
log = logging.getLogger("prepforge.worker")

setup_logging()  # arq only configures its own logger; show ours in `docker compose logs`


async def run_code(ctx: dict, language: str, code: str, inputs: list[str],
                   extra_files: dict[str, str] | None = None, args: list[str] | None = None) -> list[dict]:
    raw = await ctx["sandbox"].execute(language, code, inputs, extra_files, args)
    return [asdict(r) for r in raw]


async def startup(ctx: dict) -> None:
    sandbox = DockerSandbox()
    sandbox.check_runtime()
    if settings.sandbox_prebuild_images:
        # Build (once) the images for every enabled language, so the first submission isn't slow.
        for tag in await anyio.to_thread.run_sync(sandbox.ensure_images):
            log.info("sandbox image ready: %s", tag)
    log.info("languages enabled: %s", ", ".join(enabled_languages()))
    if sandbox.pool:
        await anyio.to_thread.run_sync(sandbox.warm_up)
        log.info("warm pool ready: %d idle container(s) per language", sandbox.pool.size)
    ctx["sandbox"] = sandbox


async def shutdown(ctx: dict) -> None:
    if "sandbox" in ctx:
        await anyio.to_thread.run_sync(ctx["sandbox"].close)


class WorkerSettings:
    functions = [run_code]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url or "redis://localhost:6379")
    max_jobs = settings.sandbox_max_concurrency
    # Compile + up to ~50 tests at the per-test wall limit; the container has its own hard cap too.
    job_timeout = int(settings.sandbox_compile_wall_s + 50 * settings.sandbox_wall_limit_s)
    keep_result = 120
    poll_delay = 0.05  # seconds between queue checks (default 0.5s adds visible latency to every run)
    max_tries = 1  # never re-run untrusted code automatically
