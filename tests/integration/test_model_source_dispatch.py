"""Owned model-source dispatch through the real relay (#2123 WP-C1, design v3 §6, §13.4).

A stub OpenAI-compatible upstream (``tests/integration/model_source_helpers``)
serves ``/v1/responses``; the proxy app is driven either through the httpx
client or, where the test must observe chunks live or disconnect mid-flight,
through a small ASGI harness that owns ``receive``/``send`` (httpx's ASGI
transport buffers the whole body before returning).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from sqlalchemy import select
from starlette.requests import Request

from app.db.models import ApiKeyUsageReservation, ModelSource, RequestLog
from app.db.session import SessionLocal
from app.modules.proxy import api as proxy_api
from app.modules.proxy import source_dispatch as dispatch_module
from app.modules.proxy.source_admission import get_source_bulkhead
from tests.integration.model_source_helpers import (
    _AsgiStream,
    _create_model_source,
    _enable_api_key_auth,
    stub_source_upstreams,
)

pytestmark = pytest.mark.integration

_UpstreamHandler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@pytest.fixture
async def source_upstream() -> AsyncIterator[Callable[..., Awaitable[str]]]:
    async with stub_source_upstreams() as start:
        yield start


# -- SSE frames ------------------------------------------------------------------------------


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def _created(response_id: str = "resp_dispatch_1") -> bytes:
    return _sse(
        {
            "type": "response.created",
            "sequence_number": 0,
            "response": {"id": response_id, "object": "response", "status": "in_progress", "output": []},
        }
    )


_ITEM_ADDED = _sse(
    {
        "type": "response.output_item.added",
        "sequence_number": 1,
        "output_index": 0,
        "item": {"id": "msg_1", "type": "message", "role": "assistant", "status": "in_progress", "content": []},
    }
)
_DELTA = _sse(
    {
        "type": "response.output_text.delta",
        "sequence_number": 2,
        "item_id": "msg_1",
        "output_index": 0,
        "content_index": 0,
        "delta": "hello from the source",
    }
)


def _completed(usage: dict[str, int] | None, response_id: str = "resp_dispatch_1") -> bytes:
    response: dict[str, Any] = {
        "id": response_id,
        "object": "response",
        "status": "completed",
        "output": [
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "hello from the source", "annotations": []}],
            }
        ],
    }
    if usage is not None:
        response["usage"] = usage
    return _sse({"type": "response.completed", "sequence_number": 3, "response": response})


_USAGE = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}


@dataclass(slots=True)
class _StubState:
    requests: list[dict[str, Any]] = field(default_factory=list)
    headers: list[dict[str, str]] = field(default_factory=list)
    cancelled: int = 0
    finished: int = 0


def _sse_handler(
    state: _StubState,
    *,
    before_hold: list[bytes],
    hold: asyncio.Event | None = None,
    after_hold: list[bytes] | None = None,
    delay_headers: asyncio.Event | None = None,
) -> _UpstreamHandler:
    async def handler(request: web.Request) -> web.StreamResponse:
        state.requests.append(await request.json())
        state.headers.append(dict(request.headers))
        try:
            if delay_headers is not None:
                await delay_headers.wait()
            response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            for frame in before_hold:
                await response.write(frame)
            if hold is not None:
                await hold.wait()
            for frame in after_hold or []:
                await response.write(frame)
            await response.write_eof()
            state.finished += 1
            return response
        except asyncio.CancelledError:
            state.cancelled += 1
            raise

    return handler


# -- ASGI harness ---------------------------------------------------------------------------------


def _app(async_client: Any) -> Any:
    return async_client._transport.app


async def _drain(async_client: Any) -> None:
    service = getattr(_app(async_client).state, "proxy_service", None)
    if service is not None and hasattr(service, "drain_persistence_tasks"):
        await service.drain_persistence_tasks(timeout_seconds=5)


# -- database views ----------------------------------------------------------------------------------


async def _source_rows(source_id: str) -> list[RequestLog]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(RequestLog).where(RequestLog.model_source_id == source_id).order_by(RequestLog.id)
        )
        return list(result.scalars().all())


async def _reservations(api_key_id: str) -> list[ApiKeyUsageReservation]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(ApiKeyUsageReservation)
            .where(ApiKeyUsageReservation.api_key_id == api_key_id)
            .order_by(ApiKeyUsageReservation.created_at)
        )
        return list(result.scalars().all())


async def _create_limited_key(async_client: Any, source_id: str, *, name: str) -> tuple[str, str]:
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": name,
            "assignedSourceIds": [source_id],
            "limits": [{"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 100_000}],
        },
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    return payload["key"], payload["id"]


async def _create_unlimited_key(async_client: Any, source_id: str, *, name: str) -> tuple[str, str]:
    created = await async_client.post(
        "/api/api-keys/",
        json={"name": name, "assignedSourceIds": [source_id]},
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    return payload["key"], payload["id"]


def _request_body(model: str, **extra: Any) -> dict[str, Any]:
    return {
        "model": model,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
        **extra,
    }


# -- live streaming and settlement --------------------------------------------------------------


@pytest.mark.asyncio
async def test_limited_key_streams_live_and_settles_from_source_usage(async_client, source_upstream) -> None:
    await _enable_api_key_auth(async_client)
    state = _StubState()
    hold = asyncio.Event()
    base_url = await source_upstream(
        _sse_handler(state, before_hold=[_created(), _ITEM_ADDED, _DELTA], hold=hold, after_hold=[_completed(_USAGE)])
    )
    model = "dispatch-live-limited"
    source_id = await _create_model_source(
        async_client,
        name=model,
        model=model,
        base_url=base_url,
        supports_responses=True,
        input_per_1m=3.0,
        output_per_1m=6.0,
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    stream = _AsgiStream(
        app=_app(async_client),
        path="/v1/responses",
        headers={"authorization": f"Bearer {key}", "session_id": "sess-dispatch-1"},
        body=json.dumps(
            _request_body(
                model,
                client_metadata={"harness": "codex"},
                stream_options={"include_obfuscation": False, "reasoning_summary_delivery": "interleaved"},
                prompt_cache_key="thread-cache-1",
            )
        ).encode(),
    )
    runner = asyncio.create_task(stream.run())
    # Live: the first output item reaches the client before the source's terminal frame exists.
    await stream.wait_for_text("response.output_item.added")
    assert not hold.is_set()
    hold.set()
    await asyncio.wait_for(runner, timeout=10)
    await _drain(async_client)

    assert stream.status == 200
    text = stream.received().decode()
    assert "response.created" in text and "response.completed" in text
    assert text.index("response.output_text.delta") < text.index("response.completed")

    forwarded = state.requests[0]
    # Telemetry stripped at the stub: the Codex object whole, the Codex key out of
    # the standard ``stream_options`` object (decision 52).
    assert "client_metadata" not in forwarded
    assert forwarded["stream_options"] == {"include_obfuscation": False}
    assert forwarded["prompt_cache_key"] == "thread-cache-1"
    assert forwarded["stream"] is True

    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["finalized"]
    assert reservations[0].input_tokens == 100 and reservations[0].output_tokens == 20
    assert reservations[0].cost_microdollars == 420

    rows = await _source_rows(source_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "success"
    assert row.source == "model_source"
    assert row.account_id is None
    assert row.api_key_id == key_id
    assert row.model_source_kind == "openai_compatible"
    assert row.request_id == "resp_dispatch_1"
    assert row.archive_request_id is not None and row.archive_request_id != row.request_id
    assert row.session_id == "sess-dispatch-1"
    assert row.input_tokens == 100 and row.output_tokens == 20
    assert row.cost_usd == pytest.approx(0.00042)
    assert row.transport == "http" and row.upstream_transport == "openai_compatible_http"
    assert row.service_tier is None
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
async def test_limited_key_missing_usage_settles_at_the_estimate(
    async_client, source_upstream, caplog: pytest.LogCaptureFixture
) -> None:
    await _enable_api_key_auth(async_client)
    state = _StubState()
    base_url = await source_upstream(
        _sse_handler(state, before_hold=[_created(), _ITEM_ADDED, _DELTA, _completed(None)])
    )
    model = "dispatch-missing-usage"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    with caplog.at_level(logging.WARNING, logger="app.modules.proxy.source_dispatch"):
        async with async_client.stream(
            "POST", "/v1/responses", headers={"Authorization": f"Bearer {key}"}, json=_request_body(model)
        ) as response:
            assert response.status_code == 200
            text = "".join([chunk async for chunk in response.aiter_text()])
    assert "response.completed" in text

    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["finalized"]
    assert reservations[0].input_tokens is not None and reservations[0].input_tokens > 0
    assert reservations[0].output_tokens == 2_048
    assert any("source_usage_missing_settled_at_estimate" in record.getMessage() for record in caplog.records)
    rows = await _source_rows(source_id)
    assert [row.status for row in rows] == ["success"]
    assert rows[0].input_tokens is None and rows[0].output_tokens is None


@pytest.mark.asyncio
async def test_limited_key_cancel_after_the_first_output_item_settles_at_the_estimate(
    async_client, source_upstream
) -> None:
    await _enable_api_key_auth(async_client)
    state = _StubState()
    hold = asyncio.Event()
    base_url = await source_upstream(
        _sse_handler(state, before_hold=[_created(), _ITEM_ADDED, _DELTA], hold=hold, after_hold=[_completed(_USAGE)]),
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    model = "dispatch-cancel-after-item"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    stream = _AsgiStream(
        app=_app(async_client),
        path="/v1/responses",
        headers={"authorization": f"Bearer {key}"},
        body=json.dumps(_request_body(model)).encode(),
    )
    runner = asyncio.create_task(stream.run())
    await stream.wait_for_text("response.output_text.delta")
    stream.disconnect()
    await asyncio.wait_for(runner, timeout=10)
    await _drain(async_client)
    hold.set()

    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["finalized"]
    assert reservations[0].output_tokens == 2_048
    rows = await _source_rows(source_id)
    assert [row.status for row in rows] == ["cancelled"]
    assert rows[0].error_code == "client_disconnected"
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
async def test_limited_key_cancel_after_delivered_deltas_without_an_output_item_settles_at_the_estimate(
    async_client, source_upstream
) -> None:
    """A source that streams ``*.delta`` frames without ``response.output_item.added`` delivered content; a client
    that leaves after receiving it owes the cancel estimate exactly like one that saw an output item."""

    await _enable_api_key_auth(async_client)
    state = _StubState()
    hold = asyncio.Event()
    base_url = await source_upstream(
        _sse_handler(state, before_hold=[_created(), _DELTA], hold=hold, after_hold=[_completed(_USAGE)]),
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    model = "dispatch-cancel-after-delta-only"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    stream = _AsgiStream(
        app=_app(async_client),
        path="/v1/responses",
        headers={"authorization": f"Bearer {key}"},
        body=json.dumps(_request_body(model)).encode(),
    )
    runner = asyncio.create_task(stream.run())
    await stream.wait_for_text("response.output_text.delta")
    assert "response.output_item.added" not in stream.received().decode()
    stream.disconnect()
    await asyncio.wait_for(runner, timeout=10)
    await _drain(async_client)
    hold.set()

    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["finalized"]
    assert reservations[0].output_tokens == 2_048
    rows = await _source_rows(source_id)
    assert [(row.status, row.error_code) for row in rows] == [("cancelled", "client_disconnected")]
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
async def test_limited_key_cancel_after_a_vendor_event_the_public_contract_dropped_releases(
    async_client, source_upstream
) -> None:
    """The source emits a vendor event outside ``response.*`` (the frame parser classifies any unknown type as
    content and the stream body flushes it), but the public SDK contract drops it: the wire carried only
    ``response.created`` and ``response.in_progress``, so a client that leaves owes nothing."""

    await _enable_api_key_auth(async_client)
    state = _StubState()
    hold = asyncio.Event()
    vendor_event = _sse({"type": "codex.rate_limits", "rate_limits": {"primary": {"used_percent": 12}}})
    in_progress = _sse(
        {
            "type": "response.in_progress",
            "sequence_number": 1,
            "response": {"id": "resp_dispatch_1", "object": "response", "status": "in_progress", "output": []},
        }
    )
    base_url = await source_upstream(
        _sse_handler(
            state,
            before_hold=[_created(), vendor_event, in_progress],
            hold=hold,
            after_hold=[_ITEM_ADDED, _completed(_USAGE)],
        ),
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    model = "dispatch-cancel-after-dropped-vendor-event"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    stream = _AsgiStream(
        app=_app(async_client),
        path="/v1/responses",
        headers={"authorization": f"Bearer {key}"},
        body=json.dumps(_request_body(model)).encode(),
    )
    runner = asyncio.create_task(stream.run())
    await stream.wait_for_text("response.in_progress")
    received = stream.received().decode()
    assert "response.created" in received
    assert "codex.rate_limits" not in received
    assert "response.output_item.added" not in received
    stream.disconnect()
    await asyncio.wait_for(runner, timeout=10)
    await _drain(async_client)
    hold.set()

    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["released"]
    rows = await _source_rows(source_id)
    assert [(row.status, row.error_code) for row in rows] == [("cancelled", "client_disconnected")]
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
async def test_limited_key_cancel_while_content_is_parked_ahead_of_response_created_releases(
    async_client, source_upstream
) -> None:
    """A ``response.output_text.delta`` that arrives before any ``response.created`` is parked in the public
    wrapper's pre-created buffer (the stream body already flushed it, so its flush-point flag is set); the SSE
    comment the source sends next is relayed, so the client received no event frame at all when it leaves."""

    await _enable_api_key_auth(async_client)
    state = _StubState()
    hold = asyncio.Event()
    base_url = await source_upstream(
        _sse_handler(
            state,
            before_hold=[_DELTA, b": parked\n\n"],
            hold=hold,
            after_hold=[_created(), _completed(_USAGE)],
        ),
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    model = "dispatch-cancel-parked-pre-created-delta"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    stream = _AsgiStream(
        app=_app(async_client),
        path="/v1/responses",
        headers={"authorization": f"Bearer {key}"},
        body=json.dumps(_request_body(model)).encode(),
    )
    runner = asyncio.create_task(stream.run())
    await stream.wait_for_text(": parked")
    received = stream.received()
    assert b"event:" not in received and b"data:" not in received, received
    stream.disconnect()
    await asyncio.wait_for(runner, timeout=10)
    await _drain(async_client)
    hold.set()

    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["released"]
    rows = await _source_rows(source_id)
    assert [(row.status, row.error_code) for row in rows] == [("cancelled", "client_disconnected")]
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
async def test_limited_key_disconnect_after_the_relayed_success_terminal_is_a_success(
    async_client, source_upstream
) -> None:
    """Codex tears the stream down as soon as ``response.completed`` arrives while the source is still open: the
    client received the whole answer, so the row is a success settled from the terminal's usage, not a cancel."""

    await _enable_api_key_auth(async_client)
    state = _StubState()
    hold = asyncio.Event()
    base_url = await source_upstream(
        _sse_handler(state, before_hold=[_created(), _ITEM_ADDED, _DELTA, _completed(_USAGE)], hold=hold),
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    model = "dispatch-disconnect-after-completed"
    source_id = await _create_model_source(
        async_client,
        name=model,
        model=model,
        base_url=base_url,
        supports_responses=True,
        input_per_1m=3.0,
        output_per_1m=6.0,
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    stream = _AsgiStream(
        app=_app(async_client),
        path="/v1/responses",
        headers={"authorization": f"Bearer {key}"},
        body=json.dumps(_request_body(model)).encode(),
    )
    runner = asyncio.create_task(stream.run())
    await stream.wait_for_text("response.completed")
    assert not hold.is_set()
    stream.disconnect()
    await asyncio.wait_for(runner, timeout=10)
    await _drain(async_client)
    hold.set()

    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["finalized"]
    assert reservations[0].input_tokens == 100 and reservations[0].output_tokens == 20
    assert reservations[0].cost_microdollars == 420
    rows = await _source_rows(source_id)
    assert [(row.status, row.error_code) for row in rows] == [("success", None)]
    assert rows[0].input_tokens == 100 and rows[0].output_tokens == 20
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
async def test_limited_key_cancel_before_the_first_output_item_releases(async_client, source_upstream) -> None:
    await _enable_api_key_auth(async_client)
    state = _StubState()
    hold = asyncio.Event()
    base_url = await source_upstream(
        _sse_handler(state, before_hold=[_created()], hold=hold, after_hold=[_ITEM_ADDED, _completed(_USAGE)]),
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    model = "dispatch-cancel-before-item"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    stream = _AsgiStream(
        app=_app(async_client),
        path="/v1/responses",
        headers={"authorization": f"Bearer {key}"},
        body=json.dumps(_request_body(model)).encode(),
    )
    runner = asyncio.create_task(stream.run())
    await stream.wait_for_text("response.created")
    stream.disconnect()
    await asyncio.wait_for(runner, timeout=10)
    await _drain(async_client)
    hold.set()

    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["released"]
    rows = await _source_rows(source_id)
    assert [row.status for row in rows] == ["cancelled"]
    assert get_source_bulkhead().in_flight(source_id) == 0


def _cr_framed(payload: dict[str, Any]) -> bytes:
    """A typed event block framed with bare CR line endings (legal SSE; LF and CRLF are what known servers emit)."""

    return f"event: {payload['type']}\rdata: {json.dumps(payload)}\r\r".encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
async def test_limited_key_cancel_after_bare_cr_framed_bookkeeping_releases(
    async_client, source_upstream, path: str
) -> None:
    """The settlement layer reads a relayed frame's ``event:`` line with the SSE boundary set (CR, LF or CRLF): a
    bare-CR framed ``response.created`` / ``response.in_progress`` is bookkeeping, not an unknown content-bearing
    type, so a client that leaves after them owes nothing."""

    await _enable_api_key_auth(async_client)
    state = _StubState()
    hold = asyncio.Event()
    lifecycle = {"id": "resp_dispatch_1", "object": "response", "status": "in_progress", "output": []}
    created = {"type": "response.created", "sequence_number": 0, "response": lifecycle}
    in_progress = {"type": "response.in_progress", "sequence_number": 1, "response": lifecycle}
    base_url = await source_upstream(
        _sse_handler(
            state,
            before_hold=[_cr_framed(created), _cr_framed(in_progress)],
            hold=hold,
            after_hold=[_ITEM_ADDED, _completed(_USAGE)],
        ),
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    model = f"dispatch-cancel-after-cr-framed-{path.split('/')[1]}"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    stream = _AsgiStream(
        app=_app(async_client),
        path=path,
        headers={"authorization": f"Bearer {key}", "originator": "codex_cli_rs"},
        body=json.dumps({"model": model, "instructions": "hi", "input": [], "stream": True}).encode(),
    )
    runner = asyncio.create_task(stream.run())
    await stream.wait_for_text("response.in_progress")
    assert "response.output_item.added" not in stream.received().decode()
    stream.disconnect()
    await asyncio.wait_for(runner, timeout=10)
    await _drain(async_client)
    hold.set()

    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["released"]
    rows = await _source_rows(source_id)
    assert [(row.status, row.error_code) for row in rows] == [("cancelled", "client_disconnected")]
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
async def test_non_stream_without_usage_releases_and_answers_502(async_client, source_upstream) -> None:
    await _enable_api_key_auth(async_client)
    state = _StubState()

    async def responses(request: web.Request) -> web.Response:
        state.requests.append(await request.json())
        return web.json_response(
            {"id": "resp_json_no_usage", "object": "response", "status": "completed", "output": []}
        )

    base_url = await source_upstream(responses)
    model = "dispatch-non-stream-no-usage"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    response = await async_client.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={**_request_body(model), "stream": False},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "usage_unavailable"
    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["released"]
    rows = await _source_rows(source_id)
    assert [(row.status, row.error_code) for row in rows] == [("error", "usage_unavailable")]
    assert rows[0].request_id == "resp_json_no_usage"


@pytest.mark.asyncio
async def test_non_stream_success_settles_and_attributes_the_source_response_id(async_client, source_upstream) -> None:
    await _enable_api_key_auth(async_client)

    async def responses(request: web.Request) -> web.Response:
        await request.json()
        return web.json_response(
            {"id": "resp_json_ok", "object": "response", "status": "completed", "output": [], "usage": _USAGE}
        )

    base_url = await source_upstream(responses)
    model = "dispatch-non-stream-ok"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    response = await async_client.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {key}", "session_id": "sess-json"},
        json={**_request_body(model), "stream": False, "service_tier": "priority"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "resp_json_ok"
    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["finalized"]
    rows = await _source_rows(source_id)
    assert len(rows) == 1
    assert rows[0].status == "success"
    assert rows[0].request_id == "resp_json_ok"
    assert rows[0].session_id == "sess-json"
    assert rows[0].requested_service_tier == "priority"
    assert rows[0].service_tier is None
    assert rows[0].input_tokens == 100


# -- abandonment (design §6.4, §13.4 a-e) --------------------------------------------------------


@pytest.mark.asyncio
async def test_client_leaving_during_delayed_headers_abandons_the_open(async_client, source_upstream) -> None:
    """(a) the open is cancelled within one poll, the slot and reservation are released, a cancelled row is written."""

    await _enable_api_key_auth(async_client)
    state = _StubState()
    delay_headers = asyncio.Event()
    base_url = await source_upstream(
        _sse_handler(state, before_hold=[_created(), _completed(_USAGE)], delay_headers=delay_headers),
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    model = "dispatch-abandon-open"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    stream = _AsgiStream(
        app=_app(async_client),
        path="/v1/responses",
        headers={"authorization": f"Bearer {key}"},
        body=json.dumps(_request_body(model)).encode(),
    )
    runner = asyncio.create_task(stream.run())
    deadline = time.monotonic() + 5
    while not state.requests and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert state.requests, "the stub never received the open"
    left_at = time.monotonic()
    stream.disconnect()
    await asyncio.wait_for(runner, timeout=10)
    abandoned_after = time.monotonic() - left_at
    await _drain(async_client)
    delay_headers.set()

    assert abandoned_after < 2.0
    assert stream.chunks == []
    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["released"]
    rows = await _source_rows(source_id)
    assert [(row.status, row.error_code) for row in rows] == [("cancelled", "client_disconnected_during_open")]
    assert get_source_bulkhead().in_flight(source_id) == 0
    deadline = time.monotonic() + 5
    while state.cancelled == 0 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert state.cancelled == 1, "the stub connection was not closed when the client left"


@pytest.mark.asyncio
async def test_disconnect_before_the_body_starts_finalizes_the_transport(async_client, source_upstream) -> None:
    """(b) an ASGI ``http.disconnect`` before Starlette iterates the body still reaches one ``finish()``."""

    await _enable_api_key_auth(async_client)
    state = _StubState()
    hold = asyncio.Event()
    base_url = await source_upstream(
        _sse_handler(state, before_hold=[_created()], hold=hold, after_hold=[_completed(_USAGE)]),
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    model = "dispatch-disconnect-before-body"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    stream = _AsgiStream(
        app=_app(async_client),
        path="/v1/responses",
        headers={"authorization": f"Bearer {key}"},
        body=json.dumps(_request_body(model)).encode(),
    )
    # The disconnect is already queued when the response object starts.
    stream.disconnect()
    await asyncio.wait_for(stream.run(), timeout=10)
    await _drain(async_client)
    hold.set()

    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["released"]
    rows = await _source_rows(source_id)
    assert len(rows) == 1
    assert rows[0].status == "cancelled"
    assert rows[0].error_code in {"client_disconnected_before_body", "client_disconnected"}
    assert get_source_bulkhead().in_flight(source_id) == 0
    deadline = time.monotonic() + 5
    while state.cancelled == 0 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert state.cancelled == 1, "the stub connection was not closed"


@pytest.mark.asyncio
async def test_client_leaving_after_the_stall_window_is_a_stall_abandonment(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(c) headers delayed past the evidence window, then the client leaves -> ``source_stall_abandoned``."""

    monkeypatch.setattr(dispatch_module, "STALL_EVIDENCE_SECONDS", 0.3)
    await _enable_api_key_auth(async_client)
    state = _StubState()
    delay_headers = asyncio.Event()
    base_url = await source_upstream(
        _sse_handler(state, before_hold=[_created(), _completed(_USAGE)], delay_headers=delay_headers),
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    model = "dispatch-stall"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    stream = _AsgiStream(
        app=_app(async_client),
        path="/v1/responses",
        headers={"authorization": f"Bearer {key}"},
        body=json.dumps(_request_body(model)).encode(),
    )
    runner = asyncio.create_task(stream.run())
    await asyncio.sleep(0.6)
    stream.disconnect()
    await asyncio.wait_for(runner, timeout=10)
    await _drain(async_client)
    delay_headers.set()

    rows = await _source_rows(source_id)
    assert [(row.status, row.error_code) for row in rows] == [("cancelled", "source_stall_abandoned")]
    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["released"]
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
async def test_cancellation_between_the_reservation_and_the_open_releases(
    async_client, source_upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(d) a ``CancelledError`` after the owner exists is abandoned as ``dispatch_interrupted``."""

    await _enable_api_key_auth(async_client)
    state = _StubState()
    base_url = await source_upstream(_sse_handler(state, before_hold=[_created(), _completed(_USAGE)]))
    model = "dispatch-interrupted"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    def interrupt(*args: object, **kwargs: object) -> dict[str, Any]:
        raise asyncio.CancelledError

    monkeypatch.setattr(proxy_api, "_shape_source_responses_payload", interrupt)
    stream = _AsgiStream(
        app=_app(async_client),
        path="/v1/responses",
        headers={"authorization": f"Bearer {key}"},
        body=json.dumps(_request_body(model)).encode(),
    )
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(stream.run(), timeout=10)
    await _drain(async_client)

    assert state.requests == []
    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["released"]
    rows = await _source_rows(source_id)
    assert [(row.status, row.error_code) for row in rows] == [("cancelled", "dispatch_interrupted")]
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
async def test_client_leaving_after_the_heartbeat_releases_everything(async_client, source_upstream) -> None:
    """(e) an SDK client on the Codex route leaves after the initial heartbeat, before content: everything is released.

    The heartbeat is prepended for non-native clients of ``/backend-api/codex/responses`` (native Codex keeps the
    verbatim lifecycle without proxy keepalives), so this exercises the layer that owns nothing being closed first.
    """

    await _enable_api_key_auth(async_client)
    state = _StubState()
    hold = asyncio.Event()
    base_url = await source_upstream(
        _sse_handler(state, before_hold=[_created()], hold=hold, after_hold=[_ITEM_ADDED, _completed(_USAGE)]),
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    model = "dispatch-native-heartbeat"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    stream = _AsgiStream(
        app=_app(async_client),
        path="/backend-api/codex/responses",
        headers={"authorization": f"Bearer {key}", "user-agent": "python-httpx/0.28"},
        body=json.dumps({"model": model, "instructions": "hi", "input": [], "stream": True}).encode(),
    )
    runner = asyncio.create_task(stream.run())
    await stream.wait_for_text("codex.keepalive")
    stream.disconnect()
    await asyncio.wait_for(runner, timeout=10)
    await _drain(async_client)
    hold.set()

    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["released"]
    rows = await _source_rows(source_id)
    assert [row.status for row in rows] == ["cancelled"]
    assert get_source_bulkhead().in_flight(source_id) == 0


# -- bulkhead ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_concurrent_request_over_max_concurrency_is_busy(async_client, source_upstream) -> None:
    await _enable_api_key_auth(async_client)
    state = _StubState()
    hold = asyncio.Event()
    base_url = await source_upstream(
        _sse_handler(state, before_hold=[_created()], hold=hold, after_hold=[_completed(_USAGE)]),
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    model = "dispatch-bulkhead"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    patched = await async_client.patch(f"/api/model-sources/{source_id}", json={"maxConcurrency": 1})
    assert patched.status_code == 200, patched.text
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    first = _AsgiStream(
        app=_app(async_client),
        path="/v1/responses",
        headers={"authorization": f"Bearer {key}"},
        body=json.dumps(_request_body(model)).encode(),
    )
    first_runner = asyncio.create_task(first.run())
    await first.wait_for_text("response.created")
    assert get_source_bulkhead().in_flight(source_id) == 1

    second = await async_client.post(
        "/v1/responses", headers={"Authorization": f"Bearer {key}"}, json=_request_body(model)
    )
    assert second.status_code == 503
    assert second.json()["error"]["code"] == "model_source_busy"
    assert second.headers.get("retry-after") == "1"
    # The loser reserved nothing: only the winner's reservation exists.
    assert len(await _reservations(key_id)) == 1
    assert len(state.requests) == 1

    hold.set()
    await asyncio.wait_for(first_runner, timeout=10)
    await _drain(async_client)
    assert get_source_bulkhead().in_flight(source_id) == 0
    rows = await _source_rows(source_id)
    assert [row.status for row in rows] == ["success"]
    third = await async_client.post(
        "/v1/responses", headers={"Authorization": f"Bearer {key}"}, json=_request_body(model)
    )
    assert third.status_code == 200


@pytest.mark.asyncio
async def test_admission_estimate_failure_after_the_claim_releases_the_bulkhead_slot(
    async_client, source_upstream
) -> None:
    """I13: a budget estimate that raises after the slot was claimed must leave nothing owned.

    A lone surrogate inside the first ~8 KiB of the body survives JSON parsing
    and request validation but makes the budget serializer raise
    ``UnicodeEncodeError``. The request is a 500 either way; the slot, the
    reservation and the row must not exist afterwards, or a ``max_concurrency``
    source answers ``503 model_source_busy`` forever on this worker.
    """

    await _enable_api_key_auth(async_client)
    state = _StubState()
    base_url = await source_upstream(
        _sse_handler(state, before_hold=[_created(), _ITEM_ADDED, _DELTA, _completed(_USAGE)])
    )
    model = "dispatch-estimate-raises"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    patched = await async_client.patch(f"/api/model-sources/{source_id}", json={"maxConcurrency": 1})
    assert patched.status_code == 200, patched.text
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    body = _request_body(model)
    body["input"] = [{"role": "user", "content": [{"type": "input_text", "text": "\ud800"}]}]
    # ``ensure_ascii`` keeps the surrogate as the six-character JSON escape, so
    # the bytes are valid JSON and the client-side encode cannot be what raises.
    raw = json.dumps(body).encode("ascii")
    try:
        response = await async_client.post(
            "/v1/responses",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            content=raw,
        )
    except UnicodeEncodeError:
        # httpx's ASGI transport re-raises the app exception after the 500 was sent.
        pass
    else:
        assert response.status_code == 500, response.text
    await _drain(async_client)

    assert get_source_bulkhead().in_flight(source_id) == 0
    assert await _reservations(key_id) == []
    assert await _source_rows(source_id) == []
    assert state.requests == []

    follow_up = await async_client.post(
        "/v1/responses", headers={"Authorization": f"Bearer {key}"}, json=_request_body(model)
    )
    assert follow_up.status_code == 200, follow_up.text
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
async def test_admission_estimate_exception_is_covered_by_the_route_helper_latch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct call: whatever the estimate raises, the claim is released before the exception leaves the helper."""

    source = ModelSource(
        id="src_estimate_raises",
        name="estimate-raises",
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=False,
        supports_responses=True,
        max_concurrency=1,
    )

    def exploding_estimate(_payload: object) -> object:
        raise RuntimeError("estimate exploded")

    async def never_opened(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the source must not be opened when admission fails")

    monkeypatch.setattr(proxy_api, "estimate_api_key_request_usage", exploding_estimate)
    monkeypatch.setattr(proxy_api, "stream_source_responses", never_opened)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/responses",
            "headers": [],
            "client": ("203.0.113.9", 54321),
        }
    )
    payload = proxy_api.ResponsesRequest.model_validate(
        {"model": "src-model", "instructions": "hi", "input": [], "stream": True}
    )

    with pytest.raises(RuntimeError, match="estimate exploded"):
        await proxy_api._source_responses_response(
            request,
            payload,
            source=source,
            api_key=None,
            rate_limit_headers={},
            pre_normalization_effort=None,
        )

    assert get_source_bulkhead().in_flight(source.id) == 0
    # The next claim for the same source succeeds: nothing stayed owned.
    claims = proxy_api.try_claim_source_admission(source)
    assert claims is not None
    claims.release_if_unowned()


# -- honest source errors ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_429_passes_through_with_its_retry_after_and_releases(async_client, source_upstream) -> None:
    await _enable_api_key_auth(async_client)

    async def rate_limited(request: web.Request) -> web.Response:
        await request.json()
        return web.json_response(
            {"error": {"message": "slow down", "type": "rate_limit_error", "code": "rate_limit_exceeded"}},
            status=429,
            headers={"Retry-After": "7"},
        )

    base_url = await source_upstream(rate_limited)
    model = "dispatch-source-429"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    response = await async_client.post(
        "/v1/responses", headers={"Authorization": f"Bearer {key}"}, json=_request_body(model)
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limit_exceeded"
    assert response.headers.get("retry-after") == "7"
    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["released"]
    rows = await _source_rows(source_id)
    assert [(row.status, row.upstream_status_code) for row in rows] == [("error", 429)]
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
async def test_source_failure_terminal_releases_the_limited_key(async_client, source_upstream) -> None:
    """A source that answers ``response.failed`` without usage produced no answer: release, never estimate."""

    await _enable_api_key_auth(async_client)
    state = _StubState()
    failed = _sse(
        {
            "type": "response.failed",
            "sequence_number": 1,
            "response": {
                "id": "resp_dispatch_failed",
                "object": "response",
                "status": "failed",
                "error": {"code": "server_error", "message": "upstream exploded"},
            },
        }
    )
    base_url = await source_upstream(_sse_handler(state, before_hold=[_created("resp_dispatch_failed"), failed]))
    model = "dispatch-failure-terminal"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    async with async_client.stream(
        "POST", "/v1/responses", headers={"Authorization": f"Bearer {key}"}, json=_request_body(model)
    ) as response:
        assert response.status_code == 200
        text = "".join([chunk async for chunk in response.aiter_text()])

    assert "response.failed" in text
    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["released"]
    rows = await _source_rows(source_id)
    assert [(row.status, row.error_code) for row in rows] == [("error", "model_source_response_failed")]
    assert rows[0].request_id == "resp_dispatch_failed"
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
async def test_typeless_error_record_is_a_failure_terminal_for_the_settlement(async_client, source_upstream) -> None:
    """A source that ends with a typeless ``{"error": {...}}`` record (no ``type`` field) produced no answer: the
    public wrapper classifies it as the ``error`` terminal and relays ``response.failed``; the settlement must record
    ``model_source_response_failed`` (not a truncated stream) and release the limited key."""

    await _enable_api_key_auth(async_client)
    state = _StubState()
    typeless_error = _sse({"error": {"message": "overloaded", "type": "server_error", "code": "overloaded"}})
    base_url = await source_upstream(_sse_handler(state, before_hold=[_created(), typeless_error]))
    model = "dispatch-typeless-error"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    async with async_client.stream(
        "POST", "/v1/responses", headers={"Authorization": f"Bearer {key}"}, json=_request_body(model)
    ) as response:
        assert response.status_code == 200
        text = "".join([chunk async for chunk in response.aiter_text()])

    assert "response.failed" in text and "overloaded" in text
    assert "upstream_stream_truncated" not in text
    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["released"]
    rows = await _source_rows(source_id)
    assert [(row.status, row.error_code) for row in rows] == [("error", "model_source_response_failed")]
    assert rows[0].input_tokens is None and rows[0].output_tokens is None
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
async def test_native_client_tearing_down_on_a_typeless_error_record_is_an_error_not_a_cancel(
    async_client, source_upstream
) -> None:
    """Native Codex receives the typeless ``{"error": {...}}`` record verbatim and tears the stream down on it while
    the source still holds the connection: the client received a failure, so the attempt is
    ``error model_source_response_failed`` and released -- never a ``cancelled`` row settled at the estimate."""

    await _enable_api_key_auth(async_client)
    state = _StubState()
    hold = asyncio.Event()
    typeless_error = _sse({"error": {"message": "overloaded", "type": "server_error", "code": "overloaded"}})
    base_url = await source_upstream(
        _sse_handler(state, before_hold=[_created(), _ITEM_ADDED, typeless_error], hold=hold),
        handler_cancellation=True,
        shutdown_timeout=1.0,
    )
    model = "dispatch-native-typeless-error"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    stream = _AsgiStream(
        app=_app(async_client),
        path="/backend-api/codex/responses",
        headers={"authorization": f"Bearer {key}", "originator": "codex_cli_rs"},
        body=json.dumps({"model": model, "instructions": "hi", "input": [], "stream": True}).encode(),
    )
    runner = asyncio.create_task(stream.run())
    await stream.wait_for_text('"overloaded"')
    stream.disconnect()
    await asyncio.wait_for(runner, timeout=10)
    await _drain(async_client)
    hold.set()

    assert stream.received().count(b'"error"') >= 1
    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["released"]
    rows = await _source_rows(source_id)
    assert [(row.status, row.error_code) for row in rows] == [("error", "model_source_response_failed")]
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
async def test_clean_eof_without_a_terminal_is_recorded_as_truncated_and_released(
    async_client, source_upstream
) -> None:
    """The source closes the body cleanly after content but before any terminal: the SDK client receives the
    synthesized ``response.failed upstream_stream_truncated``, so the row is an error and nothing is charged."""

    await _enable_api_key_auth(async_client)
    state = _StubState()
    base_url = await source_upstream(_sse_handler(state, before_hold=[_created(), _ITEM_ADDED, _DELTA]))
    model = "dispatch-clean-eof"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    async with async_client.stream(
        "POST", "/v1/responses", headers={"Authorization": f"Bearer {key}"}, json=_request_body(model)
    ) as response:
        assert response.status_code == 200
        text = "".join([chunk async for chunk in response.aiter_text()])

    assert "response.output_text.delta" in text
    assert "response.failed" in text and "upstream_stream_truncated" in text
    assert "response.completed" not in text
    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["released"]
    rows = await _source_rows(source_id)
    assert [(row.status, row.error_code) for row in rows] == [("error", "model_source_stream_truncated")]
    assert rows[0].input_tokens is None and rows[0].output_tokens is None
    assert rows[0].request_id == "resp_dispatch_1"
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
async def test_success_terminal_the_public_wrapper_rewrites_into_a_failure_is_an_error_and_released(
    async_client, source_upstream
) -> None:
    """The source ends with ``response.completed`` whose ``response`` is not an object: the public normalizer relays
    ``response.failed invalid_json`` to the client, so settlement must follow the wire (error, released), not the
    parser's ``completed`` observation (success settled at the estimate)."""

    await _enable_api_key_auth(async_client)
    state = _StubState()
    malformed_completed = _sse({"type": "response.completed", "sequence_number": 3, "response": None})
    base_url = await source_upstream(_sse_handler(state, before_hold=[_created(), _ITEM_ADDED, malformed_completed]))
    model = "dispatch-rewritten-terminal"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    async with async_client.stream(
        "POST", "/v1/responses", headers={"Authorization": f"Bearer {key}"}, json=_request_body(model)
    ) as response:
        assert response.status_code == 200
        text = "".join([chunk async for chunk in response.aiter_text()])

    assert "response.output_item.added" in text
    assert "response.failed" in text and "invalid_json" in text
    assert "response.completed" not in text
    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["released"]
    rows = await _source_rows(source_id)
    assert [(row.status, row.error_code) for row in rows] == [("error", "model_source_response_invalid")]
    assert rows[0].input_tokens is None and rows[0].output_tokens is None
    assert rows[0].request_id == "resp_dispatch_1"
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
async def test_typed_event_trailing_a_rewritten_terminal_keeps_the_error_and_the_release(
    async_client, source_upstream
) -> None:
    """The source keeps emitting a typed event after the ``response.completed`` the wrapper rewrote into
    ``response.failed invalid_json``; the client-visible terminal is latched, so the trailing frame does not turn the
    attempt into a ``success`` settled at the estimate."""

    await _enable_api_key_auth(async_client)
    state = _StubState()
    malformed_completed = _sse({"type": "response.completed", "sequence_number": 3, "response": None})
    trailing_delta = _sse(
        {
            "type": "response.output_text.delta",
            "sequence_number": 4,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "delta": "late",
        }
    )
    base_url = await source_upstream(
        _sse_handler(state, before_hold=[_created(), _ITEM_ADDED, malformed_completed, trailing_delta])
    )
    model = "dispatch-trailing-after-rewrite"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name=f"{model}-key")

    async with async_client.stream(
        "POST", "/v1/responses", headers={"Authorization": f"Bearer {key}"}, json=_request_body(model)
    ) as response:
        assert response.status_code == 200
        text = "".join([chunk async for chunk in response.aiter_text()])

    assert "response.failed" in text and "invalid_json" in text
    assert text.index("response.failed") < text.index('"late"')
    reservations = await _reservations(key_id)
    assert [reservation.status for reservation in reservations] == ["released"]
    rows = await _source_rows(source_id)
    assert [(row.status, row.error_code) for row in rows] == [("error", "model_source_response_invalid")]
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
async def test_unlimited_key_streams_live_without_a_settlement(async_client, source_upstream) -> None:
    await _enable_api_key_auth(async_client)
    state = _StubState()
    base_url = await source_upstream(
        _sse_handler(state, before_hold=[_created(), _ITEM_ADDED, _DELTA, _completed(None)])
    )
    model = "dispatch-unlimited"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_unlimited_key(async_client, source_id, name=f"{model}-key")

    async with async_client.stream(
        "POST", "/v1/responses", headers={"Authorization": f"Bearer {key}"}, json=_request_body(model)
    ) as response:
        assert response.status_code == 200
        text = "".join([chunk async for chunk in response.aiter_text()])
    assert "response.completed" in text
    reservations = await _reservations(key_id)
    assert all(reservation.status in {"released", "finalized"} for reservation in reservations)
    rows = await _source_rows(source_id)
    assert [row.status for row in rows] == ["success"]


# -- zero hot-path cost for subscription traffic --------------------------------------------------------------


def test_direct_routing_claims_only_inside_the_source_route_helper() -> None:
    """The bulkhead claim and the owner are reachable only from ``_source_responses_response`` (I9)."""

    module = ast.parse(Path(proxy_api.__file__).read_text(encoding="utf-8"))
    parents = {child: parent for parent in ast.walk(module) for child in ast.iter_child_nodes(parent)}

    def enclosing_function(node: ast.AST) -> str | None:
        current = parents.get(node)
        while current is not None:
            if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
                return current.name
            current = parents.get(current)
        return None

    claim_sites = [
        enclosing_function(node)
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"try_claim_source_admission", "SourceDispatch"}
    ]
    assert claim_sites, "the source route no longer claims through the bulkhead"
    assert set(claim_sites) == {"_source_responses_response"}
    assert "context: ProxyContext | None = None" in inspect.getsource(proxy_api._source_responses_response)
