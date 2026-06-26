from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update

from app.core.auth import generate_unique_account_id
from app.core.auth.refresh import RefreshError
from app.core.clients.rate_limit_reset_credits import (
    ConsumeResetCreditError,
    ConsumeResetCreditResponse,
    RateLimitResetCreditsSnapshot,
    ResetCreditItem,
)
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.upstream_proxy import UpstreamProxyRouteError
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
async def _clear_reset_credit_store():
    await get_rate_limit_reset_credits_store().invalidate()
    yield
    await get_rate_limit_reset_credits_store().invalidate()


def _encode_jwt(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return generate_unique_account_id(account_id, email)


async def _enable_api_key_auth(async_client) -> None:
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert response.status_code == 200


async def _create_api_key(async_client, *, name: str) -> tuple[str, str]:
    response = await async_client.post("/api/api-keys/", json={"name": name})
    assert response.status_code == 200
    payload = response.json()
    return payload["id"], payload["key"]


async def _seed_snapshot(
    account_id: str,
    *,
    available_count: int,
    credits: list[ResetCreditItem],
) -> None:
    await get_rate_limit_reset_credits_store().set(
        account_id,
        RateLimitResetCreditsSnapshot(
            available_count=available_count,
            nearest_expires_at=min(
                (
                    credit.expires_at
                    for credit in credits
                    if credit.status == "available" and credit.expires_at is not None
                ),
                default=None,
            ),
            credits=credits,
        ),
    )


@pytest.mark.asyncio
async def test_v1_reset_credit_requires_valid_bearer_key(async_client):
    await _enable_api_key_auth(async_client)

    missing = await async_client.get("/v1/reset-credit")
    invalid = await async_client.get(
        "/v1/reset-credit",
        headers={"Authorization": "Bearer invalid-key"},
    )

    for response in (missing, invalid):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_v1_reset_credit_accepts_valid_bearer_key_when_proxy_auth_disabled(async_client):
    account_id = await _import_account(
        async_client,
        "acc-reset-self-service",
        "self-service@example.com",
    )
    _, key = await _create_api_key(async_client, name="reset-credit-self-service")
    await _seed_snapshot(
        account_id,
        available_count=1,
        credits=[
            ResetCreditItem(
                id="credit-self-service",
                status="available",
                expires_at=datetime(2031, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            )
        ],
    )

    response = await async_client.get(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "account_id": account_id,
            "email": "self-service@example.com",
            "redeem_id": "credit-self-service",
            "expiredAt": "2031-01-02T03:04:05Z",
        }
    ]


@pytest.mark.asyncio
async def test_v1_reset_credit_scoped_pool_returns_all_available_credits_for_assigned_account(async_client):
    await _enable_api_key_auth(async_client)
    assigned_email = "real-assigned@example.com"
    other_email = "other@example.com"
    assigned_account_id = await _import_account(async_client, "acc-reset-assigned", assigned_email)
    other_account_id = await _import_account(async_client, "acc-reset-other", other_email)

    key_id, key = await _create_api_key(async_client, name="reset-credit-scoped")
    assign = await async_client.patch(
        f"/api/api-keys/{key_id}",
        json={"assignedAccountIds": [assigned_account_id]},
    )
    assert assign.status_code == 200

    soonest = datetime(2031, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    later = soonest + timedelta(hours=2)
    await _seed_snapshot(
        assigned_account_id,
        available_count=2,
        credits=[
            ResetCreditItem(id="credit-later", status="available", expires_at=later),
            ResetCreditItem(id="credit-soonest", status="available", expires_at=soonest),
            ResetCreditItem(id="credit-redeemed", status="redeemed", expires_at=soonest - timedelta(hours=1)),
        ],
    )
    await _seed_snapshot(
        other_account_id,
        available_count=1,
        credits=[ResetCreditItem(id="credit-other", status="available", expires_at=soonest + timedelta(days=1))],
    )

    response = await async_client.get(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "account_id": assigned_account_id,
            "email": assigned_email,
            "redeem_id": "credit-soonest",
            "expiredAt": "2031-01-02T03:04:05Z",
        },
        {
            "account_id": assigned_account_id,
            "email": assigned_email,
            "redeem_id": "credit-later",
            "expiredAt": "2031-01-02T05:04:05Z",
        },
    ]


@pytest.mark.asyncio
async def test_v1_reset_credit_null_expiry_available_credit_is_returned(async_client):
    await _enable_api_key_auth(async_client)
    email = "null-expiry@example.com"
    account_id = await _import_account(async_client, "acc-reset-null-expiry", email)

    _, key = await _create_api_key(async_client, name="reset-credit-null-expiry")
    await _seed_snapshot(
        account_id,
        available_count=1,
        credits=[ResetCreditItem(id="credit-null-expiry", status="available", expires_at=None)],
    )

    response = await async_client.get(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "account_id": account_id,
            "email": email,
            "redeem_id": "credit-null-expiry",
            "expiredAt": None,
        }
    ]


@pytest.mark.asyncio
async def test_v1_reset_credit_mixed_null_expiry_orders_dated_credit_before_null_expiry(async_client):
    await _enable_api_key_auth(async_client)
    email = "mixed-null-expiry@example.com"
    account_id = await _import_account(async_client, "acc-reset-mixed-null-expiry", email)

    _, key = await _create_api_key(async_client, name="reset-credit-mixed-null-expiry")
    expires_at = datetime(2031, 2, 1, 1, 2, 3, tzinfo=timezone.utc)
    await _seed_snapshot(
        account_id,
        available_count=2,
        credits=[
            ResetCreditItem(id="credit-null-expiry", status="available", expires_at=None),
            ResetCreditItem(id="credit-dated", status="available", expires_at=expires_at),
        ],
    )

    response = await async_client.get(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "account_id": account_id,
            "email": email,
            "redeem_id": "credit-dated",
            "expiredAt": "2031-02-01T01:02:03Z",
        },
        {
            "account_id": account_id,
            "email": email,
            "redeem_id": "credit-null-expiry",
            "expiredAt": None,
        },
    ]


@pytest.mark.asyncio
async def test_v1_reset_credit_selectable_accounts_excludes_paused_accounts(async_client):
    await _enable_api_key_auth(async_client)
    active_email = "active@example.com"
    paused_email = "paused@example.com"
    active_account_id = await _import_account(async_client, "acc-reset-active", active_email)
    paused_account_id = await _import_account(async_client, "acc-reset-paused", paused_email)

    pause = await async_client.post(
        f"/api/accounts/{paused_account_id}/pause",
        json={"reason": "test pause"},
    )
    assert pause.status_code == 200

    _, key = await _create_api_key(async_client, name="reset-credit-unscoped")
    expires_at = datetime(2031, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    await _seed_snapshot(
        active_account_id,
        available_count=1,
        credits=[ResetCreditItem(id="credit-active", status="available", expires_at=expires_at)],
    )
    await _seed_snapshot(
        paused_account_id,
        available_count=1,
        credits=[ResetCreditItem(id="credit-paused", status="available", expires_at=expires_at - timedelta(hours=1))],
    )

    response = await async_client.get(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "account_id": active_account_id,
            "email": active_email,
            "redeem_id": "credit-active",
            "expiredAt": "2031-02-03T04:05:06Z",
        }
    ]


@pytest.mark.asyncio
async def test_v1_reset_credit_excludes_accounts_without_chatgpt_account_id(async_client):
    await _enable_api_key_auth(async_client)
    active_account_id = await _import_account(async_client, "acc-reset-chatgpt-present", "present@example.com")
    missing_id_account_id = await _import_account(async_client, "acc-reset-chatgpt-missing", "missing-id@example.com")

    async with SessionLocal() as session:
        await session.execute(
            update(Account).where(Account.id == missing_id_account_id).values(chatgpt_account_id=None)
        )
        await session.commit()

    _, key = await _create_api_key(async_client, name="reset-credit-chatgpt-filter")
    expires_at = datetime(2031, 2, 4, 4, 5, 6, tzinfo=timezone.utc)
    await _seed_snapshot(
        active_account_id,
        available_count=1,
        credits=[ResetCreditItem(id="credit-active", status="available", expires_at=expires_at)],
    )
    await _seed_snapshot(
        missing_id_account_id,
        available_count=1,
        credits=[ResetCreditItem(id="credit-missing-id", status="available", expires_at=expires_at)],
    )

    response = await async_client.get(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "account_id": active_account_id,
            "email": "present@example.com",
            "redeem_id": "credit-active",
            "expiredAt": "2031-02-04T04:05:06Z",
        }
    ]


@pytest.mark.asyncio
async def test_v1_reset_credit_duplicate_email_accounts_return_separate_entries(async_client):
    await _enable_api_key_auth(async_client)
    shared_email = "duplicate@example.com"
    first_account_id = await _import_account(async_client, "acc-reset-duplicate-1", shared_email)
    second_account_id = await _import_account(async_client, "acc-reset-duplicate-2", shared_email)

    _, key = await _create_api_key(async_client, name="reset-credit-duplicate-email")
    first_expires_at = datetime(2031, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
    second_expires_at = first_expires_at + timedelta(hours=1)
    await _seed_snapshot(
        first_account_id,
        available_count=1,
        credits=[ResetCreditItem(id="credit-duplicate-1", status="available", expires_at=first_expires_at)],
    )
    await _seed_snapshot(
        second_account_id,
        available_count=1,
        credits=[ResetCreditItem(id="credit-duplicate-2", status="available", expires_at=second_expires_at)],
    )

    response = await async_client.get(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "account_id": first_account_id,
            "email": shared_email,
            "redeem_id": "credit-duplicate-1",
            "expiredAt": "2031-03-04T05:06:07Z",
        },
        {
            "account_id": second_account_id,
            "email": shared_email,
            "redeem_id": "credit-duplicate-2",
            "expiredAt": "2031-03-04T06:06:07Z",
        },
    ]


@pytest.mark.asyncio
async def test_v1_reset_credit_post_outside_api_key_scope_returns_403(async_client, monkeypatch: pytest.MonkeyPatch):
    await _enable_api_key_auth(async_client)
    allowed_account_id = await _import_account(async_client, "acc-reset-post-allowed", "allowed@example.com")
    blocked_account_id = await _import_account(async_client, "acc-reset-post-blocked", "blocked@example.com")

    key_id, key = await _create_api_key(async_client, name="reset-credit-post-scope")
    assign = await async_client.patch(
        f"/api/api-keys/{key_id}",
        json={"assignedAccountIds": [allowed_account_id]},
    )
    assert assign.status_code == 200

    consume_mock = AsyncMock()
    monkeypatch.setattr("app.modules.proxy.api.consume_reset_credit", consume_mock)

    response = await async_client.post(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
        json={"account_id": blocked_account_id, "redeem_id": "credit-blocked"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["type"] == "permission_error"
    consume_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_v1_reset_credit_post_rejects_account_without_chatgpt_account_id(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
):
    await _enable_api_key_auth(async_client)
    account_id = await _import_account(async_client, "acc-reset-post-missing-chatgpt", "post-missing@example.com")

    async with SessionLocal() as session:
        await session.execute(update(Account).where(Account.id == account_id).values(chatgpt_account_id=None))
        await session.commit()

    _, key = await _create_api_key(async_client, name="reset-credit-post-missing-chatgpt")
    consume_mock = AsyncMock()
    monkeypatch.setattr("app.modules.proxy.api.consume_reset_credit", consume_mock)

    response = await async_client.post(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
        json={"account_id": account_id, "redeem_id": "credit-missing-chatgpt"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["type"] == "permission_error"
    consume_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_v1_reset_credit_post_unavailable_redeem_id_returns_409(async_client, monkeypatch: pytest.MonkeyPatch):
    await _enable_api_key_auth(async_client)
    account_id = await _import_account(async_client, "acc-reset-post-missing", "missing@example.com")

    _, key = await _create_api_key(async_client, name="reset-credit-post-missing")
    await _seed_snapshot(
        account_id,
        available_count=1,
        credits=[
            ResetCreditItem(
                id="credit-available",
                status="available",
                expires_at=datetime(2031, 4, 1, tzinfo=timezone.utc),
            )
        ],
    )

    consume_mock = AsyncMock()
    resolve_route_mock = AsyncMock(side_effect=AssertionError("route resolution should not run"))
    monkeypatch.setattr("app.modules.proxy.api.consume_reset_credit", consume_mock)
    monkeypatch.setattr("app.modules.proxy.api._resolve_reset_credit_route", resolve_route_mock)

    response = await async_client.post(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
        json={"account_id": account_id, "redeem_id": "credit-missing"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_request_error"
    consume_mock.assert_not_awaited()
    resolve_route_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_v1_reset_credit_post_returns_503_when_route_resolution_fails_for_available_credit(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
):
    await _enable_api_key_auth(async_client)
    account_id = await _import_account(async_client, "acc-reset-post-route-fail", "route-fail@example.com")

    _, key = await _create_api_key(async_client, name="reset-credit-post-route-fail")
    await _seed_snapshot(
        account_id,
        available_count=1,
        credits=[
            ResetCreditItem(
                id="credit-route-fail",
                status="available",
                expires_at=datetime(2031, 4, 3, tzinfo=timezone.utc),
            )
        ],
    )

    consume_mock = AsyncMock()

    async def fake_resolve_route(*args, **kwargs):
        del args, kwargs
        raise UpstreamProxyRouteError("pool_unavailable", account_id=account_id)

    monkeypatch.setattr("app.modules.proxy.api.consume_reset_credit", consume_mock)
    monkeypatch.setattr("app.modules.proxy.api._resolve_reset_credit_route", fake_resolve_route)

    response = await async_client.post(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
        json={"account_id": account_id, "redeem_id": "credit-route-fail"},
    )

    assert response.status_code == 503
    assert response.json()["error"] == {
        "message": "Unable to resolve upstream proxy route",
        "type": "server_error",
        "code": "server_error",
    }
    consume_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_v1_reset_credit_post_upstream_conflict_invalidates_stale_snapshot(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
):
    await _enable_api_key_auth(async_client)
    account_id = await _import_account(async_client, "acc-reset-post-conflict", "conflict@example.com")

    _, key = await _create_api_key(async_client, name="reset-credit-post-conflict")
    await _seed_snapshot(
        account_id,
        available_count=1,
        credits=[
            ResetCreditItem(
                id="credit-conflict",
                status="available",
                expires_at=datetime(2031, 4, 2, tzinfo=timezone.utc),
            )
        ],
    )

    async def fake_consume(*args, **kwargs):
        del args, kwargs
        raise ConsumeResetCreditError(409, "credit already redeemed upstream", code="credit_unavailable")

    monkeypatch.setattr("app.modules.proxy.api.consume_reset_credit", fake_consume)

    response = await async_client.post(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
        json={"account_id": account_id, "redeem_id": "credit-conflict"},
    )

    assert response.status_code == 409
    assert response.json()["error"] == {
        "message": "credit already redeemed upstream",
        "type": "invalid_request_error",
        "code": "invalid_request_error",
    }
    assert get_rate_limit_reset_credits_store().get(account_id) is None


@pytest.mark.asyncio
async def test_v1_reset_credit_post_consumes_exact_credit_and_invalidates_snapshot(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
):
    await _enable_api_key_auth(async_client)
    email = "exact-credit@example.com"
    account_id = await _import_account(async_client, "acc-reset-post-exact", email)

    _, key = await _create_api_key(async_client, name="reset-credit-post-exact")
    soonest = datetime(2031, 5, 1, 1, 0, 0, tzinfo=timezone.utc)
    later = soonest + timedelta(hours=2)
    await _seed_snapshot(
        account_id,
        available_count=2,
        credits=[
            ResetCreditItem(id="credit-soonest", status="available", expires_at=soonest),
            ResetCreditItem(id="credit-later", status="available", expires_at=later),
        ],
    )

    consume_mock = AsyncMock(
        return_value=ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {"id": "credit-later", "status": "redeemed", "redeemed_at": "2031-05-01T03:30:00Z"},
                "windows_reset": 1,
            }
        )
    )
    monkeypatch.setattr("app.modules.proxy.api.consume_reset_credit", consume_mock)

    response = await async_client.post(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
        json={"account_id": account_id, "redeem_id": "credit-later"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": "reset",
        "windows_reset": 1,
        "redeemed_at": "2031-05-01T03:30:00Z",
    }
    consume_mock.assert_awaited_once()
    consume_args = consume_mock.await_args
    assert consume_args is not None
    assert consume_args.args[2] == "credit-later"
    assert get_rate_limit_reset_credits_store().get(account_id) is None


@pytest.mark.asyncio
async def test_v1_reset_credit_post_force_refreshes_usage_and_invalidates_selection_cache(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
):
    await _enable_api_key_auth(async_client)
    account_id = await _import_account(async_client, "acc-reset-post-refresh", "refresh@example.com")

    _, key = await _create_api_key(async_client, name="reset-credit-post-refresh")
    await _seed_snapshot(
        account_id,
        available_count=1,
        credits=[
            ResetCreditItem(
                id="credit-refresh",
                status="available",
                expires_at=datetime(2031, 5, 2, 1, 0, 0, tzinfo=timezone.utc),
            )
        ],
    )

    consume_mock = AsyncMock(
        return_value=ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {"id": "credit-refresh", "status": "redeemed", "redeemed_at": "2031-05-02T01:30:00Z"},
                "windows_reset": 1,
            }
        )
    )
    force_refresh_calls: list[tuple[str, str]] = []

    class StubUsageUpdater:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        async def force_refresh(self, account) -> bool:
            force_refresh_calls.append((account.id, account.status.value))
            return True

    class SelectionCache:
        def __init__(self) -> None:
            self.invalidations = 0

        def invalidate(self) -> None:
            self.invalidations += 1

    selection_cache = SelectionCache()

    monkeypatch.setattr("app.modules.proxy.api.consume_reset_credit", consume_mock)
    monkeypatch.setattr("app.modules.proxy.api.UsageUpdater", StubUsageUpdater)
    monkeypatch.setattr("app.modules.proxy.api.get_account_selection_cache", lambda: selection_cache)

    response = await async_client.post(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
        json={"account_id": account_id, "redeem_id": "credit-refresh"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": "reset",
        "windows_reset": 1,
        "redeemed_at": "2031-05-02T01:30:00Z",
    }
    assert force_refresh_calls == [(account_id, "active")]
    assert selection_cache.invalidations == 1
    assert get_rate_limit_reset_credits_store().get(account_id) is None


@pytest.mark.asyncio
async def test_v1_reset_credit_post_refreshes_account_before_consuming_credit(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
):
    await _enable_api_key_auth(async_client)
    account_id = await _import_account(
        async_client,
        "acc-reset-post-refresh-token",
        "refresh-token@example.com",
    )

    _, key = await _create_api_key(async_client, name="reset-credit-post-refresh-token")
    await _seed_snapshot(
        account_id,
        available_count=1,
        credits=[
            ResetCreditItem(
                id="credit-refresh-token",
                status="available",
                expires_at=datetime(2031, 5, 2, tzinfo=timezone.utc),
            )
        ],
    )

    refreshed_account = SimpleNamespace(
        id=account_id,
        status=AccountStatus.ACTIVE,
        access_token_encrypted=TokenEncryptor().encrypt("fresh-access-token"),
        chatgpt_account_id="chatgpt-refresh-token",
    )
    events: list[str] = []

    async def fake_ensure_fresh(requested_account_id: str):
        events.append("refresh")
        assert requested_account_id == account_id
        return refreshed_account

    async def fake_consume(
        access_token: str,
        chatgpt_account_id: str,
        credit_id: str,
        *,
        route: object | None = None,
        allow_direct_egress: bool = False,
    ):
        events.append("consume")
        assert access_token == "fresh-access-token"
        assert chatgpt_account_id == "chatgpt-refresh-token"
        assert credit_id == "credit-refresh-token"
        assert route is None
        assert allow_direct_egress is True
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {
                    "id": credit_id,
                    "status": "redeemed",
                    "redeemed_at": "2031-05-02T00:30:00Z",
                },
                "windows_reset": 1,
            }
        )

    monkeypatch.setattr("app.modules.proxy.api._ensure_v1_reset_credit_account_fresh", fake_ensure_fresh)
    monkeypatch.setattr("app.modules.proxy.api.consume_reset_credit", fake_consume)

    response = await async_client.post(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
        json={"account_id": account_id, "redeem_id": "credit-refresh-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": "reset",
        "windows_reset": 1,
        "redeemed_at": "2031-05-02T00:30:00Z",
    }
    assert events == ["refresh", "consume"]
    assert get_rate_limit_reset_credits_store().get(account_id) is None


@pytest.mark.asyncio
async def test_v1_reset_credit_post_returns_conflict_when_account_refresh_fails(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
):
    await _enable_api_key_auth(async_client)
    account_id = await _import_account(
        async_client,
        "acc-reset-post-refresh-failure",
        "refresh-failure@example.com",
    )

    _, key = await _create_api_key(async_client, name="reset-credit-post-refresh-failure")
    await _seed_snapshot(
        account_id,
        available_count=1,
        credits=[
            ResetCreditItem(
                id="credit-refresh-failure",
                status="available",
                expires_at=datetime(2031, 5, 2, tzinfo=timezone.utc),
            )
        ],
    )

    class SelectionCache:
        def __init__(self) -> None:
            self.invalidations = 0

        def invalidate(self) -> None:
            self.invalidations += 1

    async def fake_ensure_fresh(requested_account_id: str):
        assert requested_account_id == account_id
        raise RefreshError("invalid_grant", "refresh token expired", True)

    selection_cache = SelectionCache()
    consume_mock = AsyncMock()

    monkeypatch.setattr("app.modules.proxy.api._ensure_v1_reset_credit_account_fresh", fake_ensure_fresh)
    monkeypatch.setattr("app.modules.proxy.api.consume_reset_credit", consume_mock)
    monkeypatch.setattr("app.modules.proxy.api.get_account_selection_cache", lambda: selection_cache)

    response = await async_client.post(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
        json={"account_id": account_id, "redeem_id": "credit-refresh-failure"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "message": "Reset credit redeem could not refresh account credentials: refresh token expired",
            "type": "invalid_request_error",
            "code": "invalid_request_error",
        }
    }
    assert selection_cache.invalidations == 1
    consume_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_v1_reset_credit_post_holds_session_open_through_lock_and_upstream_consume(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
):
    await _enable_api_key_auth(async_client)
    account_id = await _import_account(
        async_client,
        "acc-reset-post-session-lifecycle",
        "session-lifecycle@example.com",
    )

    _, key = await _create_api_key(async_client, name="reset-credit-post-session-lifecycle")
    await _seed_snapshot(
        account_id,
        available_count=1,
        credits=[
            ResetCreditItem(
                id="credit-session-lifecycle",
                status="available",
                expires_at=datetime(2031, 6, 1, tzinfo=timezone.utc),
            )
        ],
    )

    events: list[str] = []
    repo_session = object()
    account = SimpleNamespace(
        id=account_id,
        status=AccountStatus.ACTIVE,
        access_token_encrypted=TokenEncryptor().encrypt("access-token"),
        chatgpt_account_id="chatgpt-session-lifecycle",
    )

    class SessionManager:
        async def __aenter__(self):
            events.append("session_enter")
            return repo_session

        async def __aexit__(self, exc_type, exc, tb):
            events.append("session_exit")
            return False

    class StubAccountsRepository:
        def __init__(self, repo_session_arg):
            events.append("repo_init")
            assert repo_session_arg is repo_session

        async def get_by_id(self, requested_account_id: str):
            events.append("repo_get")
            assert requested_account_id == account_id
            return account

    @asynccontextmanager
    async def fake_serialize_reset_credit_redeem(requested_account_id: str, *, session: object | None):
        events.append("lock_wait")
        assert requested_account_id == account_id
        assert session is repo_session
        events.append("lock_enter")
        try:
            yield
        finally:
            events.append("lock_exit")

    async def fake_consume(
        access_token: str,
        chatgpt_account_id: str,
        credit_id: str,
        *,
        route: object | None = None,
        allow_direct_egress: bool = False,
    ):
        events.append("consume")
        assert access_token == "access-token"
        assert chatgpt_account_id == "chatgpt-session-lifecycle"
        assert credit_id == "credit-session-lifecycle"
        assert route is None
        assert allow_direct_egress is True
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {
                    "id": credit_id,
                    "status": "redeemed",
                    "redeemed_at": "2031-06-01T00:30:00Z",
                },
                "windows_reset": 1,
            }
        )

    async def fake_resolve_route(route_session, requested_account_id: str):
        events.append("route_resolve")
        assert route_session is repo_session
        assert requested_account_id == account_id
        return None

    async def fake_refresh_usage_after_redeem(refreshed_account_id: str) -> None:
        events.append("refresh_usage")
        assert refreshed_account_id == account_id

    monkeypatch.setattr("app.modules.proxy.api.get_background_session", lambda: SessionManager())
    monkeypatch.setattr("app.modules.proxy.api.AccountsRepository", StubAccountsRepository)
    monkeypatch.setattr("app.modules.proxy.api._resolve_reset_credit_route", fake_resolve_route)
    monkeypatch.setattr("app.modules.proxy.api.serialize_reset_credit_redeem", fake_serialize_reset_credit_redeem)
    monkeypatch.setattr(
        "app.modules.proxy.api._ensure_v1_reset_credit_account_fresh",
        AsyncMock(return_value=account),
    )
    monkeypatch.setattr("app.modules.proxy.api.consume_reset_credit", fake_consume)
    monkeypatch.setattr(
        "app.modules.proxy.api._refresh_usage_after_v1_reset_credit_redeem",
        fake_refresh_usage_after_redeem,
    )

    response = await async_client.post(
        "/v1/reset-credit",
        headers={"Authorization": f"Bearer {key}"},
        json={"account_id": account_id, "redeem_id": "credit-session-lifecycle"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": "reset",
        "windows_reset": 1,
        "redeemed_at": "2031-06-01T00:30:00Z",
    }
    assert events == [
        "session_enter",
        "repo_init",
        "repo_get",
        "lock_wait",
        "lock_enter",
        "route_resolve",
        "consume",
        "refresh_usage",
        "lock_exit",
        "session_exit",
    ]
    assert get_rate_limit_reset_credits_store().get(account_id) is None


@pytest.mark.asyncio
async def test_v1_reset_credit_post_preserves_success_when_post_redeem_usage_refresh_fails(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    await _enable_api_key_auth(async_client)
    account_id = await _import_account(
        async_client,
        "acc-reset-post-refresh-raise",
        "refresh-raise@example.com",
    )

    _, key = await _create_api_key(async_client, name="reset-credit-post-refresh-raise")
    await _seed_snapshot(
        account_id,
        available_count=1,
        credits=[
            ResetCreditItem(
                id="credit-refresh-raise",
                status="available",
                expires_at=datetime(2031, 6, 2, tzinfo=timezone.utc),
            )
        ],
    )

    account = SimpleNamespace(
        id=account_id,
        status=AccountStatus.ACTIVE,
        access_token_encrypted=TokenEncryptor().encrypt("access-token"),
        chatgpt_account_id="chatgpt-refresh-raise",
    )

    async def fake_consume(
        access_token: str,
        chatgpt_account_id: str,
        credit_id: str,
        *,
        route: object | None = None,
        allow_direct_egress: bool = False,
    ):
        assert access_token == "access-token"
        assert chatgpt_account_id == "chatgpt-refresh-raise"
        assert credit_id == "credit-refresh-raise"
        assert route is None
        assert allow_direct_egress is True
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {
                    "id": credit_id,
                    "status": "redeemed",
                    "redeemed_at": "2031-06-02T00:30:00Z",
                },
                "windows_reset": 1,
            }
        )

    async def fake_refresh_usage_after_redeem(refreshed_account_id: str) -> None:
        assert refreshed_account_id == account_id
        raise RuntimeError("usage refresh failed")

    monkeypatch.setattr(
        "app.modules.proxy.api._ensure_v1_reset_credit_account_fresh",
        AsyncMock(return_value=account),
    )
    monkeypatch.setattr("app.modules.proxy.api.consume_reset_credit", fake_consume)
    monkeypatch.setattr(
        "app.modules.proxy.api._refresh_usage_after_v1_reset_credit_redeem",
        fake_refresh_usage_after_redeem,
    )

    with caplog.at_level("WARNING", logger="app.modules.proxy.api"):
        response = await async_client.post(
            "/v1/reset-credit",
            headers={"Authorization": f"Bearer {key}"},
            json={"account_id": account_id, "redeem_id": "credit-refresh-raise"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "code": "reset",
        "windows_reset": 1,
        "redeemed_at": "2031-06-02T00:30:00Z",
    }
    assert "V1 reset credit consume succeeded but usage refresh failed" in caplog.text
    assert get_rate_limit_reset_credits_store().get(account_id) is None
