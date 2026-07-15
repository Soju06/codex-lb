"""Tests for the CODEX_LB_TRACE channels and the removed-settings warning.

Introduced by the ``reduce-settings-surface-phase-1`` change (issue #1340).
"""

from __future__ import annotations

import logging

import pytest

from app.core.config.settings import _REMOVED_SETTINGS, Settings, warn_removed_settings

pytestmark = pytest.mark.unit


def test_trace_defaults_to_no_channels():
    settings = Settings()
    assert settings.trace == ""
    assert settings.trace_channels == frozenset()


def test_trace_parses_comma_separated_channels(monkeypatch):
    monkeypatch.setenv("CODEX_LB_TRACE", "shape,upstream_payload")
    settings = Settings()
    assert settings.trace_channels == frozenset({"shape", "upstream_payload"})


def test_trace_normalizes_whitespace_case_and_empty_entries():
    settings = Settings(trace=" Shape , SERVICE_TIER ,, payload ,")
    assert settings.trace_channels == frozenset({"shape", "service_tier", "payload"})


def test_trace_channels_is_cached_per_settings_instance():
    settings = Settings(trace="shape")
    assert settings.trace_channels is settings.trace_channels


def test_removed_log_settings_env_vars_are_ignored(monkeypatch):
    monkeypatch.setenv("CODEX_LB_LOG_PROXY_REQUEST_SHAPE", "true")
    monkeypatch.setenv("CODEX_LB_LOG_UPSTREAM_REQUEST_PAYLOAD", "true")
    settings = Settings()
    assert settings.trace_channels == frozenset()
    assert not hasattr(settings, "log_proxy_request_shape")


def test_warn_removed_settings_logs_one_warning_listing_found_names(caplog):
    environ = {
        "CODEX_LB_AUTH_BASE_URL": "https://auth.example.test",
        "CODEX_LB_TOKEN_REFRESH_CLAIM_WAIT_SECONDS": "9.0",
        "CODEX_LB_TRACE": "shape",  # current setting, never reported
        "UNRELATED": "1",
    }
    with caplog.at_level(logging.WARNING, logger="app.core.config.settings"):
        found = warn_removed_settings(environ)

    assert found == ["CODEX_LB_AUTH_BASE_URL", "CODEX_LB_TOKEN_REFRESH_CLAIM_WAIT_SECONDS"]
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "CODEX_LB_AUTH_BASE_URL" in message
    assert "CODEX_LB_TOKEN_REFRESH_CLAIM_WAIT_SECONDS" in message
    assert "PRINCIPLES.md P2" in message
    assert "#1340" in message
    # Values must never be logged.
    assert "auth.example.test" not in message
    assert "9.0" not in message


def test_warn_removed_settings_is_silent_when_nothing_is_set(caplog):
    with caplog.at_level(logging.WARNING, logger="app.core.config.settings"):
        found = warn_removed_settings({"CODEX_LB_TRACE": "shape"})

    assert found == []
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_warn_removed_settings_scans_env_files(tmp_path, monkeypatch, caplog):
    env_file = tmp_path / ".env.local"
    env_file.write_text("CODEX_LB_BULKHEAD_PROXY_HTTP_LIMIT=64\n", encoding="utf-8")
    monkeypatch.setattr("app.core.config.settings.ENV_FILES", (tmp_path / ".env", env_file))
    monkeypatch.delenv("CODEX_LB_BULKHEAD_PROXY_HTTP_LIMIT", raising=False)

    with caplog.at_level(logging.WARNING, logger="app.core.config.settings"):
        found = warn_removed_settings()

    assert found == ["CODEX_LB_BULKHEAD_PROXY_HTTP_LIMIT"]
    assert "64" not in caplog.text


def test_removed_settings_tuple_covers_all_five_groups():
    assert len(_REMOVED_SETTINGS) == 24
    assert all(name.startswith("CODEX_LB_") for name in _REMOVED_SETTINGS)
