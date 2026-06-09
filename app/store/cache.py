"""Content-hash dedup cache — the headline cost lever.

On a cache hit (same file bytes + same model version): return the stored
verdict immediately, spending zero embedding or LLM tokens.

The unique index on (contentHash, modelVersion) in models.py makes get()
a single indexed lookup.  put() uses upsert so re-running is safe.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.store.models import get_cache_col


def get(content_hash: str, model_version: str) -> dict[str, Any] | None:
    """Return the cached analysis dict, or None on a cache miss."""
    return get_cache_col().find_one(
        {"contentHash": content_hash, "modelVersion": model_version},
        {"_id": 0},
    )


def get_by_attachment(attachment_id: str, model_version: str) -> dict[str, Any] | None:
    """Return the most recent cached verdict for an attachmentId (by analyzedAt).

    Used by the /result polling endpoint when no job record exists — e.g. the
    attachment was analyzed by a previous deploy whose job rows have since aged
    out. Keyed on the existing attachmentId index.
    """
    return get_cache_col().find_one(
        {"attachmentId": attachment_id, "modelVersion": model_version},
        {"_id": 0},
        sort=[("analyzedAt", -1)],
    )


def put(
    *,
    attachment_id: str,
    content_hash: str,
    model_version: str,
    status: str,
    score: int,
    summary: str,
    score_components: dict[str, int],
    vector_ids: list[str] | None = None,
) -> None:
    """Upsert a cache record.  Safe to call multiple times with the same key."""
    get_cache_col().update_one(
        {"contentHash": content_hash, "modelVersion": model_version},
        {"$set": {
            "attachmentId":    attachment_id,
            "contentHash":     content_hash,
            "modelVersion":    model_version,
            "status":          status,
            "score":           score,
            "summary":         summary,
            "scoreComponents": score_components,
            "vectorIds":       vector_ids or [],
            "analyzedAt":      datetime.now(UTC),
        }},
        upsert=True,
    )
