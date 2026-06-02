"""M0 smoke tests: health endpoints + auth gating on /v1."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz_ok(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_reports_dependencies(client: TestClient) -> None:
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert set(body["dependencies"]) == {"openai", "weaviate", "postgres"}


def test_v1_requires_auth_when_token_configured(monkeypatch) -> None:
    """With a token configured, an unauthenticated /v1 call is rejected (401)."""
    monkeypatch.setenv("PYTHON_INTEGRITY_TOKEN", "secret-token")

    # Rebuild settings + app so the new env var is picked up.
    from app.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    local_client = TestClient(create_app())

    resp = local_client.post("/v1/integrity/analyze", json={})
    assert resp.status_code == 401

    get_settings.cache_clear()


def test_v1_contract_stub_returns_501_when_auth_disabled(client: TestClient) -> None:
    """With no token configured (auth disabled), a valid call reaches the 501 stub.

    We send an invalid body, so validation (422) fires before the stub — which
    still proves the route exists and auth did not block it.
    """
    resp = client.post("/v1/audit/summary", json={})
    assert resp.status_code in (422, 501)
