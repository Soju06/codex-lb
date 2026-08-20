from __future__ import annotations

import ast
import asyncio
import inspect
import time
from collections import deque
from typing import Any, cast

import anyio
import pytest
import uvicorn

from app.core import server as core_server
from app.core import shutdown as shutdown_state
from app.core.config.settings import Settings
from app.modules.proxy import service as proxy_service

pytestmark = pytest.mark.unit


async def _noop_app(scope: Any, receive: Any, send: Any) -> None:  # pragma: no cover
    del scope, receive, send


def _config() -> uvicorn.Config:
    return uvicorn.Config(_noop_app, timeout_graceful_shutdown=30)


@pytest.fixture(autouse=True)
def _reset_shutdown_state() -> Any:
    shutdown_state.reset()
    yield
    shutdown_state.reset()


# ---------------------------------------------------------------- hazard_13


@pytest.mark.asyncio
async def test_h13_drain_completes_before_super_shutdown_is_created() -> None:
    """Order proof: wait_for_in_flight_drain must finish before sockets close."""

    order: list[str] = []
    real_wait = shutdown_state.wait_for_in_flight_drain

    async def traced_wait(*args: Any, **kwargs: Any) -> bool:
        order.append("drain-start")
        result = await real_wait(*args, **kwargs)
        order.append("drain-end")
        return result

    server = core_server.GracefulDrainServer(_config(), drain_timeout_seconds=1.0)

    async def fake_super_shutdown(self: Any, sockets: Any = None) -> None:
        order.append("close-sockets")

    shutdown_state.increment_in_flight()

    async def release_later() -> None:
        await asyncio.sleep(0.2)
        shutdown_state.decrement_in_flight()

    releaser = asyncio.create_task(release_later())

    orig = uvicorn.Server.shutdown
    uvicorn.Server.shutdown = fake_super_shutdown  # type: ignore[assignment]
    orig_wait = shutdown_state.wait_for_in_flight_drain
    shutdown_state.wait_for_in_flight_drain = cast(Any, traced_wait)
    try:
        await server.shutdown()
    finally:
        uvicorn.Server.shutdown = orig  # type: ignore[assignment]
        shutdown_state.wait_for_in_flight_drain = orig_wait  # type: ignore[assignment]
        await releaser

    assert order == ["drain-start", "drain-end", "close-sockets"], order


@pytest.mark.asyncio
async def test_h13_drain_wait_is_bounded_by_the_shared_deadline() -> None:
    shutdown_state.commit_shutdown(timeout_seconds=0.3)
    shutdown_state.increment_in_flight()  # never released
    started = time.monotonic()
    drained = await shutdown_state.wait_for_in_flight_drain(timeout_seconds=1000.0)
    elapsed = time.monotonic() - started
    assert drained is False
    assert elapsed < 1.0, elapsed


@pytest.mark.asyncio
async def test_h13_work_admitted_after_drain_barrier_is_not_awaited_by_the_drain() -> None:
    """A drain-allowed path can register in-flight work after the drain returned."""

    shutdown_state.commit_shutdown(timeout_seconds=5.0)
    assert await shutdown_state.wait_for_in_flight_drain(timeout_seconds=5.0) is True
    # InFlightMiddleware admits /internal/bridge/responses while draining
    # (app/core/middleware/inflight.py:14 + :81) and counts it (:79).
    shutdown_state.increment_in_flight()
    assert shutdown_state.get_in_flight() == 1


def test_h13_inflight_middleware_admission_gate_paths() -> None:
    from app.core.middleware import inflight

    assert "/internal/bridge/responses" in inflight._DRAIN_ALLOWED_HTTP_PATHS
    assert "/internal/bridge/responses" not in inflight._IN_FLIGHT_EXCLUDED_HTTP_PATHS
    assert inflight._IN_FLIGHT_WEBSOCKET_PATHS == frozenset({"/backend-api/codex/responses", "/v1/responses"})


# ---------------------------------------------------------------- hazard_14


def _lifespan_persistence_drain_budgets() -> list[str]:
    """Return the timeout expression each nested persistence drain is given.

    Source inspection rather than a runtime probe: the hazard is a call site
    silently swapped to a different deadline helper, which every mocked
    shutdown run would still pass.
    """

    source = inspect.getsource(__import__("app.main", fromlist=["lifespan"]).lifespan)
    module = ast.parse(source)
    assignments: dict[str, str] = {}
    budgets: list[str] = []
    for node in ast.walk(module):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assignments[node.targets[0].id] = ast.unparse(node.value)
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "_drain_proxy_persistence_tasks":
            continue
        budget = ast.unparse(node.args[1])
        budgets.append(assignments.get(budget, budget))
    return budgets


def test_h14_nested_lifespan_cleanup_uses_remaining_drain_budget() -> None:
    """Nested lifespan cleanup must not receive the full configured drain.

    Each nested drain is pinned to its own deadline. The settlement pre-drain
    runs after the drain barrier, so a drain that used its whole budget would
    leave it nothing to settle with; it draws on the shared drain-plus-reserve
    remainder instead. The post-teardown drain runs inside the drain window and
    uses the drain remainder.
    """

    reserve = core_server.POST_DRAIN_CLEANUP_TIMEOUT_SECONDS
    assert reserve == 25.0
    assert _lifespan_persistence_drain_budgets() == [
        "shutdown_state.remaining_post_drain_cleanup_timeout_seconds() or 0.0",
        "shutdown_state.remaining_drain_timeout_seconds() or 0.0",
    ]
    source = inspect.getsource(__import__("app.main", fromlist=["lifespan"]).lifespan)
    assert "settings.shutdown_drain_timeout_seconds" not in source.split("finally:")[-1]


@pytest.mark.parametrize("value", [0, -5])
def test_h14_settings_reject_non_positive_drain_timeouts(value: int) -> None:
    with pytest.raises(ValueError):
        Settings(shutdown_drain_timeout_seconds=value)


def test_h14_settings_rejects_absurd_drain_timeout() -> None:
    with pytest.raises(ValueError):
        Settings(shutdown_drain_timeout_seconds=86400)


@pytest.mark.asyncio
async def test_h17_terminal_settlement_gets_post_drain_reserve_grace() -> None:
    """Exhausted drain still leaves the terminal settlement its reserve."""

    service = proxy_service.ProxyService(cast(Any, lambda: None))
    release = asyncio.Event()
    finalize_started = asyncio.Event()
    wait_timeout: list[float] = []
    real_wait = asyncio.wait

    async def blocked_finalize(*_args: Any, **_kwargs: Any) -> None:
        finalize_started.set()
        await release.wait()

    async def traced_wait(fs: Any, *, timeout: float | None = None, **kwargs: Any) -> Any:
        wait_timeout.append(float(timeout or 0.0))
        return await real_wait(fs, timeout=0, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(service, "_finalize_claimed_websocket_requests", blocked_finalize)
    monkeypatch.setattr(asyncio, "wait", traced_wait)
    shutdown_state.commit_shutdown(timeout_seconds=0.0)
    shutdown_state.set_post_drain_cleanup_timeout_seconds(core_server.POST_DRAIN_CLEANUP_TIMEOUT_SECONDS)
    caller = asyncio.create_task(
        service._fail_pending_websocket_requests(
            account=None,
            account_id_value=None,
            pending_requests=cast(Any, deque([object()])),
            pending_lock=anyio.Lock(),
            error_code="cancelled",
            error_message="cancelled",
            api_key=None,
        )
    )
    await asyncio.wait_for(finalize_started.wait(), timeout=1)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    release.set()
    monkeypatch.undo()
    assert wait_timeout and wait_timeout[0] > 0
    assert wait_timeout[0] <= core_server.POST_DRAIN_CLEANUP_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_h14_post_drain_wait_expires_at_deadline_plus_reserve() -> None:
    server = core_server.GracefulDrainServer(
        _config(),
        drain_timeout_seconds=0.4,
        post_drain_cleanup_timeout_seconds=0.3,
    )
    captured: dict[str, float] = {}
    real_wait = asyncio.wait

    async def traced_wait(fs: Any, *, timeout: float | None = None, **kw: Any) -> Any:
        captured["timeout"] = float(timeout or 0.0)
        return await real_wait(fs, timeout=timeout, **kw)

    async def fake_super_shutdown(self: Any, sockets: Any = None) -> None:
        return None

    orig = uvicorn.Server.shutdown
    uvicorn.Server.shutdown = fake_super_shutdown  # type: ignore[assignment]
    asyncio.wait = cast(Any, traced_wait)
    try:
        await server.shutdown()
    finally:
        uvicorn.Server.shutdown = orig  # type: ignore[assignment]
        asyncio.wait = real_wait  # type: ignore[assignment]

    # remaining (<= 0.4) + reserve (0.3)
    assert captured["timeout"] <= 0.4 + 0.3 + 1e-6, captured
    assert captured["timeout"] > 0.3, captured
