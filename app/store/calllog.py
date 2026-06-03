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

import contextlib
from datetime import UTC, datetime
from typing import Any

from app.store.models import get_log_col


def append(row: dict[str, Any]) -> None:
    """Insert one call-log row.  Never raises — log failures are swallowed."""
    with contextlib.suppress(Exception):
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
