from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.core.config.settings import Settings, get_settings
from app.core.timeout_invariants import (
    TIMEOUT_INVARIANT_RULES,
    TimeoutInvariantError,
    find_timeout_invariant_violations,
    main,
    validate_runtime_timeout_invariants,
    validate_timeout_invariants,
)
from app.modules.proxy import durable_bridge_repository
from app.modules.proxy._service.http_bridge import retry_circuit

pytestmark = pytest.mark.unit


def test_default_settings_satisfy_timeout_invariants() -> None:
    settings = Settings()
    assert len(TIMEOUT_INVARIANT_RULES) == 8
    assert find_timeout_invariant_violations(settings) == []


def _timeout_settings(**overrides: float | bool) -> SimpleNamespace:
    settings = Settings()
    values = {
        name: getattr(settings, name)
        for name in (
            "upstream_connect_timeout_seconds",
            "proxy_request_budget_seconds",
            "http_responses_stream_request_budget_seconds",
            "compact_request_budget_seconds",
            "stream_idle_timeout_seconds",
            "sse_keepalive_interval_seconds",
            "usage_fetch_timeout_seconds",
            "usage_refresh_interval_seconds",
            "rate_limit_reset_credits_refresh_interval_seconds",
            "http_responses_session_bridge_request_budget_seconds",
            "http_responses_session_bridge_idle_ttl_seconds",
            "http_responses_session_bridge_codex_idle_ttl_seconds",
            "http_responses_session_bridge_stuck_gate_retire_after_seconds",
            "http_responses_session_bridge_clean_close_retry_jitter_max_seconds",
            "proxy_admission_wait_timeout_seconds",
            "proxy_account_lease_ttl_seconds",
            "proxy_refresh_failure_cooldown_seconds",
            "model_registry_enabled",
            "model_registry_snapshot_max_age_seconds",
            "timeout_invariant_validation_strict",
        )
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("rule_id", "overrides"),
    [
        ("admission-wait-within-proxy-budget", {"proxy_request_budget_seconds": 9.0}),
        ("admission-wait-within-stream-budget", {"http_responses_stream_request_budget_seconds": 9.0}),
        ("admission-wait-within-compact-budget", {"compact_request_budget_seconds": 9.0}),
        (
            "bridge-stuck-gate-retire-within-bridge-budget",
            {"http_responses_session_bridge_request_budget_seconds": 600.0},
        ),
        ("account-lease-ttl-covers-proxy-budget", {"proxy_account_lease_ttl_seconds": 599.0}),
        ("account-lease-ttl-covers-compact-budget", {"proxy_account_lease_ttl_seconds": 179.0}),
        (
            "model-registry-snapshot-outlives-refresh-interval",
            {"model_registry_enabled": True, "model_registry_snapshot_max_age_seconds": 300.0},
        ),
    ],
)
def test_each_settings_backed_rule_names_violation(rule_id: str, overrides: dict[str, float]) -> None:
    settings = _timeout_settings(**overrides)

    violations = find_timeout_invariant_violations(settings)

    assert any(violation.rule.id == rule_id for violation in violations)
    formatted = "\n".join(violation.format() for violation in violations)
    assert rule_id in formatted


def test_disabled_model_registry_skips_snapshot_cadence_rule() -> None:
    settings = _timeout_settings(
        model_registry_enabled=False,
        model_registry_snapshot_max_age_seconds=1.0,
    )

    violations = find_timeout_invariant_violations(settings)

    assert all(violation.rule.id != "model-registry-snapshot-outlives-refresh-interval" for violation in violations)


def test_durable_bridge_retry_circuit_rule_names_violation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        durable_bridge_repository,
        "DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS",
        retry_circuit._HTTP_BRIDGE_RETRY_CIRCUIT_MAX_BACKOFF_SECONDS
        + retry_circuit._HTTP_BRIDGE_RETRY_CIRCUIT_HALF_OPEN_LEASE_SECONDS
        - 1.0,
    )

    violations = find_timeout_invariant_violations(Settings())

    rule_id = "durable-bridge-retry-circuit-ttl-covers-backoff-and-half-open"
    assert any(violation.rule.id == rule_id for violation in violations)
    assert rule_id in "\n".join(violation.format() for violation in violations)


def test_non_strict_startup_validation_logs_critical(caplog: pytest.LogCaptureFixture) -> None:
    settings = Settings(proxy_request_budget_seconds=5.0)

    with caplog.at_level(logging.CRITICAL, logger="app.core.timeout_invariants"):
        violations = validate_runtime_timeout_invariants(settings)

    assert violations
    assert "timeout invariant violation: admission-wait-within-proxy-budget" in caplog.text


def test_strict_mode_raises() -> None:
    settings = Settings(
        proxy_request_budget_seconds=5.0,
        timeout_invariant_validation_strict=True,
    )

    with pytest.raises(TimeoutInvariantError) as exc_info:
        validate_runtime_timeout_invariants(settings)

    assert "admission-wait-within-proxy-budget" in str(exc_info.value)


def test_explicit_strict_validation_raises() -> None:
    settings = Settings(proxy_request_budget_seconds=5.0)

    with pytest.raises(TimeoutInvariantError, match="admission-wait-within-proxy-budget"):
        validate_timeout_invariants(settings, strict=True, log=False)


def test_cli_entrypoint_accepts_defaults(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    captured = capsys.readouterr()
    assert "timeout invariant rules satisfied" in captured.out


def test_cli_strict_flag_exits_one_and_reports_rule(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("CODEX_LB_PROXY_REQUEST_BUDGET_SECONDS", "5")
    try:
        assert main(["--strict"]) == 1
    finally:
        get_settings.cache_clear()

    captured = capsys.readouterr()
    assert "admission-wait-within-proxy-budget" in captured.err


def test_cli_without_strict_exits_zero_and_reports_violation(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("CODEX_LB_PROXY_REQUEST_BUDGET_SECONDS", "5")
    try:
        with caplog.at_level(logging.CRITICAL, logger="app.core.timeout_invariants"):
            assert main([]) == 0
    finally:
        get_settings.cache_clear()

    captured = capsys.readouterr()
    assert "admission-wait-within-proxy-budget" in captured.err
    assert "timeout invariant violation: admission-wait-within-proxy-budget" in caplog.text
