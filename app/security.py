"""Request authentication for `/v1` routes.

Two mechanisms (either is sufficient, both can be required by config):
  * Bearer token  — `Authorization: Bearer <PYTHON_INTEGRITY_TOKEN>`
  * HMAC signature — `X-Ready2Go-Signature: hex(HMAC_SHA256(HMAC_SECRET, body))`

Health endpoints are intentionally left open.

Production safety: if env == production and no token is configured the service
FAILS CLOSED (401 on every request) rather than silently disabling auth.
This prevents a missing env var from opening the API to anonymous callers.
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import Header, HTTPException, Request, status

from app.config import get_settings


def _bearer_ok(authorization: str | None, expected_token: str, is_production: bool) -> bool:
    if not expected_token:
        # Production with no token → fail closed; dev/staging → auth disabled.
        return not is_production
    if not authorization or not authorization.startswith("Bearer "):
        return False
    presented = authorization.removeprefix("Bearer ").strip()
    return hmac.compare_digest(presented, expected_token)


async def _hmac_ok(request: Request, signature: str | None, secret: str) -> bool:
    if not secret:
        return True  # HMAC not configured -> skip.
    if not signature:
        return False
    body = await request.body()
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


async def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    x_ready2go_signature: str | None = Header(default=None),
) -> None:
    """FastAPI dependency that guards protected routes.

    Raises 401 unless the configured credentials are satisfied.
    In production, a missing PYTHON_INTEGRITY_TOKEN fails closed (not open).
    """
    settings = get_settings()

    if not _bearer_ok(authorization, settings.python_integrity_token, settings.is_production):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
        )

    if not await _hmac_ok(request, x_ready2go_signature, settings.hmac_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid HMAC signature",
        )
