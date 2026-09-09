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
        "CODEX_LB_REQUEST_LOG_RETENTION_DAYS": "90",
        "CODEX_LB_WARMUP_MODEL": "gpt-5.4-nano",
        "CODEX_LB_TRACE": "shape",  # current setting, never reported
        "UNRELATED": "1",
    }
    with caplog.at_level(logging.WARNING, logger="app.core.config.settings"):
        found = warn_removed_settings(environ)

    assert found == ["CODEX_LB_REQUEST_LOG_RETENTION_DAYS", "CODEX_LB_WARMUP_MODEL"]
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "CODEX_LB_REQUEST_LOG_RETENTION_DAYS" in message
    assert "CODEX_LB_WARMUP_MODEL" in message
    assert "PRINCIPLES.md P2" in message
    assert "#1340" in message
    # Values must never be logged.
    assert "90" not in message
    assert "gpt-5.4-nano" not in message


def test_warn_removed_settings_is_silent_when_nothing_is_set(caplog):
    with caplog.at_level(logging.WARNING, logger="app.core.config.settings"):
        found = warn_removed_settings({"CODEX_LB_TRACE": "shape"})

    assert found == []
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_warn_removed_settings_scans_env_files(tmp_path, monkeypatch, caplog):
    env_file = tmp_path / ".env.local"
    env_file.write_text("CODEX_LB_OPENAI_CACHE_AFFINITY_MAX_AGE_SECONDS=64\n", encoding="utf-8")
    monkeypatch.setattr("app.core.config.settings.ENV_FILES", (tmp_path / ".env", env_file))
    monkeypatch.delenv("CODEX_LB_OPENAI_CACHE_AFFINITY_MAX_AGE_SECONDS", raising=False)

    with caplog.at_level(logging.WARNING, logger="app.core.config.settings"):
        found = warn_removed_settings()

    assert found == ["CODEX_LB_OPENAI_CACHE_AFFINITY_MAX_AGE_SECONDS"]
    assert "64" not in caplog.text


def test_removed_settings_tuple_covers_the_current_warning_batch():
    # Only the batches removed in the most recent release stay listed; names
    # whose one-release warning window has passed are pruned. Six names from
    # remove-dead-env-settings + CODEX_LB_UPSTREAM_STREAM_TRANSPORT
    # (remove-upstream-stream-transport-env: the dashboard owns the value).
    assert len(_REMOVED_SETTINGS) == 7
    assert all(name.startswith("CODEX_LB_") for name in _REMOVED_SETTINGS)
    assert len(set(_REMOVED_SETTINGS)) == len(_REMOVED_SETTINGS)


def test_dead_env_settings_are_listed_and_ignored(monkeypatch):
    removed_names = (
        "CODEX_LB_REQUEST_LOG_RETENTION_DAYS",
        "CODEX_LB_USAGE_HISTORY_RETENTION_DAYS",
        "CODEX_LB_HTTP_DOWNSTREAM_TRANSPORT_POLICY",
        "CODEX_LB_OPENAI_CACHE_AFFINITY_MAX_AGE_SECONDS",
        "CODEX_LB_WARMUP_MODEL",
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_GATEWAY_SAFE_MODE",
    )
    for name in removed_names:
        assert name in _REMOVED_SETTINGS

    monkeypatch.setenv("CODEX_LB_REQUEST_LOG_RETENTION_DAYS", "7")  # would have failed the old floor
    monkeypatch.setenv("CODEX_LB_HTTP_DOWNSTREAM_TRANSPORT_POLICY", "sometimes")  # old Literal rejected this
    monkeypatch.setenv("CODEX_LB_WARMUP_MODEL", "   ")  # old validator rejected blanks
    monkeypatch.setenv("CODEX_LB_OPENAI_CACHE_AFFINITY_MAX_AGE_SECONDS", "0")
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_GATEWAY_SAFE_MODE", "true")
    settings = Settings()
    for name in removed_names:
        assert not hasattr(settings, name.removeprefix("CODEX_LB_").lower())
    found = warn_removed_settings(
        {
            "CODEX_LB_USAGE_HISTORY_RETENTION_DAYS": "45",
            "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_GATEWAY_SAFE_MODE": "true",
        }
    )
    assert found == [
        "CODEX_LB_USAGE_HISTORY_RETENTION_DAYS",
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_GATEWAY_SAFE_MODE",
    ]


def test_warn_removed_settings_matches_names_case_insensitively(caplog):
    # pydantic-settings read the former fields case-insensitively, so a
    # lowercase declaration that used to take effect must still be reported
    # (under its canonical name).
    with caplog.at_level(logging.WARNING, logger="app.core.config.settings"):
        found = warn_removed_settings({"codex_lb_warmup_model": "gpt-5.4-nano"})
    assert found == ["CODEX_LB_WARMUP_MODEL"]
    assert "CODEX_LB_WARMUP_MODEL" in caplog.text
    assert "gpt-5.4-nano" not in caplog.text


def test_expired_removed_names_are_silently_ignored(monkeypatch, caplog):
    # Phases 1-4 (July 2026) had their warning release; the names stay inert
    # via extra="ignore" but no longer trip the startup warning.
    expired = {
        "CODEX_LB_AUTH_BASE_URL": "https://auth.example.test",
        "CODEX_LB_QUOTA_PLANNER_TICK_SECONDS": "60",
        "CODEX_LB_DATABASE_POOL_RECYCLE_SECONDS": "600",
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_CANARY_PERCENT": "25.0",
    }
    for name, value in expired.items():
        monkeypatch.setenv(name, value)
    settings = Settings()
    assert not hasattr(settings, "auth_base_url")
    assert not hasattr(settings, "quota_planner_tick_seconds")
    with caplog.at_level(logging.WARNING, logger="app.core.config.settings"):
        assert warn_removed_settings(expired) == []
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_upstream_stream_transport_env_is_removed_and_ignored(monkeypatch):
    assert "CODEX_LB_UPSTREAM_STREAM_TRANSPORT" in _REMOVED_SETTINGS
    monkeypatch.setenv("CODEX_LB_UPSTREAM_STREAM_TRANSPORT", "http")
    settings = Settings()
    assert not hasattr(settings, "upstream_stream_transport")
    assert "CODEX_LB_UPSTREAM_STREAM_TRANSPORT" in warn_removed_settings({"CODEX_LB_UPSTREAM_STREAM_TRANSPORT": "http"})
