from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update

from app.core.clients.rate_limit_reset_credits import (
    ConsumeResetCreditError,
    ConsumeResetCreditResponse,
    ResetCreditFetchError,
)
from app.core.crypto import TokenEncryptor
from app.core.usage import reset_credits_refresh_scheduler as sched
from app.db.models import Account, AuditLog, DashboardSettings, ResetCreditRedeemRequest
from app.db.session import SessionLocal
from app.modules.rate_limit_reset_credits import api, outcomes, redeem_coordination
from app.modules.rate_limit_reset_credits.redeem_coordination import pin_redeem_request
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store
from tests.integration.test_rate_limit_reset_credits_api import (
    _credit,
    _import_test_account,
    _snapshot,
    _upstream_response,
)

pytestmark = pytest.mark.integration


async def _enable_auto(account_id):
    async with SessionLocal() as session:
        await session.execute(update(DashboardSettings).values(auto_redeem_reset_credits_before_expiry=True))
        await session.commit()
        account = await session.get(Account, account_id)
        assert account is not None
        return account


def _confirmed(credit_id):
    return ConsumeResetCreditResponse.model_validate(
        {"code": "reset", "windows_reset": 1, "credit": {"id": credit_id, "status": "redeemed"}}
    )


@pytest.mark.asyncio
async def test_scheduler_processes_remaining_accounts_after_real_consume_session_closes(async_client, monkeypatch):
    account_ids = [
        await _import_test_account(async_client, email=f"burst-{index}@example.com", account_id=f"burst-{index}")
        for index in range(8)
    ]
    await _enable_auto(account_ids[0])
    credit = _credit("exact", expires_at=(datetime.now(UTC) + timedelta(minutes=4)).isoformat())
    consumed = []
    verified = []
    all_verified = asyncio.Event()

    async def fetch(_token, upstream_account_id, **kwargs):
        return _upstream_response([] if upstream_account_id in consumed else [credit])

    async def consume(_token, upstream_account_id, credit_id, **kwargs):
        consumed.append(upstream_account_id)
        return _confirmed(credit_id)

    async def verify(account):
        # Access both identity and credentials after the consuming session was
        # closed; this is where the review's DetachedInstanceError occurred.
        assert account.access_token_encrypted
        verified.append(account.id)
        if len(verified) == len(account_ids):
            all_verified.set()

    monkeypatch.setattr(sched, "fetch_reset_credits", fetch)
    monkeypatch.setattr(api, "consume_reset_credit", consume)
    monkeypatch.setattr(sched, "_refresh_usage_after_auto_redeem", verify)
    monkeypatch.setattr(sched, "_resolve_reset_credits_refresh_route", AsyncMock(return_value=None))
    monkeypatch.setattr(sched, "_resolve_reset_credits_consume_route", AsyncMock(return_value=None))
    monkeypatch.setattr(sched.RateLimitResetCreditsRefreshScheduler, "_startup_delay_seconds", lambda _: 0)
    scheduler = sched.RateLimitResetCreditsRefreshScheduler(interval_seconds=60)
    await scheduler.start()
    try:
        await asyncio.wait_for(all_verified.wait(), timeout=15)
        assert scheduler._task is not None and not scheduler._task.done()
        assert sorted(verified) == sorted(account_ids)
        assert len(consumed) == len(set(consumed)) == 8
    finally:
        await scheduler.stop()
    assert not scheduler._inflight_deadlines
    assert scheduler._task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["dashboard", "automatic", "v1"])
async def test_received_receipt_survives_transient_database_failure(async_client, monkeypatch, path):
    account_id = await _import_test_account(async_client, email=f"settle-{path}@example.com", account_id=path)
    credit = _credit("receipt", expires_at=(datetime.now(UTC) + timedelta(minutes=4)).isoformat())
    store = get_rate_limit_reset_credits_store()
    await store.set(account_id, _snapshot([credit]))
    consume = AsyncMock(return_value=_confirmed(credit.id))
    fetch = AsyncMock(return_value=_upstream_response([credit]))
    real_finish = outcomes.finish_attempt
    writes = 0

    async def fail_once(*args, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 1:
            raise RuntimeError("transient receipt write failure")
        await real_finish(*args, **kwargs)

    monkeypatch.setattr(outcomes, "finish_attempt", fail_once)
    monkeypatch.setattr(api, "consume_reset_credit", consume)
    monkeypatch.setattr(api, "fetch_reset_credits", fetch)
    monkeypatch.setattr(api, "_build_refresh_usage_callback", lambda _: None)
    if path == "automatic":
        account = await _enable_auto(account_id)
        scheduler = sched.RateLimitResetCreditsRefreshScheduler(interval_seconds=60)
        assert await sched._auto_redeem_reset_credit(
            account,
            snapshot=_snapshot([credit]),
            encryptor=TokenEncryptor(),
            store=store,
            fetch_fn=fetch,
            redeem_fn=None,
            resolve_route=None,
            on_confirmed=scheduler._schedule_verification,
        )
        assert scheduler._verifications[account_id].account.id == account_id
    elif path == "v1":
        from tests.integration.test_v1_reset_credit import _create_api_key

        _, key = await _create_api_key(async_client, name="settlement")
        monkeypatch.setattr("app.modules.proxy.api.consume_reset_credit", consume)
        monkeypatch.setattr("app.modules.proxy.api.fetch_reset_credits", fetch)
        monkeypatch.setattr("app.modules.proxy.api._refresh_usage_after_v1_reset_credit_redeem", AsyncMock())
        response = await async_client.post(
            "/v1/reset-credit",
            headers={"Authorization": f"Bearer {key}"},
            json={"account_id": account_id, "redeem_id": credit.id},
        )
        assert response.status_code == 200, response.text
    else:
        response = await async_client.post(
            f"/api/accounts/{account_id}/rate-limit-reset-credits/consume",
            json={"redeemRequestId": "receipt"},
        )
        assert response.status_code == 200, response.text
    receipt = await outcomes.find_credit_request(account_id, credit.id)
    assert receipt is not None and receipt.outcome == "confirmed_reset"
    assert writes == 2
    consume.assert_awaited_once()
    async with SessionLocal() as session:
        audits = (
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "account_rate_limit_reset_credit_consumed",
                    AuditLog.request_id == receipt.redeem_request_id,
                )
            )
        ).all()
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_cancelled_consume_finishes_received_receipt_and_releases_claim(async_client, monkeypatch):
    account_id = await _import_test_account(
        async_client, email="cancel-receipt@example.com", account_id="cancel-receipt"
    )
    credit = _credit("receipt")
    store = get_rate_limit_reset_credits_store()
    await store.set(account_id, _snapshot([credit]))
    entered = asyncio.Event()
    release = asyncio.Event()
    real_finish = outcomes.finish_attempt

    async def blocked_finish(*args, **kwargs):
        entered.set()
        await release.wait()
        await real_finish(*args, **kwargs)

    consume = AsyncMock(return_value=_confirmed(credit.id))
    monkeypatch.setattr(outcomes, "finish_attempt", blocked_finish)
    monkeypatch.setattr(api, "fetch_reset_credits", AsyncMock(return_value=_upstream_response([credit])))
    monkeypatch.setattr(api, "consume_reset_credit", consume)
    monkeypatch.setattr(api, "_build_refresh_usage_callback", lambda _: None)
    url = f"/api/accounts/{account_id}/rate-limit-reset-credits/consume"
    task = asyncio.create_task(async_client.post(url, json={"redeemRequestId": "cancelled"}))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    receipt = await outcomes.get_request(account_id, "cancelled")
    assert receipt is not None and receipt.outcome == "confirmed_reset"
    assert not receipt.usage_verified
    assert store.get(account_id) is None
    # Both durable replay and claim acquisition still work after cancellation.
    response = await async_client.post(url, json={"redeemRequestId": "cancelled"})
    assert response.status_code == 200
    consume.assert_awaited_once()


@pytest.mark.asyncio
async def test_deadline_retry_recovers_before_expiry_with_original_request(async_client, monkeypatch):
    account_id = await _import_test_account(async_client, email="near-expiry@example.com", account_id="near-expiry")
    account = await _enable_auto(account_id)
    now = datetime.now(UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    for module in (sched, outcomes, api):
        monkeypatch.setattr(module, "datetime", Clock)
    expiry = now + timedelta(seconds=20)
    credit = _credit("original", expires_at=expiry.isoformat())
    snapshot = _snapshot([credit])
    scheduler = sched.RateLimitResetCreditsRefreshScheduler(interval_seconds=60)
    calls = []

    async def consume(_token, _upstream_id, credit_id, **kwargs):
        calls.append((credit_id, kwargs["redeem_request_id"]))
        scheduler._stop.set()
        if len(calls) == 1:
            raise ConsumeResetCreditError(status_code=503, message="temporary failure")
        return _confirmed(credit_id)

    monkeypatch.setattr(api, "consume_reset_credit", consume)
    monkeypatch.setattr(sched, "fetch_reset_credits", AsyncMock(return_value=_upstream_response([credit])))
    monkeypatch.setattr(sched, "_resolve_reset_credits_consume_route", AsyncMock(return_value=None))
    scheduler._schedule_snapshot(account, snapshot)
    await scheduler._deadline_worker()
    due_at = scheduler._deadlines[account_id].due_at
    receipt = await outcomes.find_credit_request(account_id, credit.id)
    assert receipt is not None and receipt.next_retry_at is not None
    assert now < due_at == outcomes.utc(receipt.next_retry_at) < expiry
    now = due_at
    scheduler._stop.clear()
    await scheduler._deadline_worker()
    assert len(calls) == 2 and calls[0] == calls[1]
    assert account_id not in scheduler._deadlines
    receipt = await outcomes.find_credit_request(account_id, credit.id)
    assert receipt is not None and receipt.outcome == "confirmed_reset"
    assert outcomes.retry_at(expiry - timedelta(seconds=1), expiry) == expiry


@pytest.mark.asyncio
async def test_post_consume_preserves_authoritative_zero(async_client, monkeypatch):
    aid = await _import_test_account(async_client, email="zero@example.com", account_id="zero")
    first, second = _credit("first"), _credit("second")
    store = get_rate_limit_reset_credits_store()
    await store.set(aid, _snapshot([first, second]))
    done = False

    async def fetch(*a, **kw):
        return _upstream_response([first, second], available_count=0 if done else 2)

    async def consume(*a, **kw):
        nonlocal done
        done = True
        return ConsumeResetCreditResponse.model_validate(
            {"code": "reset", "windows_reset": 1, "credit": {"id": "first", "status": "redeemed"}}
        )

    monkeypatch.setattr(api, "fetch_reset_credits", fetch)
    monkeypatch.setattr(api, "consume_reset_credit", consume)
    monkeypatch.setattr(api, "_build_refresh_usage_callback", lambda _: None)
    result = await async_client.post(
        f"/api/accounts/{aid}/rate-limit-reset-credits/consume", json={"redeemRequestId": "zero-test"}
    )
    assert result.status_code == 200
    summary = (await async_client.get(f"/api/accounts/{aid}/summary")).json()
    assert summary["availableResetCredits"] == 0, summary["availableResetCredits"]


@pytest.mark.asyncio
async def test_two_manual_requests_do_not_reconsume_confirmed_credit(async_client, monkeypatch):
    aid = await _import_test_account(async_client, email="manual@example.com", account_id="manual")
    first, second = _credit("first"), _credit("second", expires_at="2026-12-01T00:00:00Z")
    store = get_rate_limit_reset_credits_store()
    await store.set(aid, _snapshot([first, second]))
    calls = []

    async def fetch(*a, **kw):
        return _upstream_response([first, second])

    async def consume(*a, **kw):
        calls.append((a[2], kw["redeem_request_id"]))
        return ConsumeResetCreditResponse.model_validate(
            {"code": "reset", "windows_reset": 1, "credit": {"id": a[2], "status": "redeemed"}}
        )

    monkeypatch.setattr(api, "fetch_reset_credits", fetch)
    monkeypatch.setattr(api, "consume_reset_credit", consume)
    monkeypatch.setattr(api, "_build_refresh_usage_callback", lambda _: None)
    url = f"/api/accounts/{aid}/rate-limit-reset-credits/consume"
    for request_id, expected in (("R1", 200), ("R2", 409), ("R1", 200)):
        response = await async_client.post(url, json={"redeemRequestId": request_id})
        assert response.status_code == expected, response.text
    assert [c[0] for c in calls] == ["first"], calls


@pytest.mark.asyncio
async def test_failed_preflight_after_consume_failure_keeps_future_backoff(async_client, monkeypatch):
    aid = await _import_test_account(async_client, email="past-due@example.com", account_id="past-due")
    account = await _enable_auto(aid)
    now = datetime.now(UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    for module in (sched, outcomes, api):
        monkeypatch.setattr(module, "datetime", Clock)
    expiry = now + timedelta(seconds=20)
    credit = _credit("C1", expires_at=expiry.isoformat())
    scheduler = sched.RateLimitResetCreditsRefreshScheduler(interval_seconds=60)

    async def consume(*args, **kwargs):
        scheduler._stop.set()
        raise ConsumeResetCreditError(503, "transient POST failure")

    monkeypatch.setattr(api, "consume_reset_credit", consume)
    monkeypatch.setattr(sched, "_resolve_reset_credits_consume_route", AsyncMock(return_value=None))
    monkeypatch.setattr(sched, "fetch_reset_credits", AsyncMock(return_value=_upstream_response([credit])))
    scheduler._schedule_snapshot(account, _snapshot([credit]))
    await scheduler._deadline_worker()
    now = scheduler._deadlines[aid].due_at + timedelta(milliseconds=1)

    async def failed_preflight(*args, **kwargs):
        scheduler._stop.set()
        raise ResetCreditFetchError(403, "failed GET")

    monkeypatch.setattr(sched, "fetch_reset_credits", failed_preflight)
    scheduler._stop.clear()
    await scheduler._deadline_worker()
    due_at = scheduler._deadlines[aid].due_at
    assert due_at > now, "past persisted retry time causes immediate retries without backoff"


@pytest.mark.asyncio
async def test_partial_deadline_restore_does_not_leave_expired_orm_work(async_client, monkeypatch):
    ids = [
        await _import_test_account(async_client, email=f"restore{i}@example.com", account_id=f"restore{i}")
        for i in range(2)
    ]
    for index, aid in enumerate(ids):
        await pin_redeem_request(aid, f"R{index}", f"C{index}")
        await outcomes.finish_attempt(aid, f"R{index}", "confirmed_reset", result=_confirmed(f"C{index}"))
    real_background_session = sched.get_background_session

    @asynccontextmanager
    async def partial_failure():
        async with real_background_session() as session:
            real_get = session.get
            calls = 0

            async def get(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("transient database read failure during second account")
                return await real_get(*args, **kwargs)

            monkeypatch.setattr(session, "get", get)
            yield session

    monkeypatch.setattr(sched, "get_background_session", partial_failure)
    scheduler = sched.RateLimitResetCreditsRefreshScheduler(interval_seconds=60)
    await scheduler._restore_deadlines()
    assert len(scheduler._verifications) == 1
    work = next(iter(scheduler._verifications.values()))
    assert work.account.id in ids, "queued work must remain readable after failed restoration cleanup"


@pytest.mark.asyncio
async def test_no_body_retry_recovers_original_request_after_transient_failure(async_client, monkeypatch):
    account_id = await _import_test_account(async_client, email="no-body-retry@example.com", account_id="no-body-retry")
    credit = _credit("original", expires_at=(datetime.now(UTC) + timedelta(minutes=10)).isoformat())
    await get_rate_limit_reset_credits_store().set(account_id, _snapshot([credit]))
    monkeypatch.setattr(api, "fetch_reset_credits", AsyncMock(return_value=_upstream_response([credit])))
    consume = AsyncMock(side_effect=[ConsumeResetCreditError(503, "temporary failure"), _confirmed(credit.id)])
    monkeypatch.setattr(api, "consume_reset_credit", consume)
    monkeypatch.setattr(api, "_build_refresh_usage_callback", lambda _: None)
    url = f"/api/accounts/{account_id}/rate-limit-reset-credits/consume"
    first = await async_client.post(url)
    assert first.status_code == 503
    recovered = await async_client.post(url)
    assert recovered.status_code == 200, recovered.text
    assert consume.await_count == 2
    assert (
        consume.await_args_list[0].kwargs["redeem_request_id"] == consume.await_args_list[1].kwargs["redeem_request_id"]
    )
    assert consume.await_args_list[0].args[2] == consume.await_args_list[1].args[2] == credit.id


@pytest.mark.asyncio
async def test_new_client_id_recovers_original_attempt_and_stays_bound_after_confirmation(async_client, monkeypatch):
    account_id = await _import_test_account(async_client, email="reload@example.com", account_id="reload")
    first = _credit("C1", expires_at=(datetime.now(UTC) + timedelta(minutes=10)).isoformat())
    later = _credit("C2", expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat())
    await get_rate_limit_reset_credits_store().set(account_id, _snapshot([first, later]))
    credits = [first, later]

    async def fetch(*args, **kwargs):
        return _upstream_response(credits)

    consume = AsyncMock(side_effect=[ConsumeResetCreditError(503, "temporary failure"), _confirmed(first.id)])
    monkeypatch.setattr(api, "fetch_reset_credits", fetch)
    monkeypatch.setattr(api, "consume_reset_credit", consume)
    monkeypatch.setattr(api, "_build_refresh_usage_callback", lambda _: None)
    url = f"/api/accounts/{account_id}/rate-limit-reset-credits/consume"
    assert (await async_client.post(url, json={"redeemRequestId": "R1"})).status_code == 503
    recovered = await async_client.post(url, json={"redeemRequestId": "R2"})
    assert recovered.status_code == 200, recovered.text
    credits = [later]
    for request_id in ("R1", "R2"):
        replay = await async_client.post(url, json={"redeemRequestId": request_id})
        assert replay.status_code == 200 and replay.json()["outcome"] == "confirmed_reset"
    assert consume.await_count == 2
    assert [call.kwargs["redeem_request_id"] for call in consume.await_args_list] == ["R1", "R1"]
    assert [call.args[2] for call in consume.await_args_list] == ["C1", "C1"]


@pytest.mark.asyncio
async def test_alias_replay_after_original_pin_ages_out(async_client, monkeypatch):
    aid = await _import_test_account(async_client, email="ttl@example.com", account_id="ttl")
    expiry = datetime.now(UTC) + timedelta(days=2)
    c1 = _credit("C1", expires_at=expiry.isoformat())
    c2 = _credit("C2", expires_at=(expiry + timedelta(days=1)).isoformat())
    store = get_rate_limit_reset_credits_store()
    await store.set(aid, _snapshot([c1, c2]))
    current = [c1, c2]

    async def fetch(*args, **kwargs):
        return _upstream_response(current)

    consume = AsyncMock(side_effect=[ConsumeResetCreditError(503, "transient"), _confirmed("C1"), _confirmed("C1")])
    monkeypatch.setattr(api, "fetch_reset_credits", fetch)
    monkeypatch.setattr(api, "consume_reset_credit", consume)
    monkeypatch.setattr(api, "_build_refresh_usage_callback", lambda _: None)
    url = f"/api/accounts/{aid}/rate-limit-reset-credits/consume"
    assert (await async_client.post(url, json={"redeemRequestId": "R1"})).status_code == 503
    async with SessionLocal() as session:
        await session.execute(
            update(ResetCreditRedeemRequest)
            .where(ResetCreditRedeemRequest.redeem_request_id == "R1")
            .values(created_at=datetime.now(UTC) - timedelta(hours=23))
        )
        await session.commit()
    assert (await async_client.post(url, json={"redeemRequestId": "R2"})).status_code == 200
    current = [c2]
    # One hour later: R2 is still valid, but its canonical receipt's pin has expired.
    async with SessionLocal() as session:
        await session.execute(
            update(ResetCreditRedeemRequest)
            .where(ResetCreditRedeemRequest.redeem_request_id == "R1")
            .values(created_at=datetime.now(UTC) - timedelta(hours=25))
        )
        await session.commit()
    # An unrelated pin triggers normal purge while the alias is still live.
    await pin_redeem_request(aid, "other-request", "other-credit")
    response = await async_client.post(url, json={"redeemRequestId": "R2"})
    assert response.status_code == 200 and response.json()["outcome"] == "confirmed_reset"
    assert any(row.redeem_request_id == "R1" for row in await outcomes.unresolved_requests())
    assert consume.await_count == 2, (
        "Valid alias must replay confirmed receipt, not consume again under a new upstream identity"
    )
    async with SessionLocal() as session:
        await session.execute(
            update(ResetCreditRedeemRequest)
            .where(ResetCreditRedeemRequest.redeem_request_id == "R2")
            .values(created_at=datetime.now(UTC) - timedelta(hours=25))
        )
        await session.commit()
    await pin_redeem_request(aid, "purge-request", "other-credit")
    assert await outcomes.get_request(aid, "R1") is None
    assert await outcomes.get_request(aid, "R2") is None


@pytest.mark.asyncio
async def test_alias_clock_skew_does_not_replace_canonical_owner(async_client, monkeypatch):
    aid = await _import_test_account(async_client, email="clock@example.com", account_id="clock")
    c1 = _credit("C1", expires_at=(datetime.now(UTC) + timedelta(days=2)).isoformat())
    store = get_rate_limit_reset_credits_store()
    await store.set(aid, _snapshot([c1]))
    consume = AsyncMock(side_effect=[ConsumeResetCreditError(503, "transient"), _confirmed("C1"), _confirmed("C1")])
    monkeypatch.setattr(api, "fetch_reset_credits", AsyncMock(return_value=_upstream_response([c1])))
    monkeypatch.setattr(api, "consume_reset_credit", consume)
    monkeypatch.setattr(api, "_build_refresh_usage_callback", lambda _: None)
    url = f"/api/accounts/{aid}/rate-limit-reset-credits/consume"
    assert (await async_client.post(url, json={"redeemRequestId": "R1"})).status_code == 503

    class SlowReplica(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) - timedelta(seconds=30)

    with monkeypatch.context() as m:
        m.setattr(redeem_coordination, "datetime", SlowReplica)
        assert (await async_client.post(url, json={"redeemRequestId": "R2"})).status_code == 200
    response = await async_client.post(url, json={"redeemRequestId": "R2"})
    assert response.status_code == 200 and response.json()["outcome"] == "confirmed_reset"
    assert consume.await_count == 2
