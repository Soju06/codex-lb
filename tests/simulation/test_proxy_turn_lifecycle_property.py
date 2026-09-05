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
  terminal bookkeeping *after* the production claim, a seeded number of loop
  turns later, so it lands on different awaits of the claim-to-finalize
  window. Production then has to settle the claimed request through the
  shielded abort path (``_settle_aborted_http_bridge_terminal_states``,
  issue #1594) or finish the deferred release it was in.
* ``retry_request`` runs ``_retry_http_bridge_precreated_request`` once the
  request has left pending ownership; the production ownership guard is what
  must reject it.

The API-key reservation is settled by whichever production path gets there
first (finalize, detach or abort); the recording service attributes each
settlement to its path so "exactly one terminal outcome" is checked against
what production actually did. Lease releases go through the real
``WorkAdmissionController`` gate and the request state's own release
callbacks; the modelled releases suspend on a virtual timer, standing in for
the DB writes they replace, so cancellations can land inside them.

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
from collections import deque
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal, Protocol, cast

import anyio
import pytest

from app.core.clients.proxy_websocket import UpstreamWebSocket
from app.db.models import AccountStatus
from app.modules.api_keys.service import ApiKeyData, ApiKeyUsageReservationData
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.websocket.helpers import _release_websocket_response_create_gate
from app.modules.proxy.work_admission import AdmissionLease, WorkAdmissionController
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

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
_SCHEDULE_COUNT = 200
_MAX_EXTRA_EVENTS = 3
_ADVANCE_SECONDS = 0.05
_ADVANCE_STEPS = 12
_ADMISSION_TIMEOUT_SECONDS = 10.0
# The modelled lease releases suspend on a virtual timer, like the DB writes
# they stand in for, so a settlement cancel can land inside a release.
_RELEASE_LATENCY_SECONDS = 0.01
# A completed frame without a response id: the terminal claim goes through the
# anonymous match and the request stays retryable-looking until finalization.
_COMPLETED_TERMINAL_TEXT = '{"type":"response.completed","response":{"object":"response","status":"completed"}}'

ScheduleStep = tuple[ScheduleEvent, float, int]
Schedule = tuple[ScheduleStep, ...]

# Set by the recording service around each production settlement path so a
# reservation release can be attributed to the path that performed it. Tasks
# spawned inside a path inherit the value through their context copy.
_settlement_path: contextvars.ContextVar[SettlementPath] = contextvars.ContextVar(
    "settlement_path", default="unattributed"
)


class _BridgeTurn(Protocol):
    async def start(self) -> None: ...

    async def dispatch(self, event: ScheduleEvent, delay: float, yields: int) -> None: ...

    def snapshot(self) -> "_TurnSnapshot": ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _TurnSnapshot:
    reservation_settlements: tuple[SettlementPath, ...]
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
    """

    def __init__(
        self,
        repo_factory: Any,
        *,
        clock: VirtualClock,
        scheduler: VirtualScheduler,
        work_admission: WorkAdmissionController,
    ) -> None:
        super().__init__(repo_factory, clock=clock, scheduler=scheduler)
        self.reservation_settlements: list[SettlementPath] = []
        self.finalizations = 0
        self.abort_settlements = 0
        self.account_successes = 0
        self._retry_work_admission = work_admission
        self._load_balancer.record_success = self._record_account_success

    async def _record_account_success(self, account: Any) -> None:
        del account
        self.account_successes += 1

    async def _release_websocket_reservation(self, reservation: ApiKeyUsageReservationData | None) -> None:
        if reservation is None:
            return
        await self._scheduler.sleep(_RELEASE_LATENCY_SECONDS)
        self.reservation_settlements.append(_settlement_path.get())

    async def _settle_stream_api_key_usage(
        self,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        settlement: Any,
        request_id: str,
        *,
        wait_for_settlement: bool = False,
    ) -> bool:
        del settlement, request_id, wait_for_settlement
        if api_key is None or api_key_reservation is None:
            return True
        await self._scheduler.sleep(_RELEASE_LATENCY_SECONDS)
        self.reservation_settlements.append(_settlement_path.get())
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

    def __init__(self, scheduler: VirtualScheduler) -> None:
        self.scheduler = scheduler
        self.retry_results: list[bool] = []
        self.detach_results: list[bool] = []
        self.account_releases = 0
        self.terminal_attempts = 0
        self.terminal_cancellations = 0
        self.settlement_cancel_attempts = 0
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
        await self.scheduler.sleep(_RELEASE_LATENCY_SECONDS)
        self.account_releases += 1

    async def _record_retry_send(self, _text: str) -> None:
        self.retry_sends += 1

    async def _close_upstream(self) -> None:
        self.upstream_closes += 1

    async def dispatch(self, event: ScheduleEvent, delay: float, yields: int) -> None:
        await self.scheduler.sleep(delay)
        await self.handle(event, yields)

    async def handle(self, event: ScheduleEvent, yields: int) -> None:
        if event == "admission_wait":
            await self._wait_for_admission()
        elif event == "upstream_terminal":
            await self._deliver_upstream_terminal()
        elif event == "downstream_cancel":
            await self._detach_downstream()
        elif event == "settlement_cancel":
            await self._cancel_in_flight_settlement(yields)
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

    async def _cancel_in_flight_settlement(self, yields: int) -> None:
        """Deliver a real cancellation into terminal bookkeeping after the claim.

        The claim is production's ownership transfer (issue #1594): from here
        on only the bookkeeping continuation can settle the reservation, so a
        cancellation landing anywhere between the claim and finalization must
        be survived by the shielded abort path or the deferred releases.
        """

        self.settlement_cancel_attempts += 1
        await self._wait_until_claimed()
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
                task.cancel()
                return

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

    def __init__(self, scheduler: VirtualScheduler) -> None:
        super().__init__(scheduler)
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
    so only the abandoned-shield oracle sampled by the scheduler can reject it.
    """

    async def _release_account_create_lease(self, lease: object) -> None:
        assert lease == "account-create-lease"
        inner = self.scheduler.create_task(self.scheduler.sleep(_RELEASE_LATENCY_SECONDS))
        for _ in range(3):
            asyncio.shield(inner).cancel()
        await inner
        self.account_releases += 1


def _schedule_for_seed(seed: int) -> Schedule:
    """Build one deterministic schedule.

    Every schedule contains all five lifecycle events at least once (so a
    terminal and a settlement cancel always arrive), plus up to three repeats,
    each with its own virtual wake-up delay and post-claim yield count.
    """

    rng = random.Random(seed)
    events: list[ScheduleEvent] = list(_EVENTS)
    rng.shuffle(events)
    for _ in range(rng.randint(0, _MAX_EXTRA_EVENTS)):
        events.insert(rng.randrange(len(events) + 1), rng.choice(_EVENTS))
    return tuple((event, rng.choice(_STEP_DELAYS), rng.choice(_SETTLEMENT_YIELDS)) for event in events)


def _assert_bridge_turn_invariants(turn: _BridgeTurn, *, seed: int, schedule: Schedule) -> None:
    snapshot = turn.snapshot()
    context = f"seed={seed} schedule={schedule} snapshot={snapshot}"

    def violated(invariant: str) -> str:
        # The snapshot repr names every field, so canaries match on this
        # leading marker rather than on a field name anywhere in the message.
        return f"violated={invariant} {context}"

    expected_terminal_attempts = sum(event == "upstream_terminal" for event, _delay, _yields in schedule)
    expected_retry_attempts = sum(event == "retry_request" for event, _delay, _yields in schedule)
    # Exactly one production path settled the API-key reservation: the
    # finalizer, the downstream detach backstop or the shielded abort path.
    assert len(snapshot.reservation_settlements) == 1, violated("terminal_outcomes")
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
    assert snapshot.abandoned_shield_callbacks == 0, violated("abandoned_shields")


async def _run_schedule(turn: _BridgeTurn, schedule: Schedule, scheduler: VirtualScheduler) -> None:
    await turn.start()
    tasks = [
        scheduler.create_task(turn.dispatch(event, delay, yields), name=f"turn-{index}-{event}")
        for index, (event, delay, yields) in enumerate(schedule)
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
    schedule_count: int = _SCHEDULE_COUNT,
) -> list[_TurnSnapshot]:
    snapshots: list[_TurnSnapshot] = []
    for seed in range(schedule_count):
        schedule = _schedule_for_seed(seed)
        scheduler = VirtualScheduler(VirtualClock())
        turn = turn_factory(scheduler)
        try:
            await _run_schedule(turn, schedule, scheduler)
            _assert_bridge_turn_invariants(turn, seed=seed, schedule=schedule)
            snapshots.append(turn.snapshot())
        except BaseException:
            # Reproduce this exact interleaving with _schedule_for_seed(<seed>).
            print(f"bridge turn schedule check failed seed={seed} schedule={schedule}")
            raise
        finally:
            await turn.aclose()
            await scheduler.cancel_owned_tasks()
    return snapshots


@pytest.mark.asyncio
async def test_bridge_turn_lifecycle_seeded_schedules_settle_exactly_once() -> None:
    await _check_schedules(_ProductionBridgeTurn)


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
    assert any(snapshot.retry_results for snapshot in snapshots)
    assert any(snapshot.admission_waiters_admitted > 1 for snapshot in snapshots)


@pytest.mark.asyncio
async def test_bridge_turn_lifecycle_schedule_set_is_large_and_varied() -> None:
    schedules = [_schedule_for_seed(seed) for seed in range(_SCHEDULE_COUNT)]

    assert len(schedules) >= 200
    assert len(set(schedules)) >= 150, "the seeded schedules collapse onto too few interleavings"
    for schedule in schedules:
        events = [event for event, _delay, _yields in schedule]
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
async def test_bridge_turn_lifecycle_checker_catches_reshielded_release_canary() -> None:
    with pytest.raises(AssertionError, match=r"^violated=abandoned_shields "):
        await _check_schedules(_ReshieldingLeaseReleaseBridgeTurn)
