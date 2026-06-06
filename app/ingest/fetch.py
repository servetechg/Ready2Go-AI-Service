"""Download attachment bytes from any URL and compute a SHA-256 content hash.

Used by:
  - scripts/prep/seed_mongo.py  — fetch .gov PDFs for seeding (Phase A)
  - app/api/integrity.py        — fetch Cloudinary attachments on the live request path (later)

Design decisions:
  - Streams the response so we can enforce the 25 MB size cap without buffering
    the full body first (mirrors Next.js MAX_BYTES = 25 * 1024 * 1024).
  - Computes SHA-256 over the final bytes — this is the dedup/cache key used by
    the analysis_cache table and the content-hash cost lever (ARCHITECTURE §4/§8.1).
  - Raises typed errors so callers can skip-and-log per document without aborting
    the entire run (seed_mongo.py catches these per SeedDoc).
  - Sets a descriptive User-Agent because .gov hosts enforce one and will return
    403/blocked responses for requests with no UA header.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import httpx

# Maximum download size for an attachment. Raised above the Next.js upload limit
# (25 MB) so the service can also ingest larger files fetched directly (e.g. big
# .gov source PDFs during testing/seeding). Note: real user uploads are still
# bounded by the Next.js side; this only caps what THIS service will download.
MAX_BYTES = 50 * 1_024 * 1_024  # 50 MB

_USER_AGENT = (
    "ready2go-ai-prep/0.1 "
    "(continuity document seeder; contact info@servetechglobal.com)"
)

# MIME prefixes accepted as valid document content.
_ALLOWED_MIME_PREFIXES = (
    "application/pdf",
    "application/vnd.openxmlformats",  # .docx / .xlsx
    "application/msword",
    "application/vnd.ms-excel",
    "text/csv",
    "text/plain",
    "application/octet-stream",        # some .gov servers return this for PDFs
)


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

@dataclass
class FetchResult:
    """Successful download result."""
    data: bytes          # raw file bytes
    content_hash: str    # hex SHA-256 of `data` — dedup/cache key
    mime: str            # Content-Type reported by the server
    size_bytes: int      # len(data)


# ---------------------------------------------------------------------------
# Typed errors — callers catch these to skip-and-log without full abort
# ---------------------------------------------------------------------------

class FetchError(Exception):
    """Base class for all fetch failures."""
    def __init__(self, url: str, reason: str) -> None:
        self.url = url
        self.reason = reason
        super().__init__(f"fetch failed [{url}]: {reason}")


class FetchHTTPError(FetchError):
    """Non-200 HTTP response."""
    def __init__(self, url: str, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(url, f"HTTP {status_code}")


class FetchSizeError(FetchError):
    """Response body exceeds MAX_BYTES."""
    def __init__(self, url: str) -> None:
        super().__init__(url, f"response exceeds {MAX_BYTES // (1024 * 1024)} MB limit")


class FetchTypeError(FetchError):
    """Content-Type is not a recognised document type."""
    def __init__(self, url: str, mime: str) -> None:
        self.mime = mime
        super().__init__(url, f"unexpected Content-Type '{mime}'")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def fetch_bytes(url: str, *, timeout: float = 60.0) -> FetchResult:
    """Download *url* and return a FetchResult.

    Enforces the 25 MB cap by streaming and counting bytes before assembling
    the final buffer, so an oversized file is rejected early without wasting
    memory or bandwidth.

    Args:
        url:     The full HTTPS URL to download (Cloudinary secure_url or .gov PDF).
        timeout: Total request timeout in seconds (default 60 — gov PDFs can be slow).

    Raises:
        FetchHTTPError:  Server returned a non-200 status.
        FetchSizeError:  Response body exceeds 25 MB.
        FetchTypeError:  Content-Type is not a recognised document MIME type.
        FetchError:      Any other network / connection failure.
    """
    headers = {"User-Agent": _USER_AGENT}

    try:
        # Nested (not combined) because client.stream() needs `client` bound first.
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:  # noqa: SIM117
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code != 200:
                    raise FetchHTTPError(url, response.status_code)

                # Validate Content-Type before reading the body.
                mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if mime and not any(mime.startswith(p) for p in _ALLOWED_MIME_PREFIXES):
                    raise FetchTypeError(url, mime)

                # Stream-read with size guard.
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise FetchSizeError(url)
                    chunks.append(chunk)

    except (FetchHTTPError, FetchSizeError, FetchTypeError):
        raise
    except httpx.HTTPError as exc:
        raise FetchError(url, str(exc)) from exc

    data = b"".join(chunks)
    content_hash = hashlib.sha256(data).hexdigest()
    final_mime = mime or "application/octet-stream"

    return FetchResult(
        data=data,
        content_hash=content_hash,
        mime=final_mime,
        size_bytes=len(data),
    )
