import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.api.routes import (
    analytics, assignments, auth, documents, engagement, interviews, notifications, oauth, org, practice, problems,
    review, submissions,
)
from app.core.config import get_settings
from app.core.logs import setup_logging
from app.db import models  # noqa: F401  (register tables)
from app.db.session import configure_rls, system_session
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


def create_app() -> FastAPI:
    settings = get_settings()
    settings.validate_for_production()
    app = FastAPI(title=f"{settings.app_name} API", version="0.2.0", lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )
    for r in (auth.router, oauth.router, org.router, problems.router, submissions.router, interviews.router, analytics.router,
              documents.router, practice.router, assignments.router, review.router, notifications.router,
              engagement.router):
        app.include_router(r, prefix="/api")

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "llm_provider": settings.llm_provider, "sandbox_backend": settings.sandbox_backend}

    return app


app = create_app()
