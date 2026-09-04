from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.db.models import LimitType
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository, UsageReservationData
from app.modules.api_keys.service import (
    ApiKeyCreateData,
    ApiKeyRateLimitExceededError,
    ApiKeyRequestUsageBudget,
    ApiKeysService,
    LimitRuleInput,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_rejected_reservation_extension_rolls_back_limit_and_ledger_updates(_reset_db_state) -> None:
    del _reset_db_state
    async with SessionLocal() as session:
        repository = ApiKeysRepository(session)
        service = ApiKeysService(repository)
        created = await service.create_key(
            ApiKeyCreateData(
                name="reservation-extension-rollback",
                allowed_models=None,
                expires_at=None,
                limits=[
                    LimitRuleInput(limit_type="input_tokens", limit_window="weekly", max_value=1_000),
                    LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=349),
                ],
            )
        )
        reservation = await service.enforce_limits_for_request(
            created.id,
            request_model="gpt-5.1",
            request_usage_budget=ApiKeyRequestUsageBudget(input_tokens=100, output_tokens=50),
        )
        assert reservation is not None

        with pytest.raises(ApiKeyRateLimitExceededError):
            await service.extend_usage_reservation(
                reservation.reservation_id,
                request_service_tier=None,
                request_usage_budget=ApiKeyRequestUsageBudget(input_tokens=200),
            )

    async with SessionLocal() as session:
        repository = ApiKeysRepository(session)
        stored = await repository.get_usage_reservation(reservation.reservation_id)
        limits = await repository.get_limits_by_key(created.id)
        assert stored is not None
        assert {item.limit_type: item.reserved_delta for item in stored.items} == {
            LimitType.INPUT_TOKENS: 100,
            LimitType.TOTAL_TOKENS: 150,
        }
        assert {limit.limit_type: limit.current_value for limit in limits} == {
            LimitType.INPUT_TOKENS: 100,
            LimitType.TOTAL_TOKENS: 150,
        }


@pytest.mark.asyncio
async def test_reservation_reduction_rollover_preserves_new_window_until_terminal_settlement(_reset_db_state) -> None:
    del _reset_db_state
    async with SessionLocal() as session:
        repository = ApiKeysRepository(session)
        service = ApiKeysService(repository)
        created = await service.create_key(
            ApiKeyCreateData(
                name="reservation-reduction-rollover",
                allowed_models=None,
                expires_at=None,
                limits=[
                    LimitRuleInput(limit_type="input_tokens", limit_window="weekly", max_value=1_000),
                    LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000),
                ],
            )
        )
        reservation = await service.enforce_limits_for_request(
            created.id,
            request_model="gpt-5.1",
            request_usage_budget=ApiKeyRequestUsageBudget(input_tokens=100, output_tokens=50),
        )
        assert reservation is not None

        limits = await repository.get_limits_by_key(created.id)
        total_limit = next(limit for limit in limits if limit.limit_type == LimitType.TOTAL_TOKENS)
        original_total_reset_at = total_limit.reset_at
        assert original_total_reset_at is not None
        rollover_reset_at = original_total_reset_at + timedelta(days=7)
        total_limit.current_value = 0
        total_limit.reset_at = rollover_reset_at
        await repository.commit()

    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        with pytest.raises(RuntimeError, match="failed to reduce API key usage reservation"):
            await service.reduce_usage_reservation(
                reservation.reservation_id,
                request_service_tier=None,
                request_usage_budget=ApiKeyRequestUsageBudget(input_tokens=50),
            )

    async with SessionLocal() as session:
        repository = ApiKeysRepository(session)
        stored = await repository.get_usage_reservation(reservation.reservation_id)
        limits = await repository.get_limits_by_key(created.id)
        assert stored is not None
        assert stored.status == "reserved"
        assert {item.limit_type: item.reserved_delta for item in stored.items} == {
            LimitType.INPUT_TOKENS: 100,
            LimitType.TOTAL_TOKENS: 150,
        }
        assert {limit.limit_type: limit.current_value for limit in limits} == {
            LimitType.INPUT_TOKENS: 100,
            LimitType.TOTAL_TOKENS: 0,
        }

        await ApiKeysService(repository).finalize_usage_reservation(
            reservation.reservation_id,
            model="gpt-5.1",
            input_tokens=80,
            output_tokens=20,
        )

    async with SessionLocal() as session:
        repository = ApiKeysRepository(session)
        stored = await repository.get_usage_reservation(reservation.reservation_id)
        limits = await repository.get_limits_by_key(created.id)
        assert stored is not None
        assert stored.status == "finalized"
        assert {item.limit_type: item.actual_delta for item in stored.items} == {
            LimitType.INPUT_TOKENS: 80,
            LimitType.TOTAL_TOKENS: 100,
        }
        assert {limit.limit_type: limit.current_value for limit in limits} == {
            LimitType.INPUT_TOKENS: 80,
            LimitType.TOTAL_TOKENS: 0,
        }
        total_limit = next(limit for limit in limits if limit.limit_type == LimitType.TOTAL_TOKENS)
        assert total_limit.reset_at == rollover_reset_at


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["finalize", "release"])
async def test_reservation_extension_serializes_with_terminal_settlement(_reset_db_state, terminal: str) -> None:
    del _reset_db_state
    async with SessionLocal() as session:
        repository = ApiKeysRepository(session)
        service = ApiKeysService(repository)
        created = await service.create_key(
            ApiKeyCreateData(
                name=f"reservation-extension-{terminal}",
                allowed_models=None,
                expires_at=None,
                limits=[
                    LimitRuleInput(limit_type="input_tokens", limit_window="weekly", max_value=1_000),
                    LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000),
                ],
            )
        )
        reservation = await service.enforce_limits_for_request(
            created.id,
            request_model="gpt-5.1",
            request_usage_budget=ApiKeyRequestUsageBudget(input_tokens=100, output_tokens=50),
        )
        assert reservation is not None

    extension_has_lock = asyncio.Event()
    allow_extension = asyncio.Event()

    class _PausedExtensionRepository(ApiKeysRepository):
        async def get_usage_reservation_for_update(self, reservation_id: str) -> UsageReservationData | None:
            stored = await super().get_usage_reservation_for_update(reservation_id)
            extension_has_lock.set()
            await allow_extension.wait()
            return stored

    async with SessionLocal() as extension_session, SessionLocal() as terminal_session:
        extension_service = ApiKeysService(_PausedExtensionRepository(extension_session))
        terminal_service = ApiKeysService(ApiKeysRepository(terminal_session))
        extension_task = asyncio.create_task(
            extension_service.extend_usage_reservation(
                reservation.reservation_id,
                request_service_tier=None,
                request_usage_budget=ApiKeyRequestUsageBudget(input_tokens=200),
            )
        )
        await extension_has_lock.wait()
        if terminal == "finalize":
            terminal_task = asyncio.create_task(
                terminal_service.finalize_usage_reservation(
                    reservation.reservation_id,
                    model="gpt-5.1",
                    input_tokens=30,
                    output_tokens=20,
                )
            )
        else:
            terminal_task = asyncio.create_task(terminal_service.release_usage_reservation(reservation.reservation_id))
        await asyncio.sleep(0)
        allow_extension.set()
        assert await extension_task is True
        await terminal_task

    async with SessionLocal() as session:
        repository = ApiKeysRepository(session)
        stored = await repository.get_usage_reservation(reservation.reservation_id)
        limits = await repository.get_limits_by_key(created.id)
        assert stored is not None
        assert stored.status == ("finalized" if terminal == "finalize" else "released")
        expected = 50 if terminal == "finalize" else 0
        assert {limit.limit_type: limit.current_value for limit in limits} == {
            LimitType.INPUT_TOKENS: 30 if terminal == "finalize" else 0,
            LimitType.TOTAL_TOKENS: expected,
        }
