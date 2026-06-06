"""Config-driven score bands, weights, and LLM-judge gate.

All thresholds and weights come from settings so they can be tuned without
a code change.  This module is the single source of truth for:
  - signal weights (must sum to ~1.0)
  - status banding (score -> Compliant / Under Review / Non-Compliant)
  - LLM judge band (borderline range that triggers an optional AI tie-break)
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings


@dataclass(frozen=True)
class Weights:
    """Signal weights used by the composite scorer.

    Sum should be ~1.0.  Loaded once from settings.
    """
    content: float
    name: float
    quality: float
    duplication: float


@dataclass(frozen=True)
class Bands:
    """Status banding thresholds (0-100 integer scale).

    score >= in_sync   -> "Compliant"
    score >= reviewing -> "Under Review"
    else               -> "Non-Compliant"
    """
    in_sync: int
    reviewing: int


@dataclass(frozen=True)
class JudgeBand:
    """Score range [low, high] that triggers the optional LLM judge call."""
    low: int
    high: int

    def contains(self, score: int) -> bool:
        return self.low <= score <= self.high


def get_weights() -> Weights:
    s = get_settings()
    return Weights(
        content=s.weight_content,
        name=s.weight_name,
        quality=s.weight_quality,
        duplication=s.weight_duplication,
    )


def get_bands() -> Bands:
    s = get_settings()
    return Bands(in_sync=s.band_in_sync, reviewing=s.band_reviewing)


def get_judge_band() -> JudgeBand:
    """Parse "60,72" string from settings into a JudgeBand."""
    s = get_settings()
    try:
        low_s, high_s = s.llm_judge_band.split(",")
        return JudgeBand(low=int(low_s.strip()), high=int(high_s.strip()))
    except (ValueError, AttributeError):
        return JudgeBand(low=60, high=72)


def score_to_status(score: int, bands: Bands | None = None) -> str:
    """Map an integer score 0-100 to an integrity status string.

    Returns one of: "Compliant", "Under Review", "Non-Compliant".
    These exact strings are the contract with the Next.js UI.
    """
    b = bands or get_bands()
    if score >= b.in_sync:
        return "Compliant"
    if score >= b.reviewing:
        return "Under Review"
    return "Non-Compliant"
