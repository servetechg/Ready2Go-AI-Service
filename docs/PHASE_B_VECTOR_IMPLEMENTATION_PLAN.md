# Phase B — Vector Pipeline Implementation Plan

> **Purpose:** the step-by-step build plan for the integrity pipeline behind
> `POST /v1/integrity/analyze` and `POST /v1/audit/summary` — turning every 6-line
> stub under `app/{ingest,vectors,scoring,store,summary,llm}/` into working code.
>
> **Read first:** `docs/VECTOR_DB_AND_RAG_CONCEPTS.md` (the *why* / mental model)
> and `docs/PYTHON_AI_SERVICE_ARCHITECTURE.md` (the contract). This doc is the
> *how/what/in-what-order*.
>
> **Supersedes** the retired data-prep Phase B notes; Phase A (seeding) is done.

---

## 0. Locked decisions this plan builds on

- **Stores:** Weaviate (self-hosted, vectors) + MongoDB (`ai_*` collections inside
  the app's `ready2go` DB). No Postgres, no separate DB.
- **Writeback:** per-doc verdict returned **synchronously**; **Next.js** writes the
  `aiIntegrity*` fields. Python writes only `ai_analysis_cache`, `ai_audit_state`,
  `ai_call_log` and the Weaviate chunks. Python never writes the app's domain docs.
- **Audit:** regenerated **on-demand + debounced** (UI refresh / debounced job),
  never per upload. Per-doc score/summary still updates instantly on each upload.
- **Tenant key:** `tenantKey = "sub_" + ownerUserId` — the Weaviate tenant name and
  the `ai_*` scoping key.
- **Models:** `text-embedding-3-small` (vectors), `gpt-4o-mini` (summaries / judge).

---

## 1. The request flow (what we're wiring toward)

### `POST /v1/integrity/analyze` (per upload, synchronous)
```
1. validate AnalyzeRequest (schemas.py — done)
2. fetch bytes + sha256                          (ingest/fetch.py — done)
3. CACHE CHECK: ai_analysis_cache[(contentHash, modelVersion)]
      └─ hit  → return cached verdict, cacheHit:true, ZERO tokens
4. extract text + quality                        (ingest/extract.py)
5. chunk text                                    (ingest/chunk.py)
6. embed chunks (batched)                         (llm/embeddings.py)
7. upsert chunks into Weaviate (tenant-scoped)    (vectors/repo.py)
8. compute 5 signals → composite → status/score   (scoring/*)
      └─ optional gated LLM judge for borderline   (llm/client.py)
9. per-doc one-liner (from the doc's COMPLETE chunk set)  (summary/per_doc.py)
10. persist ai_analysis_cache + update ai_audit_state (dirty=true)  (store/*)
11. log calls to ai_call_log
12. return AnalyzeResponse  ──▶  Next.js writes aiIntegrity* fields
```

### `POST /v1/audit/summary` (on-demand / debounced, synchronous)
```
1. validate AuditSummaryRequest (schemas.py — done)
2. read rolling ai_audit_state[tenantKey] + capped sample of stored one-liners
3. bounded reduce → {summary ≤360, findings 2–4 ≤140, posture, averageScore}
4. return AuditSummaryResponse  ──▶  Next.js upserts continuityauditreports
```

---

## 2. Build order (dependency-sorted milestones)

Each module turns an existing stub into code, ships with tests, and is independently
runnable. Order matters — later modules import earlier ones.

### M1 — Ingestion (parse + chunk)
| File | Function (signature sketch) | Notes |
|---|---|---|
| `app/ingest/extract.py` | `DocumentParser` **protocol** + `get_parser()`; default `BasicParser.extract(data, ext) -> Extraction{text, quality}`, `ExtractionQuality = {chars, pages_or_rows, is_scan_only, is_empty}` | **Swappable backend (D5).** `BasicParser` (v1): PDF pdfplumber→pypdf fallback, DOCX python-docx, XLSX openpyxl (sheet→rows), CSV stdlib. **No length cap.** `is_scan_only` = PDF with pages but ~0 chars; `is_empty` = ~0 chars. **`LiteParseParser` = future backend** (same protocol; adds OCR for scan-only PDFs), config-selected, off by default — no system deps in v1. |
| `app/ingest/chunk.py` | `chunk(text: str, *, max_chunks: int) -> list[Chunk]` where `Chunk = {index, text, token_count}` | **Single chunker (D6).** tiktoken `cl100k_base`; ~500 tokens, ~50 overlap (config-tunable later); bounded by `settings.max_chunks_per_doc`. (Adaptive Chunking evaluated & skipped — see "Researched & evaluated" below.) |

**Acceptance:** golden-file tests per type incl. a scan-only PDF and an empty file;
chunk token bounds + overlap respected; cap enforced.

### M2 — LLM clients (embeddings + chat)
| File | Function | Notes |
|---|---|---|
| `app/llm/client.py` | `async chat(messages, *, model, max_tokens) -> dict` | OpenAI SDK; `response_format=json_object`; tenacity retry (exp backoff + jitter); meters tokens → `ai_call_log`. |
| `app/llm/embeddings.py` | `async embed_texts(texts: list[str]) -> list[list[float]]` | one batched call for all chunks of a doc; retry; token meter. Returns 1536-d vectors. |

**Acceptance:** mocked OpenAI (respx) returns deterministic vectors; retry path
covered; a call-log row is written per call.

### M3 — Vectors (Weaviate)
| File | Function | Notes |
|---|---|---|
| `app/vectors/schema.py` | `DocChunk` (multi-tenant) + `CategoryPrototype` (global) collection definitions | properties: `attachmentId, planId, category, fileName, contentHash, chunkIndex, text, modelVersion`; vector supplied by us (no Weaviate vectorizer module). |
| `app/vectors/client.py` | `get_client()` + `ensure_collections()` | connect to self-hosted `WEAVIATE_URL`; lazy singleton; bootstrap collections on first use. |
| `app/vectors/repo.py` | `upsert_chunks(tenant, attachment_id, chunks, vectors, meta)`, `delete_by_attachment(tenant, attachment_id)`, `get_all_chunks(tenant, attachment_id)` (complete per-doc fetch-by-id, for the summary — D7), `content_centroid(...)`, `sibling_similarities(tenant, plan_id, centroid)`, `prototype_similarities(centroid)`, `hybrid_name_search(tenant, query)` | every op tenant-scoped; re-upload = delete-then-insert that attachment's chunks. `get_all_chunks` is a filtered lookup, not a similarity search. |

**Acceptance:** upsert→query round-trip; **tenant A query returns zero tenant-B
objects**; re-upsert replaces, not duplicates.

> **Category prototypes** are seeded once (global, not per-tenant) from the
> canonical `.gov` references in `docs/COOP BC PLANS AND DOCS RESOURCES.md`
> (coop = CGC + FCD Planning Framework; bcp = NIST 800-34 + CISA ESS; compliance =
> CMS EP Rule + Appendix Z). A small `scripts/prep/seed_prototypes.py` builds them.

### M4 — Scoring (the math)
| File | Function | Notes |
|---|---|---|
| `app/scoring/thresholds.py` | weights + bands read from `config.py` | weights already in settings (`weight_content` … `weight_duplication`); bands `band_in_sync=71`, `band_reviewing=41`. |
| `app/scoring/signals.py` | one pure function per signal → `float` in `[0,1]`: `content_alignment`, `name_alignment`, `category_fit`, `extraction_quality`, `duplication` | pure + unit-testable; inputs are vectors/sims + quality meta. |
| `app/scoring/integrity.py` | `score(signals, quality) -> {status, score, components}` | weighted sum → 0–100; **hard overrides:** empty/scan-only caps status `Reviewing` & score ≤45; strong category mismatch forces `Deviation Found`. **Optional gated LLM judge** only when score ∈ `llm_judge_band` (e.g. 60–72). |

**Acceptance:** signal math in `[0,1]`; composite clamps to `[0,100]` int; status
maps to bands; overrides fire; component scores surfaced in `details.componentScores`.

### M5 — State (MongoDB `ai_*` collections in `ready2go`)
| File | Function | Notes |
|---|---|---|
| `app/store/models.py` | request-path Mongo accessor + `ensure_indexes()` | **Default driver: pymongo wrapped in `run_in_threadpool`** (ops are tiny; avoids adding `motor`). Collections: `ai_analysis_cache` (unique `(contentHash, modelVersion)`), `ai_audit_state` (`_id = tenantKey`), `ai_call_log`. Keep `app/store/mongo.py` as the prep-only connector. |
| `app/store/cache.py` | `get(content_hash, model_version)`, `put(record)` | the dedup short-circuit (headline cost lever). |
| `app/store/aggregate.py` | `update(tenant, verdict)` (O(1), set `dirty=true`), `read(tenant)` | rolling counts/integrity/scoreSum/notable[]. |
| `app/store/calllog.py` | `append(row)` | tokens, latency, success/error. |

**Acceptance:** identical-bytes second analyze ⇒ `cacheHit:true`, **zero** OpenAI
calls (assert via mocked client + `ai_call_log`); `ai_audit_state` updates O(1).

### M6 — Summaries
| File | Function | Notes |
|---|---|---|
| `app/summary/per_doc.py` | `one_liner(all_chunks, signals) -> str` (≤280) | grounded in the document's **complete** chunk set (via `get_all_chunks`), **not** a top-k subset (D7); map-reduce over chunks if they exceed the prompt budget. Backup line if LLM unavailable. |
| `app/summary/audit.py` | `build(state, sample) -> AuditSummaryResponse`; `derive_posture(state)` | bounded reduce: aggregate stats + capped sample of stored one-liners (`settings.audit_sample_cap`). Posture deterministic (`derivePosture` rules, PROJECT_CONTEXT §6.5). |

**Acceptance:** one-liner ≤280 + grounded; audit ≤360 / 2–4 findings ≤140;
**bounded-cost test**: comparable prompt token count at N=10 vs N=5000 (mocked).

### M7 — Wire the routes (flip the 501s)
| File | Change |
|---|---|
| `app/api/integrity.py` | implement `analyze` per §1 flow; implement `/rescan` (iterate `attachmentIds`, honour cache unless `force`). Return `AnalyzeResponse`. |
| `app/api/audit.py` | implement `summary` per §1 flow. Return `AuditSummaryResponse`. |

**Acceptance:** contract tests (status enum, score 0–100 int, summary ≤280); make
the test client **hermetic** (clear `PYTHON_INTEGRITY_TOKEN`) so the existing
`test_v1_contract_stub_returns_501_when_auth_disabled` is deterministic.

---

## 3. Cross-cutting requirements (apply to every module)

- **Tenant isolation, fail-closed:** every Weaviate + `ai_*` op carries `tenantKey`;
  a missing tenant is a hard error, never a silent global query.
- **Idempotency:** keyed by `attachmentId` + `contentHash`; re-runs are safe.
- **Resilience:** tenacity retries on OpenAI/Weaviate; on scoring failure return a
  clear `Reviewing` fallback (score ~50) **and log it** — the UI tolerates a
  pending badge.
- **Observability:** an `ai_call_log` row + structured log for **every**
  OpenAI/Weaviate call (tokens, latency, success).
- **Cost levers (ranked):** content-hash cache › bounded audit reduce › batched
  embeddings › debounced audit › gated LLM judge › cheap models › chunk cap.
- **Secrets:** env only (`OPENAI_API_KEY`, `WEAVIATE_URL`/`WEAVIATE_API_KEY`,
  `MONGODB_URI`); nothing hard-coded.

---

## 4. End-to-end verification

- `uv run pytest` green; `ruff` + `mypy` clean.
- **Per module:** the acceptance tests listed in §2.
- **Cache:** re-upload identical bytes ⇒ `cacheHit:true`, 0 OpenAI calls.
- **Isolation:** tenant A cannot read tenant B chunks.
- **Bounded audit:** token count flat at N=10 vs N=5000 (mocked corpus).
- **Staging E2E:** with `INTEGRITY_BACKEND=python`, upload one of each file type via
  the `/emergency-plan` UI → badge shows a real composite score + one-liner; the
  "AI-Driven Continuity Audit" stays short after many uploads.
- **Resilience:** kill Python mid-upload ⇒ upload still completes, badge stays
  `Reviewing`, `/v1/integrity/rescan` backfills.

---

## 5. Open (non-blocking) choices

- **Request-path Mongo driver:** pymongo + `run_in_threadpool` (default) vs adding
  `motor` (async-native). Revisit only if profiling shows the threadpool hop hurts.
- **OCR for scan-only PDFs:** v1 applies the quality penalty. The chosen *future*
  OCR path is **swapping the parser backend to `LiteParseParser`** (D5) — no rewrite
  of callers. Enable when scan-only volume warrants it.
- **Chunk size/overlap tuning:** start ~500/50; calibrate against the seeded corpus.
- **`inferCoopPlanMetadata` (planId/label/category inference):** stays in Next.js
  for v1; migrating to Python is a later phase.

---

## 6. Researched & evaluated (deferred / declined)

Two libraries were assessed against this architecture and **not adopted for v1**.
Recorded here with the trigger that would justify revisiting.

| Library | Decision | Why (against our design) | Revisit when |
|---|---|---|---|
| **LiteParse v2.0** (LlamaIndex; local, LLM-free parser, built-in OCR, Apache-2.0) | **Deferred — stay ready.** Slots in as `LiteParseParser` behind the `DocumentParser` protocol (D5). | Real draw is OCR + unified parsing, but our `.gov` corpus is overwhelmingly digital-text PDFs; it adds system deps (LibreOffice + ImageMagick) and is alpha (v2 repo tag still v1); bbox/screenshots unused in v1. | A meaningful volume of **scan-only PDFs** appears, or we want **visual citations**. Then enable the backend — no caller changes. |
| **Adaptive Chunking** (Ekimetrics; per-doc chunker selection, LREC 2026) | **Skipped for v1.** Single token-aware chunker instead (D6). | Its gains are **RAG retrieval quality on heterogeneous corpora**; we score by **averaging all chunks into a centroid** (boundary quality matters little) on a **homogeneous** corpus and don't do classic RAG. Alpha + heavy deps (sentence-transformers/spaCy/sklearn); its strongest metric needs a **non-commercial-licensed** coref model. | We add **corpus Q&A (real RAG)**, or measure **high chunker-choice variance** across a newly heterogeneous corpus. Cheap interim option: borrow the two embedding-free metrics (Size Compliance, Block Integrity) as a chunk-quality check. |
