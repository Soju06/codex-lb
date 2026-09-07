"""Seeded schedule exploration for the proxy bridge turn lifecycle.

`TEST_AUDIT.md` names this as the first property worth checking: for any
interleaving of an admission wait, an upstream terminal event, a downstream
cancellation, a cancellation delivered into the in-flight settlement and a
retry request, a bridge request must reach exactly one terminal outcome and
must release its response-create, API-key and account leases exactly once.

Every schedule runs on the deterministic ``VirtualScheduler``. Each lifecycle
event is a concurrent task that wakes at a seeded virtual deadline, so events
sharing a deadline genuinely interleave at their ``await`` points instead of
running in a fixed order. The turn drives *production* settlement paths, not a
model of them:

* ``upstream_terminal`` feeds a ``response.completed`` frame through
  ``ProxyService._process_http_bridge_upstream_text``: the production terminal
  bookkeeping claims the request out of ``pending_requests`` under
  ``pending_lock``, records the claim (``terminal_settlement_phase``),
  publishes the frame and finalizes through
  ``_finalize_websocket_request_state`` and
  ``_release_websocket_response_create_gate``. The frame carries no response
  id on purpose: the anonymous-match claim leaves the request looking
  retryable (``response_id is None``, ``awaiting_response_created``) until
  finalization, which is exactly the window the retry ownership guard
  protects.
* ``downstream_cancel`` is the production detach backstop,
  ``_detach_http_bridge_request``, which settles a request still in pending
  ownership and must leave one already claimed by the terminal path alone.
* ``settlement_cancel`` is a real ``task.cancel()`` delivered into the
  terminal bookkeeping *after* the production claim, at a seeded virtual
  offset (zero, inside the account-lease release, inside the reservation
  write, after the settlement transfer) plus a seeded number of loop turns,
  so it lands on different awaits of the claim-to-finalize window and
  beyond it. Production then has to settle the claimed request through the
  shielded abort path (``_settle_aborted_http_bridge_terminal_states``,
  issue #1594), finish the deferred release it was in, or leave an already
  transferred settlement alone.
* ``retry_request`` runs ``_retry_http_bridge_precreated_request`` once the
  request has left pending ownership; the production ownership guard is what
  must reject it.

The API-key reservation is settled by whichever production path gets there
first (finalize, detach or abort); the recording service attributes each
settlement to its path so "exactly one terminal outcome" is checked against
what production actually did. The repository boundary is modelled the way
production has it: a reservation write suspends on a virtual timer (the DB
round trip it stands in for), the finalizer's settlement runs as a detached
scheduler-owned write like ``_settle_stream_api_key_usage``, and the first
write to reach a reservation id flips it while later writes are recorded as
*redundant*: production's ``status == "reserved"`` compare-and-set in
``ApiKeysService`` makes them no-ops. "Released exactly once" therefore means
exactly one effective flip. Pristine production does issue redundant release
calls in reachable detach/terminal/abort races (the detach decides under
``pending_lock``, awaits the gate release and only then reads the
reservation the terminal path may already be settling), so the redundant
count is reported in every snapshot and pinned by a known-failing test
rather than folded into the invariant. The modelled write latencies are
parametrized (``_LATENCY_PROFILES``) so the invariant is not an artifact of
one timer ordering. Lease releases go through the real
``WorkAdmissionController`` gate and the request state's own release
callbacks.

The canaries at the bottom plant production-shaped bugs (a detach that
releases ownership it does not hold, an abort path that never settles, a
dropped reservation release, a post-settlement retry that reacquires, and a
lease release that re-shields a pending task) and assert that the checker
rejects each one by the invariant it violates. A checker that cannot catch a
planted bug proves nothing.
"""

from __future__ import annotations

import asyncio
import contextvars
import random
from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal, Protocol, cast

import anyio
import pytest

from app.core.clients.proxy_websocket import UpstreamWebSocket
from app.core.utils.shared_future import wait_on_shared_future
from app.db.models import AccountStatus
from app.modules.api_keys.service import ApiKeyData, ApiKeyUsageReservationData
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.websocket.helpers import _release_websocket_response_create_gate
from app.modules.proxy.work_admission import AdmissionLease, WorkAdmissionController
from tests.simulation.test_anyio_lock_cancelled_waiter import (
    ANYIO_LOCK_RELEASE_SKIPS_CANCELLED_WAITERS,
    ANYIO_LOCK_WEDGE_REASON,
)
from tests.simulation.virtual_time import ABANDONED_SHIELD_ORACLE_SUPPORTED, VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit

ScheduleEvent = Literal[
    "admission_wait",
    "upstream_terminal",
    "downstream_cancel",
    "settlement_cancel",
    "retry_request",
]
SettlementPath = Literal["finalize", "detach", "abort", "unattributed"]

_EVENTS: tuple[ScheduleEvent, ...] = (
    "admission_wait",
    "upstream_terminal",
    "downstream_cancel",
    "settlement_cancel",
    "retry_request",
)
# Repeated zero delays make simultaneous wakeups - and therefore true
# interleaving at the await points inside the production settlement paths -
# the common case rather than a rare one.
_STEP_DELAYS: tuple[float, ...] = (0.0, 0.0, 0.05, 0.1)
# Loop turns a settlement cancel or a retry waits after the production claim
# before acting, so it lands on different awaits of the bookkeeping.
_SETTLEMENT_YIELDS: tuple[int, ...] = (0, 0, 1, 2, 3, 5, 8)
# Virtual seconds a settlement cancel waits after the claim before the loop
# turns above. Loop turns alone always land before the first modelled write
# completes, so these offsets reach the account-lease release (0..0.01 under
# the default latency), the reservation write (0.01..0.02) and the window after
# the settlement transfer (0.02+); the coverage test proves each is reached.
_SETTLEMENT_CANCEL_OFFSETS: tuple[float, ...] = (0.0, 0.0, 0.005, 0.015, 0.025)
_SCHEDULE_COUNT = 200
# Seeds (default latency, first 3000) whose injected reader cancellation races
# a ``pending_lock`` release in the same tick and wedges the lock on the pinned
# anyio 4.13 (see ``test_anyio_lock_cancelled_waiter.py``). Outside the default
# 200-seed run; pinned as a strict expected failure so a widened schedule count
# or a dependency bump reports the change instead of looking like harness rot.
_ANYIO_LOCK_WEDGE_SEEDS: tuple[int, ...] = (1234,)
_MAX_EXTRA_EVENTS = 3
_ADVANCE_SECONDS = 0.05
_ADVANCE_STEPS = 12
_ADMISSION_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class _ReleaseLatency:
    """Virtual durations of the modelled DB writes.

    ``reservation_write`` is the API-key reservation settlement/release round
    trip, ``account_lease_release`` the account create-lease release and
    ``health_write`` the load balancer's post-settlement success write. The
    writes suspend on a virtual timer so a settlement cancel can land inside
    them (and, through the health write, after the settlement transfer);
    several profiles are checked because which write finishes first decides
    which production path reads a still-set reservation.
    """

    reservation_write: float
    account_lease_release: float
    health_write: float = 0.01


_DEFAULT_LATENCY = _ReleaseLatency(reservation_write=0.01, account_lease_release=0.01)
_LATENCY_PROFILES: dict[str, _ReleaseLatency] = {
    "uniform-10ms": _DEFAULT_LATENCY,
    "uniform-30ms": _ReleaseLatency(reservation_write=0.03, account_lease_release=0.03, health_write=0.03),
    "reservation-slower": _ReleaseLatency(reservation_write=0.02, account_lease_release=0.01),
    "account-lease-slower": _ReleaseLatency(reservation_write=0.01, account_lease_release=0.02),
}
# A completed frame without a response id: the terminal claim goes through the
# anonymous match and the request stays retryable-looking until finalization.
_COMPLETED_TERMINAL_TEXT = '{"type":"response.completed","response":{"object":"response","status":"completed"}}'

# (event, virtual wake-up delay, post-claim loop turns, post-claim virtual offset)
ScheduleStep = tuple[ScheduleEvent, float, int, float]
Schedule = tuple[ScheduleStep, ...]
CancelLanding = Literal["before_settlement", "inside_reservation_write", "after_settlement"]

# Set by the recording service around each production settlement path so a
# reservation release can be attributed to the path that performed it. Tasks
# spawned inside a path inherit the value through their context copy.
_settlement_path: contextvars.ContextVar[SettlementPath] = contextvars.ContextVar(
    "settlement_path", default="unattributed"
)


class _BridgeTurn(Protocol):
    async def start(self) -> None: ...

    async def dispatch(self, event: ScheduleEvent, delay: float, yields: int, cancel_offset: float) -> None: ...

    def snapshot(self) -> "_TurnSnapshot": ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _TurnSnapshot:
    reservation_settlements: tuple[SettlementPath, ...]
    redundant_reservation_releases: tuple[SettlementPath, ...]
    interrupted_reservation_writes: int
    cancel_landings: tuple[CancelLanding, ...]
    finalizations: int
    abort_settlements: int
    response_create_releases: int
    account_releases: int
    retry_results: tuple[bool, ...]
    detach_results: tuple[bool, ...]
    terminal_attempts: int
    terminal_cancellations: int
    settlement_cancel_attempts: int
    admission_waiters: int
    admission_waiters_admitted: int
    request_pending: bool
    settlement_phase: str | None
    create_ownership_cleared: bool
    max_owned_task_callbacks: int
    abandoned_shield_callbacks: int


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
    """Proxy service whose DB boundaries record instead of writing.

    Subclassing keeps the production call graph intact instead of patching
    bound methods onto an instance. The overrides sit exactly at the repository
    boundary (reservation release / settlement, load-balancer health write,
    reconnect) and the settlement-path wrappers only tag the context so the
    reservation settlement can be attributed to the production path that
    performed it.

    The reservation boundary keeps production's shape: ``_release_websocket_reservation``
    awaits its write in the calling task (production's ``anyio`` shield does
    not stop a raw ``Task.cancel()``, so the write can be interrupted there),
    while ``_settle_stream_api_key_usage`` spawns the write as a detached
    scheduler-owned task and, when asked to wait, waits under a shield that
    absorbs the caller's cancellation until the write finishes, exactly as
    production does. Every completed write goes through ``_write_reservation``,
    the model of ``ApiKeysService``'s ``status == "reserved"`` compare-and-set:
    the first write per reservation id is the effective settlement, any later
    one is recorded as redundant.
    """

    def __init__(
        self,
        repo_factory: Any,
        *,
        clock: VirtualClock,
        scheduler: VirtualScheduler,
        work_admission: WorkAdmissionController,
        latency: _ReleaseLatency = _DEFAULT_LATENCY,
    ) -> None:
        super().__init__(repo_factory, clock=clock, scheduler=scheduler)
        self.latency = latency
        self.reservation_settlements: list[SettlementPath] = []
        self.redundant_reservation_releases: list[SettlementPath] = []
        self.interrupted_reservation_writes = 0
        self.reservation_writes_in_flight = 0
        self._settled_reservation_ids: set[str] = set()
        self.finalizations = 0
        self.abort_settlements = 0
        self.account_successes = 0
        self._retry_work_admission = work_admission
        self._load_balancer.record_success = self._record_account_success

    async def _record_account_success(self, account: Any) -> None:
        # The load balancer's success write is the finalizer's first suspension
        # after the settlement transfer; giving it a round trip is what lets a
        # cancellation land on the post-settlement guards.
        del account
        await self._scheduler.sleep(self.latency.health_write)
        self.account_successes += 1

    async def _write_reservation(self, reservation: ApiKeyUsageReservationData, path: SettlementPath) -> None:
        """One modelled DB round trip ending in the compare-and-set on the reservation row."""

        self.reservation_writes_in_flight += 1
        try:
            await self._scheduler.sleep(self.latency.reservation_write)
        except asyncio.CancelledError:
            self.interrupted_reservation_writes += 1
            raise
        finally:
            self.reservation_writes_in_flight -= 1
        if reservation.reservation_id in self._settled_reservation_ids:
            self.redundant_reservation_releases.append(path)
            return
        self._settled_reservation_ids.add(reservation.reservation_id)
        self.reservation_settlements.append(path)

    async def _release_websocket_reservation(self, reservation: ApiKeyUsageReservationData | None) -> None:
        if reservation is None:
            return
        await self._write_reservation(reservation, _settlement_path.get())

    async def _settle_stream_api_key_usage(
        self,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        settlement: Any,
        request_id: str,
        *,
        wait_for_settlement: bool = False,
    ) -> bool:
        del settlement
        if api_key is None or api_key_reservation is None:
            return True
        # Production detaches the settlement transaction into a tracked task
        # and, for ordering-sensitive callers, waits for it under a shield
        # that keeps waiting through the caller's own cancellation.
        task = self._scheduler.create_task(
            self._write_reservation(api_key_reservation, _settlement_path.get()),
            name=f"proxy-stream-api-key-settle-{request_id}",
        )
        if wait_for_settlement:
            with anyio.CancelScope(shield=True):
                while True:
                    try:
                        await wait_on_shared_future(task, scheduler=self._scheduler)
                        break
                    except asyncio.CancelledError:
                        if task.cancelled():
                            break
                    except Exception:
                        break
        return True

    async def _finalize_websocket_request_state(self, *args: Any, **kwargs: Any) -> None:
        self.finalizations += 1
        token = _settlement_path.set("finalize")
        try:
            return await super()._finalize_websocket_request_state(*args, **kwargs)
        finally:
            _settlement_path.reset(token)

    async def _settle_aborted_http_bridge_terminal_states(self, session: Any, request_states: Any) -> None:
        self.abort_settlements += 1
        token = _settlement_path.set("abort")
        try:
            return await super()._settle_aborted_http_bridge_terminal_states(session, request_states)
        finally:
            _settlement_path.reset(token)

    async def _detach_http_bridge_request(self, session: Any, *, request_state: Any) -> bool:
        token = _settlement_path.set("detach")
        try:
            return await super()._detach_http_bridge_request(session, request_state=request_state)
        finally:
            _settlement_path.reset(token)

    async def _reconnect_http_bridge_session(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def _release_retry_account_lease(self, _lease: object) -> None:
        return None

    def _get_work_admission(self) -> WorkAdmissionController:
        return self._retry_work_admission

    def _http_bridge_text_with_account_installation_id(
        self,
        session: Any,
        request_state: Any,
        text_data: str,
    ) -> str:
        del session, request_state
        return text_data


def _make_api_key() -> ApiKeyData:
    return ApiKeyData(
        id="key-property",
        name="property",
        key_prefix="sk-property",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=proxy_service.utcnow(),
        last_used_at=None,
    )


class _ProductionBridgeTurn:
    """One bridge turn driven through the production settlement paths."""

    # Canaries swap in a service subclass carrying a planted bug.
    service_class: type[_RecordingProxyService] = _RecordingProxyService

    def __init__(self, scheduler: VirtualScheduler, *, latency: _ReleaseLatency = _DEFAULT_LATENCY) -> None:
        self.scheduler = scheduler
        self.latency = latency
        self.retry_results: list[bool] = []
        self.detach_results: list[bool] = []
        self.account_releases = 0
        self.terminal_attempts = 0
        self.terminal_cancellations = 0
        self.settlement_cancel_attempts = 0
        self.cancel_landings: list[CancelLanding] = []
        self.admission_waiters = 0
        self.admission_waiters_admitted = 0
        self.retry_sends = 0
        self.upstream_closes = 0
        self._terminal_tasks: set[asyncio.Task[Any]] = set()
        self._schedule_cancelled: set[asyncio.Task[Any]] = set()
        # A single response-create permit: this turn holds it, so an admission
        # wait can only be admitted once a settlement path hands it back.
        self.admission = WorkAdmissionController(
            token_refresh_limit=0,
            websocket_connect_limit=0,
            response_create_limit=1,
            compact_response_create_limit=0,
            admission_wait_timeout_seconds=_ADMISSION_TIMEOUT_SECONDS,
            scheduler=scheduler,
        )
        self.service = self.service_class(
            cast(Any, SimpleNamespace()),
            clock=scheduler.clock,
            scheduler=scheduler,
            work_admission=self.admission,
            latency=latency,
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
            request_text='{"type":"response.create","model":"gpt-5.5","input":[]}',
            bridge_request_deadline=1_000_000_000_000.0,
            api_key=_make_api_key(),
        )
        key_value = "property-retry"
        self.session = proxy_service._HTTPBridgeSession(
            key=proxy_service._HTTPBridgeSessionKey("prompt_cache", key_value, None),
            headers={},
            affinity=proxy_service._AffinityPolicy(
                key=key_value,
                kind=proxy_service.StickySessionKind.CODEX_SESSION,
            ),
            request_model="gpt-5.5",
            account=cast(
                Any,
                SimpleNamespace(
                    id="account-property-retry",
                    status=AccountStatus.ACTIVE,
                    plan_type="plus",
                    chatgpt_account_id=None,
                    security_work_authorized=False,
                ),
            ),
            upstream=cast(
                UpstreamWebSocket, SimpleNamespace(send_text=self._record_retry_send, close=self._close_upstream)
            ),
            upstream_control=proxy_service._WebSocketUpstreamControl(),
            pending_requests=deque([self.request_state]),
            pending_lock=anyio.Lock(),
            response_create_gate=self.response_create_gate,
            queued_request_count=1,
            last_used_at=1.0,
            idle_ttl_seconds=120.0,
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
        await self.scheduler.sleep(self.latency.account_lease_release)
        self.account_releases += 1

    async def _record_retry_send(self, _text: str) -> None:
        self.retry_sends += 1

    async def _close_upstream(self) -> None:
        self.upstream_closes += 1

    async def dispatch(self, event: ScheduleEvent, delay: float, yields: int, cancel_offset: float) -> None:
        await self.scheduler.sleep(delay)
        await self.handle(event, yields, cancel_offset)

    async def handle(self, event: ScheduleEvent, yields: int, cancel_offset: float) -> None:
        if event == "admission_wait":
            await self._wait_for_admission()
        elif event == "upstream_terminal":
            await self._deliver_upstream_terminal()
        elif event == "downstream_cancel":
            await self._detach_downstream()
        elif event == "settlement_cancel":
            await self._cancel_in_flight_settlement(yields, cancel_offset)
        else:
            await self._retry_after_claim(yields)

    async def _wait_until_claimed(self) -> None:
        """Wait until production took the request out of pending ownership.

        Reads product state rather than a scaffold flag: the terminal claim
        pops the request under ``pending_lock``, and a detach that retires the
        session clears the deque.
        """

        while self.request_state in self.session.pending_requests:
            await asyncio.sleep(0)

    async def _deliver_upstream_terminal(self) -> None:
        self.terminal_attempts += 1
        task = asyncio.current_task()
        assert task is not None
        self._terminal_tasks.add(task)
        try:
            await self.service._process_http_bridge_upstream_text(self.session, _COMPLETED_TERMINAL_TEXT)
        except asyncio.CancelledError:
            if task not in self._schedule_cancelled:
                raise
            # The schedule cancelled this bookkeeping on purpose; production
            # has run its abort settlement and re-raised. Absorb the injected
            # cancellation so the dispatch task completes like a reader whose
            # cancellation was consumed by its owner.
            task.uncancel()
            self.terminal_cancellations += 1
        finally:
            self._terminal_tasks.discard(task)

    async def _detach_downstream(self) -> None:
        self.detach_results.append(
            await self.service._detach_http_bridge_request(self.session, request_state=self.request_state)
        )

    async def _cancel_in_flight_settlement(self, yields: int, cancel_offset: float) -> None:
        """Deliver a real cancellation into terminal bookkeeping after the claim.

        The claim is production's ownership transfer (issue #1594): from here
        on only the bookkeeping continuation can settle the reservation, so a
        cancellation landing anywhere between the claim and finalization must
        be survived by the shielded abort path or the deferred releases, and
        one landing after the settlement transfer must leave the settled
        reservation alone. The virtual offset is what reaches the release and
        post-settlement windows: loop turns alone always land before the first
        modelled write completes.
        """

        self.settlement_cancel_attempts += 1
        await self._wait_until_claimed()
        if cancel_offset > 0:
            await self.scheduler.sleep(cancel_offset)
        for _ in range(yields):
            await asyncio.sleep(0)
        # One raw cancellation per bookkeeping task, which is the contract the
        # reader owners honour (``_await_cancelled_task`` cancels once and
        # re-tracks a stubborn child with ``cancel_task=False``). anyio's
        # shield does not stop ``asyncio.Task.cancel()``, so a second raw
        # cancellation would cut into the shielded abort settlement itself.
        for task in sorted(self._terminal_tasks, key=lambda candidate: candidate.get_name()):
            if not task.done() and task not in self._schedule_cancelled:
                self._schedule_cancelled.add(task)
                self.cancel_landings.append(self._classify_cancel_landing())
                task.cancel()
                return

    def _classify_cancel_landing(self) -> CancelLanding:
        """Where the cancellation lands relative to the reservation settlement, from product state."""

        if self.service.reservation_settlements:
            return "after_settlement"
        if self.service.reservation_writes_in_flight:
            return "inside_reservation_write"
        return "before_settlement"

    async def _retry_after_claim(self, yields: int) -> None:
        """Drive the real retry owner check once ownership moved.

        The retry lands a seeded number of loop turns after the claim, inside
        the window where the request still looks retryable (no response id,
        awaiting created) but is owned by the bookkeeping continuation. The
        production ownership guard must reject it; reaching reconnect or
        admission reacquisition would return ``True`` and violate the property.
        """

        await self._wait_until_claimed()
        for _ in range(yields):
            await asyncio.sleep(0)
        retried = await self.service._retry_http_bridge_precreated_request(
            self.session,
            request_state=self.request_state,
        )
        self.retry_results.append(retried)

    async def _wait_for_admission(self) -> None:
        """A queued request contending for the permit this turn still holds.

        It can only be admitted once a settlement path hands the response-create
        permit back to the real gate, then it releases the permit again so a
        later waiter in the same schedule can be admitted too.
        """

        self.admission_waiters += 1
        lease = await self.admission.acquire_response_create()
        self.admission_waiters_admitted += 1
        lease.release()

    def snapshot(self) -> _TurnSnapshot:
        lease = self.admission_lease
        request_state = self.request_state
        return _TurnSnapshot(
            reservation_settlements=tuple(self.service.reservation_settlements),
            redundant_reservation_releases=tuple(self.service.redundant_reservation_releases),
            interrupted_reservation_writes=self.service.interrupted_reservation_writes,
            cancel_landings=tuple(self.cancel_landings),
            finalizations=self.service.finalizations,
            abort_settlements=self.service.abort_settlements,
            response_create_releases=0 if lease is None else lease.release_count,
            account_releases=self.account_releases,
            retry_results=tuple(self.retry_results),
            detach_results=tuple(self.detach_results),
            terminal_attempts=self.terminal_attempts,
            terminal_cancellations=self.terminal_cancellations,
            settlement_cancel_attempts=self.settlement_cancel_attempts,
            admission_waiters=self.admission_waiters,
            admission_waiters_admitted=self.admission_waiters_admitted,
            request_pending=request_state in self.session.pending_requests,
            settlement_phase=request_state.terminal_settlement_phase,
            create_ownership_cleared=(
                request_state.response_create_admission is None
                and request_state.account_response_create_lease is None
                and request_state.account_response_create_release is None
                and not request_state.response_create_gate_acquired
                and not request_state.awaiting_response_created
            ),
            max_owned_task_callbacks=self.scheduler.max_pending_owned_task_callbacks,
            abandoned_shield_callbacks=self.scheduler.max_abandoned_shield_callbacks,
        )


class _DoubleReleaseOnDetachProxyService(_RecordingProxyService):
    """Service whose detach releases the create admission it does not own.

    The cancel path gives back the admission it observes *before* the shared
    gate release takes ownership of it, so a detach racing the terminal path
    releases the response-create permit a second time. That is the "release
    what you no longer own" shape from `TAXONOMY.md`.
    """

    async def _detach_http_bridge_request(self, session: Any, *, request_state: Any) -> bool:
        admission = request_state.response_create_admission
        if admission is not None:
            admission.release()
        return await super()._detach_http_bridge_request(session, request_state=request_state)


class _LostAbortSettlementProxyService(_RecordingProxyService):
    """Service whose abort path never settles a claimed request.

    Once terminal bookkeeping popped the request from pending ownership, the
    abort path is the only owner left (issue #1594). Skipping it leaks the
    reservation whenever a settlement cancel lands between the claim and the
    finalizer's settlement; a claim that is never recorded fails the same way.
    """

    async def _settle_aborted_http_bridge_terminal_states(self, session: Any, request_states: Any) -> None:
        del session, request_states
        self.abort_settlements += 1


class _DroppedApiKeyReleaseProxyService(_RecordingProxyService):
    """Service whose API-key reservation release silently does nothing."""

    async def _release_websocket_reservation(self, reservation: ApiKeyUsageReservationData | None) -> None:
        del reservation


class _DoubleReleaseOnDetachBridgeTurn(_ProductionBridgeTurn):
    service_class = _DoubleReleaseOnDetachProxyService


class _LostAbortSettlementBridgeTurn(_ProductionBridgeTurn):
    service_class = _LostAbortSettlementProxyService


class _DroppedApiKeyReleaseBridgeTurn(_ProductionBridgeTurn):
    """Turn whose detach and abort settlements drop the reservation release.

    Terminal bookkeeping, the response-create permit and the account lease
    all settle exactly once, so only the reservation settlement count exposes
    the planted bug: a release path that returns without releasing.
    """

    service_class = _DroppedApiKeyReleaseProxyService


class _RetryReacquiresAfterSettlementBridgeTurn(_ProductionBridgeTurn):
    """Toy turn carrying a post-settlement retry ownership bug.

    It plants stale pending ownership after settlement finished, then drives
    the production retry operation through reconnect, admission reacquisition,
    upstream send and cleanup. The old checker missed this exact behavior
    because ``retry_request`` only toggled a flag.
    """

    def __init__(self, scheduler: VirtualScheduler, *, latency: _ReleaseLatency = _DEFAULT_LATENCY) -> None:
        super().__init__(scheduler, latency=latency)
        self._retry_canary_lock = asyncio.Lock()

    async def _retry_after_claim(self, yields: int) -> None:
        del yields
        request_state = self.request_state
        async with self._retry_canary_lock:
            if any(self.retry_results):
                self.retry_results.append(False)
                return
            await self._wait_until_claimed()
            while request_state.terminal_settlement_phase is not None or request_state.awaiting_response_created:
                await asyncio.sleep(0)
            # Plant stale pending ownership after settlement finished, then
            # drive the production retry directly (the base class would wait
            # for the request to leave pending ownership again).
            self.session.closed = False
            self.session.pending_requests.append(request_state)
            request_state.awaiting_response_created = True
            request_state.response_id = None
            request_state.response_event_count = 0
            request_state.event_queue = asyncio.Queue()
            request_state.replay_count = 0
            request_state.response_create_admission_reacquire_required = True
            request_state.account_response_create_lease = cast(Any, "retry-account-create-lease")
            request_state.account_response_create_release = self.service._release_retry_account_lease
            retried = await self.service._retry_http_bridge_precreated_request(
                self.session,
                request_state=request_state,
            )
            self.retry_results.append(retried)
            if retried:
                assert self.retry_sends > 0
                await _release_websocket_response_create_gate(
                    request_state,
                    self.response_create_gate,
                    scheduler=self.scheduler,
                )
            # Undo the planted ownership so the other events' production-state
            # polls terminate and a refused retry (a detach already retired the
            # session) leaves no residue; the recorded ``True`` is what the
            # checker rejects.
            async with self.session.pending_lock:
                if request_state in self.session.pending_requests:
                    self.session.pending_requests.remove(request_state)
            request_state.awaiting_response_created = False
            request_state.response_create_admission_reacquire_required = False
            request_state.account_response_create_lease = None
            request_state.account_response_create_release = None


class _ReshieldingLeaseReleaseBridgeTurn(_ProductionBridgeTurn):
    """Toy turn whose account lease release re-shields a pending task.

    Each abandoned ``asyncio.shield`` attempt leaves one done callback behind
    on the still-pending inner task (Python 3.14), the growth pattern behind
    the 2026-08-30 event-loop livelock. The functional outcome is unaffected,
    so only the abandoned-shield oracle sampled by the scheduler can reject it,
    and only on an interpreter where the residue exists.
    """

    async def _release_account_create_lease(self, lease: object) -> None:
        assert lease == "account-create-lease"
        inner = self.scheduler.create_task(self.scheduler.sleep(self.latency.account_lease_release))
        for _ in range(3):
            asyncio.shield(inner).cancel()
        await inner
        self.account_releases += 1


def _schedule_for_seed(seed: int) -> Schedule:
    """Build one deterministic schedule.

    Every schedule contains all five lifecycle events at least once (so a
    terminal and a settlement cancel always arrive), plus up to three repeats,
    each with its own virtual wake-up delay, post-claim yield count and
    post-claim virtual offset.
    """

    rng = random.Random(seed)
    events: list[ScheduleEvent] = list(_EVENTS)
    rng.shuffle(events)
    for _ in range(rng.randint(0, _MAX_EXTRA_EVENTS)):
        events.insert(rng.randrange(len(events) + 1), rng.choice(_EVENTS))
    return tuple(
        (event, rng.choice(_STEP_DELAYS), rng.choice(_SETTLEMENT_YIELDS), rng.choice(_SETTLEMENT_CANCEL_OFFSETS))
        for event in events
    )


def _assert_bridge_turn_invariants(turn: _BridgeTurn, *, seed: int, schedule: Schedule) -> None:
    snapshot = turn.snapshot()
    context = f"seed={seed} schedule={schedule} snapshot={snapshot}"

    def violated(invariant: str) -> str:
        # The snapshot repr names every field, so canaries match on this
        # leading marker rather than on a field name anywhere in the message.
        return f"violated={invariant} {context}"

    expected_terminal_attempts = sum(event == "upstream_terminal" for event, _delay, _yields, _offset in schedule)
    expected_retry_attempts = sum(event == "retry_request" for event, _delay, _yields, _offset in schedule)
    # Exactly one production path settled the API-key reservation: the
    # finalizer, the downstream detach backstop or the shielded abort path.
    # "Settled" is the effective compare-and-set flip the recording service
    # models; redundant release calls that hit an already settled reservation
    # are reported separately (``redundant_reservation_releases``) and pinned
    # by ``test_bridge_turn_lifecycle_settles_reservation_with_a_single_write``.
    assert len(snapshot.reservation_settlements) == 1, violated("terminal_outcomes")
    # Settlement transfer is exclusive: once the finalizer settled the
    # reservation (or handed it to the detached settlement write and cleared
    # the claim), the shielded abort path must find no claim and leave the
    # reservation alone. This is what the post-settlement guards implement;
    # the detach backstop is deliberately not held to it (known-failing pin).
    assert not (
        snapshot.reservation_settlements == ("finalize",) and "abort" in snapshot.redundant_reservation_releases
    ), violated("abort_after_transfer")
    assert snapshot.finalizations <= 1, violated("finalizations")
    assert snapshot.response_create_releases == 1, violated("response_create_releases")
    assert snapshot.account_releases == 1, violated("account_releases")
    assert len(snapshot.retry_results) == expected_retry_attempts, violated("retry_results")
    assert not any(snapshot.retry_results), violated("retry_results")
    # Ownership converged: the request left the pending deque for good, no
    # settlement claim is left behind and every create owner was cleared.
    assert not snapshot.request_pending, violated("request_ownership")
    assert snapshot.settlement_phase is None, violated("request_ownership")
    assert snapshot.create_ownership_cleared, violated("request_ownership")
    assert snapshot.terminal_attempts == expected_terminal_attempts, violated("terminal_attempts")
    # Liveness: the permit really went back to the real admission gate, so the
    # release counters above cannot be satisfied by never releasing at all.
    assert snapshot.admission_waiters_admitted == snapshot.admission_waiters, violated("admission_waiters_admitted")
    # No wait loop re-shielded a pending owned task: every cancelled shield
    # attempt leaves a callback behind on the still-pending task, the growth
    # behind the 2026-08-30 event-loop livelock. A live shield counts as zero.
    # The oracle only observes on CPython 3.14+ (the residue does not exist on
    # 3.13, where CI's unit slice runs), so this line is vacuous there.
    assert snapshot.abandoned_shield_callbacks == 0, violated("abandoned_shields")


async def _run_schedule(turn: _BridgeTurn, schedule: Schedule, scheduler: VirtualScheduler) -> None:
    await turn.start()
    tasks = [
        scheduler.create_task(turn.dispatch(event, delay, yields, cancel_offset), name=f"turn-{index}-{event}")
        for index, (event, delay, yields, cancel_offset) in enumerate(schedule)
    ]
    await scheduler.drain()
    for _ in range(_ADVANCE_STEPS):
        if all(task.done() for task in scheduler.owned_tasks):
            break
        await scheduler.advance(_ADVANCE_SECONDS)
    assert all(task.done() for task in tasks), f"schedule did not quiesce schedule={schedule}"
    # Every task production spawned on this turn is scheduler-owned, so a
    # lingering one is a leak of the turn, not test scaffolding.
    lingering = sorted(task.get_name() for task in scheduler.owned_tasks if not task.done())
    assert not lingering, f"owned tasks still pending after the schedule: {lingering} schedule={schedule}"
    await asyncio.gather(*tasks)


async def _check_schedules(
    turn_factory: type[_ProductionBridgeTurn],
    *,
    latency: _ReleaseLatency = _DEFAULT_LATENCY,
    schedule_count: int = _SCHEDULE_COUNT,
    seeds: Iterable[int] | None = None,
) -> list[_TurnSnapshot]:
    snapshots: list[_TurnSnapshot] = []
    for seed in range(schedule_count) if seeds is None else seeds:
        schedule = _schedule_for_seed(seed)
        scheduler = VirtualScheduler(VirtualClock())
        turn = turn_factory(scheduler, latency=latency)
        try:
            await _run_schedule(turn, schedule, scheduler)
            _assert_bridge_turn_invariants(turn, seed=seed, schedule=schedule)
            snapshots.append(turn.snapshot())
        except BaseException:
            # Reproduce this exact interleaving with _schedule_for_seed(<seed>).
            print(f"bridge turn schedule check failed seed={seed} latency={latency} schedule={schedule}")
            raise
        finally:
            await turn.aclose()
            await scheduler.cancel_owned_tasks()
    return snapshots


@pytest.mark.asyncio
@pytest.mark.parametrize("latency", list(_LATENCY_PROFILES.values()), ids=list(_LATENCY_PROFILES))
async def test_bridge_turn_lifecycle_seeded_schedules_settle_exactly_once(latency: _ReleaseLatency) -> None:
    """Exactly one effective settlement under every modelled write-latency ordering."""

    await _check_schedules(_ProductionBridgeTurn, latency=latency)


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "known production behavior: reachable detach/terminal/abort races issue a second (or third) release call "
        "for the same reservation; the DB compare-and-set makes it a no-op, so only the redundant round trip "
        "remains. Flips to XPASS when production settles with a single write; then drop the marker."
    ),
)
async def test_bridge_turn_lifecycle_settles_reservation_with_a_single_write() -> None:
    """Pins the redundant-release observation so nobody mistakes it for harness rot.

    `_detach_http_bridge_request` decides `detached` under `pending_lock`,
    awaits the gate release and only then reads `request_state.api_key_reservation`,
    which the terminal path (draining-branch finalizer or the shielded abort
    settlement) may already be writing; both paths clear the reservation only
    after their awaited write returns. The seeded schedules reach that shape
    under every latency profile.
    """

    for latency in _LATENCY_PROFILES.values():
        snapshots = await _check_schedules(_ProductionBridgeTurn, latency=latency)
        redundant = [
            snapshot.redundant_reservation_releases for snapshot in snapshots if snapshot.redundant_reservation_releases
        ]
        assert not redundant, f"latency={latency} redundant releases in {len(redundant)} schedules: {redundant[:3]}"


@pytest.mark.asyncio
@pytest.mark.xfail(
    ANYIO_LOCK_RELEASE_SKIPS_CANCELLED_WAITERS,
    strict=True,
    raises=AssertionError,
    reason=ANYIO_LOCK_WEDGE_REASON,
)
async def test_bridge_turn_lifecycle_known_anyio_lock_wedge_seeds_quiesce() -> None:
    """The production turn's own shape of the anyio wedge: the reader cancel races a ``pending_lock`` release.

    On the pinned anyio the schedule does not quiesce (``pending_lock`` is left
    unowned with a live waiter); with the 4.14 ``Lock.release`` fix these seeds
    pass the full invariant set.
    """

    await _check_schedules(_ProductionBridgeTurn, seeds=_ANYIO_LOCK_WEDGE_SEEDS)


@pytest.mark.asyncio
async def test_bridge_turn_lifecycle_schedules_exercise_every_settlement_path_and_real_cancellation() -> None:
    """The schedule set covers what the property claims to cover.

    Without this, the cancellation event could silently degrade into a no-op
    (never landing inside the bookkeeping) and one settlement path could stop
    being reached, while the exactly-once assertions kept passing.
    """

    snapshots = await _check_schedules(_ProductionBridgeTurn)

    settled_by = {path for snapshot in snapshots for path in snapshot.reservation_settlements}
    assert settled_by == {"finalize", "detach", "abort"}
    cancelled_mid_settlement = sum(snapshot.terminal_cancellations > 0 for snapshot in snapshots)
    assert cancelled_mid_settlement >= _SCHEDULE_COUNT // 4, cancelled_mid_settlement
    cancelled_inside_finalizer = sum(
        snapshot.terminal_cancellations > 0 and snapshot.finalizations == 1 for snapshot in snapshots
    )
    assert cancelled_inside_finalizer >= _SCHEDULE_COUNT // 10, cancelled_inside_finalizer
    # The cancellation reaches every window of the settlement, not only the
    # gate/lease release before the first write: inside a reservation write
    # (production's raw cancel cuts through the anyio shield there) and after
    # the settlement transfer, where the post-settlement guards are what keep
    # the abort path from settling again.
    landings = Counter(landing for snapshot in snapshots for landing in snapshot.cancel_landings)
    assert landings["before_settlement"] >= _SCHEDULE_COUNT // 10, landings
    assert landings["inside_reservation_write"] >= _SCHEDULE_COUNT // 20, landings
    assert landings["after_settlement"] >= _SCHEDULE_COUNT // 20, landings
    assert sum(snapshot.interrupted_reservation_writes for snapshot in snapshots) >= _SCHEDULE_COUNT // 20
    assert any(snapshot.retry_results for snapshot in snapshots)
    assert any(snapshot.admission_waiters_admitted > 1 for snapshot in snapshots)


@pytest.mark.asyncio
async def test_bridge_turn_lifecycle_schedule_set_is_large_and_varied() -> None:
    schedules = [_schedule_for_seed(seed) for seed in range(_SCHEDULE_COUNT)]

    assert len(schedules) >= 200
    assert len(set(schedules)) >= 150, "the seeded schedules collapse onto too few interleavings"
    for schedule in schedules:
        events = [event for event, _delay, _yields, _offset in schedule]
        assert set(_EVENTS).issubset(events)


@pytest.mark.asyncio
async def test_bridge_turn_lifecycle_checker_catches_double_release_canary() -> None:
    with pytest.raises(AssertionError, match=r"^violated=response_create_releases "):
        await _check_schedules(_DoubleReleaseOnDetachBridgeTurn)


@pytest.mark.asyncio
async def test_bridge_turn_lifecycle_checker_catches_lost_abort_settlement_canary() -> None:
    with pytest.raises(AssertionError, match=r"^violated=terminal_outcomes "):
        await _check_schedules(_LostAbortSettlementBridgeTurn)


@pytest.mark.asyncio
async def test_bridge_turn_lifecycle_checker_catches_dropped_api_key_release_canary() -> None:
    with pytest.raises(AssertionError, match=r"^violated=terminal_outcomes "):
        await _check_schedules(_DroppedApiKeyReleaseBridgeTurn)


@pytest.mark.asyncio
async def test_bridge_turn_lifecycle_checker_catches_retry_reacquisition_canary() -> None:
    with pytest.raises(AssertionError, match=r"^violated=retry_results "):
        await _check_schedules(_RetryReacquiresAfterSettlementBridgeTurn)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not ABANDONED_SHIELD_ORACLE_SUPPORTED,
    reason="asyncio.shield leaves no callback residue before CPython 3.14; the oracle cannot see this canary",
)
async def test_bridge_turn_lifecycle_checker_catches_reshielded_release_canary() -> None:
    with pytest.raises(AssertionError, match=r"^violated=abandoned_shields "):
        await _check_schedules(_ReshieldingLeaseReleaseBridgeTurn)


@pytest.mark.asyncio
@pytest.mark.skipif(ABANDONED_SHIELD_ORACLE_SUPPORTED, reason="on CPython 3.14+ the canary is caught (test above)")
async def test_bridge_turn_lifecycle_reshielded_release_canary_is_invisible_before_python_3_14() -> None:
    """Documents the CI blind spot: on 3.13 the leak shape has no observable residue.

    The functional invariants still hold for this canary, so the checker
    accepts it; the production image runs 3.14, where the sibling test proves
    the oracle rejects it.
    """

    snapshots = await _check_schedules(_ReshieldingLeaseReleaseBridgeTurn)

    assert all(snapshot.abandoned_shield_callbacks == 0 for snapshot in snapshots)
