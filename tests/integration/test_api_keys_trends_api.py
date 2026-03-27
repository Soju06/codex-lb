from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.utils.time import utcnow
from app.db.models import RequestLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


async def _create_api_key(async_client, *, name: str) -> str:
    response = await async_client.post("/api/api-keys/", json={"name": name})
    assert response.status_code == 200
    return response.json()["id"]


async def _insert_request_logs(*rows: RequestLog) -> None:
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
@pytest.mark.parametrize("endpoint", ["trends", "usage-7d"])
async def test_api_key_detail_endpoints_return_404_for_missing_key(async_client, endpoint: str):
    response = await async_client.get(f"/api/api-keys/missing-key/{endpoint}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_trends_returns_hourly_zero_filled_points_with_bucket_aggregation(async_client):
    key_id = await _create_api_key(async_client, name="trend-key")
    now = utcnow()

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
