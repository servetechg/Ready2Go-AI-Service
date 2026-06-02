"""Remove ALL seed data — Mongo plans + (optionally) Cloudinary assets.

Safety
------
The delete filter is EXCLUSIVELY `{"seedSource": SEED_MARK}`. This marker is
set only by seed_mongo.py on synthetic/seed-owner documents. Real subadmin
plans never carry this field, so cleanup cannot touch production data.

Phase B note: Weaviate tenant cleanup and staging-file deletion are deferred
to the Phase B implementation (on hold). They are noted here as no-ops for now.

Usage
-----
  python -m scripts.prep.cleanup           # delete seed data from Mongo
  python -m scripts.prep.cleanup --dry-run # show what would be deleted
"""

from __future__ import annotations

import sys

import structlog
from app.config import get_settings
from app.logging import configure_logging
from app.store.mongo import plans

log = structlog.get_logger(__name__)


def cleanup(dry_run: bool = False) -> None:
    settings = get_settings()
    mark = settings.seed_mark
    col = plans()

    # ---- Count what will be removed ----
    seed_count = col.count_documents({"seedSource": mark})
    if seed_count == 0:
        log.info("cleanup.nothing_to_remove", seed_mark=mark)
        return

    log.info("cleanup.found", seed_plans=seed_count, seed_mark=mark)

    if dry_run:
        # Collect some ids for the preview
        sample = list(col.find({"seedSource": mark}, {"planId": 1, "ownerUserId": 1}).limit(5))
        for doc in sample:
            log.info(
                "cleanup.dry_run_would_delete",
                plan_id=doc.get("planId"),
                owner=str(doc.get("ownerUserId", ""))[:16],
            )
        if seed_count > 5:
            log.info("cleanup.dry_run_more", remaining=seed_count - 5)
        log.info(
            "cleanup.dry_run_note",
            msg="No changes written — re-run without --dry-run to delete.",
        )
        return

    # ---- Delete from MongoDB ----
    result = col.delete_many({"seedSource": mark})
    log.info("cleanup.mongo_deleted", deleted_count=result.deleted_count)

    # ---- Cloudinary cleanup (only if seeding used Cloudinary) ----
    if settings.seed_use_cloudinary and settings.cloudinary_cloud_name:
        log.info(
            "cleanup.cloudinary_note",
            msg=(
                "SEED_USE_CLOUDINARY=true was set. Cloudinary assets in "
                f"'{settings.cloudinary_folder}' that were uploaded during seeding "
                "should be manually deleted from the Cloudinary media library, "
                "or use the Cloudinary API to delete by folder prefix."
            ),
        )
    else:
        log.info(
            "cleanup.cloudinary_skip",
            msg="Cloudinary seeding was not used — nothing to clean there.",
        )

    # ---- Phase B: Weaviate + staging (deferred) ----
    log.info(
        "cleanup.phase_b_note",
        msg=(
            "Phase B (Weaviate tenant deletion + data/staging/*.jsonl removal) "
            "is deferred until Phase B is implemented."
        ),
    )

    log.info("cleanup.complete", msg="Seed data removed from MongoDB.")


def main() -> None:
    configure_logging("INFO", json_logs=False)
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("\n⚠  DRY RUN — nothing will be deleted.\n")
    cleanup(dry_run=dry_run)


if __name__ == "__main__":
    main()
