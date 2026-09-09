from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.clients import rate_limit_resets
from app.core.clients.rate_limit_resets import ResetCreditDetails, ResetCreditsError, ResetCreditsPayload
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.modules.accounts import reset_credit_scheduler as scheduler_module
from app.modules.accounts.reset_credit_scheduler import (
    ResetCreditAutoRedeemScheduler,
    _rank_candidates_by_credit_expiry,
    exhausted_pool_or_none,
)
from app.modules.accounts.schemas import AccountResetCreditConsumeResponse

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_journal(monkeypatch):
    journal = AsyncMock()
    journal.active.return_value = None
    journal.latest_applied_at.return_value = None
    monkeypatch.setattr(scheduler_module, "ResetCreditAttemptsRepository", lambda session: journal)


def _account(
    account_id: str,
    status: AccountStatus,
    *,
    provider: str = "openai",
    subscription_status: str | None = None,
) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=f"chatgpt-{account_id}",
        email=f"{account_id}@example.com",
        plan_type="pro",
        provider=provider,
        subscription_status=subscription_status,
        access_token_encrypted=encryptor.encrypt("token-not-a-real-secret"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime(2026, 7, 17),
        status=status,
        deactivation_reason=None,
    )


def _credit(credit_id: str, *, expires_at: str | None, status: str = "available") -> ResetCreditDetails:
    return ResetCreditDetails(
        id=credit_id,
        reset_type="codex_rate_limits",
        status=status,
        granted_at="2026-06-18T00:00:00Z",
        expires_at=expires_at,
    )


class TestExhaustedPoolOrNone:
    def test_returns_none_when_any_account_active(self):
        accounts = [
            _account("a1", AccountStatus.ACTIVE),
            _account("a2", AccountStatus.QUOTA_EXCEEDED),
        ]
        assert exhausted_pool_or_none(accounts) is None

    def test_returns_none_for_empty_pool(self):
        assert exhausted_pool_or_none([]) is None
        assert exhausted_pool_or_none([_account("a1", AccountStatus.PAUSED)]) is None

    def test_non_serving_accounts_do_not_block_redemption(self):
        accounts = [
            _account("a1", AccountStatus.QUOTA_EXCEEDED),
            _account("a2", AccountStatus.PAUSED),
            _account("a3", AccountStatus.DEACTIVATED),
            _account("a4", AccountStatus.REAUTH_REQUIRED),
        ]
        result = exhausted_pool_or_none(accounts)
        assert result is not None
        assert [a.id for a in result] == ["a1"]

    def test_canceled_subscription_accounts_are_excluded(self):
        accounts = [
            _account("a1", AccountStatus.QUOTA_EXCEEDED),
            # A canceled-but-active account must not suppress redemption.
            _account("a2", AccountStatus.ACTIVE, subscription_status="canceled"),
        ]
        result = exhausted_pool_or_none(accounts)
        assert result is not None
        assert [a.id for a in result] == ["a1"]

    def test_non_openai_accounts_are_ignored(self):
        accounts = [
            _account("a1", AccountStatus.QUOTA_EXCEEDED),
            _account("a2", AccountStatus.ACTIVE, provider="anthropic"),
        ]
        result = exhausted_pool_or_none(accounts)
        assert result is not None
        assert [a.id for a in result] == ["a1"]

    def test_quota_exceeded_ordered_before_rate_limited(self):
        accounts = [
            _account("a1", AccountStatus.RATE_LIMITED),
            _account("a2", AccountStatus.QUOTA_EXCEEDED),
        ]
        result = exhausted_pool_or_none(accounts)
        assert result is not None
        assert [a.id for a in result] == ["a2", "a1"]


class TestRankCandidates:
    @pytest.mark.asyncio
    async def test_prefers_earliest_expiring_credit(self, monkeypatch):
        accounts = [
            _account("a1", AccountStatus.QUOTA_EXCEEDED),
            _account("a2", AccountStatus.QUOTA_EXCEEDED),
        ]
        credits_by_account = {
            "chatgpt-a1": [_credit("c-late", expires_at="2026-08-12T00:00:00Z")],
            "chatgpt-a2": [_credit("c-soon", expires_at="2026-07-18T00:00:00Z")],
        }

        async def _fake_fetch(*, access_token, chatgpt_account_id):  # noqa: ARG001
            return ResetCreditsPayload(credits=credits_by_account[chatgpt_account_id], available_count=1)

        monkeypatch.setattr(rate_limit_resets, "fetch_reset_credits", _fake_fetch)

        ranked = await _rank_candidates_by_credit_expiry(accounts, TokenEncryptor())
        assert [(a.id, c) for a, c in ranked] == [("a2", "c-soon"), ("a1", "c-late")]

    @pytest.mark.asyncio
    async def test_skips_accounts_without_available_credits_and_fetch_failures(self, monkeypatch):
        accounts = [
            _account("a1", AccountStatus.QUOTA_EXCEEDED),
            _account("a2", AccountStatus.QUOTA_EXCEEDED),
            _account("a3", AccountStatus.QUOTA_EXCEEDED),
        ]

        async def _fake_fetch(*, access_token, chatgpt_account_id):  # noqa: ARG001
            if chatgpt_account_id == "chatgpt-a1":
                raise ResetCreditsError(500, "boom")
            if chatgpt_account_id == "chatgpt-a2":
                return ResetCreditsPayload(
                    credits=[_credit("c-redeemed", expires_at=None, status="redeemed")],
                    available_count=0,
                )
            return ResetCreditsPayload(credits=[_credit("c-ok", expires_at=None)], available_count=1)

        monkeypatch.setattr(rate_limit_resets, "fetch_reset_credits", _fake_fetch)

        ranked = await _rank_candidates_by_credit_expiry(accounts, TokenEncryptor())
        assert [(a.id, c) for a, c in ranked] == [("a3", "c-ok")]


def _scheduler(cooldown_seconds: int = 900) -> ResetCreditAutoRedeemScheduler:
    return ResetCreditAutoRedeemScheduler(
        interval_seconds=60,
        cooldown_seconds=cooldown_seconds,
        enabled=True,
    )


def _consume_response(code: str = "reset") -> AccountResetCreditConsumeResponse:
    return AccountResetCreditConsumeResponse(
        status="redeemed" if code in ("reset", "already_redeemed") else "not_redeemed",
        account_id="a1",
        code=code,
        windows_reset=1 if code == "reset" else 0,
    )


class TestRedeemSweep:
    @pytest.mark.asyncio
    async def test_redeems_first_candidate_and_starts_cooldown(self, monkeypatch):
        scheduler = _scheduler()
        accounts = [_account("a1", AccountStatus.QUOTA_EXCEEDED)]
        service = AsyncMock()
        service.redeem_rate_limit_reset_credit.return_value = _consume_response("reset")
        monkeypatch.setattr(scheduler_module, "_build_accounts_service", lambda repo, session: service)

        async def _fake_rank(candidates, encryptor):  # noqa: ARG001
            return [(accounts[0], "c-1")]

        monkeypatch.setattr(scheduler_module, "_rank_candidates_by_credit_expiry", _fake_rank)

        await scheduler._redeem_first_available(AsyncMock(), AsyncMock(), accounts)
        service.redeem_rate_limit_reset_credit.assert_awaited_once_with("a1", credit_id="c-1", trigger="auto")
        service.probe_account.assert_awaited_once_with("a1")
        assert scheduler._cooldown_active() is True

    @pytest.mark.asyncio
    async def test_uncertain_failure_does_not_consume_next_candidate(self, monkeypatch):
        scheduler = _scheduler()
        accounts = [
            _account("a1", AccountStatus.QUOTA_EXCEEDED),
            _account("a2", AccountStatus.QUOTA_EXCEEDED),
        ]
        calls: list[Any] = []

        async def _redeem(account_id, credit_id=None, *, trigger="auto"):
            calls.append((account_id, credit_id))
            if account_id == "a1":
                raise ResetCreditsError(500, "boom")
            return _consume_response("reset")

        service = AsyncMock()
        service.redeem_rate_limit_reset_credit.side_effect = _redeem
        monkeypatch.setattr(scheduler_module, "_build_accounts_service", lambda repo, session: service)

        async def _fake_rank(candidates, encryptor):  # noqa: ARG001
            return [(accounts[0], "c-1"), (accounts[1], "c-2")]

        monkeypatch.setattr(scheduler_module, "_rank_candidates_by_credit_expiry", _fake_rank)

        await scheduler._redeem_first_available(AsyncMock(), AsyncMock(), accounts)
        assert calls == [("a1", "c-1")]
        assert scheduler._cooldown_active() is False

    @pytest.mark.asyncio
    async def test_no_credits_no_cooldown(self, monkeypatch):
        scheduler = _scheduler()
        service = AsyncMock()
        monkeypatch.setattr(scheduler_module, "_build_accounts_service", lambda repo, session: service)

        async def _fake_rank(candidates, encryptor):  # noqa: ARG001
            return []

        monkeypatch.setattr(scheduler_module, "_rank_candidates_by_credit_expiry", _fake_rank)

        candidates = [_account("a1", AccountStatus.QUOTA_EXCEEDED)]
        await scheduler._redeem_first_available(AsyncMock(), AsyncMock(), candidates)
        service.redeem_rate_limit_reset_credit.assert_not_awaited()
        assert scheduler._cooldown_active() is False

    @pytest.mark.asyncio
    async def test_cooldown_still_checks_leadership_for_pending_recovery(self, monkeypatch):
        scheduler = _scheduler()
        scheduler._last_redeemed_at = scheduler_module.utcnow()

        acquired = AsyncMock()
        acquired.try_acquire.return_value = False
        monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: acquired)

        await scheduler._tick()
        acquired.try_acquire.assert_awaited_once()


class TestExpiresWithin:
    def test_within_window(self):
        now = datetime(2026, 7, 17, 12, 0, 0)
        assert scheduler_module._expires_within("2026-07-18T00:30:00Z", now, timedelta(hours=24)) is True

    def test_outside_window(self):
        now = datetime(2026, 7, 17, 12, 0, 0)
        assert scheduler_module._expires_within("2026-08-12T00:00:00Z", now, timedelta(hours=24)) is False

    def test_missing_or_invalid_expiry(self):
        now = datetime(2026, 7, 17, 12, 0, 0)
        assert scheduler_module._expires_within(None, now, timedelta(hours=24)) is False
        assert scheduler_module._expires_within("not-a-date", now, timedelta(hours=24)) is False


class TestExpirySweep:
    @pytest.mark.asyncio
    async def test_redeems_expiring_credit_without_touching_cooldown(self, monkeypatch):
        scheduler = _scheduler()
        scheduler.expiry_enabled = True
        scheduler.expiry_window_hours = 24
        account = _account("a1", AccountStatus.ACTIVE)

        soon = (scheduler_module.utcnow() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        late = (scheduler_module.utcnow() + timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")

        async def _fake_fetch(*, access_token, chatgpt_account_id):  # noqa: ARG001
            return ResetCreditsPayload(
                credits=[_credit("c-soon", expires_at=soon), _credit("c-late", expires_at=late)],
                available_count=2,
            )

        monkeypatch.setattr(rate_limit_resets, "fetch_reset_credits", _fake_fetch)

        service = AsyncMock()
        service.redeem_rate_limit_reset_credit.return_value = _consume_response("reset")
        monkeypatch.setattr(scheduler_module, "_build_accounts_service", lambda repo, session: service)

        await scheduler._expiry_sweep(AsyncMock(), AsyncMock(), [account])
        service.redeem_rate_limit_reset_credit.assert_awaited_once_with("a1", credit_id="c-soon", trigger="expiring")
        service.probe_account.assert_awaited_once_with("a1")
        assert scheduler._cooldown_active() is False

    @pytest.mark.asyncio
    async def test_nothing_to_reset_is_non_fatal(self, monkeypatch):
        scheduler = _scheduler()
        scheduler.expiry_enabled = True
        account = _account("a1", AccountStatus.ACTIVE)
        soon = (scheduler_module.utcnow() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

        async def _fake_fetch(*, access_token, chatgpt_account_id):  # noqa: ARG001
            return ResetCreditsPayload(credits=[_credit("c-soon", expires_at=soon)], available_count=1)

        monkeypatch.setattr(rate_limit_resets, "fetch_reset_credits", _fake_fetch)

        service = AsyncMock()
        service.redeem_rate_limit_reset_credit.return_value = _consume_response("nothing_to_reset")
        monkeypatch.setattr(scheduler_module, "_build_accounts_service", lambda repo, session: service)

        await scheduler._expiry_sweep(AsyncMock(), AsyncMock(), [account])
        service.redeem_rate_limit_reset_credit.assert_awaited_once()
        service.probe_account.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exhaustion_cooldown_does_not_block_expiry_sweep(self, monkeypatch):
        scheduler = _scheduler()
        scheduler.expiry_enabled = True
        scheduler._last_redeemed_at = scheduler_module.utcnow()

        leader = AsyncMock()
        leader.try_acquire.return_value = True
        monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: leader)

        sweep_calls: list[Any] = []

        async def _fake_sweep(self, session, repo, accounts):  # noqa: ARG001
            sweep_calls.append(accounts)

        monkeypatch.setattr(ResetCreditAutoRedeemScheduler, "_expiry_sweep", _fake_sweep)

        class _FakeSessionCtx:
            async def __aenter__(self):
                return AsyncMock()

            async def __aexit__(self, *args):
                return False

        monkeypatch.setattr(scheduler_module, "get_background_session", lambda: _FakeSessionCtx())

        repo = AsyncMock()
        repo.list_accounts.return_value = []
        monkeypatch.setattr(scheduler_module, "AccountsRepository", lambda session: repo)

        await scheduler._tick()
        assert len(sweep_calls) == 1
        assert scheduler._last_expiry_sweep_at is not None

    @pytest.mark.asyncio
    async def test_expiry_sweep_interval_gating(self):
        scheduler = _scheduler()
        scheduler.expiry_enabled = True
        scheduler.expiry_sweep_interval_seconds = 3600
        assert scheduler._expiry_sweep_due() is True
        scheduler._last_expiry_sweep_at = scheduler_module.utcnow()
        assert scheduler._expiry_sweep_due() is False

    def test_expiry_kill_switch(self):
        scheduler = _scheduler()
        scheduler.expiry_enabled = False
        assert scheduler._expiry_sweep_due() is False
