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
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.schemas import AuditSummaryRequest, AuditSummaryResponse
from app.security import require_auth
from app.store import aggregate, calllog
from app.summary.audit import build

router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_auth)])


@router.post("/summary", response_model=AuditSummaryResponse)
async def summary(payload: AuditSummaryRequest) -> AuditSummaryResponse:
    """Return a bounded {summary, findings, posture, averageScore}."""
    tenant = (
        payload.tenant_context.tenant_key
        if payload.tenant_context
        else ""
    )

    # 2. Read rolling state.
    state = await run_in_threadpool(aggregate.read, tenant)

    # 3. Build capped sample from notable list (worst scores first).
    notable = state.get("notable", [])

    def _log(row: dict) -> None:
        calllog.append(row)

    # 4. Build bounded audit narrative.
    result = await build(
        state,
        notable,
        tenant_key=tenant,
        log_call=_log,
    )

    # 5. Mark state clean.
    if tenant:
        await run_in_threadpool(aggregate.mark_clean, tenant)

    return result
