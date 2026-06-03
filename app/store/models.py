"""MongoDB `ai_*` collection accessors + index bootstrap.

Collections (all in the `ready2go` DB alongside app domain docs):
  ai_analysis_cache  — dedup cache; unique (contentHash, modelVersion)
  ai_audit_state     — per-tenant rolling aggregate; _id = tenantKey
  ai_call_log        — append-only AI call audit trail

Driver: pymongo (sync).
Used from async FastAPI routes via starlette's run_in_threadpool so we
don't need a separate motor dependency — the ops are tiny (single-doc reads
and upserts) so the threadpool overhead is negligible at this scale.

The prep/seed scripts use app/store/mongo.py for their separate connection;
this module is the request-path accessor only.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import pymongo
from pymongo.collection import Collection

from app.config import get_settings

logger = logging.getLogger(__name__)

# Collection names — referenced by cache.py, aggregate.py, calllog.py.
CACHE_COLLECTION = "ai_analysis_cache"
STATE_COLLECTION = "ai_audit_state"
LOG_COLLECTION   = "ai_call_log"

_client: pymongo.MongoClient | None = None  # type: ignore[type-arg]


@lru_cache(maxsize=1)
def _db() -> pymongo.database.Database:  # type: ignore[type-arg]
    global _client
    settings = get_settings()
    if not settings.mongodb_uri:
        raise RuntimeError(
            "MONGODB_URI is not configured — set it in .env for request-path Mongo ops."
        )
    _client = pymongo.MongoClient(settings.mongodb_uri, appname="r2g-ai-service")
    return _client[settings.mongodb_db]


def get_cache_col() -> Collection:  # type: ignore[type-arg]
    return _db()[CACHE_COLLECTION]


def get_state_col() -> Collection:  # type: ignore[type-arg]
    return _db()[STATE_COLLECTION]


def get_log_col() -> Collection:  # type: ignore[type-arg]
    return _db()[LOG_COLLECTION]


def ensure_indexes() -> None:
    """Create indexes that don't exist yet (idempotent, safe to call on startup)."""
    db = _db()

    # ai_analysis_cache — the dedup lookup must be fast + unique.
    cache = db[CACHE_COLLECTION]
    cache.create_index(
        [("contentHash", pymongo.ASCENDING), ("modelVersion", pymongo.ASCENDING)],
        unique=True,
        name="cache_hash_model",
        background=True,
    )
    cache.create_index("attachmentId", background=True)

    # ai_audit_state — _id IS the tenantKey (set explicitly on insert).
    # No additional index needed; _id is always indexed.

    # ai_call_log — time-series, query by ts descending.
    log = db[LOG_COLLECTION]
    log.create_index([("ts", pymongo.DESCENDING)], background=True)
    log.create_index("attachmentId", background=True)

    logger.info("store.indexes_ensured")
