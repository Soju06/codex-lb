from __future__ import annotations

import logging

import pytest

from app.core.logging import build_log_config, configure_logging, resolve_log_level

pytestmark = pytest.mark.unit


def test_resolve_log_level_falls_back_to_info_for_invalid_values() -> None:
    assert resolve_log_level("debug") == "DEBUG"
    assert resolve_log_level(" noisy ") == "INFO"
    assert resolve_log_level(None) == "INFO"


def test_build_log_config_disables_uvicorn_access_logs_and_uses_stdout_handler() -> None:
    config = build_log_config("debug")

    assert config["root"]["level"] == "DEBUG"
    assert config["handlers"]["default"]["stream"] == "ext://sys.stdout"
    assert config["loggers"]["uvicorn.access"] == {
        "handlers": [],
        "level": "WARNING",
        "propagate": False,
    }


def test_configure_logging_applies_root_level(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_LOG_LEVEL", "debug")

    resolved = configure_logging()

    assert resolved == "DEBUG"
    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG
