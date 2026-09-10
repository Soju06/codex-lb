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
    # (remove-upstream-stream-transport-env: the dashboard owns the value)
    # + 27 never-tuned core tunables (constantize-core-tunables)
    # + seven K2 bridge names (constantize-session-bridge-tunables).
    assert len(_REMOVED_SETTINGS) == 34 + 7
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


CONSTANTIZED_CORE_TUNABLE_ENV_NAMES = (
    "CODEX_LB_UPSTREAM_COMPACT_TIMEOUT_SECONDS",
    "CODEX_LB_MAX_SSE_EVENT_BYTES",
    "CODEX_LB_UPSTREAM_RESPONSE_CREATE_MAX_BYTES",
    "CODEX_LB_OAUTH_TIMEOUT_SECONDS",
    "CODEX_LB_TOKEN_REFRESH_TIMEOUT_SECONDS",
    "CODEX_LB_TOKEN_REFRESH_CLAIM_TTL_SECONDS",
    "CODEX_LB_PROXY_REFRESH_FAILURE_COOLDOWN_SECONDS",
    "CODEX_LB_PROXY_ADMISSION_WAIT_TIMEOUT_SECONDS",
    "CODEX_LB_USAGE_FETCH_TIMEOUT_SECONDS",
    "CODEX_LB_USAGE_FETCH_MAX_RETRIES",
    "CODEX_LB_USAGE_REFRESH_ENABLED",
    "CODEX_LB_USAGE_REFRESH_INTERVAL_SECONDS",
    "CODEX_LB_USAGE_REFRESH_AUTH_FAILURE_COOLDOWN_SECONDS",
    "CODEX_LB_LIVE_USAGE_INGESTION_ENABLED",
    "CODEX_LB_RATE_LIMIT_RESET_CREDITS_REFRESH_INTERVAL_SECONDS",
    "CODEX_LB_STICKY_SESSION_CLEANUP_ENABLED",
    "CODEX_LB_QUOTA_PLANNER_SCHEDULER_ENABLED",
    "CODEX_LB_MODEL_REGISTRY_ENABLED",
    "CODEX_LB_MAX_DECOMPRESSED_BODY_BYTES",
    "CODEX_LB_MAX_DECOMPRESSED_RESPONSES_BODY_BYTES",
    "CODEX_LB_IMAGE_INLINE_FETCH_ENABLED",
    "CODEX_LB_IMAGE_INLINE_ALLOWED_HOSTS",
    "CODEX_LB_IMAGES_DEFAULT_MODEL",
    "CODEX_LB_OPENAI_PROMPT_CACHE_KEY_DERIVATION_ENABLED",
    "CODEX_LB_PROXY_TOKEN_REFRESH_LIMIT",
    "CODEX_LB_PROXY_UPSTREAM_WEBSOCKET_CONNECT_LIMIT",
    "CODEX_LB_PROXY_COMPACT_RESPONSE_CREATE_LIMIT",
)


def test_constantized_core_tunables_are_listed_and_ignored(monkeypatch):
    assert len(CONSTANTIZED_CORE_TUNABLE_ENV_NAMES) == 27
    for name in CONSTANTIZED_CORE_TUNABLE_ENV_NAMES:
        assert name in _REMOVED_SETTINGS
        # Values that the removed validators used to reject must be inert now.
        monkeypatch.setenv(name, "not-a-number")
    settings = Settings()
    for name in CONSTANTIZED_CORE_TUNABLE_ENV_NAMES:
        assert not hasattr(settings, name.removeprefix("CODEX_LB_").lower())
    # Kept on purpose: still a real setting (see the constantize-core-tunables change).
    assert settings.token_refresh_interval_days == 8
    assert settings.rate_limit_reset_credits_refresh_enabled is True
    assert settings.proxy_response_create_limit == 256


# K2 bridge (constantize-session-bridge-tunables)
_K2_BRIDGE_REMOVED_NAMES = (
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_IDLE_TTL_SECONDS",
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_IDLE_TTL_SECONDS",
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_STUCK_GATE_RETIRE_AFTER_SECONDS",
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ANCHOR_POISON_FAILURE_THRESHOLD",
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_SERVER_RECOVERY_MAX_ATTEMPTS",
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CLEAN_CLOSE_RETRY_JITTER_MAX_SECONDS",
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_OPERATION_LEDGER_ENABLED",
)


def test_constantized_bridge_tunables_are_listed_and_ignored(monkeypatch, caplog):
    for name in _K2_BRIDGE_REMOVED_NAMES:
        assert name in _REMOVED_SETTINGS
        monkeypatch.setenv(name, "1")

    settings = Settings()
    for name in _K2_BRIDGE_REMOVED_NAMES:
        assert not hasattr(settings, name.removeprefix("CODEX_LB_").lower())

    with caplog.at_level(logging.WARNING, logger="app.core.config.settings"):
        found = warn_removed_settings({name: "1" for name in _K2_BRIDGE_REMOVED_NAMES})

    assert found == list(_K2_BRIDGE_REMOVED_NAMES)


# end K2 bridge
