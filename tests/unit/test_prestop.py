from __future__ import annotations

from collections.abc import Callable

import pytest

from app.core.prestop import DrainStatus, LocalDrainClient, run_prestop

pytestmark = pytest.mark.unit


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class _DrainClient:
    def __init__(
        self,
        status: Callable[[], DrainStatus],
        *,
        start_error: Exception | None = None,
        status_error: Exception | None = None,
        start_action: Callable[[], None] | None = None,
        status_action: Callable[[], None] | None = None,
        effective_deadline: float | None = None,
    ) -> None:
        self._status = status
        self._start_error = start_error
        self._status_error = status_error
        self._start_action = start_action
        self._status_action = status_action
        self._effective_deadline = effective_deadline
        self.start_deadlines: list[float] = []
        self.start_timeouts: list[float] = []
        self.status_timeouts: list[float] = []

    def start_drain(self, *, deadline_monotonic: float, timeout_seconds: float) -> float:
        self.start_deadlines.append(deadline_monotonic)
        self.start_timeouts.append(timeout_seconds)
        if self._start_action is not None:
            self._start_action()
        if self._start_error is not None:
            raise self._start_error
        if self._effective_deadline is not None:
            return self._effective_deadline
        return deadline_monotonic

    def get_status(self, *, timeout_seconds: float) -> DrainStatus:
        self.status_timeouts.append(timeout_seconds)
        if self._status_action is not None:
            self._status_action()
        if self._status_error is not None:
            raise self._status_error
        return self._status()


def test_prestop_waits_for_routing_dwell_before_zero_in_flight_exit() -> None:
    clock = _Clock()
    client = _DrainClient(lambda: DrainStatus(draining=True, in_flight=0))

    drained = run_prestop(
        client=client,
        routing_dwell_seconds=3,
        drain_timeout_seconds=10,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        poll_interval_seconds=1,
    )

    assert drained is True
    assert clock.now == 3
    assert client.start_deadlines == [10]
    assert client.start_timeouts == [2]
    assert all(0 < timeout <= 2 for timeout in client.status_timeouts)


def test_prestop_start_latency_consumes_shared_dwell_and_deadline() -> None:
    clock = _Clock()
    client = _DrainClient(
        lambda: DrainStatus(draining=True, in_flight=0),
        start_action=lambda: setattr(clock, "now", clock.now + 2),
    )

    drained = run_prestop(
        client=client,
        routing_dwell_seconds=3,
        drain_timeout_seconds=10,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        poll_interval_seconds=1,
    )

    assert drained is True
    assert client.start_deadlines == [10]
    assert clock.now == 3
    assert clock.sleeps == [1]


def test_local_drain_client_sends_hook_deadline_header() -> None:
    captured: dict[str, object] = {}

    class _Response:
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return (
                b'{"status":"ok","checks":{"draining":"true","shutdown_committed":"true","deadline_monotonic":"123.5"}}'
            )

    def urlopen(request: object, *, timeout: float) -> _Response:
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response()

    client = LocalDrainClient("http://127.0.0.1:2455", urlopen=urlopen)
    effective_deadline = client.start_drain(deadline_monotonic=123.5, timeout_seconds=2)

    request = captured["request"]
    assert getattr(request, "get_header")("X-codex-lb-drain-deadline-monotonic") == "123.5"
    assert captured["timeout"] == 2
    assert effective_deadline == 123.5


@pytest.mark.parametrize(
    "payload",
    [
        b"{}",
        b'{"status":"ok","checks":{"draining":"false","shutdown_committed":"true","deadline_monotonic":"5"}}',
        b'{"status":"ok","checks":{"draining":"true","shutdown_committed":"false","deadline_monotonic":"5"}}',
        b'{"status":"ok","checks":{"draining":"true","shutdown_committed":"true","deadline_monotonic":"nan"}}',
    ],
)
def test_local_drain_client_rejects_invalid_start_contract(payload: bytes) -> None:
    class _Response:
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return payload

    def urlopen(_request: object, *, timeout: float) -> _Response:
        assert timeout == 2
        return _Response()

    client = LocalDrainClient("http://127.0.0.1:2455", urlopen=urlopen)

    with pytest.raises(ValueError, match="drain start response"):
        client.start_drain(deadline_monotonic=5, timeout_seconds=2)


def test_prestop_waits_for_in_flight_after_routing_dwell() -> None:
    clock = _Clock()

    def status() -> DrainStatus:
        return DrainStatus(draining=True, in_flight=0 if clock.now >= 4 else 2)

    client = _DrainClient(status)

    drained = run_prestop(
        client=client,
        routing_dwell_seconds=2,
        drain_timeout_seconds=10,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        poll_interval_seconds=1,
    )

    assert drained is True
    assert clock.now == 4


def test_prestop_active_work_is_bounded_by_shared_deadline() -> None:
    clock = _Clock()
    client = _DrainClient(lambda: DrainStatus(draining=True, in_flight=1))

    drained = run_prestop(
        client=client,
        routing_dwell_seconds=2,
        drain_timeout_seconds=5,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        poll_interval_seconds=1,
    )

    assert drained is False
    assert clock.now == 5
    assert sum(clock.sleeps) == 5


def test_prestop_reuses_earlier_effective_application_deadline() -> None:
    clock = _Clock()
    client = _DrainClient(
        lambda: DrainStatus(draining=True, in_flight=1),
        effective_deadline=5,
    )

    drained = run_prestop(
        client=client,
        routing_dwell_seconds=2,
        drain_timeout_seconds=9,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        poll_interval_seconds=1,
    )

    assert drained is False
    assert client.start_deadlines == [9]
    assert clock.now == 5
    assert sum(clock.sleeps) == 5


def test_prestop_does_not_outlive_effective_deadline_before_routing_dwell() -> None:
    clock = _Clock()
    client = _DrainClient(
        lambda: DrainStatus(draining=True, in_flight=0),
        effective_deadline=3,
    )

    drained = run_prestop(
        client=client,
        routing_dwell_seconds=5,
        drain_timeout_seconds=10,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        poll_interval_seconds=1,
    )

    assert drained is False
    assert clock.now == 3


def test_prestop_rejects_zero_in_flight_status_returned_after_deadline() -> None:
    clock = _Clock()
    client = _DrainClient(
        lambda: DrainStatus(draining=True, in_flight=0),
        status_action=lambda: setattr(clock, "now", 3),
    )

    drained = run_prestop(
        client=client,
        routing_dwell_seconds=0,
        drain_timeout_seconds=2,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert drained is False
    assert client.status_timeouts == [2]


def test_prestop_start_failure_exits_without_waiting() -> None:
    clock = _Clock()
    client = _DrainClient(
        lambda: DrainStatus(draining=True, in_flight=0),
        start_error=OSError("unavailable"),
    )

    with pytest.raises(OSError, match="unavailable"):
        run_prestop(
            client=client,
            routing_dwell_seconds=3,
            drain_timeout_seconds=10,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    assert clock.sleeps == []
    assert client.status_timeouts == []


def test_prestop_status_failure_exits_without_blind_wait() -> None:
    clock = _Clock()
    client = _DrainClient(
        lambda: DrainStatus(draining=True, in_flight=0),
        status_error=OSError("unavailable"),
    )

    with pytest.raises(OSError, match="unavailable"):
        run_prestop(
            client=client,
            routing_dwell_seconds=3,
            drain_timeout_seconds=10,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    assert clock.sleeps == []


def test_local_drain_client_rejects_non_ok_status_contract() -> None:
    class _Response:
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"status":"error","checks":{"draining":"true","in_flight":"0"}}'

    def urlopen(_request: object, *, timeout: float) -> _Response:
        assert timeout == 2
        return _Response()

    client = LocalDrainClient("http://127.0.0.1:2455", urlopen=urlopen)

    with pytest.raises(ValueError, match="drain status response must be an ok object"):
        client.get_status(timeout_seconds=2)


def test_prestop_fails_fast_if_status_reports_reopened_admission() -> None:
    clock = _Clock()
    client = _DrainClient(lambda: DrainStatus(draining=False, in_flight=0))

    with pytest.raises(ValueError, match="not draining"):
        run_prestop(
            client=client,
            routing_dwell_seconds=3,
            drain_timeout_seconds=10,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    assert clock.sleeps == []


@pytest.mark.parametrize(
    ("routing_dwell_seconds", "drain_timeout_seconds"),
    [
        (-1, 10),
        (11, 10),
        (0, 0),
    ],
)
def test_prestop_rejects_invalid_timeout_contract(
    routing_dwell_seconds: float,
    drain_timeout_seconds: float,
) -> None:
    client = _DrainClient(lambda: DrainStatus(draining=True, in_flight=0))

    with pytest.raises(ValueError):
        run_prestop(
            client=client,
            routing_dwell_seconds=routing_dwell_seconds,
            drain_timeout_seconds=drain_timeout_seconds,
        )
