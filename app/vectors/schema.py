"""Weaviate collection definitions.

DocChunk (multi-tenant):
  One tenant per subadmin (tenantKey = "sub_" + ownerUserId).
  Each document's text chunks live here: text + vector + metadata.
  The isolation boundary is enforced by Weaviate — tenant A can never
  read tenant B objects.

CategoryPrototype (global, no multi-tenancy):
  Canonical reference embeddings for coop / bcp / compliance categories.
  Used by the category-fit scoring signal.  Seeded once from .gov sources.
"""

from __future__ import annotations

import weaviate.classes.config as wvc

# Collection names — referenced by both schema and repo so they stay in sync.
DOC_CHUNK_COLLECTION = "DocChunk"
CATEGORY_PROTOTYPE_COLLECTION = "CategoryPrototype"


def doc_chunk_collection_config() -> wvc.CollectionConfig:
    """Return the CollectionConfig for DocChunk (multi-tenant).

    We supply vectors ourselves (no Weaviate vectorizer module); Weaviate just
    stores and indexes them.  BM25 keyword index is also enabled on `text` so
    hybrid search works out of the box.
    """
    return wvc.CollectionConfig(
        name=DOC_CHUNK_COLLECTION,
        description="Text chunks from continuity-vault attachments, one tenant per subadmin.",
        vectorizer_config=wvc.Configure.Vectorizer.none(),
        multi_tenancy_config=wvc.Configure.multi_tenancy(enabled=True),
        properties=[
            wvc.Property(name="attachmentId", data_type=wvc.DataType.TEXT,
                         description="MongoDB attachments._id (hex string). Idempotency key."),
            wvc.Property(name="planId", data_type=wvc.DataType.TEXT,
                         description="Plan slug (groups files belonging to one plan)."),
            wvc.Property(name="category", data_type=wvc.DataType.TEXT,
                         description="coop | bcp | compliance"),
            wvc.Property(name="fileName", data_type=wvc.DataType.TEXT,
                         description="Original file name (used for name-alignment signal)."),
            wvc.Property(name="contentHash", data_type=wvc.DataType.TEXT,
                         skip_vectorization=True,
                         description="SHA-256 of file bytes — dedup/cache key."),
            wvc.Property(name="chunkIndex", data_type=wvc.DataType.INT,
                         description="0-based position of this chunk in the document."),
            wvc.Property(name="text", data_type=wvc.DataType.TEXT,
                         description="The chunk text (BM25 indexed for hybrid search)."),
            wvc.Property(name="modelVersion", data_type=wvc.DataType.TEXT,
                         skip_vectorization=True,
                         description="integrity-v1 (for re-embed invalidation)."),
        ],
    )


def category_prototype_collection_config() -> wvc.CollectionConfig:
    """Return the CollectionConfig for CategoryPrototype (global, no tenancy).

    One object per category (coop, bcp, compliance).  Each stores the
    mean embedding vector of its canonical reference documents.
    """
    return wvc.CollectionConfig(
        name=CATEGORY_PROTOTYPE_COLLECTION,
        description="Global category reference vectors (coop/bcp/compliance) for scoring.",
        vectorizer_config=wvc.Configure.Vectorizer.none(),
        properties=[
            wvc.Property(name="category", data_type=wvc.DataType.TEXT,
                         description="coop | bcp | compliance"),
            wvc.Property(name="modelVersion", data_type=wvc.DataType.TEXT,
                         description="Embedding model version — re-seed when changed."),
        ],
    )
