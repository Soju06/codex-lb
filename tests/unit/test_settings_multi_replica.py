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
    assert settings.disable_bootstrap_token is False
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
    assert "metrics_port must not be 2455" in str(exc_info.value)


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


def test_settings_disable_bootstrap_token_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_DISABLE_BOOTSTRAP_TOKEN", "true")
    settings = Settings()
    assert settings.disable_bootstrap_token is True


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
