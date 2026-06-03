"""Weaviate v4 client — lazy singleton + collection bootstrap.

get_client():
  Returns a connected weaviate.WeaviateClient, creating it on first call.
  Subsequent calls return the same cached instance.

ensure_collections():
  Creates DocChunk and CategoryPrototype collections if they don't exist yet.
  Safe to call on every startup — it's idempotent.

Both functions are sync; Weaviate v4's HTTP client is sync-friendly and the
ops (connect, check exists, create collection) are fast enough that wrapping
them in run_in_threadpool is not necessary at this scale.
"""

from __future__ import annotations

import logging

import weaviate

from app.config import get_settings
from app.vectors.schema import (
    CATEGORY_PROTOTYPE_COLLECTION,
    DOC_CHUNK_COLLECTION,
    category_prototype_collection_config,
    doc_chunk_collection_config,
)

logger = logging.getLogger(__name__)

_client: weaviate.WeaviateClient | None = None


def get_client() -> weaviate.WeaviateClient:
    """Return the cached Weaviate client, creating it on first call."""
    global _client
    if _client is None or not _client.is_connected():
        settings = get_settings()
        if not settings.weaviate_url:
            raise RuntimeError(
                "WEAVIATE_URL is not configured — set it in .env to use vector ops."
            )
        additional_headers: dict[str, str] = {}
        if settings.weaviate_api_key:
            additional_headers["X-Weaviate-Api-Key"] = settings.weaviate_api_key

        _client = weaviate.connect_to_custom(
            http_host=_host(settings.weaviate_url),
            http_port=_port(settings.weaviate_url),
            http_secure=settings.weaviate_url.startswith("https"),
            grpc_host=_host(settings.weaviate_url),
            grpc_port=50051,
            grpc_secure=settings.weaviate_url.startswith("https"),
            headers=additional_headers,
        )
        logger.info("weaviate.connected url=%s", settings.weaviate_url)
    return _client


def ensure_collections() -> None:
    """Create DocChunk and CategoryPrototype collections if absent (idempotent)."""
    client = get_client()
    existing = {c.name for c in client.collections.list_all(simple=True).values()}  # type: ignore[arg-type]

    if DOC_CHUNK_COLLECTION not in existing:
        client.collections.create_from_config(doc_chunk_collection_config())
        logger.info("weaviate.collection_created name=%s", DOC_CHUNK_COLLECTION)

    if CATEGORY_PROTOTYPE_COLLECTION not in existing:
        client.collections.create_from_config(category_prototype_collection_config())
        logger.info("weaviate.collection_created name=%s", CATEGORY_PROTOTYPE_COLLECTION)


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _host(url: str) -> str:
    """Extract host from a URL string like 'http://localhost:8080'."""
    url = url.rstrip("/")
    if "://" in url:
        url = url.split("://", 1)[1]
    return url.split(":")[0]


def _port(url: str) -> int:
    """Extract port from a URL string; default 8080."""
    url = url.rstrip("/")
    if "://" in url:
        url = url.split("://", 1)[1]
    parts = url.split(":")
    if len(parts) == 2:
        try:
            return int(parts[1])
        except ValueError:
            pass
    return 8080
