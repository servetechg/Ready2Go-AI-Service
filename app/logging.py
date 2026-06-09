"""Structured logging setup (structlog + stdlib rotating file handlers).

Two log sinks:
  - Console  — pretty in dev, JSON lines in production (visible via docker logs).
  - logs/app.log   — RotatingFileHandler, all levels, JSON lines.
  - logs/error.log — RotatingFileHandler, WARNING+, JSON lines (fast triage).

Call `configure_logging()` once at startup, then obtain loggers via
`structlog.get_logger(__name__)`.

Request correlation IDs are injected by app.middleware and show up in every
log line automatically via structlog.contextvars.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys

import structlog


def configure_logging(
    level: str = "INFO",
    *,
    json_logs: bool = False,
    log_dir: str = "logs",
    log_to_file: bool = True,
    log_max_bytes: int = 10 * 1024 * 1024,
    log_backups: int = 5,
) -> None:
    """Configure stdlib logging + structlog.

    The console sink is always on. When *log_to_file* is True, two rotating file
    sinks are also added (``app.log`` all-levels and ``error.log`` WARNING+);
    when False, logging is console-only and nothing is written to disk.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    if log_to_file:
        os.makedirs(log_dir, exist_ok=True)

    # Shared structlog pre-processors (run before the final renderer).
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),
    ]

    # Format: JSON lines for files (always), human-readable for dev console.
    json_renderer = structlog.processors.JSONRenderer()
    console_renderer: structlog.types.Processor = (
        json_renderer if json_logs else structlog.dev.ConsoleRenderer()
    )

    # ---------------------------------------------------------------------------
    # stdlib root logger — handlers write to console + two rotating files.
    # ---------------------------------------------------------------------------
    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Remove any handlers already attached (e.g. from a previous configure call).
    for h in root.handlers[:]:
        root.removeHandler(h)

    # Console handler (always on).
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    root.addHandler(console_handler)

    # Rotating file sinks — only when LOG_TO_FILE is enabled.
    file_handlers: list[logging.Handler] = []
    if log_to_file:
        # Rotating app.log — all levels, JSON lines.
        app_log_path = os.path.join(log_dir, "app.log")
        file_handler = logging.handlers.RotatingFileHandler(
            app_log_path,
            maxBytes=log_max_bytes,
            backupCount=log_backups,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        root.addHandler(file_handler)
        file_handlers.append(file_handler)

        # Rotating error.log — WARNING+ only for fast triage.
        error_log_path = os.path.join(log_dir, "error.log")
        error_handler = logging.handlers.RotatingFileHandler(
            error_log_path,
            maxBytes=log_max_bytes,
            backupCount=log_backups,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.WARNING)
        root.addHandler(error_handler)
        file_handlers.append(error_handler)

    # ---------------------------------------------------------------------------
    # structlog — routes through stdlib so every handler above receives events.
    # ---------------------------------------------------------------------------
    structlog.configure(
        processors=[
            *shared_processors,
            # Bridge: pass structlog events into stdlib logging so our handlers fire.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # The ProcessorFormatter drives the final render for the file handlers (JSON).
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            json_renderer,          # files always JSON
        ],
        foreign_pre_chain=shared_processors,
    )
    for handler in file_handlers:
        handler.setFormatter(formatter)

    # Console uses its own renderer (pretty or JSON per env).
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            console_renderer,
        ],
        foreign_pre_chain=shared_processors,
    )
    console_handler.setFormatter(console_formatter)
