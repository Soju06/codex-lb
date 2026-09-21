from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.auth import generate_unique_account_id
from app.core.clients.rate_limit_reset_credits import (
    ConsumeResetCreditResponse,
    RateLimitResetCreditsSnapshot,
    ResetCreditItem,
    ResetCreditsResponse,
)
from app.db.session import SessionLocal
from app.modules.rate_limit_reset_credits import api as reset_credits_api
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


async def _import_test_account(async_client, *, email: str, account_id: str) -> str:
    payload = {
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


def _credit(credit_id: str, *, expires_at: str = "2026-07-12T00:00:00Z") -> ResetCreditItem:
    return ResetCreditItem.model_validate({"id": credit_id, "status": "available", "expires_at": expires_at})


def _upstream_response(credits: list[ResetCreditItem], available_count: int | None = None) -> ResetCreditsResponse:
    count = available_count if available_count is not None else len(credits)
    return ResetCreditsResponse(credits=credits, available_count=count)


def _snapshot(credits: list[ResetCreditItem], available_count: int | None = None) -> RateLimitResetCreditsSnapshot:
    available = available_count if available_count is not None else len(credits)
    expiries = [
        credit.expires_at for credit in credits if credit.status == "available" and credit.expires_at is not None
    ]
    return RateLimitResetCreditsSnapshot(
        available_count=available,
        nearest_expires_at=min(expiries) if expiries else None,
        credits=credits,
    )


@pytest.mark.asyncio
async def test_consume_paused_account_returns_409(async_client, monkeypatch) -> None:
    async def _should_not_fetch(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        raise AssertionError("paused account should not invoke upstream fetch")

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", _should_not_fetch)

    account_id = await _import_test_account(
        async_client,
        email="reset-paused@example.com",
        account_id="acc_reset_paused",
    )
    pause_resp = await async_client.post(f"/api/accounts/{account_id}/pause")
    assert pause_resp.status_code == 200

    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "account_not_reset_credit_applicable"


@pytest.mark.asyncio
async def test_consume_active_account_returns_success_with_mocked_upstream(async_client, monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_fetch(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        captured["fetch_account_id"] = account_id
        captured["fetch_had_token"] = bool(access_token)
        return _upstream_response([_credit("credit-1")])

    async def _fake_consume(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        redeem_request_id: str | None = None,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        captured.update(
            {
                "consume_account_id": account_id,
                "consume_credit_id": credit_id,
                "redeem_request_id": redeem_request_id,
                "consume_had_token": bool(access_token),
            }
        )
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {
                    "id": credit_id,
                    "status": "redeemed",
                    "redeemed_at": "2026-06-13T13:12:31Z",
                },
                "windows_reset": 2,
            }
        )

    async def _noop_refresh(account) -> None:  # noqa: ANN001
        return None

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", _fake_fetch)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", _fake_consume)
    monkeypatch.setattr(reset_credits_api, "_build_refresh_usage_callback", lambda _context: _noop_refresh)

    account_id = await _import_test_account(
        async_client,
        email="reset-active@example.com",
        account_id="acc_reset_active",
    )

    await get_rate_limit_reset_credits_store().set(account_id, _snapshot([_credit("credit-1")]))

    response = await async_client.post(
        f"/api/accounts/{account_id}/rate-limit-reset-credits/consume",
        json={"redeemRequestId": " dashboard-retry-id "},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == "reset"
    assert body["windowsReset"] == 2
    assert body["redeemedAt"] is not None
    assert datetime.fromisoformat(body["redeemedAt"].replace("Z", "+00:00")).year == 2026

    assert captured["fetch_account_id"] == "acc_reset_active"
    assert captured["fetch_had_token"] is True
    assert captured["consume_account_id"] == "acc_reset_active"
    assert captured["consume_credit_id"] == "credit-1"
    assert captured["redeem_request_id"] == "dashboard-retry-id"
    assert captured["consume_had_token"] is True


@pytest.mark.asyncio
async def test_consume_without_cached_snapshot_returns_409_without_fetch(async_client, monkeypatch) -> None:
    async def _should_not_fetch(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        raise AssertionError("uncached consume should not invoke upstream fetch")

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", _should_not_fetch)

    account_id = await _import_test_account(
        async_client,
        email="reset-no-cache@example.com",
        account_id="acc_reset_no_cache",
    )

    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_available_reset_credit"


@pytest.mark.asyncio
async def test_consume_reauth_required_account_returns_409(async_client, monkeypatch) -> None:
    async def _should_not_fetch(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        raise AssertionError("reauth account should not invoke upstream fetch")

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", _should_not_fetch)

    account_id = await _import_test_account(
        async_client,
        email="reset-reauth@example.com",
        account_id="acc_reset_reauth",
    )

    async with SessionLocal() as session:
        from sqlalchemy import update

        from app.db.models import Account, AccountStatus

        await session.execute(
            update(Account).where(Account.id == account_id).values(status=AccountStatus.REAUTH_REQUIRED)
        )
        await session.commit()

    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "account_not_reset_credit_applicable"


@pytest.mark.asyncio
async def test_get_returns_null_on_cache_miss_without_upstream_fetch(async_client, monkeypatch) -> None:
    account_id = await _import_test_account(
        async_client,
        email="reset-get@example.com",
        account_id="acc_reset_get",
    )

    async def _should_not_fetch(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        raise AssertionError("cache-miss GET should not invoke upstream fetch")

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", _should_not_fetch)

    response = await async_client.get(f"/api/accounts/{account_id}/rate-limit-reset-credits")
    assert response.status_code == 200, response.text
    assert response.json() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh_fails", [False, True])
async def test_confirmed_reset_retry_returns_receipt_without_second_consume(
    async_client,
    monkeypatch,
    refresh_fails: bool,
) -> None:
    from app.modules.rate_limit_reset_credits import outcomes

    account_id = await _import_test_account(async_client, email="receipt@example.com", account_id="receipt")
    credit = _credit("original")
    await get_rate_limit_reset_credits_store().set(account_id, _snapshot([credit]))
    calls = []

    async def fetch(*args, **kwargs):
        return _upstream_response([credit] if not calls else [])

    async def consume(*args, **kwargs):
        calls.append((args[2], kwargs["redeem_request_id"]))
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "windows_reset": 1,
                "credit": {"id": "original", "status": "redeemed", "redeemed_at": "2026-09-21T00:00:00Z"},
            }
        )

    async def refresh(account):
        row = await outcomes.get_request(account.id, "receipt-test")
        assert row is not None
        assert row is not None and row.outcome == "confirmed_reset"
        if refresh_fails:
            raise RuntimeError("usage unavailable")

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", fetch)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", consume)
    monkeypatch.setattr(reset_credits_api, "_build_refresh_usage_callback", lambda _: refresh)
    for _ in range(2):
        response = await async_client.post(
            f"/api/accounts/{account_id}/rate-limit-reset-credits/consume",
            json={"redeemRequestId": "receipt-test"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["outcome"] == "confirmed_reset"
    assert calls == [("original", "receipt-test")]
    row = await outcomes.get_request(account_id, "receipt-test")
    assert row is not None
    assert row.usage_verified is not refresh_fails


@pytest.mark.asyncio
async def test_lost_consume_response_retries_pinned_credit_and_records_outcome(async_client, monkeypatch) -> None:
    from app.core.clients.rate_limit_reset_credits import ConsumeResetCreditError
    from app.modules.rate_limit_reset_credits import outcomes

    account_id = await _import_test_account(async_client, email="lost@example.com", account_id="lost")
    original, later = _credit("original"), _credit("later", expires_at="2026-10-12T00:00:00Z")
    await get_rate_limit_reset_credits_store().set(account_id, _snapshot([original, later]))
    calls = []

    async def fetch(*args, **kwargs):
        return _upstream_response([original, later] if not calls else [later])

    async def consume(*args, **kwargs):
        calls.append((args[2], kwargs["redeem_request_id"]))
        if len(calls) == 1:
            raise ConsumeResetCreditError(503, "lost_response", "lost response")
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "windows_reset": 1,
                "credit": {"id": "original", "status": "redeemed"},
            }
        )

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", fetch)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", consume)
    monkeypatch.setattr(reset_credits_api, "_build_refresh_usage_callback", lambda _: None)
    url = f"/api/accounts/{account_id}/rate-limit-reset-credits/consume"
    first = await async_client.post(url, json={"redeemRequestId": "lost-test"})
    assert first.status_code == 503, first.text
    row = await outcomes.get_request(account_id, "lost-test")
    assert row is not None
    assert row.outcome == "unknown"
    second = await async_client.post(url, json={"redeemRequestId": "lost-test"})
    assert second.status_code == 200, second.text
    assert calls == [("original", "lost-test"), ("original", "lost-test")]
    row = await outcomes.get_request(account_id, "lost-test")
    assert row is not None
    assert row.outcome == "confirmed_reset" and row.attempt_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, expected_outcome, remaining", [("available", "unknown", 1), ("redeemed", "no_reset", 0)]
)
async def test_http_200_no_reset_does_not_confirm_restored_quota(
    async_client, monkeypatch, status, expected_outcome, remaining
) -> None:
    from app.modules.rate_limit_reset_credits import outcomes

    account_id = await _import_test_account(async_client, email="noreset@example.com", account_id="noreset")
    credit = _credit("original")
    await get_rate_limit_reset_credits_store().set(account_id, _snapshot([credit]))

    async def fetch(*args, **kwargs):
        return _upstream_response([credit])

    async def consume(*args, **kwargs):
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "unrecognized",
                "windows_reset": 0,
                "credit": {"id": "original", "status": status},
            }
        )

    async def refresh(account):
        raise AssertionError("unconfirmed response must not be treated as restored quota")

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", fetch)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", consume)
    monkeypatch.setattr(reset_credits_api, "_build_refresh_usage_callback", lambda _: refresh)
    response = await async_client.post(
        f"/api/accounts/{account_id}/rate-limit-reset-credits/consume",
        json={"redeemRequestId": "no-reset"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == expected_outcome
    row = await outcomes.get_request(account_id, "no-reset")
    assert row is not None
    assert row.outcome == expected_outcome and row.windows_reset == 0
    snapshot = get_rate_limit_reset_credits_store().get(account_id)
    assert snapshot is not None and snapshot.available_count == remaining


@pytest.mark.asyncio
async def test_targeted_summary_reads_only_requested_account(async_client, monkeypatch) -> None:
    from app.modules.accounts.repository import AccountsRepository

    account_id = await _import_test_account(async_client, email="summary@example.com", account_id="summary")
    await get_rate_limit_reset_credits_store().set(account_id, _snapshot([_credit("original")]))

    async def no_full_list(*args, **kwargs):
        raise AssertionError("targeted summary must not enumerate all accounts")

    monkeypatch.setattr(AccountsRepository, "list_accounts", no_full_list)
    response = await async_client.get(f"/api/accounts/{account_id}/summary")
    assert response.status_code == 200, response.text
    assert response.json()["accountId"] == account_id
    assert response.json()["availableResetCredits"] == 1
    assert response.json()["resetCreditFetchedAt"] is not None
    assert (await async_client.get("/api/accounts/missing/summary")).status_code == 404
    assert (await async_client.get(f"/api/accounts/{account_id}/summary/", follow_redirects=True)).status_code == 200
    from sqlalchemy import update

    from app.db.models import Account

    async with SessionLocal() as session:
        await session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(
                delete_requested_at=datetime.now().replace(tzinfo=None),
            )
        )
        await session.commit()
    missing = await async_client.get(f"/api/accounts/{account_id}/summary")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "account_not_found"


@pytest.mark.asyncio
async def test_targeted_summary_keeps_dashboard_auth_on_both_paths(async_client) -> None:
    assert (
        await async_client.post(
            "/api/dashboard-auth/password/setup",
            json={"password": "test-password-summary-123"},
        )
    ).status_code == 200
    await async_client.post("/api/dashboard-auth/logout", json={})
    for path in ("/api/accounts/missing/summary", "/api/accounts/missing/summary/"):
        response = await async_client.get(path)
        assert response.status_code == 401
        assert "error" in response.json() and "code" in response.json()["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("target_available", [True, False, "rescheduled"])
async def test_automatic_retry_reuses_failed_pin_without_substituting_later_credit(
    async_client,
    monkeypatch,
    target_available: bool | str,
) -> None:
    from datetime import UTC, timedelta

    from sqlalchemy import update

    from app.core.crypto import TokenEncryptor
    from app.core.usage.reset_credits_refresh_scheduler import _auto_redeem_request_id, _auto_redeem_reset_credit
    from app.db.models import Account, DashboardSettings
    from app.modules.rate_limit_reset_credits import outcomes
    from app.modules.rate_limit_reset_credits.redeem_coordination import pin_redeem_request

    account_id = await _import_test_account(async_client, email="auto-retry@example.com", account_id="auto-retry")
    expiry = datetime.now(UTC) + timedelta(minutes=20)
    original = _credit("original", expires_at=expiry.isoformat())
    later = _credit("later", expires_at=(expiry + timedelta(days=7)).isoformat())
    snapshot = _snapshot([original, later])
    store = get_rate_limit_reset_credits_store()
    await store.set(account_id, snapshot)
    async with SessionLocal() as session:
        await session.execute(update(DashboardSettings).values(auto_redeem_reset_credits_before_expiry=True))
        await session.commit()
        account = await session.get(Account, account_id)
        assert account is not None
    # A historical day-scoped id pinned before a failed POST must be reused.
    await pin_redeem_request(account_id, "legacy-day-id", "original")
    if target_available == "rescheduled":
        await outcomes.begin_attempt(
            account_id,
            "legacy-day-id",
            expires_at=expiry - timedelta(minutes=1),
            automatic=True,
        )
    calls = []

    async def fetch(*args, **kwargs):
        return _upstream_response([original, later] if target_available else [later])

    async def consume(*args, **kwargs):
        calls.append((args[2], kwargs["redeem_request_id"]))
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "windows_reset": 1,
                "credit": {"id": "original", "status": "redeemed"},
            }
        )

    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", consume)
    import app.core.usage.reset_credits_refresh_scheduler as scheduler

    monkeypatch.setattr(scheduler, "_refresh_usage_after_auto_redeem", lambda account: _noop_refresh_for_test())
    if target_available is True:
        assert await _auto_redeem_reset_credit(
            account,
            snapshot=snapshot,
            encryptor=TokenEncryptor(),
            store=store,
            fetch_fn=fetch,
            redeem_fn=None,
            resolve_route=None,
        )
        assert calls == [("original", "legacy-day-id")]
        row = await outcomes.get_request(account_id, "legacy-day-id")
        assert row is not None
        assert row.outcome == "confirmed_reset" and row.credit_expires_at is not None
    else:
        from app.core.exceptions import DashboardConflictError

        with pytest.raises(DashboardConflictError):
            await _auto_redeem_reset_credit(
                account,
                snapshot=snapshot,
                encryptor=TokenEncryptor(),
                store=store,
                fetch_fn=fetch,
                redeem_fn=None,
                resolve_route=None,
            )
        assert calls == []
    # Never create a parallel logical attempt under the new per-credit id.
    request_id = _auto_redeem_request_id(account, snapshot)
    assert request_id is not None
    assert await outcomes.get_request(account_id, request_id) is None


async def _noop_refresh_for_test() -> None:
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["disabled", "paused", "reauth", "deleted", "expiry_changed", "expired"])
async def test_auto_redeem_rechecks_changes_after_fetch(async_client, monkeypatch, transition) -> None:
    from datetime import UTC, timedelta

    from sqlalchemy import update

    from app.core.crypto import TokenEncryptor
    from app.core.exceptions import DashboardConflictError
    from app.core.usage.reset_credits_refresh_scheduler import _auto_redeem_reset_credit
    from app.db.models import Account, AccountStatus, DashboardSettings

    account_id = await _import_test_account(async_client, email="guard@example.com", account_id="guard")
    expiry = datetime.now(UTC) + timedelta(minutes=4)
    original = _credit("original", expires_at=expiry.isoformat())
    snapshot = _snapshot([original])
    async with SessionLocal() as session:
        await session.execute(update(DashboardSettings).values(auto_redeem_reset_credits_before_expiry=True))
        await session.commit()
        account = await session.get(Account, account_id)
        assert account is not None

    async def fetch(*args, **kwargs):
        async with SessionLocal() as session:
            if transition == "disabled":
                await session.execute(update(DashboardSettings).values(auto_redeem_reset_credits_before_expiry=False))
            elif transition in {"paused", "reauth", "deleted"}:
                values = {
                    "paused": {"status": AccountStatus.PAUSED},
                    "reauth": {"status": AccountStatus.REAUTH_REQUIRED},
                    "deleted": {"delete_requested_at": datetime.now(UTC).replace(tzinfo=None)},
                }[transition]
                await session.execute(update(Account).where(Account.id == account_id).values(**values))
            await session.commit()
        fresh = original.model_copy()
        if transition == "expiry_changed":
            fresh.expires_at = expiry + timedelta(minutes=1)
        if transition == "expired":
            fresh.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        return _upstream_response([fresh])

    consume = AsyncMock()
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", consume)
    with pytest.raises(DashboardConflictError):
        await _auto_redeem_reset_credit(
            account,
            snapshot=snapshot,
            encryptor=TokenEncryptor(),
            store=get_rate_limit_reset_credits_store(),
            fetch_fn=fetch,
            redeem_fn=None,
            resolve_route=None,
        )
    consume.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduler_restart_recovers_exact_attempt_and_separate_usage_verification(async_client) -> None:
    from datetime import UTC, timedelta

    from app.core.usage.reset_credits_refresh_scheduler import RateLimitResetCreditsRefreshScheduler
    from app.modules.rate_limit_reset_credits import outcomes
    from app.modules.rate_limit_reset_credits.redeem_coordination import pin_redeem_request

    account_id = await _import_test_account(async_client, email="restart@example.com", account_id="restart")
    expiry = datetime.now(UTC) + timedelta(minutes=4)
    await pin_redeem_request(account_id, "original-request", "original")
    await outcomes.begin_attempt(account_id, "original-request", expires_at=expiry, automatic=True)
    await outcomes.finish_attempt(account_id, "original-request", "unknown")
    restarted = RateLimitResetCreditsRefreshScheduler(interval_seconds=60)
    await restarted._restore_deadlines()
    target = restarted._deadlines[account_id].snapshot.credits[0]
    assert target.expires_at is not None
    assert target.id == "original" and outcomes.utc(target.expires_at) == expiry
    assert restarted._verifications == {}

    await outcomes.finish_attempt(
        account_id,
        "original-request",
        "confirmed_reset",
        result=ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "windows_reset": 1,
                "credit": {"id": "original", "status": "redeemed"},
            }
        ),
    )
    restarted = RateLimitResetCreditsRefreshScheduler(interval_seconds=60)
    await restarted._restore_deadlines()
    assert restarted._deadlines == {}
    assert restarted._verifications[account_id].verify_request_id == "original-request"


@pytest.mark.asyncio
async def test_three_replicas_auto_redeem_same_credit_once(async_client, monkeypatch) -> None:
    import asyncio
    from datetime import UTC, timedelta

    from sqlalchemy import update

    from app.core.crypto import TokenEncryptor
    from app.core.usage.reset_credits_refresh_scheduler import _auto_redeem_reset_credit
    from app.db.models import Account, DashboardSettings
    from app.modules.rate_limit_reset_credits.store import RateLimitResetCreditsStore

    account_id = await _import_test_account(async_client, email="three@example.com", account_id="three")
    original = _credit("original", expires_at=(datetime.now(UTC) + timedelta(minutes=4)).isoformat())
    later = _credit("later", expires_at=(datetime.now(UTC) + timedelta(days=7)).isoformat())
    snapshot = _snapshot([original, later])
    async with SessionLocal() as session:
        await session.execute(update(DashboardSettings).values(auto_redeem_reset_credits_before_expiry=True))
        await session.commit()
        account = await session.get(Account, account_id)
        assert account is not None
    calls = []

    async def fetch(*args, **kwargs):
        return _upstream_response([original, later])

    async def consume(*args, **kwargs):
        await asyncio.sleep(0.05)
        calls.append((args[2], kwargs["redeem_request_id"]))
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "windows_reset": 1,
                "credit": {"id": "original", "status": "redeemed"},
            }
        )

    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", consume)
    results = await asyncio.gather(
        *[
            _auto_redeem_reset_credit(
                account,
                snapshot=snapshot,
                encryptor=TokenEncryptor(),
                store=RateLimitResetCreditsStore(),
                fetch_fn=fetch,
                redeem_fn=None,
                resolve_route=None,
            )
            for _ in range(3)
        ]
    )
    assert results == [True, True, True]
    assert len(calls) == 1 and calls[0][0] == "original"
