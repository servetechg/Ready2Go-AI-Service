# Next.js Tenancy Alignment — Implementation Plan (FILE 1)

> **Purpose:** make the Next.js MongoDB side **per-subadmin tenant-aware** so that, once
> the Python AI service vectorizes continuity documents, **one subadmin's data can never
> mix with another's**, and each subadmin's audit reflects only their own vault.
>
> **Status:** implemented against the `earth-quick-alert` (Next.js) repo. Companion:
> `docs/DATA_PREP_IMPLEMENTATION_PLAN.md` (FILE 2).
>
> **Audience:** the Next.js team / Claude Code executing the change.

---

## 0. TL;DR — strategy change (parallel collections, not in-place)

**Decision (locked with stakeholder):** we do **not** mutate the live `EmergencyPlan` /
`ContinuityAudit` collections. Changing the schema/indexes of a collection the live
product already reads and writes risks disturbing existing functionality. Instead we
**stand up new, tenant-aware collections alongside the old ones**, run them in parallel,
and **retire the legacy collections only once the new path is fully proven in
production.**

| # | Change | File | Type |
|---|--------|------|------|
| 1 | New tenant-aware plan model (`ownerUserId` + compound unique index) | `models/ContinuityPlan.ts` | **new** model |
| 2 | New per-subadmin audit model (keyed by `ownerUserId`) | `models/ContinuityAuditReport.ts` | **new** model |
| 3 | New plan CRUD + upload route, scoped by `ownerUserId`, with Python tenant webhook | `app/api/admin/continuity-plans/route.ts` | **new** route |
| 4 | New steps route, scoped by `ownerUserId` | `app/api/admin/continuity-plans/steps/route.ts` | **new** route |
| 5 | New attachment-delete route, scoped by `ownerUserId` | `app/api/admin/continuity-plans/attachment/route.ts` | **new** route |
| 6 | New per-subadmin audit route | `app/api/admin/continuity-plans/audit-summary/route.ts` | **new** route |
| 7 | **Re-point the admin UI** so every subadmin create/upload/edit/delete/audit hits the new routes | `app/(admin)/emergency-plan/page.tsx` | **edit** |
| 8 | Legacy `EmergencyPlan` / `ContinuityAudit` collections + `emergency-plans/**` routes | — | **untouched** |

**Cut-over (this branch):** the admin continuity-vault page is switched **now** from
`/api/admin/emergency-plans/**` to `/api/admin/continuity-plans/**`. From this point on,
**anything a subadmin creates or uploads lands in the new tenant-aware collections** — the
old collections receive no new writes. They remain readable/intact for rollback until
formally decommissioned.

**Why a new domain name (`Continuity*`)?** The stored data is the continuity vault
(COOP / BCP / compliance documents), so the collection name now reflects the data it
holds rather than the legacy `Emergency*` label. The same convention flows through
everywhere: collections `continuityplans` / `continuityauditreports`, routes under
`/api/admin/continuity-plans/**`.

**Backfill:** none. The new collections **start empty** and populate from new uploads
only. No migration script runs against legacy data; the old collections keep serving the
current UI until cut-over. (This replaces the earlier in-place backfill plan.)

**Non-negotiable contract (unchanged):** the four `aiIntegrity{Status,Score,Summary,AnalyzedAt}`
field names and the audit output shape (`summary, findings, posture, averageScore,
totals, integrity`) **stay exactly as they are** (PROJECT_CONTEXT §12). The new models
copy these field-for-field so the UI and the Python contract need no change.

---

## 1. Why this change is required

The legacy schema has **no concept of an owner**:

1. **`EmergencyPlan` has no owner field** (PROJECT_CONTEXT §4.2). Every subadmin's plans
   live in one global collection with no way to tell whose they are.
2. **`planId` is globally unique** (`unique: true`), and `inferCoopPlanMetadata`
   *deliberately* makes slugs collide for similar documents ("If two different uploads
   describe the same program, the slug MUST collide" — PROJECT_CONTEXT §6.3). The moment
   two *different subadmins* upload similar docs (e.g. both a "pandemic-coop-plan"), their
   files **merge into one shared plan**. Once that plan is vectorized under a single
   tenant, subadmin B can retrieve subadmin A's content. **This is a cross-tenant data
   leak.**
3. **`ContinuityAudit` is a global singleton** (`scope:'global'`, unique — PROJECT_CONTEXT
   §4.3). There is exactly one audit for the whole platform, blending all subadmins.

The Python service's entire isolation model assumes a `tenantKey` (its Pydantic
`TenantContext.tenant_key` in `app/schemas.py`; Weaviate native multi-tenancy in
ARCHITECTURE §4.1). **Legacy Mongo cannot supply one.** The new collections do.

**Tenant boundary:** the **uploading subadmin**. `tenantKey = "sub_" + ownerUserId`,
where `ownerUserId` is the subadmin's `User._id` (carried on the session JWT as
`session.user.id`). One Weaviate tenant per subadmin; one audit per subadmin.

**Rather than re-shaping the live collections**, we capture this owner on a fresh schema
so the live product is never at risk during the transition.

---

## 2. `models/ContinuityPlan.ts` (new — successor to `EmergencyPlan`)

A brand-new collection (`continuityplans`). The legacy `EmergencyPlan` is left exactly
as-is.

### 2.1 Owner field

```ts
ownerUserId: { type: Schema.Types.ObjectId, ref: 'User', required: true, index: true },
// Optional, non-breaking — kept for future org-level roll-ups. Not the isolation key.
licenseId:   { type: Schema.Types.ObjectId, ref: 'License', default: null, index: true },
```

`ownerUserId` is the **tenant key source**. `licenseId` is informational only for v1
(super-admin roll-ups, billing) and must **not** be used for isolation.

### 2.2 Per-owner `planId` uniqueness

`planId` is **not** globally unique on this collection. A compound unique index makes the
slug unique *within a subadmin*, never across:

```ts
planId: { type: String, required: true },   // unique per owner (compound index below)
ContinuityPlanSchema.index({ ownerUserId: 1, planId: 1 }, { unique: true });
```

This preserves the intended "same program → same plan" merge behaviour **inside one
subadmin's vault** while guaranteeing two subadmins can each have their own
`pandemic-coop-plan` with zero collision.

### 2.3 Result

```ts
const ContinuityPlanSchema = new Schema({
    ownerUserId: { type: Schema.Types.ObjectId, ref: 'User', required: true, index: true },
    licenseId:   { type: Schema.Types.ObjectId, ref: 'License', default: null, index: true },
    planId:   { type: String, required: true },          // unique per owner (compound index)
    label:    { type: String, required: true },
    overview: { type: String, required: true },
    category: { type: String, enum: ['coop','bcp','compliance'] },
    steps:    [{ type: String }],
    attachments: [ /* unchanged — incl. the four aiIntegrity* fields */ ],
}, { timestamps: true });

ContinuityPlanSchema.index({ ownerUserId: 1, planId: 1 }, { unique: true });
```

Attachments subdoc (incl. `aiIntegrityStatus/Score/Summary/AnalyzedAt`) is copied
**field-for-field** from the legacy schema.

---

## 3. `models/ContinuityAuditReport.ts` (new — successor to `ContinuityAudit`)

A brand-new collection (`continuityauditreports`) with **one document per subadmin**. The
legacy global `ContinuityAudit` is left untouched.

```ts
ownerUserId: { type: Schema.Types.ObjectId, ref: 'User', required: true, unique: true, index: true },
// (no `scope` field — the global singleton concept is gone on the new collection)
```

Everything else (`summary, findings, posture, averageScore, totals, integrity,
generatedAt`) is copied from the legacy schema **unchanged** — only the key changes from
`scope` to `ownerUserId`. The output shape the UI consumes is identical.

---

## 4. Upload route — `app/api/admin/continuity-plans/route.ts` (new)

Mirrors the legacy `emergency-plans` POST handler but targets `ContinuityPlan` and is
fully owner-scoped.

### 4.1 Stamp the owner on plan create / scope find-or-create

```ts
const ownerUserId = session.user.id;   // already available from getSession()

let plan = await ContinuityPlan.findOne({ ownerUserId, planId: resolvedPlanId });
if (!plan) {
  plan = new ContinuityPlan({
    ownerUserId,
    licenseId: session.user.licenseId ?? null,
    planId: resolvedPlanId, label: resolvedLabel, overview: resolvedOverview,
    category: resolvedCategory, steps: [], attachments: [],
  });
}
```

### 4.2 Scope the integrity write-back selector

```ts
await ContinuityPlan.updateOne(
  { ownerUserId, planId: resolvedPlanId, 'attachments._id': attachmentId },
  { $set: { /* the four aiIntegrity* fields — unchanged */ } },
);
```

### 4.3 Send the tenant in the Python webhook (M7 integration point)

When `INTEGRITY_BACKEND=python`, the integrity call is routed to the Python service with
the tenant derived from the owner; otherwise the existing OpenAI path runs (default).

```ts
const body = {
  tenantContext: { tenantKey: `sub_${ownerUserId}`, actorUserId: String(ownerUserId) },
  plan: { planId: plan.planId, label: plan.label, overview: plan.overview,
          category: plan.category, steps: plan.steps },
  attachment: { attachmentId: String(attachmentId), fileName, fileExtension: ext,
                fileMime: mime, fileSizeBytes: buffer.length,
                fileUrl: upload.secure_url, cloudinaryPublicId: upload.public_id,
                cloudinaryResourceType: upload.resource_type },
};
// POST `${PYTHON_URL}/v1/integrity/analyze` with Bearer PYTHON_INTEGRITY_TOKEN
```

This matches the Python `AnalyzeRequest` contract (`app/schemas.py`) exactly. Env vars:
`INTEGRITY_BACKEND`, `PYTHON_URL`, `PYTHON_INTEGRITY_TOKEN`.

The same handler also exposes `GET` (list this owner's plans), `PUT` (create/replace
scoped to `{ ownerUserId, planId }`), and `PATCH` (update scoped to `{ ownerUserId,
planId }`).

---

## 5. Read & edit routes — every selector scoped by `ownerUserId`

All sibling new routes scope **every** `ContinuityPlan` query so a subadmin can only ever
see or mutate their own plans.

| Route | Method | Selector |
|-------|--------|----------|
| `/api/admin/continuity-plans` | `GET` | `ContinuityPlan.find({ ownerUserId })` |
| `/api/admin/continuity-plans` | `PUT` | create/replace scoped to `{ ownerUserId, planId }` |
| `/api/admin/continuity-plans` | `PATCH` | update selector includes `ownerUserId` |
| `/api/admin/continuity-plans/steps` | `POST` | selector includes `ownerUserId` |
| `/api/admin/continuity-plans/attachment` | `DELETE` | selector includes `ownerUserId` |

**Rule of thumb:** *every* `ContinuityPlan` query gets `ownerUserId` in its filter. A
query without it is a tenancy bug.

### 5.1 Super-admin visibility (optional, off by default)

Every role (incl. `super-admin`, `admin`) sees **only their own** uploads. If product
later wants super-admin to see *all* vaults, gate it behind an explicit opt-in
(`?all=1`, super-admin only) — never let a non-super-admin reach an unfiltered branch.

---

## 6. Audit route — `app/api/admin/continuity-plans/audit-summary/route.ts` (new)

Per-subadmin, against the new collections.

### 6.1 Scope the input build

```ts
async function buildAuditInput(ownerUserId: string): Promise<ContinuityAuditInput> {
  const plans = await ContinuityPlan.find({ ownerUserId }).lean();
  // …rest of the aggregation (counts, integrity, averageScore) is unchanged…
}
```

Each subadmin's audit scans only their own (small) plan set — which also avoids the
legacy global `find({})` corpus scan.

### 6.2 Scope GET and POST

```ts
const ownerUserId = session.user.id;

// GET: return this subadmin's cached audit
const audit = await ContinuityAuditReport.findOne({ ownerUserId });

// POST: regenerate + upsert this subadmin's audit
const input  = await buildAuditInput(ownerUserId);
const result = await openaiService.generateContinuityAuditSummary(input);
await ContinuityAuditReport.findOneAndUpdate(
  { ownerUserId },
  { $set: { ownerUserId, summary: result.summary, findings: result.findings,
            posture: result.posture, averageScore: result.averageScore,
            totals: input.totals, integrity: input.integrity, generatedAt: new Date() } },
  { upsert: true, new: true },
);
```

The audit output shape is identical to the legacy endpoint, so when the frontend is
re-pointed at `/api/admin/continuity-plans/audit-summary` it renders
`summary/findings/posture` verbatim with no component change.

---

## 6a. Cut-over — re-point the admin UI to the new routes (`app/(admin)/emergency-plan/page.tsx`)

This is the step that makes the change **live for subadmins**. The admin continuity-vault
page issues seven `fetch` calls; every one is switched from the legacy prefix to the new
one so the whole surface (list, upload, edit, steps, attachment delete, audit
load/refresh) operates on the new collections in lockstep:

```ts
// BEFORE → AFTER  (all seven call sites)
'/api/admin/emergency-plans'                  → '/api/admin/continuity-plans'
'/api/admin/emergency-plans/audit-summary'    → '/api/admin/continuity-plans/audit-summary'
'/api/admin/emergency-plans/steps'            → '/api/admin/continuity-plans/steps'
'/api/admin/emergency-plans/attachment'       → '/api/admin/continuity-plans/attachment'
```

Because the new routes return the **same response shapes** as the legacy ones, no other
component code changes. The instant this ships, **new subadmin uploads/creates write only
to `continuityplans` / `continuityauditreports`**; the legacy collections stop receiving
writes.

> The user-facing `/api/user/emergency-plan` route is **unrelated** — it reads the `User`
> model (contacts, supply kit, meeting points), not the continuity vault — so it is left
> untouched.

---

## 7. No data migration (parallel start-empty)

Because the new collections are **independent and start empty**, there is **no backfill
script and no index rebuild against legacy data**. The legacy `EmergencyPlan` /
`ContinuityAudit` collections and their `emergency-plans/**` routes keep running
unchanged.

The new collections build their indexes (the compound `(ownerUserId, planId)` unique on
`continuityplans`, the unique `ownerUserId` on `continuityauditreports`) automatically
from the Mongoose schema definitions the first time the models are used — no manual index
surgery on live data.

> If, **later**, stakeholders decide to carry historical legacy documents into the new
> collections, that becomes a separate one-off copy job (read legacy → write tenant-aware
> copies under a designated owner). It is explicitly **out of scope** for this start-empty
> rollout.

---

## 8. Rollout & verification

### 8.1 Order of operations (single execution for this branch)
1. Ship the **new models + new routes** (§2–§6) — additive only.
2. **Re-point the admin UI** (§6a) so subadmin create/upload now writes to the new
   collections. *(This branch executes steps 1–2 together — one go.)*
3. Wire the Python webhook tenant (§4.3) under `INTEGRITY_BACKEND=python`.
4. **Only after the new path is proven**, decommission the legacy collections + routes.

### 8.2 Verification checklist
- [ ] Admin UI calls only `/api/admin/continuity-plans/**`; a new subadmin upload writes to `continuityplans` (and **not** to legacy `emergencyplans`).
- [ ] New collections exist and are populated by new uploads (`continuityplans`, `continuityauditreports`).
- [ ] `(ownerUserId, planId)` unique index exists on `continuityplans`.
- [ ] Two test subadmins can each create a plan with the **same** `planId` without error.
- [ ] Subadmin A's `GET /continuity-plans` returns **none** of subadmin B's plans.
- [ ] Subadmin A cannot `PATCH`/`DELETE` a plan/attachment owned by B (selector misses → no-op).
- [ ] Each subadmin's `GET /continuity-plans/audit-summary` returns an audit scoped to them.
- [ ] The four `aiIntegrity*` fields and the audit output shape are byte-for-byte unchanged.
- [ ] Upload → integrity write-back updates only the owner's attachment.
- [ ] Legacy `emergency-plans/**` + `EmergencyPlan` / `ContinuityAudit` still behave exactly as before.

### 8.3 Reversibility
Fully additive — rolling back means deleting the new collections, models, and routes. The
legacy product is never modified, so there is nothing to un-migrate.

---

## 9. Contract guardrails (must hold)

- **Do not** rename or retype any `aiIntegrity*` field or any audit output field — the UI
  keys off them (PROJECT_CONTEXT §12). The new models copy them verbatim.
- `category` stays `coop|bcp|compliance`; the synthetic `response` bucket remains
  client-inferred and is never stored (PROJECT_CONTEXT §4.2).
- `tenantKey` sent to Python is exactly `"sub_" + ownerUserId` — it must match what the
  data-prep pipeline (FILE 2) and Weaviate tenants use, or scores will write to the wrong
  tenant.
- This plan adds **new collections + scoping only**; the Python request/response contract
  (`app/schemas.py`) is untouched, and the legacy collections are untouched.

---

*End of FILE 1 — `docs/NEXTJS_TENANCY_ALIGNMENT_PLAN.md`.*
