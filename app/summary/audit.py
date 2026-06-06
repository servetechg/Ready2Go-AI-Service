"""Bounded vault-wide audit summary.

Input:
  - Rolling ai_audit_state for this tenant (aggregate counters).
  - A capped sample of stored one-liners from recent / notable docs.

The LLM only ever sees aggregate numbers + a small sample — never the whole
corpus.  Cost is O(1) regardless of how many documents exist.

derive_posture() is deterministic (no LLM): mirrors the rules from
PROJECT_CONTEXT §6.5 verbatim so the frontend gets consistent behaviour.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.config import get_settings
from app.llm.client import chat_json
from app.schemas import AuditSummaryResponse

_LogCall = Callable[[dict[str, Any]], None] | None


async def build(
    state: dict[str, Any],
    sample: list[dict[str, Any]],
    *,
    tenant_key: str,
    log_call: _LogCall = None,
) -> AuditSummaryResponse:
    """Build a bounded AuditSummaryResponse.

    Args:
        state:      The ai_audit_state doc for this tenant.
        sample:     Capped list of per-doc summary dicts
                    [{fileName, status, score, summary, planId}].
        tenant_key: For logging.
        log_call:   Optional calllog.append callback.
    """
    settings = get_settings()
    posture = derive_posture(state)
    counts = state.get("counts", {})
    integrity = state.get("integrity", {})
    score_sum = state.get("scoreSum", 0)
    score_count = state.get("scoreCount", 0)
    average_score = round(score_sum / score_count) if score_count else 0

    analyzed = (
        integrity.get("inSync", 0)
        + integrity.get("reviewing", 0)
        + integrity.get("deviation", 0)
    )
    totals = {
        "plans": (
            counts.get("coop", 0)
            + counts.get("bcp", 0)
            + counts.get("compliance", 0)
        ),
        "attachments": sum(integrity.values()),
        "analyzed": analyzed,
    }

    if not settings.openai_api_key or totals["plans"] == 0:
        summary, findings = _fallback_text(totals, posture)
        return AuditSummaryResponse(
            summary=summary,
            findings=findings,
            posture=posture,
            average_score=average_score,
        )

    summary, findings = await _llm_summary(
        totals=totals,
        integrity=integrity,
        counts=counts,
        average_score=average_score,
        sample=sample,
        log_call=log_call,
    )

    return AuditSummaryResponse(
        summary=summary,
        findings=findings,
        posture=posture,
        average_score=average_score,
    )


def derive_posture(state: dict) -> str:
    """Deterministic posture from rolling state.

    Mirrors PROJECT_CONTEXT §6.5 verbatim:
      - 'At Risk' if no plans, any deviations, avg <55, or nothing analyzed.
      - 'Steady' if any Reviewing or avg <75.
      - 'Resilient' otherwise.
    """
    integrity  = state.get("integrity", {})
    score_sum  = state.get("scoreSum", 0)
    score_count = state.get("scoreCount", 0)
    avg = round(score_sum / score_count) if score_count else 0

    counts = state.get("counts", {})
    total_plans = counts.get("coop", 0) + counts.get("bcp", 0) + counts.get("compliance", 0)

    if total_plans == 0:
        return "At Risk"

    deviations = integrity.get("deviation", 0)
    reviewing  = integrity.get("reviewing", 0)
    analyzed = (
        integrity.get("inSync", 0)
        + integrity.get("reviewing", 0)
        + integrity.get("deviation", 0)
    )

    if deviations > 0 or avg < 55 or analyzed == 0:
        return "At Risk"
    if reviewing > 0 or avg < 75:
        return "Steady"
    return "Resilient"


# ---------------------------------------------------------------------------
# LLM narrative
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are a Continuity-of-Operations auditor for the Ready2Go platform. "
    "Given aggregate statistics and a sample of document summaries, produce "
    "ONLY valid JSON: "
    "{\"summary\": \"<detailed narrative, 4-6 sentences, max 1400 chars, no markdown>\", "
    "\"findings\": [\"<actionable bullet that explains the issue and its impact, "
    "max 350 chars>\", ...]} "
    "Rules: 4 to 8 findings, ordered by urgency; each finding must explain what the "
    "issue is, which plans/categories it affects, and why it matters. "
    "The summary should describe overall posture, coverage across categories, the "
    "balance of Compliant vs Under Review vs Non-Compliant files, and the most "
    "important risks. "
    "Highlight: coverage gaps, low scores, Non-Compliant files, plans without "
    "steps or attachments, missing analysis. "
    "Do NOT give legal advice or recommend actions outside continuity management."
)


async def _llm_summary(
    *,
    totals: dict[str, Any],
    integrity: dict[str, Any],
    counts: dict[str, Any],
    average_score: int,
    sample: list[dict[str, Any]],
    log_call: _LogCall = None,
) -> tuple[str, list[str]]:
    """Call the LLM to produce summary + findings."""
    payload = {
        "totals":             totals,
        "averageScore":       average_score,
        "categoryCounts":     counts,
        "integrityBreakdown": integrity,
        "sampleDocs":         sample,   # full list from caller; capped or unlimited
    }

    import json
    result = await chat_json(
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": json.dumps(payload)},
        ],
        fallback={"summary": "", "findings": []},
        max_tokens=1500,   # raised to handle larger sample lists
        log_call=log_call,
    )

    raw_summary = str(result.get("summary", "")).strip()[:1500]
    raw_findings = result.get("findings", [])
    if not isinstance(raw_findings, list):
        raw_findings = []
    findings = [str(f).strip()[:350] for f in raw_findings if str(f).strip()][:8]

    if not raw_summary:
        raw_summary, findings = _fallback_text(totals, "")

    return raw_summary, findings


def _fallback_text(totals: dict, posture: str) -> tuple[str, list[str]]:
    plans = totals.get("plans", 0)
    attachments = totals.get("attachments", 0)
    if plans == 0:
        return (
            "No continuity plans yet — upload documents to begin "
            "tracking COOP/BCP/Compliance posture.",
            [],
        )
    summary = (
        f"Continuity vault holds {plans} plan{'s' if plans != 1 else ''} "
        f"and {attachments} attachment{'s' if attachments != 1 else ''}. "
        "Configure OPENAI_API_KEY for a tailored audit."
    )
    return summary[:1500], []
