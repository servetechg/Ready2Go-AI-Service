# Ready2Go AI Service — Next.js Integration Guide

> **Audience:** the Next.js engineering team integrating the COOP/BC ("continuity integrity")
> features. This guide describes **exactly what the Python AI service does, every endpoint, every
> field, every behaviour, and every database table involved**, so you can design and implement the
> Next.js side with full knowledge of the contract. It contains **no Next.js code on purpose** —
> the implementation logic is yours to design; this is the complete spec you build against.
>
> For the deep internals (why two databases, how chunking/embeddings work, scoring math) see the
> companion [SERVICE_DOCUMENTATION.md](./SERVICE_DOCUMENTATION.md). This guide is the integration
> contract; that one is the engineering manual.
>
> **⚠️ Hard cutover.** The contract below is the **single source of truth**. A hard cutover has
> already happened — the old verdict strings, the old audit keys, the old `/rescan` endpoint, the
> old `details.similarFiles` field, and the old single‑tenant collections are **no longer
> accepted/produced**. See [§9 Legacy → New mapping](#9-legacy--new-mapping-hard-cutover). Do not
> build against anything in older docs that contradicts this file.

---

## Table of Contents

1. [What the service is (and the integration boundary)](#1-what-the-service-is-and-the-integration-boundary)
2. [Authentication](#2-authentication)
3. [The end-to-end flow you implement](#3-the-end-to-end-flow-you-implement)
4. [Full endpoint reference](#4-full-endpoint-reference)
5. [Behavioural contracts you must respect](#5-behavioural-contracts-you-must-respect)
6. [How scoring & summaries work (so your UI can explain them)](#6-how-scoring--summaries-work-so-your-ui-can-explain-them)
7. [Data model & ownership (who writes what)](#7-data-model--ownership-who-writes-what)
8. [MongoDB tables — schemas you should know](#8-mongodb-tables--schemas-you-should-know)
9. [Legacy → New mapping (hard cutover)](#9-legacy--new-mapping-hard-cutover)
10. [Operational notes & failure handling](#10-operational-notes--failure-handling)

---

## 1. What the service is (and the integration boundary)

The Ready2Go AI Service is an internal **server-to-server microservice** (FastAPI/Python, default
port `8000`). It scores whether an uploaded document actually belongs in the continuity plan it was
filed under, writes a plain-English review summary, and produces an organisation-wide audit
narrative.

- **Next.js is the only caller.** End users never talk to the AI service directly — your Next.js
  server calls it on their behalf, over HTTPS, on a trusted/private network.
- **It analyses three plan categories:** `coop` (Continuity of Operations), `bcp` (Business
  Continuity Plan), `compliance` (regulatory/policy). The UI may also show a synthetic `response`
  bucket — see [§5](#5-behavioural-contracts-you-must-respect).
- **It is stateless in itself.** All durable state lives in MongoDB (its own `ai_*` collections),
  in Weaviate (the vector store), and in log files. You can restart it without losing data.
- **What you send it:** plan context (label, overview, category, steps), the attachment metadata,
  and a downloadable `fileUrl`. The service downloads and reads the file itself — you do **not** send
  file bytes or extracted text (an `extractedText` field exists but is **ignored** today).
- **What it gives you back:** a `status`, a `score` (0–100), four component scores, a multi-section
  `summary`, and — separately, on demand — duplicate/similar files and an org audit narrative.

The integration boundary is deliberately clean:

| Side | Owns / does |
|------|-------------|
| **Next.js** | Owns the business documents (`continuityplans`, `continuityauditreports`), file storage (Cloudinary), the UI, and **writes the AI results back** onto its own documents. Decides when to call the AI service. |
| **AI service** | Owns its `ai_*` MongoDB collections + the Weaviate vector store. Does all embedding/scoring/summarising. **Never writes your business documents.** |

---

## 2. Authentication

Every `/v1/*` route requires auth. Health/metrics endpoints are open.

- **Bearer token (required).** Send `Authorization: Bearer <PYTHON_INTEGRITY_TOKEN>`. This is a
  shared secret configured identically on the AI service and on your Next.js server. It is compared
  in constant time. **Rotate** it by redeploying both sides with a new value.
- **HMAC body signature (optional).** If the service has `HMAC_SECRET` configured, you must also send
  `X-Ready2Go-Signature: hex(HMAC_SHA256(HMAC_SECRET, rawRequestBody))`, computed over the **exact
  raw bytes** of the request body you send. If `HMAC_SECRET` is not configured, omit this header.
- **Production fail-closed.** In production, if the token is missing the service rejects every
  request (and refuses to boot). There is no "auth disabled" mode in production.
- **401** is returned for a missing/invalid bearer token or a bad HMAC signature.

There is intentionally **no JWT / per-end-user identity** at this hop — the user already
authenticated to Next.js, and Next.js is the single trusted caller. If you want to record *who*
triggered an analysis for your own audit trail, pass it as `actorUserId` (it's stored for the trail
only and does not affect results).

---

## 3. The end-to-end flow you implement

This is the lifecycle you wire up on the Next.js side. (Described, not coded — implement it however
fits your stack.)

**A. When a subadmin uploads a document to a plan:**

1. Store the file (Cloudinary) and push an `attachments[]` sub-document onto the plan in
   `continuityplans` as you do today. The sub-doc's `_id` **is the `attachmentId`** you'll use
   everywhere below.
2. Call **`POST /v1/integrity/analyze`** with the plan context + attachment metadata. It returns
   **HTTP 202** immediately with `{state:"processing", attachmentId, pollUrl}` — it does **not**
   block while the (possibly 30–90s) pipeline runs.
3. **Poll `GET /v1/integrity/result/{attachmentId}`** (≈ every 2s) until `state` is no longer
   `processing`:
   - `state:"done"` → read `result` (an `AnalyzeResponse`) and write its fields onto the attachment
     (see the `aiIntegrity*` mapping in [§7](#7-data-model--ownership-who-writes-what)).
   - `state:"error"` → surface `detail` to the user and allow a re-submit (re-`POST /analyze`).
   - If you exceed your own polling budget (e.g. > 5 min), just `POST /analyze` again — it overwrites
     the job and is cheap on a cache hit.

**B. When you want to show duplicates / related files for a document:**

- Call **`GET /v1/integrity/similar/{attachmentId}?tenantKey=...`** on demand (e.g. when the user
  opens the document). It returns the live list of exact duplicates + semantic near-duplicates
  across the whole tenant vault.

**C. When a subadmin deletes a document:**

- Call **`DELETE /v1/integrity/attachments`** with `{tenantKey, attachmentIds:[...]}` so the AI
  service purges its cache, audit entry, job row, and vectors for that document. (Then remove the
  attachment from `continuityplans` on your side as usual.)

**D. When you want to refresh the organisation audit:**

- Build the aggregate payload from your `continuityplans` data and call **`POST /v1/audit/summary`**.
  Upsert the returned narrative into `continuityauditreports`.

**E. To re-analyse a document from scratch** (e.g. after a model change or a known-bad result):

- Call `DELETE /v1/integrity/attachments` for it (clears the cache), then `POST /analyze` again.
  (There is no `/rescan` endpoint anymore.)

---

## 4. Full endpoint reference

Base URL = the service host (e.g. `http://ai-service:8000`). All bodies are JSON. **Send camelCase**
field names. The service accepts both camelCase and snake_case, but camelCase is the documented
contract; responses are emitted in the camelCase aliases shown.

`tenantKey` convention everywhere: **`"sub_" + ownerUserId`** (the subadmin's Mongo `User._id` hex).
A blank/missing `tenantKey` is rejected with **HTTP 400** on every endpoint that needs it.

### 4.1 `POST /v1/integrity/analyze` — queue a document for scoring (async, 202)

Validates the request, queues the pipeline to run in the background, and returns **202** immediately.

**Request body:**

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `tenantContext.tenantKey` | string | ✅ | `"sub_"+ownerUserId`. Tenant isolation + audit-state key. Blank → 400. |
| `tenantContext.actorUserId` | string \| null | ❌ | Who triggered it (recorded for your trail only; does not affect the result). |
| `plan.planId` | string | ✅ | The plan the file is filed under. Groups sibling files for duplication. |
| `plan.label` | string | ✅ | Plan name. Used by the content + name signals. |
| `plan.overview` | string | ❌ (default `""`) | Plan description. Folded into the content-alignment text. The richer, the better the scoring. |
| `plan.category` | `"coop"\|"bcp"\|"compliance"` | ❌ (default `"coop"`) | Plan type. Used in the name-match query, chunk metadata, and audit counts. |
| `plan.steps` | string[] | ❌ (default `[]`) | Plan procedure steps. Folded into the content-alignment text. |
| `attachment.attachmentId` | string | ✅ | The document id (your `attachments._id`). Idempotency key in Weaviate + cache. |
| `attachment.fileName` | string | ✅ | Original filename. Drives the name signal and feeds the summary. |
| `attachment.fileExtension` | `"pdf"\|"docx"\|"csv"\|"xlsx"` | ✅ | Selects the text parser. Only these four are supported. |
| `attachment.fileUrl` | string | ✅ | Where the service downloads the bytes from (your Cloudinary secure URL). |
| `attachment.fileMime` | string \| null | ❌ | Informational. |
| `attachment.fileSizeBytes` | int \| null | ❌ | Informational. |
| `attachment.cloudinaryPublicId` | string \| null | ❌ | Informational. |
| `attachment.cloudinaryResourceType` | string \| null | ❌ | Informational. |
| `extractedText` | string \| null | ❌ | **Reserved — ignored in v1.** The service extracts text itself. |
| `vectorKey` | string \| null | ❌ | **Reserved — ignored in v1.** |

**202 response (`AnalyzeAccepted`):**

| Field | Type | Meaning |
|-------|------|---------|
| `state` | `"processing"` | Always `processing` on the ack. |
| `attachmentId` | string | Echoes your attachment id — poll with it. |
| `pollUrl` | string | Convenience path, e.g. `/v1/integrity/result/att_001`. |

**Errors:** `400` if `tenantKey` is blank. (A bad `fileUrl` does **not** fail here — it surfaces
later as a job `error` on `/result`.)

### 4.2 `GET /v1/integrity/result/{attachmentId}` — poll for the verdict

Poll ≈ every 2s until `state != "processing"`. Always **HTTP 200** while a job (or a cached verdict)
exists; **404** only if the attachment was never submitted and has no cached verdict.

**Response (`AnalyzeResultEnvelope`):**

| `state` | Meaning | `result` | `detail` |
|---------|---------|----------|----------|
| `processing` | Pipeline still running — keep polling. | `null` | `null` |
| `done` | Finished — use `result`. | `AnalyzeResponse` (below) | `null` |
| `error` | Hard failure (e.g. file unreachable, or interrupted by a restart). Re-submit to retry. | `null` | human-readable reason |

> A **timeout or transient dependency failure still resolves to `done`** with a graceful *degraded*
> result (`result.details.degraded = true`) — not `error`. `error` is reserved for hard failures
> like an unfetchable `fileUrl` or a job interrupted by a service restart.

**`result` payload (`AnalyzeResponse`):**

| Field | Type | Meaning |
|-------|------|---------|
| `status` | `"Compliant"\|"Under Review"\|"Non-Compliant"` | The headline verdict. **Exact strings** — your UI keys off them. |
| `score` | int 0–100 | Composite integrity score. |
| `summary` | string ≤ 2000 | A plan-review write-up in **three labeled sections** — `Overview`, `What went well`, `Areas for improvement` — separated by blank lines. Carries **no** score/status language. **Contains newlines — render preserving line breaks** (e.g. `white-space: pre-line`). |
| `analyzedAt` | datetime (ISO) | When this verdict was produced. |
| `modelVersion` | string | e.g. `integrity-v1`. Lets you compare results over time. |
| `details.componentScores` | object \| null | `{content, name, quality, duplication}`, each int 0–100 (or null). The "why" behind the score. |
| `details.cacheHit` | bool | True = recognised these exact file bytes and returned the saved verdict (0 AI tokens). |
| `details.degraded` | bool | True = a needed dependency was unreachable, so the result is **provisional and was not cached**; it will be recomputed next time. |

> **Note:** the analyze result does **not** contain similar/duplicate files. Fetch those separately
> from [§4.3](#43-get-v1integritysimilarattachmentid--live-duplicate--related-files).

### 4.3 `GET /v1/integrity/similar/{attachmentId}` — live duplicate / related files

Computes, live against the vector store, the files most similar to one attachment across the whole
tenant vault. **Never 500s** (returns a partial/empty list on a vector-store error) and **never
returns stale/deleted files**.

**Params:**

| Param | In | Type | Required | Meaning |
|-------|-----|------|----------|---------|
| `attachmentId` | path | string | ✅ | The document to find neighbours for. |
| `tenantKey` | query | string | ✅ | Tenant scope. Blank → 400. |

**Two tiers (merged & de-duplicated, sorted by similarity desc, capped at `SIMILAR_MAX_RESULTS` = 5):**

1. **Exact duplicates** — other attachments in the tenant sharing this file's `contentHash`. Returned
   with `similarity = 1.0`, `exactDuplicate = true`.
2. **Semantic near-duplicates** — vault-wide vector search on the document's centroid, filtered to
   the current `modelVersion`; matches below `SIMILAR_MIN_SIMILARITY` (= 0.55) are dropped.

**Response (`SimilarFilesResponse`):**

| Field | Type | Meaning |
|-------|------|---------|
| `attachmentId` | string | Echoes the queried document. |
| `similar[]` | array | Matches, best first. Each entry: |
| `similar[].attachmentId` | string | The matched document's id. |
| `similar[].fileName` | string | The matched file's name. |
| `similar[].planId` | string | The plan the match is filed under. |
| `similar[].similarity` | float 0–1 | `1.0` for an exact byte duplicate, else cosine similarity. |
| `similar[].exactDuplicate` | bool | True = byte-for-byte identical contents. |

### 4.4 `DELETE /v1/integrity/attachments` — permanently purge documents

Call when a document is deleted. For each id, within the tenant scope, it removes the cache
verdict(s), the per-attachment audit entry, the analyze-job row, and the Weaviate chunks. Each id is
purged independently. Returns **HTTP 200**.

**Request body:**

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `tenantKey` | string | ✅ (non-blank) | Tenant scope. Blank → 400. |
| `attachmentIds` | string[] \| null | ❌ | Ids to purge. Empty/missing → no-op (`{deleted:0, notFound:0}`). |

**Response:**

| Field | Type | Meaning |
|-------|------|---------|
| `deleted` | int | Attachments that had a cached verdict removed. |
| `notFound` | int | Attachments that had no cached verdict to remove (already absent). |

> A Weaviate delete failure for one id is logged but does **not** fail the call — the Mongo purge
> still counts and the rest of the ids are still processed.

### 4.5 `POST /v1/audit/summary` — organisation-wide audit narrative

Produces a bounded, content-driven audit paragraph + findings + a deterministic posture for the
whole tenant. It primarily reads the AI service's **own rolling audit state** for the tenant; the
payload you send supplies the headline totals and acts as the deterministic fallback if anything
fails. **Always HTTP 200** (never 500 — on internal failure it returns a payload-derived fallback
flagged `degraded:true`).

**Request body (`AuditSummaryRequest`):**

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `tenantContext.tenantKey` | string \| null | ❌ | Tenant whose rolling state to read. |
| `totals` | `{plans, attachments, analyzed}` (ints) | ✅ | Headline counts. |
| `averageScore` | int | ❌ (default 0) | Org average you computed. |
| `counts` | `{coop, bcp, compliance, response}` (ints) | ✅ | Files per plan type. |
| `integrity` | `{compliant, underReview, nonCompliant, unanalyzed}` (ints) | ✅ | Files per verdict bucket. **Must use these keys** — legacy `{inSync, reviewing, deviation}` are **not accepted** (read as 0). |
| `plans[]` | `AuditPlan[]` | ❌ (default `[]`) | Per-plan detail (below). |

`AuditPlan`: `{planId, label, category ("coop"\|"bcp"\|"compliance"\|"response"), attachmentCount,
stepCount, attachments[]}`.
`AuditPlan.attachments[]` (sampled, worst-scoring): `{fileName (req), status?, score?, summary?}`.

**Response (`AuditSummaryResponse`):**

| Field | Type | Meaning |
|-------|------|---------|
| `summary` | string ≤ 1500 | Content-driven narrative of what the plans collectively cover, with overall strengths and gaps. **No scores/verdicts in the prose.** |
| `findings` | string[] (4–8, each ≤ 350) | **Balanced** bullets — both strengths and areas for improvement. |
| `posture` | `"Resilient"\|"Steady"\|"At Risk"` | Deterministic overall readiness rating (see [§6](#6-how-scoring--summaries-work-so-your-ui-can-explain-them)). |
| `averageScore` | int | Org average integrity score. |
| `degraded` | bool | True = the narrative used a **reduced fallback sample** (or a payload-derived fallback) and may be less complete; it regenerates fully next run. |

### 4.6 Operational endpoints

| Method & path | Auth | Returns |
|---------------|------|---------|
| `GET /healthz` | none | `{"status":"ok"}` — liveness only (no dependency checks). |
| `GET /readyz` | none | `{status, env, modelVersion, dependencies:{weaviate, mongodb, openai}}` — live connectivity probes; `status:"degraded"` if any configured dependency fails. Use this for orchestration readiness gating, not `/healthz`. |
| `GET /metrics` | none | In-process counters: `requests_total`, `cache_hits/misses`, `cache_hit_ratio`, `pipeline_errors`, `pipeline_timeouts`, `tokens_total`, `avg_latency_ms` (reset on restart). |
| `GET /v1/diagnostics/calls?attachmentId=&limit=` | **required** | Recent `ai_call_log` rows (`limit` default 50, max 200) for cost/error tracing. Filter by `attachmentId` to see one document's AI spend. |

---

## 5. Behavioural contracts you must respect

- **Verdict strings are exact UI keys.** `status` is always exactly `Compliant`, `Under Review`, or
  `Non-Compliant`. Don't normalise case/spacing — match them exactly.
- **Score is 0–100 (int).** Banding: `≥ 71` → Compliant, `41–70` → Under Review, `< 41` →
  Non-Compliant (configurable on the service via `BAND_COMPLIANT` / `BAND_UNDER_REVIEW`).
- **Component scores explain the score.** `content` (weight 50%), `name` (19%), `quality` (19%),
  `duplication` (12%). Great material for "why did it score this?" tooltips.
- **Summaries contain newlines.** The per-doc `summary` has three labeled sections separated by blank
  lines. **Render with line breaks preserved** or it will look like one run-on paragraph.
- **`degraded:true` means provisional.** A dependency (OpenAI/Weaviate) was unreachable or the run
  timed out. The result was **not cached**; show it as "still being reviewed" rather than final, and
  it will be recomputed on the next analyze. Do not treat a degraded score as authoritative for the
  audit.
- **`cacheHit:true` means free + instant.** The exact same file bytes (same `modelVersion`) were
  analysed before. Same verdict, zero AI cost.
- **`tenantKey` is mandatory** on analyze, similar, delete (non-blank). It scopes every vector/cache
  operation. Always `"sub_" + ownerUserId`.
- **`attachmentId` is your idempotency key.** Re-analysing the same attachment **replaces** its
  vectors and its audit entry — it is never double-counted. Re-using an id for a different file is a
  mistake.
- **Category vocabulary.** Store only `coop | bcp | compliance` on plans. The UI's fourth `response`
  bucket is **synthetic** (inferred client-side from the plan, e.g. by `planId`) — never persist
  `"response"` as a plan category, though the audit endpoint *does* accept `response` in its
  `counts`/`AuditPlan.category` for display.
- **Posture is deterministic, not AI.** `At Risk` if there are no plans, any Non-Compliant docs, an
  average < 55, or nothing analysed; `Steady` if any Under Review or average < 75; else `Resilient`.
  You can predict/replicate it client-side if needed.
- **Audit `integrity` keys are the new ones only.** Send `{compliant, underReview, nonCompliant,
  unanalyzed}`. The old keys are silently read as 0 — which skews posture and the fallback narrative.

---

## 6. How scoring & summaries work (so your UI can explain them)

You don't need to reimplement any of this, but understanding it lets your UI present accurate "why"
copy and tooltips.

**The composite score = a weighted blend of four independent signals, each 0–1:**

| Signal | Weight | What it measures | Intuition |
|--------|--------|------------------|-----------|
| **content** | 0.50 | Does the document's *meaning* match the plan? | Cosine similarity of the document's averaged embedding vs the plan's "label + overview + steps". The dominant factor. |
| **name** | 0.19 | Does the filename match the plan? | Cosine similarity of the cleaned filename vs the plan's "label + category". Absolute & per-document — a good file can't be penalised just because other files exist. |
| **quality** | 0.19 | Did we extract real text? | Deterministic from extraction: empty/scanned-image files score very low; rich text scores high. |
| **duplication** | 0.12 | Is it a near-duplicate of a sibling in the same plan? | Unique file → high; near-identical sibling → low. |

**From signals to a verdict:** weighted sum → 0–100 score → banded into a status. Two extra rules:
- **Empty / scan-only override:** such documents are capped at score 45 and land in `Under Review`
  (or `Non-Compliant` if below the review band) regardless of the blend.
- **Borderline LLM judge:** if the score lands in the `60–72` band and an OpenAI key is configured,
  a cheap `gpt-4o-mini` call may confirm/adjust the `{status, score}`. Most documents never trigger
  it; if it fails, the computed values stand.

**Per-document summary** (the `summary` field): a plan-review in three labeled sections —
`Overview` (what the document is + the response actions it describes), `What went well` (genuine
strengths), `Areas for improvement` (concrete gaps). It deliberately carries no scores/verdicts (the
`status`/`score` fields cover that). Long documents are summarised via concurrent map-reduce.

**Organisation audit** (`POST /v1/audit/summary`): the service reads its rolling per-tenant state,
samples documents (worst-scoring first), and asks the LLM for a **content-driven** narrative +
balanced findings. The numeric `averageScore` and the deterministic `posture` come from the rolling
state. The narrative never mentions scores/verdicts — it describes coverage and readiness.

---

## 7. Data model & ownership (who writes what)

```
        ┌──────────────────────────── Next.js OWNS ────────────────────────────┐
        │  continuityplans            (plans + attachments[] + aiIntegrity* )   │
        │  continuityauditreports     (org audit narrative per tenant)          │
        │  Cloudinary                 (the actual files)                        │
        └───────────────────────────────────────────────────────────────────────┘
                                  │  calls /v1/* (Bearer + optional HMAC)
                                  ▼
        ┌─────────────────────────── AI service OWNS ──────────────────────────┐
        │  ai_analysis_cache   ai_audit_state   ai_call_log   ai_analysis_jobs  │  (MongoDB, same DB)
        │  DocChunk            (Weaviate vector store, multi-tenant)            │
        └───────────────────────────────────────────────────────────────────────┘
```

**The collections in play for you are three** (plus the AI service's `ai_*` ones you only read for
diagnostics):

1. **`continuityplans`** *(Next.js writes)* — your plans, each with an `attachments[]` array. **You
   write four fields onto each attachment from the analyze `result`:**

   | Attachment field | Source from `AnalyzeResponse` |
   |------------------|-------------------------------|
   | `aiIntegrityStatus` | `result.status` (`Compliant` / `Under Review` / `Non-Compliant`) |
   | `aiIntegrityScore` | `result.score` (0–100) |
   | `aiIntegritySummary` | `result.summary` (the three-section text) |
   | `aiIntegrityAnalyzedAt` | a timestamp when you applied the result (or `result.analyzedAt`) |

   The attachment sub-document's `_id` is the `attachmentId` you send to the AI service.

2. **`continuityauditreports`** *(Next.js writes)* — upsert the `POST /v1/audit/summary` result here
   (one report per subadmin/tenant): `{summary, findings, posture, averageScore, totals, integrity,
   generatedAt}`.

3. **`continuityplans` again for audit input** — when calling `POST /v1/audit/summary` you build the
   payload (`totals`, `counts`, `integrity`, `plans[]`) by aggregating over your `continuityplans`
   documents and their attachments' `aiIntegrity*` fields.

> **Tenancy.** Both collections are tenant-aware via `ownerUserId` (the subadmin's `User._id`). The
> AI service identifies the same tenant as `tenantKey = "sub_" + ownerUserId`. Keep that derivation
> consistent — it's how the AI service isolates vectors, cache, and audit state per customer.

The AI service **never** writes `continuityplans` / `continuityauditreports` at request time. The
only place this repo writes them is the offline dev/staging **seed scripts** (`scripts/prep/*`).

---

## 8. MongoDB tables — schemas you should know

All collections live in the **one** `ready2go` database. The AI service's collections are prefixed
`ai_`; yours are the continuity collections. Field-level detail for the AI-owned tables (you read
these only for diagnostics — never write them):

**`ai_analysis_cache`** — dedup cache; a hit returns the saved verdict for 0 tokens.
`{attachmentId, contentHash, modelVersion, status, score, summary, scoreComponents{content,name,quality,duplication}, vectorIds[], analyzedAt}`.
Unique index `(contentHash, modelVersion)`; index `attachmentId`.

**`ai_audit_state`** — one document per tenant (`_id = tenantKey`). Holds a **per-attachment map**:
`documents.<attachmentId> = {category, status, score, fileName, planId, summary(≤600 chars), updatedAt}`.
All audit aggregates are **derived on read** from this map (no stored counters). Re-analyse →
replaces the entry (idempotent); delete → `$unset`s it (it leaves the audit). This is why the audit
average can never drift above 100 and deleted docs actually disappear.

**`ai_call_log`** — append-only AI-cost trail:
`{ts, kind("embed"|"chat"), attachmentId, model, tokens, latency_ms, success, error}`. Index `ts`
desc + `attachmentId`. Surfaced via `GET /v1/diagnostics/calls`.

**`ai_analysis_jobs`** — async job tracking (`_id = attachmentId`):
`{tenantKey, modelVersion, state("processing"|"done"|"error"), result, detail, startedAt, updatedAt}`.
This is what `GET /result/{id}` reads. Stale `processing` jobs (from a crash mid-run) are reaped to
`error` on startup so your poller is never stuck forever.

**Weaviate `DocChunk`** *(service-internal; you never touch it)* — multi-tenant vector store, one
object per ~500-token chunk: properties `{attachmentId, planId, category, fileName, contentHash,
chunkIndex, text, modelVersion}` + a 1536-dim OpenAI vector. The exact-duplicate detection in
`/similar` keys off `contentHash`; the semantic search uses the vectors filtered by `modelVersion`.

**Your collections** (schemas you implement/own — summarised here for completeness):

- `continuityplans`: `{ownerUserId, planId, label, overview, category(coop|bcp|compliance), steps[],
  attachments[{ _id, fileName, fileUrl, size, uploadedAt, cloudinaryPublicId, cloudinaryResourceType,
  aiIntegrityStatus, aiIntegrityScore, aiIntegritySummary, aiIntegrityAnalyzedAt }], createdAt, updatedAt}`.
- `continuityauditreports`: `{ (tenant scope: ownerUserId), summary, findings[], posture, averageScore,
  totals{plans,attachments,analyzed}, integrity{compliant,underReview,nonCompliant,unanalyzed}, generatedAt}`.

---

## 9. Legacy → New mapping (hard cutover)

A hard cutover has happened. The left column is **gone / not accepted** — build only against the
right column.

| Area | Old (removed / not accepted) | New (the only accepted form) |
|------|------------------------------|------------------------------|
| Verdict status string | `In Sync` | `Compliant` |
| | `Reviewing` | `Under Review` |
| | `Deviation Found` | `Non-Compliant` |
| Audit `integrity` keys | `{inSync, reviewing, deviation, unanalyzed}` | `{compliant, underReview, nonCompliant, unanalyzed}` |
| Band env names (service) | `BAND_IN_SYNC`, `BAND_REVIEWING` | `BAND_COMPLIANT`, `BAND_UNDER_REVIEW` |
| Re-run/clear endpoint | `POST /v1/integrity/rescan` | `DELETE /v1/integrity/attachments` (clears), then `POST /analyze` |
| Similar/duplicate files | `analyzeResponse.details.similarFiles` | `GET /v1/integrity/similar/{attachmentId}?tenantKey=` |
| Analyze response shape | synchronous `200` with verdict | async `202` + poll `GET /result/{id}` |
| Plan collection | single-tenant `EmergencyPlan` | tenant-aware `continuityplans` (`ownerUserId`) |
| Audit collection | singleton `ContinuityAudit` (`scope:"global"`) | per-tenant `continuityauditreports` |

If a request still sends an old shape (e.g. the legacy `integrity` keys), those values are read as
**0** — there is no alias/fallback. Deploy the Next.js side in lockstep with the new contract.

---

## 10. Operational notes & failure handling

- **Shared secrets.** `PYTHON_INTEGRITY_TOKEN` (and `HMAC_SECRET` if used) must match on both sides.
  `OPENAI_API_KEY`, `WEAVIATE_URL`, and `MONGODB_URI` are the AI service's secrets — you don't send
  them, but the AI service needs them (it shares the same `ready2go` Mongo database as your app).
- **Polling budget & re-submit.** Poll `/result` ≈ every 2s. If you exceed your budget (e.g. > 5
  min), re-`POST /analyze` — it overwrites the job and is cheap on a cache hit. After a service
  restart, an in-flight job becomes `error` ("interrupted… re-submit to retry") — handle that by
  re-submitting.
- **Concurrency.** The service caps concurrent heavy pipelines (`ANALYZE_CONCURRENCY`, default 3);
  extra analyze requests queue rather than overload it. You can fire many `POST /analyze` calls; they
  won't all run at once, but each returns its 202 immediately.
- **Timeouts.** A single background run is bounded (`ANALYZE_TIMEOUT_S`, default 300s). On timeout you
  get a graceful `done` + `degraded:true` result, not an error.
- **Never assume a 500 is fatal data loss.** `/analyze` (202), `/result`, `/similar`, and
  `/audit/summary` are designed to degrade gracefully, not 500. A genuine hard failure shows up as a
  job `state:"error"` with a `detail`, or `degraded:true` on the result — surface those to the user
  and allow a retry.
- **What forces a fresh analysis.** Identical file bytes + same `modelVersion` → cache hit (no
  re-work). To force a recompute, `DELETE /attachments` (clears the cache) then `POST /analyze`. The
  AI service team can also bump `MODEL_VERSION` to invalidate every cached verdict at once.
- **Cost/debug visibility.** Use `GET /v1/diagnostics/calls?attachmentId=` to see exactly which AI
  calls a document incurred (tokens, latency, success/error). Use `GET /readyz` to confirm the
  service's dependencies (Weaviate/Mongo/OpenAI) are healthy before relying on it.

---

*This guide tracks the `main` branch of the AI service and is the integration source of truth. If an
endpoint, field, status string, or collection changes, this file is updated alongside
[SERVICE_DOCUMENTATION.md](./SERVICE_DOCUMENTATION.md).*
