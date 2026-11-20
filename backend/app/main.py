import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.api.routes import (
    analytics, assignments, auth, contests, discussions, documents, engagement, interviews, notifications, oauth, org,
    peer, practice, problems, review, submissions,
)
from app.core.config import get_settings
from app.core.logs import setup_logging
from app.core.observability import RequestContextMiddleware, configure_sentry, metrics_allowed, metrics_payload
from app.db import models  # noqa: F401  (register tables)
from app.db.session import configure_rls, engine, system_session
from app.seed import seed_global_problems
from app.services.email import wait_for_outgoing
from app.services.email_jobs import email_jobs_loop

setup_logging()
log = logging.getLogger("prepforge")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is managed by Alembic: run `alembic upgrade head` before starting the API.
    log.info("row-level security: %s", await configure_rls())
    if get_settings().seed_demo_data:
        try:
            async with system_session() as db:
                await seed_global_problems(db)
        except (OperationalError, ProgrammingError) as e:
            raise RuntimeError("Database schema missing - run `alembic upgrade head` first") from e
    settings = get_settings()
    log.info("email: %s backend", settings.email_backend)
    jobs = None
    if settings.email_jobs_enabled and settings.email_backend != "none":
        jobs = asyncio.create_task(email_jobs_loop())
    yield
    if jobs:
        jobs.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await jobs
    await asyncio.wait_for(wait_for_outgoing(), timeout=20)


async def readiness() -> dict[str, str]:
    """Can this instance serve traffic? Checks the database and (if configured) Redis."""
    checks: dict[str, str] = {}
    try:
        async with engine.connect() as conn:
            await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=3)
        checks["database"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["database"] = f"error: {type(e).__name__}"
    url = get_settings().redis_url
    if url:
        import redis.asyncio as redis

        try:
            client = redis.from_url(url)
            await asyncio.wait_for(client.ping(), timeout=3)
            await client.aclose()
            checks["redis"] = "ok"
        except Exception as e:  # noqa: BLE001
            checks["redis"] = f"error: {type(e).__name__}"
    return checks


def create_app() -> FastAPI:
    settings = get_settings()
    settings.validate_for_production()
    if configure_sentry():
        log.info("error tracking: Sentry enabled")
    app = FastAPI(title=f"{settings.app_name} API", version=settings.app_version, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )
    for r in (auth.router, oauth.router, org.router, problems.router, submissions.router, interviews.router, analytics.router,
              documents.router, practice.router, assignments.router, review.router, notifications.router,
              engagement.router, peer.router, contests.router, discussions.router):
        app.include_router(r, prefix="/api")

    @app.get("/api/health")
    async def health():
        """Liveness: the process is up (doesn't touch dependencies, so it never restarts a healthy API)."""
        return {"status": "ok", "version": settings.app_version, "llm_provider": settings.llm_provider,
                "sandbox_backend": settings.sandbox_backend}

    @app.get("/api/ready")
    async def ready():
        """Readiness: database and Redis reachable. 503 tells a load balancer to stop sending traffic."""
        checks = await readiness()
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse({"status": "ready" if ok else "unavailable", "checks": checks}, status_code=200 if ok else 503)

    @app.get("/api/metrics", include_in_schema=False)
    async def metrics(request: Request):
        if not metrics_allowed(request.client.host if request.client else None, request.headers.get("authorization")):
            return Response(status_code=404)
        body, content_type = metrics_payload()
        return Response(body, media_type=content_type)

    # Outermost, so request IDs cover everything (including CORS preflights and errors).
    app.add_middleware(RequestContextMiddleware, fastapi_app=app)
    return app


app = create_app()
