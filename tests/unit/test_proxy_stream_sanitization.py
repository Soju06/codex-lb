from __future__ import annotations

import pytest

from app.core.types import JsonValue
from app.modules.proxy.service import (
    _extract_stream_text_output,
    _extract_text_delta,
    _is_internal_tool_trace_text,
    _is_reasoning_stream_event,
    _should_hide_reasoning_for_stream,
)

pytestmark = pytest.mark.unit


def test_extract_text_delta_reads_delta_text() -> None:
    payload: dict[str, JsonValue] = {"delta": "hello world"}

    assert _extract_text_delta("response.output_text.delta", payload) == "hello world"
    assert _extract_text_delta("response.refusal.delta", payload) == "hello world"
    assert _extract_text_delta("response.completed", payload) is None


@pytest.mark.parametrize(
    ("event_type", "payload", "expected"),
    [
        ("response.output_text.delta", {"delta": "delta text"}, "delta text"),
        ("response.output_text.done", {"text": "done text"}, "done text"),
        ("response.content_part.done", {"part": {"type": "output_text", "text": "part text"}}, "part text"),
        ("response.content_part.done", {"part": {"type": "refusal", "refusal": "cannot do that"}}, "cannot do that"),
        ("response.content_part.done", {"part": {"type": "output_image", "image_url": "https://x"}}, None),
        ("response.completed", {"response": {"id": "r1"}}, None),
    ],
)
def test_extract_stream_text_output(event_type: str, payload: dict[str, JsonValue], expected: str | None) -> None:
    assert _extract_stream_text_output(event_type, payload) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('assistant to=functions.read {"path":"x"}', True),
        ("assistant to=multi_tool_use.parallel", True),
        ('\u0c9f\u0ccd\u0c9f\u0cc1 to=functions.bash commentary {"command":"bun run lint"}', True),
        ("*** Begin Patch", True),
        ("*** Update File: app/modules/proxy/service.py", True),
        ("tool_uses", True),
        ("recipient_name", True),
        ("hello from codexlb", False),
        ("This is a normal reply", False),
    ],
)
def test_internal_tool_trace_text_detection(value: str, expected: bool) -> None:
    assert _is_internal_tool_trace_text(value) is expected


@pytest.mark.parametrize(
    ("mode", "headers", "expected"),
    [
        ("on", {}, True),
        ("off", {"User-Agent": "OpenCode/1.0"}, False),
        ("auto", {"User-Agent": "OpenCode/1.0"}, True),
        ("auto", {"X-OpenAI-Client-User-Agent": "OpenCode Desktop"}, True),
        ("auto", {"User-Agent": "Mozilla/5.0"}, False),
        ("bogus", {"User-Agent": "OpenCode/1.0"}, True),
    ],
)
def test_should_hide_reasoning_for_stream(mode: str, headers: dict[str, str], expected: bool) -> None:
    assert _should_hide_reasoning_for_stream(headers=headers, mode=mode) is expected


@pytest.mark.parametrize(
    ("event_type", "payload", "expected"),
    [
        ("response.reasoning.delta", None, True),
        ("response.summary.delta", None, True),
        ("response.output_text.delta", {"delta": "hello"}, False),
        ("response.completed", {"response": {"id": "r1", "summary": "auto"}}, False),
        (None, {"reasoning": "step-by-step"}, False),
        (None, {"summary": "short recap"}, False),
        (None, {"type": "summary"}, False),
        (None, {"part": {"type": "summary"}}, False),
        (None, {"content": [{"type": "text", "text": "Here is a summary of the result."}]}, False),
        (None, {"content": [{"type": "text", "text": "plain text"}]}, False),
    ],
)
def test_is_reasoning_stream_event(
    event_type: str | None,
    payload: dict[str, JsonValue] | None,
    expected: bool,
) -> None:
    assert _is_reasoning_stream_event(event_type, payload) is expected
