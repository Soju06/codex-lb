"""A returned probe remains exclusive when the owner clock does not advance."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge.retry_circuit import DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS
from tests.simulation.virtual_time import VirtualClock
from tests.unit.test_proxy_http_bridge import _make_bridge_session

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("release_delay", [0.0, 1.0], ids=["same-tick", "later-tick"])
async def test_returned_probe_survives_same_tick_durable_miss_and_admits_one_replacement(
    release_delay: float,
) -> None:
    clock = VirtualClock(monotonic_value=100.0)
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    cast(Any, service)._clock = clock
    session = _make_bridge_session(key_value="returned-probe-frozen-clock")
    row = SimpleNamespace(
        consecutive_failures=2,
        cooldown_until_epoch=clock.time() - 1.0,
        last_detail="stream_incomplete",
        updated_at_epoch=clock.time() - 10.0,
        admission_generation=0,
    )
    lookup = AsyncMock(return_value=row)
    service._durable_bridge = SimpleNamespace(lookup_retry_circuit=lookup)
    owner = object()
    claims: list[tuple[float, object, int]] = []
    assert await service._http_bridge_precreated_retry_allowed(session, probe_owner=owner, claimed_lease_out=claims)
    assert len(claims) == 1
    deadline, claimed_owner, generation = claims[0]
    assert claimed_owner is owner
    state = cast(Any, service)._http_bridge_retry_circuits[session.key]
    clock.advance(release_delay)
    assert await service._release_http_bridge_retry_circuit_half_open(
        session,
        detail="probe_not_dispatched",
        probe_owner=owner,
        expected_half_open_until=deadline,
        expected_half_open_generation=generation,
    )
    assert state.consecutive_failures == row.consecutive_failures
    assert state.half_open_owner_token is None

    # Every call observes the same clock tick, but the return happened after
    # the initial durable load. A subsequent miss cannot erase that return.
    lookup.return_value = None
    replacements = [object(), object(), object()]
    replacement_claims: list[list[tuple[float, object, int]]] = [[], [], []]
    admitted = await asyncio.gather(
        *(
            service._http_bridge_precreated_retry_allowed(session, probe_owner=replacement, claimed_lease_out=claim)
            for replacement, claim in zip(replacements, replacement_claims, strict=True)
        )
    )
    assert sum(admitted) == 1, (
        f"returned probe admitted {admitted}; "
        f"load={state.last_durable_load_monotonic}, return={state.last_half_open_release_monotonic}"
    )
    assert sum(len(claim) for claim in replacement_claims) == 1
    winner = admitted.index(True)
    replacement_state = cast(Any, service)._http_bridge_retry_circuits[session.key]
    assert replacement_state.consecutive_failures == row.consecutive_failures
    assert replacement_state.half_open_owner_token is replacements[winner]
    assert replacement_claims[winner][0][2] > generation


async def _admitted_probe(*, now: float = 0.0) -> Any:
    clock = VirtualClock(monotonic_value=now)
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    cast(Any, service)._clock = clock
    session = _make_bridge_session(key_value="probe-return-reconciliation")
    row = SimpleNamespace(
        consecutive_failures=2,
        cooldown_until_epoch=clock.time() - 1.0,
        last_detail="stream_incomplete",
        updated_at_epoch=clock.time() - 10.0,
        admission_generation=0,
    )
    service._durable_bridge = SimpleNamespace(lookup_retry_circuit=AsyncMock(return_value=row))
    owner = object()
    claims: list[tuple[float, object, int]] = []
    assert await service._http_bridge_precreated_retry_allowed(session, probe_owner=owner, claimed_lease_out=claims)
    return SimpleNamespace(
        clock=clock,
        service=service,
        session=session,
        row=row,
        owner=owner,
        claim=claims[0],
        state=cast(Any, service)._http_bridge_retry_circuits[session.key],
    )


async def _return_probe(case: Any) -> None:
    assert await case.service._release_http_bridge_retry_circuit_half_open(
        case.session,
        detail="probe_not_dispatched",
        probe_owner=case.owner,
        expected_half_open_until=case.claim[0],
        expected_half_open_generation=case.claim[2],
    )


@pytest.mark.parametrize("operation", ["lookup", "purge", "persist"])
async def test_durable_operation_started_before_return_cannot_consume_it(operation: str) -> None:
    case = await _admitted_probe()
    started = asyncio.Event()
    finish = asyncio.Event()
    reset = SimpleNamespace(
        consecutive_failures=0,
        cooldown_until_epoch=0.0,
        last_detail=None,
        updated_at_epoch=case.clock.time(),
        admission_generation=0,
    )

    async def complete_after_return(**kwargs: Any) -> Any:
        started.set()
        await finish.wait()
        return True if operation == "purge" else reset

    if operation == "lookup":
        case.service._durable_bridge.lookup_retry_circuit = AsyncMock(side_effect=complete_after_return)
    elif operation == "purge":
        case.row.updated_at_epoch = case.clock.time() - DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS - 1.0
        case.service._durable_bridge.purge_retry_circuit = AsyncMock(side_effect=complete_after_return)
    else:
        case.service._durable_bridge.persist_retry_circuit = AsyncMock(side_effect=complete_after_return)
    task = asyncio.create_task(
        case.service._persist_http_bridge_retry_circuit(case.session, case.state)
        if operation == "persist"
        else case.service._load_http_bridge_retry_circuit(case.session)
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await _return_probe(case)
        finish.set()
        await task
        assert case.service._http_bridge_retry_circuits[case.session.key] is case.state
        assert case.state.half_open_return_pending
        assert case.state.consecutive_failures == 2
        # A reset observed by a fresh lookup is authoritative after the return.
        case.service._durable_bridge.lookup_retry_circuit = AsyncMock(return_value=reset)
        await case.service._load_http_bridge_retry_circuit(case.session)
        assert not case.state.half_open_return_pending
        assert case.state.consecutive_failures == 0
        claims: list[tuple[float, object, int]] = []
        assert await case.service._http_bridge_precreated_retry_allowed(case.session, claimed_lease_out=claims)
        assert claims == []
    finally:
        finish.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("future_cooldown", [False, True])
async def test_zero_clock_return_survives_repeated_loads_until_replacement_claim(future_cooldown: bool) -> None:
    case = await _admitted_probe()
    await _return_probe(case)
    if future_cooldown:
        case.row.cooldown_until_epoch = case.clock.time() + 20.0
        case.row.updated_at_epoch += 1.0
    for _ in range(3):
        await case.service._load_http_bridge_retry_circuit(case.session)
        assert case.state.half_open_return_pending
    if future_cooldown:
        assert not await case.service._http_bridge_precreated_retry_allowed(case.session)
        case.clock.advance(21.0)
    claims: list[tuple[float, object, int]] = []
    assert await case.service._http_bridge_precreated_retry_allowed(case.session, claimed_lease_out=claims)
    assert len(claims) == 1
    assert claims[0][2] > case.claim[2]
    assert not case.state.half_open_return_pending
    assert not await case.service._http_bridge_precreated_retry_allowed(case.session)


async def test_zero_clock_without_return_does_not_create_probe() -> None:
    case = await _admitted_probe()
    reset = SimpleNamespace(
        consecutive_failures=0,
        cooldown_until_epoch=0.0,
        last_detail=None,
        updated_at_epoch=case.clock.time(),
        admission_generation=0,
    )
    # A separate untouched key has no local return or existing probe.
    session = _make_bridge_session(key_value="untouched-zero-clock")
    case.service._durable_bridge.lookup_retry_circuit = AsyncMock(return_value=reset)
    for _ in range(3):
        claims: list[tuple[float, object, int]] = []
        assert await case.service._http_bridge_precreated_retry_allowed(session, claimed_lease_out=claims)
        assert claims == []


async def test_consumed_return_does_not_outlive_replacement_owner_and_fresh_reset() -> None:
    case = await _admitted_probe()
    await _return_probe(case)
    case.owner = object()
    claims: list[tuple[float, object, int]] = []
    assert await case.service._http_bridge_precreated_retry_allowed(
        case.session, probe_owner=case.owner, claimed_lease_out=claims
    )
    case.claim = claims[0]
    assert not case.state.half_open_return_pending
    reset = SimpleNamespace(
        consecutive_failures=0,
        cooldown_until_epoch=0.0,
        last_detail=None,
        updated_at_epoch=case.clock.time(),
        admission_generation=0,
    )
    case.service._durable_bridge.lookup_retry_circuit = AsyncMock(return_value=reset)
    await case.service._load_http_bridge_retry_circuit(case.session)
    assert case.state.half_open_owner_token is case.owner
    assert case.state.consecutive_failures == 2
    await _return_probe(case)
    await case.service._load_http_bridge_retry_circuit(case.session)
    assert not case.state.half_open_return_pending
    assert case.state.consecutive_failures == 0
    for _ in range(2):
        claims = []
        assert await case.service._http_bridge_precreated_retry_allowed(case.session, claimed_lease_out=claims)
        assert claims == []


@pytest.mark.parametrize("operation", ["lookup", "persist"])
async def test_in_flight_durable_cooldown_still_suppresses_admission_after_return(operation: str) -> None:
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

    async def complete_after_return(**kwargs: Any) -> Any:
        started.set()
        await finish.wait()
        return future

    claims: list[tuple[float, object, int]] = []
    if operation == "lookup":
        case.service._durable_bridge.lookup_retry_circuit = AsyncMock(side_effect=complete_after_return)
        operation_call = case.service._http_bridge_precreated_retry_allowed(case.session, claimed_lease_out=claims)
    else:
        case.service._durable_bridge.persist_retry_circuit = AsyncMock(side_effect=complete_after_return)
        operation_call = case.service._persist_http_bridge_retry_circuit(case.session, case.state)
    task = asyncio.create_task(operation_call)
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await _return_probe(case)
        finish.set()
        result = await task
        if operation == "persist":
            # A later miss must not mask a discarded write result by reloading it.
            case.service._durable_bridge.lookup_retry_circuit = AsyncMock(return_value=None)
            result = await case.service._http_bridge_precreated_retry_allowed(case.session, claimed_lease_out=claims)
        assert result is False
        assert claims == []
        assert case.state.half_open_return_pending
        assert case.state.cooldown_until == 120.0
        assert case.state.consecutive_failures == 3
        case.clock.advance(21.0)
        case.service._durable_bridge.lookup_retry_circuit = AsyncMock(return_value=future)
        assert await case.service._http_bridge_precreated_retry_allowed(case.session, claimed_lease_out=claims)
        assert len(claims) == 1
        assert claims[0][2] > case.claim[2]
        assert not await case.service._http_bridge_precreated_retry_allowed(case.session)
    finally:
        finish.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
