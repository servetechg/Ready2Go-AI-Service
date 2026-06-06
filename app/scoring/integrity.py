"""Composite integrity scorer.

Pipeline:
  1. Weighted sum of the four signals -> raw score 0-100.
  2. Hard override (scan-only / empty) that can cap or force status.
  3. Optional LLM judge for scores in the borderline band (60-72).
  4. Return {status, score, components}.

The status strings are the exact values expected by Next.js:
  "Compliant" | "Under Review" | "Non-Compliant"
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


@dataclass
class SignalInputs:
    """All pre-computed signals fed into the composite scorer."""
    content: float       # [0,1]
    name: float          # [0,1]
    quality: float       # [0,1]
    duplication: float   # [0,1]


@dataclass
class IntegrityResult:
    """The output of the composite scorer."""
    status: str          # "Compliant" | "Under Review" | "Non-Compliant"
    score: int           # 0-100
    components: dict[str, int]  # per-signal scores for explainability
    used_llm_judge: bool = False


def compute(
    signals: SignalInputs,
    quality: ExtractionQuality,
    *,
    weights: Weights | None = None,
    bands: Bands | None = None,
    judge_band: JudgeBand | None = None,
) -> IntegrityResult:
    """Compute the composite integrity score and status.

    Args:
        signals:           The four pre-computed signal floats.
        quality:           Extraction quality metadata (for the hard override).
        weights/bands/judge_band: Override from defaults (useful in tests).
    """
    w = weights or get_weights()
    b = bands or get_bands()
    j = judge_band or get_judge_band()

    # 1. Weighted sum -> 0-100
    raw = (
        signals.content    * w.content
        + signals.name     * w.name
        + signals.quality  * w.quality
        + signals.duplication * w.duplication
    )
    score = round(max(0.0, min(1.0, raw)) * 100)

    components = {
        "content":     round(signals.content    * 100),
        "name":        round(signals.name        * 100),
        "quality":     round(signals.quality     * 100),
        "duplication": round(signals.duplication * 100),
    }

    # 2. Hard override — scan-only or empty: cap at Under Review + score <= 45.
    if quality.is_empty or quality.is_scan_only:
        score = min(score, 45)
        status = "Under Review" if score >= b.reviewing else "Non-Compliant"
        return IntegrityResult(status=status, score=score, components=components)

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
