"""Weaviate v4 client — lazy singleton + collection bootstrap.

get_client():
  Returns a connected weaviate.WeaviateClient, creating it on first call.
  Subsequent calls return the same cached instance.

ensure_collections():
  Creates the DocChunk collection if it doesn't exist yet.
  Safe to call on every startup — it's idempotent.

Both functions are sync; Weaviate v4's HTTP client is sync-friendly and the
ops (connect, check exists, create collection) are fast enough that wrapping
them in run_in_threadpool is not necessary at this scale.
"""

from __future__ import annotations

import logging

import weaviate
import weaviate.classes.config as wvc

from app.config import get_settings
from app.vectors.schema import (
    DOC_CHUNK_COLLECTION,
    doc_chunk_properties,
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

        # HTTP port: explicit override (WEAVIATE_HTTP_PORT) else parsed from the URL.
        # gRPC port: WEAVIATE_GRPC_PORT (default 50051) — configurable for managed/
        # cloud Weaviate which may expose gRPC on a non-default port.
        http_port = settings.weaviate_http_port or _port(settings.weaviate_url)
        _client = weaviate.connect_to_custom(
            http_host=_host(settings.weaviate_url),
            http_port=http_port,
            http_secure=settings.weaviate_url.startswith("https"),
            grpc_host=_host(settings.weaviate_url),
            grpc_port=settings.weaviate_grpc_port,
            grpc_secure=settings.weaviate_url.startswith("https"),
            headers=additional_headers,
        )
        logger.info("weaviate.connected url=%s", settings.weaviate_url)
    return _client


def close_client() -> None:
    """Close the Weaviate client (call during shutdown)."""
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception as exc:
            logger.debug(
                "weaviate.close_failed detail=%s error=%s",
                "Closing the Weaviate client raised during shutdown; the reference is "
                "dropped anyway so the process can exit cleanly.",
                exc,
            )
        finally:
            _client = None
        logger.info("weaviate.disconnected")


def ensure_collections() -> None:
    """Create the DocChunk collection if absent (idempotent)."""
    client = get_client()
    # list_all() returns dict[name, CollectionConfig]; keys are collection names.
    existing = set(client.collections.list_all(simple=True).keys())

    if DOC_CHUNK_COLLECTION not in existing:
        client.collections.create(
            name=DOC_CHUNK_COLLECTION,
            properties=doc_chunk_properties(),
            # No Weaviate vectorizer — we supply OpenAI vectors ourselves.
            vectorizer_config=wvc.Configure.Vectorizer.none(),
            multi_tenancy_config=wvc.Configure.multi_tenancy(enabled=True),
        )
        logger.info("weaviate.collection_created name=%s", DOC_CHUNK_COLLECTION)


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
