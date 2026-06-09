"""Per-document <=2000-char plan-review summary.

The summary is a review of the document framed for the demo, with three labeled
sections — Overview / What went well / Areas for improvement — describing what the
plan covers and the response actions it defines for an event. It deliberately
carries NO score/status language (those live as separate response fields).

Approach (D7 — full-document, no retrieval subset):
  - Use the complete chunk set (all of this document's chunks from Weaviate).
  - If the chunks fit in the LLM context budget, send them all at once.
  - If too many chunks, map-reduce: group into batches, extract key points +
    strengths + gaps per batch, then reduce into the three-section summary.
  - Never does a similarity search — we own the whole document.
  - Falls back to a deterministic placeholder (same section shape) if the LLM is
    unavailable or the API key is not configured.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from app.config import get_settings
from app.llm.client import chat_json
from app.vectors.repo import StoredChunk

_LogCall = Callable[[dict[str, Any]], None] | None

# Rough character budget per LLM call before we switch to map-reduce.
# ~4 chars/token, 4000 tokens of context headroom = ~16 000 chars.
_SINGLE_PASS_CHARS = 16_000

# Characters per group in map-reduce batching. Larger batch = fewer MAP calls
# for very large documents (we no longer cap the number of batches, so this is
# the main lever keeping cost bounded while still covering the whole document).
_BATCH_CHARS = 12_000

# Max MAP calls in flight at once. A large document (e.g. 500+ pages → ~70+
# batches) would otherwise run sequentially and blow the request timeout. Running
# them concurrently keeps full-document coverage but bounds wall-clock to roughly
# ceil(batches / _MAP_CONCURRENCY) round-trips. Kept modest to stay within OpenAI
# rate limits (tenacity still retries on 429s).
_MAP_CONCURRENCY = 8


async def one_liner(
    chunks: list[StoredChunk],
    *,
    file_name: str,
    plan_label: str,
    plan_category: str,
    log_call: _LogCall = None,
) -> str:
    """Generate a <=2000-char plain-English summary (all major points) for this document.

    Args:
        chunks:        ALL chunks for this attachment (from get_all_chunks).
        file_name:     Original file name (context for the LLM).
        plan_label:    The plan's label (grounding context).
        plan_category: coop | bcp | compliance.
        log_call:      Optional calllog.append callback.

    Returns:
        <=2000-char plain-English summary covering all major points.  Never empty
        (falls back to a deterministic description if the LLM fails).
    """
    settings = get_settings()
    if not settings.openai_api_key:
        return _fallback(file_name, plan_label, plan_category)

    combined_text = "\n".join(c.text for c in sorted(chunks, key=lambda c: c.chunk_index))
    combined_text = combined_text.strip()

    if not combined_text:
        return _fallback(file_name, plan_label, plan_category)

    if len(combined_text) <= _SINGLE_PASS_CHARS:
        # Single-pass: send the full text.
        raw = await _call_one_liner(
            combined_text, file_name, plan_label, plan_category, log_call=log_call
        )
    else:
        # Map-reduce: batch -> phrases -> combine.
        raw = await _map_reduce(
            combined_text, file_name, plan_label, plan_category, log_call=log_call
        )

    summary = str(raw).strip()
    if not summary:
        return _fallback(file_name, plan_label, plan_category)
    return summary[:2000]


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are a continuity-of-operations reviewer for the Ready2Go platform. You are "
    "reviewing one plan/document and how it prepares for or responds to an event. "
    "Return ONLY valid JSON: {\"summary\": \"<plain text, no markdown, max 1800 chars>\"}. "
    "The summary MUST contain exactly these three labeled sections, each on its own line "
    "and separated by a blank line, in this order:\n"
    "Overview: 3-5 sentences on what this document is, the scope it covers, and the "
    "concrete actions it describes for the event — whether already taken or planned "
    "(procedures, roles/responsibilities, timelines, systems/resources, coordination).\n"
    "What went well: 2-4 sentences (or short clauses) on the genuine strengths — what "
    "this plan covers thoroughly or handles effectively.\n"
    "Areas for improvement: 2-4 sentences on concrete gaps, missing elements, or weak "
    "spots that would benefit from attention.\n"
    "Keep every point grounded ONLY in the provided text — do not invent specifics. "
    "Do NOT mention any score, rating, status, percentage, or words like compliant / "
    "non-compliant / under review. Do NOT mention the file name. No marketing language. "
    "Keep the three section labels exactly as written above."
)


async def _call_one_liner(
    text: str,
    file_name: str,
    plan_label: str,
    plan_category: str,
    log_call: _LogCall = None,
) -> str:
    result = await chat_json(
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": (
                f"Plan: {plan_label} ({plan_category})\n"
                f"File: {file_name}\n\n"
                f"Document text:\n{text[:_SINGLE_PASS_CHARS]}"
            )},
        ],
        fallback={"summary": ""},
        max_tokens=700,
        log_call=log_call,
    )
    return result.get("summary", "")


async def _map_reduce(
    text: str,
    file_name: str,
    plan_label: str,
    plan_category: str,
    log_call: _LogCall = None,
) -> str:
    # MAP: chunk text into batches, get a short phrase per batch.
    # No batch cap — every part of the document is summarised so the final
    # summary reflects the WHOLE document, not just its opening. Cost stays
    # bounded by using a larger batch size (fewer, bigger MAP calls).
    batches = [text[i:i + _BATCH_CHARS] for i in range(0, len(text), _BATCH_CHARS)]

    # Run the MAP calls with bounded concurrency. Sequential awaits would make a
    # large document (~70+ batches) exceed the request timeout; a semaphore keeps
    # us within OpenAI rate limits while collapsing wall-clock time. asyncio.gather
    # preserves order, so the reduced summary still follows the document's flow.
    semaphore = asyncio.Semaphore(_MAP_CONCURRENCY)

    async def _summarise_batch(batch: str) -> str:
        async with semaphore:
            r = await chat_json(
                messages=[
                    {"role": "system", "content":
                        "From this excerpt of a continuity/response plan, extract the key "
                        "points it covers, any notable strengths (things handled well), and "
                        "any gaps or missing elements. 1-3 short sentences. "
                        "Return JSON: {\"phrase\": \"<text>\"}"},
                    {"role": "user", "content": batch},
                ],
                fallback={"phrase": ""},
                max_tokens=200,
                log_call=log_call,
            )
            return r.get("phrase", "").strip()

    results = await asyncio.gather(*(_summarise_batch(b) for b in batches))
    phrases = [p for p in results if p]

    if not phrases:
        return ""

    combined_phrases = " ".join(phrases)
    # REDUCE: combine phrases into one sentence.
    r = await chat_json(
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": (
                f"Plan: {plan_label} ({plan_category})\n"
                f"File: {file_name}\n\n"
                f"Combined document phrases:\n{combined_phrases}"
            )},
        ],
        fallback={"summary": ""},
        max_tokens=700,
        log_call=log_call,
    )
    return r.get("summary", "")


def _fallback(file_name: str, plan_label: str, plan_category: str) -> str:
    """Deterministic placeholder when the LLM is unavailable.

    Keeps the same three-section shape so the UI renders consistently; the real
    strengths/improvements are filled in on the next successful run.
    """
    cat_map = {"coop": "COOP", "bcp": "BCP", "compliance": "Compliance"}
    cat = cat_map.get(plan_category, "continuity")
    return (
        f"Overview: A {cat} document filed under the {plan_label} plan. "
        "The document has been stored, but a detailed content review is not yet "
        "available.\n\n"
        "What went well: Pending — a full review will be generated on the next "
        "successful run.\n\n"
        "Areas for improvement: Pending — a full review will be generated on the "
        "next successful run."
    )[:2000]
