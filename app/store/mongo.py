"""MongoDB access for offline prep/seed scripts ONLY.

This module is imported exclusively by `scripts/prep/*`.
It must NEVER be imported by `app/api/*` or any FastAPI route — the live request
path stays Mongo-free (ARCHITECTURE §1.3: Next.js owns all request-time Mongo writes).

Connection targets the Next.js `ready2go` database.

The Next.js app now uses tenant-aware PARALLEL collections (each record carries
`ownerUserId`); the old `emergencyplans` / `continuityaudits` are deprecated and
no longer read by the UI. Collection names follow Mongoose's pluralisation:
  ContinuityPlan        -> continuityplans
  ContinuityAuditReport -> continuityauditreports
"""

from __future__ import annotations

from functools import lru_cache

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from app.config import get_settings

_PLANS_COLLECTION = "continuityplans"
_AUDITS_COLLECTION = "continuityauditreports"


@lru_cache(maxsize=1)
def get_client() -> MongoClient:  # type: ignore[type-arg]
    """Return the process-wide MongoClient, built lazily on first call.

    Raises RuntimeError clearly if MONGODB_URI is not configured so the caller
    gets an actionable message instead of a cryptic connection error.
    """
    settings = get_settings()
    if not settings.mongodb_uri:
        raise RuntimeError(
            "MONGODB_URI is not set.\n"
            "Add it to your .env (pointing at a DEV/STAGING cluster — never production).\n"
            "See .env.example for the full list of required seed variables."
        )
    return MongoClient(settings.mongodb_uri, appname="r2g-ai-prep")


def get_db() -> Database:  # type: ignore[type-arg]
    """Return the configured database handle."""
    return get_client()[get_settings().mongodb_db]


def plans() -> Collection:  # type: ignore[type-arg]
    """The `continuityplans` collection — the tenant-aware integration surface."""
    return get_db()[_PLANS_COLLECTION]


def audits() -> Collection:  # type: ignore[type-arg]
    """The `continuityauditreports` collection (one report per subadmin)."""
    return get_db()[_AUDITS_COLLECTION]
