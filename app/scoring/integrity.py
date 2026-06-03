"""Composite integrity scorer.

Pipeline:
  1. Weighted sum of the five signals -> raw score 0-100.
  2. Hard overrides (scan-only, mis-filed) that can cap or force status.
  3. Optional LLM judge for scores in the borderline band (60-72).
  4. Return {status, score, components}.

The status strings are the exact values expected by Next.js:
  "In Sync" | "Reviewing" | "Deviation Found"
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ingest.extract import ExtractionQuality
from app.scoring.thresholds import (
    Bands,
    JudgeBand,
    Weights,
    get_bands,
    get_judge_band,
    get_weights,
    score_to_status,
)

# Threshold for "category mismatch is significant enough to force Deviation Found".
# If the best OTHER category prototype similarity exceeds declared by this margin
# the file is considered mis-filed.
_MISFILED_GAP = 0.20


@dataclass
class SignalInputs:
    """All pre-computed signals fed into the composite scorer."""
    content: float       # [0,1]
    name: float          # [0,1]
    category: float      # [0,1]
    quality: float       # [0,1]
    duplication: float   # [0,1]


@dataclass
class IntegrityResult:
    """The output of the composite scorer."""
    status: str          # "In Sync" | "Reviewing" | "Deviation Found"
    score: int           # 0-100
    components: dict[str, int]  # per-signal scores for explainability
    used_llm_judge: bool = False


def compute(
    signals: SignalInputs,
    quality: ExtractionQuality,
    *,
    declared_category: str = "",
    prototype_sims: dict[str, float] | None = None,
    weights: Weights | None = None,
    bands: Bands | None = None,
    judge_band: JudgeBand | None = None,
) -> IntegrityResult:
    """Compute the composite integrity score and status.

    Args:
        signals:           The five pre-computed signal floats.
        quality:           Extraction quality metadata (for hard overrides).
        declared_category: The plan's stored category (coop|bcp|compliance).
        prototype_sims:    {category: similarity} from vectors/repo.
        weights/bands/judge_band: Override from defaults (useful in tests).
    """
    w = weights or get_weights()
    b = bands or get_bands()
    j = judge_band or get_judge_band()

    # 1. Weighted sum -> 0-100
    raw = (
        signals.content    * w.content
        + signals.name     * w.name
        + signals.category * w.category
        + signals.quality  * w.quality
        + signals.duplication * w.duplication
    )
    score = round(max(0.0, min(1.0, raw)) * 100)

    components = {
        "content":     round(signals.content    * 100),
        "name":        round(signals.name        * 100),
        "category":    round(signals.category    * 100),
        "quality":     round(signals.quality     * 100),
        "duplication": round(signals.duplication * 100),
    }

    # 2a. Hard override — scan-only or empty: cap at Reviewing + score <= 45.
    if quality.is_empty or quality.is_scan_only:
        score = min(score, 45)
        status = "Reviewing" if score >= b.reviewing else "Deviation Found"
        return IntegrityResult(status=status, score=score, components=components)

    # 2b. Hard override — strong category mismatch -> Deviation Found.
    if _is_misfiled(declared_category, prototype_sims):
        score = min(score, b.reviewing - 1)  # push below Reviewing threshold
        return IntegrityResult(
            status="Deviation Found", score=score, components=components
        )

    # 3. Normal banding.
    status = score_to_status(score, b)

    # 4. Borderline: flag that LLM judge could be applied by the caller.
    used_judge = j.contains(score)

    return IntegrityResult(
        status=status,
        score=score,
        components=components,
        used_llm_judge=used_judge,
    )


def _is_misfiled(
    declared_category: str,
    prototype_sims: dict[str, float] | None,
) -> bool:
    """True if a different category prototype is significantly more similar."""
    if not prototype_sims or not declared_category:
        return False
    declared_sim = prototype_sims.get(declared_category, 0.0)
    other_sims = [v for k, v in prototype_sims.items() if k != declared_category]
    if not other_sims:
        return False
    return max(other_sims) - declared_sim > _MISFILED_GAP
