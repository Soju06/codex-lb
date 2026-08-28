from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from importlib import import_module

import pytest

from app.core.shutdown import wait_for_tasks_to_drain
from app.main import (
    InFlightMiddleware,
    _drain_detached_control_plane_tasks,
    _drain_proxy_persistence_tasks,
    _release_leader_lease_within,
)

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
async def test_lifespan_recovery_settlement_pre_drain_uses_remaining_deadline() -> None:
    calls: list[dict[str, object]] = []

    class _ProxyService:
        async def drain_persistence_tasks(self, **kwargs: object) -> bool:
            calls.append(kwargs)
            return True

    assert await _drain_proxy_persistence_tasks(
        _ProxyService(),
        3.25,
        task_name_prefixes=("http-bridge-recovery-settlement-",),
        failure_message="unused",
    )

    assert calls == [
        {
            "timeout_seconds": 3.25,
            "task_name_prefixes": ("http-bridge-recovery-settlement-",),
        }
    ]


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
def reset_shutdown_state() -> Iterator[None]:
    shutdown_state.reset()
    yield
    shutdown_state.reset()


def test_set_draining_updates_shutdown_state() -> None:
    shutdown_state.set_draining(True)

    assert shutdown_state._draining is True


def test_begin_drain_reuses_one_process_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(shutdown_state.time, "monotonic", lambda: clock[0])

    first_deadline = shutdown_state.begin_drain(timeout_seconds=30)
    clock[0] = 110.0
    repeated_deadline = shutdown_state.begin_drain(timeout_seconds=60)

    assert first_deadline == 130.0
    assert repeated_deadline == first_deadline
    assert shutdown_state.remaining_drain_timeout_seconds() == 20.0
    assert shutdown_state.is_draining() is True
    assert shutdown_state.is_bridge_drain_active() is True


def test_commit_shutdown_uses_hook_deadline_and_sigterm_does_not_extend_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(shutdown_state.time, "monotonic", lambda: clock[0])

    hook_deadline = shutdown_state.commit_shutdown(
        timeout_seconds=30,
        deadline_monotonic=110,
    )
    clock[0] = 102.0
    committed_deadline = shutdown_state.commit_shutdown(timeout_seconds=30)

    assert hook_deadline == 110
    assert committed_deadline == hook_deadline
    assert shutdown_state.remaining_drain_timeout_seconds() == 8


def test_commit_shutdown_publishes_earlier_candidate_than_operator_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutdown_state.time, "monotonic", lambda: 100.0)
    operator_deadline = shutdown_state.begin_drain(timeout_seconds=60)

    committed_deadline = shutdown_state.commit_shutdown(
        timeout_seconds=30,
        deadline_monotonic=130,
    )

    assert operator_deadline == 160
    assert committed_deadline == 130
    assert shutdown_state.is_shutdown_committed() is True
    assert shutdown_state.stop_drain() is False
    assert shutdown_state.remaining_drain_timeout_seconds() == 30


def test_repeated_commit_reuses_first_committed_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(shutdown_state.time, "monotonic", lambda: clock[0])
    first_deadline = shutdown_state.commit_shutdown(
        timeout_seconds=30,
        deadline_monotonic=130,
    )

    clock[0] = 105.0
    repeated_deadline = shutdown_state.commit_shutdown(timeout_seconds=1)

    assert first_deadline == 130
    assert repeated_deadline == first_deadline
    assert shutdown_state.remaining_drain_timeout_seconds() == 25


def test_late_hook_deadline_tightens_signal_commit_anchored_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [102.0]
    monkeypatch.setattr(shutdown_state.time, "monotonic", lambda: clock[0])
    signal_deadline = shutdown_state.commit_shutdown(timeout_seconds=30)

    clock[0] = 104.0
    hook_deadline = shutdown_state.commit_shutdown(
        timeout_seconds=30,
        deadline_monotonic=110,
    )

    assert signal_deadline == 132
    assert hook_deadline == 110
    assert shutdown_state.remaining_drain_timeout_seconds() == 6


def test_deadline_commit_reentered_by_signal_keeps_earliest_committed_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal_deadlines: list[float] = []
    signal_delivered = False

    def signal_reentrant_monotonic() -> float:
        nonlocal signal_delivered
        if not signal_delivered:
            signal_delivered = True
            signal_deadlines.append(shutdown_state.commit_shutdown(timeout_seconds=30))
        return 100.0

    monkeypatch.setattr(shutdown_state.time, "monotonic", signal_reentrant_monotonic)

    hook_deadline = shutdown_state.commit_shutdown(
        timeout_seconds=30,
        deadline_monotonic=105,
    )
    later_signal_deadline = shutdown_state.commit_shutdown(timeout_seconds=1)

    assert signal_deadlines == [130]
    assert hook_deadline == 105
    assert later_signal_deadline == hook_deadline
    assert shutdown_state.remaining_drain_timeout_seconds() == 5


@pytest.mark.parametrize("reenter_before_outer_publish", [False, True])
def test_deadline_commit_reentrancy_cannot_overwrite_an_earlier_candidate(
    monkeypatch: pytest.MonkeyPatch,
    reenter_before_outer_publish: bool,
) -> None:
    clock = [100.0]
    signal_deadlines: list[float] = []

    class _SignalReentrantCandidates(list[float]):
        def __init__(self) -> None:
            super().__init__()
            self._armed = True
            self._reentrant = False

        def append(self, value: float) -> None:
            if not reenter_before_outer_publish:
                super().append(value)
            if self._armed and not self._reentrant:
                self._armed = False
                self._reentrant = True
                try:
                    signal_deadlines.append(shutdown_state.commit_shutdown(timeout_seconds=1))
                finally:
                    self._reentrant = False
            if reenter_before_outer_publish:
                super().append(value)

    monkeypatch.setattr(shutdown_state.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        shutdown_state,
        "_shutdown_deadline_candidates",
        _SignalReentrantCandidates(),
    )

    hook_deadline = shutdown_state.commit_shutdown(
        timeout_seconds=100,
        deadline_monotonic=200,
    )

    assert signal_deadlines == [101]
    assert hook_deadline == 101
    assert shutdown_state.remaining_drain_timeout_seconds() == 1


@pytest.mark.parametrize(
    ("outer_timeout", "signal_timeout", "expected_deadline"),
    [
        (5.0, 30.0, 105.0),
        (30.0, 5.0, 105.0),
    ],
)
def test_begin_drain_signal_reentrancy_keeps_earliest_deadline(
    monkeypatch: pytest.MonkeyPatch,
    outer_timeout: float,
    signal_timeout: float,
    expected_deadline: float,
) -> None:
    signal_deadlines: list[float] = []
    signal_delivered = False

    def signal_reentrant_monotonic() -> float:
        nonlocal signal_delivered
        if not signal_delivered:
            signal_delivered = True
            signal_deadlines.append(shutdown_state.commit_shutdown(signal_timeout))
        return 100.0

    monkeypatch.setattr(shutdown_state.time, "monotonic", signal_reentrant_monotonic)

    outer_deadline = shutdown_state.begin_drain(outer_timeout)

    assert signal_deadlines
    assert outer_deadline == expected_deadline
    assert shutdown_state.commit_shutdown(60.0) == expected_deadline
    assert shutdown_state.remaining_drain_timeout_seconds() == 5.0


def test_begin_drain_clamps_hook_deadline_to_configured_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutdown_state.time, "monotonic", lambda: 100.0)

    deadline = shutdown_state.begin_drain(
        timeout_seconds=30,
        deadline_monotonic=1_000,
    )

    assert deadline == 130


@pytest.mark.parametrize("deadline", [float("inf"), float("-inf"), float("nan")])
def test_begin_drain_rejects_non_finite_hook_deadline(
    monkeypatch: pytest.MonkeyPatch,
    deadline: float,
) -> None:
    monkeypatch.setattr(shutdown_state.time, "monotonic", lambda: 100.0)

    with pytest.raises(ValueError, match="finite"):
        shutdown_state.begin_drain(
            timeout_seconds=30,
            deadline_monotonic=deadline,
        )


@pytest.mark.parametrize("deadline", [float("inf"), float("-inf"), float("nan")])
def test_explicit_deadline_is_validated_after_drain_already_started(
    monkeypatch: pytest.MonkeyPatch,
    deadline: float,
) -> None:
    monkeypatch.setattr(shutdown_state.time, "monotonic", lambda: 100.0)
    original_deadline = shutdown_state.begin_drain(timeout_seconds=30)

    with pytest.raises(ValueError, match="finite"):
        shutdown_state.begin_drain(
            timeout_seconds=30,
            deadline_monotonic=deadline,
        )

    assert shutdown_state.remaining_drain_timeout_seconds() == original_deadline - 100


def test_stopping_drain_clears_process_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutdown_state.time, "monotonic", lambda: 100.0)
    shutdown_state.begin_drain(timeout_seconds=30)

    stopped = shutdown_state.stop_drain()

    assert stopped is True
    assert shutdown_state.remaining_drain_timeout_seconds() is None
    assert shutdown_state.is_draining() is False


def test_committed_shutdown_cannot_reopen_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutdown_state.time, "monotonic", lambda: 100.0)
    operator_deadline = shutdown_state.begin_drain(timeout_seconds=30)

    committed_deadline = shutdown_state.commit_shutdown(timeout_seconds=60)
    stopped = shutdown_state.stop_drain()

    assert committed_deadline == operator_deadline
    assert stopped is False
    assert shutdown_state.is_shutdown_committed() is True
    assert shutdown_state.is_draining() is True
    assert shutdown_state.remaining_drain_timeout_seconds() == 30.0


def test_stop_drain_reentrant_signal_preserves_earliest_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(shutdown_state.time, "monotonic", lambda: clock[0])
    operator_deadline = shutdown_state.begin_drain(timeout_seconds=30)
    signal_deadlines: list[float] = []

    class _CommitDuringFirstShutdownCheck:
        def __init__(self) -> None:
            self._armed = True
            self._reentrant = False

        def __bool__(self) -> bool:
            if self._armed and not self._reentrant:
                self._armed = False
                self._reentrant = True
                try:
                    signal_deadlines.append(shutdown_state.commit_shutdown(timeout_seconds=30))
                finally:
                    self._reentrant = False
            return False

    clock[0] = 102.0
    monkeypatch.setattr(
        shutdown_state,
        "_shutdown_committed",
        _CommitDuringFirstShutdownCheck(),
    )

    stopped = shutdown_state.stop_drain()

    assert signal_deadlines == [operator_deadline]
    assert stopped is False
    assert shutdown_state.is_shutdown_committed() is True
    assert shutdown_state.is_draining() is True
    assert shutdown_state.remaining_drain_timeout_seconds() == 28


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
async def test_wait_for_in_flight_drain_uses_shared_deadline_without_poll_overshoot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    sleeps: list[float] = []
    monkeypatch.setattr(shutdown_state.time, "monotonic", lambda: clock[0])

    async def advance_clock(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(shutdown_state.asyncio, "sleep", advance_clock)
    shutdown_state.increment_in_flight()
    shutdown_state.begin_drain(timeout_seconds=0.05)

    drained = await shutdown_state.wait_for_in_flight_drain(
        timeout_seconds=10,
        poll_interval_seconds=1,
    )

    assert drained is False
    assert sleeps == [pytest.approx(0.05)]
    assert clock[0] == pytest.approx(100.05)


@pytest.mark.asyncio
async def test_wait_for_in_flight_drain_observes_late_earlier_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    sleeps: list[float] = []
    monkeypatch.setattr(shutdown_state.time, "monotonic", lambda: clock[0])

    async def advance_clock(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds
        if len(sleeps) == 1:
            shutdown_state.commit_shutdown(
                timeout_seconds=30,
                deadline_monotonic=clock[0] + 0.005,
            )

    monkeypatch.setattr(shutdown_state.asyncio, "sleep", advance_clock)
    shutdown_state.increment_in_flight()
    shutdown_state.commit_shutdown(timeout_seconds=30)

    drained = await shutdown_state.wait_for_in_flight_drain(
        timeout_seconds=30,
        poll_interval_seconds=0.01,
    )

    assert drained is False
    assert sleeps == [pytest.approx(0.01), pytest.approx(0.005)]
    assert clock[0] == pytest.approx(100.015)


@pytest.mark.asyncio
async def test_wait_for_in_flight_drain_aborts_without_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shutdown_state.increment_in_flight()

    async def fail_sleep(_seconds: float) -> None:
        pytest.fail("forced shutdown must not sleep again")

    monkeypatch.setattr(shutdown_state.asyncio, "sleep", fail_sleep)

    drained = await shutdown_state.wait_for_in_flight_drain(
        timeout_seconds=30,
        should_abort=lambda: True,
    )

    assert drained is False


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
async def test_in_flight_middleware_checks_http_drain_after_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_called = False

    async def inner_app(scope, receive, send):  # noqa: ANN001, ARG001
        nonlocal app_called
        app_called = True

    def observe_drain_after_registration() -> bool:
        assert shutdown_state.get_in_flight() == 1
        return True

    monkeypatch.setattr(shutdown_state, "is_draining", observe_drain_after_registration)
    middleware = InFlightMiddleware(inner_app)
    sent_messages: list[dict] = []

    async def receive():  # noqa: ANN202
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):  # noqa: ANN001, ANN202
        sent_messages.append(msg)

    await middleware(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/responses",
            "raw_path": b"/v1/responses",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )

    assert app_called is False
    assert sent_messages[0]["type"] == "http.response.start"
    assert sent_messages[0]["status"] == 503
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
async def test_in_flight_middleware_tracks_websocket_connections() -> None:
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

    assert in_flight_during_ws == 1
    assert shutdown_state.get_in_flight() == 0


@pytest.mark.asyncio
async def test_in_flight_middleware_checks_websocket_drain_after_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_called = False

    async def inner_app(scope, receive, send):  # noqa: ANN001, ARG001
        nonlocal app_called
        app_called = True

    def observe_drain_after_registration() -> bool:
        assert shutdown_state.get_in_flight() == 1
        return True

    monkeypatch.setattr(shutdown_state, "is_draining", observe_drain_after_registration)
    middleware = InFlightMiddleware(inner_app)
    sent_messages: list[dict] = []

    async def ws_receive():  # noqa: ANN202
        return {"type": "websocket.connect"}

    async def ws_send(msg):  # noqa: ANN001, ANN202
        sent_messages.append(msg)

    await middleware({"type": "websocket", "path": "/v1/responses"}, ws_receive, ws_send)

    assert app_called is False
    assert sent_messages == [{"type": "websocket.close", "code": 1013, "reason": "Server is draining"}]
    assert shutdown_state.get_in_flight() == 0


@pytest.mark.asyncio
async def test_in_flight_middleware_does_not_hold_non_responses_websocket_open() -> None:
    in_flight_during_ws: int | None = None

    async def inner_app(scope, receive, send):  # noqa: ANN001, ARG001
        nonlocal in_flight_during_ws
        in_flight_during_ws = shutdown_state.get_in_flight()

    middleware = InFlightMiddleware(inner_app)

    async def ws_receive():  # noqa: ANN202
        return {"type": "websocket.connect"}

    async def ws_send(msg):  # noqa: ANN001, ANN202
        pass

    await middleware({"type": "websocket", "path": "/v1/live/call_123"}, ws_receive, ws_send)

    assert in_flight_during_ws == 0
    assert shutdown_state.get_in_flight() == 0


@pytest.mark.asyncio
async def test_in_flight_middleware_rejects_new_websocket_during_drain() -> None:
    shutdown_state.set_draining(True)
    app_called = False

    async def inner_app(scope, receive, send):  # noqa: ANN001, ARG001
        nonlocal app_called
        app_called = True

    middleware = InFlightMiddleware(inner_app)

    async def ws_receive():  # noqa: ANN202
        return {"type": "websocket.connect"}

    sent_messages: list[dict] = []

    async def ws_send(msg):  # noqa: ANN001, ANN202
        sent_messages.append(msg)

    await middleware({"type": "websocket", "path": "/v1/responses"}, ws_receive, ws_send)

    assert app_called is False
    assert sent_messages == [{"type": "websocket.close", "code": 1013, "reason": "Server is draining"}]
    assert shutdown_state.get_in_flight() == 0


@pytest.mark.asyncio
async def test_websocket_admitted_before_drain_remains_in_flight_until_handler_exits() -> None:
    handler_started = asyncio.Event()
    release_handler = asyncio.Event()

    async def inner_app(scope, receive, send):  # noqa: ANN001, ARG001
        handler_started.set()
        await release_handler.wait()

    middleware = InFlightMiddleware(inner_app)

    async def ws_receive():  # noqa: ANN202
        return {"type": "websocket.connect"}

    async def ws_send(msg):  # noqa: ANN001, ANN202
        pass

    scope_task = asyncio.create_task(middleware({"type": "websocket", "path": "/v1/responses"}, ws_receive, ws_send))
    await handler_started.wait()
    shutdown_state.set_draining(True)

    assert shutdown_state.get_in_flight() == 1
    assert await shutdown_state.wait_for_in_flight_drain(timeout_seconds=0) is False

    release_handler.set()
    await scope_task
    assert shutdown_state.get_in_flight() == 0


@pytest.mark.asyncio
async def test_cancelled_websocket_scope_releases_in_flight_count() -> None:
    handler_started = asyncio.Event()

    async def inner_app(scope, receive, send):  # noqa: ANN001, ARG001
        handler_started.set()
        await asyncio.Event().wait()

    middleware = InFlightMiddleware(inner_app)

    async def ws_receive():  # noqa: ANN202
        return {"type": "websocket.connect"}

    async def ws_send(msg):  # noqa: ANN001, ANN202
        pass

    scope_task = asyncio.create_task(middleware({"type": "websocket", "path": "/v1/responses"}, ws_receive, ws_send))
    await handler_started.wait()
    assert shutdown_state.get_in_flight() == 1

    scope_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await scope_task

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
