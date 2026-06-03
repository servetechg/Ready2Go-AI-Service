"""Chat-completions wrapper: retry, JSON output, token metering.

Used by:
  - summary/per_doc.py   — generate the <=280-char one-liner
  - scoring/integrity.py — optional LLM judge for borderline scores (60-72 band)
  - summary/audit.py     — bounded vault audit narrative

All callers receive a plain dict (parsed from the LLM's JSON response).
A static fallback dict is returned on failure so callers never get an exception;
the call is still logged so cost / errors are visible.
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

_RETRYABLE = (openai.RateLimitError, openai.APIConnectionError, openai.APIStatusError)
_RETRY = retry(
    retry=retry_if_exception_type(_RETRYABLE),
    stop=stop_after_attempt(4),
    wait=wait_combine(wait_exponential(multiplier=1, min=1, max=60), wait_random(0, 2)),
    reraise=True,
)

LogCallFn = Callable[[dict[str, Any]], None]


async def chat_json(
    messages: list[dict[str, str]],
    *,
    fallback: dict[str, Any],
    model: str | None = None,
    max_tokens: int = 512,
    log_call: LogCallFn | None = None,
) -> dict[str, Any]:
    """Send a chat request and return the parsed JSON response dict.

    Args:
        messages:   OpenAI messages list [{"role": ..., "content": ...}].
        fallback:   Returned if the call fails or the response cannot be parsed.
                    Callers set a sensible default so the pipeline degrades
                    gracefully (e.g. status='Reviewing', score=50).
        model:      Override the model (default from settings.openai_summary_model).
        max_tokens: Max completion tokens.
        log_call:   Optional callback — same shape as embeddings.embed_texts.

    Returns:
        Parsed dict, or *fallback* on any failure.
    """
    import json

    settings = get_settings()
    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    effective_model = model or settings.openai_summary_model

    t0 = time.monotonic()
    try:
        response = await _chat_with_retry(client, effective_model, messages, max_tokens)
        latency_ms = int((time.monotonic() - t0) * 1000)
        usage = response.usage
        tokens = usage.total_tokens if usage else 0
        content = response.choices[0].message.content or ""
        parsed = json.loads(content)
        if log_call:
            log_call({
                "kind": "chat",
                "model": effective_model,
                "tokens": tokens,
                "latency_ms": latency_ms,
                "success": True,
                "error": None,
            })
        return parsed
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        if log_call:
            log_call({
                "kind": "chat",
                "model": effective_model,
                "tokens": 0,
                "latency_ms": latency_ms,
                "success": False,
                "error": str(exc),
            })
        return fallback


@_RETRY
async def _chat_with_retry(
    client: openai.AsyncOpenAI,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
) -> Any:
    return await client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
        temperature=0.2,
    )
