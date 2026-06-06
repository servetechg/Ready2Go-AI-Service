"""Weaviate collection definitions.

DocChunk (multi-tenant):
  One tenant per subadmin (tenantKey = "sub_" + ownerUserId).
  Each document's text chunks live here: text + vector + metadata.
  The isolation boundary is enforced by Weaviate — tenant A can never
  read tenant B objects.
"""

from __future__ import annotations

import weaviate.classes.config as wvc

# Collection names — referenced by both schema and repo so they stay in sync.
DOC_CHUNK_COLLECTION = "DocChunk"


def doc_chunk_properties() -> list[wvc.Property]:
    """Properties for the DocChunk collection."""
    return [
        wvc.Property(
            name="attachmentId",
            data_type=wvc.DataType.TEXT,
            description="MongoDB attachments._id (hex string). Idempotency key.",
        ),
        wvc.Property(
            name="planId",
            data_type=wvc.DataType.TEXT,
            description="Plan slug (groups files belonging to one plan).",
        ),
        wvc.Property(
            name="category",
            data_type=wvc.DataType.TEXT,
            description="coop | bcp | compliance",
        ),
        wvc.Property(
            name="fileName",
            data_type=wvc.DataType.TEXT,
            description="Original file name (used for name-alignment signal).",
        ),
        wvc.Property(
            name="contentHash",
            data_type=wvc.DataType.TEXT,
            skip_vectorization=True,
            description="SHA-256 of file bytes — dedup/cache key.",
        ),
        wvc.Property(
            name="chunkIndex",
            data_type=wvc.DataType.INT,
            description="0-based position of this chunk in the document.",
        ),
        wvc.Property(
            name="text",
            data_type=wvc.DataType.TEXT,
            description="The chunk text (BM25 indexed for hybrid search).",
        ),
        wvc.Property(
            name="modelVersion",
            data_type=wvc.DataType.TEXT,
            skip_vectorization=True,
            description="integrity-v1 (for re-embed invalidation).",
        ),
    ]
