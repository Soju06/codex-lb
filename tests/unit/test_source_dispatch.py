"""Single-owner model-source dispatch lifecycle (#2123 WP-C1, design v3 §6, §13.1).

Virtual time (``tests/simulation/virtual_time.py``) drives the disconnect watch
and the settlement policy; the request-log repository and the reservation
primitives are recording fakes so every latch can be asserted exactly-once.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Coroutine
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st
from starlette.requests import Request

from app.db.models import ModelSource
from app.modules.api_keys.service import (
    API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS,
    API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS,
    ApiKeyRequestUsageBudget,
    ApiKeyUsageReservationData,
)
from app.modules.model_sources.forwarding import (
    ModelSourceForwardingError,
    SourceChatStream,
    SourceStreamTransport,
    SourceUsage,
    SourceUsageHolder,
)
from app.modules.proxy import source_dispatch as dispatch_module
from app.modules.proxy.model_source_pins import PinIntent, PinWrite
from app.modules.proxy.source_admission import SourceAdmission, SourceBulkhead
from app.modules.proxy.source_dispatch import (
    ABANDON_CLIENT_DISCONNECTED_BEFORE_BODY,
    ABANDON_CLIENT_DISCONNECTED_DURING_OPEN,
    ABANDON_SOURCE_STALL,
    OPEN_DISCONNECT_POLL_SECONDS,
    STALL_EVIDENCE_SECONDS,
    ClientDisconnectedDuringOpen,
    SourceChatStreamOwner,
    SourceDispatch,
    SourcePinCommitError,
    SourceStreamingResponse,
    estimate_settlement_usage,
    open_with_disconnect_watch,
    settlement_stream,
    synthesized_pin_failure_frames,
)
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit


# -- doubles -------------------------------------------------------------------------


def _source(source_id: str = "src_dispatch", max_concurrency: int | None = None) -> ModelSource:
    return ModelSource(
        id=source_id,
        name=source_id,
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=False,
        supports_responses=True,
        max_concurrency=max_concurrency,
    )


def _reservation(*, limited: bool = True) -> ApiKeyUsageReservationData:
    return ApiKeyUsageReservationData(
        reservation_id="res-1",
        key_id="key-1",
        model="src-model",
        has_applicable_limits=limited,
    )


class _FakeRequest:
    """Minimal Starlette ``Request`` whose disconnect state a test flips."""

    def __init__(self, *, headers: dict[str, str] | None = None) -> None:
        self.disconnected = asyncio.Event()
        raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/responses",
            "headers": raw_headers,
            "client": ("203.0.113.9", 54321),
            "asgi": {"version": "3.0", "spec_version": "2.3"},
        }
        self.request = Request(scope, self._receive)

    async def _receive(self) -> dict[str, object]:
        await self.disconnected.wait()
        return {"type": "http.disconnect"}


@dataclass(slots=True)
class _FakeStream:
    usage_holder: SourceUsageHolder
    body: AsyncIterator[bytes]
    upstream_status_code: int = 200
    closed: int = 0

    async def aclose(self) -> None:
        self.closed += 1


@dataclass(slots=True)
class _Recorder:
    settle_calls: list[dict[str, object]] = field(default_factory=list)
    release_calls: list[object] = field(default_factory=list)
    rows: list[dict[str, object]] = field(default_factory=list)
    cleanup_actions: list[str] = field(default_factory=list)
    settle_result: bool = True
    release_error: Exception | None = None
    settle_error: Exception | None = None

    async def settle(
        self,
        reservation: ApiKeyUsageReservationData,
        *,
        source: ModelSource,
        model: str,
        usage: SourceUsage | None,
        cost_usd_override: float | None = None,
    ) -> bool:
        if self.settle_error is not None:
            raise self.settle_error
        self.settle_calls.append(
            {
                "reservation": reservation,
                "source_id": source.id,
                "model": model,
                "usage": usage,
                "cost_usd_override": cost_usd_override,
            }
        )
        return self.settle_result

    async def release(self, reservation: ApiKeyUsageReservationData) -> None:
        if self.release_error is not None:
            raise self.release_error
        self.release_calls.append(reservation)

    def _schedule_cancel_safe_cleanup(
        self,
        coro: Coroutine[Any, Any, None],
        *,
        action: str,
        request_id: str,
    ) -> asyncio.Task[None]:
        self.cleanup_actions.append(action)
        coro.close()
        return cast(asyncio.Task[None], None)


class _FakeTrial:
    """``TrialClaim`` double: records the first-item observation and the terminal result, in order."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def observe_first_output_item(self) -> None:
        self.events.append("first_output_item")

    def settle(self, result: str) -> None:
        self.events.append(f"settle:{result}")


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorded = _Recorder()

    class _FakeRepository:
        def __init__(self, session: object) -> None:
            del session

        async def add_log(self, **kwargs: object) -> None:
            recorded.rows.append(kwargs)

    @contextlib.asynccontextmanager
    async def fake_session() -> AsyncIterator[None]:
        yield None

    monkeypatch.setattr(dispatch_module, "RequestLogsRepository", _FakeRepository)
    monkeypatch.setattr(dispatch_module, "get_background_session", fake_session)
    return recorded


def _owner(
    recorder: _Recorder,
    *,
    request: _FakeRequest | None = None,
    reservation: ApiKeyUsageReservationData | None = None,
    scheduler: VirtualScheduler | None = None,
    clock: VirtualClock | None = None,
    bulkhead: SourceBulkhead | None = None,
    source: ModelSource | None = None,
    admission_budget: ApiKeyRequestUsageBudget | None = None,
    trial: _FakeTrial | None = None,
    **overrides: object,
) -> SourceDispatch:
    active_source = source or _source()
    active_bulkhead = bulkhead or SourceBulkhead()
    slot = active_bulkhead.try_acquire(active_source.id, active_source.max_concurrency)
    claims = SourceAdmission(slot=slot, trial=trial, bulkhead=active_bulkhead)
    kwargs: dict[str, Any] = {
        "request": (request or _FakeRequest()).request,
        "source": active_source,
        "model": "src-model",
        "api_key": None,
        "reservation": reservation,
        "claims": claims,
        "admission_budget": admission_budget,
        "requested_service_tier": "priority",
        "cleanup_scheduler": recorder,
        "scheduler": scheduler or dispatch_module.REAL_SCHEDULER,
        "clock": clock or dispatch_module.REAL_CLOCK,
        "settle_reservation": recorder.settle,
        "release_reservation": recorder.release,
        "request_id": "req-proxy-1",
    }
    kwargs.update(overrides)
    owner = SourceDispatch(**kwargs)
    claims.transfer_to(owner)
    return owner


async def _frames(*frames: bytes) -> AsyncIterator[bytes]:
    for frame in frames:
        yield frame


def _attach_stream(owner: SourceDispatch, *, holder: SourceUsageHolder | None = None) -> _FakeStream:
    stream = _FakeStream(usage_holder=holder or SourceUsageHolder(), body=_frames())
    owner.stream = cast(Any, stream)
    return stream


# -- finish() latch ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finish_runs_every_step_when_release_raises(recorder: _Recorder) -> None:
    recorder.release_error = RuntimeError("db down")
    bulkhead = SourceBulkhead()
    owner = _owner(recorder, reservation=_reservation(), bulkhead=bulkhead)
    stream = _attach_stream(owner)

    await owner.finish(status="error", error_code="model_source_timeout", upstream_status_code=None)

    assert stream.closed == 1
    assert recorder.cleanup_actions == ["source_dispatch_release_retry"]
    assert bulkhead.in_flight(owner.source.id) == 0
    assert len(recorder.rows) == 1
    row = recorder.rows[0]
    assert row["status"] == "error"
    assert row["error_code"] == "model_source_timeout"
    assert row["account_id"] is None
    assert row["source"] == "model_source"
    assert row["model_source_id"] == owner.source.id
    assert owner.finished is True


@pytest.mark.asyncio
async def test_finish_and_abandon_are_a_single_latch(recorder: _Recorder) -> None:
    bulkhead = SourceBulkhead()
    owner = _owner(recorder, reservation=_reservation(), bulkhead=bulkhead)
    stream = _attach_stream(owner)

    await owner.finish(status="success", usage=SourceUsage(input_tokens=10, output_tokens=5))
    await owner.abandon(ABANDON_CLIENT_DISCONNECTED_DURING_OPEN)
    await owner.finish(status="cancelled")
    await owner.finalize_transport()

    assert stream.closed == 1
    assert len(recorder.settle_calls) == 1
    assert recorder.release_calls == []
    assert len(recorder.rows) == 1
    assert recorder.rows[0]["status"] == "success"
    assert bulkhead.in_flight(owner.source.id) == 0


@pytest.mark.asyncio
async def test_abandon_writes_a_cancelled_row_and_releases_everything(recorder: _Recorder) -> None:
    bulkhead = SourceBulkhead()
    clock = VirtualClock()
    owner = _owner(recorder, reservation=_reservation(), bulkhead=bulkhead, clock=clock)
    owner.sent_at = clock.monotonic()
    stream = _attach_stream(owner)
    clock.advance(12.0)

    await owner.abandon(ABANDON_SOURCE_STALL)

    assert stream.closed == 1
    assert recorder.release_calls == [owner.reservation]
    assert recorder.settle_calls == []
    assert bulkhead.in_flight(owner.source.id) == 0
    assert len(recorder.rows) == 1
    assert recorder.rows[0]["status"] == "cancelled"
    assert recorder.rows[0]["error_code"] == ABANDON_SOURCE_STALL


@pytest.mark.asyncio
async def test_finalize_transport_finishes_a_never_started_body_exactly_once(recorder: _Recorder) -> None:
    owner = _owner(recorder, reservation=_reservation())
    stream = _attach_stream(owner)

    async def never_started() -> AsyncIterator[str]:
        yield "data: never\n\n"

    body = never_started()
    SourceStreamingResponse(body, owner=owner)

    await owner.finalize_transport()
    await owner.finalize_transport()

    assert stream.closed == 1
    assert recorder.release_calls == [owner.reservation]
    assert len(recorder.rows) == 1
    assert recorder.rows[0]["status"] == "cancelled"
    assert recorder.rows[0]["error_code"] == ABANDON_CLIENT_DISCONNECTED_BEFORE_BODY


@pytest.mark.asyncio
async def test_finalize_transport_after_a_completed_body_does_not_double_finish(recorder: _Recorder) -> None:
    owner = _owner(recorder, reservation=_reservation(limited=False))
    holder = SourceUsageHolder(usage=SourceUsage(input_tokens=3, output_tokens=2), terminal_kind="completed")
    _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        yield "data: a\n\n"

    body = settlement_stream(owner, inner())
    SourceStreamingResponse(body, owner=owner)
    assert [chunk async for chunk in body] == ["data: a\n\n"]
    await owner.finalize_transport()

    assert len(recorder.rows) == 1
    assert recorder.rows[0]["status"] == "success"
    assert len(recorder.settle_calls) == 1


# -- settle policy -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_with_usage_settles_at_source_usage_and_cost(recorder: _Recorder) -> None:
    owner = _owner(recorder, reservation=_reservation())
    usage = SourceUsage(input_tokens=100, output_tokens=20, cached_input_tokens=5)
    await owner.finish(status="success", usage=usage)

    assert len(recorder.settle_calls) == 1
    assert recorder.settle_calls[0]["usage"] == usage
    # Unpriced catalog entry -> 0.0, never None for known usage.
    assert recorder.settle_calls[0]["cost_usd_override"] == 0.0
    row = recorder.rows[0]
    assert row["input_tokens"] == 100 and row["output_tokens"] == 20 and row["cached_input_tokens"] == 5
    assert row["cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_success_without_usage_on_a_limited_key_settles_at_the_estimate(
    recorder: _Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    owner = _owner(recorder, reservation=_reservation(limited=True))
    _attach_stream(owner, holder=SourceUsageHolder(first_output_item_seen=True, delta_chars=20_000))

    with caplog.at_level(logging.WARNING, logger="app.modules.proxy.source_dispatch"):
        await owner.finish(status="success")

    assert recorder.release_calls == []
    assert len(recorder.settle_calls) == 1
    estimate = recorder.settle_calls[0]["usage"]
    assert estimate == SourceUsage(
        input_tokens=API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS, output_tokens=5_000, cached_input_tokens=0
    )
    assert any("source_usage_missing_settled_at_estimate" in record.getMessage() for record in caplog.records)
    # Estimates are never written to the row as usage; the row stays a success.
    row = recorder.rows[0]
    assert row["status"] == "success"
    assert row["input_tokens"] is None and row["output_tokens"] is None


@pytest.mark.asyncio
async def test_estimate_uses_the_admission_input_budget(recorder: _Recorder) -> None:
    owner = _owner(
        recorder,
        reservation=_reservation(limited=True),
        admission_budget=ApiKeyRequestUsageBudget(input_tokens=1_234, output_tokens=None),
    )
    await owner.finish(status="success")
    assert recorder.settle_calls[0]["usage"] == SourceUsage(input_tokens=1_234, output_tokens=2_048)


@pytest.mark.asyncio
async def test_success_without_usage_on_an_unlimited_key_releases(recorder: _Recorder) -> None:
    owner = _owner(recorder, reservation=_reservation(limited=False))
    await owner.finish(status="success")
    assert recorder.settle_calls == []
    assert recorder.release_calls == [owner.reservation]
    assert recorder.rows[0]["status"] == "success"


@pytest.mark.asyncio
async def test_cancel_after_the_first_output_item_on_a_limited_key_settles_at_the_estimate(
    recorder: _Recorder,
) -> None:
    owner = _owner(recorder, reservation=_reservation(limited=True), content_delivered=True)
    _attach_stream(
        owner, holder=SourceUsageHolder(first_output_item_seen=True, content_delivered=True, delta_chars=400)
    )
    await owner.finish(status="cancelled", error_code="client_disconnected")

    assert recorder.release_calls == []
    assert recorder.settle_calls[0]["usage"] == SourceUsage(
        input_tokens=API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS,
        output_tokens=API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS,
    )
    assert recorder.rows[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_before_the_first_output_item_releases(recorder: _Recorder) -> None:
    owner = _owner(recorder, reservation=_reservation(limited=True))
    _attach_stream(owner, holder=SourceUsageHolder(first_output_item_seen=False, delta_chars=0))
    await owner.finish(status="cancelled", error_code="client_disconnected")
    assert recorder.settle_calls == []
    assert recorder.release_calls == [owner.reservation]


@pytest.mark.asyncio
async def test_cancel_after_delivered_content_without_an_output_item_settles_at_the_estimate(
    recorder: _Recorder,
) -> None:
    """A ``*.delta``-only or completed-only answer carries no ``response.output_item.added``; once the settlement
    layer handed its frame to the transport the cancel policy keys on that delivery, not on the parser's item
    observation."""

    owner = _owner(recorder, reservation=_reservation(limited=True), content_delivered=True)
    _attach_stream(
        owner,
        holder=SourceUsageHolder(
            first_content_seen=True, content_delivered=True, first_output_item_seen=False, delta_chars=400
        ),
    )
    await owner.finish(status="cancelled", error_code="client_disconnected")

    assert recorder.release_calls == []
    assert recorder.settle_calls[0]["usage"] == SourceUsage(
        input_tokens=API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS,
        output_tokens=API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS,
    )
    assert recorder.rows[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_with_an_output_item_observed_but_nothing_delivered_releases(recorder: _Recorder) -> None:
    """The parser saw ``response.output_item.added`` inside bytes still withheld ahead of the pin write; the client
    left before anything was flushed, so nothing may be charged."""

    owner = _owner(recorder, reservation=_reservation(limited=True))
    _attach_stream(
        owner,
        holder=SourceUsageHolder(
            first_content_seen=True, first_output_item_seen=True, content_delivered=False, delta_chars=400
        ),
    )
    await owner.finish(status="cancelled", error_code="client_disconnected")

    assert recorder.settle_calls == []
    assert recorder.release_calls == [owner.reservation]


@pytest.mark.asyncio
async def test_cancel_with_the_body_flush_flag_but_no_relayed_content_releases(recorder: _Recorder) -> None:
    """``SourceUsageHolder.content_delivered`` is the body's flush-point evidence; the public wrapper above it can
    still drop or park the frame it counted, so the owner never mirrors it. Only the settlement layer, which hands
    frames to the transport, marks delivery."""

    owner = _owner(recorder, reservation=_reservation(limited=True))
    _attach_stream(
        owner,
        holder=SourceUsageHolder(
            first_content_seen=True, first_output_item_seen=True, content_delivered=True, delta_chars=400
        ),
    )
    await owner.finish(status="cancelled", error_code="client_disconnected")

    assert owner.content_delivered is False
    assert recorder.settle_calls == []
    assert recorder.release_calls == [owner.reservation]
    assert recorder.rows[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_after_the_first_output_item_on_an_unlimited_key_releases(recorder: _Recorder) -> None:
    owner = _owner(recorder, reservation=_reservation(limited=False), content_delivered=True)
    _attach_stream(
        owner, holder=SourceUsageHolder(first_output_item_seen=True, content_delivered=True, delta_chars=9_000)
    )
    await owner.finish(status="cancelled")
    assert recorder.settle_calls == []
    assert recorder.release_calls == [owner.reservation]


@pytest.mark.asyncio
async def test_cancel_with_captured_usage_settles_at_that_usage(recorder: _Recorder) -> None:
    owner = _owner(recorder, reservation=_reservation(limited=True))
    usage = SourceUsage(input_tokens=7, output_tokens=3)
    _attach_stream(owner, holder=SourceUsageHolder(usage=usage, first_output_item_seen=True))
    await owner.finish(status="cancelled")
    assert recorder.settle_calls[0]["usage"] == usage
    assert recorder.release_calls == []


@pytest.mark.asyncio
async def test_error_releases_even_when_usage_is_present(recorder: _Recorder) -> None:
    owner = _owner(recorder, reservation=_reservation(limited=True))
    await owner.finish(status="error", error_code="model_source_idle_timeout", usage=SourceUsage(1, 1))
    assert recorder.settle_calls == []
    assert recorder.release_calls == [owner.reservation]


@pytest.mark.asyncio
async def test_no_reservation_touches_nothing(recorder: _Recorder) -> None:
    owner = _owner(recorder, reservation=None)
    await owner.finish(status="success", usage=SourceUsage(1, 1))
    assert recorder.settle_calls == [] and recorder.release_calls == []
    assert len(recorder.rows) == 1


@pytest.mark.asyncio
async def test_settlement_failure_is_recorded_as_usage_settlement_failed(recorder: _Recorder) -> None:
    recorder.settle_result = False
    owner = _owner(recorder, reservation=_reservation())
    await owner.finish(status="success", usage=SourceUsage(1, 1))
    assert owner.settlement_failed is True
    row = recorder.rows[0]
    assert row["status"] == "error"
    assert row["error_code"] == "usage_settlement_failed"


@pytest.mark.asyncio
async def test_settlement_raising_falls_back_to_release(recorder: _Recorder) -> None:
    recorder.settle_error = RuntimeError("settle exploded")
    owner = _owner(recorder, reservation=_reservation())
    await owner.finish(status="success", usage=SourceUsage(1, 1))
    assert recorder.release_calls == [owner.reservation]
    assert owner.settlement_failed is True
    assert recorder.rows[0]["error_code"] == "usage_settlement_failed"


@pytest.mark.asyncio
async def test_missing_reservation_primitives_log_an_error_instead_of_raising(
    recorder: _Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    owner = _owner(recorder, reservation=_reservation(), settle_reservation=None, release_reservation=None)
    with caplog.at_level(logging.ERROR, logger="app.modules.proxy.source_dispatch"):
        await owner.finish(status="success", usage=SourceUsage(1, 1))
    assert any("source_dispatch_missing" in record.getMessage() for record in caplog.records)
    assert len(recorder.rows) == 1


@given(
    budget=st.one_of(st.none(), st.integers(min_value=1, max_value=200_000)),
    delta_chars=st.integers(min_value=0, max_value=2_000_000),
)
def test_estimate_settlement_usage_figures(budget: int | None, delta_chars: int) -> None:
    admission_budget = None if budget is None else ApiKeyRequestUsageBudget(input_tokens=budget, output_tokens=None)
    estimate = estimate_settlement_usage(admission_budget=admission_budget, delta_chars=delta_chars)
    assert estimate.input_tokens == (API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS if budget is None else budget)
    assert estimate.output_tokens == max(API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS, delta_chars // 4)
    assert estimate.cached_input_tokens == 0
    assert estimate.output_tokens > 0 and estimate.input_tokens > 0


# -- request-log row -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_carries_source_attribution_fields(recorder: _Recorder) -> None:
    request = _FakeRequest(headers={"session_id": "sess-42", "user-agent": "codex_cli_rs/1.0", "thread-id": "thr-1"})
    owner = _owner(recorder, request=request, reservation=None)
    holder = SourceUsageHolder(response_id="resp_source_1")
    _attach_stream(owner, holder=holder)

    await owner.finish(status="success", upstream_status_code=200)

    row = recorder.rows[0]
    assert row["request_id"] == "resp_source_1"
    assert row["archive_request_id"] == "req-proxy-1"
    assert row["session_id"] == "sess-42"
    assert row["requested_service_tier"] == "priority"
    assert row["service_tier"] is None
    assert row["transport"] == "http"
    assert row["upstream_transport"] == "openai_compatible_http"
    assert row["upstream_status_code"] == 200
    assert row["model_source_kind"] == "openai_compatible"
    assert row["useragent"] == "codex_cli_rs/1.0"


@pytest.mark.asyncio
async def test_row_falls_back_to_the_proxy_request_id_and_honours_the_overflow_attribution(
    recorder: _Recorder,
) -> None:
    owner = _owner(recorder, reservation=None, request_log_source="other_source", dispatch_kind="other")
    await owner.finish(status="cancelled", error_code="client_disconnected")
    row = recorder.rows[0]
    assert row["request_id"] == "req-proxy-1"
    assert row["archive_request_id"] == "req-proxy-1"
    assert row["source"] == "other_source"


@pytest.mark.asyncio
async def test_non_stream_source_response_id_is_the_row_request_id(recorder: _Recorder) -> None:
    owner = _owner(recorder, reservation=None)
    owner.source_response_id = "resp_json_1"
    await owner.finish(status="success", usage=SourceUsage(1, 1))
    assert recorder.rows[0]["request_id"] == "resp_json_1"


# -- disconnect watch ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_returns_the_open_result(recorder: _Recorder) -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    request = _FakeRequest()
    owner = _owner(recorder, request=request, scheduler=scheduler, clock=clock)

    async def open_stream() -> str:
        return "stream"

    result = await open_with_disconnect_watch(request.request, owner, open_stream())
    assert result == "stream"
    assert owner.sent_at == 0.0 or owner.sent_at == clock.monotonic()
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_watch_raises_on_disconnect_and_cancels_the_open(recorder: _Recorder) -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    request = _FakeRequest()
    owner = _owner(recorder, request=request, scheduler=scheduler, clock=clock)
    gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    cancelled = asyncio.Event()

    async def open_stream() -> str:
        try:
            await gate
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return "stream"

    watch = asyncio.create_task(open_with_disconnect_watch(request.request, owner, open_stream()))
    await scheduler.advance(OPEN_DISCONNECT_POLL_SECONDS)
    assert not watch.done()
    request.disconnected.set()
    await scheduler.advance(OPEN_DISCONNECT_POLL_SECONDS)
    with pytest.raises(ClientDisconnectedDuringOpen) as excinfo:
        await watch
    assert excinfo.value.stall is False
    assert excinfo.value.pending_seconds == pytest.approx(2 * OPEN_DISCONNECT_POLL_SECONDS)
    assert cancelled.is_set()
    assert all(task.done() for task in scheduler.owned_tasks)


@pytest.mark.asyncio
async def test_watch_reports_a_stall_after_the_evidence_window_without_a_frame(recorder: _Recorder) -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    request = _FakeRequest()
    owner = _owner(recorder, request=request, scheduler=scheduler, clock=clock)
    gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def open_stream() -> str:
        await gate
        return "stream"

    watch = asyncio.create_task(open_with_disconnect_watch(request.request, owner, open_stream()))
    await scheduler.advance(STALL_EVIDENCE_SECONDS)
    request.disconnected.set()
    await scheduler.advance(OPEN_DISCONNECT_POLL_SECONDS)
    with pytest.raises(ClientDisconnectedDuringOpen) as excinfo:
        await watch
    assert excinfo.value.stall is True
    assert excinfo.value.pending_seconds >= STALL_EVIDENCE_SECONDS


@pytest.mark.asyncio
async def test_watch_stall_is_not_reported_once_a_frame_was_seen(recorder: _Recorder) -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    request = _FakeRequest()
    owner = _owner(recorder, request=request, scheduler=scheduler, clock=clock)
    gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def open_stream() -> str:
        await gate
        return "stream"

    watch = asyncio.create_task(open_with_disconnect_watch(request.request, owner, open_stream()))
    await scheduler.advance(STALL_EVIDENCE_SECONDS)
    owner.first_frame_at = clock.monotonic()
    request.disconnected.set()
    await scheduler.advance(OPEN_DISCONNECT_POLL_SECONDS)
    with pytest.raises(ClientDisconnectedDuringOpen) as excinfo:
        await watch
    assert excinfo.value.stall is False


@pytest.mark.asyncio
async def test_watch_completion_in_the_disconnect_iteration_is_closed_by_abandon(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CL-5a: the open assigns ``owner.stream`` itself, so a stream completing into a leaving caller is closed."""

    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    request = _FakeRequest()
    owner = _owner(recorder, request=request, scheduler=scheduler, clock=clock)
    stream = _FakeStream(usage_holder=SourceUsageHolder(), body=_frames())
    release_open = asyncio.Event()

    async def open_stream() -> None:
        await release_open.wait()
        owner.stream = cast(Any, stream)

    async def disconnected_while_the_open_completes() -> bool:
        # The client leaves in the same poll iteration in which the open completes.
        release_open.set()
        for _ in range(3):
            await asyncio.sleep(0)
        return True

    monkeypatch.setattr(request.request, "is_disconnected", disconnected_while_the_open_completes)
    watch = asyncio.create_task(open_with_disconnect_watch(request.request, owner, open_stream()))
    await scheduler.advance(OPEN_DISCONNECT_POLL_SECONDS)
    with pytest.raises(ClientDisconnectedDuringOpen):
        await watch
    assert owner.stream is not None
    await owner.abandon(ABANDON_CLIENT_DISCONNECTED_DURING_OPEN)
    assert stream.closed == 1
    assert recorder.rows[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_watch_caller_cancellation_cancels_and_awaits_the_child(recorder: _Recorder) -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    request = _FakeRequest()
    owner = _owner(recorder, request=request, scheduler=scheduler, clock=clock)
    gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    child_finished = asyncio.Event()

    async def open_stream() -> str:
        try:
            await gate
        finally:
            await asyncio.sleep(0)
            child_finished.set()
        return "stream"

    watch = asyncio.create_task(open_with_disconnect_watch(request.request, owner, open_stream()))
    await scheduler.advance(OPEN_DISCONNECT_POLL_SECONDS)
    watch.cancel()
    with pytest.raises(asyncio.CancelledError):
        await watch
    assert child_finished.is_set()
    assert all(task.done() for task in scheduler.owned_tasks)


@pytest.mark.asyncio
async def test_watch_logs_a_failing_child_without_replacing_the_disconnect(
    recorder: _Recorder, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    request = _FakeRequest()
    owner = _owner(recorder, request=request, scheduler=scheduler, clock=clock)
    release_open = asyncio.Event()

    async def open_stream() -> None:
        await release_open.wait()
        raise RuntimeError("source open failed late")

    async def disconnected_while_the_open_fails() -> bool:
        release_open.set()
        for _ in range(3):
            await asyncio.sleep(0)
        return True

    monkeypatch.setattr(request.request, "is_disconnected", disconnected_while_the_open_fails)
    watch = asyncio.create_task(open_with_disconnect_watch(request.request, owner, open_stream()))
    with caplog.at_level(logging.WARNING, logger="app.modules.proxy.source_dispatch"):
        await scheduler.advance(OPEN_DISCONNECT_POLL_SECONDS)
        with pytest.raises(ClientDisconnectedDuringOpen):
            await watch
    assert any("model_source_open_failed_after_abandonment" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_watch_propagates_the_open_error(recorder: _Recorder) -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    request = _FakeRequest()
    owner = _owner(recorder, request=request, scheduler=scheduler, clock=clock)

    async def open_stream() -> str:
        raise ModelSourceForwardingError(
            status_code=504,
            payload={"error": {"code": "model_source_timeout", "message": "x", "type": "upstream_error"}},
            timeout_phase="header",
        )

    with pytest.raises(ModelSourceForwardingError) as excinfo:
        await open_with_disconnect_watch(request.request, owner, open_stream())
    assert excinfo.value.timeout_phase == "header"


# -- settlement stream ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settlement_stream_success_settles_from_the_holder(recorder: _Recorder) -> None:
    owner = _owner(recorder, reservation=_reservation())
    holder = SourceUsageHolder(first_output_item_seen=True)
    stream = _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        yield "data: one\n\n"
        holder.usage = SourceUsage(input_tokens=9, output_tokens=4)
        holder.terminal_kind = "completed"
        yield "data: two\n\n"

    chunks = [chunk async for chunk in settlement_stream(owner, inner())]
    assert chunks == ["data: one\n\n", "data: two\n\n"]
    assert recorder.settle_calls[0]["usage"] == SourceUsage(input_tokens=9, output_tokens=4)
    assert stream.closed == 1
    assert recorder.rows[0]["status"] == "success"
    assert owner.claims.released is True


@pytest.mark.asyncio
async def test_settlement_stream_aclose_before_the_first_item_releases(recorder: _Recorder) -> None:
    owner = _owner(recorder, reservation=_reservation())
    stream = _attach_stream(owner)
    inner_closed = asyncio.Event()

    async def inner() -> AsyncIterator[str]:
        try:
            yield "data: created\n\n"
            yield "data: never\n\n"
        finally:
            inner_closed.set()

    body = cast(AsyncGenerator[str, None], settlement_stream(owner, inner()))
    assert await body.__anext__() == "data: created\n\n"
    await body.aclose()

    assert inner_closed.is_set()
    assert recorder.release_calls == [owner.reservation]
    assert recorder.settle_calls == []
    assert stream.closed == 1
    assert recorder.rows[0]["status"] == "cancelled"
    assert recorder.rows[0]["error_code"] == "client_disconnected"


@pytest.mark.asyncio
async def test_settlement_stream_task_cancellation_after_the_first_item_settles_at_estimate(
    recorder: _Recorder,
) -> None:
    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder()
    _attach_stream(owner, holder=holder)
    gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def inner() -> AsyncIterator[str]:
        holder.first_output_item_seen = True
        holder.content_delivered = True
        holder.delta_chars = 12_000
        yield "event: response.output_item.added\ndata: {}\n\n"
        await gate
        yield "data: never\n\n"

    body = settlement_stream(owner, inner())

    async def consume() -> list[str]:
        return [chunk async for chunk in body]

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert recorder.release_calls == []
    assert recorder.settle_calls[0]["usage"] == SourceUsage(
        input_tokens=API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS, output_tokens=3_000
    )
    assert recorder.rows[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_settlement_stream_task_cancellation_after_delivered_deltas_without_an_item_settles_at_estimate(
    recorder: _Recorder,
) -> None:
    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder()
    _attach_stream(owner, holder=holder)
    gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def inner() -> AsyncIterator[str]:
        holder.first_content_seen = True
        holder.content_delivered = True
        holder.delta_chars = 12_000
        yield "event: response.output_text.delta\ndata: {}\n\n"
        await gate
        yield "data: never\n\n"

    body = settlement_stream(owner, inner())

    async def consume() -> list[str]:
        return [chunk async for chunk in body]

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert holder.first_output_item_seen is False
    assert recorder.release_calls == []
    assert recorder.settle_calls[0]["usage"] == SourceUsage(
        input_tokens=API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS, output_tokens=3_000
    )
    assert recorder.rows[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_settlement_stream_cancel_after_frames_the_wrapper_dropped_releases_despite_the_body_flag(
    recorder: _Recorder,
) -> None:
    """The body flushed a chunk the parser classified as content (a vendor event, a delta parked ahead of
    ``response.created``), but the public wrapper never relayed it: only bookkeeping frames reached the transport,
    so a client that leaves is released, whatever the holder's flush-point flag says."""

    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder()
    _attach_stream(owner, holder=holder)
    gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def inner() -> AsyncIterator[str]:
        holder.first_content_seen = True
        holder.content_delivered = True
        holder.delta_chars = 12_000
        yield "event: response.created\ndata: {}\n\n"
        yield "event: response.in_progress\ndata: {}\n\n"
        await gate
        yield "event: response.output_text.delta\ndata: {}\n\n"

    body = settlement_stream(owner, inner())

    async def consume() -> list[str]:
        return [chunk async for chunk in body]

    consumer = asyncio.create_task(consume())
    for _ in range(4):
        await asyncio.sleep(0)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert owner.content_delivered is False
    assert recorder.settle_calls == []
    assert recorder.release_calls == [owner.reservation]
    assert recorder.rows[0]["status"] == "cancelled"
    assert recorder.rows[0]["error_code"] == "client_disconnected"


@pytest.mark.asyncio
async def test_settlement_stream_relayed_content_frame_marks_delivery_without_the_body_flag(
    recorder: _Recorder,
) -> None:
    """Delivery is derived from the frames the settlement layer hands to the transport, not read off the holder: a
    relayed ``response.output_item.added`` charges the cancel estimate even when the body never set its flag."""

    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder()
    _attach_stream(owner, holder=holder)
    gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def inner() -> AsyncIterator[str]:
        yield "event: response.created\ndata: {}\n\n"
        assert owner.content_delivered is False
        holder.first_content_seen = True
        holder.first_output_item_seen = True
        yield "event: response.output_item.added\ndata: {}\n\n"
        await gate
        yield "data: never\n\n"

    body = settlement_stream(owner, inner())

    async def consume() -> list[str]:
        return [chunk async for chunk in body]

    consumer = asyncio.create_task(consume())
    for _ in range(4):
        await asyncio.sleep(0)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert holder.content_delivered is False
    assert owner.content_delivered is True
    assert recorder.release_calls == []
    assert recorder.settle_calls[0]["usage"] == SourceUsage(
        input_tokens=API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS,
        output_tokens=API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS,
    )
    assert recorder.rows[0]["status"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_kind", ["completed", "incomplete"])
async def test_settlement_stream_cancel_after_a_relayed_success_terminal_is_a_success(
    recorder: _Recorder, terminal_kind: str
) -> None:
    """Symmetric with the relayed-failure rule: a client that leaves right after the relayed success terminal (before
    the source's own EOF, ``[DONE]`` or keepalives) received the whole answer, so the row is a success settled like a
    completed stream, never ``cancelled``."""

    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder()
    _attach_stream(owner, holder=holder)
    gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    relayed = (
        f"event: response.{terminal_kind}\n"
        f'data: {{"type":"response.{terminal_kind}","response":{{"id":"resp_done","status":"{terminal_kind}"}}}}\n\n'
    )

    async def inner() -> AsyncIterator[str]:
        yield "event: response.created\ndata: {}\n\n"
        holder.first_content_seen = True
        holder.content_delivered = True
        holder.first_output_item_seen = True
        holder.terminal_kind = cast(Any, terminal_kind)
        holder.usage = SourceUsage(input_tokens=9, output_tokens=4)
        yield relayed
        # The source has not closed yet; the client disconnects here.
        await gate
        yield "data: [DONE]\n\n"

    body = settlement_stream(owner, inner())

    async def consume() -> list[str]:
        return [chunk async for chunk in body]

    consumer = asyncio.create_task(consume())
    for _ in range(6):
        await asyncio.sleep(0)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert recorder.release_calls == []
    assert recorder.settle_calls[0]["usage"] == SourceUsage(input_tokens=9, output_tokens=4)
    assert recorder.rows[0]["status"] == "success"
    assert recorder.rows[0]["error_code"] is None


@pytest.mark.asyncio
async def test_settlement_stream_forwarding_error_records_the_source_code_and_timeout_phase(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    phases: list[str] = []

    class _Counter:
        def labels(self, **labels: str) -> _Counter:
            phases.append(labels["phase"])
            return self

        def inc(self, amount: float = 1) -> None:
            pass

    monkeypatch.setattr(dispatch_module, "model_source_timeout_total", _Counter())
    owner = _owner(recorder, reservation=_reservation())
    _attach_stream(owner)

    async def inner() -> AsyncIterator[str]:
        yield "data: created\n\n"
        raise ModelSourceForwardingError(
            status_code=504,
            payload={"error": {"code": "model_source_idle_timeout", "message": "idle", "type": "upstream_error"}},
            timeout_phase="idle",
        )

    with pytest.raises(ModelSourceForwardingError):
        _ = [chunk async for chunk in settlement_stream(owner, inner())]

    assert phases == ["idle"]
    assert recorder.release_calls == [owner.reservation]
    assert recorder.rows[0]["status"] == "error"
    assert recorder.rows[0]["error_code"] == "model_source_idle_timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_kind", ["failed", "error"])
async def test_settlement_stream_failure_terminal_releases_a_limited_key_instead_of_estimating(
    recorder: _Recorder, terminal_kind: str
) -> None:
    """A relayed ``response.failed``/``error`` ends the stream normally; it is an error, never a charged success."""

    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder()
    _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        yield "data: created\n\n"
        holder.terminal_kind = cast(Any, terminal_kind)
        yield "data: failed\n\n"

    chunks = [chunk async for chunk in settlement_stream(owner, inner())]

    assert chunks == ["data: created\n\n", "data: failed\n\n"]
    assert recorder.settle_calls == []
    assert recorder.release_calls == [owner.reservation]
    assert recorder.rows[0]["status"] == "error"
    assert recorder.rows[0]["error_code"] == "model_source_response_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_kind", ["failed", "error"])
async def test_settlement_stream_cancel_after_a_relayed_failure_terminal_releases_not_estimates(
    recorder: _Recorder, terminal_kind: str
) -> None:
    """A client that leaves right after a relayed ``response.failed``/``error`` (before the source's own EOF --
    Codex tears the stream down on the failure terminal while many sources still send ``[DONE]``/keepalives) has
    received a failure, so the reservation is released and the row is an error, never charged at the cancel
    estimate (design v3 §6.4; api-keys 'Failure terminal is never charged')."""

    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder()
    _attach_stream(owner, holder=holder)
    gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def inner() -> AsyncIterator[str]:
        holder.first_output_item_seen = True
        holder.delta_chars = 12_000
        yield "event: response.output_item.added\ndata: {}\n\n"
        holder.terminal_kind = cast(Any, terminal_kind)
        yield "event: response.failed\ndata: {}\n\n"
        # The source has not closed yet; the client disconnects here.
        await gate
        yield "data: never\n\n"

    body = settlement_stream(owner, inner())

    async def consume() -> list[str]:
        return [chunk async for chunk in body]

    consumer = asyncio.create_task(consume())
    for _ in range(6):
        await asyncio.sleep(0)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert recorder.settle_calls == []
    assert recorder.release_calls == [owner.reservation]
    assert recorder.rows[0]["status"] == "error"
    assert recorder.rows[0]["error_code"] == "model_source_response_failed"
    assert recorder.rows[0]["input_tokens"] is None


_SYNTHESIZED_TRUNCATION = (
    "event: response.failed\n"
    'data: {"type":"response.failed","sequence_number":3,"response":{"id":"resp_upstream_stream_truncated",'
    '"object":"response","status":"failed","error":{"code":"upstream_stream_truncated","message":"truncated"}}}\n\n'
)
_RELAYED_COMPLETED = (
    "event: response.completed\n"
    'data: {"type":"response.completed","response":{"id":"resp_big","status":"completed",'
    '"output":[{"type":"message"}]}}\n\n'
)


@pytest.mark.asyncio
async def test_settlement_stream_cancel_after_a_relayed_typeless_error_record_releases_not_estimates(
    recorder: _Recorder,
) -> None:
    """Native mode relays a source's typeless ``{"error": {...}}`` record verbatim; the public wrapper classifies it as
    the ``error`` terminal, so a client that tears down on it received a failure: released, never the estimate."""

    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder(first_content_seen=True, first_output_item_seen=True, delta_chars=400)
    stream = _attach_stream(owner, holder=holder)
    gate = asyncio.Event()

    async def inner() -> AsyncIterator[str]:
        yield "event: response.created\ndata: {}\n\n"
        yield "event: response.output_text.delta\ndata: {}\n\n"
        yield 'data: {"error":{"message":"boom","code":"server_error"}}\n\n'
        await gate.wait()

    async def consume() -> None:
        async for _chunk in settlement_stream(owner, inner()):
            pass

    task = asyncio.create_task(consume())
    for _ in range(10):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert recorder.settle_calls == []
    assert recorder.release_calls == [owner.reservation]
    assert stream.closed == 1
    assert recorder.rows[0]["status"] == "error"
    assert recorder.rows[0]["error_code"] == "model_source_response_failed"


@pytest.mark.asyncio
async def test_settlement_stream_clean_eof_without_a_terminal_is_truncated_and_released(recorder: _Recorder) -> None:
    """A source that closes after content but before any terminal delivered a failure (the wrapper synthesizes
    ``response.failed upstream_stream_truncated``): the row is an error and a limited key is never charged."""

    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder(first_content_seen=True, first_output_item_seen=True, delta_chars=400)
    stream = _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        yield "event: response.created\ndata: {}\n\n"
        yield "event: response.output_text.delta\ndata: {}\n\n"
        yield _SYNTHESIZED_TRUNCATION

    chunks = [chunk async for chunk in settlement_stream(owner, inner())]

    assert len(chunks) == 3
    assert holder.terminal_kind is None
    assert recorder.settle_calls == []
    assert recorder.release_calls == [owner.reservation]
    assert stream.closed == 1
    assert recorder.rows[0]["status"] == "error"
    assert recorder.rows[0]["error_code"] == "model_source_stream_truncated"
    assert recorder.rows[0]["input_tokens"] is None
    assert owner.claims.released is True


@pytest.mark.asyncio
async def test_settlement_stream_relayed_success_terminal_the_parser_missed_settles_at_the_estimate(
    recorder: _Recorder,
) -> None:
    """Decision 24: a success terminal that outgrew the parser's frame cap still reached the client."""

    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder(first_content_seen=True, first_output_item_seen=True, delta_chars=8_000)
    _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        yield "event: response.created\ndata: {}\n\n"
        yield _RELAYED_COMPLETED

    _ = [chunk async for chunk in settlement_stream(owner, inner())]

    assert holder.terminal_kind is None
    assert recorder.release_calls == []
    assert recorder.settle_calls[0]["usage"] == SourceUsage(
        input_tokens=API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS, output_tokens=2_048
    )
    assert recorder.rows[0]["status"] == "success"


@pytest.mark.asyncio
async def test_settlement_stream_trailing_sentinel_and_keepalives_do_not_hide_the_relayed_terminal(
    recorder: _Recorder,
) -> None:
    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder(first_content_seen=True, first_output_item_seen=True)
    _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        yield ": keepalive\n\n"
        yield _RELAYED_COMPLETED
        yield 'event: codex.keepalive\ndata: {"type":"codex.keepalive"}\n\n'
        yield "data: [DONE]\n\n"

    _ = [chunk async for chunk in settlement_stream(owner, inner())]

    assert recorder.rows[0]["status"] == "success"
    assert len(recorder.settle_calls) == 1


@pytest.mark.asyncio
async def test_settlement_stream_unparseable_relayed_tail_without_a_terminal_is_truncated(recorder: _Recorder) -> None:
    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder(first_content_seen=True)
    _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        yield "event: response.created\ndata: {}\n\n"
        yield 'data: {"type":"response.completed","response":{"id":"resp_bad"}\n\n'

    _ = [chunk async for chunk in settlement_stream(owner, inner())]

    assert recorder.rows[0]["status"] == "error"
    assert recorder.rows[0]["error_code"] == "model_source_stream_truncated"
    assert recorder.release_calls == [owner.reservation]


_SYNTHESIZED_INVALID_JSON = (
    "event: response.failed\n"
    'data: {"type":"response.failed","sequence_number":3,"response":{"id":"resp_invalid_json",'
    '"object":"response","status":"failed","error":{"code":"invalid_json","message":"malformed"}}}\n\n'
)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_kind", ["completed", "incomplete"])
async def test_settlement_stream_success_terminal_rewritten_into_a_relayed_failure_is_an_error(
    recorder: _Recorder, terminal_kind: str
) -> None:
    """The parser read the source's success terminal, but the public wrapper rewrote it into ``response.failed``
    (non-mapping ``response``, invalid output items): the wire carried a failure, so the row is an error and a
    limited key is released, never settled at the estimate."""

    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder()
    stream = _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        yield "event: response.created\ndata: {}\n\n"
        holder.first_content_seen = True
        holder.first_output_item_seen = True
        yield "event: response.output_item.added\ndata: {}\n\n"
        holder.terminal_kind = cast(Any, terminal_kind)
        yield _SYNTHESIZED_INVALID_JSON

    chunks = [chunk async for chunk in settlement_stream(owner, inner())]

    assert len(chunks) == 3
    assert recorder.settle_calls == []
    assert recorder.release_calls == [owner.reservation]
    assert stream.closed == 1
    assert recorder.rows[0]["status"] == "error"
    assert recorder.rows[0]["error_code"] == "model_source_response_invalid"
    assert recorder.rows[0]["input_tokens"] is None
    assert owner.claims.released is True


@pytest.mark.asyncio
async def test_settlement_stream_typed_event_trailing_a_rewritten_terminal_keeps_the_failure(
    recorder: _Recorder,
) -> None:
    """The source keeps emitting typed events after the terminal the wrapper rewrote into ``response.failed``: the
    client-visible terminal is latched, so the trailing frame does not turn the attempt back into a charged success."""

    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder()
    stream = _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        yield "event: response.created\ndata: {}\n\n"
        holder.first_content_seen = True
        holder.first_output_item_seen = True
        yield "event: response.output_item.added\ndata: {}\n\n"
        holder.terminal_kind = "completed"
        yield _SYNTHESIZED_INVALID_JSON
        yield "event: response.output_text.done\ndata: {}\n\n"

    chunks = [chunk async for chunk in settlement_stream(owner, inner())]

    assert len(chunks) == 4
    assert recorder.settle_calls == []
    assert recorder.release_calls == [owner.reservation]
    assert stream.closed == 1
    assert recorder.rows[0]["status"] == "error"
    assert recorder.rows[0]["error_code"] == "model_source_response_invalid"


@pytest.mark.asyncio
async def test_settlement_stream_cancel_after_a_relayed_failure_and_a_trailing_typed_event_releases(
    recorder: _Recorder,
) -> None:
    """A relayed failure terminal is sticky in the cancel branch too: a delta relayed behind it before the client
    tears down does not reset the classification to a cancel settled at the estimate."""

    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder(first_content_seen=True, first_output_item_seen=True, delta_chars=400)
    stream = _attach_stream(owner, holder=holder)
    gate = asyncio.Event()

    async def inner() -> AsyncIterator[str]:
        yield "event: response.created\ndata: {}\n\n"
        yield "event: response.failed\ndata: {}\n\n"
        yield "event: response.output_text.delta\ndata: {}\n\n"
        await gate.wait()

    async def consume() -> None:
        async for _chunk in settlement_stream(owner, inner()):
            pass

    task = asyncio.create_task(consume())
    for _ in range(10):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert recorder.settle_calls == []
    assert recorder.release_calls == [owner.reservation]
    assert stream.closed == 1
    assert recorder.rows[0]["status"] == "error"
    assert recorder.rows[0]["error_code"] == "model_source_response_failed"


@pytest.mark.asyncio
async def test_settlement_stream_typed_event_trailing_a_relayed_success_terminal_stays_a_success(
    recorder: _Recorder,
) -> None:
    """Symmetric latch: a vendor frame relayed after ``response.completed`` the parser missed (oversized) does not
    demote the delivered answer to a truncated stream."""

    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder(first_content_seen=True, first_output_item_seen=True)
    _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        yield "event: response.created\ndata: {}\n\n"
        yield _RELAYED_COMPLETED
        yield 'event: codex.rate_limits\ndata: {"type":"codex.rate_limits"}\n\n'

    _ = [chunk async for chunk in settlement_stream(owner, inner())]

    assert holder.terminal_kind is None
    assert recorder.release_calls == []
    assert len(recorder.settle_calls) == 1
    assert recorder.rows[0]["status"] == "success"


@pytest.mark.asyncio
async def test_settlement_stream_relayed_failure_after_a_relayed_success_is_an_error(recorder: _Recorder) -> None:
    """Failure precedence: a source that follows its success terminal with a failure terminal delivered a failure."""

    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder(first_content_seen=True, first_output_item_seen=True)
    _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        yield "event: response.created\ndata: {}\n\n"
        yield _RELAYED_COMPLETED
        yield "event: error\ndata: {}\n\n"

    _ = [chunk async for chunk in settlement_stream(owner, inner())]

    assert recorder.settle_calls == []
    assert recorder.release_calls == [owner.reservation]
    assert recorder.rows[0]["status"] == "error"


@pytest.mark.asyncio
async def test_settlement_stream_success_terminal_relayed_as_a_success_stays_a_success(recorder: _Recorder) -> None:
    """Control for the rewrite rule: a parsed success terminal the wrapper relayed intact settles as before."""

    owner = _owner(recorder, reservation=_reservation(limited=True))
    holder = SourceUsageHolder(first_content_seen=True, first_output_item_seen=True)
    _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        yield "event: response.created\ndata: {}\n\n"
        holder.terminal_kind = "completed"
        holder.usage = SourceUsage(input_tokens=11, output_tokens=5)
        yield _RELAYED_COMPLETED

    _ = [chunk async for chunk in settlement_stream(owner, inner())]

    assert recorder.release_calls == []
    assert recorder.settle_calls[0]["usage"] == SourceUsage(input_tokens=11, output_tokens=5)
    assert recorder.rows[0]["status"] == "success"


@pytest.mark.parametrize(
    ("frame", "kind"),
    [
        ("event: response.completed\ndata: {}\n\n", "completed"),
        ("event: response.incomplete\r\ndata: {}\r\n\r\n", "incomplete"),
        ("event: response.completed\rdata: {}\r\r", "completed"),
        ("event: response.failed\rdata: {}\r\r", "failed"),
        ("event: response.failed\ndata: {}\n\n", "failed"),
        ("event: error\ndata: {}\n\n", "error"),
        ('data: {"type":"response.completed"}\n\n', "completed"),
        ('data: {"type":\ndata: "response.completed"}\n\n', "completed"),
        ('data: {"type":"response.completed"}\r\r', "completed"),
        ('data: {"error":{"message":"boom","code":"server_error"}}\n\n', "error"),
        ('data: {"error":"boom"}\n\n', None),
        ('data: {"type":"response.output_text.delta"}\n\n', None),
        ("event: response.output_item.added\ndata: {}\n\n", None),
        ("data: [DONE]\n\n", None),
        (": keepalive\n\n", None),
        ("data: not json\n\n", None),
        ("", None),
        (None, None),
    ],
)
def test_relayed_terminal_kind_table(frame: str | None, kind: str | None) -> None:
    assert dispatch_module.relayed_terminal_kind(frame) == kind


@pytest.mark.parametrize(
    ("frame", "delivers"),
    [
        ("event: response.created\ndata: {}\n\n", False),
        ("event: response.in_progress\ndata: {}\n\n", False),
        ("event: response.created\rdata: {}\r\r", False),
        ("event: response.in_progress\rdata: {}\r\r", False),
        ("event: response.queued\ndata: {}\n\n", False),
        ("event: response.output_item.added\ndata: {}\n\n", True),
        ("event: response.output_text.delta\r\ndata: {}\r\n\r\n", True),
        ("event: response.output_text.delta\rdata: {}\r\r", True),
        ("event: response.reasoning_summary_text.delta\ndata: {}\n\n", True),
        ("event: response.completed\ndata: {}\n\n", True),
        ("event: response.incomplete\ndata: {}\n\n", True),
        ("event: codex.rate_limits\ndata: {}\n\n", True),
        ("event: response.failed\ndata: {}\n\n", False),
        ("event: error\ndata: {}\n\n", False),
        ('data: {"type":"response.output_item.added"}\n\n', True),
        ('data: {"type":"response.created"}\n\n', False),
        ('data: {"type":\ndata: "response.created"}\n\n', False),
        ('data: {"type":"response.created"}\r\r', False),
        ('data: {"delta":"\u2028","type":"response.output_text.delta"}\n\n', True),
        ('data: {"type":"response.failed"}\n\n', False),
        ('data: {"error":{"message":"boom"}}\n\n', False),
        ('data: {"type":"response.completed"}\n\n', True),
        ('data: {"delta":"no type"}\n\n', False),
        ("data: [1,2]\n\n", False),
        ("data: not json\n\n", False),
        ("data: [DONE]\n\n", False),
        (": keepalive\n\n", False),
        ('event: codex.keepalive\ndata: {"type":"codex.keepalive"}\n\n', False),
        ("", False),
        (None, False),
    ],
)
def test_relayed_frame_delivers_content_table(frame: str | None, delivers: bool) -> None:
    """Parity with ``classify_responses_frame``: content, success terminals and unknown event types deliver;
    bookkeeping, failure terminals and frames without a typed event do not."""

    assert dispatch_module.relayed_frame_delivers_content(frame) is delivers


@pytest.mark.asyncio
async def test_settlement_stream_unexpected_exception_is_a_stream_error(recorder: _Recorder) -> None:
    owner = _owner(recorder, reservation=_reservation())
    _attach_stream(owner)

    async def inner() -> AsyncIterator[str]:
        yield "data: created\n\n"
        raise ValueError("boom")

    with pytest.raises(ValueError):
        _ = [chunk async for chunk in settlement_stream(owner, inner())]
    assert recorder.rows[0]["error_code"] == "model_source_stream_error"
    assert recorder.rows[0]["error_message"] == "ValueError"
    assert recorder.release_calls == [owner.reservation]


# -- pin hook and synthesized pair ------------------------------------------------------------------


class _FakeExecutor:
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.calls = 0

    async def commit(self, intent: PinIntent, *, drain_until: object, scheduler: object, clock: object) -> str:
        self.calls += 1
        return self.outcome


def _intent() -> PinIntent:
    return PinIntent(
        writes=(PinWrite(pin_key="thread\nabc", kind="thread", source_id="src", api_key_id=None),), thread_key="abc"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["not_written", "unknown"])
async def test_on_first_content_raises_when_the_pin_did_not_verify(recorder: _Recorder, outcome: str) -> None:
    executor = _FakeExecutor(outcome)
    owner = _owner(recorder, pin_intent=_intent(), pin_executor=executor)
    with pytest.raises(SourcePinCommitError) as excinfo:
        await owner.on_first_content(SourceUsageHolder())
    assert excinfo.value.outcome == outcome
    assert owner.pin_outcome == outcome
    assert executor.calls == 1


@pytest.mark.asyncio
async def test_on_first_content_written_lets_the_stream_proceed(recorder: _Recorder) -> None:
    executor = _FakeExecutor("written")
    owner = _owner(recorder, pin_intent=_intent(), pin_executor=executor)
    await owner.on_first_content(SourceUsageHolder())
    assert owner.pin_outcome == "written"


@pytest.mark.asyncio
async def test_on_first_content_without_an_intent_is_a_no_op(recorder: _Recorder) -> None:
    owner = _owner(recorder)
    await owner.on_first_content(SourceUsageHolder())
    assert owner.pin_outcome is None


def test_synthesized_pair_shape() -> None:
    envelope = {"id": "resp_src", "object": "response", "model": "src-model", "created_at": 1}
    frames = synthesized_pin_failure_frames(envelope, error_code="pin_unavailable")
    assert len(frames) == 2
    created = json.loads(frames[0].split("data: ", 1)[1])
    failed = json.loads(frames[1].split("data: ", 1)[1])
    assert frames[0].startswith("event: response.created\n")
    assert frames[1].startswith("event: response.failed\n")
    assert created["sequence_number"] == 0 and failed["sequence_number"] == 1
    assert created["response"]["id"] == "resp_src" and failed["response"]["id"] == "resp_src"
    assert created["response"]["status"] == "in_progress"
    assert failed["response"]["status"] == "failed"
    assert failed["response"]["error"]["code"] == "pin_unavailable"
    assert failed["response"]["error"]["type"] == "server_error"
    minimal = synthesized_pin_failure_frames(None, error_code="pin_unavailable")
    assert json.loads(minimal[0].split("data: ", 1)[1])["response"]["object"] == "response"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_row_code"), [("not_written", "pin_failed"), ("unknown", "pin_unverified")]
)
async def test_settlement_stream_pin_failure_yields_exactly_the_pair_and_releases(
    recorder: _Recorder, outcome: str, expected_row_code: str
) -> None:
    executor = _FakeExecutor(outcome)
    owner = _owner(
        recorder,
        reservation=_reservation(),
        pin_intent=_intent(),
        pin_executor=executor,
        pin_failure_error_code="pin_failed",
        pin_unverified_error_code="pin_unverified",
    )
    holder = SourceUsageHolder(created_envelope={"id": "resp_src", "object": "response"})
    stream = _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        # The forwarding body withholds non-content frames and awaits the hook at the first content frame.
        await owner.on_first_content(holder)
        yield "data: never delivered\n\n"

    chunks = [chunk async for chunk in settlement_stream(owner, inner())]

    assert len(chunks) == 2
    assert "response.created" in chunks[0] and "response.failed" in chunks[1]
    assert "never delivered" not in "".join(chunks)
    assert json.loads(chunks[1].split("data: ", 1)[1])["response"]["error"]["code"] == "pin_failed"
    assert recorder.release_calls == [owner.reservation]
    assert recorder.settle_calls == []
    assert stream.closed == 1
    assert len(recorder.rows) == 1
    assert recorder.rows[0]["status"] == "error"
    assert recorder.rows[0]["error_code"] == expected_row_code


@pytest.mark.asyncio
async def test_settlement_stream_pin_written_flushes_withheld_frames(recorder: _Recorder) -> None:
    executor = _FakeExecutor("written")
    owner = _owner(recorder, reservation=_reservation(limited=False), pin_intent=_intent(), pin_executor=executor)
    holder = SourceUsageHolder(terminal_kind="completed")
    _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        withheld = ["data: created\n\n", "data: in_progress\n\n"]
        await owner.on_first_content(holder)
        for frame in withheld:
            yield frame
        yield "data: item\n\n"

    chunks = [chunk async for chunk in settlement_stream(owner, inner())]
    assert chunks == ["data: created\n\n", "data: in_progress\n\n", "data: item\n\n"]
    assert recorder.rows[0]["status"] == "success"


# -- SourceStreamingResponse transport ------------------------------------------------------------------


async def _run_response(
    response: SourceStreamingResponse,
    *,
    disconnect_immediately: bool,
) -> list[dict[str, object]]:
    sent: list[dict[str, object]] = []
    disconnect = asyncio.Event()
    if disconnect_immediately:
        disconnect.set()

    async def receive() -> dict[str, object]:
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        await asyncio.sleep(0)
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "method": "POST", "path": "/"}
    await response(scope, cast(Any, receive), cast(Any, send))
    return sent


@pytest.mark.asyncio
async def test_streaming_response_completion_writes_exactly_one_row(recorder: _Recorder) -> None:
    owner = _owner(recorder, reservation=_reservation(limited=False))
    holder = SourceUsageHolder(usage=SourceUsage(input_tokens=2, output_tokens=1), terminal_kind="completed")
    stream = _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        yield "data: a\n\n"
        yield "data: b\n\n"

    response = SourceStreamingResponse(settlement_stream(owner, inner()), owner=owner, headers={"X-Test": "1"})
    sent = await _run_response(response, disconnect_immediately=False)

    bodies = [message["body"] for message in sent if message["type"] == "http.response.body"]
    assert b"".join(cast(list[bytes], bodies)) == b"data: a\n\ndata: b\n\n"
    assert len(recorder.rows) == 1
    assert recorder.rows[0]["status"] == "success"
    assert len(recorder.settle_calls) == 1
    assert stream.closed == 1
    assert owner.claims.released is True


@pytest.mark.asyncio
async def test_streaming_response_disconnect_before_the_body_starts_finishes_cancelled(recorder: _Recorder) -> None:
    """CL-6: an ASGI disconnect before Starlette iterates the body still reaches one ``finish()``."""

    owner = _owner(recorder, reservation=_reservation(limited=True))
    stream = _attach_stream(owner)
    started = asyncio.Event()

    async def inner() -> AsyncIterator[str]:
        started.set()
        yield "data: a\n\n"

    response = SourceStreamingResponse(settlement_stream(owner, inner()), owner=owner)
    await _run_response(response, disconnect_immediately=True)

    assert not started.is_set()
    assert stream.closed == 1
    assert recorder.release_calls == [owner.reservation]
    assert recorder.settle_calls == []
    assert len(recorder.rows) == 1
    assert recorder.rows[0]["status"] == "cancelled"
    assert recorder.rows[0]["error_code"] == ABANDON_CLIENT_DISCONNECTED_BEFORE_BODY
    assert owner.claims.released is True


# -- chat-completions transport owner (WP-C1 follow-up F1) -------------------------------------------


def _chat_stream(record: list[str]) -> SourceChatStream:
    """A ``SourceChatStream`` whose transport is a real ``SourceStreamTransport`` over a recording exit stack."""

    stack = AsyncExitStack()

    async def release_transport() -> None:
        record.append("transport_closed")

    stack.push_async_callback(release_transport)

    async def body() -> AsyncIterator[bytes]:
        yield b"data: chunk\n\n"

    return SourceChatStream(
        body=body(),
        usage_holder=SourceUsageHolder(),
        upstream_status_code=200,
        transport=SourceStreamTransport(stack, scheduler=dispatch_module.REAL_SCHEDULER),
    )


def _chat_owner(record: list[str]) -> SourceChatStreamOwner:
    async def abandoned_before_body() -> None:
        record.append("abandoned_before_body")

    return SourceChatStreamOwner(stream=_chat_stream(record), on_abandoned_before_body=abandoned_before_body)


@pytest.mark.asyncio
async def test_chat_owner_disconnect_before_the_body_starts_closes_the_transport_and_records_once() -> None:
    """Chat parity with CL-6: an ASGI disconnect before Starlette iterates the body closes the source transport
    directly and runs the route's abandoned-before-body step exactly once, even when finalized again."""

    record: list[str] = []
    owner = _chat_owner(record)
    started = asyncio.Event()

    async def outer() -> AsyncIterator[bytes]:
        owner.body_started = True  # what the route's settlement generator does on entry
        started.set()
        yield b"data: a\n\n"

    response = SourceStreamingResponse(outer(), owner=owner)
    await _run_response(response, disconnect_immediately=True)

    assert not started.is_set()
    assert record == ["transport_closed", "abandoned_before_body"]
    assert owner.finished is True and owner.body_started is False

    await owner.finalize_transport()
    assert record == ["transport_closed", "abandoned_before_body"]


@pytest.mark.asyncio
async def test_chat_owner_leaves_a_started_body_to_its_own_settlement() -> None:
    """Once the settlement generator ran it owns the outcome: the finalizer neither closes the transport (the body's
    ``finally`` does) nor records a second outcome."""

    record: list[str] = []
    owner = _chat_owner(record)

    async def outer() -> AsyncIterator[bytes]:
        owner.body_started = True
        try:
            yield b"data: a\n\n"
            yield b"data: b\n\n"
        finally:
            record.append("body_finally")

    response = SourceStreamingResponse(outer(), owner=owner)
    sent = await _run_response(response, disconnect_immediately=False)

    bodies = [message["body"] for message in sent if message["type"] == "http.response.body"]
    assert b"".join(cast(list[bytes], bodies)) == b"data: a\n\ndata: b\n\n"
    assert record == ["body_finally"]
    assert owner.finished is False


@pytest.mark.asyncio
async def test_chat_owner_finalizer_closes_a_body_suspended_at_a_yield_before_deciding() -> None:
    """A body abandoned mid-yield (the first write failed after the generator started) is closed first, so its own
    cleanup runs and the abandoned-before-body step does not."""

    record: list[str] = []
    owner = _chat_owner(record)

    async def outer() -> AsyncIterator[bytes]:
        owner.body_started = True
        try:
            yield b"data: a\n\n"
        finally:
            record.append("body_finally")

    body = outer()
    SourceStreamingResponse(body, owner=owner)
    assert await anext(body) == b"data: a\n\n"

    await owner.finalize_transport()

    assert record == ["body_finally"]
    assert owner.finished is False


@pytest.mark.asyncio
async def test_chat_owner_records_the_abandonment_even_when_the_transport_close_raises() -> None:
    record: list[str] = []
    stack = AsyncExitStack()

    async def broken_release() -> None:
        record.append("transport_close_attempted")
        raise RuntimeError("connector gone")

    stack.push_async_callback(broken_release)

    async def body() -> AsyncIterator[bytes]:
        yield b""

    async def abandoned_before_body() -> None:
        record.append("abandoned_before_body")

    owner = SourceChatStreamOwner(
        stream=SourceChatStream(
            body=body(),
            usage_holder=SourceUsageHolder(),
            upstream_status_code=200,
            transport=SourceStreamTransport(stack, scheduler=dispatch_module.REAL_SCHEDULER),
        ),
        on_abandoned_before_body=abandoned_before_body,
    )
    SourceStreamingResponse(body(), owner=owner)

    await owner.finalize_transport()

    assert record == ["transport_close_attempted", "abandoned_before_body"]
    assert owner.finished is True


# -- metric increments (spec scenario -> counter assertions) --------------------------------------


class _LabelRecorder:
    """Records ``labels(**kw).inc()`` so a scenario can assert the exact label set.

    ``SourceDispatch`` increments through ``_inc``/``.labels(...).inc()``, which
    swallow a wrong label set (a missing/extra label would raise inside the
    metrics client and be logged, never surfaced), so the observability and
    api-keys delta clauses that end in a counter increment need a direct assertion.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.incs = 0

    def labels(self, **labels: str) -> _LabelRecorder:
        self.calls.append(labels)
        return self

    def inc(self, amount: float = 1) -> None:
        self.incs += 1


@pytest.mark.asyncio
async def test_success_dispatch_increments_dispatch_total_with_kind_and_status(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """observability 'Successful dispatch row attribution': ``dispatch_total{kind=direct,status=success}``."""

    counter = _LabelRecorder()
    monkeypatch.setattr(dispatch_module, "model_source_dispatch_total", counter)
    owner = _owner(recorder, reservation=None)
    await owner.finish(status="success", usage=SourceUsage(input_tokens=1, output_tokens=1))

    assert counter.calls == [{"kind": "direct", "status": "success"}]
    assert counter.incs == 1


@pytest.mark.asyncio
async def test_abandon_during_open_increments_abandoned_total_with_stage(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """observability 'Client leaves while the open is pending': ``dispatch_abandoned_total{stage=during_open}``."""

    counter = _LabelRecorder()
    monkeypatch.setattr(dispatch_module, "model_source_dispatch_abandoned_total", counter)
    owner = _owner(recorder, reservation=_reservation())
    _attach_stream(owner)
    await owner.abandon(ABANDON_CLIENT_DISCONNECTED_DURING_OPEN)

    assert counter.calls == [{"stage": "during_open"}]
    assert counter.incs == 1


@pytest.mark.asyncio
async def test_missing_usage_increments_usage_estimated_total_with_source_and_cause(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """api-keys 'Missing usage settles at the estimate': ``usage_estimated_total{source_id,cause=missing_usage}``."""

    counter = _LabelRecorder()
    monkeypatch.setattr(dispatch_module, "model_source_usage_estimated_total", counter)
    owner = _owner(recorder, reservation=_reservation(limited=True))
    _attach_stream(owner, holder=SourceUsageHolder(first_output_item_seen=True, delta_chars=20_000))
    await owner.finish(status="success")

    assert counter.calls == [{"source_id": owner.source.id, "cause": "missing_usage"}]
    assert counter.incs == 1


# -- on_finished hook and anchor resolution (#2123 WP-C2, C1 gaps 9/10) ------------------------------


@dataclass(slots=True)
class _HookRecorder:
    calls: list[tuple[SourceDispatch, str]] = field(default_factory=list)
    error: Exception | None = None

    def __call__(self, owner: SourceDispatch, status: str) -> None:
        self.calls.append((owner, status))
        if self.error is not None:
            raise self.error


@pytest.mark.asyncio
async def test_on_finished_is_called_exactly_once_even_when_release_and_row_raise(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder.release_error = RuntimeError("db down")
    hook = _HookRecorder()
    owner = _owner(recorder, reservation=_reservation(), on_finished=hook)
    _attach_stream(owner)

    async def failing_row(self: SourceDispatch, **kwargs: object) -> None:
        raise RuntimeError("row down")

    monkeypatch.setattr(SourceDispatch, "write_row", failing_row)
    with pytest.raises(RuntimeError, match="row down"):
        await owner.finish(status="error", error_code="model_source_timeout")
    await owner.finish(status="error", error_code="model_source_timeout")
    await owner.finish(status="success")
    await owner.abandon(ABANDON_CLIENT_DISCONNECTED_DURING_OPEN)

    assert hook.calls == [(owner, "error")]
    assert owner.finished is True


@pytest.mark.asyncio
async def test_on_finished_receives_the_settlement_corrected_status(recorder: _Recorder) -> None:
    recorder.settle_result = False
    hook = _HookRecorder()
    owner = _owner(recorder, reservation=_reservation(limited=False), on_finished=hook)
    _attach_stream(owner)
    await owner.finish(status="success", usage=SourceUsage(input_tokens=3, output_tokens=2))
    assert hook.calls == [(owner, "error")]
    assert recorder.rows[0]["error_code"] == "usage_settlement_failed"


@pytest.mark.asyncio
async def test_on_finished_failure_never_breaks_the_latch(recorder: _Recorder, caplog) -> None:
    hook = _HookRecorder(error=RuntimeError("metrics down"))
    bulkhead = SourceBulkhead()
    owner = _owner(recorder, reservation=_reservation(), bulkhead=bulkhead, on_finished=hook)
    _attach_stream(owner)
    caplog.set_level(logging.WARNING, logger="app.modules.proxy.source_dispatch")
    await owner.finish(status="success", usage=SourceUsage(input_tokens=1, output_tokens=1))
    assert len(hook.calls) == 1
    assert len(recorder.rows) == 1
    assert bulkhead.in_flight(owner.source.id) == 0
    assert "source_dispatch_on_finished_failed" in caplog.text


@pytest.mark.asyncio
async def test_on_finished_defaults_to_none_for_direct_routing(recorder: _Recorder) -> None:
    owner = _owner(recorder)
    assert owner.on_finished is None
    _attach_stream(owner)
    await owner.finish(status="success")
    assert len(recorder.rows) == 1


class _ResolvingExecutor:
    def __init__(self) -> None:
        self.intents: list[PinIntent] = []

    async def commit(self, intent: PinIntent, *, drain_until: object, scheduler: object, clock: object) -> str:
        self.intents.append(intent)
        return "written"


@pytest.mark.asyncio
async def test_on_first_content_commits_the_intent_resolved_against_the_source_response_id(recorder: _Recorder) -> None:
    """The anchor row for an SDK chain lands in the same transaction as the thread pin (design §3, §6.3)."""

    executor = _ResolvingExecutor()
    intent = PinIntent(
        writes=(PinWrite(pin_key="thread\nabc", kind="thread", source_id="src", api_key_id="key-1"),),
        thread_key="abc",
        source_id="src",
        anchor_api_key_id="key-1",
        anchor=True,
    )
    owner = _owner(recorder, pin_intent=intent, pin_executor=executor)
    await owner.on_first_content(SourceUsageHolder(response_id="resp_source_1"))

    assert len(executor.intents) == 1
    committed = executor.intents[0]
    assert [write.pin_key for write in committed.writes] == ["thread\nabc", "anchor\nkey-1\nresp_source_1"]
    assert committed.writes[1].kind == "anchor"
    assert committed.writes[1].source_id == "src"
    assert owner.pin_outcome == "written"


@pytest.mark.asyncio
async def test_on_first_content_without_a_response_id_commits_the_thread_pin_only(recorder: _Recorder) -> None:
    executor = _ResolvingExecutor()
    intent = PinIntent(
        writes=(PinWrite(pin_key="thread\nabc", kind="thread", source_id="src", api_key_id=None),),
        thread_key="abc",
        source_id="src",
        anchor=True,
    )
    owner = _owner(recorder, pin_intent=intent, pin_executor=executor)
    await owner.on_first_content(SourceUsageHolder())
    assert [write.pin_key for write in executor.intents[0].writes] == ["thread\nabc"]


@pytest.mark.asyncio
async def test_on_first_content_store_false_intent_never_anchors(recorder: _Recorder) -> None:
    """Mutant: anchor written for ``store: false`` (``anchor=False``)."""

    executor = _ResolvingExecutor()
    owner = _owner(recorder, pin_intent=_intent(), pin_executor=executor)
    await owner.on_first_content(SourceUsageHolder(response_id="resp_source_1"))
    assert [write.kind for write in executor.intents[0].writes] == ["thread"]


# -- breaker trial: first output item and the terminal classification (design §8.3) ---------------------------


@pytest.mark.asyncio
async def test_settlement_stream_settles_the_trial_at_the_first_output_item_before_the_stream_ends(
    recorder: _Recorder,
) -> None:
    """The first output item yielded closes a half-open breaker while the slot stays held until ``finish()``.

    Mutant (the trial settles only in the ``finally``): a recovered source streaming a long answer keeps the
    breaker half-open with the lease held, and every other overflow request is declined ``breaker_open`` until
    the stream ends or the 120 s lease expires.
    """

    trial = _FakeTrial()
    bulkhead = SourceBulkhead()
    owner = _owner(recorder, reservation=_reservation(limited=False), bulkhead=bulkhead, trial=trial)
    holder = SourceUsageHolder()
    _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        yield "event: response.created\ndata: {}\n\n"
        # The parser saw ``response.output_item.added`` and the body released it past the pin hook.
        holder.first_content_seen = True
        holder.first_output_item_seen = True
        yield "event: response.output_item.added\ndata: {}\n\n"
        yield "event: response.output_text.delta\ndata: {}\n\n"
        holder.terminal_kind = "completed"
        yield "event: response.completed\ndata: {}\n\n"

    chunks: list[str] = []
    async for chunk in settlement_stream(owner, inner()):
        chunks.append(chunk)
        if len(chunks) == 1:
            assert trial.events == [], "bookkeeping frames never conclude the trial"
        if len(chunks) == 2:
            # The item frame has been handed to the transport: the trial is concluded, the slot still held.
            assert trial.events == ["first_output_item"]
            assert bulkhead.in_flight(owner.source.id) == 1
            assert owner.claims.released is False

    assert len(chunks) == 4
    assert trial.events == ["first_output_item", "settle:success"]
    assert bulkhead.in_flight(owner.source.id) == 0 and owner.claims.released is True
    assert recorder.rows[0]["status"] == "success"


@pytest.mark.asyncio
async def test_settlement_stream_cancel_after_the_first_item_keeps_the_early_trial_conclusion(
    recorder: _Recorder,
) -> None:
    """A client cancel after any frame is never counted: the trial was concluded at the item, the terminal is
    ``inconclusive`` and the slot is released exactly once."""

    trial = _FakeTrial()
    owner = _owner(recorder, reservation=_reservation(limited=True), trial=trial)
    holder = SourceUsageHolder()
    _attach_stream(owner, holder=holder)
    gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def inner() -> AsyncIterator[str]:
        holder.first_content_seen = True
        holder.first_output_item_seen = True
        yield "event: response.output_item.added\ndata: {}\n\n"
        await gate
        yield "data: never\n\n"

    body = settlement_stream(owner, inner())

    async def consume() -> list[str]:
        return [chunk async for chunk in body]

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert trial.events == ["first_output_item"]
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert trial.events == ["first_output_item", "settle:inconclusive"]
    assert owner.claims.released is True
    assert recorder.rows[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_settlement_stream_pin_failure_never_concludes_the_trial(recorder: _Recorder) -> None:
    """The item frame is still withheld when the pin commit fails, so a pin failure is ``inconclusive`` (spec)."""

    trial = _FakeTrial()
    executor = _FakeExecutor("not_written")
    owner = _owner(
        recorder,
        reservation=_reservation(),
        pin_intent=_intent(),
        pin_executor=executor,
        trial=trial,
        pin_failure_error_code="pin_failed",
        pin_unverified_error_code="pin_unverified",
    )
    holder = SourceUsageHolder(
        first_content_seen=True, first_output_item_seen=True, created_envelope={"id": "resp_src", "object": "response"}
    )
    _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        await owner.on_first_content(holder)
        yield "data: never delivered\n\n"

    chunks = [chunk async for chunk in settlement_stream(owner, inner())]

    assert len(chunks) == 2
    assert trial.events == ["settle:inconclusive"]
    assert owner.claims.released is True


@pytest.mark.asyncio
async def test_settlement_stream_success_without_an_output_item_is_inconclusive(recorder: _Recorder) -> None:
    trial = _FakeTrial()
    owner = _owner(recorder, reservation=_reservation(limited=False), trial=trial)
    _attach_stream(owner, holder=SourceUsageHolder(terminal_kind="completed"))

    async def inner() -> AsyncIterator[str]:
        yield "event: response.created\ndata: {}\n\n"
        yield "event: response.completed\ndata: {}\n\n"

    _ = [chunk async for chunk in settlement_stream(owner, inner())]
    assert trial.events == ["settle:inconclusive"]


@pytest.mark.asyncio
async def test_first_output_item_observation_failure_never_breaks_the_stream(recorder: _Recorder, caplog) -> None:
    class _Exploding(_FakeTrial):
        def observe_first_output_item(self) -> None:
            raise RuntimeError("breaker gauge down")

    trial = _Exploding()
    owner = _owner(recorder, reservation=_reservation(limited=False), trial=trial)
    holder = SourceUsageHolder(first_content_seen=True, first_output_item_seen=True, terminal_kind="completed")
    _attach_stream(owner, holder=holder)
    caplog.set_level(logging.WARNING, logger="app.modules.proxy.source_dispatch")

    async def inner() -> AsyncIterator[str]:
        yield "event: response.output_item.added\ndata: {}\n\n"
        yield "event: response.completed\ndata: {}\n\n"

    chunks = [chunk async for chunk in settlement_stream(owner, inner())]

    assert len(chunks) == 2
    assert trial.events == ["settle:success"]
    assert "source_dispatch_first_output_item_failed" in caplog.text
    assert recorder.rows[0]["status"] == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raiser",
    [
        ModelSourceForwardingError(
            status_code=504,
            payload={"error": {"code": "model_source_idle_timeout", "message": "idle", "type": "upstream_error"}},
            timeout_phase="idle",
        ),
        ValueError("connection reset by peer"),
    ],
    ids=["idle-timeout", "transport-drop"],
)
async def test_settlement_stream_stream_break_after_the_first_item_is_a_counted_failure(
    recorder: _Recorder, raiser: Exception
) -> None:
    """Spec: after the first frame an idle timeout or a transport drop is counted. Mutant (``inconclusive`` once an
    item was seen): a source that emits one item and then stalls costs a full idle window per attempt and never
    opens the breaker; pinned conversations keep paying it."""

    trial = _FakeTrial()
    owner = _owner(recorder, reservation=_reservation(), trial=trial)
    holder = SourceUsageHolder(first_content_seen=True, first_output_item_seen=True)
    _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        yield "event: response.created\ndata: {}\n\n"
        yield "event: response.output_item.added\ndata: {}\n\n"
        raise raiser

    with pytest.raises(type(raiser)):
        _ = [chunk async for chunk in settlement_stream(owner, inner())]

    assert trial.events == ["first_output_item", "settle:failure"]
    assert recorder.rows[0]["status"] == "error"
    assert recorder.release_calls == [owner.reservation]


@pytest.mark.asyncio
async def test_settlement_stream_truncation_after_the_first_item_is_a_counted_failure(recorder: _Recorder) -> None:
    """A clean end of the stream without a terminal is a transport drop for the breaker: the client received the
    wrapper's synthesized ``response.failed``, the source did not finish its answer."""

    trial = _FakeTrial()
    owner = _owner(recorder, reservation=_reservation(), trial=trial)
    holder = SourceUsageHolder(first_content_seen=True, first_output_item_seen=True, delta_chars=400)
    _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        yield "event: response.created\ndata: {}\n\n"
        yield "event: response.output_item.added\ndata: {}\n\n"
        yield _SYNTHESIZED_TRUNCATION

    _ = [chunk async for chunk in settlement_stream(owner, inner())]

    assert recorder.rows[0]["error_code"] == "model_source_stream_truncated"
    assert trial.events == ["first_output_item", "settle:failure"]


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_kind", ["failed", "error"])
async def test_settlement_stream_failure_terminal_after_the_first_item_is_not_counted(
    recorder: _Recorder, terminal_kind: str
) -> None:
    """Spec: a failure terminal is counted before the first output item only (after it the answer was cut by the
    source's own terminal, not by the transport)."""

    trial = _FakeTrial()
    owner = _owner(recorder, reservation=_reservation(), trial=trial)
    holder = SourceUsageHolder(first_content_seen=True, first_output_item_seen=True)
    _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        yield "event: response.output_item.added\ndata: {}\n\n"
        holder.terminal_kind = cast(Any, terminal_kind)
        yield (
            f"event: response.{terminal_kind}\ndata: {{}}\n\n"
            if terminal_kind == "failed"
            else ('event: error\ndata: {"type":"error"}\n\n')
        )

    _ = [chunk async for chunk in settlement_stream(owner, inner())]

    assert recorder.rows[0]["error_code"] == "model_source_response_failed"
    assert trial.events == ["first_output_item", "settle:inconclusive"]


@pytest.mark.asyncio
async def test_settlement_stream_failure_terminal_before_the_first_item_is_counted(recorder: _Recorder) -> None:
    trial = _FakeTrial()
    owner = _owner(recorder, reservation=_reservation(), trial=trial)
    _attach_stream(owner, holder=SourceUsageHolder(terminal_kind="failed"))

    async def inner() -> AsyncIterator[str]:
        yield "event: response.created\ndata: {}\n\n"
        yield "event: response.failed\ndata: {}\n\n"

    _ = [chunk async for chunk in settlement_stream(owner, inner())]
    assert trial.events == ["settle:failure"]


@pytest.mark.parametrize(
    ("status", "error_code", "first_item", "forwarding", "expected"),
    [
        ("success", None, True, None, "success"),
        ("success", None, False, None, "inconclusive"),
        ("cancelled", "client_disconnected", True, None, "inconclusive"),
        ("cancelled", "client_disconnected", False, None, "inconclusive"),
        ("error", "model_source_stream_error", True, None, "failure"),
        ("error", "model_source_stream_truncated", True, None, "failure"),
        ("error", "model_source_response_failed", True, None, "inconclusive"),
        ("error", "model_source_response_failed", False, None, "failure"),
        ("error", "model_source_response_invalid", True, None, "inconclusive"),
        ("error", "model_source_response_invalid", False, None, "failure"),
        ("error", "model_source_idle_timeout", True, "idle", "failure"),
        ("error", "invalid_upstream_response", True, "none", "failure"),
    ],
)
def test_stream_trial_result_table(
    status: str, error_code: str | None, first_item: bool, forwarding: str | None, expected: str
) -> None:
    from app.modules.proxy.source_dispatch import stream_trial_result

    forwarding_error: ModelSourceForwardingError | None = None
    if forwarding is not None:
        forwarding_error = ModelSourceForwardingError(
            status_code=502,
            payload={"error": {"code": error_code, "message": "x", "type": "upstream_error"}},
            timeout_phase="idle" if forwarding == "idle" else None,
        )
    result = stream_trial_result(
        cast(Any, status),
        error_code=error_code,
        first_output_item_seen=first_item,
        forwarding_error=forwarding_error,
    )
    assert result == expected


# -- pin-commit outcome counter through the ``on_finished`` hook (spec: pin_commit_failed / _unverified) ---------


class _OutcomeCounter:
    def __init__(self) -> None:
        self.outcomes: list[tuple[str, str]] = []
        self._pending: tuple[str, str] | None = None

    def labels(self, **labels: str) -> _OutcomeCounter:
        self._pending = (labels["route"], labels["outcome"])
        return self

    def inc(self, amount: float = 1) -> None:
        assert self._pending is not None
        self.outcomes.append(self._pending)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected"), [("not_written", "pin_commit_failed"), ("unknown", "pin_commit_unverified")]
)
async def test_settlement_stream_pin_failure_records_the_pin_commit_outcome_once(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch, outcome: str, expected: str
) -> None:
    """Streaming path: ``on_first_content`` sets ``pin_outcome`` before the pin-failure ``finish()``, whose single
    ``on_finished`` call records the outcome under the decision's route exactly once."""

    from app.modules.proxy import overflow as overflow_module

    counter = _OutcomeCounter()
    monkeypatch.setattr(overflow_module, "subscription_overflow_total", counter)
    executor = _FakeExecutor(outcome)
    owner = _owner(
        recorder,
        reservation=_reservation(),
        pin_intent=_intent(),
        pin_executor=executor,
        on_finished=overflow_module.OverflowFinishedHook("codex_responses"),
    )
    holder = SourceUsageHolder(first_content_seen=True, created_envelope={"id": "resp_src", "object": "response"})
    _attach_stream(owner, holder=holder)

    async def inner() -> AsyncIterator[str]:
        await owner.on_first_content(holder)
        yield "data: never delivered\n\n"

    chunks = [chunk async for chunk in settlement_stream(owner, inner())]

    assert len(chunks) == 2
    assert counter.outcomes == [("codex_responses", expected)]
    await owner.finish(status="error", error_code="again")  # the latch: no second count
    assert counter.outcomes == [("codex_responses", expected)]


@pytest.mark.asyncio
async def test_non_stream_pin_failure_finish_records_the_pin_commit_outcome_once(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-streaming path (``_finish_non_stream_source_dispatch``): ``pin_outcome`` is set on the owner and the
    pin-failure ``finish()`` runs the same hook."""

    from app.modules.proxy import overflow as overflow_module

    counter = _OutcomeCounter()
    monkeypatch.setattr(overflow_module, "subscription_overflow_total", counter)
    hook = overflow_module.OverflowFinishedHook("v1_responses")
    owner = _owner(recorder, reservation=_reservation(), on_finished=hook)
    _attach_stream(owner)
    owner.pin_outcome = "unknown"

    await owner.finish(status="error", error_code=owner.pin_failure_row_code)

    assert counter.outcomes == [("v1_responses", "pin_commit_unverified")]
    assert recorder.release_calls == [owner.reservation]


@pytest.mark.asyncio
async def test_written_pin_records_no_pin_commit_outcome(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.proxy import overflow as overflow_module

    counter = _OutcomeCounter()
    monkeypatch.setattr(overflow_module, "subscription_overflow_total", counter)
    owner = _owner(
        recorder,
        pin_intent=_intent(),
        pin_executor=_FakeExecutor("written"),
        on_finished=overflow_module.OverflowFinishedHook("codex_responses"),
    )
    _attach_stream(owner, holder=SourceUsageHolder(terminal_kind="completed"))
    await owner.on_first_content(SourceUsageHolder())
    await owner.finish(status="success")
    assert counter.outcomes == []
