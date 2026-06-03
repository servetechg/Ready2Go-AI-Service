"""Token-aware text chunking bounded by MAX_CHUNKS_PER_DOC.

Strategy:
  - Encode the full text with tiktoken cl100k_base (same tokenizer as
    text-embedding-3-small, so token counts are accurate for the embedding API).
  - Slide a window of `chunk_tokens` tokens, stepping `chunk_tokens - overlap`
    tokens each time so consecutive chunks share `overlap` tokens of context.
  - Stop once `max_chunks` chunks are produced, discarding the tail.
    This bounds embedding cost for large documents.

Why overlap?  Sentences and ideas near a boundary appear in BOTH adjacent chunks,
so embedding the centroid captures the full document — not just pieces that happen
to land cleanly within a window.
"""

from __future__ import annotations

from dataclasses import dataclass

import tiktoken

# Shared encoder — cl100k_base is the tokenizer for text-embedding-3-small.
_ENCODING = tiktoken.get_encoding("cl100k_base")

# Default window size and overlap (tokens).  Tune via caller if needed.
CHUNK_TOKENS = 500
OVERLAP_TOKENS = 50


@dataclass
class Chunk:
    """One chunk ready for embedding."""
    index: int        # 0-based position in the document
    text: str         # decoded text of this chunk
    token_count: int  # number of tokens (for budget tracking)


def chunk(
    text: str,
    *,
    max_chunks: int,
    chunk_tokens: int = CHUNK_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[Chunk]:
    """Split *text* into overlapping token-bounded chunks.

    Args:
        text:          The full extracted document text.
        max_chunks:    Hard upper bound on chunks produced (from settings).
        chunk_tokens:  Target tokens per chunk (default 500).
        overlap_tokens: Tokens shared between consecutive chunks (default 50).

    Returns:
        List of Chunk objects, at most max_chunks long.
        Empty if text is empty or whitespace-only.
    """
    text = text.strip()
    if not text:
        return []

    # Ensure overlap is smaller than window (guards against bad config).
    overlap_tokens = min(overlap_tokens, chunk_tokens - 1)
    step = chunk_tokens - overlap_tokens  # tokens advanced per chunk

    token_ids = _ENCODING.encode(text)
    total = len(token_ids)

    if total == 0:
        return []

    chunks: list[Chunk] = []
    start = 0

    while start < total and len(chunks) < max_chunks:
        end = min(start + chunk_tokens, total)
        window_ids = token_ids[start:end]
        chunk_text = _ENCODING.decode(window_ids)
        chunks.append(Chunk(
            index=len(chunks),
            text=chunk_text,
            token_count=len(window_ids),
        ))
        if end == total:
            break
        start += step

    return chunks
