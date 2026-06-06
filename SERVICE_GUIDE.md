# Ready2Go AI Service — Complete Guide (from zero knowledge)

This is the **go-to document** for understanding this service. It assumes you
know nothing about it. Read top to bottom, or jump to a section. Every term is
defined the first time it appears.

> **One-line summary:** This is a small backend service. The Next.js app sends it
> an uploaded document; it figures out *whether that document actually belongs in
> the plan it was filed under*, gives it a **score (0–100)** and a **status**, and
> writes a short plain-English **summary**. That's it.

---

## Table of contents

1. [What this service is](#1-what-this-service-is)
2. [The big picture (architecture)](#2-the-big-picture-architecture)
3. [The analyze pipeline, step by step](#3-the-analyze-pipeline-step-by-step)
4. [How scoring works (the 5 signals)](#4-how-scoring-works-the-5-signals)
5. [The databases — and why we need BOTH Mongo and a vector DB](#5-the-databases--and-why-we-need-both-mongo-and-a-vector-db)
6. [What "DocChunk" is](#6-what-docchunk-is)
7. [The API contract for Next.js](#7-the-api-contract-for-nextjs)
8. [Auth: why a shared secret, not JWT](#8-auth-why-a-shared-secret-not-jwt)
9. [Where everything is stored & how to inspect it](#9-where-everything-is-stored--how-to-inspect-it)
10. [Configuration reference (.env)](#10-configuration-reference-env)
11. [Glossary](#11-glossary)

---

## 1. What this service is

Imagine a filing cabinet of business-continuity documents (disaster recovery
plans, compliance checklists, etc.). A person uploads a PDF and says "this belongs
in the *Main BCP* plan." But does it really? Maybe they uploaded a cyber-security
checklist into a flood-recovery plan by mistake.

**This service is the automated reviewer that checks that filing.** For each
uploaded document it answers three questions:

| Output | Example | Meaning |
|---|---|---|
| **status** | `In Sync` / `Reviewing` / `Deviation Found` | Does it belong here? |
| **score** | `0`–`100` | How confident, as a number |
| **summary** | "A 5-page FEMA checklist for prioritizing cyber assets…" | What the document *is*, in one sentence |

It is an **internal microservice**. The only thing that ever calls it is the
**Next.js app**. End users never talk to it directly.

It's built with **FastAPI** (a Python web framework) and uses three external
systems: **OpenAI** (to understand text), **Weaviate** (a vector database), and
**MongoDB Atlas** (a regular database). Those are explained below.

---

## 2. The big picture (architecture)

```
┌──────────────┐   logs in,            ┌─────────────────────────────┐
│  End user    │   uploads a file      │        Next.js app          │
│ (browser)    │ ───────────────────▶  │  • authenticates the human  │
└──────────────┘                       │    (JWT / session)          │
                                       │  • owns ALL app-domain      │
                                       │    Mongo writes             │
                                       └───────────────┬─────────────┘
                                                       │ HTTP POST
                                                       │ Authorization: Bearer <shared secret>
                                                       │ body carries tenantKey, plan, attachment
                                                       ▼
                                       ┌─────────────────────────────┐
                                       │   THIS service (Python)     │
                                       │   FastAPI on port 8000      │
                                       └───┬──────────┬──────────┬───┘
                                           │          │          │
                          embeddings +     │          │          │  reads/writes its OWN
                          chat summaries   │          │          │  ai_* collections
                                           ▼          ▼          ▼
                                  ┌──────────┐  ┌──────────┐  ┌──────────────┐
                                  │  OpenAI  │  │ Weaviate │  │ MongoDB Atlas│
                                  │   API    │  │ (vectors)│  │  (ai_* docs) │
                                  └──────────┘  └──────────┘  └──────────────┘
```

### The two golden rules of this architecture

1. **Next.js owns the human and the app's domain data.** It logs the user in, and
   it is the *only* thing that writes the app's real collections
   (`continuityplans`, `continuityauditreports`, …). This Python service **never**
   writes those.
2. **This service owns only its `ai_*` bookkeeping collections** (cache, audit
   state, call log) inside the *same* MongoDB database (`ready2go`). It's a
   trusted internal worker, not a public API.

This separation is why the auth is a simple shared secret and why identity is
passed in the request body — see [section 8](#8-auth-why-a-shared-secret-not-jwt).

---

## 3. The analyze pipeline, step by step

When Next.js calls `POST /v1/integrity/analyze`, the service runs an 11-step
**pipeline**. In the logs, each step prints a line beginning with `pipeline.`.
Here's exactly what each one does (code: [app/api/integrity.py](app/api/integrity.py)):

| # | Log line | Plain English |
|---|---|---|
| 1 | `pipeline.fetched` | Download the document from `fileUrl` (the raw bytes). |
| 2 | `pipeline.cache_hit` / `cache_miss` | Compute a fingerprint (SHA-256) of the bytes. If we've analyzed these *exact* bytes before → return the saved answer instantly (a **cache hit**, costs nothing). Otherwise continue. |
| 3 | `pipeline.extracted` | Pull the readable text out of the PDF/DOCX/CSV/XLSX. |
| 4 | `pipeline.chunked` | Split that text into smaller pieces ("chunks") the AI can handle. |
| 5 | `pipeline.embedded` | Ask OpenAI to turn each chunk into an **embedding** — a list of 1536 numbers that represents its *meaning*. |
| 6 | `pipeline.vectors_upserted` (+ `weaviate.tenant_created`) | Store those vectors in Weaviate, under this customer's private tenant. |
| 7 | `pipeline.scored` | Compute the 5 signals → a final score + status (see [section 4](#4-how-scoring-works-the-5-signals)). |
| 8 | `pipeline.llm_judge` *(optional)* | If the score is borderline (60–72), ask the AI for a tie-breaking opinion. |
| 9 | `pipeline.summarized` | Ask OpenAI to write the one-line summary of the document. |
| 10 | `pipeline.persisted` | Save the result to MongoDB (cache + audit counters). **Skipped if the result was `degraded`** — see note below. |
| 11 | `pipeline.completed` | Return the answer to Next.js as JSON. |

> **About the `/healthz` noise:** Between your real requests you'll see a `GET
> /healthz ... 200 OK` every ~10 seconds. That's just Docker's health check
> ("are you alive?"). It is **not** your traffic; ignore it.

### "degraded" — an important concept

`degraded: true` means **a dependency we needed was unreachable** during this run
(e.g. OpenAI embeddings failed, or Weaviate was down). When that happens:

- The service still returns a result (it never crashes — *graceful degradation*).
- But the result is **not saved to the cache or audit counters**, because some
  signals couldn't be computed and the score would be misleadingly low. The next
  call re-analyzes from scratch.

An empty document (no text) is **not** degraded — that's a legitimate, low-quality
result and it *is* cached.

---

## 4. How scoring works (the 5 signals)

The score is a weighted blend of **5 independent signals**, each a number from 0
to 1. Pure math, no AI needed (code: [app/scoring/signals.py](app/scoring/signals.py),
weights in [app/scoring/thresholds.py](app/scoring/thresholds.py)).

| Signal | Weight | Question it answers | How |
|---|---|---|---|
| **content_alignment** | **0.40** | Does the document's *meaning* match the plan? | Cosine similarity between the document's average vector and the plan's text vector. |
| **name_alignment** | **0.15** | Does the filename match the plan? | Weaviate hybrid (keyword + vector) search of the filename vs the plan label. |
| **category_fit** | **0.20** | Is it filed under the right category (coop/bcp/compliance)? | Compare document vector to each category's reference vector; penalize if another category fits better (mis-filed). |
| **extraction_quality** | **0.15** | Did we get usable text out of it? | Based on characters/pages extracted; heavy penalty for empty or scan-only (image) files. |
| **duplication** | **0.10** | Is it a near-duplicate of another file in the plan? | Distance to the nearest sibling document; near-duplicates score low. |

The weighted blend produces a 0–100 score, then a **band** turns it into a status
(code: [app/scoring/thresholds.py](app/scoring/thresholds.py)):

| Score | Status |
|---|---|
| **≥ 71** | `In Sync` |
| **41 – 70** | `Reviewing` |
| **≤ 40** | `Deviation Found` |

If the score lands in the **LLM judge band (60–72)** it's genuinely borderline, so
the service optionally asks the AI to confirm or flag it.

### Worked example (your real test run)

Your FEMA cyber checklist filed under "Main BCP" scored:

```
components = {content: 22, name: 0, category: 50, quality: 93, duplication: 100}
→ score = 43 → status = Reviewing
```

- **content 22** — a *cyber* checklist only weakly matches a generic *BCP* plan.
- **name 0** — the filename `fema_cyber-asset-id-prioritization-checklist.pdf`
  doesn't match the label "Main BCP" at all.
- **category 50** — neutral, because no category reference vectors are seeded yet.
- **quality 93** — clean text extracted from a real (non-scanned) 5-page PDF.
- **duplication 100** — it's the only file, so nothing to duplicate.

Result: **43 / Reviewing** is a *correct, meaningful* verdict — the document is
borderline for that plan.

---

## 5. The databases — and why we need BOTH Mongo and a vector DB

This is the most common point of confusion, so here it is plainly.

> **MongoDB stores FACTS. Weaviate stores MEANING.** They answer completely
> different questions and neither can do the other's job.

### MongoDB Atlas — facts & bookkeeping

MongoDB is a normal database (documents/JSON). This service keeps **three** of its
own collections here, all prefixed `ai_` (code:
[app/store/models.py](app/store/models.py)). They live in the same `ready2go`
database as the app, but only this service reads/writes them.

| Collection | Purpose | Keyed by | Written | Shape (key fields) |
|---|---|---|---|---|
| **`ai_analysis_cache`** | **Dedup cache.** The headline cost lever — never re-analyze the same bytes twice. | `contentHash` + `modelVersion` (unique) | after each successful analyze | `attachmentId, status, score, summary, scoreComponents, analyzedAt` ([cache.py](app/store/cache.py)) |
| **`ai_audit_state`** | **Rolling totals per customer.** Powers the vault-wide audit cheaply (O(1) per upload — no scanning all docs). | `_id = tenantKey` | after each successful analyze | `counts{coop,bcp,…}, integrity{inSync,reviewing,deviation}, scoreSum, scoreCount, notable[], dirty` ([aggregate.py](app/store/aggregate.py)) |
| **`ai_call_log`** | **Audit trail of AI spend.** One row per OpenAI call so you can see cost and debug failures. | auto + `attachmentId`, `ts` | every OpenAI call | `kind, model, tokens, latency_ms, success, error, ts` |

### Weaviate — meaning & similarity (the vector database)

A document's *meaning* can be represented as a **vector** (a long list of numbers
from OpenAI). Two documents about similar topics have vectors that are
mathematically "close." Weaviate is a database **specialized for storing these
vectors and finding the nearest ones fast.**

This is the entire reason it exists. Scoring needs questions like:

- "What is this document's meaning **most similar** to?" (content alignment)
- "Which category vector is **closest**?" (category fit)
- "Is there a **near-duplicate** sibling?" (duplication)
- "Which stored file best matches this **filename**?" (name alignment)

These are all **nearest-neighbour** searches over vectors. **MongoDB cannot do
that** — it can find exact matches and ranges, but not "closest in meaning."

### The distinction, side by side

| | MongoDB `ai_analysis_cache` | Weaviate `DocChunk` |
|---|---|---|
| Question it answers | "Have I seen these **exact bytes** before?" | "Is this document's **meaning** close to others?" |
| Match type | Exact fingerprint (SHA-256) | Fuzzy / similarity (cosine distance) |
| Stores | The final verdict (score, status, summary) | The 1536-number vector of each chunk |
| If you removed it | Every call would re-pay OpenAI costs | Scoring could not compute content/category/duplication/name signals |

So: the **cache** saves money by skipping repeat work; the **vector DB** is what
makes the scoring possible in the first place. They are complementary, not
redundant.

---

## 6. What "DocChunk" is

`DocChunk` is the name of the main **collection inside Weaviate** (the vector DB's
equivalent of a table). Code: [app/vectors/schema.py](app/vectors/schema.py).

- A document is split into **chunks** (step 4 of the pipeline). Each chunk becomes
  one row in `DocChunk`.
- Each row holds the chunk's **text**, its **vector** (1536 numbers), and metadata:

| Field | Meaning |
|---|---|
| `attachmentId` | Which document this chunk belongs to (the idempotency key). |
| `planId` | Which plan the document is filed under. |
| `category` | `coop` / `bcp` / `compliance`. |
| `fileName` | Original filename (used by the name-alignment signal). |
| `contentHash` | SHA-256 of the file bytes (ties back to the cache). |
| `chunkIndex` | Position of this chunk in the document (0, 1, 2, …). |
| `text` | The chunk's text (also keyword-indexed for hybrid search). |
| `modelVersion` | Embedding model version, so old vectors can be invalidated. |

### Multi-tenancy (data isolation)

`DocChunk` is **multi-tenant**: each customer (subadmin) gets a private partition
called a **tenant**, named `sub_<ownerUserId>`. Weaviate enforces that tenant A
can never read tenant B's data.

> **Note (a bug we fixed this session):** with multi-tenancy on, a tenant must be
> **created** before you can write to it. The code now calls `ensure_tenant()`
> automatically before the first write — that's the `weaviate.tenant_created` log
> line you saw.

### The second collection: `CategoryPrototype`

A small, **global** (non-tenant) collection holding one reference vector per
category (`coop`, `bcp`, `compliance`). The category-fit signal compares a
document against these. It's seeded once from authoritative sources; until then,
category-fit returns a neutral 0.5.

---

## 7. The API contract for Next.js

Base URL (local Docker): `http://localhost:8000`. All `/v1/*` routes require the
auth header (see [section 8](#8-auth-why-a-shared-secret-not-jwt)). All request
bodies are JSON with **camelCase** field names.

All shapes below come from [app/schemas.py](app/schemas.py).

### How Next.js calls it

```
POST  http://<host>:8000/v1/integrity/analyze
Headers:
  Authorization: Bearer <PYTHON_INTEGRITY_TOKEN>
  Content-Type: application/json
Body: { …nested JSON… }
```

---

### 7.1 `POST /v1/integrity/analyze` — score one document

The body is **nested** into three objects: `tenantContext`, `plan`, `attachment`.
(A flat body returns `422 Unprocessable Entity`.)

**`tenantContext`** — who this is for:

| Field | Type | Required | Purpose |
|---|---|---|---|
| `tenantKey` | string | ✅ | The customer partition, e.g. `sub_<ownerUserId>`. Scopes all vector ops + audit state. |
| `actorUserId` | string | optional | The user who triggered it (for logging/audit only). |

**`plan`** — the plan the document was filed under (this is the "ground truth" the
document is compared against):

| Field | Type | Required | Purpose |
|---|---|---|---|
| `planId` | string | ✅ | Plan identifier; groups sibling documents. |
| `label` | string | ✅ | Human name, e.g. "Main BCP". Used by name-alignment. |
| `overview` | string | optional | Plan description. Becomes part of the content-alignment reference text. |
| `category` | enum | optional (default `coop`) | One of `coop` \| `bcp` \| `compliance`. |
| `steps` | string[] | optional | Plan steps; also fold into the content reference text. |

**`attachment`** — the document itself:

| Field | Type | Required | Purpose |
|---|---|---|---|
| `attachmentId` | string | ✅ | The document's MongoDB id (hex). Idempotency key everywhere. |
| `fileName` | string | ✅ | Original filename. Drives name-alignment. |
| `fileExtension` | enum | ✅ | One of `pdf` \| `docx` \| `csv` \| `xlsx`. Picks the text extractor. |
| `fileUrl` | string | ✅ | A **reachable** URL to download the bytes. A bad URL → `502`. |
| `fileMime` | string | optional | MIME type hint. |
| `fileSizeBytes` | int | optional | Size hint. |
| `cloudinaryPublicId` | string | optional | Cloudinary reference. |
| `cloudinaryResourceType` | string | optional | Cloudinary reference. |

**Top-level optional fields:**

| Field | Type | Purpose |
|---|---|---|
| `extractedText` | string | If Next.js already extracted the text, pass it to skip download/extract. |
| `vectorKey` | string | Reserved hint for vector storage. |

**Example request:**

```bash
curl -X POST http://localhost:8000/v1/integrity/analyze \
  -H "Authorization: Bearer change-me-long-random-token" \
  -H "Content-Type: application/json" \
  -d '{
    "tenantContext": { "tenantKey": "sub_6a054db0dc2d5c9cc796a36b" },
    "plan": {
      "planId": "plan-abc",
      "label": "Main BCP",
      "category": "bcp",
      "overview": "Keep operations running during disruptions.",
      "steps": ["Identify risks", "Define recovery procedures"]
    },
    "attachment": {
      "attachmentId": "att_001",
      "fileName": "bcp_procedures.pdf",
      "fileExtension": "pdf",
      "fileUrl": "https://example.com/bcp_procedures.pdf"
    }
  }'
```

**Response (`AnalyzeResponse`):**

```jsonc
{
  "status": "Reviewing",            // In Sync | Reviewing | Deviation Found
  "score": 43,                       // 0–100
  "summary": "A 5-page checklist…",  // ≤ 280 chars
  "analyzedAt": "2026-06-04T11:55:40Z",
  "modelVersion": "integrity-v1",
  "details": {
    "componentScores": { "content": 22, "name": 0, "category": 50, "quality": 93, "duplication": 100 },
    "similarFiles": [ { "attachmentId": "att_009", "similarity": 0.81 } ],
    "cacheHit": false,               // true if served from cache (free)
    "degraded": false                // true if a dependency was down
  }
}
```

> Next.js takes this response and writes the `aiIntegrity*` fields onto its own
> document — **this service never writes them itself.**

---

### 7.2 `POST /v1/audit/summary` — vault-wide audit narrative

Produces a bounded, cheap summary of the whole vault from the rolling
`ai_audit_state` counters (it never re-reads every document).

**Request (`AuditSummaryRequest`):** `tenantContext`, plus pre-aggregated
`totals`, `averageScore`, `counts`, `integrity` breakdown, and a `plans[]` list.
(These are computed by Next.js and passed in.)

**Response (`AuditSummaryResponse`):**

| Field | Type | Meaning |
|---|---|---|
| `summary` | string (≤360) | Plain-English overview of the vault. |
| `findings` | string[] (≤4) | Actionable bullet points, most urgent first. |
| `posture` | enum | `Resilient` \| `Steady` \| `At Risk` (deterministic — see [audit.py](app/summary/audit.py)). |
| `averageScore` | int | Average integrity score across analyzed docs. |

---

### 7.3 `POST /v1/integrity/rescan` — force re-analysis (backfill)

Clears cached results (and Weaviate chunks) so the next `analyze` runs fresh.
Code: [app/api/integrity.py](app/api/integrity.py).

**Request (`RescanRequest`):**

| Field | Type | Purpose |
|---|---|---|
| `attachmentIds` | string[] | Which documents to rescan. |
| `tenantKey` | string | Their tenant. |
| `force` | bool | If `true`, clear even already-cached entries; if `false`, only those without a cache entry. |

**Response:** `{ "scheduled": N, "skipped": M, "message": "…" }` (HTTP `202`).

---

### 7.4 Operational endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /healthz` | none | Liveness — "the process is up." Always `{"status":"ok"}`. |
| `GET /readyz` | none | Readiness — live probes of Weaviate, MongoDB, OpenAI. Returns `ok` or `degraded` + per-dependency detail. |
| `GET /metrics` | none | Counters: request count, cache-hit ratio, token spend, error rate, average latency. |
| `GET /v1/diagnostics/calls?attachmentId=…` | ✅ | Recent `ai_call_log` rows to trace one document's AI cost/errors. |

Code: [app/api/health.py](app/api/health.py).

---

## 8. Auth: why a shared secret, not JWT

You asked: *"we are using JWT, right?"* — **No, and on purpose.** Here's the
reasoning, because it's a deliberate, professional design choice.

### There are two different auth problems

| Problem | Who | Right tool |
|---|---|---|
| **End-user auth** | A human ↔ the Next.js app | **JWT / session** ✅ (Next.js already does this) |
| **Service-to-service auth** | Next.js ↔ this internal service | **Shared secret** ✅ (what we use) |

This service has the *second* problem, not the first. It is an internal worker
with exactly **one** caller (Next.js) and is not exposed to end users.

### Why a shared secret is the correct choice here

The check is a single constant-time string comparison
(`Authorization: Bearer <PYTHON_INTEGRITY_TOKEN>`, code:
[app/security.py](app/security.py)):

- **Simple & robust.** No token expiry, no refresh flow, no public-key (JWKS)
  fetching, no clock-skew issues — all of which JWT would add for zero benefit
  with a single trusted caller.
- **Right-sized.** JWT shines when *many different* clients with *different
  identities and permissions* call you and you must verify claims without a shared
  database. None of that applies here.
- **Defense in depth available.** There's an optional second factor: an HMAC
  signature of the request body (`HMAC_SECRET`, `X-Ready2Go-Signature`). If set,
  the body must be signed too — so even a leaked bearer token isn't enough.
- **Fail-closed in production.** If the token env var is missing in production,
  the service rejects *everything* (401) rather than silently allowing anonymous
  access. In development with no token, auth is open for convenience.

### Why identity comes from the request body, not a JWT

The service deliberately does **not** re-authenticate the end user. Next.js
*already* verified the human and resolved who they are. It then passes the
resolved identity explicitly in the body:

```jsonc
"tenantContext": { "tenantKey": "sub_<ownerUserId>", "actorUserId": "<userId>" }
```

So the bearer token answers **"is the caller really Next.js?"**, and the body
answers **"which customer/user is this work for?"**. Making the service parse a
user JWT would mean duplicating Next.js's auth logic, sharing its signing keys,
and coupling the two services — all downside, no upside, for an internal worker.

### When you *would* switch to JWT

If this service ever becomes **directly client-facing** (browsers/mobile call it
without going through Next.js), or gains **multiple independent callers** with
different permissions, then per-request JWTs (with claims for tenant/role) become
the right model. Until then, the shared secret is the professional, minimal,
correct choice.

---

## 9. Where everything is stored & how to inspect it

| Data | Lives where | Survives restart? | How to look |
|---|---|---|---|
| **App logs (all)** | console **and** `/app/logs/app.log` *inside the api container* | console: in Docker; file: ❌ lost on container recreate (no volume mounted) | `docker compose logs -f api` |
| **Error logs (warnings+)** | `/app/logs/error.log` inside the container | ❌ ephemeral | `docker compose exec api cat /app/logs/error.log` |
| **Vector DB (Weaviate)** | `weaviate_data` Docker volume | ✅ survives `down`; wiped only by `down -v` | `curl http://localhost:8080/v1/schema` |
| **Mongo `ai_*` data** | MongoDB **Atlas** (cloud) | ✅ always | Atlas web UI → `ready2go` DB, or the one-liner below |

Logging is configured in [app/logging.py](app/logging.py) and
[app/config.py](app/config.py) (`log_dir`, `log_max_bytes`, `log_backups` — the
files rotate so they can't grow unbounded).

**Inspect the vector DB:**

```bash
curl http://localhost:8080/v1/.well-known/ready                       # alive?
curl http://localhost:8080/v1/schema | python3 -m json.tool           # collections
curl http://localhost:8080/v1/schema/DocChunk/tenants | python3 -m json.tool  # tenants
```

**Inspect the Mongo `ai_*` collections (from inside the container):**

```bash
docker compose exec api python -c "
import app.store.models as m
db = m._db()
print('cache:', db['ai_analysis_cache'].count_documents({}))
print('state:', db['ai_audit_state'].count_documents({}))
print('calls:', db['ai_call_log'].count_documents({}))
"
```

> Want the logs saved on your laptop instead of inside the container? Add a volume
> mount for `logs/` in `docker-compose.yml`. Ask and it can be added.

---

## 10. Configuration reference (.env)

All settings are environment variables loaded from `.env`
(code: [app/config.py](app/config.py)). The important ones:

| Variable | Example | What it does |
|---|---|---|
| `ENV` | `development` | `development` \| `staging` \| `production`. Controls fail-closed auth + JSON logs. |
| `PORT` | `8000` | Port the server listens on. |
| `PYTHON_INTEGRITY_TOKEN` | `change-me-…` | The shared-secret bearer token. Empty + dev = auth off; empty + prod = reject all. |
| `HMAC_SECRET` | *(empty)* | Optional body-signature secret (extra security). Empty = disabled. |
| `OPENAI_API_KEY` | `sk-…` | Required for embeddings + summaries. |
| `OPENAI_EMBED_MODEL` | `text-embedding-3-small` | Embedding model (1536-d vectors). |
| `OPENAI_SUMMARY_MODEL` | `gpt-4o-mini` | Chat model for summaries + LLM judge. |
| `WEAVIATE_URL` | `http://weaviate:8080` | Vector DB location. Empty = vector features off (degraded). |
| `MONGODB_URI` | `mongodb+srv://…` | MongoDB Atlas connection string. |
| `MONGODB_DB` | `ready2go` | Database holding the `ai_*` collections. |
| `MODEL_VERSION` | `integrity-v1` | Bump to invalidate cache + re-embed everything. |
| `WEIGHT_*`, `BAND_*`, `LLM_JUDGE_BAND` | see `.env` | Tune scoring weights and status thresholds without code changes. |

> **Gotcha we hit this session:** a *blank* value like `OPENAI_SUMMARY_MODEL=`
> used to override the default with an empty string → OpenAI returned `400 Bad
> Request` on every summary. The config now treats blank values as "use the
> default," so this can't silently break again. Still, prefer leaving a real value
> or removing the line entirely.

---

## 11. Glossary

| Term | Plain meaning |
|---|---|
| **Embedding / vector** | A list of numbers (here 1536 of them) that represents the *meaning* of a piece of text. Similar meaning → mathematically close vectors. |
| **Chunk** | A small slice of a document's text, sized so the AI can process it. |
| **Centroid** | The average of all a document's chunk vectors — one vector that represents the whole document. |
| **Cosine similarity / distance** | A way to measure how "close" two vectors are. Similarity 1.0 = identical meaning; 0 = unrelated. |
| **Tenant** | A private data partition for one customer in Weaviate (`sub_<ownerUserId>`). Tenants can't see each other. |
| **Multi-tenancy** | Storing many customers in one database while keeping each one isolated. |
| **Cache hit** | We've analyzed these exact file bytes before, so we return the saved answer for free. |
| **Degraded** | A dependency (OpenAI/Weaviate) was unreachable; we returned a best-effort result but didn't save it. |
| **Posture** | The vault's overall health label: `Resilient` / `Steady` / `At Risk`. |
| **Idempotency key** | An id (`attachmentId`) that lets us re-run safely without creating duplicates. |
| **Pipeline** | The ordered series of steps that turn an uploaded file into a score + summary. |

---

### Related docs

- [README.md](README.md) — quick project overview.
- [DOCKER.md](DOCKER.md) — how to build, run, and operate the service in Docker.
