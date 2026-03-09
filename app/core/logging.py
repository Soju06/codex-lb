from __future__ import annotations

import logging
import os
from logging.config import dictConfig
from typing import Any

DEFAULT_LOG_LEVEL = "INFO"
_ALLOWED_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}


def resolve_log_level(raw_level: str | None) -> str:
    if raw_level is None:
        return DEFAULT_LOG_LEVEL
    normalized = raw_level.strip().upper()
    if normalized in _ALLOWED_LOG_LEVELS:
        return normalized
    return DEFAULT_LOG_LEVEL


def build_log_config(level: str | None = None) -> dict[str, Any]:
    resolved_level = resolve_log_level(level or os.getenv("CODEX_LB_LOG_LEVEL"))
    formatter = {
        "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        "datefmt": "%Y-%m-%d %H:%M:%S",
    }
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"standard": formatter},
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "stream": "ext://sys.stdout",
            }
        },
        "root": {"handlers": ["default"], "level": resolved_level},
        "loggers": {
            "uvicorn": {"level": resolved_level},
            "uvicorn.error": {"level": resolved_level, "propagate": True},
            "uvicorn.access": {"handlers": [], "level": "WARNING", "propagate": False},
        },
    }


def configure_logging(level: str | None = None) -> str:
    resolved_level = resolve_log_level(level or os.getenv("CODEX_LB_LOG_LEVEL"))
    dictConfig(build_log_config(resolved_level))
    logging.captureWarnings(True)
    return resolved_level
