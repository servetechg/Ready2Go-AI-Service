# Ready2Go AI Service — Complete Service Documentation

> **Read this first.** This is the single "go‑to" document for the Ready2Go AI Service. It is
> written so that a person with **zero prior knowledge** of this codebase (or even of vector
> databases and embeddings) can read it top to bottom and understand *what the service does, how
> every piece works, and why each decision was made.* It also doubles as the reference manual for
> any backend developer extending the service.
>
> Audience: new engineers, backend devs, the Next.js team integrating with this service, and
> ourselves six months from now.

---

## Table of Contents

1. [What Is This Service? (Plain English)](#1-what-is-this-service-plain-english)
2. [The Big Picture (Architecture)](#2-the-big-picture-architecture)
3. [Core Concepts Glossary](#3-core-concepts-glossary)
4. [What Is a "Doc Chunk"?](#4-what-is-a-doc-chunk)
5. [The Two Databases — Why Both?](#5-the-two-databases--why-both)
6. [End‑to‑End Request Flow (The 12‑Step Pipeline)](#6-end-to-end-request-flow-the-12-step-pipeline)
7. [The Scoring Engine (4 Signals)](#7-the-scoring-engine-4-signals)
8. [Summarization (Per‑Doc & Audit)](#8-summarization-per-doc--audit)
9. [API Reference for Next.js](#9-api-reference-for-nextjs)
10. [Authentication — Why Shared‑Secret, Not JWT](#10-authentication--why-shared-secret-not-jwt)
11. [Where All Data Lives](#11-where-all-data-lives)
12. [Logging & Observability](#12-logging--observability)
13. [Configuration Reference](#13-configuration-reference)
14. [Complete File‑by‑File Guide](#14-complete-file-by-file-guide)
15. [Running, Testing & Local Dev](#15-running-testing--local-dev)
16. [Design Decisions & FAQ](#16-design-decisions--faq)

---

## 1. What Is This Service? (Plain English)

The Ready2Go platform helps government and enterprise customers manage **continuity plans** —
documents that describe how an organisation keeps operating during a disruption (an earthquake,
a cyber‑attack, a power outage). There are three kinds of plans:

- **COOP** — *Continuity of Operations* (keep the agency running).
- **BCP** — *Business Continuity Plan* (keep the business running).
- **Compliance** — regulatory/policy documents.

Customers upload files (PDFs, Word docs, spreadsheets) and **file each one under a plan**. The
problem: people make mistakes. They attach a cybersecurity checklist to an earthquake plan, or
upload a blank scanned page, or upload the same document twice.

**This service is the librarian that double‑checks every filing.** For each uploaded document it
answers three questions:

1. **Does this document actually belong in the plan it was filed under?** → a **status**:
   `Compliant`, `Under Review`, or `Non-Compliant`.
2. **How confident are we?** → a **score** from `0` to `100`.
3. **What is this document, in plain English?** → a **summary** (a detailed write-up covering all the major points, ≤ 2000 characters).

It also produces an **organisation‑wide audit narrative**: a short paragraph + a few findings +
an overall "posture" (`Resilient` / `Steady` / `At Risk`) summarising the health of *all* a
customer's plans.

**Who calls it?** Only the **Next.js web application** (our own frontend/backend). This is an
internal, server‑to‑server microservice. End users never talk to it directly — the Next.js server
calls it on their behalf.

**The core trick:** the service uses **AI embeddings** (numerical "meaning fingerprints" of text)
plus simple metadata checks to decide whether a document's *meaning* matches the plan it's filed
under. That's why it's an "AI service."

---

## 2. The Big Picture (Architecture)

```
   ┌────────────────────┐
   │  Next.js App        │  (the ONLY caller — server-to-server)
   │  (web frontend +    │
   │   API routes)       │
   └─────────┬──────────┘
             │  HTTPS + Authorization: Bearer <token>
             │  (optional X-Ready2Go-Signature HMAC)
             ▼
   ┌────────────────────────────────────────────────────────┐
   │            Ready2Go AI Service  (this repo)              │
   │            FastAPI (Python), runs on port 8000           │
   │                                                          │
   │  /v1/integrity/analyze   ← score one document           │
   │  /v1/integrity/rescan    ← clear cache / re-run          │
   │  /v1/audit/summary       ← org-wide audit narrative      │
   │  /healthz /readyz /metrics /v1/diagnostics/calls         │
   └───┬──────────────┬───────────────┬─────────────────────┘
       │              │               │
       ▼              ▼               ▼
 ┌──────────┐  ┌─────────────┐  ┌──────────────────┐
 │ OpenAI   │  │  Weaviate   │  │  MongoDB (Atlas) │
 │ API      │  │ (vector DB) │  │  ai_* collections│
 │          │  │             │  │                  │
 │ embeds + │  │ stores chunk│  │ cache + audit    │
 │ LLM text │  │ vectors for │  │ state + call log │
 │          │  │ similarity  │  │                  │
 └──────────┘  └─────────────┘  └──────────────────┘
```

| Dependency  | What it is | Why we need it |
|-------------|-----------|----------------|
| **OpenAI API** | Cloud AI provider | Turns text into **embeddings** (`text-embedding-3-small`, 1536 numbers per chunk) and runs the optional **LLM judge** + summaries (`gpt-4o-mini`). |
| **Weaviate** | Open‑source **vector database** (v1.27.0, runs in Docker) | Stores every document's chunk vectors so we can ask "how similar is this document to the plan / to other documents in the same plan?" |
| **MongoDB** | Document database (managed on **Atlas**, shared with the Next.js app) | Stores the **result cache**, the **rolling audit counters**, and the **AI call log**. Lives in the `ready2go` database in dedicated `ai_*` collections. |

The service is **stateless in itself** — every piece of durable state lives in Weaviate, MongoDB,
or the log files. You can kill and restart the container and lose nothing important.

---

## 3. Core Concepts Glossary

Read this once; everything else will make sense.

| Term | Plain‑English meaning |
|------|----------------------|
| **Embedding / vector** | A list of 1536 numbers that represents the *meaning* of a piece of text. Similar meanings → similar number lists. Produced by OpenAI's `text-embedding-3-small`. |
| **Cosine similarity** | A math measure (−1 … 1) of how close two vectors point in the same direction. 1.0 = identical meaning, 0 = unrelated. We use it everywhere. |
| **Cosine distance** | The inverse: `0` = identical, `2` = opposite. Weaviate returns *distance*; we convert as needed. |
| **Chunk** | A small slice of a document's text (≈ 500 tokens). Big documents are split into many chunks. See [§4](#4-what-is-a-doc-chunk). |
| **Centroid** | The *average* vector of all of a document's chunks. One vector that represents the whole document's meaning. |
| **Tenant** | One customer (a "subadmin"). Their data is isolated. Tenant key = `"sub_" + ownerUserId`. |
| **Content hash** | A SHA‑256 fingerprint of the raw file bytes. Two identical files → same hash → cache hit. |
| **Signal** | One of four independent 0…1 measurements that feed the score (content, name, quality, duplication). |
| **Band / status** | The score is mapped to a status word using thresholds ("bands"): ≥71 Compliant, 41–70 Under Review, <41 Non-Compliant. |
| **Degraded** | A flag meaning "a dependency we needed (OpenAI/Weaviate) was down, so this result is provisional and was NOT cached." |

---

## 4. What Is a "Doc Chunk"?

You'll see the word **chunk** everywhere in the code and logs, so here is exactly what it means.

### The problem it solves
OpenAI's embedding model can only embed a limited amount of text at once, and embedding a giant
document as one blob produces a blurry, useless "average of everything" vector. So we **split each
document into smaller, overlapping pieces called chunks** and embed each piece separately.

### The exact algorithm — `app/ingest/chunk.py`
1. The full extracted text is tokenised with **tiktoken's `cl100k_base`** encoder — the *same*
   tokenizer `text-embedding-3-small` uses, so token counts are accurate.
2. A sliding window of **500 tokens** (`CHUNK_TOKENS`) moves across the text.
3. Each step advances by **450 tokens** (`500 − 50`), so consecutive chunks **share 50 tokens**
   (`OVERLAP_TOKENS`) of context.
4. Production stops at **`max_chunks_per_doc` = 200** chunks; any tail beyond that is discarded
   (this caps embedding cost for very large documents).

```
Full document tokens:  [t0 t1 t2 ............................ t1399]

Chunk 0:  t0   … t499     (500 tokens)
Chunk 1:  t450 … t949     (overlaps chunk 0 by 50 tokens)
Chunk 2:  t900 … t1399    (overlaps chunk 1 by 50 tokens)
          ▲ index 0-based, stored as `chunkIndex` in Weaviate
```

### Why overlap?
A sentence sitting on a chunk boundary would otherwise be cut in half and lose meaning. With a
50‑token overlap, boundary ideas appear in **both** neighbouring chunks, so when we average all the
chunk vectors into a **centroid** we capture the whole document faithfully — not just text that
happened to land neatly inside a window.

### The chunk "names" you'll see in code (this often confuses people)
The same concept has **three names** depending on where it is in its lifecycle:

| Name | Where | Meaning |
|------|-------|---------|
| `Chunk` (dataclass: `index`, `text`, `token_count`) | `app/ingest/chunk.py` | A chunk freshly cut from text, **before** it has a vector. |
| **`DocChunk`** | Weaviate collection name | The *table* in the vector DB where chunks + vectors + metadata are stored. |
| `chunkIndex` | Weaviate property | The 0‑based position field on a stored chunk. |
| `StoredChunk` (dataclass: `chunk_index`, `text`, `attachment_id`, `vector`) | `app/vectors/repo.py` | A chunk read **back** from Weaviate, now carrying its 1536‑dim vector. |

So when chat/logs say "doc chunk," they mean one of these — a 500‑token, overlapping slice of one
uploaded document, stored as a row in the Weaviate **`DocChunk`** collection.

---

## 5. The Two Databases — Why Both?

This is the most common question, so it gets its own section. **We use two databases because they
answer two completely different questions.**

| | **Weaviate (vector DB)** | **MongoDB (`ai_*` collections)** |
|---|---|---|
| Question it answers | "What does this document **mean**, and how similar is it to other things?" | "Have I **already processed** this exact file, and what are the running totals?" |
| Stores | Chunk **vectors** (1536 numbers each) + chunk text + metadata | Final **verdicts** (score/status/summary), rolling **counters**, AI **call log** |
| Query style | Nearest‑neighbour / similarity search, hybrid keyword+vector search | Exact key lookup, `$inc` counter updates, time‑sorted reads |
| Without it | No semantic scoring at all — can't tell a cyber doc from an earthquake doc | No caching (re‑pay OpenAI every time), no O(1) audit, no cost trail |

### "If MongoDB already caches the result, why have a vector DB at all?"

Because **they cache different things for different reasons:**

- **MongoDB cache (`ai_analysis_cache`) only knows about files it has seen byte‑for‑byte.** Its
  key is `(contentHash, modelVersion)`. If the *exact same bytes* come back, it returns the stored
  verdict instantly and spends **zero OpenAI tokens**. It is a pure **don't‑redo‑identical‑work**
  optimisation. It cannot compare two *different* documents and cannot find near‑duplicates.

- **Weaviate does the actual thinking.** It holds the *meaning* of every document so the scoring
  engine can ask questions a cache never could:
  - *Content alignment* — how close is this document's meaning to the plan's description?
  - *Duplication* — is there a near‑identical sibling already in this plan?

  (The *name* signal — filename vs plan label — is computed in-process from
  embeddings, not via a Weaviate query.)

In short: **MongoDB is the receipt drawer; Weaviate is the brain.** The cache makes us *fast and
cheap*; the vector DB makes us *smart*. Remove the cache and the service still works but costs more;
remove Weaviate and the service can no longer score anything meaningfully.

### The three MongoDB `ai_*` collections (all inside the `ready2go` DB)

| Collection | Key | Purpose |
|------------|-----|---------|
| `ai_analysis_cache` | unique `(contentHash, modelVersion)` | Dedup cache of final verdicts (status/score/summary/components). A hit = 0 tokens. Also stores `vectorIds` — the Weaviate chunk-object UUIDs for this document, as a direct reference to its vectors. |
| `ai_audit_state` | `_id = tenantKey` | Per‑customer rolling counters (totals, integrity breakdown, score sum) plus two per‑doc lists — `notable` (worst‑20) and `all_analyzed` (every doc) — each entry holding `{fileName, status, score, planId, summary}` (summary excerpt ≤600 chars). Updated O(1) per analyze. Powers the audit endpoint without re‑scanning anything. |
| `ai_call_log` | auto, sorted by `ts` | Append‑only record of every OpenAI call (kind, model, tokens, latency, success/error) for cost tracking and debugging. |

> **Ownership boundary:** MongoDB's `ready2go` database is **shared** with the Next.js app. Next.js
> owns the business documents (`continuityplans`, `continuityauditreports`); this Python service
> only ever reads/writes its own `ai_*` collections. They never step on each other.

---

## 6. End‑to‑End Request Flow (The 12‑Step Pipeline)

This is what happens when Next.js calls `POST /v1/integrity/analyze`. The orchestration lives in
[app/api/integrity.py](../app/api/integrity.py).

```
 1. Validate AnalyzeRequest               (schemas.py — Pydantic)
 2. Fetch file bytes + SHA-256            (ingest/fetch.py)
 3. Cache check (contentHash, modelVersion)
        └─ HIT  → return cached verdict immediately (0 tokens) ──► DONE
        └─ MISS → continue
 ── steps 4–11 run inside a 25s timeout + catch-all guard ──
 4. Extract text + quality                (ingest/extract.py)
 5. Chunk text into ≤200 overlapping chunks (ingest/chunk.py)
 6. Embed all chunks in ONE batched call  (llm/embeddings.py → OpenAI)
 7. Upsert chunks+vectors into Weaviate   (vectors/repo.py, DocChunk)
 8. Compute 4 signals → composite score   (scoring/*)
        └─ if score in 60–72 band → optional LLM judge (llm/client.py)
 9. Paragraph summary from full chunk set (summary/per_doc.py)
10. Persist cache + audit state (skipped if degraded) (store/*)
11. Log the AI call(s)                     (store/calllog.py)
12. Return AnalyzeResponse → Next.js stores aiIntegrity* fields
```

### Key behaviours to understand

- **Cache short‑circuit (step 3).** If the file's content hash is already cached for this model
  version, steps 4–11 are skipped entirely. The response has `details.cacheHit = true` and costs
  nothing. (Note: a cache‑hit response returns the stored component scores but an **empty**
  `similarFiles` list — siblings are only computed on a full run.)

- **Timeout + graceful fallback.** Steps 4–11 run inside `asyncio.wait_for(..., timeout=25s)`
  (`request_timeout_s`). On **timeout** or any **unexpected exception**, the service does **not**
  return a 500. Instead it returns a safe fallback: `status="Under Review"`, `score=50`,
  `summary="Analysis unavailable — will retry on next request."`, `degraded=true`. Next.js can
  show the document as "still being reviewed" rather than erroring.

- **Fetch failure is the one hard error.** If the file can't be downloaded (step 2), the service
  returns **HTTP 502 Bad Gateway** — there's nothing to analyse.

- **Degraded ≠ empty.** "Degraded" specifically means *a dependency we needed was unreachable*:
  - `embed_failed` — we had text but OpenAI embeddings failed.
  - `weaviate_failed` — we had vectors but Weaviate rejected the upsert.
  An empty/scanned document (zero chunks) is **not** degraded — that's a legitimate low‑quality
  result worth caching. **Degraded results are deliberately NOT cached** (step 10 is skipped) so a
  transient outage never poisons the cache with an artificially low score.

- **Tenant is required.** A blank `tenantKey` → HTTP 400.

---

## 7. The Scoring Engine (4 Signals)

The score is a **weighted blend of four independent signals**, each a pure function returning a
float in `[0, 1]`. The signals live in [app/scoring/signals.py](../app/scoring/signals.py); the
blend and overrides live in [app/scoring/integrity.py](../app/scoring/integrity.py); the tunable
numbers live in [app/scoring/thresholds.py](../app/scoring/thresholds.py) (sourced from config).

| # | Signal | Weight | What it measures | How it's computed |
|---|--------|--------|------------------|-------------------|
| 1 | **content** | **0.50** | Does the document's meaning match the plan? | Cosine similarity of the document **centroid** vs the embedding of `"{label} {overview} {steps}"`. Negative cosine clamped to 0. |
| 2 | **name** | **0.19** | Does the filename match the plan? | **In-process cosine** between the embedding of the cleaned **filename** and the embedding of `"{label} {category}"`. Both are produced in the one batched OpenAI embed call. Per-document and absolute (0…1) — it does **not** depend on other documents, so a valid file can never be silently zeroed by ranking outside a top-N window (the earlier hybrid-search bug). |
| 3 | **quality** | **0.19** | Did we extract real text? | Deterministic from extraction metadata: empty → `0.05`, scan‑only → `0.10`, `<500` chars → linear ramp `0.1→0.8`, beyond → log curve toward `1.0`. |
| 4 | **duplication** | **0.12** | Is it a near‑duplicate of a sibling? | Maps nearest‑sibling cosine distance to `[0,1]`. No siblings → `1.0` (unique). Near‑identical → near `0`. |

> **Removed signal — `category` (prototype fit).** A fifth signal once compared the document
> centroid against a per‑category *prototype* embedding. It was removed because the prototypes were
> never seeded (it always returned a neutral `0.5` placeholder) and added cost/complexity for no
> signal. Its `0.20` weight was redistributed across the remaining four (which is why content is now
> `0.50` and the others rose). The `category` field on a plan is still used — for the name‑match
> query, chunk metadata, and audit counts — but it no longer feeds the score directly.

### From signals to a final answer — `compute()`

1. **Weighted sum** → `raw` → scaled to `0–100`: `score = round(clamp01(raw) * 100)`.
2. **Component scores** (each signal × 100) are returned for explainability (the UI shows them).
3. **Hard override** (this can bypass the normal banding):
   - **Empty or scan‑only** document → score capped at **45**, status becomes `Under Review` (or
     `Non-Compliant` if below the reviewing band).
4. **Normal banding** (`score_to_status`):
   - `score ≥ 71` (`band_in_sync`) → **Compliant**
   - `score ≥ 41` (`band_reviewing`) → **Under Review**
   - else → **Non-Compliant**
5. **LLM judge gate.** If the score falls in the **60–72** band (`llm_judge_band`) *and* an OpenAI
   key is configured, the pipeline calls `gpt-4o-mini` with the score + an 8000‑char excerpt and
   asks it to confirm/adjust the `{status, score}`. This is a cheap tie‑breaker for borderline
   cases only — most documents never trigger it. If the judge call fails, the computed values are
   kept.

All weights, bands, and the judge band are **environment‑configurable** — see [§13](#13-configuration-reference).

---

## 8. Summarization (Per‑Doc & Audit)

### Per‑document summary — `app/summary/per_doc.py`
After scoring, the service writes a **detailed plain‑English summary** (8–12 sentences, ≤ 2000
chars) that captures **all the major points** of the document — its purpose, scope, key procedures
and steps, roles/responsibilities, timelines/recovery objectives, named systems/dependencies, and
any notable gaps. It is written this richly on purpose: the same summary is later fed into the
organisation audit so the auditor can reason about each document's actual content. Strategy "D7": it
uses the **complete chunk set** of the document (we own the whole document, so no retrieval/subset is
needed).

- **Short docs** (combined text ≤ 16,000 chars): a **single LLM pass** → `{"summary": "..."}`.
- **Long docs** (> 16,000 chars): **map‑reduce** — summarise text in 12,000‑char batches (**no
  batch cap**, so the whole document is covered, not just its opening) into short phrases, then
  reduce those phrases into one final summary. Cost is bounded by the larger batch size.
- **No OpenAI key / extraction failed:** a deterministic fallback like
  `"{CATEGORY} artifact '{fileName}' … pending analysis"`. Always ≤ 2000 chars.

### Organisation audit narrative — `app/summary/audit.py`
`POST /v1/audit/summary` produces a short audit paragraph for the whole customer. It does **not**
re‑scan documents — it reads the **rolling counters** already accumulated in `ai_audit_state`.

- **Posture** is **deterministic** (`derive_posture`): `At Risk` (no plans, any deviations, avg
  score < 55, or nothing analysed), `Steady` (any reviewing or avg < 75), else `Resilient`.
- **Narrative** (`summary` ≤ 1500 chars + up to **8** `findings`, each ≤ 350 chars) is generated by
  `gpt-4o-mini` from the totals, category counts, integrity breakdown, and a sample of analyzed
  documents — **each sample entry now carries that document's summary excerpt** (≤600 chars, stored
  in `ai_audit_state`), so the auditor reasons about real content, not just scores. The sample is the
  worst‑scoring docs (size set by `AUDIT_SAMPLE_CAP`; `0` = the whole corpus). Falls back to a
  deterministic message if no key/plans.
- **Always graceful:** if the rolling‑state read or the LLM reduce fails, the endpoint **does not
  500** — it logs the cause and returns a deterministic fallback built from the request payload
  (reusing `derive_posture`), leaving the audit state "dirty" so the next call retries.

---

## 9. API Reference for Next.js

Base URL: the service host (e.g. `http://localhost:8000` in dev). All `/v1/*` routes require auth
(see [§10](#10-authentication--why-shared-secret-not-jwt)). Request/response bodies are JSON.

> **camelCase vs snake_case.** Every model accepts **both** (Pydantic `populate_by_name=True`).
> Next.js should send **camelCase** (the alias column below). Responses are emitted using the field
> names as defined — Next.js should read the camelCase aliases shown in the examples.
>
> **For the frontend developer — the "UI Tooltip" column.** Each field table below has a final
> column written in plain, end‑user language. This is ready‑to‑use copy for the small **ℹ️ ("i")
> info icon** shown next to a field/value in the UI: on hover, display that text so the user
> understands what the field means and *how it was calculated*. You can drop these strings straight
> into the frontend (e.g. as a `tooltip` map keyed by field name) — no backend change needed.

### How Next.js sends a payload

```ts
await fetch("http://ai-service:8000/v1/integrity/analyze", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Authorization": `Bearer ${process.env.PYTHON_INTEGRITY_TOKEN}`,
    // optional, only if HMAC_SECRET is configured on the service:
    // "X-Ready2Go-Signature": hmacSha256Hex(secret, rawBody),
  },
  body: JSON.stringify(payload), // the AnalyzeRequest below
});
```

If HMAC is enabled, the signature must be computed over the **exact raw request body bytes** that
are sent.

---

### 9.1 `POST /v1/integrity/analyze` — score one document

Runs the full 12‑step pipeline. Always returns **HTTP 200** (even on graceful fallback); only a
fetch failure returns **502**, and a missing `tenantKey` returns **400**.

#### Request body: `AnalyzeRequest`

```json
{
  "tenantContext": {
    "tenantKey": "sub_6a054db0dc2d5c9cc796a36b",
    "actorUserId": "user_123"
  },
  "plan": {
    "planId": "plan_abc",
    "label": "Main Business Continuity Plan",
    "overview": "Keep core operations running during disruptions.",
    "category": "bcp",
    "steps": ["Identify critical functions", "Define recovery procedures"]
  },
  "attachment": {
    "attachmentId": "att_001",
    "fileName": "bcp_procedures.pdf",
    "fileExtension": "pdf",
    "fileUrl": "https://res.cloudinary.com/.../bcp_procedures.pdf",
    "fileMime": "application/pdf",
    "fileSizeBytes": 152340,
    "cloudinaryPublicId": "earthquick/emergency-plans/abc",
    "cloudinaryResourceType": "image"
  },
  "extractedText": null,
  "vectorKey": null
}
```

**Field‑by‑field:**

`tenantContext` (object, **required**) — `TenantContext`:

| Field | Alias | Type | Req | Meaning | UI Tooltip (ℹ️ on hover) |
|-------|-------|------|-----|---------|--------------------------|
| `tenant_key` | `tenantKey` | string | ✅ | Customer/tenant id, `"sub_"+ownerUserId`. Drives Weaviate tenant isolation + audit state `_id`. | "Identifies which customer account this analysis belongs to. Your documents are kept completely separate from every other customer's." |
| `actor_user_id` | `actorUserId` | string\|null | ❌ | Who triggered the analysis (audit only). | "The user who started this check. Recorded for the audit trail only — it doesn't affect the result." |

`plan` (object, **required**) — `PlanContext`:

| Field | Alias | Type | Req | Meaning | UI Tooltip (ℹ️ on hover) |
|-------|-------|------|-----|---------|--------------------------|
| `plan_id` | `planId` | string | ✅ | Plan identifier; groups sibling files for duplication checks. | "The continuity plan this file is filed under. We compare the file against the other files in this same plan to spot duplicates." |
| `label` | — | string | ✅ | Plan name; used in content + name signals. | "The plan's name. We check whether the document's content and filename actually relate to this plan." |
| `overview` | — | string | ❌ (default `""`) | Plan description; folded into the content‑alignment text. | "A short description of the plan. The richer this is, the more accurately we can tell whether a document belongs here." |
| `category` | — | `"coop"\|"bcp"\|"compliance"` | ❌ (default `"coop"`) | Declared category; used in the name‑match query, chunk metadata, and audit counts (no longer a scoring signal). | "The type of plan (COOP, BCP, or Compliance). Used to group the document and to help match its filename to the plan." |
| `steps` | — | string[] | ❌ (default `[]`) | Plan steps; folded into the content‑alignment text. | "The plan's procedure steps. Used to better understand what the plan is about when judging if a document fits." |

`attachment` (object, **required**) — `AttachmentRef`:

| Field | Alias | Type | Req | Meaning | UI Tooltip (ℹ️ on hover) |
|-------|-------|------|-----|---------|--------------------------|
| `attachment_id` | `attachmentId` | string | ✅ | Document id (MongoDB `attachments._id`). Idempotency key in Weaviate. | "The unique id of this uploaded file. Re‑analysing the same file updates its record rather than creating a duplicate." |
| `file_name` | `fileName` | string | ✅ | Original filename; used by the name signal & summary. | "The original name of the uploaded file. A relevant filename slightly boosts the score." |
| `file_extension` | `fileExtension` | `"pdf"\|"docx"\|"csv"\|"xlsx"` | ✅ | Picks the parser path. | "The file type. Determines how we read the text out of it. Supported: PDF, DOCX, CSV, XLSX." |
| `file_url` | `fileUrl` | string | ✅ | Where the service downloads the bytes from. | "The location we download the file from to read its contents." |
| `file_mime` | `fileMime` | string\|null | ❌ | Informational. | "The file's MIME type (e.g. application/pdf). Informational only." |
| `file_size_bytes` | `fileSizeBytes` | int\|null | ❌ | Informational. | "The file size in bytes. Informational only." |
| `cloudinary_public_id` | `cloudinaryPublicId` | string\|null | ❌ | Informational. | "Internal storage reference for the file. Informational only." |
| `cloudinary_resource_type` | `cloudinaryResourceType` | string\|null | ❌ | Informational. | "Internal storage type for the file. Informational only." |

Top‑level `AnalyzeRequest`:

| Field | Alias | Type | Req | Meaning | UI Tooltip (ℹ️ on hover) |
|-------|-------|------|-----|---------|--------------------------|
| `tenant_context` | `tenantContext` | object | ✅ | See above. | "Which customer this request belongs to." |
| `plan` | — | object | ✅ | See above. | "Details of the plan the file is filed under." |
| `attachment` | — | object | ✅ | See above. | "Details of the file being checked." |
| `extracted_text` | `extractedText` | string\|null | ❌ | Reserved; **not used in v1** (the service extracts text itself). | "Reserved for future use — the service reads the file's text itself, so this is ignored today." |
| `vector_key` | `vectorKey` | string\|null | ❌ | Reserved hint; **not used in v1**. | "Reserved for future use. Ignored today." |

#### Response body: `AnalyzeResponse`

```json
{
  "status": "Under Review",
  "score": 63,
  "summary": "A 12-page business continuity procedure for the finance department. It defines recovery roles, escalation contacts, and target recovery times for core systems, and walks through failover steps for the primary data centre. It covers backup verification and staff call-trees, but does not specify a testing cadence or name an owner for plan maintenance.",
  "analyzedAt": "2026-06-05T14:30:00Z",
  "modelVersion": "integrity-v1",
  "details": {
    "componentScores": { "content": 71, "name": 40, "quality": 95, "duplication": 100 },
    "similarFiles": [ { "attachmentId": "att_009", "similarity": 0.81 } ],
    "cacheHit": false,
    "degraded": false
  }
}
```

| Field | Alias | Type | Meaning | UI Tooltip (ℹ️ on hover) |
|-------|-------|------|---------|--------------------------|
| `status` | — | `"Compliant"\|"Under Review"\|"Non-Compliant"` | The headline verdict. Exact strings — the UI keys off them. | "Our verdict on whether this document belongs in this plan. **Compliant** = good match (score ≥ 71); **Under Review** = borderline, worth a human look (41–70); **Non-Compliant** = likely mis‑filed or unreadable (below 41)." |
| `score` | — | int 0–100 | Composite integrity score. | "An overall confidence score from 0–100 that this document fits the plan. It blends four checks: content match (50%), filename match (19%), text quality (19%), and uniqueness (12%)." |
| `summary` | — | string ≤2000 | A detailed write-up covering all the document's major points. | "An AI‑generated summary of what this document actually is — its purpose, scope, key procedures, roles, timelines, and any gaps — based on its full text." |
| `analyzed_at` | `analyzedAt` | datetime (ISO) | When this response was produced. | "The date and time this analysis was performed." |
| `model_version` | `modelVersion` | string | e.g. `integrity-v1`; bump invalidates cache. | "The version of the analysis model used. Shown so results can be compared fairly over time." |
| `details` | — | object\|null | `AnalyzeDetails` (below). | "The detailed breakdown behind the score (see below)." |

`details` → `AnalyzeDetails`:

| Field | Alias | Type | Meaning | UI Tooltip (ℹ️ on hover) |
|-------|-------|------|---------|--------------------------|
| `component_scores` | `componentScores` | object\|null | Per‑signal 0–100 breakdown (`content`,`name`,`quality`,`duplication`), each int or null. | "The four individual scores (0–100) that make up the overall score — so you can see *why* a document scored the way it did. See each sub‑score's tooltip below." |
| `similar_files` | `similarFiles` | array | Up to **3** `{attachmentId, similarity}` siblings (empty on a cache hit / when no centroid). | "Other files in the same plan that look most similar to this one (with a 0–1 similarity). Useful for spotting duplicates or related documents." |
| `cache_hit` | `cacheHit` | bool | True if served from cache (0 tokens). | "True means we recognised this exact file from a previous check and returned the saved result instantly — no re‑processing needed." |
| `degraded` | `degraded` | bool | True if a needed dependency was unreachable; result not cached. | "True means a temporary system issue (AI or vector service unavailable) made this result provisional. It wasn't saved and will be recalculated on the next try." |

The four **component scores** (each 0–100) have their own tooltips:

| Sub‑score | UI Tooltip (ℹ️ on hover) |
|-----------|--------------------------|
| `content` | "How closely the document's *meaning* matches what this plan is about. The biggest factor in the overall score (50%). Calculated by comparing the document's text to the plan's name, description, and steps." |
| `name` | "How well the filename matches the plan. Counts for 19%. A relevant filename helps; a random one doesn't hurt much." |
| `quality` | "How readable the document was. Counts for 19%. Blank, empty, or scanned‑image files (no real text) score very low here." |
| `duplication` | "How unique this file is compared to others in the same plan. Counts for 12%. A near‑duplicate of an existing file scores low; a one‑of‑a‑kind file scores high." |

---

### 9.2 `POST /v1/integrity/rescan` — clear cache / re‑run

Prepares one or more attachments to be re‑analysed (e.g. after a model bump or a known bad result).
Returns **HTTP 202 Accepted**. It does **not** re‑analyse inline — it deletes the cache entry (and
Weaviate chunks) so the next `/analyze` call rebuilds everything.

#### Request body: `RescanRequest`

| Field | Alias | Type | Req | Meaning | UI Tooltip (ℹ️ on hover) |
|-------|-------|------|-----|---------|--------------------------|
| `attachment_ids` | `attachmentIds` | string[]\|null | ❌ | Documents to reprocess. If empty/missing → no‑op. | "The list of files to re‑check. They'll be re‑analysed fresh the next time they're opened." |
| `tenant_key` | `tenantKey` | string\|null | ❌* | Tenant; required (non‑blank) for the Weaviate chunk delete. | "Which customer these files belong to." |
| `force` | — | bool | ❌ (default `false`) | `false`: skip attachments already cached. `true`: delete cache and re‑run regardless. | "Turn on to force a complete re‑analysis even if we already have a saved result. Leave off to only re‑check files that haven't been analysed yet." |

#### Response

```json
{ "scheduled": 3, "skipped": 1,
  "message": "Rescan prepared for 3 attachment(s). They will be re-analyzed on the next /analyze call. (1 skipped — already cached; use force=true to override.)" }
```

---

### 9.3 `POST /v1/audit/summary` — org‑wide audit narrative

Builds a bounded summary from the rolling per‑tenant counters (`ai_audit_state`). Next.js supplies
aggregate context in the body; the service primarily uses the stored rolling state for the tenant.
Returns **HTTP 200**.

#### Request body: `AuditSummaryRequest`

```json
{
  "tenantContext": { "tenantKey": "sub_6a054db0dc2d5c9cc796a36b" },
  "totals": { "plans": 8, "attachments": 47, "analyzed": 35 },
  "averageScore": 62,
  "counts": { "coop": 12, "bcp": 20, "compliance": 10, "response": 5 },
  "integrity": { "inSync": 18, "reviewing": 12, "deviation": 5, "unanalyzed": 12 },
  "plans": [
    {
      "planId": "plan_abc",
      "label": "Main BCP",
      "category": "bcp",
      "attachmentCount": 12,
      "stepCount": 6,
      "attachments": [
        { "fileName": "old_policy.pdf", "status": "Non-Compliant", "score": 22, "summary": "..." }
      ]
    }
  ]
}
```

Top‑level fields:

| Field | Alias | Type | Req | Meaning | UI Tooltip (ℹ️ on hover) |
|-------|-------|------|-----|---------|--------------------------|
| `tenant_context` | `tenantContext` | object\|null | ❌ | Tenant whose rolling state to read. | "Which customer this audit is for." |
| `totals` | — | `AuditTotals` | ✅ | `{plans, attachments, analyzed}` (ints, default 0). | "Headline counts: how many plans and files exist, and how many have been analysed so far." |
| `average_score` | `averageScore` | int | ❌ (default 0) | Org average supplied by Next.js. | "The average integrity score across all analysed files — a quick health number for the whole account." |
| `counts` | — | `AuditCounts` | ✅ | `{coop, bcp, compliance, response}` (ints). | "How many files fall into each plan type (COOP, BCP, Compliance, Response)." |
| `integrity` | — | `IntegrityBreakdown` | ✅ | `{inSync, reviewing, deviation, unanalyzed}` (ints; alias `inSync`). The JSON keys are internal counter names; they map to the Compliant / Under Review / Non-Compliant verdicts respectively. | "How many files landed in each verdict bucket: Compliant, Under Review, Non-Compliant, and not‑yet‑analysed." |
| `plans` | — | `AuditPlan[]` | ❌ (default `[]`) | Plan‑level detail (see below). | "Per‑plan breakdown used to write the audit narrative." |

`AuditPlan`:

| Field | Alias | Type | Req | Meaning | UI Tooltip (ℹ️ on hover) |
|-------|-------|------|-----|---------|--------------------------|
| `plan_id` | `planId` | string | ✅ | Plan id. | "The plan being summarised." |
| `label` | — | string | ✅ | Plan name. | "The plan's name." |
| `category` | — | `"coop"\|"bcp"\|"compliance"\|"response"` | ✅ | Note: audit allows `response` too. | "The plan's type (COOP, BCP, Compliance, or Response)." |
| `attachment_count` | `attachmentCount` | int | ❌ (0) | Total attachments. | "How many files are filed under this plan." |
| `step_count` | `stepCount` | int | ❌ (0) | Total steps. | "How many procedure steps this plan defines." |
| `attachments` | — | `AuditAttachment[]` | ❌ (`[]`) | Sampled worst scorers. | "A sample of this plan's files (lowest‑scoring first) used to highlight problem areas in the audit." |

`AuditAttachment`: `{ file_name/fileName (req), status?, score?, summary? }` — one file's name, its verdict, its score, and its paragraph summary (tooltips as in the analyze response above).

#### Response body: `AuditSummaryResponse`

```json
{
  "summary": "8 plans, 47 attachments tracked. Coverage gaps and a few mis-filed compliance docs.",
  "findings": [
    "12 attachments still unanalyzed.",
    "5 deviations found, mostly in compliance.",
    "Average integrity score 62 — trending down."
  ],
  "posture": "Steady",
  "averageScore": 62
}
```

| Field | Alias | Type | Meaning | UI Tooltip (ℹ️ on hover) |
|-------|-------|------|---------|--------------------------|
| `summary` | — | string ≤1500 | Detailed audit narrative. | "A detailed AI‑written summary of the overall health of this account's continuity plans." |
| `findings` | — | string[] (≤8) | Key bullet findings. | "Up to eight key takeaways — the most important things to act on, such as coverage gaps or mis‑filed documents." |
| `posture` | — | `"Resilient"\|"Steady"\|"At Risk"` | Deterministic overall posture. | "An overall readiness rating. **Resilient** = healthy; **Steady** = mostly fine with some items to review; **At Risk** = significant gaps or deviations that need attention." |
| `average_score` | `averageScore` | int | Org average. | "The average integrity score (0–100) across all analysed files in the account." |
| `degraded` | — | bool | True when a **reduced fallback sample** (worst-N) was used instead of the full set — e.g. the full-corpus audit was too big for the model, or an internal failure forced a payload-derived response. | "True means this audit was generated from a reduced set of documents (a safety fallback), so it may be less complete than usual. It will be regenerated fully on the next run." |

---

### 9.4 Operational endpoints

| Method & path | Auth | Returns |
|---------------|------|---------|
| `GET /healthz` | none | `{"status":"ok"}` — liveness only (no dependency checks). |
| `GET /readyz` | none | `{status, env, modelVersion, dependencies:{weaviate, mongodb, openai}}` — live connectivity probes; `degraded` if any configured dep fails. |
| `GET /metrics` | none | In‑process counters: `requests_total`, `cache_hits/misses`, `cache_hit_ratio`, `pipeline_errors`, `pipeline_timeouts`, `tokens_total`, `avg_latency_ms`. |
| `GET /v1/diagnostics/calls?attachmentId=&limit=` | **required** | Recent `ai_call_log` rows (`limit` default 50, max 200) for cost/error tracing. |

---

## 10. Authentication — Why Shared‑Secret, Not JWT

### How it works today — `app/security.py`
Two mechanisms guard every `/v1/*` route (health endpoints are intentionally open):

1. **Bearer token** — `Authorization: Bearer <PYTHON_INTEGRITY_TOKEN>`. Compared in constant time
   (`hmac.compare_digest`) against the shared secret.
2. **Optional HMAC body signature** — `X-Ready2Go-Signature: hex(HMAC_SHA256(HMAC_SECRET, rawBody))`.
   Skipped if `HMAC_SECRET` is not configured; enforced if it is.

**Fail‑closed in production:** if `env=production` and `PYTHON_INTEGRITY_TOKEN` is empty, the
service rejects **every** request (and refuses to even boot via `validate_production_secrets`). A
missing env var can never silently open the API.

### Why a shared secret instead of JWT? (and why the payload isn't self‑extracted from a token)

This is a deliberate, professional choice for this architecture:

- **There is exactly one trusted caller** — the Next.js server. This is **server‑to‑server**
  traffic on a private network, not browsers hitting the API directly. JWT's main job is to carry a
  *verifiable end‑user identity with claims*; we have no end user at this hop. The user already
  authenticated to Next.js; Next.js is the only authority calling us.
- **The business context is rich, nested, and large.** The request carries `tenantContext`, full
  `plan` (label, overview, steps), and `attachment` (URLs, ids, sizes). That data does **not**
  belong inside a token — tokens are meant to be small, signed identity assertions, not document
  payloads. So we send the full JSON body and authenticate the *channel* with a shared secret. The
  user asked "why not let JWT self‑extract it?" — because a JWT would only carry identity/claims,
  not the document/plan content we actually need to score; that content has to be in the body
  regardless, so a JWT would add ceremony without removing the payload.
- **Operational simplicity & rotation.** A shared secret is one env var on each side, rotated with
  a single redeploy. No public/private keypair, no JWKS endpoint, no token issuance/expiry/refresh
  machinery to run for a single internal consumer.
- **Tamper‑evidence when needed.** The optional HMAC signature gives integrity protection over the
  exact bytes — defence‑in‑depth without the weight of full JWT/OAuth.

#### Comparison

| Approach | Best for | Pros here | Cons here |
|----------|----------|-----------|-----------|
| **Shared‑secret Bearer** (chosen) | One trusted internal caller | Dead simple, rotatable, constant‑time check, fail‑closed | Single secret to protect; no per‑user identity (not needed) |
| **+ HMAC body signature** (optional, on) | Integrity over the wire | Detects tampering/replay of the body | Caller must sign the raw body |
| **JWT** | Multiple/3rd‑party callers, end‑user identity, fine‑grained claims | Self‑describing identity, expiry, no shared secret distribution | Overkill for one internal S2S caller; key management, doesn't remove the need to send the body |

**Professional recommendation (and current stance):** keep **Bearer + optional HMAC** for this
internal service. Introduce **JWT only if** the service ever gains *multiple* or *third‑party*
callers, or needs to carry and enforce *end‑user* identity/claims itself. Until then, JWT would add
operational cost without security benefit. If/when adopted, prefer short‑lived asymmetric (RS256/
EdDSA) tokens validated against a JWKS endpoint, used **in addition to** TLS and network isolation.

---

## 11. Where All Data Lives

| Data | System | Physical location | Survives restart? | Tenant‑isolated? |
|------|--------|-------------------|-------------------|------------------|
| **Document chunks + vectors** | Weaviate | Docker named volume `weaviate_data` mounted at `/var/lib/weaviate` (LSM store + HNSW vector index + BM25 inverted index + WAL) | ✅ survives `docker compose down`; **deleted** only by `down -v` | ✅ `DocChunk` is multi‑tenant (`sub_<ownerUserId>`) |
| **Result cache** | MongoDB Atlas | `ready2go.ai_analysis_cache` | ✅ (Atlas‑managed, backed up) | by content hash (tenant‑agnostic) |
| **Rolling audit state** | MongoDB Atlas | `ready2go.ai_audit_state` (`_id = tenantKey`) | ✅ | ✅ one doc per tenant |
| **AI call log** | MongoDB Atlas | `ready2go.ai_call_log` | ✅ | appended (carries `attachmentId`) |
| **App logs (all levels)** | Filesystem | `logs/app.log` (JSON, rotating 10 MB × 5) | ⚠️ **ephemeral in container** — lost on restart unless a volume is mounted | n/a |
| **Error logs (WARNING+)** | Filesystem | `logs/error.log` (JSON, rotating 10 MB × 5) | ⚠️ ephemeral in container | n/a |

### Docker topology — `docker-compose.yml`
- **`api`** — this service, port `8000`, env from `.env`, overrides `WEAVIATE_URL=http://weaviate:8080`,
  `RELOAD=false`; has a `/healthz` healthcheck; depends on `weaviate`.
- **`weaviate`** — `cr.weaviate.io/semitechnologies/weaviate:1.27.0`, ports `8080` (HTTP) & `50051`
  (gRPC), anonymous access enabled, `DEFAULT_VECTORIZER_MODULE=none` (we supply our own vectors),
  data on the `weaviate_data` volume.
- **MongoDB is NOT in compose** — it's the external Atlas cluster from `MONGODB_URI` (shared with
  Next.js). Weaviate is self‑hosted in dev; production points at a managed equivalent.

> **Production note:** mount a volume for `logs/` (or ship stdout to a log aggregator) if you need
> durable file logs; in production the console renders JSON, which is the recommended path for
> container log collection.

---

## 12. Logging & Observability

Configured in [app/logging.py](../app/logging.py) via **structlog** bridged into stdlib logging.

- **Three sinks:** console (pretty in dev, JSON in prod), `logs/app.log` (all levels, JSON,
  rotating), `logs/error.log` (WARNING+, JSON, rotating). Rotation: `log_max_bytes` (10 MB) ×
  `log_backups` (5).
- **Request correlation:** [app/middleware.py](../app/middleware.py) `CorrelationMiddleware`
  assigns each request a UUID `request_id`, binds it into structlog contextvars (so *every* log
  line in that request carries it), logs `http.request`/`http.response` with latency, and returns
  it as the **`X-Request-ID`** response header. The analyze pipeline additionally binds `tenant`
  and `attachment_id`.
- **Pipeline events:** each step emits a structured event — `pipeline.fetched`,
  `pipeline.cache_hit`/`cache_miss`, `pipeline.extracted`, `pipeline.chunked`, `pipeline.embedded`,
  `pipeline.vectors_upserted`, `pipeline.scored`, `pipeline.llm_judge`, `pipeline.summarized`,
  `pipeline.persisted`, `pipeline.completed`, plus failure warnings (`pipeline.embed_failed_degraded`,
  `pipeline.weaviate_read_failed`, `pipeline.query_embed_failed`, `pipeline.sibling_search_failed`,
  `pipeline.similar_files_failed`, …), `pipeline.timeout`, `pipeline.unexpected_error`,
  `integrity.analyzed`, plus `audit.fallback_sample` / `audit.summary_failed` on the audit path.
- **Explanatory `detail` field.** Every WARNING/ERROR — and the key INFO milestones — carries a
  plain‑English `detail` string alongside the short dotted event code: it states *what happened, the
  impact (degraded / defaulting / skipped), and the likely cause*. The dotted code stays for
  grep/dashboards; the `detail` makes a single line readable on its own. This was added so a failing
  dependency or signal surfaces a clear message instead of silently collapsing to a default (the way
  the `name` signal once silently returned 0).
- **No silent swallows.** Exception handling across the service never discards an error without
  logging it. Failures degrade gracefully (the request still returns a usable result) **and** are
  visible in the logs.
- **Metrics:** `GET /metrics` exposes in‑process counters (reset on restart).
- **Cost/error tracing:** every OpenAI call is appended to `ai_call_log`; inspect via
  `GET /v1/diagnostics/calls`.

To trace one document end‑to‑end: `grep '"request_id":"<uuid>"' logs/app.log` (or filter by
`attachment_id`).

---

## 13. Configuration Reference

All settings are environment variables, loaded once and cached by `get_settings()` in
[app/config.py](../app/config.py). Defaults shown.

> **How to read this table.** "Default" is what the service uses if you don't set the variable.
> "Purpose" explains *what it does, when you'd change it, and what breaks if it's wrong* — so even
> someone who has never touched the service can configure it safely.

| Env var | Default | Purpose (what it does · when to change · impact) |
|---------|---------|---------|
| `ENV` | `development` | Declares which environment this instance is: `development`, `staging`, or `production`. **Why it matters:** when set to `production` the service becomes strict — it *refuses to boot* unless the required secrets (`OPENAI_API_KEY`, `WEAVIATE_URL`, `MONGODB_URI`, `PYTHON_INTEGRITY_TOKEN`) are present, and auth is **fail‑closed** (no token = every request rejected). In dev/staging it's lenient so you can run locally without secrets. Set it correctly per deployment. |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | The network address and port the API listens on. `0.0.0.0` means "accept connections on all interfaces" (needed inside Docker). Change `PORT` only if `8000` clashes with another service. |
| `LOG_LEVEL` | `INFO` | How chatty the logs are: `DEBUG` (everything, noisy), `INFO` (normal), `WARNING`/`ERROR` (problems only). Use `DEBUG` when diagnosing an issue, `INFO` in normal operation. |
| `RELOAD` | `false` | Auto‑restart the server when source files change. Handy while coding locally; **always `false` in containers/production** (it wastes resources and isn't safe under load). |
| `REQUEST_TIMEOUT_S` | `25.0` | The maximum seconds the analyze pipeline (download → embed → score → summarise) may run before it gives up and returns the safe "Under Review/50/degraded" fallback. **Raise it** if large PDFs legitimately need more time; **lower it** if you'd rather fail fast. Too low → healthy documents wrongly fall back; too high → slow requests hold connections open. |
| `LOG_DIR` | `logs` | Folder where the rotating log files (`app.log`, `error.log`) are written. Point it at a mounted volume if you want logs to survive container restarts. |
| `LOG_MAX_BYTES` | `10485760` (10 MB) | How big a single log file may grow before it's "rotated" (renamed and a fresh one started). Prevents one giant unbounded log file from filling the disk. |
| `LOG_BACKUPS` | `5` | How many rotated old log files to keep before the oldest is deleted. With the defaults you keep ~50 MB of history per log. Increase for longer retention. |
| `PYTHON_INTEGRITY_TOKEN` | `""` | The **shared password** the Next.js app must send as `Authorization: Bearer <token>`. This is the primary auth gate. **In production it is mandatory** — empty means the service rejects everything (fail‑closed). In dev, leaving it empty disables auth for convenience. Rotate it by updating this value on both this service and Next.js. |
| `HMAC_SECRET` | `""` | Optional second layer of security: a secret used to verify a signature over the exact request body (`X-Ready2Go-Signature`). Detects tampering/replay. Leave empty to skip; set it (matching Next.js) for defence‑in‑depth on untrusted networks. |
| `OPENAI_API_KEY` | `""` | Credential for calling OpenAI (embeddings + the summary/judge model). **Without it** the service can't generate vectors or summaries and silently degrades to metadata‑only/fallback behaviour. **Required in production.** This is a billable secret — keep it private. |
| `OPENAI_EMBED_MODEL` | `text-embedding-3-small` | Which OpenAI model converts text into the 1536‑number meaning vectors. Changing it changes the vector space, so you must re‑embed existing documents (bump `MODEL_VERSION`). Rarely changed. A blank value is auto‑replaced with the default so a stray empty env var can't break OpenAI calls. |
| `OPENAI_SUMMARY_MODEL` | `gpt-4o-mini` | Which chat model writes the paragraph document summaries and acts as the borderline "LLM judge". `gpt-4o-mini` is chosen for being cheap and fast. Swap for a stronger model if you want richer summaries at higher cost. Blank → default. |
| `WEAVIATE_URL` | `""` | Address of the vector database. **Without it** there is no semantic scoring at all (content/category/duplication/name signals can't run). **Required in production.** In Docker dev this is set to `http://weaviate:8080`. |
| `WEAVIATE_API_KEY` | `""` | Auth key for a secured/managed Weaviate. Leave empty for the local anonymous dev instance; set it for a hosted production cluster. |
| `MONGODB_URI` | `""` | Connection string to MongoDB (Atlas). **Without it** the cache, rolling audit counters, and call log don't work — every request would re‑pay OpenAI and the audit endpoint would be empty. **Required in production.** Contains credentials — keep it secret. |
| `MONGODB_DB` | `ready2go` | Which database inside the cluster to use. It's **shared** with the Next.js app; this service only touches its own `ai_*` collections. Change only if you intentionally isolate environments by database name. |
| `MODEL_VERSION` | `integrity-v1` | A label stamped onto every result and used as part of the cache key. **This is your "cache reset" lever:** bump it (e.g. `integrity-v2`) whenever you change the embedding model, scoring logic, or prompts so old cached verdicts are ignored and documents get re‑analysed. |
| `MAX_CHUNKS_PER_DOC` | `200` | Controls how many 500‑token slices a document is split into before embedding. **Set to `0` for no limit** — the entire document is chunked and embedded regardless of size (use when complete coverage matters more than cost, e.g. full compliance checks on large PDFs). Any positive value silently drops the tail beyond that count. Default `200` handles typical PDFs; `0` gives complete coverage at higher token cost. |
| `AUDIT_SAMPLE_CAP` | `25` | Controls how many analyzed files are passed to the AI when generating the org audit narrative. **Set to `0` to pass ALL analyzed documents** — the AI gets the full picture (accurate but costs more tokens for large vaults). Any positive N passes only the worst-N scoring files. The `all_analyzed` array in `ai_audit_state` stores every analyzed doc (no score threshold, no cap) precisely for this. Default `25` = worst 25 files only; `0` = complete corpus. |
| `AUDIT_FALLBACK_CAP` | `50` | Safety net for `AUDIT_SAMPLE_CAP=0`. If the full-corpus audit call fails (typically the payload exceeds the model's context window), the audit automatically **retries with only the worst-`AUDIT_FALLBACK_CAP` documents** and returns the result with **`degraded: true`** so the caller knows a reduced sample was used. Raise it for richer fallbacks, lower it to be safer/cheaper. |
| `WEIGHT_CONTENT` | `0.50` | How much the **content‑match** signal counts toward the final score (the biggest factor — does the document's meaning match the plan?). The four weights should sum to ~1.0. Increase to punish off‑topic documents harder. |
| `WEIGHT_NAME` | `0.19` | How much the **filename‑match** signal counts. Increase if filenames are reliable in your data; decrease if people name files randomly. |
| `WEIGHT_QUALITY` | `0.19` | How much the **extraction‑quality** signal counts (did we actually get real text, or was it a blank/scanned page?). Increase to penalise unreadable uploads more. |
| `WEIGHT_DUPLICATION` | `0.12` | How much the **uniqueness** signal counts (is this a near‑duplicate of another file in the same plan?). Increase to discourage duplicate uploads. |
| `BAND_IN_SYNC` | `71` | The score cutoff at/above which a document is labelled **Compliant** (good). Raise it to make "Compliant" harder to earn (stricter), lower it to be more lenient. |
| `BAND_REVIEWING` | `41` | The score cutoff at/above which a document is **Under Review**; anything below becomes **Non-Compliant**. Tune together with `BAND_IN_SYNC` to set how strict the three‑way verdict is. |
| `LLM_JUDGE_BAND` | `60,72` | The borderline score range (`low,high`) where the cheap rule‑based score is uncertain, so the service spends an extra AI call to double‑check the verdict. Widen it to use the AI judge more often (more accurate, more cost); narrow/disable to save money. |
| `PARSER_BACKEND` | `basic` | Which engine extracts text from files. `basic` = built‑in pure‑Python readers (PDF/DOCX/XLSX/CSV), no OCR. `liteparse` is reserved for a future OCR‑capable backend (reads scanned/image PDFs). Leave as `basic` unless OCR is added. |
| Cloudinary / Seed vars | — | `CLOUDINARY_*` and `SEED_*` are used only by **offline seed/prep scripts** to create dev/staging test data — they are never read on the live request path. Ignore them for normal operation. |

---

## 14. Complete File‑by‑File Guide

Every Python module under `app/`, its role, and key functions. (Who/what/why per file.)

### Top level
| File | Role |
|------|------|
| [app/main.py](../app/main.py) | App entrypoint. `create_app()` builds the FastAPI app, adds `CorrelationMiddleware`, registers routers (health open; integrity/audit behind `require_auth`). `lifespan()` configures logging, validates prod secrets, bootstraps Weaviate collections + Mongo indexes on startup, and closes clients on shutdown. `run()` launches uvicorn. |
| [app/config.py](../app/config.py) | Typed env settings (`Settings`), cached `get_settings()`, and `validate_production_secrets()` (fail‑closed boot). Single source of truth for all knobs. |
| [app/schemas.py](../app/schemas.py) | The **wire contract** with Next.js — all Pydantic request/response models and the `Literal` status/category types. Field names + limits are a hard contract. |
| [app/security.py](../app/security.py) | `require_auth` dependency — bearer token + optional HMAC, constant‑time, fail‑closed in prod. |
| [app/middleware.py](../app/middleware.py) | `CorrelationMiddleware` — per‑request UUID, structlog binding, `http.request`/`http.response` logs, `X-Request-ID` header. |
| [app/logging.py](../app/logging.py) | `configure_logging()` — structlog + rotating file handlers (`app.log`, `error.log`), console renderer (pretty/JSON). |

### `app/api/` — HTTP routes
| File | Role |
|------|------|
| [app/api/integrity.py](../app/api/integrity.py) | `POST /v1/integrity/analyze` (the 12‑step pipeline, timeout + graceful fallback) and `POST /v1/integrity/rescan`. Orchestrates fetch→extract→chunk→embed→upsert→score→judge→summarize→persist. |
| [app/api/audit.py](../app/api/audit.py) | `POST /v1/audit/summary` — reads rolling state, builds bounded narrative, marks state clean. |
| [app/api/health.py](../app/api/health.py) | `/healthz`, `/readyz` (live dep probes), `/metrics` (in‑process counters + `record_request`), `/v1/diagnostics/calls` (auth‑gated call‑log reader). |

### `app/ingest/` — getting text out of files
| File | Role |
|------|------|
| [app/ingest/fetch.py](../app/ingest/fetch.py) | `fetch_bytes()` — downloads the file (size‑capped), computes SHA‑256, returns `FetchResult`; typed `FetchError` → 502. |
| [app/ingest/extract.py](../app/ingest/extract.py) | `BasicParser` (PDF via pdfplumber→pypdf, DOCX, XLSX, CSV) behind a `DocumentParser` protocol; `get_parser()` factory. Produces `Extraction(text, quality)` with `ExtractionQuality(chars, pages_or_rows, is_scan_only, is_empty)`. |
| [app/ingest/chunk.py](../app/ingest/chunk.py) | `chunk()` — token‑aware 500/50 overlapping windows (tiktoken `cl100k_base`), capped at `max_chunks`. Defines the `Chunk` dataclass. |

### `app/llm/` — OpenAI wrappers
| File | Role |
|------|------|
| [app/llm/embeddings.py](../app/llm/embeddings.py) | `embed_texts()` — single batched embeddings call, retries (tenacity), logs to call log; raises on failure (caller degrades). |
| [app/llm/client.py](../app/llm/client.py) | `chat_json()` — JSON‑mode chat with retries and a **fallback dict** (never raises); used by the LLM judge and summaries. |

### `app/scoring/` — turning signals into a verdict
| File | Role |
|------|------|
| [app/scoring/signals.py](../app/scoring/signals.py) | The four pure signal functions (`content_alignment`, `name_alignment`, `extraction_quality`, `duplication`) + cosine helper. |
| [app/scoring/integrity.py](../app/scoring/integrity.py) | `compute()` — weighted blend, hard override (scan/empty cap), banding, judge flag. Returns `IntegrityResult`. |
| [app/scoring/thresholds.py](../app/scoring/thresholds.py) | Config‑driven `Weights`, `Bands`, `JudgeBand`; `score_to_status()`. |

### `app/summary/` — natural‑language output
| File | Role |
|------|------|
| [app/summary/per_doc.py](../app/summary/per_doc.py) | `one_liner()` — per‑doc ≤2000‑char detailed summary covering all major points (single‑pass or map‑reduce) with deterministic fallback. |
| [app/summary/audit.py](../app/summary/audit.py) | `build()` + `derive_posture()` — bounded org audit narrative (≤1500 chars, ≤8 findings, posture). |

### `app/vectors/` — the Weaviate layer
| File | Role |
|------|------|
| [app/vectors/client.py](../app/vectors/client.py) | Lazy singleton Weaviate v4 client (`get_client`), `ensure_collections()` bootstrap, `close_client()`. Vectorizer disabled — we supply OpenAI vectors. |
| [app/vectors/schema.py](../app/vectors/schema.py) | Collection definitions: `DocChunk` (multi‑tenant) properties. |
| [app/vectors/repo.py](../app/vectors/repo.py) | CRUD + similarity ops: `upsert_chunks` (returns inserted chunk UUIDs → stored in `ai_analysis_cache.vectorIds`), `delete_by_attachment`, `get_all_chunks` (paginated — reads every chunk, no fixed ceiling), `content_centroid`, `sibling_similarities`. Defines `StoredChunk`. |

### `app/store/` — the MongoDB layer
| File | Role |
|------|------|
| [app/store/models.py](../app/store/models.py) | Request‑path Mongo client + collection accessors (`get_cache_col`/`get_state_col`/`get_log_col`) and `ensure_indexes()`. Collection name constants. |
| [app/store/cache.py](../app/store/cache.py) | `get()` / `put()` for `ai_analysis_cache` (the dedup cache). |
| [app/store/aggregate.py](../app/store/aggregate.py) | `update()` (O(1) `$inc` of rolling counters + `$push` to `all_analyzed` and bounded `notable`, each entry carrying a ≤600‑char summary excerpt), `read()`, `mark_clean()` for `ai_audit_state`. |
| [app/store/calllog.py](../app/store/calllog.py) | `append()` — one row per OpenAI call into `ai_call_log` (never raises). |
| [app/store/mongo.py](../app/store/mongo.py) | Separate connection for **offline** prep/seed scripts; never imported by request‑path routes. |

---

## 15. Running, Testing & Local Dev

### Run locally
```bash
# install deps (uses uv)
uv sync

# run the API (reads .env)
uv run main         # console entrypoint -> uvicorn on HOST:PORT

# or the full dev stack (API + local Weaviate)
docker compose up --build
```
Set at minimum in `.env`: `OPENAI_API_KEY`, `WEAVIATE_URL`, `MONGODB_URI`, and (for prod)
`PYTHON_INTEGRITY_TOKEN`.

### Smoke test
```bash
curl localhost:8000/healthz          # {"status":"ok"}
curl localhost:8000/readyz           # per-dependency health
```

### Tests & quality gates
```bash
uv run pytest            # unit + contract tests (tests/)
uv run ruff check app/   # lint
uv run mypy app/         # type check
```
Test suite covers: `test_scoring.py` (signal math + banding + scan/empty override), `test_analyze_contract.py`
(auth guard, response contract, cache‑hit path, graceful Weaviate‑failure fallback, fetch→502, the
**name signal scored from the filename** + graceful handling when embeddings fail, and the
**graceful audit fallback**),
`test_store_cache.py` (cache round‑trip + model‑version isolation), `test_ingest.py`, `test_health.py`.
Mocks: `mongomock` (Mongo), `respx` (OpenAI HTTP), `unittest.mock` (Weaviate).

---

## 16. Design Decisions & FAQ

**Q: Why two databases (Weaviate + MongoDB)?**
They answer different questions. Weaviate stores *meaning* (vectors) for semantic scoring; MongoDB
stores *results, counters, and logs* for caching and audit. See [§5](#5-the-two-databases--why-both).

**Q: If results are cached in MongoDB, why keep the vector DB?**
The cache only short‑circuits *byte‑identical* re‑uploads. All the actual analysis — content match,
duplicate detection, name search — requires the vectors in Weaviate. The cache makes
us cheap; Weaviate makes us smart.

**Q: Why a content‑hash cache key?**
Same bytes ⇒ same answer. `(contentHash, modelVersion)` is a unique index, so a hit is one indexed
lookup and costs zero OpenAI tokens. Bumping `MODEL_VERSION` cleanly invalidates everything.

**Q: Why rolling counters for audit instead of querying all docs?**
`ai_audit_state` is incremented O(1) on each analyze, so the audit endpoint is O(1) regardless of
how many documents a customer has — no expensive full scans.

**Q: Why four weighted signals instead of one AI score?**
Interpretability and robustness. Each signal is explainable (the UI shows the breakdown) and tunable
via env weights, and multi‑factor scoring resists single‑signal false positives. (A fifth
"category‑fit" signal was removed because its reference prototypes were never seeded, so it only ever
returned a neutral placeholder; its weight was folded into the other four.) The expensive LLM judge
is reserved for the narrow 60–72 borderline band only.

**Q: Why graceful degradation instead of erroring?**
A document review shouldn't 500 because OpenAI/Weaviate hiccuped. The service returns a safe
`Under Review/50/degraded` result and, crucially, **does not cache it**, so the next call re‑analyses
cleanly once dependencies recover. The same principle applies to the audit endpoint, which returns a
payload‑derived fallback instead of erroring.

**Q: Why chunk with overlap?**
So boundary sentences aren't lost; the document centroid then represents the whole document. See
[§4](#4-what-is-a-doc-chunk).

**Q: Why shared‑secret auth, not JWT?**
One trusted internal server‑to‑server caller; the rich business context must travel in the body
regardless; a shared secret (+ optional HMAC) is simpler, rotatable, and fail‑closed. Adopt JWT only
when multiple/third‑party callers or end‑user identity enter the picture. See
[§10](#10-authentication--why-shared-secret-not-jwt).

**Q: What is the "doc chunk" the chat keeps mentioning?**
A 500‑token, 50‑token‑overlapping slice of one document, stored as a row in the Weaviate `DocChunk`
collection. See [§4](#4-what-is-a-doc-chunk).

---

*This document reflects the service as implemented in the `develop` branch. When you change a
schema field, an env var, a weight/band, or a pipeline step, update the matching section here so it
stays the single source of truth.*
