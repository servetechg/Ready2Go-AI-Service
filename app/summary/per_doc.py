"""Per-document <=280-char one-liner.

Approach (D7 — full-document, no retrieval subset):
  - Use the complete chunk set (all of this document's chunks from Weaviate).
  - If the chunks fit in the LLM context budget, send them all at once.
  - If too many chunks, map-reduce: group into batches, summarise each batch
    to a phrase, then reduce the phrases to one sentence.
  - Never does a similarity search — we own the whole document.
  - Falls back to a deterministic string if the LLM is unavailable or the
    API key is not configured.
"""

from __future__ import annotations

from app.config import get_settings
from app.llm.client import chat_json
from app.vectors.repo import StoredChunk

# Rough character budget per LLM call before we switch to map-reduce.
# ~4 chars/token, 4000 tokens of context headroom = ~16 000 chars.
_SINGLE_PASS_CHARS = 16_000

# Characters per group in map-reduce batching.
_BATCH_CHARS = 6_000


async def one_liner(
    chunks: list[StoredChunk],
    *,
    file_name: str,
    plan_label: str,
    plan_category: str,
    log_call=None,
) -> str:
    """Generate a <=280-char plain-English one-liner for this document.

    Args:
        chunks:        ALL chunks for this attachment (from get_all_chunks).
        file_name:     Original file name (context for the LLM).
        plan_label:    The plan's label (grounding context).
        plan_category: coop | bcp | compliance.
        log_call:      Optional calllog.append callback.

    Returns:
        <=280-char plain-English string.  Never empty (falls back to a
        deterministic description if the LLM fails).
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
    return summary[:280]


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are a continuity-of-operations document analyst for the Ready2Go platform. "
    "Return ONLY valid JSON: {\"summary\": \"<plain English, max 220 chars, no markdown>\"} "
    "Describe what this specific document IS and does — factually, grounded in the text. "
    "No marketing language. Do not mention the file name."
)


async def _call_one_liner(
    text: str,
    file_name: str,
    plan_label: str,
    plan_category: str,
    log_call=None,
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
        max_tokens=120,
        log_call=log_call,
    )
    return result.get("summary", "")


async def _map_reduce(
    text: str,
    file_name: str,
    plan_label: str,
    plan_category: str,
    log_call=None,
) -> str:
    # MAP: chunk text into batches, get a short phrase per batch.
    batches = [text[i:i + _BATCH_CHARS] for i in range(0, len(text), _BATCH_CHARS)]
    phrases = []
    for batch in batches[:8]:   # cap at 8 batches (enough for very large docs)
        r = await chat_json(
            messages=[
                {"role": "system", "content":
                    "Summarise this excerpt in 1-2 sentences. "
                    "Return JSON: {\"phrase\": \"<text>\"}"},
                {"role": "user", "content": batch},
            ],
            fallback={"phrase": ""},
            max_tokens=80,
            log_call=log_call,
        )
        p = r.get("phrase", "").strip()
        if p:
            phrases.append(p)

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
        max_tokens=120,
        log_call=log_call,
    )
    return r.get("summary", "")


def _fallback(file_name: str, plan_label: str, plan_category: str) -> str:
    """Deterministic description when the LLM is unavailable."""
    cat_map = {"coop": "COOP", "bcp": "BCP", "compliance": "Compliance"}
    cat = cat_map.get(plan_category, "continuity")
    return f"{cat} artifact '{file_name}' filed under {plan_label} — pending analysis."[:280]
