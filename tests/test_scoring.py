"""Unit tests for the scoring pipeline (M4-M6).

All pure-function tests — no I/O, no mocking required.
"""

from __future__ import annotations

import pytest
from app.ingest.extract import ExtractionQuality
from app.scoring import signals as sig
from app.scoring.integrity import IntegrityResult, SignalInputs, compute
from app.scoring.thresholds import Bands, JudgeBand, Weights, score_to_status

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _quality(
    chars: int = 1000,
    pages_or_rows: int = 2,
    is_scan_only: bool = False,
    is_empty: bool = False,
) -> ExtractionQuality:
    return ExtractionQuality(
        chars=chars,
        pages_or_rows=pages_or_rows,
        is_scan_only=is_scan_only,
        is_empty=is_empty,
    )


def _unit_vec(dims: int = 4, value: float = 1.0) -> list[float]:
    """Return a normalised vector with the same value in each dimension."""
    import math
    v = [value] * dims
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


def _default_weights() -> Weights:
    return Weights(content=0.50, name=0.19, quality=0.19, duplication=0.12)


def _default_bands() -> Bands:
    return Bands(compliant=71, under_review=41)


def _default_judge() -> JudgeBand:
    return JudgeBand(low=60, high=72)


# ---------------------------------------------------------------------------
# signals.py — content_alignment
# ---------------------------------------------------------------------------

class TestContentAlignment:
    def test_identical_unit_vectors_return_one(self):
        v = _unit_vec()
        assert sig.content_alignment(v, v) == pytest.approx(1.0, abs=1e-5)

    def test_orthogonal_vectors_return_zero(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert sig.content_alignment(a, b) == pytest.approx(0.0, abs=1e-5)

    def test_negative_cosine_clamped_to_zero(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert sig.content_alignment(a, b) == 0.0

    def test_none_doc_centroid_returns_zero(self):
        assert sig.content_alignment(None, [1.0, 0.0]) == 0.0

    def test_none_plan_vector_returns_zero(self):
        assert sig.content_alignment([1.0, 0.0], None) == 0.0


# ---------------------------------------------------------------------------
# signals.py — name_alignment
# ---------------------------------------------------------------------------

class TestNameAlignment:
    def test_none_returns_zero(self):
        assert sig.name_alignment(None) == 0.0

    def test_perfect_score_one(self):
        assert sig.name_alignment(1.0) == 1.0

    def test_mid_score_passthrough(self):
        assert sig.name_alignment(0.65) == pytest.approx(0.65)

    def test_negative_clamped_to_zero(self):
        assert sig.name_alignment(-0.5) == 0.0

    def test_above_one_clamped(self):
        assert sig.name_alignment(1.5) == 1.0


# ---------------------------------------------------------------------------
# signals.py — extraction_quality
# ---------------------------------------------------------------------------

class TestExtractionQuality:
    def test_empty_doc_low_score(self):
        assert sig.extraction_quality(_quality(chars=0, is_empty=True)) < 0.2

    def test_scan_only_low_score(self):
        assert sig.extraction_quality(_quality(is_scan_only=True)) < 0.2

    def test_normal_doc_high_score(self):
        assert sig.extraction_quality(_quality(chars=5000)) > 0.8

    def test_score_in_range(self):
        for chars in [0, 10, 100, 500, 1000, 5000]:
            q = _quality(chars=chars, is_empty=chars == 0)
            s = sig.extraction_quality(q)
            assert 0.0 <= s <= 1.0, f"out of range for chars={chars}: {s}"


# ---------------------------------------------------------------------------
# signals.py — duplication
# ---------------------------------------------------------------------------

class TestDuplication:
    def test_no_siblings_perfect_uniqueness(self):
        assert sig.duplication(None) == 1.0

    def test_identical_sibling_zero_score(self):
        assert sig.duplication(0.0) == 0.0

    def test_distinct_sibling_high_score(self):
        assert sig.duplication(0.9) == pytest.approx(0.9)

    def test_distance_clamped_below_zero(self):
        assert sig.duplication(-0.1) == 0.0

    def test_distance_clamped_above_one(self):
        assert sig.duplication(1.5) == 1.0


# ---------------------------------------------------------------------------
# thresholds.py — score_to_status
# ---------------------------------------------------------------------------

class TestScoreToStatus:
    def setup_method(self):
        self.bands = _default_bands()

    def test_high_score_in_sync(self):
        assert score_to_status(90, self.bands) == "Compliant"

    def test_boundary_in_sync(self):
        assert score_to_status(71, self.bands) == "Compliant"

    def test_reviewing_range(self):
        assert score_to_status(55, self.bands) == "Under Review"

    def test_boundary_reviewing(self):
        assert score_to_status(41, self.bands) == "Under Review"

    def test_low_score_deviation(self):
        assert score_to_status(10, self.bands) == "Non-Compliant"

    def test_zero_deviation(self):
        assert score_to_status(0, self.bands) == "Non-Compliant"

    def test_hundred_in_sync(self):
        assert score_to_status(100, self.bands) == "Compliant"


# ---------------------------------------------------------------------------
# integrity.py — compute() composite scorer
# ---------------------------------------------------------------------------

class TestComputeScorer:
    def setup_method(self):
        self.weights = _default_weights()
        self.bands = _default_bands()
        self.judge = _default_judge()

    def _compute(self, signals: SignalInputs, **kwargs) -> IntegrityResult:
        return compute(
            signals,
            _quality(),
            weights=self.weights,
            bands=self.bands,
            judge_band=self.judge,
            **kwargs,
        )

    def test_all_perfect_signals_in_sync(self):
        s = SignalInputs(content=1.0, name=1.0, quality=1.0, duplication=1.0)
        result = self._compute(s)
        assert result.status == "Compliant"
        assert result.score == 100

    def test_all_zero_signals_deviation(self):
        s = SignalInputs(content=0.0, name=0.0, quality=0.0, duplication=0.0)
        result = self._compute(s)
        assert result.status == "Non-Compliant"
        assert result.score == 0

    def test_score_clamps_to_0_100(self):
        s = SignalInputs(content=1.5, name=1.5, quality=1.5, duplication=1.5)
        result = self._compute(s)
        assert 0 <= result.score <= 100

    def test_components_present(self):
        s = SignalInputs(content=0.8, name=0.6, quality=0.9, duplication=1.0)
        result = self._compute(s)
        expected_keys = {"content", "name", "quality", "duplication"}
        assert set(result.components.keys()) == expected_keys

    def test_scan_only_capped_at_45(self):
        s = SignalInputs(content=1.0, name=1.0, quality=1.0, duplication=1.0)
        result = compute(
            s,
            _quality(is_scan_only=True),
            weights=self.weights,
            bands=self.bands,
            judge_band=self.judge,
        )
        assert result.score <= 45
        assert result.status in {"Under Review", "Non-Compliant"}

    def test_empty_doc_capped_at_45(self):
        s = SignalInputs(content=1.0, name=1.0, quality=0.05, duplication=1.0)
        result = compute(
            s,
            _quality(chars=0, is_empty=True),
            weights=self.weights,
            bands=self.bands,
            judge_band=self.judge,
        )
        assert result.score <= 45

    def test_borderline_score_flags_llm_judge(self):
        # score around 65 should flag used_llm_judge
        s = SignalInputs(content=0.65, name=0.65, quality=0.65, duplication=0.65)
        result = self._compute(s)
        if 60 <= result.score <= 72:
            assert result.used_llm_judge is True

    def test_high_score_no_llm_judge(self):
        s = SignalInputs(content=1.0, name=1.0, quality=1.0, duplication=1.0)
        result = self._compute(s)
        assert result.used_llm_judge is False
