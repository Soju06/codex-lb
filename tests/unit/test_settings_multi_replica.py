from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config.settings import Settings

pytestmark = pytest.mark.unit


def test_settings_multi_replica_defaults():
    settings = Settings()
    assert settings.metrics_enabled is False
    assert settings.metrics_port == 9090
    assert settings.log_format == "text"
    assert settings.leader_election_enabled is False
    assert settings.leader_election_ttl_seconds == 600
    assert settings.circuit_breaker_enabled is False
    assert settings.circuit_breaker_failure_threshold == 5
    assert settings.circuit_breaker_recovery_timeout_seconds == 60
    assert settings.backpressure_max_concurrent_requests == 0
    assert settings.bulkhead_proxy_http_limit == settings.bulkhead_proxy_limit
    assert settings.bulkhead_proxy_websocket_limit == settings.bulkhead_proxy_limit
    assert settings.bulkhead_proxy_compact_limit == 16
    assert settings.proxy_token_refresh_limit == 64
    assert settings.proxy_upstream_websocket_connect_limit == 128
    assert settings.proxy_response_create_limit == 256
    assert settings.proxy_compact_response_create_limit == 64
    assert settings.proxy_downstream_websocket_idle_timeout_seconds == 120.0
    assert settings.max_sse_event_bytes == 16 * 1024 * 1024
    assert settings.proxy_refresh_failure_cooldown_seconds == 5.0
    assert settings.usage_refresh_auth_failure_cooldown_seconds == 300.0
    assert settings.otel_enabled is False
    assert settings.otel_exporter_endpoint == ""
    assert settings.shutdown_drain_timeout_seconds == 30
    assert settings.http_connector_limit == 100
    assert settings.http_connector_limit_per_host == 50


def test_settings_metrics_enabled_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_METRICS_ENABLED", "true")
    settings = Settings()
    assert settings.metrics_enabled is True


def test_settings_metrics_port_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_METRICS_PORT", "8080")
    settings = Settings()
    assert settings.metrics_port == 8080


def test_settings_rejects_metrics_port_2455(monkeypatch):
    monkeypatch.setenv("CODEX_LB_METRICS_PORT", "2455")
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert "metrics_port must not match the main application port (2455)" in str(exc_info.value)


def test_settings_log_format_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_LOG_FORMAT", "json")
    settings = Settings()
    assert settings.log_format == "json"


def test_settings_leader_election_enabled_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_LEADER_ELECTION_ENABLED", "true")
    settings = Settings()
    assert settings.leader_election_enabled is True


def test_settings_leader_election_ttl_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_LEADER_ELECTION_TTL_SECONDS", "60")
    settings = Settings()
    assert settings.leader_election_ttl_seconds == 60


def test_settings_circuit_breaker_enabled_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_CIRCUIT_BREAKER_ENABLED", "true")
    settings = Settings()
    assert settings.circuit_breaker_enabled is True


def test_settings_circuit_breaker_failure_threshold_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "10")
    settings = Settings()
    assert settings.circuit_breaker_failure_threshold == 10


def test_settings_circuit_breaker_recovery_timeout_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS", "120")
    settings = Settings()
    assert settings.circuit_breaker_recovery_timeout_seconds == 120


def test_settings_backpressure_max_concurrent_requests_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_BACKPRESSURE_MAX_CONCURRENT_REQUESTS", "50")
    settings = Settings()
    assert settings.backpressure_max_concurrent_requests == 50


def test_settings_split_bulkhead_limits_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_BULKHEAD_PROXY_HTTP_LIMIT", "40")
    monkeypatch.setenv("CODEX_LB_BULKHEAD_PROXY_WEBSOCKET_LIMIT", "25")
    monkeypatch.setenv("CODEX_LB_BULKHEAD_PROXY_COMPACT_LIMIT", "8")
    settings = Settings()
    assert settings.bulkhead_proxy_http_limit == 40
    assert settings.bulkhead_proxy_websocket_limit == 25
    assert settings.bulkhead_proxy_compact_limit == 8


def test_settings_split_bulkhead_limits_allow_explicit_zero(monkeypatch):
    monkeypatch.setenv("CODEX_LB_BULKHEAD_PROXY_HTTP_LIMIT", "0")
    monkeypatch.setenv("CODEX_LB_BULKHEAD_PROXY_WEBSOCKET_LIMIT", "0")
    monkeypatch.setenv("CODEX_LB_BULKHEAD_PROXY_COMPACT_LIMIT", "0")
    settings = Settings()
    assert settings.bulkhead_proxy_http_limit == 0
    assert settings.bulkhead_proxy_websocket_limit == 0
    assert settings.bulkhead_proxy_compact_limit == 0


def test_settings_work_admission_limits_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_PROXY_TOKEN_REFRESH_LIMIT", "7")
    monkeypatch.setenv("CODEX_LB_PROXY_UPSTREAM_WEBSOCKET_CONNECT_LIMIT", "9")
    monkeypatch.setenv("CODEX_LB_PROXY_RESPONSE_CREATE_LIMIT", "11")
    monkeypatch.setenv("CODEX_LB_PROXY_COMPACT_RESPONSE_CREATE_LIMIT", "3")
    settings = Settings()
    assert settings.proxy_token_refresh_limit == 7
    assert settings.proxy_upstream_websocket_connect_limit == 9
    assert settings.proxy_response_create_limit == 11
    assert settings.proxy_compact_response_create_limit == 3


def test_settings_proxy_downstream_websocket_idle_timeout_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_PROXY_DOWNSTREAM_WEBSOCKET_IDLE_TIMEOUT_SECONDS", "45")
    settings = Settings()
    assert settings.proxy_downstream_websocket_idle_timeout_seconds == 45.0


def test_settings_otel_enabled_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_OTEL_ENABLED", "true")
    settings = Settings()
    assert settings.otel_enabled is True


def test_settings_otel_exporter_endpoint_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_OTEL_EXPORTER_ENDPOINT", "http://localhost:4317")
    settings = Settings()
    assert settings.otel_exporter_endpoint == "http://localhost:4317"


def test_settings_shutdown_drain_timeout_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_SHUTDOWN_DRAIN_TIMEOUT_SECONDS", "60")
    settings = Settings()
    assert settings.shutdown_drain_timeout_seconds == 60


def test_settings_http_connector_limit_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_HTTP_CONNECTOR_LIMIT", "200")
    settings = Settings()
    assert settings.http_connector_limit == 200


def test_settings_http_connector_limit_per_host_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_HTTP_CONNECTOR_LIMIT_PER_HOST", "75")
    settings = Settings()
    assert settings.http_connector_limit_per_host == 75
