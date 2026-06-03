"""FastAPI application entrypoint.

`run()` is the console-script target wired in pyproject.toml, so the service
boots with `uv run main`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api import audit, health, integrity
from app.config import get_settings
from app.logging import configure_logging

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.is_production)
    log.info(
        "service.startup",
        env=settings.env,
        model_version=settings.model_version,
        embed_model=settings.openai_embed_model,
    )
    # Future: warm up lazy clients (Weaviate / MongoDB / OpenAI) here.
    yield
    log.info("service.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Ready2Go AI Service",
        version="0.1.0",
        summary="Vector-backed continuity integrity scoring + bounded audit summaries.",
        lifespan=lifespan,
    )
    # Health is unauthenticated; the /v1 routers carry their own auth dependency.
    app.include_router(health.router)
    app.include_router(integrity.router, prefix="/v1")
    app.include_router(audit.router, prefix="/v1")
    return app


app = create_app()


def run() -> None:
    """Entrypoint for `uv run main`."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
