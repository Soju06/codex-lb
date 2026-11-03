from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.clients.usage import ConsumeRateLimitResetCreditResponse
from app.core.crypto import TokenEncryptor
from app.core.usage.models import UsagePayload
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.dependencies import _proxy_repo_context
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.load_balancer import LoadBalancer, RuntimeState
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import UsageUpdater

pytestmark = pytest.mark.integration


async def _seed_account(*, weekly_only=False, old_baseline=False):
    now = int(time.time())
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="reset-recovery",
            chatgpt_account_id="synthetic-workspace",
            email="reset@example.invalid",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("synthetic-access"),
            refresh_token_encrypted=encryptor.encrypt("synthetic-refresh"),
            id_token_encrypted=encryptor.encrypt("synthetic-id"),
            last_refresh=datetime.now(timezone.utc).replace(tzinfo=None),
            status=AccountStatus.RATE_LIMITED,
            blocked_at=now - 10,
            reset_at=now + 3600,
        )
        session.add(account)
        await session.commit()
        repo = UsageRepository(session)
        recorded_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=60 if old_baseline else 0)
        if not weekly_only:
            await repo.add_entry(
                account.id, 100, window="primary", reset_at=now + 3600, window_minutes=300, recorded_at=recorded_at
            )
        await repo.add_entry(
            account.id, 100, window="secondary", reset_at=now + 3600, window_minutes=10080, recorded_at=recorded_at
        )
    return now


@pytest.mark.parametrize("weekly_only", [False, True])
@pytest.mark.parametrize("old_baseline", [False, True])
async def test_dashboard_reset_clears_confirmed_reset_during_429_cooldown(
    async_client, monkeypatch, weekly_only, old_baseline
):
    now = await _seed_account(weekly_only=weekly_only, old_baseline=old_baseline)

    async def fresh(self, account, **kwargs):
        return account

    monkeypatch.setattr(AuthManager, "ensure_fresh", fresh)
    monkeypatch.setattr(
        "app.modules.accounts.service.consume_rate_limit_reset_credit",
        AsyncMock(return_value=ConsumeRateLimitResetCreditResponse(code="reset", windows_reset=2)),
    )
    windows = {"secondary_window": {"used_percent": 0, "reset_at": now + 604800, "limit_window_seconds": 604800}}
    if not weekly_only:
        windows["primary_window"] = {"used_percent": 0, "reset_at": now + 18000, "limit_window_seconds": 18000}
    monkeypatch.setattr(
        "app.modules.usage.updater.fetch_usage",
        AsyncMock(return_value=UsagePayload.model_validate({"plan_type": "plus", "rate_limit": windows})),
    )
    probe = AsyncMock(return_value=200)
    monkeypatch.setattr("app.modules.usage.updater.send_usage_probe", probe)
    balancer = LoadBalancer(_proxy_repo_context)
    balancer._runtime["reset-recovery"] = RuntimeState(
        blocked_at=now - 10,
        reset_at=now + 3600,
        cooldown_until=now + 3600,
        error_count=1,
        last_error_at=now - 10,
    )
    response = await async_client.post("/api/accounts/reset-recovery/usage-reset-credits/consume")
    assert response.status_code == 200, response.text
    assert response.json()["accountStatusAfter"] == "active"
    async with SessionLocal() as session:
        account = await AccountsRepository(session).get_by_id("reset-recovery")
        assert account is not None
        assert account.status == AccountStatus.ACTIVE
        assert account.reset_at is None
        assert account.blocked_at is None
    probe.assert_not_awaited()
    selected = await balancer.select_account(routing_strategy="usage_weighted")
    assert selected.account is not None
    assert selected.account.id == "reset-recovery"
    assert balancer._runtime["reset-recovery"].cooldown_until is None
    assert balancer._runtime["reset-recovery"].error_count == 1
    listing = await async_client.get("/api/accounts")
    assert listing.json()["accounts"][0]["status"] == "active"


@pytest.mark.parametrize("outcome", ["reset", "unchanged", "fetch_failure", "newer_block", "paused"])
async def test_dashboard_reset_wakes_stale_usage_once(async_client, monkeypatch, outcome):
    now = await _seed_account(weekly_only=True, old_baseline=True)

    async def fresh(self, account, **kwargs):
        return account

    monkeypatch.setattr(AuthManager, "ensure_fresh", fresh)
    monkeypatch.setattr(
        "app.modules.accounts.service.consume_rate_limit_reset_credit",
        AsyncMock(return_value=ConsumeRateLimitResetCreditResponse(code="reset", windows_reset=1)),
    )
    wake = AsyncMock(return_value=200)
    monkeypatch.setattr("app.modules.usage.updater.send_usage_probe", wake)
    calls = 0

    async def fetch(**kwargs):
        nonlocal calls
        calls += 1
        assert kwargs["account_id"] == "synthetic-workspace"
        if calls == 2:
            wake.assert_awaited_once()
            if outcome == "fetch_failure":
                from app.core.clients.usage import UsageFetchError

                raise UsageFetchError(503, "synthetic failure")
            if outcome in ("newer_block", "paused"):
                async with SessionLocal() as session:
                    await AccountsRepository(session).update_status(
                        "reset-recovery",
                        AccountStatus.PAUSED if outcome == "paused" else AccountStatus.RATE_LIMITED,
                        None,
                        now + 7200,
                        blocked_at=int(time.time()),
                    )
        reopened = calls == 2 and outcome != "unchanged"
        return UsagePayload.model_validate(
            {
                "plan_type": "plus",
                "rate_limit": {
                    "secondary_window": {
                        "used_percent": 0 if reopened else 100,
                        "reset_at": now + (604800 if reopened else 3600),
                        "limit_window_seconds": 604800,
                    }
                },
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", fetch)
    response = await async_client.post("/api/accounts/reset-recovery/usage-reset-credits/consume")
    assert response.status_code == 200, response.text
    assert response.json()["code"] == "reset"
    wake.assert_awaited_once()
    assert calls == 2
    expected = "active" if outcome == "reset" else "paused" if outcome == "paused" else "rate_limited"
    assert response.json()["accountStatusAfter"] == expected


@pytest.mark.parametrize("reopens", [False, True])
async def test_natural_rollover_claim_survives_repeated_refreshes(async_client, monkeypatch, reopens):
    now = await _seed_account(weekly_only=True)
    reset_at = now - 60
    async with SessionLocal() as session:
        await AccountsRepository(session).update_status(
            "reset-recovery",
            AccountStatus.RATE_LIMITED,
            None,
            reset_at,
            blocked_at=now - 300,
        )
        await UsageRepository(session).add_entry(
            "reset-recovery",
            100,
            window="secondary",
            reset_at=reset_at,
            window_minutes=10080,
        )

    async def fresh(self, account, **kwargs):
        return account

    monkeypatch.setattr(AuthManager, "ensure_fresh", fresh)
    wake = AsyncMock(return_value=429)
    monkeypatch.setattr("app.modules.usage.updater.send_usage_probe", wake)

    async def fetch_payload(**kwargs):
        return UsagePayload.model_validate(
            {
                "plan_type": "plus",
                "rate_limit": {
                    "secondary_window": {
                        "used_percent": 0 if reopens and wake.await_count else 100,
                        "reset_at": now + 604800 if reopens and wake.await_count else reset_at,
                        "limit_window_seconds": 604800,
                    },
                },
            }
        )

    fetch = AsyncMock(side_effect=fetch_payload)
    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", fetch)
    for _ in range(3):
        async with SessionLocal() as session:
            accounts = AccountsRepository(session)
            account = await accounts.get_by_id("reset-recovery")
            assert account is not None
            updater = UsageUpdater(UsageRepository(session), accounts)
            await updater._refresh_account(
                account, usage_account_id=account.chatgpt_account_id, allow_rollover_wake=True
            )
    wake.assert_awaited_once()
    assert fetch.await_count == 4
    assert account.status == (AccountStatus.ACTIVE if reopens else AccountStatus.RATE_LIMITED)


@pytest.mark.parametrize("surface", ["credit_picker", "codex", "v1"])
async def test_other_consume_endpoints_recover_using_real_updater(async_client, monkeypatch, surface):
    from app.core.clients.rate_limit_reset_credits import (
        ConsumeResetCreditResponse,
        RateLimitResetCreditsSnapshot,
        ResetCreditItem,
        ResetCreditsResponse,
    )
    from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store

    now = await _seed_account(weekly_only=True)
    credit = ResetCreditItem(id="synthetic-credit", status="available", expires_at=datetime(2099, 1, 1))
    await get_rate_limit_reset_credits_store().set(
        "reset-recovery",
        RateLimitResetCreditsSnapshot(
            available_count=1,
            nearest_expires_at=credit.expires_at,
            credits=[credit],
        ),
    )

    async def fresh(self, account, **kwargs):
        return account

    monkeypatch.setattr(AuthManager, "ensure_fresh", fresh)
    for module in ("app.modules.proxy.api", "app.modules.rate_limit_reset_credits.api"):
        monkeypatch.setattr(
            f"{module}.fetch_reset_credits",
            AsyncMock(
                return_value=ResetCreditsResponse(
                    available_count=1,
                    credits=[credit],
                )
            ),
        )
        monkeypatch.setattr(
            f"{module}.consume_reset_credit",
            AsyncMock(
                return_value=ConsumeResetCreditResponse(
                    code="reset",
                    windows_reset=1,
                    credit={"id": "synthetic-credit", "status": "redeemed", "redeemed_at": "2099-01-01T00:00:00Z"},
                )
            ),
        )
    monkeypatch.setattr(
        "app.modules.proxy.api.consume_rate_limit_reset_credit",
        AsyncMock(
            return_value=ConsumeRateLimitResetCreditResponse(code="reset", windows_reset=1),
        ),
    )
    payload = UsagePayload.model_validate(
        {
            "plan_type": "plus",
            "rate_limit": {
                "secondary_window": {
                    "used_percent": 0,
                    "reset_at": now + 604800,
                    "limit_window_seconds": 604800,
                }
            },
        }
    )
    fetch = AsyncMock(return_value=payload)
    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", fetch)
    monkeypatch.setattr("app.core.auth.dependencies.fetch_usage", AsyncMock(return_value=payload))
    probe = AsyncMock(return_value=200)
    monkeypatch.setattr("app.modules.usage.updater.send_usage_probe", probe)

    if surface == "credit_picker":
        response = await async_client.post("/api/accounts/reset-recovery/rate-limit-reset-credits/consume")
    elif surface == "codex":
        response = await async_client.post(
            "/api/codex/rate-limit-reset-credits/consume",
            headers={"Authorization": "Bearer synthetic-caller-token", "chatgpt-account-id": "synthetic-workspace"},
            json={"redeem_request_id": "synthetic-request"},
        )
    else:
        key_response = await async_client.post("/api/api-keys/", json={"name": "synthetic-reset"})
        assert key_response.status_code == 200
        response = await async_client.post(
            "/v1/reset-credit",
            headers={"Authorization": f"Bearer {key_response.json()['key']}"},
            json={"account_id": "reset-recovery", "redeem_id": "synthetic-credit"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["code"] == "reset"
    async with SessionLocal() as session:
        account = await AccountsRepository(session).get_by_id("reset-recovery")
        assert account is not None and account.status == AccountStatus.ACTIVE
        assert account.blocked_at is None and account.reset_at is None
    probe.assert_not_awaited()
    if surface == "codex":
        assert fetch.await_args is not None
        assert fetch.await_args.kwargs["access_token"] == "synthetic-caller-token"


async def test_rollover_claim_is_atomic_and_does_not_hide_warmup_history(async_client):
    import asyncio

    from app.core.utils.time import utcnow
    from app.modules.limit_warmup.repository import LimitWarmupRepository
    from app.modules.usage.repository import UsageWindowWrite
    from app.modules.usage.reset_recovery import claim_rollover_wake

    now = await _seed_account(weekly_only=True)
    async with SessionLocal() as session:
        account = await AccountsRepository(session).get_by_id("reset-recovery")
        assert account is not None
        ordinary = await LimitWarmupRepository(session).try_create_attempt(
            account_id=account.id,
            window="secondary",
            reset_at=now - 60,
            model="synthetic",
            attempted_at=utcnow(),
        )
        assert ordinary is not None
    window = UsageWindowWrite(window="secondary", used_percent=100, reset_at=now - 60, window_minutes=10080)
    claims = await asyncio.gather(*(claim_rollover_wake(account, [window]) for _ in range(2)))
    assert sum(claim is not None for claim in claims) == 1
    next_window = UsageWindowWrite(window="secondary", used_percent=100, reset_at=now - 10, window_minutes=10080)
    assert await claim_rollover_wake(account, [next_window]) is not None
    async with SessionLocal() as session:
        latest = await LimitWarmupRepository(session).latest_attempt_for_account(account.id)
        assert latest is not None and latest.id == ordinary.id


@pytest.mark.parametrize("transient_count", [0, 2, 4])
async def test_reset_removes_only_quota_error_backoff(async_client, monkeypatch, transient_count):
    now = await _seed_account(weekly_only=True)
    balancer = LoadBalancer(_proxy_repo_context)
    async with SessionLocal() as session:
        account = await AccountsRepository(session).get_by_id("reset-recovery")
        assert account is not None
        if transient_count:
            await balancer.record_errors(account, transient_count)
        for _ in range(4):
            await balancer.mark_rate_limit(account, {"message": "Try again in 3600s"})

    async def fresh(self, account, **kwargs):
        return account

    monkeypatch.setattr(AuthManager, "ensure_fresh", fresh)
    monkeypatch.setattr(
        "app.modules.accounts.service.consume_rate_limit_reset_credit",
        AsyncMock(
            return_value=ConsumeRateLimitResetCreditResponse(code="reset", windows_reset=1),
        ),
    )
    monkeypatch.setattr(
        "app.modules.usage.updater.fetch_usage",
        AsyncMock(
            return_value=UsagePayload.model_validate(
                {
                    "plan_type": "plus",
                    "rate_limit": {
                        "secondary_window": {
                            "used_percent": 0,
                            "reset_at": now + 604800,
                            "limit_window_seconds": 604800,
                        }
                    },
                }
            )
        ),
    )
    response = await async_client.post("/api/accounts/reset-recovery/usage-reset-credits/consume")
    assert response.status_code == 200 and response.json()["accountStatusAfter"] == "active"
    selected = await balancer.select_account(routing_strategy="usage_weighted")
    runtime = balancer._runtime["reset-recovery"]
    assert runtime.error_count == transient_count
    assert runtime.quota_error_count == 0
    assert runtime.cooldown_until is None
    if transient_count < 3:
        assert selected.account is not None
    else:
        assert selected.account is None


@pytest.mark.parametrize("cooldown_elapsed", [True, False])
async def test_account_badge_uses_same_replica_runtime_before_selection(async_client, monkeypatch, cooldown_elapsed):
    from app.dependencies import get_proxy_service_for_app

    now = await _seed_account()
    async with SessionLocal() as session:
        usage = UsageRepository(session)
        await usage.add_entry("reset-recovery", 0, window="primary", reset_at=now + 18000, window_minutes=300)
        await usage.add_entry("reset-recovery", 0, window="secondary", reset_at=now + 604800, window_minutes=10080)
    # This is the same service instance the route and ordinary proxy use.
    service = get_proxy_service_for_app(async_client._transport.app)
    balancer = service._load_balancer
    runtime = RuntimeState(
        blocked_at=now - 10, reset_at=now + 3600, cooldown_until=now - 1 if cooldown_elapsed else now + 3600
    )
    balancer._runtime["reset-recovery"] = runtime
    original_version = runtime.version
    listing = await async_client.get("/api/accounts")
    assert listing.status_code == 200
    expected = "active" if cooldown_elapsed else "rate_limited"
    assert listing.json()["accounts"][0]["status"] == expected
    assert runtime.version == original_version
    selected = await balancer.select_account(routing_strategy="usage_weighted")
    assert (selected.account is not None) == cooldown_elapsed
