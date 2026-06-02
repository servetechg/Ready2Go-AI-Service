# Ready2Go — Python AI Continuity Service: Architecture & Implementation Plan

> **Status:** Approved architecture baseline. Build proceeds **module by module**
> (see §10). Nothing is coded yet; this document is the contract we build against
> and revise as requirements evolve.
>
> **Companion doc:** `docs/PROJECT_CONTEXT.md` (the self-contained Next.js context).
> Section references like "PROJECT_CONTEXT §6.4" point there.

---

## 1. Context — why we are building this

### 1.1 The problem with the current pipeline

Today, on every continuity-document upload, the Next.js route
`app/api/admin/emergency-plans/route.ts` (verified: integrity call at **line 338**,
write-back at **lines 349–359**) does a single, un-cached OpenAI chat call to
score "AI Integrity" and writes `aiIntegrity{Status,Score,Summary,AnalyzedAt}`
onto the `EmergencyPlan.attachments[]` subdoc. A second call
(`generateContinuityAuditSummary`, `audit-summary/route.ts` lines 161–188)
produces the aggregate "AI-Driven Continuity Audit".

This has concrete, confirmed weaknesses (PROJECT_CONTEXT §6.8 / §14):

1. **Unbounded, un-persisted summary.** The audit narrative is rebuilt from
   scratch by re-walking `EmergencyPlan.find({})` and re-feeding per-document
   info to the LLM. With thousands of documents this grows long, slow, and
   expensive — and because **extracted text is persisted nowhere**, shortening
   or refreshing means reprocessing the whole corpus.
2. **Scoring is casual, not calculated.** A single LLM verdict, no similarity
   math, no content-vs-name signal, no category-fit check.
3. **8 000-char truncation + silent extraction failure** — long PDFs scored on
   their prefix; scan-only/empty files silently scored "35–55 conservatively".
4. **No caching** — re-uploading identical bytes re-spends tokens.
5. **No retries / no structured logging** — `callOpenAI` swallows errors and
   returns a static fallback; no audit trail of AI calls.

### 1.2 The goal

A separate, independently-deployable Python service that:

- **Ingests files into a vector DB** (Weaviate) once, chunked and embedded, so
  large corpora are handled without re-reading raw documents.
- Computes **AI Integrity as a real composite similarity score** (content
  alignment + name/slug alignment + category fit + extraction quality +
  duplication), not a single casual LLM verdict.
- Produces a **bounded, incremental audit summary** that stays short and cheap
  no matter how many documents exist — generated from **persisted aggregate
  state + a capped sample**, never by reprocessing the corpus.
- Is **cost-controlled** (content-hash dedup cache is the headline lever),
  **reliable** (retries, idempotency, structured logging), and **low-latency**
  (cache hits are instant; embeddings batched).

### 1.3 Locked decisions

| Decision | Choice |
|---|---|
| **Transport / writeback** | **REST, synchronous.** Python returns JSON `{status, score, summary}`; **Next.js keeps writing MongoDB** (existing `EmergencyPlan.updateOne` / `ContinuityAudit.findOneAndUpdate`). Lowest blast radius; Python never touches the app's Mongo. |
| **Vector DB** | **Weaviate** (Weaviate Cloud / WCD managed). Chosen for native multi-tenancy + hybrid (BM25 + vector) search. |
| **Embeddings / LLM** | **OpenAI** — `text-embedding-3-small` for vectors, `gpt-4o-mini` for summaries. Reuses existing `OPENAI_API_KEY`. |
| **Hosting** | **Managed PaaS** (Render / Railway / Fly.io) — container reachable from Vercel by URL. |

### 1.4 Non-goals (explicitly out of scope for v1)

- Replacing the other ~15 AI functions in `lib/services/openai-service.ts` (risk-assessment,
  dashboards, alerts). Those stay in Next.js (PROJECT_CONTEXT §6.5). This
  service is **only** integrity + audit. (Later phases may migrate more.)
- Changing the Mongo schema or the UI. The four `aiIntegrity*` field names and
  the `ContinuityAudit` shape are a **hard contract** (PROJECT_CONTEXT §12).
- Real-time websockets / Redis. REST-sync was chosen; we leave a clean seam to
  add a queue later if upload volume demands it.

---

## 2. High-level architecture

```
                          ┌───────────────────────────────────────────────┐
   Next.js (Vercel)       │            Python service (PaaS)               │
 ┌──────────────────┐     │  FastAPI + uvicorn  ── r2g-continuity-ai       │
 │ POST /emergency- │     │                                               │
 │   plans (upload) │     │  ┌──────────┐  ┌───────────┐  ┌────────────┐  │
 │  ...plan.save()  │     │  │ Ingest & │  │ Embedding │  │ Integrity  │  │
 │       │          │     │  │ extract  │─▶│ + Weaviate │─▶│  scorer    │  │
 │       ▼          │     │  └──────────┘  └───────────┘  └────────────┘  │
 │  HTTPS  ─────────┼────▶│        ▲             │              │         │
 │  POST /v1/       │ JSON│        │             ▼              ▼         │
 │   integrity/     │◀────┼────────┘      ┌────────────┐  ┌────────────┐  │
 │   analyze        │     │               │  Postgres  │  │  Summary / │  │
 │       │          │     │               │  (state,   │  │  audit gen │  │
 │       ▼          │     │               │  cache,    │  └────────────┘  │
 │ EmergencyPlan.   │     │               │  logs)     │                  │
 │  updateOne(...)  │     │               └────────────┘                  │
 └──────────────────┘     └───────────────────────────────────────────────┘
        │                                  ▲   ▲
        ▼                                  │   │  fetch file bytes
   MongoDB Atlas                    Cloudinary  (secure_url from payload)
 (Next.js owns writes)             (earthquick/emergency-plans)
```

**Key flows**

- **Per-file analyze (sync REST):** Next.js, after `plan.save()` (so
  `attachment._id` exists), calls `POST /v1/integrity/analyze` with plan context
  + the attachment's Cloudinary `fileUrl`. Python downloads bytes, hashes,
  checks cache, extracts+chunks+embeds (Weaviate), computes composite score +
  one-liner, returns JSON. **Next.js writes the four `aiIntegrity*` fields.**
- **Aggregate audit (sync REST):** Next.js calls `POST /v1/audit/summary` with
  the existing `ContinuityAuditInput` snapshot. Python merges it with its
  persisted aggregate state, generates a **bounded** narrative + findings +
  posture, returns `ContinuityAuditSummary`. **Next.js upserts `ContinuityAudit`.**

**Why Python does its own extraction (not reuse Next.js's 8 000-char text):**
fixing weaknesses #1/#3 requires full-document chunking and an
extraction-quality signal. Python fetches the file from Cloudinary and extracts
without the 8 000 cap, so scoring reflects the whole document. (Next.js may
still pass `extractedText` as a fast-path hint, but Python is authoritative.)

---

## 3. Tech stack & repository layout

### 3.1 Stack

| Concern | Choice | Notes |
|---|---|---|
| Language / runtime | **Python 3.12** | |
| Web framework | **FastAPI + uvicorn** | async, typed, OpenAPI for free |
| Validation | **Pydantic v2** | request/response contracts |
| HTTP client | **httpx** (async) | Cloudinary download, OpenAI |
| Retries | **tenacity** | exp backoff + jitter on OpenAI/Weaviate |
| Vector DB client | **weaviate-client v4** | WCD managed |
| Embeddings/LLM | **openai** SDK | `text-embedding-3-small`, `gpt-4o-mini` |
| Relational state | **Postgres** (managed add-on) + **SQLAlchemy 2 / psycopg** | dedup cache, aggregate state, call logs |
| Extraction | **pypdf**/**pdfplumber** (PDF), **python-docx** (DOCX), **openpyxl** (XLSX), stdlib `csv` | mirror Next.js coverage; OCR is a v2 option |
| Tokenizer | **tiktoken** | token budgeting for chunks & prompts |
| Logging | **structlog** | JSON structured logs |
| Tests | **pytest** + **respx**/**httpx mock** | |
| Container | **Docker** | deploy to Render/Railway/Fly |

### 3.2 Repo layout (`r2g-continuity-ai`)

```
r2g-continuity-ai/
├── app/
│   ├── main.py                 # FastAPI app, lifespan, router mount
│   ├── config.py               # pydantic-settings; all env vars
│   ├── security.py             # HMAC / bearer auth dependency
│   ├── api/
│   │   ├── integrity.py        # POST /v1/integrity/analyze, /rescan
│   │   ├── audit.py            # POST /v1/audit/summary
│   │   └── health.py           # /healthz, /readyz
│   ├── ingest/
│   │   ├── fetch.py            # Cloudinary download (httpx, hash)
│   │   ├── extract.py          # pdf/docx/xlsx/csv → text + quality meta
│   │   └── chunk.py            # token-aware chunking
│   ├── vectors/
│   │   ├── client.py           # Weaviate client + collection bootstrap
│   │   ├── schema.py           # collection/tenant definitions
│   │   └── repo.py             # upsert, hybrid search, prototypes
│   ├── scoring/
│   │   ├── integrity.py        # composite scorer (the math)
│   │   ├── signals.py          # content/name/category/quality/dup signals
│   │   └── thresholds.py       # calibrated bands (config-driven)
│   ├── summary/
│   │   ├── per_doc.py          # ≤280-char one-liner gen
│   │   └── audit.py            # incremental state + bounded map-reduce
│   ├── store/
│   │   ├── models.py           # SQLAlchemy tables
│   │   ├── cache.py            # content-hash dedup cache
│   │   ├── aggregate.py        # rolling audit state
│   │   └── calllog.py          # AI call audit trail
│   ├── llm/
│   │   ├── embeddings.py       # batched embeddings + retry
│   │   └── client.py           # chat completions + retry + token meter
│   └── schemas.py              # Pydantic request/response models
├── tests/
├── migrations/                 # alembic
├── Dockerfile
├── docker-compose.yml          # local: app + postgres + weaviate
├── pyproject.toml
└── README.md
```

---

## 4. Data model

### 4.1 Weaviate (vectors)

**Collection `DocChunk`**, **multi-tenant** (one tenant per `tenantKey` =
`licenseId` or `city|state|country`). Vector = `text-embedding-3-small` (1536-d).

| Property | Type | Purpose |
|---|---|---|
| `attachmentId` | text (indexed) | Mongo `attachments._id` — idempotency key |
| `planId` | text | grouping |
| `category` | text | `coop`/`bcp`/`compliance` (declared) |
| `fileName` | text | name signal + BM25 |
| `contentHash` | text | dedup / cache key |
| `chunkIndex` | int | order |
| `text` | text | the chunk (BM25 hybrid) |
| `modelVersion` | text | re-embed invalidation |

**Collection `CategoryPrototype`** (global, tiny): canonical reference text per
category (coop/bcp/compliance) used for **category-fit** scoring. Seeded once,
versioned.

> Tenancy lets us delete/replace all chunks for an attachment with a single
> tenant-scoped filtered delete, and keeps cross-tenant data isolated.

### 4.2 Postgres (Python-owned state — Next.js never reads it)

| Table | Key columns | Purpose |
|---|---|---|
| `analysis_cache` | `attachment_id`, `content_hash`, `model_version`, `status`, `score`, `summary`, `score_components(jsonb)`, `vector_ids(jsonb)`, `analyzed_at` | **dedup cache** — if `(content_hash, model_version)` unchanged → return cached verdict, skip embeddings + LLM. Headline cost lever. |
| `audit_state` | `tenant_key` (pk), `counts(jsonb)`, `integrity(jsonb)`, `score_sum`, `score_count`, `notable(jsonb)`, `dirty(bool)`, `updated_at` | **incremental aggregate** — O(1) update per analyze; powers bounded summaries without corpus reprocessing |
| `ai_call_log` | `id`, `ts`, `kind`, `attachment_id`, `model`, `prompt_tokens`, `completion_tokens`, `latency_ms`, `success`, `error` | observability / cost audit trail (fixes weakness #10) |

> Postgres + Weaviate are two managed add-ons. We *could* collapse state into
> Weaviate later; kept separate for clean relational queries on cache/logs.

---

## 5. API contract

All endpoints under `/v1`, auth via `Authorization: Bearer <PYTHON_INTEGRITY_TOKEN>`
**and/or** `X-Ready2Go-Signature: hex(HMAC_SHA256(secret, body))` (PROJECT_CONTEXT §11.A).

### 5.1 `POST /v1/integrity/analyze`

**Request** (matches PROJECT_CONTEXT §11 "Shared input payload"): `tenantContext`,
`plan{planId,label,overview,category,steps}`,
`attachment{attachmentId,fileName,fileExtension,fileMime,fileSizeBytes,fileUrl,cloudinaryPublicId,cloudinaryResourceType}`,
optional `extractedText` (fast-path hint), `vectorKey`.

**Response** (matches §11 "Shared response payload"):
```jsonc
{
  "status": "In Sync",            // "In Sync" | "Reviewing" | "Deviation Found"
  "score": 84,                    // integer 0..100
  "summary": "≤280 chars, plain text",
  "analyzedAt": "ISO-8601",
  "modelVersion": "integrity-v1",
  "details": {                    // optional, ignored by current UI
    "componentScores": { "content": 86, "name": 78, "category": 90, "quality": 95, "duplication": 70 },
    "similarFiles": [{ "attachmentId": "...", "similarity": 0.91 }],
    "cacheHit": false
  }
}
```
**Contract guardrails (must hold):** `status` is one of the three exact
case-sensitive strings; `score` integer clamped `[0,100]`; `summary ≤ 280` chars.
Next.js feeds these straight into the existing `updateOne`.

### 5.2 `POST /v1/audit/summary`

**Request:** the existing `ContinuityAuditInput` (PROJECT_CONTEXT §11.C) +
`tenantContext`. **Response:** `ContinuityAuditSummary`
`{ summary(≤360), findings[2..4, ≤140 each], posture, averageScore }`.

### 5.3 `POST /v1/integrity/rescan` (backfill / re-run)

`{ attachmentIds?: string[], tenantKey?, force?: bool }` → re-analyze (honor
cache unless `force`). Drives historical backfill (PROJECT_CONTEXT §11.5).

### 5.4 `GET /healthz` / `GET /readyz`

Liveness + dependency readiness (Weaviate, Postgres, OpenAI reachable).

---

## 6. The integrity scoring model (the "real calculation")

Replaces the single casual LLM verdict with a **weighted composite** of
independent signals, each in `[0,1]`. All weights/thresholds live in config so
they are tunable, not magic constants.

| Signal | How computed | Default weight |
|---|---|---|
| **contentAlignment** | cosine sim between the doc **content centroid** (mean of chunk embeddings) and the **plan-context embedding** (`label + overview + steps`) | 0.40 |
| **nameAlignment** | Weaviate **hybrid** score of `fileName`/`planId` slug vs plan label/category (semantic + BM25 keyword) | 0.15 |
| **categoryFit** | sim of content centroid to the declared category's `CategoryPrototype`, **penalized** if a *different* category prototype scores higher (mis-filed doc) | 0.20 |
| **extractionQuality** | deterministic: chars extracted / expected, chunk count, scan-only/empty detection. Replaces the "score 35–55 conservatively" hack with a real penalty | 0.15 |
| **duplication / intra-plan consistency** | nearest-neighbor sim to sibling attachments in the same plan; flags near-dups and incoherent additions | 0.10 |

```
raw   = Σ(weight_i × signal_i)              # 0..1
score = round(100 × raw)                    # 0..100, clamped
```

**Status banding** (config-driven, defaults aligned to current UI semantics,
PROJECT_CONTEXT §6.4):
- `score ≥ 71` → **In Sync**
- `41 ≤ score ≤ 70` → **Reviewing**
- `score ≤ 40` → **Deviation Found**
- **Hard overrides:** empty/scan-only extraction caps status at **Reviewing**
  and score ≤ 45; a strong category mismatch forces **Deviation Found**.

**Optional LLM judge (cost-gated):** only when the composite lands in a narrow
borderline band (e.g. 60–72) do we spend one cheap `gpt-4o-mini` rubric call to
break the tie. Most documents never trigger it → cost stays low.

> This makes the score explainable (`details.componentScores`) and calibratable,
> directly answering "proper calculation rather than casually defined".

---

## 7. The summary architecture (fixes the unbounded-summary problem)

### 7.1 Per-document one-liner

Generated once per `(contentHash, modelVersion)` and **persisted** (Postgres
cache + written to Mongo `aiIntegritySummary` by Next.js). ≤280 chars, grounded
in the top chunks + composite signals. Re-upload of identical bytes ⇒ cache hit,
**zero** new tokens.

### 7.2 Aggregate audit — incremental, bounded, never reprocesses

The core fix. The audit narrative is **not** "one appended line per document".

1. **Rolling state (O(1) per upload).** On each analyze, update
   `audit_state[tenant]`: category counts, integrity breakdown, running
   score sum/count, and a **bounded `notable[]` list** (e.g. top-K worst
   deviations, empty-category flags, plans missing steps/attachments). No corpus
   scan.
2. **Bounded reduce for the narrative.** When `POST /v1/audit/summary` fires,
   the LLM sees only: the **aggregate stats** + a **capped sample** of the most
   salient *stored* per-doc summaries (e.g. ≤25 items: worst scores + coverage
   gaps). Input size — and therefore cost and output length — is **constant**
   whether there are 10 or 10 000 documents.
3. **Map-reduce for huge vaults (optional scale path).** If a tenant grows very
   large, cluster stored per-doc summaries by category/plan, summarize each
   cluster (the "map") from **stored summaries only**, then reduce cluster
   summaries into the final ≤360-char narrative. Raw documents are never
   re-read.
4. **Throttled regeneration.** Mark state `dirty` on analyze; regenerate the
   narrative on the UI "refresh" action and/or a debounced background job — not
   on every single upload. Avoids per-upload LLM spend.
5. **Posture** stays deterministic (`derivePosture`, PROJECT_CONTEXT §6.5),
   computed from the rolling state — no LLM needed, thresholds in config.

> Result: the summary stays short and current, costs a bounded amount per
> refresh, and never requires reprocessing all uploaded documents.

---

## 8. Cross-cutting concerns

### 8.1 Cost levers (ranked)

1. **Content-hash dedup cache** — skip embeddings *and* LLM on unchanged bytes/
   model. Biggest lever (PROJECT_CONTEXT §11.5).
2. **Bounded audit reduce-input** (capped sample) — audit cost is O(1), not O(N).
3. **Batched embeddings** — embed all chunks of a doc in one API call.
4. **Throttled/debounced audit regeneration** — not per upload.
5. **Cost-gated LLM judge** — only for borderline scores.
6. **Cheap models** — `text-embedding-3-small` + `gpt-4o-mini`.
7. **Chunk cap per document** — bound worst-case embedding spend on huge files.

### 8.2 Reliability

- **Retries with exponential backoff + jitter** (tenacity) on OpenAI + Weaviate
  (fixes weakness #5).
- **Idempotency** by `attachmentId` + `contentHash`; re-runs are safe.
- **Graceful degradation** — if scoring fails, return a clear `Reviewing`
  fallback (score ~50) **and log it**; Next.js UI already tolerates a pending
  badge.
- **Circuit breaker + timeout on the Next.js→Python call** (e.g. 25 s) so an
  unhealthy Python service never hangs an upload; on timeout the badge stays
  `Reviewing` and `/v1/integrity/rescan` (or a retry) backfills it.

### 8.3 Latency

- Cache hit → near-instant. Cold path dominated by extract+embed; batched
  embeddings keep typical docs within a few seconds, comfortably inside the
  ≤30 s happy-path budget (PROJECT_CONTEXT §11 / §15.8). Async ack is acceptable
  because the UI shows `Reviewing` until populated.

### 8.4 Observability

- `ai_call_log` row + structured JSON log for **every** OpenAI/Weaviate call
  (tokens, latency, success) — fixes weakness #10.
- `/healthz` + `/readyz`; basic Prometheus-style counters (requests, cache-hit
  ratio, token spend) exposable later.

### 8.5 Security / multi-tenancy

- Shared-secret bearer + optional HMAC body signature on all `/v1` routes.
- Tenant isolation enforced at the Weaviate tenant boundary and on every
  Postgres query (`tenant_key`).
- OpenAI/Weaviate/Cloudinary creds via env only; no secrets in code.

### 8.6 Config / env vars (`app/config.py`)

`OPENAI_API_KEY`, `OPENAI_EMBED_MODEL`(=text-embedding-3-small),
`OPENAI_SUMMARY_MODEL`(=gpt-4o-mini), `WEAVIATE_URL`, `WEAVIATE_API_KEY`,
`DATABASE_URL`(Postgres), `PYTHON_INTEGRITY_TOKEN`, `HMAC_SECRET`,
`MODEL_VERSION`, scoring weights + thresholds, `MAX_CHUNKS_PER_DOC`,
`AUDIT_SAMPLE_CAP`, `LLM_JUDGE_BAND`, `REQUEST_TIMEOUT_S`.

---

## 9. Next.js-side changes (minimal, reversible)

Behind a **feature flag** `INTEGRITY_BACKEND=python|legacy` (PROJECT_CONTEXT §11.5):

1. In `app/api/admin/emergency-plans/route.ts` (~line 338): replace the
   `openaiService.analyzeCoopAttachmentIntegrity(...)` call with an `httpx`/`fetch`
   POST to `${PYTHON_URL}/v1/integrity/analyze`, then feed the JSON into the
   **existing** `EmergencyPlan.updateOne` block (lines 349–359 unchanged).
   Wrap in try/timeout; on failure keep current fallback behavior.
2. In `app/api/admin/emergency-plans/audit-summary/route.ts` `POST` (~line 170):
   replace `openaiService.generateContinuityAuditSummary(input)` with a POST to
   `${PYTHON_URL}/v1/audit/summary`; existing upsert (lines 173–188) unchanged.
3. New env vars in Next.js: `PYTHON_URL`, `PYTHON_INTEGRITY_TOKEN`,
   `INTEGRITY_BACKEND`.
4. **Do not delete** `inferCoopPlanMetadata` / `analyzeCoopAttachmentIntegrity` /
   `generateContinuityAuditSummary` yet — keep as `legacy` fallback (cleanup is a
   later phase, PROJECT_CONTEXT §15.10).

> Note: `inferCoopPlanMetadata` (planId/label/category inference) is **kept in
> Next.js** for v1 — it's a separate concern from integrity. Migrating it is an
> optional later phase.

### 9.1 Rollout strategy

**Shadow → flag → cutover** (PROJECT_CONTEXT §11.5):
1. **Shadow:** Python runs in parallel, results logged/compared (optionally
   written to `aiIntegrity*V2` fields) while UI still reads legacy. Validate
   parity for N days.
2. **Feature flag:** flip `INTEGRITY_BACKEND=python` per environment.
3. **Backfill:** `POST /v1/integrity/rescan` over existing `attachment._id`s so
   historical files get vector-scored.
4. **Cleanup:** once stable, remove legacy functions behind the flag.

---

## 10. Build sequence (module by module)

Each milestone is independently shippable and testable. Stop/adjust between any.

| # | Module | Outcome | Acceptance |
|---|---|---|---|
| **M0** | **Skeleton & infra** — FastAPI app, config, auth, Docker, `/healthz`, deploy to PaaS, provision WCD + Postgres | Service is live and authenticated | `GET /healthz` 200 from Vercel egress; auth rejects bad token |
| **M1** | **Ingestion & extraction** — Cloudinary fetch, hash, pdf/docx/xlsx/csv extraction + quality meta, token-aware chunking | Any allowed file → clean chunks + extraction-quality score | Golden-file tests for each type incl. scan-only/empty |
| **M2** | **Vectors** — Weaviate collections, multi-tenancy, batched embeddings, upsert, hybrid search, category prototypes seed | Doc chunks embedded + searchable per tenant | Upsert+query round-trip; tenant isolation test |
| **M3** | **Integrity scorer** — the 5 signals + composite + banding + overrides + optional gated LLM judge | `POST /v1/integrity/analyze` returns valid contract JSON | Contract tests (status/score/summary bounds); component scores explainable |
| **M4** | **Dedup cache** — `analysis_cache`, content-hash short-circuit, call logging | Re-upload of identical bytes = cache hit, 0 tokens | Second identical call asserts `cacheHit:true`, no OpenAI call |
| **M5** | **Per-doc summary** — ≤280-char grounded one-liner, persisted | Stable, bounded one-liners | Length + grounding tests |
| **M6** | **Audit** — rolling `audit_state`, bounded reduce summary, posture, throttle | `POST /v1/audit/summary` bounded cost/length at any N | Summary length + constant-cost test at N=10 and N=5000 (mocked) |
| **M7** | **Next.js integration** — flag, REST calls, timeout/fallback, env | End-to-end upload → Python → Mongo write → UI badge updates | Manual upload on staging shows real score + bounded audit |
| **M8** | **Rollout & backfill** — shadow compare, `/rescan`, observability dashboards | Production parity, historical files scored | Parity report; backfill completes |

---

## 11. Verification

- **Unit/contract (pytest):** extraction golden files; scorer signal math;
  contract bounds (status enum, score `[0,100]` int, summary ≤280); audit
  bounds (summary ≤360, 2–4 findings ≤140).
- **Cache test:** identical-bytes re-analyze ⇒ `cacheHit:true`, zero OpenAI
  calls (assert via mocked client + `ai_call_log`).
- **Bounded-cost test:** audit at N=10 vs N=5000 (mocked corpus) ⇒ comparable
  prompt token count (proves O(1) reduce).
- **Tenant isolation:** tenant A cannot retrieve tenant B chunks.
- **End-to-end (staging):** upload each file type via the `/emergency-plan` UI
  with `INTEGRITY_BACKEND=python`; confirm the badge shows a real composite
  score + one-liner, and the "AI-Driven Continuity Audit" stays short after many
  uploads.
- **Resilience:** kill Python mid-upload ⇒ upload still completes, badge stays
  `Reviewing`, `/rescan` backfills.

---

## 12. Open items to confirm as we build (non-blocking)

1. **Tenant key** — `licenseId` vs `city|state|country`? (default: `licenseId`,
   fallback to location triple). Affects Weaviate tenant granularity.
2. **OCR for scan-only PDFs** — v2 (e.g. Tesseract / a hosted OCR) or accept the
   low-quality penalty for v1? (default: penalty in v1.)
3. **Audit regeneration trigger** — UI button only, or also a debounced
   background refresh? (default: button + debounce.)
4. **Postgres vs single-store** — keep Postgres for state/cache/logs, or fold
   into Weaviate later? (default: Postgres for v1.)
5. **`inferCoopPlanMetadata` migration** — keep in Next.js (default) or move to
   Python in a later phase?

These have sane defaults and won't block M0–M3.
