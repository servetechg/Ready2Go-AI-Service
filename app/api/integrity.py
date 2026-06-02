"""Per-file integrity endpoints.

Routes + request/response contracts are wired now; the scoring logic lands in
Steps 2-6. Until then these return 501 so the API surface is testable and the
Next.js integration can be built against a stable contract.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.schemas import AnalyzeRequest, AnalyzeResponse, RescanRequest
from app.security import require_auth

router = APIRouter(prefix="/integrity", tags=["integrity"], dependencies=[Depends(require_auth)])


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
async def analyze(payload: AnalyzeRequest) -> AnalyzeResponse:
    """Analyze one attachment and return {status, score, summary}.

    TODO(Step 2-6): fetch+extract -> embed/upsert -> composite score -> summary.
    """
    raise _not_implemented("integrity.analyze")


@router.post("/rescan", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def rescan(payload: RescanRequest) -> dict[str, object]:
    """Re-run analysis for given attachments (backfill).

    TODO(Step 8): iterate attachmentIds, honoring cache unless force=True.
    """
    raise _not_implemented("integrity.rescan")


def _not_implemented(op: str):
    from fastapi import HTTPException

    return HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"{op} is not implemented yet (scaffold / M0).",
    )
