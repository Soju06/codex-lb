from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import pytest

import app.modules.settings.service as settings_service_module
from app.core.config.dashboard_overrides import DASHBOARD_TIMEOUT_SETTINGS
from app.core.config.settings import Settings
from app.db.models import DashboardSettings, DashboardUser
from app.modules.settings.repository import SettingsRepository
from app.modules.settings.service import (
    InheritableValue,
    SettingSource,
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

        async def list_active_password_users(self) -> list[DashboardUser]:
            # No accounts: these tests exercise settings resolution, not enrolment.
            return []

    # Environment differs from the code default for the stream limit (8) only.
    monkeypatch.setattr(
        settings_service_module,
        "get_settings",
        lambda: SimpleNamespace(
            proxy_account_response_create_limit=4,
            proxy_account_stream_limit=12,
            proxy_account_stream_recovery_reserve=1,
            proxy_api_key_fair_share_congestion_threshold_pct=0,
            # C2-2 routing/overload: the lease TTL differs from its default.
            proxy_overload_isolation_seconds=1800,
            proxy_account_error_rate_weighting_enabled=True,
            proxy_account_inflight_penalty_pct=2.5,
            proxy_account_lease_token_weight=1.0,
            proxy_account_lease_ttl_seconds=300.0,
        ),
    )
    row.proxy_overload_isolation_seconds = None
    row.proxy_account_error_rate_weighting_enabled = False
    row.proxy_account_inflight_penalty_pct = None
    row.proxy_account_lease_token_weight = None
    row.proxy_account_lease_ttl_seconds = None

    settings = await SettingsService(cast(SettingsRepository, _Repository())).get_settings()

    assert settings.provenance == {
        # Thread cache identity: NULL column and a stub startup-settings object
        # without the field, so it falls back to the ``shared`` code default.
        "thread_cache_identity_mode": InheritableValue("shared", "default", "shared", "shared"),
        "proxy_account_response_create_limit": InheritableValue(4, "default", 4, 4),
        "proxy_account_stream_limit": InheritableValue(12, "env", 12, 8),
        "proxy_account_stream_recovery_reserve": InheritableValue(3, "dashboard", 1, 1),
        "proxy_api_key_fair_share_congestion_threshold_pct": InheritableValue(0, "default", 0, 0),
        "proxy_overload_isolation_seconds": InheritableValue(1800, "default", 1800, 1800),
        "proxy_account_error_rate_weighting_enabled": InheritableValue(False, "dashboard", True, True),
        "proxy_account_inflight_penalty_pct": InheritableValue(2.5, "default", 2.5, 2.5),
        "proxy_account_lease_token_weight": InheritableValue(1.0, "default", 1.0, 1.0),
        "proxy_account_lease_ttl_seconds": InheritableValue(300.0, "env", 300.0, 900.0),
        "request_log_retention_days": InheritableValue(30, "dashboard", None, 0),
        "usage_history_retention_days": InheritableValue(0, "default", None, 0),
        # C2-3 resilience toggles: NULL columns, env double without the fields
        # -> code defaults.
        "soft_drain_enabled": InheritableValue(True, "default", True, True),
        "deterministic_failover_enabled": InheritableValue(True, "default", True, True),
        "circuit_breaker_enabled": InheritableValue(False, "default", False, False),
        # M3 codex prewarm: NULL column, env double without the field -> off.
        "http_responses_session_bridge_codex_prewarm_enabled": InheritableValue(False, "default", False, False),
        # M2 background jobs: NULL columns, env double without the fields ->
        # code defaults (every scheduler on).
        "auth_guardian_enabled": InheritableValue(True, "default", True, True),
        "automations_scheduler_enabled": InheritableValue(True, "default", True, True),
        "rate_limit_reset_credits_refresh_enabled": InheritableValue(True, "default", True, True),
        # M5 conversation archive: NULL column, env double without the field
        # -> code default (off).
        "conversation_archive_enabled": InheritableValue(False, "default", False, False),
        # R2 spool retention: NULL column, env double without the field -> the
        # 7-day code default.
        "http_responses_session_bridge_operation_spool_retention_seconds": InheritableValue(
            604800.0, "default", 604800.0, 604800.0
        ),
        # C2-1 timeouts: NULL columns and a startup fake without the fields
        # resolve to the code default.
        **{
            name: InheritableValue(
                Settings.model_fields[name].default,
                "default",
                Settings.model_fields[name].default,
                Settings.model_fields[name].default,
            )
            for name in DASHBOARD_TIMEOUT_SETTINGS
        },
    }
    assert settings.proxy_account_error_rate_weighting_enabled is False
    assert settings.proxy_account_lease_ttl_seconds == 300.0
    # The flat effective fields come from the same resolution.
    assert settings.proxy_account_stream_limit == 12
    assert settings.proxy_account_stream_recovery_reserve == 3
    assert settings.request_log_retention_days == 30


def test_resolve_inheritable_handles_float_timeouts_including_zero_keepalive() -> None:
    assert resolve_inheritable(None, 12.5, 10.0) == InheritableValue(12.5, "env", 12.5, 10.0)
    assert resolve_inheritable(None, 10.0, 10.0) == InheritableValue(10.0, "default", 10.0, 10.0)
    assert resolve_inheritable(0.0, 12.5, 10.0) == InheritableValue(0.0, "dashboard", 12.5, 10.0)


@pytest.mark.asyncio
async def test_timeout_settings_resolve_dashboard_then_environment_then_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = DashboardSettings()
    row.proxy_request_budget_seconds = 900.0
    row.sse_keepalive_interval_seconds = None
    row.upstream_connect_timeout_seconds = None

    class _Repository:
        async def get_or_create(self) -> DashboardSettings:
            return row

        async def list_active_password_users(self) -> list[DashboardUser]:
            # No accounts: these tests exercise settings resolution, not enrolment.
            return []

    monkeypatch.setattr(
        settings_service_module,
        "get_settings",
        lambda: SimpleNamespace(proxy_request_budget_seconds=601.0, sse_keepalive_interval_seconds=3.0),
    )

    settings = await SettingsService(cast(SettingsRepository, _Repository())).get_settings()

    assert settings.proxy_request_budget_seconds == 900.0
    assert settings.provenance["proxy_request_budget_seconds"] == InheritableValue(900.0, "dashboard", 601.0, 600.0)
    assert settings.sse_keepalive_interval_seconds == 3.0
    assert settings.provenance["sse_keepalive_interval_seconds"] == InheritableValue(3.0, "env", 3.0, 10.0)
    assert settings.upstream_connect_timeout_seconds == 8.0
    assert settings.provenance["upstream_connect_timeout_seconds"] == InheritableValue(8.0, "default", 8.0, 8.0)


@pytest.mark.asyncio
async def test_stream_and_bridge_budgets_resolve_dashboard_then_environment_then_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # M1 stream/bridge budgets: the three resolver states on the two new columns.
    row = DashboardSettings()
    row.http_responses_stream_request_budget_seconds = 3600.0
    row.http_responses_session_bridge_request_budget_seconds = None

    class _Repository:
        async def get_or_create(self) -> DashboardSettings:
            return row

        async def list_active_password_users(self) -> list[DashboardUser]:
            # No accounts: these tests exercise settings resolution, not enrolment.
            return []

    # (a) dashboard column set -> dashboard; (b) NULL column + env differs -> env.
    monkeypatch.setattr(
        settings_service_module,
        "get_settings",
        lambda: SimpleNamespace(
            http_responses_stream_request_budget_seconds=5000.0,
            http_responses_session_bridge_request_budget_seconds=5400.0,
        ),
    )
    settings = await SettingsService(cast(SettingsRepository, _Repository())).get_settings()
    assert settings.http_responses_stream_request_budget_seconds == 3600.0
    assert settings.provenance["http_responses_stream_request_budget_seconds"] == InheritableValue(
        3600.0, "dashboard", 5000.0, 7200.0
    )
    assert settings.http_responses_session_bridge_request_budget_seconds == 5400.0
    assert settings.provenance["http_responses_session_bridge_request_budget_seconds"] == InheritableValue(
        5400.0, "env", 5400.0, 7200.0
    )

    # (c) NULL column and no environment value -> code default.
    row.http_responses_stream_request_budget_seconds = None
    monkeypatch.setattr(settings_service_module, "get_settings", lambda: SimpleNamespace())
    settings = await SettingsService(cast(SettingsRepository, _Repository())).get_settings()
    assert settings.http_responses_stream_request_budget_seconds == 7200.0
    assert settings.provenance["http_responses_stream_request_budget_seconds"] == InheritableValue(
        7200.0, "default", 7200.0, 7200.0
    )
    assert settings.provenance["http_responses_session_bridge_request_budget_seconds"].source == "default"


@pytest.mark.asyncio
async def test_migrated_null_account_caps_inherit_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    row = DashboardSettings()
    row.proxy_account_response_create_limit = None
    row.proxy_account_stream_limit = None
    row.proxy_account_stream_recovery_reserve = None

    class _Repository:
        async def get_or_create(self) -> DashboardSettings:
            return row

        async def list_active_password_users(self) -> list[DashboardUser]:
            # No accounts: these tests exercise settings resolution, not enrolment.
            return []

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

        async def list_active_password_users(self) -> list[DashboardUser]:
            # No accounts: these tests exercise settings resolution, not enrolment.
            return []

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

        async def list_active_password_users(self) -> list[DashboardUser]:
            # No accounts: these tests exercise settings resolution, not enrolment.
            return []

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

        async def list_active_password_users(self) -> list[DashboardUser]:
            # No accounts: these tests exercise settings resolution, not enrolment.
            return []

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

        async def list_active_password_users(self) -> list[DashboardUser]:
            # No accounts: these tests exercise settings resolution, not enrolment.
            return []

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


# --- M3 codex prewarm --------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("column_value", "env_value", "expected"),
    [
        # NULL column + env alias differs from the default -> env layer.
        (None, True, InheritableValue(True, "env", True, False)),
        # NULL column + env alias equals the default -> default layer.
        (None, False, InheritableValue(False, "default", False, False)),
        # Dashboard value wins in both directions, including an explicit False.
        (False, True, InheritableValue(False, "dashboard", True, False)),
        (True, False, InheritableValue(True, "dashboard", False, False)),
    ],
)
async def test_settings_data_resolves_codex_prewarm_switch_with_provenance(
    monkeypatch: pytest.MonkeyPatch,
    column_value: bool | None,
    env_value: bool,
    expected: InheritableValue[bool],
) -> None:
    row = DashboardSettings()
    row.http_responses_session_bridge_codex_prewarm_enabled = column_value

    class _Repository:
        async def get_or_create(self) -> DashboardSettings:
            return row

        async def list_active_password_users(self) -> list[DashboardUser]:
            # No accounts: these tests exercise settings resolution, not enrolment.
            return []

    monkeypatch.setattr(
        settings_service_module,
        "get_settings",
        lambda: SimpleNamespace(
            proxy_account_response_create_limit=4,
            proxy_account_stream_limit=8,
            proxy_account_stream_recovery_reserve=1,
            proxy_api_key_fair_share_congestion_threshold_pct=0,
            http_responses_session_bridge_codex_prewarm_enabled=env_value,
        ),
    )

    data = await SettingsService(cast(SettingsRepository, _Repository())).get_settings()

    assert data.http_responses_session_bridge_codex_prewarm_enabled is expected.value
    assert data.provenance["http_responses_session_bridge_codex_prewarm_enabled"] == expected


# --- M2 background jobs -------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_data_resolves_background_job_toggles_with_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """M2 background jobs: dashboard column > deprecated env alias > default, plus the topology flag."""
    row = DashboardSettings()
    row.auth_guardian_enabled = None
    row.automations_scheduler_enabled = False
    row.rate_limit_reset_credits_refresh_enabled = None
    # A real Settings so the topology gate is evaluated: two-replica ring
    # without leader election blocks the guardian whatever the toggle says.
    startup = Settings(
        _env_file=None,
        auth_guardian_enabled=True,
        automations_scheduler_enabled=True,
        rate_limit_reset_credits_refresh_enabled=False,
        leader_election_enabled=False,
        http_responses_session_bridge_instance_id="pod-a",
        http_responses_session_bridge_instance_ring=["pod-a", "pod-b"],
    )
    monkeypatch.setattr(settings_service_module, "get_settings", lambda: startup)

    class _Repository:
        async def get_or_create(self) -> DashboardSettings:
            return row

        async def list_active_password_users(self) -> list[DashboardUser]:
            # No accounts: these tests exercise settings resolution, not enrolment.
            return []

    service = SettingsService(cast(SettingsRepository, _Repository()))

    data = await service.get_settings()

    assert data.auth_guardian_enabled is True
    assert data.auth_guardian_blocked_by_topology is True
    assert data.automations_scheduler_enabled is False
    assert data.rate_limit_reset_credits_refresh_enabled is False
    assert data.provenance["auth_guardian_enabled"] == InheritableValue(True, "default", True, True)
    assert data.provenance["automations_scheduler_enabled"] == InheritableValue(False, "dashboard", True, True)
    assert data.provenance["rate_limit_reset_credits_refresh_enabled"] == InheritableValue(False, "env", False, True)

    startup.leader_election_enabled = True
    assert (await service.get_settings()).auth_guardian_blocked_by_topology is False


# M5 conversation archive
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("column", "env", "expected", "source"),
    [
        (None, False, False, "default"),
        (None, True, True, "env"),
        (True, False, True, "dashboard"),
        (False, True, False, "dashboard"),
    ],
)
async def test_settings_data_resolves_conversation_archive_toggle_with_provenance(
    monkeypatch: pytest.MonkeyPatch, column: bool | None, env: bool, expected: bool, source: SettingSource
) -> None:
    row = DashboardSettings()
    row.conversation_archive_enabled = column

    class _Repository:
        async def get_or_create(self) -> DashboardSettings:
            return row

        async def list_active_password_users(self) -> list[DashboardUser]:
            # No accounts: these tests exercise settings resolution, not enrolment.
            return []

    monkeypatch.setattr(
        settings_service_module,
        "get_settings",
        lambda: SimpleNamespace(
            proxy_account_response_create_limit=4,
            proxy_account_stream_limit=8,
            proxy_account_stream_recovery_reserve=1,
            proxy_api_key_fair_share_congestion_threshold_pct=0,
            conversation_archive_enabled=env,
        ),
    )

    data = await SettingsService(cast(SettingsRepository, _Repository())).get_settings()

    assert data.conversation_archive_enabled is expected
    assert data.provenance["conversation_archive_enabled"] == InheritableValue(expected, source, env, False)


def test_conversation_archive_env_shadow_warning_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A set CODEX_LB_CONVERSATION_ARCHIVE_ENABLED that the dashboard column overrides is reported at startup."""
    row = DashboardSettings()
    row.conversation_archive_enabled = False
    environment = Settings(conversation_archive_enabled=True)

    shadowed = settings_service_module.warn_environment_shadowed_by_dashboard(row, environment)

    assert "conversation_archive_enabled" in shadowed


# end M5 conversation archive
