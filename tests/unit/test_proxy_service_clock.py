from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.modules.proxy import service as proxy_service
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit


def _service(
    clock: VirtualClock | None = None,
    scheduler: VirtualScheduler | None = None,
) -> proxy_service.ProxyService:
    if clock is None:
        return proxy_service.ProxyService(cast(Any, SimpleNamespace()))
    return proxy_service.ProxyService(
        cast(Any, SimpleNamespace()),
        clock=clock,
        scheduler=scheduler or VirtualScheduler(clock),
    )


def test_remaining_budget_uses_injected_virtual_clock() -> None:
    clock = VirtualClock(monotonic_value=10.0)
    service = _service(clock)

    assert service._remaining_budget_seconds(15.0) == 5.0

    clock.advance(2.0)

    assert service._remaining_budget_seconds(15.0) == 3.0


def test_remaining_budget_keeps_real_clock_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 100.0
    monkeypatch.setattr(time, "monotonic", lambda: now)
    service = _service()

    assert service._remaining_budget_seconds(107.5) == 7.5


@pytest.mark.asyncio
async def test_thread_goal_refresh_and_upstream_budget_use_virtual_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = VirtualClock()
    service = _service(clock)
    account = SimpleNamespace(
        id="account-virtual-clock",
        access_token_encrypted=b"encrypted",
        chatgpt_account_id="upstream-account",
    )
    settings = SimpleNamespace(
        openai_cache_affinity_max_age_seconds=3600,
        prefer_earlier_reset_accounts=False,
    )
    refresh = AsyncMock(return_value=account)
    select = AsyncMock(return_value=proxy_service.AccountSelection(cast(Any, account), None))
    upstream = AsyncMock(return_value={"goal": {"id": "goal-virtual-clock"}})

    class SettingsCache:
        async def get(self) -> object:
            return settings

    monkeypatch.setattr(proxy_service, "get_settings", lambda: SimpleNamespace(proxy_request_budget_seconds=30.0))
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: SettingsCache())
    monkeypatch.setattr(service, "_select_account_with_budget_compatible", select)
    monkeypatch.setattr(service, "_ensure_fresh", refresh)
    monkeypatch.setattr(service, "_resolve_upstream_route_for_account", AsyncMock(return_value=None))
    monkeypatch.setattr(service._encryptor, "decrypt", lambda _encrypted: "access-token")
    monkeypatch.setattr(service._load_balancer, "record_success", AsyncMock())
    monkeypatch.setattr(service, "_write_request_log", AsyncMock())
    monkeypatch.setattr(proxy_service, "core_thread_goal_request", upstream)

    response = await service.thread_goal_request("get", {}, {})

    refresh_call = refresh.await_args
    upstream_call = upstream.await_args

    assert response == {"goal": {"id": "goal-virtual-clock"}}
    assert refresh_call is not None
    assert refresh_call.kwargs["timeout_seconds"] == 30.0
    assert upstream_call is not None
    assert upstream_call.kwargs["timeout_seconds"] == 30.0


@pytest.mark.asyncio
async def test_account_selection_timeout_uses_virtual_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    service = _service(clock, scheduler)
    selection_started = asyncio.Event()
    selection_never_finishes = asyncio.Event()

    class SettingsCache:
        async def get(self) -> object:
            return proxy_service.get_settings()

    async def blocked_select_account(**_kwargs: object) -> object:
        selection_started.set()
        await selection_never_finishes.wait()
        raise AssertionError("blocked selection unexpectedly resumed")

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: SettingsCache())
    monkeypatch.setattr(service._load_balancer, "select_account", blocked_select_account)

    selection_task = scheduler.create_task(
        service._select_account_with_budget(
            deadline=clock.monotonic() + 30.0,
            request_id="req-virtual-selection-timeout",
            kind="thread_goal_get",
        )
    )
    await scheduler.drain()
    assert selection_started.is_set()

    await scheduler.advance(30.0)

    assert selection_task.done()
    with pytest.raises(proxy_service.ProxyResponseError) as exc_info:
        await selection_task
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_account_selection_keeps_real_scheduler_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    expected_selection = proxy_service.AccountSelection(None, "No active accounts available")
    select_account = AsyncMock(return_value=expected_selection)

    class SettingsCache:
        async def get(self) -> object:
            return proxy_service.get_settings()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: SettingsCache())
    monkeypatch.setattr(service._load_balancer, "select_account", select_account)

    selection = await service._select_account_with_budget(
        deadline=service._clock.monotonic() + 30.0,
        request_id="req-real-selection-timeout",
        kind="thread_goal_get",
    )

    assert selection is expected_selection
    select_account.assert_awaited_once()
