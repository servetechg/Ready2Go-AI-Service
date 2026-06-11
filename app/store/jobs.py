"""Async analyze job tracking — backs the /analyze (202) + /result polling flow.

One document per attachment (``_id = attachmentId``) records where its analysis
is: ``processing`` while the background pipeline runs, then ``done`` (with the
full AnalyzeResponse payload) or ``error`` (with an explanatory detail). Next.js
polls ``GET /v1/integrity/result/{attachmentId}`` until the state settles.

Document shape:
  {
    _id:          attachmentId,
    tenantKey:    str,
    modelVersion: str,
    state:        "processing" | "done" | "error",
    result:       <AnalyzeResponse dict> | None,
    detail:       <str> | None,            # error explanation when state == error
    startedAt:    datetime,
    updatedAt:    datetime,
  }
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.store.models import get_jobs_col


def start(attachment_id: str, *, tenant_key: str, model_version: str) -> None:
    """Mark an attachment as freshly queued/processing (overwrites any prior run)."""
    now = datetime.now(UTC)
    get_jobs_col().update_one(
        {"_id": attachment_id},
        {
            "$set": {
                "tenantKey":    tenant_key,
                "modelVersion": model_version,
                "state":        "processing",
                "result":       None,
                "detail":       None,
                "startedAt":    now,
                "updatedAt":    now,
            }
        },
        upsert=True,
    )


def complete(attachment_id: str, result: dict[str, Any]) -> None:
    """Store the finished AnalyzeResponse payload and flip state -> done."""
    get_jobs_col().update_one(
        {"_id": attachment_id},
        {"$set": {
            "state":     "done",
            "result":    result,
            "detail":    None,
            "updatedAt": datetime.now(UTC),
        }},
        upsert=True,
    )


def fail(attachment_id: str, detail: str) -> None:
    """Flip state -> error with an explanatory detail (no result payload)."""
    get_jobs_col().update_one(
        {"_id": attachment_id},
        {"$set": {
            "state":     "error",
            "result":    None,
            "detail":    detail,
            "updatedAt": datetime.now(UTC),
        }},
        upsert=True,
    )


def get(attachment_id: str) -> dict[str, Any] | None:
    """Return the job document for *attachment_id*, or None if never submitted."""
    return get_jobs_col().find_one({"_id": attachment_id})


def remove(attachment_id: str) -> None:
    """Delete the job row for *attachment_id* (called when the document is purged)."""
    get_jobs_col().delete_one({"_id": attachment_id})


def reap_stale(stale_seconds: int) -> int:
    """Flip jobs stuck in `processing` past *stale_seconds* to `error`.

    Called on startup so a crash/restart mid-pipeline never leaves a job wedged
    in `processing` forever (the in-process background task is gone after a
    restart). Returns the number of jobs reaped. Next.js should re-submit on a
    polling timeout, which overwrites the reaped record with a fresh run.
    """
    from datetime import timedelta

    cutoff = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    result = get_jobs_col().update_many(
        {"state": "processing", "updatedAt": {"$lt": cutoff}},
        {"$set": {
            "state":     "error",
            "detail":    "Analysis interrupted (service restarted mid-run). Re-submit to retry.",
            "updatedAt": datetime.now(UTC),
        }},
    )
    return result.modified_count
