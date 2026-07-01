from __future__ import annotations

import json

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService
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


async def _create_api_key(name: str) -> str:
    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        created = await service.create_key(ApiKeyCreateData(name=name, allowed_models=None, limits=[]))
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


def _assert_no_forbidden_keys(node: object) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            assert key not in _FORBIDDEN_KEYS, f"sensitive key '{key}' leaked into fleet response"
            _assert_no_forbidden_keys(value)
    elif isinstance(node, list):
        for item in node:
            _assert_no_forbidden_keys(item)


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
    primary_reset = 1735862400
    secondary_reset = 1736467200
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

    class FakeUsageUpdater:
        def __init__(self, usage_repo, accounts_repo, additional_usage_repo):
            self.usage_repo = usage_repo
            self.accounts_repo = accounts_repo
            self.additional_usage_repo = additional_usage_repo

        async def refresh_accounts(self, accounts, latest_primary):
            refresh_calls.append([account.id for account in accounts])
            assert isinstance(latest_primary, dict)
            return True

    class FakeRateLimitHeadersCache:
        async def invalidate(self):
            invalidations.append("rate_limit_headers")

    class FakeAccountSelectionCache:
        def invalidate(self):
            invalidations.append("account_selection")

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
    assert invalidations == ["rate_limit_headers", "account_selection"]
