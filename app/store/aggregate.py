"""Per-tenant rolling audit state — keyed by attachment for correct dedup/removal.

ai_audit_state document shape (one per tenantKey, _id = tenantKey):
  {
    _id:        tenantKey,
    documents:  {                          # one entry per attachment (the source of truth)
      <attachmentId>: {
        category, status, score, fileName, planId, summary, contentHash, updatedAt
      },
      ...
    },
    updatedAt:  datetime,
  }

Why a per-attachment map (not rolling $inc counters):
  - **Idempotent.** Re-analysing the same attachment REPLACES its entry — it is never
    double-counted, so the derived average can never drift above 100.
  - **Removable.** Deleting a document `$unset`s its entry, so it actually disappears
    from the subadmin's audit (the old append-only lists keyed by fileName couldn't).
  - **Bounded.** Exactly one entry per attachment — no unbounded duplicate growth.

The audit endpoint's `counts` / `integrity` / `scoreSum` / `scoreCount` / `all_analyzed`
/ `notable` are all DERIVED on read from `documents`, so they are always consistent with
the current set of analysed documents (no stale counters to maintain).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.store.models import get_state_col

# Per-item summary excerpt length stored in the rolling state. The FULL summary lives in
# ai_analysis_cache; here we keep a bounded excerpt so the audit LLM can read what each
# document is about while keeping the per-tenant doc well under MongoDB's 16 MB limit.
_AUDIT_SUMMARY_CHARS = 600
# Score threshold below which a derived `notable` entry is flagged (worst scorers).
_NOTABLE_THRESHOLD = 60
# Max items in the derived `notable` list.
_NOTABLE_CAP = 20

# Maps the per-document status string -> the integrity bucket key (new vocabulary).
# Legacy all_analyzed entries also stored the verdict string here, so the same map covers them.
_STATUS_BUCKET = {
    "Compliant":     "compliant",
    "Under Review":  "underReview",
    "Non-Compliant": "nonCompliant",
}


def update(
    tenant_key: str,
    *,
    attachment_id: str,
    category: str,
    status: str,
    score: int,
    file_name: str,
    plan_id: str,
    summary: str = "",
    content_hash: str = "",
) -> None:
    """Idempotently record one attachment's latest verdict for *tenant_key*.

    `$set documents.<attachmentId>` REPLACES any prior entry for the same attachment,
    so re-analysing never double-counts. No `$inc`/`$push` — aggregates are derived on read.

    *content_hash* (SHA-256 of the file bytes) makes this map double as the authoritative,
    tenant-scoped `contentHash -> attachmentIds` index used by the `/similar` exact-duplicate
    tier — which is why this is now written on EVERY analyze, INCLUDING cache hits (where no
    Weaviate chunk exists for the new attachment to carry the hash). See `find_by_content_hash`.
    """
    summary_excerpt = (summary or "").strip()[:_AUDIT_SUMMARY_CHARS]
    now = datetime.now(UTC)
    get_state_col().update_one(
        {"_id": tenant_key},
        {
            "$set": {
                f"documents.{attachment_id}": {
                    "category":    category,
                    "status":      status,
                    "score":       score,
                    "fileName":    file_name,
                    "planId":      plan_id,
                    "summary":     summary_excerpt,
                    "contentHash": content_hash,
                    "updatedAt":   now,
                },
                "updatedAt": now,
            },
            "$setOnInsert": {"_id": tenant_key},
        },
        upsert=True,
    )


def get_content_hash(tenant_key: str, attachment_id: str) -> str | None:
    """Return the stored contentHash for one attachment, or None if unknown.

    Used by `/similar` to resolve the queried attachment's hash without a Weaviate read —
    crucial for cache-hit files, which have no Weaviate chunks but DO have an audit entry.
    """
    doc = get_state_col().find_one(
        {"_id": tenant_key},
        {f"documents.{attachment_id}.contentHash": 1},
    )
    if not doc:
        return None
    entry = (doc.get("documents") or {}).get(attachment_id) or {}
    return entry.get("contentHash") or None


def find_by_content_hash(
    tenant_key: str,
    content_hash: str,
    *,
    exclude_attachment_id: str,
) -> list[dict[str, str]]:
    """Other attachments in *tenant_key* whose stored contentHash matches → exact duplicates.

    Scans the tenant's per-attachment `documents` map (one Mongo doc). This is the index that
    survives cache hits: a re-uploaded identical file is registered here even though it never
    reaches Weaviate. Returns `[{attachmentId, fileName, planId}]`, excluding the queried id.
    """
    if not content_hash:
        return []
    doc = get_state_col().find_one(
        {"_id": tenant_key},
        {"documents": 1},
    )
    documents = (doc or {}).get("documents")
    if not isinstance(documents, dict):
        return []

    matches: list[dict[str, str]] = []
    for att_id, entry in documents.items():
        if att_id == exclude_attachment_id:
            continue
        if not isinstance(entry, dict) or entry.get("contentHash") != content_hash:
            continue
        matches.append({
            "attachmentId": att_id,
            "fileName":     entry.get("fileName", ""),
            "planId":       entry.get("planId", ""),
        })
    return matches


def remove(tenant_key: str, attachment_id: str) -> None:
    """Drop one attachment's entry so it disappears from the tenant's audit."""
    get_state_col().update_one(
        {"_id": tenant_key},
        {"$unset": {f"documents.{attachment_id}": ""},
         "$set": {"updatedAt": datetime.now(UTC)}},
    )


def read(tenant_key: str) -> dict[str, Any]:
    """Return the derived audit state for *tenant_key* (or a zero-state dict).

    Aggregates `counts` / `integrity` / `scoreSum` / `scoreCount` / `all_analyzed` /
    `notable` from the per-attachment `documents` map. Falls back to deriving from a
    legacy doc (old `all_analyzed` list, deduped by fileName) so tenants written before
    this change still produce a correct audit until their docs are re-analysed.
    """
    doc = get_state_col().find_one({"_id": tenant_key})
    if not doc:
        return _zero_state()

    documents = doc.get("documents")
    if isinstance(documents, dict) and documents:
        return _derive(list(documents.values()))

    # Legacy compat: derive from the old append-only list, deduped by fileName so a
    # previously double-counted doc collapses to one entry.
    legacy = doc.get("all_analyzed") or doc.get("notable") or []
    if legacy:
        by_name: dict[str, dict] = {}
        for item in legacy:
            by_name[item.get("fileName", id(item))] = item
        return _derive(list(by_name.values()))

    return _zero_state()


def _derive(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the audit-state shape `summary/audit.py` expects from per-doc entries."""
    counts = {"coop": 0, "bcp": 0, "compliance": 0, "response": 0}
    integrity = {"compliant": 0, "underReview": 0, "nonCompliant": 0, "unanalyzed": 0}
    score_sum = 0
    score_count = 0
    all_analyzed: list[dict[str, Any]] = []

    for it in items:
        category = it.get("category", "coop")
        if category in counts:
            counts[category] += 1
        bucket = _STATUS_BUCKET.get(it.get("status", ""), "unanalyzed")
        integrity[bucket] += 1
        score = it.get("score", 0) or 0
        score_sum += score
        score_count += 1
        all_analyzed.append({
            "fileName": it.get("fileName"),
            "status":   it.get("status"),
            "score":    score,
            "planId":   it.get("planId"),
            "summary":  it.get("summary", ""),
        })

    notable = sorted(
        (d for d in all_analyzed if (d.get("score") or 0) < _NOTABLE_THRESHOLD),
        key=lambda d: d.get("score") or 0,
    )[:_NOTABLE_CAP]

    return {
        "counts":       counts,
        "integrity":    integrity,
        "scoreSum":     score_sum,
        "scoreCount":   score_count,
        "all_analyzed": all_analyzed,
        "notable":      notable,
        "updatedAt":    None,
    }


def _zero_state() -> dict[str, Any]:
    return _derive([])
