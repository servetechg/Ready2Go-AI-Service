"""Per-tenant rolling audit state — O(1) update per analyze.

ai_audit_state document shape (one per tenantKey, _id = tenantKey):
  {
    _id:           tenantKey,
    counts:        {coop: N, bcp: N, compliance: N, response: N},
    integrity:     {compliant: N, underReview: N, nonCompliant: N, unanalyzed: N},
    scoreSum:      N,
    scoreCount:    N,
    notable:       [{fileName, status, score, planId, summary}],  # worst-N (score<60), bounded
    all_analyzed:  [{fileName, status, score, planId, summary}],  # every doc, no cap/threshold
    dirty:         bool,  # True when a new analyze happened since last audit
    updatedAt:     datetime,
  }

update() increments counters in a single MongoDB $inc/$set/$push — never reads or
rewrites the whole doc.  This is what makes the audit O(1) per upload.

all_analyzed holds every analyzed doc regardless of score so the audit endpoint
can pass ALL docs to the LLM when AUDIT_SAMPLE_CAP=0.  notable keeps the
worst-20 (score<60) for cheap fast access when a bounded sample is sufficient.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pymongo

from app.store.models import get_state_col

# Maximum number of "notable" items kept (worst scores, gaps).
_NOTABLE_CAP = 20
# Score threshold below which an item is "notable" (worth flagging in audit).
_NOTABLE_THRESHOLD = 60
# Per-item summary excerpt length stored in the rolling state. The FULL summary
# lives in ai_analysis_cache; here we keep a bounded excerpt so the audit LLM can
# read what each document is about without risking MongoDB's 16 MB doc-size limit
# (all_analyzed is uncapped — one entry per analyzed document).
_AUDIT_SUMMARY_CHARS = 600


def update(
    tenant_key: str,
    *,
    category: str,
    status: str,
    score: int,
    file_name: str,
    plan_id: str,
    summary: str = "",
) -> None:
    """O(1) increment of rolling audit state for *tenant_key*.

    Increments the appropriate integrity counter, accumulates score sum,
    and records the doc (with a bounded summary excerpt) in all_analyzed — and
    in the notable list too if the score is low.  Marks dirty=True.
    """
    summary_excerpt = (summary or "").strip()[:_AUDIT_SUMMARY_CHARS]
    col = get_state_col()

    # Map status string -> integrity sub-field name (new status vocabulary).
    integrity_field = {
        "Compliant":     "integrity.compliant",
        "Under Review":  "integrity.underReview",
        "Non-Compliant": "integrity.nonCompliant",
    }.get(status, "integrity.unanalyzed")

    # Map category -> counts sub-field.
    valid_cats = {"coop", "bcp", "compliance", "response"}
    cat_field = f"counts.{category}" if category in valid_cats else "counts.coop"

    update_doc: dict[str, Any] = {
        "$inc": {
            cat_field:       1,
            integrity_field: 1,
            "scoreSum":      score,
            "scoreCount":    1,
        },
        "$set": {
            "dirty":     True,
            "updatedAt": datetime.now(UTC),
        },
        "$setOnInsert": {
            "_id": tenant_key,
        },
        # Always record every analyzed doc — no score threshold, no cap.
        # AUDIT_SAMPLE_CAP=0 uses this list to give the LLM the full corpus.
        "$push": {
            "all_analyzed": {
                "fileName": file_name,
                "status":   status,
                "score":    score,
                "planId":   plan_id,
                "summary":  summary_excerpt,
            }
        },
    }

    col.update_one({"_id": tenant_key}, update_doc, upsert=True)

    # Also keep a bounded notable list (worst-N, score < threshold) for fast access.
    if score < _NOTABLE_THRESHOLD:
        col.update_one(
            {"_id": tenant_key},
            {
                "$push": {
                    "notable": {
                        "$each": [{"fileName": file_name, "status": status,
                                   "score": score, "planId": plan_id,
                                   "summary": summary_excerpt}],
                        "$sort": {"score": pymongo.ASCENDING},
                        "$slice": _NOTABLE_CAP,
                    }
                }
            },
        )


def read(tenant_key: str) -> dict[str, Any]:
    """Return the current rolling state for *tenant_key* (or a zero-state dict)."""
    doc = get_state_col().find_one({"_id": tenant_key}, {"_id": 0})
    if doc:
        return _normalize_integrity(doc)
    return _zero_state()


def _normalize_integrity(doc: dict[str, Any]) -> dict[str, Any]:
    """Fold any legacy integrity bucket keys into the new status vocabulary.

    Older ai_audit_state docs were written with inSync/reviewing/deviation. We
    rename those to compliant/underReview/nonCompliant on read so existing tenants
    keep correct counts without a migration script — counts fully converge to the
    new keys as new analyses arrive.
    """
    integrity = doc.get("integrity")
    if not isinstance(integrity, dict):
        return doc
    legacy_map = {"inSync": "compliant", "reviewing": "underReview", "deviation": "nonCompliant"}
    if not any(old in integrity for old in legacy_map):
        return doc
    merged = {"compliant": 0, "underReview": 0, "nonCompliant": 0, "unanalyzed": 0}
    for key, value in integrity.items():
        target = legacy_map.get(key, key)
        merged[target] = merged.get(target, 0) + (value or 0)
    doc["integrity"] = merged
    return doc


def mark_clean(tenant_key: str) -> None:
    """Mark the audit as clean (called after a summary is generated)."""
    get_state_col().update_one(
        {"_id": tenant_key},
        {"$set": {"dirty": False, "updatedAt": datetime.now(UTC)}},
    )


def _zero_state() -> dict[str, Any]:
    return {
        "counts":       {"coop": 0, "bcp": 0, "compliance": 0, "response": 0},
        "integrity":    {"compliant": 0, "underReview": 0, "nonCompliant": 0, "unanalyzed": 0},
        "scoreSum":     0,
        "scoreCount":   0,
        "notable":      [],
        "all_analyzed": [],
        "dirty":        False,
        "updatedAt":    None,
    }
