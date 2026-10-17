from __future__ import annotations

import asyncio
from typing import Literal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import ApiKeyLimit, ApiKeyUsageReservation
from app.db.session import SessionLocal, engine
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import (
    ApiKeyCreateData,
    ApiKeyRequestUsageBudget,
    ApiKeysService,
    LimitRuleInput,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(engine.dialect.name != "postgresql", reason="requires PostgreSQL row locks"),
]

Settlement = Literal["release", "finalize"]


async def _create_reservations() -> tuple[str, str, str]:
    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        key = await service.create_key(
            ApiKeyCreateData(
                name="concurrent-steering-reservation",
                allowed_models=None,
                expires_at=None,
                limits=[LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000_000)],
            )
        )
        reservation = await service.enforce_limits_for_request(
            key.id,
            request_model="gpt-6-astra",
            request_usage_budget=ApiKeyRequestUsageBudget(input_tokens=100, output_tokens=50),
        )
        unrelated = await service.enforce_limits_for_request(
            key.id,
            request_model="gpt-6-astra",
            request_usage_budget=ApiKeyRequestUsageBudget(input_tokens=40, output_tokens=10),
        )
        assert reservation is not None and unrelated is not None
        return key.id, reservation.reservation_id, unrelated.reservation_id


async def _retain_reservation(session: AsyncSession, reservation_id: str) -> ApiKeyUsageReservation:
    return (
        await session.execute(
            select(ApiKeyUsageReservation)
            .options(selectinload(ApiKeyUsageReservation.items))
            .where(ApiKeyUsageReservation.id == reservation_id)
        )
    ).scalar_one()


async def _adjust(service: ApiKeysService, reservation_id: str, input_delta: int) -> bool:
    adjust = service.extend_usage_reservation if input_delta > 0 else service.reduce_usage_reservation
    return await adjust(
        reservation_id,
        request_service_tier=None,
        request_usage_budget=ApiKeyRequestUsageBudget(input_tokens=abs(input_delta)),
    )


async def _settle(service: ApiKeysService, reservation_id: str, action: Settlement) -> None:
    if action == "release":
        await service.release_usage_reservation(reservation_id)
    else:
        await service.finalize_usage_reservation(reservation_id, model="gpt-6-astra", input_tokens=5, output_tokens=2)


async def _assert_accounting(
    key_id: str, reservation_id: str, unrelated_id: str, reserved_delta: int, action: Settlement | None
) -> None:
    async with SessionLocal() as session:
        reservation = await _retain_reservation(session, reservation_id)
        unrelated = await _retain_reservation(session, unrelated_id)
        actual = None if action is None else (0 if action == "release" else 7)
        assert reservation.items[0].reserved_delta == reserved_delta
        assert reservation.items[0].actual_delta == actual
        assert reservation.status == ("reserved" if action is None else f"{action}d")
        assert unrelated.status == "reserved"
        assert unrelated.items[0].reserved_delta == 50
        assert unrelated.items[0].actual_delta is None
        assert await session.scalar(select(ApiKeyLimit.current_value).where(ApiKeyLimit.api_key_id == key_id)) == (
            50 + (reserved_delta if actual is None else actual)
        )


async def _wait_for_database_lock(waiter_pid: int, blocker_pid: int) -> None:
    # Observe the actual PostgreSQL lock wait rather than assuming that a task
    # has reached the query after a scheduling delay.
    async with asyncio.timeout(5), SessionLocal() as observer:
        while True:
            blockers = await observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter_pid})
            if blocker_pid in blockers:
                return
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["release", "finalize"])
@pytest.mark.parametrize("input_delta", [17, -17], ids=["extend", "reduce"])
async def test_settlement_waits_for_adjustment_and_reconciles_current_budget(
    db_setup, monkeypatch, action, input_delta
):
    key_id, reservation_id, unrelated_id = await _create_reservations()
    async with SessionLocal() as settling, SessionLocal() as adjusting:
        settling_repo = ApiKeysRepository(settling)
        adjusting_repo = ApiKeysRepository(adjusting)
        retained = await _retain_reservation(settling, reservation_id)
        settling_pid = await settling.scalar(text("SELECT pg_backend_pid()"))
        adjusting_pid = await adjusting.scalar(text("SELECT pg_backend_pid()"))
        read_done, start_claim, adjustment_ready, allow_commit = (asyncio.Event() for _ in range(4))
        original_claim = settling_repo.transition_usage_reservation_status
        original_commit = adjusting_repo.commit

        async def claim_after_read(*args, **kwargs):
            read_done.set()
            await start_claim.wait()
            return await original_claim(*args, **kwargs)

        async def hold_adjustment_commit():
            adjustment_ready.set()
            await allow_commit.wait()
            await original_commit()

        monkeypatch.setattr(settling_repo, "transition_usage_reservation_status", claim_after_read)
        monkeypatch.setattr(adjusting_repo, "commit", hold_adjustment_commit)
        async with asyncio.timeout(10), asyncio.TaskGroup() as tasks:
            tasks.create_task(_settle(ApiKeysService(settling_repo), reservation_id, action))
            await read_done.wait()
            adjustment = tasks.create_task(_adjust(ApiKeysService(adjusting_repo), reservation_id, input_delta))
            await adjustment_ready.wait()
            start_claim.set()
            await _wait_for_database_lock(settling_pid, adjusting_pid)
            allow_commit.set()
        assert adjustment.result() is True
        # Keep the ORM graph alive across both reads in the settlement session.
        assert retained.id == reservation_id
    await _assert_accounting(key_id, reservation_id, unrelated_id, 150 + input_delta, action)
    # A competing/repeated terminal must not refund or charge a second time.
    async with SessionLocal() as repeated:
        service = ApiKeysService(ApiKeysRepository(repeated))
        await service.release_usage_reservation(reservation_id)
        await service.finalize_usage_reservation(reservation_id, model="gpt-6-astra", input_tokens=99, output_tokens=99)
    await _assert_accounting(key_id, reservation_id, unrelated_id, 150 + input_delta, action)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["release", "finalize"])
@pytest.mark.parametrize("input_delta", [17, -17], ids=["extend", "reduce"])
async def test_adjustment_waiting_for_settlement_is_a_noop(db_setup, monkeypatch, action, input_delta):
    key_id, reservation_id, unrelated_id = await _create_reservations()
    async with SessionLocal() as settling, SessionLocal() as adjusting:
        settling_repo = ApiKeysRepository(settling)
        retained = await _retain_reservation(adjusting, reservation_id)
        settling_pid = await settling.scalar(text("SELECT pg_backend_pid()"))
        adjusting_pid = await adjusting.scalar(text("SELECT pg_backend_pid()"))
        claimed, allow_settlement = asyncio.Event(), asyncio.Event()
        original_claim = settling_repo.transition_usage_reservation_status

        async def hold_claim(*args, **kwargs):
            result = await original_claim(*args, **kwargs)
            claimed.set()
            await allow_settlement.wait()
            return result

        monkeypatch.setattr(settling_repo, "transition_usage_reservation_status", hold_claim)
        async with asyncio.timeout(10), asyncio.TaskGroup() as tasks:
            tasks.create_task(_settle(ApiKeysService(settling_repo), reservation_id, action))
            await claimed.wait()
            adjustment = tasks.create_task(
                _adjust(ApiKeysService(ApiKeysRepository(adjusting)), reservation_id, input_delta)
            )
            await _wait_for_database_lock(adjusting_pid, settling_pid)
            allow_settlement.set()
        assert adjustment.result() is False
        # Rollback may expire this retained graph; do not trigger lazy loading.
        assert retained in adjusting
    await _assert_accounting(key_id, reservation_id, unrelated_id, 150, action)


@pytest.mark.asyncio
@pytest.mark.parametrize("first_delta", [17, -17], ids=["extend-first", "reduce-first"])
@pytest.mark.parametrize("second_delta", [31, -31], ids=["extend-second", "reduce-second"])
async def test_serialized_adjustments_preserve_the_sum_of_owned_budgets(
    db_setup, monkeypatch, first_delta, second_delta
):
    key_id, reservation_id, unrelated_id = await _create_reservations()
    async with SessionLocal() as first, SessionLocal() as second:
        first_repo = ApiKeysRepository(first)
        retained = await _retain_reservation(second, reservation_id)
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        first_ready, allow_commit = asyncio.Event(), asyncio.Event()
        original_commit = first_repo.commit

        async def hold_first_commit():
            first_ready.set()
            await allow_commit.wait()
            await original_commit()

        monkeypatch.setattr(first_repo, "commit", hold_first_commit)
        async with asyncio.timeout(10), asyncio.TaskGroup() as tasks:
            first_adjustment = tasks.create_task(_adjust(ApiKeysService(first_repo), reservation_id, first_delta))
            await first_ready.wait()
            second_adjustment = tasks.create_task(
                _adjust(ApiKeysService(ApiKeysRepository(second)), reservation_id, second_delta)
            )
            await _wait_for_database_lock(second_pid, first_pid)
            allow_commit.set()
        assert first_adjustment.result() is True and second_adjustment.result() is True
        assert retained.id == reservation_id
    reserved_delta = 150 + first_delta + second_delta
    await _assert_accounting(key_id, reservation_id, unrelated_id, reserved_delta, None)
    async with SessionLocal() as settling:
        await _settle(ApiKeysService(ApiKeysRepository(settling)), reservation_id, "finalize")
    await _assert_accounting(key_id, reservation_id, unrelated_id, reserved_delta, "finalize")
