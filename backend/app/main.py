import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.api.routes import analytics, auth, documents, interviews, org, practice, problems, submissions
from app.core.config import get_settings
from app.db import models  # noqa: F401  (register tables)
from app.db.session import SessionLocal
from app.seed import seed_global_problems

log = logging.getLogger("prepforge")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is managed by Alembic: run `alembic upgrade head` before starting the API.
    if get_settings().seed_demo_data:
        try:
            async with SessionLocal() as db:
                await seed_global_problems(db)
        except (OperationalError, ProgrammingError) as e:
            raise RuntimeError("Database schema missing - run `alembic upgrade head` first") from e
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    settings.validate_for_production()
    app = FastAPI(title=f"{settings.app_name} API", version="0.2.0", lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )
    for r in (auth.router, org.router, problems.router, submissions.router, interviews.router, analytics.router,
              documents.router, practice.router):
        app.include_router(r, prefix="/api")

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "llm_provider": settings.llm_provider, "sandbox_backend": settings.sandbox_backend}

    return app


app = create_app()
