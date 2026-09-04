from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.openai.parsing import _LIFECYCLE_EVENT_TYPES, classify_event_type, parse_sse_event
from app.core.types import JsonValue
from app.core.utils.sse import (
    CODEX_KEEPALIVE_FRAME,
    SSE_KEEPALIVE_FRAME,
    ParsedSseBlock,
    extract_sse_data,
    format_sse_data,
    format_sse_event,
    format_sse_event_from_text,
    inject_sse_keepalives,
    parse_sse_data_json,
    parse_sse_data_json_text,
    sse_block_with_payload,
    sse_event_type_from_block,
)
from tests.unit.hypothesis_strategies import json_objects, json_values

pytestmark = pytest.mark.unit


def test_format_sse_event_serializes_payload():
    payload = {"type": "response.completed", "response": {"id": "resp_1"}}
    result = format_sse_event(payload)
    assert result == 'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'


@given(payload=json_objects)
@settings(max_examples=40, deadline=None)
def test_format_sse_event_round_trips_arbitrary_json_objects(payload):
    assert parse_sse_data_json(format_sse_event(payload)) == payload


@given(payload=json_objects)
@settings(max_examples=40, deadline=None)
def test_format_sse_data_round_trips_arbitrary_json_objects(payload):
    assert parse_sse_data_json(format_sse_data(payload)) == payload


@given(
    boundary=st.sampled_from(["\r", "\n", "\r\n"]),
    key=st.text(max_size=40),
    value=st.integers(),
)
@settings(max_examples=30, deadline=None)
def test_sse_line_boundaries_are_equivalent_in_multiline_data(boundary, key, value):
    encoded_key = json.dumps(key, ensure_ascii=True)
    block = f"data: {{{encoded_key}:" + boundary + f"data: {value}}}" + boundary * 2

    assert parse_sse_data_json(block) == {key: value}


@given(text=st.text(max_size=80))
@settings(max_examples=30, deadline=None)
def test_sse_unicode_line_separators_remain_data(text):
    payload = {"value": f"before{text}\u2028middle\u2029after"}
    block = "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

    assert parse_sse_data_json(block) == payload


@given(
    boundary=st.sampled_from(["\r", "\n", "\r\n"]),
    first=st.text(alphabet=st.characters(blacklist_categories=("C", "Z")), min_size=1, max_size=40),
    second=st.text(alphabet=st.characters(blacklist_categories=("C", "Z")), min_size=1, max_size=40),
)
@settings(max_examples=30, deadline=None)
def test_sse_multiline_data_ignores_comments_and_joins_with_newline(boundary, first, second):
    block = f": comment{boundary}data: {first}{boundary}event: ignored{boundary}data: {second}{boundary}{boundary}"

    assert extract_sse_data(block) == f"{first}\n{second}"


@given(value=st.one_of(st.none(), st.booleans(), st.integers(), st.lists(json_values, max_size=4)))
@settings(max_examples=30, deadline=None)
def test_parse_sse_data_json_rejects_non_object_json(value):
    assert parse_sse_data_json("data: " + json.dumps(value) + "\n\n") is None


async def _agen(items: list[str]) -> AsyncIterator[str]:
    for item in items:
        yield item


async def _slow_agen(items: list[str], delay: float) -> AsyncIterator[str]:
    for item in items:
        await asyncio.sleep(delay)
        yield item


@pytest.mark.asyncio
async def test_inject_sse_keepalives_passes_through_when_disabled():
    out = [chunk async for chunk in inject_sse_keepalives(_agen(["a\n\n", "b\n\n"]), 0)]
    assert out == ["a\n\n", "b\n\n"]


@pytest.mark.asyncio
async def test_inject_sse_keepalives_no_pings_when_source_is_fast():
    out = [chunk async for chunk in inject_sse_keepalives(_agen(["a\n\n", "b\n\n"]), 5.0)]
    assert out == ["a\n\n", "b\n\n"]


@pytest.mark.asyncio
async def test_inject_sse_keepalives_emits_pings_on_idle_gap():
    callbacks: list[str] = []
    out = [
        chunk
        async for chunk in inject_sse_keepalives(
            _slow_agen(["a\n\n"], delay=0.25),
            0.05,
            on_keepalive=lambda: callbacks.append("sent"),
        )
    ]
    assert out[-1] == "a\n\n"
    assert SSE_KEEPALIVE_FRAME in out
    assert out.count(SSE_KEEPALIVE_FRAME) >= 2
    assert len(callbacks) == out.count(SSE_KEEPALIVE_FRAME)


@pytest.mark.asyncio
async def test_inject_sse_keepalives_cancels_idle_source_when_downstream_closes():
    source_cancelled = asyncio.Event()

    async def source() -> AsyncIterator[str]:
        try:
            await asyncio.Event().wait()
        finally:
            source_cancelled.set()
        yield ""  # pragma: no cover - keeps this a pending async generator

    stream = inject_sse_keepalives(source(), 0.01)
    assert await anext(stream) == SSE_KEEPALIVE_FRAME

    await cast(Any, stream).aclose()

    assert source_cancelled.is_set()


@pytest.mark.asyncio
async def test_inject_sse_keepalives_can_emit_codex_event_frame():
    out = [
        chunk
        async for chunk in inject_sse_keepalives(
            _slow_agen(["a\n\n"], delay=0.25),
            0.05,
            keepalive_frame=CODEX_KEEPALIVE_FRAME,
        )
    ]
    assert out[-1] == "a\n\n"
    assert CODEX_KEEPALIVE_FRAME in out
    assert out.count(CODEX_KEEPALIVE_FRAME) >= 2


@pytest.mark.asyncio
async def test_inject_sse_keepalives_keepalive_frame_is_sse_comment():
    assert SSE_KEEPALIVE_FRAME.startswith(":")
    assert SSE_KEEPALIVE_FRAME.endswith("\n\n")


def test_extract_sse_data_preserves_unicode_line_separators():
    # U+2028 / U+2029 are valid *unescaped* inside JSON strings. The SSE spec
    # delimits lines only by CR/LF/CRLF, so they must not split a data: payload.
    payload = {"type": "response.output_text.delta", "delta": "line1\u2028line2\u2029end"}
    block = "event: response.output_text.delta\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

    data = extract_sse_data(block)

    assert data is not None
    assert json.loads(data) == payload


def test_parse_sse_data_json_preserves_unicode_line_separators():
    payload = {"type": "response.completed", "response": {"id": "resp_1", "status": "completed", "note": "a\u2028b"}}
    block = "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

    assert parse_sse_data_json(block) == payload


def test_parse_sse_event_parses_payload_with_unicode_line_separators():
    # The proxy receive path relies on parse_sse_event for terminal-event
    # detection, dedupe, and usage; an unescaped U+2028 used to drop the event.
    payload = {"type": "response.output_text.delta", "delta": "x\u2028y\u2029z"}
    block = "event: response.output_text.delta\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

    event = parse_sse_event(block)

    assert event is not None
    assert event.type == "response.output_text.delta"


def test_extract_sse_data_joins_crlf_multiline_data():
    # CR, LF, and CRLF all remain valid line boundaries after the fix.
    block = "data: line1\r\ndata: line2\rdata: line3\n\n"

    assert extract_sse_data(block) == "line1\nline2\nline3"


def test_classify_event_type_prefers_string_type_field():
    assert classify_event_type({"type": "response.output_text.delta", "delta": "x"}) == "response.output_text.delta"


def test_classify_event_type_maps_typeless_error_payload_to_error():
    assert classify_event_type({"error": {"message": "boom"}, "status": 400}) == "error"


def test_classify_event_type_rejects_non_dict_and_typeless_payloads():
    assert classify_event_type(None) is None
    assert classify_event_type([1, 2, 3]) is None
    assert classify_event_type({"type": 42}) is None
    assert classify_event_type({"delta": "x"}) is None


def test_lifecycle_event_types_cover_terminal_and_created_frames():
    assert _LIFECYCLE_EVENT_TYPES == frozenset(
        {
            "response.created",
            "response.completed",
            "response.incomplete",
            "response.failed",
            "error",
        }
    )


def test_sse_event_type_from_block_extracts_type_from_canonical_block():
    block = 'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"hi"}\n\n'

    assert sse_event_type_from_block(block) == "response.output_text.delta"


def test_sse_event_type_from_block_accepts_raw_utf8_payloads():
    block = 'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"안녕"}\n\n'

    assert sse_event_type_from_block(block) == "response.output_text.delta"


def test_sse_event_type_from_block_rejects_data_only_blocks():
    assert sse_event_type_from_block('data: {"type":"response.output_text.delta","delta":"hi"}\n\n') is None


def test_sse_event_type_from_block_rejects_trailing_event_field_ordering():
    # `event:` after `data:` is legal SSE but not the canonical framing this
    # proxy relays verbatim; callers must fall back to a full parse.
    block = 'data: {"type":"response.output_text.delta","delta":"hi"}\nevent: response.output_text.delta\n\n'

    assert sse_event_type_from_block(block) is None


def test_sse_event_type_from_block_rejects_non_lf_framing_and_multiline_data():
    crlf = 'event: response.output_text.delta\r\ndata: {"type":"response.output_text.delta"}\r\n\r\n'
    multiline = 'event: response.output_text.delta\ndata: {"type":\ndata: "response.output_text.delta"}\n\n'

    assert sse_event_type_from_block(crlf) is None
    assert sse_event_type_from_block(multiline) is None


def test_sse_event_type_from_block_rejects_non_object_data_payloads():
    assert sse_event_type_from_block("event: done\ndata: [DONE]\n\n") is None
    assert sse_event_type_from_block("event: ping\ndata: \n\n") is None


_UNUSUAL_DATA_TEXTS = [
    "",
    "   ",
    "[DONE]",
    " [DONE] ",
    "{not-json}",
    "[1,2]",
    "null",
    '"string"',
    '{"a":1}\n',
    '{"a":1}\r\n',
    ' {"a":1}',
    '{"a":1} ',
    '{\n  "type": "response.completed",\n  "response": {"id": "resp_1"}\n}',
    '{"type":"response.output_text.delta","delta":"\u2028\u2029 \ud55c\uae00 \ud14d\uc2a4\ud2b8"}',
    '{"error":{"code":"server_error","message":"boom"}}',
]


@pytest.mark.parametrize("text", _UNUSUAL_DATA_TEXTS)
def test_parse_sse_data_json_text_matches_framed_parse(text: str) -> None:
    assert parse_sse_data_json_text(text) == parse_sse_data_json(f"data: {text}\n\n")


@given(payload=json_objects, ensure_ascii=st.booleans(), indent=st.sampled_from([None, 2]))
@settings(max_examples=60, deadline=None)
def test_parse_sse_data_json_text_matches_framed_parse_for_json_objects(payload, ensure_ascii, indent) -> None:
    text = json.dumps(payload, ensure_ascii=ensure_ascii, indent=indent)
    assert parse_sse_data_json_text(text) == parse_sse_data_json(f"data: {text}\n\n")


def test_parse_sse_data_json_text_preserves_unicode_line_separators() -> None:
    text = '{"type":"response.output_text.delta","delta":"a\u2028b\u2029c"}'
    payload = parse_sse_data_json_text(text)
    assert payload == {"type": "response.output_text.delta", "delta": "a\u2028b\u2029c"}


def test_format_sse_event_from_text_frames_upstream_text_verbatim() -> None:
    text = '{"type":"response.output_text.delta","item_id":"msg_1","delta":"\ud55c\uae00 \u2028 caf\u00e9"}'
    payload = parse_sse_data_json_text(text)
    assert payload is not None

    block = format_sse_event_from_text(payload, text)

    assert block == f"event: response.output_text.delta\ndata: {text}\n\n"
    assert sse_event_type_from_block(block) == "response.output_text.delta"
    assert parse_sse_data_json(block) == parse_sse_data_json(format_sse_event(payload))
    # Upstream UTF-8 stays as-is instead of being re-escaped.
    assert "\ud55c\uae00" in block
    assert "\\u" not in block


def test_format_sse_event_from_text_is_byte_identical_for_ascii_compact_text() -> None:
    payload = {"type": "response.completed", "sequence_number": 7, "response": {"id": "resp_1", "output": []}}
    text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    assert format_sse_event_from_text(payload, text) == format_sse_event(payload)


def test_format_sse_event_from_text_keeps_typeless_error_frames_data_only() -> None:
    text = '{"error":{"code":"server_error","message":"boom"}}'
    payload = parse_sse_data_json_text(text)
    assert payload is not None
    assert classify_event_type(payload) == "error"

    block = format_sse_event_from_text(payload, text)

    assert block == f"data: {text}\n\n"
    assert block == format_sse_event(payload)


@pytest.mark.parametrize(
    "text",
    [
        '{\n  "type": "response.completed",\n  "response": {"id": "resp_1"}\n}',
        '{"type":"response.completed","response":{"id":"resp_1"}}\n',
        ' {"type":"response.completed","response":{"id":"resp_1"}}',
    ],
)
def test_format_sse_event_from_text_falls_back_to_serialization_for_non_canonical_text(text: str) -> None:
    payload = json.loads(text)
    assert format_sse_event_from_text(payload, text) == format_sse_event(payload)


@given(payload=json_objects, ensure_ascii=st.booleans())
@settings(max_examples=40, deadline=None)
def test_format_sse_event_from_text_round_trips_arbitrary_json_objects(payload, ensure_ascii) -> None:
    text = json.dumps(payload, ensure_ascii=ensure_ascii, separators=(",", ":"))
    block = format_sse_event_from_text(payload, text)
    assert parse_sse_data_json(block) == payload
    assert sse_event_type_from_block(block) == sse_event_type_from_block(format_sse_event(payload))


def test_parsed_sse_block_behaves_like_str_and_short_circuits_parse() -> None:
    payload: dict[str, JsonValue] = {"type": "response.output_text.delta", "delta": "hi"}
    plain = format_sse_event(payload)
    block = sse_block_with_payload(plain, payload)

    assert isinstance(block, ParsedSseBlock)
    assert isinstance(block, str)
    assert block == plain
    assert block.startswith("event: response.output_text.delta\n")
    assert block.encode("utf-8") == plain.encode("utf-8")
    assert parse_sse_data_json(block) is payload
    # Derived strings are plain ``str`` and take the full parse path.
    assert type(block.strip()) is str
    assert parse_sse_data_json(block.strip()) == payload
    assert parse_sse_data_json(block + "") == payload
    # Re-attaching the same payload is an identity operation.
    assert sse_block_with_payload(block, payload) is block


def test_parsed_sse_block_with_none_payload_matches_unparseable_parse() -> None:
    for plain in (": keepalive\n\n", "data: [DONE]\n\n", "data: {not-json}\n\n"):
        assert parse_sse_data_json(plain) is None
        assert parse_sse_data_json(sse_block_with_payload(plain, None)) is None


@given(payload=json_objects)
@settings(max_examples=40, deadline=None)
def test_sse_block_with_payload_agrees_with_full_parse(payload) -> None:
    plain = format_sse_event(payload)
    block = sse_block_with_payload(plain, parse_sse_data_json(plain))
    assert parse_sse_data_json(block) == parse_sse_data_json(plain)
    assert extract_sse_data(block) == extract_sse_data(plain)
