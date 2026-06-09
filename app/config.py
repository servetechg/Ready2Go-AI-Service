"""Application configuration.

All settings are environment-driven (see `.env.example`). Loaded once and cached
via `get_settings()`. Nothing here reaches out to a network — construction is cheap
and safe to call at import time.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed view over the service's environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Runtime --------------------------------------------------------
    env: str = Field(default="development", description="development | staging | production")
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    reload: bool = False
    request_timeout_s: float = 25.0

    # ---- Async analyze (background pipeline + polling) ------------------
    # /v1/integrity/analyze returns 202 immediately and runs the pipeline in the
    # background; Next.js polls /v1/integrity/result/{attachmentId}.
    # analyze_concurrency caps how many heavy pipelines run at once (memory guard);
    # extra requests queue. analyze_timeout_s bounds a single background run (more
    # generous than request_timeout_s since no client is blocked). job_stale_seconds
    # is the age past which a still-"processing" job is reaped to "error" on startup.
    analyze_concurrency: int = 3
    analyze_timeout_s: float = 300.0
    job_stale_seconds: int = 600

    # ---- Storage paths & persistence -----------------------------------
    # DATA_DIR is the base directory for everything this service persists. Each
    # artifact below defaults to a sub-folder of DATA_DIR but can be overridden
    # individually to any absolute path (another drive / NFS / cloud mount).
    # Default "." keeps today's behaviour (logs under ./logs).
    data_dir: str = "."

    # ---- Logging --------------------------------------------------------
    # log_dir defaults to "{data_dir}/logs" (filled by _derive_paths) unless an
    # explicit LOG_DIR is set, which always wins.
    log_dir: str | None = None
    # LOG_TO_FILE=false → console-only logging, no app.log / error.log on disk.
    log_to_file: bool = True
    log_max_bytes: int = 10 * 1024 * 1024   # 10 MB per file
    log_backups: int = 5

    # ---- Auth (Next.js -> this service) --------------------------------
    python_integrity_token: str = Field(default="", description="Shared bearer token")
    hmac_secret: str = Field(default="", description="Optional HMAC body-signing secret")

    # ---- OpenAI ---------------------------------------------------------
    openai_api_key: str = ""
    openai_embed_model: str = "text-embedding-3-small"
    openai_summary_model: str = "gpt-4o-mini"

    @field_validator("openai_embed_model", mode="before")
    @classmethod
    def _embed_model_default(cls, v: str | None) -> str:
        # A blank env value (OPENAI_EMBED_MODEL=) must not override the default
        # with an empty string — that would make OpenAI reject the request (400).
        return (v or "").strip() or "text-embedding-3-small"

    @field_validator("openai_summary_model", mode="before")
    @classmethod
    def _summary_model_default(cls, v: str | None) -> str:
        return (v or "").strip() or "gpt-4o-mini"

    # ---- Weaviate (vector store) ---------------------------------------
    weaviate_url: str = ""
    weaviate_api_key: str = ""
    # gRPC port for the Weaviate v4 client. HTTP port defaults to the one parsed
    # from WEAVIATE_URL; set weaviate_http_port to override (e.g. managed/cloud).
    weaviate_grpc_port: int = 50051
    weaviate_http_port: int | None = None

    # ---- MongoDB -------------------------------------------------------
    # ONE database (`mongodb_db`, default "ready2go"), TWO disjoint uses:
    #   1. Python's OWN request-path state lives in dedicated `ai_*` COLLECTIONS
    #      in this same DB: `ai_analysis_cache` / `ai_audit_state` / `ai_call_log`.
    #      Next.js never reads them; Python never writes the app's domain docs
    #      (`continuityplans` / `continuityauditreports`) — Next.js owns those
    #      writes (ARCHITECTURE §4.2).
    #   2. The offline prep/seed scripts (scripts/prep/*) read/write the app's
    #      tenant-aware collections; point MONGODB_URI at a DEV/STAGING cluster
    #      for those.
    mongodb_uri: str = ""
    # One DB for app docs + Python's ai_* collections (PROJECT_CONTEXT §4).
    mongodb_db: str = "ready2go"

    # ---- Cloudinary (optional — seed upload only) ----------------------
    # Only consulted when SEED_USE_CLOUDINARY=true. Reuses the same Cloudinary
    # account as the Next.js app so seed fileUrls match production shape exactly.
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""
    cloudinary_folder: str = "earthquick/emergency-plans"

    # ---- Seed control (dev-only test corpus) ---------------------------
    # SEED_OWNER_IDS: comma-separated REAL subadmin User._id hex strings.
    # When set, seed data is visible when you log in as those subadmins in the UI.
    # When blank, reserved synthetic ObjectIds are used (safe, but not loginnable).
    seed_owner_ids: str = ""
    seed_use_cloudinary: bool = False
    # All seed artifacts carry this marker — cleanup.py deletes by it exclusively.
    seed_mark: str = "gov-coop-bcp-2026"

    # ---- Model / scoring knobs -----------------------------------------
    model_version: str = "integrity-v1"
    max_chunks_per_doc: int = 200
    audit_sample_cap: int = 25
    # Fallback for the org audit: when AUDIT_SAMPLE_CAP=0 (send the full corpus to
    # the audit LLM) and that call fails — typically because the payload exceeds
    # the model's context window — the audit automatically retries with only the
    # worst-scoring AUDIT_FALLBACK_CAP documents, so a usable AI narrative is still
    # produced. The response is flagged degraded=true to signal the fallback.
    audit_fallback_cap: int = 50
    # Composite integrity-signal weights (must sum to ~1.0).
    weight_content: float = 0.50
    weight_name: float = 0.19
    weight_quality: float = 0.19
    weight_duplication: float = 0.12
    # Status banding thresholds (0..100), named after the new status vocabulary
    # (Compliant / Under Review). Only BAND_COMPLIANT / BAND_UNDER_REVIEW are
    # accepted — the old names (BAND_IN_SYNC / BAND_REVIEWING) are no longer read.
    band_compliant: int = 71
    band_under_review: int = 41
    # Borderline band that triggers the optional LLM judge, e.g. "60,72".
    llm_judge_band: str = "60,72"

    # ---- Document parser backend ---------------------------------------
    # "basic" (default) uses pdfplumber/pypdf/docx/xlsx.
    # Switch to "liteparse" via env when a drop-in LiteParse adapter is added.
    parser_backend: str = "basic"

    @model_validator(mode="after")
    def _derive_paths(self) -> Settings:
        # Fill artifact paths from DATA_DIR when not set explicitly. An explicit
        # LOG_DIR (the per-artifact override) always wins.
        if not self.log_dir:
            self.log_dir = os.path.join(self.data_dir, "logs")
        return self

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"production", "prod"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached, process-wide settings instance."""
    return Settings()


def validate_production_secrets(settings: Settings) -> None:
    """Raise RuntimeError if required production secrets are absent.

    Call this at startup when env == production so the service refuses to
    boot rather than silently opening auth holes or crashing mid-request.
    """
    if not settings.is_production:
        return

    missing = []
    if not settings.openai_api_key:
        missing.append("OPENAI_API_KEY")
    if not settings.weaviate_url:
        missing.append("WEAVIATE_URL")
    if not settings.mongodb_uri:
        missing.append("MONGODB_URI")
    if not settings.python_integrity_token:
        missing.append("PYTHON_INTEGRITY_TOKEN")

    if missing:
        raise RuntimeError(
            f"Production startup blocked — required secrets missing: {', '.join(missing)}. "
            "Set them in the environment before starting the service."
        )
