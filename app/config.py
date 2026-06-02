"""Application configuration.

All settings are environment-driven (see `.env.example`). Loaded once and cached
via `get_settings()`. Nothing here reaches out to a network — construction is cheap
and safe to call at import time.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
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

    # ---- Auth (Next.js -> this service) --------------------------------
    python_integrity_token: str = Field(default="", description="Shared bearer token")
    hmac_secret: str = Field(default="", description="Optional HMAC body-signing secret")

    # ---- OpenAI ---------------------------------------------------------
    openai_api_key: str = ""
    openai_embed_model: str = "text-embedding-3-small"
    openai_summary_model: str = "gpt-4o-mini"

    # ---- Weaviate (vector store) ---------------------------------------
    weaviate_url: str = ""
    weaviate_api_key: str = ""

    # ---- Postgres (state / cache / logs) -------------------------------
    database_url: str = ""

    # ---- MongoDB (offline prep/seed scripts only — never the request path) ----
    # Targets the tenant-aware `continuityplans` / `continuityauditreports`
    # collections. The live FastAPI request path stays Mongo-free (ARCHITECTURE §1.3).
    # Point MONGODB_URI at a DEV/STAGING cluster. Use a read-only user for
    # the Phase-B prepare.py; seed_mongo.py (which writes) needs a write user.
    mongodb_uri: str = ""
    mongodb_db: str = "ready2go"  # default DB name — PROJECT_CONTEXT §4

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
    # Composite integrity-signal weights (must sum to ~1.0).
    weight_content: float = 0.40
    weight_name: float = 0.15
    weight_category: float = 0.20
    weight_quality: float = 0.15
    weight_duplication: float = 0.10
    # Status banding thresholds (0..100).
    band_in_sync: int = 71
    band_reviewing: int = 41
    # Borderline band that triggers the optional LLM judge, e.g. "60,72".
    llm_judge_band: str = "60,72"

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"production", "prod"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached, process-wide settings instance."""
    return Settings()
