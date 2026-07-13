"""Cross-replica safety regressions for reset-credit redemption.

Two replicas are simulated with the repo's established patterns: separate
``SessionLocal`` sessions over the one shared database, and a fresh
``RateLimitResetCreditsStore`` instance standing in for a freshly booted
replica's process-local memory.
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.auth import generate_unique_account_id
from app.core.cache.invalidation import NAMESPACE_RESET_CREDITS, CacheInvalidationPoller
from app.core.clients.rate_limit_reset_credits import (
    ConsumeResetCreditResponse,
    RateLimitResetCreditsSnapshot,
    ResetCreditItem,
    ResetCreditsResponse,
)
from app.db.models import ResetCreditRedeemClaim, ResetCreditRedeemRequest
from app.db.session import SessionLocal
from app.modules.rate_limit_reset_credits import api as reset_credits_api
from app.modules.rate_limit_reset_credits.redeem_coordination import (
    RedeemClaimTimeoutError,
    acquire_redeem_claim,
    get_pinned_redeem_credit_id,
    pin_redeem_request,
    release_redeem_claim,
    renew_redeem_claim_periodically,
    try_acquire_redeem_claim,
)
from app.modules.rate_limit_reset_credits.store import (
    RateLimitResetCreditsStore,
    get_rate_limit_reset_credits_store,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _clear_reset_credit_store():
    await get_rate_limit_reset_credits_store().invalidate()
    yield
    await get_rate_limit_reset_credits_store().invalidate()


def _encode_jwt(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


async def _import_account(async_client, *, email: str, account_id: str) -> str:
    payload: dict[str, object] = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token-not-a-real-secret",
            "refreshToken": "refresh",
            "accountId": account_id,
        },
    }
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200, response.text
    return generate_unique_account_id(account_id, email)


async def _create_api_key(async_client, *, name: str) -> str:
    response = await async_client.post("/api/api-keys/", json={"name": name})
    assert response.status_code == 200
    return response.json()["key"]


def _credit(
    credit_id: str,
    *,
    status: str = "available",
    expires_at: str | None = "2026-08-01T00:00:00Z",
) -> ResetCreditItem:
    return ResetCreditItem.model_validate({"id": credit_id, "status": status, "expires_at": expires_at})


def _upstream_response(credits: list[ResetCreditItem], available_count: int | None = None) -> ResetCreditsResponse:
    count = available_count if available_count is not None else sum(1 for c in credits if c.status == "available")
    return ResetCreditsResponse(credits=credits, available_count=count)


def _snapshot(credits: list[ResetCreditItem]) -> RateLimitResetCreditsSnapshot:
    expiries = [
        credit.expires_at for credit in credits if credit.status == "available" and credit.expires_at is not None
    ]
    return RateLimitResetCreditsSnapshot(
        available_count=sum(1 for credit in credits if credit.status == "available"),
        nearest_expires_at=min(expiries) if expiries else None,
        credits=credits,
    )


def _success_consume(credit_id: str) -> ConsumeResetCreditResponse:
    return ConsumeResetCreditResponse.model_validate(
        {
            "code": "reset",
            "credit": {"id": credit_id, "status": "redeemed", "redeemed_at": "2026-07-12T01:02:03Z"},
            "windows_reset": 1,
        }
    )


async def _noop_refresh(account) -> None:  # noqa: ANN001
    return None


# --- cross-replica idempotency (durable redeem_request_id -> credit_id ledger) ---


@pytest.mark.asyncio
async def test_dashboard_redeem_retry_on_second_replica_reuses_pinned_credit(async_client, monkeypatch) -> None:
    """A retry served by a different replica must consume the original credit.

    Before the durable ledger, the redeem_request_id -> credit_id map lived in
    process memory, so replica B picked (and burned) a second credit.
    """
    account_id = await _import_account(
        async_client,
        email="replica-retry@example.com",
        account_id="acc_replica_retry",
    )

    soonest = _credit("credit-1", expires_at="2026-07-20T00:00:00Z")
    later = _credit("credit-2", expires_at="2026-08-20T00:00:00Z")
    upstream_state = {"response": _upstream_response([soonest, later])}
    consume_calls: list[str] = []

    async def fake_fetch(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        return upstream_state["response"]

    async def fake_consume(
        access_token: str,
        chatgpt_account_id: str | None,
        credit_id: str,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        consume_calls.append(credit_id)
        return _success_consume(credit_id)

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", fake_fetch)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", fake_consume)
    monkeypatch.setattr(reset_credits_api, "_build_refresh_usage_callback", lambda _context: _noop_refresh)

    # Replica A: first attempt selects and consumes the soonest credit.
    await get_rate_limit_reset_credits_store().set(account_id, _snapshot([soonest, later]))
    first = await async_client.post(
        f"/api/accounts/{account_id}/rate-limit-reset-credits/consume",
        json={"redeemRequestId": "retry-R"},
    )
    assert first.status_code == 200, first.text
    assert consume_calls == ["credit-1"]

    # Replica B: fresh process memory whose refresh already observed the
    # post-redeem upstream state (credit-1 redeemed, credit-2 available).
    upstream_state["response"] = _upstream_response(
        [_credit("credit-1", status="redeemed", expires_at="2026-07-20T00:00:00Z"), later]
    )
    replica_b_store = RateLimitResetCreditsStore()
    await replica_b_store.set(account_id, _snapshot([later]))
    monkeypatch.setattr(reset_credits_api, "get_rate_limit_reset_credits_store", lambda: replica_b_store)

    retry = await async_client.post(
        f"/api/accounts/{account_id}/rate-limit-reset-credits/consume",
        json={"redeemRequestId": "retry-R"},
    )

    assert retry.status_code == 200, retry.text
    # The retry re-targets the originally pinned credit; credit-2 stays unburned.
    assert consume_calls == ["credit-1", "credit-1"]

    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(ResetCreditRedeemRequest).where(ResetCreditRedeemRequest.account_id == account_id)
                )
            )
            .scalars()
            .all()
        )
    assert [(row.redeem_request_id, row.credit_id) for row in rows] == [("retry-R", "credit-1")]


@pytest.mark.asyncio
async def test_dashboard_redeem_with_request_id_but_no_pin_returns_409_on_empty_fetch(
    async_client, monkeypatch
) -> None:
    """A retry-shaped request (redeem_request_id supplied) with a stale cached
    credit but NO durable pin must treat the fresh empty fetch as authoritative
    and 409 instead of pinning the stale credit and consuming upstream."""
    account_id = await _import_account(
        async_client,
        email="stale-cache-no-pin@example.com",
        account_id="acc_stale_no_pin",
    )

    stale = _credit("credit-stale", expires_at="2026-07-20T00:00:00Z")
    consume_calls: list[str] = []

    async def fake_fetch(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        # Upstream is authoritative and reports nothing available.
        return _upstream_response([_credit("credit-stale", status="redeemed")], available_count=0)

    async def fake_consume(
        access_token: str,
        chatgpt_account_id: str | None,
        credit_id: str,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        consume_calls.append(credit_id)
        return _success_consume(credit_id)

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", fake_fetch)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", fake_consume)
    monkeypatch.setattr(reset_credits_api, "_build_refresh_usage_callback", lambda _context: _noop_refresh)

    # Stale local snapshot still lists the credit as available.
    await get_rate_limit_reset_credits_store().set(account_id, _snapshot([stale]))

    response = await async_client.post(
        f"/api/accounts/{account_id}/rate-limit-reset-credits/consume",
        json={"redeemRequestId": "never-pinned-R"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "no_available_reset_credit"
    # No upstream consume for the stale credit.
    assert consume_calls == []
    # The stale snapshot was replaced with the fresh empty one.
    snapshot = get_rate_limit_reset_credits_store().get(account_id)
    assert snapshot is not None
    assert snapshot.available_count == 0
    # Nothing was pinned for the unproven retry.
    assert await get_pinned_redeem_credit_id(account_id, "never-pinned-R") is None


# --- SQLite cross-process serialization via the durable claim row ---


@pytest.mark.asyncio
async def test_redeem_claim_serializes_two_holders_across_sessions(async_client) -> None:
    account_id = await _import_account(
        async_client,
        email="claim-serialize@example.com",
        account_id="acc_claim_serialize",
    )

    assert await try_acquire_redeem_claim(account_id, "holder-1") is True
    # A second process cannot take a live claim.
    assert await try_acquire_redeem_claim(account_id, "holder-2") is False

    await release_redeem_claim(account_id, "holder-1")
    assert await try_acquire_redeem_claim(account_id, "holder-2") is True
    await release_redeem_claim(account_id, "holder-2")


@pytest.mark.asyncio
async def test_acquire_redeem_claim_times_out_while_peer_holds(async_client) -> None:
    account_id = await _import_account(
        async_client,
        email="claim-timeout@example.com",
        account_id="acc_claim_timeout",
    )

    assert await try_acquire_redeem_claim(account_id, "holder-1") is True
    try:
        with pytest.raises(RedeemClaimTimeoutError):
            await acquire_redeem_claim(
                account_id,
                "holder-2",
                retry_interval_seconds=0.02,
                timeout_seconds=0.2,
            )
    finally:
        await release_redeem_claim(account_id, "holder-1")


@pytest.mark.asyncio
async def test_expired_redeem_claim_is_taken_over(async_client) -> None:
    account_id = await _import_account(
        async_client,
        email="claim-expired@example.com",
        account_id="acc_claim_expired",
    )

    async with SessionLocal() as session:
        session.add(
            ResetCreditRedeemClaim(
                account_id=account_id,
                holder_id="crashed-holder",
                expires_at=datetime.now(UTC) - timedelta(seconds=5),
            )
        )
        await session.commit()

    assert await try_acquire_redeem_claim(account_id, "holder-2") is True
    await release_redeem_claim(account_id, "holder-2")


@pytest.mark.asyncio
async def test_redeem_claim_heartbeat_keeps_live_claim_from_takeover(async_client) -> None:
    """A renewed lease outlives its original expiry; without the heartbeat the
    claim would be taken over mid-section by a second process."""
    account_id = await _import_account(
        async_client,
        email="claim-heartbeat@example.com",
        account_id="acc_claim_heartbeat",
    )

    assert await try_acquire_redeem_claim(account_id, "holder-1", lease_seconds=0.5) is True
    heartbeat = asyncio.create_task(
        renew_redeem_claim_periodically(
            account_id,
            "holder-1",
            lease_seconds=0.5,
            renew_interval_seconds=0.1,
        )
    )
    try:
        # Well past the original 0.5s lease: renewal must still hold the claim.
        await asyncio.sleep(1.0)
        assert await try_acquire_redeem_claim(account_id, "holder-2") is False
    finally:
        heartbeat.cancel()
        with pytest.raises(asyncio.CancelledError):
            await heartbeat

    # Once the heartbeat stops, lease expiry recovers the claim as before.
    await asyncio.sleep(0.7)
    assert await try_acquire_redeem_claim(account_id, "holder-2") is True
    await release_redeem_claim(account_id, "holder-2")


@pytest.mark.asyncio
async def test_dashboard_consume_outliving_the_lease_keeps_the_claim(async_client, monkeypatch) -> None:
    """A redemption slower than one lease is not taken over by a peer process."""
    account_id = await _import_account(
        async_client,
        email="claim-slow-redeem@example.com",
        account_id="acc_claim_slow_redeem",
    )

    monkeypatch.setattr(
        reset_credits_api,
        "acquire_redeem_claim",
        partial(acquire_redeem_claim, lease_seconds=0.5),
    )
    monkeypatch.setattr(
        reset_credits_api,
        "renew_redeem_claim_periodically",
        partial(renew_redeem_claim_periodically, lease_seconds=0.5, renew_interval_seconds=0.1),
    )

    only = _credit("credit-slow", expires_at="2026-07-20T00:00:00Z")

    async def fake_fetch(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        return _upstream_response([only])

    consume_entered = asyncio.Event()
    consume_release = asyncio.Event()
    consume_calls: list[str] = []

    async def fake_consume(
        access_token: str,
        chatgpt_account_id: str | None,
        credit_id: str,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        consume_calls.append(credit_id)
        consume_entered.set()
        await consume_release.wait()
        return _success_consume(credit_id)

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", fake_fetch)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", fake_consume)
    monkeypatch.setattr(reset_credits_api, "_build_refresh_usage_callback", lambda _context: _noop_refresh)

    await get_rate_limit_reset_credits_store().set(account_id, _snapshot([only]))

    request = asyncio.create_task(async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume"))
    await asyncio.wait_for(consume_entered.wait(), timeout=10)

    # Hold the critical section past the original 0.5s lease; a second
    # process's takeover attempt must fail because the heartbeat renewed it.
    await asyncio.sleep(1.0)
    assert await try_acquire_redeem_claim(account_id, "intruder") is False

    consume_release.set()
    response = await request
    assert response.status_code == 200, response.text
    assert consume_calls == ["credit-slow"]


@pytest.mark.asyncio
async def test_concurrent_dashboard_consumes_contend_on_db_claim(async_client, monkeypatch) -> None:
    """Two concurrent consume requests share one upstream consume.

    On the sqlite-with-session arm the durable claim row is the sole
    serializer, so the two request tasks contend exactly as two processes
    sharing one SQLite file would.
    """
    account_id = await _import_account(
        async_client,
        email="claim-route@example.com",
        account_id="acc_claim_route",
    )

    only = _credit("credit-only", expires_at="2026-07-20T00:00:00Z")
    fetch_calls = 0

    async def fake_fetch(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        nonlocal fetch_calls
        fetch_calls += 1
        if fetch_calls == 1:
            return _upstream_response([only])
        return _upstream_response([], available_count=0)

    started = asyncio.Event()
    release = asyncio.Event()
    consume_calls: list[str] = []

    async def fake_consume(
        access_token: str,
        chatgpt_account_id: str | None,
        credit_id: str,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        consume_calls.append(credit_id)
        started.set()
        await release.wait()
        return _success_consume(credit_id)

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", fake_fetch)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", fake_consume)
    monkeypatch.setattr(reset_credits_api, "_build_refresh_usage_callback", lambda _context: _noop_refresh)

    await get_rate_limit_reset_credits_store().set(account_id, _snapshot([only]))

    first = asyncio.create_task(async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume"))
    await asyncio.wait_for(started.wait(), timeout=10)

    second = asyncio.create_task(async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume"))
    # Let the second request reach (and spin on) the claim while the first
    # holder is mid-consume.
    await asyncio.sleep(0.3)
    assert consume_calls == ["credit-only"]

    release.set()
    first_response = await first
    second_response = await second

    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 409
    assert second_response.json()["error"]["code"] == "no_available_reset_credit"
    # The single available credit was consumed exactly once.
    assert consume_calls == ["credit-only"]


@pytest.mark.asyncio
async def test_dashboard_consume_succeeds_over_expired_claim(async_client, monkeypatch) -> None:
    account_id = await _import_account(
        async_client,
        email="claim-lease@example.com",
        account_id="acc_claim_lease",
    )

    async with SessionLocal() as session:
        session.add(
            ResetCreditRedeemClaim(
                account_id=account_id,
                holder_id="crashed-holder",
                expires_at=datetime.now(UTC) - timedelta(seconds=5),
            )
        )
        await session.commit()

    only = _credit("credit-lease", expires_at="2026-07-20T00:00:00Z")

    async def fake_fetch(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        return _upstream_response([only])

    consume_mock = AsyncMock(return_value=_success_consume("credit-lease"))
    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", fake_fetch)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", consume_mock)
    monkeypatch.setattr(reset_credits_api, "_build_refresh_usage_callback", lambda _context: _noop_refresh)

    await get_rate_limit_reset_credits_store().set(account_id, _snapshot([only]))

    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")

    assert response.status_code == 200, response.text
    consume_mock.assert_awaited_once()


# --- durable ledger primitives ---


@pytest.mark.asyncio
async def test_pin_redeem_request_first_writer_wins_and_purges_expired_rows(async_client) -> None:
    account_id = await _import_account(
        async_client,
        email="ledger@example.com",
        account_id="acc_ledger",
    )

    async with SessionLocal() as session:
        session.add(
            ResetCreditRedeemRequest(
                account_id=account_id,
                redeem_request_id="ancient-R",
                credit_id="ancient-credit",
                created_at=datetime.now(UTC) - timedelta(hours=25),
            )
        )
        await session.commit()

    assert await get_pinned_redeem_credit_id(account_id, "fresh-R") is None
    assert await pin_redeem_request(account_id, "fresh-R", "credit-a") == "credit-a"
    # First writer wins: a concurrent replica pinning a different credit gets
    # the original one back.
    assert await pin_redeem_request(account_id, "fresh-R", "credit-b") == "credit-a"
    assert await get_pinned_redeem_credit_id(account_id, "fresh-R") == "credit-a"
    # Rows past the 24h TTL were purged opportunistically by the write.
    assert await get_pinned_redeem_credit_id(account_id, "ancient-R") is None


@pytest.mark.asyncio
async def test_pin_redeem_request_reused_after_ttl_repins_new_credit(async_client) -> None:
    """Reusing a redeem_request_id after its prior row aged past the TTL must
    persist the NEW pin, not silently drop it via ON CONFLICT DO NOTHING on the
    stale (soon-purged) row."""
    account_id = await _import_account(
        async_client,
        email="ledger-ttl-reuse@example.com",
        account_id="acc_ledger_ttl_reuse",
    )

    async with SessionLocal() as session:
        session.add(
            ResetCreditRedeemRequest(
                account_id=account_id,
                redeem_request_id="reused-R",
                credit_id="old-credit",
                created_at=datetime.now(UTC) - timedelta(hours=25),
            )
        )
        await session.commit()

    # The same redeem_request_id is reused after its old row is past the 24h
    # TTL; the new attempt selected a different credit.
    assert await pin_redeem_request(account_id, "reused-R", "new-credit") == "new-credit"
    # The new pin is durable, so a cross-replica retry retargets it.
    assert await get_pinned_redeem_credit_id(account_id, "reused-R") == "new-credit"

    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(ResetCreditRedeemRequest).where(ResetCreditRedeemRequest.account_id == account_id)
                )
            )
            .scalars()
            .all()
        )
    assert [(row.redeem_request_id, row.credit_id) for row in rows] == [("reused-R", "new-credit")]


@pytest.mark.asyncio
async def test_get_pinned_redeem_credit_id_ignores_expired_rows(async_client) -> None:
    """An expired pin (older than the 24h TTL) must read as absent so the
    caller re-selects and re-pins a fresh credit instead of retargeting the
    stale credit id. The read TTL matches ``pin_redeem_request``'s purge TTL,
    so the row reads as absent even before any purge write runs."""
    account_id = await _import_account(
        async_client,
        email="ledger-expired-read@example.com",
        account_id="acc_ledger_expired_read",
    )

    async with SessionLocal() as session:
        session.add(
            ResetCreditRedeemRequest(
                account_id=account_id,
                redeem_request_id="expired-R",
                credit_id="stale-credit",
                created_at=datetime.now(UTC) - timedelta(hours=25),
            )
        )
        session.add(
            ResetCreditRedeemRequest(
                account_id=account_id,
                redeem_request_id="fresh-R",
                credit_id="fresh-credit",
                created_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        await session.commit()

    # Expired row is filtered on read (no purge write has run), while a
    # within-TTL row is still returned.
    assert await get_pinned_redeem_credit_id(account_id, "expired-R") is None
    assert await get_pinned_redeem_credit_id(account_id, "fresh-R") == "fresh-credit"


# --- v1 fresh-replica fallback (false 409 without it) ---


@pytest.mark.asyncio
async def test_v1_redeem_on_fresh_replica_falls_back_to_upstream_and_succeeds(async_client, monkeypatch) -> None:
    """A freshly started replica with an empty snapshot store must not 409 a
    redeem_id that upstream reports as available."""
    account_id = await _import_account(
        async_client,
        email="v1-fresh@example.com",
        account_id="acc_v1_fresh",
    )
    key = await _create_api_key(async_client, name="reset-credit-fresh-replica")

    fetch_mock = AsyncMock(
        return_value=_upstream_response([_credit("credit-fresh", expires_at="2026-08-01T00:00:00Z")])
    )
    consume_mock = AsyncMock(return_value=_success_consume("credit-fresh"))
    monkeypatch.setattr("app.modules.proxy.api.fetch_reset_credits", fetch_mock)
    monkeypatch.setattr("app.modules.proxy.api.consume_reset_credit", consume_mock)

    # Fresh replica: process-local store is empty for this account.
    assert get_rate_limit_reset_credits_store().get(account_id) is None

    response = await async_client.post(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
        json={"account_id": account_id, "redeem_id": "credit-fresh"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["code"] == "reset"
    fetch_mock.assert_awaited_once()
    consume_mock.assert_awaited_once()
    assert consume_mock.await_args is not None
    assert consume_mock.await_args.args[2] == "credit-fresh"


@pytest.mark.asyncio
async def test_v1_redeem_on_fresh_replica_returns_409_and_caches_fresh_snapshot(async_client, monkeypatch) -> None:
    account_id = await _import_account(
        async_client,
        email="v1-fresh-conflict@example.com",
        account_id="acc_v1_fresh_conflict",
    )
    key = await _create_api_key(async_client, name="reset-credit-fresh-conflict")

    fetch_mock = AsyncMock(
        return_value=_upstream_response(
            [_credit("credit-gone", status="redeemed", expires_at="2026-08-01T00:00:00Z")],
            available_count=0,
        )
    )
    consume_mock = AsyncMock()
    monkeypatch.setattr("app.modules.proxy.api.fetch_reset_credits", fetch_mock)
    monkeypatch.setattr("app.modules.proxy.api.consume_reset_credit", consume_mock)

    response = await async_client.post(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
        json={"account_id": account_id, "redeem_id": "credit-gone"},
    )

    assert response.status_code == 409
    consume_mock.assert_not_awaited()
    snapshot = get_rate_limit_reset_credits_store().get(account_id)
    assert snapshot is not None
    assert snapshot.available_count == 0


@pytest.mark.asyncio
async def test_v1_redeem_revalidates_stale_cached_credit_after_claim(async_client, monkeypatch) -> None:
    """A cached snapshot listing the credit as available must NOT short-circuit
    the upstream re-validation: after winning the cross-replica claim a peer may
    already have redeemed it, so the endpoint must 409 without a second consume."""
    account_id = await _import_account(
        async_client,
        email="v1-stale-cache@example.com",
        account_id="acc_v1_stale_cache",
    )
    key = await _create_api_key(async_client, name="reset-credit-stale-cache")

    # Replica-local snapshot still lists credit-x as available (a peer redeemed
    # it while this replica's invalidation poll had not yet fired).
    await get_rate_limit_reset_credits_store().set(
        account_id, _snapshot([_credit("credit-x", expires_at="2026-08-01T00:00:00Z")])
    )

    # Authoritative upstream fetch reports the credit already redeemed.
    fetch_mock = AsyncMock(
        return_value=_upstream_response(
            [_credit("credit-x", status="redeemed", expires_at="2026-08-01T00:00:00Z")],
            available_count=0,
        )
    )
    consume_mock = AsyncMock()
    monkeypatch.setattr("app.modules.proxy.api.fetch_reset_credits", fetch_mock)
    monkeypatch.setattr("app.modules.proxy.api.consume_reset_credit", consume_mock)

    response = await async_client.post(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
        json={"account_id": account_id, "redeem_id": "credit-x"},
    )

    assert response.status_code == 409, response.text
    # Re-validation happened and no second upstream consume was sent.
    fetch_mock.assert_awaited_once()
    consume_mock.assert_not_awaited()
    # The stale snapshot was replaced with the fresh (empty) upstream state.
    snapshot = get_rate_limit_reset_credits_store().get(account_id)
    assert snapshot is not None
    assert snapshot.available_count == 0


# --- cross-replica snapshot invalidation via the version-counter bus ---


@pytest.mark.asyncio
async def test_peer_replica_store_is_cleared_after_consume_via_invalidation_bus(async_client, monkeypatch) -> None:
    account_id = await _import_account(
        async_client,
        email="bus-invalidate@example.com",
        account_id="acc_bus_invalidate",
    )

    only = _credit("credit-bus", expires_at="2026-07-20T00:00:00Z")

    async def fake_fetch(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        return _upstream_response([only])

    consume_mock = AsyncMock(return_value=_success_consume("credit-bus"))
    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", fake_fetch)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", consume_mock)
    monkeypatch.setattr(reset_credits_api, "_build_refresh_usage_callback", lambda _context: _noop_refresh)

    # Peer replica B with its own poller and populated process-local store.
    replica_b_store = RateLimitResetCreditsStore()
    await replica_b_store.set(account_id, _snapshot([only]))
    poller_b = CacheInvalidationPoller(SessionLocal)
    poller_b.on_invalidation(NAMESPACE_RESET_CREDITS, replica_b_store.invalidate)
    await poller_b._poll_once()  # baseline versions

    await get_rate_limit_reset_credits_store().set(account_id, _snapshot([only]))
    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 200, response.text

    assert replica_b_store.get(account_id) is not None
    await poller_b._poll_once()
    # Replica B converges within one poll tick instead of waiting for its next
    # (up to 60s) refresh tick.
    assert replica_b_store.get(account_id) is None
