"""Five independent integrity signals, each in [0, 1].

All functions are pure: they take pre-computed numbers and return a float.
No I/O, no side effects — fully unit-testable without mocking anything.

Signal             Weight  What it measures
---------          ------  ----------------
content_alignment  0.40    Doc centroid vs plan-context embedding (cosine).
name_alignment     0.15    Hybrid search score of filename vs plan label.
category_fit       0.20    Centroid vs declared-category prototype; penalised
                           if a different category scores higher (mis-filed).
extraction_quality 0.15    Chars/pages extracted; scan-only / empty penalty.
duplication        0.10    Nearest-sibling distance; near-dups score low.

Weights live in config (thresholds.py); this module does not read config.
"""

from __future__ import annotations

import math

from app.ingest.extract import ExtractionQuality

# ---------------------------------------------------------------------------
# 1. Content alignment
# ---------------------------------------------------------------------------

def content_alignment(
    doc_centroid: list[float] | None,
    plan_context_vector: list[float] | None,
) -> float:
    """Cosine similarity between doc centroid and plan-context embedding.

    The plan-context vector is the embedding of "<label> <overview> <steps>".
    Both vectors should already be L2-normalised (text-embedding-3-small returns
    normalised vectors); if not we normalise here.
    Returns 0.0 if either vector is missing.
    """
    if doc_centroid is None or plan_context_vector is None:
        return 0.0
    sim = _cosine(doc_centroid, plan_context_vector)
    return float(max(0.0, sim))  # clamp; negative cosine = Deviation territory


# ---------------------------------------------------------------------------
# 2. Name alignment
# ---------------------------------------------------------------------------

def name_alignment(hybrid_score: float | None) -> float:
    """Convert a Weaviate hybrid search score to [0, 1].

    Weaviate hybrid scores are in [0, 1] already (higher = better match).
    Returns 0 if no score available.
    """
    if hybrid_score is None:
        return 0.0
    return float(max(0.0, min(1.0, hybrid_score)))


# ---------------------------------------------------------------------------
# 3. Category fit
# ---------------------------------------------------------------------------

def category_fit(
    declared_category: str,
    prototype_sims: dict[str, float],
) -> float:
    """How well the doc fits its declared category vs other categories.

    Logic:
    - Base score = similarity of doc centroid to the declared category prototype.
    - Penalty: if a DIFFERENT category prototype scores higher (the doc looks
      like it belongs elsewhere), subtract a penalty proportional to the gap.
    - Returns 0.0 if no prototypes are available (graceful degradation).
    """
    if not prototype_sims:
        return 0.5  # neutral when prototypes not seeded yet

    declared_sim = prototype_sims.get(declared_category, 0.0)
    other_sims = [v for k, v in prototype_sims.items() if k != declared_category]

    if not other_sims:
        # Only one category prototype exists — use its similarity directly.
        return float(max(0.0, min(1.0, declared_sim)))

    best_other = max(other_sims)
    if best_other > declared_sim:
        # Mis-filed: penalise proportionally to the gap.
        gap = best_other - declared_sim
        penalised = max(0.0, declared_sim - gap * 0.5)
        return float(penalised)

    return float(max(0.0, min(1.0, declared_sim)))


# ---------------------------------------------------------------------------
# 4. Extraction quality
# ---------------------------------------------------------------------------

# Thresholds for "good" extraction — rough empirical targets.
_MIN_CHARS_GOOD = 500     # below this, quality degrades gradually
_MIN_PAGES_GOOD = 1       # at least one page/row should be present


def extraction_quality(quality: ExtractionQuality) -> float:
    """Deterministic quality signal from the extraction metadata.

    - Empty or scan-only: severe penalty (0.05-0.15).
    - Very short text: linear ramp from 0.1 to 0.8 up to MIN_CHARS_GOOD.
    - Normal extraction: 0.8 to 1.0 depending on depth.

    This replaces the old "score 35-55 conservatively" guess with a real number.
    """
    if quality.is_empty:
        return 0.05
    if quality.is_scan_only:
        return 0.10

    chars = quality.chars
    if chars < _MIN_CHARS_GOOD:
        # Linear ramp: 0.1 at 0 chars, 0.8 at MIN_CHARS_GOOD chars.
        return float(0.1 + (chars / _MIN_CHARS_GOOD) * 0.7)

    # Beyond the minimum: diminishing returns toward 1.0.
    # log curve: at 1x good = 0.80, at 10x good = ~0.93, at 100x = ~1.0.
    extra = math.log1p(chars / _MIN_CHARS_GOOD) / math.log1p(100)
    return float(min(1.0, 0.80 + extra * 0.20))


# ---------------------------------------------------------------------------
# 5. Duplication
# ---------------------------------------------------------------------------

def duplication(nearest_sibling_distance: float | None) -> float:
    """Score how unique this file is among its siblings in the same plan.

    *nearest_sibling_distance* is Weaviate cosine distance (0=identical, 2=opposite).
    - No siblings → 1.0 (perfectly unique by default).
    - Very similar sibling (distance near 0) → low score (likely duplicate).
    - Dissimilar siblings → score approaches 1.0.
    """
    if nearest_sibling_distance is None:
        return 1.0  # no siblings — treat as unique

    # Map distance [0, 1] to score [0, 1]:
    #   distance 0.0 (identical)   -> score 0.0
    #   distance 0.3 (very similar)-> score ~0.3
    #   distance 0.5 (distinct)    -> score ~0.7
    #   distance 1.0 (orthogonal)  -> score 1.0
    dist = max(0.0, min(1.0, nearest_sibling_distance))
    return float(dist)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors (works even if not normalised)."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
