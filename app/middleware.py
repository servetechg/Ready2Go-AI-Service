"""ASGI middleware for request correlation IDs.

Generates a uuid4 request_id per request and binds it into
structlog.contextvars so every log line in that request carries the same ID.
The ID is also echoed back as X-Request-ID in the response, allowing Next.js
to correlate logs with a specific upload.

Usage:
    app.add_middleware(CorrelationMiddleware)

Then in any module:
    import structlog
    log = structlog.get_logger(__name__)
    log.info("my.event", foo="bar")  # automatically includes request_id
"""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

log = structlog.get_logger(__name__)


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Bind a unique request_id into every log line for the duration of a request."""

    async def dispatch(self, request: Request, call_next: object) -> Response:
        request_id = str(uuid.uuid4())
        start = time.perf_counter()

        # Bind to structlog contextvars — cleared automatically per-request by
        # structlog.contextvars when the context exits.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        log.info(
            "http.request",
            method=request.method,
            path=request.url.path,
        )

        response: Response = await call_next(request)  # type: ignore[operator]

        latency_ms = round((time.perf_counter() - start) * 1000, 1)
        log.info(
            "http.response",
            status_code=response.status_code,
            latency_ms=latency_ms,
        )

        response.headers["X-Request-ID"] = request_id
        return response
