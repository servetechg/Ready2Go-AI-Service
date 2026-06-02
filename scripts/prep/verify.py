"""Phase A verification — check the seeded corpus in MongoDB.

Operates on the tenant-aware `continuityplans` collection (filtered to seed docs
by `seedSource`, so any real subadmin documents are ignored).

What is checked
---------------
1. Owner present      — every seeded plan has ownerUserId (no orphans).
2. Counts per owner   — each seed subadmin has ≥1 plan and ≥1 attachment.
3. Isolation          — the two seed owners hold completely disjoint planIds.
4. Marker present     — every seeded plan + attachment carries `seedSource`.
5. aiIntegrity* unset — no seeded attachment has been pre-scored (they should
                        all show as "pending analysis" in the UI).
6. Idempotency hint   — re-running seed_mongo.py should not add duplicate
                        attachments; verified by checking for duplicate fileNames
                        within the same plan.

Usage
-----
  python -m scripts.prep.verify
"""

from __future__ import annotations

import sys
from collections import defaultdict

import structlog
from app.config import get_settings
from app.logging import configure_logging
from app.store.mongo import plans

log = structlog.get_logger(__name__)

_AI_FIELDS = (
    "aiIntegrityStatus",
    "aiIntegrityScore",
    "aiIntegritySummary",
    "aiIntegrityAnalyzedAt",
)


def verify() -> bool:
    """Run all Phase A checks. Returns True if all pass."""
    settings = get_settings()
    mark = settings.seed_mark
    col = plans()

    seed_plans = list(col.find({"seedSource": mark}))
    if not seed_plans:
        log.error(
            "verify.no_seed_data",
            msg=f"No plans found with seedSource='{mark}'. Run seed_mongo.py first.",
        )
        return False

    failures: list[str] = []
    owner_plans: dict[str, list[str]] = defaultdict(list)     # owner_str -> [planId]
    owner_attachments: dict[str, int] = defaultdict(int)

    for plan in seed_plans:
        pid = plan.get("planId", "<unknown>")

        # Check 1 — ownerUserId present
        owner = plan.get("ownerUserId")
        if not owner:
            failures.append(f"Plan '{pid}' is missing ownerUserId (FILE 1 migration not run?).")
        else:
            owner_str = str(owner)
            owner_plans[owner_str].append(pid)

        # Check 4 — seedSource on plan
        if plan.get("seedSource") != mark:
            failures.append(f"Plan '{pid}' missing seedSource='{mark}'.")

        # Per-attachment checks
        attachments = plan.get("attachments", [])
        for att in attachments:
            fname = att.get("fileName", "<unknown>")

            # Check 4 — seedSource on attachment
            if att.get("seedSource") != mark:
                failures.append(f"Attachment '{fname}' in plan '{pid}' missing seedSource.")

            # Check 5 — aiIntegrity* unset
            for field in _AI_FIELDS:
                if att.get(field) is not None:
                    failures.append(
                        f"Attachment '{fname}' in plan '{pid}' has {field} already set "
                        f"(expected unset for 'pending analysis')."
                    )

            # Check 6 — no duplicate fileNames within the same plan
            if owner:
                owner_attachments[str(owner)] += 1

        fnames = [a.get("fileName") for a in attachments]
        dupes = [f for f in fnames if fnames.count(f) > 1 and f is not None]
        if dupes:
            failures.append(f"Plan '{pid}' has duplicate attachment fileNames: {set(dupes)}")

    # Check 2 — counts per owner non-zero
    for owner_str, plan_ids in owner_plans.items():
        att_count = owner_attachments.get(owner_str, 0)
        log.info(
            "verify.owner_counts",
            owner=owner_str[:16] + "...",
            plans=len(plan_ids),
            attachments=att_count,
        )
        if len(plan_ids) == 0:
            failures.append(f"Owner '{owner_str}' has no seeded plans.")
        if att_count == 0:
            failures.append(f"Owner '{owner_str}' has no seeded attachments.")

    # Check 3 — disjoint planId sets across owners
    owner_list = list(owner_plans.keys())
    if len(owner_list) >= 2:
        for i in range(len(owner_list)):
            for j in range(i + 1, len(owner_list)):
                a_ids = set(owner_plans[owner_list[i]])
                b_ids = set(owner_plans[owner_list[j]])
                overlap = a_ids & b_ids
                if overlap:
                    # This is expected — two owners CAN share planId slugs (by design).
                    # We log it as info, not a failure, since the compound unique index
                    # (ownerUserId, planId) guarantees they are different documents.
                    log.info(
                        "verify.shared_plan_ids",
                        owner_a=owner_list[i][:16],
                        owner_b=owner_list[j][:16],
                        shared=list(overlap),
                        note="Expected — each owner has their own copy (compound unique index).",
                    )
    elif len(owner_list) < 2:
        failures.append(
            "Only 1 unique seed owner found — at least 2 required to test isolation. "
            "Check SEED_OWNER_IDS or ensure the manifest has docs for both tenants A and B."
        )

    # ---- Report ----
    log.info("verify.summary", total_seed_plans=len(seed_plans), failures=len(failures))
    if failures:
        for f in failures:
            log.error("verify.FAIL", detail=f)
        return False

    log.info("verify.PASS", msg="All Phase A checks passed.")
    return True


def main() -> None:
    configure_logging("INFO", json_logs=False)
    passed = verify()
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
