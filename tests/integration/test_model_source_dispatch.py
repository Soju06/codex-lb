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

from app.db.models import ApiKeyUsageReservation, RequestLog
from app.db.session import SessionLocal
from app.modules.proxy import api as proxy_api
from app.modules.proxy import source_dispatch as dispatch_module
from app.modules.proxy.source_admission import get_source_bulkhead
from tests.integration.model_source_helpers import (
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


@dataclass(slots=True)
class _AsgiStream:
    """Drive the ASGI app for one request while owning ``receive``/``send``."""

    app: Any
    path: str
    headers: dict[str, str]
    body: bytes
    status: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    chunks: list[bytes] = field(default_factory=list)
    _disconnect: asyncio.Event = field(default_factory=asyncio.Event)
    _chunk_arrived: asyncio.Event = field(default_factory=asyncio.Event)
    _body_sent: bool = False

    def disconnect(self) -> None:
        self._disconnect.set()

    def received(self) -> bytes:
        return b"".join(self.chunks)

    async def wait_for_text(self, needle: str, *, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while needle.encode() not in self.received():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"{needle!r} not received; got {self.received()!r}")
            self._chunk_arrived.clear()
            try:
                await asyncio.wait_for(self._chunk_arrived.wait(), timeout=remaining)
            except TimeoutError:
                raise AssertionError(f"{needle!r} not received; got {self.received()!r}") from None

    async def _receive(self) -> dict[str, Any]:
        if not self._body_sent:
            self._body_sent = True
            return {"type": "http.request", "body": self.body, "more_body": False}
        await self._disconnect.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: dict[str, Any]) -> None:
        await asyncio.sleep(0)
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self.response_headers = {key.decode().lower(): value.decode() for key, value in message.get("headers", [])}
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                self.chunks.append(bytes(body))
                self._chunk_arrived.set()

    async def run(self) -> None:
        raw_headers = [(key.lower().encode(), value.encode()) for key, value in self.headers.items()]
        raw_headers.append((b"host", b"testserver"))
        raw_headers.append((b"content-type", b"application/json"))
        raw_headers.append((b"content-length", str(len(self.body)).encode()))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": self.path,
            "raw_path": self.path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": raw_headers,
            "client": ("127.0.0.1", 41000),
            "server": ("testserver", 80),
        }
        await self.app(scope, self._receive, self._send)


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
                stream_options={"include_obfuscation": False},
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
    assert "client_metadata" not in forwarded and "stream_options" not in forwarded
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
