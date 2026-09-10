from __future__ import annotations

import asyncio
from datetime import timedelta, timezone

import pytest

from app.core.clients.usage import UsageFetchError
from app.core.crypto import TokenEncryptor
from app.core.types import JsonObject
from app.core.usage.models import UsagePayload
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository
from app.modules.usage.updater import UsageUpdater

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

HEADERS = {"Authorization": "Bearer original-token", "chatgpt-account-id": "original-account"}
ORIGINAL: JsonObject = {
    "user_id": "original-user",
    "account_id": "original-account",
    "plan_type": "plus",
    "rate_limit": {"allowed": False, "limit_reached": True},
    "rate_limit_reached_type": {"type": "rate_limit_reached"},
    "rate_limit_upsell": {"banner_type": "luna_reserve"},
    "credits": {"has_credits": False, "unlimited": False, "balance": "0.00"},
    "spend_control": {"reached": False, "enabled": True},
    "rate_limit_reset_credits": {"available_count": 2, "private_account_metadata": "preserved"},
    "future_account_metadata": {"billing": "original-only"},
    "additional_rate_limits": [
        {
            "limit_name": "gpt-reserve",
            "metered_feature": "reserve",
            "normal_model_slug": "gpt-5.6-luna",
            "rate_limit": {"allowed": True, "limit_reached": False},
        }
    ],
}


@pytest.fixture(autouse=True)
def isolate_upstream(monkeypatch):
    async def fetch(*, access_token: str, account_id: str | None, **kwargs) -> UsagePayload:
        assert access_token == "original-token"
        assert account_id == "original-account"
        return UsagePayload.from_upstream(ORIGINAL)

    async def refresh(self, accounts, latest_usage, *, own_singleflight_sessions, join_existing):
        assert own_singleflight_sessions is True
        assert join_existing is True
        return False

    monkeypatch.setattr("app.core.auth.dependencies.fetch_usage", fetch)
    monkeypatch.setattr(UsageUpdater, "refresh_accounts", refresh)


async def seed_pool(
    *, second_used: float = 20, stale: bool = False, second_status=AccountStatus.ACTIVE, second_plan="pro"
):
    encryptor = TokenEncryptor()
    now = utcnow()
    reset_at = int(now.replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        accounts = AccountsRepository(session)
        usage = UsageRepository(session)
        for account_id, plan, used, status in (
            ("primary", "plus", 100, AccountStatus.ACTIVE),
            ("second", second_plan, second_used, second_status),
        ):
            await accounts.upsert(
                Account(
                    id=account_id,
                    chatgpt_account_id="original-account" if account_id == "primary" else "second-account",
                    email=f"{account_id}@example.test",
                    plan_type=plan,
                    access_token_encrypted=encryptor.encrypt("unused-token"),
                    refresh_token_encrypted=encryptor.encrypt("unused-refresh"),
                    id_token_encrypted=encryptor.encrypt("unused-id"),
                    last_refresh=now,
                    status=status,
                )
            )
            for window, minutes in (("primary", 300), ("secondary", 10080)):
                await usage.add_entry(
                    account_id,
                    used,
                    window=window,
                    reset_at=reset_at + minutes * 60 - 60,
                    window_minutes=minutes,
                    recorded_at=now - timedelta(minutes=10) if stale else now,
                )


@pytest.mark.parametrize("suffix", ["", "/"])
async def test_desktop_usage_returns_pool_with_original_account_envelope(async_client, db_setup, suffix):
    await seed_pool()
    response = await async_client.get(f"/api/codex/desktop/usage{suffix}", headers=HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["rate_limit"]["allowed"] is True
    assert payload["rate_limit"]["limit_reached"] is False
    # Existing weekly capacities are 7,560 for Plus and 50,400 for Pro.
    assert payload["rate_limit"]["secondary_window"]["used_percent"] == 30
    for field in (
        "user_id",
        "account_id",
        "plan_type",
        "credits",
        "spend_control",
        "rate_limit_reset_credits",
        "future_account_metadata",
        "additional_rate_limits",
    ):
        assert payload[field] == ORIGINAL[field]
    assert "rate_limit_reached_type" not in payload
    assert "rate_limit_upsell" not in payload
    assert response.headers["cache-control"] == "no-store"


async def test_desktop_usage_fresh_exhausted_pool_remains_exhausted(async_client, db_setup):
    await seed_pool(second_used=100)
    response = await async_client.get("/api/codex/desktop/usage", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["rate_limit"]["allowed"] is False
    assert response.json()["rate_limit"]["primary_window"]["used_percent"] == 100
    assert response.json()["rate_limit_reached_type"] == ORIGINAL["rate_limit_reached_type"]


async def test_desktop_usage_accepts_reported_weekly_primary_with_secondary_placeholders(async_client, db_setup):
    await seed_pool()
    now = utcnow()
    reset_at = int(now.replace(tzinfo=timezone.utc).timestamp()) + 400000
    async with SessionLocal() as session:
        usage = UsageRepository(session)
        for account, percent in (("primary", 52), ("second", 54)):
            await usage.add_entry(account, percent, window="primary", window_minutes=10080, reset_at=reset_at)
            await usage.add_entry(account, 0, window="secondary", window_minutes=0, reset_at=None)
    response = await async_client.get("/api/codex/desktop/usage", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["rate_limit"]["allowed"] is True
    assert response.json()["rate_limit"]["primary_window"] is None
    assert response.json()["rate_limit"]["secondary_window"]["used_percent"] == 53


async def test_desktop_model_quota_requires_main_capacity_on_same_account(async_client, db_setup):
    await seed_pool(second_plan="plus")
    reset_at = int(utcnow().replace(tzinfo=timezone.utc).timestamp()) + 300
    async with SessionLocal() as session:
        usage = AdditionalUsageRepository(session)
        for account, percent in (("primary", 0), ("second", 100)):
            await usage.add_entry(
                account, "gpt-6-astra", "astra", "primary", percent, reset_at=reset_at, window_minutes=300
            )
    response = await async_client.get("/api/codex/desktop/usage", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["rate_limit"]["allowed"] is True
    astra = next(
        bucket for bucket in response.json()["additional_rate_limits"] if bucket["limit_name"] == "gpt-6-astra"
    )
    assert astra["rate_limit"]["allowed"] is False


async def test_desktop_model_limit_with_different_metered_feature_is_not_replaced(async_client, db_setup, monkeypatch):
    await seed_pool(second_plan="plus")
    original: JsonObject = {
        **ORIGINAL,
        "additional_rate_limits": [
            {"limit_name": "gpt-6-astra", "metered_feature": "original-bucket", "rate_limit": {"allowed": False}}
        ],
    }

    async def fetch(**kwargs):
        return UsagePayload.from_upstream(original)

    monkeypatch.setattr("app.core.auth.dependencies.fetch_usage", fetch)
    reset_at = int(utcnow().replace(tzinfo=timezone.utc).timestamp()) + 300
    async with SessionLocal() as session:
        await AdditionalUsageRepository(session).add_entry(
            "second", "gpt-6-astra", "different-bucket", "primary", 0, reset_at=reset_at, window_minutes=300
        )
    response = await async_client.get("/api/codex/desktop/usage", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["additional_rate_limits"] == original["additional_rate_limits"]


async def test_desktop_mixed_plan_model_capacity_is_not_invented(async_client, db_setup):
    await seed_pool()
    reset_at = int(utcnow().replace(tzinfo=timezone.utc).timestamp()) + 300
    async with SessionLocal() as session:
        usage = AdditionalUsageRepository(session)
        for account, percent in (("primary", 100), ("second", 0)):
            await usage.add_entry(
                account, "gpt-6-astra", "astra", "primary", percent, reset_at=reset_at, window_minutes=300
            )
    response = await async_client.get("/api/codex/desktop/usage", headers=HEADERS)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "pooled_usage_unavailable"


async def test_desktop_usage_failed_refresh_cannot_advertise_stale_capacity(async_client, db_setup):
    await seed_pool(stale=True)
    response = await async_client.get("/api/codex/desktop/usage", headers=HEADERS)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "pooled_usage_unavailable"
    assert "rate_limit" not in response.json()


@pytest.mark.parametrize("status", [AccountStatus.PAUSED, AccountStatus.DEACTIVATED, AccountStatus.REAUTH_REQUIRED])
async def test_desktop_usage_unavailable_account_cannot_reopen_primary_quota(async_client, db_setup, status):
    await seed_pool(second_status=status)
    response = await async_client.get("/api/codex/desktop/usage", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["rate_limit"]["allowed"] is False
    assert response.json()["rate_limit"]["secondary_window"]["used_percent"] == 100


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer sk-clb-only"},
        {"chatgpt-account-id": "original-account"},
        {**HEADERS, "chatgpt-account-id": "unknown-account"},
    ],
)
async def test_desktop_usage_rejects_unauthorized_callers(async_client, db_setup, headers):
    response = await async_client.get("/api/codex/desktop/usage", headers=headers)
    assert response.status_code == 401
    assert "rate_limit" not in response.json()


async def test_desktop_usage_rejects_capability_before_identity_io(async_client, db_setup):
    response = await async_client.get(
        "/api/codex/desktop/usage", headers={**HEADERS, "X-Codex-LB-Required-Capability": "trusted_cyber"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "required_capability_transport_unsupported"


@pytest.mark.parametrize("upstream_status,expected", [(401, 401), (403, 401), (429, 429), (500, 503), (0, 503)])
async def test_desktop_usage_identity_errors_do_not_return_pool(
    async_client, db_setup, monkeypatch, upstream_status, expected
):
    await seed_pool()

    async def fail(**kwargs):
        raise UsageFetchError(upstream_status, "upstream unavailable")

    monkeypatch.setattr("app.core.auth.dependencies.fetch_usage", fail)
    response = await async_client.get("/api/codex/desktop/usage", headers=HEADERS)
    assert response.status_code == expected
    assert "rate_limit" not in response.json()


async def test_desktop_usage_cancelled_refresh_propagates_and_releases_read_scope(async_client, db_setup, monkeypatch):
    await seed_pool()
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def wait_for_cancel(self, accounts, latest, **kwargs):
        # A separate write while refresh waits proves the initial read transaction ended.
        async with SessionLocal() as session:
            await UsageRepository(session).add_entry("primary", 100, window="primary")
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(UsageUpdater, "refresh_accounts", wait_for_cancel)
    task = asyncio.create_task(async_client.get("/api/codex/desktop/usage", headers=HEADERS))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("suffix", ["", "/"])
@pytest.mark.parametrize("stale,expected_status", [(False, 200), (True, 503)])
async def test_desktop_refresh_deadline_uses_only_fresh_persisted_observations(
    async_client, db_setup, monkeypatch, suffix, stale, expected_status
):
    from app.modules.desktop_usage import service

    await seed_pool(stale=stale)
    refresh_cancelled = asyncio.Event()

    async def slow_refresh(self, accounts, latest_usage, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            refresh_cancelled.set()

    monkeypatch.setattr(service, "_REFRESH_TIMEOUT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(UsageUpdater, "refresh_accounts", slow_refresh)
    response = await asyncio.wait_for(async_client.get(f"/api/codex/desktop/usage{suffix}", headers=HEADERS), timeout=5)
    assert refresh_cancelled.is_set()
    assert response.status_code == expected_status
    if stale:
        assert response.json()["error"]["code"] == "pooled_usage_unavailable"
        assert "rate_limit" not in response.json()
    else:
        assert response.json()["rate_limit"]["secondary_window"]["used_percent"] == 30
        assert response.json()["account_id"] == "original-account"
