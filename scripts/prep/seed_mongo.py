"""DEV-ONLY: Build the removable .gov continuity-document test corpus in MongoDB.

Targets the tenant-aware `continuityplans` collection (the new parallel collection
the Next.js UI now reads from — each record carries `ownerUserId`).

What this script does
---------------------
For each document in the seed manifest (scripts/prep/manifest.py):
  1. Download bytes from the .gov URL using app/ingest/fetch.py.
  2. Optionally upload bytes to Cloudinary (when SEED_USE_CLOUDINARY=true) so the
     stored fileUrl is a Cloudinary secure_url — identical to real upload shape.
     Default (SEED_USE_CLOUDINARY=false): store the .gov URL directly as fileUrl.
  3. Upsert a ContinuityPlan document keyed by (ownerUserId, planId) and push the
     attachment (idempotent — won't duplicate if the same fileName already exists).

Owner IDs
---------
Set SEED_OWNER_IDS to comma-separated REAL subadmin User._id hex strings so the
seeded documents show up when you log in as those subadmins in the Next.js UI.
If blank, reserved synthetic ObjectIds are used — data exists in Mongo but no
one can log in as that owner (still useful for isolation tests).

Safety
------
  - Every seeded EmergencyPlan and attachment carries a `seedSource` field set to
    SEED_MARK. cleanup.py deletes exclusively by that marker — it cannot touch
    any real subadmin data.
  - aiIntegrity* fields are deliberately left UNSET (null) so documents appear
    as "pending analysis" in the UI, ready for the future scoring pipeline.
  - Use --dry-run to preview every doc that would be written without touching Mongo.
  - MUST be pointed at a DEV/STAGING Mongo — never production.

Usage
-----
  python -m scripts.prep.seed_mongo           # normal run
  python -m scripts.prep.seed_mongo --dry-run # preview only, no writes
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import PurePosixPath

import structlog
from app.config import get_settings
from app.ingest.fetch import FetchError, fetch_bytes
from app.logging import configure_logging
from app.store.mongo import plans
from bson import ObjectId

from scripts.prep.manifest import SEED_DOCS, SKIPPED, SeedDoc

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Reserved synthetic subadmin ObjectIds — obviously fake, never collision-risk
# with real users (all zeros except the last byte).
# ---------------------------------------------------------------------------
_SYNTHETIC_OWNERS: dict[str, ObjectId] = {
    "A": ObjectId("0000000000000000000000a1"),
    "B": ObjectId("0000000000000000000000a2"),
}


def _resolve_owners(settings_seed_owner_ids: str) -> dict[str, ObjectId]:
    """Return {tenant_label: ObjectId} from SEED_OWNER_IDS or fall back to synthetic."""
    raw = [s.strip() for s in settings_seed_owner_ids.split(",") if s.strip()]
    if not raw:
        log.warning(
            "seed.owners.synthetic",
            msg=(
                "SEED_OWNER_IDS is not set — using reserved synthetic ObjectIds. "
                "Seeded docs will NOT be visible when you log in to the Next.js UI. "
                "Set SEED_OWNER_IDS=<subadmin_id_A>,<subadmin_id_B> for UI-visible data."
            ),
        )
        return dict(_SYNTHETIC_OWNERS)

    labels = sorted({d.tenant for d in SEED_DOCS})
    if len(raw) < len(labels):
        log.warning(
            "seed.owners.partial",
            provided=len(raw),
            required=len(labels),
            msg="Fewer SEED_OWNER_IDS than tenant labels — reusing last id for remaining tenants.",
        )
        while len(raw) < len(labels):
            raw.append(raw[-1])

    try:
        return {label: ObjectId(raw[i]) for i, label in enumerate(labels)}
    except Exception as exc:
        log.error("seed.owners.invalid", error=str(exc))
        raise SystemExit(1) from exc


def _filename_from(doc: SeedDoc) -> str:
    """Derive a clean filename from the URL path."""
    name = PurePosixPath(doc.url).name
    return name if name else f"{doc.plan_id}.pdf"


async def _maybe_cloudinary_upload(
    data: bytes,
    filename: str,
    mime: str,
    settings,
) -> tuple[str, str | None]:
    """Upload bytes to Cloudinary and return (secure_url, public_id).

    Falls back to raising so seed_mongo.py can log and continue.
    Only called when SEED_USE_CLOUDINARY=true.
    """
    import cloudinary
    import cloudinary.uploader

    cloudinary.config(
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        secure=True,
    )
    result = cloudinary.uploader.upload(
        data,
        resource_type="raw",
        folder=settings.cloudinary_folder,
        filename_override=filename,
        use_filename=True,
        unique_filename=True,
        access_mode="public",
    )
    return result["secure_url"], result["public_id"]


async def seed(dry_run: bool = False) -> None:
    settings = get_settings()
    owners = _resolve_owners(settings.seed_owner_ids)
    mark = settings.seed_mark
    now = datetime.now(tz=UTC)

    # Print the SKIPPED list once at the start so it's in the log.
    for name, reason in SKIPPED.items():
        log.info("seed.skipped_manifest_entry", document=name, reason=reason)

    success = skipped_fetch = already_exists = 0

    for doc in SEED_DOCS:
        owner_id = owners[doc.tenant]
        filename = _filename_from(doc)
        log_ctx = {"title": doc.title[:60], "category": doc.category, "tenant": doc.tenant}

        # ---- Download ----
        try:
            result = await fetch_bytes(doc.url)
        except FetchError as exc:
            log.warning("seed.fetch_failed", reason=exc.reason, **log_ctx)
            skipped_fetch += 1
            continue

        log.info(
            "seed.fetched",
            size_kb=round(result.size_bytes / 1024),
            hash_prefix=result.content_hash[:8],
            **log_ctx,
        )

        # ---- Cloudinary upload (optional) ----
        file_url = doc.url          # default: store the .gov URL directly
        cloudinary_public_id: str | None = None

        if settings.seed_use_cloudinary:
            try:
                file_url, cloudinary_public_id = await _maybe_cloudinary_upload(
                    result.data, filename, result.mime, settings
                )
                log.info("seed.cloudinary_uploaded", public_id=cloudinary_public_id, **log_ctx)
            except Exception as exc:
                log.warning(
                    "seed.cloudinary_failed",
                    error=str(exc),
                    fallback="storing .gov URL directly",
                    **log_ctx,
                )
                file_url = doc.url

        # ---- Build the attachment subdoc ----
        # aiIntegrity* fields deliberately omitted — the future pipeline fills them.
        attachment: dict = {
            "_id": ObjectId(),
            "fileName": filename,
            "fileUrl": file_url,
            "size": result.size_bytes,
            "uploadedAt": now,
            "cloudinaryPublicId": cloudinary_public_id,
            "cloudinaryResourceType": "raw",
            "contentHash": result.content_hash,   # convenience; ignored by Next.js
            "seedSource": mark,                    # cleanup key
        }

        if dry_run:
            log.info(
                "seed.dry_run",
                owner_id=str(owner_id),
                plan_id=doc.plan_id,
                file_url=file_url[:60],
                **log_ctx,
            )
            success += 1
            continue

        # ---- Upsert EmergencyPlan; push attachment only if not already there ----
        col = plans()

        # Check if the attachment filename already exists under this plan.
        existing = col.find_one(
            {"ownerUserId": owner_id, "planId": doc.plan_id, "attachments.fileName": filename},
            {"_id": 1},
        )
        if existing:
            log.info("seed.already_exists", plan_id=doc.plan_id, file=filename)
            already_exists += 1
            continue

        col.update_one(
            {"ownerUserId": owner_id, "planId": doc.plan_id},
            {
                "$setOnInsert": {
                    "ownerUserId": owner_id,
                    "licenseId": None,
                    "planId": doc.plan_id,
                    "label": doc.title,
                    "overview": f"Seed continuity reference: {doc.title}.",
                    "category": doc.category,
                    "steps": [],
                    "seedSource": mark,
                    "createdAt": now,
                    "updatedAt": now,
                    "__v": 0,  # Mongoose version key — matches app-written docs
                },
                "$push": {"attachments": attachment},
            },
            upsert=True,
        )
        log.info("seed.written", plan_id=doc.plan_id, file=filename, **log_ctx)
        success += 1

    # ---- Summary ----
    mode = "[DRY RUN] " if dry_run else ""
    log.info(
        "seed.complete",
        mode=mode,
        seeded=success,
        skipped_fetch_error=skipped_fetch,
        already_existed=already_exists,
        total_in_manifest=len(SEED_DOCS),
    )
    if dry_run:
        log.info("seed.dry_run_note", msg="No changes were written to MongoDB.")


def main() -> None:
    configure_logging("INFO", json_logs=False)
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("\n⚠  DRY RUN — nothing will be written to MongoDB.\n")
    asyncio.run(seed(dry_run=dry_run))


if __name__ == "__main__":
    main()
