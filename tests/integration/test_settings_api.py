from __future__ import annotations

import base64
import json
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

import app.modules.settings.api as settings_api_module
from app.core.auth import generate_unique_account_id
from app.core.config.settings_cache import get_settings_cache
from app.db.models import Account, AccountStatus, DashboardSettings, ProxyEndpoint
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(
                {
                    "email": email,
                    "chatgpt_account_id": account_id,
                    "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
                }
            ),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return generate_unique_account_id(account_id, email)


@pytest.mark.asyncio
async def test_settings_api_get_and_update(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["stickyThreadsEnabled"] is True
    assert payload["upstreamStreamTransport"] == "auto"
    assert payload["prohibitFastMode"] is False
    # A fresh settings row seeds NULL overrides: the effective value inherits
    # the environment and the override is reported as absent.
    assert payload["proxyAccountResponseCreateLimit"] == 4
    assert payload["proxyAccountResponseCreateLimitEnvironmentValue"] == 4
    assert payload["proxyAccountResponseCreateLimitOverride"] is None
    assert payload["proxyAccountStreamLimit"] == 8
    assert payload["proxyAccountStreamLimitEnvironmentValue"] == 8
    assert payload["proxyAccountStreamLimitOverride"] is None
    assert payload["proxyAccountStreamRecoveryReserve"] == 1
    assert payload["proxyAccountStreamRecoveryReserveEnvironmentValue"] == 1
    assert payload["proxyAccountStreamRecoveryReserveOverride"] is None
    assert payload["proxyApiKeyFairShareCongestionThresholdPct"] == 0
    assert payload["proxyApiKeyFairShareCongestionThresholdPctEnvironmentValue"] == 0
    assert payload["proxyApiKeyFairShareCongestionThresholdPctOverride"] is None
    assert payload["upstreamProxyRoutingEnabled"] is False
    assert payload["upstreamProxyDefaultPoolId"] is None
    assert payload["preferEarlierResetAccounts"] is True
    assert payload["preferEarlierResetWindow"] == "secondary"
    assert payload["showResetCreditBadges"] is True
    assert payload["autoRedeemResetCreditsBeforeExpiry"] is False
    assert payload["showResetCreditExpiryBadge"] is True
    assert payload["routingStrategy"] == "capacity_weighted"
    assert payload["relativeAvailabilityPower"] == 2.0
    assert payload["relativeAvailabilityTopK"] == 5
    assert payload["singleAccountId"] is None
    assert payload["subscriptionOverflowSourceId"] is None
    assert payload["subscriptionOverflowDrainUntil"] is None
    assert payload["subscriptionOverflowPinsExpireBy"] is None
    assert payload["openaiCacheAffinityMaxAgeSeconds"] == 1800
    assert payload["dashboardSessionTtlSeconds"] == 31536000
    assert payload["httpResponsesSessionBridgePromptCacheIdleTtlSeconds"] == 3600
    assert payload["httpResponsesSessionBridgeGatewaySafeMode"] is False
    assert payload["stickyReallocationBudgetThresholdPct"] == 95.0
    assert payload["stickyReallocationPrimaryBudgetThresholdPct"] == 95.0
    assert payload["stickyReallocationSecondaryBudgetThresholdPct"] == 100.0
    assert payload["warmupModel"] == "gpt-5.4-mini"
    assert payload["importWithoutOverwrite"] is True
    assert payload["totpRequiredOnLogin"] is False
    assert payload["apiKeyAuthEnabled"] is False
    assert payload["hideUpstreamQuotaFromApiKeys"] is False
    assert payload["limitWarmupEnabled"] is False
    assert payload["limitWarmupWindows"] == "both"
    assert payload["limitWarmupModel"] == "auto"
    assert payload["limitWarmupPrompt"] == "Say OK."
    assert payload["limitWarmupCooldownSeconds"] == 3600
    assert payload["limitWarmupExhaustedThresholdPercent"] == 99.0
    assert payload["limitWarmupIdleThresholdPercent"] == 1.0
    assert payload["limitWarmupMinAvailablePercent"] == 100.0
    assert payload["weeklyPaceWorkingDays"] == "0,1,2,3,4,5,6"
    assert payload["weeklyPaceSmoothingMinutes"] == 30
    assert payload["limitWarmupStaggeredIdleEnabled"] is False

    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "upstreamStreamTransport": "websocket",
            "prohibitFastMode": True,
            "proxyAccountResponseCreateLimit": 12,
            "proxyAccountStreamLimit": 24,
            "proxyAccountStreamRecoveryReserve": 3,
            "proxyApiKeyFairShareCongestionThresholdPct": 80,
            "upstreamProxyRoutingEnabled": True,
            "upstreamProxyDefaultPoolId": None,
            "preferEarlierResetAccounts": False,
            "routingStrategy": "relative_availability",
            "relativeAvailabilityPower": 1.5,
            "relativeAvailabilityTopK": 7,
            "preferEarlierResetWindow": "secondary",
            "showResetCreditBadges": False,
            "autoRedeemResetCreditsBeforeExpiry": True,
            "showResetCreditExpiryBadge": False,
            "singleAccountId": None,
            "openaiCacheAffinityMaxAgeSeconds": 180,
            "dashboardSessionTtlSeconds": 31536000,
            "httpResponsesSessionBridgePromptCacheIdleTtlSeconds": 1800,
            "httpResponsesSessionBridgeGatewaySafeMode": True,
            "stickyReallocationBudgetThresholdPct": 85.0,
            "stickyReallocationPrimaryBudgetThresholdPct": 85.0,
            "stickyReallocationSecondaryBudgetThresholdPct": 98.0,
            "warmupModel": "gpt-5.4-nano",
            "importWithoutOverwrite": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
            "hideUpstreamQuotaFromApiKeys": True,
            "limitWarmupEnabled": True,
            "limitWarmupWindows": "primary",
            "limitWarmupModel": "gpt-5.1-codex-mini",
            "limitWarmupPrompt": "Say OK.",
            "limitWarmupCooldownSeconds": 7200,
            "limitWarmupExhaustedThresholdPercent": 98.5,
            "limitWarmupIdleThresholdPercent": 2.0,
            "limitWarmupMinAvailablePercent": 99.0,
            "weeklyPaceWorkingDays": "0,1,2,3,4",
            "weeklyPaceSmoothingMinutes": 120,
            "limitWarmupStaggeredIdleEnabled": True,
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["stickyThreadsEnabled"] is False
    assert updated["upstreamStreamTransport"] == "websocket"
    assert updated["prohibitFastMode"] is True
    assert updated["proxyAccountResponseCreateLimit"] == 12
    assert updated["proxyAccountResponseCreateLimitOverride"] == 12
    assert updated["proxyAccountStreamLimit"] == 24
    assert updated["proxyAccountStreamLimitOverride"] == 24
    assert updated["proxyAccountStreamRecoveryReserve"] == 3
    assert updated["proxyAccountStreamRecoveryReserveOverride"] == 3
    assert updated["proxyApiKeyFairShareCongestionThresholdPct"] == 80
    assert updated["proxyApiKeyFairShareCongestionThresholdPctOverride"] == 80
    assert updated["upstreamProxyRoutingEnabled"] is True
    assert updated["upstreamProxyDefaultPoolId"] is None
    assert updated["preferEarlierResetAccounts"] is False
    assert updated["routingStrategy"] == "relative_availability"
    assert updated["relativeAvailabilityPower"] == 1.5
    assert updated["relativeAvailabilityTopK"] == 7
    assert updated["preferEarlierResetWindow"] == "secondary"
    assert updated["showResetCreditBadges"] is False
    assert updated["autoRedeemResetCreditsBeforeExpiry"] is True
    assert updated["showResetCreditExpiryBadge"] is False
    assert updated["singleAccountId"] is None
    assert updated["openaiCacheAffinityMaxAgeSeconds"] == 180
    assert updated["dashboardSessionTtlSeconds"] == 31536000
    assert updated["httpResponsesSessionBridgePromptCacheIdleTtlSeconds"] == 1800
    assert updated["httpResponsesSessionBridgeGatewaySafeMode"] is True
    assert updated["stickyReallocationBudgetThresholdPct"] == 85.0
    assert updated["stickyReallocationPrimaryBudgetThresholdPct"] == 85.0
    assert updated["stickyReallocationSecondaryBudgetThresholdPct"] == 98.0
    assert updated["warmupModel"] == "gpt-5.4-nano"
    assert updated["importWithoutOverwrite"] is False
    assert updated["totpRequiredOnLogin"] is False
    assert updated["apiKeyAuthEnabled"] is True
    assert updated["hideUpstreamQuotaFromApiKeys"] is True
    assert updated["limitWarmupEnabled"] is True
    assert updated["limitWarmupWindows"] == "primary"
    assert updated["limitWarmupModel"] == "gpt-5.1-codex-mini"
    assert updated["limitWarmupPrompt"] == "Say OK."
    assert updated["limitWarmupCooldownSeconds"] == 7200
    assert updated["limitWarmupExhaustedThresholdPercent"] == 98.5
    assert updated["limitWarmupIdleThresholdPercent"] == 2.0
    assert updated["limitWarmupMinAvailablePercent"] == 99.0
    assert updated["weeklyPaceWorkingDays"] == "0,1,2,3,4"
    assert updated["weeklyPaceSmoothingMinutes"] == 120
    assert updated["limitWarmupStaggeredIdleEnabled"] is True

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["stickyThreadsEnabled"] is False
    assert payload["upstreamStreamTransport"] == "websocket"
    assert payload["prohibitFastMode"] is True
    assert payload["proxyAccountResponseCreateLimit"] == 12
    assert payload["proxyAccountResponseCreateLimitOverride"] == 12
    assert payload["proxyAccountStreamLimit"] == 24
    assert payload["proxyAccountStreamLimitOverride"] == 24
    assert payload["proxyAccountStreamRecoveryReserve"] == 3
    assert payload["proxyAccountStreamRecoveryReserveOverride"] == 3
    assert payload["proxyApiKeyFairShareCongestionThresholdPct"] == 80
    assert payload["proxyApiKeyFairShareCongestionThresholdPctOverride"] == 80
    assert payload["upstreamProxyRoutingEnabled"] is True
    assert payload["upstreamProxyDefaultPoolId"] is None
    assert payload["preferEarlierResetAccounts"] is False
    assert payload["routingStrategy"] == "relative_availability"
    assert payload["relativeAvailabilityPower"] == 1.5
    assert payload["relativeAvailabilityTopK"] == 7
    assert payload["preferEarlierResetWindow"] == "secondary"
    assert payload["showResetCreditBadges"] is False
    assert payload["autoRedeemResetCreditsBeforeExpiry"] is True
    assert payload["showResetCreditExpiryBadge"] is False
    assert payload["singleAccountId"] is None
    assert payload["openaiCacheAffinityMaxAgeSeconds"] == 180
    assert payload["dashboardSessionTtlSeconds"] == 31536000
    assert payload["httpResponsesSessionBridgePromptCacheIdleTtlSeconds"] == 1800
    assert payload["httpResponsesSessionBridgeGatewaySafeMode"] is True
    assert payload["stickyReallocationBudgetThresholdPct"] == 85.0
    assert payload["stickyReallocationPrimaryBudgetThresholdPct"] == 85.0
    assert payload["stickyReallocationSecondaryBudgetThresholdPct"] == 98.0
    assert payload["warmupModel"] == "gpt-5.4-nano"
    assert payload["importWithoutOverwrite"] is False
    assert payload["totpRequiredOnLogin"] is False
    assert payload["apiKeyAuthEnabled"] is True
    assert payload["hideUpstreamQuotaFromApiKeys"] is True
    assert payload["limitWarmupEnabled"] is True
    assert payload["limitWarmupWindows"] == "primary"
    assert payload["limitWarmupModel"] == "gpt-5.1-codex-mini"
    assert payload["limitWarmupPrompt"] == "Say OK."
    assert payload["limitWarmupCooldownSeconds"] == 7200
    assert payload["limitWarmupExhaustedThresholdPercent"] == 98.5
    assert payload["limitWarmupIdleThresholdPercent"] == 2.0
    assert payload["limitWarmupMinAvailablePercent"] == 99.0
    assert payload["weeklyPaceWorkingDays"] == "0,1,2,3,4"
    assert payload["weeklyPaceSmoothingMinutes"] == 120


@pytest.mark.asyncio
async def test_settings_api_capacity_overrides_support_absent_null_and_explicit_values(async_client):
    configured = await async_client.put(
        "/api/settings",
        json={
            "proxyAccountResponseCreateLimit": 12,
            "proxyAccountStreamLimit": 24,
            "proxyAccountStreamRecoveryReserve": 3,
            "proxyApiKeyFairShareCongestionThresholdPct": 80,
        },
    )
    assert configured.status_code == 200

    unchanged = await async_client.put("/api/settings", json={"warmupModel": "gpt-5.6-sol"})
    assert unchanged.status_code == 200
    unchanged_payload = unchanged.json()
    assert unchanged_payload["proxyAccountResponseCreateLimitOverride"] == 12
    assert unchanged_payload["proxyAccountStreamLimitOverride"] == 24
    assert unchanged_payload["proxyAccountStreamRecoveryReserveOverride"] == 3
    assert unchanged_payload["proxyApiKeyFairShareCongestionThresholdPctOverride"] == 80

    cleared = await async_client.put(
        "/api/settings",
        json={
            "proxyAccountResponseCreateLimit": None,
            "proxyAccountStreamLimit": None,
            "proxyAccountStreamRecoveryReserve": None,
            "proxyApiKeyFairShareCongestionThresholdPct": None,
        },
    )
    assert cleared.status_code == 200
    cleared_payload = cleared.json()
    assert cleared_payload["proxyAccountResponseCreateLimit"] == 4
    assert cleared_payload["proxyAccountResponseCreateLimitOverride"] is None
    assert cleared_payload["proxyAccountStreamLimit"] == 8
    assert cleared_payload["proxyAccountStreamLimitOverride"] is None
    assert cleared_payload["proxyAccountStreamRecoveryReserve"] == 1
    assert cleared_payload["proxyAccountStreamRecoveryReserveOverride"] is None
    assert cleared_payload["proxyApiKeyFairShareCongestionThresholdPct"] == 0
    assert cleared_payload["proxyApiKeyFairShareCongestionThresholdPctOverride"] is None

    pinned_to_effective = await async_client.put(
        "/api/settings",
        json={"proxyAccountStreamLimit": 8},
    )
    assert pinned_to_effective.status_code == 200
    pinned_payload = pinned_to_effective.json()
    assert pinned_payload["proxyAccountStreamLimit"] == 8
    assert pinned_payload["proxyAccountStreamLimitOverride"] == 8


@pytest.mark.asyncio
async def test_settings_api_reports_stream_limit_provenance_in_each_state(async_client, monkeypatch):
    from app.modules.settings import service as settings_service

    # Fresh row: the column is NULL and the test environment leaves the cap at
    # its code default, so the value is reported as the default.
    initial = await async_client.get("/api/settings")
    assert initial.status_code == 200
    assert initial.json()["provenance"]["proxy_account_stream_limit"] == {
        "source": "default",
        "envValue": 8,
        "default": 8,
    }

    # PUT a value: dashboard-owned, environment and default still reported.
    configured = await async_client.put("/api/settings", json={"proxyAccountStreamLimit": 24})
    assert configured.status_code == 200
    configured_payload = configured.json()
    assert configured_payload["proxyAccountStreamLimit"] == 24
    assert configured_payload["provenance"]["proxy_account_stream_limit"] == {
        "source": "dashboard",
        "envValue": 8,
        "default": 8,
    }

    # PUT null clears the column; with the environment differing from the
    # default the value is inherited from the environment.
    inherited = settings_service.get_settings().model_copy(update={"proxy_account_stream_limit": 12})
    monkeypatch.setattr(settings_service, "get_settings", lambda: inherited)
    cleared = await async_client.put("/api/settings", json={"proxyAccountStreamLimit": None})
    assert cleared.status_code == 200
    cleared_payload = cleared.json()
    assert cleared_payload["proxyAccountStreamLimit"] == 12
    assert cleared_payload["proxyAccountStreamLimitOverride"] is None
    assert cleared_payload["provenance"]["proxy_account_stream_limit"] == {
        "source": "env",
        "envValue": 12,
        "default": 8,
    }

    # Omitting the field leaves the inherited state untouched, and every
    # inheritable setting has a provenance entry.
    unchanged = await async_client.put("/api/settings", json={"warmupModel": "gpt-5.6-sol"})
    assert unchanged.status_code == 200
    provenance = unchanged.json()["provenance"]
    assert provenance["proxy_account_stream_limit"]["source"] == "env"
    assert set(provenance) == {
        "proxy_account_response_create_limit",
        "proxy_account_stream_limit",
        "proxy_account_stream_recovery_reserve",
        "proxy_api_key_fair_share_congestion_threshold_pct",
        # C2-2 routing/overload
        "proxy_overload_isolation_seconds",
        "proxy_account_error_rate_weighting_enabled",
        "proxy_account_inflight_penalty_pct",
        "proxy_account_lease_token_weight",
        "proxy_account_lease_ttl_seconds",
        "request_log_retention_days",
        "usage_history_retention_days",
        "soft_drain_enabled",
        "deterministic_failover_enabled",
        "circuit_breaker_enabled",
        # M3 codex prewarm
        "http_responses_session_bridge_codex_prewarm_enabled",
        # M2 background jobs
        "auth_guardian_enabled",
        "automations_scheduler_enabled",
        "rate_limit_reset_credits_refresh_enabled",
        "conversation_archive_enabled",  # M5 conversation archive
        "http_responses_session_bridge_operation_spool_retention_seconds",  # R2 spool retention
        # C2-1 timeouts
        "upstream_connect_timeout_seconds",
        "proxy_request_budget_seconds",
        "compact_request_budget_seconds",
        "transcription_request_budget_seconds",
        "stream_idle_timeout_seconds",
        "proxy_downstream_websocket_idle_timeout_seconds",
        "sse_keepalive_interval_seconds",
        # M1 stream/bridge budgets
        "http_responses_stream_request_budget_seconds",
        "http_responses_session_bridge_request_budget_seconds",
        # Thread cache identity mode
        "thread_cache_identity_mode",
    }
    # Retention is database-only: no environment value, NULL reads as default.
    assert provenance["request_log_retention_days"] == {"source": "default", "envValue": None, "default": 0}


@pytest.mark.asyncio
async def test_settings_api_resilience_toggles_round_trip_with_provenance(async_client, monkeypatch):
    """C2-3 resilience toggles: default -> dashboard -> cleared/env -> unchanged on omit."""
    from app.modules.settings import service as settings_service

    initial = await async_client.get("/api/settings")
    assert initial.status_code == 200
    payload = initial.json()
    assert payload["softDrainEnabled"] is True
    assert payload["deterministicFailoverEnabled"] is True
    assert payload["circuitBreakerEnabled"] is False
    assert payload["provenance"]["soft_drain_enabled"] == {"source": "default", "envValue": True, "default": True}
    assert payload["provenance"]["circuit_breaker_enabled"] == {
        "source": "default",
        "envValue": False,
        "default": False,
    }

    # Storing a value, including the same value the environment provides, makes
    # the toggle dashboard-owned.
    stored = await async_client.put(
        "/api/settings",
        json={"circuitBreakerEnabled": True, "softDrainEnabled": True, "deterministicFailoverEnabled": False},
    )
    assert stored.status_code == 200
    stored_payload = stored.json()
    assert stored_payload["circuitBreakerEnabled"] is True
    assert stored_payload["softDrainEnabled"] is True
    assert stored_payload["deterministicFailoverEnabled"] is False
    for name in ("soft_drain_enabled", "deterministic_failover_enabled", "circuit_breaker_enabled"):
        assert stored_payload["provenance"][name]["source"] == "dashboard"

    # Explicit null clears the column; with the deprecated env alias differing
    # from the default the toggle is inherited from the environment.
    inherited = settings_service.get_settings().model_copy(update={"circuit_breaker_enabled": True})
    monkeypatch.setattr(settings_service, "get_settings", lambda: inherited)
    cleared = await async_client.put("/api/settings", json={"circuitBreakerEnabled": None, "softDrainEnabled": None})
    assert cleared.status_code == 200
    cleared_payload = cleared.json()
    assert cleared_payload["circuitBreakerEnabled"] is True
    assert cleared_payload["provenance"]["circuit_breaker_enabled"] == {
        "source": "env",
        "envValue": True,
        "default": False,
    }
    assert cleared_payload["provenance"]["soft_drain_enabled"] == {
        "source": "default",
        "envValue": True,
        "default": True,
    }
    # The untouched toggle keeps its dashboard value.
    assert cleared_payload["deterministicFailoverEnabled"] is False
    assert cleared_payload["provenance"]["deterministic_failover_enabled"]["source"] == "dashboard"

    # Omitting the fields (any unrelated save) leaves every toggle as it was:
    # the dashboard never copies the inherited value into the column.
    unchanged = await async_client.put("/api/settings", json={"warmupModel": "gpt-5.6-sol"})
    assert unchanged.status_code == 200
    unchanged_payload = unchanged.json()
    assert unchanged_payload["provenance"]["circuit_breaker_enabled"]["source"] == "env"
    assert unchanged_payload["provenance"]["soft_drain_enabled"]["source"] == "default"
    assert unchanged_payload["provenance"]["deterministic_failover_enabled"]["source"] == "dashboard"
    async with SessionLocal() as session:
        row = await session.get(DashboardSettings, 1)
        assert row is not None
        assert row.circuit_breaker_enabled is None
        assert row.soft_drain_enabled is None
        assert row.deterministic_failover_enabled is False


@pytest.mark.asyncio
async def test_settings_api_timeout_round_trip_with_provenance_and_null_clear(async_client, monkeypatch):
    from app.modules.settings import service as settings_service

    initial = await async_client.get("/api/settings")
    assert initial.status_code == 200
    payload = initial.json()
    assert payload["proxyRequestBudgetSeconds"] == 600.0
    assert payload["provenance"]["proxy_request_budget_seconds"] == {
        "source": "default",
        "envValue": 600.0,
        "default": 600.0,
    }
    assert payload["provenance"]["sse_keepalive_interval_seconds"]["source"] == "default"

    configured = await async_client.put(
        "/api/settings",
        json={"proxyRequestBudgetSeconds": 900, "sseKeepaliveIntervalSeconds": 0},
    )
    assert configured.status_code == 200
    configured_payload = configured.json()
    assert configured_payload["proxyRequestBudgetSeconds"] == 900.0
    assert configured_payload["provenance"]["proxy_request_budget_seconds"] == {
        "source": "dashboard",
        "envValue": 600.0,
        "default": 600.0,
    }
    # 0 disables keepalives and is a dashboard value like any other.
    assert configured_payload["sseKeepaliveIntervalSeconds"] == 0.0
    assert configured_payload["provenance"]["sse_keepalive_interval_seconds"]["source"] == "dashboard"

    async with SessionLocal() as session:
        row = await session.get(DashboardSettings, 1)
        assert row is not None
        assert row.proxy_request_budget_seconds == 900.0
        assert row.sse_keepalive_interval_seconds == 0.0
        assert row.upstream_connect_timeout_seconds is None

    # Omitting the fields leaves them untouched.
    unchanged = await async_client.put("/api/settings", json={"warmupModel": "gpt-5.6-sol"})
    assert unchanged.status_code == 200
    assert unchanged.json()["proxyRequestBudgetSeconds"] == 900.0

    # null clears the column; with the environment differing from the default
    # the value is inherited from the environment.
    inherited = settings_service.get_settings().model_copy(update={"proxy_request_budget_seconds": 700.0})
    monkeypatch.setattr(settings_service, "get_settings", lambda: inherited)
    cleared = await async_client.put(
        "/api/settings", json={"proxyRequestBudgetSeconds": None, "sseKeepaliveIntervalSeconds": None}
    )
    assert cleared.status_code == 200
    cleared_payload = cleared.json()
    assert cleared_payload["proxyRequestBudgetSeconds"] == 700.0
    assert cleared_payload["provenance"]["proxy_request_budget_seconds"] == {
        "source": "env",
        "envValue": 700.0,
        "default": 600.0,
    }
    assert cleared_payload["provenance"]["sse_keepalive_interval_seconds"]["source"] == "default"


@pytest.mark.asyncio
async def test_settings_api_reports_environment_timeouts_outside_the_put_bounds(async_client, monkeypatch):
    """An env value the Settings model accepts must never make GET fail, even if PUT would reject it."""
    from app.modules.settings import service as settings_service

    # ``upstream_connect_timeout_seconds`` is unbounded in Settings; 0 is a legal environment value.
    inherited = settings_service.get_settings().model_copy(update={"upstream_connect_timeout_seconds": 0.0})
    monkeypatch.setattr(settings_service, "get_settings", lambda: inherited)

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["upstreamConnectTimeoutSeconds"] == 0.0
    assert payload["provenance"]["upstream_connect_timeout_seconds"] == {
        "source": "env",
        "envValue": 0.0,
        "default": 8.0,
    }

    # The same value is not accepted as a dashboard value.
    rejected = await async_client.put("/api/settings", json={"upstreamConnectTimeoutSeconds": 0})
    assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_settings_api_rejects_timeouts_that_break_invariants_against_effective_values(async_client):
    # Connect timeout above the (default 600 s) effective proxy budget.
    response = await async_client.put("/api/settings", json={"upstreamConnectTimeoutSeconds": 700})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "timeout_invariant_violation"
    assert "upstream-connect-within-proxy-budget" in body["error"]["message"]

    # Proxy budget below the (environment-only) admission wait of 10 s.
    response = await async_client.put("/api/settings", json={"proxyRequestBudgetSeconds": 5})
    assert response.status_code == 400
    assert "admission-wait-within-proxy-budget" in response.json()["error"]["message"]

    # Raising the budgets in the same PUT satisfies the rules on the effective values.
    response = await async_client.put(
        "/api/settings",
        json={
            "upstreamConnectTimeoutSeconds": 130,
            "compactRequestBudgetSeconds": 200,
            "transcriptionRequestBudgetSeconds": 150,
            "proxyRequestBudgetSeconds": 700,
        },
    )
    assert response.status_code == 200
    assert response.json()["upstreamConnectTimeoutSeconds"] == 130.0

    # Lowering one budget below the stored connect timeout is rejected on the
    # effective (dashboard) values, not the environment ones.
    response = await async_client.put("/api/settings", json={"compactRequestBudgetSeconds": 100})
    assert response.status_code == 400
    assert "upstream-connect-within-compact-budget" in response.json()["error"]["message"]

    # Out-of-range scalars are schema errors.
    response = await async_client.put("/api/settings", json={"streamIdleTimeoutSeconds": 0})
    assert response.status_code == 422
    response = await async_client.put("/api/settings", json={"sseKeepaliveIntervalSeconds": -1})
    assert response.status_code == 422


# M1 stream/bridge budgets
@pytest.mark.asyncio
async def test_settings_api_stream_and_bridge_budget_round_trip_with_provenance_and_null_clear(
    async_client, monkeypatch
):
    from app.modules.settings import service as settings_service

    initial = await async_client.get("/api/settings")
    assert initial.status_code == 200
    payload = initial.json()
    assert payload["httpResponsesStreamRequestBudgetSeconds"] == 7200.0
    assert payload["httpResponsesSessionBridgeRequestBudgetSeconds"] == 7200.0
    assert payload["provenance"]["http_responses_stream_request_budget_seconds"] == {
        "source": "default",
        "envValue": 7200.0,
        "default": 7200.0,
    }
    assert payload["provenance"]["http_responses_session_bridge_request_budget_seconds"]["source"] == "default"

    configured = await async_client.put(
        "/api/settings",
        json={"httpResponsesStreamRequestBudgetSeconds": 3600, "httpResponsesSessionBridgeRequestBudgetSeconds": 5400},
    )
    assert configured.status_code == 200
    configured_payload = configured.json()
    assert configured_payload["httpResponsesStreamRequestBudgetSeconds"] == 3600.0
    assert configured_payload["httpResponsesSessionBridgeRequestBudgetSeconds"] == 5400.0
    assert configured_payload["provenance"]["http_responses_stream_request_budget_seconds"]["source"] == "dashboard"
    assert (
        configured_payload["provenance"]["http_responses_session_bridge_request_budget_seconds"]["source"]
        == "dashboard"
    )

    async with SessionLocal() as session:
        row = await session.get(DashboardSettings, 1)
        assert row is not None
        assert row.http_responses_stream_request_budget_seconds == 3600.0
        assert row.http_responses_session_bridge_request_budget_seconds == 5400.0
        assert row.proxy_request_budget_seconds is None

    # Omitting the fields leaves them untouched.
    unchanged = await async_client.put("/api/settings", json={"warmupModel": "gpt-5.6-sol"})
    assert unchanged.status_code == 200
    assert unchanged.json()["httpResponsesStreamRequestBudgetSeconds"] == 3600.0
    assert unchanged.json()["httpResponsesSessionBridgeRequestBudgetSeconds"] == 5400.0

    # null clears the column: the stream budget inherits an environment value
    # that differs from the default, the bridge budget falls back to the default.
    inherited = settings_service.get_settings().model_copy(
        update={"http_responses_stream_request_budget_seconds": 5000.0}
    )
    monkeypatch.setattr(settings_service, "get_settings", lambda: inherited)
    cleared = await async_client.put(
        "/api/settings",
        json={"httpResponsesStreamRequestBudgetSeconds": None, "httpResponsesSessionBridgeRequestBudgetSeconds": None},
    )
    assert cleared.status_code == 200
    cleared_payload = cleared.json()
    assert cleared_payload["httpResponsesStreamRequestBudgetSeconds"] == 5000.0
    assert cleared_payload["provenance"]["http_responses_stream_request_budget_seconds"] == {
        "source": "env",
        "envValue": 5000.0,
        "default": 7200.0,
    }
    assert cleared_payload["httpResponsesSessionBridgeRequestBudgetSeconds"] == 7200.0
    assert cleared_payload["provenance"]["http_responses_session_bridge_request_budget_seconds"]["source"] == "default"


@pytest.mark.asyncio
async def test_settings_api_rejects_stream_and_bridge_budgets_that_break_invariants(async_client):
    # A connect timeout that fits the proxy/compact/transcription budgets but
    # not the stream budget stored in the same PUT (upstream-connect-within-stream-budget).
    response = await async_client.put(
        "/api/settings",
        json={"upstreamConnectTimeoutSeconds": 100, "httpResponsesStreamRequestBudgetSeconds": 60},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "timeout_invariant_violation"
    assert "upstream-connect-within-stream-budget" in body["error"]["message"]
    assert "upstream-connect-within-proxy-budget" not in body["error"]["message"]

    # The bridge budget must exceed twice the fixed 300 s stuck-gate threshold.
    response = await async_client.put("/api/settings", json={"httpResponsesSessionBridgeRequestBudgetSeconds": 600})
    assert response.status_code == 400
    assert "bridge-stuck-gate-retire-within-bridge-budget" in response.json()["error"]["message"]
    current = await async_client.get("/api/settings")
    assert current.json()["provenance"]["http_responses_session_bridge_request_budget_seconds"]["source"] == "default"

    response = await async_client.put("/api/settings", json={"httpResponsesSessionBridgeRequestBudgetSeconds": 601})
    assert response.status_code == 200
    assert response.json()["httpResponsesSessionBridgeRequestBudgetSeconds"] == 601.0

    # The bridge path spends the connect timeout inside the bridge budget, so a
    # connect timeout above it is rejected too. The sibling budgets are raised in
    # the same PUT, leaving `upstream-connect-within-bridge-budget` as the only
    # violation the change introduces.
    response = await async_client.put(
        "/api/settings",
        json={
            "upstreamConnectTimeoutSeconds": 700,
            "proxyRequestBudgetSeconds": 5000,
            "compactRequestBudgetSeconds": 5000,
            "transcriptionRequestBudgetSeconds": 5000,
            "httpResponsesSessionBridgeRequestBudgetSeconds": 650,
        },
    )
    assert response.status_code == 400
    message = response.json()["error"]["message"]
    assert "upstream-connect-within-bridge-budget" in message
    assert "upstream-connect-within-proxy-budget" not in message
    assert "upstream-connect-within-stream-budget" not in message

    # Stream budget below the environment-only admission wait (10 s).
    response = await async_client.put("/api/settings", json={"httpResponsesStreamRequestBudgetSeconds": 5})
    assert response.status_code == 400
    assert "admission-wait-within-stream-budget" in response.json()["error"]["message"]

    # Out-of-range scalars are schema errors.
    response = await async_client.put("/api/settings", json={"httpResponsesStreamRequestBudgetSeconds": 0})
    assert response.status_code == 422
    response = await async_client.put("/api/settings", json={"httpResponsesSessionBridgeRequestBudgetSeconds": 86401})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_stream_request_deadline_honours_dashboard_budget_over_environment(async_client):
    """A dashboard stream budget (3600 s) beats the 7200 s environment value without a restart.

    The value is stored through the settings API, read back from the
    ``SettingsCache`` snapshot and bound the way ``DashboardOverridesMiddleware``
    binds it for a request; the proxy service settings facade that
    ``_stream_request_budget_seconds`` reads then yields the dashboard value.
    Outside the binding the environment value keeps applying.
    """
    from app.core.config.dashboard_overrides import dashboard_overrides_bound
    from app.modules.proxy import service as proxy_service_module
    from app.modules.proxy._service.streaming.helpers import _stream_request_budget_seconds

    assert proxy_service_module.get_settings().http_responses_stream_request_budget_seconds == 7200.0
    response = await async_client.put("/api/settings", json={"httpResponsesStreamRequestBudgetSeconds": 3600})
    assert response.status_code == 200
    snapshot = await get_settings_cache().get()
    assert snapshot.http_responses_stream_request_budget_seconds == 3600.0

    try:
        with dashboard_overrides_bound(snapshot):
            bound = proxy_service_module.get_settings()
            assert bound.http_responses_stream_request_budget_seconds == 3600.0
            assert _stream_request_budget_seconds(bound, request_transport="http") == 3600.0
            assert _stream_request_budget_seconds(bound, request_transport="websocket") == 3600.0
        unbound = proxy_service_module.get_settings()
        assert _stream_request_budget_seconds(unbound, request_transport="http") == 7200.0
    finally:
        # The process-wide SettingsCache outlives the per-test database.
        await async_client.put("/api/settings", json={"httpResponsesStreamRequestBudgetSeconds": None})
        await get_settings_cache().invalidate(propagate=False)


# end M1 stream/bridge budgets


@pytest.mark.asyncio
async def test_unrelated_settings_update_preserves_inherited_account_cap_nulls(async_client, monkeypatch):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200

    async with SessionLocal() as session:
        settings = await session.get(DashboardSettings, 1)
        assert settings is not None
        settings.proxy_account_response_create_limit = None
        settings.proxy_account_stream_limit = None
        settings.proxy_account_stream_recovery_reserve = None
        settings.proxy_api_key_fair_share_congestion_threshold_pct = None
        await session.commit()
    await get_settings_cache().invalidate()

    from app.modules.settings import service as settings_service

    inherited = settings_service.get_settings().model_copy(
        update={
            "proxy_account_stream_limit": 1,
            "proxy_account_stream_recovery_reserve": 2,
        }
    )
    monkeypatch.setattr(settings_service, "get_settings", lambda: inherited)

    response = await async_client.put("/api/settings", json={"warmupModel": "gpt-5.6-sol"})
    assert response.status_code == 200
    assert response.json()["warmupModel"] == "gpt-5.6-sol"

    async with SessionLocal() as session:
        settings = await session.get(DashboardSettings, 1)
        assert settings is not None
        assert settings.proxy_account_response_create_limit is None
        assert settings.proxy_account_stream_limit is None
        assert settings.proxy_account_stream_recovery_reserve is None
        assert settings.proxy_api_key_fair_share_congestion_threshold_pct is None


@pytest.mark.asyncio
async def test_settings_api_accepts_fill_first_routing_strategy(async_client):
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": True,
            "preferEarlierResetAccounts": True,
            "routingStrategy": "fill_first",
        },
    )
    assert response.status_code == 200
    assert response.json()["routingStrategy"] == "fill_first"

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    assert response.json()["routingStrategy"] == "fill_first"


@pytest.mark.asyncio
async def test_settings_api_rejects_stream_recovery_reserve_above_bounded_stream_cap(async_client):
    response = await async_client.put(
        "/api/settings",
        json={
            "proxyAccountStreamLimit": 2,
            "proxyAccountStreamRecoveryReserve": 3,
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_proxy_account_stream_recovery_reserve"

    unlimited = await async_client.put(
        "/api/settings",
        json={
            "proxyAccountStreamLimit": 0,
            "proxyAccountStreamRecoveryReserve": 3,
        },
    )

    assert unlimited.status_code == 200
    assert unlimited.json()["proxyAccountStreamLimit"] == 0
    assert unlimited.json()["proxyAccountStreamRecoveryReserve"] == 3


@pytest.mark.asyncio
async def test_settings_api_rejects_clear_that_would_violate_environment_capacity(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
):
    async with SessionLocal() as session:
        settings = await session.get(DashboardSettings, 1)
        assert settings is not None
        settings.proxy_account_stream_limit = 24
        settings.proxy_account_stream_recovery_reserve = 3
        await session.commit()
    await get_settings_cache().invalidate()

    startup_settings = settings_api_module.get_app_settings()
    monkeypatch.setattr(
        settings_api_module,
        "get_app_settings",
        lambda: startup_settings.model_copy(update={"proxy_account_stream_limit": 2}),
    )

    response = await async_client.put("/api/settings", json={"proxyAccountStreamLimit": None})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_proxy_account_stream_recovery_reserve"

    async with SessionLocal() as session:
        settings = await session.get(DashboardSettings, 1)
        assert settings is not None
        assert settings.proxy_account_stream_limit == 24
        assert settings.proxy_account_stream_recovery_reserve == 3


@pytest.mark.asyncio
async def test_settings_api_returns_known_additional_quota_policies(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()

    assert payload["additionalQuotaRoutingPolicies"] == {}
    assert payload["additionalQuotaPolicies"] == [
        {
            "quotaKey": "codex_spark",
            "displayLabel": "GPT-5.3-Codex-Spark",
            "routingPolicy": "burn_first",
            "modelIds": ["gpt_5_3_codex_spark"],
        }
    ]

    update_payload = {
        "stickyThreadsEnabled": payload["stickyThreadsEnabled"],
        "preferEarlierResetAccounts": payload["preferEarlierResetAccounts"],
        "additionalQuotaRoutingPolicies": {"codex_spark": "preserve"},
    }
    response = await async_client.put("/api/settings", json=update_payload)
    assert response.status_code == 200
    updated = response.json()
    assert updated["additionalQuotaRoutingPolicies"] == {"codex_spark": "preserve"}
    assert updated["additionalQuotaPolicies"][0]["routingPolicy"] == "preserve"

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    persisted = response.json()
    assert persisted["additionalQuotaRoutingPolicies"] == {"codex_spark": "preserve"}
    assert persisted["additionalQuotaPolicies"][0]["routingPolicy"] == "preserve"


@pytest.mark.asyncio
async def test_settings_legacy_sticky_threshold_updates_primary_threshold(async_client):
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": True,
            "preferEarlierResetAccounts": True,
            "stickyReallocationBudgetThresholdPct": 88.0,
        },
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["stickyReallocationBudgetThresholdPct"] == 88.0
    assert updated["stickyReallocationPrimaryBudgetThresholdPct"] == 88.0
    assert updated["stickyReallocationSecondaryBudgetThresholdPct"] == 100.0


@pytest.mark.asyncio
async def test_settings_primary_sticky_threshold_updates_legacy_threshold(async_client):
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": True,
            "preferEarlierResetAccounts": True,
            "stickyReallocationPrimaryBudgetThresholdPct": 87.0,
        },
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["stickyReallocationBudgetThresholdPct"] == 87.0
    assert updated["stickyReallocationPrimaryBudgetThresholdPct"] == 87.0
    assert updated["stickyReallocationSecondaryBudgetThresholdPct"] == 100.0


@pytest.mark.asyncio
async def test_settings_api_rejects_unknown_routing_strategy(async_client):
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": True,
            "preferEarlierResetAccounts": True,
            "routingStrategy": "fill_last",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_settings_full_put_rejects_conflicting_sticky_threshold_aliases(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    payload["stickyReallocationBudgetThresholdPct"] = 86.0

    response = await async_client.put("/api/settings", json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "conflicting_sticky_reallocation_thresholds"


@pytest.mark.asyncio
async def test_settings_full_put_allows_unrelated_save_with_divergent_sticky_thresholds(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200

    async with SessionLocal() as session:
        await session.execute(
            text(
                """
                UPDATE dashboard_settings
                SET sticky_reallocation_budget_threshold_pct = 82.0,
                    sticky_reallocation_primary_budget_threshold_pct = 91.0
                WHERE id = 1
                """
            )
        )
        await session.commit()

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["stickyReallocationBudgetThresholdPct"] == 82.0
    assert payload["stickyReallocationPrimaryBudgetThresholdPct"] == 91.0
    payload["importWithoutOverwrite"] = not payload["importWithoutOverwrite"]

    response = await async_client.put("/api/settings", json=payload)

    assert response.status_code == 200
    updated = response.json()
    assert updated["importWithoutOverwrite"] == payload["importWithoutOverwrite"]
    assert updated["stickyReallocationBudgetThresholdPct"] == 82.0
    assert updated["stickyReallocationPrimaryBudgetThresholdPct"] == 91.0


@pytest.mark.asyncio
async def test_settings_full_put_rejects_out_of_range_sticky_threshold(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    payload["stickyReallocationBudgetThresholdPct"] = 101.0

    response = await async_client.put("/api/settings", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_settings_api_rejects_out_of_range_api_key_fair_share_threshold(async_client):
    response = await async_client.put(
        "/api/settings",
        json={"proxyApiKeyFairShareCongestionThresholdPct": 101},
    )

    assert response.status_code == 422

    negative = await async_client.put(
        "/api/settings",
        json={"proxyApiKeyFairShareCongestionThresholdPct": -1},
    )

    assert negative.status_code == 422


@pytest.mark.asyncio
async def test_settings_api_allows_partial_updates(async_client):
    original_response = await async_client.get("/api/settings")
    assert original_response.status_code == 200
    original = original_response.json()

    response = await async_client.put(
        "/api/settings",
        json={"warmupModel": "gpt-5.4-pro"},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["warmupModel"] == "gpt-5.4-pro"
    assert updated["stickyThreadsEnabled"] == original["stickyThreadsEnabled"]
    assert updated["preferEarlierResetAccounts"] == original["preferEarlierResetAccounts"]
    assert updated["routingStrategy"] == original["routingStrategy"]
    assert updated["upstreamProxyRoutingEnabled"] == original["upstreamProxyRoutingEnabled"]
    assert updated["upstreamProxyDefaultPoolId"] == original["upstreamProxyDefaultPoolId"]
    assert updated["hideUpstreamQuotaFromApiKeys"] == original["hideUpstreamQuotaFromApiKeys"]


@pytest.mark.asyncio
async def test_settings_api_rejects_invalid_weekly_pace_working_days(async_client):
    response = await async_client.put(
        "/api/settings",
        json={"weeklyPaceWorkingDays": "0,1,7"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_settings_api_rejects_invalid_weekly_pace_smoothing_minutes(async_client):
    response = await async_client.put(
        "/api/settings",
        json={"weeklyPaceSmoothingMinutes": 45},
    )

    assert response.status_code == 422


async def test_upstream_proxy_admin_controls(async_client):
    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={
            "name": "Proxy A",
            "scheme": "https",
            "host": "proxy.internal",
            "port": 8080,
            "username": "user",
            "password": "secret",
        },
    )
    assert endpoint.status_code == 200
    endpoint_payload = endpoint.json()
    assert endpoint_payload["host"] == "proxy.internal"
    assert "password" not in endpoint_payload

    pool = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "Pool A", "endpointIds": [endpoint_payload["id"]]},
    )
    assert pool.status_code == 200
    pool_payload = pool.json()
    assert pool_payload["endpointIds"] == [endpoint_payload["id"]]

    settings = await async_client.get("/api/settings")
    body = settings.json()
    body["upstreamProxyRoutingEnabled"] = True
    body["upstreamProxyDefaultPoolId"] = pool_payload["id"]
    updated = await async_client.put("/api/settings", json=body)
    assert updated.status_code == 200
    assert updated.json()["upstreamProxyDefaultPoolId"] == pool_payload["id"]

    body["upstreamProxyDefaultPoolId"] = None
    cleared = await async_client.put("/api/settings", json=body)
    assert cleared.status_code == 200
    assert cleared.json()["upstreamProxyDefaultPoolId"] is None

    body["upstreamProxyDefaultPoolId"] = pool_payload["id"]
    updated = await async_client.put("/api/settings", json=body)
    assert updated.status_code == 200

    admin = await async_client.get("/api/settings/upstream-proxy")
    assert admin.status_code == 200
    admin_payload = admin.json()
    assert admin_payload["routingEnabled"] is True
    assert admin_payload["defaultPoolId"] == pool_payload["id"]
    assert admin_payload["endpoints"][0]["id"] == endpoint_payload["id"]
    assert admin_payload["pools"][0]["endpointIds"] == [endpoint_payload["id"]]


@pytest.mark.asyncio
async def test_upstream_proxy_endpoint_test_probes_configured_proxy(async_client, monkeypatch):
    captured: dict[str, object] = {}

    class _Response:
        status_code = 204

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            captured["url"] = url
            return _Response()

    monkeypatch.setattr("app.modules.settings.api.httpx.AsyncClient", _FakeAsyncClient)

    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={
            "name": "Proxy A",
            "scheme": "https",
            "host": "proxy.internal",
            "port": 8080,
            "username": "user",
            "password": "secret",
        },
    )
    assert endpoint.status_code == 200

    response = await async_client.post(
        f"/api/settings/upstream-proxy/endpoints/{endpoint.json()['id']}/test",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["statusCode"] == 204
    assert payload["error"] is None
    client_kwargs = cast(dict[str, Any], captured["client_kwargs"])
    assert captured["url"] == "https://chatgpt.com/cdn-cgi/trace"
    assert client_kwargs["proxy"] == "https://user:secret@proxy.internal:8080"
    assert "secret" not in str(payload)


@pytest.mark.asyncio
async def test_upstream_proxy_endpoint_test_rejects_proxy_auth_response(async_client, monkeypatch):
    class _Response:
        status_code = 407

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            return _Response()

    monkeypatch.setattr("app.modules.settings.api.httpx.AsyncClient", _FakeAsyncClient)

    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={
            "name": "Proxy Auth",
            "scheme": "https",
            "host": "proxy.internal",
            "port": 8080,
            "username": "user",
            "password": "wrong",
        },
    )
    assert endpoint.status_code == 200

    response = await async_client.post(
        f"/api/settings/upstream-proxy/endpoints/{endpoint.json()['id']}/test",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["statusCode"] == 407
    assert payload["error"] == "proxy_auth_failed"
    assert "wrong" not in str(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["http", "socks5", "socks5h"])
async def test_upstream_proxy_endpoint_create_allows_plaintext_credentials_with_warning_flag(async_client, scheme: str):
    response = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={
            "name": "Plaintext proxy",
            "scheme": scheme,
            "host": "proxy.internal",
            "port": 8080,
            "username": "user",
            "password": "secret",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["plaintextCredentials"] is True
    assert "secret" not in str(payload)

    listing = await async_client.get("/api/settings/upstream-proxy")
    assert listing.status_code == 200
    flags = {endpoint["id"]: endpoint["plaintextCredentials"] for endpoint in listing.json()["endpoints"]}
    assert flags[payload["id"]] is True


@pytest.mark.asyncio
async def test_upstream_proxy_endpoint_https_credentials_are_not_flagged(async_client):
    response = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={
            "name": "TLS proxy",
            "scheme": "https",
            "host": "proxy.internal",
            "port": 8443,
            "username": "user",
            "password": "secret",
        },
    )

    assert response.status_code == 200
    assert response.json()["plaintextCredentials"] is False


@pytest.mark.asyncio
async def test_upstream_proxy_endpoint_create_rejects_colon_in_username(async_client):
    response = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={
            "name": "Colon proxy",
            "scheme": "https",
            "host": "proxy.internal",
            "port": 8080,
            "username": "user:name",
            "password": "secret",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_proxy_username"


@pytest.mark.asyncio
async def test_upstream_proxy_endpoint_test_reports_unresolvable_persisted_row(async_client):
    # A row persisted before the resolver rule existed must report the reason,
    # not surface an unhandled 500 from the test route.
    async with SessionLocal() as session:
        row = ProxyEndpoint(name="Legacy", scheme="https", host="proxy.internal", port=8080, username="user:name")
        session.add(row)
        await session.commit()
        endpoint_id = row.id

    response = await async_client.post(f"/api/settings/upstream-proxy/endpoints/{endpoint_id}/test")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"] == "invalid_proxy_username"
    assert payload["statusCode"] is None


@pytest.mark.asyncio
async def test_upstream_proxy_endpoint_test_probes_socks_proxy(async_client, monkeypatch):
    captured: dict[str, object] = {}

    class _Response:
        status = 204

    class _FakeConnector:
        def __init__(self, **kwargs):
            captured["connector_kwargs"] = kwargs

    class _FakeAiohttpSession:
        def __init__(self, **kwargs):
            captured["session_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, **kwargs):
            captured["url"] = url
            captured["get_kwargs"] = kwargs
            return _Response()

    monkeypatch.setattr("app.modules.settings.api.ProxyConnector", _FakeConnector)
    monkeypatch.setattr("app.modules.settings.api.aiohttp.ClientSession", _FakeAiohttpSession)

    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={
            "name": "Proxy A",
            "scheme": "socks5",
            "host": "proxy.internal",
            "port": 1080,
        },
    )
    assert endpoint.status_code == 200

    response = await async_client.post(
        f"/api/settings/upstream-proxy/endpoints/{endpoint.json()['id']}/test",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["statusCode"] == 204
    assert payload["error"] is None
    connector_kwargs = cast(dict[str, Any], captured["connector_kwargs"])
    session_kwargs = cast(dict[str, Any], captured["session_kwargs"])
    assert captured["url"] == "https://chatgpt.com/cdn-cgi/trace"
    assert cast(dict[str, Any], captured["get_kwargs"])["allow_redirects"] is False
    assert connector_kwargs["host"] == "proxy.internal"
    assert connector_kwargs["port"] == 1080
    assert connector_kwargs["rdns"] is True
    assert session_kwargs["trust_env"] is False


@pytest.mark.asyncio
async def test_upstream_proxy_pool_rejects_missing_endpoint(async_client):
    response = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "Broken Pool", "endpointIds": ["missing-endpoint"]},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "proxy_endpoint_not_found"


@pytest.mark.asyncio
async def test_upstream_proxy_pool_member_rejects_missing_endpoint(async_client):
    pool = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "Pool A", "endpointIds": []},
    )
    assert pool.status_code == 200

    response = await async_client.post(
        f"/api/settings/upstream-proxy/pools/{pool.json()['id']}/members",
        json={"endpointId": "missing-endpoint"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "proxy_endpoint_not_found"


@pytest.mark.asyncio
async def test_upstream_proxy_pool_member_rejects_duplicate_endpoint(async_client):
    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={"name": "Proxy A", "scheme": "http", "host": "proxy.internal", "port": 8080},
    )
    assert endpoint.status_code == 200
    endpoint_id = endpoint.json()["id"]
    pool = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "Pool A", "endpointIds": [endpoint_id]},
    )
    assert pool.status_code == 200

    response = await async_client.post(
        f"/api/settings/upstream-proxy/pools/{pool.json()['id']}/members",
        json={"endpointId": endpoint_id},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "proxy_pool_member_duplicate"


@pytest.mark.asyncio
async def test_settings_update_rejects_missing_default_proxy_pool(async_client):
    settings = await async_client.get("/api/settings")
    body = settings.json()
    body["upstreamProxyDefaultPoolId"] = "missing-pool"

    response = await async_client.put("/api/settings", json=body)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "proxy_pool_not_found"


@pytest.mark.asyncio
async def test_account_proxy_binding_rejects_missing_targets(async_client):
    missing_account = await async_client.put(
        "/api/settings/upstream-proxy/accounts/missing-account/binding",
        json={"poolId": "missing-pool", "isActive": True},
    )
    assert missing_account.status_code == 400
    assert missing_account.json()["error"]["code"] == "account_not_found"

    account_id = await _import_account(async_client, "acc-settings-proxy-binding", "settings-proxy@example.com")
    missing_pool = await async_client.put(
        f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
        json={"poolId": "missing-pool", "isActive": True},
    )
    assert missing_pool.status_code == 400
    assert missing_pool.json()["error"]["code"] == "proxy_pool_not_found"


@pytest.mark.asyncio
async def test_account_proxy_binding_reactivates_proxy_unreachable_account(async_client):
    from app.modules.proxy.account_cache import (
        get_account_selection_cache,
        is_account_routing_unavailable,
        mark_account_routing_unavailable,
    )

    cache_generation = get_account_selection_cache().generation
    account_id = await _import_account(async_client, "acc-settings-proxy-repair", "settings-proxy-repair@example.com")
    mark_account_routing_unavailable(account_id)
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        account.status = AccountStatus.DEACTIVATED
        account.deactivation_reason = "proxy_unreachable: ProxyConnectionError - connection refused"
        await session.commit()

    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={"name": "repair proxy", "scheme": "http", "host": "proxy.test", "port": 8080},
    )
    assert endpoint.status_code == 200
    pool = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "repair pool", "endpointIds": [endpoint.json()["id"]]},
    )
    assert pool.status_code == 200
    binding = await async_client.put(
        f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
        json={"poolId": pool.json()["id"], "isActive": True},
    )

    assert binding.status_code == 200
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        assert account.status == AccountStatus.ACTIVE
        assert account.deactivation_reason is None
    assert get_account_selection_cache().generation > cache_generation
    assert is_account_routing_unavailable(account_id) is False


@pytest.mark.asyncio
async def test_account_proxy_binding_reactivation_invalidates_after_commit(async_client, monkeypatch):
    """Regression: the reactivation path must invalidate the selection cache (and
    enqueue its coalesced ``account_selection`` bump) only AFTER the status commit.

    If ``invalidate()`` runs before ``session.commit()``, the poller can flush the
    pending bump while the reactivation is still uncommitted, so a peer rebuilds
    selection/routing inputs from the pre-commit DEACTIVATED row. We assert the
    request-scoped ``after_commit`` fires before ``invalidate()`` is called.
    """
    from sqlalchemy import event
    from sqlalchemy.orm import Session as SyncSession

    from app.modules.proxy.account_cache import (
        get_account_selection_cache,
        mark_account_routing_unavailable,
    )

    account_id = await _import_account(async_client, "acc-settings-proxy-order", "settings-proxy-order@example.com")
    mark_account_routing_unavailable(account_id)
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        account.status = AccountStatus.DEACTIVATED
        account.deactivation_reason = "proxy_unreachable: ProxyConnectionError - connection refused"
        await session.commit()

    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={"name": "order proxy", "scheme": "http", "host": "proxy.test", "port": 8080},
    )
    assert endpoint.status_code == 200
    pool = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "order pool", "endpointIds": [endpoint.json()["id"]]},
    )
    assert pool.status_code == 200

    cache = get_account_selection_cache()
    original_invalidate = cache.invalidate
    sequence: list[str] = []
    armed = {"on": False}

    def _after_commit(_session: SyncSession) -> None:
        if armed["on"]:
            sequence.append("commit")

    def _spy_invalidate(*args, **kwargs):
        if armed["on"]:
            sequence.append("invalidate")
        return original_invalidate(*args, **kwargs)

    monkeypatch.setattr(cache, "invalidate", _spy_invalidate)
    event.listen(SyncSession, "after_commit", _after_commit)
    armed["on"] = True
    try:
        binding = await async_client.put(
            f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
            json={"poolId": pool.json()["id"], "isActive": True},
        )
    finally:
        armed["on"] = False
        event.remove(SyncSession, "after_commit", _after_commit)

    assert binding.status_code == 200
    assert "invalidate" in sequence, "reactivation must invalidate the selection cache"
    assert "commit" in sequence, "reactivation must commit the status change"
    # The status commit must land before the invalidate/bump so peers re-read the
    # committed (ACTIVE) row, never the pre-commit DEACTIVATED one.
    assert sequence.index("commit") < sequence.index("invalidate")


@pytest.mark.asyncio
async def test_account_proxy_binding_closes_existing_bridge_sessions(async_client, monkeypatch):
    close_sessions = AsyncMock()
    monkeypatch.setattr(
        "app.modules.settings.api.get_proxy_service_for_app",
        lambda _app: type("_ProxyService", (), {"close_http_bridge_sessions_for_account": close_sessions})(),
    )
    account_id = await _import_account(async_client, "acc-settings-proxy-close", "settings-proxy-close@example.com")
    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={"name": "close proxy", "scheme": "http", "host": "proxy.test", "port": 8080},
    )
    assert endpoint.status_code == 200
    pool = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "close pool", "endpointIds": [endpoint.json()["id"]]},
    )
    assert pool.status_code == 200

    binding = await async_client.put(
        f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
        json={"poolId": pool.json()["id"], "isActive": True},
    )

    assert binding.status_code == 200
    close_sessions.assert_awaited_once_with(account_id)


@pytest.mark.asyncio
async def test_account_proxy_binding_disable_closes_existing_bridge_sessions(async_client, monkeypatch):
    close_sessions = AsyncMock()
    monkeypatch.setattr(
        "app.modules.settings.api.get_proxy_service_for_app",
        lambda _app: type("_ProxyService", (), {"close_http_bridge_sessions_for_account": close_sessions})(),
    )
    account_id = await _import_account(
        async_client,
        "acc-settings-proxy-disable-close",
        "settings-proxy-disable-close@example.com",
    )
    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={"name": "disable close proxy", "scheme": "http", "host": "proxy.test", "port": 8080},
    )
    assert endpoint.status_code == 200
    pool = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "disable close pool", "endpointIds": [endpoint.json()["id"]]},
    )
    assert pool.status_code == 200
    enabled = await async_client.put(
        f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
        json={"poolId": pool.json()["id"], "isActive": True},
    )
    assert enabled.status_code == 200
    close_sessions.reset_mock()

    disabled = await async_client.put(
        f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
        json={"poolId": pool.json()["id"], "isActive": False},
    )

    assert disabled.status_code == 200
    close_sessions.assert_awaited_once_with(account_id)


@pytest.mark.asyncio
async def test_account_proxy_binding_rebind_active_account_closes_bridge_sessions(async_client, monkeypatch):
    close_sessions = AsyncMock()
    monkeypatch.setattr(
        "app.modules.settings.api.get_proxy_service_for_app",
        lambda _app: type("_ProxyService", (), {"close_http_bridge_sessions_for_account": close_sessions})(),
    )

    account_id = await _import_account(
        async_client,
        "acc-settings-proxy-rebind-close",
        "settings-proxy-rebind-close@example.com",
    )

    endpoint_one = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={"name": "rebind close proxy one", "scheme": "http", "host": "proxy.test", "port": 8080},
    )
    assert endpoint_one.status_code == 200
    pool_one = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "rebind close pool one", "endpointIds": [endpoint_one.json()["id"]]},
    )
    assert pool_one.status_code == 200

    endpoint_two = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={"name": "rebind close proxy two", "scheme": "http", "host": "proxy-2.test", "port": 8080},
    )
    assert endpoint_two.status_code == 200
    pool_two = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "rebind close pool two", "endpointIds": [endpoint_two.json()["id"]]},
    )
    assert pool_two.status_code == 200

    first_binding = await async_client.put(
        f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
        json={"poolId": pool_one.json()["id"], "isActive": True},
    )
    assert first_binding.status_code == 200
    close_sessions.assert_awaited_once_with(account_id)
    close_sessions.reset_mock()

    rebinding = await async_client.put(
        f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
        json={"poolId": pool_two.json()["id"], "isActive": True},
    )
    assert rebinding.status_code == 200
    close_sessions.assert_awaited_once_with(account_id)


@pytest.mark.asyncio
async def test_account_proxy_binding_does_not_reactivate_session_deactivated_account(async_client):
    account_id = await _import_account(async_client, "acc-settings-proxy-reauth", "settings-proxy-reauth@example.com")
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        account.status = AccountStatus.DEACTIVATED
        account.deactivation_reason = "ChatGPT session ended - re-login required"
        await session.commit()

    endpoint = await async_client.post(
        "/api/settings/upstream-proxy/endpoints",
        json={"name": "reauth proxy", "scheme": "http", "host": "proxy.test", "port": 8080},
    )
    assert endpoint.status_code == 200
    pool = await async_client.post(
        "/api/settings/upstream-proxy/pools",
        json={"name": "reauth pool", "endpointIds": [endpoint.json()["id"]]},
    )
    assert pool.status_code == 200
    binding = await async_client.put(
        f"/api/settings/upstream-proxy/accounts/{account_id}/binding",
        json={"poolId": pool.json()["id"], "isActive": True},
    )

    assert binding.status_code == 200
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        assert account.status == AccountStatus.DEACTIVATED
        assert account.deactivation_reason == "ChatGPT session ended - re-login required"


@pytest.mark.asyncio
async def test_settings_api_retention_override_update_persists_and_round_trips(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    # Fresh row has NULL overrides and the test env sets no alias: effective 0.
    assert body["requestLogRetentionDays"] == 0
    assert body["usageHistoryRetentionDays"] == 0
    assert body["requestLogRetentionOverrideDays"] is None
    assert body["usageHistoryRetentionOverrideDays"] is None

    response = await async_client.put(
        "/api/settings",
        json={"requestLogRetentionOverrideDays": 30, "usageHistoryRetentionOverrideDays": 45},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["requestLogRetentionDays"] == 30
    assert body["usageHistoryRetentionDays"] == 45
    assert body["requestLogRetentionOverrideDays"] == 30
    assert body["usageHistoryRetentionOverrideDays"] == 45

    async with SessionLocal() as session:
        settings = await session.get(DashboardSettings, 1)
        assert settings is not None
        assert settings.request_log_retention_days == 30
        assert settings.usage_history_retention_days == 45

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["requestLogRetentionDays"] == 30
    assert body["usageHistoryRetentionDays"] == 45
    assert body["requestLogRetentionOverrideDays"] == 30
    assert body["usageHistoryRetentionOverrideDays"] == 45


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"requestLogRetentionOverrideDays": 7},  # below the 30-day floor
        {"requestLogRetentionOverrideDays": 3651},  # above the 3650 cap
        {"requestLogRetentionOverrideDays": -1},
        {"usageHistoryRetentionOverrideDays": 10},  # below the 45-day floor
        {"usageHistoryRetentionOverrideDays": 3651},
    ],
)
async def test_settings_api_rejects_unsafe_retention_values(async_client, payload):
    response = await async_client.put("/api/settings", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"

    # The stored settings are unchanged (NULL = never configured = disabled).
    async with SessionLocal() as session:
        settings = await session.get(DashboardSettings, 1)
        if settings is not None:
            assert settings.request_log_retention_days is None
            assert settings.usage_history_retention_days is None


@pytest.mark.asyncio
async def test_settings_api_retention_null_reads_as_disabled(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    # NULL (never configured) is reported as the effective value 0 = disabled
    # with the raw override exposed as null.
    assert body["requestLogRetentionDays"] == 0
    assert body["usageHistoryRetentionDays"] == 0
    assert body["requestLogRetentionOverrideDays"] is None
    assert body["usageHistoryRetentionOverrideDays"] is None

    # A stored value is the effective value, including an explicit 0.
    response = await async_client.put(
        "/api/settings",
        json={"requestLogRetentionOverrideDays": 90, "usageHistoryRetentionOverrideDays": 0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["requestLogRetentionDays"] == 90
    assert body["usageHistoryRetentionDays"] == 0
    assert body["requestLogRetentionOverrideDays"] == 90
    assert body["usageHistoryRetentionOverrideDays"] == 0


@pytest.mark.asyncio
async def test_unrelated_settings_update_preserves_inherited_retention_nulls(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200

    response = await async_client.put("/api/settings", json={"warmupModel": "gpt-5.6-sol"})
    assert response.status_code == 200

    async with SessionLocal() as session:
        settings = await session.get(DashboardSettings, 1)
        assert settings is not None
        assert settings.request_log_retention_days is None
        assert settings.usage_history_retention_days is None


@pytest.mark.asyncio
async def test_retention_override_tri_state_echo_capture_and_clear(async_client):
    """Override semantics: null echoes round-trip, a present value is stored,
    and present-null clears back to NULL (= disabled)."""
    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["requestLogRetentionDays"] == 0
    assert body["requestLogRetentionOverrideDays"] is None

    # A full-save client echoes the override fields verbatim: null stays null.
    response = await async_client.put(
        "/api/settings",
        json={
            "requestLogRetentionOverrideDays": body["requestLogRetentionOverrideDays"],
            "usageHistoryRetentionOverrideDays": body["usageHistoryRetentionOverrideDays"],
        },
    )
    assert response.status_code == 200
    async with SessionLocal() as session:
        settings = await session.get(DashboardSettings, 1)
        assert settings is not None
        assert settings.request_log_retention_days is None
        assert settings.usage_history_retention_days is None

    response = await async_client.put("/api/settings", json={"requestLogRetentionOverrideDays": 90})
    assert response.status_code == 200
    body = response.json()
    assert body["requestLogRetentionDays"] == 90
    assert body["requestLogRetentionOverrideDays"] == 90
    async with SessionLocal() as session:
        settings = await session.get(DashboardSettings, 1)
        assert settings is not None
        assert settings.request_log_retention_days == 90

    # Present-null clears the stored value; the effective value is disabled again.
    response = await async_client.put("/api/settings", json={"requestLogRetentionOverrideDays": None})
    assert response.status_code == 200
    body = response.json()
    assert body["requestLogRetentionDays"] == 0
    assert body["requestLogRetentionOverrideDays"] is None
    async with SessionLocal() as session:
        settings = await session.get(DashboardSettings, 1)
        assert settings is not None
        assert settings.request_log_retention_days is None


@pytest.mark.asyncio
async def test_auto_redeem_opt_in_rejected_while_reset_credit_polling_disabled(async_client):
    # M2 background jobs: the gate reads the effective (dashboard) toggle, not the env alias.
    disabled = await async_client.put("/api/settings", json={"rateLimitResetCreditsRefreshEnabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["rateLimitResetCreditsRefreshEnabled"] is False

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["autoRedeemResetCreditsBeforeExpiry"] is False
    payload["autoRedeemResetCreditsBeforeExpiry"] = True

    response = await async_client.put("/api/settings", json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "reset_credit_polling_disabled"
    assert "Background jobs" in response.json()["error"]["message"]

    # Re-enabling polling in the same request satisfies the gate: the proposed
    # effective value is what counts, and so does clearing the dashboard value
    # back to the inherited (env default = on) layer.
    both = await async_client.put(
        "/api/settings",
        json={"autoRedeemResetCreditsBeforeExpiry": True, "rateLimitResetCreditsRefreshEnabled": True},
    )
    assert both.status_code == 200
    assert both.json()["autoRedeemResetCreditsBeforeExpiry"] is True
    reset = await async_client.put(
        "/api/settings",
        json={"autoRedeemResetCreditsBeforeExpiry": False, "rateLimitResetCreditsRefreshEnabled": False},
    )
    assert reset.status_code == 200
    cleared = await async_client.put(
        "/api/settings",
        json={"autoRedeemResetCreditsBeforeExpiry": True, "rateLimitResetCreditsRefreshEnabled": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["provenance"]["rate_limit_reset_credits_refresh_enabled"]["source"] == "default"


@pytest.mark.asyncio
async def test_disabling_polling_is_rejected_while_auto_redeem_is_on(async_client):
    """M2 background jobs: the gate is symmetric — the other direction cannot create the same dead state."""
    opt_in = await async_client.put("/api/settings", json={"autoRedeemResetCreditsBeforeExpiry": True})
    assert opt_in.status_code == 200
    assert opt_in.json()["autoRedeemResetCreditsBeforeExpiry"] is True

    response = await async_client.put("/api/settings", json={"rateLimitResetCreditsRefreshEnabled": False})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "reset_credit_polling_disabled"
    current = await async_client.get("/api/settings")
    assert current.json()["rateLimitResetCreditsRefreshEnabled"] is True

    # Turning both off in one request is consistent, so it is accepted.
    both_off = await async_client.put(
        "/api/settings",
        json={"autoRedeemResetCreditsBeforeExpiry": False, "rateLimitResetCreditsRefreshEnabled": False},
    )
    assert both_off.status_code == 200
    assert both_off.json()["rateLimitResetCreditsRefreshEnabled"] is False


@pytest.mark.asyncio
async def test_full_put_with_persisted_auto_redeem_allowed_while_polling_disabled(async_client):
    async with SessionLocal() as session:
        await session.execute(
            text(
                "UPDATE dashboard_settings SET auto_redeem_reset_credits_before_expiry = 1, "
                "rate_limit_reset_credits_refresh_enabled = 0 WHERE id = 1"
            )
        )
        await session.commit()

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["autoRedeemResetCreditsBeforeExpiry"] is True

    response = await async_client.put("/api/settings", json=payload)

    assert response.status_code == 200
    assert response.json()["autoRedeemResetCreditsBeforeExpiry"] is True


@pytest.mark.asyncio
async def test_settings_api_background_job_toggles_round_trip_with_provenance(async_client, monkeypatch):
    """M2 background jobs: default -> dashboard -> cleared/env -> unchanged on omit, plus the topology flag."""
    from app.modules.settings import service as settings_service

    initial = await async_client.get("/api/settings")
    assert initial.status_code == 200
    payload = initial.json()
    for camel in ("authGuardianEnabled", "automationsSchedulerEnabled", "rateLimitResetCreditsRefreshEnabled"):
        assert payload[camel] is True
    assert payload["authGuardianBlockedByTopology"] is False
    for name in ("auth_guardian_enabled", "automations_scheduler_enabled", "rate_limit_reset_credits_refresh_enabled"):
        assert payload["provenance"][name] == {"source": "default", "envValue": True, "default": True}

    # Storing a value (including the inherited one) makes the toggle dashboard-owned.
    stored = await async_client.put(
        "/api/settings",
        json={
            "authGuardianEnabled": True,
            "automationsSchedulerEnabled": False,
            "rateLimitResetCreditsRefreshEnabled": False,
        },
    )
    assert stored.status_code == 200
    stored_payload = stored.json()
    assert stored_payload["authGuardianEnabled"] is True
    assert stored_payload["automationsSchedulerEnabled"] is False
    assert stored_payload["rateLimitResetCreditsRefreshEnabled"] is False
    for name in ("auth_guardian_enabled", "automations_scheduler_enabled", "rate_limit_reset_credits_refresh_enabled"):
        assert stored_payload["provenance"][name]["source"] == "dashboard"

    # Explicit null clears the column; a deprecated env alias that differs from
    # the default is inherited, and the topology gate is reported alongside.
    inherited = settings_service.get_settings().model_copy(
        update={
            "auth_guardian_enabled": False,
            "leader_election_enabled": False,
            "http_responses_session_bridge_instance_ring": ["pod-a", "pod-b"],
        }
    )
    monkeypatch.setattr(settings_service, "get_settings", lambda: inherited)
    cleared = await async_client.put(
        "/api/settings", json={"authGuardianEnabled": None, "rateLimitResetCreditsRefreshEnabled": None}
    )
    assert cleared.status_code == 200
    cleared_payload = cleared.json()
    assert cleared_payload["authGuardianEnabled"] is False
    assert cleared_payload["authGuardianBlockedByTopology"] is True
    assert cleared_payload["provenance"]["auth_guardian_enabled"] == {
        "source": "env",
        "envValue": False,
        "default": True,
    }
    assert cleared_payload["provenance"]["rate_limit_reset_credits_refresh_enabled"] == {
        "source": "default",
        "envValue": True,
        "default": True,
    }
    assert cleared_payload["automationsSchedulerEnabled"] is False
    assert cleared_payload["provenance"]["automations_scheduler_enabled"]["source"] == "dashboard"

    # Omitting the fields (any unrelated save) leaves every toggle as it was.
    unchanged = await async_client.put("/api/settings", json={"warmupModel": "gpt-5.6-sol"})
    assert unchanged.status_code == 200
    unchanged_payload = unchanged.json()
    assert unchanged_payload["provenance"]["auth_guardian_enabled"]["source"] == "env"
    assert unchanged_payload["provenance"]["automations_scheduler_enabled"]["source"] == "dashboard"
    assert unchanged_payload["provenance"]["rate_limit_reset_credits_refresh_enabled"]["source"] == "default"
    async with SessionLocal() as session:
        row = await session.get(DashboardSettings, 1)
        assert row is not None
        assert row.auth_guardian_enabled is None
        assert row.automations_scheduler_enabled is False
        assert row.rate_limit_reset_credits_refresh_enabled is None


# --- C2-2 routing/overload: dashboard-managed routing weights and isolation ---

_ROUTING_OVERLOAD_FIELDS = {
    "proxyOverloadIsolationSeconds": ("proxy_overload_isolation_seconds", 1800),
    "proxyAccountErrorRateWeightingEnabled": ("proxy_account_error_rate_weighting_enabled", True),
    "proxyAccountInflightPenaltyPct": ("proxy_account_inflight_penalty_pct", 2.5),
    "proxyAccountLeaseTokenWeight": ("proxy_account_lease_token_weight", 1.0),
    "proxyAccountLeaseTtlSeconds": ("proxy_account_lease_ttl_seconds", 900.0),
}


@pytest.mark.asyncio
async def test_settings_api_routing_overload_settings_round_trip(async_client, monkeypatch):
    from app.modules.settings import service as settings_service

    # Fresh row: every column is NULL and the test environment leaves the
    # fields at their code defaults.
    initial = await async_client.get("/api/settings")
    assert initial.status_code == 200
    initial_payload = initial.json()
    for camel, (snake, default) in _ROUTING_OVERLOAD_FIELDS.items():
        assert initial_payload[camel] == default
        assert initial_payload["provenance"][snake] == {"source": "default", "envValue": default, "default": default}

    # Dashboard values win, including 0 and false.
    configured = await async_client.put(
        "/api/settings",
        json={
            "proxyOverloadIsolationSeconds": 0,
            "proxyAccountErrorRateWeightingEnabled": False,
            "proxyAccountInflightPenaltyPct": 7.5,
            "proxyAccountLeaseTokenWeight": 0.25,
            "proxyAccountLeaseTtlSeconds": 1200,
        },
    )
    assert configured.status_code == 200
    configured_payload = configured.json()
    assert configured_payload["proxyOverloadIsolationSeconds"] == 0
    assert configured_payload["proxyAccountErrorRateWeightingEnabled"] is False
    assert configured_payload["proxyAccountInflightPenaltyPct"] == 7.5
    assert configured_payload["proxyAccountLeaseTokenWeight"] == 0.25
    assert configured_payload["proxyAccountLeaseTtlSeconds"] == 1200.0
    for _camel, (snake, default) in _ROUTING_OVERLOAD_FIELDS.items():
        assert configured_payload["provenance"][snake]["source"] == "dashboard"
        assert configured_payload["provenance"][snake]["default"] == default

    # Omitting the fields leaves the dashboard values untouched.
    unchanged = await async_client.put("/api/settings", json={"warmupModel": "gpt-5.6-sol"})
    assert unchanged.status_code == 200
    assert unchanged.json()["proxyAccountLeaseTtlSeconds"] == 1200.0
    assert unchanged.json()["provenance"]["proxy_account_lease_ttl_seconds"]["source"] == "dashboard"

    # An explicit null returns to inheritance; with the environment differing
    # from the default the value comes from the environment.
    inherited = settings_service.get_settings().model_copy(
        update={"proxy_account_lease_ttl_seconds": 1800.0, "proxy_overload_isolation_seconds": 600}
    )
    monkeypatch.setattr(settings_service, "get_settings", lambda: inherited)
    cleared = await async_client.put(
        "/api/settings",
        json={"proxyAccountLeaseTtlSeconds": None, "proxyOverloadIsolationSeconds": None},
    )
    assert cleared.status_code == 200
    cleared_payload = cleared.json()
    assert cleared_payload["proxyAccountLeaseTtlSeconds"] == 1800.0
    assert cleared_payload["provenance"]["proxy_account_lease_ttl_seconds"] == {
        "source": "env",
        "envValue": 1800.0,
        "default": 900.0,
    }
    assert cleared_payload["proxyOverloadIsolationSeconds"] == 600
    assert cleared_payload["provenance"]["proxy_overload_isolation_seconds"]["source"] == "env"
    # The other three are still dashboard-owned.
    assert cleared_payload["provenance"]["proxy_account_inflight_penalty_pct"]["source"] == "dashboard"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"proxyOverloadIsolationSeconds": -1},
        {"proxyOverloadIsolationSeconds": 1.5},
        {"proxyAccountInflightPenaltyPct": -0.1},
        {"proxyAccountInflightPenaltyPct": 100.5},
        {"proxyAccountLeaseTokenWeight": -1},
        {"proxyAccountLeaseTtlSeconds": 0},
        {"proxyAccountErrorRateWeightingEnabled": 2},
    ],
)
async def test_settings_api_rejects_out_of_bounds_routing_overload_values(async_client, payload):
    response = await async_client.put("/api/settings", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_dashboard_overload_isolation_window_controls_the_balancer_without_restart(async_client):
    """The isolation window the balancer applies follows the dashboard value.

    The process environment still says 1800 s (the code default in the test
    environment); once the dashboard stores 240 s the next request snapshot
    hands the balancer 240 s and the overload funnel isolates for that long.
    """
    from datetime import datetime, timezone

    from app.core.crypto import TokenEncryptor
    from app.modules.proxy._load_balancer.overload_backoff import (
        OVERLOAD_ISOLATION_TRIP_LEVEL,
        OVERLOAD_TRIP_COUNT,
        record_upstream_overload,
    )
    from app.modules.proxy.load_balancer import LoadBalancer, effective_routing_tunables
    from tests.simulation.virtual_time import VirtualClock

    response = await async_client.put("/api/settings", json={"proxyOverloadIsolationSeconds": 240})
    assert response.status_code == 200

    # The request path reads one cached snapshot and hands it to the balancer.
    snapshot = effective_routing_tunables(await get_settings_cache().get())
    assert snapshot.overload_isolation_seconds == 240.0
    assert effective_routing_tunables().overload_isolation_seconds == 1800.0  # environment untouched

    clock = VirtualClock(epoch_value=2_000_000_000.0)
    balancer = LoadBalancer(cast(Any, None), clock=clock)
    encryptor = TokenEncryptor()
    account = Account(
        id="acc-dashboard-isolation",
        chatgpt_account_id="workspace-acc-dashboard-isolation",
        email="isolation@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime.now(tz=timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    lease = await balancer.acquire_account_lease(account.id, kind="response_create", routing_tunables=snapshot)
    await balancer.release_account_lease(lease)
    runtime = balancer._runtime[account.id]
    runtime.overload_backoff_level = OVERLOAD_ISOLATION_TRIP_LEVEL - 1
    runtime.overload_last_trip_at = clock.time()
    for _ in range(OVERLOAD_TRIP_COUNT):
        await record_upstream_overload(balancer, account)

    assert runtime.overload_isolated_until == pytest.approx(clock.time() + 240.0)


@pytest.mark.asyncio
async def test_settings_api_rejects_dashboard_lease_ttl_below_request_budgets(async_client):
    """The dashboard lease TTL is held to the ``account-lease-ttl-covers-*`` invariants.

    With the default 600 s proxy and 180 s compact request budgets a 120 s TTL
    would let stale reclaim take a response-create lease away from a healthy
    request, so the write is rejected by the same PUT-time check the C2-1
    timeouts use and nothing is stored.
    """
    response = await async_client.put("/api/settings", json={"proxyAccountLeaseTtlSeconds": 120})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "timeout_invariant_violation"
    assert "account-lease-ttl-covers-proxy-budget" in response.json()["error"]["message"]
    assert "account-lease-ttl-covers-compact-budget" in response.json()["error"]["message"]

    current = await async_client.get("/api/settings")
    assert current.json()["provenance"]["proxy_account_lease_ttl_seconds"]["source"] == "default"

    accepted = await async_client.put("/api/settings", json={"proxyAccountLeaseTtlSeconds": 600})
    assert accepted.status_code == 200
    assert accepted.json()["proxyAccountLeaseTtlSeconds"] == 600.0


@pytest.mark.asyncio
async def test_settings_api_lease_ttl_and_request_budgets_are_checked_on_effective_values(async_client):
    """One combined check over the merged dashboard values (C2-1 budgets + C2-2 lease TTL).

    A TTL is judged against the *effective* budgets (dashboard value when set,
    else environment), and a budget raised from the dashboard is judged against
    the *effective* TTL, so neither side can be driven past the other.
    """
    # (a) Dashboard proxy budget 900 (env 600) — accepted: the effective TTL (default 900) still covers it.
    response = await async_client.put("/api/settings", json={"proxyRequestBudgetSeconds": 900})
    assert response.status_code == 200
    assert response.json()["provenance"]["proxy_request_budget_seconds"]["source"] == "dashboard"

    # A TTL of 700 satisfies the environment budget (600) but not the dashboard one (900) → rejected.
    response = await async_client.put("/api/settings", json={"proxyAccountLeaseTtlSeconds": 700})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "timeout_invariant_violation"
    assert "account-lease-ttl-covers-proxy-budget" in body["error"]["message"]
    assert "proxy_request_budget_seconds=900" in body["error"]["message"]
    current = await async_client.get("/api/settings")
    assert current.json()["provenance"]["proxy_account_lease_ttl_seconds"]["source"] == "default"

    # (c) A TTL that covers the effective budgets is stored.
    response = await async_client.put("/api/settings", json={"proxyAccountLeaseTtlSeconds": 1200})
    assert response.status_code == 200
    assert response.json()["provenance"]["proxy_account_lease_ttl_seconds"]["source"] == "dashboard"

    # (b) Raising the compact budget above the dashboard TTL (1200) is rejected on the effective TTL.
    response = await async_client.put("/api/settings", json={"compactRequestBudgetSeconds": 1500})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "timeout_invariant_violation"
    assert "account-lease-ttl-covers-compact-budget" in body["error"]["message"]
    assert "proxy_account_lease_ttl_seconds=1200" in body["error"]["message"]
    current = await async_client.get("/api/settings")
    assert current.json()["provenance"]["compact_request_budget_seconds"]["source"] == "default"

    # (c) Valid combinations pass: a compact budget under the TTL, and TTL + budget raised in one PUT.
    response = await async_client.put("/api/settings", json={"compactRequestBudgetSeconds": 1000})
    assert response.status_code == 200
    assert response.json()["compactRequestBudgetSeconds"] == 1000.0
    response = await async_client.put(
        "/api/settings",
        json={"proxyAccountLeaseTtlSeconds": 1600, "compactRequestBudgetSeconds": 1500},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["proxyAccountLeaseTtlSeconds"] == 1600.0
    assert payload["compactRequestBudgetSeconds"] == 1500.0

    # Clearing the TTL is judged on the inherited TTL (env 900) against the effective budgets (900 / 1500) → rejected.
    response = await async_client.put("/api/settings", json={"proxyAccountLeaseTtlSeconds": None})
    assert response.status_code == 400
    assert "account-lease-ttl-covers-compact-budget" in response.json()["error"]["message"]

    # Clearing the compact budget first (back to env 180) lets the TTL inherit again.
    response = await async_client.put("/api/settings", json={"compactRequestBudgetSeconds": None})
    assert response.status_code == 200
    response = await async_client.put("/api/settings", json={"proxyAccountLeaseTtlSeconds": None})
    assert response.status_code == 200
    assert response.json()["provenance"]["proxy_account_lease_ttl_seconds"]["source"] == "default"
    assert response.json()["proxyAccountLeaseTtlSeconds"] == 900.0


@pytest.mark.asyncio
async def test_settings_api_reads_inherited_penalty_above_the_dashboard_write_cap(async_client, monkeypatch):
    """An environment penalty above 100 is valid for ``Settings`` and must stay readable."""
    from app.modules.settings import service as settings_service

    inherited = settings_service.get_settings().model_copy(update={"proxy_account_inflight_penalty_pct": 150.0})
    monkeypatch.setattr(settings_service, "get_settings", lambda: inherited)

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    assert response.json()["proxyAccountInflightPenaltyPct"] == 150.0
    assert response.json()["provenance"]["proxy_account_inflight_penalty_pct"]["source"] == "env"

    rejected = await async_client.put("/api/settings", json={"proxyAccountInflightPenaltyPct": 150})
    assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_settings_api_codex_prewarm_round_trip_with_provenance(async_client, monkeypatch):
    """M3 codex prewarm: default -> dashboard -> cleared/env -> unchanged on omit."""
    from app.modules.settings import service as settings_service

    name = "http_responses_session_bridge_codex_prewarm_enabled"
    initial = await async_client.get("/api/settings")
    assert initial.status_code == 200
    payload = initial.json()
    assert payload["httpResponsesSessionBridgeCodexPrewarmEnabled"] is False
    assert payload["provenance"][name] == {"source": "default", "envValue": False, "default": False}

    stored = await async_client.put("/api/settings", json={"httpResponsesSessionBridgeCodexPrewarmEnabled": True})
    assert stored.status_code == 200
    assert stored.json()["httpResponsesSessionBridgeCodexPrewarmEnabled"] is True
    assert stored.json()["provenance"][name]["source"] == "dashboard"

    # Storing the inherited value (False is a value, not a clear) keeps the
    # switch dashboard-owned.
    stored_off = await async_client.put("/api/settings", json={"httpResponsesSessionBridgeCodexPrewarmEnabled": False})
    assert stored_off.status_code == 200
    assert stored_off.json()["httpResponsesSessionBridgeCodexPrewarmEnabled"] is False
    assert stored_off.json()["provenance"][name] == {"source": "dashboard", "envValue": False, "default": False}

    # Explicit null clears the column; with the deprecated env alias differing
    # from the default the switch is inherited from the environment.
    inherited = settings_service.get_settings().model_copy(update={name: True})
    monkeypatch.setattr(settings_service, "get_settings", lambda: inherited)
    cleared = await async_client.put("/api/settings", json={"httpResponsesSessionBridgeCodexPrewarmEnabled": None})
    assert cleared.status_code == 200
    assert cleared.json()["httpResponsesSessionBridgeCodexPrewarmEnabled"] is True
    assert cleared.json()["provenance"][name] == {"source": "env", "envValue": True, "default": False}

    # Omitting the field (any unrelated save) leaves the switch inherited: the
    # dashboard never copies the inherited value into the column.
    unchanged = await async_client.put("/api/settings", json={"warmupModel": "gpt-5.6-sol"})
    assert unchanged.status_code == 200
    assert unchanged.json()["provenance"][name]["source"] == "env"
    async with SessionLocal() as session:
        row = await session.get(DashboardSettings, 1)
        assert row is not None
        assert row.http_responses_session_bridge_codex_prewarm_enabled is None


@pytest.mark.asyncio
async def test_settings_api_codex_prewarm_dashboard_value_reaches_the_bridge_resolver_without_restart(async_client):
    """M3 codex prewarm: the bridge reads the switch off the ``Settings`` the
    request entry point overlaid with the settings-cache snapshot, so a dashboard
    PUT flips it for the next new Codex session with the process (and its startup
    ``Settings``) untouched."""
    from app.core.config.dashboard_overrides import effective_settings
    from app.core.config.settings import get_settings
    from app.modules.proxy._service.http_bridge.helpers import _http_bridge_prewarm_enabled

    startup_settings = get_settings()

    async def prewarm_enabled_for_the_next_request() -> bool:
        # What ``DashboardOverridesMiddleware`` binds and the proxy facade
        # applies, without standing up a request.
        snapshot = await get_settings_cache().get()
        return _http_bridge_prewarm_enabled(effective_settings(snapshot, startup_settings))

    assert startup_settings.http_responses_session_bridge_codex_prewarm_enabled is False
    assert await prewarm_enabled_for_the_next_request() is False

    enabled = await async_client.put("/api/settings", json={"httpResponsesSessionBridgeCodexPrewarmEnabled": True})
    assert enabled.status_code == 200
    assert await prewarm_enabled_for_the_next_request() is True

    cleared = await async_client.put("/api/settings", json={"httpResponsesSessionBridgeCodexPrewarmEnabled": None})
    assert cleared.status_code == 200
    assert await prewarm_enabled_for_the_next_request() is False


# M4 model catalogue: per-model context window overrides sub-API.
_OVERRIDES_PATH = "/api/settings/model-context-window-overrides"


@pytest.mark.asyncio
async def test_model_context_window_overrides_round_trip_with_provenance(async_client, monkeypatch):
    initial = await async_client.get(_OVERRIDES_PATH)
    assert initial.status_code == 200
    assert initial.json() == {"overrides": []}

    # The environment dict is the per-slug fallback and is reported as such.
    environment = settings_api_module.get_app_settings().model_copy(
        update={"model_context_window_overrides": {"gpt-5.4": 300_000}}
    )
    monkeypatch.setattr(settings_api_module, "get_app_settings", lambda: environment)
    inherited = await async_client.get(_OVERRIDES_PATH)
    assert inherited.json() == {
        "overrides": [{"slug": "gpt-5.4", "contextWindow": 300_000, "source": "env", "envValue": 300_000}]
    }

    # A dashboard row wins for its slug and still reports the environment value.
    stored = await async_client.put(f"{_OVERRIDES_PATH}/gpt-5.4", json={"contextWindow": 515_000})
    assert stored.status_code == 200
    assert stored.json()["overrides"] == [
        {"slug": "gpt-5.4", "contextWindow": 515_000, "source": "dashboard", "envValue": 300_000}
    ]

    # Rows for slugs the environment does not know have no environment value.
    added = await async_client.put(f"{_OVERRIDES_PATH}/custom-model", json={"contextWindow": 32_768})
    assert added.status_code == 200
    assert added.json()["overrides"] == [
        {"slug": "custom-model", "contextWindow": 32_768, "source": "dashboard", "envValue": None},
        {"slug": "gpt-5.4", "contextWindow": 515_000, "source": "dashboard", "envValue": 300_000},
    ]

    # PUT on an existing slug updates in place (no duplicate row).
    updated = await async_client.put(f"{_OVERRIDES_PATH}/custom-model", json={"contextWindow": 65_536})
    assert [o["contextWindow"] for o in updated.json()["overrides"] if o["slug"] == "custom-model"] == [65_536]

    async with SessionLocal() as session:
        rows = (await session.execute(text("SELECT slug, context_window FROM model_context_window_overrides"))).all()
    assert sorted(tuple(row) for row in rows) == [("custom-model", 65_536), ("gpt-5.4", 515_000)]

    # Deleting the row returns the slug to the environment entry; deleting a
    # dashboard-only slug removes it from the list.
    cleared = await async_client.delete(f"{_OVERRIDES_PATH}/gpt-5.4")
    assert cleared.status_code == 200
    assert cleared.json()["overrides"] == [
        {"slug": "custom-model", "contextWindow": 65_536, "source": "dashboard", "envValue": None},
        {"slug": "gpt-5.4", "contextWindow": 300_000, "source": "env", "envValue": 300_000},
    ]
    removed = await async_client.delete(f"{_OVERRIDES_PATH}/custom-model")
    assert removed.json()["overrides"] == [
        {"slug": "gpt-5.4", "contextWindow": 300_000, "source": "env", "envValue": 300_000}
    ]


@pytest.mark.asyncio
async def test_model_context_window_override_delete_without_row_is_not_found(async_client, monkeypatch):
    environment = settings_api_module.get_app_settings().model_copy(
        update={"model_context_window_overrides": {"gpt-5.4": 300_000}}
    )
    monkeypatch.setattr(settings_api_module, "get_app_settings", lambda: environment)
    # An environment-inherited entry has no dashboard row to delete.
    response = await async_client.delete(f"{_OVERRIDES_PATH}/gpt-5.4")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_context_window_override_not_found"
    assert (await async_client.delete(f"{_OVERRIDES_PATH}/never-stored")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"contextWindow": 0},
        {"contextWindow": -1},
        {"contextWindow": 1.5},
        # A token count is an integer: a float, a numeric string and a bool are
        # operator mistakes, not values to coerce into a stored window.
        {"contextWindow": 515000.0},
        {"contextWindow": "515000"},
        {"contextWindow": True},
        {"contextWindow": None},
        # Wider than the database Integer column: a 422, not a commit-time 500.
        {"contextWindow": 2_147_483_648},
        {},
    ],
)
async def test_model_context_window_override_rejects_invalid_window(async_client, payload):
    response = await async_client.put(f"{_OVERRIDES_PATH}/gpt-5.4", json=payload)
    assert response.status_code == 422
    assert (await async_client.get(_OVERRIDES_PATH)).json() == {"overrides": []}


@pytest.mark.asyncio
# The padded variants must be rejected, not silently trimmed into "gpt-5.4".
@pytest.mark.parametrize("slug", ["%20", "gpt%205.4", "a%09b", "%20gpt-5.4", "gpt-5.4%20", "x" * 257])
async def test_model_context_window_override_rejects_invalid_slug(async_client, slug):
    response = await async_client.put(f"{_OVERRIDES_PATH}/{slug}", json={"contextWindow": 1000})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_model_slug"
    assert (await async_client.get(_OVERRIDES_PATH)).json() == {"overrides": []}


@pytest.mark.asyncio
async def test_model_context_window_override_concurrent_creates_of_one_slug_both_succeed(async_client):
    # Create-or-replace is one upsert statement, so two writers racing on the
    # same previously absent slug cannot leave one of them with a primary-key 500.
    import asyncio

    responses = await asyncio.gather(
        *(
            async_client.put(f"{_OVERRIDES_PATH}/gpt-5.4", json={"contextWindow": window})
            for window in (515_000, 400_000)
        )
    )
    assert [response.status_code for response in responses] == [200, 200]
    listed = (await async_client.get(_OVERRIDES_PATH)).json()["overrides"]
    assert [entry["slug"] for entry in listed] == ["gpt-5.4"]
    assert listed[0]["contextWindow"] in {515_000, 400_000}


@pytest.mark.asyncio
async def test_model_context_window_override_slug_may_contain_a_slash(async_client):
    # Source-catalog slugs such as "vendor/model" are reachable through the
    # path-typed segment.
    response = await async_client.put(f"{_OVERRIDES_PATH}/vendor/model-a", json={"contextWindow": 4096})
    assert response.status_code == 200
    assert response.json()["overrides"][0]["slug"] == "vendor/model-a"
    assert (await async_client.delete(f"{_OVERRIDES_PATH}/vendor/model-a")).status_code == 200


# M5 conversation archive
@pytest.mark.asyncio
async def test_settings_api_conversation_archive_round_trip_with_provenance(async_client, monkeypatch):
    """default -> dashboard on -> cleared/env -> unchanged on omit; the archive dir is read-only."""
    from app.modules.settings import service as settings_service

    initial = await async_client.get("/api/settings")
    assert initial.status_code == 200
    payload = initial.json()
    assert payload["conversationArchiveEnabled"] is False
    assert payload["provenance"]["conversation_archive_enabled"] == {
        "source": "default",
        "envValue": False,
        "default": False,
    }
    # T1 directory of this replica, shown read-only next to the toggle.
    assert payload["conversationArchiveDir"] == str(settings_service.get_settings().conversation_archive_dir)

    enabled = await async_client.put("/api/settings", json={"conversationArchiveEnabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["conversationArchiveEnabled"] is True
    assert enabled.json()["provenance"]["conversation_archive_enabled"]["source"] == "dashboard"

    # The directory is not writable through the API (unknown fields are ignored).
    renamed = await async_client.put("/api/settings", json={"conversationArchiveDir": "/elsewhere"})
    assert renamed.status_code == 200
    assert renamed.json()["conversationArchiveDir"] == payload["conversationArchiveDir"]

    # Explicit null clears the column; the deprecated env alias applies again.
    inherited = settings_service.get_settings().model_copy(update={"conversation_archive_enabled": True})
    monkeypatch.setattr(settings_service, "get_settings", lambda: inherited)
    cleared = await async_client.put("/api/settings", json={"conversationArchiveEnabled": None})
    assert cleared.status_code == 200
    assert cleared.json()["conversationArchiveEnabled"] is True
    assert cleared.json()["provenance"]["conversation_archive_enabled"] == {
        "source": "env",
        "envValue": True,
        "default": False,
    }

    # Omitting the field (any unrelated save) never copies the inherited value
    # into the column.
    unchanged = await async_client.put("/api/settings", json={"warmupModel": "gpt-5.6-sol"})
    assert unchanged.status_code == 200
    assert unchanged.json()["provenance"]["conversation_archive_enabled"]["source"] == "env"
    async with SessionLocal() as session:
        row = await session.get(DashboardSettings, 1)
        assert row is not None
        assert row.conversation_archive_enabled is None


# end M5 conversation archive


# R2 spool retention
@pytest.mark.asyncio
async def test_settings_api_spool_retention_round_trip_with_provenance(async_client, monkeypatch):
    """default -> dashboard value -> cleared/env -> unchanged on omit; the floor is reported."""
    from app.modules.settings import service as settings_service

    setting = "http_responses_session_bridge_operation_spool_retention_seconds"

    initial = await async_client.get("/api/settings")
    assert initial.status_code == 200
    payload = initial.json()
    assert payload["httpResponsesSessionBridgeOperationSpoolRetentionSeconds"] == 604800.0
    assert payload["provenance"][setting] == {"source": "default", "envValue": 604800.0, "default": 604800.0}
    # The floor the API enforces, so the card can mirror it before saving. At
    # shipped defaults the bridge request budget (7200s) binds.
    floor = payload["httpResponsesSessionBridgeOperationSpoolRetentionFloorSeconds"]
    assert floor == 7200.0

    configured = await async_client.put(
        "/api/settings", json={"httpResponsesSessionBridgeOperationSpoolRetentionSeconds": 86400}
    )
    assert configured.status_code == 200
    assert configured.json()["httpResponsesSessionBridgeOperationSpoolRetentionSeconds"] == 86400.0
    assert configured.json()["provenance"][setting]["source"] == "dashboard"

    # Explicit null clears the column; the deprecated env alias applies again.
    inherited = settings_service.get_settings().model_copy(update={setting: 259200.0})
    monkeypatch.setattr(settings_service, "get_settings", lambda: inherited)
    cleared = await async_client.put(
        "/api/settings", json={"httpResponsesSessionBridgeOperationSpoolRetentionSeconds": None}
    )
    assert cleared.status_code == 200
    assert cleared.json()["httpResponsesSessionBridgeOperationSpoolRetentionSeconds"] == 259200.0
    assert cleared.json()["provenance"][setting] == {
        "source": "env",
        "envValue": 259200.0,
        "default": 604800.0,
    }

    # Omitting the field never copies the inherited value into the column.
    unchanged = await async_client.put("/api/settings", json={"warmupModel": "gpt-5.6-sol"})
    assert unchanged.status_code == 200
    assert unchanged.json()["provenance"][setting]["source"] == "env"
    async with SessionLocal() as session:
        row = await session.get(DashboardSettings, 1)
        assert row is not None
        assert getattr(row, setting) is None


@pytest.mark.asyncio
async def test_settings_api_rejects_spool_retention_below_the_replay_floor(async_client):
    """A window shorter than the longest replay window is refused, naming the binding term."""
    at_floor = await async_client.put(
        "/api/settings", json={"httpResponsesSessionBridgeOperationSpoolRetentionSeconds": 7200}
    )
    assert at_floor.status_code == 200
    assert at_floor.json()["httpResponsesSessionBridgeOperationSpoolRetentionSeconds"] == 7200.0

    below = await async_client.put(
        "/api/settings", json={"httpResponsesSessionBridgeOperationSpoolRetentionSeconds": 7199}
    )
    assert below.status_code == 400
    body = below.json()
    assert body["error"]["code"] == "spool_retention_below_floor"
    assert "stale_operation_abandonment_window" in body["error"]["message"]

    # The rejected value was not stored.
    unchanged = await async_client.get("/api/settings")
    assert unchanged.json()["httpResponsesSessionBridgeOperationSpoolRetentionSeconds"] == 7200.0


@pytest.mark.asyncio
async def test_settings_api_floor_keeps_claimed_retry_circuit_grace_under_a_lowered_budget(async_client):
    """Lowering the bridge budget must not drop the floor below a claimed circuit's two TTLs."""
    lowered = await async_client.put("/api/settings", json={"httpResponsesSessionBridgeRequestBudgetSeconds": 3600})
    assert lowered.status_code == 200
    # The abandonment window fell to 3600s, but an ever-claimed retry circuit is
    # still honoured for two TTLs, so the reported floor stays at 7200s.
    assert lowered.json()["httpResponsesSessionBridgeOperationSpoolRetentionFloorSeconds"] == 7200.0

    below = await async_client.put(
        "/api/settings", json={"httpResponsesSessionBridgeOperationSpoolRetentionSeconds": 3600}
    )
    assert below.status_code == 400
    body = below.json()
    assert body["error"]["code"] == "spool_retention_below_floor"
    assert "claimed_retry_circuit_lifetime" in body["error"]["message"]

    at_floor = await async_client.put(
        "/api/settings", json={"httpResponsesSessionBridgeOperationSpoolRetentionSeconds": 7200}
    )
    assert at_floor.status_code == 200
    assert at_floor.json()["httpResponsesSessionBridgeOperationSpoolRetentionSeconds"] == 7200.0


@pytest.mark.asyncio
async def test_settings_api_below_floor_state_allows_no_worse_updates_only(async_client, monkeypatch):
    """An env alias below the floor must not make the dashboard read-only *or* a free-for-all.

    The column is NULL, so this state was never validated by the API; it must
    still refuse anything that deepens the violation.
    """
    from app.modules.settings import service as settings_service

    below = settings_service.get_settings().model_copy(
        update={"http_responses_session_bridge_operation_spool_retention_seconds": 60.0}
    )
    monkeypatch.setattr(settings_service, "get_settings", lambda: below)

    initial = await async_client.get("/api/settings")
    assert initial.json()["httpResponsesSessionBridgeOperationSpoolRetentionSeconds"] == 60.0
    assert initial.json()["httpResponsesSessionBridgeOperationSpoolRetentionFloorSeconds"] == 7200.0

    # Unrelated edits still go through while below the floor.
    unrelated = await async_client.put("/api/settings", json={"warmupModel": "gpt-5.6-sol"})
    assert unrelated.status_code == 200
    # ... including one that touches a floor input without raising the floor.
    lowered_input = await async_client.put(
        "/api/settings", json={"httpResponsesSessionBridgePromptCacheIdleTtlSeconds": 1800}
    )
    assert lowered_input.status_code == 200

    # Lowering the retention further is refused, naming the deepening.
    deeper = await async_client.put(
        "/api/settings", json={"httpResponsesSessionBridgeOperationSpoolRetentionSeconds": 1}
    )
    assert deeper.status_code == 400
    body = deeper.json()
    assert body["error"]["code"] == "spool_retention_below_floor"
    assert "already below its replay floor" in body["error"]["message"]

    # Raising a floor input while below the floor is refused too.
    raised_floor = await async_client.put(
        "/api/settings", json={"httpResponsesSessionBridgePromptCacheIdleTtlSeconds": 86400}
    )
    assert raised_floor.status_code == 400
    assert raised_floor.json()["error"]["code"] == "spool_retention_below_floor"

    # Raising the retention toward (but not to) the floor is an improvement.
    toward = await async_client.put(
        "/api/settings", json={"httpResponsesSessionBridgeOperationSpoolRetentionSeconds": 3600}
    )
    assert toward.status_code == 200
    assert toward.json()["httpResponsesSessionBridgeOperationSpoolRetentionSeconds"] == 3600.0

    # And clearing back to the below-floor env alias is refused: that would
    # lower the effective retention again.
    cleared = await async_client.put(
        "/api/settings", json={"httpResponsesSessionBridgeOperationSpoolRetentionSeconds": None}
    )
    assert cleared.status_code == 400
    assert cleared.json()["error"]["code"] == "spool_retention_below_floor"

    # Reaching the floor is accepted and restores the ordinary rule.
    at_floor = await async_client.put(
        "/api/settings", json={"httpResponsesSessionBridgeOperationSpoolRetentionSeconds": 7200}
    )
    assert at_floor.status_code == 200
    back_below = await async_client.put(
        "/api/settings", json={"httpResponsesSessionBridgeOperationSpoolRetentionSeconds": 3600}
    )
    assert back_below.status_code == 400
    assert "must be at least 7200s" in back_below.json()["error"]["message"]


@pytest.mark.asyncio
async def test_settings_api_rejects_raising_a_reuse_window_above_the_spool_retention(async_client):
    """Raising a reuse window past the stored spool retention introduces the same violation."""
    configured = await async_client.put(
        "/api/settings", json={"httpResponsesSessionBridgeOperationSpoolRetentionSeconds": 7200}
    )
    assert configured.status_code == 200

    raised = await async_client.put(
        "/api/settings", json={"httpResponsesSessionBridgePromptCacheIdleTtlSeconds": 10800}
    )
    assert raised.status_code == 400
    body = raised.json()
    assert body["error"]["code"] == "spool_retention_below_floor"
    assert "bridge_session_reuse_window" in body["error"]["message"]

    # An explicit null on a non-nullable reuse window means "unchanged", not
    # "inherit"; the floor check must not treat it as a missing environment layer.
    null_window = await async_client.put(
        "/api/settings", json={"httpResponsesSessionBridgePromptCacheIdleTtlSeconds": None}
    )
    assert null_window.status_code == 200

    # Raising it together with a spool retention that covers it is accepted.
    both = await async_client.put(
        "/api/settings",
        json={
            "httpResponsesSessionBridgePromptCacheIdleTtlSeconds": 10800,
            "httpResponsesSessionBridgeOperationSpoolRetentionSeconds": 10800,
        },
    )
    assert both.status_code == 200
    assert both.json()["httpResponsesSessionBridgePromptCacheIdleTtlSeconds"] == 10800
    assert both.json()["httpResponsesSessionBridgeOperationSpoolRetentionSeconds"] == 10800.0


# end R2 spool retention
