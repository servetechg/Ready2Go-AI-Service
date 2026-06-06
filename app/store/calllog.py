"""Append-only AI call audit trail.

Every OpenAI embedding or chat call writes one row here via the log_call
callback that embeddings.py and llm/client.py accept.

Schema (ai_call_log):
  ts:             datetime (UTC)
  kind:           "embed" | "chat"
  attachmentId:   str (may be empty for non-attachment calls)
  model:          model name string
  tokens:         total tokens used (0 on failure)
  latency_ms:     wall-clock milliseconds
  success:        bool
  error:          str | null
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from app.store.models import get_log_col

log = structlog.get_logger(__name__)


def append(row: dict[str, Any]) -> None:
    """Insert one call-log row.

    Non-fatal by design: the AI cost/audit trail must never break a live request.
    But the failure is no longer fully silent — it is logged at WARNING so a
    broken ai_call_log sink (e.g. Mongo down) is visible instead of hidden.
    """
    try:
        get_log_col().insert_one({
            "ts":           datetime.now(UTC),
            "kind":         row.get("kind", ""),
            "attachmentId": row.get("attachmentId", ""),
            "model":        row.get("model", ""),
            "tokens":       row.get("tokens", 0),
            "latency_ms":   row.get("latency_ms", 0),
            "success":      row.get("success", False),
            "error":        row.get("error"),
        })
    except Exception as exc:
        log.warning(
            "calllog.append_failed",
            detail=(
                "Could not write a row to the ai_call_log audit trail. The request "
                "itself is unaffected (this is best-effort cost/audit logging), but "
                "AI call metrics will be incomplete until the log sink recovers. "
                "Cause below."
            ),
            kind=row.get("kind", ""),
            error=str(exc),
        )
