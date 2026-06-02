"""Liveness and readiness endpoints (unauthenticated)."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. No dependency checks."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> dict[str, object]:
    """Readiness: report whether each downstream dependency is configured.

    In M0 we only report configuration presence (no live pings yet) so the
    endpoint never crashes before the integrations land. Later steps replace the
    "configured" booleans with real connectivity probes.
    """
    settings = get_settings()
    dependencies = {
        "openai": bool(settings.openai_api_key),
        "weaviate": bool(settings.weaviate_url),
        "postgres": bool(settings.database_url),
    }
    return {
        "status": "ok",
        "env": settings.env,
        "modelVersion": settings.model_version,
        "dependencies": dependencies,
    }
