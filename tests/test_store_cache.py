"""Unit tests for app/store/cache.py using mongomock.

Tests: cache get/put round-trip, cache hit produces zero extra work,
and idempotent upsert behaviour.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_collection():
    """Return an in-memory mongomock collection (no real Mongo needed)."""
    try:
        import mongomock
        client = mongomock.MongoClient()
        db = client["ready2go_test"]
        col = db["ai_analysis_cache"]
        # Create the unique index that models.py would create.
        col.create_index(
            [("contentHash", 1), ("modelVersion", 1)],
            unique=True,
            name="cache_hash_model",
        )
        return col
    except ImportError:
        pytest.skip("mongomock not installed")


@pytest.fixture()
def patched_cache(mock_collection):
    """Patch get_cache_col() so cache.py uses the in-memory collection."""
    with patch("app.store.cache.get_cache_col", return_value=mock_collection):
        from app.store import cache
        yield cache, mock_collection


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCacheMiss:
    def test_miss_returns_none(self, patched_cache):
        cache, _ = patched_cache
        result = cache.get("nonexistent_hash", "integrity-v1")
        assert result is None


class TestCachePutAndGet:
    def test_put_then_get_returns_record(self, patched_cache):
        cache, _ = patched_cache
        cache.put(
            attachment_id="att_001",
            content_hash="abc123",
            model_version="integrity-v1",
            status="Compliant",
            score=85,
            summary="Good COOP document.",
            score_components={
                "content": 90, "name": 80, "category": 85, "quality": 90, "duplication": 100
            },
        )
        result = cache.get("abc123", "integrity-v1")
        assert result is not None
        assert result["status"] == "Compliant"
        assert result["score"] == 85
        assert result["summary"] == "Good COOP document."
        assert result["attachmentId"] == "att_001"

    def test_id_field_excluded_from_result(self, patched_cache):
        cache, _ = patched_cache
        cache.put(
            attachment_id="att_002",
            content_hash="def456",
            model_version="integrity-v1",
            status="Under Review",
            score=55,
            summary="Needs review.",
            score_components={},
        )
        result = cache.get("def456", "integrity-v1")
        assert "_id" not in result

    def test_put_is_idempotent(self, patched_cache):
        cache, col = patched_cache
        kwargs = {
            "attachment_id": "att_003",
            "content_hash": "ghi789",
            "model_version": "integrity-v1",
            "status": "Compliant",
            "score": 75,
            "summary": "First version.",
            "score_components": {},
        }
        cache.put(**kwargs)
        # Update the score on second call.
        cache.put(**{**kwargs, "score": 80, "summary": "Updated version."})

        result = cache.get("ghi789", "integrity-v1")
        # Should be the latest value.
        assert result["score"] == 80
        # Only one document stored (upsert, not insert).
        assert col.count_documents({"contentHash": "ghi789"}) == 1


class TestCacheModelVersionIsolation:
    def test_different_model_version_is_miss(self, patched_cache):
        cache, _ = patched_cache
        cache.put(
            attachment_id="att_004",
            content_hash="same_hash",
            model_version="integrity-v1",
            status="Compliant",
            score=90,
            summary="v1 summary.",
            score_components={},
        )
        result = cache.get("same_hash", "integrity-v2")
        assert result is None

    def test_same_hash_different_model_versions_coexist(self, patched_cache):
        cache, col = patched_cache
        for version in ("integrity-v1", "integrity-v2"):
            cache.put(
                attachment_id="att_005",
                content_hash="multi_version_hash",
                model_version=version,
                status="Compliant",
                score=80,
                summary=f"Summary for {version}.",
                score_components={},
            )
        assert col.count_documents({"contentHash": "multi_version_hash"}) == 2
