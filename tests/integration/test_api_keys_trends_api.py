from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository

pytestmark = pytest.mark.integration


async def _create_api_key(async_client, *, name: str) -> str:
    response = await async_client.post("/api/api-keys/", json={"name": name})
    assert response.status_code == 200
    return response.json()["id"]


async def _insert_request_logs(*rows: RequestLog) -> None:
    async with SessionLocal() as session:
        session.add_all(rows)
        await session.commit()


def _make_account(account_id: str, email: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


async def _insert_accounts(*rows: Account) -> None:
    async with SessionLocal() as session:
        session.add_all(rows)
        await session.commit()


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _hour_bucket(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.replace(minute=0, second=0, microsecond=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["trends", "usage-7d", "account-usage-7d"])
async def test_api_key_detail_endpoints_return_404_for_missing_key(async_client, endpoint: str):
    response = await async_client.get(f"/api/api-keys/missing-key/{endpoint}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_trends_returns_hourly_zero_filled_points_with_bucket_aggregation(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
):
    key_id = await _create_api_key(async_client, name="trend-key")
    now = datetime(2026, 3, 30, 12, 0, 0)
    monkeypatch.setattr("app.modules.api_keys.service.utcnow", lambda: now)

    aggregated_bucket = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=5)
    separate_bucket = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    same_bucket_a = aggregated_bucket + timedelta(minutes=10)
    same_bucket_b = aggregated_bucket + timedelta(minutes=45)
    other_bucket = separate_bucket + timedelta(minutes=15)
    old_row = now - timedelta(days=8)

    await _insert_request_logs(
        RequestLog(
            api_key_id=key_id,
            request_id="req-trend-a",
            requested_at=same_bucket_a,
            model="gpt-5.1",
            status="ok",
            input_tokens=30,
            output_tokens=12,
            cached_input_tokens=6,
            cost_usd=0.125,
        ),
        RequestLog(
            api_key_id=key_id,
            request_id="req-trend-b",
            requested_at=same_bucket_b,
            model="gpt-5.1",
            status="ok",
            input_tokens=7,
            output_tokens=3,
            cached_input_tokens=2,
            cost_usd=0.075,
        ),
        RequestLog(
            api_key_id=key_id,
            request_id="req-trend-c",
            requested_at=other_bucket,
            model="gpt-5.1",
            status="ok",
            input_tokens=10,
            output_tokens=5,
            cached_input_tokens=1,
            cost_usd=0.05,
        ),
        RequestLog(
            api_key_id=key_id,
            request_id="req-trend-d",
            requested_at=other_bucket + timedelta(minutes=10),
            model="gpt-5.1",
            status="ok",
            input_tokens=4,
            output_tokens=None,
            reasoning_tokens=6,
            cached_input_tokens=0,
            cost_usd=0.02,
        ),
        RequestLog(
            api_key_id=key_id,
            request_id="req-trend-old",
            requested_at=old_row,
            model="gpt-5.1",
            status="ok",
            input_tokens=999,
            output_tokens=999,
            cached_input_tokens=999,
            cost_usd=9.99,
        ),
    )

    response = await async_client.get(f"/api/api-keys/{key_id}/trends")
    assert response.status_code == 200

    payload = response.json()
    assert payload["keyId"] == key_id
    assert len(payload["cost"]) == 168
    assert len(payload["tokens"]) == 168

    cost_times = [_parse_utc(point["t"]) for point in payload["cost"]]
    token_times = [_parse_utc(point["t"]) for point in payload["tokens"]]
    assert cost_times == token_times
    assert cost_times == sorted(cost_times)
    assert cost_times[1] - cost_times[0] == timedelta(hours=1)

    cost_by_bucket = {_parse_utc(point["t"]): point["v"] for point in payload["cost"]}
    tokens_by_bucket = {_parse_utc(point["t"]): point["v"] for point in payload["tokens"]}

    assert tokens_by_bucket[_hour_bucket(same_bucket_a)] == pytest.approx(52.0)
    assert cost_by_bucket[_hour_bucket(same_bucket_a)] == pytest.approx(0.2)
    assert tokens_by_bucket[_hour_bucket(other_bucket)] == pytest.approx(25.0)
    assert cost_by_bucket[_hour_bucket(other_bucket)] == pytest.approx(0.07)

    assert sum(point["v"] for point in payload["tokens"]) == pytest.approx(77.0)
    assert sum(point["v"] for point in payload["cost"]) == pytest.approx(0.27)
    assert sum(1 for point in payload["tokens"] if point["v"] == 0) > 0


@pytest.mark.asyncio
async def test_trends_include_oldest_partial_hour_for_rolling_7d_window(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
):
    key_id = await _create_api_key(async_client, name="trend-partial-window-key")
    now = datetime(2026, 3, 30, 10, 37, 0)
    since = now - timedelta(days=7)
    monkeypatch.setattr("app.modules.api_keys.service.utcnow", lambda: now)

    oldest_included = since + timedelta(minutes=5)
    oldest_excluded = since - timedelta(minutes=1)
    newest_included = now.replace(minute=10, second=0, microsecond=0)
    newest_excluded = now

    await _insert_request_logs(
        RequestLog(
            api_key_id=key_id,
            request_id="req-trend-partial-old-excluded",
            requested_at=oldest_excluded,
            model="gpt-5.1",
            status="ok",
            input_tokens=50,
            output_tokens=5,
            cached_input_tokens=0,
            cost_usd=0.5,
        ),
        RequestLog(
            api_key_id=key_id,
            request_id="req-trend-partial-old-included",
            requested_at=oldest_included,
            model="gpt-5.1",
            status="ok",
            input_tokens=10,
            output_tokens=2,
            cached_input_tokens=0,
            cost_usd=0.1,
        ),
        RequestLog(
            api_key_id=key_id,
            request_id="req-trend-partial-new-included",
            requested_at=newest_included,
            model="gpt-5.1",
            status="ok",
            input_tokens=4,
            output_tokens=None,
            reasoning_tokens=3,
            cached_input_tokens=0,
            cost_usd=0.05,
        ),
        RequestLog(
            api_key_id=key_id,
            request_id="req-trend-partial-new-excluded",
            requested_at=newest_excluded,
            model="gpt-5.1",
            status="ok",
            input_tokens=100,
            output_tokens=20,
            cached_input_tokens=0,
            cost_usd=1.0,
        ),
    )

    response = await async_client.get(f"/api/api-keys/{key_id}/trends")
    assert response.status_code == 200

    payload = response.json()
    assert len(payload["cost"]) == 169
    assert len(payload["tokens"]) == 169

    cost_times = [_parse_utc(point["t"]) for point in payload["cost"]]
    cost_by_bucket = {_parse_utc(point["t"]): point["v"] for point in payload["cost"]}
    tokens_by_bucket = {_parse_utc(point["t"]): point["v"] for point in payload["tokens"]}

    assert cost_times[0] == _hour_bucket(since)
    assert cost_times[-1] == _hour_bucket(now)
    assert tokens_by_bucket[_hour_bucket(oldest_included)] == pytest.approx(12.0)
    assert cost_by_bucket[_hour_bucket(oldest_included)] == pytest.approx(0.1)
    assert tokens_by_bucket[_hour_bucket(newest_included)] == pytest.approx(7.0)
    assert cost_by_bucket[_hour_bucket(newest_included)] == pytest.approx(0.05)
    assert sum(point["v"] for point in payload["tokens"]) == pytest.approx(19.0)
    assert sum(point["v"] for point in payload["cost"]) == pytest.approx(0.15)


@pytest.mark.asyncio
async def test_trends_repository_bounds_results_to_the_requested_window(async_client):
    key_id = await _create_api_key(async_client, name="trend-window-key")
    window_start = datetime(2026, 3, 23, 10, 37, 0)
    window_end = datetime(2026, 3, 30, 10, 37, 0)
    included_oldest = window_start + timedelta(minutes=5)
    included_newest = window_end - timedelta(minutes=30)
    excluded_old = window_start - timedelta(seconds=1)
    excluded_future = window_end

    await _insert_request_logs(
        RequestLog(
            api_key_id=key_id,
            request_id="req-trend-bounded-old",
            requested_at=excluded_old,
            model="gpt-5.1",
            status="ok",
            input_tokens=99,
            output_tokens=1,
            cached_input_tokens=0,
            cost_usd=0.99,
        ),
        RequestLog(
            api_key_id=key_id,
            request_id="req-trend-bounded-a",
            requested_at=included_oldest,
            model="gpt-5.1",
            status="ok",
            input_tokens=10,
            output_tokens=2,
            cached_input_tokens=0,
            cost_usd=0.1,
        ),
        RequestLog(
            api_key_id=key_id,
            request_id="req-trend-bounded-b",
            requested_at=included_newest,
            model="gpt-5.1",
            status="ok",
            input_tokens=4,
            output_tokens=3,
            cached_input_tokens=0,
            cost_usd=0.05,
        ),
        RequestLog(
            api_key_id=key_id,
            request_id="req-trend-bounded-future",
            requested_at=excluded_future,
            model="gpt-5.1",
            status="ok",
            input_tokens=500,
            output_tokens=100,
            cached_input_tokens=0,
            cost_usd=5.0,
        ),
    )

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        buckets = await repo.trends_by_key(key_id, window_start, window_end, 3600)

    assert [bucket.bucket_epoch for bucket in buckets] == [
        int(_hour_bucket(included_oldest).timestamp()),
        int(_hour_bucket(included_newest).timestamp()),
    ]
    assert [bucket.total_tokens for bucket in buckets] == [12, 7]
    assert [bucket.total_cost_usd for bucket in buckets] == [pytest.approx(0.1), pytest.approx(0.05)]


@pytest.mark.asyncio
async def test_usage_7d_sums_only_recent_request_logs(async_client):
    key_id = await _create_api_key(async_client, name="usage-key")
    now = utcnow()

    await _insert_request_logs(
        RequestLog(
            api_key_id=key_id,
            request_id="req-usage-a",
            requested_at=now - timedelta(days=1, hours=2),
            model="gpt-5.1",
            status="ok",
            input_tokens=120,
            output_tokens=35,
            cached_input_tokens=20,
            cost_usd=0.42,
        ),
        RequestLog(
            api_key_id=key_id,
            request_id="req-usage-b",
            requested_at=now - timedelta(hours=8),
            model="gpt-5.1",
            status="error",
            input_tokens=8,
            output_tokens=2,
            cached_input_tokens=0,
            cost_usd=0.08,
        ),
        RequestLog(
            api_key_id=key_id,
            request_id="req-usage-c",
            requested_at=now - timedelta(hours=3),
            model="gpt-5.1",
            status="ok",
            input_tokens=2,
            output_tokens=None,
            reasoning_tokens=7,
            cached_input_tokens=1,
            cost_usd=0.03,
        ),
        RequestLog(
            api_key_id=key_id,
            request_id="req-usage-old",
            requested_at=now - timedelta(days=7, minutes=1),
            model="gpt-5.1",
            status="ok",
            input_tokens=500,
            output_tokens=100,
            cached_input_tokens=50,
            cost_usd=5.0,
        ),
    )

    response = await async_client.get(f"/api/api-keys/{key_id}/usage-7d")
    assert response.status_code == 200

    payload = response.json()
    assert payload == {
        "keyId": key_id,
        "totalTokens": 174,
        "totalCostUsd": 0.53,
        "totalRequests": 3,
        "cachedInputTokens": 21,
    }


@pytest.mark.asyncio
async def test_usage_7d_clamps_cached_input_tokens_to_total_input(async_client):
    key_id = await _create_api_key(async_client, name="usage-key-clamped-cache")
    now = utcnow()

    await _insert_request_logs(
        RequestLog(
            api_key_id=key_id,
            request_id="req-usage-clamp-a",
            requested_at=now - timedelta(hours=4),
            model="gpt-5.1",
            status="ok",
            input_tokens=10,
            output_tokens=3,
            cached_input_tokens=999,
            cost_usd=0.12,
        ),
        RequestLog(
            api_key_id=key_id,
            request_id="req-usage-clamp-b",
            requested_at=now - timedelta(hours=1),
            model="gpt-5.1",
            status="ok",
            input_tokens=4,
            output_tokens=None,
            reasoning_tokens=2,
            cached_input_tokens=8,
            cost_usd=0.03,
        ),
    )

    response = await async_client.get(f"/api/api-keys/{key_id}/usage-7d")
    assert response.status_code == 200

    payload = response.json()
    assert payload == {
        "keyId": key_id,
        "totalTokens": 19,
        "totalCostUsd": 0.15,
        "totalRequests": 2,
        "cachedInputTokens": 14,
    }


@pytest.mark.asyncio
async def test_account_usage_7d_sorts_known_accounts_by_cost_and_places_unknown_last(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
):
    key_id = await _create_api_key(async_client, name="account-breakdown-key")
    now = datetime(2026, 4, 2, 12, 0, 0)
    monkeypatch.setattr("app.modules.api_keys.service.utcnow", lambda: now)

    await _insert_accounts(
        _make_account("acc_low", "low@example.com"),
        _make_account("acc_high", "high@example.com"),
    )
    await _insert_request_logs(
        RequestLog(
            api_key_id=key_id,
            account_id="acc_low",
            request_id="req-account-low",
            requested_at=now - timedelta(hours=5),
            model="gpt-5.1",
            status="ok",
            input_tokens=10,
            output_tokens=5,
            cost_usd=1.25,
        ),
        RequestLog(
            api_key_id=key_id,
            account_id="acc_high",
            request_id="req-account-high",
            requested_at=now - timedelta(hours=4),
            model="gpt-5.1",
            status="ok",
            input_tokens=20,
            output_tokens=10,
            cost_usd=3.5,
        ),
        RequestLog(
            api_key_id=key_id,
            account_id=None,
            request_id="req-account-unknown",
            requested_at=now - timedelta(hours=3),
            model="gpt-5.1",
            status="ok",
            input_tokens=30,
            output_tokens=10,
            cost_usd=99.0,
        ),
        RequestLog(
            api_key_id=key_id,
            account_id="acc_high",
            request_id="req-account-old",
            requested_at=now - timedelta(days=7, minutes=1),
            model="gpt-5.1",
            status="ok",
            input_tokens=100,
            output_tokens=100,
            cost_usd=40.0,
        ),
    )

    response = await async_client.get(f"/api/api-keys/{key_id}/account-usage-7d")
    assert response.status_code == 200

    payload = response.json()
    assert payload == {
        "keyId": key_id,
        "accounts": [
            {
                "accountId": "acc_high",
                "displayName": "high@example.com",
                "totalCostUsd": 3.5,
                "totalTokens": 30,
                "totalRequests": 1,
            },
            {
                "accountId": "acc_low",
                "displayName": "low@example.com",
                "totalCostUsd": 1.25,
                "totalTokens": 15,
                "totalRequests": 1,
            },
            {
                "accountId": None,
                "displayName": "Unknown Account",
                "totalCostUsd": 99.0,
                "totalTokens": 40,
                "totalRequests": 1,
            },
        ],
    }
