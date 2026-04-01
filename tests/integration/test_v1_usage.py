from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.utils.time import utcnow
from app.db.models import ApiKeyLimit, LimitType, LimitWindow
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService, LimitRuleInput
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


async def _create_api_key(
    *,
    name: str,
    limits: list[LimitRuleInput] | None = None,
) -> tuple[str, str]:
    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        created = await service.create_key(
            ApiKeyCreateData(
                name=name,
                allowed_models=None,
                limits=limits or [],
            )
        )
    return created.id, created.key


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "expected_message"),
    [
        ({}, "Missing API key in Authorization header"),
        ({"Authorization": "Bearer invalid-key"}, "Invalid API key"),
    ],
)
async def test_v1_usage_requires_valid_bearer_key_when_global_auth_disabled(async_client, headers, expected_message):
    response = await async_client.get("/v1/usage", headers=headers)

    assert response.status_code == 401
    payload = response.json()
    assert payload["error"]["code"] == "invalid_api_key"
    assert payload["error"]["message"] == expected_message


@pytest.mark.asyncio
async def test_v1_usage_returns_zero_usage_for_key_without_logs(async_client):
    _, plain_key = await _create_api_key(name="zero-usage")

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200
    assert response.json() == {
        "request_count": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "total_cost_usd": 0.0,
        "limits": [],
    }


@pytest.mark.asyncio
async def test_v1_usage_scopes_usage_and_limits_to_authenticated_key(async_client):
    key_a_id, key_a = await _create_api_key(
        name="usage-key-a",
        limits=[LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000)],
    )
    key_b_id, _ = await _create_api_key(
        name="usage-key-b",
        limits=[LimitRuleInput(limit_type="cost_usd", limit_window="monthly", max_value=5_000_000)],
    )

    now = utcnow()
    limit_a_total_reset = now + timedelta(days=6)
    limit_a_cost_reset = now + timedelta(days=20)
    limit_b_reset = now + timedelta(days=20)

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        await repo.replace_limits(
            key_a_id,
            [
                ApiKeyLimit(
                    api_key_id=key_a_id,
                    limit_type=LimitType.TOTAL_TOKENS,
                    limit_window=LimitWindow.WEEKLY,
                    max_value=1_000,
                    current_value=420,
                    model_filter="gpt-5.4",
                    reset_at=limit_a_total_reset,
                ),
                ApiKeyLimit(
                    api_key_id=key_a_id,
                    limit_type=LimitType.COST_USD,
                    limit_window=LimitWindow.MONTHLY,
                    max_value=10_000_000,
                    current_value=2_500_000,
                    model_filter=None,
                    reset_at=limit_a_cost_reset,
                ),
            ],
        )
        await repo.replace_limits(
            key_b_id,
            [
                ApiKeyLimit(
                    api_key_id=key_b_id,
                    limit_type=LimitType.COST_USD,
                    limit_window=LimitWindow.MONTHLY,
                    max_value=5_000_000,
                    current_value=5,
                    model_filter=None,
                    reset_at=limit_b_reset,
                )
            ],
        )

        logs = RequestLogsRepository(session)
        await logs.add_log(
            account_id=None,
            api_key_id=key_a_id,
            request_id="req_v1_usage_a1",
            model="gpt-5.4",
            input_tokens=100,
            output_tokens=25,
            cached_input_tokens=20,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=2),
        )
        await logs.add_log(
            account_id=None,
            api_key_id=key_a_id,
            request_id="req_v1_usage_a2",
            model="gpt-5.4",
            input_tokens=10,
            output_tokens=5,
            cached_input_tokens=2,
            latency_ms=80,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=1),
        )
        await logs.add_log(
            account_id=None,
            api_key_id=key_b_id,
            request_id="req_v1_usage_b1",
            model="gpt-5.4-mini",
            input_tokens=999,
            output_tokens=111,
            cached_input_tokens=50,
            latency_ms=90,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=3),
        )

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {key_a}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["request_count"] == 2
    assert payload["total_tokens"] == 140
    assert payload["cached_input_tokens"] == 22
    assert payload["total_cost_usd"] > 0
    assert payload["limits"] == [
        {
            "limit_type": "total_tokens",
            "limit_window": "weekly",
            "max_value": 1000,
            "current_value": 420,
            "remaining_value": 580,
            "model_filter": "gpt-5.4",
            "reset_at": limit_a_total_reset.isoformat() + "Z",
        },
        {
            "limit_type": "cost_usd",
            "limit_window": "monthly",
            "max_value": 10000000,
            "current_value": 2500000,
            "remaining_value": 7500000,
            "model_filter": None,
            "reset_at": limit_a_cost_reset.isoformat() + "Z",
        },
    ]


@pytest.mark.asyncio
async def test_v1_usage_still_works_when_global_api_key_auth_is_disabled(async_client):
    _, plain_key = await _create_api_key(name="self-usage-auth-disabled")

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200
