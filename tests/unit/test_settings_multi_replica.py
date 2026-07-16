from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import settings as settings_module
from app.core.config.settings import Settings

pytestmark = pytest.mark.unit


def test_settings_multi_replica_defaults():
    settings = Settings()
    assert settings.metrics_enabled is False
    assert settings.metrics_port == 9090
    assert settings.log_format == "text"
    assert settings.leader_election_enabled is True
    assert settings.leader_election_ttl_seconds == 60
    assert settings.auth_guardian_enabled is False
    assert settings.circuit_breaker_enabled is False
    assert settings.backpressure_max_concurrent_requests == 0
    assert settings.bulkhead_proxy_limit == 512
    assert settings.bulkhead_dashboard_limit == 50
    assert settings.proxy_token_refresh_limit == 64
    assert settings.proxy_upstream_websocket_connect_limit == 128
    assert settings.proxy_response_create_limit == 256
    assert settings.proxy_compact_response_create_limit == 64
    assert settings.compact_request_budget_seconds == 180.0
    assert settings.proxy_request_budget_seconds == 600.0
    assert settings.http_responses_session_bridge_request_budget_seconds == 7200.0
    assert settings.stream_idle_timeout_seconds == 7200.0
    assert settings.proxy_downstream_websocket_idle_timeout_seconds == 120.0
    assert settings.http_responses_stream_request_budget_seconds == 7200.0
    assert settings.max_sse_event_bytes == 16 * 1024 * 1024
    assert settings.proxy_refresh_failure_cooldown_seconds == 5.0
    assert settings.conversation_archive_queue_max_bytes == 256 * 1024 * 1024
    assert settings.usage_refresh_auth_failure_cooldown_seconds == 300.0
    assert settings.otel_enabled is False
    assert settings.otel_exporter_endpoint == ""
    assert settings.shutdown_drain_timeout_seconds == 30
    assert settings.http_connector_limit == 100
    assert settings.http_connector_limit_per_host == 50
    assert settings.http_downstream_transport_policy == "smart"


@pytest.mark.parametrize("policy", ["smart", "always_http", "always_websocket", "pinned"])
def test_settings_http_downstream_transport_policy_from_env(monkeypatch, policy: str):
    monkeypatch.setenv("CODEX_LB_HTTP_DOWNSTREAM_TRANSPORT_POLICY", policy)
    settings = Settings()
    assert settings.http_downstream_transport_policy == policy


def test_settings_http_downstream_transport_policy_rejects_unknown(monkeypatch):
    monkeypatch.setenv("CODEX_LB_HTTP_DOWNSTREAM_TRANSPORT_POLICY", "sometimes")
    with pytest.raises(ValidationError):
        Settings()


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


def test_settings_conversation_archive_queue_max_bytes_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_CONVERSATION_ARCHIVE_QUEUE_MAX_BYTES", "16777216")
    settings = Settings()
    assert settings.conversation_archive_queue_max_bytes == 16 * 1024 * 1024


def test_settings_leader_election_enabled_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_LEADER_ELECTION_ENABLED", "false")
    settings = Settings()
    assert settings.leader_election_enabled is False


def test_settings_leader_election_ttl_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_LEADER_ELECTION_TTL_SECONDS", "90")
    settings = Settings()
    assert settings.leader_election_ttl_seconds == 90


def test_settings_leader_election_ttl_rejects_below_minimum(monkeypatch):
    monkeypatch.setenv("CODEX_LB_LEADER_ELECTION_TTL_SECONDS", "2")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_circuit_breaker_enabled_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_CIRCUIT_BREAKER_ENABLED", "true")
    settings = Settings()
    assert settings.circuit_breaker_enabled is True


def test_settings_circuit_breaker_tuning_env_overrides_are_removed(monkeypatch):
    # Failure threshold and recovery timeout became fixed constants in
    # app/core/resilience/circuit_breaker.py (issue #1340 phase 2); the env
    # vars are ignored and the fields no longer exist.
    monkeypatch.setenv("CODEX_LB_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "10")
    monkeypatch.setenv("CODEX_LB_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS", "120")
    settings = Settings()
    assert not hasattr(settings, "circuit_breaker_failure_threshold")
    assert not hasattr(settings, "circuit_breaker_recovery_timeout_seconds")


def test_settings_backpressure_max_concurrent_requests_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_LB_BACKPRESSURE_MAX_CONCURRENT_REQUESTS", "50")
    settings = Settings()
    assert settings.backpressure_max_concurrent_requests == 50


def test_settings_split_bulkhead_env_overrides_are_removed(monkeypatch):
    # Per-class bulkhead overrides were removed (issue #1340); the env vars
    # are ignored and the per-class limits always derive from
    # bulkhead_proxy_limit inside BulkheadSemaphore.
    monkeypatch.setenv("CODEX_LB_BULKHEAD_PROXY_HTTP_LIMIT", "40")
    monkeypatch.setenv("CODEX_LB_BULKHEAD_PROXY_WEBSOCKET_LIMIT", "25")
    monkeypatch.setenv("CODEX_LB_BULKHEAD_PROXY_COMPACT_LIMIT", "8")
    settings = Settings()
    assert not hasattr(settings, "bulkhead_proxy_http_limit")
    assert not hasattr(settings, "bulkhead_proxy_websocket_limit")
    assert not hasattr(settings, "bulkhead_proxy_compact_limit")
    assert settings.bulkhead_proxy_limit == 512


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


def test_settings_upstream_websocket_proxy_env_defaults_to_direct_when_unset(monkeypatch):
    for name in (
        "ws_proxy",
        "WS_PROXY",
        "wss_proxy",
        "WSS_PROXY",
        "http_proxy",
        "HTTP_PROXY",
        "https_proxy",
        "HTTPS_PROXY",
        "socks_proxy",
        "SOCKS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("CODEX_LB_UPSTREAM_WEBSOCKET_TRUST_ENV", raising=False)

    settings = Settings()

    assert settings.upstream_websocket_trust_env is False


def test_settings_upstream_websocket_proxy_env_ignores_os_proxy_settings(monkeypatch):
    import app.core.utils.proxy_env as proxy_env_module

    for name in (
        "ws_proxy",
        "WS_PROXY",
        "wss_proxy",
        "WSS_PROXY",
        "http_proxy",
        "HTTP_PROXY",
        "https_proxy",
        "HTTPS_PROXY",
        "socks_proxy",
        "SOCKS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("CODEX_LB_UPSTREAM_WEBSOCKET_TRUST_ENV", raising=False)
    monkeypatch.setattr(
        proxy_env_module.urllib.request,
        "getproxies",
        lambda: {"https": "http://127.0.0.1:7890"},
    )

    settings = Settings()

    assert settings.upstream_websocket_trust_env is False


def test_settings_upstream_websocket_proxy_env_auto_enables_when_proxy_is_present(monkeypatch):
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7890")
    monkeypatch.delenv("CODEX_LB_UPSTREAM_WEBSOCKET_TRUST_ENV", raising=False)

    settings = Settings()

    assert settings.upstream_websocket_trust_env is True


def test_settings_upstream_websocket_proxy_env_auto_enables_when_socks_proxy_is_present(monkeypatch):
    monkeypatch.setenv("socks_proxy", "socks5://127.0.0.1:7890")
    monkeypatch.delenv("CODEX_LB_UPSTREAM_WEBSOCKET_TRUST_ENV", raising=False)

    settings = Settings()

    assert settings.upstream_websocket_trust_env is True


def test_settings_upstream_websocket_proxy_env_auto_enables_when_dotenv_proxy_is_present(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_local_file = tmp_path / ".env.local"
    env_file.write_text("", encoding="utf-8")
    env_local_file.write_text("https_proxy=http://127.0.0.1:7890\n", encoding="utf-8")
    monkeypatch.setattr(settings_module, "ENV_FILES", (env_file, env_local_file))
    for name in (
        "https_proxy",
        "HTTPS_PROXY",
        "CODEX_LB_UPSTREAM_WEBSOCKET_TRUST_ENV",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings()

    assert settings.upstream_websocket_trust_env is True


def test_settings_upstream_websocket_proxy_env_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7890")
    monkeypatch.setenv("CODEX_LB_UPSTREAM_WEBSOCKET_TRUST_ENV", "false")

    settings = Settings()

    assert settings.upstream_websocket_trust_env is False


def test_settings_workers_per_instance_defaults_to_one(monkeypatch):
    # The default (one worker per instance) requires no operator action and is
    # accepted exactly as before: caps are partitioned per replica via the ring.
    monkeypatch.delenv("CODEX_LB_WORKERS_PER_INSTANCE", raising=False)

    settings = Settings()

    assert settings.workers_per_instance == 1


def test_settings_workers_per_instance_explicit_one_is_accepted(monkeypatch):
    monkeypatch.setenv("CODEX_LB_WORKERS_PER_INSTANCE", "1")

    settings = Settings()

    assert settings.workers_per_instance == 1


def test_settings_rejects_multiple_workers_per_instance(monkeypatch):
    # Multi-worker per instance is unsupported for shared per-account caps and
    # MUST fail fast: intra-pod worker cap partitioning cannot be made reliable,
    # so operators run one worker per pod/container and scale via replicas.
    monkeypatch.setenv("CODEX_LB_WORKERS_PER_INSTANCE", "2")

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    message = str(exc_info.value)
    assert "CODEX_LB_WORKERS_PER_INSTANCE" in message
    assert "not supported" in message
    assert "scale horizontally via replicas" in message
