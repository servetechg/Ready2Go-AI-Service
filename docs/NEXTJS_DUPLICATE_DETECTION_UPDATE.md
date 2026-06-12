# Next.js Update Notice — Duplicate Detection Now Works on Re-Uploads (Cache Hits)

> **Audience:** the Ready2Go Next.js team.
> **Type:** behavioural change on the AI service. **No breaking contract change.**
> **Companion docs:** [NEXTJS_INTEGRATION_GUIDE.md](./NEXTJS_INTEGRATION_GUIDE.md) (full contract),
> [SERVICE_DOCUMENTATION.md](./SERVICE_DOCUMENTATION.md) (internals). This note covers only what
> changed and how to align your UI/flow with it.

---

## 1. TL;DR

We fixed a gap where **re-uploading a byte-for-byte identical file** (which returns a *cache hit*,
zero AI tokens) produced **"No similar files found"** in the duplicates panel — even though it is the
most certain duplicate possible. Now the AI service maintains a tenant-scoped content-hash index and
records every analysis (including cache hits), so **`GET /v1/integrity/similar/{attachmentId}` now
detects exact duplicates in both directions, every time.**

- **Required Next.js code change: NONE.** Every endpoint's request/response shape is **unchanged**.
  Your existing `/similar` consumer keeps working and simply starts returning the duplicates it was
  missing before.
- **Recommended Next.js work: small UI/UX alignment** so users clearly see exact duplicates (badge /
  "Duplicate of …"), plus confirming two flows you likely already have (call `/similar` to display
  duplicates; call `DELETE /attachments` on document deletion).

---

## 2. What changed on the AI service (behavioural)

| # | Change | Effect you will observe |
|---|--------|-------------------------|
| 1 | **Exact-duplicate detection survives a cache hit.** Every analyze — including cache hits — now registers the attachment + its `contentHash` in a tenant-scoped index. | `/similar/{id}` returns the matching file(s) for re-uploaded identical files, where it previously returned `[]`. |
| 2 | **Two-source exact tier (union).** `/similar` merges Weaviate's content-hash match **and** the new Mongo index. | More reliable exact detection; works even if the vector store is temporarily degraded. |
| 3 | **Zero-token vector clone on cache hit.** When a re-upload has a same-tenant original already vectorized, the service clones those vectors under the new id (no OpenAI re-embedding). | Re-uploaded files also show up in **semantic** ("related, not identical") results — still at zero token cost. |
| 4 | **Re-uploaded files now count in the service's internal audit state.** Previously a cache-hit attachment was never registered. | Mainly internal; see [§6](#6-behavioural-notes-to-keep-in-mind) — your audit payload is built from your own data, so your numbers are unaffected. |
| 5 | **Delete also clears the duplicate index.** `DELETE /v1/integrity/attachments` removes the entry from both Weaviate and the index. | Duplicates of a deleted file correctly **stop** being reported. |

All of this is internal to the AI service. **You do not send or store anything new.**

---

## 3. Contract impact — nothing to migrate

| Endpoint | Request shape | Response shape | Action for Next.js |
|----------|---------------|----------------|--------------------|
| `GET /v1/integrity/similar/{attachmentId}?tenantKey=` | unchanged | unchanged | None — you just get better results |
| `POST /v1/integrity/analyze` | unchanged | unchanged | None |
| `GET /v1/integrity/result/{attachmentId}` | unchanged | unchanged | None |
| `DELETE /v1/integrity/attachments` | unchanged | unchanged | None (keep calling it on delete) |
| `POST /v1/audit/summary` | unchanged | unchanged | None |

No new fields, no renamed fields, no status/enum changes. This is purely additive behaviour behind a
stable contract.

---

## 4. What we recommend you implement (to align with the feature)

None of these are required for correctness, but they turn the fix into a visible, complete feature.
Listed highest-value first.

1. **Show exact duplicates prominently.** When `/similar` returns an entry with `exactDuplicate: true`
   (`similarity = 1.0`), render it as a strong signal — e.g. a **"Duplicate"** badge on the file row
   and a **"Exact duplicate of {fileName}"** line in the document modal. Treat `exactDuplicate: false`
   entries (similarity < 1.0) as softer "Related documents."
2. **Call `/similar` at the right moments.** It is computed **live** against current vault state, so
   call it when the user **opens a document** (detail view / modal) and/or right after analysis
   settles (`/result` → `state: "done"`). Re-call it after a delete so the panel reflects the change.
   Don't cache its result long-term — it's meant to be queried on demand.
3. **(Optional) Warn at upload time.** If you want to discourage duplicate uploads, after the
   attachment is created you can call `/similar/{newAttachmentId}` and, if an `exactDuplicate` is
   returned, surface a non-blocking "You already uploaded this file (as {fileName})" notice. This now
   works even when the upload was a cache hit.
4. **Keep the two flows you already have.** (a) Pass the correct `tenantKey` = `"sub_" + ownerUserId`
   on every `/similar` call. (b) On document deletion, call `DELETE /v1/integrity/attachments` with
   that document's `attachmentId` — this is what removes it from the duplicate index so stale
   duplicates don't linger.

---

## 5. How to read the `/similar` response

Shape is unchanged; here is how to interpret it for the duplicate UX.

```json
{
  "attachmentId": "att_B",
  "similar": [
    { "attachmentId": "att_A", "fileName": "recovery-plan.pdf", "planId": "plan_x", "similarity": 1.0,  "exactDuplicate": true  },
    { "attachmentId": "att_Q", "fileName": "recovery-plan-v2.pdf", "planId": "plan_x", "similarity": 0.82, "exactDuplicate": false }
  ]
}
```

| Field | Meaning | Suggested UI treatment |
|-------|---------|------------------------|
| `exactDuplicate: true` (`similarity: 1.0`) | Byte-for-byte identical file already in this tenant. The certain case. | "Duplicate" badge; "Exact duplicate of {fileName}". |
| `exactDuplicate: false` (`similarity` 0.55–0.99) | Semantically very similar but not identical. | "Related document ({percentage}% similar)". |
| `similar: []` | No duplicates or near-duplicates in the tenant. | "No similar files found." |

Notes:
- Results are **tenant-scoped** — another customer's identical file never appears.
- Capped at the 5 closest matches, sorted by similarity descending.
- The endpoint **never 500s** — on an internal hiccup it returns a partial/empty list, so render
  defensively (empty list = "none found").

---

## 6. Behavioural notes to keep in mind

- **Audit counts.** Re-uploaded (cache-hit) files are now registered in the AI service's internal
  rolling audit state, where before they were skipped. **Your audit numbers are unaffected**, because
  you build the `POST /v1/audit/summary` payload (`totals`/`counts`/`integrity`) from your own
  `continuityplans` data — every attachment, duplicate or not, was already counted on your side. This
  change simply makes the service's internal state consistent with yours. Each distinct
  `attachmentId` (even a duplicate) is counted once; re-analyzing the same id replaces, never
  double-counts.
- **Timing.** A cache-hit analyze is fast (zero AI tokens) but still flows through the async
  202 → poll `/result` path. Keep polling until `state: "done"`; the duplicate becomes visible in
  `/similar` as soon as the job completes.
- **Cross-tenant uploads.** If two different tenants upload the same public document, each sees only
  its **own** copies in `/similar` — never the other tenant's. (Internally the verdict cache is shared
  for cost reasons, but duplicate detection is strictly tenant-isolated.)
- **`attachmentId` discipline.** Continue using the `attachments[]` sub-document `_id` as the
  `attachmentId`. Re-using an id for a different file would corrupt detection; a fresh upload must get
  a fresh id.

---

## 7. Verification scenarios (for your QA)

1. Upload file **A**, then upload the **identical** file **B** (B logs a cache hit on our side).
   - `GET /v1/integrity/similar/{B}` includes **A** with `exactDuplicate: true, similarity: 1.0`.
   - `GET /v1/integrity/similar/{A}` includes **B** likewise (both directions).
2. Upload a **3rd** identical copy **C** → all three cross-reference each other.
3. Upload a **different but related** document → appears with `exactDuplicate: false` and a
   sub-1.0 similarity (only if ≥ 0.55).
4. Same file uploaded by a **different tenant** → does **not** appear in the first tenant's results.
5. `DELETE /v1/integrity/attachments` for **B** → B disappears from A's similar list.

---

## 8. Optional future enhancement (NOT implemented — available on request)

We considered exposing the file's `contentHash` on the analyze response
(`result.details.contentHash`) so you could group/flag exact duplicates **client-side** as a
belt-and-suspenders, independent of `/similar`. **This was deliberately not built** — the server-side
`/similar` fix fully covers the need and owns vault-wide, tenant-isolated duplicate truth. If you
later want the client-side option, tell us and we'll add the single additive field (no breaking
change). Until then, do not expect a `contentHash` field on responses.

---

## 9. Bottom line

- **Do nothing and it still works better** — the duplicates you were missing now appear in `/similar`.
- **Spend a little UI effort** on an exact-duplicate badge / "Duplicate of …" and on calling
  `/similar` when a document is opened, and you have a complete, obvious duplicate-detection feature.
- **Keep** passing the correct `tenantKey` and calling `DELETE /attachments` on deletion — those two
  habits are what keep the index accurate.

*This notice reflects the AI service on `main`. The endpoint contracts in
[NEXTJS_INTEGRATION_GUIDE.md](./NEXTJS_INTEGRATION_GUIDE.md) remain the source of truth.*
