from __future__ import annotations

import asyncio
import logging
from importlib import import_module

import pytest

from app.core.shutdown import wait_for_tasks_to_drain
from app.main import InFlightMiddleware, _drain_detached_control_plane_tasks, _release_leader_lease_within

app_main = import_module("app.main")
shutdown_state = import_module("app.core.shutdown")

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_wait_for_tasks_to_drain_rechecks_tasks_added_by_done_callback() -> None:
    tasks: set[asyncio.Task[None]] = set()
    followup_started = asyncio.Event()
    allow_followup_finish = asyncio.Event()

    async def finish_immediately() -> None:
        return None

    async def followup() -> None:
        followup_started.set()
        await allow_followup_finish.wait()

    first = asyncio.create_task(finish_immediately())
    tasks.add(first)

    def spawn_followup(_: asyncio.Task[None]) -> None:
        task = asyncio.create_task(followup())
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    first.add_done_callback(spawn_followup)
    first.add_done_callback(tasks.discard)

    drain = asyncio.create_task(wait_for_tasks_to_drain(tasks, timeout_seconds=1))
    await asyncio.wait_for(followup_started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert not drain.done()

    allow_followup_finish.set()
    assert await drain == set()
    assert tasks == set()


@pytest.mark.asyncio
async def test_wait_for_tasks_to_drain_returns_pending_at_deadline() -> None:
    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait(), name="deadline-test-task")

    pending = await wait_for_tasks_to_drain({task}, timeout_seconds=0)

    assert pending == {task}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_wait_for_tasks_to_drain_resnapshots_registry_at_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_gate = asyncio.Event()
    late_gate = asyncio.Event()

    async def wait_for_gate(gate: asyncio.Event) -> None:
        await gate.wait()

    initial_task = asyncio.create_task(wait_for_gate(initial_gate), name="initial-task")
    tasks = {initial_task}
    late_task: asyncio.Task[None] | None = None

    async def add_late_task_then_timeout(
        pending: set[asyncio.Task[None]],
        *,
        timeout: float,
    ) -> tuple[set[asyncio.Task[None]], set[asyncio.Task[None]]]:
        nonlocal late_task
        assert timeout > 0
        late_task = asyncio.create_task(wait_for_gate(late_gate), name="late-task")
        tasks.add(late_task)
        return set(), pending

    monkeypatch.setattr(shutdown_state.asyncio, "wait", add_late_task_then_timeout)

    overdue = await wait_for_tasks_to_drain(tasks, timeout_seconds=1)

    assert late_task is not None
    assert overdue == {initial_task, late_task}

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_control_plane_drains_are_failure_isolated(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fleet_drained = asyncio.Event()

    async def fail_audit_drain(_: float) -> bool:
        raise RuntimeError("audit drain failed")

    async def drain_fleet(_: float) -> bool:
        fleet_drained.set()
        return True

    monkeypatch.setattr(app_main, "drain_audit_log_tasks", fail_audit_drain)
    monkeypatch.setattr(app_main.fleet_api, "drain_background_refresh_tasks", drain_fleet)

    with caplog.at_level(logging.WARNING, logger="app.main"):
        await _drain_detached_control_plane_tasks(1)

    assert fleet_drained.is_set()
    assert "Failed to drain audit log tasks during shutdown" in caplog.text


@pytest.mark.asyncio
async def test_control_plane_drain_requires_stable_clean_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_calls = 0
    fleet_calls = 0
    late_fleet_seen = False

    async def drain_audit(_: float) -> bool:
        nonlocal audit_calls
        audit_calls += 1
        return True

    async def drain_fleet(_: float) -> bool:
        nonlocal fleet_calls, late_fleet_seen
        fleet_calls += 1
        if fleet_calls == 1:
            await asyncio.sleep(0)
            return True
        late_fleet_seen = True
        return True

    monkeypatch.setattr(app_main, "drain_audit_log_tasks", drain_audit)
    monkeypatch.setattr(app_main.fleet_api, "drain_background_refresh_tasks", drain_fleet)

    await _drain_detached_control_plane_tasks(1)

    assert audit_calls == 2
    assert fleet_calls == 2
    assert late_fleet_seen is True


@pytest.mark.asyncio
async def test_release_leader_lease_within_returns_when_release_wedged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: release() uses a background DB session whose rollback/close
    # shield and await their own teardown, so a wedged DB call cannot be
    # unwound by cancellation. The shutdown release step must still return
    # within its deadline (abandoning the release) instead of hanging.
    class _WedgedElection:
        def __init__(self) -> None:
            self.gate = asyncio.Event()
            self.started = asyncio.Event()

        async def release(self) -> None:
            self.started.set()
            await self.gate.wait()

    election = _WedgedElection()
    monkeypatch.setattr(app_main, "get_leader_election", lambda: election)

    loop = asyncio.get_running_loop()
    start = loop.time()
    await _release_leader_lease_within(0.2)
    elapsed = loop.time() - start

    assert 0.2 <= elapsed < 1.0
    assert election.started.is_set()

    # Let the abandoned release finish so no task dangles past the test.
    election.gate.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_release_leader_lease_within_awaits_quick_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FastElection:
        def __init__(self) -> None:
            self.released = False

        async def release(self) -> None:
            self.released = True

    election = _FastElection()
    monkeypatch.setattr(app_main, "get_leader_election", lambda: election)

    await _release_leader_lease_within(5)

    assert election.released is True


@pytest.mark.asyncio
async def test_release_leader_lease_within_swallows_release_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BrokenElection:
        async def release(self) -> None:
            raise RuntimeError("db down")

    monkeypatch.setattr(app_main, "get_leader_election", lambda: _BrokenElection())

    # Must not raise: a failed release must never fail shutdown.
    await _release_leader_lease_within(5)


@pytest.fixture(autouse=True)
def reset_shutdown_state():  # noqa: ANN201
    shutdown_state.reset()
    yield
    shutdown_state.reset()


def test_set_draining_updates_shutdown_state() -> None:
    shutdown_state.set_draining(True)

    assert shutdown_state._draining is True


def test_reset_reopens_control_plane_task_admission() -> None:
    shutdown_state.close_control_plane_task_admission()

    shutdown_state.reset()

    assert shutdown_state.is_control_plane_task_admission_open() is True


@pytest.mark.asyncio
async def test_wait_for_in_flight_drain_waits_until_zero() -> None:
    shutdown_state.increment_in_flight()

    async def release_request() -> None:
        await asyncio.sleep(0.05)
        shutdown_state.decrement_in_flight()

    release_task = asyncio.create_task(release_request())

    drained = await shutdown_state.wait_for_in_flight_drain(timeout_seconds=1.0, poll_interval_seconds=0.01)

    await release_task
    assert drained is True
    assert shutdown_state.get_in_flight() == 0


@pytest.mark.asyncio
async def test_wait_for_in_flight_drain_respects_timeout() -> None:
    shutdown_state.increment_in_flight()

    drained = await shutdown_state.wait_for_in_flight_drain(timeout_seconds=0.05, poll_interval_seconds=0.01)

    assert drained is False
    assert shutdown_state.get_in_flight() == 1


@pytest.mark.asyncio
async def test_in_flight_middleware_increments_and_decrements() -> None:
    in_flight_during_app: int | None = None

    async def inner_app(scope, receive, send):  # noqa: ANN001, ARG001
        nonlocal in_flight_during_app
        in_flight_during_app = shutdown_state.get_in_flight()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok":true}'})

    middleware = InFlightMiddleware(inner_app)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def receive():  # noqa: ANN202
        return {"type": "http.request", "body": b"", "more_body": False}

    sent_messages: list[dict] = []

    async def send(msg):  # noqa: ANN001, ANN202
        sent_messages.append(msg)

    await middleware(scope, receive, send)

    assert in_flight_during_app == 1
    assert shutdown_state.get_in_flight() == 0


@pytest.mark.asyncio
async def test_in_flight_middleware_does_not_count_drain_status() -> None:
    in_flight_during_app: int | None = None

    async def inner_app(scope, receive, send):  # noqa: ANN001, ARG001
        nonlocal in_flight_during_app
        in_flight_during_app = shutdown_state.get_in_flight()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok":true}'})

    middleware = InFlightMiddleware(inner_app)
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/internal/drain/status",
        "raw_path": b"/internal/drain/status",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }

    async def receive():  # noqa: ANN202
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):  # noqa: ANN001, ANN202
        pass

    await middleware(scope, receive, send)

    assert in_flight_during_app == 0
    assert shutdown_state.get_in_flight() == 0


@pytest.mark.asyncio
async def test_in_flight_middleware_skips_websocket_connections() -> None:
    in_flight_during_ws: int | None = None

    async def inner_app(scope, receive, send):  # noqa: ANN001, ARG001
        nonlocal in_flight_during_ws
        in_flight_during_ws = shutdown_state.get_in_flight()

    middleware = InFlightMiddleware(inner_app)

    scope = {"type": "websocket", "path": "/v1/responses"}

    async def ws_receive():  # noqa: ANN202
        return {"type": "websocket.connect"}

    async def ws_send(msg):  # noqa: ANN001, ANN202
        pass

    await middleware(scope, ws_receive, ws_send)

    assert in_flight_during_ws == 0
    assert shutdown_state.get_in_flight() == 0


@pytest.mark.asyncio
async def test_in_flight_middleware_skips_lifespan() -> None:
    app_called = False

    async def inner_app(scope, receive, send):  # noqa: ANN001, ARG001
        nonlocal app_called
        app_called = True

    middleware = InFlightMiddleware(inner_app)

    async def ls_receive():  # noqa: ANN202
        return {}

    async def ls_send(msg):  # noqa: ANN001, ANN202
        pass

    await middleware({"type": "lifespan"}, ls_receive, ls_send)

    assert app_called is True
    assert shutdown_state.get_in_flight() == 0


@pytest.mark.asyncio
async def test_in_flight_middleware_allows_internal_bridge_handoff_during_drain() -> None:
    shutdown_state.set_draining(True)
    app_called = False

    async def inner_app(scope, receive, send):  # noqa: ANN001, ARG001
        nonlocal app_called
        app_called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok":true}'})

    middleware = InFlightMiddleware(inner_app)
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/internal/bridge/responses",
        "raw_path": b"/internal/bridge/responses",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }

    async def receive():  # noqa: ANN202
        return {"type": "http.request", "body": b"{}", "more_body": False}

    sent_messages: list[dict] = []

    async def send(msg):  # noqa: ANN001, ANN202
        sent_messages.append(msg)

    await middleware(scope, receive, send)

    assert app_called is True
    assert sent_messages[0]["status"] == 200


@pytest.mark.asyncio
async def test_in_flight_middleware_allows_drain_status_during_drain() -> None:
    shutdown_state.set_draining(True)
    app_called = False

    async def inner_app(scope, receive, send):  # noqa: ANN001, ARG001
        nonlocal app_called
        app_called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok":true}'})

    middleware = InFlightMiddleware(inner_app)
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/internal/drain/status",
        "raw_path": b"/internal/drain/status",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }

    async def receive():  # noqa: ANN202
        return {"type": "http.request", "body": b"", "more_body": False}

    sent_messages: list[dict] = []

    async def send(msg):  # noqa: ANN001, ANN202
        sent_messages.append(msg)

    await middleware(scope, receive, send)

    assert app_called is True
    assert sent_messages[0]["status"] == 200


@pytest.mark.asyncio
async def test_in_flight_middleware_allows_drain_stop_during_drain() -> None:
    shutdown_state.set_draining(True)
    app_called = False

    async def inner_app(scope, receive, send):  # noqa: ANN001, ARG001
        nonlocal app_called
        app_called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok":true}'})

    middleware = InFlightMiddleware(inner_app)
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/internal/drain/stop",
        "raw_path": b"/internal/drain/stop",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }

    async def receive():  # noqa: ANN202
        return {"type": "http.request", "body": b"", "more_body": False}

    sent_messages: list[dict] = []

    async def send(msg):  # noqa: ANN001, ANN202
        sent_messages.append(msg)

    await middleware(scope, receive, send)

    assert app_called is True
    assert sent_messages[0]["status"] == 200
