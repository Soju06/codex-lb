from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.modules.proxy import api
from app.modules.proxy._service import support

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("service_owns", [False, True])
async def test_chat_bridge_cancellation_preserves_settlement_owner(monkeypatch, service_owns):
    release = AsyncMock()
    monkeypatch.setattr(api, "_release_reservation_best_effort", release)
    started, closed = asyncio.Event(), asyncio.Event()

    async def upstream():
        if service_owns:
            support._signal_propagated_responses_service_cleanup_ready()
        try:
            started.set()
            await asyncio.Future()
            yield "unreachable"
        finally:
            closed.set()

    guarded = api._guard_chat_bridge_reservation(upstream(), reservation=None, service=object())

    async def next_item():
        return await anext(guarded)

    probe = asyncio.create_task(next_item())
    await asyncio.wait_for(started.wait(), timeout=2)
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe
    assert closed.is_set()
    assert release.await_count == (0 if service_owns else 1)


@pytest.mark.asyncio
async def test_chat_bridge_task_handoff_closes_underlying_stream_and_restores_context(monkeypatch):
    release = AsyncMock()
    monkeypatch.setattr(api, "_release_reservation_best_effort", release)
    closed = asyncio.Event()
    outer_signal = asyncio.Event()

    async def upstream():
        try:
            yield "created"
            yield "delta"
        finally:
            # Service can transfer cleanup during close as well as startup.
            support._signal_propagated_responses_service_cleanup_ready()
            closed.set()

    outer_token = support._bind_propagated_responses_service_cleanup_ready(outer_signal)
    try:
        guarded = api._guard_chat_bridge_reservation(upstream(), reservation=None, service=object())

        async def next_item():
            return await anext(guarded)

        assert await asyncio.create_task(next_item()) == "created"
        assert await asyncio.create_task(next_item()) == "delta"
        await api._aclose_stream(guarded)
        assert closed.is_set()
        release.assert_not_awaited()
        assert not outer_signal.is_set()
        support._signal_propagated_responses_service_cleanup_ready()
        assert outer_signal.is_set()
    finally:
        support._reset_propagated_responses_service_cleanup_ready(outer_token)
