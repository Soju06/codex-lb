"""Executable rehearsals of the pre-flip canary drills (#2123, ``docs/routing.md`` "Canary and drills").

Until now the drills existed only as prose: a seven-row table in
``docs/routing.md`` and the matching clauses in the
``add-subscription-overflow-model-source`` spec delta. An operator ran them by
hand against a canary deployment and compared what they saw with a sentence.
This module is the same list, executable: the **Expected** cell of every table
row is a list of clauses, and each clause is either asserted by a
``test_drill_*`` here or listed in the runbook as one only the canary can
settle. A drill asserts what *its own clauses* promise rather than a fixed
checklist -- the wire answer every time, its exact message where the clause
states one, and then whichever of the ``request_logs`` rows, the
``model_source_pins`` rows, the outcome label, the API-key reservation, the
source bulkhead and the breaker state those clauses are actually about. No
drill asserts all six: one that runs without an API key has no reservation to
check, Disconnect and Silent headers pin the counters their own rows are about
(the abandonment ``stage``, the timeout ``phase``) rather than the outcome
label, and only Stall is a row about the breaker. Padding the rest with
vacuous assertions would say less, not more.

Which assertion covers which clause is not a claim in a docstring:
``tests/unit/test_overflow_drill_coverage.py`` holds the map, parses the table
out of ``docs/routing.md`` and fails when a clause has neither an assertion
still present here -- in a drill nothing has switched off, which it checks too
-- nor an entry in the runbook's "Not rehearsed" list. The
clauses in that list are the ones nothing in one process can settle -- whether
live ChatGPT traffic keeps flowing through a stall (this suite's pool is
exhausted by construction, so it can only assert that the stalled source never
touches the ChatGPT connector), the cross-replica settings-cache window (it
needs a second replica) and whether the real source mints its ``response.id``
on ``response.created`` (the stub here is built to; only the canary can tell
you about the source you are about to designate).

What it is and is not (design v3 §13.4): these are in-process ASGI tests
against a stub aiohttp source and the test database. ``make
test-overflow-drills`` **rehearses** every drill contract against the
production route and forwarding stack before the canary exists; it cannot
target a deployed canary, and it says nothing about real firewalls, real
calendars, multi-replica settings propagation or whether the real source mints
``response.id`` early. That residue is enumerated beside the table in
``docs/routing.md`` and stays manual.

Mechanics, and why not virtual time: ``SourceDispatch`` takes its scheduler
from ``scheduler_for(context.service)`` (``api.py``), so virtualising the
deadline clock at route level would virtualise the whole request path. The
numeric deadlines stay pinned by ``test_model_source_forwarding_deadlines.py``
in virtual time; the drills pin the *route's reaction*, and shorten the open's
two deadlines by re-binding ``_open_source_stream`` with smaller defaults (a
``functools.partial``: they are default parameters, not module constants, so
patching the constants would not work). The client-visible message still names
the production value, because ``_timeout_error`` reads the module constant at
call time -- which is exactly the string the runbook tells the operator to
expect, asserted verbatim while the test runs in fractions of a second.

Overlap with the narrower suites (``test_subscription_overflow_routing.py``'s
abandonment and release tests, the forwarding deadline suite, the retention
suite's pin purge, #2354's anchor tests) is deliberate: those pin one mechanism
each, and none of them asserts one drill row's outcomes together.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select

import app.core.clients.http as http_module
from app.core.config.settings_cache import get_settings_cache
from app.core.retention.job import run_retention_pass
from app.core.utils.time import utcnow
from app.db.models import ApiKeyUsageReservation, DashboardSettings
from app.db.session import SessionLocal
from app.modules.model_sources import forwarding as forwarding_module
from app.modules.proxy import model_source_pins as pins_module
from app.modules.proxy import overflow as overflow_module
from app.modules.proxy import source_dispatch as dispatch_module
from app.modules.proxy.model_source_pins import (
    PIN_KIND_ANCHOR,
    PIN_KIND_THREAD,
    anchor_pin_key,
    thread_pin_key,
)
from app.modules.proxy.overflow import (
    MODEL_SOURCE_UNAVAILABLE_CODE,
    PIN_UNAVAILABLE_CODE,
    REQUEST_LOG_SOURCE_FRESH,
    REQUEST_LOG_SOURCE_PINNED,
    ROUTE_CODEX_RESPONSES,
    ROUTE_V1_RESPONSES,
    SOURCE_UNAVAILABLE_CODE,
    UNSUPPORTED_INPUT_CODE,
)
from app.modules.proxy.source_admission import get_source_bulkhead
from app.modules.settings.subscription_overflow import DRAIN_WINDOW, PIN_IDLE_TTL, PIN_TOMBSTONE_GRACE
from tests.integration.model_source_helpers import (
    _AsgiStream,
    _create_model_source,
    _enable_api_key_auth,
    stub_source_upstreams,
)
from tests.integration.test_model_source_dispatch import (
    _DELTA,
    _ITEM_ADDED,
    _USAGE,
    _app,
    _completed,
    _created,
    _drain,
    _sse_handler,
    _StubState,
)
from tests.integration.test_model_source_forwarding_deadlines import _silent_tcp_upstream
from tests.integration.test_subscription_overflow_golden import (
    _forbid_configured_only_work,
    _golden_429_bytes,
    _twice,
)
from tests.integration.test_subscription_overflow_routing import (
    CODEX_ROUTE,
    REGISTRY_SLUG,
    V1_ROUTE,
    _all_rows,
    _canned_subscription_stream,
    _codex_body,
    _designate,
    _events,
    _exhausted_scene,
    _forbid_subscription_stream,
    _lifecycle,
    _native_headers,
    _pin_rows,
    _pool_is_healthy,
    _release_hold_on_pin_commit,
    _seed_exhausted_pool,
    _thread_key,
    _todays_429,
    _write_pin,
)

pytestmark = [pytest.mark.integration, pytest.mark.overflow_drill]

_OVERFLOW_LOGGER = "app.modules.proxy.overflow"
_RETENTION_LOGGER = "app.core.retention.job"
# A dropped-SYN address: RFC 5737 TEST-NET-1 on the discard port. Reserved for
# documentation and routed nowhere, so the connect phase is what fails.
_BLACK_HOLE_URL = "http://192.0.2.1:9/v1"
# The frames a client cannot see before the response id is known, so the ones
# ``response.created`` must precede for an SDK turn to be anchorable.
_CONTENT_BEARING_EVENTS = frozenset({"response.output_item.added", "response.output_text.delta"})
# Short enough that a drill finishes in well under a second; the wire text the
# assertions pin still names the production deadline.
_SHORT_DEADLINE_SECONDS = 0.4


@pytest.fixture
async def source_upstream() -> AsyncIterator[Callable[..., Awaitable[str]]]:
    # Local on purpose: a ``source_upstream`` fixture imported across modules
    # trips ruff's F811 on every test parameter (model_source_helpers.py:99).
    async with stub_source_upstreams() as start:
        yield start


@pytest.fixture(autouse=True)
def _reset_overflow_singletons() -> Iterator[None]:
    """Reset the per-process overflow singletons around every drill.

    ``tests/conftest.py::_reset_global_state`` resets a dozen caches but none
    of these; the existing suites stay isolated only by never reusing a source
    or thread id. The stall drill deliberately trips the breaker and the
    clear-then-touch drill drives the pin toucher's per-replica memo, so
    without this the suite would depend on its own execution order.
    """

    def reset() -> None:
        overflow_module._BREAKER = None
        overflow_module._FAST_DECLINE = None
        overflow_module._PIN_EXECUTOR = None
        overflow_module._PIN_TOUCHER = None
        pins_module._PIN_CACHE = None

    reset()
    yield
    reset()


# -- helpers -------------------------------------------------------------------------------------


class _LabelRecorder:
    """Prometheus counter double: records ``labels(**kwargs)`` in increment order."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.incs = 0
        self._pending: dict[str, str] | None = None

    def labels(self, **labels: str) -> _LabelRecorder:
        self._pending = labels
        return self

    def inc(self, amount: float = 1) -> None:
        assert self._pending is not None
        self.calls.append(self._pending)
        self.incs += 1

    def phases(self) -> list[str]:
        return [call["phase"] for call in self.calls]

    def stages(self) -> list[str]:
        return [call["stage"] for call in self.calls]


class _OutcomeRecorder(_LabelRecorder):
    """``codex_lb_subscription_overflow_total`` as a mutable list of ``(route, outcome)``."""

    def __init__(self) -> None:
        super().__init__()
        self.outcomes: list[tuple[str, str]] = []

    def inc(self, amount: float = 1) -> None:
        super().inc(amount)
        call = self.calls[-1]
        self.outcomes.append((call["route"], call["outcome"]))


def _spy_overflow_outcomes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    recorder = _OutcomeRecorder()
    monkeypatch.setattr(overflow_module, "subscription_overflow_total", recorder)
    return recorder.outcomes


def _shorten_open_deadlines(
    monkeypatch: pytest.MonkeyPatch,
    *,
    header_seconds: float | None = _SHORT_DEADLINE_SECONDS,
    first_frame_seconds: float | None = _SHORT_DEADLINE_SECONDS,
) -> None:
    """Re-bind ``_open_source_stream`` with smaller deadline *defaults*.

    ``stream_responses`` resolves the name on the module at call time, and the
    two deadlines are default parameters of ``_open_source_stream``: patching
    ``SOURCE_HEADER_DEADLINE_SECONDS`` / ``SOURCE_FIRST_FRAME_DEADLINE_SECONDS``
    would not change the open at all (only the message text). The partial
    leaves those constants alone, which is what keeps the client-visible
    message naming the production value.
    """

    monkeypatch.setattr(
        forwarding_module,
        "_open_source_stream",
        functools.partial(
            forwarding_module._open_source_stream,
            header_deadline_seconds=header_seconds,
            first_frame_deadline_seconds=first_frame_seconds,
        ),
    )


async def _designated_source(
    async_client: Any,
    *,
    tag: str,
    base_url: str,
    models: list[str] | None = None,
) -> str:
    """Create a priced Responses source listing ``REGISTRY_SLUG`` and designate it for overflow."""

    source_id = await _create_model_source(
        async_client,
        name=f"overflow-{tag}",
        model=(models or [REGISTRY_SLUG])[0],
        base_url=base_url,
        supports_responses=True,
        input_per_1m=2.0,
        output_per_1m=8.0,
    )
    await _designate(async_client, source_id)
    return source_id


async def _limited_unscoped_key(async_client: Any, *, name: str) -> tuple[str, str]:
    """A limited key with no source assignment: scoped keys decline overflow (``key_scope``)."""

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": name,
            "limits": [{"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 100_000}],
        },
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    return payload["key"], payload["id"]


async def _reservation_states(api_key_id: str) -> list[tuple[str, int | None, int | None]]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(ApiKeyUsageReservation)
            .where(ApiKeyUsageReservation.api_key_id == api_key_id)
            .order_by(ApiKeyUsageReservation.created_at)
        )
        return [
            (reservation.status, reservation.input_tokens, reservation.output_tokens)
            for reservation in result.scalars().all()
        ]


async def _set_overflow_settings(*, source_id: str | None, drain_until: datetime | None) -> None:
    """Write the two ship-dark columns straight to the row and drop the settings cache.

    The drills that need a *past* clear time cannot get there through
    ``PUT /api/settings``, which always arms ``now + DRAIN_WINDOW``.
    """

    async with SessionLocal() as session:
        row = (await session.execute(select(DashboardSettings).where(DashboardSettings.id == 1))).scalar_one_or_none()
        assert row is not None, "the dashboard settings row must exist before the drill rewrites it"
        row.subscription_overflow_source_id = source_id
        row.subscription_overflow_drain_until = (
            None if drain_until is None else drain_until.astimezone(timezone.utc).replace(tzinfo=None)
        )
        await session.commit()
    await get_settings_cache().invalidate(propagate=False)


def _chatgpt_connections_acquired() -> int:
    session = http_module.get_http_client().session
    connector = session.connector
    assert connector is not None
    return len(connector._acquired)


def _model_source_connections_acquired() -> int:
    client = http_module.get_http_client()
    session = client.model_source_session
    assert session is not None, "the dedicated model-source session is built with the generation"
    connector = session.connector
    assert connector is not None
    return len(connector._acquired)


async def _wait_for_stub_request(state: _StubState, *, count: int = 1) -> None:
    deadline = time.monotonic() + 5
    while len(state.requests) < count and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert len(state.requests) >= count, f"the stub never received {count} open(s)"


async def _wait_for_stub_cancel(state: _StubState) -> None:
    deadline = time.monotonic() + 5
    while state.cancelled == 0 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert state.cancelled == 1, "the stub connection was not closed when the client left"


def _aware(value: datetime) -> datetime:
    """The pin columns are timezone-aware; ``utcnow()`` and the settings row are naive UTC."""

    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _error(response: Any) -> dict[str, Any]:
    body = response.json()
    assert isinstance(body, dict) and "error" in body, response.text
    return body["error"]


def _ciphertext_input() -> list[dict[str, Any]]:
    """A transcript the source owns: a reasoning item with encrypted content cannot move to an account."""

    return [
        {"type": "message", "id": "msg_src_1", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        {"type": "reasoning", "id": "rs_src_1", "summary": [], "encrypted_content": "c2VjcmV0"},
    ]


def _source_free_input() -> list[dict[str, Any]]:
    return [
        {"type": "message", "id": "msg_src_1", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        {"type": "message", "id": "msg_src_2", "role": "assistant", "content": [{"type": "output_text", "text": "y"}]},
    ]


# == Drill 1 -- Disconnect =======================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("leg", "expected_error_codes"),
    [
        ("during-open", {"client_disconnected_during_open"}),
        ("before-body", {"client_disconnected_before_body", "client_disconnected"}),
    ],
    ids=["during-open", "before-body"],
)
async def test_drill_disconnect_mid_dispatch_is_a_cancelled_attempt(
    async_client,
    source_upstream,
    monkeypatch: pytest.MonkeyPatch,
    leg: str,
    expected_error_codes: set[str],
) -> None:
    """Runbook row **Disconnect**: Esc during time-to-first-byte.

    One ``cancelled`` row, no pin, the source slot released, the API-key
    reservation released with no usage, the source connection closed and the
    abandonment counter stamped with its stage -- and the client saw nothing.
    The two legs are the two windows a real Esc lands in: the open is still
    pending, or the headers are out and the body has not started.
    """

    _forbid_subscription_stream(monkeypatch)
    abandoned = _LabelRecorder()
    monkeypatch.setattr(dispatch_module, "model_source_dispatch_abandoned_total", abandoned)
    await _enable_api_key_auth(async_client)
    key, key_id = await _limited_unscoped_key(async_client, name=f"drill-disconnect-{leg}")
    hold = asyncio.Event()
    scene = await _exhausted_scene(
        async_client,
        source_upstream,
        tag=f"drill_disconnect_{leg.replace('-', '_')}",
        # "during-open" withholds every frame, so the open never returns;
        # "before-body" lets ``response.created`` out so the route reaches
        # ``http.response.start`` and stalls there.
        frames=[] if leg == "during-open" else [_created()],
        hold=hold,
        after_hold=[_ITEM_ADDED, _DELTA, _completed(_USAGE)],
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    thread_id = f"thr_drill_disconnect_{leg}"
    stream = _AsgiStream(
        app=_app(async_client),
        path=CODEX_ROUTE,
        headers={**_native_headers(thread_id), "authorization": f"Bearer {key}"},
        body=json.dumps(_codex_body()).encode(),
        stall_response_start=leg == "before-body",
    )

    runner = asyncio.create_task(stream.run())
    if leg == "during-open":
        await _wait_for_stub_request(scene.state)
    else:
        await stream.wait_for_response_start()
    left_at = time.monotonic()
    stream.disconnect()
    await asyncio.wait_for(runner, timeout=10)
    abandoned_after = time.monotonic() - left_at
    await _drain(async_client)
    hold.set()

    assert abandoned_after < 2.0, "the dispatch must not outlive the client by a deadline"
    assert stream.chunks == [], "nothing may reach a client that has already left"
    rows = await _all_rows()
    assert [(row.status, row.source, row.api_key_id) for row in rows] == [
        ("cancelled", REQUEST_LOG_SOURCE_FRESH, key_id)
    ]
    assert rows[0].error_code in expected_error_codes, rows[0].error_code
    assert await _reservation_states(key_id) == [("released", None, None)]
    assert await _pin_rows() == []
    assert get_source_bulkhead().in_flight(scene.source_id) == 0
    assert abandoned.stages() == ["during_open" if leg == "during-open" else "before_body"]
    await _wait_for_stub_cancel(scene.state)


@pytest.mark.asyncio
async def test_disconnect_drill_control_a_served_dispatch_finalizes_the_reservation(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control the Disconnect drill needs: ``released`` is not what a *served* overflow turn writes."""

    _forbid_subscription_stream(monkeypatch)
    await _enable_api_key_auth(async_client)
    key, key_id = await _limited_unscoped_key(async_client, name="drill-disconnect-control")
    await _exhausted_scene(async_client, source_upstream, tag="drill_disconnect_control")

    response = await async_client.post(
        CODEX_ROUTE,
        json=_codex_body(),
        headers={**_native_headers("thr_drill_disconnect_control"), "authorization": f"Bearer {key}"},
    )
    await _drain(async_client)

    assert response.status_code == 200, response.text
    assert await _reservation_states(key_id) == [
        ("finalized", _USAGE["input_tokens"], _USAGE["output_tokens"]),
    ]


# == Drill 2 -- Stall ============================================================================


@pytest.mark.asyncio
async def test_drill_stall_fails_closed_and_opens_the_breaker(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runbook row **Stall**: a black-holed source, in the two shapes "black-holed" actually takes.

    A dropped-SYN address never reaches the header wait, so it answers ``502``
    ``model_source_unreachable`` -- *not* the ``504 model_source_timeout`` the
    prose used to promise. A source that accepts TCP and stays silent is the
    one that produces the ``504``. Three counted failures open the breaker:
    fresh requests fall back to today's ``429`` without touching the source,
    a pinned conversation gets ``503`` with ``Retry-After`` instead, and the
    ChatGPT connector is untouched throughout.
    """

    _forbid_subscription_stream(monkeypatch)
    outcomes = _spy_overflow_outcomes(monkeypatch)
    reset_at = await _seed_exhausted_pool(async_client, tag="drill_stall")
    breaker = overflow_module.get_source_breaker()

    # (a) connect phase: a dropped SYN is unreachable, never a timeout. The
    # counter label is deliberately not asserted here: whether the kernel
    # drops the SYN (``connect`` phase) or answers ENETUNREACH (no phase at
    # all) depends on the host's routing table, and both land on the same
    # status and code -- which is the part the runbook promises.
    monkeypatch.setattr(forwarding_module, "SOURCE_CONNECT_DEADLINE_SECONDS", _SHORT_DEADLINE_SECONDS)
    black_hole_id = await _designated_source(async_client, tag="drill_stall_blackhole", base_url=_BLACK_HOLE_URL)
    unreachable = await async_client.post(
        CODEX_ROUTE, json=_codex_body(), headers=_native_headers("thr_drill_stall_connect")
    )
    await _drain(async_client)

    assert unreachable.status_code == 502, unreachable.text
    assert _error(unreachable)["code"] == "model_source_unreachable"
    assert b"data:" not in unreachable.content, "the error document is the whole answer; no `200` preceded it"
    assert [(row.status, row.error_code) for row in await _all_rows()] == [("error", "model_source_unreachable")]
    assert breaker.failures(black_hole_id) == 1

    # (b) header phase: TCP accepted, nothing written back -> 504, naming the production deadline.
    timeouts = _LabelRecorder()
    monkeypatch.setattr(dispatch_module, "model_source_timeout_total", timeouts)
    _shorten_open_deadlines(monkeypatch)
    async with _silent_tcp_upstream() as silent:
        silent_id = await _designated_source(async_client, tag="drill_stall_silent", base_url=silent.base_url)
        pending = asyncio.create_task(
            async_client.post(CODEX_ROUTE, json=_codex_body(), headers=_native_headers("thr_drill_stall_header"))
        )
        await silent.wait_received(1)
        # §8.4: a stalled source occupies its own connector, never ChatGPT's.
        assert _chatgpt_connections_acquired() == 0
        assert _model_source_connections_acquired() >= 1
        timed_out = await asyncio.wait_for(pending, timeout=10)
    await _drain(async_client)

    assert timed_out.status_code == 504, timed_out.text
    header_error = _error(timed_out)
    assert header_error["code"] == "model_source_timeout"
    assert "response headers within 20s" in header_error["message"], header_error["message"]
    assert b"data:" not in timed_out.content, "the header wait fails before any `200` reaches the client"
    assert timeouts.phases() == ["header"]
    assert breaker.failures(silent_id) == 1

    # (c) three counted failures on one source open the breaker.
    hold = asyncio.Event()
    state = _StubState()
    base_url = await source_upstream(
        _sse_handler(state, before_hold=[], hold=hold, after_hold=[_completed(_USAGE)]),
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    stalling_id = await _designated_source(async_client, tag="drill_stall_breaker", base_url=base_url)
    statuses = [
        (
            await async_client.post(
                CODEX_ROUTE, json=_codex_body(), headers=_native_headers(f"thr_drill_stall_trip_{attempt}")
            )
        ).status_code
        for attempt in range(3)
    ]
    await _drain(async_client)
    assert statuses == [504, 504, 504]
    assert timeouts.phases() == ["header", "first_frame", "first_frame", "first_frame"]
    assert breaker.failures(stalling_id) == 3
    assert breaker.state(stalling_id, time.monotonic()) == "open"

    pinned_thread = "thr_drill_stall_pinned"
    await _write_pin(thread_pin_key(_thread_key(pinned_thread)), kind=PIN_KIND_THREAD, source_id=stalling_id)
    fresh = await async_client.post(CODEX_ROUTE, json=_codex_body(), headers=_native_headers("thr_drill_stall_fresh"))
    pinned = await async_client.post(CODEX_ROUTE, json=_codex_body(), headers=_native_headers(pinned_thread))
    await _drain(async_client)
    hold.set()

    # A fresh request keeps today's answer verbatim; only a pinned conversation is told to retry.
    assert fresh.status_code == 429
    assert fresh.json() == _todays_429(reset_at)
    assert pinned.status_code == 503, pinned.text
    assert _error(pinned)["code"] == MODEL_SOURCE_UNAVAILABLE_CODE
    # The runbook names the literal, so the drill pins the literal as well as
    # the constant: a re-tuned ``RETRY_AFTER_SECONDS`` must edit the row too.
    assert pinned.headers["retry-after"] == str(overflow_module.RETRY_AFTER_SECONDS) == "2"
    assert len(state.requests) == 3, "an open breaker must not reach the source again"
    assert outcomes[-2:] == [
        (ROUTE_CODEX_RESPONSES, "declined_breaker_open"),
        (ROUTE_CODEX_RESPONSES, "pinned_breaker_open"),
    ]
    assert _chatgpt_connections_acquired() == 0


# == Drill 3 -- Silent headers ===================================================================


@pytest.mark.asyncio
async def test_drill_silent_headers_send_nothing_and_leave_no_pin(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runbook row **Silent headers**: the source answers ``200`` and then nothing.

    The open reads the first frame, so the first-frame deadline fires before
    any byte of a ``200`` reaches the client: the answer is the ``504`` error
    document and nothing else, no pin was written and the reservation is
    released.

    The whole request is bounded, because "fails closed" and "fails closed
    *promptly*" are different promises: an open that returned at the headers
    (the pre-hardening shape) would hold the client's stream open until the
    idle window instead, which must fail this test rather than hang the run.
    """

    _forbid_subscription_stream(monkeypatch)
    timeouts = _LabelRecorder()
    monkeypatch.setattr(dispatch_module, "model_source_timeout_total", timeouts)
    _shorten_open_deadlines(monkeypatch)
    await _enable_api_key_auth(async_client)
    key, key_id = await _limited_unscoped_key(async_client, name="drill-silent-headers")
    hold = asyncio.Event()
    scene = await _exhausted_scene(
        async_client,
        source_upstream,
        tag="drill_silent_headers",
        frames=[],
        hold=hold,
        after_hold=[_created(), _completed(_USAGE)],
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )

    response = await asyncio.wait_for(
        async_client.post(
            CODEX_ROUTE,
            json=_codex_body(),
            headers={**_native_headers("thr_drill_silent_headers"), "authorization": f"Bearer {key}"},
        ),
        timeout=15,
    )
    await _drain(async_client)
    hold.set()

    assert response.status_code == 504, response.text
    error = _error(response)
    assert error["code"] == "model_source_timeout"
    assert "the first response frame within 30s" in error["message"], error["message"]
    assert timeouts.phases() == ["first_frame"]
    assert b"data:" not in response.content, "no stream frame may precede the failure"
    assert response.content == json.dumps({"error": error}, separators=(",", ":")).encode()
    assert [(row.status, row.error_code, row.source) for row in await _all_rows()] == [
        ("error", "model_source_timeout", REQUEST_LOG_SOURCE_FRESH)
    ]
    assert await _pin_rows() == []
    assert await _reservation_states(key_id) == [("released", None, None)]
    assert get_source_bulkhead().in_flight(scene.source_id) == 0
    await _wait_for_stub_cancel(scene.state)


# == Drill 4 -- Neutral release ==================================================================

# The six tool shapes a pinned Codex turn can carry, and whether the release
# target (a subscription account) can take them. The four stateless Codex
# declarations are native there (``STATELESS_DECLARABLE_TOOL_TYPES``); a hosted
# tool carries provider- or account-side state (a vector store, a container)
# and is never portable. Every real Codex turn declares *something*, which is
# what makes this matrix -- rather than a body with no ``tools`` key -- the
# drill.
_RELEASE_TOOL_SHAPES: tuple[tuple[str, list[dict[str, Any]], bool], ...] = (
    ("local_shell", [{"type": "local_shell"}], True),
    ("shell-with-description", [{"type": "shell", "description": "run a command"}], True),
    ("apply_patch", [{"type": "apply_patch"}], True),
    (
        "function",
        [
            {
                "type": "function",
                "name": "lookup",
                "description": "look something up",
                "parameters": {"type": "object", "properties": {}},
                "strict": False,
            }
        ],
        True,
    ),
    ("file_search-vector-stores", [{"type": "file_search", "vector_store_ids": ["vs_1"]}], False),
    ("shell-with-container", [{"type": "shell", "container": {"type": "auto"}}], False),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape_id", "tools", "releasable"),
    _RELEASE_TOOL_SHAPES,
    ids=[shape_id for shape_id, _tools, _releasable in _RELEASE_TOOL_SHAPES],
)
async def test_drill_neutral_release_frees_a_source_free_conversation(
    async_client,
    source_upstream,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    shape_id: str,
    tools: list[dict[str, Any]],
    releasable: bool,
) -> None:
    """Runbook row **Neutral release**: disable the source under a pinned conversation.

    A source-free transcript is released *neutrally*: the pin is deleted
    durably first, then a subscription account serves the id-stripped
    transcript, and the row carries the account with no source. A transcript
    the account cannot reproduce keeps its pin and ends with
    ``400 subscription_overflow_source_unavailable`` -- and neither upstream is
    contacted.
    """

    outcomes = _spy_overflow_outcomes(monkeypatch)
    scene = await _exhausted_scene(async_client, source_upstream, tag=f"drill_release_{shape_id[:8]}")
    thread_id = f"thr_drill_release_{shape_id}"
    await _write_pin(thread_pin_key(_thread_key(thread_id)), kind=PIN_KIND_THREAD, source_id=scene.source_id)
    disabled = await async_client.patch(f"/api/model-sources/{scene.source_id}", json={"isEnabled": False})
    assert disabled.status_code == 200, disabled.text
    await _pool_is_healthy(async_client, tag=f"release_{shape_id[:8]}")
    attempts = _forbid_subscription_stream(monkeypatch)
    relayed = _canned_subscription_stream(monkeypatch, response_id="resp_drill_released")
    body = {**_codex_body(), "tools": tools, "input": _source_free_input()}

    with caplog.at_level(logging.WARNING, logger=_OVERFLOW_LOGGER):
        response = await async_client.post(CODEX_ROUTE, json=body, headers=_native_headers(thread_id))
    await _drain(async_client)

    assert scene.state.requests == [], "a disabled source must not be contacted"
    if releasable:
        assert response.status_code == 200, response.text
        created, terminals = _lifecycle(_events(response.text))
        assert created == ["resp_drill_released"]
        assert terminals == ["response.completed"]
        assert await _pin_rows() == [], "the pin is deleted durably before the account serves the thread"
        assert len(relayed) == 1
        relayed_input = relayed[0]["input"]
        assert [item.get("role") for item in relayed_input] == ["user", "assistant"]
        assert all("id" not in item for item in relayed_input), relayed_input
        assert outcomes == [(ROUTE_CODEX_RESPONSES, "pinned_released_neutral")]
        assert "subscription_overflow_pinned_released_neutral" in caplog.text
        rows = await _all_rows()
        assert [(row.account_id is not None, row.model_source_id) for row in rows] == [(True, None)]
    else:
        assert response.status_code == 400, response.text
        error = _error(response)
        assert error["code"] == SOURCE_UNAVAILABLE_CODE
        assert error["type"] == "invalid_request_error"
        assert attempts == [] and relayed == [], "an unservable pinned turn reaches neither upstream"
        assert [pin.source_id for pin in await _pin_rows()] == [scene.source_id], "a refused thread keeps its pin"
        assert outcomes == [(ROUTE_CODEX_RESPONSES, "pinned_unservable_source_disabled")]
        assert "subscription_overflow_pinned_unservable cause=source_disabled" in caplog.text


@pytest.mark.asyncio
async def test_drill_neutral_release_refuses_a_ciphertext_transcript_whatever_it_declares(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the row: a reasoning-bearing conversation gets ``400`` and keeps its pin.

    Reasoning ciphertext is unreleasable even behind a stateless declaration --
    the tool shapes above are the *portable* axis, this is the transcript one.
    """

    outcomes = _spy_overflow_outcomes(monkeypatch)
    scene = await _exhausted_scene(async_client, source_upstream, tag="drill_release_cipher")
    thread_id = "thr_drill_release_cipher"
    await _write_pin(thread_pin_key(_thread_key(thread_id)), kind=PIN_KIND_THREAD, source_id=scene.source_id)
    disabled = await async_client.patch(f"/api/model-sources/{scene.source_id}", json={"isEnabled": False})
    assert disabled.status_code == 200, disabled.text
    await _pool_is_healthy(async_client, tag="release_cipher")
    attempts = _forbid_subscription_stream(monkeypatch)
    body = {**_codex_body(), "tools": [{"type": "local_shell"}], "input": _ciphertext_input()}

    response = await async_client.post(CODEX_ROUTE, json=body, headers=_native_headers(thread_id))
    await _drain(async_client)

    assert response.status_code == 400, response.text
    assert _error(response)["code"] == SOURCE_UNAVAILABLE_CODE
    assert attempts == [] and scene.state.requests == []
    assert [pin.source_id for pin in await _pin_rows()] == [scene.source_id], "a refused thread keeps its pin"
    assert outcomes == [(ROUTE_CODEX_RESPONSES, "pinned_unservable_source_disabled")]


# == Drill 5 -- Clear-then-touch =================================================================


@pytest.mark.asyncio
async def test_drill_clear_then_touch_expires_at_day_seven(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Runbook row **Clear-then-touch**: Off, then keep using a pinned conversation daily.

    Rehearsed without waiting a week: the clear time is placed in the past and
    the drain deadline written to what ``PUT /api/settings`` would have armed
    then, so the real touch path and the real drain cap run against a
    *reachable* state. Day 6 -- the source still serves the conversation and
    the touch slides its expiry to the clear plus ``PIN_IDLE_TTL``, never
    beyond, with ``purge_at`` still inside the lookup window. Day 8 -- the row
    is a tombstone: a ciphertext turn is refused permanently and a source-free
    one is released to an account. At ``drain_until`` -- the row's last
    promise -- the retention pass finds every remaining row purgeable and the
    table ends empty, with no drain-invariant alarm.
    """

    attempts = _forbid_subscription_stream(monkeypatch)
    outcomes = _spy_overflow_outcomes(monkeypatch)
    scene = await _exhausted_scene(async_client, source_upstream, tag="drill_drain")

    # -- day 6 of the drain window: the source still owns the conversation ---------------------
    day_six_clear = _aware(utcnow()) - timedelta(days=6)
    drain_until = day_six_clear + DRAIN_WINDOW
    live_thread = "thr_drill_drain_live"
    live_key = thread_pin_key(_thread_key(live_thread))
    await _write_pin(
        live_key,
        kind=PIN_KIND_THREAD,
        source_id=scene.source_id,
        now=day_six_clear - timedelta(hours=1),
        drain_until=drain_until,
    )
    await _set_overflow_settings(source_id=None, drain_until=drain_until)

    served = await async_client.post(
        CODEX_ROUTE,
        json={**_codex_body(), "input": _ciphertext_input()},
        headers=_native_headers(live_thread),
    )
    await _drain(async_client)

    assert served.status_code == 200, served.text
    assert len(scene.state.requests) == 1
    assert attempts == [], "a draining conversation never falls back to a subscription account"
    assert [(row.status, row.source) for row in await _all_rows()] == [("success", REQUEST_LOG_SOURCE_PINNED)]
    assert outcomes == [(ROUTE_CODEX_RESPONSES, "dispatched_pinned")]

    expected_expiry = day_six_clear + PIN_IDLE_TTL
    rows = await _pin_rows()
    # The touched thread row plus the anchor the served turn minted: the cap
    # must hold for every row the drain produces, not only the one it slides.
    anchor_key = anchor_pin_key(None, "resp_dispatch_1")
    assert {row.pin_key for row in rows} == {anchor_key, live_key}
    for row in rows:
        touched_expiry = _aware(row.expires_at)
        purge_at = _aware(row.purge_at)
        # The touch may not push the row past the clear + TTL cap ...
        assert abs((touched_expiry - expected_expiry).total_seconds()) < 5, (touched_expiry, expected_expiry)
        # ... and the tombstone grace must stay inside the lookup window (CL-3).
        assert purge_at == touched_expiry + PIN_TOMBSTONE_GRACE
        assert purge_at < drain_until, (purge_at, drain_until)

    # -- day 8: past the pin idle TTL, the row is a tombstone -----------------------------------
    day_eight_clear = _aware(utcnow()) - timedelta(days=8)
    tombstone_drain_until = day_eight_clear + DRAIN_WINDOW
    tombstone_thread = "thr_drill_drain_tombstone"
    await _write_pin(
        thread_pin_key(_thread_key(tombstone_thread)),
        kind=PIN_KIND_THREAD,
        source_id=scene.source_id,
        now=day_eight_clear - timedelta(hours=1),
        drain_until=tombstone_drain_until,
    )
    await _set_overflow_settings(source_id=None, drain_until=tombstone_drain_until)
    await _pool_is_healthy(async_client, tag="drain_tombstone")
    outcomes.clear()

    refused = await async_client.post(
        CODEX_ROUTE,
        json={**_codex_body(), "input": _ciphertext_input()},
        headers=_native_headers(tombstone_thread),
    )
    await _drain(async_client)

    assert refused.status_code == 400, refused.text
    tombstone_error = _error(refused)
    assert tombstone_error["code"] == UNSUPPORTED_INPUT_CODE
    assert "has expired" in tombstone_error["message"], tombstone_error["message"]
    assert attempts == [], "an expired conversation must not be replayed on an account"
    assert len(scene.state.requests) == 1, "nor forwarded to the source"
    assert outcomes == [(ROUTE_CODEX_RESPONSES, "pinned_unservable_tombstone")]

    # A source-free transcript on the same tombstone is released instead of refused.
    relayed = _canned_subscription_stream(monkeypatch, response_id="resp_drill_drain_released")
    outcomes.clear()
    released = await async_client.post(
        CODEX_ROUTE,
        json={**_codex_body(), "tools": [{"type": "local_shell"}], "input": _source_free_input()},
        headers=_native_headers(tombstone_thread),
    )
    await _drain(async_client)

    assert released.status_code == 200, released.text
    assert len(relayed) == 1
    assert outcomes == [(ROUTE_CODEX_RESPONSES, "pinned_released_neutral")]
    survivors = await _pin_rows()
    assert {row.pin_key for row in survivors} == {anchor_key, live_key}, "the tombstone is gone, the day-6 rows remain"

    # -- the drain deadline: the retention pass finds nothing left ------------------------------
    # The rows above belong to a window that closes three weeks from now, so
    # the deadline is rehearsed one last touch later: the same rows re-written
    # an hour before a window that has just closed. That is the state CL-3
    # guarantees production reaches -- every write inside the drain is capped
    # to ``purge_at < drain_until`` however late it lands, which is *why* the
    # table can be empty at the deadline rather than merely stale. The cap is
    # production's, not the test's, and the pass that deletes the rows is the
    # hourly one, run here with retention disabled as it is by default.
    closed_drain_until = _aware(utcnow()) - timedelta(minutes=1)
    for row in survivors:
        await _write_pin(
            row.pin_key,
            kind=row.kind,
            source_id=row.source_id,
            api_key_id=row.api_key_id,
            now=closed_drain_until - timedelta(hours=1),
            drain_until=closed_drain_until,
        )
    await _set_overflow_settings(source_id=None, drain_until=closed_drain_until)
    assert {row.pin_key for row in await _pin_rows() if _aware(row.purge_at) < closed_drain_until} == {
        row.pin_key for row in survivors
    }, "every row the drain wrote is purgeable once its window has closed"

    with caplog.at_level(logging.WARNING, logger=_RETENTION_LOGGER):
        pruned = await run_retention_pass()

    assert pruned["model_source_pins"] == len(survivors)
    assert await _pin_rows() == [], "the pin table is empty at the drain deadline"
    assert "model_source_pins_drain_invariant_violated" not in caplog.text


# == Drill 6 -- Kill switches ====================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("switch", "pinned_status", "clears_designation", "fresh_outcome", "pinned_outcome"),
    [
        ("off", 200, True, "declined_drain_mode", "dispatched_pinned"),
        ("disable-source", 400, False, "declined_no_source", "pinned_unservable_source_disabled"),
        ("delete-source", 400, True, "declined_drain_mode", "pinned_unservable_source_deleted"),
    ],
)
async def test_drill_kill_switches_restore_subscription_behaviour(
    async_client,
    source_upstream,
    monkeypatch: pytest.MonkeyPatch,
    switch: str,
    pinned_status: int,
    clears_designation: bool,
    fresh_outcome: str,
    pinned_outcome: str,
) -> None:
    """Runbook row **Kill switches**: Off, then disable, then delete the source.

    Each switch returns a fresh exhausted request to today's answer *byte for
    byte* -- proven twice over, against the same request served with the
    decision module stubbed out and against the committed golden literal --
    with no hint header and no ``Retry-After``. A pinned reasoning-bearing
    conversation never reaches a subscription account either way: while the
    drain is armed its source keeps serving it -- which is what the row means
    by "pinned conversations drain", asserted here as the served turn, its
    ``subscription_overflow_pinned`` row, the ``dispatched_pinned`` outcome and
    the surviving pin rows, still the source's and still inside the idle TTL --
    and once the source is gone it ends with ``400`` and keeps its pin. A
    *source-free* conversation on that same gone source is released to an
    account instead, which is the row's other half.
    """

    attempts = _forbid_subscription_stream(monkeypatch)
    outcomes = _spy_overflow_outcomes(monkeypatch)
    scene = await _exhausted_scene(async_client, source_upstream, tag=f"drill_switch_{switch[:6]}")
    pinned_thread = f"thr_drill_switch_{switch}"
    pinned_key = thread_pin_key(_thread_key(pinned_thread))
    await _write_pin(pinned_key, kind=PIN_KIND_THREAD, source_id=scene.source_id)

    if switch == "off":
        await _designate(async_client, None)
    elif switch == "disable-source":
        disabled = await async_client.patch(f"/api/model-sources/{scene.source_id}", json={"isEnabled": False})
        assert disabled.status_code == 200, disabled.text
    else:
        deleted = await async_client.delete(f"/api/model-sources/{scene.source_id}")
        assert deleted.status_code in {200, 204}, deleted.text

    settings = await async_client.get("/api/settings")
    assert settings.status_code == 200, settings.text
    designation = settings.json()["subscriptionOverflowSourceId"]
    drain_until = settings.json()["subscriptionOverflowDrainUntil"]
    # Off and delete clear the designation and arm the drain in the same
    # transaction; disabling is deliberately asymmetric -- it stops the source
    # now and leaves the designation (and so no drain window) in place, which
    # is why a disabled source refuses its pinned conversations instead of
    # draining them.
    if clears_designation:
        assert designation is None and drain_until is not None, settings.text
    else:
        assert designation == scene.source_id and drain_until is None, settings.text

    real, stubbed = await _twice(
        async_client,
        monkeypatch,
        lambda: async_client.post(
            CODEX_ROUTE,
            json=_codex_body(),
            headers=_native_headers("thr_drill_switch_fresh", session_id=f"sess_drill_switch_{switch}"),
        ),
    )
    status, headers, content = real

    assert real == stubbed, "a switched-off overflow must be indistinguishable from never having shipped"
    assert status == 429
    assert content == _golden_429_bytes(scene.reset_at)
    assert "x-codex-promo-message" not in headers
    assert "retry-after" not in headers

    pinned = await async_client.post(
        CODEX_ROUTE,
        json={**_codex_body(), "input": _ciphertext_input()},
        headers=_native_headers(pinned_thread),
    )
    await _drain(async_client)

    assert pinned.status_code == pinned_status, pinned.text
    assert attempts == [], "a reasoning-bearing pinned conversation never lands on an account"
    assert outcomes == [
        (ROUTE_CODEX_RESPONSES, fresh_outcome),
        (ROUTE_CODEX_RESPONSES, pinned_outcome),
    ]
    rows = [(row.status, row.source, row.error_code) for row in await _all_rows()]
    # The ``_twice`` pair: two rows of today's decline, with no source
    # attribution on either -- a declined request is not an overflow attempt.
    assert rows[:2] == [("error", None, "usage_limit_reached")] * 2
    pins = {row.pin_key: row for row in await _pin_rows()}
    if pinned_status == 200:
        # "Pinned conversations drain for <= 7 days": the source served the
        # turn, the row says which source kind it was, the pin (and the anchor
        # the served turn minted) survived the switch still pointing at that
        # source, and neither is live beyond the idle TTL.
        assert rows[2:] == [("success", REQUEST_LOG_SOURCE_PINNED, None)]
        assert set(pins) == {pinned_key, anchor_pin_key(None, "resp_dispatch_1")}
        assert {row.source_id for row in pins.values()} == {scene.source_id}
        now = _aware(utcnow())
        for row in pins.values():
            assert now < _aware(row.expires_at) <= now + PIN_IDLE_TTL, (row.pin_key, row.expires_at)
    else:
        assert _error(pinned)["code"] == SOURCE_UNAVAILABLE_CODE
        # A refusal is not a release: the conversation stays the source's, and
        # a turn that never reached an upstream writes no request-log row.
        assert rows[2:] == []
        assert set(pins) == {pinned_key}
        assert pins[pinned_key].source_id == scene.source_id

        # "... releases source-free conversations": the other half of the same
        # switch, on a transcript an account can reproduce. The delete leg is
        # the only rehearsal anywhere that deletes a source and then releases a
        # conversation off it -- the cause differs (``source_deleted`` vs
        # ``source_disabled``) even though the release path is shared.
        await _pool_is_healthy(async_client, tag=f"switch_{switch[:6]}")
        relayed = _canned_subscription_stream(monkeypatch, response_id=f"resp_drill_switch_{switch[:6]}")
        released = await async_client.post(
            CODEX_ROUTE,
            json={**_codex_body(), "tools": [{"type": "local_shell"}], "input": _source_free_input()},
            headers=_native_headers(pinned_thread),
        )
        await _drain(async_client)

        assert released.status_code == 200, released.text
        assert len(relayed) == 1, "the release target is a subscription account"
        assert outcomes[-1] == (ROUTE_CODEX_RESPONSES, "pinned_released_neutral")
        assert await _pin_rows() == [], "a released conversation is no longer the source's"
        assert (await _all_rows())[-1].account_id is not None
    assert get_source_bulkhead().in_flight(scene.source_id) == 0


@pytest.mark.asyncio
async def test_kill_switch_fast_path_returns_only_once_the_drain_deadline_elapses(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half the runbook never said: Off restores the *answer* at once, the zero-cost path at ``drain_until``.

    While the drain is armed the request path still performs its bounded pin
    read; the bytes are unchanged, the cost is not. Only when the deadline has
    passed does ``_settings_off`` take the fast path again and the probe,
    lookup, selection, portability walk and admission claim all fall to zero.
    """

    _forbid_subscription_stream(monkeypatch)
    scene = await _exhausted_scene(async_client, source_upstream, tag="drill_fast_path")
    cleared = await _designate(async_client, None)
    assert cleared["subscriptionOverflowDrainUntil"] is not None

    armed_counters = _forbid_configured_only_work(monkeypatch)
    armed = await async_client.post(CODEX_ROUTE, json=_codex_body(), headers=_native_headers("thr_drill_fast_armed"))
    assert armed.status_code == 429
    assert armed.json() == _todays_429(scene.reset_at)
    assert armed_counters["lookup_pin_bounded"] >= 1, armed_counters

    await _set_overflow_settings(source_id=None, drain_until=_aware(utcnow()) - timedelta(minutes=1))
    with pytest.MonkeyPatch.context() as elapsed_patch:
        elapsed_counters = _forbid_configured_only_work(elapsed_patch)
        elapsed = await async_client.post(
            CODEX_ROUTE, json=_codex_body(), headers=_native_headers("thr_drill_fast_elapsed")
        )

    assert elapsed.status_code == 429
    assert elapsed.json() == _todays_429(scene.reset_at)
    assert elapsed_counters == dict.fromkeys(elapsed_counters, 0), elapsed_counters


# == Drill 7 -- Anchor timing ====================================================================


@pytest.mark.asyncio
async def test_drill_anchor_timing_refuses_an_unanchorable_sdk_turn(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runbook row **Anchor timing**, failing half: an id that only arrives in the terminal.

    A source whose ``response.id`` is unknown while the first content frame is
    in flight cannot serve SDK overflow at all: the turn is refused with
    ``subscription_overflow_pin_unavailable`` before any source content reaches
    the client, neither the anchor nor the thread pin is written, the row is an
    error and the API-key reservation is released. This is the observable the
    operator reads off the canary's SSE stream, in the form the proxy takes
    when the answer is "no".
    """

    attempts = _forbid_subscription_stream(monkeypatch)
    outcomes = _spy_overflow_outcomes(monkeypatch)
    await _enable_api_key_auth(async_client)
    key, key_id = await _limited_unscoped_key(async_client, name="drill-anchor-late")
    sdk_headers = {"user-agent": "openai-python/1.99", "authorization": f"Bearer {key}"}
    hold = asyncio.Event()
    scene = await _exhausted_scene(
        async_client,
        source_upstream,
        tag="drill_anchor_late",
        frames=[_DELTA],
        hold=hold,
        after_hold=[_completed(_USAGE, "resp_drill_anchor_late")],
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    _release_hold_on_pin_commit(monkeypatch, hold)

    try:
        refused = await async_client.post(V1_ROUTE, json=_codex_body(), headers=sdk_headers)
    finally:
        hold.set()  # safety net: unblock the stub even if the commit is never reached
    await _drain(async_client)

    assert refused.status_code == 200, refused.text  # the lifecycle is carried by the SSE terminal
    events = _events(refused.text)
    assert [event["type"] for event in events] == ["response.created", "response.failed"]
    assert events[1]["response"]["error"]["code"] == PIN_UNAVAILABLE_CODE
    assert "hello from the source" not in refused.text, "no source content may reach an unanchored client"
    assert await _pin_rows() == [], "an unanchorable turn writes neither the anchor nor the thread pin"
    assert [(row.status, row.error_code, row.source) for row in await _all_rows()] == [
        ("error", PIN_UNAVAILABLE_CODE, REQUEST_LOG_SOURCE_FRESH)
    ]
    assert outcomes == [
        (ROUTE_V1_RESPONSES, "dispatched_fresh"),
        (ROUTE_V1_RESPONSES, "pin_commit_failed"),
    ]
    assert await _reservation_states(key_id) == [("released", None, None)]
    assert attempts == []
    assert get_source_bulkhead().in_flight(scene.source_id) == 0


@pytest.mark.asyncio
async def test_drill_anchor_timing_accepts_a_source_that_mints_its_id_first(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The passing half of the same row: an early ``response.created`` id anchors the chain to its source.

    Whether the *real* source mints its id that early is the canary's job (the
    stub here is built to); what this pins is the proxy's half -- given an id
    on ``response.created``, that event still reaches the client before any
    content-bearing frame, the anchor row exists and the turn is ``200``, and
    the next SDK turn goes back to the same source on the strength of that row.
    The follow-up runs against a *healthy* pool on purpose -- the anchor row,
    not exhaustion, is what must send it back to the source.
    """

    attempts = _forbid_subscription_stream(monkeypatch)
    outcomes = _spy_overflow_outcomes(monkeypatch)
    await _enable_api_key_auth(async_client)
    key, key_id = await _limited_unscoped_key(async_client, name="drill-anchor-early")
    sdk_headers = {"user-agent": "openai-python/1.99", "authorization": f"Bearer {key}"}
    scene = await _exhausted_scene(
        async_client,
        source_upstream,
        tag="drill_anchor_early",
        frames=[_created("resp_drill_anchor"), _ITEM_ADDED, _DELTA, _completed(_USAGE, "resp_drill_anchor")],
    )

    anchored = await async_client.post(V1_ROUTE, json=_codex_body(), headers=sdk_headers)
    await _drain(async_client)

    assert anchored.status_code == 200, anchored.text
    assert _lifecycle(_events(anchored.text)) == (["resp_drill_anchor"], ["response.completed"])
    # "... **before** its first content-bearing frame": the ordering the row is
    # about, read off the client's stream rather than assumed from the stub --
    # a proxy that buffered ``response.created`` behind the first delta would
    # leave an SDK client unable to anchor its follow-up.
    event_types = [event["type"] for event in _events(anchored.text)]
    content_at = [index for index, name in enumerate(event_types) if name in _CONTENT_BEARING_EVENTS]
    assert content_at, event_types
    assert event_types.index("response.created") < content_at[0], event_types
    assert [(pin.kind, pin.pin_key, pin.source_id) for pin in await _pin_rows()] == [
        (PIN_KIND_ANCHOR, anchor_pin_key(key_id, "resp_drill_anchor"), scene.source_id)
    ]

    await _pool_is_healthy(async_client, tag="drill_anchor_early")
    follow_up = await async_client.post(
        V1_ROUTE,
        json={**_codex_body(), "previous_response_id": "resp_drill_anchor"},
        headers=sdk_headers,
    )
    await _drain(async_client)

    assert follow_up.status_code == 200, follow_up.text
    assert len(scene.state.requests) == 2
    assert scene.state.requests[1]["previous_response_id"] == "resp_drill_anchor"
    assert outcomes == [
        (ROUTE_V1_RESPONSES, "dispatched_fresh"),
        (ROUTE_V1_RESPONSES, "dispatched_anchor"),
    ]
    assert [(row.status, row.source) for row in await _all_rows()] == [
        ("success", REQUEST_LOG_SOURCE_FRESH),
        ("success", REQUEST_LOG_SOURCE_PINNED),
    ]
    assert attempts == []


# == Adjacent behaviour the Stall row does not cover =============================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "headers"),
    [
        (CODEX_ROUTE, _native_headers("thr_drill_idle_native")),
        (V1_ROUTE, {"user-agent": "openai-python/1.99"}),
    ],
    ids=["codex-native", "v1-sdk"],
)
async def test_mid_stream_idle_cut_truncates_the_stream_without_a_terminal(
    async_client,
    source_upstream,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    headers: dict[str, str],
) -> None:
    """A source that goes silent *after* its first frame truncates the answer; it does not synthesize one.

    The three stall drills all fail before a ``200``. This is the fourth
    window, and the only one where bytes have already left: the idle cap
    (``min(stream_idle_timeout_seconds, 300)``) cuts the relay and the
    forwarding error comes out of the ASGI body, for native and SDK shaping
    alike -- so the client sees a truncated stream with no terminal event.
    ``_normalize_public_responses_stream`` synthesizes only on normal
    exhaustion (design Appendix B); a synthesized terminal here would be a
    deliberate change, not a bug fix, so the drill records what is.
    """

    _forbid_subscription_stream(monkeypatch)
    hold = asyncio.Event()
    scene = await _exhausted_scene(
        async_client,
        source_upstream,
        tag=f"drill_idle_{path.count('/')}",
        frames=[_created(), _ITEM_ADDED, _DELTA],
        hold=hold,
        after_hold=[_completed(_USAGE)],
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    idle = await async_client.put("/api/settings", json={"streamIdleTimeoutSeconds": 1.0})
    assert idle.status_code == 200, idle.text
    thread_id = headers.get("thread-id")
    breaker = overflow_module.get_source_breaker()
    stream = _AsgiStream(
        app=_app(async_client),
        path=path,
        headers=headers,
        body=json.dumps(_codex_body()).encode(),
    )

    with pytest.raises(forwarding_module.ModelSourceForwardingError) as raised:
        await asyncio.wait_for(stream.run(), timeout=30)
    await _drain(async_client)
    hold.set()

    assert raised.value.status_code == 504
    assert raised.value.timeout_phase == "idle"
    assert stream.status == 200, "the 200 status line was already on the wire"
    wire = stream.received().decode()
    assert "hello from the source" in wire
    assert "response.completed" not in wire and "response.failed" not in wire
    assert [(row.status, row.error_code, row.source) for row in await _all_rows()] == [
        ("error", "model_source_idle_timeout", REQUEST_LOG_SOURCE_FRESH)
    ]
    assert breaker.failures(scene.source_id) == 1
    # The pin landed at the first content frame, before the cut: the
    # conversation stays the source's, which is why the next turn is routed
    # there rather than replayed on an account.
    if thread_id is not None:
        assert thread_pin_key(_thread_key(thread_id)) in {pin.pin_key for pin in await _pin_rows()}
    assert get_source_bulkhead().in_flight(scene.source_id) == 0
