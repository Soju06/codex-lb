from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update

from app.core.clients import rate_limit_resets
from app.core.clients.rate_limit_resets import ConsumeResetCreditPayload, ResetCreditDetails, ResetCreditsPayload
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ResetCreditAttempt
from app.db.session import SessionLocal
from app.modules.accounts import reset_credit_scheduler as scheduler_module
from app.modules.accounts import service as service_module
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.reset_credit_attempts import ResetCreditAttemptsRepository
from app.modules.accounts.reset_credit_scheduler import ResetCreditAutoRedeemScheduler
from app.modules.accounts.service import AccountResetCreditsUnavailableError, AccountsService
from app.modules.usage.repository import UsageRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _account() -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id="recovery-account",
                email="recovery@example.invalid",
                provider="openai",
                plan_type="pro",
                chatgpt_account_id="recovery-account",
                status=AccountStatus.QUOTA_EXCEEDED,
                access_token_encrypted=encryptor.encrypt("test-access"),
                refresh_token_encrypted=encryptor.encrypt("test-refresh"),
                id_token_encrypted=encryptor.encrypt("test-id"),
                last_refresh=utcnow(),
            )
        )
        await session.commit()


def _service(session) -> AccountsService:
    service = AccountsService(AccountsRepository(session), UsageRepository(session))
    service._usage_updater = AsyncMock()
    service._usage_updater.force_refresh.return_value = True
    return service


@pytest.fixture
def upstream(monkeypatch):
    inventory = ResetCreditsPayload(
        available_count=2,
        credits=[
            ResetCreditDetails(
                id=f"credit-{i}", reset_type="weekly", status="available", granted_at="2026-09-01T00:00:00Z"
            )
            for i in (1, 2)
        ],
    )
    fetch = AsyncMock(return_value=inventory)
    consume = AsyncMock(return_value=ConsumeResetCreditPayload(code="reset", windows_reset=2))
    monkeypatch.setattr(rate_limit_resets, "fetch_reset_credits", fetch)
    monkeypatch.setattr(rate_limit_resets, "consume_reset_credit", consume)
    monkeypatch.setattr(service_module, "refresh_standard_capacity", AsyncMock(return_value=True))
    return consume


async def _expire_lease() -> None:
    async with SessionLocal() as session:
        await session.execute(update(ResetCreditAttempt).values(lease_until=utcnow() - timedelta(seconds=1)))
        await session.commit()


async def test_unknown_consume_survives_restart_and_reuses_request_id(db_setup, upstream):
    await _account()
    upstream.side_effect = [TimeoutError("unknown result"), ConsumeResetCreditPayload(code="already_redeemed")]
    async with SessionLocal() as session:
        with pytest.raises(TimeoutError):
            await _service(session).redeem_rate_limit_reset_credit("recovery-account", "credit-1", trigger="auto")
    first_id = upstream.await_args.kwargs["redeem_request_id"]
    async with SessionLocal() as session:
        active = await ResetCreditAttemptsRepository(session).active()
        assert active.id == first_id and active.state == "pending"
        with pytest.raises(AccountResetCreditsUnavailableError):
            await _service(session).redeem_rate_limit_reset_credit("recovery-account", "credit-2")
    assert upstream.await_count == 1
    await _expire_lease()
    async with SessionLocal() as session:
        result = await _service(session).redeem_rate_limit_reset_credit("recovery-account", "credit-1")
        assert result.code == "already_redeemed"
        assert await ResetCreditAttemptsRepository(session).active() is None
    assert upstream.await_args.kwargs["redeem_request_id"] == first_id


async def test_confirmed_reset_is_durable_before_refresh_and_never_consumed_twice(db_setup, upstream, monkeypatch):
    await _account()
    monkeypatch.setattr(
        service_module, "refresh_standard_capacity", AsyncMock(side_effect=RuntimeError("refresh failed"))
    )
    async with SessionLocal() as session:
        with pytest.raises(RuntimeError, match="refresh failed"):
            await _service(session).redeem_rate_limit_reset_credit("recovery-account", "credit-1", trigger="auto")
    async with SessionLocal() as session:
        journal = ResetCreditAttemptsRepository(session)
        attempt = await journal.active()
        assert attempt.state == "applied" and attempt.result_code == "reset"
        assert await journal.latest_applied_at() is not None
    await _expire_lease()
    monkeypatch.setattr(service_module, "refresh_standard_capacity", AsyncMock(return_value=True))
    async with SessionLocal() as session:
        result = await _service(session).redeem_rate_limit_reset_credit("recovery-account", "credit-1")
        assert result.code == "already_redeemed"
    assert upstream.await_count == 1


async def test_concurrent_sessions_allow_one_reservation_and_fence_stale_settlement(db_setup):
    async with SessionLocal() as first, SessionLocal() as second:
        a = ResetCreditAttemptsRepository(first)
        b = ResetCreditAttemptsRepository(second)
        old = await a.create("account-a", "credit-a", "auto")
        assert old is not None
        assert await b.create("account-b", "credit-b", "expiring") is None
        await _expire_lease()
        current = await b.active()
        assert await b.acquire(current)
        assert current.lease_owner != old.lease_owner
        with pytest.raises(RuntimeError, match="superseded"):
            await a.settle(old, "no_credit")
        assert (await b.active()).id == current.id
        await b.applied(current, "reset", 2)
        async with SessionLocal() as reader:
            row = (await reader.execute(select(ResetCreditAttempt))).scalar_one()
            assert row.state == "applied" and row.active_slot == 1


async def test_zero_inventory_cannot_consume_or_reserve(db_setup, upstream, monkeypatch):
    await _account()
    monkeypatch.setattr(
        rate_limit_resets,
        "fetch_reset_credits",
        AsyncMock(return_value=ResetCreditsPayload(credits=[], available_count=0)),
    )
    async with SessionLocal() as session:
        result = await _service(session).redeem_rate_limit_reset_credit("recovery-account")
        assert result.code == "no_credit"
        assert await ResetCreditAttemptsRepository(session).active() is None
    upstream.assert_not_awaited()


@pytest.mark.parametrize("capacity,redeem_count", [(True, 0), (None, 0), (False, 1)])
async def test_scheduler_refreshes_before_exhaustion_redemption(db_setup, monkeypatch, capacity, redeem_count):
    await _account()
    leader = AsyncMock()
    leader.try_acquire.return_value = True
    monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: leader)

    @asynccontextmanager
    async def session_context():
        async with SessionLocal() as session:
            yield session

    monkeypatch.setattr(scheduler_module, "get_background_session", session_context)
    fresh = AsyncMock(return_value=capacity)
    monkeypatch.setattr(scheduler_module, "refresh_standard_capacity", fresh)
    redeem = AsyncMock()
    monkeypatch.setattr(ResetCreditAutoRedeemScheduler, "_redeem_first_available", redeem)
    scheduler = ResetCreditAutoRedeemScheduler(interval_seconds=60, cooldown_seconds=900, enabled=True)
    await scheduler._tick()
    fresh.assert_awaited_once()
    assert redeem.await_count == redeem_count


async def test_scheduler_restores_auto_cooldown_after_restart(db_setup, monkeypatch):
    await _account()
    async with SessionLocal() as session:
        journal = ResetCreditAttemptsRepository(session)
        attempt = await journal.create("recovery-account", "credit-1", "auto")
        await journal.applied(attempt, "reset", 2)
        await journal.settle(attempt, "reset")
    leader = AsyncMock()
    leader.try_acquire.return_value = True
    monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: leader)

    @asynccontextmanager
    async def session_context():
        async with SessionLocal() as session:
            yield session

    monkeypatch.setattr(scheduler_module, "get_background_session", session_context)
    redeem = AsyncMock()
    monkeypatch.setattr(ResetCreditAutoRedeemScheduler, "_redeem_first_available", redeem)
    scheduler = ResetCreditAutoRedeemScheduler(interval_seconds=60, cooldown_seconds=900, enabled=True)
    await scheduler._tick()
    assert scheduler._cooldown_active()
    redeem.assert_not_awaited()
