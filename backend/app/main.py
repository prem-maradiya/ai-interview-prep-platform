from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import analytics, auth, interviews, org, problems, submissions
from app.core.config import get_settings
from app.db import models  # noqa: F401  (register tables)
from app.db.session import Base, SessionLocal, engine
from app.seed import seed_global_problems


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience: create tables on startup. Use Alembic migrations for production.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    if get_settings().seed_demo_data:
        async with SessionLocal() as db:
            await seed_global_problems(db)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=f"{settings.app_name} API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )
    for r in (auth.router, org.router, problems.router, submissions.router, interviews.router, analytics.router):
        app.include_router(r, prefix="/api")

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "llm_provider": settings.llm_provider}

    return app


app = create_app()
