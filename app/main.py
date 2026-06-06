"""FastAPI application entrypoint.

`run()` is the console-script target wired in pyproject.toml, so the service
boots with `uv run main`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from app.api import audit, health, integrity
from app.config import get_settings, validate_production_secrets
from app.logging import configure_logging
from app.middleware import CorrelationMiddleware

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(
        settings.log_level,
        json_logs=settings.is_production,
        log_dir=settings.log_dir,
        log_max_bytes=settings.log_max_bytes,
        log_backups=settings.log_backups,
    )

    # Fail hard in production if required secrets are absent.
    validate_production_secrets(settings)

    log.info(
        "service.startup",
        env=settings.env,
        model_version=settings.model_version,
        embed_model=settings.openai_embed_model,
    )

    # Bootstrap Weaviate collections (idempotent, safe on every start).
    if settings.weaviate_url:
        try:
            from app.vectors.client import ensure_collections
            await run_in_threadpool(ensure_collections)
            log.info("startup.weaviate_collections_ready")
        except Exception as exc:
            log.error(
                "startup.weaviate_bootstrap_failed",
                detail=(
                    "Could not create/verify Weaviate collections at startup. The "
                    "service still boots, but vector-dependent features (content, "
                    "name, duplication signals) will be degraded until Weaviate is "
                    "reachable. Cause below."
                ),
                error=str(exc),
            )
            # Non-fatal: service can still handle requests that don't use vectors.

    # Bootstrap MongoDB indexes (idempotent, safe on every start).
    if settings.mongodb_uri:
        try:
            from app.store.models import ensure_indexes
            await run_in_threadpool(ensure_indexes)
            log.info("startup.mongo_indexes_ready")
        except Exception as exc:
            log.error(
                "startup.mongo_bootstrap_failed",
                detail=(
                    "Could not create/verify MongoDB indexes at startup. The service "
                    "still boots, but the dedup cache and audit-state reads/writes may "
                    "be slow or fail until Mongo is reachable. Cause below."
                ),
                error=str(exc),
            )
            # Non-fatal: service degrades gracefully without Mongo.

    yield

    # ---------------------------------------------------------------------------
    # Graceful shutdown — close external clients so sockets drain cleanly.
    # ---------------------------------------------------------------------------
    log.info("service.shutdown")
    try:
        from app.vectors.client import close_client
        close_client()
    except Exception as exc:
        log.debug(
            "shutdown.weaviate_close_failed",
            detail="Closing the Weaviate client during shutdown raised; ignoring.",
            error=str(exc),
        )

    try:
        from app.store.models import _client as mongo_client  # type: ignore[attr-defined]
        if mongo_client is not None:
            mongo_client.close()
            log.info("mongo.disconnected")
    except Exception as exc:
        log.debug(
            "shutdown.mongo_close_failed",
            detail="Closing the MongoDB client during shutdown raised; ignoring.",
            error=str(exc),
        )


def create_app() -> FastAPI:
    app = FastAPI(
        title="Ready2Go AI Service",
        version="0.1.0",
        summary="Vector-backed continuity integrity scoring + bounded audit summaries.",
        lifespan=lifespan,
    )

    # Correlation IDs on every request — must be first so all routes see the ID.
    app.add_middleware(CorrelationMiddleware)

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
