"""WebSocket parity for subscription-exhaustion overflow (#2123 WP-D folded into WP-C2).

Two helpers back the two mixin call sites:

* ``bounce_exhausted_websocket_turn`` runs only after account selection
  answered ``usage_limit_reached``. An eligible turn is bounced in-band with a
  60 s bounce row and the wrapped ``{"type": "error", "status": 503, ...}``
  connect-failure event; every decline leaves today's 429 event byte-identical.
* ``bounce_pinned_or_anchored_websocket_turn`` bounces pinned threads (live or
  tombstoned), live anchors without a recorded subscription owner, and lookup
  failures (fail-closed toward HTTP), on the first turn and on a reused socket.

The pure eligibility helpers (``overflow_thread_key``, ``fresh_decline_reason``,
``portability_decline``) belong to ``app.modules.proxy.overflow`` and are
stubbed at this module's import seam; the integration suite exercises the real
ones end to end.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import pytest
from fastapi import WebSocket

import app.core.metrics.prometheus as prometheus_module
import app.modules.proxy._service.websocket.overflow as ws_overflow
import app.modules.proxy.service as proxy_service
from app.core.clock import clock_for, scheduler_for
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.affinity import _codex_backend_identity
from app.modules.proxy.load_balancer import AccountSelection
from app.modules.proxy.model_source_pins import (
    PIN_KIND_ANCHOR,
    PIN_KIND_BOUNCE,
    PIN_KIND_THREAD,
    PinIntent,
    PinLookupResult,
    PinLookupTimeout,
    PinRecord,
    PinWrite,
    anchor_pin_key,
    bounce_pin_key,
    thread_pin_key,
)
from app.modules.proxy.overflow import OVERFLOW_OUTCOMES, WS_BOUNCE_CODE
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler
from tests.unit.test_proxy_utils import (
    _make_account,
    _make_proxy_settings,
    _QueuedTestUpstreamWebSocket,
    _repo_factory,
    _RequestLogsRecorder,
    _SettingsCache,
)
from tests.unit.test_proxy_websocket_model_source_guard import _completed_turn, _create_frame, _Downstream

pytestmark = pytest.mark.unit

_THREAD_ID = "thr_ws_overflow_0001"
_SOURCE_ID = "src_overflow"
_MODEL = "gpt-5.4"
_RESETS_AT = 1_700_003_600
_NON_RETRYABLE_CODES = {"server_is_overloaded", "slow_down"}
_NATIVE_HEADERS = {
    "user-agent": "codex_cli_rs/0.150.0 (Linux)",
    "originator": "codex_cli_rs",
    "thread-id": _THREAD_ID,
    "session-id": "sess_ws_overflow",
}


# --- fixtures and fakes ----------------------------------------------------------------------------


def _thread_key(thread_id: str = _THREAD_ID) -> str:
    """The ``thread_only`` selection key the pin table is keyed by (never the process-thread form)."""

    key = _codex_backend_identity({"thread-id": thread_id}).thread_selection_key
    assert key is not None
    return key


def _fake_overflow_thread_key(headers: Any) -> str | None:
    thread_id = next((value for key, value in headers.items() if key.lower() == "thread-id"), None)
    return _thread_key(thread_id) if thread_id else None


def _api_key(key_id: str = "key_ws_overflow") -> ApiKeyData:
    return ApiKeyData(
        id=key_id,
        name="ws overflow",
        key_prefix="sk-test-ws-overflow",
        allowed_models=[],
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 1, 1),
        last_used_at=None,
    )


def _settings(*, source_id: str | None = None, drain_until: datetime | None = None) -> SimpleNamespace:
    settings = _make_proxy_settings()
    settings.subscription_overflow_source_id = source_id
    settings.subscription_overflow_drain_until = drain_until
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    return settings


def _install_settings(monkeypatch: pytest.MonkeyPatch, settings: SimpleNamespace) -> None:
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))


class _Counter:
    def __init__(self) -> None:
        self.samples: list[tuple[str, str]] = []

    def labels(self, **labels: str) -> SimpleNamespace:
        def inc(amount: float = 1.0) -> None:
            del amount
            self.samples.append((labels["route"], labels["outcome"]))

        return SimpleNamespace(inc=inc)

    def outcomes(self) -> list[str]:
        assert all(route == "websocket" for route, _ in self.samples)
        return [outcome for _, outcome in self.samples]


def _install_counter(monkeypatch: pytest.MonkeyPatch) -> _Counter:
    counter = _Counter()
    monkeypatch.setattr(prometheus_module, "subscription_overflow_total", counter, raising=False)
    return counter


class _BounceExecutor:
    """Stands in for the base ``PinWriteExecutor``; records the intent and drain cap of every commit."""

    def __init__(self, outcome: str = "written", *, error: BaseException | None = None) -> None:
        self.outcome = outcome
        self.error = error
        self.commits: list[tuple[PinIntent, datetime | None, object, object]] = []

    async def commit(self, intent: PinIntent, *, drain_until: datetime | None, scheduler: object, clock: object) -> str:
        self.commits.append((intent, drain_until, scheduler, clock))
        if self.error is not None:
            raise self.error
        return self.outcome


def _install_bounce_executor(
    monkeypatch: pytest.MonkeyPatch, executor: _BounceExecutor | None = None
) -> _BounceExecutor:
    executor = executor or _BounceExecutor()
    monkeypatch.setattr(ws_overflow, "_BOUNCE_EXECUTOR", executor)
    return executor


class _PureHelpers:
    """The ``app.modules.proxy.overflow`` seam: records every call so the ship-dark spy can assert zero of them."""

    def __init__(
        self,
        *,
        decline: str | None = None,
        portability: tuple[str | None, str | None] = (None, None),
        source: object | None = None,
        raise_in_decline: BaseException | None = None,
    ) -> None:
        self.decline = decline
        self.portability = portability
        self.source = SimpleNamespace(id=_SOURCE_ID) if source is None else source
        self.raise_in_decline = raise_in_decline
        self.decline_calls: list[dict[str, Any]] = []
        self.select_calls: list[tuple[object, ...]] = []
        self.portability_calls: list[dict[str, Any]] = []
        self.thread_key_calls = 0
        self.fast_decline = object()
        self.breaker = object()

    def install(self, monkeypatch: pytest.MonkeyPatch, *, select_result: object = "default") -> None:
        helpers = self

        def thread_key(headers: Any) -> str | None:
            helpers.thread_key_calls += 1
            return _fake_overflow_thread_key(headers)

        def fresh_decline_reason(headers: Any, api_key: Any, **kwargs: object) -> str | None:
            helpers.decline_calls.append({"headers": dict(headers), "api_key": api_key, **kwargs})
            if helpers.raise_in_decline is not None:
                raise helpers.raise_in_decline
            return helpers.decline

        async def select_overflow_model_source(*args: object, **kwargs: object) -> object:
            helpers.select_calls.append((args, kwargs))
            if select_result == "default":
                return (helpers.source, _MODEL)
            return select_result

        def portability_decline(body: Any, headers: Any, *, source: object, model: str) -> tuple[Any, Any]:
            helpers.portability_calls.append({"body": body, "headers": dict(headers), "source": source, "model": model})
            return helpers.portability

        monkeypatch.setattr(ws_overflow, "overflow_thread_key", thread_key)
        monkeypatch.setattr(ws_overflow, "fresh_decline_reason", fresh_decline_reason)
        monkeypatch.setattr(ws_overflow, "select_overflow_model_source", select_overflow_model_source)
        monkeypatch.setattr(ws_overflow, "portability_decline", portability_decline)
        monkeypatch.setattr(ws_overflow, "get_fast_decline_set", lambda: helpers.fast_decline)
        monkeypatch.setattr(ws_overflow, "get_source_breaker", lambda: helpers.breaker)


def _request_state(
    *,
    request_id: str = "req_ws_overflow",
    model: str = _MODEL,
    frame: dict[str, object] | None = None,
    **overrides: Any,
) -> proxy_service._WebSocketRequestState:
    body = {"type": "response.create", "model": model, "input": [{"role": "user", "content": "hi"}], "stream": True}
    if frame is not None:
        body = frame
    state = proxy_service._WebSocketRequestState(
        request_id=request_id,
        model=model,
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )
    state.fresh_upstream_request_text = json.dumps(body, separators=(",", ":"))
    for name, value in overrides.items():
        setattr(state, name, value)
    return state


def _usage_limit_selection() -> AccountSelection:
    return AccountSelection(
        account=None,
        error_message="Rate limit exceeded. Try again in 1h",
        error_code="usage_limit_reached",
        resets_at=_RESETS_AT,
    )


def _pin_record(pin_key: str, *, kind: str, expires_in: timedelta, source_id: str = _SOURCE_ID) -> PinRecord:
    now = datetime.now(timezone.utc)
    return PinRecord(
        pin_key=pin_key,
        kind=kind,
        source_id=source_id,
        api_key_id=None,
        created_at=now - timedelta(hours=1),
        last_seen_at=now - timedelta(minutes=1),
        expires_at=now + expires_in,
        purge_at=now + expires_in + timedelta(days=21),
    )


class _PinStore:
    """``lookup_pin_bounded`` stand-in keyed by pin key; records the scheduler/clock every lookup was given."""

    def __init__(self, results: dict[str, PinLookupResult] | None = None, *, error: Exception | None = None) -> None:
        self.results = results or {}
        self.error = error
        self.lookups: list[tuple[str, object, object, object]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = self

        async def lookup_pin_bounded(
            pin_key: str, *, cache: object, scheduler: object, clock: object
        ) -> PinLookupResult:
            store.lookups.append((pin_key, cache, scheduler, clock))
            if store.error is not None:
                raise store.error
            return store.results.get(pin_key, PinLookupResult("none", None))

        monkeypatch.setattr(ws_overflow, "lookup_pin_bounded", lookup_pin_bounded)


async def _run_selection(
    service: proxy_service.ProxyService,
    request_state: proxy_service._WebSocketRequestState,
    *,
    headers: dict[str, str] | None,
    api_key: ApiKeyData | None = None,
    websocket_send: AsyncMock | None = None,
) -> tuple[object, AsyncMock]:
    """Drive ``_select_websocket_connect_account`` into the exhausted branch with the real connect-failure emitter."""

    websocket_send = websocket_send or AsyncMock()
    result = await service._select_websocket_connect_account(
        10_000.0,
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        prefer_earlier_reset_window="secondary",
        routing_strategy="usage_weighted",
        model=request_state.model,
        request_state=request_state,
        api_key=api_key,
        client_send_lock=anyio.Lock(),
        websocket=cast(WebSocket, SimpleNamespace(send_text=websocket_send)),
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
        exclude_account_ids=set(),
        preferred_account_id=None,
        headers=headers,
    )
    return result, websocket_send


def _service(monkeypatch: pytest.MonkeyPatch) -> tuple[proxy_service.ProxyService, _RequestLogsRecorder, AsyncMock]:
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    monkeypatch.setattr(service, "_select_account_with_budget", AsyncMock(return_value=_usage_limit_selection()))
    released = AsyncMock()
    monkeypatch.setattr(service, "_release_websocket_request_state_reservation", released)
    return service, request_logs, released


def _sent_text(websocket_send: AsyncMock) -> str:
    call = websocket_send.await_args
    assert call is not None, "an event must reach the client"
    return cast(str, call.args[0])


def _sent_event(websocket_send: AsyncMock) -> dict[str, Any]:
    assert websocket_send.await_count == 1, "exactly one event reaches the client"
    return json.loads(_sent_text(websocket_send))


# --- fresh exhaustion: in-band 503 + bounce row -----------------------------------------------------------


@pytest.mark.asyncio
async def test_exhausted_eligible_turn_bounces_with_503_event_and_bounce_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Design §7.3 row 1: eligible fresh exhaustion over WebSocket -> bounce row + wrapped 503 event."""

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    helpers = _PureHelpers()
    helpers.install(monkeypatch)
    service, request_logs, released = _service(monkeypatch)
    request_state = _request_state()

    result, websocket_send = await _run_selection(service, request_state, headers=_NATIVE_HEADERS)

    assert result is None
    event = _sent_event(websocket_send)
    assert event["type"] == "error"
    assert event["status"] == 503, "a 4xx is terminal client-side and would strand the session on the socket"
    assert isinstance(event["status"], int), "Codex ignores an error event without a numeric top-level status"
    assert event["error"]["code"] == WS_BOUNCE_CODE == "model_source_requires_http_transport"
    assert event["error"]["type"] == "server_error"
    assert event["error"]["code"] not in _NON_RETRYABLE_CODES
    assert "resets_at" not in event["error"], "the bounce is not a usage-limit answer"

    # The bounce row: thread-keyed (never per API key), the source of the decision, a bounce kind.
    assert len(executor.commits) == 1
    intent, drain_until, scheduler, clock = executor.commits[0]
    assert intent.writes == (PinWrite(bounce_pin_key(_thread_key()), PIN_KIND_BOUNCE, _SOURCE_ID, None),)
    assert intent.thread_key == _thread_key()
    assert drain_until is None
    assert scheduler is scheduler_for(service) and clock is clock_for(service)
    assert bounce_pin_key(_thread_key()).startswith("bounce\n")

    # Today's connect-failure lifecycle: reservation released, row finalized, counted once.
    released.assert_awaited_once_with(request_state)
    assert await service.drain_persistence_tasks(timeout_seconds=1)
    assert request_logs.calls and request_logs.calls[-1]["error_code"] == WS_BOUNCE_CODE
    assert request_logs.calls[-1]["status"] == "error"
    assert request_logs.calls[-1]["account_id"] is None
    assert counter.outcomes() == ["bounced_ws_event"]

    # Eligibility was judged against the frame body as the HTTP route would see it (no envelope/telemetry).
    assert helpers.decline_calls[0]["thread_key"] == _thread_key()
    assert helpers.decline_calls[0]["source_id"] == _SOURCE_ID
    assert helpers.decline_calls[0]["fast_decline"] is helpers.fast_decline
    assert helpers.decline_calls[0]["breaker"] is helpers.breaker
    assert helpers.select_calls[0][0] == (_SOURCE_ID, _MODEL, None)
    assert helpers.select_calls[0][1] == {"raw_model": None, "require_streaming": True}
    judged = helpers.portability_calls[0]["body"]
    assert "type" not in judged and judged["model"] == _MODEL and judged["stream"] is True


@pytest.mark.asyncio
async def test_bounce_row_records_the_api_key_but_is_never_keyed_by_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutant: a bounce row keyed per API key would miss the re-handshake of a rotated key (CP-17)."""

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    _PureHelpers().install(monkeypatch)
    service, _, _ = _service(monkeypatch)
    api_key = _api_key("key_rotated")
    request_state = _request_state(api_key=api_key)

    await _run_selection(service, request_state, headers=_NATIVE_HEADERS, api_key=api_key)

    (intent, *_rest), *_ = executor.commits
    (write,) = intent.writes
    assert write.pin_key == bounce_pin_key(_thread_key())
    assert "key_rotated" not in write.pin_key
    assert write.api_key_id == "key_rotated", "attribution keeps the presenting key"


@pytest.mark.asyncio
async def test_bounce_row_is_drain_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The executor receives the armed drain deadline so ``purge_at < drain_until`` for every row (CL-3)."""

    drain_until = datetime(2026, 12, 1, 12, 0, 0)
    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID, drain_until=drain_until))
    _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    _PureHelpers().install(monkeypatch)
    service, _, _ = _service(monkeypatch)

    await _run_selection(service, _request_state(), headers=_NATIVE_HEADERS)

    (_intent, passed_drain_until, *_rest), *_ = executor.commits
    assert passed_drain_until == drain_until.replace(tzinfo=timezone.utc)


@pytest.mark.parametrize("failure", ["not_written", "unknown", RuntimeError("writer wedged")], ids=str)
@pytest.mark.asyncio
async def test_bounce_row_failure_is_logged_and_not_fatal(
    monkeypatch: pytest.MonkeyPatch, failure: str | Exception, caplog: pytest.LogCaptureFixture
) -> None:
    """The row is a latency optimisation: Codex re-handshakes per retry and the HTTP route re-decides."""

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    if isinstance(failure, Exception):
        _install_bounce_executor(monkeypatch, _BounceExecutor(error=failure))
    else:
        _install_bounce_executor(monkeypatch, _BounceExecutor(failure))
    _PureHelpers().install(monkeypatch)
    service, _, _ = _service(monkeypatch)

    with caplog.at_level("WARNING", logger=ws_overflow.__name__):
        result, websocket_send = await _run_selection(service, _request_state(), headers=_NATIVE_HEADERS)

    assert result is None
    assert _sent_event(websocket_send)["status"] == 503
    assert counter.outcomes() == ["bounced_ws_event"]
    assert any("subscription_overflow_bounce_row" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_bounce_row_write_cancellation_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """``CancelledError`` is a ``BaseException``: never swallowed into a bounce, no event emitted."""

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    _install_bounce_executor(monkeypatch, _BounceExecutor(error=asyncio.CancelledError()))
    _PureHelpers().install(monkeypatch)
    service, _, released = _service(monkeypatch)
    websocket_send = AsyncMock()

    with pytest.raises(asyncio.CancelledError):
        await _run_selection(service, _request_state(), headers=_NATIVE_HEADERS, websocket_send=websocket_send)

    websocket_send.assert_not_awaited()
    released.assert_not_awaited()
    assert counter.outcomes() == []


@pytest.mark.asyncio
async def test_non_native_turn_without_thread_key_gets_the_503_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Design §7.3: a non-native session has no thread key -> in-band 503, no bounce row."""

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    _PureHelpers().install(monkeypatch)  # the pure decline helper admits the SDK turn (no ``no_thread_key``)
    service, _, _ = _service(monkeypatch)

    result, websocket_send = await _run_selection(
        service, _request_state(), headers={"user-agent": "openai-python/1.0", "authorization": "Bearer x"}
    )

    assert result is None
    assert _sent_event(websocket_send)["status"] == 503
    assert executor.commits == [], "no thread key -> nothing to key a bounce row by"
    assert counter.outcomes() == ["bounced_ws_event"]


# --- every decline keeps today's 429 event byte-identical -----------------------------------------------


async def _todays_429_bytes(monkeypatch: pytest.MonkeyPatch, request_state_factory: Any) -> str:
    _install_settings(monkeypatch, _settings())
    service, _, _ = _service(monkeypatch)
    _result, websocket_send = await _run_selection(service, request_state_factory(), headers=_NATIVE_HEADERS)
    return _sent_text(websocket_send)


_O1_DECLINES = [
    "pin_commit_recent_failure",
    "turn_state_bound",
    "opportunistic",
    "key_scope",
    "no_thread_key",
    "background_job",
    "source_excluded",
    "breaker_open",
]


@pytest.mark.parametrize("reason", _O1_DECLINES)
@pytest.mark.asyncio
async def test_o1_declines_keep_todays_429_event_byte_identical(monkeypatch: pytest.MonkeyPatch, reason: str) -> None:
    baseline = await _todays_429_bytes(monkeypatch, _request_state)

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    helpers = _PureHelpers(decline=reason)
    helpers.install(monkeypatch)
    service, request_logs, _ = _service(monkeypatch)

    result, websocket_send = await _run_selection(service, _request_state(), headers=_NATIVE_HEADERS)

    assert result is None
    assert _sent_text(websocket_send) == baseline
    event = json.loads(baseline)
    assert event["status"] == 429 and event["error"]["code"] == "usage_limit_reached"
    assert event["error"]["resets_at"] == _RESETS_AT
    assert counter.outcomes() == [f"declined_{reason}"]
    assert executor.commits == []
    assert helpers.select_calls == [], "an O(1) decline never reaches the source query"
    assert helpers.portability_calls == []
    assert await service.drain_persistence_tasks(timeout_seconds=1)
    assert request_logs.calls[-1]["error_code"] == "usage_limit_reached"


@pytest.mark.parametrize(
    ("portability", "expected_outcome"),
    [
        (("not_portable_history", "reasoning item"), "declined_not_portable_history"),
        (("not_portable_input", "tools:namespace"), "declined_not_portable_input"),
    ],
    ids=["history", "input"],
)
@pytest.mark.asyncio
async def test_portability_declines_keep_todays_429_event_byte_identical(
    monkeypatch: pytest.MonkeyPatch, portability: tuple[str, str], expected_outcome: str
) -> None:
    """No hint over WebSocket: the caller's 429 event is emitted unchanged for ``not_portable_history`` too."""

    baseline = await _todays_429_bytes(monkeypatch, _request_state)

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    helpers = _PureHelpers(portability=portability)
    helpers.install(monkeypatch)
    service, _, _ = _service(monkeypatch)

    _result, websocket_send = await _run_selection(service, _request_state(), headers=_NATIVE_HEADERS)

    assert _sent_text(websocket_send) == baseline
    assert counter.outcomes() == [expected_outcome]
    assert executor.commits == []
    assert len(helpers.portability_calls) == 1


@pytest.mark.asyncio
async def test_unresolvable_source_model_keeps_todays_429_event(monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = await _todays_429_bytes(monkeypatch, _request_state)

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    helpers = _PureHelpers()
    helpers.install(monkeypatch, select_result=None)
    service, _, _ = _service(monkeypatch)

    _result, websocket_send = await _run_selection(service, _request_state(), headers=_NATIVE_HEADERS)

    assert _sent_text(websocket_send) == baseline
    assert counter.outcomes() == ["declined_no_source"]
    assert executor.commits == []
    assert helpers.portability_calls == [], "no body walk without a resolvable source model"


@pytest.mark.asyncio
async def test_turn_without_a_frame_body_keeps_todays_429_event(monkeypatch: pytest.MonkeyPatch) -> None:
    def state_without_frame() -> proxy_service._WebSocketRequestState:
        return _request_state(fresh_upstream_request_text=None, request_text=None)

    baseline = await _todays_429_bytes(monkeypatch, state_without_frame)

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    helpers = _PureHelpers()
    helpers.install(monkeypatch)
    service, _, _ = _service(monkeypatch)

    _result, websocket_send = await _run_selection(service, state_without_frame(), headers=_NATIVE_HEADERS)

    assert _sent_text(websocket_send) == baseline
    assert counter.outcomes() == ["declined_not_portable_input"]
    assert executor.commits == [] and helpers.portability_calls == []


@pytest.mark.asyncio
async def test_drain_mode_never_bounces_fresh_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Design §8.8: after the designation is cleared only pinned conversations resolve; nothing fresh overflows."""

    baseline = await _todays_429_bytes(monkeypatch, _request_state)

    drain_until = datetime.now(timezone.utc) + timedelta(days=20)
    _install_settings(monkeypatch, _settings(drain_until=drain_until.replace(tzinfo=None)))
    counter = _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    helpers = _PureHelpers()
    helpers.install(monkeypatch)
    service, _, _ = _service(monkeypatch)

    _result, websocket_send = await _run_selection(service, _request_state(), headers=_NATIVE_HEADERS)

    assert _sent_text(websocket_send) == baseline
    assert counter.outcomes() == ["declined_drain_mode"]
    assert executor.commits == [] and helpers.decline_calls == [] and helpers.select_calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"require_security_work_authorized": True},
        {"previous_response_id": "resp_subscription_owned"},
        {"proxy_injected_previous_response_id": True},
        {"precreated_replay_reason": "model_rejected"},
    ],
    ids=["capability-session", "client-continuation", "proxy-injected-anchor", "precreated-replay"],
)
@pytest.mark.asyncio
async def test_unjudged_turn_classes_keep_todays_answer_without_any_helper_call(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any]
) -> None:
    """Capability sessions, continuations and precreated replays are never fresh-bounced (§4.2 (3), api.py)."""

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    helpers = _PureHelpers()
    helpers.install(monkeypatch)
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    emit = AsyncMock()
    monkeypatch.setattr(service, "_emit_websocket_connect_failure", emit)
    request_state = _request_state(**overrides)

    bounced = await ws_overflow.bounce_exhausted_websocket_turn(
        cast(Any, service),
        cast(WebSocket, SimpleNamespace()),
        client_send_lock=anyio.Lock(),
        api_key=None,
        request_state=request_state,
        headers=_NATIVE_HEADERS,
    )

    assert bounced is False
    emit.assert_not_awaited()
    assert counter.outcomes() == [] and executor.commits == []
    assert helpers.thread_key_calls == 0 and helpers.decline_calls == [] and helpers.select_calls == []


@pytest.mark.asyncio
async def test_decision_error_falls_through_to_todays_429(monkeypatch: pytest.MonkeyPatch) -> None:
    """Design §8.1: an infrastructure error in the fresh decision keeps today's answer; nothing is owned."""

    baseline = await _todays_429_bytes(monkeypatch, _request_state)

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    _PureHelpers(raise_in_decline=RuntimeError("model_sources table is unavailable")).install(monkeypatch)
    service, _, _ = _service(monkeypatch)

    _result, websocket_send = await _run_selection(service, _request_state(), headers=_NATIVE_HEADERS)

    assert _sent_text(websocket_send) == baseline
    assert counter.outcomes() == ["decision_error"]
    assert executor.commits == []


@pytest.mark.asyncio
async def test_fresh_decision_cancellation_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    _install_bounce_executor(monkeypatch)
    _PureHelpers(raise_in_decline=asyncio.CancelledError()).install(monkeypatch)
    service, _, _ = _service(monkeypatch)

    with pytest.raises(asyncio.CancelledError):
        await _run_selection(service, _request_state(), headers=_NATIVE_HEADERS)

    assert counter.outcomes() == []


@pytest.mark.asyncio
async def test_selection_without_handshake_headers_declines_as_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers that do not plumb headers (``headers=None``) judge an empty mapping: no thread key -> today's 429."""

    baseline = await _todays_429_bytes(monkeypatch, _request_state)

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    helpers = _PureHelpers(decline="no_thread_key")
    helpers.install(monkeypatch)
    service, _, _ = _service(monkeypatch)

    _result, websocket_send = await _run_selection(service, _request_state(), headers=None)

    assert _sent_text(websocket_send) == baseline
    assert helpers.decline_calls[0]["headers"] == {} and helpers.decline_calls[0]["thread_key"] is None
    assert executor.commits == []


# --- ship-dark: both settings columns NULL ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_settings_perform_no_lookup_select_or_body_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """I9 spy: with both columns NULL neither helper touches a pin, source, breaker or body; today's 429 flows."""

    _install_settings(monkeypatch, _settings())
    counter = _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    helpers = _PureHelpers()
    helpers.install(monkeypatch)
    store = _PinStore()
    store.install(monkeypatch)
    service, _, _ = _service(monkeypatch)
    request_state = _request_state(previous_response_id="resp_anchor_candidate")

    result, websocket_send = await _run_selection(service, request_state, headers=_NATIVE_HEADERS)
    pinned = await ws_overflow.bounce_pinned_or_anchored_websocket_turn(
        cast(Any, service),
        cast(WebSocket, SimpleNamespace()),
        client_send_lock=anyio.Lock(),
        api_key=None,
        request_state=request_state,
        headers=_NATIVE_HEADERS,
    )

    assert result is None and pinned is False
    assert _sent_event(websocket_send)["status"] == 429
    assert helpers.thread_key_calls == 0
    assert helpers.decline_calls == [] and helpers.select_calls == [] and helpers.portability_calls == []
    assert store.lookups == [] and executor.commits == []
    assert counter.outcomes() == []


@pytest.mark.asyncio
async def test_elapsed_drain_deadline_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once ``drain_until`` has passed the lookups stop: every row was purge-capped below it (CL-3)."""

    _install_settings(monkeypatch, _settings(drain_until=datetime(2020, 1, 1)))
    helpers = _PureHelpers()
    helpers.install(monkeypatch)
    store = _PinStore()
    store.install(monkeypatch)
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))

    pinned = await ws_overflow.bounce_pinned_or_anchored_websocket_turn(
        cast(Any, service),
        cast(WebSocket, SimpleNamespace()),
        client_send_lock=anyio.Lock(),
        api_key=None,
        request_state=_request_state(),
        headers=_NATIVE_HEADERS,
    )

    assert pinned is False and store.lookups == [] and helpers.thread_key_calls == 0


# --- pinned / anchored turns ------------------------------------------------------------------------------


async def _run_pinned(
    service: proxy_service.ProxyService,
    request_state: proxy_service._WebSocketRequestState,
    *,
    headers: dict[str, str] = _NATIVE_HEADERS,
    api_key: ApiKeyData | None = None,
) -> tuple[bool, AsyncMock]:
    websocket_send = AsyncMock()
    bounced = await ws_overflow.bounce_pinned_or_anchored_websocket_turn(
        cast(Any, service),
        cast(WebSocket, SimpleNamespace(send_text=websocket_send)),
        client_send_lock=anyio.Lock(),
        api_key=api_key,
        request_state=request_state,
        headers=headers,
    )
    return bounced, websocket_send


@pytest.mark.parametrize(
    ("expires_in", "expected_state"),
    [(timedelta(days=3), "live"), (timedelta(days=-3), "expired")],
    ids=["live", "tombstone"],
)
@pytest.mark.asyncio
async def test_pinned_thread_bounces_on_live_and_tombstoned_evidence(
    monkeypatch: pytest.MonkeyPatch, expires_in: timedelta, expected_state: str
) -> None:
    """I7: a pinned thread's history never reaches a subscription account; HTTP answers (source, 400 or 503)."""

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    _PureHelpers().install(monkeypatch)
    pin_key = thread_pin_key(_thread_key())
    record = _pin_record(pin_key, kind=PIN_KIND_THREAD, expires_in=expires_in, source_id="src_pinned")
    store = _PinStore({pin_key: PinLookupResult(cast(Any, expected_state), record)})
    store.install(monkeypatch)
    service, request_logs, released = _service(monkeypatch)
    request_state = _request_state()

    bounced, websocket_send = await _run_pinned(service, request_state)

    assert bounced is True
    event = _sent_event(websocket_send)
    assert event["status"] == 503 and event["error"]["code"] == WS_BOUNCE_CODE
    assert event["error"]["code"] not in _NON_RETRYABLE_CODES
    assert [lookup[0] for lookup in store.lookups] == [pin_key], "exactly one bounded thread-key lookup"
    (intent, *_rest), *_ = executor.commits
    assert intent.writes == (PinWrite(bounce_pin_key(_thread_key()), PIN_KIND_BOUNCE, "src_pinned", None),)
    released.assert_awaited_once_with(request_state)
    assert await service.drain_persistence_tasks(timeout_seconds=1)
    assert request_logs.calls[-1]["error_code"] == WS_BOUNCE_CODE
    assert counter.outcomes() == ["bounced_ws_event"]


@pytest.mark.asyncio
async def test_unpinned_thread_is_not_bounced(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    _PureHelpers().install(monkeypatch)
    store = _PinStore()
    store.install(monkeypatch)
    service, _, released = _service(monkeypatch)

    bounced, websocket_send = await _run_pinned(service, _request_state())

    assert bounced is False
    websocket_send.assert_not_awaited()
    released.assert_not_awaited()
    assert [lookup[0] for lookup in store.lookups] == [thread_pin_key(_thread_key())]
    assert counter.outcomes() == [] and executor.commits == []


@pytest.mark.asyncio
async def test_pinned_lookup_uses_the_proxy_scheduler_and_clock_without_a_replica_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero timing allowance: the bounded read takes the service's seams; a turn reads the row, never a snapshot."""

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    _install_counter(monkeypatch)
    _install_bounce_executor(monkeypatch)
    _PureHelpers().install(monkeypatch)
    store = _PinStore()
    store.install(monkeypatch)
    service, _, _ = _service(monkeypatch)
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    service._scheduler = scheduler  # type: ignore[attr-defined]
    service._clock = clock  # type: ignore[attr-defined]

    await _run_pinned(service, _request_state())

    (_key, cache, passed_scheduler, passed_clock), *_ = store.lookups
    assert cache is None
    assert passed_scheduler is scheduler and passed_clock is clock


@pytest.mark.parametrize(
    "error",
    [PinLookupTimeout("model-source pin lookup exceeded 2s"), RuntimeError("database unavailable")],
    ids=["lookup-timeout", "infrastructure-error"],
)
@pytest.mark.asyncio
async def test_pinned_lookup_failure_bounces_without_a_bounce_row(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """CL-12: a thread whose pin state is unknowable fails closed toward HTTP, where the decision answers 503."""

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    _PureHelpers().install(monkeypatch)
    _PinStore(error=error).install(monkeypatch)
    service, _, released = _service(monkeypatch)
    request_state = _request_state()

    bounced, websocket_send = await _run_pinned(service, request_state)

    assert bounced is True
    event = _sent_event(websocket_send)
    assert event["status"] == 503 and event["error"]["code"] == WS_BOUNCE_CODE
    assert executor.commits == [], "no source id is known, so no bounce row can be written"
    released.assert_awaited_once_with(request_state)
    expected = "pinned_lookup_timeout" if isinstance(error, PinLookupTimeout) else "decision_error"
    assert counter.outcomes() == [expected]


@pytest.mark.asyncio
async def test_pinned_lookup_cancellation_disposes_the_prepared_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mixin calls this helper after the turn's API-key usage was reserved and before the turn is registered in
    ``pending_requests`` (the scope cleanup fails registered turns only), so a cancellation inside the lookup must
    release the reservation and finalize a ``cancelled`` row before it propagates. Mutant: the cancellation
    propagates with nothing released -- the reservation leaks until the 6 h stale reclaim and no terminal row
    exists."""

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    _PureHelpers().install(monkeypatch)
    _PinStore(error=cast(Any, asyncio.CancelledError())).install(monkeypatch)
    service, request_logs, released = _service(monkeypatch)
    request_state = _request_state()

    with pytest.raises(asyncio.CancelledError):
        await _run_pinned(service, request_state)

    released.assert_awaited_once_with(request_state)
    assert await service.drain_persistence_tasks(timeout_seconds=1)
    assert [(call["status"], call["error_code"]) for call in request_logs.calls] == [("cancelled", "stream_incomplete")]
    assert counter.outcomes() == []


@pytest.mark.asyncio
async def test_pinned_bounce_row_write_cancellation_disposes_the_prepared_turn_without_an_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same window, one await later: a cancellation inside the bounce-row write disposes the turn and emits nothing."""

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    _install_bounce_executor(monkeypatch, _BounceExecutor(error=asyncio.CancelledError()))
    _PureHelpers().install(monkeypatch)
    pin_key = thread_pin_key(_thread_key())
    record = _pin_record(pin_key, kind=PIN_KIND_THREAD, expires_in=timedelta(days=3))
    _PinStore({pin_key: PinLookupResult("live", record)}).install(monkeypatch)
    service, request_logs, released = _service(monkeypatch)
    request_state = _request_state()
    websocket_send = AsyncMock()

    with pytest.raises(asyncio.CancelledError):
        await ws_overflow.bounce_pinned_or_anchored_websocket_turn(
            cast(Any, service),
            cast(WebSocket, SimpleNamespace(send_text=websocket_send)),
            client_send_lock=anyio.Lock(),
            api_key=None,
            request_state=request_state,
            headers=_NATIVE_HEADERS,
        )

    websocket_send.assert_not_awaited()
    released.assert_awaited_once_with(request_state)
    assert await service.drain_persistence_tasks(timeout_seconds=1)
    assert [(call["status"], call["error_code"]) for call in request_logs.calls] == [("cancelled", "stream_incomplete")]
    assert counter.outcomes() == []


@pytest.mark.asyncio
async def test_cancellation_inside_the_bounce_emitter_is_not_disposed_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    """The connect-failure emitter owns its own release and row; a cancellation delivered at its send leaves exactly
    one release and one (error) row behind -- the guard must not add a second disposal."""

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    _install_bounce_executor(monkeypatch)
    _PureHelpers().install(monkeypatch)
    pin_key = thread_pin_key(_thread_key())
    record = _pin_record(pin_key, kind=PIN_KIND_THREAD, expires_in=timedelta(days=3))
    _PinStore({pin_key: PinLookupResult("live", record)}).install(monkeypatch)
    service, request_logs, released = _service(monkeypatch)
    request_state = _request_state()
    websocket_send = AsyncMock(side_effect=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await ws_overflow.bounce_pinned_or_anchored_websocket_turn(
            cast(Any, service),
            cast(WebSocket, SimpleNamespace(send_text=websocket_send)),
            client_send_lock=anyio.Lock(),
            api_key=None,
            request_state=request_state,
            headers=_NATIVE_HEADERS,
        )

    assert released.await_count == 1
    assert await service.drain_persistence_tasks(timeout_seconds=1)
    assert [(call["status"], call["error_code"]) for call in request_logs.calls] == [("error", WS_BOUNCE_CODE)]
    assert counter.outcomes() == []


@pytest.mark.asyncio
async def test_anchored_previous_response_bounces_only_without_a_recorded_subscription_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An anchor row is consulted only when continuity found no subscription owner; a recorded owner wins."""

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    _PureHelpers().install(monkeypatch)
    api_key = _api_key("key_anchor")
    anchor_key = anchor_pin_key("key_anchor", "resp_source_minted")
    record = _pin_record(anchor_key, kind=PIN_KIND_ANCHOR, expires_in=timedelta(days=2), source_id="src_anchor")
    store = _PinStore({anchor_key: PinLookupResult("live", record)})
    store.install(monkeypatch)
    service, _, released = _service(monkeypatch)

    # No thread key (SDK client), anchored, no recorded owner -> bounce; no bounce row without a thread key.
    request_state = _request_state(previous_response_id="resp_source_minted", api_key=api_key)
    bounced, websocket_send = await _run_pinned(service, request_state, headers={"user-agent": "sdk"}, api_key=api_key)
    assert bounced is True
    assert _sent_event(websocket_send)["error"]["code"] == WS_BOUNCE_CODE
    assert [lookup[0] for lookup in store.lookups] == [anchor_key]
    assert executor.commits == []
    released.assert_awaited_once_with(request_state)
    assert counter.outcomes() == ["bounced_ws_event"]

    # Same anchor, but continuity recorded a subscription owner -> the anchor is never even consulted.
    store.lookups.clear()
    owned = _request_state(
        previous_response_id="resp_source_minted",
        previous_response_owner_account_id="acc_subscription_owner",
        api_key=api_key,
    )
    bounced, websocket_send = await _run_pinned(service, owned, headers={"user-agent": "sdk"}, api_key=api_key)
    assert bounced is False
    websocket_send.assert_not_awaited()
    assert store.lookups == []


@pytest.mark.asyncio
async def test_anchored_turn_with_a_thread_key_writes_the_bounce_row_for_the_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    _PureHelpers().install(monkeypatch)
    anchor_key = anchor_pin_key(None, "resp_source_minted")
    record = _pin_record(anchor_key, kind=PIN_KIND_ANCHOR, expires_in=timedelta(days=2), source_id="src_anchor")
    store = _PinStore({anchor_key: PinLookupResult("live", record)})
    store.install(monkeypatch)
    service, _, _ = _service(monkeypatch)

    bounced, _ = await _run_pinned(service, _request_state(previous_response_id="resp_source_minted"))

    assert bounced is True
    assert [lookup[0] for lookup in store.lookups] == [thread_pin_key(_thread_key()), anchor_key]
    (intent, *_rest), *_ = executor.commits
    assert intent.writes == (PinWrite(bounce_pin_key(_thread_key()), PIN_KIND_BOUNCE, "src_anchor", None),)


@pytest.mark.asyncio
async def test_expired_anchor_does_not_bounce(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    _install_counter(monkeypatch)
    _install_bounce_executor(monkeypatch)
    _PureHelpers().install(monkeypatch)
    anchor_key = anchor_pin_key(None, "resp_old")
    record = _pin_record(anchor_key, kind=PIN_KIND_ANCHOR, expires_in=timedelta(days=-1))
    _PinStore({anchor_key: PinLookupResult("expired", record)}).install(monkeypatch)
    service, _, _ = _service(monkeypatch)

    bounced, websocket_send = await _run_pinned(
        service, _request_state(previous_response_id="resp_old"), headers={"user-agent": "sdk"}
    )

    assert bounced is False
    websocket_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_turn_without_thread_key_or_previous_response_never_looks_up(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    _PureHelpers().install(monkeypatch)
    store = _PinStore()
    store.install(monkeypatch)
    service, _, _ = _service(monkeypatch)

    bounced, _ = await _run_pinned(service, _request_state(), headers={"user-agent": "sdk"})

    assert bounced is False and store.lookups == []


@pytest.mark.asyncio
async def test_drain_mode_still_bounces_pinned_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Design §8.8: pinned conversations drain on their source after the designation is cleared."""

    drain_until = datetime.now(timezone.utc) + timedelta(days=20)
    _install_settings(monkeypatch, _settings(drain_until=drain_until.replace(tzinfo=None)))
    _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    _PureHelpers().install(monkeypatch)
    pin_key = thread_pin_key(_thread_key())
    record = _pin_record(pin_key, kind=PIN_KIND_THREAD, expires_in=timedelta(days=3))
    _PinStore({pin_key: PinLookupResult("live", record)}).install(monkeypatch)
    service, _, _ = _service(monkeypatch)

    bounced, websocket_send = await _run_pinned(service, _request_state())

    assert bounced is True
    assert _sent_event(websocket_send)["status"] == 503
    (_intent, passed_drain_until, *_rest), *_ = executor.commits
    assert passed_drain_until == drain_until.replace(microsecond=drain_until.microsecond)


# --- mixin wiring: first turn and reused socket -------------------------------------------------------------


def _session_service(monkeypatch: pytest.MonkeyPatch) -> tuple[proxy_service.ProxyService, _RequestLogsRecorder]:
    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    monkeypatch.setattr(service, "_resolve_compact_turn_state_owner", AsyncMock(return_value=None))
    return service, request_logs


@pytest.mark.asyncio
async def test_first_turn_on_a_pinned_thread_is_bounced_before_any_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pinned check runs before the connect path, so no subscription socket is ever opened for the thread."""

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    _PureHelpers().install(monkeypatch)
    pin_key = thread_pin_key(_thread_key())
    record = _pin_record(pin_key, kind=PIN_KIND_THREAD, expires_in=timedelta(days=3))
    _PinStore({pin_key: PinLookupResult("live", record)}).install(monkeypatch)
    service, request_logs = _session_service(monkeypatch)
    connect = AsyncMock(side_effect=AssertionError("a pinned first turn must not reach the connect path"))
    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", connect)
    released = AsyncMock()
    monkeypatch.setattr(proxy_service.ProxyService, "_release_websocket_request_state_reservation", released)
    downstream = _Downstream([_create_frame(_MODEL)])

    await service.proxy_responses_websocket(
        cast(WebSocket, downstream),
        dict(_NATIVE_HEADERS),
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=None,
    )

    assert len(downstream.sent_text) == 1
    event = json.loads(downstream.sent_text[0])
    assert event == {
        "type": "error",
        "status": 503,
        "error": {"message": ws_overflow.WS_BOUNCE_PINNED_MESSAGE, "type": "server_error", "code": WS_BOUNCE_CODE},
    }
    connect.assert_not_awaited()
    assert released.await_count == 1
    assert len(executor.commits) == 1
    assert counter.outcomes() == ["bounced_ws_event"]
    assert await service.drain_persistence_tasks(timeout_seconds=1)
    assert [call["error_code"] for call in request_logs.calls] == [WS_BOUNCE_CODE]


@pytest.mark.asyncio
async def test_reused_socket_pinned_turn_is_bounced_with_reservation_released_and_row_finalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thread pinned mid-session (over HTTP on another replica) is bounced on the open socket; the frame never
    reaches the attached subscription upstream."""

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    _PureHelpers().install(monkeypatch)
    pin_key = thread_pin_key(_thread_key())
    record = _pin_record(pin_key, kind=PIN_KIND_THREAD, expires_in=timedelta(days=3))
    store = _PinStore()
    store.install(monkeypatch)
    service, request_logs = _session_service(monkeypatch)
    account = _make_account("acc_ws_overflow_reuse")
    upstream = _QueuedTestUpstreamWebSocket(_completed_turn("resp_turn_one"))

    async def fake_connect(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        return account, upstream

    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", fake_connect)
    released = AsyncMock()
    monkeypatch.setattr(proxy_service.ProxyService, "_release_websocket_request_state_reservation", released)

    class _PinBetweenTurns(_Downstream):
        """Delivers the second frame only after the first turn's terminal, once the thread has been pinned."""

        delivered = 0

        async def receive(self) -> dict[str, object]:
            if self.pending and self.delivered:
                await self.turn_completed.wait()
                self.turn_completed.clear()
                store.results[pin_key] = PinLookupResult("live", record)
            if self.pending:
                self.delivered += 1
                return {"type": "websocket.receive", "text": self.pending.pop(0)}
            await self.done.wait()
            return {"type": "websocket.disconnect"}

    downstream = _PinBetweenTurns([_create_frame(_MODEL), _create_frame(_MODEL)])

    await service.proxy_responses_websocket(
        cast(WebSocket, downstream),
        dict(_NATIVE_HEADERS),
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=None,
    )

    assert any("resp_turn_one" in text for text in downstream.sent_text), "the subscription turn completes"
    bounce_events = [json.loads(text) for text in downstream.sent_text if '"status":503' in text]
    assert len(bounce_events) == 1
    assert bounce_events[0]["error"]["code"] == WS_BOUNCE_CODE
    assert len(upstream.sent_text) == 1, "the pinned turn must not be forwarded to the subscription upstream"
    assert [lookup[0] for lookup in store.lookups] == [pin_key, pin_key]
    assert released.await_count >= 1
    assert len(executor.commits) == 1
    assert counter.outcomes() == ["bounced_ws_event"]
    assert await service.drain_persistence_tasks(timeout_seconds=1)
    assert WS_BOUNCE_CODE in {call.get("error_code") for call in request_logs.calls}


@pytest.mark.asyncio
async def test_scope_cancellation_during_the_pinned_lookup_disposes_the_prepared_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the mixin: the session task is cancelled while the first turn's pin lookup is in flight -- after the
    turn's reservation, before its registration -- and the turn is disposed exactly once with a ``cancelled`` row.
    Without the guard the scope cleanup sees no registered turn and the reservation leaks."""

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    _PureHelpers().install(monkeypatch)
    lookup_entered = asyncio.Event()
    never = asyncio.Event()

    async def blocking_lookup(pin_key: str, *, cache: object, scheduler: object, clock: object) -> PinLookupResult:
        del pin_key, cache, scheduler, clock
        lookup_entered.set()
        await never.wait()
        return PinLookupResult("none", None)

    monkeypatch.setattr(ws_overflow, "lookup_pin_bounded", blocking_lookup)
    service, request_logs = _session_service(monkeypatch)
    connect = AsyncMock(side_effect=AssertionError("the cancelled turn must not reach the connect path"))
    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", connect)
    released = AsyncMock()
    monkeypatch.setattr(proxy_service.ProxyService, "_release_websocket_request_state_reservation", released)
    downstream = _Downstream([_create_frame(_MODEL)])

    session = asyncio.create_task(
        service.proxy_responses_websocket(
            cast(WebSocket, downstream),
            dict(_NATIVE_HEADERS),
            codex_session_affinity=False,
            openai_cache_affinity=False,
            api_key=None,
        )
    )
    await asyncio.wait_for(lookup_entered.wait(), timeout=5)
    session.cancel()
    with pytest.raises(asyncio.CancelledError):
        await session

    assert released.await_count == 1
    assert await service.drain_persistence_tasks(timeout_seconds=1)
    assert [(call["status"], call["error_code"]) for call in request_logs.calls] == [("cancelled", "stream_incomplete")]
    assert downstream.sent_text == []
    connect.assert_not_awaited()
    assert counter.outcomes() == []


@pytest.mark.asyncio
async def test_exhausted_first_turn_over_a_session_is_bounced_through_the_connect_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the mixin: the handshake headers reach the selection branch and the bounce replaces the 429."""

    _install_settings(monkeypatch, _settings(source_id=_SOURCE_ID))
    counter = _install_counter(monkeypatch)
    executor = _install_bounce_executor(monkeypatch)
    helpers = _PureHelpers()
    helpers.install(monkeypatch)
    _PinStore().install(monkeypatch)
    service, request_logs = _session_service(monkeypatch)
    monkeypatch.setattr(service, "_select_account_with_budget", AsyncMock(return_value=_usage_limit_selection()))
    released = AsyncMock()
    monkeypatch.setattr(proxy_service.ProxyService, "_release_websocket_request_state_reservation", released)
    downstream = _Downstream([_create_frame(_MODEL)])

    await service.proxy_responses_websocket(
        cast(WebSocket, downstream),
        dict(_NATIVE_HEADERS),
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=None,
    )

    assert len(downstream.sent_text) == 1
    event = json.loads(downstream.sent_text[0])
    assert event["status"] == 503 and event["error"]["code"] == WS_BOUNCE_CODE
    assert helpers.decline_calls[0]["thread_key"] == _thread_key()
    assert "thread-id" in helpers.decline_calls[0]["headers"]
    assert len(executor.commits) == 1
    assert counter.outcomes() == ["bounced_ws_event"]
    assert await service.drain_persistence_tasks(timeout_seconds=1)
    assert [call["error_code"] for call in request_logs.calls] == [WS_BOUNCE_CODE]


# --- contract pins ---------------------------------------------------------------------------------------------


def test_every_outcome_this_helper_emits_is_in_the_closed_enum() -> None:
    emitted = {
        ws_overflow.OUTCOME_BOUNCED_WS_EVENT,
        ws_overflow.OUTCOME_PINNED_LOOKUP_TIMEOUT,
        ws_overflow.OUTCOME_DECISION_ERROR,
        ws_overflow._DECLINED_DRAIN_MODE,
        ws_overflow._DECLINED_NO_SOURCE,
        ws_overflow._DECLINED_NOT_PORTABLE_INPUT,
    } | {f"declined_{reason}" for reason in _O1_DECLINES}
    assert emitted <= OVERFLOW_OUTCOMES


def test_bounce_code_is_the_retryable_transport_code() -> None:
    assert WS_BOUNCE_CODE == "model_source_requires_http_transport"
    assert WS_BOUNCE_CODE not in _NON_RETRYABLE_CODES
    for message in (ws_overflow.WS_BOUNCE_FRESH_MESSAGE, ws_overflow.WS_BOUNCE_PINNED_MESSAGE):
        assert _SOURCE_ID not in message and "HTTP" in message
