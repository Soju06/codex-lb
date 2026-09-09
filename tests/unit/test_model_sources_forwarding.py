from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from types import SimpleNamespace
from typing import cast

import aiohttp
import anyio
import pytest

import app.modules.model_sources.forwarding as forwarding_module
from app.core.crypto import TokenEncryptor
from app.core.types import JsonValue
from app.db.models import ModelSource
from app.modules.model_sources.forwarding import (
    SOURCE_CONNECT_DEADLINE_SECONDS,
    SOURCE_FIRST_FRAME_DEADLINE_SECONDS,
    SOURCE_HEADER_DEADLINE_SECONDS,
    SOURCE_STREAM_IDLE_CAP_SECONDS,
    ModelSourceForwardingError,
    SourceChatStream,
    SourceResponsesStream,
    SourceStreamUsageParser,
    SourceUsage,
    SourceUsageHolder,
    _audio_seconds_from_body,
    _error_payload_from_body,
    _redact_source_error_payload,
    _timings_from_audio_body,
    _timings_from_metrics,
    _timings_from_payload,
    _usage_from_audio_body,
    classify_responses_frame,
    source_stream_idle_seconds,
)
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit


class _FakeEncryptor:
    def decrypt(self, token: bytes) -> str:
        assert token == b"encrypted-source-key"
        return "source-secret-token"


def _fake_encryptor() -> TokenEncryptor:
    return cast(TokenEncryptor, _FakeEncryptor())


def test_chat_stream_usage_parser_handles_split_sse_frame() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")

    parser.feed(b'data: {"usage":{"prompt_tokens":12,')
    parser.feed(b'"completion_tokens":5,"prompt_tokens_details":{"cached_tokens":3}}}\n\n')

    assert holder.usage is not None
    assert holder.usage.input_tokens == 12
    assert holder.usage.output_tokens == 5
    assert holder.usage.cached_input_tokens == 3


def test_chat_stream_usage_parser_handles_crlf_frames() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")

    parser.feed(
        b'data: {"usage":{"prompt_tokens":12,"completion_tokens":5,'
        b'"prompt_tokens_details":{"cached_tokens":3}}}\r\n\r\n'
    )

    assert holder.usage is not None
    assert holder.usage.input_tokens == 12
    assert holder.usage.output_tokens == 5
    assert holder.usage.cached_input_tokens == 3


def test_chat_stream_usage_parser_handles_crlf_split_across_chunks() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")

    parser.feed(b'data: {"usage":{"prompt_tokens":2,"completion_tokens":1}}\r')
    parser.feed(b"\n\r\n")

    assert holder.usage is not None
    assert holder.usage.input_tokens == 2
    assert holder.usage.output_tokens == 1


def test_chat_stream_usage_parser_keeps_a_crlf_split_inside_a_frame_as_one_line_ending() -> None:
    """A CRLF whose CR closes one chunk and whose LF opens the next is one line ending, not a frame boundary."""

    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")

    parser.feed(b'data: {"usage":{"prompt_tokens":2,\r')
    parser.feed(b'\ndata: "completion_tokens":1}}\r\n\r\n')

    assert holder.usage is not None
    assert holder.usage.input_tokens == 2
    assert holder.usage.output_tokens == 1


def test_responses_stream_usage_parser_keeps_a_crlf_split_inside_a_frame_as_one_line_ending() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")

    parser.feed(b'data: {"type":"response.completed",\r')
    parser.feed(b'\ndata: "response":{"id":"resp_crlf","usage":{"input_tokens":3,"output_tokens":2}}}\r\n\r\n')

    assert holder.usage is not None
    assert holder.usage.input_tokens == 3
    assert holder.usage.output_tokens == 2
    assert holder.terminal_kind == "completed"
    assert holder.response_id == "resp_crlf"
    assert holder.first_content_seen is True


def test_stream_usage_parser_bare_cr_before_a_crlf_chunk_is_still_two_line_endings() -> None:
    """Control: a CR closing a chunk followed by a chunk that opens with CRLF (not a lone LF) stays a blank line."""

    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")

    parser.feed(b'data: {"usage":{"prompt_tokens":5,"completion_tokens":4}}\r')
    parser.feed(b"\r\ndata: [DONE]\r\n\r\n")

    assert holder.usage is not None
    assert holder.usage.input_tokens == 5
    assert holder.usage.output_tokens == 4


def test_stream_usage_parser_bounds_buffer_without_frame_boundaries() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")

    for _ in range(600):
        parser.feed(b"x" * 4096)

    assert len(parser._buffer) <= SourceStreamUsageParser._MAX_BUFFER_CHARS


def test_chat_stream_usage_parser_rejects_negative_tokens() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")

    parser.feed(b'data: {"usage":{"prompt_tokens":-5,"completion_tokens":3}}\n\n')

    assert holder.usage is None


def test_responses_stream_usage_parser_rejects_negative_tokens() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")

    parser.feed(b'data: {"type":"response.completed","response":{"usage":{"input_tokens":4,"output_tokens":-1}}}\n\n')

    assert holder.usage is None


def test_responses_stream_usage_parser_handles_split_sse_frame() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")

    parser.feed(b'data: {"type":"response.completed","response":{"usage":{"input_tokens":7,')
    parser.feed(b'"output_tokens":4,"input_tokens_details":{"cached_tokens":2}}}}\n\n')

    assert holder.usage is not None
    assert holder.usage.input_tokens == 7
    assert holder.usage.output_tokens == 4
    assert holder.usage.cached_input_tokens == 2


def test_audio_usage_parser_accepts_total_tokens_only_json() -> None:
    usage = _usage_from_audio_body(
        b'{"text":"hello","usage":{"total_tokens":42}}',
        "application/json; charset=utf-8",
    )

    assert usage is not None
    assert usage.input_tokens == 42
    assert usage.output_tokens == 0


def test_audio_usage_parser_ignores_duration_only_usage() -> None:
    usage = _usage_from_audio_body(
        b'{"text":"hello","usage":{"type":"duration","seconds":3.5}}',
        "application/json",
    )

    assert usage is None


def test_audio_seconds_from_top_level_duration() -> None:
    assert _audio_seconds_from_body(b'{"text":"hi","duration":30.464}', "application/json") == 30.464


def test_audio_seconds_from_usage_seconds_fallback() -> None:
    assert _audio_seconds_from_body(b'{"text":"hi","usage":{"seconds":12.5}}', "application/json") == 12.5


def test_audio_seconds_ignores_nonpositive_and_nonjson() -> None:
    assert _audio_seconds_from_body(b'{"duration":0}', "application/json") is None
    assert _audio_seconds_from_body(b'{"duration":-4}', "application/json") is None
    assert _audio_seconds_from_body(b"plain text", "text/plain") is None
    assert _audio_seconds_from_body(b'{"duration":true}', "application/json") is None


def test_audio_error_payload_preserves_text_body() -> None:
    payload = _error_payload_from_body(b"missing required field: file", "text/plain; charset=utf-8")
    error = cast(dict[str, object], payload["error"])
    assert isinstance(error, dict)

    assert error["code"] == "model_source_error"
    assert error["message"] == "missing required field: file"


def test_source_error_payload_redacts_configured_api_key() -> None:
    source = ModelSource(
        id="src_redact",
        name="Redact",
        kind="openai_compatible",
        base_url="http://127.0.0.1:8000/v1",
        api_key_encrypted=b"encrypted-source-key",
    )
    payload: dict[str, JsonValue] = {
        "error": {
            "message": "upstream echoed Authorization: Bearer source-secret-token",
            "details": ["source-secret-token", {"header": "Bearer source-secret-token"}],
            "code": "bad_request",
        }
    }

    redacted = _redact_source_error_payload(payload, source, encryptor=_fake_encryptor())
    error = cast(dict[str, object], redacted["error"])

    assert "source-secret-token" not in str(redacted)
    assert error["message"] == "upstream echoed Authorization: Bearer [REDACTED]"
    assert error["details"] == ["[REDACTED]", {"header": "Bearer [REDACTED]"}]


def test_timings_from_metrics_preserves_dashboard_generation_speed() -> None:
    # The upstream's tokens_per_second includes TTFT, while the dashboard
    # intentionally reports generation-only throughput for all request kinds.
    metrics: dict[str, JsonValue] = {
        "time_to_first_token_ms": 108.83,
        "generation_time_ms": 162.98,
        "queue_time_ms": 0.037,
        "mean_itl_ms": 20.37,
        "tokens_per_second": 33.11,
    }

    timings = _timings_from_metrics(metrics)

    assert timings is not None
    assert timings.latency_first_token_ms == 109
    assert timings.latency_ms == 272
    completion_tokens = 9
    generation_ms = timings.latency_ms - timings.latency_first_token_ms
    dashboard_tps = completion_tokens / (generation_ms / 1000)
    assert dashboard_tps == pytest.approx(55.22, abs=0.05)


def test_timings_from_payload_reads_top_level_metrics() -> None:
    payload: dict[str, JsonValue] = {
        "usage": {"prompt_tokens": 28, "completion_tokens": 9, "total_tokens": 37},
        "metrics": {"time_to_first_token_ms": 100, "generation_time_ms": 50},
    }

    timings = _timings_from_payload(payload)

    assert timings is not None
    assert timings.latency_first_token_ms == 100
    assert timings.latency_ms == 150


def test_timings_from_payload_none_without_metrics() -> None:
    assert _timings_from_payload({"usage": {"prompt_tokens": 1, "completion_tokens": 1}}) is None


def test_timings_from_metrics_rejects_negative_or_missing_values() -> None:
    assert _timings_from_metrics({"time_to_first_token_ms": -1, "generation_time_ms": 10}) is None
    assert _timings_from_metrics({"time_to_first_token_ms": 10}) is None
    assert _timings_from_metrics({"generation_time_ms": 10}) is None
    assert _timings_from_metrics({"time_to_first_token_ms": True, "generation_time_ms": 10}) is None


@pytest.mark.parametrize("invalid_value", [float("nan"), float("inf"), float("-inf")])
def test_timings_from_metrics_rejects_non_finite_values(invalid_value: float) -> None:
    assert _timings_from_metrics({"time_to_first_token_ms": invalid_value, "generation_time_ms": 10}) is None
    assert _timings_from_metrics({"time_to_first_token_ms": 10, "generation_time_ms": invalid_value}) is None


def test_timings_from_audio_body_parses_json_metrics() -> None:
    body = b'{"text":"hi","metrics":{"time_to_first_token_ms":20,"generation_time_ms":80}}'

    timings = _timings_from_audio_body(body, "application/json")

    assert timings is not None
    assert timings.latency_first_token_ms == 20
    assert timings.latency_ms == 100


def test_chat_stream_usage_parser_captures_metrics_from_final_frame() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")

    parser.feed(
        b'data: {"usage":{"prompt_tokens":28,"completion_tokens":9},'
        b'"metrics":{"time_to_first_token_ms":108.83,"generation_time_ms":162.98}}\n\n'
    )

    assert holder.usage is not None
    assert holder.timings is not None
    assert holder.timings.latency_first_token_ms == 109
    assert holder.timings.latency_ms == 272


def test_chat_stream_usage_parser_ignores_non_finite_metrics() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")

    parser.feed(
        b'data: {"usage":{"prompt_tokens":1,"completion_tokens":1},'
        b'"metrics":{"time_to_first_token_ms":NaN,"generation_time_ms":10}}\n\n'
    )

    assert holder.usage is not None
    assert holder.timings is None


def test_responses_stream_usage_parser_captures_nested_response_metrics() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")

    parser.feed(
        b'data: {"type":"response.completed","response":{'
        b'"usage":{"input_tokens":7,"output_tokens":4},'
        b'"metrics":{"time_to_first_token_ms":50,"generation_time_ms":100}}}\n\n'
    )

    assert holder.timings is not None
    assert holder.timings.latency_first_token_ms == 50
    assert holder.timings.latency_ms == 150


# ---------------------------------------------------------------------------
# WP-C1 P2: frame classification, holder observations, transport hardening
# ---------------------------------------------------------------------------

_FORWARDING_LOGGER = "app.modules.model_sources.forwarding"


def _sse(event: dict[str, object]) -> bytes:
    return f"data: {json.dumps(event)}\n\n".encode()


def _responses_source(
    *, source_id: str = "src_p2", timeout_seconds: float | None = None, api_key: bool = False
) -> ModelSource:
    return ModelSource(
        id=source_id,
        name=source_id,
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        # ``_fake_encryptor`` decrypts this token; keyless sources send no Authorization header.
        api_key_encrypted=b"encrypted-source-key" if api_key else None,
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=True,
        timeout_seconds=timeout_seconds,
    )


def _forever() -> Awaitable[bool]:
    return asyncio.Event().wait()


class _FakeContent:
    """Minimal ``aiohttp.StreamReader`` double: ``readany`` for the open, ``iter_chunked`` for the body.

    Like the real reader, both draw from one stream: when the open returned at
    the headers without ``readany`` (chat completions), the body's first
    ``iter_chunked`` read delivers ``first`` -- behind ``first_gate`` -- and a
    stream whose ``first`` is empty is at EOF.
    """

    def __init__(
        self,
        first: bytes,
        rest: list[bytes | BaseException] | None = None,
        *,
        first_gate: Callable[[], Awaitable[object]] | None = None,
        stall_after_rest: bool = False,
    ) -> None:
        self._first = first
        self._rest = list(rest or [])
        self._first_gate = first_gate
        self._stall_after_rest = stall_after_rest
        self.readany_calls = 0

    async def readany(self) -> bytes:
        self.readany_calls += 1
        if self._first_gate is not None:
            await self._first_gate()
        return self._first

    def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            if self.readany_calls == 0:
                if self._first_gate is not None:
                    await self._first_gate()
                if not self._first:
                    return
                yield self._first
            for item in self._rest:
                if isinstance(item, BaseException):
                    raise item
                yield item
            if self._stall_after_rest:
                await _forever()

        return gen()


class _FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        content: _FakeContent | None = None,
        headers: dict[str, str] | None = None,
        json_body: object = None,
        json_gate: Callable[[], Awaitable[object]] | None = None,
    ) -> None:
        self.status = status
        self.content = content if content is not None else _FakeContent(b"")
        self.headers = headers or {}
        self._json_body = json_body
        self._json_gate = json_gate
        self.json_calls = 0

    async def json(self, content_type: str | None = None) -> object:
        del content_type
        self.json_calls += 1
        if self._json_gate is not None:
            await self._json_gate()
        return self._json_body


class _PostContext:
    def __init__(
        self,
        response: _FakeResponse,
        *,
        enter_gate: Callable[[], Awaitable[object]] | None = None,
        enter_error: BaseException | None = None,
        on_enter: Callable[[], None] | None = None,
    ) -> None:
        self._response = response
        self._enter_gate = enter_gate
        self._enter_error = enter_error
        self._on_enter = on_enter
        self.exited = 0

    async def __aenter__(self) -> _FakeResponse:
        if self._on_enter is not None:
            self._on_enter()
        if self._enter_gate is not None:
            await self._enter_gate()
        if self._enter_error is not None:
            raise self._enter_error
        return self._response

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        del exc_type, exc, tb
        self.exited += 1
        return False


class _FakeSession:
    def __init__(self, context: _PostContext) -> None:
        self._context = context
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, *, headers: dict[str, str], json: object, timeout: aiohttp.ClientTimeout) -> _PostContext:
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self._context


class _SessionLease:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session
        self.released = 0

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        del exc_type, exc, tb
        self.released += 1
        return False


def _install_session(
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeResponse,
    *,
    enter_gate: Callable[[], Awaitable[object]] | None = None,
    enter_error: BaseException | None = None,
    on_enter: Callable[[], None] | None = None,
) -> tuple[_FakeSession, _PostContext, _SessionLease]:
    context = _PostContext(response, enter_gate=enter_gate, enter_error=enter_error, on_enter=on_enter)
    session = _FakeSession(context)
    lease = _SessionLease(session)
    monkeypatch.setattr(forwarding_module, "lease_model_source_session", lambda: lease)
    monkeypatch.setattr(
        forwarding_module,
        "get_settings",
        lambda: SimpleNamespace(stream_idle_timeout_seconds=7200.0),
    )
    return session, context, lease


def _virtual() -> tuple[VirtualClock, VirtualScheduler]:
    clock = VirtualClock()
    return clock, VirtualScheduler(clock)


async def _collect(body: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in body]


# -- classification -----------------------------------------------------------


@pytest.mark.parametrize(
    ("event_type", "kind"),
    [
        ("response.created", "non_content"),
        ("response.in_progress", "non_content"),
        ("response.queued", "non_content"),
        (None, "non_content"),
        ("response.output_item.added", "content"),
        ("response.output_text.delta", "content"),
        ("response.reasoning_summary_text.delta", "content"),
        ("response.function_call_arguments.delta", "content"),
        ("response.output_item.done", "content"),
        ("response.some_future_event", "content"),
        ("", "content"),
        ("response.completed", "success_terminal"),
        ("response.incomplete", "success_terminal"),
        ("response.failed", "failure_terminal"),
        ("error", "failure_terminal"),
    ],
)
def test_classify_responses_frame_table(event_type: str | None, kind: str) -> None:
    assert classify_responses_frame(event_type) == kind


def test_responses_parser_ignores_keepalive_comments_and_done_sentinel() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")

    parser.feed(b": keepalive\n\n")
    parser.feed(b"data: [DONE]\n\n")
    parser.feed(b"data: not json\n\n")
    parser.feed(b"data: [1, 2, 3]\n\n")

    assert holder.first_content_seen is False
    assert holder.terminal_kind is None
    assert holder.response_id is None


def test_responses_parser_records_holder_observations_from_a_synthetic_stream() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")
    created = {"id": "resp_1", "object": "response", "status": "in_progress", "model": "m"}

    parser.feed(_sse({"type": "response.created", "sequence_number": 0, "response": created}))
    assert holder.response_id == "resp_1"
    assert holder.created_envelope == created
    assert holder.first_content_seen is False

    parser.feed(_sse({"type": "response.in_progress", "response": created}))
    assert holder.first_content_seen is False

    parser.feed(_sse({"type": "response.output_item.added", "item": {"type": "message"}}))
    assert holder.first_content_seen is True
    assert holder.first_output_item_seen is True
    assert holder.terminal_kind is None

    parser.feed(_sse({"type": "response.output_text.delta", "delta": "Hello"}))
    parser.feed(_sse({"type": "response.output_text.delta", "delta": " world"}))
    parser.feed(_sse({"type": "response.output_text.delta", "delta": 7}))
    assert holder.delta_chars == len("Hello world")

    parser.feed(
        _sse(
            {
                "type": "response.completed",
                "response": {"id": "resp_1", "usage": {"input_tokens": 3, "output_tokens": 2}},
            }
        )
    )
    assert holder.terminal_kind == "completed"
    assert holder.usage is not None
    assert holder.usage.output_tokens == 2


@pytest.mark.parametrize(
    ("event_type", "terminal_kind"),
    [
        ("response.completed", "completed"),
        ("response.incomplete", "incomplete"),
        ("response.failed", "failed"),
        ("error", "error"),
    ],
)
def test_responses_parser_records_terminal_kind(event_type: str, terminal_kind: str) -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")

    parser.feed(_sse({"type": event_type, "response": {"id": "resp_t"}}))

    assert holder.terminal_kind == terminal_kind
    # A success terminal delivers the response (an empty completion is still
    # delivered content); a failure terminal delivers nothing.
    assert holder.first_content_seen is (terminal_kind in {"completed", "incomplete"})


def test_responses_parser_classifies_a_typeless_error_record_as_a_failure_terminal() -> None:
    """The public wrapper's ``classify_event_type`` reads a typeless ``{"error": {...}}`` record as ``error``; the
    parser must agree so the record is a failure terminal (flushed without the hook, settled as an error), not
    bookkeeping."""

    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")

    parser.feed(_sse({"type": "response.created", "response": {"id": "resp_typeless"}}))
    parser.feed(_sse({"error": {"message": "boom", "type": "server_error", "code": "overloaded"}}))

    assert holder.terminal_kind == "error"
    assert holder.first_content_seen is False
    # A typeless record without an error object stays untyped bookkeeping, as in the wrapper.
    other = SourceUsageHolder()
    SourceStreamUsageParser(other, response_shape="responses").feed(_sse({"error": "boom"}))
    assert other.terminal_kind is None
    assert other.first_content_seen is False


def test_responses_parser_falls_back_to_any_response_id_when_created_was_not_seen() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")

    parser.feed(_sse({"type": "response.output_text.delta", "delta": "x", "response": {"id": "resp_late"}}))
    parser.feed(_sse({"type": "response.created", "response": {"id": "resp_created"}}))

    assert holder.response_id == "resp_created"
    assert holder.created_envelope == {"id": "resp_created"}


def test_chat_parser_does_not_observe_responses_frames() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")

    parser.feed(_sse({"type": "response.output_text.delta", "delta": "Hello"}))

    assert holder.first_content_seen is False
    assert holder.delta_chars == 0


def test_responses_parser_joins_multi_line_data_fields_like_the_wrapper() -> None:
    """SSE allows a data JSON to be split across multiple ``data:`` lines (joined with newlines).

    The usage parser must observe the joined event exactly as the public wrapper's
    ``parse_sse_data_json`` reconstructs it; parsing each ``data:`` line independently silently
    lost every holder observation for such a frame (codex review P2).
    """

    from app.core.utils.sse import parse_sse_data_json

    block = (
        b"event: response.output_item.added\n"
        b'data: {"type": "response.output_item.added",\n'
        b'data:  "output_index": 0}\n\n'
    )
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")
    parser.feed(block)

    assert holder.first_content_seen is True
    assert holder.first_output_item_seen is True
    assert parse_sse_data_json(block.decode()) == {"type": "response.output_item.added", "output_index": 0}


def test_responses_parser_captures_usage_and_terminal_from_a_multi_line_completed_frame() -> None:
    block = (
        b"event: response.completed\n"
        b'data: {"type": "response.completed",\n'
        b'data:  "response": {"id": "resp_ml", "usage": {"input_tokens": 5, "output_tokens": 3}}}\n\n'
    )
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")
    parser.feed(block)

    assert holder.terminal_kind == "completed"
    assert holder.usage == SourceUsage(input_tokens=5, output_tokens=3)
    assert holder.response_id == "resp_ml"


def test_chat_parser_joins_multi_line_data_fields_for_usage() -> None:
    block = b'data: {\ndata: "usage": {"prompt_tokens": 6, "completion_tokens": 2}\ndata: }\n\n'
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")
    parser.feed(block)

    assert holder.usage == SourceUsage(input_tokens=6, output_tokens=2)


_BOM = b"\xef\xbb\xbf"


def _bom_completed_event(response_id: str) -> dict[str, object]:
    return {
        "type": "response.completed",
        "response": {
            "id": response_id,
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "hi"}]}],
            "usage": {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
        },
    }


@pytest.mark.parametrize(
    "chunks",
    [
        pytest.param([_BOM + _sse(_bom_completed_event("resp_bom"))], id="bom_and_frame_in_one_chunk"),
        pytest.param([_BOM, _sse(_bom_completed_event("resp_bom"))], id="bom_alone_in_the_first_chunk"),
        pytest.param([_BOM[:1], _BOM[1:] + _sse(_bom_completed_event("resp_bom"))], id="bom_split_across_chunks"),
    ],
)
def test_responses_parser_ignores_one_leading_utf8_bom_like_the_wrapper(chunks: list[bytes]) -> None:
    """The public wrapper's event-block reassembler drops one leading UTF-8 BOM and delivers the frame; the usage
    parser must classify the same record (I11: delivered => pinned) instead of reading the field ``\\ufeffdata`` and
    observing nothing."""

    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")

    for chunk in chunks:
        parser.feed(chunk)

    assert holder.terminal_kind == "completed"
    assert holder.first_content_seen is True
    assert holder.usage == SourceUsage(input_tokens=3, output_tokens=5)
    assert holder.response_id == "resp_bom"


def test_responses_parser_strips_only_the_stream_leading_bom() -> None:
    """A BOM inside the stream is not a BOM to the reassembler either: both sides leave the record unparsed."""

    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")

    parser.feed(_sse({"type": "response.created", "response": {"id": "resp_mid"}}))
    parser.feed(_BOM + _sse(_bom_completed_event("resp_mid")))

    assert holder.response_id == "resp_mid"
    assert holder.terminal_kind is None
    assert holder.usage is None


def test_chat_parser_ignores_one_leading_utf8_bom() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")

    parser.feed(_BOM + b'data: {"usage": {"prompt_tokens": 6, "completion_tokens": 2}}\n\n')

    assert holder.usage == SourceUsage(input_tokens=6, output_tokens=2)


@pytest.mark.asyncio
async def test_stream_body_bom_prefixed_data_only_terminal_runs_the_hook_before_flushing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source whose first bytes are a BOM followed by a data-only ``response.completed`` delivers an answer the
    wrapper parses (it strips the BOM); the hook must run before that frame is released and its usage must reach the
    holder -- the parser reading ``\\ufeffdata`` instead bypassed both."""

    completed = _BOM + _sse(_bom_completed_event("resp_bom_hook"))
    response = _FakeResponse(content=_FakeContent(completed, []))
    _session, _context, lease = _install_session(monkeypatch, response)
    order: list[str] = []

    async def hook(holder: SourceUsageHolder) -> None:
        order.append(f"hook:{holder.terminal_kind}")

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)
    delivered: list[bytes] = []
    async for chunk in stream.body:
        if not order or not order[-1].startswith("yield"):
            order.append("yield")
        delivered.append(chunk)

    assert order == ["hook:completed", "yield"]
    # Bytes are relayed verbatim; the public wrapper strips the BOM downstream.
    assert delivered == [completed]
    assert stream.usage_holder.usage == SourceUsage(input_tokens=3, output_tokens=5)
    assert lease.released == 1


# -- constants ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("configured", "expected"),
    [(7200.0, 300.0), (300.0, 300.0), (30.0, 30.0), (0.5, 0.5)],
)
def test_source_stream_idle_seconds_is_capped(
    monkeypatch: pytest.MonkeyPatch, configured: float, expected: float
) -> None:
    monkeypatch.setattr(
        forwarding_module, "get_settings", lambda: SimpleNamespace(stream_idle_timeout_seconds=configured)
    )

    assert source_stream_idle_seconds() == expected
    assert source_stream_idle_seconds() <= SOURCE_STREAM_IDLE_CAP_SECONDS


def test_source_deadline_constants_are_the_design_figures() -> None:
    assert SOURCE_CONNECT_DEADLINE_SECONDS == 10.0
    assert SOURCE_HEADER_DEADLINE_SECONDS == 20.0
    assert SOURCE_FIRST_FRAME_DEADLINE_SECONDS == 30.0
    assert SOURCE_STREAM_IDLE_CAP_SECONDS == 300.0


# -- stream open: first chunk, timeouts, ordering -------------------------------


@pytest.mark.asyncio
async def test_stream_responses_yields_the_first_chunk_before_reading_further(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _sse({"type": "response.created", "response": {"id": "resp_first"}})
    response = _FakeResponse(content=_FakeContent(created, stall_after_rest=True))
    session, context, lease = _install_session(monkeypatch, response)
    clock, scheduler = _virtual()
    clock.advance(12.5)

    stream = await forwarding_module.stream_responses(
        _responses_source(), {"model": "m", "stream": True}, scheduler=scheduler, clock=clock
    )

    assert stream.upstream_status_code == 200
    assert stream.usage_holder.first_frame_at == 12.5
    assert response.content.readany_calls == 1
    timeout = cast(aiohttp.ClientTimeout, session.calls[0]["timeout"])
    assert timeout.total == 600.0
    # ``connect`` is not armed: it also bounds the pooled-connection wait (codex review P2).
    assert timeout.connect is None
    assert timeout.sock_connect == SOURCE_CONNECT_DEADLINE_SECONDS
    # The idle cap is the body's scheduler timer, never aiohttp's socket timer
    # (which would pre-empt the header/first-frame phases under a low idle window).
    assert timeout.sock_read is None
    assert cast(dict[str, str], session.calls[0]["headers"])["Accept"] == "text/event-stream"

    # The first chunk is already in hand: it is yielded without touching the
    # (stalled) remainder of the body.
    first = await asyncio.wait_for(anext(stream.body), timeout=1)
    assert first == created
    assert stream.usage_holder.response_id == "resp_first"

    await stream.aclose()
    assert lease.released == 1
    assert context.exited == 1
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_open_source_stream_returns_the_first_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeResponse(content=_FakeContent(b"data: hello\n\n"))
    _install_session(monkeypatch, response)

    stack, opened, first_chunk = await forwarding_module._open_source_stream(
        _responses_source(api_key=True), "/responses", {"model": "m"}, encryptor=_fake_encryptor()
    )

    assert opened is response
    assert first_chunk == b"data: hello\n\n"
    await stack.aclose()


@pytest.mark.asyncio
async def test_open_source_stream_header_deadline_expires_at_twenty_virtual_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse(content=_FakeContent(b"data: never\n\n"))
    _session, context, lease = _install_session(monkeypatch, response, enter_gate=_forever)
    clock, scheduler = _virtual()

    task = scheduler.create_task(
        forwarding_module.stream_responses(
            _responses_source(source_id="src_slow_headers"),
            {"model": "m", "stream": True},
            scheduler=scheduler,
            clock=clock,
        )
    )
    await scheduler.advance(19.9)
    assert not task.done()

    await scheduler.advance(0.1)
    assert task.done()
    with pytest.raises(ModelSourceForwardingError) as excinfo:
        task.result()

    error = excinfo.value
    assert error.status_code == 504
    assert error.upstream_status_code is None
    assert error.timeout_phase == "header"
    assert cast(dict[str, object], error.payload["error"])["code"] == "model_source_timeout"
    assert lease.released == 1
    assert context.exited == 0  # the request context was never entered
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_open_source_stream_first_frame_deadline_expires_at_thirty_virtual_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse(content=_FakeContent(b"data: late\n\n", first_gate=_forever))
    _session, context, lease = _install_session(monkeypatch, response)
    clock, scheduler = _virtual()

    task = scheduler.create_task(
        forwarding_module.stream_responses(
            _responses_source(), {"model": "m", "stream": True}, scheduler=scheduler, clock=clock
        )
    )
    await scheduler.advance(29.9)
    assert not task.done()

    await scheduler.advance(0.1)
    assert task.done()
    with pytest.raises(ModelSourceForwardingError) as excinfo:
        task.result()

    error = excinfo.value
    assert error.status_code == 504
    assert error.timeout_phase == "first_frame"
    assert cast(dict[str, object], error.payload["error"])["code"] == "model_source_timeout"
    # Headers arrived, so the response context was entered and is released.
    assert context.exited == 1
    assert lease.released == 1
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_open_source_stream_connect_timeout_stays_unreachable_with_connect_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse()
    _session, _context, lease = _install_session(
        monkeypatch, response, enter_error=aiohttp.ConnectionTimeoutError("Connection timeout to host")
    )

    with pytest.raises(ModelSourceForwardingError) as excinfo:
        await forwarding_module.stream_responses(_responses_source(), {"model": "m"})

    error = excinfo.value
    assert error.status_code == 502
    assert error.timeout_phase == "connect"
    assert cast(dict[str, object], error.payload["error"])["code"] == "model_source_unreachable"
    assert lease.released == 1


@pytest.mark.asyncio
async def test_open_source_stream_transport_error_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_session(monkeypatch, _FakeResponse(), enter_error=aiohttp.ClientOSError(111, "connection refused"))

    with pytest.raises(ModelSourceForwardingError) as excinfo:
        await forwarding_module.stream_responses(_responses_source(), {"model": "m"})

    assert excinfo.value.status_code == 502
    assert excinfo.value.timeout_phase is None
    assert cast(dict[str, object], excinfo.value.payload["error"])["code"] == "model_source_unreachable"


@pytest.mark.asyncio
async def test_open_source_stream_empty_first_chunk_is_invalid_upstream_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse(content=_FakeContent(b""))
    _session, context, lease = _install_session(monkeypatch, response)

    with pytest.raises(ModelSourceForwardingError) as excinfo:
        await forwarding_module.stream_responses(_responses_source(), {"model": "m"})

    error = excinfo.value
    assert error.status_code == 502
    assert error.upstream_status_code == 200
    assert cast(dict[str, object], error.payload["error"])["code"] == "invalid_upstream_response"
    assert context.exited == 1
    assert lease.released == 1


@pytest.mark.asyncio
async def test_open_source_stream_arms_real_anyio_deadlines_around_headers_and_first_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under the production scheduler the two phases run inside real ``anyio.fail_after`` scopes."""

    observed: dict[str, float] = {}

    def record_header_deadline() -> None:
        observed["header"] = anyio.current_effective_deadline() - anyio.current_time()

    async def record_first_frame_deadline() -> None:
        observed["first_frame"] = anyio.current_effective_deadline() - anyio.current_time()

    response = _FakeResponse(content=_FakeContent(b"data: x\n\n", first_gate=record_first_frame_deadline))
    _install_session(monkeypatch, response, on_enter=record_header_deadline)

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"})
    await stream.aclose()

    assert observed["header"] == pytest.approx(SOURCE_HEADER_DEADLINE_SECONDS, abs=1.0)
    assert observed["first_frame"] == pytest.approx(SOURCE_FIRST_FRAME_DEADLINE_SECONDS, abs=1.0)


@pytest.mark.asyncio
async def test_cancelling_the_open_during_the_first_frame_wait_releases_the_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse(content=_FakeContent(b"data: late\n\n", first_gate=_forever))
    _session, context, lease = _install_session(monkeypatch, response)
    clock, scheduler = _virtual()

    task = scheduler.create_task(
        forwarding_module.stream_responses(
            _responses_source(), {"model": "m", "stream": True}, scheduler=scheduler, clock=clock
        )
    )
    await scheduler.drain()
    assert response.content.readany_calls == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert context.exited == 1
    assert lease.released == 1
    await scheduler.cancel_owned_tasks()


# -- stream body: idle timeout, hook, close latch --------------------------------


@pytest.mark.parametrize(
    ("configured_idle", "expected_idle"),
    [(7200.0, 300.0), (30.0, 30.0)],
)
@pytest.mark.asyncio
async def test_stream_body_idle_cap_expires_in_virtual_time_never_after_7200(
    monkeypatch: pytest.MonkeyPatch, configured_idle: float, expected_idle: float
) -> None:
    created = _sse({"type": "response.created", "response": {"id": "resp_idle"}})
    response = _FakeResponse(content=_FakeContent(created, stall_after_rest=True))
    _session, context, lease = _install_session(monkeypatch, response)
    monkeypatch.setattr(
        forwarding_module, "get_settings", lambda: SimpleNamespace(stream_idle_timeout_seconds=configured_idle)
    )
    clock, scheduler = _virtual()

    stream = await forwarding_module.stream_responses(
        _responses_source(), {"model": "m"}, scheduler=scheduler, clock=clock
    )
    chunks: list[bytes] = []

    async def consume() -> None:
        async for chunk in stream.body:
            chunks.append(chunk)

    task = scheduler.create_task(consume())
    await scheduler.drain()
    assert chunks == [created]

    await scheduler.advance(expected_idle - 0.1)
    assert not task.done()

    await scheduler.advance(0.1)
    assert task.done()
    with pytest.raises(ModelSourceForwardingError) as excinfo:
        task.result()

    error = excinfo.value
    assert error.status_code == 504
    assert error.timeout_phase == "idle"
    assert cast(dict[str, object], error.payload["error"])["code"] == "model_source_idle_timeout"
    assert context.exited == 1
    assert lease.released == 1
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_low_idle_window_does_not_shorten_the_header_or_first_frame_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 5 s idle window bounds silence *after* the first frame only (codex review P2)."""

    first_frame_gate = asyncio.Event()

    async def wait_first_frame() -> None:
        await first_frame_gate.wait()

    response = _FakeResponse(content=_FakeContent(b"data: late\n\n", first_gate=wait_first_frame))
    headers_gate = asyncio.Event()
    _session, _context, lease = _install_session(monkeypatch, response, enter_gate=headers_gate.wait)
    monkeypatch.setattr(forwarding_module, "get_settings", lambda: SimpleNamespace(stream_idle_timeout_seconds=5.0))
    clock, scheduler = _virtual()

    task = scheduler.create_task(
        forwarding_module.stream_responses(_responses_source(), {"model": "m"}, scheduler=scheduler, clock=clock)
    )
    # Headers take 15 s (> idle, < header deadline): still waiting, not failed.
    await scheduler.advance(15.0)
    assert not task.done()
    headers_gate.set()
    await scheduler.drain()
    # The first frame takes another 25 s (> idle, < first-frame deadline).
    await scheduler.advance(25.0)
    assert not task.done()
    first_frame_gate.set()
    await scheduler.drain()

    stream = task.result()
    assert stream.usage_holder.first_frame_at == 40.0
    await stream.aclose()
    assert lease.released == 1
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_stream_body_lets_the_source_total_budget_expiry_propagate_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """aiohttp's ``total`` timer mid-stream is not the idle cap: today's raw propagation is kept."""

    budget_expired = TimeoutError("total budget")
    response = _FakeResponse(content=_FakeContent(b"data: a\n\n", [budget_expired]))
    _session, _context, lease = _install_session(monkeypatch, response)

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"})
    with pytest.raises(TimeoutError) as excinfo:
        await _collect(stream.body)

    assert excinfo.value is budget_expired
    assert not isinstance(excinfo.value, ModelSourceForwardingError)
    assert lease.released == 1


@pytest.mark.asyncio
async def test_stream_body_lets_non_timeout_transport_errors_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeResponse(
        content=_FakeContent(b"data: a\n\n", [aiohttp.ClientPayloadError("Response payload is not completed")])
    )
    _session, _context, lease = _install_session(monkeypatch, response)

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"})
    with pytest.raises(aiohttp.ClientPayloadError):
        await _collect(stream.body)

    assert lease.released == 1


@pytest.mark.asyncio
async def test_stream_body_withholds_bookkeeping_frames_until_the_hook_ran(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _sse({"type": "response.created", "response": {"id": "resp_hook"}})
    in_progress = _sse({"type": "response.in_progress", "response": {"id": "resp_hook"}})
    keepalive = b": keepalive\n\n"
    content = _sse({"type": "response.output_item.added", "item": {"type": "message"}}) + _sse(
        {"type": "response.output_text.delta", "delta": "Hi"}
    )
    completed = _sse({"type": "response.completed", "response": {"id": "resp_hook"}})
    response = _FakeResponse(content=_FakeContent(created, [in_progress, keepalive, content, completed]))
    _session, _context, lease = _install_session(monkeypatch, response)
    order: list[str] = []

    async def hook(holder: SourceUsageHolder) -> None:
        order.append(f"hook:first_output_item={holder.first_output_item_seen}:delta_chars={holder.delta_chars}")

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)
    delivered: list[bytes] = []
    async for chunk in stream.body:
        if not order or not order[-1].startswith("yield"):
            order.append("yield")
        delivered.append(chunk)

    # Nothing reached the consumer before the hook; afterwards every withheld
    # chunk is flushed in arrival order and the stream continues live.
    assert order == ["hook:first_output_item=True:delta_chars=2", "yield"]
    assert delivered == [created, in_progress, keepalive, content, completed]
    assert lease.released == 1


@pytest.mark.asyncio
async def test_stream_body_hook_runs_once_and_later_chunks_stream_live(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _sse({"type": "response.output_text.delta", "delta": "a"})
    second = _sse({"type": "response.output_text.delta", "delta": "b"})
    response = _FakeResponse(content=_FakeContent(first, [second], stall_after_rest=True))
    _install_session(monkeypatch, response)
    calls = 0

    async def hook(_holder: SourceUsageHolder) -> None:
        nonlocal calls
        calls += 1

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)
    assert await asyncio.wait_for(anext(stream.body), timeout=1) == first
    assert await asyncio.wait_for(anext(stream.body), timeout=1) == second
    assert calls == 1
    await stream.aclose()


@pytest.mark.asyncio
async def test_stream_body_success_terminal_without_output_triggers_the_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _sse({"type": "response.created", "response": {"id": "resp_empty"}})
    completed = _sse({"type": "response.completed", "response": {"id": "resp_empty", "output": []}})
    response = _FakeResponse(content=_FakeContent(created, [completed]))
    _install_session(monkeypatch, response)
    hooked: list[str | None] = []

    async def hook(holder: SourceUsageHolder) -> None:
        hooked.append(holder.terminal_kind)

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)

    assert await _collect(stream.body) == [created, completed]
    assert hooked == ["completed"]


@pytest.mark.asyncio
async def test_stream_body_crlf_split_across_chunks_runs_the_hook_before_flushing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CRLF-framed success terminal whose line ending is cut between two aiohttp chunks is one frame to the
    reassembler that delivers it, so it must be one frame to the parser that arms the hook."""

    created = b'data: {"type":"response.created","response":{"id":"resp_crlf"}}\r\n\r\n'
    head = b'data: {"type":"response.completed",\r'
    tail = b'\ndata: "response":{"id":"resp_crlf","output":[],"usage":{"input_tokens":3,"output_tokens":2}}}\r\n\r\n'
    response = _FakeResponse(content=_FakeContent(created, [head, tail]))
    _session, _context, lease = _install_session(monkeypatch, response)
    hooked: list[str | None] = []

    async def hook(holder: SourceUsageHolder) -> None:
        hooked.append(holder.terminal_kind)

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)

    assert b"".join(await _collect(stream.body)) == created + head + tail
    assert hooked == ["completed"]
    assert stream.usage_holder.usage is not None
    assert stream.usage_holder.usage.input_tokens == 3
    assert lease.released == 1


@pytest.mark.asyncio
async def test_stream_body_failure_terminal_without_content_flushes_without_the_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _sse({"type": "response.created", "response": {"id": "resp_fail"}})
    failed = _sse({"type": "response.failed", "response": {"id": "resp_fail", "error": {"code": "x"}}})
    response = _FakeResponse(content=_FakeContent(created, [failed]))
    _session, _context, lease = _install_session(monkeypatch, response)
    hook_calls = 0

    async def hook(_holder: SourceUsageHolder) -> None:
        nonlocal hook_calls
        hook_calls += 1

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)

    assert await _collect(stream.body) == [created, failed]
    assert hook_calls == 0
    assert stream.usage_holder.terminal_kind == "failed"
    assert stream.usage_holder.first_content_seen is False
    assert lease.released == 1


@pytest.mark.asyncio
async def test_stream_body_typeless_error_record_flushes_without_the_hook_before_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typeless ``{"error": {...}}`` record is the failure terminal: the withheld frames flush at once (not at
    EOF) and the hook never runs."""

    created = _sse({"type": "response.created", "response": {"id": "resp_typeless"}})
    failed = _sse({"error": {"message": "boom", "type": "server_error", "code": "overloaded"}})
    response = _FakeResponse(content=_FakeContent(created, [failed], stall_after_rest=True))
    _session, _context, lease = _install_session(monkeypatch, response)
    hook_calls = 0

    async def hook(_holder: SourceUsageHolder) -> None:
        nonlocal hook_calls
        hook_calls += 1

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)

    assert await asyncio.wait_for(anext(stream.body), timeout=1) == created
    assert await asyncio.wait_for(anext(stream.body), timeout=1) == failed
    assert hook_calls == 0
    assert stream.usage_holder.terminal_kind == "error"
    assert stream.usage_holder.first_content_seen is False
    await stream.aclose()
    assert lease.released == 1


@pytest.mark.asyncio
async def test_stream_body_eof_while_withholding_flushes_without_the_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _sse({"type": "response.created", "response": {"id": "resp_eof"}})
    response = _FakeResponse(content=_FakeContent(created, []))
    _install_session(monkeypatch, response)
    hook_calls = 0

    async def hook(_holder: SourceUsageHolder) -> None:
        nonlocal hook_calls
        hook_calls += 1

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)

    assert await _collect(stream.body) == [created]
    assert hook_calls == 0


@pytest.mark.asyncio
async def test_stream_body_hook_failure_drops_withheld_frames_and_releases_the_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _PinFailed(Exception):
        pass

    created = _sse({"type": "response.created", "response": {"id": "resp_pin"}})
    delta = _sse({"type": "response.output_text.delta", "delta": "secret"})
    response = _FakeResponse(content=_FakeContent(created, [delta, _sse({"type": "response.completed"})]))
    _session, context, lease = _install_session(monkeypatch, response)

    async def hook(_holder: SourceUsageHolder) -> None:
        raise _PinFailed("not_written")

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)
    delivered: list[bytes] = []
    with pytest.raises(_PinFailed):
        async for chunk in stream.body:
            delivered.append(chunk)

    assert delivered == []
    assert context.exited == 1
    assert lease.released == 1


@pytest.mark.asyncio
async def test_stream_body_without_hook_never_withholds(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _sse({"type": "response.created", "response": {"id": "resp_live"}})
    response = _FakeResponse(content=_FakeContent(created, stall_after_rest=True))
    _install_session(monkeypatch, response)

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"})

    # A bookkeeping frame is delivered immediately when nothing is armed.
    assert await asyncio.wait_for(anext(stream.body), timeout=1) == created
    await stream.aclose()


@pytest.mark.asyncio
async def test_stream_aclose_before_iteration_releases_lease_and_response_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse(content=_FakeContent(b"data: x\n\n", stall_after_rest=True))
    _session, context, lease = _install_session(monkeypatch, response)

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"})
    assert isinstance(stream, SourceResponsesStream)

    await stream.aclose()
    await stream.aclose()

    assert context.exited == 1
    assert lease.released == 1
    # Closing the never-started body afterwards is a no-op for the transport.
    await cast(AsyncGenerator[bytes, None], stream.body).aclose()
    assert lease.released == 1


@pytest.mark.asyncio
async def test_stream_body_finally_and_direct_aclose_converge_on_one_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse(content=_FakeContent(b"data: x\n\n", stall_after_rest=True))
    _session, context, lease = _install_session(monkeypatch, response)

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"})
    assert await asyncio.wait_for(anext(stream.body), timeout=1) == b"data: x\n\n"
    body = cast(AsyncGenerator[bytes, None], stream.body)

    await asyncio.gather(stream.aclose(), body.aclose())

    assert context.exited == 1
    assert lease.released == 1


def test_synthetic_source_responses_stream_aclose_is_a_no_op() -> None:
    async def body() -> AsyncIterator[bytes]:
        yield b""

    stream = SourceResponsesStream(body=body(), usage_holder=SourceUsageHolder(), upstream_status_code=200)
    assert stream.transport is None
    asyncio.run(stream.aclose())


@pytest.mark.asyncio
async def test_stream_chat_completion_aclose_before_iteration_releases_lease_and_response_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chat stream owns its transport like the Responses stream: a body that never starts (the client left
    between the route returning and the response start) is released by ``aclose()`` instead of garbage collection."""

    response = _FakeResponse(content=_FakeContent(b"data: x\n\n", first_gate=_forever))
    _session, context, lease = _install_session(monkeypatch, response)

    stream = await forwarding_module.stream_chat_completion(_responses_source(), {"model": "m"})
    assert isinstance(stream, SourceChatStream)
    assert stream.transport is not None
    assert context.exited == 0 and lease.released == 0

    await stream.aclose()
    await stream.aclose()

    assert context.exited == 1
    assert lease.released == 1
    # Closing the never-started body afterwards is a no-op for the transport.
    await cast(AsyncGenerator[bytes, None], stream.body).aclose()
    assert context.exited == 1
    assert lease.released == 1


def test_synthetic_source_chat_stream_aclose_is_a_no_op() -> None:
    async def body() -> AsyncIterator[bytes]:
        yield b""

    stream = SourceChatStream(body=body(), usage_holder=SourceUsageHolder(), upstream_status_code=200)
    assert stream.transport is None
    asyncio.run(stream.aclose())


@pytest.mark.parametrize(
    ("label", "error"),
    [
        ("idle_timeout", forwarding_module._idle_timeout_error(300.0)),
        ("unreachable", forwarding_module._unreachable_error(aiohttp.ClientPayloadError("mid-body"))),
        ("withheld_cap", forwarding_module._withheld_cap_error(forwarding_module.SOURCE_STREAM_WITHHELD_CAP_BYTES + 1)),
        ("empty_stream", forwarding_module.empty_stream_error(200)),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_body_phase_errors_carry_no_retry_after(label: str, error: ModelSourceForwardingError) -> None:
    """Every ``ModelSourceForwardingError`` a started body can raise is post-open: a source ``Retry-After`` rides a
    pre-open 4xx/5xx that the open site answers, so handlers that only consume a started body (the buffered
    limited-key chat handler) have no header to merge. Adding ``retry_after`` to one of these means revisiting them."""

    assert error.retry_after is None, label


# -- status passthrough and credential recode -----------------------------------


@pytest.mark.asyncio
async def test_stream_responses_passes_429_through_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"error": {"message": "slow down", "type": "rate_limit_error", "code": "rate_limit_exceeded"}}
    response = _FakeResponse(status=429, headers={"Retry-After": " 7 "}, json_body=payload)
    _session, context, lease = _install_session(monkeypatch, response)

    with pytest.raises(ModelSourceForwardingError) as excinfo:
        await forwarding_module.stream_responses(
            _responses_source(api_key=True), {"model": "m"}, encryptor=_fake_encryptor()
        )

    error = excinfo.value
    assert error.status_code == 429
    assert error.upstream_status_code == 429
    assert error.retry_after == "7"
    assert error.timeout_phase is None
    assert error.payload == payload
    assert response.json_calls == 1
    assert context.exited == 1
    assert lease.released == 1


@pytest.mark.asyncio
async def test_stream_responses_passes_404_through_without_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"error": {"message": "no such model", "type": "invalid_request_error", "code": "model_not_found"}}
    response = _FakeResponse(status=404, json_body=payload)
    _install_session(monkeypatch, response)

    with pytest.raises(ModelSourceForwardingError) as excinfo:
        await forwarding_module.stream_responses(
            _responses_source(api_key=True), {"model": "m"}, encryptor=_fake_encryptor()
        )

    assert excinfo.value.status_code == 404
    assert excinfo.value.retry_after is None
    assert excinfo.value.payload == payload


@pytest.mark.asyncio
async def test_stream_responses_5xx_error_body_read_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeResponse(
        status=503, headers={"Retry-After": "3"}, json_body={"error": {"message": "late"}}, json_gate=_forever
    )
    _session, _context, lease = _install_session(monkeypatch, response)
    clock, scheduler = _virtual()

    task = scheduler.create_task(
        forwarding_module.stream_responses(
            _responses_source(api_key=True),
            {"model": "m"},
            encryptor=_fake_encryptor(),
            scheduler=scheduler,
            clock=clock,
        )
    )
    await scheduler.advance(SOURCE_FIRST_FRAME_DEADLINE_SECONDS)

    with pytest.raises(ModelSourceForwardingError) as excinfo:
        task.result()
    error = excinfo.value
    # Honest status and Retry-After survive; the unreadable body degrades to the generic envelope.
    assert error.status_code == 503
    assert error.retry_after == "3"
    assert cast(dict[str, object], error.payload["error"])["code"] == "model_source_error"
    assert lease.released == 1
    await scheduler.cancel_owned_tasks()


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.asyncio
async def test_stream_responses_recodes_credential_rejection_without_reading_or_logging_the_body(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, status: int
) -> None:
    masked_key = "sk-live-****************ken"
    payload = {
        "error": {
            "message": f"Incorrect API key provided: {masked_key}. Authorization: Bearer source-secret-token",
            "type": "invalid_request_error",
            "code": "invalid_api_key",
        }
    }
    response = _FakeResponse(status=status, headers={"Retry-After": "1"}, json_body=payload)
    _session, context, lease = _install_session(monkeypatch, response)
    caplog.set_level(logging.INFO, logger=_FORWARDING_LOGGER)

    with pytest.raises(ModelSourceForwardingError) as excinfo:
        await forwarding_module.stream_responses(
            _responses_source(api_key=True, source_id="src_reject"), {"model": "m"}, encryptor=_fake_encryptor()
        )

    error = excinfo.value
    assert error.status_code == 502
    assert error.upstream_status_code == status
    assert error.retry_after == "1"
    assert error.payload == {
        "error": {
            "message": "OpenAI-compatible model source rejected the proxy's credentials",
            "type": "upstream_error",
            "code": "model_source_credentials_error",
        }
    }
    serialized = json.dumps(error.payload)
    for fragment in (masked_key, "source-secret-token", "Incorrect API key", "invalid_api_key"):
        assert fragment not in serialized
        assert all(fragment not in record.getMessage() for record in caplog.records)
    assert response.json_calls == 0
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "src_reject" in warnings[0].getMessage()
    assert str(status) in warnings[0].getMessage()
    assert context.exited == 1
    assert lease.released == 1


@pytest.mark.asyncio
async def test_stream_responses_recode_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"error": {"message": "bad key", "type": "invalid_request_error", "code": "invalid_api_key"}}
    _install_session(monkeypatch, _FakeResponse(status=401, json_body=payload))

    with pytest.raises(ModelSourceForwardingError) as excinfo:
        await forwarding_module.stream_responses(
            _responses_source(api_key=True),
            {"model": "m"},
            encryptor=_fake_encryptor(),
            recode_credential_failures=False,
        )

    assert excinfo.value.status_code == 401
    assert excinfo.value.payload == payload


@pytest.mark.asyncio
async def test_stream_chat_completion_keeps_the_source_401_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"error": {"message": "bad key", "type": "invalid_request_error", "code": "invalid_api_key"}}
    response = _FakeResponse(status=401, json_body=payload)
    session, _context, _lease = _install_session(monkeypatch, response)

    with pytest.raises(ModelSourceForwardingError) as excinfo:
        await forwarding_module.stream_chat_completion(
            _responses_source(api_key=True), {"model": "m"}, encryptor=_fake_encryptor()
        )

    assert excinfo.value.status_code == 401
    assert excinfo.value.payload == payload
    assert response.json_calls == 1
    # Chat streams share the hardened open: same connect bounds, no socket read timer.
    timeout = cast(aiohttp.ClientTimeout, session.calls[0]["timeout"])
    assert timeout.connect is None
    assert timeout.sock_connect == SOURCE_CONNECT_DEADLINE_SECONDS
    assert timeout.sock_read is None


@pytest.mark.asyncio
async def test_stream_chat_completion_returns_at_the_headers_and_reads_the_first_token_in_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chat open completes at the source's headers, as on ``main``: the first token is the body's first read."""

    first_token = asyncio.Event()
    first = b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
    response = _FakeResponse(content=_FakeContent(first, first_gate=first_token.wait, stall_after_rest=True))
    _session, _context, lease = _install_session(monkeypatch, response)

    stream = await asyncio.wait_for(
        forwarding_module.stream_chat_completion(_responses_source(), {"model": "m"}), timeout=1
    )
    assert response.content.readany_calls == 0
    assert stream.usage_holder.first_frame_at is None

    first_read = asyncio.ensure_future(anext(stream.body))
    await asyncio.sleep(0)
    assert not first_read.done()
    first_token.set()
    assert await asyncio.wait_for(first_read, timeout=1) == first
    assert stream.usage_holder.first_frame_at is not None

    await cast(AsyncGenerator[bytes, None], stream.body).aclose()
    assert lease.released == 1


@pytest.mark.asyncio
async def test_stream_chat_completion_cancelled_during_prompt_processing_releases_the_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client that leaves while the source is still processing the prompt frees the connection and the lease."""

    response = _FakeResponse(content=_FakeContent(b"never", first_gate=_forever))
    _session, context, lease = _install_session(monkeypatch, response)

    stream = await asyncio.wait_for(
        forwarding_module.stream_chat_completion(_responses_source(), {"model": "m"}), timeout=1
    )
    first_read = asyncio.ensure_future(anext(stream.body))
    await asyncio.sleep(0)
    assert not first_read.done()
    first_read.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_read

    assert context.exited == 1
    assert lease.released == 1
    assert stream.usage_holder.first_frame_at is None


@pytest.mark.asyncio
async def test_stream_chat_completion_empty_2xx_stream_ends_the_body_cleanly_without_a_first_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decision 50: the client already holds the ``200`` at the source's headers, so a source that closes before its
    first chunk ends the body as the clean empty stream ``main`` relayed (no exception out of a started body); the
    stream owner reads the empty-stream verdict from ``first_frame_at`` staying ``None``."""

    response = _FakeResponse(status=200, content=_FakeContent(b""))
    _session, context, lease = _install_session(monkeypatch, response)

    stream = await forwarding_module.stream_chat_completion(_responses_source(), {"model": "m"})
    delivered = [chunk async for chunk in stream.body]

    assert delivered == []
    assert stream.usage_holder.first_frame_at is None
    assert stream.usage_holder.usage is None
    assert context.exited == 1
    assert lease.released == 1


@pytest.mark.asyncio
async def test_open_source_stream_without_a_first_frame_deadline_does_not_read_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse(content=_FakeContent(b"data: late\n\n", first_gate=_forever))
    _session, _context, lease = _install_session(monkeypatch, response)

    stack, opened, first_chunk = await asyncio.wait_for(
        forwarding_module._open_source_stream(
            _responses_source(),
            "/chat/completions",
            {"model": "m"},
            encryptor=None,
            header_deadline_seconds=None,
            first_frame_deadline_seconds=None,
        ),
        timeout=1,
    )

    assert opened is response
    assert first_chunk is None
    assert response.content.readany_calls == 0
    await stack.aclose()
    assert lease.released == 1


# -- non-stream forward ---------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_responses_bounds_connect_and_total_only_and_survives_a_long_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock, scheduler = _virtual()
    observed: dict[str, float] = {}

    async def slow_generation() -> None:
        observed["effective_deadline"] = anyio.current_effective_deadline()
        await scheduler.sleep(20 * 60)

    body = {"id": "resp_long", "output": [], "usage": {"input_tokens": 1, "output_tokens": 1}}
    response = _FakeResponse(json_body=body)
    session, context, lease = _install_session(monkeypatch, response, enter_gate=slow_generation)

    task = scheduler.create_task(
        forwarding_module.forward_responses(_responses_source(timeout_seconds=1800), {"model": "m"})
    )
    await scheduler.advance(20 * 60)

    result = task.result()
    assert result.payload == body
    assert result.upstream_status_code == 200
    # No anyio header/first-frame scope wraps a non-stream forward.
    assert observed["effective_deadline"] == math.inf
    timeout = cast(aiohttp.ClientTimeout, session.calls[0]["timeout"])
    assert timeout.total == 1800.0
    # ``connect`` is not armed: it also bounds the pooled-connection wait (codex review P2).
    assert timeout.connect is None
    assert timeout.sock_connect == SOURCE_CONNECT_DEADLINE_SECONDS
    assert timeout.sock_read is None
    assert cast(dict[str, str], session.calls[0]["headers"])["Accept"] == "application/json"
    assert context.exited == 1
    assert lease.released == 1
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_forward_responses_recodes_401_and_never_reads_the_body(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    response = _FakeResponse(
        status=401,
        json_body={"error": {"message": "Incorrect API key provided: sk-****abcd", "code": "invalid_api_key"}},
    )
    _install_session(monkeypatch, response)
    caplog.set_level(logging.INFO, logger=_FORWARDING_LOGGER)

    with pytest.raises(ModelSourceForwardingError) as excinfo:
        await forwarding_module.forward_responses(
            _responses_source(api_key=True), {"model": "m"}, encryptor=_fake_encryptor()
        )

    assert excinfo.value.status_code == 502
    assert excinfo.value.upstream_status_code == 401
    assert cast(dict[str, object], excinfo.value.payload["error"])["code"] == "model_source_credentials_error"
    assert response.json_calls == 0
    assert all("sk-****abcd" not in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_forward_responses_passes_429_through_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"error": {"message": "slow down", "type": "rate_limit_error", "code": "rate_limit_exceeded"}}
    _install_session(monkeypatch, _FakeResponse(status=429, headers={"Retry-After": "12"}, json_body=payload))

    with pytest.raises(ModelSourceForwardingError) as excinfo:
        await forwarding_module.forward_responses(
            _responses_source(api_key=True), {"model": "m"}, encryptor=_fake_encryptor()
        )

    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after == "12"
    assert excinfo.value.payload == payload


@pytest.mark.asyncio
async def test_forward_responses_recode_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"error": {"message": "bad key", "type": "invalid_request_error", "code": "invalid_api_key"}}
    _install_session(monkeypatch, _FakeResponse(status=403, json_body=payload))

    with pytest.raises(ModelSourceForwardingError) as excinfo:
        await forwarding_module.forward_responses(
            _responses_source(api_key=True),
            {"model": "m"},
            encryptor=_fake_encryptor(),
            recode_credential_failures=False,
        )

    assert excinfo.value.status_code == 403
    assert excinfo.value.payload == payload


@pytest.mark.asyncio
async def test_forward_chat_completion_uses_the_dedicated_session_with_connect_deadlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {"id": "chatcmpl_1", "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    session, _context, lease = _install_session(monkeypatch, _FakeResponse(json_body=body))

    result = await forwarding_module.forward_chat_completion(_responses_source(), {"model": "m"})

    assert result.payload == body
    timeout = cast(aiohttp.ClientTimeout, session.calls[0]["timeout"])
    assert timeout.connect is None
    assert timeout.sock_connect == SOURCE_CONNECT_DEADLINE_SECONDS
    assert timeout.sock_read is None
    assert lease.released == 1


@pytest.mark.asyncio
async def test_forward_chat_completion_keeps_401_passthrough_and_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"error": {"message": "bad key", "type": "invalid_request_error", "code": "invalid_api_key"}}
    _install_session(monkeypatch, _FakeResponse(status=401, headers={"Retry-After": "2"}, json_body=payload))

    with pytest.raises(ModelSourceForwardingError) as excinfo:
        await forwarding_module.forward_chat_completion(
            _responses_source(api_key=True), {"model": "m"}, encryptor=_fake_encryptor()
        )

    assert excinfo.value.status_code == 401
    assert excinfo.value.retry_after == "2"
    assert excinfo.value.payload == payload


# -- oversized frames (buffer cap) and the pre-content hook ----------------------------------------------------

_OVERSIZED_CHUNK_BYTES = 65536


def _oversized_completed_frame(response_id: str) -> bytes:
    """A single ``response.completed`` frame larger than the parser's buffer cap (its remainder never parses).

    Several read chunks larger than the cap: the parser truncates the buffer at
    the end of a feed that leaves the frame incomplete, so the frame boundary
    eventually closes a remainder that no longer starts with ``data:``.
    """

    text = "x" * (SourceStreamUsageParser._MAX_BUFFER_CHARS + 4 * _OVERSIZED_CHUNK_BYTES)
    return _sse(
        {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
                "usage": {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
            },
        }
    )


def _chunked(data: bytes, size: int = _OVERSIZED_CHUNK_BYTES) -> list[bytes]:
    return [data[offset : offset + size] for offset in range(0, len(data), size)]


def test_stream_usage_parser_counts_an_oversized_responses_frame_as_content() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")
    parser.feed(_sse({"type": "response.created", "response": {"id": "resp_big"}}))
    assert holder.first_content_seen is False

    for chunk in _chunked(_oversized_completed_frame("resp_big")):
        parser.feed(chunk)

    # The truncated remainder cannot be classified by its event type, so the
    # frame is content by construction (no bookkeeping envelope is this large).
    assert holder.first_content_seen is True
    assert len(parser._buffer) <= SourceStreamUsageParser._MAX_BUFFER_CHARS


def test_stream_usage_parser_oversized_chat_frames_do_not_touch_responses_observations() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")

    for _ in range(300):
        parser.feed(b"y" * 4096)

    assert holder.first_content_seen is False


@pytest.mark.asyncio
async def test_stream_body_oversized_terminal_frame_runs_the_hook_before_flushing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _sse({"type": "response.created", "response": {"id": "resp_big"}})
    completed = _oversized_completed_frame("resp_big")
    rest = cast(list[bytes | BaseException], _chunked(completed))
    response = _FakeResponse(content=_FakeContent(created, rest))
    _session, _context, lease = _install_session(monkeypatch, response)
    order: list[str] = []

    async def hook(holder: SourceUsageHolder) -> None:
        order.append(f"hook:first_content_seen={holder.first_content_seen}")

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)
    delivered: list[bytes] = []
    async for chunk in stream.body:
        if not order or not order[-1].startswith("yield"):
            order.append("yield")
        delivered.append(chunk)

    # Delivered => pinned (I11): the hook ran before the first byte of the
    # oversized frame was released, and the withheld bytes were flushed in order.
    assert order == ["hook:first_content_seen=True", "yield"]
    assert b"".join(delivered) == created + completed
    assert stream.usage_holder.first_content_seen is True
    assert lease.released == 1


@pytest.mark.asyncio
async def test_stream_body_marks_content_delivered_at_the_flush_point_without_a_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _sse({"type": "response.created", "response": {"id": "resp_deliver"}})
    delta = _sse({"type": "response.output_text.delta", "delta": "Hi"})
    response = _FakeResponse(content=_FakeContent(created, [delta], stall_after_rest=True))
    _install_session(monkeypatch, response)

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"})
    holder = stream.usage_holder
    assert await asyncio.wait_for(anext(stream.body), timeout=1) == created
    # Bookkeeping only: nothing that counts as content has reached the consumer.
    assert holder.first_content_seen is False
    assert holder.content_delivered is False
    assert await asyncio.wait_for(anext(stream.body), timeout=1) == delta
    assert holder.first_content_seen is True
    assert holder.first_output_item_seen is False
    assert holder.content_delivered is True
    await stream.aclose()


@pytest.mark.asyncio
async def test_stream_body_hook_interrupted_by_cancellation_leaves_content_undelivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parser observed ``response.output_item.added`` on the withheld chunk; the consumer is cancelled while the
    pin hook is still in flight, so no byte reached the client and the holder must say so."""

    created = _sse({"type": "response.created", "response": {"id": "resp_pin"}})
    item = _sse({"type": "response.output_item.added", "item": {"type": "message"}})
    response = _FakeResponse(content=_FakeContent(created, [item], stall_after_rest=True))
    _session, _context, lease = _install_session(monkeypatch, response)
    hook_entered = asyncio.Event()
    hook_gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def hook(_holder: SourceUsageHolder) -> None:
        hook_entered.set()
        await hook_gate

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)
    holder = stream.usage_holder
    delivered: list[bytes] = []

    async def consume() -> None:
        async for chunk in stream.body:
            delivered.append(chunk)

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(hook_entered.wait(), timeout=1)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert delivered == []
    assert holder.first_output_item_seen is True
    assert holder.first_content_seen is True
    assert holder.content_delivered is False
    assert lease.released == 1


@pytest.mark.asyncio
async def test_stream_body_marks_content_delivered_when_the_hook_flush_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _sse({"type": "response.created", "response": {"id": "resp_flush"}})
    item = _sse({"type": "response.output_item.added", "item": {"type": "message"}})
    response = _FakeResponse(content=_FakeContent(created, [item], stall_after_rest=True))
    _install_session(monkeypatch, response)
    observed: list[bool] = []

    async def hook(holder: SourceUsageHolder) -> None:
        observed.append(holder.content_delivered)

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)
    holder = stream.usage_holder
    assert await asyncio.wait_for(anext(stream.body), timeout=1) == created
    assert observed == [False], "the hook runs before anything is flushed"
    assert holder.content_delivered is True
    assert await asyncio.wait_for(anext(stream.body), timeout=1) == item
    await stream.aclose()


@pytest.mark.asyncio
async def test_stream_body_failure_terminal_flush_delivers_no_content(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _sse({"type": "response.created", "response": {"id": "resp_failed"}})
    failed = _sse({"type": "response.failed", "response": {"id": "resp_failed", "error": {"code": "x"}}})
    response = _FakeResponse(content=_FakeContent(created, [failed]))
    _install_session(monkeypatch, response)

    async def hook(_holder: SourceUsageHolder) -> None:
        raise AssertionError("a failure terminal never runs the hook")

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)

    assert await _collect(stream.body) == [created, failed]
    assert stream.usage_holder.content_delivered is False


@pytest.mark.asyncio
async def test_stream_body_eof_tail_with_content_marks_content_delivered_after_the_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _sse({"type": "response.created", "response": {"id": "resp_tail_deliver"}})
    tail = f"data: {json.dumps(_bom_completed_event('resp_tail_deliver'))}\n".encode()
    response = _FakeResponse(content=_FakeContent(created, [tail]))
    _install_session(monkeypatch, response)
    observed: list[bool] = []

    async def hook(holder: SourceUsageHolder) -> None:
        observed.append(holder.content_delivered)

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)

    assert await _collect(stream.body) == [created, tail]
    assert observed == [False]
    assert stream.usage_holder.content_delivered is True


# -- EOF tail classification and the withheld-bytes cap (pre-content hook) -----------------------------------------


def _unterminated(event: dict[str, object]) -> bytes:
    """A final SSE record the source closed with a single newline (no blank-line terminator)."""

    return f"data: {json.dumps(event)}\n".encode()


def _completed_with_output(response_id: str) -> dict[str, object]:
    return {
        "type": "response.completed",
        "response": {
            "id": response_id,
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "hi"}]}],
            "usage": {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
        },
    }


def test_stream_usage_parser_finish_classifies_an_unterminated_tail() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")
    parser.feed(_sse({"type": "response.created", "response": {"id": "resp_tail"}}))
    parser.feed(_unterminated(_completed_with_output("resp_tail")))
    # Only blank-line terminated frames were parsed so far.
    assert holder.terminal_kind is None
    assert holder.first_content_seen is False
    assert holder.usage is None

    parser.finish()

    assert holder.terminal_kind == "completed"
    assert holder.first_content_seen is True
    assert holder.usage == SourceUsage(input_tokens=3, output_tokens=5)
    assert parser._buffer == ""
    parser.finish()
    assert holder.terminal_kind == "completed"


def test_stream_usage_parser_finish_ignores_a_cut_frame() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")
    parser.feed(b'data: {"type":"response.output_text.delta","delta":"ab')

    parser.finish()

    # A record cut mid-JSON is not a frame any client can parse: nothing observed.
    assert holder.first_content_seen is False
    assert holder.terminal_kind is None
    assert parser._buffer == ""


@pytest.mark.asyncio
async def test_stream_body_unterminated_eof_tail_runs_the_hook_before_flushing(monkeypatch: pytest.MonkeyPatch) -> None:
    """I11: a success terminal the source closed with a single newline is delivered content."""

    created = _sse({"type": "response.created", "response": {"id": "resp_tail"}})
    completed_tail = _unterminated(_completed_with_output("resp_tail"))
    response = _FakeResponse(content=_FakeContent(created, [completed_tail]))
    _session, _context, lease = _install_session(monkeypatch, response)
    order: list[str] = []

    async def hook(holder: SourceUsageHolder) -> None:
        order.append(f"hook:terminal={holder.terminal_kind}")

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)
    delivered: list[bytes] = []
    async for chunk in stream.body:
        if not order or not order[-1].startswith("yield"):
            order.append("yield")
        delivered.append(chunk)

    assert order == ["hook:terminal=completed", "yield"]
    assert delivered == [created, completed_tail]
    assert stream.usage_holder.first_content_seen is True
    assert stream.usage_holder.usage == SourceUsage(input_tokens=3, output_tokens=5)
    assert lease.released == 1


@pytest.mark.asyncio
async def test_stream_body_unterminated_failure_tail_flushes_without_the_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _sse({"type": "response.created", "response": {"id": "resp_tail"}})
    failed_tail = _unterminated({"type": "response.failed", "response": {"id": "resp_tail", "error": {"code": "x"}}})
    response = _FakeResponse(content=_FakeContent(created, [failed_tail]))
    _session, _context, lease = _install_session(monkeypatch, response)
    hook_calls = 0

    async def hook(_holder: SourceUsageHolder) -> None:
        nonlocal hook_calls
        hook_calls += 1

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)

    assert await _collect(stream.body) == [created, failed_tail]
    assert hook_calls == 0
    assert stream.usage_holder.terminal_kind == "failed"
    assert stream.usage_holder.first_content_seen is False
    assert lease.released == 1


@pytest.mark.asyncio
async def test_stream_body_eof_tail_observations_reach_the_holder_without_a_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _sse({"type": "response.created", "response": {"id": "resp_tail"}})
    completed_tail = _unterminated(_completed_with_output("resp_tail"))
    response = _FakeResponse(content=_FakeContent(created, [completed_tail]))
    _install_session(monkeypatch, response)

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"})

    assert await _collect(stream.body) == [created, completed_tail]
    assert stream.usage_holder.terminal_kind == "completed"
    assert stream.usage_holder.usage == SourceUsage(input_tokens=3, output_tokens=5)


@pytest.mark.asyncio
async def test_stream_body_withheld_bytes_are_capped_while_the_hook_is_armed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Decision 31: complete bookkeeping frames clear the parser buffer, so the withheld buffer needs its own bound."""

    monkeypatch.setattr(forwarding_module, "SOURCE_STREAM_WITHHELD_CAP_BYTES", 2048)
    created = _sse({"type": "response.created", "response": {"id": "resp_spam"}})
    in_progress = _sse({"type": "response.in_progress", "response": {"id": "resp_spam"}})
    rest = cast(list[bytes | BaseException], [in_progress] * 64)
    response = _FakeResponse(content=_FakeContent(created, rest, stall_after_rest=True))
    _session, context, lease = _install_session(monkeypatch, response)
    hook_calls = 0

    async def hook(_holder: SourceUsageHolder) -> None:
        nonlocal hook_calls
        hook_calls += 1

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)
    delivered: list[bytes] = []
    with pytest.raises(ModelSourceForwardingError) as excinfo:
        async for chunk in stream.body:
            delivered.append(chunk)

    assert delivered == []
    assert hook_calls == 0
    assert excinfo.value.status_code == 502
    assert cast(dict[str, object], excinfo.value.payload["error"])["code"] == "invalid_upstream_response"
    assert context.exited == 1
    assert lease.released == 1


@pytest.mark.asyncio
async def test_stream_body_without_a_hook_is_never_bounded_by_the_withheld_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(forwarding_module, "SOURCE_STREAM_WITHHELD_CAP_BYTES", 256)
    created = _sse({"type": "response.created", "response": {"id": "resp_live"}})
    in_progress = _sse({"type": "response.in_progress", "response": {"id": "resp_live"}})
    rest = cast(list[bytes | BaseException], [in_progress] * 64)
    response = _FakeResponse(content=_FakeContent(created, rest))
    _install_session(monkeypatch, response)

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"})

    assert await _collect(stream.body) == [created, *([in_progress] * 64)]


@pytest.mark.asyncio
async def test_stream_body_oversized_content_frame_wins_over_the_withheld_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """The withheld cap is twice the parser cap: an oversized frame is classified as content before it trips."""

    monkeypatch.setattr(SourceStreamUsageParser, "_MAX_BUFFER_CHARS", 4096)
    monkeypatch.setattr(forwarding_module, "SOURCE_STREAM_WITHHELD_CAP_BYTES", 8192)
    created = _sse({"type": "response.created", "response": {"id": "resp_big"}})
    completed = _sse(
        {
            "type": "response.completed",
            "response": {
                "id": "resp_big",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "x" * 20_000}]}],
            },
        }
    )
    rest = cast(list[bytes | BaseException], _chunked(completed, 1024))
    response = _FakeResponse(content=_FakeContent(created, rest))
    _session, _context, lease = _install_session(monkeypatch, response)
    hook_calls = 0

    async def hook(holder: SourceUsageHolder) -> None:
        nonlocal hook_calls
        hook_calls += 1
        assert holder.first_content_seen is True

    stream = await forwarding_module.stream_responses(_responses_source(), {"model": "m"}, on_first_content=hook)

    assert b"".join(await _collect(stream.body)) == created + completed
    assert hook_calls == 1
    assert lease.released == 1


# -- chat-completions streams: pre-first-token phases are bounded by the total budget only ---------------------------


@pytest.mark.asyncio
async def test_stream_chat_completion_pre_first_token_phases_outlive_the_responses_deadlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chat stream has no bookkeeping frame: the open returns at the headers, however late they come, and the
    body's first read waits for the first token past the Responses first-frame deadline."""

    clock, scheduler = _virtual()
    first = b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
    response = _FakeResponse(
        content=_FakeContent(first, first_gate=lambda: scheduler.sleep(SOURCE_FIRST_FRAME_DEADLINE_SECONDS + 15))
    )
    _session, _context, lease = _install_session(
        monkeypatch, response, enter_gate=lambda: scheduler.sleep(SOURCE_HEADER_DEADLINE_SECONDS + 5)
    )

    task = scheduler.create_task(
        forwarding_module.stream_chat_completion(_responses_source(), {"model": "m"}, scheduler=scheduler, clock=clock)
    )
    await scheduler.advance(SOURCE_HEADER_DEADLINE_SECONDS + 4.9)
    assert not task.done()
    await scheduler.advance(0.1)
    assert task.done()
    stream = task.result()
    assert stream.usage_holder.first_frame_at is None

    async def read_first_token() -> bytes:
        return await anext(stream.body)

    first_read = scheduler.create_task(read_first_token())
    await scheduler.advance(SOURCE_FIRST_FRAME_DEADLINE_SECONDS + 14.9)
    assert not first_read.done()
    await scheduler.advance(0.1)
    assert first_read.done()
    assert first_read.result() == first
    headers_at = SOURCE_HEADER_DEADLINE_SECONDS + 5
    assert stream.usage_holder.first_frame_at == headers_at + SOURCE_FIRST_FRAME_DEADLINE_SECONDS + 15
    await cast(AsyncGenerator[bytes, None], stream.body).aclose()
    assert lease.released == 1
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_stream_chat_completion_total_budget_expiry_keeps_the_unreachable_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no phase deadline armed, a ``TimeoutError`` is the source's total budget: the pre-hardening 502."""

    _install_session(monkeypatch, _FakeResponse(content=_FakeContent(b"x")), enter_error=TimeoutError("total"))
    with pytest.raises(ModelSourceForwardingError) as before_headers:
        await forwarding_module.stream_chat_completion(_responses_source(), {"model": "m"})
    assert before_headers.value.status_code == 502
    assert cast(dict[str, object], before_headers.value.payload["error"])["code"] == "model_source_unreachable"
    assert before_headers.value.timeout_phase is None

    async def budget_expired() -> None:
        raise TimeoutError("total")

    response = _FakeResponse(content=_FakeContent(b"x", first_gate=budget_expired))
    _session, context, lease = _install_session(monkeypatch, response)
    stream = await forwarding_module.stream_chat_completion(_responses_source(), {"model": "m"})
    # The open returned at the headers; the body's first read carries the verdict.
    with pytest.raises(ModelSourceForwardingError) as before_first_token:
        await anext(stream.body)
    assert before_first_token.value.status_code == 502
    assert cast(dict[str, object], before_first_token.value.payload["error"])["code"] == "model_source_unreachable"
    assert before_first_token.value.timeout_phase is None
    assert context.exited == 1
    assert lease.released == 1


@pytest.mark.asyncio
async def test_stream_responses_keeps_both_open_deadlines_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scoping is per shape: Responses streams still expire at the header deadline."""

    clock, scheduler = _virtual()
    response = _FakeResponse(content=_FakeContent(b"data: late\n\n"))
    _install_session(monkeypatch, response, enter_gate=_forever)

    task = scheduler.create_task(
        forwarding_module.stream_responses(_responses_source(), {"model": "m"}, scheduler=scheduler, clock=clock)
    )
    await scheduler.advance(SOURCE_HEADER_DEADLINE_SECONDS)
    assert task.done()
    with pytest.raises(ModelSourceForwardingError) as excinfo:
        task.result()
    assert excinfo.value.timeout_phase == "header"
    await scheduler.cancel_owned_tasks()
