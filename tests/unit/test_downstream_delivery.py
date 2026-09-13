"""``DeliveryTracedStreamingResponse``: one bounded outcome per Responses SSE stream.

Drives the response through a fake ASGI scope/receive/send (the same shape
``tests/unit/test_source_dispatch.py`` uses for ``SourceStreamingResponse``)
and asserts the outcome, the counter increment, the log line and — because the
trace is observational — that the forwarded messages are untouched.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Iterator
from typing import Any, cast

import pytest

from app.core.http_protocol import HTTP_DISCONNECTED_STATE
from app.core.metrics import prometheus
from app.core.utils.request_id import reset_request_id, set_request_id
from app.modules.proxy import downstream_delivery
from app.modules.proxy.downstream_delivery import (
    OUTCOME_CANCELLED_BEFORE_TERMINAL,
    OUTCOME_ENDED_WITHOUT_TERMINAL,
    OUTCOME_EXCEPTION_BEFORE_TERMINAL,
    OUTCOME_TERMINAL_AFTER_DISCONNECT,
    OUTCOME_TERMINAL_WRITTEN,
    OUTCOMES,
    DeliveryTracedStreamingResponse,
)

_CREATED = 'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_1"}}\n\n'
_DELTA = 'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"hi"}\n\n'
_COMPLETED = 'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'


class _FakeCounter:
    """Records ``labels(...).inc()`` calls; prometheus_client is an optional extra and absent in CI."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def labels(self, **labels: str) -> _FakeCounter:
        self.calls.append(labels)
        return self

    def inc(self) -> None:
        pass


@pytest.fixture(autouse=True)
def counter(monkeypatch: pytest.MonkeyPatch) -> _FakeCounter:
    fake = _FakeCounter()
    monkeypatch.setattr(downstream_delivery, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(downstream_delivery, "stream_terminal_delivery_total", fake)
    return fake


async def _iter(*chunks: str) -> AsyncIterator[str]:
    for chunk in chunks:
        await asyncio.sleep(0)
        yield chunk


async def _run(
    response: DeliveryTracedStreamingResponse,
    *,
    state: dict[str, object] | None = None,
    stamp_before_chunk: int | None = None,
) -> list[dict[str, object]]:
    """Run the response; optionally stamp the disconnect right before the n-th body chunk is sent."""
    sent: list[dict[str, object]] = []
    scope_state: dict[str, object] = {} if state is None else state
    body_sends = 0

    async def receive() -> dict[str, object]:
        await asyncio.Event().wait()  # the client never signals http.disconnect on its own
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        nonlocal body_sends
        if message["type"] == "http.response.body":
            body_sends += 1
            if stamp_before_chunk is not None and body_sends == stamp_before_chunk:
                scope_state[HTTP_DISCONNECTED_STATE] = "eof"
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": "POST",
        "path": "/",
        "state": scope_state,
    }
    await response(scope, cast(Any, receive), cast(Any, send))
    return sent


def _bodies(sent: list[dict[str, object]]) -> list[bytes]:
    return [cast(bytes, message["body"]) for message in sent if message["type"] == "http.response.body"]


@pytest.fixture
def request_id() -> Iterator[str]:
    token = set_request_id("req-trace-1")
    try:
        yield "req-trace-1"
    finally:
        reset_request_id(token)


async def test_native_passthrough_terminal_is_terminal_written(
    caplog: pytest.LogCaptureFixture, request_id: str, counter: _FakeCounter
) -> None:
    response = DeliveryTracedStreamingResponse(_iter(_CREATED, _DELTA, _COMPLETED), surface="responses")

    with caplog.at_level(logging.DEBUG, logger=downstream_delivery.__name__):
        sent = await _run(response)

    assert response.outcome == OUTCOME_TERMINAL_WRITTEN
    assert counter.calls == [{"surface": "responses", "outcome": OUTCOME_TERMINAL_WRITTEN}]
    # Forwarded bytes are untouched and the final more_body=False is still sent exactly once;
    # that empty message is not counted as a chunk.
    assert _bodies(sent) == [_CREATED.encode(), _DELTA.encode(), _COMPLETED.encode(), b""]
    assert [message["more_body"] for message in sent[1:]] == [True, True, True, False]
    record = next(record for record in caplog.records if "responses_stream_terminal_delivery" in record.getMessage())
    assert record.levelno == logging.DEBUG
    total_bytes = len(_CREATED + _DELTA + _COMPLETED)
    assert (
        record.getMessage() == f"responses_stream_terminal_delivery request_id={request_id} surface=responses "
        f"outcome=terminal_written terminal=response.completed chunks=3 bytes={total_bytes} exc=None"
    )


@pytest.mark.parametrize(
    ("frame", "terminal"),
    [
        (_COMPLETED, "response.completed"),
        ('event: response.failed\ndata: {"type":"response.failed"}\n\n', "response.failed"),
        ('event: response.incomplete\ndata: {"type":"response.incomplete"}\n\n', "response.incomplete"),
        ('event: error\ndata: {"type":"error","code":"x"}\n\n', "error"),
        ("event: response.completed\r\ndata: {}\r\n\r\n", "response.completed"),
    ],
)
async def test_every_terminal_event_type_is_recognised(frame: str, terminal: str) -> None:
    response = DeliveryTracedStreamingResponse(_iter(_CREATED, frame), surface="responses")
    await _run(response)
    assert response.outcome == OUTCOME_TERMINAL_WRITTEN
    assert response._terminal_type == terminal


async def test_terminal_frame_split_across_chunks_is_recognised() -> None:
    frame = _COMPLETED
    # Split inside the ``event:`` line and again inside the data line.
    parts = [frame[:12], frame[12:40], frame[40:]]
    response = DeliveryTracedStreamingResponse(_iter(_DELTA, *parts), surface="responses")
    await _run(response)
    assert response.outcome == OUTCOME_TERMINAL_WRITTEN
    assert response._terminal_type == "response.completed"


_EVENT_LINE, _REST_OF_BLOCK = _COMPLETED.split("\n", 1)
_CRLF_COMPLETED = "event: response.completed\r\ndata: {}\r\n\r\n"


class _CommitRecordingResponse(DeliveryTracedStreamingResponse):
    """Records ``_terminal_type`` as observed right after each non-empty body chunk's ``send`` returned."""

    def __init__(self, content: AsyncIterator[str], *, surface: str) -> None:
        super().__init__(content, surface=surface)
        self.committed_after: list[str | None] = []

    def _observe_body(self, body: bytes) -> bool:
        result = super()._observe_body(body)
        if body:
            self.committed_after.append(self._terminal_type)
        return result


@pytest.mark.parametrize(
    "parts",
    [
        # ``event:`` line alone, then ``data:`` + terminator.
        [_EVENT_LINE + "\n", _REST_OF_BLOCK],
        # ``event:`` line and ``data:`` line, blank-line terminator in its own chunk.
        [_EVENT_LINE + "\n", _REST_OF_BLOCK[:-1], "\n"],
        # LF terminator split byte by byte: ``...}\n`` | ``\n``.
        [_COMPLETED[:-1], _COMPLETED[-1:]],
        # CRLF terminator split ``\r\n\r`` | ``\n``.
        [_CRLF_COMPLETED[:-1], _CRLF_COMPLETED[-1:]],
        # CRLF terminator split ``\r\n`` | ``\r\n``.
        [_CRLF_COMPLETED[:-2], _CRLF_COMPLETED[-2:]],
        # Mixed framing: LF-terminated lines, CRLF blank line.
        [_EVENT_LINE + "\n", "data: {}\n", "\r\n"],
    ],
    ids=["event-then-rest", "terminator-alone", "lf-split", "crlf-split-3-1", "crlf-split-2-2", "mixed-lf-crlf"],
)
async def test_split_terminal_block_is_committed_only_by_the_chunk_carrying_its_terminator(parts: list[str]) -> None:
    response = _CommitRecordingResponse(_iter(_CREATED, *parts), surface="responses")
    await _run(response)
    assert response.outcome == OUTCOME_TERMINAL_WRITTEN
    # ``_CREATED`` plus every part but the last leave the terminal uncommitted.
    assert response.committed_after == [None] * len(parts) + ["response.completed"]


async def test_split_terminal_frame_disconnect_before_its_payload_is_terminal_after_disconnect(
    caplog: pytest.LogCaptureFixture, counter: _FakeCounter
) -> None:
    """The ``event:`` line reached the writer, the peer left, the ``data:`` + terminator chunk was dropped."""
    response = DeliveryTracedStreamingResponse(_iter(_CREATED, _EVENT_LINE + "\n", _REST_OF_BLOCK), surface="responses")
    with caplog.at_level(logging.DEBUG, logger=downstream_delivery.__name__):
        sent = await _run(response, stamp_before_chunk=3)  # stamped right before the payload chunk's send

    assert response.outcome == OUTCOME_TERMINAL_AFTER_DISCONNECT
    assert counter.calls == [{"surface": "responses", "outcome": OUTCOME_TERMINAL_AFTER_DISCONNECT}]
    assert _bodies(sent)[-3:] == [(_EVENT_LINE + "\n").encode(), _REST_OF_BLOCK.encode(), b""]
    record = next(record for record in caplog.records if "responses_stream_terminal_delivery" in record.getMessage())
    assert record.levelno == logging.WARNING
    assert "outcome=terminal_after_disconnect terminal=response.completed chunks=3" in record.getMessage()


async def test_split_terminal_frame_disconnect_before_its_terminator_is_terminal_after_disconnect() -> None:
    # ``event:`` and ``data:`` lines were written; only the blank line was dropped.
    response = DeliveryTracedStreamingResponse(_iter(_CREATED, _COMPLETED[:-1], "\n"), surface="responses")
    await _run(response, stamp_before_chunk=3)
    assert response.outcome == OUTCOME_TERMINAL_AFTER_DISCONNECT


async def test_split_terminal_frame_server_cancel_before_its_terminator_is_cancelled_before_terminal(
    caplog: pytest.LogCaptureFixture, counter: _FakeCounter
) -> None:
    event_line_sent = asyncio.Event()

    async def body() -> AsyncIterator[str]:
        yield _CREATED
        yield _EVENT_LINE + "\n"
        yield _REST_OF_BLOCK[:-1]  # ``data:`` line without the blank-line terminator
        event_line_sent.set()
        await asyncio.Event().wait()  # the terminator never comes
        yield "\n"  # pragma: no cover

    response = DeliveryTracedStreamingResponse(body(), surface="responses")
    with caplog.at_level(logging.DEBUG, logger=downstream_delivery.__name__):
        task = asyncio.create_task(_run(response))
        await event_line_sent.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert response.outcome == OUTCOME_CANCELLED_BEFORE_TERMINAL
    assert response._terminal_type is None
    assert response._pending_terminal == "response.completed"
    assert counter.calls == [{"surface": "responses", "outcome": OUTCOME_CANCELLED_BEFORE_TERMINAL}]
    record = next(record for record in caplog.records if "responses_stream_terminal_delivery" in record.getMessage())
    assert record.levelno == logging.INFO
    assert "outcome=cancelled_before_terminal terminal=None chunks=3" in record.getMessage()
    assert record.getMessage().endswith("exc=CancelledError")


async def test_split_terminal_frame_client_disconnect_before_its_terminator_is_cancelled_before_terminal() -> None:
    """Starlette's ASGI 2.3 path: ``http.disconnect`` lands between the ``event:`` line and the terminator."""
    event_line_sent = asyncio.Event()

    async def body() -> AsyncIterator[str]:
        yield _CREATED
        yield _EVENT_LINE + "\n"
        event_line_sent.set()
        await asyncio.Event().wait()
        yield _REST_OF_BLOCK  # pragma: no cover

    async def receive() -> dict[str, object]:
        await event_line_sent.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        pass

    response = DeliveryTracedStreamingResponse(body(), surface="responses")
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "method": "POST", "path": "/"}
    await response(scope, cast(Any, receive), cast(Any, send))
    assert response.outcome == OUTCOME_CANCELLED_BEFORE_TERMINAL
    assert response._terminal_type is None


async def test_stream_ending_inside_the_terminal_block_is_ended_without_terminal() -> None:
    """An SSE client discards an unterminated event at EOF, so it is not a delivered terminal."""
    response = DeliveryTracedStreamingResponse(_iter(_CREATED, _COMPLETED[:-1]), surface="responses")
    await _run(response)
    assert response.outcome == OUTCOME_ENDED_WITHOUT_TERMINAL
    assert response._pending_terminal == "response.completed"


async def test_blank_line_before_the_event_line_does_not_terminate_its_block() -> None:
    # The previous frame's ``\n\n`` sits in the rolling tail right before the
    # ``event:`` line; it must not be mistaken for the terminal block's end.
    response = _CommitRecordingResponse(_iter(_CREATED + _EVENT_LINE, "\n" + _REST_OF_BLOCK[:-1]), surface="responses")
    await _run(response)
    assert response.committed_after == [None, None]
    assert response.outcome == OUTCOME_ENDED_WITHOUT_TERMINAL


async def test_terminal_type_inside_data_payload_does_not_count() -> None:
    # The event type only counts on an ``event:`` line; a delta whose text
    # mentions it (or a ``data:`` echo of it) is not a terminal frame.
    lookalike = 'event: response.output_text.delta\ndata: {"delta":"\\nevent: response.completed"}\n\n'
    response = DeliveryTracedStreamingResponse(_iter(_CREATED, lookalike), surface="responses")
    await _run(response)
    assert response.outcome == OUTCOME_ENDED_WITHOUT_TERMINAL


@pytest.mark.parametrize(
    "parts",
    [
        # ``\\nevent: response.comp`` + ``leted"}`` inside a JSON string, split at the boundary.
        ['event: response.output_text.delta\ndata: {"delta":"\\nevent: response.comp', 'leted"}\n\n'],
        # An ``event:`` line that ends exactly at the chunk boundary is not a terminal until
        # its line ends; the continuation shows it never was one.
        ["event: response.completed", "_v2\ndata: {}\n\n"],
    ],
    ids=["json-string-split", "event-line-continues"],
)
async def test_look_alikes_split_across_chunks_do_not_count(parts: list[str]) -> None:
    response = DeliveryTracedStreamingResponse(_iter(_CREATED, *parts), surface="responses")
    await _run(response)
    assert response.outcome == OUTCOME_ENDED_WITHOUT_TERMINAL


class _CountingPattern:
    """Wraps the compiled terminal pattern and counts how it is used."""

    def __init__(self, pattern: re.Pattern[bytes]) -> None:
        self._pattern = pattern
        self.match_calls = 0
        self.search_calls = 0

    def match(self, *args: Any, **kwargs: Any) -> re.Match[bytes] | None:
        self.match_calls += 1
        return self._pattern.match(*args, **kwargs)

    def search(self, *args: Any, **kwargs: Any) -> re.Match[bytes] | None:
        self.search_calls += 1
        return self._pattern.search(*args, **kwargs)


async def test_large_non_terminal_frame_costs_one_anchored_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 512 KiB ``output_item.done`` frame must not be walked by the regex.

    The detector may anchor one ``match`` at offset 0 per chunk and fall back to
    a ``bytes.find`` for further line starts; it must never ``search`` a body.
    """
    counting = _CountingPattern(downstream_delivery._TERMINAL_LINE_RE)
    monkeypatch.setattr(downstream_delivery, "_TERMINAL_LINE_RE", counting)
    arguments = "x" * (512 * 1024)
    big_frame = (
        'event: response.output_item.done\ndata: {"type":"response.output_item.done","item":{"arguments":"'
        + arguments
        + '"}}\n\n'
    )
    response = DeliveryTracedStreamingResponse(_iter(_CREATED, big_frame, _COMPLETED), surface="responses")

    await _run(response)

    assert response.outcome == OUTCOME_TERMINAL_WRITTEN
    assert counting.search_calls == 0
    assert counting.match_calls == 3  # one anchored match per non-empty chunk, none inside the body


async def test_terminal_sent_after_disconnect_stamp(
    caplog: pytest.LogCaptureFixture, request_id: str, counter: _FakeCounter
) -> None:
    response = DeliveryTracedStreamingResponse(_iter(_CREATED, _DELTA, _COMPLETED), surface="responses")

    with caplog.at_level(logging.DEBUG, logger=downstream_delivery.__name__):
        # The protocol stamps the scope in connection_lost before any task resumes;
        # here the stamp lands right before the terminal chunk reaches send().
        sent = await _run(response, stamp_before_chunk=3)

    assert response.outcome == OUTCOME_TERMINAL_AFTER_DISCONNECT
    assert counter.calls == [{"surface": "responses", "outcome": OUTCOME_TERMINAL_AFTER_DISCONNECT}]
    assert _bodies(sent)[-2:] == [_COMPLETED.encode(), b""]  # bytes still forwarded, nothing synthesized
    record = next(record for record in caplog.records if "responses_stream_terminal_delivery" in record.getMessage())
    assert record.levelno == logging.WARNING
    assert "outcome=terminal_after_disconnect terminal=response.completed" in record.getMessage()


async def test_disconnect_stamp_after_the_terminal_write_is_still_terminal_written() -> None:
    response = DeliveryTracedStreamingResponse(_iter(_CREATED, _COMPLETED), surface="responses")
    await _run(response, stamp_before_chunk=3)  # stamp arrives with the final more_body=False send
    assert response.outcome == OUTCOME_TERMINAL_WRITTEN


async def test_generator_exception_before_terminal_is_reraised_without_synthetic_terminator(
    caplog: pytest.LogCaptureFixture, counter: _FakeCounter
) -> None:

    async def body() -> AsyncIterator[str]:
        yield _CREATED
        yield _DELTA
        raise ValueError("wrapper failed while formatting the terminal block")

    response = DeliveryTracedStreamingResponse(body(), surface="responses")
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "method": "POST", "path": "/"}
    with caplog.at_level(logging.DEBUG, logger=downstream_delivery.__name__):
        with pytest.raises(ValueError):
            await response(scope, cast(Any, receive), cast(Any, send))

    assert response.outcome == OUTCOME_EXCEPTION_BEFORE_TERMINAL
    assert counter.calls == [{"surface": "responses", "outcome": OUTCOME_EXCEPTION_BEFORE_TERMINAL}]
    assert _bodies(sent) == [_CREATED.encode(), _DELTA.encode()]
    assert all(message.get("more_body", True) for message in sent[1:])  # no more_body=False was sent
    record = next(record for record in caplog.records if "responses_stream_terminal_delivery" in record.getMessage())
    assert record.levelno == logging.WARNING
    assert "outcome=exception_before_terminal terminal=None chunks=2" in record.getMessage()
    assert record.getMessage().endswith("exc=ValueError")


async def test_generator_exception_after_terminal_is_terminal_written_with_exc_name() -> None:
    async def body() -> AsyncIterator[str]:
        yield _COMPLETED
        raise RuntimeError("cleanup failed after the terminal")

    response = DeliveryTracedStreamingResponse(body(), surface="responses")
    with pytest.raises(RuntimeError):
        await _run(response)
    assert response.outcome == OUTCOME_TERMINAL_WRITTEN


async def test_server_cancel_while_send_waits_on_terminal_chunk_is_cancelled_before_terminal(
    caplog: pytest.LogCaptureFixture, counter: _FakeCounter
) -> None:
    """uvicorn drains write backpressure *before* writing: a cancel inside ``send`` means no write."""
    terminal_send_started = asyncio.Event()
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body" and b"response.completed" in cast(bytes, message["body"]):
            terminal_send_started.set()
            await asyncio.Event().wait()  # suspended in flow.drain(); the server task is cancelled here
        sent.append(message)

    response = DeliveryTracedStreamingResponse(_iter(_CREATED, _COMPLETED), surface="responses")
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "method": "POST", "path": "/"}
    with caplog.at_level(logging.DEBUG, logger=downstream_delivery.__name__):
        task = asyncio.create_task(response(scope, cast(Any, receive), cast(Any, send)))
        await terminal_send_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert response.outcome == OUTCOME_CANCELLED_BEFORE_TERMINAL
    assert counter.calls == [{"surface": "responses", "outcome": OUTCOME_CANCELLED_BEFORE_TERMINAL}]
    assert _bodies(sent) == [_CREATED.encode()]
    record = next(record for record in caplog.records if "responses_stream_terminal_delivery" in record.getMessage())
    assert "outcome=cancelled_before_terminal terminal=None chunks=1" in record.getMessage()
    assert record.getMessage().endswith("exc=CancelledError")


async def test_cancel_after_terminal_is_terminal_written_with_cancelled_exc(
    caplog: pytest.LogCaptureFixture, counter: _FakeCounter
) -> None:
    terminal_sent = asyncio.Event()

    async def body() -> AsyncIterator[str]:
        yield _COMPLETED
        terminal_sent.set()
        await asyncio.Event().wait()  # e.g. uvicorn shutdown drain cancels the task after the terminal
        yield ""  # pragma: no cover

    response = DeliveryTracedStreamingResponse(body(), surface="responses")
    with caplog.at_level(logging.DEBUG, logger=downstream_delivery.__name__):
        task = asyncio.create_task(_run(response))
        await terminal_sent.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert response.outcome == OUTCOME_TERMINAL_WRITTEN
    assert counter.calls == [{"surface": "responses", "outcome": OUTCOME_TERMINAL_WRITTEN}]
    record = next(record for record in caplog.records if "responses_stream_terminal_delivery" in record.getMessage())
    assert record.levelno == logging.DEBUG
    assert "outcome=terminal_written terminal=response.completed chunks=1" in record.getMessage()
    assert record.getMessage().endswith("exc=CancelledError")


async def test_cancelled_before_terminal(caplog: pytest.LogCaptureFixture, counter: _FakeCounter) -> None:
    first_chunk_sent = asyncio.Event()

    async def body() -> AsyncIterator[str]:
        yield _CREATED
        first_chunk_sent.set()
        await asyncio.Event().wait()  # upstream never produces the terminal
        yield _COMPLETED  # pragma: no cover

    response = DeliveryTracedStreamingResponse(body(), surface="responses")
    with caplog.at_level(logging.DEBUG, logger=downstream_delivery.__name__):
        task = asyncio.create_task(_run(response))
        await first_chunk_sent.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert response.outcome == OUTCOME_CANCELLED_BEFORE_TERMINAL
    assert counter.calls == [{"surface": "responses", "outcome": OUTCOME_CANCELLED_BEFORE_TERMINAL}]
    record = next(record for record in caplog.records if "responses_stream_terminal_delivery" in record.getMessage())
    assert record.levelno == logging.INFO
    assert "outcome=cancelled_before_terminal terminal=None chunks=1" in record.getMessage()
    assert record.getMessage().endswith("exc=CancelledError")


async def test_client_disconnect_message_cancels_before_terminal(caplog: pytest.LogCaptureFixture) -> None:
    """Starlette's ASGI 2.3 path: ``http.disconnect`` on receive() cancels the body iteration."""
    first_chunk_sent = asyncio.Event()

    async def body() -> AsyncIterator[str]:
        yield _CREATED
        first_chunk_sent.set()
        await asyncio.Event().wait()
        yield _COMPLETED  # pragma: no cover

    async def receive() -> dict[str, object]:
        await first_chunk_sent.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        pass

    response = DeliveryTracedStreamingResponse(body(), surface="responses")
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "method": "POST", "path": "/"}
    with caplog.at_level(logging.DEBUG, logger=downstream_delivery.__name__):
        await response(scope, cast(Any, receive), cast(Any, send))
    assert response.outcome == OUTCOME_CANCELLED_BEFORE_TERMINAL
    record = next(record for record in caplog.records if "responses_stream_terminal_delivery" in record.getMessage())
    assert record.levelno == logging.INFO
    assert record.getMessage().endswith("exc=None")  # absorbed by Starlette, not a server-side cancel


async def test_stream_ending_without_terminal(caplog: pytest.LogCaptureFixture, counter: _FakeCounter) -> None:
    response = DeliveryTracedStreamingResponse(_iter(_CREATED, _DELTA), surface="responses")
    with caplog.at_level(logging.DEBUG, logger=downstream_delivery.__name__):
        await _run(response)
    assert response.outcome == OUTCOME_ENDED_WITHOUT_TERMINAL
    assert counter.calls == [{"surface": "responses", "outcome": OUTCOME_ENDED_WITHOUT_TERMINAL}]
    record = next(record for record in caplog.records if "responses_stream_terminal_delivery" in record.getMessage())
    assert record.levelno == logging.WARNING


async def test_missing_scope_state_is_tolerated() -> None:
    response = DeliveryTracedStreamingResponse(_iter(_COMPLETED), surface="responses")
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "method": "POST", "path": "/"}
    await response(scope, cast(Any, receive), cast(Any, send))
    assert response.outcome == OUTCOME_TERMINAL_WRITTEN
    assert _bodies(sent) == [_COMPLETED.encode(), b""]


async def test_no_prometheus_client_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downstream_delivery, "PROMETHEUS_AVAILABLE", False)
    monkeypatch.setattr(downstream_delivery, "stream_terminal_delivery_total", None)
    response = DeliveryTracedStreamingResponse(_iter(_COMPLETED), surface="responses")
    await _run(response)
    assert response.outcome == OUTCOME_TERMINAL_WRITTEN


async def test_real_prometheus_counter_registration_and_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the real ``Counter`` when the ``metrics`` extra is installed (skipped otherwise)."""
    pytest.importorskip("prometheus_client")
    assert prometheus.PROMETHEUS_AVAILABLE and prometheus.stream_terminal_delivery_total is not None
    monkeypatch.setattr(downstream_delivery, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(
        downstream_delivery, "stream_terminal_delivery_total", prometheus.stream_terminal_delivery_total
    )
    registry = cast(Any, prometheus.REGISTRY)
    labels = {"surface": "responses", "outcome": OUTCOME_TERMINAL_WRITTEN}
    before = registry.get_sample_value("codex_lb_stream_terminal_delivery_total", labels) or 0.0

    response = DeliveryTracedStreamingResponse(_iter(_COMPLETED), surface="responses")
    await _run(response)

    assert registry.get_sample_value("codex_lb_stream_terminal_delivery_total", labels) == before + 1.0


def test_outcome_label_set_is_closed() -> None:
    assert OUTCOMES == {
        "terminal_written",
        "terminal_after_disconnect",
        "exception_before_terminal",
        "cancelled_before_terminal",
        "ended_without_terminal",
    }
    assert "stream_terminal_delivery_total" in prometheus.__all__
