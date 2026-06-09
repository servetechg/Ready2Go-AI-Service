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
) -> tuple[AuditSummaryResponse, bool]:
    """Build a bounded AuditSummaryResponse.

    Returns:
        (response, llm_ok) — *llm_ok* is True only when the LLM actually produced
        a narrative. It is False when the deterministic fallback text was used
        (no API key, no plans, or the LLM call failed/empty). The caller uses
        this to decide whether to retry with a smaller fallback sample.

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
        integrity.get("compliant", 0)
        + integrity.get("underReview", 0)
        + integrity.get("nonCompliant", 0)
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
        return (
            AuditSummaryResponse(
                summary=summary,
                findings=findings,
                posture=posture,
                average_score=average_score,
            ),
            False,  # deterministic fallback, not an LLM narrative
        )

    summary, findings, llm_ok = await _llm_summary(
        totals=totals,
        counts=counts,
        sample=sample,
        log_call=log_call,
    )

    return (
        AuditSummaryResponse(
            summary=summary,
            findings=findings,
            posture=posture,
            average_score=average_score,
        ),
        llm_ok,
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

    deviations = integrity.get("nonCompliant", 0)
    reviewing  = integrity.get("underReview", 0)
    analyzed = (
        integrity.get("compliant", 0)
        + integrity.get("underReview", 0)
        + integrity.get("nonCompliant", 0)
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
    "You are a Continuity-of-Operations reviewer for the Ready2Go platform preparing "
    "a demo-ready review of an organisation's plans. You are given category coverage "
    "counts and a list of analyzed documents in `sampleDocs`; each entry has a "
    "`fileName`, `planId`, and a `summary` describing the document's content and how it "
    "prepares for or responds to an event. USE these per-document summaries to ground "
    "your narrative and findings in the real content — name and explain specific "
    "documents/plans where relevant. "
    "Produce ONLY valid JSON: "
    "{\"summary\": \"<narrative, 4-6 sentences, max 1400 chars, no markdown>\", "
    "\"findings\": [\"<bullet, max 350 chars>\", ...]} "
    "The summary should describe what the plans COLLECTIVELY cover across the "
    "organisation, the response actions they define, what is handled well overall, and "
    "where the biggest areas for improvement are. "
    "`findings` must be 4 to 8 BALANCED bullets — include both strengths (what went "
    "well across the plans) AND areas for improvement (gaps, missing elements, coverage "
    "holes). Each bullet explains the point and which plans/documents/categories it "
    "relates to. "
    "Do NOT mention any score, rating, status, percentage, or words like compliant / "
    "non-compliant / under review — describe content and readiness, not grades. "
    "Do NOT give legal advice or recommend actions outside continuity management."
)


async def _llm_summary(
    *,
    totals: dict[str, Any],
    counts: dict[str, Any],
    sample: list[dict[str, Any]],
    log_call: _LogCall = None,
) -> tuple[str, list[str], bool]:
    """Call the LLM to produce summary + findings.

    Returns (summary, findings, llm_ok). llm_ok is False when the LLM call failed
    or returned an empty summary (so the deterministic fallback text was used) —
    the caller treats that as a signal to retry with a smaller fallback sample.
    """
    # Deliberately omit averageScore / integrityBreakdown so the narrative is driven
    # by document CONTENT and coverage — not scores/verdicts. Also strip per-doc
    # status/score so the model only sees what each document is about.
    content_docs = [
        {"fileName": d.get("fileName"), "planId": d.get("planId"), "summary": d.get("summary", "")}
        for d in sample
    ]
    payload = {
        "totals":         totals,
        "categoryCounts": counts,
        "sampleDocs":     content_docs,   # content only; status/score removed
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

    llm_ok = bool(raw_summary)
    if not raw_summary:
        raw_summary, findings = _fallback_text(totals, "")

    return raw_summary, findings, llm_ok


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
