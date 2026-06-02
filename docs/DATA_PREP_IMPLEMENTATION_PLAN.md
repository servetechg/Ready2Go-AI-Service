# Data Preparation — Implementation Plan (FILE 2)

> **Purpose:** stand up the **MongoDB connection** and **build a removable test corpus**
> of real continuity documents directly in Mongo (the public `.gov` sources), aligned with
> the new per-subadmin tenant schema from FILE 1 — so the future scoring/audit
> functionality can be validated against realistic data. Turning that data into
> vectorized, ingestion-ready records for Weaviate is a **separate, later** phase.
>
> **Status:** plan. Companion: `docs/NEXTJS_TENANCY_ALIGNMENT_PLAN.md` (FILE 1), whose
> Mongo schema changes the user has begun applying in the Next.js service.
>
> **Scaffold baseline:** the service is at M0 — `app/{config,schemas,security,main}.py`
> are implemented; `app/ingest/*`, `app/vectors/*`, `app/store/*` are TODO stubs.
> Stack/deps: `pyproject.toml`. Local stores: `docker-compose.yml`.

---

## Phase split — what is active vs on hold

| Phase | Scope | Status |
|-------|-------|--------|
| **A — Connection + build data (DO NOW)** | Mongo connector, deps/config, seed manifest, download + (optional) Cloudinary, **write test `EmergencyPlan` docs into Mongo** aligned to FILE 1, cleanup tooling | **Active** |
| **B — Vectorize / ingest (ON HOLD)** | Text extraction, token chunking, JSONL staging (`prepare.py`), category prototypes, isolation verification of staged chunks, Weaviate upsert | **Deferred — done separately later** |

**Phase A deliverable:** a set of realistic, tenant-owned, **removable** `ContinuityPlan`
documents in the dev/staging MongoDB `continuityplans` collection (with `aiIntegrity*` left
unset, i.e. "pending analysis"), ready to exercise the new tenant-aware vault in the UI.

Section tags below: **[A]** = build now, **[B]** = on hold. Phase B sections are kept for
continuity but are out of scope for the current task.

---

## 0. TL;DR — what gets built

```
scripts/
  prep/
    manifest.py        # [A] the .gov seed sources (structured, from RESOURCES.md)
    seed_mongo.py      # [A] DEV-ONLY: download gov PDFs -> (opt Cloudinary) -> write test EmergencyPlans
    cleanup.py         # [A] one command: remove ALL seed data (Mongo [+ Cloudinary])
    prepare.py         # [B] READ-ONLY: Mongo attachments -> fetch+extract+chunk -> JSONL staging
    seed_prototypes.py # [B] build global CategoryPrototype reference text
    verify.py          # [A/B] seed correctness now; staged-chunk isolation later
app/
  store/mongo.py       # [A] NEW: pymongo connector (prep/seed only, fenced from request path)
  ingest/fetch.py      # [A] implement: download + sha256 + validation (also reused live later)
  ingest/extract.py    # [B] pdf/docx/xlsx/csv -> text + quality
  ingest/chunk.py      # [B] tiktoken token-aware chunking
data/
  staging/<tenantKey>.jsonl   # [B] ingestion-ready records, grouped by tenant
```

**Phase A deliverable:** removable, tenant-owned `EmergencyPlan` docs **in MongoDB**
(matching production shape; `aiIntegrity*` unset). **Phase B deliverable (later):**
`data/staging/<tenantKey>.jsonl` chunks ready for the Weaviate upsert.

---

## 1. Goal & guardrails (read this first)

| Guardrail | Rule |
|-----------|------|
| **Read real data read-only** | The prep pipeline opens Mongo with a read-only connection. It **never** writes to real subadmin plans. |
| **Seed writes are dev-only & removable** | `seed_mongo.py` writes test `EmergencyPlan` docs, but every one is tagged `seedSource` and owned by a **reserved synthetic subadmin id**, so cleanup is a single delete. Point it at a **dev/staging** Mongo, never production. |
| **Tenant on every record (fail-closed)** | Any record/chunk produced without a `tenantKey` is a hard error — the pipeline aborts rather than emit unscoped data. |
| **Two tenants minimum** | Seed under **≥2** synthetic subadmins so isolation is actually testable (A must never see B). |
| **No request-path coupling** | `app/store/mongo.py` is imported only by `scripts/prep/*`, never by `app/api/*`. The locked "Python never touches Mongo on the request path" rule stays intact. |
| **Secrets via env only** | `MONGODB_URI`, `CLOUDINARY_*`, `OPENAI_API_KEY` come from `.env`; nothing hard-coded. |

**Tenant key:** `tenantKey = "sub_" + ownerUserId`, identical to FILE 1 and the Weaviate
tenant name. Synthetic test subadmins use reserved, obviously-fake ObjectIds (e.g.
`0000000000000000000000a1`, `…a2`) so they can never collide with real users and are
trivial to target for cleanup.

---

## 2. Dependencies & configuration  **[A]**

### 2.1 `pyproject.toml` — add
```toml
"pymongo>=4.9",          # sync driver for the offline prep/seed scripts
"cloudinary>=1.41",      # upload seed PDFs so fileUrl matches production shape (optional)
```
(`httpx`, `pdfplumber`, `pypdf`, `python-docx`, `openpyxl`, `tiktoken`, `openai`,
`weaviate-client` are already present.)

### 2.2 `app/config.py` — add settings
```python
# ---- MongoDB (read-only prep/seed access; NOT the request path) ----
mongodb_uri: str = ""                 # e.g. mongodb+srv://...  (dev/staging)
mongodb_db: str = "ready2go"          # PROJECT_CONTEXT §4 default DB name
# ---- Cloudinary (seed upload only) ----
cloudinary_cloud_name: str = ""
cloudinary_api_key: str = ""
cloudinary_api_secret: str = ""
cloudinary_folder: str = "earthquick/emergency-plans"
```
Add the same keys to `.env.example` (values blank). Reuse the existing `MODEL_VERSION`,
`MAX_CHUNKS_PER_DOC`, `OPENAI_EMBED_MODEL` already defined there.

### 2.3 `app/store/mongo.py` — connector (NEW)
A small pymongo connector imported **only** by `scripts/prep/*` (never `app/api/*`), so the
live request path stays Mongo-free (ARCHITECTURE §1.3). It exposes a lazy, process-wide
client plus `plans()` / `audits()` helpers for the **tenant-aware** `continuityplans` /
`continuityauditreports` collections (the new per-subadmin collections the Next.js UI reads
from; the old `emergencyplans` / `continuityaudits` are deprecated).

```python
"""MongoDB access for offline prep/seed scripts ONLY.
Never imported by app/api/* — keeps the request path Mongo-free (ARCHITECTURE §1.3)."""
from functools import lru_cache
from pymongo import MongoClient
from app.config import get_settings

@lru_cache(maxsize=1)
def get_client() -> MongoClient:
    s = get_settings()
    if not s.mongodb_uri:
        raise RuntimeError("MONGODB_URI not set — prep/seed scripts require it")
    return MongoClient(s.mongodb_uri, appname="r2g-ai-prep")

def get_db():            return get_client()[get_settings().mongodb_db]
def plans():            return get_db()["emergencyplans"]
def audits():           return get_db()["continuityaudits"]
```
> **Permissions are enforced by the Mongo user, not the code.** Use a **read-only** user
> for the (Phase B) read-only `prepare.py`; the (Phase A) `seed_mongo.py` writes, so point
> it at a **dev/staging** DB with a dev-scoped write credential — **never production**.

---

## 3. Ingest modules

Only the downloader is needed for Phase A (to fetch bytes for size/hash and the optional
Cloudinary upload). Extraction and chunking are **Phase B**.

### 3.1 `app/ingest/fetch.py`  **[A]**
- `async def fetch_bytes(url: str) -> FetchResult` using `httpx.AsyncClient`.
- Set a real `User-Agent` (the `.gov` hosts require one; RESOURCES §3 notes UA).
- Validate: HTTP 200, `Content-Type` is a document type, size ≤ 25 MB (mirror Next.js
  `MAX_BYTES`, PROJECT_CONTEXT §7).
- Compute `content_hash = sha256(bytes).hexdigest()` (the dedup/cache key, ARCHITECTURE §4).
- Return `{ bytes, content_hash, mime, size }`. Raise typed errors for 404 / oversize /
  wrong-type so the manifest runner can skip-and-log.

### 3.2 `app/ingest/extract.py`  **[B — ON HOLD]**
- `def extract(data: bytes, ext: str) -> Extraction` returning
  `{ text: str, quality: ExtractionQuality }`.
- Per type (mirror Next.js coverage, PROJECT_CONTEXT §7): **PDF** → `pdfplumber` (fallback
  `pypdf`), **DOCX** → `python-docx`, **XLSX** → `openpyxl` (sheet → CSV-ish text),
  **CSV** → stdlib `csv`.
- **No length cap** (fixes weakness #1/#3 — the whole point of moving off Next.js's
  8 000-char prefix).
- `ExtractionQuality` = `{ chars: int, pages_or_rows: int, is_scan_only: bool,
  is_empty: bool }`. `is_scan_only` = PDF with pages but ~0 extractable chars;
  `is_empty` = ~0 chars overall. This deterministic signal replaces the
  "score 35–55 conservatively" hack (ARCHITECTURE §6).

### 3.3 `app/ingest/chunk.py`  **[B — ON HOLD]**
- `def chunk(text: str, *, max_chunks: int) -> list[Chunk]` using `tiktoken`
  (`cl100k_base`) for token-aware splitting with overlap (target ~500 tokens, ~50
  overlap — config-tunable).
- Bounded by `MAX_CHUNKS_PER_DOC` (config) to cap worst-case embedding spend
  (ARCHITECTURE §8.1 lever #7). Each `Chunk` = `{ index, text, token_count }`.

> Equivalent to the future plan's LangChain `RecursiveCharacterTextSplitter`; using
> `tiktoken` directly keeps token budgeting exact and avoids an extra heavy dep. (If the
> team prefers LangChain, swap here without touching callers.)

---

## 4. Seed manifest — `scripts/prep/manifest.py`  **[A]**

Encode the RESOURCES doc as structured, fetchable entries. **Only openly-fetchable `.gov`
PDFs** are included; the rest are explicitly skipped with a reason.

```python
@dataclass(frozen=True)
class SeedDoc:
    title: str
    url: str
    category: Literal["coop", "bcp", "compliance"]
    plan_id: str           # slug; groups files into a plan within a test subadmin
    tenant: str            # which synthetic subadmin owns it (to spread across ≥2 tenants)

SEED_DOCS: list[SeedDoc] = [
  # --- COOP (FEMA ONCP doctrine + templates) -> test subadmin A ---
  SeedDoc("FCD — Continuity Program Management Requirements (Aug 2024)",
          "https://www.fema.gov/sites/default/files/documents/fema_oncp_fcd-federal-executive-branch-continuity-program-management-requirements.pdf",
          "coop", "federal-continuity-program-mgmt", "A"),
  SeedDoc("FCD — Continuity Planning Framework",
          "https://www.fema.gov/sites/default/files/documents/fema_federal-continuity-directive-planning-framework.pdf",
          "coop", "continuity-planning-framework", "A"),
  SeedDoc("FCD — Essential Functions Risk Identification & Management",
          "https://www.fema.gov/sites/default/files/documents/fema_oncp-fcd-federal-executive-branch-essential-functions-risk-identification-management.pdf",
          "coop", "essential-functions-risk-mgmt", "A"),
  SeedDoc("Continuity Guidance Circular (Aug 2024)",
          "https://www.fema.gov/sites/default/files/documents/fema_continuity-guidance-circular_082024.pdf",
          "coop", "continuity-guidance-circular", "A"),
  SeedDoc("FEMA Continuity Plan Template (Federal D/A, Oct 2020)",
          "https://www.fema.gov/sites/default/files/2020-10/fema_planning-template-federal-departments-agencies_october-2020_0.pdf",
          "coop", "continuity-plan-template", "A"),
  SeedDoc("FEMA Reconstitution Plan/Annex Template",
          "https://www.fema.gov/sites/default/files/2020-09/fema_reconstitution-plan_template_10-22-19.pdf",
          "coop", "reconstitution-plan-template", "A"),
  SeedDoc("FEMA Continuity Risk Toolkit",
          "https://www.fema.gov/sites/default/files/2020-07/Continuity-Risk-Toolkit_013118.pdf",
          "coop", "continuity-risk-toolkit", "A"),
  SeedDoc("FCD 1 (Jan 2017, legacy)",
          "https://www.fema.gov/sites/default/files/2020-07/January2017FCD1.pdf",
          "coop", "fcd-1-2017", "A"),

  # --- BCP (IT/contingency + sector continuity) -> test subadmin B ---
  SeedDoc("NIST SP 800-34 Rev.1 — Contingency Planning Guide",
          "https://nvlpubs.nist.gov/nistpubs/legacy/sp/nistspecialpublication800-34r1.pdf",
          "bcp", "nist-800-34-contingency", "B"),
  SeedDoc("CISA ESS Continuity Planning Suite — Checklist",
          "https://www.cisa.gov/sites/default/files/publications/emergency-services-sector-continuity-planning-suite-checklist-022018-508.pdf",
          "bcp", "cisa-ess-checklist", "B"),
  SeedDoc("CISA ESS — Continuity Capability Evaluation Form (Nov 2023)",
          "https://www.cisa.gov/sites/default/files/2023-12/ess-continuity-capability-evaluation-form_112023_508.pdf",
          "bcp", "cisa-ess-capability-eval", "B"),
  SeedDoc("CISA ESS — Orders of Succession Worksheet",
          "https://www.cisa.gov/sites/default/files/publications/emergency-services-sector-continuity-planning-suite-worksheet-2-orders-of-succession-022018-508.pdf",
          "bcp", "cisa-ess-succession", "B"),
  SeedDoc("FEMA Non-Federal Continuity Plan Template",
          "https://www.fema.gov/sites/default/files/2020-10/non-federal-continuity-plan-template_083118.pdf",
          "bcp", "non-federal-continuity-template", "B"),

  # --- COMPLIANCE (regulatory) -> test subadmin B ---
  SeedDoc("CMS Emergency Preparedness Rule (2016 Final, govinfo)",
          "https://www.govinfo.gov/content/pkg/FR-2016-09-16/pdf/2016-21404.pdf",
          "compliance", "cms-emergency-preparedness-rule", "B"),
  SeedDoc("CMS SOM Appendix Z — EP Interpretive Guidance",
          "https://www.cms.gov/Regulations-and-Guidance/Guidance/Manuals/downloads/som107ap_z_emergprep.pdf",
          "compliance", "cms-appendix-z", "B"),
]

# Explicitly NOT fetchable — record so stakeholders don't chase them (RESOURCES §Caveats):
SKIPPED = {
  "PPD-40": "classified; no public full text (use FCD-1 / EO 13961 as surrogates)",
  "NFPA 1600/1660": "copyrighted standard; registration/purchase only — not an open PDF",
}
```

> **Document-type spread:** the gov sources are PDF-heavy. To exercise DOCX/XLSX/CSV
> extraction too, optionally add the FEMA Devolution **.docx** template
> (`…/devolution-plan-template_082319.docx`) and a small hand-made XLSX/CSV
> (e.g. an orders-of-succession table) under a test subadmin. Without these, DOCX/XLSX/CSV
> paths go untested by the seed.

---

## 5. Seed writer — `scripts/prep/seed_mongo.py` (DEV-ONLY, removable)  **[A]**

> **Alignment with FILE 1 (must match the schema you are applying in Next.js):** every
> seeded `EmergencyPlan` sets **`ownerUserId`** (required by FILE 1), and uniqueness is now
> per-owner via the compound `(ownerUserId, planId)` index — so two synthetic subadmins can
> hold the same `planId` without collision. The doc also carries an extra `seedSource`
> marker (ignored by the app) for one-command cleanup. `aiIntegrity*` are left **unset**
> (the future pipeline fills them). Run the FILE 1 migration/index changes **before**
> seeding, or the compound unique index build may conflict.
>
> **Owner ids:** resolve from `SEED_OWNER_IDS` (comma-separated **real** subadmin `User._id`
> hex strings) so the data is visible when you log in as that subadmin. If unset, fall back
> to reserved synthetic ids with a loud warning (data won't attach to a loginnable account).
>
> **`fileUrl` source:** default = the direct `.gov` URL (no Cloudinary needed). If
> `SEED_USE_CLOUDINARY=true` and creds are present, upload bytes to Cloudinary first so
> `fileUrl` matches production shape. Support a `--dry-run` flag that previews the docs
> without writing to Mongo.

For each `SeedDoc`:
1. `fetch_bytes(url)` (§3.1). On 404/oversize/wrong-type → **skip + log**, continue.
2. **(Recommended)** upload bytes to Cloudinary `earthquick/emergency-plans` (raw) so the
   stored `fileUrl` is a Cloudinary `secure_url` — identical shape to real uploads, so the
   prep pipeline exercises the true fetch path.
   *Lighter alternative:* store the original `.gov` URL as `fileUrl` (httpx can fetch it
   directly) and skip Cloudinary — fewer creds, but `fileUrl` won't match production shape.
3. **Upsert** an `EmergencyPlan` for `(ownerUserId, planId)` and push the attachment:

```python
SYNTHETIC = {  # reserved, obviously-fake — never collide with real users
  "A": ObjectId("0000000000000000000000a1"),
  "B": ObjectId("0000000000000000000000a2"),
}
SEED_MARK = "gov-coop-bcp-2026"   # the single cleanup key

doc = {
  "ownerUserId": SYNTHETIC[d.tenant],
  "licenseId": None,
  "planId": d.plan_id,
  "label": d.title,
  "overview": f"Seed continuity reference: {d.title}.",
  "category": d.category,
  "steps": [],
  "seedSource": SEED_MARK,                 # <-- removable marker (extra field, ignored by app)
}
attachment = {
  "fileName": filename_from(d),
  "fileUrl": secure_url,                    # Cloudinary (or .gov url in light mode)
  "size": size, "uploadedAt": now,
  "cloudinaryPublicId": public_id, "cloudinaryResourceType": "raw",
  "contentHash": content_hash,             # convenience for prep; harmless extra field
  "seedSource": SEED_MARK,
  # aiIntegrity* deliberately left UNSET — the future pipeline fills them.
}
# upsert plan by (ownerUserId, planId); $push attachment if same fileName not present
```

**Idempotency:** re-running upserts the plan and only pushes an attachment whose
`fileName` isn't already there → safe to run repeatedly.

**Why ≥2 tenants:** subadmin A (COOP doctrine) and subadmin B (BCP + compliance) give a
real isolation test — verify in §7 that A's Weaviate/staging never contains B's chunks.

---

## 6. Prep / normalization pipeline — `scripts/prep/prepare.py`  **[B — ON HOLD]**

> Deferred to the separate vectorization phase. Documented here for continuity.

**Read-only.** Iterate every `EmergencyPlan` attachment (seed + any real data) and emit
ingestion-ready chunk records grouped by tenant.

```python
for plan in db().emergencyplans.find({}, no_cursor_timeout=False):     # read-only
    owner = plan.get("ownerUserId")
    if not owner:
        raise RuntimeError(f"plan {plan['_id']} has no ownerUserId — run FILE 1 migration first")
    tenant_key = f"sub_{owner}"
    for att in plan.get("attachments", []):
        res   = await fetch_bytes(att["fileUrl"])                       # §3.1
        ext   = ext_from(att["fileName"])
        ex    = extract(res.bytes, ext)                                # §3.2
        chunks = chunk(ex.text, max_chunks=settings.max_chunks_per_doc) # §3.3
        for c in chunks:
            record = {
              "tenantKey":    tenant_key,                              # FAIL-CLOSED: must exist
              "attachmentId": str(att["_id"]),
              "planId":       plan["planId"],
              "category":     plan.get("category", "coop"),
              "fileName":     att["fileName"],
              "fileUrl":      att["fileUrl"],
              "contentHash":  res.content_hash,
              "chunkIndex":   c.index,
              "text":         c.text,
              "modelVersion": settings.model_version,                  # "integrity-v1"
              "extractionQuality": ex.quality.__dict__,                # carried for the scorer
            }
            write_jsonl(f"data/staging/{tenant_key}.jsonl", record)
```

Output: `data/staging/sub_<ownerUserId>.jsonl`, **one file per tenant**, one JSON record
per chunk. This is exactly the shape the M2 Weaviate `DocChunk` upsert consumes (property
names match ARCHITECTURE §4.1: `attachmentId, planId, category, fileName, contentHash,
chunkIndex, text, modelVersion`), with `tenantKey` driving the Weaviate **tenant**.

> This pipeline is also the backbone of the live `/v1/integrity/analyze` path — there it
> runs for a single attachment from the webhook instead of looping Mongo. Same
> fetch/extract/chunk modules; same record shape.

---

## 7. Category prototypes — `scripts/prep/seed_prototypes.py` (global, NOT removable)  **[B — ON HOLD]**

The category-fit signal (ARCHITECTURE §6) needs a canonical reference per category. Pick
1–3 authoritative docs each and store their extracted text as `CategoryPrototype`
reference (global, tenant-agnostic — **not** under any subadmin, **not** tagged
`seedSource`).

| Category | Prototype source(s) |
|----------|---------------------|
| `coop` | Continuity Guidance Circular (Aug 2024) + FCD Planning Framework |
| `bcp` | NIST SP 800-34 Rev.1 + CISA ESS Checklist |
| `compliance` | CMS EP Rule (2016 Final) + CMS Appendix Z |

Extract → (optionally summarize to a representative excerpt to bound size) → store as the
`CategoryPrototype` collection text (ARCHITECTURE §4.1), versioned by `modelVersion`. These
persist across cleanups — they are scoring infrastructure, not test data.

---

## 8. Verification — `scripts/prep/verify.py`

### 8.1 Phase A checks (run now, against Mongo)  **[A]**
- **Owner present:** every seeded `EmergencyPlan` has an `ownerUserId` (no orphans).
- **Counts per owner:** print `{ownerUserId: {plans, attachments}}` for each seed subadmin;
  each non-zero, and the two synthetic/real owners hold **disjoint** plans.
- **Per-owner uniqueness honored:** the same `planId` can exist under two owners; re-running
  the seed does **not** duplicate attachments (idempotency holds).
- **Marker present:** every seeded doc + attachment carries `seedSource` (so cleanup is total).
- **`aiIntegrity*` unset:** seeded attachments show as "pending analysis" (not pre-scored).

### 8.2 Phase B checks (later, against staging/Weaviate)  **[B — ON HOLD]**
- **Tenant present (fail-closed):** every staged record has a non-empty `tenantKey`.
- **Isolation:** `data/staging/sub_…a1.jsonl` and `…a2.jsonl` share no `attachmentId` /
  `contentHash`; post-Weaviate, a tenant-A query returns zero tenant-B objects.
- **Extraction quality surfaced:** list any attachment flagged `is_scan_only`/`is_empty`.
- **Hash stability:** re-running `prepare.py` yields identical `contentHash` per attachment.

---

## 9. Cleanup — `scripts/prep/cleanup.py` ("remove the data we added")  **[A]**

When live subadmin data arrives, one command wipes **only** the seed set:

```python
mark = settings.seed_mark   # "gov-coop-bcp-2026"
# 1) [A] Mongo: delete seed plans (and their attachments) by marker
plans().delete_many({"seedSource": mark})
# 2) [A] Cloudinary: destroy uploaded seed assets (only if seeding used Cloudinary)
# 3) [B] Weaviate: drop the seed tenants (only after Phase B ingestion)
# 4) [B] Staging: rm data/staging/<seed tenant>.jsonl
# CategoryPrototype is NOT touched — it is permanent global infra (Phase B).
```

Because every seed artifact carries `seedSource` and a reserved synthetic owner, cleanup
**cannot** touch real subadmin data.

---

## 10. Run order

### Phase A — now
```
1. (FILE 1 first) apply Mongo schema + indexes so every EmergencyPlan has ownerUserId.
2. add deps (pymongo, cloudinary) + uv sync
3. set MONGODB_URI / MONGODB_DB (dev/staging!), SEED_OWNER_IDS, optionally CLOUDINARY_* in .env
4. implement app/store/mongo.py + app/ingest/fetch.py   (§2.3, §3.1)
5. python -m scripts.prep.seed_mongo --dry-run   # preview docs, no writes
6. python -m scripts.prep.seed_mongo             # build removable test corpus in Mongo
7. python -m scripts.prep.verify                 # Phase A checks (§8.1)
#  log in as the seed subadmin in the Next.js UI -> confirm their vault shows the docs
#  cleanup any time: python -m scripts.prep.cleanup
```

### Phase B — later (separate task, on hold)
```
implement extract/chunk -> python -m scripts.prep.prepare -> seed_prototypes -> verify(§8.2)
-> Weaviate upsert (tenant = "sub_"+ownerUserId)
```

Each step is independently runnable and re-runnable (idempotent).

---

## 11. Security / robustness checklist (the "no loopholes" pass)

**Phase A (now):**
- [ ] `MONGODB_URI` points at a **non-production** DB; seed writes use a dev-scoped
      credential. Run `--dry-run` first.
- [ ] `app/store/mongo.py` is imported by `scripts/prep/*` only — **never** by `app/api/*`
      (grep-enforced); request path stays Mongo-free.
- [ ] Every seed artifact tagged `seedSource` ⇒ cleanup is total and cannot hit real data.
- [ ] Seeded `ownerUserId` matches FILE 1's required field; uniqueness is per-owner.
- [ ] Fetch validates content-type + 25 MB cap + real User-Agent; `.gov` 404s and the
      classified/copyrighted skips (PPD-40, NFPA) are logged, never fatal.
- [ ] No secrets in code; all via `.env`.

**Phase B (later):**
- [ ] Pipeline **fails closed** on any record missing `tenantKey`.
- [ ] `tenantKey` format `"sub_"+ownerUserId` is identical across FILE 1, this prep, and
      Weaviate tenants — a mismatch would mis-route data.
- [ ] `data/staging/` is git-ignored (may contain document text).
- [ ] `contentHash` is computed and carried so the dedup cache works from day one.

---

*End of FILE 2 — `docs/DATA_PREP_IMPLEMENTATION_PLAN.md`.*
