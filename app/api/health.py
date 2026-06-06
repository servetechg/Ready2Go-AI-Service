"""Liveness, readiness, metrics, and diagnostics endpoints (unauthenticated except diagnostics)."""

from __future__ import annotations

import time
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query

from app.config import get_settings
from app.security import require_auth

router = APIRouter(tags=["health"])

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# In-process metrics counters (lightweight; replaced by Prometheus client if desired)
# ---------------------------------------------------------------------------
_metrics: dict[str, Any] = {
    "requests_total": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "pipeline_errors": 0,
    "pipeline_timeouts": 0,
    "tokens_total": 0,
    "latency_ms_sum": 0.0,
    "latency_ms_count": 0,
}


def record_request(*, cache_hit: bool, error: bool = False, timeout: bool = False,
                   tokens: int = 0, latency_ms: float = 0.0) -> None:
    """Increment in-process metrics counters (called from the pipeline)."""
    _metrics["requests_total"] += 1
    if cache_hit:
        _metrics["cache_hits"] += 1
    else:
        _metrics["cache_misses"] += 1
    if error:
        _metrics["pipeline_errors"] += 1
    if timeout:
        _metrics["pipeline_timeouts"] += 1
    _metrics["tokens_total"] += tokens
    _metrics["latency_ms_sum"] += latency_ms
    _metrics["latency_ms_count"] += 1


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. No dependency checks."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> dict[str, object]:
    """Readiness: live connectivity probes for each downstream dependency.

    Returns per-dependency status so orchestrators don't route traffic to a
    half-broken instance.  Each probe is attempted independently — one failure
    doesn't hide others.
    """
    settings = get_settings()
    deps: dict[str, object] = {}
    overall_ok = True

    # ── Weaviate ──────────────────────────────────────────────────────────
    if settings.weaviate_url:
        t0 = time.perf_counter()
        try:
            from app.vectors.client import get_client
            client = get_client()
            ready = client.is_ready()
            deps["weaviate"] = {
                "ok": ready,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }
            if not ready:
                overall_ok = False
        except Exception as exc:
            log.warning(
                "readyz.weaviate_probe_failed",
                detail=(
                    "Weaviate readiness probe failed; marking the service 'degraded' "
                    "so orchestrators avoid routing vector traffic here. Cause below."
                ),
                error=str(exc),
            )
            deps["weaviate"] = {"ok": False, "error": str(exc)}
            overall_ok = False
    else:
        deps["weaviate"] = {"ok": False, "error": "WEAVIATE_URL not configured"}
        overall_ok = False

    # ── MongoDB ───────────────────────────────────────────────────────────
    if settings.mongodb_uri:
        t0 = time.perf_counter()
        try:
            from app.store.models import _client as mongo_client  # type: ignore[attr-defined]
            from app.store.models import _db
            _db()
            if mongo_client is not None:
                mongo_client.admin.command("ping")
            deps["mongodb"] = {
                "ok": True,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        except Exception as exc:
            log.warning(
                "readyz.mongodb_probe_failed",
                detail=(
                    "MongoDB readiness probe failed; marking the service 'degraded'. "
                    "The dedup cache and audit state depend on Mongo. Cause below."
                ),
                error=str(exc),
            )
            deps["mongodb"] = {"ok": False, "error": str(exc)}
            overall_ok = False
    else:
        deps["mongodb"] = {"ok": False, "error": "MONGODB_URI not configured"}
        overall_ok = False

    # ── OpenAI ────────────────────────────────────────────────────────────
    # We only check that the key is present (a live API call would be too expensive here).
    openai_ok = bool(settings.openai_api_key)
    deps["openai"] = {"ok": openai_ok, "configured": openai_ok}
    if not settings.openai_api_key:
        overall_ok = False

    return {
        "status": "ok" if overall_ok else "degraded",
        "env": settings.env,
        "modelVersion": settings.model_version,
        "dependencies": deps,
    }


@router.get("/metrics")
async def metrics() -> dict[str, object]:
    """Prometheus-style counters (plain JSON for easy scraping or curl inspection).

    Tracks: request count, cache-hit ratio, token spend, error rate, avg latency.
    """
    total = _metrics["requests_total"]
    cache_hit_ratio = (
        round(_metrics["cache_hits"] / total, 4) if total else 0.0
    )
    avg_latency = (
        round(_metrics["latency_ms_sum"] / _metrics["latency_ms_count"], 1)
        if _metrics["latency_ms_count"] else 0.0
    )
    return {
        "requests_total": total,
        "cache_hits": _metrics["cache_hits"],
        "cache_misses": _metrics["cache_misses"],
        "cache_hit_ratio": cache_hit_ratio,
        "pipeline_errors": _metrics["pipeline_errors"],
        "pipeline_timeouts": _metrics["pipeline_timeouts"],
        "tokens_total": _metrics["tokens_total"],
        "avg_latency_ms": avg_latency,
    }


@router.get("/v1/diagnostics/calls", dependencies=[Depends(require_auth)])
async def diagnostics_calls(
    attachment_id: str | None = Query(default=None, alias="attachmentId"),
    limit: int = Query(default=50, le=200),
) -> dict[str, object]:
    """Return recent AI call log entries for cost/error diagnostics.

    Auth-gated. Filter by attachmentId to trace one document's AI spend.
    """
    from starlette.concurrency import run_in_threadpool

    from app.store.models import get_log_col

    def _query() -> list[dict[str, object]]:
        filt: dict[str, object] = {}
        if attachment_id:
            filt["attachmentId"] = attachment_id
        cursor = (
            get_log_col()
            .find(filt, {"_id": 0})
            .sort("ts", -1)
            .limit(limit)
        )
        rows = []
        for doc in cursor:
            # Serialize datetime to ISO string for JSON.
            if "ts" in doc and hasattr(doc["ts"], "isoformat"):
                doc["ts"] = doc["ts"].isoformat()
            rows.append(doc)
        return rows

    rows = await run_in_threadpool(_query)
    return {"count": len(rows), "calls": rows}
