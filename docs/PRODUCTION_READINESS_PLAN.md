# Production-Readiness Plan — Ready2Go AI Service

> **Purpose:** a complete, evidence-based audit of the current codebase and a robust,
> phased plan to make the integrity service production-grade. Covers correctness bugs,
> a proper logging/observability mechanism, testing, resilience, security, and ops.
>
> **Audience:** the engineering team executing the hardening pass.
>
> **Status of the codebase today:** the functional modules (M1–M7) are *written and
> import cleanly*, but the service is **not yet runnable end-to-end** — the vector
> layer has runtime-fatal API bugs, dependencies are never bootstrapped at startup,
> error handling is incomplete, and there is no real logging or test coverage beyond
> the ingestion module.
>
> **How findings were verified:** each item below was confirmed by running the code,
> `ruff`, `mypy`, and direct API probes against the installed `weaviate-client 4.21.2`.
> Evidence is cited inline. The exact Weaviate v4 fixes in §2.1 were cross-checked
> against `docs/weaviate-implementation-context.md` and re-verified against our installed
> client version (the context file targets server 1.36 / a newer client, so symbols were
> confirmed locally rather than assumed).

---

## 0. Executive summary

| Area | State today | Target |
|---|---|---|
| **Correctness** | Vector layer crashes at runtime (wrong Weaviate v4 API); deps never bootstrapped | All pipeline steps run end-to-end against real Weaviate + Mongo |
| **Logging** | stdout only; no files; ~12 log lines; most pipeline steps silent | Rotating file logs + per-step structured events + request correlation IDs |
| **Observability** | No way to watch a document move through the pipeline | Trace every step; `ai_call_log` + metrics; live readiness probes |
| **Testing** | Only M1 tested (13 tests); 20 mypy errors; no integration/coverage/CI | M2–M7 unit + an end-to-end test; mypy clean; coverage report; CI gate |
| **Resilience** | No timeouts; OpenAI/Weaviate failure → 500 | Bounded timeouts, retries, graceful `Reviewing` fallback, circuit breaker |
| **Security** | Auth **fails open** when token unset | Fail-closed in production; secret validation at startup |

**Headline risk:** "17/17 tests pass" is misleading. Tests cover only ingestion (M1).
The vector store, scoring, summaries, and the whole `/v1/integrity/analyze` pipeline
have **zero tests**, which is why the runtime-fatal Weaviate bugs went undetected.

---

## 1. Issue register (severity-ranked)

> **P0 = blocks the service from running. P1 = required before production. P2 = important hardening. P3 = nice-to-have.**

| # | Sev | Area | Issue | Evidence |
|---|-----|------|-------|----------|
| 1 | **P0** | Correctness | `app/vectors/schema.py` uses `wvc.CollectionConfig`, which does not exist in weaviate-client 4.21 | `mypy`: *"Module has no attribute CollectionConfig"*; runtime `AttributeError` |
| 2 | **P0** | Correctness | `app/vectors/repo.py` uses `wvq.DataObject` (it lives in `weaviate.classes.data`, not `.query`) | probe: `query.DataObject: False`, `data.DataObject: True` |
| 3 | **P0** | Startup | `ensure_collections()` and `ensure_indexes()` are **never called** → Weaviate collections + Mongo indexes never created | grep: no callers of either function |
| 4 | **P1** | Resilience | `/analyze` only catches `FetchError`; an OpenAI/Weaviate/Mongo failure → unhandled **500** (plan promised a graceful `Reviewing` fallback) | `app/api/integrity.py` has only one try/except (fetch) |
| 5 | **P1** | Resilience | `request_timeout_s` setting is **dead** — no timeout/circuit-breaker on the pipeline | grep: no usage outside `config.py` |
| 6 | **P1** | Security | Auth **fails open**: `_bearer_ok` returns `True` when the token is empty → a missing env var silently disables auth | `app/security.py:21-23` |
| 7 | **P1** | Logging | No file logging (stdout only), no rotation, no `logs/` dir; most pipeline steps emit nothing; no correlation IDs | `app/logging.py` uses `PrintLoggerFactory`→stdout; only 12 log calls total |
| 8 | **P1** | Testing | Only M1 has tests; M2–M7 + the `/analyze` pipeline untested; **20 mypy errors**; no coverage; results not saved; no CI | `mypy app/` → 20 errors in 6 files |
| 9 | **P2** | Observability | `/readyz` reports config *presence*, not live connectivity to Weaviate/Mongo/OpenAI | `app/api/health.py` checks `bool(setting)` only |
| 10 | **P2** | Correctness | `/v1/integrity/rescan` is a no-op stub (returns a message, re-analyzes nothing) | `app/api/integrity.py` rescan handler |
| 11 | **P2** | Lifecycle | pymongo + Weaviate clients are never closed on shutdown; global client not concurrency-guarded | `store/models.py`, `vectors/client.py` |
| 12 | **P2** | Types | 20 mypy errors — some real (missing annotations), some alias false-positives in response construction | `mypy app/` |
| 13 | **P3** | Config | `parser_backend` is read via `getattr` in `extract.py` but not declared in `Settings` (can't be set by env) | `app/ingest/extract.py:get_parser` |
| 14 | **P3** | Ops | No CI/CD, no pre-commit hooks, no metrics endpoint, no rate limiting | repo has no `.github/`, no `.pre-commit-config.yaml` |

---

## 2. Correctness fixes (P0 — service cannot run without these)

### 2.1 Rewrite the Weaviate layer against the real v4 API
**Files:** `app/vectors/schema.py`, `app/vectors/client.py`, `app/vectors/repo.py`

> **API confirmed against the installed `weaviate-client 4.21.2`** (and cross-checked
> with `docs/weaviate-implementation-context.md`). Use these exact calls:

- **Collections** must be created with `client.collections.create(...)` directly — there
  is **no `CollectionConfig` object** to pass into `create_from_config()`. Delete the
  `doc_chunk_collection_config()` / `category_prototype_collection_config()` builders and
  inline the create call. Because we **bring our own OpenAI vectors**, use
  `Configure.Vectors.self_provided()` (verified present in 4.21.2):
  ```python
  import weaviate.classes.config as wvc
  client.collections.create(
      name="DocChunk",
      properties=[wvc.Property(name="attachmentId", data_type=wvc.DataType.TEXT), ...],
      vector_config=wvc.Configure.Vectors.self_provided(),         # BYO vectors
      multi_tenancy_config=wvc.Configure.multi_tenancy(enabled=True),
      vector_index_config=wvc.Configure.VectorIndex.dynamic(),     # see 2.1.1
  )
  ```
- **Inserts** must use `weaviate.classes.data.DataObject` (import as `wvd`), **not**
  `wvq.DataObject` (the query module has no `DataObject`).
- **Verify every other v4 call** against the installed client with a smoke test (below):
  `near_vector`, `hybrid` (single unnamed vector → no `target_vector` arg),
  `fetch_objects(include_vector=True)` (vector returned under the `"default"` key),
  `delete_many(where=…)`, `with_tenant(...)`, `collections.list_all(simple=True)`.
- **Acceptance:** a new integration test (§5.3) spins up the docker-compose Weaviate,
  calls `ensure_collections()`, upserts a doc, fetches it back, and asserts tenant
  isolation. This test is what would have caught bugs #1 and #2.

#### 2.1.1 Choose the vector index deliberately (architecture refinement)
Per `docs/weaviate-implementation-context.md` §5/§14, the index type should match corpus
size. Our corpora are **small and per-tenant** (dozens–low-hundreds of chunks per
subadmin), so the HNSW default is overkill:
- **`dynamic`** (recommended) — starts `flat` (exact, zero index-build cost) and
  auto-upgrades to HNSW once a tenant grows past the threshold. Best of both.
- **`flat`** — fine if we're confident tenants stay small.
- **`hnsw`** — only if a single tenant will hold many thousands of chunks.
- Keep the default **cosine** distance — it matches `text-embedding-3-small` (normalised
  vectors) and the cosine math in `scoring/signals.py`.
- **No vectorizer module / inference container is needed** because vectors are
  self-provided. Our [docker-compose.yml](docker-compose.yml) already sets
  `DEFAULT_VECTORIZER_MODULE: none` and `ENABLE_MODULES: ""` — that is **correct**; leave it.

#### 2.1.2 Pinned server version
The context file documents Weaviate **1.36.x**; our compose pins **1.27.0**. Every feature
we use (multi-tenancy, hybrid/BM25, bring-your-own vectors, `dynamic` index) predates 1.27,
so 1.27 is safe. Optionally bump to a current `1.36.x` for fixes — but **re-run the §5.3
integration test after any bump**, since client/server API details drift between minors.

### 2.2 Bootstrap dependencies at startup
**File:** `app/main.py` (lifespan)

- Call `vectors.client.ensure_collections()` and `store.models.ensure_indexes()` inside the FastAPI `lifespan` startup, wrapped so a transient failure logs loudly but doesn't crash boot (with a readiness probe reflecting the real state).
- **Acceptance:** on a fresh Weaviate + Mongo, the first `/analyze` succeeds without manual setup.

### 2.3 Make `/rescan` actually work
**File:** `app/api/integrity.py`

- Implement the loop: for each `attachmentId`, look up its cached record (or the original payload), re-run `analyze` honoring the cache unless `force=True`. For v1 a bounded synchronous loop is acceptable; document the async-queue upgrade path.

---

## 3. Proper logging & observability mechanism (the core ask)

This is the centerpiece. Today: logs go to **stdout only**, there are **no log files**, and
the pipeline is **silent** between "cache_hit" and "analyzed" — you cannot watch a document
being ingested. The target design:

### 3.1 Two-sink structured logging
**File:** rewrite `app/logging.py`

- Keep **structlog** (already in use) but route output through stdlib logging **handlers** so we get both console *and* files:
  - **Console** — pretty in dev, JSON in prod (unchanged behaviour for `docker logs`).
  - **`logs/app.log`** — `RotatingFileHandler` (e.g. 10 MB × 5 backups), **all** levels, JSON lines.
  - **`logs/error.log`** — `RotatingFileHandler`, `WARNING`+ only, so failures are isolated for fast triage.
- `logs/` is created at startup and **git-ignored** (add to `.gitignore`).
- Log level + file paths + max-size/backups come from `Settings` (`log_dir`, `log_max_bytes`, `log_backups`).

### 3.2 Request correlation IDs (trace one document end-to-end)
**New file:** `app/middleware.py`

- Add an ASGI middleware that, on every request, generates a `request_id` (uuid4) and binds it (+ `tenantKey`, `attachmentId` once known) into `structlog.contextvars`. Every log line in that request then carries the same `request_id`, so:
  ```
  grep '"request_id":"<id>"' logs/app.log
  ```
  returns the **complete journey** of one upload across all modules.
- Echo the id back as an `X-Request-ID` response header so Next.js can correlate.

### 3.3 Per-step pipeline logging (watch the background events)
**File:** `app/api/integrity.py` + each module

Emit one structured `info` event at **every** pipeline step, so the log reads like a trace:

| Step | Event name | Key fields |
|---|---|---|
| Fetch | `pipeline.fetched` | `bytes`, `content_hash`, `mime` |
| Cache | `pipeline.cache_hit` / `pipeline.cache_miss` | `content_hash` |
| Extract | `pipeline.extracted` | `chars`, `pages_or_rows`, `is_scan_only`, `is_empty` |
| Chunk | `pipeline.chunked` | `chunk_count`, `total_tokens` |
| Embed | `pipeline.embedded` | `vector_count`, `tokens`, `latency_ms` |
| Upsert | `pipeline.vectors_upserted` | `count`, `tenant` |
| Score | `pipeline.scored` | `score`, `status`, `components` |
| Judge | `pipeline.llm_judge` | `before`, `after` (only if fired) |
| Summary | `pipeline.summarized` | `length`, `map_reduce: bool` |
| Persist | `pipeline.persisted` | `cache: true`, `audit_state_updated: true` |
| Done | `pipeline.completed` | `total_latency_ms`, `cache_hit` |

Result: tailing `logs/app.log` shows the live progress of every ingestion, and you can see
**which file/function handled each event** (the event name maps 1:1 to the module).

### 3.4 Keep + surface the AI call log
- `ai_call_log` (Mongo) already records every OpenAI call (tokens/latency/success) — keep it, and add a read-only `GET /v1/diagnostics/calls?attachmentId=…` (auth-gated) so cost/errors for a document are queryable without DB access.

### 3.5 Metrics (P2)
- Expose Prometheus-style counters at `GET /metrics`: request count, cache-hit ratio, token spend, error rate, p50/p95 latency. (Use `prometheus-client` or a lightweight in-process counter for v1.)

---

## 4. Resilience & error handling (P1)

**File:** `app/api/integrity.py`, `app/config.py`

- **Wrap the whole pipeline** (steps 4–11) in a guarded block: on any unexpected failure, log it with the `request_id` and return the graceful `AnalyzeResponse(status="Reviewing", score=50, summary="Analysis unavailable — will retry.")` the plan promised, instead of a 500. The UI already tolerates a pending badge.
- **Enforce `request_timeout_s`**: wrap the pipeline in `asyncio.wait_for(...)`; on timeout return the `Reviewing` fallback and log `pipeline.timeout`. This is the circuit-breaker the architecture (§8.2) specified.
- **Per-dependency degradation:** if Weaviate is unreachable, skip the vector-derived signals (content/category/duplication), score on the remaining signals + quality, and flag `degraded: true` in `details`. Never let one dependency take the whole request down.
- **Idempotency on retry:** confirm `upsert_chunks` (delete-then-insert) is safe to re-run — add a test.

---

## 5. Testing & QA (P1)

### 5.1 Fix the 20 mypy errors
Run `uv run mypy app/` as a gate. Real fixes: add parameter annotations in `summary/per_doc.py`, return annotation in `integrity.py` helpers, fix the `StoredChunk.vector` typing in `repo.py`, and resolve the `AnalyzeResponse` alias construction (use field names or `model_construct`/`by_alias` consistently).

### 5.2 Unit tests for M2–M6 (mocked — no live services)
- **Embeddings/LLM** (`respx` to mock OpenAI): batching, retry path, call-log row written, fallback returned on failure.
- **Scoring** (pure functions — easiest, highest value): each signal in `[0,1]`; composite clamps to `[0,100]`; bands map correctly; **hard overrides fire** (scan-only caps at Reviewing/45; mis-filed → Deviation Found); judge-band detection.
- **Store** (mongomock or a test Mongo): cache get/put round-trip; **cache hit ⇒ 0 OpenAI calls**; `ai_audit_state` O(1) increments; notable-list cap.
- **Summaries**: one-liner ≤280; map-reduce path for large docs; deterministic fallback when no API key; `derive_posture` truth table.

### 5.3 Integration test (the one that would have caught the P0 bugs)
- Against docker-compose Weaviate: `ensure_collections()` → upsert tenant A + tenant B → assert **tenant A query returns zero tenant-B objects** → re-upsert replaces (no duplicates) → `get_all_chunks` returns the complete set.

### 5.4 End-to-end test of `/v1/integrity/analyze`
- Mock OpenAI (`respx`) + real test Weaviate/Mongo; post a real `AnalyzeRequest`; assert contract (status enum, score 0–100 int, summary ≤280); second identical call asserts `cacheHit: true` with **zero** new OpenAI calls.

### 5.5 Save results + CI
- `addopts` already has `-q`; add `--junitxml=reports/junit.xml --cov=app --cov-report=html:reports/coverage`. Commit `reports/` to `.gitignore`.
- Add a **GitHub Actions** workflow: `uv sync` → `ruff check` → `mypy` → `pytest` (with a Weaviate + Mongo service container). Gate merges on green.
- Make the existing `test_v1_contract_stub_returns_501_when_auth_disabled` **hermetic** (clear `PYTHON_INTEGRITY_TOKEN` in the fixture) so it stops depending on local `.env`.

---

## 6. Security (P1)

**File:** `app/security.py`, `app/config.py`, `app/main.py`

- **Fail closed in production:** if `env == production` and `python_integrity_token` is empty, refuse to start (raise at startup) rather than silently disabling auth. Keep the dev convenience (auth off) only for non-prod.
- **Validate required secrets at startup:** in prod, assert presence of `OPENAI_API_KEY`, `WEAVIATE_URL`, `MONGODB_URI`, `PYTHON_INTEGRITY_TOKEN`; log a clear fatal if missing.
- **Rate limiting (P2):** add a simple per-token limiter so a runaway Next.js loop can't exhaust the OpenAI budget.
- **Never log secrets or full document text** — the per-step logger logs *counts and hashes*, not content (verify in review).

---

## 7. Configuration, lifecycle & ops (P2)

- **Declare `parser_backend`** in `Settings` (default `"basic"`) so the LiteParse swap is env-driven.
- **Graceful shutdown:** close the pymongo and Weaviate clients in the lifespan teardown.
- **Live readiness:** `/readyz` should actually ping Weaviate (`is_ready()`), Mongo (`ping`), and optionally OpenAI (cheap models list) — returning per-dependency status, so orchestrators don't route traffic to a half-broken instance.
- **`.gitignore`:** add `logs/`, `reports/`, `data/staging/`.
- **Pre-commit hooks (P3):** `ruff`, `ruff format`, `mypy` on staged files.
- **Pin the Weaviate image** (already pinned to `1.27.0` in compose — keep it) and document the supported `weaviate-client` range.

---

## 8. Phased remediation roadmap

> Do them in order — each phase leaves the service in a better, shippable state.

### Phase 0 — Make it run (P0)  *[blocks everything]*
1. Rewrite the Weaviate layer against the real v4 API (§2.1).
2. Bootstrap `ensure_collections()` + `ensure_indexes()` at startup (§2.2).
3. Add the integration test (§5.3) so this can never silently regress.
4. **Exit criteria:** a fresh stack ingests one real document end-to-end without manual setup.

### Phase 1 — Make it observable & safe (P1)
5. New logging mechanism: file sinks + rotation + correlation IDs + per-step events (§3.1–3.3).
6. Pipeline-wide error handling, timeout/circuit-breaker, graceful `Reviewing` fallback (§4).
7. Auth fail-closed + startup secret validation (§6).
8. Fix 20 mypy errors; add M2–M6 unit tests + the end-to-end `/analyze` test; CI gate (§5).
9. **Exit criteria:** you can `grep` one `request_id` and see the full ingestion trace; a dependency outage degrades gracefully instead of 500; CI is green.

### Phase 2 — Make it operable (P2)
10. Live `/readyz` probes; `/metrics`; `/v1/diagnostics/calls`; graceful client shutdown; rate limiting; implement `/rescan` (§3.4, §3.5, §4, §6, §7).
11. **Exit criteria:** dashboards show cache-hit ratio, token spend, error rate, latency; on-call can diagnose from logs + metrics alone.

### Phase 3 — Polish (P3)
12. Pre-commit hooks; `parser_backend` config; docs for runbooks/alerts.

---

## 9. Definition of "production-grade" (acceptance checklist)

- [ ] Fresh `docker compose up` → upload each file type via the real flow → real score + one-liner returned; **no manual DB/collection setup**.
- [ ] `logs/app.log` + `logs/error.log` exist, rotate, and a single `request_id` traces a document across every step.
- [ ] Every pipeline step emits a structured event (fetch→…→completed).
- [ ] OpenAI **or** Weaviate **or** Mongo outage → graceful `Reviewing` fallback + logged error, **never a 500**.
- [ ] Re-upload identical bytes ⇒ `cacheHit: true`, **zero** OpenAI calls (asserted in CI).
- [ ] Tenant A can never read Tenant B vectors (asserted in CI).
- [ ] Auth fails **closed** in production; startup refuses to boot without required secrets.
- [ ] `ruff` clean · `mypy` clean · `pytest` green with coverage report · CI gates merges.
- [ ] `/readyz` reflects **live** dependency health; `/metrics` exposes cost/latency/error counters.

---

## 10. Appendix — verification commands used in this audit

```bash
# Weaviate API mismatch (P0 #1, #2)
uv run python -c "import weaviate.classes.config as wvc; print(hasattr(wvc,'CollectionConfig'))"   # → False
uv run python -c "import weaviate.classes.query as wvq;  print(hasattr(wvq,'DataObject'))"          # → False
uv run python -c "import weaviate.classes.data  as wvd;  print(hasattr(wvd,'DataObject'))"          # → True

# Orphaned bootstrap (P0 #3)
grep -rn "ensure_collections\|ensure_indexes" app/ | grep -v "def ensure"   # → no callers

# Dead timeout (P1 #5)
grep -rn "request_timeout_s" app/ | grep -v config.py                       # → empty

# Type + correctness gate (P1 #8)
uv run mypy app/                                                             # → 20 errors in 6 files
uv run ruff check app/ tests/                                               # → clean (style only)
```

*End of `docs/PRODUCTION_READINESS_PLAN.md`.*
