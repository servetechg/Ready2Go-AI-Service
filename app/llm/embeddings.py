"""Batched text embeddings via OpenAI text-embedding-3-small.

Key design choices:
  - One API call for ALL chunks of a document (batching) — cheaper + faster
    than one call per chunk.
  - tenacity retries with exponential back-off + jitter on transient failures
    (rate-limit, 5xx, network hiccup).  This fixes the silent-failure weakness
    of the old Next.js pipeline.
  - Caller supplies a log_call callback so every embedding call is recorded in
    ai_call_log (token count, latency, success/error).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import openai
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_combine,
    wait_exponential,
    wait_random,
)

from app.config import get_settings

# Dimension for text-embedding-3-small
EMBEDDING_DIM = 1536

# Retry policy: up to 4 attempts, exp back-off 1s-60s + 0-2s jitter.
_RETRYABLE = (openai.RateLimitError, openai.APIConnectionError, openai.APIStatusError)
_RETRY = retry(
    retry=retry_if_exception_type(_RETRYABLE),
    stop=stop_after_attempt(4),
    wait=wait_combine(wait_exponential(multiplier=1, min=1, max=60), wait_random(0, 2)),
    reraise=True,
)

# Signature for the optional call-log callback.
LogCallFn = Callable[[dict[str, Any]], None]


async def embed_texts(
    texts: list[str],
    *,
    log_call: LogCallFn | None = None,
) -> list[list[float]]:
    """Embed *texts* in a single batched API call.

    Args:
        texts:    List of strings to embed (all chunks of one document).
        log_call: Optional callback receiving a dict with
                  {kind, model, tokens, latency_ms, success, error}.
                  Pass store/calllog.append to record every call.

    Returns:
        List of 1536-d float vectors, one per input text, in order.

    Raises:
        openai.OpenAIError: after all retry attempts are exhausted.
    """
    settings = get_settings()
    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    model = settings.openai_embed_model

    t0 = time.monotonic()
    try:
        response = await _embed_with_retry(client, model, texts)
        latency_ms = int((time.monotonic() - t0) * 1000)
        tokens = response.usage.total_tokens if response.usage else 0
        if log_call:
            log_call({
                "kind": "embed",
                "model": model,
                "tokens": tokens,
                "latency_ms": latency_ms,
                "success": True,
                "error": None,
            })
        # Sort by index to guarantee order matches input.
        sorted_data = sorted(response.data, key=lambda d: d.index)
        return [d.embedding for d in sorted_data]
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        if log_call:
            log_call({
                "kind": "embed",
                "model": model,
                "tokens": 0,
                "latency_ms": latency_ms,
                "success": False,
                "error": str(exc),
            })
        raise


@_RETRY
async def _embed_with_retry(
    client: openai.AsyncOpenAI,
    model: str,
    texts: list[str],
) -> Any:
    return await client.embeddings.create(model=model, input=texts)
