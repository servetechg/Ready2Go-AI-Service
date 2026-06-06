"""Aggregate continuity-audit endpoint.

POST /v1/audit/summary — on-demand bounded audit narrative.

Flow:
  1. Validate AuditSummaryRequest         (schemas.py)
  2. Read rolling ai_audit_state[tenant]  (store/aggregate.py)
  3. Build capped sample from notable list
  4. bounded reduce -> {summary, findings, posture, averageScore}
                                          (summary/audit.py)
  5. Mark state clean (dirty=False)       (store/aggregate.py)
  6. Return AuditSummaryResponse -> Next.js upserts continuityauditreports

The whole flow is wrapped in a guard: a Mongo read or LLM failure must NOT
500 the endpoint. On failure we return a deterministic fallback derived from
the request payload (which already carries the totals the UI computed) so the
caller always gets a usable response, and the cause is logged explicitly.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.schemas import AuditSummaryRequest, AuditSummaryResponse
from app.security import require_auth
from app.store import aggregate, calllog
from app.summary.audit import build, derive_posture

router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_auth)])

log = structlog.get_logger(__name__)


@router.post("/summary", response_model=AuditSummaryResponse)
async def summary(payload: AuditSummaryRequest) -> AuditSummaryResponse:
    """Return a bounded {summary, findings, posture, averageScore}.

    Never raises to the client: on any internal failure it logs the cause and
    returns a deterministic fallback built from the request payload.
    """
    tenant = (
        payload.tenant_context.tenant_key
        if payload.tenant_context
        else ""
    )

    try:
        # 2. Read rolling state.
        state = await run_in_threadpool(aggregate.read, tenant)

        # 3. Build sample for the audit LLM.
        #    AUDIT_SAMPLE_CAP=0  → pass ALL analyzed docs (full-corpus audit).
        #    AUDIT_SAMPLE_CAP=N  → pass worst-N from all_analyzed (sorted by score asc).
        #    Falls back to the legacy `notable` list for tenants analysed before
        #    all_analyzed was introduced.
        settings = get_settings()
        all_analyzed = state.get("all_analyzed", [])
        notable = state.get("notable", [])
        source = all_analyzed if all_analyzed else notable
        source_sorted = sorted(source, key=lambda x: x.get("score", 100))

        if settings.audit_sample_cap == 0:
            sample = source_sorted          # entire corpus
        else:
            sample = source_sorted[:settings.audit_sample_cap]

        def _log(row: dict) -> None:
            calllog.append(row)

        # 4. Build audit narrative (bounded or full-corpus depending on config).
        result = await build(
            state,
            sample,
            tenant_key=tenant,
            log_call=_log,
        )

        # 5. Mark state clean.
        if tenant:
            await run_in_threadpool(aggregate.mark_clean, tenant)

        return result

    except Exception as exc:
        log.error(
            "audit.summary_failed",
            detail=(
                "Generating the vault-wide audit summary failed (rolling-state read "
                "or LLM reduce raised). Returning a deterministic fallback built from "
                "the request payload so the caller still gets a usable response; the "
                "audit state is left 'dirty' so the next request retries. Cause below."
            ),
            tenant=tenant,
            error=str(exc),
        )
        return _fallback_from_payload(payload)


def _fallback_from_payload(payload: AuditSummaryRequest) -> AuditSummaryResponse:
    """Deterministic response derived from the request payload (no Mongo/LLM).

    Reuses derive_posture() by shaping the payload counters into the state dict
    it expects, so the posture stays consistent with the normal path.
    """
    pseudo_state = {
        "integrity": {
            "inSync": payload.integrity.in_sync,
            "reviewing": payload.integrity.reviewing,
            "deviation": payload.integrity.deviation,
        },
        "scoreSum": payload.average_score,
        "scoreCount": 1 if payload.average_score else 0,
        "counts": {
            "coop": payload.counts.coop,
            "bcp": payload.counts.bcp,
            "compliance": payload.counts.compliance,
        },
    }
    posture = derive_posture(pseudo_state)

    plans = payload.totals.plans
    attachments = payload.totals.attachments
    summary_text = (
        f"Audit narrative temporarily unavailable. Based on the latest figures, the "
        f"vault holds {plans} plan{'s' if plans != 1 else ''} and {attachments} "
        f"attachment{'s' if attachments != 1 else ''} with an average integrity score "
        f"of {payload.average_score}. A full summary will be generated on the next "
        f"request."
    )[:1500]

    return AuditSummaryResponse(
        summary=summary_text,
        findings=[],
        posture=posture,
        average_score=payload.average_score,
    )
