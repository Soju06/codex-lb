"""R2 spool retention: resolver precedence and the replay floor it must clear."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.config.settings import Settings, get_settings
from app.core.config.spool_retention import (
    OPERATION_SPOOL_RETENTION_SETTING,
    binding_spool_retention_floor_term,
    bridge_session_reuse_window_seconds,
    operation_spool_retention_floor_seconds,
    resolve_operation_spool_retention_seconds,
    spool_retention_floor_terms_seconds,
    warn_spool_retention_below_floor,
)
from app.modules.proxy._service.http_bridge import helpers as http_bridge_helpers
from app.modules.proxy.durable_bridge_repository import DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS

_DEFAULT = float(Settings.model_fields[OPERATION_SPOOL_RETENTION_SETTING].default)


def _row(**overrides: float | None) -> SimpleNamespace:
    values: dict[str, float | None] = {
        "openai_cache_affinity_max_age_seconds": 1800,
        "http_responses_session_bridge_prompt_cache_idle_ttl_seconds": 3600,
        "http_responses_session_bridge_request_budget_seconds": None,
        OPERATION_SPOOL_RETENTION_SETTING: None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _environment(**overrides: float) -> SimpleNamespace:
    values: dict[str, float] = {
        OPERATION_SPOOL_RETENTION_SETTING: _DEFAULT,
        "http_responses_session_bridge_request_budget_seconds": 7200.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_null_column_inherits_the_environment_alias_then_the_default() -> None:
    assert resolve_operation_spool_retention_seconds(_row(), startup_settings=_environment()) == _DEFAULT
    assert (
        resolve_operation_spool_retention_seconds(
            _row(), startup_settings=_environment(**{OPERATION_SPOOL_RETENTION_SETTING: 259200.0})
        )
        == 259200.0
    )


def test_dashboard_value_wins_over_the_environment_alias() -> None:
    row = _row(**{OPERATION_SPOOL_RETENTION_SETTING: 86400.0})
    environment = _environment(**{OPERATION_SPOOL_RETENTION_SETTING: 259200.0})
    assert resolve_operation_spool_retention_seconds(row, startup_settings=environment) == 86400.0


def test_missing_snapshot_falls_back_to_the_environment_layer() -> None:
    """A caller without a dashboard row (startup before the first load) keeps today's behaviour."""
    assert (
        resolve_operation_spool_retention_seconds(
            None, startup_settings=_environment(**{OPERATION_SPOOL_RETENTION_SETTING: 172800.0})
        )
        == 172800.0
    )


def test_floor_covers_every_window_that_can_still_read_the_spool() -> None:
    terms = spool_retention_floor_terms_seconds(_row(), startup_settings=_environment())
    assert terms == {
        # max(1800 affinity, 3600 prompt-cache TTL, 120 idle, 900 codex idle)
        "bridge_session_reuse_window": 3600.0,
        # max(1800 sweep floor, 7200 bridge request budget)
        "stale_operation_abandonment_window": 7200.0,
        # An ever-claimed circuit gets one extra TTL of grace, so the term is
        # two TTLs, not one (retry_circuit.py / purge_retry_circuits_before).
        "claimed_retry_circuit_lifetime": 2 * float(DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS),
    }
    assert operation_spool_retention_floor_seconds(_row(), startup_settings=_environment()) == 7200.0
    # The shipped default clears the floor by a wide margin.
    assert _DEFAULT > 7200.0


def test_claimed_retry_circuit_grace_binds_below_the_default_bridge_budget() -> None:
    """A lowered bridge budget must not drop the floor below a claimed circuit's lifetime."""
    environment = _environment(http_responses_session_bridge_request_budget_seconds=3600.0)
    terms = spool_retention_floor_terms_seconds(_row(), startup_settings=environment)
    assert terms["stale_operation_abandonment_window"] == 3600.0
    assert binding_spool_retention_floor_term(_row(), startup_settings=environment) == (
        "claimed_retry_circuit_lifetime",
        2 * float(DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS),
    )


def test_floor_follows_the_dashboard_reuse_windows() -> None:
    row = _row(http_responses_session_bridge_prompt_cache_idle_ttl_seconds=86400)
    assert binding_spool_retention_floor_term(row, startup_settings=_environment()) == (
        "bridge_session_reuse_window",
        86400.0,
    )
    # A dashboard bridge budget overrides the environment one in the same term.
    budgeted = _row(http_responses_session_bridge_request_budget_seconds=43200.0)
    assert binding_spool_retention_floor_term(budgeted, startup_settings=_environment()) == (
        "stale_operation_abandonment_window",
        43200.0,
    )


def test_reuse_window_reads_the_bridge_idle_ttls_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two idle TTLs are module constants; a raised value must move the floor."""
    monkeypatch.setattr(http_bridge_helpers, "HTTP_BRIDGE_CODEX_IDLE_TTL_SECONDS", 99999.0)
    assert bridge_session_reuse_window_seconds(_row()) == 99999.0


def test_warn_below_floor_logs_the_binding_term_and_both_numbers(caplog: pytest.LogCaptureFixture) -> None:
    environment = _environment(**{OPERATION_SPOOL_RETENTION_SETTING: 60.0})
    with caplog.at_level("WARNING", logger="app.core.config.spool_retention"):
        reported = warn_spool_retention_below_floor(_row(), environment)

    assert reported == ("stale_operation_abandonment_window", 60.0, 7200.0)
    assert "below its replay floor" in caplog.text
    assert "60s < 7200s" in caplog.text
    assert "stale_operation_abandonment_window" in caplog.text
    # Only the two numbers and the term name; no other setting's value leaks.
    assert "3600" not in caplog.text


def test_warn_below_floor_is_silent_at_the_shipped_default(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="app.core.config.spool_retention"):
        assert warn_spool_retention_below_floor(_row(), _environment()) is None
    assert caplog.text == ""


@pytest.mark.asyncio
async def test_startup_report_warns_when_the_env_alias_sits_below_the_floor(monkeypatch, caplog) -> None:
    """The warn-only startup pass (#2221) also reports the spool floor."""
    import app.main as main_module

    dashboard_row = _row()

    class _Cache:
        async def get(self) -> object:
            return dashboard_row

    monkeypatch.setattr(main_module, "get_settings_cache", lambda: _Cache())
    monkeypatch.setattr(main_module, "warn_environment_shadowed_by_dashboard", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "effective_settings", lambda _row_, base: base)
    monkeypatch.setattr(main_module, "validate_timeout_invariants", lambda *args, **kwargs: [])

    environment = get_settings().model_copy(update={OPERATION_SPOOL_RETENTION_SETTING: 60.0})
    with caplog.at_level("WARNING", logger="app.core.config.spool_retention"):
        await main_module._report_dashboard_timeout_overrides(environment)

    assert "below its replay floor" in caplog.text
    assert "60s < 7200s" in caplog.text


def test_partial_snapshot_degrades_to_the_constant_reuse_terms() -> None:
    """Startup and scheduler tests build partial snapshots; a missing column is skipped, not fatal."""
    assert bridge_session_reuse_window_seconds(SimpleNamespace()) == max(
        float(http_bridge_helpers.HTTP_BRIDGE_IDLE_TTL_SECONDS),
        float(http_bridge_helpers.HTTP_BRIDGE_CODEX_IDLE_TTL_SECONDS),
    )
