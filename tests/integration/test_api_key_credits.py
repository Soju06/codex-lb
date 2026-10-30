from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.utils.time import utcnow
from app.db.models import ApiKeyLimit, ApiKeyUsageReservationItem, LimitType, RequestLog
from app.db.session import SessionLocal
from app.modules.accounts.usage_time_rollup import WARMUP_REQUEST_KINDS
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import (
    ApiKeyCreateData,
    ApiKeyRateLimitExceededError,
    ApiKeyRequestUsageBudget,
    ApiKeysService,
    ApiKeyUpdateData,
    ApiKeyValidationError,
    LimitRuleInput,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_credit_settlement_persists_fractions_and_usage_api_keeps_whole_credit_units(async_client) -> None:
    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        created = await service.create_key(
            ApiKeyCreateData(
                name="credits-persisted",
                allowed_models=None,
                limits=[LimitRuleInput(limit_type="credits", limit_window="5h", max_value=1)],
            )
        )
    for _ in range(2):
        async with SessionLocal() as session:
            service = ApiKeysService(ApiKeysRepository(session))
            reservation = await service.enforce_limits_for_request(
                created.id,
                request_model="gpt-5.6-sol",
                request_usage_budget=ApiKeyRequestUsageBudget(input_tokens=1, output_tokens=0),
            )
            assert reservation is not None
            await service.finalize_usage_reservation(
                reservation.reservation_id,
                model="gpt-5.6-sol",
                input_tokens=6_000,
                output_tokens=0,
            )
    async with SessionLocal() as session:
        limit = (await session.scalars(select(ApiKeyLimit).where(ApiKeyLimit.api_key_id == created.id))).one()
        assert limit.max_value == 100_000_000
        assert limit.current_value == 120_000_000
        actual = list(await session.scalars(select(ApiKeyUsageReservationItem.actual_delta)))
        assert actual == [60_000_000, 60_000_000]
        service = ApiKeysService(ApiKeysRepository(session))
        with pytest.raises(ApiKeyRateLimitExceededError, match="credits"):
            await service.enforce_limits_for_request(created.id, request_model="gpt-5.6-sol")
    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {created.key}"})
    assert response.status_code == 200
    limit = response.json()["limits"][0]
    assert (limit["limit_type"], limit["max_value"], limit["current_value"], limit["remaining_value"]) == (
        "credits",
        1,
        1,
        0,
    )


@pytest.mark.asyncio
async def test_credit_reservations_cannot_double_spend_and_release_exactly(async_client) -> None:
    async with SessionLocal() as session:
        created = await ApiKeysService(ApiKeysRepository(session)).create_key(
            ApiKeyCreateData(
                name="credits-concurrent",
                allowed_models=None,
                limits=[LimitRuleInput(limit_type="credits", limit_window="5h", max_value=1)],
            )
        )

    async def reserve():
        async with SessionLocal() as session:
            return await ApiKeysService(ApiKeysRepository(session)).enforce_limits_for_request(
                created.id,
                request_model="gpt-6-astra",
            )

    results = await asyncio.gather(reserve(), reserve(), return_exceptions=True)
    granted = [result for result in results if not isinstance(result, BaseException)]
    assert len(granted) == 1
    assert sum(isinstance(result, ApiKeyRateLimitExceededError) for result in results) == 1
    reservation = granted[0]
    assert reservation is not None
    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        await service.release_usage_reservation(reservation.reservation_id)
        await service.release_usage_reservation(reservation.reservation_id)
        limit = (await session.scalars(select(ApiKeyLimit).where(ApiKeyLimit.api_key_id == created.id))).one()
        assert limit.current_value == 0


@pytest.mark.asyncio
async def test_credit_pricing_basis_survives_sessions_and_response_metadata(async_client) -> None:
    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        created = await service.create_key(
            ApiKeyCreateData(
                name="credits-basis",
                allowed_models=None,
                limits=[LimitRuleInput(limit_type="credits", limit_window="5h", max_value=1)],
            )
        )
        reservation = await service.enforce_limits_for_request(
            created.id,
            request_model="gpt-6.1-sol",
            request_service_tier="fast",
        )
        assert reservation is not None
    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        stored = await repo.get_usage_reservation(reservation.reservation_id)
        assert stored is not None
        assert stored.credit_service_tier == "fast"
        await ApiKeysService(repo).finalize_usage_reservation(
            reservation.reservation_id,
            model="response-model-snapshot",
            service_tier="response-tier-snapshot",
            input_tokens=100_000,
            output_tokens=0,
            cached_input_tokens=100_000,
        )
        limit = (await session.scalars(select(ApiKeyLimit).where(ApiKeyLimit.api_key_id == created.id))).one()
        assert limit.current_value == 50_000_000
    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {created.key}"})
    assert response.status_code == 200
    limit = response.json()["limits"][0]
    assert (limit["max_value"], limit["current_value"], limit["remaining_value"]) == (1, 0, 1)


@pytest.mark.asyncio
async def test_credit_limit_refuses_fixed_point_overflow(async_client) -> None:
    response = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "credit-overflow",
            "limits": [{"limitType": "credits", "limitWindow": "5h", "maxValue": 92_233_720_369}],
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_credit_storage_ceiling_stays_exhausted_after_a_later_release(async_client) -> None:
    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        service = ApiKeysService(repo)
        created = await service.create_key(
            ApiKeyCreateData(
                name="credit-ceiling",
                allowed_models=None,
                limits=[LimitRuleInput(limit_type="credits", limit_window="5h", max_value=92_233_720_368)],
            )
        )
        limit = (await session.scalars(select(ApiKeyLimit).where(ApiKeyLimit.api_key_id == created.id))).one()
        limit.current_value = limit.max_value - 200_000_000
        await session.commit()
        first = await service.enforce_limits_for_request(
            created.id,
            request_model="gpt-5.6-sol",
            request_usage_budget=ApiKeyRequestUsageBudget(input_tokens=0, output_tokens=0),
        )
        second = await service.enforce_limits_for_request(created.id, request_model="gpt-5.6-sol")
        assert first is not None and second is not None
        await service.finalize_usage_reservation(
            first.reservation_id,
            model="gpt-5.6-sol",
            input_tokens=30_000,
            output_tokens=0,
        )
        await service.release_usage_reservation(second.reservation_id)
        stored = await session.scalar(select(ApiKeyLimit.current_value).where(ApiKeyLimit.api_key_id == created.id))
        assert stored == 2**63 - 1
        assert isinstance(stored, int)
        with pytest.raises(ApiKeyRateLimitExceededError):
            await service.enforce_limits_for_request(created.id, request_model="gpt-5.6-sol")


@pytest.mark.asyncio
async def test_unpriced_credit_admission_rolls_back_other_reservations(async_client) -> None:
    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        created = await service.create_key(
            ApiKeyCreateData(
                name="credit-rollback",
                allowed_models=None,
                limits=[
                    LimitRuleInput(limit_type="total_tokens", limit_window="5h", max_value=1_000_000),
                    LimitRuleInput(limit_type="credits", limit_window="5h", max_value=1),
                ],
            )
        )
        with pytest.raises(ApiKeyRateLimitExceededError, match="No published Codex credit rate"):
            await service.enforce_limits_for_request(created.id, request_model="unpriced-model")
        assert list(
            await session.scalars(select(ApiKeyLimit.current_value).where(ApiKeyLimit.api_key_id == created.id))
        ) == [0, 0]
        assert not list(await session.scalars(select(ApiKeyUsageReservationItem.reservation_id)))


@pytest.mark.asyncio
async def test_credit_backfill_matches_cached_and_failed_settlement_and_preserves_usage_on_edit(async_client) -> None:
    now = utcnow()
    async with SessionLocal() as session:
        created = await ApiKeysService(ApiKeysRepository(session)).create_key(
            ApiKeyCreateData(name="credits-backfill", allowed_models=None)
        )
        session.add_all(
            [
                RequestLog(
                    api_key_id=created.id,
                    request_id="uncached",
                    model="gpt-5.6-sol",
                    status="success",
                    input_tokens=1_000,
                    output_tokens=0,
                    requested_at=now - timedelta(minutes=1),
                ),
                RequestLog(
                    api_key_id=created.id,
                    request_id="cached-failure",
                    model="gpt-5.6-sol",
                    status="error",
                    input_tokens=1_000,
                    cached_input_tokens=1_000,
                    output_tokens=0,
                    requested_at=now - timedelta(minutes=1),
                ),
                RequestLog(
                    api_key_id=created.id,
                    request_id="fast",
                    model="gpt-5.6-sol",
                    status="success",
                    input_tokens=1_000,
                    output_tokens=0,
                    service_tier="fast",
                    requested_service_tier="fast",
                    requested_at=now - timedelta(minutes=1),
                ),
                RequestLog(
                    api_key_id=created.id,
                    request_id="warmup",
                    model="gpt-5.6-sol",
                    status="success",
                    input_tokens=1_000_000,
                    output_tokens=0,
                    request_kind=next(iter(WARMUP_REQUEST_KINDS)),
                    requested_at=now - timedelta(minutes=1),
                ),
                RequestLog(
                    api_key_id=created.id,
                    request_id="outside-window",
                    model="gpt-5.6-sol",
                    status="success",
                    input_tokens=1_000_000,
                    output_tokens=0,
                    requested_at=now - timedelta(hours=6),
                ),
                RequestLog(
                    api_key_id="other-key",
                    request_id="other-key",
                    model="gpt-5.6-sol",
                    status="success",
                    input_tokens=1_000_000,
                    output_tokens=0,
                    requested_at=now - timedelta(minutes=1),
                ),
            ]
        )
        await session.commit()
        service = ApiKeysService(ApiKeysRepository(session))
        for maximum in (1, 2):
            updated = await service.update_key(
                created.id,
                ApiKeyUpdateData(
                    limits=[LimitRuleInput(limit_type="credits", limit_window="5h", max_value=maximum)],
                    limits_set=True,
                ),
            )
            assert updated.limits[0].max_value == maximum
            limit = (await session.scalars(select(ApiKeyLimit).where(ApiKeyLimit.api_key_id == created.id))).one()
            assert limit.current_value == 31_000_000
            assert limit.max_value == maximum * 100_000_000


@pytest.mark.asyncio
async def test_credit_backfill_saturates_the_combined_model_groups(async_client) -> None:
    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        created = await service.create_key(ApiKeyCreateData(name="credit-history-ceiling", allowed_models=None))
        for model, tokens in [("gpt-6-astra", 2**50), ("gpt-5.6-sol", 1)]:
            session.add(
                RequestLog(
                    api_key_id=created.id,
                    request_id=model,
                    model=model,
                    status="success",
                    input_tokens=tokens,
                    output_tokens=0,
                    requested_at=utcnow() - timedelta(minutes=1),
                )
            )
        await session.commit()
        await service.update_key(
            created.id,
            ApiKeyUpdateData(
                limits=[LimitRuleInput(limit_type="credits", limit_window="5h", max_value=1)],
                limits_set=True,
            ),
        )
        stored = await session.scalar(select(ApiKeyLimit.current_value).where(ApiKeyLimit.api_key_id == created.id))
        assert stored == 2**63 - 1


@pytest.mark.asyncio
async def test_credit_backfill_rejects_unpriced_token_bearing_history(async_client) -> None:
    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        created = await service.create_key(ApiKeyCreateData(name="credits-unpriced-history", allowed_models=None))
        session.add(
            RequestLog(
                api_key_id=created.id,
                request_id="unpriced",
                model="unpriced-model",
                status="success",
                input_tokens=1,
                output_tokens=0,
                requested_at=utcnow() - timedelta(minutes=1),
            )
        )
        await session.commit()
        with pytest.raises(ApiKeyValidationError, match="No published Codex credit rate"):
            await service.update_key(
                created.id,
                ApiKeyUpdateData(
                    limits=[LimitRuleInput(limit_type="credits", limit_window="5h", max_value=1)],
                    limits_set=True,
                ),
            )
        await session.rollback()
        assert not list(await session.scalars(select(ApiKeyLimit).where(ApiKeyLimit.limit_type == LimitType.CREDITS)))
