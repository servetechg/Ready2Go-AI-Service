# Smart Document Service — Simple Progress Checklist

> **What this is:** an easy-to-read progress tracker for the new service that
> reads uploaded continuity documents, checks how well each one fits its plan,
> and writes a short, always-up-to-date summary.
>
> **Why we're building it:** the current system is slow, costs too much, and the
> summary keeps growing longer with every file. The new service fixes all three.
>
> **How to read this:** each big step has a goal and a few small tasks. Tick the
> boxes as they get done.
>
> **Boxes:** `[ ]` = not started · `[~]` = working on it · `[x]` = done · `[!]` = stuck
> **Last updated:** 2026-06-03

---

## The big picture (at a glance)

| # | Big step | Status | Done |
|---|---|---|---|
| 0 | Get ready (decisions + accounts) | [~] In progress | 4 / 6 |
| 1 | Build the basic service | [~] In progress | 5 / 6 |
| 2 | Read the documents | [~] In progress | 1 / 6 |
| 3 | Give documents a "memory" | [ ] Not started | 0 / 6 |
| 4 | Score each document | [ ] Not started | 0 / 7 |
| 5 | Avoid repeat work (save money) | [ ] Not started | 0 / 5 |
| 6 | Write a one-line note per document | [ ] Not started | 0 / 4 |
| 7 | Write the short overall summary | [ ] Not started | 0 / 6 |
| 8 | Connect it to the main app | [ ] Not started | 0 / 6 |
| 9 | Go live safely | [ ] Not started | 0 / 6 |

**👉 Next thing to do:** Step 2 — finish reading the documents: implement text
extraction (`app/ingest/extract.py`) and chunking (`app/ingest/chunk.py`). See the
**Build bucket** section at the bottom for the full dependency-ordered task list.

---

## Step 0 — Get ready
**Goal:** decide the few open questions and open the accounts we need.

- [~] Agree on a few open choices (how documents are grouped, how often the summary refreshes, etc.)
- [x] Open an account for the "document memory" tool — **self-hosted, open-source Weaviate** (not WCD managed); see `docker-compose.yml`
- [x] Set up a small database to remember results so we don't repeat work — **MongoDB** (Python's own `ai_*` collections inside the app's `ready2go` DB; `docker-compose.yml`). No separate DB, no Postgres.
- [ ] Pick where the new service will live online (the hosting provider) — still open
- [~] Create the secret passwords/keys the service needs to stay private (`PYTHON_INTEGRITY_TOKEN`, `HMAC_SECRET` — placeholders in `.env.example`)
- [x] Confirm we can reuse the existing AI key — **reusing the existing `OPENAI_API_KEY`**

---

## Step 1 — Build the basic service
**Goal:** a simple, secure service that's online and responds when asked.
**Done when:** it answers a basic "are you alive?" check and blocks anyone without the right key.

- [x] Create the starter project (`app/`, `pyproject.toml`, `uv` env)
- [x] Set up the basic "front door" that receives requests (`app/main.py` + `app/api/*` routers; `/v1` routes return 501 against a stable contract)
- [x] Load all the settings and secret keys safely (`app/config.py`)
- [x] Add security so only the main app can talk to it (`app/security.py` bearer auth on `/v1`)
- [x] Add a simple health check ("is everything running?") (`app/api/health.py`)
- [ ] Put it online and confirm the main app can reach it (deploy to PaaS — pending)

---

## Step 2 — Read the documents
**Goal:** open each uploaded file (PDF, Word, Excel, CSV) and pull out the text — the *whole* file, not just the first part.
**Done when:** every file type is read correctly, including blank or scan-only files.

- [x] Download the uploaded file from storage (`app/ingest/fetch.py` — httpx fetch + sha256 + validation)
- [ ] Read text from PDFs
- [ ] Read text from Word, Excel, and CSV files
- [ ] Detect when a file is empty or just a scan (no real text)
- [ ] Break long documents into smaller, sensible pieces
- [ ] Stop cutting off long documents early (read all of it)

---

## Step 3 — Give documents a "memory"
**Goal:** store each document in a smart, searchable form so we can compare it to others quickly — even with thousands of files.
**Done when:** documents can be saved and found again, and each customer's files stay separate.

- [ ] Connect to the document-memory tool
- [ ] Set up the storage so each customer's files are kept apart
- [ ] Turn document text into a form the computer can compare
- [ ] Save each document (and replace it cleanly if re-uploaded)
- [ ] Be able to search documents by meaning *and* by keyword
- [ ] Add reference examples of each plan type to compare against

---

## Step 4 — Score each document *(the heart of it)*
**Goal:** give each document a fair, explainable score for how well it fits its plan — instead of a quick guess.
**Done when:** the score is reliable and we can explain *why* a document got it.

- [ ] Check if the document's **content** matches the plan
- [ ] Check if the **file name** matches the plan
- [ ] Check if it's filed under the **right category**
- [ ] Check the **quality** of the text we could read
- [ ] Check for **duplicates** or odd files in the same plan
- [ ] Combine these into one clear score and label (In Sync / Reviewing / Deviation Found)
- [ ] Use a quick AI double-check only for borderline cases (to save money)

---

## Step 5 — Avoid repeat work *(save money)*
**Goal:** never re-process the same file twice. This is the biggest cost saver.
**Done when:** re-uploading the exact same file returns the saved result instantly with no AI cost.

- [ ] Remember each result so identical files reuse it
- [ ] Skip all the work when a file hasn't changed
- [ ] Re-check a file only when our method is improved
- [ ] Keep a record of every AI request (for cost tracking and troubleshooting)
- [ ] Confirm identical re-uploads cost nothing extra

---

## Step 6 — Write a one-line note per document
**Goal:** a short, plain-English sentence describing each document.
**Done when:** every document gets a tidy one-liner that stays the same on re-upload.

- [ ] Create a short summary for each document
- [ ] Save it so it doesn't get re-written needlessly
- [ ] Send it back to the main app to show on screen
- [ ] Have a sensible backup line if the AI is unavailable

---

## Step 7 — Write the short overall summary *(fixes the long-summary problem)*
**Goal:** one short overview of the whole document library that stays short — whether there are 10 files or 10,000.
**Done when:** the summary stays brief and cheap to refresh no matter how many files exist.

- [ ] Keep a running tally as documents come in (no re-reading everything)
- [ ] Track the few most important issues (low scores, gaps, missing items)
- [ ] Build the summary from the tally + a small sample, not the whole library
- [ ] Handle very large libraries gracefully
- [ ] Work out the overall health rating (Resilient / Steady / At Risk)
- [ ] Only refresh the summary when needed, not on every single upload

---

## Step 8 — Connect it to the main app
**Goal:** plug the new service into the existing upload page, with a safety switch to turn it on/off.
**Done when:** uploading a file shows a real score and a short summary on the existing page.

- [ ] Add the on/off switch and connection settings in the main app
- [ ] Send each new upload to the new service and save its result
- [ ] If the service is slow or down, the upload still works (safe fallback)
- [ ] Use the new service for the overall summary too
- [ ] Keep the old method available as a backup
- [ ] Test uploading every file type and confirm it shows correctly

---

## Step 9 — Go live safely
**Goal:** switch over carefully, score the old files, and keep an eye on things.
**Done when:** the new service runs in production and older files are scored too.

- [ ] Run new and old side-by-side for a while to compare results
- [ ] Confirm the new results look right
- [ ] Turn the new service on for real users
- [ ] Score all the older documents that were uploaded before
- [ ] Set up a simple dashboard (cost, speed, errors)
- [ ] Do a "what if it breaks?" test to confirm uploads still work

---

## Always keep an eye on these

- [ ] **Cost** — make sure we're not paying to redo work
- [ ] **Reliability** — it should recover on its own from hiccups
- [ ] **Speed** — results should feel quick
- [ ] **Privacy & security** — each customer's data stays separate and protected
- [ ] **Don't break the screen** — the existing page must keep working exactly as before

---

## Build bucket — engineering view (dependency order)

> Added 2026-06-03 to track the *technical* tasks behind the steps above and record
> what is actually built. The core MVP is making `POST /v1/integrity/analyze` work
> end-to-end: **fetch → extract → chunk → embed → upsert → score → summarize → return JSON**
> (Next.js writes the four `aiIntegrity*` fields back to Mongo).
>
> **Locked decisions:** reuse the existing `OPENAI_API_KEY`
> (`text-embedding-3-small` + `gpt-4o-mini`); **self-hosted, open-source Weaviate**
> for vectors; **MongoDB only** for Python's own cache/state/logs — its `ai_*`
> collections inside the app's `ready2go` DB (no separate DB, **no Postgres**).
> Per-doc verdict returns **synchronously** (Next.js writes the `aiIntegrity*`
> fields); the vault audit regenerates **on-demand + debounced**. Two stores,
> both in `docker-compose.yml`. See `docs/PHASE_B_VECTOR_IMPLEMENTATION_PLAN.md`
> + `docs/VECTOR_DB_AND_RAG_CONCEPTS.md`.

**Done**
- [x] **M0 skeleton** — FastAPI app, config, bearer auth, structured logging, health; `/v1` routes return 501 against a stable contract (`app/main.py`, `config.py`, `security.py`, `schemas.py`, `api/*`)
- [x] **Data prep Phase A** — Mongo connector + seed/verify/cleanup of the `.gov` corpus into tenant-aware `EmergencyPlan` docs (`app/store/mongo.py`, `scripts/prep/{manifest,seed_mongo,verify,cleanup}.py`)
- [x] **Fetch** — Cloudinary/`.gov` download + sha256 + validation (`app/ingest/fetch.py`)
- [x] **Next.js tenancy alignment** — per-subadmin `continuityplans` / `continuityauditreports` collections + scoped routes live in the Next.js repo (was FILE 1; that plan doc retired)

**Next (in dependency order)**
| # | Task | Files (stubs today) | Step / Milestone |
|---|---|---|---|
| 1 | Text extraction (PDF/DOCX/XLSX/CSV) + empty/scan-only detection, **no length cap** | `app/ingest/extract.py` | Step 2 / M1 |
| 2 | Token-aware chunking (tiktoken `cl100k_base`, overlap, bounded by `MAX_CHUNKS_PER_DOC`) | `app/ingest/chunk.py` | Step 2 / M1 |
| 3 | Weaviate (self-hosted) client + schema + **tenant-scoped** repo + hybrid (BM25+vector) search + category prototypes | `app/vectors/{client,schema,repo}.py` | Step 3 / M2 |
| 4 | OpenAI embeddings + chat client (batched, retried via tenacity, token-metered) | `app/llm/{embeddings,client}.py` | Steps 2–4 / M2–3 |
| 5 | Composite scorer: 5 signals → weighted score → status banding → hard overrides (+ optional gated LLM judge) | `app/scoring/{signals,integrity,thresholds}.py` | Step 4 / M3 |
| 6 | Dedup cache + AI call log (content-hash short-circuit; skip identical re-uploads) | `app/store/{cache,calllog,models}.py` | Step 5 / M4 |
| 7 | Per-doc ≤280-char one-liner + incremental bounded audit + rolling state/posture | `app/summary/{per_doc,audit}.py`, `app/store/aggregate.py` | Steps 6–7 / M5–6 |
| 8 | Flip the 501 handlers to real logic; add `/v1/integrity/rescan` | `app/api/{integrity,audit}.py` | M3–6 |
| 9 | Next.js feature-flag cutover (`INTEGRITY_BACKEND=python`) + backfill + observability | (Next.js repo) | Steps 8–9 / M7–8 |

---

## Phase B (data) — vectorize the seeded corpus *(general + task context retained)*

> **Status:** ON HOLD. The detailed FILE 2 (`DATA_PREP_IMPLEMENTATION_PLAN.md`) has
> been retired now that Phase A is done; **a fresh Phase B "vector" plan will be
> written separately.** This block preserves the general intent + task list so nothing
> is lost in the meantime. Phase B shares the same `fetch`/`extract`/`chunk` modules as
> the live `/v1/integrity/analyze` path (tasks 1–2 above).

**Goal:** turn the seeded `EmergencyPlan` attachments (in Mongo) into vectorized,
ingestion-ready chunk records and load them into Weaviate, one **tenant per subadmin**.

**Guardrails (must hold):**
- **Tenant key** = `"sub_" + ownerUserId` — identical across Mongo seed, this pipeline,
  and the Weaviate tenant name. A mismatch mis-routes data.
- **Fail-closed:** any chunk produced without a `tenantKey` aborts the run.
- **Isolation:** tenant A's staging/Weaviate must contain zero of tenant B's chunks
  (≥2 synthetic subadmins seeded so this is testable).
- `data/staging/` is git-ignored (may contain document text); `contentHash` carried
  from day one so the dedup cache works.

**Tasks:**
- [ ] `app/ingest/extract.py` — per-type text + `ExtractionQuality` `{chars, pages_or_rows, is_scan_only, is_empty}` (= tasks 1 above)
- [ ] `app/ingest/chunk.py` — token-aware chunks `{index, text, token_count}` (= task 2 above)
- [ ] `scripts/prep/prepare.py` — **read-only** Mongo attachments → fetch+extract+chunk → `data/staging/sub_<ownerUserId>.jsonl` (one file per tenant; record shape matches Weaviate `DocChunk`: `attachmentId, planId, category, fileName, contentHash, chunkIndex, text, modelVersion` + `tenantKey`)
- [ ] `scripts/prep/seed_prototypes.py` — global, **non-removable** `CategoryPrototype` reference text per category (coop = CGC + FCD Planning Framework; bcp = NIST 800-34 + CISA ESS checklist; compliance = CMS EP Rule + Appendix Z — sources in `COOP BC PLANS AND DOCS RESOURCES.md`)
- [ ] `scripts/prep/verify.py` (Phase B checks) — tenant present (fail-closed), isolation across tenants, extraction-quality flags surfaced, `contentHash` stability
- [ ] Weaviate upsert — load staged chunks, tenant = `"sub_" + ownerUserId`; re-upload replaces an attachment's chunks via tenant-scoped filtered delete
- [ ] Cleanup extension — drop seed Weaviate tenants + `data/staging/<seed tenant>.jsonl` (CategoryPrototype is permanent infra, never wiped)
