"""Weaviate repository — all vector store operations the pipeline needs.

Every DocChunk operation is tenant-scoped (tenantKey = "sub_" + ownerUserId).
A missing tenant is always a hard error — never a silent global query.

Operations:
  upsert_chunks        — store one document's chunks + vectors (delete-first).
  delete_by_attachment — remove all chunks for one attachment (used by upsert
                         and by the rescan/delete-attachment routes).
  get_all_chunks       — fetch the COMPLETE chunk set for one attachment (for
                         per-doc summarization — this is a fetch-by-id, not
                         a similarity search).
  content_centroid     — compute mean vector across all of a doc's chunks.
  sibling_similarities — nearest-neighbour cosine among siblings in same plan
                         (duplication signal).
  hybrid_name_search   — BM25 + vector search of the filename/slug (name signal).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import weaviate.classes.data as wvd
import weaviate.classes.query as wvq
from weaviate.classes.tenants import Tenant

from app.vectors.client import get_client
from app.vectors.schema import DOC_CHUNK_COLLECTION

logger = logging.getLogger(__name__)

# Cosine distance that we treat as "basically identical" (duplication flag).
_DUP_THRESHOLD = 0.05


@dataclass
class StoredChunk:
    """A chunk as returned from Weaviate."""
    chunk_index: int
    text: str
    attachment_id: str
    vector: list[float] | None = None


@dataclass
class SimilarityResult:
    """Distance + metadata for a nearest-neighbour result."""
    attachment_id: str
    distance: float    # 0 = identical, 2 = opposite (Weaviate cosine distance)

    @property
    def similarity(self) -> float:
        """Convert Weaviate cosine distance to cosine similarity [-1, 1]."""
        return 1.0 - self.distance


# ---------------------------------------------------------------------------
# Write ops
# ---------------------------------------------------------------------------

def upsert_chunks(
    tenant: str,
    attachment_id: str,
    texts: list[str],
    vectors: list[list[float]],
    *,
    plan_id: str,
    category: str,
    file_name: str,
    content_hash: str,
    model_version: str,
) -> None:
    """Delete existing chunks for *attachment_id*, then insert the new ones.

    Re-upload = delete-then-insert so there are never stale duplicates.
    Idempotent: safe to call multiple times for the same attachment.
    """
    _require_tenant(tenant)
    # Multi-tenancy is enabled on DocChunk — the tenant must be registered
    # before any read/write under it, or Weaviate raises "tenant not found".
    ensure_tenant(tenant)
    delete_by_attachment(tenant, attachment_id)

    client = get_client()
    collection = client.collections.get(DOC_CHUNK_COLLECTION)
    tenant_col = collection.with_tenant(tenant)

    objects = []
    for i, (text, vector) in enumerate(zip(texts, vectors, strict=True)):
        objects.append(
            wvd.DataObject(
                properties={
                    "attachmentId": attachment_id,
                    "planId": plan_id,
                    "category": category,
                    "fileName": file_name,
                    "contentHash": content_hash,
                    "chunkIndex": i,
                    "text": text,
                    "modelVersion": model_version,
                },
                vector=vector,
            )
        )

    if objects:
        tenant_col.data.insert_many(objects)  # type: ignore[arg-type]
    logger.debug(
        "weaviate.upsert_chunks tenant=%s attachment=%s count=%d",
        tenant, attachment_id, len(objects),
    )


def delete_by_attachment(tenant: str, attachment_id: str) -> None:
    """Remove all chunks belonging to *attachment_id* within *tenant*.

    Tolerant of a not-yet-registered tenant: nothing to delete means success.
    """
    _require_tenant(tenant)
    client = get_client()
    collection = client.collections.get(DOC_CHUNK_COLLECTION)
    if not collection.tenants.exists(tenant):
        return  # tenant has no data yet — nothing to delete.
    tenant_col = collection.with_tenant(tenant)
    tenant_col.data.delete_many(
        where=wvq.Filter.by_property("attachmentId").equal(attachment_id)
    )


# ---------------------------------------------------------------------------
# Read ops
# ---------------------------------------------------------------------------

def get_all_chunks(tenant: str, attachment_id: str) -> list[StoredChunk]:
    """Fetch the COMPLETE chunk set for one attachment (fetch-by-id, not search).

    Used by per-doc summarization — we own the whole document, so there is no
    "most relevant" subset; every chunk is needed.
    """
    _require_tenant(tenant)
    client = get_client()
    collection = client.collections.get(DOC_CHUNK_COLLECTION)
    if not collection.tenants.exists(tenant):
        return []  # no data for this tenant yet.
    tenant_col = collection.with_tenant(tenant)

    result = tenant_col.query.fetch_objects(
        filters=wvq.Filter.by_property("attachmentId").equal(attachment_id),
        include_vector=True,
        limit=10_000,  # hard ceiling; chunk cap prevents hitting this in practice
    )

    chunks = []
    for obj in result.objects:
        props = obj.properties
        raw_vec = obj.vector.get("default") if obj.vector else None
        # The "default" key holds list[float] for unnamed vectors;
        # cast away the broader type that the SDK exposes for named/multi vectors.
        vec_val: list[float] | None = raw_vec if isinstance(raw_vec, list) else None  # type: ignore[assignment]
        chunks.append(StoredChunk(
            chunk_index=int(props.get("chunkIndex", 0)),  # type: ignore[arg-type]
            text=str(props.get("text", "")),
            attachment_id=str(props.get("attachmentId", "")),
            vector=vec_val,
        ))

    # Return in document order.
    chunks.sort(key=lambda c: c.chunk_index)
    return chunks


def content_centroid(chunks: list[StoredChunk]) -> list[float] | None:
    """Compute the mean vector (centroid) across all of a document's chunks.

    The centroid is the single representative vector for the whole document.
    It's compared against the plan context for the content-alignment signal.
    Returns None if no chunks have vectors.
    """
    vecs = [c.vector for c in chunks if c.vector]
    if not vecs:
        return None
    arr = np.array(vecs, dtype=np.float32)
    mean = arr.mean(axis=0)
    norm = float(np.linalg.norm(mean))
    if norm > 0:
        mean = mean / norm
    return mean.tolist()


def sibling_similarities(
    tenant: str,
    plan_id: str,
    centroid: list[float],
    *,
    exclude_attachment_id: str,
    limit: int = 10,
) -> list[SimilarityResult]:
    """Find nearest siblings (same plan, different attachment) — duplication signal.

    Returns similarity results (most similar first).
    """
    _require_tenant(tenant)
    client = get_client()
    collection = client.collections.get(DOC_CHUNK_COLLECTION)
    if not collection.tenants.exists(tenant):
        return []  # no siblings for a tenant with no data.
    tenant_col = collection.with_tenant(tenant)

    result = tenant_col.query.near_vector(
        near_vector=centroid,
        filters=wvq.Filter.by_property("planId").equal(plan_id)
                & wvq.Filter.by_property("attachmentId").not_equal(exclude_attachment_id),
        limit=limit,
        return_metadata=wvq.MetadataQuery(distance=True),
        return_properties=["attachmentId"],
    )

    seen: set[str] = set()
    sims = []
    for obj in result.objects:
        aid = str(obj.properties.get("attachmentId", ""))
        if aid in seen:
            continue
        seen.add(aid)
        dist = obj.metadata.distance if obj.metadata else 1.0
        sims.append(SimilarityResult(attachment_id=aid, distance=dist or 1.0))
    return sims


def hybrid_name_search(
    tenant: str,
    query: str,
    query_vector: list[float],
    limit: int = 5,
) -> list[SimilarityResult]:
    """BM25 + vector hybrid search on the fileName field — name-alignment signal.

    The query is typically "<planLabel> <category>" so both keyword matches
    (exact filename words) and semantic similarity contribute.

    DocChunk is created with Vectorizer.none() (we supply our own OpenAI
    vectors), so the caller MUST pass *query_vector* — the embedding of *query*.
    Without it, Weaviate cannot build the dense half of the hybrid query and the
    call fails, which is exactly how the name signal silently collapsed to 0
    before this parameter existed.

    Note: no target_vector arg for single (unnamed) vector collections.
    """
    _require_tenant(tenant)
    client = get_client()
    collection = client.collections.get(DOC_CHUNK_COLLECTION)
    if not collection.tenants.exists(tenant):
        return []  # no data to name-search for this tenant yet.
    tenant_col = collection.with_tenant(tenant)

    result = tenant_col.query.hybrid(
        query=query,
        vector=query_vector,  # dense half — required under Vectorizer.none()
        alpha=0.5,            # 50% BM25, 50% vector
        limit=limit,
        return_metadata=wvq.MetadataQuery(score=True),
        return_properties=["attachmentId", "fileName"],
    )

    seen: set[str] = set()
    hits = []
    for obj in result.objects:
        aid = str(obj.properties.get("attachmentId", ""))
        if aid in seen:
            continue
        seen.add(aid)
        score = obj.metadata.score if obj.metadata else 0.0
        hits.append(SimilarityResult(attachment_id=aid, distance=1.0 - (score or 0.0)))
    return hits


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------

def _require_tenant(tenant: str) -> None:
    if not tenant or not tenant.strip():
        raise ValueError(
            "tenantKey is required for all DocChunk operations — "
            "never run a vector op without a tenant."
        )


def ensure_tenant(tenant: str) -> None:
    """Register *tenant* in the DocChunk collection if it doesn't exist yet.

    Multi-tenancy is enabled on DocChunk (see vectors/client.ensure_collections),
    so a tenant must be created before any read or write under it. Idempotent and
    cheap (a single existence check), safe to call on every write.
    """
    _require_tenant(tenant)
    client = get_client()
    collection = client.collections.get(DOC_CHUNK_COLLECTION)
    if not collection.tenants.exists(tenant):
        collection.tenants.create(Tenant(name=tenant))
        logger.info("weaviate.tenant_created tenant=%s", tenant)
