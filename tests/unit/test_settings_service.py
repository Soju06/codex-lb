from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import pytest

import app.modules.settings.service as settings_service_module
from app.db.models import DashboardSettings
from app.modules.settings.repository import SettingsRepository
from app.modules.settings.service import (
    InheritableValue,
    SettingsService,
    _dump_additional_quota_routing_policies,
    _parse_additional_quota_routing_policies,
    resolve_inheritable,
)

pytestmark = pytest.mark.unit


def test_resolve_inheritable_null_column_uses_environment_when_it_differs_from_default() -> None:
    assert resolve_inheritable(None, 12, 8) == InheritableValue(12, "env", 12, 8)


def test_resolve_inheritable_null_column_reports_default_when_environment_matches_default() -> None:
    assert resolve_inheritable(None, 8, 8) == InheritableValue(8, "default", 8, 8)
    # Database-only settings have no environment layer at all.
    assert resolve_inheritable(None, None, 0) == InheritableValue(0, "default", None, 0)


def test_resolve_inheritable_dashboard_value_wins_including_zero() -> None:
    assert resolve_inheritable(24, 12, 8) == InheritableValue(24, "dashboard", 12, 8)
    assert resolve_inheritable(0, 12, 8) == InheritableValue(0, "dashboard", 12, 8)
    assert resolve_inheritable(0, None, 0) == InheritableValue(0, "dashboard", None, 0)


@pytest.mark.asyncio
async def test_settings_data_reports_provenance_for_every_inheritable_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = DashboardSettings()
    row.proxy_account_response_create_limit = None
    row.proxy_account_stream_limit = None
    row.proxy_account_stream_recovery_reserve = 3
    row.proxy_api_key_fair_share_congestion_threshold_pct = None
    row.request_log_retention_days = 30
    row.usage_history_retention_days = None

    class _Repository:
        async def get_or_create(self) -> DashboardSettings:
            return row

    # Environment differs from the code default for the stream limit (8) only.
    monkeypatch.setattr(
        settings_service_module,
        "get_settings",
        lambda: SimpleNamespace(
            proxy_account_response_create_limit=4,
            proxy_account_stream_limit=12,
            proxy_account_stream_recovery_reserve=1,
            proxy_api_key_fair_share_congestion_threshold_pct=0,
        ),
    )

    settings = await SettingsService(cast(SettingsRepository, _Repository())).get_settings()

    assert settings.provenance == {
        "proxy_account_response_create_limit": InheritableValue(4, "default", 4, 4),
        "proxy_account_stream_limit": InheritableValue(12, "env", 12, 8),
        "proxy_account_stream_recovery_reserve": InheritableValue(3, "dashboard", 1, 1),
        "proxy_api_key_fair_share_congestion_threshold_pct": InheritableValue(0, "default", 0, 0),
        "request_log_retention_days": InheritableValue(30, "dashboard", None, 0),
        "usage_history_retention_days": InheritableValue(0, "default", None, 0),
        # C2-3 resilience toggles: NULL columns, env double without the fields
        # -> code defaults.
        "soft_drain_enabled": InheritableValue(True, "default", True, True),
        "deterministic_failover_enabled": InheritableValue(True, "default", True, True),
        "circuit_breaker_enabled": InheritableValue(False, "default", False, False),
    }
    # The flat effective fields come from the same resolution.
    assert settings.proxy_account_stream_limit == 12
    assert settings.proxy_account_stream_recovery_reserve == 3
    assert settings.request_log_retention_days == 30


@pytest.mark.asyncio
async def test_migrated_null_account_caps_inherit_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    row = DashboardSettings()
    row.proxy_account_response_create_limit = None
    row.proxy_account_stream_limit = None
    row.proxy_account_stream_recovery_reserve = None

    class _Repository:
        async def get_or_create(self) -> DashboardSettings:
            return row

    monkeypatch.setattr(
        settings_service_module,
        "get_settings",
        lambda: type(
            "_StartupSettings",
            (),
            {
                "proxy_account_response_create_limit": 24,
                "proxy_account_stream_limit": 32,
                "proxy_account_stream_recovery_reserve": 4,
                "proxy_api_key_fair_share_congestion_threshold_pct": 0,
            },
        )(),
    )

    settings = await SettingsService(cast(SettingsRepository, _Repository())).get_settings()

    assert settings.proxy_account_response_create_limit == 24
    assert settings.proxy_account_response_create_limit_override is None
    assert settings.proxy_account_stream_limit == 32
    assert settings.proxy_account_stream_limit_override is None
    assert settings.proxy_account_stream_recovery_reserve == 4
    assert settings.proxy_account_stream_recovery_reserve_override is None


@pytest.mark.asyncio
async def test_cleared_account_cap_follows_environment_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    row = DashboardSettings()
    row.proxy_account_stream_limit = None
    startup_settings = SimpleNamespace(
        proxy_account_response_create_limit=24,
        proxy_account_stream_limit=8,
        proxy_account_stream_recovery_reserve=4,
        proxy_api_key_fair_share_congestion_threshold_pct=0,
    )

    class _Repository:
        async def get_or_create(self) -> DashboardSettings:
            return row

    monkeypatch.setattr(settings_service_module, "get_settings", lambda: startup_settings)
    service = SettingsService(cast(SettingsRepository, _Repository()))

    settings = await service.get_settings()
    assert settings.proxy_account_stream_limit == 8
    assert settings.proxy_account_stream_limit_override is None

    startup_settings.proxy_account_stream_limit = 12
    settings = await service.get_settings()
    assert settings.proxy_account_stream_limit == 12
    assert settings.proxy_account_stream_limit_override is None


@pytest.mark.asyncio
async def test_migrated_null_api_key_fair_share_threshold_inherits_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = DashboardSettings()
    row.proxy_api_key_fair_share_congestion_threshold_pct = None

    class _Repository:
        async def get_or_create(self) -> DashboardSettings:
            return row

    monkeypatch.setattr(
        settings_service_module,
        "get_settings",
        lambda: type(
            "_StartupSettings",
            (),
            {
                "proxy_account_response_create_limit": 24,
                "proxy_account_stream_limit": 32,
                "proxy_account_stream_recovery_reserve": 4,
                "proxy_api_key_fair_share_congestion_threshold_pct": 55,
            },
        )(),
    )
    service = SettingsService(cast(SettingsRepository, _Repository()))

    # NULL migrated rows inherit the environment default.
    settings = await service.get_settings()
    assert settings.proxy_api_key_fair_share_congestion_threshold_pct == 55
    assert settings.proxy_api_key_fair_share_congestion_threshold_pct_override is None

    # A non-NULL dashboard value wins, including 0 (explicitly disabled).
    row.proxy_api_key_fair_share_congestion_threshold_pct = 80
    settings = await service.get_settings()
    assert settings.proxy_api_key_fair_share_congestion_threshold_pct == 80
    assert settings.proxy_api_key_fair_share_congestion_threshold_pct_override == 80

    row.proxy_api_key_fair_share_congestion_threshold_pct = 0
    settings = await service.get_settings()
    assert settings.proxy_api_key_fair_share_congestion_threshold_pct == 0
    assert settings.proxy_api_key_fair_share_congestion_threshold_pct_override == 0


@pytest.mark.asyncio
async def test_null_retention_inherits_environment_and_dashboard_value_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = DashboardSettings()

    class _Repository:
        async def get_or_create(self) -> DashboardSettings:
            return row

    monkeypatch.setattr(
        settings_service_module,
        "get_settings",
        lambda: type(
            "_StartupSettings",
            (),
            {
                "proxy_account_response_create_limit": 24,
                "proxy_account_stream_limit": 32,
                "proxy_account_stream_recovery_reserve": 4,
                "proxy_api_key_fair_share_congestion_threshold_pct": 0,
            },
        )(),
    )
    service = SettingsService(cast(SettingsRepository, _Repository()))

    # NULL dashboard values mean retention was never configured, which is
    # disabled (0); the raw overrides stay exposed as None (= not set).
    settings = await service.get_settings()
    assert settings.request_log_retention_days == 0
    assert settings.usage_history_retention_days == 0
    assert settings.request_log_retention_override_days is None
    assert settings.usage_history_retention_override_days is None

    # Non-NULL dashboard values win, including 0 (explicitly disabled).
    row.request_log_retention_days = 30
    row.usage_history_retention_days = 0
    settings = await service.get_settings()
    assert settings.request_log_retention_days == 30
    assert settings.usage_history_retention_days == 0
    assert settings.request_log_retention_override_days == 30
    assert settings.usage_history_retention_override_days == 0


def test_parse_additional_quota_routing_policies_normalizes_aliases_and_policy_case() -> None:
    raw = json.dumps(
        {
            "codex-spark": "burn_first",
            "codex_spark": " preserve ",
            "gpt-5.3-codex-spark": "normal",
            "other": "legacy",
            123: "preserve",
        }
    )

    parsed = _parse_additional_quota_routing_policies(raw)
    assert parsed == {
        "codex_spark": "normal",
    }


def test_parse_additional_quota_routing_policies_handles_invalid_json() -> None:
    assert _parse_additional_quota_routing_policies(None) == {}
    assert _parse_additional_quota_routing_policies("not-json") == {}


def test_dump_additional_quota_routing_policies_canonicalizes_keys_and_filters_invalid() -> None:
    dumped = _dump_additional_quota_routing_policies(
        {
            "codex-spark": "normal",
            "codex_spark": "preserve",
            "  gpt-5.3-codex-spark  ": "burn_first",
            "bad-key": "normal",
        }
    )
    assert json.loads(dumped) == {"codex_spark": "burn_first"}


# --- C2-3 resilience toggles -------------------------------------------------


def test_resolve_inheritable_boolean_toggle_in_each_state() -> None:
    # NULL column + env differs from the default -> env layer.
    assert resolve_inheritable(None, False, True) == InheritableValue(False, "env", False, True)
    # NULL column + env equals the default -> default layer.
    assert resolve_inheritable(None, True, True) == InheritableValue(True, "default", True, True)
    # Dashboard value wins, including an explicit False.
    assert resolve_inheritable(False, True, True) == InheritableValue(False, "dashboard", True, True)
    assert resolve_inheritable(True, False, False) == InheritableValue(True, "dashboard", False, False)


@pytest.mark.asyncio
async def test_settings_data_resolves_resilience_toggles_with_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    row = DashboardSettings()
    row.soft_drain_enabled = None
    row.deterministic_failover_enabled = None
    row.circuit_breaker_enabled = True

    class _Repository:
        async def get_or_create(self) -> DashboardSettings:
            return row

    monkeypatch.setattr(
        settings_service_module,
        "get_settings",
        lambda: SimpleNamespace(
            proxy_account_response_create_limit=4,
            proxy_account_stream_limit=8,
            proxy_account_stream_recovery_reserve=1,
            proxy_api_key_fair_share_congestion_threshold_pct=0,
            soft_drain_enabled=True,
            deterministic_failover_enabled=False,
            circuit_breaker_enabled=False,
        ),
    )
    service = SettingsService(cast(SettingsRepository, _Repository()))

    data = await service.get_settings()

    assert data.soft_drain_enabled is True
    assert data.deterministic_failover_enabled is False
    assert data.circuit_breaker_enabled is True
    assert data.provenance["soft_drain_enabled"] == InheritableValue(True, "default", True, True)
    assert data.provenance["deterministic_failover_enabled"] == InheritableValue(False, "env", False, True)
    assert data.provenance["circuit_breaker_enabled"] == InheritableValue(True, "dashboard", False, False)
