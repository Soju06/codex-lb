from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import timedelta, timezone

import pytest

from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestKind, RequestLog, StickySession, StickySessionKind
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeyData, ApiKeysService
from app.modules.fleet import api as fleet_api
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration

_PRIMARY_WINDOW_MINUTES = 300
_SECONDARY_WINDOW_MINUTES = 10080

_FORBIDDEN_KEYS = {
    "auth",
    "access",
    "refresh",
    "id_token",
    "idToken",
    "accessToken",
    "refreshToken",
    "access_token",
    "refresh_token",
    "capacity_credits_primary",
    "capacityCreditsPrimary",
    "remaining_credits_primary",
    "remainingCreditsPrimary",
    "capacity_credits_secondary",
    "capacityCreditsSecondary",
    "remaining_credits_secondary",
    "remainingCreditsSecondary",
    "request_usage",
    "requestUsage",
    "total_cost_usd",
    "totalCostUsd",
    "additional_quotas",
    "additionalQuotas",
    "deactivation_reason",
    "deactivationReason",
    "request_id",
    "requestId",
    "archive_request_id",
    "archiveRequestId",
    "session_id",
    "sessionId",
    "api_key_id",
    "apiKeyId",
    "client_ip",
    "clientIp",
    "error_message",
    "errorMessage",
    "failure_detail",
    "failureDetail",
}


def _make_account(
    account_id: str,
    email: str,
    *,
    status: AccountStatus = AccountStatus.ACTIVE,
    plan_type: str = "plus",
) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=None,
        email=email,
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=status,
        deactivation_reason=None,
    )


async def _create_api_key(
    name: str,
    *,
    assigned_account_ids: list[str] | None = None,
    usage_sections: str = "upstream_limits,account_pool_usage",
) -> str:
    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        created = await service.create_key(
            ApiKeyCreateData(
                name=name,
                allowed_models=None,
                limits=[],
                assigned_account_ids=assigned_account_ids,
                usage_sections=usage_sections,
            )
        )
    return created.key


async def _seed_account_with_windows(
    account_id: str,
    email: str,
    *,
    primary_used_percent: float,
    secondary_used_percent: float,
    primary_reset_at: int,
    secondary_reset_at: int,
    status: AccountStatus = AccountStatus.ACTIVE,
) -> None:
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account(account_id, email, status=status))
        await usage_repo.add_entry(
            account_id,
            primary_used_percent,
            window="primary",
            reset_at=primary_reset_at,
            window_minutes=_PRIMARY_WINDOW_MINUTES,
        )
        await usage_repo.add_entry(
            account_id,
            secondary_used_percent,
            window="secondary",
            reset_at=secondary_reset_at,
            window_minutes=_SECONDARY_WINDOW_MINUTES,
        )


async def _seed_request_log(
    account_id: str,
    request_id: str,
    *,
    requested_at,
    status: str = "success",
    error_code: str | None = None,
    error_message: str | None = None,
    request_kind: str = RequestKind.NORMAL.value,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    output_tokens: int | None = 0,
    reasoning_tokens: int | None = None,
    cost_usd: float = 0.0,
    source: str | None = "codex",
    useragent_group: str | None = "codex-local",
    client_ip: str | None = None,
    session_id: str | None = None,
    api_key_id: str | None = None,
    deleted_at=None,
) -> None:
    async with SessionLocal() as session:
        session.add(
            RequestLog(
                account_id=account_id,
                request_id=request_id,
                archive_request_id=f"archive-{request_id}",
                model="gpt-5",
                request_kind=request_kind,
                requested_at=requested_at,
                deleted_at=deleted_at,
                status=status,
                error_code=error_code,
                error_message=error_message,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                cost_usd=cost_usd,
                source=source,
                useragent_group=useragent_group,
                client_ip=client_ip,
                session_id=session_id,
                api_key_id=api_key_id,
            )
        )
        await session.commit()


async def _seed_sticky_session(
    account_id: str,
    key: str,
    *,
    kind: StickySessionKind = StickySessionKind.PROMPT_CACHE,
    updated_at,
) -> None:
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=key,
                kind=kind,
                account_id=account_id,
                created_at=updated_at,
                updated_at=updated_at,
            )
        )
        await session.commit()


def _assert_no_forbidden_keys(node: object) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            assert key not in _FORBIDDEN_KEYS, f"sensitive key '{key}' leaked into fleet response"
            _assert_no_forbidden_keys(value)
    elif isinstance(node, list):
        for item in node:
            _assert_no_forbidden_keys(item)


def _window(payload: dict, key: str) -> dict:
    return next(window for window in payload["pressure"]["windows"] if window["key"] == key)


@pytest.mark.asyncio
async def test_fleet_summary_requires_api_key(async_client, db_setup):
    await _seed_account_with_windows(
        "acc_noauth",
        "noauth@example.com",
        primary_used_percent=10.0,
        secondary_used_percent=10.0,
        primary_reset_at=0,
        secondary_reset_at=0,
    )

    response = await async_client.get("/api/fleet/summary")

    assert response.status_code == 401
    assert "noauth@example.com" not in response.text
    assert "accounts" not in response.text


@pytest.mark.asyncio
async def test_fleet_summary_rejects_invalid_api_key(async_client, db_setup):
    await _seed_account_with_windows(
        "acc_badkey",
        "badkey@example.com",
        primary_used_percent=10.0,
        secondary_used_percent=10.0,
        primary_reset_at=0,
        secondary_reset_at=0,
    )

    response = await async_client.get(
        "/api/fleet/summary",
        headers={"Authorization": "Bearer sk-clb-not-a-real-key"},
    )

    assert response.status_code == 401
    assert "badkey@example.com" not in response.text


@pytest.mark.asyncio
async def test_fleet_summary_returns_minimal_projection_with_valid_key(async_client, db_setup):
    plain_key = await _create_api_key("fleet-summary-key")
    now_epoch = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
    primary_reset = now_epoch + 300
    secondary_reset = now_epoch + 5 * 24 * 3600
    await _seed_account_with_windows(
        "acc_fleet_a",
        "fleet-a@example.com",
        primary_used_percent=38.0,
        secondary_used_percent=20.0,
        primary_reset_at=primary_reset,
        secondary_reset_at=secondary_reset,
    )

    response = await async_client.get(
        "/api/fleet/summary",
        headers={"Authorization": f"Bearer {plain_key}"},
    )

    assert response.status_code == 200
    payload = response.json()
    accounts = payload["accounts"]
    assert len(accounts) == 1
    account = accounts[0]
    assert account["accountId"] == "acc_fleet_a"
    assert account["email"] == "fleet-a@example.com"
    assert account["displayName"] == "fleet-a@example.com"
    assert account["status"] == "active"
    assert account["planType"] == "plus"
    assert account["lastRefreshAt"] is not None
    assert account["primary"]["remainingPercent"] == 62
    assert account["primary"]["windowMinutes"] == _PRIMARY_WINDOW_MINUTES
    assert account["primary"]["resetAt"] is not None
    assert account["secondary"]["remainingPercent"] == 80
    assert account["secondary"]["windowMinutes"] == _SECONDARY_WINDOW_MINUTES
    assert account["secondary"]["resetAt"] is not None


@pytest.mark.asyncio
async def test_fleet_summary_omits_sensitive_fields(async_client, db_setup):
    plain_key = await _create_api_key("fleet-summary-sensitive-key")
    await _seed_account_with_windows(
        "acc_sensitive",
        "sensitive@example.com",
        primary_used_percent=25.0,
        secondary_used_percent=40.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
    )

    response = await async_client.get(
        "/api/fleet/summary",
        headers={"Authorization": f"Bearer {plain_key}"},
    )

    assert response.status_code == 200
    payload = response.json()
    _assert_no_forbidden_keys(payload)
    raw = json.dumps(payload)
    assert "access" not in raw
    assert "refresh" not in raw
    account = payload["accounts"][0]
    assert set(account.keys()) == {
        "accountId",
        "displayName",
        "email",
        "status",
        "planType",
        "primary",
        "secondary",
        "lastRefreshAt",
    }


@pytest.mark.asyncio
async def test_fleet_summary_respects_account_scoped_api_key(async_client, db_setup):
    await _seed_account_with_windows(
        "acc_scope_visible",
        "scope-visible@example.com",
        primary_used_percent=10.0,
        secondary_used_percent=20.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
    )
    await _seed_account_with_windows(
        "acc_scope_hidden",
        "scope-hidden@example.com",
        primary_used_percent=70.0,
        secondary_used_percent=80.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
    )
    plain_key = await _create_api_key("fleet-summary-scoped-key", assigned_account_ids=["acc_scope_visible"])

    response = await async_client.get(
        "/api/fleet/summary",
        headers={"Authorization": f"Bearer {plain_key}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert [account["accountId"] for account in payload["accounts"]] == ["acc_scope_visible"]
    raw = json.dumps(payload)
    assert "scope-visible@example.com" in raw
    assert "scope-hidden@example.com" not in raw


@pytest.mark.asyncio
async def test_fleet_summary_hides_usage_when_key_disables_account_pool_usage(async_client, db_setup):
    await _seed_account_with_windows(
        "acc_usage_hidden",
        "usage-hidden@example.com",
        primary_used_percent=100.0,
        secondary_used_percent=20.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
    )
    plain_key = await _create_api_key("fleet-summary-no-usage-key", usage_sections="")

    response = await async_client.get(
        "/api/fleet/summary",
        headers={"Authorization": f"Bearer {plain_key}"},
    )

    assert response.status_code == 200
    account = response.json()["accounts"][0]
    assert account["accountId"] == "acc_usage_hidden"
    assert account["email"] == "usage-hidden@example.com"
    assert account["status"] == "active"
    assert account["lastRefreshAt"] is None
    assert account["primary"] == {"remainingPercent": None, "resetAt": None, "windowMinutes": None}
    assert account["secondary"] == {"remainingPercent": None, "resetAt": None, "windowMinutes": None}


@pytest.mark.asyncio
async def test_fleet_summary_hides_usage_when_key_only_allows_upstream_limits(async_client, db_setup):
    await _seed_account_with_windows(
        "acc_upstream_only",
        "upstream-only@example.com",
        primary_used_percent=100.0,
        secondary_used_percent=20.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
    )
    plain_key = await _create_api_key("fleet-summary-upstream-only-key", usage_sections="upstream_limits")

    response = await async_client.get(
        "/api/fleet/summary",
        headers={"Authorization": f"Bearer {plain_key}"},
    )

    assert response.status_code == 200
    account = response.json()["accounts"][0]
    assert account["accountId"] == "acc_upstream_only"
    assert account["email"] == "upstream-only@example.com"
    assert account["status"] == "active"
    assert account["lastRefreshAt"] is None
    assert account["primary"] == {"remainingPercent": None, "resetAt": None, "windowMinutes": None}
    assert account["secondary"] == {"remainingPercent": None, "resetAt": None, "windowMinutes": None}


@pytest.mark.asyncio
async def test_fleet_summary_hides_usage_when_key_omits_upstream_limits(async_client, db_setup):
    await _seed_account_with_windows(
        "acc_usage_hidden",
        "usage-hidden-no-upstream@example.com",
        primary_used_percent=100.0,
        secondary_used_percent=20.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
    )
    plain_key = await _create_api_key("fleet-summary-account-pool-only-key", usage_sections="account_pool_usage")

    response = await async_client.get(
        "/api/fleet/summary",
        headers={"Authorization": f"Bearer {plain_key}"},
    )

    assert response.status_code == 200
    account = response.json()["accounts"][0]
    assert account["accountId"] == "acc_usage_hidden"
    assert account["lastRefreshAt"] is None
    assert account["primary"] == {"remainingPercent": None, "resetAt": None, "windowMinutes": None}
    assert account["secondary"] == {"remainingPercent": None, "resetAt": None, "windowMinutes": None}


@pytest.mark.asyncio
async def test_fleet_summary_hides_usage_when_global_api_key_quota_privacy_enabled(async_client, db_setup):
    plain_key = await _create_api_key("fleet-summary-global-hidden-key")
    await _seed_account_with_windows(
        "acc_global_usage_hidden",
        "global-usage-hidden@example.com",
        primary_used_percent=10.0,
        secondary_used_percent=20.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
    )

    settings = await async_client.put(
        "/api/settings",
        json={"hideUpstreamQuotaFromApiKeys": True},
    )
    assert settings.status_code == 200

    response = await async_client.get(
        "/api/fleet/summary",
        headers={"Authorization": f"Bearer {plain_key}"},
    )

    assert response.status_code == 200
    account = response.json()["accounts"][0]
    assert account["accountId"] == "acc_global_usage_hidden"
    assert account["lastRefreshAt"] is None
    assert account["primary"] == {"remainingPercent": None, "resetAt": None, "windowMinutes": None}
    assert account["secondary"] == {"remainingPercent": None, "resetAt": None, "windowMinutes": None}


@pytest.mark.asyncio
async def test_fleet_observability_requires_api_key(async_client, db_setup):
    response = await async_client.get("/api/fleet/observability")

    assert response.status_code == 401
    assert "pressure" not in response.text
    assert "sticky" not in response.text


@pytest.mark.asyncio
async def test_fleet_observability_reports_pressure_and_sticky_without_sensitive_fields(async_client, db_setup):
    await get_settings_cache().invalidate()
    plain_key = await _create_api_key("fleet-observability-key")
    await _seed_account_with_windows(
        "acc_observe",
        "observe@example.com",
        primary_used_percent=10.0,
        secondary_used_percent=20.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
    )
    now = utcnow()
    await _seed_request_log(
        "acc_observe",
        "req-observe-success",
        requested_at=now - timedelta(minutes=5),
        request_kind="prewarm",
        input_tokens=100,
        cached_input_tokens=70,
        output_tokens=10,
        cost_usd=1.0,
        client_ip="203.0.113.15",
        session_id="session-observe-secret",
        api_key_id="api-key-observe-secret",
    )
    await _seed_request_log(
        "acc_observe",
        "req-observe-error",
        requested_at=now - timedelta(minutes=10),
        status="error",
        error_code="rate_limit",
        error_message="secret upstream error body",
        input_tokens=20,
        output_tokens=0,
        cost_usd=0.5,
        client_ip="203.0.113.16",
    )
    await _seed_request_log(
        "acc_observe",
        "req-observe-reasoning",
        requested_at=now - timedelta(minutes=15),
        input_tokens=30,
        output_tokens=None,
        reasoning_tokens=25,
        cost_usd=0.25,
    )
    await _seed_request_log(
        "acc_observe",
        "req-observe-older",
        requested_at=now - timedelta(minutes=45),
        input_tokens=40,
        cached_input_tokens=10,
        output_tokens=5,
        cost_usd=0.75,
        useragent_group="worker",
    )
    await _seed_request_log(
        "acc_observe",
        "req-observe-warmup",
        requested_at=now - timedelta(minutes=2),
        request_kind=RequestKind.WARMUP.value,
        input_tokens=10_000,
        cost_usd=99.0,
    )
    await _seed_request_log(
        "acc_observe",
        "req-observe-deleted",
        requested_at=now - timedelta(minutes=2),
        deleted_at=now - timedelta(minutes=1),
        input_tokens=10_000,
        cost_usd=99.0,
    )
    await _seed_sticky_session(
        "acc_observe",
        "sticky-observe-recent-secret",
        updated_at=now - timedelta(minutes=2),
    )
    await _seed_sticky_session(
        "acc_observe",
        "sticky-observe-stale-secret",
        updated_at=now - timedelta(hours=1),
    )
    await _seed_sticky_session(
        "acc_observe",
        "sticky-observe-thread-secret",
        kind=StickySessionKind.STICKY_THREAD,
        updated_at=now - timedelta(hours=2),
    )

    response = await async_client.get(
        "/api/fleet/observability",
        headers={"Authorization": f"Bearer {plain_key}"},
    )

    assert response.status_code == 200
    payload = response.json()
    _assert_no_forbidden_keys(payload)
    assert payload["available"] is True
    assert payload["source"] == "codex-lb fleet observability"
    assert payload["generatedAt"] is not None
    assert payload["pressure"]["available"] is True
    assert {window["key"] for window in payload["pressure"]["windows"]} == {"30m", "2h"}

    thirty = _window(payload, "30m")
    assert thirty["requestCount"] == 3
    assert thirty["errorCount"] == 1
    assert thirty["inputTokens"] == 150
    assert thirty["cachedInputTokens"] == 70
    assert thirty["outputTokens"] == 35
    assert thirty["costUsd"] == 1.75
    assert thirty["topErrorCode"] == "rate_limit"
    assert thirty["byAccount"] == [
        {
            "accountId": "acc_observe",
            "email": "observe@example.com",
            "label": "observe@example.com",
            "requestCount": 3,
            "errorCount": 1,
            "inputTokens": 150,
            "cachedInputTokens": 70,
            "outputTokens": 35,
            "costUsd": 1.75,
            "lastSelectedAt": thirty["byAccount"][0]["lastSelectedAt"],
        }
    ]
    assert thirty["byAccount"][0]["lastSelectedAt"] is not None
    assert {item["requestKind"]: item["requestCount"] for item in thirty["byKind"]} == {
        "normal": 2,
        "prewarm": 1,
    }
    assert thirty["byClient"][0]["clientGroup"] == "codex-local"
    assert thirty["byClient"][0]["requestCount"] == 3

    two_hour = _window(payload, "2h")
    assert two_hour["requestCount"] == 4
    assert two_hour["errorCount"] == 1
    assert two_hour["inputTokens"] == 190
    assert two_hour["cachedInputTokens"] == 80
    assert two_hour["outputTokens"] == 40
    assert two_hour["costUsd"] == 2.5

    sticky = payload["sticky"]
    assert sticky["available"] is True
    assert sticky["total"] == 3
    assert sticky["recentCount"] == 2
    assert sticky["staleCount"] == 1
    assert sticky["staleThresholdSeconds"] == 1800
    assert sticky["byAccount"][0]["accountId"] == "acc_observe"
    assert sticky["byAccount"][0]["total"] == 3
    assert sticky["byAccount"][0]["recentCount"] == 2
    assert sticky["byAccount"][0]["staleCount"] == 1
    assert {kind["name"]: kind["staleCount"] for kind in sticky["byAccount"][0]["kinds"]} == {
        "prompt_cache": 1,
        "sticky_thread": 0,
    }

    raw = json.dumps(payload)
    assert "req-observe" not in raw
    assert "archive-req-observe" not in raw
    assert "session-observe-secret" not in raw
    assert "api-key-observe-secret" not in raw
    assert "sticky-observe" not in raw
    assert "203.0.113" not in raw
    assert "secret upstream error body" not in raw


@pytest.mark.asyncio
async def test_fleet_observability_marks_pressure_window_truncated(async_client, db_setup):
    await get_settings_cache().invalidate()
    plain_key = await _create_api_key("fleet-observability-truncated-key")
    now = utcnow()
    for index in range(11):
        account_id = f"acc_observe_truncated_{index:02d}"
        await _seed_account_with_windows(
            account_id,
            f"observe-truncated-{index:02d}@example.com",
            primary_used_percent=10.0,
            secondary_used_percent=20.0,
            primary_reset_at=1735862400,
            secondary_reset_at=1736467200,
        )
        await _seed_request_log(
            account_id,
            f"req-observe-truncated-{index:02d}",
            requested_at=now - timedelta(minutes=5),
            input_tokens=10,
            output_tokens=1,
        )

    response = await async_client.get(
        "/api/fleet/observability",
        headers={"Authorization": f"Bearer {plain_key}"},
    )

    assert response.status_code == 200
    thirty = _window(response.json(), "30m")
    assert thirty["requestCount"] == 11
    assert thirty["truncated"] is True
    assert len(thirty["byAccount"]) == 10


@pytest.mark.asyncio
async def test_fleet_observability_respects_account_scoped_api_key(async_client, db_setup):
    await get_settings_cache().invalidate()
    await _seed_account_with_windows(
        "acc_observe_visible",
        "observe-visible@example.com",
        primary_used_percent=10.0,
        secondary_used_percent=20.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
    )
    await _seed_account_with_windows(
        "acc_observe_hidden",
        "observe-hidden@example.com",
        primary_used_percent=30.0,
        secondary_used_percent=40.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
    )
    now = utcnow()
    await _seed_request_log("acc_observe_visible", "req-visible", requested_at=now - timedelta(minutes=5))
    await _seed_request_log("acc_observe_hidden", "req-hidden", requested_at=now - timedelta(minutes=5))
    await _seed_sticky_session("acc_observe_visible", "sticky-visible", updated_at=now - timedelta(minutes=5))
    await _seed_sticky_session("acc_observe_hidden", "sticky-hidden", updated_at=now - timedelta(minutes=5))
    plain_key = await _create_api_key(
        "fleet-observability-scoped-key",
        assigned_account_ids=["acc_observe_visible"],
    )

    response = await async_client.get(
        "/api/fleet/observability",
        headers={"Authorization": f"Bearer {plain_key}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert _window(payload, "30m")["requestCount"] == 1
    assert _window(payload, "30m")["byAccount"][0]["accountId"] == "acc_observe_visible"
    assert payload["sticky"]["total"] == 1
    assert payload["sticky"]["byAccount"][0]["accountId"] == "acc_observe_visible"
    raw = json.dumps(payload)
    assert "acc_observe_hidden" not in raw
    assert "observe-hidden@example.com" not in raw
    assert "req-hidden" not in raw
    assert "sticky-hidden" not in raw


@pytest.mark.asyncio
async def test_fleet_observability_hides_usage_when_key_disables_account_pool_usage(async_client, db_setup):
    await get_settings_cache().invalidate()
    plain_key = await _create_api_key("fleet-observability-no-usage-key", usage_sections="")
    await _seed_account_with_windows(
        "acc_observe_hidden_usage",
        "observe-hidden-usage@example.com",
        primary_used_percent=10.0,
        secondary_used_percent=20.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
    )
    now = utcnow()
    await _seed_request_log(
        "acc_observe_hidden_usage",
        "req-hidden-usage",
        requested_at=now - timedelta(minutes=5),
    )
    await _seed_sticky_session(
        "acc_observe_hidden_usage",
        "sticky-hidden-usage",
        updated_at=now - timedelta(minutes=5),
    )

    response = await async_client.get(
        "/api/fleet/observability",
        headers={"Authorization": f"Bearer {plain_key}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["pressure"] == {"available": False, "windows": []}
    assert payload["sticky"] == {
        "available": False,
        "total": 0,
        "recentCount": 0,
        "staleCount": 0,
        "staleThresholdSeconds": None,
        "truncated": False,
        "byAccount": [],
    }
    raw = json.dumps(payload)
    assert "observe-hidden-usage@example.com" not in raw
    assert "req-hidden-usage" not in raw
    assert "sticky-hidden-usage" not in raw


@pytest.mark.asyncio
async def test_fleet_observability_hides_usage_when_key_only_allows_upstream_limits(async_client, db_setup):
    await get_settings_cache().invalidate()
    plain_key = await _create_api_key("fleet-observability-upstream-only-key", usage_sections="upstream_limits")
    await _seed_account_with_windows(
        "acc_observe_upstream_only",
        "observe-upstream-only@example.com",
        primary_used_percent=10.0,
        secondary_used_percent=20.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
    )
    now = utcnow()
    await _seed_request_log(
        "acc_observe_upstream_only",
        "req-upstream-only",
        requested_at=now - timedelta(minutes=5),
    )
    await _seed_sticky_session(
        "acc_observe_upstream_only",
        "sticky-upstream-only",
        updated_at=now - timedelta(minutes=5),
    )

    response = await async_client.get(
        "/api/fleet/observability",
        headers={"Authorization": f"Bearer {plain_key}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["pressure"] == {"available": False, "windows": []}
    assert payload["sticky"] == {
        "available": False,
        "total": 0,
        "recentCount": 0,
        "staleCount": 0,
        "staleThresholdSeconds": None,
        "truncated": False,
        "byAccount": [],
    }
    raw = json.dumps(payload)
    assert "observe-upstream-only@example.com" not in raw
    assert "req-upstream-only" not in raw
    assert "sticky-upstream-only" not in raw


@pytest.mark.asyncio
async def test_fleet_refresh_requires_api_key(async_client, db_setup):
    response = await async_client.post("/api/fleet/refresh")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_fleet_refresh_reports_bounded_attempt_without_sensitive_fields(async_client, db_setup):
    plain_key = await _create_api_key("fleet-refresh-key")
    await _seed_account_with_windows(
        "acc_refresh_active",
        "refresh-active@example.com",
        primary_used_percent=10.0,
        secondary_used_percent=10.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
    )
    await _seed_account_with_windows(
        "acc_refresh_paused",
        "refresh-paused@example.com",
        primary_used_percent=20.0,
        secondary_used_percent=20.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
        status=AccountStatus.PAUSED,
    )
    await _seed_account_with_windows(
        "acc_refresh_reauth",
        "refresh-reauth@example.com",
        primary_used_percent=30.0,
        secondary_used_percent=30.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
        status=AccountStatus.REAUTH_REQUIRED,
    )
    await _seed_account_with_windows(
        "acc_refresh_deactivated",
        "refresh-deactivated@example.com",
        primary_used_percent=40.0,
        secondary_used_percent=40.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
        status=AccountStatus.DEACTIVATED,
    )

    response = await async_client.post(
        "/api/fleet/refresh",
        headers={"Authorization": f"Bearer {plain_key}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["usageWritten"] is False
    assert payload["accountCount"] == 4
    assert payload["attemptedCount"] == 1
    assert payload["generatedAt"] is not None
    _assert_no_forbidden_keys(payload)


@pytest.mark.asyncio
async def test_fleet_refresh_uses_route_local_usage_updater_and_invalidates_on_write(
    async_client,
    db_setup,
    monkeypatch,
):
    plain_key = await _create_api_key("fleet-refresh-updater-key")
    await _seed_account_with_windows(
        "acc_refresh_write",
        "refresh-write@example.com",
        primary_used_percent=10.0,
        secondary_used_percent=10.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
    )

    refresh_calls: list[list[str]] = []
    invalidations: list[str] = []
    updater_session_ids: list[int] = []
    background_session_ids: list[int] = []

    class FakeUsageUpdater:
        def __init__(self, usage_repo, accounts_repo, additional_usage_repo):
            self.usage_repo = usage_repo
            self.accounts_repo = accounts_repo
            self.additional_usage_repo = additional_usage_repo

        async def refresh_accounts(self, accounts, latest_primary, *, own_singleflight_sessions=False):
            assert own_singleflight_sessions is True
            updater_session_ids.append(id(self.usage_repo._session))
            refresh_calls.append([account.id for account in accounts])
            assert isinstance(latest_primary, dict)
            return True

    class FakeRateLimitHeadersCache:
        async def invalidate(self):
            invalidations.append("rate_limit_headers")

    class FakeAccountSelectionCache:
        def invalidate(self):
            invalidations.append("account_selection")

    @asynccontextmanager
    async def recording_background_session():
        async with SessionLocal() as session:
            background_session_ids.append(id(session))
            yield session

    monkeypatch.setattr("app.modules.fleet.api.get_background_session", recording_background_session)
    monkeypatch.setattr("app.modules.fleet.api.UsageUpdater", FakeUsageUpdater)
    monkeypatch.setattr(
        "app.modules.fleet.api.get_rate_limit_headers_cache",
        lambda: FakeRateLimitHeadersCache(),
    )
    monkeypatch.setattr(
        "app.modules.fleet.api.get_account_selection_cache",
        lambda: FakeAccountSelectionCache(),
    )

    response = await async_client.post(
        "/api/fleet/refresh",
        headers={"Authorization": f"Bearer {plain_key}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["usageWritten"] is True
    assert payload["accountCount"] == 1
    assert payload["attemptedCount"] == 1
    assert refresh_calls == [["acc_refresh_write"]]
    assert updater_session_ids == background_session_ids
    assert invalidations == ["rate_limit_headers", "account_selection"]


@pytest.mark.asyncio
async def test_fleet_refresh_respects_account_scoped_api_key(async_client, db_setup, monkeypatch):
    await _seed_account_with_windows(
        "acc_refresh_scope_visible",
        "refresh-scope-visible@example.com",
        primary_used_percent=10.0,
        secondary_used_percent=10.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
    )
    await _seed_account_with_windows(
        "acc_refresh_scope_hidden",
        "refresh-scope-hidden@example.com",
        primary_used_percent=20.0,
        secondary_used_percent=20.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
    )
    plain_key = await _create_api_key(
        "fleet-refresh-scoped-key",
        assigned_account_ids=["acc_refresh_scope_visible"],
    )
    refresh_calls: list[list[str]] = []

    class FakeUsageUpdater:
        def __init__(self, usage_repo, accounts_repo, additional_usage_repo):
            self.usage_repo = usage_repo
            self.accounts_repo = accounts_repo
            self.additional_usage_repo = additional_usage_repo

        async def refresh_accounts(self, accounts, latest_primary, *, own_singleflight_sessions=False):
            assert own_singleflight_sessions is True
            refresh_calls.append([account.id for account in accounts])
            assert set(latest_primary) <= {"acc_refresh_scope_visible"}
            return False

    monkeypatch.setattr("app.modules.fleet.api.UsageUpdater", FakeUsageUpdater)

    response = await async_client.post(
        "/api/fleet/refresh",
        headers={"Authorization": f"Bearer {plain_key}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accountCount"] == 1
    assert payload["attemptedCount"] == 1
    assert refresh_calls == [["acc_refresh_scope_visible"]]


@pytest.mark.asyncio
async def test_fleet_refresh_owns_session_until_shielded_refresh_finishes(db_setup, monkeypatch):
    fleet_api._BACKGROUND_REFRESH_TASKS.clear()
    await _seed_account_with_windows(
        "acc_refresh_cancel",
        "refresh-cancel@example.com",
        primary_used_percent=10.0,
        secondary_used_percent=10.0,
        primary_reset_at=1735862400,
        secondary_reset_at=1736467200,
    )
    refresh_started = asyncio.Event()
    allow_refresh_finish = asyncio.Event()
    session_exited = asyncio.Event()
    session_was_open_during_refresh: list[bool] = []
    invalidations: list[str] = []

    class FakeUsageUpdater:
        def __init__(self, usage_repo, accounts_repo, additional_usage_repo):
            self.usage_repo = usage_repo
            self.accounts_repo = accounts_repo
            self.additional_usage_repo = additional_usage_repo

        async def refresh_accounts(self, accounts, latest_primary, *, own_singleflight_sessions=False):
            assert own_singleflight_sessions is True

            async def shielded_refresh() -> bool:
                refresh_started.set()
                await allow_refresh_finish.wait()
                session_was_open_during_refresh.append(not session_exited.is_set())
                return True

            return await asyncio.shield(asyncio.create_task(shielded_refresh()))

    class FakeRateLimitHeadersCache:
        async def invalidate(self):
            invalidations.append("rate_limit_headers")

    class FakeAccountSelectionCache:
        def invalidate(self):
            invalidations.append("account_selection")

    @asynccontextmanager
    async def recording_background_session():
        async with SessionLocal() as session:
            try:
                yield session
            finally:
                session_exited.set()

    monkeypatch.setattr(fleet_api, "get_background_session", recording_background_session)
    monkeypatch.setattr(fleet_api, "UsageUpdater", FakeUsageUpdater)
    monkeypatch.setattr(fleet_api, "get_rate_limit_headers_cache", lambda: FakeRateLimitHeadersCache())
    monkeypatch.setattr(fleet_api, "get_account_selection_cache", lambda: FakeAccountSelectionCache())

    request_task = asyncio.create_task(
        fleet_api.refresh_fleet_usage(
            api_key=ApiKeyData(
                id="fleet-refresh-cancel-key",
                name="fleet refresh cancel key",
                key_prefix="fleet-refresh-cancel",
                allowed_models=None,
                enforced_model=None,
                enforced_reasoning_effort=None,
                enforced_service_tier=None,
                expires_at=None,
                is_active=True,
                created_at=utcnow(),
                last_used_at=None,
            )
        )
    )
    await asyncio.wait_for(refresh_started.wait(), timeout=1)

    request_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request_task
    await asyncio.sleep(0)

    assert not session_exited.is_set()
    assert len(fleet_api._BACKGROUND_REFRESH_TASKS) == 1
    allow_refresh_finish.set()
    await asyncio.wait_for(session_exited.wait(), timeout=1)
    await asyncio.wait_for(_wait_for_background_refresh_tasks_to_drain(), timeout=1)

    assert session_was_open_during_refresh == [True]
    assert invalidations == ["rate_limit_headers", "account_selection"]
    assert fleet_api._BACKGROUND_REFRESH_TASKS == set()


async def _wait_for_background_refresh_tasks_to_drain() -> None:
    while fleet_api._BACKGROUND_REFRESH_TASKS:
        await asyncio.sleep(0)
