"""Aggregate continuity-audit endpoint.

Contract is wired now; the bounded incremental-summary logic lands in Step 7.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas import AuditSummaryRequest, AuditSummaryResponse
from app.security import require_auth

router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_auth)])


@router.post(
    "/summary",
    response_model=AuditSummaryResponse,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
async def summary(payload: AuditSummaryRequest) -> AuditSummaryResponse:
    """Return a bounded {summary, findings, posture, averageScore}.

    TODO(Step 7): merge rolling audit_state + capped sample -> bounded narrative.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="audit.summary is not implemented yet (scaffold / M0).",
    )
