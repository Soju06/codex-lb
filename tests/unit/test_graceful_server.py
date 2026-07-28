from __future__ import annotations

import asyncio
import signal

import pytest
import uvicorn

from app.core import shutdown as shutdown_state
from app.core.server import GracefulDrainServer, SignalNeutralServer

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_shutdown_state() -> None:
    shutdown_state.reset()


def _server(
    *,
    drain_timeout_seconds: float,
    post_drain_cleanup_timeout_seconds: float = 30,
) -> GracefulDrainServer:
    config = uvicorn.Config("app.main:app", timeout_graceful_shutdown=None)
    return GracefulDrainServer(
        config,
        drain_timeout_seconds=drain_timeout_seconds,
        post_drain_cleanup_timeout_seconds=post_drain_cleanup_timeout_seconds,
    )


def test_signal_starts_drain_before_uvicorn_sets_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def commit_shutdown(timeout_seconds: float) -> float:
        events.append(f"drain:{timeout_seconds:g}")
        return 30.0

    def base_handle_exit(
        server: uvicorn.Server,
        sig: int,
        _frame: object | None,
    ) -> None:
        assert sig == signal.SIGTERM
        events.append("uvicorn-exit")
        server.should_exit = True

    monkeypatch.setattr(shutdown_state, "commit_shutdown", commit_shutdown)
    monkeypatch.setattr(uvicorn.Server, "handle_exit", base_handle_exit)
    server = _server(drain_timeout_seconds=30)

    server.handle_exit(signal.SIGTERM, None)

    assert events == ["drain:30", "uvicorn-exit"]
    assert server.should_exit is True


@pytest.mark.asyncio
async def test_serve_resets_shutdown_state_before_uvicorn_signal_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[bool, bool, float | None]] = []

    async def base_serve(
        _server: uvicorn.Server,
        sockets: list[object] | None = None,
    ) -> None:
        del sockets
        observed.append(
            (
                shutdown_state.is_shutdown_committed(),
                shutdown_state.is_draining(),
                shutdown_state.remaining_drain_timeout_seconds(),
            )
        )

    monkeypatch.setattr(uvicorn.Server, "serve", base_serve)
    shutdown_state.commit_shutdown(timeout_seconds=30)
    server = _server(drain_timeout_seconds=30)

    await server.serve()

    assert observed == [(False, False, None)]


@pytest.mark.asyncio
async def test_lifespan_startup_does_not_erase_signal_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.startup as startup_state
    import app.main as main_module

    class StartupProbe(Exception):
        pass

    observed: list[tuple[bool, bool, float | None]] = []

    class _SettingsCacheProbe:
        async def invalidate(self, *, propagate: bool) -> None:
            assert propagate is False
            observed.append(
                (
                    shutdown_state.is_shutdown_committed(),
                    shutdown_state.is_draining(),
                    shutdown_state.remaining_drain_timeout_seconds(),
                )
            )
            raise StartupProbe

    original_startup_complete = startup_state._startup_complete
    monkeypatch.setattr(main_module, "get_settings_cache", lambda: _SettingsCacheProbe())
    monkeypatch.setattr(startup_state, "reset_bridge_registration", lambda: None)
    server = _server(drain_timeout_seconds=30)
    server.handle_exit(signal.SIGTERM, None)

    try:
        with pytest.raises(StartupProbe):
            async with main_module.lifespan(main_module.app):
                pytest.fail("lifespan reached startup yield")
    finally:
        startup_state._startup_complete = original_startup_complete

    assert len(observed) == 1
    committed, draining, remaining = observed[0]
    assert committed is True
    assert draining is True
    assert remaining is not None
    assert 0 < remaining <= 30


def test_completed_embedded_lifespan_allows_next_lifecycle_to_start_clean() -> None:
    shutdown_state.commit_shutdown(timeout_seconds=30)
    shutdown_state.mark_lifespan_completed()

    shutdown_state.prepare_lifespan_start()

    assert shutdown_state.is_shutdown_committed() is False
    assert shutdown_state.is_draining() is False
    assert shutdown_state.remaining_drain_timeout_seconds() is None


def test_server_prepared_lifespan_preserves_committed_signal_latch() -> None:
    shutdown_state.prepare_server_start()
    shutdown_state.commit_shutdown(timeout_seconds=30)

    shutdown_state.prepare_lifespan_start()

    assert shutdown_state.is_shutdown_committed() is True
    assert shutdown_state.is_draining() is True
    remaining = shutdown_state.remaining_drain_timeout_seconds()
    assert remaining is not None
    assert 0 < remaining <= 30


@pytest.mark.asyncio
async def test_shutdown_waits_for_in_flight_before_uvicorn_closes_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_called = asyncio.Event()

    async def base_shutdown(
        server: uvicorn.Server,
        sockets: list[object] | None = None,
    ) -> None:
        del sockets
        assert shutdown_state.get_in_flight() == 0
        assert server.config.timeout_graceful_shutdown is not None
        assert 0 <= server.config.timeout_graceful_shutdown <= 5
        base_called.set()

    monkeypatch.setattr(uvicorn.Server, "shutdown", base_shutdown)
    shutdown_state.increment_in_flight()
    server = _server(drain_timeout_seconds=5)

    shutdown_task = asyncio.create_task(server.shutdown())
    await asyncio.sleep(0)

    assert shutdown_state.is_draining() is True
    assert base_called.is_set() is False

    shutdown_state.decrement_in_flight()
    await asyncio.wait_for(shutdown_task, timeout=1)

    assert base_called.is_set() is True
    assert server.config.timeout_graceful_shutdown is None


@pytest.mark.asyncio
async def test_shutdown_deadline_bounds_uvicorn_task_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_timeout: int | None = None

    async def base_shutdown(
        server: uvicorn.Server,
        sockets: list[object] | None = None,
    ) -> None:
        nonlocal observed_timeout
        del sockets
        observed_timeout = server.config.timeout_graceful_shutdown

    monkeypatch.setattr(uvicorn.Server, "shutdown", base_shutdown)
    shutdown_state.increment_in_flight()
    server = _server(drain_timeout_seconds=0)

    await server.shutdown()

    assert observed_timeout == 0
    assert shutdown_state.get_in_flight() == 1


@pytest.mark.asyncio
async def test_shutdown_preserves_subsecond_uvicorn_task_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_timeout: float | None = None
    observed_shutdown_wait_timeout: float | None = None

    async def base_shutdown(
        server: uvicorn.Server,
        sockets: list[object] | None = None,
    ) -> None:
        nonlocal observed_timeout
        del sockets
        observed_timeout = server.config.timeout_graceful_shutdown

    original_wait = asyncio.wait

    async def recording_wait(
        tasks: set[asyncio.Task[None]],
        *,
        timeout: float,
    ) -> tuple[set[asyncio.Task[None]], set[asyncio.Task[None]]]:
        nonlocal observed_shutdown_wait_timeout
        observed_shutdown_wait_timeout = timeout
        return await original_wait(tasks, timeout=timeout)

    monkeypatch.setattr(uvicorn.Server, "shutdown", base_shutdown)
    monkeypatch.setattr(shutdown_state, "remaining_drain_timeout_seconds", lambda: 0.625)
    monkeypatch.setattr("app.core.server.asyncio.wait", recording_wait)
    server = _server(
        drain_timeout_seconds=5,
        post_drain_cleanup_timeout_seconds=0.25,
    )

    await server.shutdown()

    assert observed_timeout == pytest.approx(0.625)
    assert observed_shutdown_wait_timeout == pytest.approx(0.875)


@pytest.mark.parametrize(
    ("captured_signal", "expected_exit_signal"),
    [
        (None, signal.SIGTERM),
        (signal.SIGINT, signal.SIGINT),
    ],
)
@pytest.mark.asyncio
async def test_shutdown_forces_process_exit_when_uvicorn_cleanup_absorbs_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    captured_signal: int | None,
    expected_exit_signal: int,
) -> None:
    class ForcedProcessExit(Exception):
        pass

    base_started = asyncio.Event()
    base_cancelled = asyncio.Event()
    release_base = asyncio.Event()
    forced_exit_signals: list[int] = []
    warnings: list[str] = []

    async def blocked_base_shutdown(
        _server: uvicorn.Server,
        sockets: list[object] | None = None,
    ) -> None:
        del sockets
        base_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            base_cancelled.set()
            await release_base.wait()

    def force_process_exit(signum: int) -> None:
        forced_exit_signals.append(signum)
        raise ForcedProcessExit

    monkeypatch.setattr(uvicorn.Server, "shutdown", blocked_base_shutdown)
    monkeypatch.setattr("app.core.server._force_process_exit", force_process_exit)
    monkeypatch.setattr(
        "app.core.server.logger.warning",
        lambda message, *args: warnings.append(message % args),
    )
    server = _server(
        drain_timeout_seconds=0,
        post_drain_cleanup_timeout_seconds=0.01,
    )
    if captured_signal is not None:
        server._captured_signals.append(captured_signal)

    with pytest.raises(ForcedProcessExit):
        await asyncio.wait_for(server.shutdown(), timeout=1)
    await asyncio.wait_for(base_cancelled.wait(), timeout=1)
    shutdown_tasks = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and task.get_name() == "uvicorn-post-drain-shutdown"
    ]
    assert len(shutdown_tasks) == 1
    assert shutdown_tasks[0].done() is False

    release_base.set()
    await asyncio.wait_for(shutdown_tasks[0], timeout=1)
    await asyncio.sleep(0)

    assert base_started.is_set()
    assert forced_exit_signals == [expected_exit_signal]
    assert any("forcing process exit" in warning for warning in warnings)
    assert server.config.timeout_graceful_shutdown is None


@pytest.mark.asyncio
async def test_shutdown_cancellation_propagates_before_connection_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_called = False

    async def base_shutdown(
        _server: uvicorn.Server,
        sockets: list[object] | None = None,
    ) -> None:
        nonlocal base_called
        del sockets
        base_called = True

    monkeypatch.setattr(uvicorn.Server, "shutdown", base_shutdown)
    shutdown_state.increment_in_flight()
    server = _server(drain_timeout_seconds=30)
    shutdown_task = asyncio.create_task(server.shutdown())
    await asyncio.sleep(0)

    shutdown_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await shutdown_task

    assert base_called is False


def test_signal_neutral_server_does_not_install_process_signal_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed_signals: list[int] = []
    monkeypatch.setattr(signal, "signal", lambda sig, handler: installed_signals.append(sig))
    server = SignalNeutralServer(uvicorn.Config("app.main:app"))

    with server.capture_signals():
        pass

    assert installed_signals == []
