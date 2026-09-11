"""A durable result cannot resurrect an episode cleared after a probe return."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.unit.test_http_bridge_probe_return_clock import _admitted_probe, _return_probe

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize(
    ("operation", "reset_operation"), [("lookup", "lookup"), ("persist", "lookup"), ("lookup", "persist")]
)
@pytest.mark.parametrize("clock_step", [0.0, 1.0], ids=["same-tick", "later-tick"])
async def test_old_durable_result_cannot_restore_cooldown_after_newer_reset(
    operation: str, reset_operation: str, clock_step: float
) -> None:
    case = await _admitted_probe(now=100.0)
    started = asyncio.Event()
    finish = asyncio.Event()
    future = SimpleNamespace(
        consecutive_failures=3,
        cooldown_until_epoch=case.clock.time() + 20.0,
        last_detail="stream_incomplete",
        updated_at_epoch=case.clock.time(),
        admission_generation=0,
    )

    async def complete_after_reset(**kwargs: Any) -> Any:
        started.set()
        await finish.wait()
        return future

    claims: list[tuple[float, object, int]] = []
    if operation == "lookup":
        case.service._durable_bridge.lookup_retry_circuit = AsyncMock(side_effect=complete_after_reset)
        operation_call = case.service._http_bridge_precreated_retry_allowed(case.session, claimed_lease_out=claims)
    else:
        case.service._durable_bridge.persist_retry_circuit = AsyncMock(side_effect=complete_after_reset)
        operation_call = case.service._persist_http_bridge_retry_circuit(case.session, case.state)
    task = asyncio.create_task(operation_call)
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)
        case.clock.advance(clock_step)
        await _return_probe(case)
        case.clock.advance(clock_step)
        reset = SimpleNamespace(
            consecutive_failures=0,
            cooldown_until_epoch=0.0,
            last_detail=None,
            updated_at_epoch=case.clock.time() + 1.0,
            admission_generation=0,
        )
        if reset_operation == "lookup":
            case.service._durable_bridge.lookup_retry_circuit = AsyncMock(return_value=reset)
            await case.service._load_http_bridge_retry_circuit(case.session)
        else:
            case.service._durable_bridge.persist_retry_circuit = AsyncMock(return_value=reset)
            await case.service._persist_http_bridge_retry_circuit(case.session, case.state)
        assert case.state.consecutive_failures == 0
        assert case.state.cooldown_until == 0.0
        assert not case.state.half_open_return_pending
        finish.set()
        result = await task
        if operation == "persist":
            # A read outage preserves the already observed reset locally.
            case.service._durable_bridge.lookup_retry_circuit = AsyncMock(
                side_effect=RuntimeError("lookup unavailable")
            )
            result = await case.service._http_bridge_precreated_retry_allowed(case.session, claimed_lease_out=claims)
        assert result is True
        assert claims == []
        assert case.state.consecutive_failures == 0
        assert case.state.cooldown_until == 0.0
        assert not case.state.half_open_return_pending
    finally:
        finish.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
