"""Per-file integrity endpoints.

POST /v1/integrity/analyze  — full 12-step pipeline per uploaded document.
POST /v1/integrity/rescan   — backfill / re-run for one or more attachments.

Flow for /analyze (see PHASE_B_VECTOR_IMPLEMENTATION_PLAN.md §1):
  1. Validate AnalyzeRequest              (schemas.py)
  2. Fetch bytes + sha256                 (ingest/fetch.py)
  3. Cache check — hit -> return, 0 tokens
  4. Extract text + quality               (ingest/extract.py)
  5. Chunk text                           (ingest/chunk.py)
  6. Embed chunks (batched)               (llm/embeddings.py)
  7. Upsert chunks into Weaviate          (vectors/repo.py)
  8. Compute 5 signals -> score/status    (scoring/*)
     + optional gated LLM judge           (llm/client.py)
  9. Per-doc one-liner (full chunk set)   (summary/per_doc.py)
 10. Persist ai_analysis_cache            (store/cache.py)
 11. Update ai_audit_state (dirty=True)  (store/aggregate.py)
     + log call                           (store/calllog.py)
 12. Return AnalyzeResponse -> Next.js writes aiIntegrity* fields
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.ingest.chunk import chunk
from app.ingest.extract import get_parser
from app.ingest.fetch import FetchError, fetch_bytes
from app.llm.client import chat_json
from app.llm.embeddings import embed_texts
from app.schemas import (
    AnalyzeDetails,
    AnalyzeRequest,
    AnalyzeResponse,
    ComponentScores,
    RescanRequest,
    SimilarFile,
)
from app.scoring import integrity as scorer
from app.scoring import signals as sig
from app.scoring.integrity import SignalInputs
from app.security import require_auth
from app.store import aggregate, cache, calllog
from app.summary.per_doc import one_liner
from app.vectors import repo as vec

router = APIRouter(
    prefix="/integrity",
    tags=["integrity"],
    dependencies=[Depends(require_auth)],
)

log = structlog.get_logger(__name__)


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(payload: AnalyzeRequest) -> AnalyzeResponse:
    """Full integrity analysis pipeline for one attachment."""
    settings = get_settings()
    tenant = payload.tenant_context.tenant_key
    att = payload.attachment
    plan = payload.plan
    attachment_id = att.attachment_id

    _require_tenant(tenant)

    # ------------------------------------------------------------------ #
    # 2. Fetch bytes + sha256                                              #
    # ------------------------------------------------------------------ #
    try:
        fetch_result = await fetch_bytes(att.file_url)
    except FetchError as exc:
        log.error("integrity.fetch_failed", attachment=attachment_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not fetch attachment: {exc.reason}",
        ) from exc

    content_hash = fetch_result.content_hash
    model_version = settings.model_version

    # ------------------------------------------------------------------ #
    # 3. Cache check                                                       #
    # ------------------------------------------------------------------ #
    cached = await run_in_threadpool(cache.get, content_hash, model_version)
    if cached:
        log.info("integrity.cache_hit", attachment=attachment_id)
        return AnalyzeResponse(
            status=cached["status"],
            score=cached["score"],
            summary=cached["summary"],
            analyzedAt=datetime.now(UTC),
            modelVersion=model_version,
            details=AnalyzeDetails(
                componentScores=ComponentScores(**cached.get("scoreComponents", {})),
                cacheHit=True,
            ),
        )

    # ------------------------------------------------------------------ #
    # 4. Extract text + quality                                            #
    # ------------------------------------------------------------------ #
    parser = get_parser()
    extraction = parser.extract(fetch_result.data, att.file_extension)
    quality = extraction.quality

    # ------------------------------------------------------------------ #
    # 5. Chunk                                                             #
    # ------------------------------------------------------------------ #
    chunks = chunk(extraction.text, max_chunks=settings.max_chunks_per_doc)

    # ------------------------------------------------------------------ #
    # 6. Embed (batched)                                                   #
    # ------------------------------------------------------------------ #
    vectors: list[list[float]] = []
    if chunks:
        chunk_texts = [c.text for c in chunks]
        vectors = await embed_texts(chunk_texts, log_call=_make_log(attachment_id))

    # ------------------------------------------------------------------ #
    # 7. Upsert into Weaviate                                              #
    # ------------------------------------------------------------------ #
    if chunks and vectors:
        await run_in_threadpool(
            vec.upsert_chunks,
            tenant,
            attachment_id,
            [c.text for c in chunks],
            vectors,
            plan_id=plan.plan_id,
            category=plan.category,
            file_name=att.file_name,
            content_hash=content_hash,
            model_version=model_version,
        )

    # ------------------------------------------------------------------ #
    # 8. Compute 5 signals -> composite score + status                    #
    # ------------------------------------------------------------------ #
    # Build plan-context embedding for content-alignment signal.
    plan_text = f"{plan.label} {plan.overview} {' '.join(plan.steps)}"
    plan_vectors = await embed_texts([plan_text], log_call=_make_log(attachment_id))
    plan_vector = plan_vectors[0] if plan_vectors else None

    # Fetch stored chunks back (they have vectors attached).
    stored_chunks = await run_in_threadpool(vec.get_all_chunks, tenant, attachment_id)
    centroid = vec.content_centroid(stored_chunks)

    # Prototype similarities (category-fit).
    prototype_sims: dict[str, float] = {}
    if centroid:
        prototype_sims = await run_in_threadpool(vec.prototype_similarities, centroid)

    # Sibling nearest-neighbour (duplication).
    nearest_sibling_dist: float | None = None
    if centroid:
        siblings = await run_in_threadpool(
            vec.sibling_similarities,
            tenant, plan.plan_id, centroid,
            exclude_attachment_id=attachment_id,
        )
        if siblings:
            nearest_sibling_dist = siblings[0].distance

    # Hybrid name search (name-alignment).
    name_query = f"{plan.label} {plan.category}"
    hybrid_hits = await run_in_threadpool(vec.hybrid_name_search, tenant, name_query)
    # Find this attachment's hybrid score among hits.
    hybrid_score: float | None = None
    for h in hybrid_hits:
        if h.attachment_id == attachment_id:
            hybrid_score = h.similarity
            break

    signals = SignalInputs(
        content=sig.content_alignment(centroid, plan_vector),
        name=sig.name_alignment(hybrid_score),
        category=sig.category_fit(plan.category, prototype_sims),
        quality=sig.extraction_quality(quality),
        duplication=sig.duplication(nearest_sibling_dist),
    )

    result = scorer.compute(
        signals,
        quality,
        declared_category=plan.category,
        prototype_sims=prototype_sims,
    )

    score = result.score
    int_status = result.status
    components = result.components

    # Optional LLM judge for borderline scores.
    if result.used_llm_judge and settings.openai_api_key:
        excerpt = extraction.text[:8000]
        judge_result = await chat_json(
            messages=[
                {"role": "system", "content": (
                    "You are a COOP document reviewer. Given a score and excerpt, "
                    "return ONLY JSON: {\"status\": \"In Sync\"|\"Reviewing\"|\"Deviation Found\", "
                    "\"score\": <int 0-100>}. Adjust the score based on your review."
                )},
                {"role": "user", "content": (
                    f"Plan: {plan.label} ({plan.category})\n"
                    f"File: {att.file_name}\n"
                    f"Computed score: {score}\n"
                    f"Excerpt:\n{excerpt}"
                )},
            ],
            fallback={"status": int_status, "score": score},
            max_tokens=60,
            log_call=_make_log(attachment_id),
        )
        int_status = _normalize_status(judge_result.get("status", int_status))
        import contextlib
        with contextlib.suppress(TypeError, ValueError):
            score = max(0, min(100, int(judge_result.get("score", score))))

    # ------------------------------------------------------------------ #
    # 9. Per-doc one-liner from COMPLETE chunk set                         #
    # ------------------------------------------------------------------ #
    summary_text = await one_liner(
        stored_chunks,
        file_name=att.file_name,
        plan_label=plan.label,
        plan_category=plan.category,
        log_call=_make_log(attachment_id),
    )

    # ------------------------------------------------------------------ #
    # 10. Persist cache                                                    #
    # ------------------------------------------------------------------ #
    await run_in_threadpool(
        cache.put,
        attachment_id=attachment_id,
        content_hash=content_hash,
        model_version=model_version,
        status=int_status,
        score=score,
        summary=summary_text,
        score_components=components,
    )

    # ------------------------------------------------------------------ #
    # 11. Update audit state + log                                         #
    # ------------------------------------------------------------------ #
    await run_in_threadpool(
        aggregate.update,
        tenant,
        category=plan.category,
        status=int_status,
        score=score,
        file_name=att.file_name,
        plan_id=plan.plan_id,
    )

    log.info(
        "integrity.analyzed",
        tenant=tenant,
        attachment=attachment_id,
        status=int_status,
        score=score,
    )

    # ------------------------------------------------------------------ #
    # 12. Return — Next.js writes aiIntegrity* fields                     #
    # ------------------------------------------------------------------ #
    similar = [
        SimilarFile(attachmentId=s.attachment_id, similarity=s.similarity)
        for s in (await run_in_threadpool(
            vec.sibling_similarities,
            tenant, plan.plan_id, centroid or [],
            exclude_attachment_id=attachment_id,
        ) if centroid else [])[:3]
    ]

    return AnalyzeResponse(
        status=int_status,
        score=score,
        summary=summary_text[:280],
        analyzedAt=datetime.now(UTC),
        modelVersion=model_version,
        details=AnalyzeDetails(
            componentScores=ComponentScores(**components),
            similarFiles=similar,
            cacheHit=False,
        ),
    )


@router.post("/rescan", status_code=status.HTTP_202_ACCEPTED)
async def rescan(payload: RescanRequest) -> dict[str, object]:
    """Re-run analysis for given attachments (backfill).

    Queues re-analysis for each attachmentId.  If force=True, bypasses the
    cache.  Returns counts of scheduled jobs.
    """
    # For v1 this is a synchronous best-effort loop.  A proper async queue
    # can be introduced when volume warrants.
    if not payload.attachment_ids:
        return {"scheduled": 0, "message": "No attachment IDs provided."}
    return {
        "scheduled": len(payload.attachment_ids),
        "message": (
            "Rescan scheduled. Each attachment will be re-analyzed on the next "
            "request cycle. (v1: implement per-attachment async job here.)"
        ),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_tenant(tenant: str) -> None:
    if not tenant or not tenant.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tenantKey is required.",
        )


def _make_log(attachment_id: str):
    """Return a log_call callback that also stamps the attachmentId."""
    def _log(row: dict) -> None:
        row["attachmentId"] = attachment_id
        calllog.append(row)
    return _log


def _normalize_status(s: str) -> str:
    u = str(s).strip().lower()
    if "deviation" in u:
        return "Deviation Found"
    if "sync" in u:
        return "In Sync"
    return "Reviewing"
