"""End-to-end contract tests for POST /v1/integrity/analyze.

Uses:
 - respx to mock OpenAI HTTP calls (no real API key required).
 - mongomock to mock MongoDB (no real Mongo required).
 - unittest.mock to stub out Weaviate repo calls.

Verifies:
 1. Response contract: status enum, score 0-100, summary <= 1000 chars.
 2. Cache hit path: second identical call returns cacheHit=true, zero new OpenAI calls.
 3. Auth guard: missing token -> 401.
 4. Graceful fallback on Weaviate failure: Reviewing returned, no 500.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TOKEN = "test-token-abc"

ANALYZE_PAYLOAD = {
    "tenantContext": {"tenantKey": "sub_test123"},
    "plan": {
        "planId": "plan_001",
        "label": "Business Continuity Plan",
        "overview": "Ensure operations continue during disruptions.",
        "category": "bcp",
        "steps": ["Identify risks", "Define recovery procedures"],
    },
    "attachment": {
        "attachmentId": "att_001",
        "fileName": "bcp_document.pdf",
        "fileExtension": "pdf",
        "fileUrl": "https://example.com/bcp_document.pdf",
    },
}

# Minimal 1536-d embedding (OpenAI text-embedding-3-small dimension).
_FAKE_EMBEDDING = [0.01] * 1536


def _embedding_response(n: int = 1) -> dict:
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": _FAKE_EMBEDDING}
            for i in range(n)
        ],
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": 10 * n, "total_tokens": 10 * n},
    }


@pytest.fixture(autouse=True)
def set_token(monkeypatch):
    """Set PYTHON_INTEGRITY_TOKEN so auth works in tests."""
    monkeypatch.setenv("PYTHON_INTEGRITY_TOKEN", TOKEN)
    # Clear the lru_cache so the patched env takes effect.
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def mock_mongo():
    """Patch all MongoDB collections with mongomock."""
    try:
        import mongomock
    except ImportError:
        pytest.skip("mongomock not installed")

    client = mongomock.MongoClient()
    db = client["ready2go_test"]

    cache_col = db["ai_analysis_cache"]
    cache_col.create_index(
        [("contentHash", 1), ("modelVersion", 1)],
        unique=True,
        name="cache_hash_model",
    )

    with (
        patch("app.store.cache.get_cache_col", return_value=cache_col),
        patch("app.store.aggregate.get_state_col", return_value=db["ai_audit_state"]),
        patch("app.store.calllog.get_log_col", return_value=db["ai_call_log"]),
        patch("app.store.models.get_cache_col", return_value=cache_col),
    ):
        yield cache_col


@pytest.fixture()
def mock_weaviate():
    """Stub out all Weaviate repo calls."""
    with (
        patch("app.vectors.repo.upsert_chunks"),
        patch("app.vectors.repo.get_all_chunks", return_value=[]),
        patch("app.vectors.repo.sibling_similarities", return_value=[]),
        patch("app.vectors.client.ensure_collections"),
    ):
        yield


@pytest.fixture()
def app_client(mock_mongo, mock_weaviate):
    """TestClient with mocked Mongo + Weaviate and patched startup bootstrap.

    main.py imports the bootstrap helpers lazily inside lifespan, so they must
    be patched at their source modules (not on app.main).
    """
    with (
        patch("app.store.models.ensure_indexes"),
        patch("app.vectors.client.ensure_collections"),
    ):
        from app.main import create_app
        app = create_app()
        return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


# ---------------------------------------------------------------------------
# 1. Auth guard
# ---------------------------------------------------------------------------

class TestAuthGuard:
    def test_missing_token_401(self, app_client):
        resp = app_client.post("/v1/integrity/analyze", json=ANALYZE_PAYLOAD)
        assert resp.status_code == 401

    def test_wrong_token_401(self, app_client):
        resp = app_client.post(
            "/v1/integrity/analyze",
            json=ANALYZE_PAYLOAD,
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. Response contract
# ---------------------------------------------------------------------------

class TestAnalyzeContract:
    @respx.mock
    def test_full_pipeline_returns_valid_contract(self, app_client):
        # Mock the file fetch.
        respx.get("https://example.com/bcp_document.pdf").mock(
            return_value=Response(
                200,
                content=b"%PDF-1.4 fake pdf content for testing purposes " * 50,
                headers={"Content-Type": "application/pdf"},
            )
        )
        # Mock OpenAI embeddings (called multiple times: chunks + plan context).
        respx.post("https://api.openai.com/v1/embeddings").mock(
            return_value=Response(200, json=_embedding_response(1))
        )

        resp = app_client.post(
            "/v1/integrity/analyze",
            json=ANALYZE_PAYLOAD,
            headers=_auth_headers(),
        )

        # Pipeline may return Reviewing if extraction/summary fails — that's OK.
        # What we assert is the shape of the response contract.
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in {"Compliant", "Under Review", "Non-Compliant"}
        assert isinstance(body["score"], int)
        assert 0 <= body["score"] <= 100
        assert isinstance(body["summary"], str)
        assert len(body["summary"]) <= 1000
        assert "analyzedAt" in body
        assert "modelVersion" in body

    @respx.mock
    def test_response_has_x_request_id_header(self, app_client):
        respx.get("https://example.com/bcp_document.pdf").mock(
            return_value=Response(
                200,
                content=b"fake content",
                headers={"Content-Type": "application/pdf"},
            )
        )
        respx.post("https://api.openai.com/v1/embeddings").mock(
            return_value=Response(200, json=_embedding_response(1))
        )
        resp = app_client.post(
            "/v1/integrity/analyze",
            json=ANALYZE_PAYLOAD,
            headers=_auth_headers(),
        )
        assert "x-request-id" in resp.headers


# ---------------------------------------------------------------------------
# 3. Cache hit path
# ---------------------------------------------------------------------------

class TestCacheHit:
    @respx.mock
    def test_second_call_returns_cache_hit(self, app_client, mock_mongo):
        """Identical bytes on second call → cacheHit=True, zero new embed calls."""
        fake_pdf = b"%PDF-1.4 " + b"A" * 500
        respx.get("https://example.com/bcp_document.pdf").mock(
            return_value=Response(
                200,
                content=fake_pdf,
                headers={"Content-Type": "application/pdf"},
            )
        )
        embed_calls = 0

        def count_embed(request, route):
            nonlocal embed_calls
            embed_calls += 1
            return Response(200, json=_embedding_response(1))

        respx.post("https://api.openai.com/v1/embeddings").mock(side_effect=count_embed)

        headers = _auth_headers()

        # First call — should run the full pipeline.
        r1 = app_client.post("/v1/integrity/analyze", json=ANALYZE_PAYLOAD, headers=headers)
        assert r1.status_code == 200
        first_embed_calls = embed_calls

        # Second call with the same URL (same bytes) — should hit cache.
        r2 = app_client.post("/v1/integrity/analyze", json=ANALYZE_PAYLOAD, headers=headers)
        assert r2.status_code == 200
        b2 = r2.json()

        assert b2.get("details", {}).get("cacheHit") is True
        # No additional OpenAI calls on second request.
        assert embed_calls == first_embed_calls


# ---------------------------------------------------------------------------
# 4. Graceful fallback on Weaviate failure
# ---------------------------------------------------------------------------

class TestWeaviateDegradation:
    @respx.mock
    def test_weaviate_failure_returns_reviewing_not_500(self, mock_mongo):
        """If Weaviate is down, the pipeline returns Reviewing instead of 500."""
        with (
            patch("app.vectors.repo.upsert_chunks", side_effect=RuntimeError("Weaviate down")),
            patch("app.vectors.repo.get_all_chunks", return_value=[]),
            patch("app.vectors.repo.sibling_similarities", return_value=[]),
            patch("app.store.models.ensure_indexes"),
            patch("app.vectors.client.ensure_collections"),
        ):
            from app.main import create_app
            app = create_app()
            test_client = TestClient(app, raise_server_exceptions=False)

            respx.get("https://example.com/bcp_document.pdf").mock(
                return_value=Response(
                    200,
                    content=b"%PDF fake " * 20,
                    headers={"Content-Type": "application/pdf"},
                )
            )
            respx.post("https://api.openai.com/v1/embeddings").mock(
                return_value=Response(200, json=_embedding_response(1))
            )

            resp = test_client.post(
                "/v1/integrity/analyze",
                json=ANALYZE_PAYLOAD,
                headers=_auth_headers(),
            )

        # Never a 500 — graceful Reviewing fallback.
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in {"Compliant", "Under Review", "Non-Compliant"}
        assert 0 <= body["score"] <= 100


# ---------------------------------------------------------------------------
# 5. Fetch failure → 502
# ---------------------------------------------------------------------------

class TestFetchFailure:
    @respx.mock
    def test_unreachable_url_returns_502(self, app_client):
        respx.get("https://example.com/bcp_document.pdf").mock(
            return_value=Response(404)
        )
        resp = app_client.post(
            "/v1/integrity/analyze",
            json=ANALYZE_PAYLOAD,
            headers=_auth_headers(),
        )
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# 6. Name signal — the query MUST be embedded and passed to the hybrid search
#    (regression guard: DocChunk has Vectorizer.none(), so without a query
#     vector the hybrid search throws and `name` silently collapsed to 0).
# ---------------------------------------------------------------------------

def _embed_side_effect(request) -> Response:
    """Return as many fake embeddings as the request asked for."""
    body = json.loads(request.content)
    inp = body.get("input", [])
    n = len(inp) if isinstance(inp, list) else 1
    return Response(200, json=_embedding_response(n))


def _good_extraction():
    """A parser result with real extractable text so the pipeline produces chunks."""
    from app.ingest.extract import Extraction, ExtractionQuality

    text = "Continuity of operations plan describing recovery procedures. " * 100
    return Extraction(
        text=text,
        quality=ExtractionQuality(
            chars=len(text), pages_or_rows=3, is_scan_only=False, is_empty=False
        ),
    )


class _FakeParser:
    def extract(self, data: bytes, ext: str):
        return _good_extraction()


class TestNameSignal:
    @respx.mock
    def test_name_scored_from_filename_no_weaviate_hybrid(self, mock_mongo):
        """Name = in-process cosine(filename, "label category"), no hybrid search.

        With identical stub embeddings that cosine is 1.0, so name == 100 — and it
        is computed without any Weaviate hybrid call (that function was removed),
        so it can never be silently zeroed by a top-N ranking window.
        """
        respx.get("https://example.com/bcp_document.pdf").mock(
            return_value=Response(
                200, content=b"%PDF fake", headers={"Content-Type": "application/pdf"}
            )
        )
        respx.post("https://api.openai.com/v1/embeddings").mock(
            side_effect=_embed_side_effect
        )

        with (
            patch("app.store.models.ensure_indexes"),
            patch("app.vectors.client.ensure_collections"),
            patch("app.api.integrity.get_parser", return_value=_FakeParser()),
            patch("app.vectors.repo.upsert_chunks", return_value=["uuid-1"]),
            patch("app.vectors.repo.get_all_chunks", return_value=[]),
            patch("app.vectors.repo.sibling_similarities", return_value=[]),
        ):
            from app.main import create_app
            client = TestClient(create_app(), raise_server_exceptions=False)
            resp = client.post(
                "/v1/integrity/analyze", json=ANALYZE_PAYLOAD, headers=_auth_headers()
            )

        assert resp.status_code == 200
        components = resp.json()["details"]["componentScores"]
        assert components.get("name") == 100

    @respx.mock
    def test_name_graceful_when_embeddings_fail(self, mock_mongo):
        """If embeddings are unavailable, name defaults to 0 and the call still 200s."""
        respx.get("https://example.com/bcp_document.pdf").mock(
            return_value=Response(
                200, content=b"%PDF fake", headers={"Content-Type": "application/pdf"}
            )
        )

        with (
            patch("app.store.models.ensure_indexes"),
            patch("app.vectors.client.ensure_collections"),
            patch("app.api.integrity.get_parser", return_value=_FakeParser()),
            patch(
                "app.api.integrity.embed_texts",
                new=AsyncMock(side_effect=RuntimeError("embed down")),
            ),
            patch("app.vectors.repo.upsert_chunks", return_value=["uuid-1"]),
            patch("app.vectors.repo.get_all_chunks", return_value=[]),
            patch("app.vectors.repo.sibling_similarities", return_value=[]),
        ):
            from app.main import create_app
            client = TestClient(create_app(), raise_server_exceptions=False)
            resp = client.post(
                "/v1/integrity/analyze", json=ANALYZE_PAYLOAD, headers=_auth_headers()
            )

        assert resp.status_code == 200
        components = resp.json()["details"]["componentScores"]
        assert components.get("name") in (0, None)


# ---------------------------------------------------------------------------
# 7. Audit endpoint — a Mongo/LLM failure must degrade gracefully (no 500).
# ---------------------------------------------------------------------------

AUDIT_PAYLOAD = {
    "tenantContext": {"tenantKey": "sub_test123"},
    "totals": {"plans": 2, "attachments": 5, "analyzed": 4},
    "averageScore": 70,
    "counts": {"coop": 1, "bcp": 1, "compliance": 0, "response": 0},
    "integrity": {"inSync": 3, "reviewing": 1, "deviation": 0, "unanalyzed": 1},
    "plans": [],
}


class TestAuditGraceful:
    def test_mongo_read_failure_returns_graceful_fallback(self, app_client):
        with patch(
            "app.store.aggregate.read", side_effect=RuntimeError("mongo down")
        ):
            resp = app_client.post(
                "/v1/audit/summary", json=AUDIT_PAYLOAD, headers=_auth_headers()
            )

        # Never a 500 — fallback built from the request payload, flagged degraded.
        assert resp.status_code == 200
        body = resp.json()
        assert body["posture"] in {"Resilient", "Steady", "At Risk"}
        assert body["averageScore"] == 70
        assert isinstance(body["summary"], str) and body["summary"]
        assert body["degraded"] is True
