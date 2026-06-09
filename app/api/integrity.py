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

import asyncio
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

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
    AnalyzeAccepted,
    AnalyzeDetails,
    AnalyzeRequest,
    AnalyzeResponse,
    AnalyzeResultEnvelope,
    ComponentScores,
    RescanRequest,
    SimilarFile,
)
from app.scoring import integrity as scorer
from app.scoring import signals as sig
from app.scoring.integrity import SignalInputs
from app.security import require_auth
from app.store import aggregate, cache, calllog, jobs
from app.summary.per_doc import one_liner
from app.vectors import repo as vec

_LogCall = Callable[[dict[str, Any]], None]

router = APIRouter(
    prefix="/integrity",
    tags=["integrity"],
    dependencies=[Depends(require_auth)],
)

log = structlog.get_logger(__name__)

# Graceful fallback returned when the pipeline times out or hits an unexpected error.
_FALLBACK_SUMMARY = "Analysis unavailable — will retry on next request."


# Caps concurrent heavy pipelines so a burst of large PDFs can't exhaust memory;
# requests beyond the cap queue on this semaphore. Bound to the running loop and
# recreated if the loop changes (keeps test isolation across event loops).
_analyze_semaphore: asyncio.Semaphore | None = None
_analyze_semaphore_loop: asyncio.AbstractEventLoop | None = None

# Strong references to in-flight background tasks. Without this the event loop only
# keeps a weak reference and a long pipeline could be garbage-collected mid-run.
_background_tasks: set[asyncio.Task[None]] = set()


def _get_analyze_semaphore() -> asyncio.Semaphore:
    global _analyze_semaphore, _analyze_semaphore_loop
    loop = asyncio.get_running_loop()
    if _analyze_semaphore is None or _analyze_semaphore_loop is not loop:
        _analyze_semaphore = asyncio.Semaphore(get_settings().analyze_concurrency)
        _analyze_semaphore_loop = loop
    return _analyze_semaphore


@router.post("/analyze", status_code=status.HTTP_202_ACCEPTED, response_model=AnalyzeAccepted)
async def analyze(payload: AnalyzeRequest) -> AnalyzeAccepted:
    """Queue an integrity analysis and return immediately (202).

    The full pipeline (fetch → extract → embed → score → summarize) runs in the
    background so the caller never blocks on slow documents. Poll
    ``GET /v1/integrity/result/{attachmentId}`` until ``state != "processing"``.
    """
    settings = get_settings()
    tenant = payload.tenant_context.tenant_key
    attachment_id = payload.attachment.attachment_id

    _require_tenant(tenant)

    # Record the job as processing BEFORE scheduling so an immediate poll sees it.
    await run_in_threadpool(
        jobs.start, attachment_id, tenant_key=tenant, model_version=settings.model_version,
    )
    task = asyncio.create_task(_background_analyze(payload, settings))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    log.info(
        "analyze.queued",
        detail="Analysis queued; pipeline runs in the background. Poll pollUrl for the result.",
        tenant=tenant,
        attachment_id=attachment_id,
    )
    return AnalyzeAccepted(
        attachment_id=attachment_id,
        poll_url=f"/v1/integrity/result/{attachment_id}",
    )


@router.get("/result/{attachment_id}", response_model=AnalyzeResultEnvelope)
async def result(attachment_id: str) -> AnalyzeResultEnvelope:
    """Poll the status/result of a queued analysis.

    Returns ``processing`` while the background pipeline runs, then ``done`` with
    the full AnalyzeResponse, or ``error`` with an explanatory detail. If no job
    record exists, falls back to a direct cache lookup before reporting unknown.
    """
    job = await run_in_threadpool(jobs.get, attachment_id)
    if job:
        result_dict = job.get("result")
        return AnalyzeResultEnvelope(
            state=job.get("state", "processing"),
            result=AnalyzeResponse(**result_dict) if result_dict else None,
            detail=job.get("detail"),
        )

    # No job row — maybe analyzed under an older deploy whose jobs aged out.
    settings = get_settings()
    cached = await run_in_threadpool(
        cache.get_by_attachment, attachment_id, settings.model_version
    )
    if cached:
        return AnalyzeResultEnvelope(
            state="done",
            result=AnalyzeResponse(
                status=cached["status"],
                score=cached["score"],
                summary=cached["summary"],
                analyzed_at=datetime.now(UTC),
                model_version=settings.model_version,
                details=AnalyzeDetails(
                    component_scores=ComponentScores(**cached.get("scoreComponents", {})),
                    cache_hit=True,
                ),
            ),
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No analysis job or cached result found for attachment {attachment_id}.",
    )


async def _background_analyze(payload: AnalyzeRequest, settings: Any) -> None:
    """Run the analyze pipeline detached from the request, recording job state.

    Fetch failure → job state ``error`` (mirrors the old 502). Pipeline timeout or
    unexpected error → job ``done`` with a graceful degraded result (mirrors the old
    200 fallback) so Next.js still gets a usable verdict. Bounded by the analyze
    semaphore so concurrent large documents don't exhaust memory.
    """
    att = payload.attachment
    tenant = payload.tenant_context.tenant_key
    attachment_id = att.attachment_id
    model_version = settings.model_version

    async with _get_analyze_semaphore():
        # Detached task: bind its own log context, clear it when done.
        structlog.contextvars.bind_contextvars(tenant=tenant, attachment_id=attachment_id)
        try:
            # 2. Fetch bytes + sha256
            try:
                t0 = time.perf_counter()
                fetch_result = await fetch_bytes(att.file_url)
                log.info(
                    "pipeline.fetched",
                    bytes=fetch_result.size_bytes,
                    content_hash=fetch_result.content_hash,
                    mime=fetch_result.mime,
                    latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                )
            except FetchError as exc:
                log.error("integrity.fetch_failed", error=str(exc))
                await run_in_threadpool(
                    jobs.fail, attachment_id, f"Could not fetch attachment: {exc.reason}"
                )
                return

            content_hash = fetch_result.content_hash

            # 3. Cache check
            cached = await run_in_threadpool(cache.get, content_hash, model_version)
            if cached:
                log.info(
                    "pipeline.cache_hit",
                    detail=(
                        "Identical file bytes were analyzed before under this model "
                        "version; returning the cached verdict, zero embedding/LLM tokens."
                    ),
                    content_hash=content_hash,
                )
                response = AnalyzeResponse(
                    status=cached["status"],
                    score=cached["score"],
                    summary=cached["summary"],
                    analyzed_at=datetime.now(UTC),
                    model_version=model_version,
                    details=AnalyzeDetails(
                        component_scores=ComponentScores(**cached.get("scoreComponents", {})),
                        cache_hit=True,
                    ),
                )
                await run_in_threadpool(jobs.complete, attachment_id, _to_jsonable(response))
                return
            log.info(
                "pipeline.cache_miss",
                detail=(
                    "No cached verdict for these exact file bytes + model version; running "
                    "the full extract/embed/score/summarize pipeline."
                ),
                content_hash=content_hash,
            )

            # Steps 4-11: timeout + catch-all guard.
            pipeline_start = time.perf_counter()
            try:
                result_response = await asyncio.wait_for(
                    _run_pipeline(payload, fetch_result, settings),
                    timeout=settings.analyze_timeout_s,
                )
                log.info(
                    "pipeline.completed",
                    total_latency_ms=round((time.perf_counter() - pipeline_start) * 1000, 1),
                    cache_hit=False,
                )
                await run_in_threadpool(
                    jobs.complete, attachment_id, _to_jsonable(result_response)
                )
            except TimeoutError:
                log.error("pipeline.timeout", timeout_s=settings.analyze_timeout_s)
                await run_in_threadpool(
                    jobs.complete, attachment_id, _to_jsonable(_reviewing_fallback(model_version))
                )
            except Exception as exc:
                log.exception("pipeline.unexpected_error", error=str(exc))
                await run_in_threadpool(
                    jobs.complete, attachment_id, _to_jsonable(_reviewing_fallback(model_version))
                )
        finally:
            structlog.contextvars.clear_contextvars()


def _to_jsonable(response: AnalyzeResponse) -> dict[str, Any]:
    """Serialize an AnalyzeResponse to a JSON-safe dict for the job store."""
    return response.model_dump(by_alias=True, mode="json")


async def _run_pipeline(
    payload: AnalyzeRequest,
    fetch_result: object,
    settings: object,
) -> AnalyzeResponse:
    """Inner pipeline (steps 4-12); called inside asyncio.wait_for."""
    from app.config import Settings
    from app.ingest.fetch import FetchResult

    assert isinstance(fetch_result, FetchResult)
    assert isinstance(settings, Settings)

    tenant = payload.tenant_context.tenant_key
    att = payload.attachment
    plan = payload.plan
    attachment_id = att.attachment_id
    model_version = settings.model_version

    # ------------------------------------------------------------------ #
    # 4. Extract text + quality                                            #
    # ------------------------------------------------------------------ #
    t0 = time.perf_counter()
    parser = get_parser()
    extraction = parser.extract(fetch_result.data, att.file_extension)
    quality = extraction.quality
    log.info(
        "pipeline.extracted",
        chars=quality.chars,
        pages_or_rows=quality.pages_or_rows,
        is_scan_only=quality.is_scan_only,
        is_empty=quality.is_empty,
        latency_ms=round((time.perf_counter() - t0) * 1000, 1),
    )

    # ------------------------------------------------------------------ #
    # 5. Chunk                                                             #
    # ------------------------------------------------------------------ #
    chunks = chunk(extraction.text, max_chunks=settings.max_chunks_per_doc)
    total_tokens = sum(c.token_count for c in chunks)
    log.info("pipeline.chunked", chunk_count=len(chunks), total_tokens=total_tokens)

    # ------------------------------------------------------------------ #
    # 6. Embed (batched)                                                   #
    # ------------------------------------------------------------------ #
    vectors: list[list[float]] = []
    if chunks:
        t0 = time.perf_counter()
        chunk_texts = [c.text for c in chunks]
        try:
            vectors = await embed_texts(chunk_texts, log_call=_make_log(attachment_id))
        except Exception as exc:
            log.warning(
                "pipeline.embed_failed_degraded",
                detail=(
                    "OpenAI embedding of the document chunks failed, so no vectors "
                    "were produced. The document will not be stored in Weaviate and "
                    "its result is treated as DEGRADED (not cached, re-analyzed next "
                    "time). Cause below."
                ),
                chunk_count=len(chunks),
                error=str(exc),
            )
        log.info(
            "pipeline.embedded",
            vector_count=len(vectors),
            tokens=total_tokens,
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
        )

    # ------------------------------------------------------------------ #
    # 7. Upsert into Weaviate                                              #
    # ------------------------------------------------------------------ #
    weaviate_ok = False
    vector_ids: list[str] = []
    if chunks and vectors:
        try:
            t0 = time.perf_counter()
            vector_ids = await run_in_threadpool(
                vec.upsert_chunks,
                tenant,
                attachment_id,
                [c.text for c in chunks],
                vectors,
                plan_id=plan.plan_id,
                category=plan.category,
                file_name=att.file_name,
                content_hash=fetch_result.content_hash,
                model_version=model_version,
            )
            weaviate_ok = True
            log.info(
                "pipeline.vectors_upserted",
                count=len(vectors),
                vector_ids=len(vector_ids),
                tenant=tenant,
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            )
        except Exception as exc:
            log.warning(
                "pipeline.weaviate_upsert_failed_degraded",
                detail=(
                    "Storing this document's chunk vectors in Weaviate failed. "
                    "Vector-based signals (content, duplication, name) will be "
                    "degraded and the result is treated as DEGRADED (not cached). "
                    "Cause below."
                ),
                error=str(exc),
            )

    # ------------------------------------------------------------------ #
    # 8. Compute the signals -> composite score + status                  #
    # ------------------------------------------------------------------ #
    # We embed three short query strings in ONE batched OpenAI call:
    #   plan_text  — "{label} {overview} {steps}" → content-alignment signal.
    #   name_query — "{label} {category}"         → the plan side of the name signal.
    #   name_text  — the cleaned filename         → the document side of the name signal.
    # The name signal is then an ABSOLUTE cosine between name_text and name_query
    # (see note below) — computed in-process, no Weaviate hybrid search needed.
    plan_text = f"{plan.label} {plan.overview} {' '.join(plan.steps)}"
    name_query = f"{plan.label} {plan.category}"
    name_text = _clean_filename(att.file_name)
    plan_vector: list[float] | None = None
    name_query_vector: list[float] | None = None
    name_text_vector: list[float] | None = None
    try:
        query_vectors = await embed_texts(
            [plan_text, name_query, name_text], log_call=_make_log(attachment_id)
        )
        plan_vector = query_vectors[0] if len(query_vectors) > 0 else None
        name_query_vector = query_vectors[1] if len(query_vectors) > 1 else None
        name_text_vector = query_vectors[2] if len(query_vectors) > 2 else None
    except Exception as exc:
        log.warning(
            "pipeline.query_embed_failed",
            detail=(
                "Could not embed the plan-context / name-query / filename text via "
                "OpenAI. The content-alignment and name-alignment signals will both "
                "default to 0, lowering this document's score. Cause below."
            ),
            error=str(exc),
        )

    # Name-alignment signal: absolute cosine of the (cleaned) filename vs the
    # plan's "{label} {category}". This is per-document and has no dependence on
    # how many other documents exist, so a valid file can never be silently
    # pushed to 0 by ranking below a top-N window (the old hybrid-search bug).
    name_score = sig.content_alignment(name_text_vector, name_query_vector)

    # Fetch stored chunks back (they have vectors attached) — skip if Weaviate failed.
    stored_chunks: list = []
    centroid: list[float] | None = None
    nearest_sibling_dist: float | None = None

    if weaviate_ok:
        try:
            stored_chunks = await run_in_threadpool(vec.get_all_chunks, tenant, attachment_id)
            centroid = vec.content_centroid(stored_chunks)
        except Exception as exc:
            log.warning(
                "pipeline.weaviate_read_failed",
                detail=(
                    "Could not read this document's chunks back from Weaviate, so "
                    "no content centroid is available. Content/category/duplication "
                    "signals will be degraded for this document."
                ),
                error=str(exc),
            )

        if centroid:
            try:
                siblings = await run_in_threadpool(
                    vec.sibling_similarities,
                    tenant, plan.plan_id, centroid,
                    exclude_attachment_id=attachment_id,
                )
                if siblings:
                    nearest_sibling_dist = siblings[0].distance
            except Exception as exc:
                log.warning(
                    "pipeline.sibling_search_failed",
                    detail=(
                        "Sibling-similarity search failed; the duplication signal "
                        "will treat this document as unique (no near-duplicate "
                        "detected) even though that could not be verified."
                    ),
                    error=str(exc),
                )

    signals = SignalInputs(
        content=sig.content_alignment(centroid, plan_vector),
        name=name_score,
        quality=sig.extraction_quality(quality),
        duplication=sig.duplication(nearest_sibling_dist),
    )

    result = scorer.compute(signals, quality)

    score = result.score
    int_status = result.status
    components = result.components

    # "Degraded" means a dependency we NEEDED was unreachable — NOT that the
    # document simply had no content to vectorize. An empty/scan-only doc yields
    # zero chunks; that is a legitimate (low-quality) result worth caching.
    #   - embed_failed:    we had text but OpenAI embeddings were unavailable.
    #   - weaviate_failed: we had vectors but the vector store rejected/failed.
    embed_failed = bool(chunks) and not vectors
    weaviate_failed = bool(chunks and vectors) and not weaviate_ok
    degraded = embed_failed or weaviate_failed

    log.info(
        "pipeline.scored",
        detail=(
            "Composite integrity score computed from the weighted signals "
            "(content/name/quality/duplication). 'degraded=true' means a required "
            "dependency was unreachable, so the signals — and this score — are not "
            "authoritative and will not be cached."
        ),
        score=score,
        status=int_status,
        components=components,
        degraded=degraded,
    )

    # Optional LLM judge for borderline scores.
    if result.used_llm_judge and settings.openai_api_key:
        try:
            t0 = time.perf_counter()
            excerpt = extraction.text[:8000]
            judge_result = await chat_json(
                messages=[
                    {"role": "system", "content": (
                        "You are a COOP document reviewer. Given a score and excerpt, "
                        'return ONLY JSON: {"status": "Compliant"|"Under Review"|"Non-Compliant",'
                        ' "score": <int 0-100>}. Adjust the score based on your review.'
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
            before_status, before_score = int_status, score
            int_status = _normalize_status(judge_result.get("status", int_status))
            try:
                score = max(0, min(100, int(judge_result.get("score", score))))
            except (TypeError, ValueError):
                log.debug(
                    "pipeline.llm_judge_score_unparseable",
                    detail=(
                        "The LLM judge returned a non-numeric 'score'; keeping the "
                        "computed score unchanged."
                    ),
                    raw_score=judge_result.get("score"),
                )
            log.info(
                "pipeline.llm_judge",
                before={"status": before_status, "score": before_score},
                after={"status": int_status, "score": score},
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            )
        except Exception as exc:
            log.warning(
                "pipeline.llm_judge_failed_degraded",
                detail=(
                    "The optional LLM judge call for this borderline score failed. "
                    "Falling back to the computed status/score unchanged. Cause below."
                ),
                error=str(exc),
            )

    # ------------------------------------------------------------------ #
    # 9. Per-doc one-liner from COMPLETE chunk set                         #
    # ------------------------------------------------------------------ #
    t0 = time.perf_counter()
    summary_text = await one_liner(
        stored_chunks,
        file_name=att.file_name,
        plan_label=plan.label,
        plan_category=plan.category,
        log_call=_make_log(attachment_id),
    )
    log.info(
        "pipeline.summarized",
        length=len(summary_text),
        map_reduce=len(stored_chunks) > 10,
        latency_ms=round((time.perf_counter() - t0) * 1000, 1),
    )

    # ------------------------------------------------------------------ #
    # 10. Persist cache + audit state                                      #
    #                                                                      #
    # Skip persistence when the result is DEGRADED: a transient dependency #
    # outage (e.g. Weaviate down) yields zeroed vector signals and an      #
    # artificially low score. Caching that would poison the dedup cache    #
    # and skew the audit aggregate until the cache entry is force-cleared. #
    # A degraded result is still returned to the caller (graceful), but is #
    # not treated as authoritative — the next call re-analyzes.            #
    # ------------------------------------------------------------------ #
    if not degraded:
        await run_in_threadpool(
            cache.put,
            attachment_id=attachment_id,
            content_hash=fetch_result.content_hash,
            model_version=model_version,
            status=int_status,
            score=score,
            summary=summary_text,
            score_components=components,
            vector_ids=vector_ids,
        )
        await run_in_threadpool(
            aggregate.update,
            tenant,
            category=plan.category,
            status=int_status,
            score=score,
            file_name=att.file_name,
            plan_id=plan.plan_id,
            summary=summary_text,
        )

    log.info(
        "pipeline.persisted",
        detail=(
            "Wrote the verdict to the dedup cache and incremented the rolling audit "
            "state. Both are skipped when the result is degraded, so a transient "
            "outage cannot poison the cache or skew the audit."
        ),
        cache=not degraded,
        audit_state_updated=not degraded,
        skipped_reason="degraded" if degraded else None,
    )

    log.info(
        "integrity.analyzed",
        detail="Analysis pipeline finished; returning the verdict to the caller.",
        status=int_status,
        score=score,
        degraded=degraded,
    )

    # ------------------------------------------------------------------ #
    # 12. Return — Next.js writes aiIntegrity* fields                     #
    # ------------------------------------------------------------------ #
    similar: list[SimilarFile] = []
    if centroid:
        try:
            sibs = await run_in_threadpool(
                vec.sibling_similarities,
                tenant, plan.plan_id, centroid,
                exclude_attachment_id=attachment_id,
            )
            similar = [
                SimilarFile(attachment_id=s.attachment_id, similarity=s.similarity)
                for s in sibs[:3]
            ]
        except Exception as exc:
            log.warning(
                "pipeline.similar_files_failed",
                detail=(
                    "Could not look up similar sibling files for the response. The "
                    "score and status are unaffected; the response simply returns an "
                    "empty similarFiles list. Cause below."
                ),
                error=str(exc),
            )

    return AnalyzeResponse(
        status=int_status,
        score=score,
        summary=summary_text[:2000],
        analyzed_at=datetime.now(UTC),
        model_version=model_version,
        details=AnalyzeDetails(
            component_scores=ComponentScores(**components),
            similar_files=similar,
            cache_hit=False,
            degraded=degraded,
        ),
    )


@router.post("/rescan", status_code=status.HTTP_202_ACCEPTED)
async def rescan(payload: RescanRequest) -> dict[str, object]:
    """Re-run analysis for given attachments (backfill).

    For each attachmentId, looks up the cached record and re-analyzes if
    force=True or no cache entry exists.  v1 uses a synchronous bounded loop;
    an async queue can be added when volume warrants.
    """
    if not payload.attachment_ids:
        return {"scheduled": 0, "message": "No attachment IDs provided."}

    settings = get_settings()
    tenant = payload.tenant_key or ""
    _require_tenant(tenant)

    scheduled = 0
    skipped = 0

    from app.store.models import get_cache_col

    def _find_cached(aid: str) -> object:
        return get_cache_col().find_one({"attachmentId": aid}, {"_id": 1})

    def _delete_cached(aid: str) -> None:
        get_cache_col().delete_many({"attachmentId": aid})

    for attachment_id in payload.attachment_ids:
        try:
            if not payload.force:
                # Check if a valid cache entry exists; skip if found.
                existing = await run_in_threadpool(_find_cached, attachment_id)
                if existing:
                    skipped += 1
                    log.info("rescan.skipped_cached", attachment_id=attachment_id)
                    continue

            # Delete cached entry so the next /analyze call re-runs the full pipeline.
            await run_in_threadpool(_delete_cached, attachment_id)
            # Also remove Weaviate chunks so they're re-embedded on next analyze.
            if settings.weaviate_url:
                try:
                    await run_in_threadpool(vec.delete_by_attachment, tenant, attachment_id)
                except Exception as exc:
                    log.warning(
                        "rescan.weaviate_delete_failed",
                        detail=(
                            "Cleared the Mongo cache entry but could not delete this "
                            "attachment's existing chunks from Weaviate. The next "
                            "/analyze deletes-then-inserts, so stale chunks are "
                            "normally overwritten — but flag this if re-analysis "
                            "looks wrong. Cause below."
                        ),
                        attachment_id=attachment_id,
                        error=str(exc),
                    )

            scheduled += 1
            log.info("rescan.scheduled", attachment_id=attachment_id, force=payload.force)

        except Exception as exc:
            log.warning(
                "rescan.error",
                detail=(
                    "Failed to prepare a rescan for this attachment (cache lookup or "
                    "delete raised). Skipping it and continuing with the rest. "
                    "Cause below."
                ),
                attachment_id=attachment_id,
                error=str(exc),
            )

    return {
        "scheduled": scheduled,
        "skipped": skipped,
        "message": (
            f"Rescan prepared for {scheduled} attachment(s). "
            f"They will be re-analyzed on the next /analyze call. "
            f"({skipped} skipped — already cached; use force=true to override.)"
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


def _make_log(attachment_id: str) -> _LogCall:
    """Return a log_call callback that stamps the attachmentId."""
    def _log(row: dict[str, Any]) -> None:
        row["attachmentId"] = attachment_id
        calllog.append(row)
    return _log


def _clean_filename(file_name: str) -> str:
    """Turn a filename into clean words for embedding.

    Strips the extension and replaces separator punctuation with spaces so the
    embedding reflects the words in the name rather than slug punctuation, e.g.
    "fema_reconstitution-plan_template_10-22-19.pdf"
        -> "fema reconstitution plan template 10 22 19".
    """
    stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    cleaned = re.sub(r"[-_.]+", " ", stem)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or file_name


def _normalize_status(s: str) -> str:
    u = str(s).strip().lower()
    # "non-compliant" contains "compliant", so test the negative form first.
    if "non" in u or "deviation" in u:
        return "Non-Compliant"
    if "compliant" in u or "sync" in u:
        return "Compliant"
    return "Under Review"


def _reviewing_fallback(model_version: str) -> AnalyzeResponse:
    """Return the graceful Under Review response when the pipeline fails or times out."""
    return AnalyzeResponse(
        status="Under Review",
        score=50,
        summary=_FALLBACK_SUMMARY,
        analyzed_at=datetime.now(UTC),
        model_version=model_version,
        details=AnalyzeDetails(
            component_scores=ComponentScores(),
            cacheHit=False,
            degraded=True,
        ),
    )
