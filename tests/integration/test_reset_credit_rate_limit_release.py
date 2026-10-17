from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from app.core.clients.rate_limit_reset_credits import (
    ConsumeResetCreditResponse,
    RateLimitResetCreditsSnapshot,
    ResetCreditItem,
    ResetCreditsResponse,
)
from app.core.clients.usage import ConsumeRateLimitResetCreditResponse
from app.core.crypto import TokenEncryptor
from app.core.usage import reset_credits_refresh_scheduler as reset_credits_scheduler
from app.core.usage.models import RateLimitPayload, UsagePayload, UsageWindow
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.db.session import SessionLocal
from app.modules.rate_limit_reset_credits import api as reset_credits_api
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store
from app.modules.usage.background_repository import BackgroundUsageRepository

pytestmark = pytest.mark.integration


def _jwt(payload: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{encoded}.sig"


async def _blocked_account(
    async_client,
    name: str,
    *,
    window_reset_offset: int = 0,
    extra_windows: tuple[str, ...] = (),
    plan_type: str = "pro",
) -> str:
    """Import an account and persist a weekly-primary 429 block.

    ``window_reset_offset`` shifts the usage row's ``reset_at`` away from the
    account's persisted deadline (a block not derived from that window);
    ``extra_windows`` adds rows in other slots carrying the same deadline.
    """
    upstream_id = f"weekly-{name}"
    email = f"{name}@example.com"
    payload = {
        "email": email,
        "chatgpt_account_id": upstream_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": plan_type},
    }
    auth = {
        "tokens": {
            "idToken": _jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": upstream_id,
        }
    }
    imported = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", json.dumps(auth), "application/json")},
    )
    assert imported.status_code == 200, imported.text
    account_id = imported.json()["accountId"]
    now = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
    old_deadline = now + 3 * 24 * 3600
    blocked_at = now - 3600
    async with SessionLocal() as session:
        await session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(status=AccountStatus.RATE_LIMITED, reset_at=old_deadline, blocked_at=blocked_at)
        )
        session.add(
            UsageHistory(
                account_id=account_id,
                recorded_at=datetime.fromtimestamp(blocked_at + 30, timezone.utc).replace(tzinfo=None),
                window="primary",
                used_percent=100.0,
                reset_at=old_deadline + window_reset_offset,
                window_minutes=10_080,
            )
        )
        for window in extra_windows:
            session.add(
                UsageHistory(
                    account_id=account_id,
                    recorded_at=datetime.fromtimestamp(blocked_at + 30, timezone.utc).replace(tzinfo=None),
                    window=window,
                    used_percent=100.0,
                    reset_at=old_deadline,
                    window_minutes=43_200,
                )
            )
        await session.commit()
    return account_id


async def _fresh_weekly_usage(
    *, used_percent: float = 0.0, reset_at: int | None = None, plan_type: str = "pro"
) -> UsagePayload:
    return UsagePayload(
        plan_type=plan_type,
        rate_limit=RateLimitPayload(
            primary_window=UsageWindow(
                used_percent=used_percent,
                reset_at=reset_at or int(utcnow().replace(tzinfo=timezone.utc).timestamp()) + 7 * 24 * 3600,
                limit_window_seconds=604_800,
            ),
            secondary_window=None,
        ),
    )


async def _assert_rate_limited(account_id: str) -> None:
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        assert account.status == AccountStatus.RATE_LIMITED
        assert account.reset_at is not None
        assert account.blocked_at is not None


async def _assert_active(account_id: str) -> None:
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        assert account.status == AccountStatus.ACTIVE
        assert account.reset_at is None
        assert account.blocked_at is None


async def _patch_usage_fetch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    used_percent: float = 0.0,
    reset_at_from_account: str | None = None,
    plan_type: str = "pro",
) -> None:
    async def _fetch_usage(**_kwargs):
        reset_at = None
        if reset_at_from_account is not None:
            async with SessionLocal() as session:
                account = await session.get(Account, reset_at_from_account)
                assert account is not None
                reset_at = account.reset_at
        return await _fresh_weekly_usage(used_percent=used_percent, reset_at=reset_at, plan_type=plan_type)

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", _fetch_usage)


async def _patch_dashboard_redeem(monkeypatch: pytest.MonkeyPatch, *, code: str, windows_reset: int) -> None:
    async def _fetch_credits(*_args, **_kwargs):
        return ResetCreditsResponse(
            credits=[ResetCreditItem(id="credit-weekly", status="available", expires_at="2030-01-01T00:00:00Z")],
            available_count=1,
        )

    async def _consume(_access_token, _account_id, _credit_id, **_kwargs):
        return ConsumeResetCreditResponse.model_validate(
            {"code": code, "windows_reset": windows_reset, "credit": {"id": "credit-weekly", "status": "redeemed"}}
        )

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", _fetch_credits)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", _consume)


async def _prime_dashboard_credit(account_id: str) -> None:
    await get_rate_limit_reset_credits_store().set(
        account_id,
        RateLimitResetCreditsSnapshot(
            available_count=1,
            nearest_expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
            credits=[ResetCreditItem(id="credit-weekly", status="available", expires_at="2030-01-01T00:00:00Z")],
        ),
    )


@pytest.mark.parametrize("path", ["service", "reset_credits_api"])
@pytest.mark.asyncio
async def test_confirmed_reset_credit_releases_weekly_primary_account(async_client, monkeypatch, path: str) -> None:
    account_id = await _blocked_account(async_client, f"release-{path}")
    await _patch_usage_fetch(monkeypatch)
    if path == "service":
        from app.modules.accounts import service as accounts_service_module

        async def _consume(*_args, **_kwargs):
            return ConsumeRateLimitResetCreditResponse(code="reset", windows_reset=1)

        monkeypatch.setattr(accounts_service_module, "consume_rate_limit_reset_credit", _consume)
        response = await async_client.post(f"/api/accounts/{account_id}/usage-reset-credits/consume")
    else:
        await _patch_dashboard_redeem(monkeypatch, code="reset", windows_reset=1)
        await _prime_dashboard_credit(account_id)
        response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 200, response.text
    await _assert_active(account_id)


@pytest.mark.asyncio
async def test_scheduler_auto_redeem_releases_weekly_primary_account(async_client, monkeypatch) -> None:
    account_id = await _blocked_account(async_client, "release-auto-redeem")
    await _patch_usage_fetch(monkeypatch)
    await _patch_dashboard_redeem(monkeypatch, code="reset", windows_reset=1)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=240)).isoformat().replace("+00:00", "Z")

    async def _fetch_expiring_credit(*_args, **_kwargs):
        return ResetCreditsResponse(
            credits=[ResetCreditItem(id="credit-weekly", status="available", expires_at=expires_at)],
            available_count=1,
        )

    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
    await reset_credits_scheduler.refresh_reset_credits_for_accounts(
        accounts=[account],
        encryptor=TokenEncryptor(),
        store=get_rate_limit_reset_credits_store(),
        fetch_fn=_fetch_expiring_credit,
        auto_redeem_before_expiry=True,
        auto_redeem_window_seconds=300,
    )
    await _assert_active(account_id)


@pytest.mark.parametrize("code,windows_reset", [("nothing_to_reset", 0), ("reset", 0)])
@pytest.mark.asyncio
async def test_unconfirmed_reset_credit_does_not_release_account(
    async_client, monkeypatch, code, windows_reset
) -> None:
    account_id = await _blocked_account(async_client, f"no-confirm-{code}-{windows_reset}")
    await _patch_usage_fetch(monkeypatch)
    await _patch_dashboard_redeem(monkeypatch, code=code, windows_reset=windows_reset)
    await _prime_dashboard_credit(account_id)
    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 200, response.text
    await _assert_rate_limited(account_id)


@pytest.mark.asyncio
async def test_confirmed_reset_with_exhausted_usage_keeps_account_rate_limited(async_client, monkeypatch) -> None:
    account_id = await _blocked_account(async_client, "still-exhausted")
    await _patch_usage_fetch(monkeypatch, used_percent=100.0)
    await _patch_dashboard_redeem(monkeypatch, code="reset", windows_reset=1)
    await _prime_dashboard_credit(account_id)
    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 200, response.text
    await _assert_rate_limited(account_id)


@pytest.mark.parametrize(
    "case,block_kwargs",
    [
        ("unanchored-deadline", {"window_reset_offset": 60}),
        ("ambiguous-deadline", {"extra_windows": ("monthly",)}),
    ],
)
@pytest.mark.asyncio
async def test_confirmed_reset_does_not_release_block_not_bound_to_one_window(
    async_client, monkeypatch, case, block_kwargs
) -> None:
    # A deadline that matches no quota window (Retry-After, model-scoped
    # throttle) or more than one does not identify the window the credit reset.
    account_id = await _blocked_account(async_client, f"unbound-{case}", **block_kwargs)
    await _patch_usage_fetch(monkeypatch)
    await _patch_dashboard_redeem(monkeypatch, code="reset", windows_reset=1)
    await _prime_dashboard_credit(account_id)
    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 200, response.text
    await _assert_rate_limited(account_id)


@pytest.mark.asyncio
async def test_confirmed_reset_does_not_release_when_blocked_window_did_not_move(async_client, monkeypatch) -> None:
    # Upstream reset some window, but the blocked window still reports the
    # persisted deadline, so its 429 is not shown to have ended.
    account_id = await _blocked_account(async_client, "window-unmoved")
    await _patch_usage_fetch(monkeypatch, reset_at_from_account=account_id)
    await _patch_dashboard_redeem(monkeypatch, code="reset", windows_reset=1)
    await _prime_dashboard_credit(account_id)
    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 200, response.text
    await _assert_rate_limited(account_id)


@pytest.mark.asyncio
async def test_429_recorded_during_redemption_is_not_released(async_client, monkeypatch) -> None:
    # A newer 429 lands while the consume request is in flight; the redemption
    # started before it, so it cannot prove that block ended.
    account_id = await _blocked_account(async_client, "crossing-429")
    await _patch_usage_fetch(monkeypatch)
    await _prime_dashboard_credit(account_id)

    async def _fetch_credits(*_args, **_kwargs):
        return ResetCreditsResponse(
            credits=[ResetCreditItem(id="credit-weekly", status="available", expires_at="2030-01-01T00:00:00Z")],
            available_count=1,
        )

    async def _consume_with_crossing_429(*_args, **_kwargs):
        async with SessionLocal() as session:
            await session.execute(update(Account).where(Account.id == account_id).values(blocked_at=int(time.time())))
            await session.commit()
        return ConsumeResetCreditResponse.model_validate(
            {"code": "reset", "windows_reset": 1, "credit": {"id": "credit-weekly", "status": "redeemed"}}
        )

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", _fetch_credits)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", _consume_with_crossing_429)
    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 200, response.text
    await _assert_rate_limited(account_id)


@pytest.mark.asyncio
async def test_429_recorded_during_scheduler_auto_redeem_is_not_released(async_client, monkeypatch) -> None:
    # The scheduler's refresh reloads the account, so it sees the newer 429.
    # Only the pre-consume fence keeps that block from being lifted.
    account_id = await _blocked_account(async_client, "crossing-429-auto")
    await _patch_usage_fetch(monkeypatch)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=240)).isoformat().replace("+00:00", "Z")

    async def _fetch_expiring_credit(*_args, **_kwargs):
        return ResetCreditsResponse(
            credits=[ResetCreditItem(id="credit-weekly", status="available", expires_at=expires_at)],
            available_count=1,
        )

    async def _consume_with_crossing_429(*_args, **_kwargs):
        async with SessionLocal() as session:
            await session.execute(update(Account).where(Account.id == account_id).values(blocked_at=int(time.time())))
            await session.commit()
        return ConsumeResetCreditResponse.model_validate(
            {"code": "reset", "windows_reset": 1, "credit": {"id": "credit-weekly", "status": "redeemed"}}
        )

    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", _consume_with_crossing_429)
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
    await reset_credits_scheduler.refresh_reset_credits_for_accounts(
        accounts=[account],
        encryptor=TokenEncryptor(),
        store=get_rate_limit_reset_credits_store(),
        fetch_fn=_fetch_expiring_credit,
        auto_redeem_before_expiry=True,
        auto_redeem_window_seconds=300,
    )
    await _assert_rate_limited(account_id)


@pytest.mark.asyncio
async def test_refresh_recorded_during_redemption_does_not_hide_blocked_window(async_client, monkeypatch) -> None:
    # An ordinary refresh lands while the consume request is in flight and
    # records the post-reset deadline first. The blocked window is still
    # identified from rows recorded before the redemption started.
    account_id = await _blocked_account(async_client, "intervening-refresh")
    await _patch_usage_fetch(monkeypatch)
    await _prime_dashboard_credit(account_id)

    async def _fetch_credits(*_args, **_kwargs):
        return ResetCreditsResponse(
            credits=[ResetCreditItem(id="credit-weekly", status="available", expires_at="2030-01-01T00:00:00Z")],
            available_count=1,
        )

    async def _consume_with_intervening_refresh(*_args, **_kwargs):
        async with SessionLocal() as session:
            session.add(
                UsageHistory(
                    account_id=account_id,
                    recorded_at=utcnow(),
                    window="primary",
                    used_percent=0.0,
                    reset_at=int(time.time()) + 7 * 24 * 3600,
                    window_minutes=10_080,
                )
            )
            await session.commit()
        return ConsumeResetCreditResponse.model_validate(
            {"code": "reset", "windows_reset": 1, "credit": {"id": "credit-weekly", "status": "redeemed"}}
        )

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", _fetch_credits)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", _consume_with_intervening_refresh)
    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 200, response.text
    await _assert_active(account_id)


@pytest.mark.asyncio
async def test_backdated_refresh_recorded_during_redemption_does_not_hide_blocked_window(
    async_client, monkeypatch
) -> None:
    # A replica with a slow clock records the post-reset deadline during the
    # consume call, with a recorded_at earlier than the redemption start. The
    # baseline is captured before the consume, so it is unaffected.
    account_id = await _blocked_account(async_client, "backdated-refresh")
    await _patch_usage_fetch(monkeypatch)
    await _prime_dashboard_credit(account_id)

    async def _fetch_credits(*_args, **_kwargs):
        return ResetCreditsResponse(
            credits=[ResetCreditItem(id="credit-weekly", status="available", expires_at="2030-01-01T00:00:00Z")],
            available_count=1,
        )

    async def _consume_with_backdated_refresh(*_args, **_kwargs):
        async with SessionLocal() as session:
            session.add(
                UsageHistory(
                    account_id=account_id,
                    recorded_at=utcnow() - timedelta(seconds=30),
                    window="primary",
                    used_percent=0.0,
                    reset_at=int(time.time()) + 7 * 24 * 3600,
                    window_minutes=10_080,
                )
            )
            await session.commit()
        return ConsumeResetCreditResponse.model_validate(
            {"code": "reset", "windows_reset": 1, "credit": {"id": "credit-weekly", "status": "redeemed"}}
        )

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", _fetch_credits)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", _consume_with_backdated_refresh)
    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 200, response.text
    await _assert_active(account_id)


@pytest.mark.asyncio
async def test_stale_row_from_an_older_fetch_does_not_make_the_block_ambiguous(async_client, monkeypatch) -> None:
    # Before upstream moved the weekly window into the primary slot it reported
    # it as secondary, with the same deadline. That older row is not part of
    # the newest fetch, so it does not count as a second matching window.
    account_id = await _blocked_account(async_client, "stale-secondary")
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None and account.blocked_at is not None
        session.add(
            UsageHistory(
                account_id=account_id,
                recorded_at=datetime.fromtimestamp(account.blocked_at - 2 * 24 * 3600, timezone.utc).replace(
                    tzinfo=None
                ),
                window="secondary",
                used_percent=80.0,
                reset_at=account.reset_at,
                window_minutes=10_080,
            )
        )
        await session.commit()
    await _patch_usage_fetch(monkeypatch)
    await _patch_dashboard_redeem(monkeypatch, code="reset", windows_reset=1)
    await _prime_dashboard_credit(account_id)
    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 200, response.text
    await _assert_active(account_id)


@pytest.mark.parametrize("code,windows_reset", [("already_redeemed", 1), ("reset", 0)])
@pytest.mark.asyncio
async def test_unconfirmed_accounts_api_redemption_does_not_release_account(
    async_client, monkeypatch, code, windows_reset
) -> None:
    from app.modules.accounts import service as accounts_service_module

    account_id = await _blocked_account(async_client, f"svc-no-confirm-{code}-{windows_reset}")
    await _patch_usage_fetch(monkeypatch)

    async def _consume(*_args, **_kwargs):
        return ConsumeRateLimitResetCreditResponse(code=code, windows_reset=windows_reset)

    monkeypatch.setattr(accounts_service_module, "consume_rate_limit_reset_credit", _consume)
    response = await async_client.post(f"/api/accounts/{account_id}/usage-reset-credits/consume")
    assert response.status_code == 200, response.text
    await _assert_rate_limited(account_id)


@pytest.mark.asyncio
async def test_failed_baseline_read_still_redeems_but_keeps_account_rate_limited(async_client, monkeypatch) -> None:
    account_id = await _blocked_account(async_client, "baseline-read-failed")
    await _patch_usage_fetch(monkeypatch)
    consumed: list[str] = []

    async def _fetch_credits(*_args, **_kwargs):
        return ResetCreditsResponse(
            credits=[ResetCreditItem(id="credit-weekly", status="available", expires_at="2030-01-01T00:00:00Z")],
            available_count=1,
        )

    async def _consume(_access_token, _account_id, credit_id, **_kwargs):
        consumed.append(credit_id)
        return ConsumeResetCreditResponse.model_validate(
            {"code": "reset", "windows_reset": 1, "credit": {"id": "credit-weekly", "status": "redeemed"}}
        )

    real_latest = BackgroundUsageRepository.latest_entry_for_account
    failures = {"left": 1}

    async def _latest_failing_once(self, *args, **kwargs):
        if failures["left"]:
            failures["left"] -= 1
            raise OperationalError("SELECT usage_history", {}, Exception("database is locked"))
        return await real_latest(self, *args, **kwargs)

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", _fetch_credits)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", _consume)
    monkeypatch.setattr(BackgroundUsageRepository, "latest_entry_for_account", _latest_failing_once)
    await _prime_dashboard_credit(account_id)
    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 200, response.text
    assert failures["left"] == 0
    assert consumed == ["credit-weekly"]
    await _assert_rate_limited(account_id)


@pytest.mark.asyncio
async def test_confirmed_reset_credit_releases_account_with_reported_raw_plan_type(async_client, monkeypatch) -> None:
    # The #2602 report's seat: raw plan type, weekly window in the primary
    # slot, no secondary window. Plan normalization itself is #2511 / #2512.
    raw_plan = "self_serve_business_prolite"
    account_id = await _blocked_account(async_client, "raw-plan", plan_type=raw_plan)
    await _patch_usage_fetch(monkeypatch, plan_type=raw_plan)
    await _patch_dashboard_redeem(monkeypatch, code="reset", windows_reset=1)
    await _prime_dashboard_credit(account_id)
    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 200, response.text
    await _assert_active(account_id)
