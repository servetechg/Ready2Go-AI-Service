"""Pydantic request/response models — the wire contract with the Next.js app.

These mirror the "Shared input/response payload" and the `ContinuityAudit*`
shapes documented in the architecture (§5) and PROJECT_CONTEXT (§11). Field names
and value constraints are a HARD contract: the Next.js UI keys off the exact
status strings and the ≤1000/≤1500-char limits.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

IntegrityStatus = Literal["Compliant", "Under Review", "Non-Compliant"]
Posture = Literal["Resilient", "Steady", "At Risk"]
PlanCategory = Literal["coop", "bcp", "compliance"]
StoredOrResponseCategory = Literal["coop", "bcp", "compliance", "response"]


# ---------------------------------------------------------------------------
# Per-file integrity: POST /v1/integrity/analyze
# ---------------------------------------------------------------------------
class TenantContext(BaseModel):
    tenant_key: str = Field(..., alias="tenantKey")
    actor_user_id: str | None = Field(default=None, alias="actorUserId")

    model_config = {"populate_by_name": True}


class PlanContext(BaseModel):
    plan_id: str = Field(..., alias="planId")
    label: str
    overview: str = ""
    category: PlanCategory = "coop"
    steps: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class AttachmentRef(BaseModel):
    attachment_id: str = Field(..., alias="attachmentId")
    file_name: str = Field(..., alias="fileName")
    file_extension: Literal["pdf", "docx", "csv", "xlsx"] = Field(..., alias="fileExtension")
    file_mime: str | None = Field(default=None, alias="fileMime")
    file_size_bytes: int | None = Field(default=None, alias="fileSizeBytes")
    file_url: str = Field(..., alias="fileUrl")
    cloudinary_public_id: str | None = Field(default=None, alias="cloudinaryPublicId")
    cloudinary_resource_type: str | None = Field(default=None, alias="cloudinaryResourceType")

    model_config = {"populate_by_name": True}


class AnalyzeRequest(BaseModel):
    tenant_context: TenantContext = Field(..., alias="tenantContext")
    plan: PlanContext
    attachment: AttachmentRef
    extracted_text: str | None = Field(default=None, alias="extractedText")
    vector_key: str | None = Field(default=None, alias="vectorKey")

    model_config = {"populate_by_name": True}


class ComponentScores(BaseModel):
    content: int | None = None
    name: int | None = None
    quality: int | None = None
    duplication: int | None = None


class SimilarFile(BaseModel):
    attachment_id: str = Field(..., alias="attachmentId")
    similarity: float

    model_config = {"populate_by_name": True}


class AnalyzeDetails(BaseModel):
    component_scores: ComponentScores | None = Field(default=None, alias="componentScores")
    similar_files: list[SimilarFile] = Field(default_factory=list, alias="similarFiles")
    cache_hit: bool = Field(default=False, alias="cacheHit")
    degraded: bool = Field(
        default=False,
        description="True when one or more dependencies (Weaviate/OpenAI) were unreachable.",
    )

    model_config = {"populate_by_name": True}


class AnalyzeResponse(BaseModel):
    status: IntegrityStatus
    score: int = Field(..., ge=0, le=100)
    summary: str = Field(..., max_length=1000)
    analyzed_at: datetime = Field(..., alias="analyzedAt")
    model_version: str = Field(..., alias="modelVersion")
    details: AnalyzeDetails | None = None

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Rescan / backfill: POST /v1/integrity/rescan
# ---------------------------------------------------------------------------
class RescanRequest(BaseModel):
    attachment_ids: list[str] | None = Field(default=None, alias="attachmentIds")
    tenant_key: str | None = Field(default=None, alias="tenantKey")
    force: bool = False

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Aggregate audit: POST /v1/audit/summary
# ---------------------------------------------------------------------------
class AuditTotals(BaseModel):
    plans: int = 0
    attachments: int = 0
    analyzed: int = 0


class AuditCounts(BaseModel):
    coop: int = 0
    bcp: int = 0
    compliance: int = 0
    response: int = 0


class IntegrityBreakdown(BaseModel):
    in_sync: int = Field(default=0, alias="inSync")
    reviewing: int = 0
    deviation: int = 0
    unanalyzed: int = 0

    model_config = {"populate_by_name": True}


class AuditAttachment(BaseModel):
    file_name: str = Field(..., alias="fileName")
    status: str | None = None
    score: int | None = None
    summary: str | None = None

    model_config = {"populate_by_name": True}


class AuditPlan(BaseModel):
    plan_id: str = Field(..., alias="planId")
    label: str
    category: StoredOrResponseCategory
    attachment_count: int = Field(default=0, alias="attachmentCount")
    step_count: int = Field(default=0, alias="stepCount")
    attachments: list[AuditAttachment] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class AuditSummaryRequest(BaseModel):
    tenant_context: TenantContext | None = Field(default=None, alias="tenantContext")
    totals: AuditTotals
    average_score: int = Field(default=0, alias="averageScore")
    counts: AuditCounts
    integrity: IntegrityBreakdown
    plans: list[AuditPlan] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class AuditSummaryResponse(BaseModel):
    summary: str = Field(..., max_length=1500)
    findings: list[str] = Field(default_factory=list, max_length=8)
    posture: Posture
    average_score: int = Field(..., alias="averageScore")

    model_config = {"populate_by_name": True}
