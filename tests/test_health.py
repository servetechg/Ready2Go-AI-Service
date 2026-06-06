"""Smoke tests: health endpoints + auth gating on /v1."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz_ok(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_returns_dependency_shape(client: TestClient) -> None:
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    # Status is "ok" when all deps are live, "degraded" otherwise.
    # In unit-test env (no Weaviate/Mongo) either is acceptable.
    assert body["status"] in {"ok", "degraded"}
    assert set(body["dependencies"]) == {"openai", "weaviate", "mongodb"}


def test_v1_requires_auth_when_token_configured(monkeypatch) -> None:
    """With a token configured, an unauthenticated /v1 call is rejected (401)."""
    monkeypatch.setenv("PYTHON_INTEGRITY_TOKEN", "secret-token")

    from app.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    local_client = TestClient(create_app())

    resp = local_client.post("/v1/integrity/analyze", json={})
    assert resp.status_code == 401

    get_settings.cache_clear()


def test_v1_auth_disabled_when_no_token_in_dev(monkeypatch) -> None:
    """In dev mode with no token configured, auth is disabled (requests pass through).

    We send an invalid body so validation (422) fires before any pipeline code,
    proving the route exists and auth did not block the request.
    """
    monkeypatch.setenv("PYTHON_INTEGRITY_TOKEN", "")
    monkeypatch.setenv("ENV", "development")

    from app.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    local_client = TestClient(create_app())

    resp = local_client.post("/v1/audit/summary", json={})
    # 422 = validation error (body invalid) — auth did not block; 401 = auth blocked.
    assert resp.status_code == 422

    get_settings.cache_clear()
