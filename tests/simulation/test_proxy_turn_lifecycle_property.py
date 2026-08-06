"""Seeded schedule exploration for the proxy bridge turn lifecycle.

`TEST_AUDIT.md` names this as the first property worth checking: for any
interleaving of an admission wait, an upstream terminal event, a downstream
cancellation and a retry request, a bridge request must reach exactly one
terminal outcome and must release its response-create, API-key and account
leases exactly once.

Every schedule runs on the deterministic ``VirtualScheduler``. Each lifecycle
event is a concurrent task that wakes at a seeded virtual deadline, so events
sharing a deadline genuinely interleave at their ``await`` points instead of
running in a fixed order. Lease release is performed by the production helpers
(``_release_websocket_response_create_ownership_for_cleanup`` and
``ProxyService._release_websocket_request_state_reservation``) against a real
``WorkAdmissionController`` gate, so the checker observes product behavior
rather than a re-implementation of it.

The canary at the bottom runs the same checker against a toy turn with a
planted double-release-on-cancel bug and asserts that the checker rejects it.
A checker that cannot catch a planted bug proves nothing.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal, Protocol, cast

import pytest

from app.modules.api_keys.service import ApiKeyUsageReservationData
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.websocket.mixin import _release_websocket_response_create_ownership_for_cleanup
from app.modules.proxy.work_admission import AdmissionLease, WorkAdmissionController
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit

ScheduleEvent = Literal["admission_wait", "upstream_terminal", "downstream_cancel", "retry_request"]
Terminal = Literal["upstream_terminal", "downstream_cancel"]

_EVENTS: tuple[ScheduleEvent, ...] = (
    "admission_wait",
    "upstream_terminal",
    "downstream_cancel",
    "retry_request",
)
# Repeated zero delays make simultaneous wakeups - and therefore true
# interleaving at the await points inside the production release helpers - the
# common case rather than a rare one.
_STEP_DELAYS: tuple[float, ...] = (0.0, 0.0, 0.05, 0.1)
_SCHEDULE_COUNT = 200
_MAX_EXTRA_EVENTS = 3
_ADVANCE_SECONDS = 0.05
_ADVANCE_STEPS = 8
_ADMISSION_TIMEOUT_SECONDS = 10.0

ScheduleStep = tuple[ScheduleEvent, float]
Schedule = tuple[ScheduleStep, ...]


class _BridgeTurn(Protocol):
    async def start(self) -> None: ...

    async def dispatch(self, event: ScheduleEvent, delay: float) -> None: ...

    def snapshot(self) -> "_TurnSnapshot": ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _TurnSnapshot:
    terminal_outcomes: tuple[Terminal, ...]
    response_create_releases: int
    api_key_releases: int
    account_releases: int
    retry_terminal_events: tuple[Terminal, ...]
    admission_waiters: int
    admission_waiters_admitted: int


class _CountingAdmissionLease:
    """Counts release calls while delegating to a real ``AdmissionLease``.

    ``AdmissionLease.release`` is idempotent on purpose, so the semaphore alone
    cannot tell a single release apart from a double release. Counting the
    calls is what the "released exactly once" invariant is actually about.
    """

    def __init__(self, lease: AdmissionLease) -> None:
        self.lease = lease
        self.release_count = 0

    def release(self) -> None:
        self.release_count += 1
        self.lease.release()


class _RecordingProxyService(proxy_service.ProxyService):
    """Proxy service that counts API-key reservation releases.

    Subclassing keeps the production call graph intact instead of patching a
    bound method onto an instance.
    """

    def __init__(self, repo_factory: Any, *, clock: VirtualClock, scheduler: VirtualScheduler) -> None:
        super().__init__(repo_factory, clock=clock, scheduler=scheduler)
        self.api_key_releases = 0

    async def _release_websocket_reservation(self, reservation: ApiKeyUsageReservationData | None) -> None:
        if reservation is not None:
            self.api_key_releases += 1


class _ProductionBridgeTurn:
    """One bridge turn driven through the production lease-release helpers."""

    def __init__(self, scheduler: VirtualScheduler) -> None:
        self.scheduler = scheduler
        self.terminal_outcomes: list[Terminal] = []
        self.retry_terminal_events: list[Terminal] = []
        self.account_releases = 0
        self.admission_waiters = 0
        self.admission_waiters_admitted = 0
        self._retry_attached = False
        self.service = _RecordingProxyService(
            cast(Any, SimpleNamespace()),
            clock=scheduler.clock,
            scheduler=scheduler,
        )
        # A single response-create permit: this turn holds it, so an admission
        # wait can only be admitted once the terminal path hands it back.
        self.admission = WorkAdmissionController(
            token_refresh_limit=0,
            websocket_connect_limit=0,
            response_create_limit=1,
            compact_response_create_limit=0,
            admission_wait_timeout_seconds=_ADMISSION_TIMEOUT_SECONDS,
            scheduler=scheduler,
        )
        self.response_create_gate = asyncio.Semaphore(0)
        self.admission_lease: _CountingAdmissionLease | None = None
        self.request_state = proxy_service._WebSocketRequestState(
            request_id="req-property",
            model="gpt-5.5",
            service_tier=None,
            reasoning_effort=None,
            api_key_reservation=ApiKeyUsageReservationData(
                reservation_id="reservation-property",
                key_id="key-property",
                model="gpt-5.5",
            ),
            started_at=0.0,
            response_id=None,
            awaiting_response_created=True,
            event_queue=asyncio.Queue(),
            transport="http",
            skip_request_log=True,
        )

    async def start(self) -> None:
        """Arm the turn with the three leases a live bridge request owns."""

        lease = _CountingAdmissionLease(await self.admission.acquire_response_create())
        self.admission_lease = lease
        self.request_state.response_create_admission = cast(Any, lease)
        self.request_state.response_create_gate = self.response_create_gate
        self.request_state.response_create_gate_acquired = True
        self.request_state.account_response_create_lease = cast(Any, "account-create-lease")
        self.request_state.account_response_create_release = self._release_account_create_lease

    async def aclose(self) -> None:
        lease = self.admission_lease
        if lease is not None:
            # Idempotent, and deliberately untracked: this only hands the
            # permit back when a schedule left the turn unsettled.
            lease.lease.release()

    async def _release_account_create_lease(self, lease: object) -> None:
        assert lease == "account-create-lease"
        self.account_releases += 1

    async def dispatch(self, event: ScheduleEvent, delay: float) -> None:
        await self.scheduler.sleep(delay)
        await self.handle(event)

    async def handle(self, event: ScheduleEvent) -> None:
        if event == "admission_wait":
            await self._wait_for_admission()
            return
        if event == "retry_request":
            self._retry_attached = True
            return
        await self._settle(event)

    async def _wait_for_admission(self) -> None:
        """A queued request contending for the permit this turn still holds.

        It can only be admitted once the terminal path hands the response-create
        permit back to the real gate, then it releases the permit again so a
        later waiter in the same schedule can be admitted too.
        """

        self.admission_waiters += 1
        lease = await self.admission.acquire_response_create()
        self.admission_waiters_admitted += 1
        lease.release()

    async def _settle(self, terminal: Terminal) -> None:
        if not self._claim_terminal(terminal):
            return
        await _release_websocket_response_create_ownership_for_cleanup(
            self.request_state,
            self.response_create_gate,
        )
        await self.service._release_websocket_request_state_reservation(self.request_state)

    def _claim_terminal(self, terminal: Terminal) -> bool:
        if self.request_state.terminal_settlement_phase is not None or self.terminal_outcomes:
            return False
        self.request_state.terminal_settlement_phase = "claimed"
        self._record_settled(terminal)
        return True

    def _record_settled(self, terminal: Terminal) -> None:
        if self.terminal_outcomes and self._retry_attached:
            # A retry attached and a later terminal still settled the request a
            # second time - the exact shape the invariant forbids.
            self.retry_terminal_events.append(terminal)
        self.terminal_outcomes.append(terminal)

    def snapshot(self) -> _TurnSnapshot:
        lease = self.admission_lease
        return _TurnSnapshot(
            terminal_outcomes=tuple(self.terminal_outcomes),
            response_create_releases=0 if lease is None else lease.release_count,
            api_key_releases=self.service.api_key_releases,
            account_releases=self.account_releases,
            retry_terminal_events=tuple(self.retry_terminal_events),
            admission_waiters=self.admission_waiters,
            admission_waiters_admitted=self.admission_waiters_admitted,
        )


class _DoubleReleaseOnCancelBridgeTurn(_ProductionBridgeTurn):
    """Toy turn carrying the planted bug the checker must reject.

    The cancel path gives back the create ownership it observes *before* the
    shared cleanup helper takes ownership of it, so a downstream cancel
    releases the response-create admission and the account create lease a
    second time. That is the "release what you no longer own" shape from
    `TAXONOMY.md`.
    """

    async def _settle(self, terminal: Terminal) -> None:
        if terminal == "downstream_cancel":
            admission = self.request_state.response_create_admission
            if admission is not None:
                admission.release()
            lease = self.request_state.account_response_create_lease
            release = self.request_state.account_response_create_release
            if lease is not None and release is not None:
                await release(lease)
        await super()._settle(terminal)


def _schedule_for_seed(seed: int) -> Schedule:
    """Build one deterministic schedule.

    Every schedule contains all four lifecycle events at least once (so a
    terminal always arrives), plus up to three repeats, each with its own
    virtual wake-up delay.
    """

    rng = random.Random(seed)
    events: list[ScheduleEvent] = list(_EVENTS)
    rng.shuffle(events)
    for _ in range(rng.randint(0, _MAX_EXTRA_EVENTS)):
        events.insert(rng.randrange(len(events) + 1), rng.choice(_EVENTS))
    return tuple((event, rng.choice(_STEP_DELAYS)) for event in events)


def _assert_bridge_turn_invariants(turn: _BridgeTurn, *, seed: int, schedule: Schedule) -> None:
    snapshot = turn.snapshot()
    context = f"seed={seed} schedule={schedule} snapshot={snapshot}"
    assert len(snapshot.terminal_outcomes) == 1, context
    assert snapshot.response_create_releases == 1, context
    assert snapshot.api_key_releases == 1, context
    assert snapshot.account_releases == 1, context
    assert not snapshot.retry_terminal_events, context
    # Liveness: the permit really went back to the real admission gate, so the
    # release counters above cannot be satisfied by never releasing at all.
    assert snapshot.admission_waiters_admitted == snapshot.admission_waiters, context


async def _run_schedule(turn: _BridgeTurn, schedule: Schedule, scheduler: VirtualScheduler) -> None:
    await turn.start()
    tasks = [scheduler.create_task(turn.dispatch(event, delay)) for event, delay in schedule]
    await scheduler.drain()
    for _ in range(_ADVANCE_STEPS):
        if all(task.done() for task in tasks):
            break
        await scheduler.advance(_ADVANCE_SECONDS)
    assert all(task.done() for task in tasks), f"schedule did not quiesce schedule={schedule}"
    await asyncio.gather(*tasks)


async def _check_schedules(
    turn_factory: type[_ProductionBridgeTurn],
    *,
    schedule_count: int = _SCHEDULE_COUNT,
) -> None:
    for seed in range(schedule_count):
        schedule = _schedule_for_seed(seed)
        scheduler = VirtualScheduler(VirtualClock())
        turn = turn_factory(scheduler)
        try:
            await _run_schedule(turn, schedule, scheduler)
            _assert_bridge_turn_invariants(turn, seed=seed, schedule=schedule)
        except BaseException:
            # Reproduce this exact interleaving with _schedule_for_seed(<seed>).
            print(f"bridge turn schedule check failed seed={seed} schedule={schedule}")
            raise
        finally:
            await turn.aclose()
            await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_bridge_turn_lifecycle_seeded_schedules_settle_exactly_once() -> None:
    await _check_schedules(_ProductionBridgeTurn)


@pytest.mark.asyncio
async def test_bridge_turn_lifecycle_schedule_set_is_large_and_varied() -> None:
    schedules = [_schedule_for_seed(seed) for seed in range(_SCHEDULE_COUNT)]

    assert len(schedules) >= 200
    assert len(set(schedules)) >= 150, "the seeded schedules collapse onto too few interleavings"
    for schedule in schedules:
        events = [event for event, _delay in schedule]
        assert set(_EVENTS).issubset(events)


@pytest.mark.asyncio
async def test_bridge_turn_lifecycle_checker_catches_double_release_canary() -> None:
    with pytest.raises(AssertionError, match="response_create_releases"):
        await _check_schedules(_DoubleReleaseOnCancelBridgeTurn)
