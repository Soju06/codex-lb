"""Persist reconciliation preserves a single positive-elapsed transition."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.modules.proxy import service as proxy_service
from tests.simulation.virtual_time import VirtualClock
from tests.unit.test_proxy_http_bridge import _make_bridge_session

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline", ["elapsed", "absent", "negative", "future"])
@pytest.mark.parametrize("failures", [1, 2])
async def test_persisted_snapshot_preserves_next_admission_transition(deadline: str, failures: int) -> None:
    clock = VirtualClock(monotonic_value=100.0)
    service = proxy_service.ProxyService(cast(Any, nullcontext()), clock=clock)
    session = _make_bridge_session(key_value="persist-positive-elapsed-probe")
    prior = SimpleNamespace(
        consecutive_failures=1,
        cooldown_until_epoch=0.0,
        last_detail="clean_close",
        updated_at_epoch=clock.time() - 20.0,
        admission_generation=0,
    )
    deadlines = {"elapsed": clock.time() - 1.0, "absent": 0.0, "negative": -1.0, "future": clock.time() + 10.0}
    merged = SimpleNamespace(
        consecutive_failures=failures,
        cooldown_until_epoch=deadlines[deadline],
        last_detail="clean_close",
        updated_at_epoch=clock.time() - 2.0,
        admission_generation=0,
    )
    lookup = AsyncMock(return_value=prior)
    persist = AsyncMock(return_value=merged)
    service._durable_bridge = SimpleNamespace(lookup_retry_circuit=lookup, persist_retry_circuit=persist)
    assert await service._load_http_bridge_retry_circuit(session)
    state = cast(Any, service)._http_bridge_retry_circuits[session.key]
    await service._persist_http_bridge_retry_circuit(session, state)
    persist.assert_awaited_once()
    assert state.consecutive_failures == failures
    assert state.persisted_updated_at_epoch == merged.updated_at_epoch
    assert state.cooldown_until == (clock.monotonic() + 10.0 if deadline == "future" else 0.0)
    lookup.return_value = merged
    claims: list[list[tuple[float, object, int]]] = [[], [], []]
    admitted = await asyncio.wait_for(
        asyncio.gather(
            *(
                service._http_bridge_precreated_retry_allowed(session, probe_owner=object(), claimed_lease_out=claim)
                for claim in claims
            )
        ),
        timeout=2,
    )
    expected_probes = int(deadline == "elapsed" and failures == 2)
    expected_admissions = 0 if deadline == "future" else 1 if expected_probes else 3
    assert sum(admitted) == expected_admissions
    assert sum(len(claim) for claim in claims) == expected_probes
    if expected_probes:
        lease = next(claim[0] for claim in claims if claim)
        assert state.half_open_until == lease[0]
        assert state.half_open_owner_token is lease[1]
        assert state.half_open_lease_generation == lease[2]
        assert not state.elapsed_durable_cooldown_pending
        # The same persist result cannot rearm the transition already consumed
        # by this probe or replace its lease.
        await service._persist_http_bridge_retry_circuit(session, state)
        assert not state.elapsed_durable_cooldown_pending
        assert state.half_open_until == lease[0]
        assert state.half_open_owner_token is lease[1]
        assert state.half_open_lease_generation == lease[2]
        assert not await service._http_bridge_precreated_retry_allowed(session, probe_owner=object())
