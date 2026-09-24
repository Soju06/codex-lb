from __future__ import annotations

import pytest

from app.modules.proxy._service.response_timing import ResponseTiming, observe_output_timing
from app.modules.proxy._service.support import _ttft_event_visible_at

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "event",
    [
        {"type": "response.output_text.done", "text": "hello"},
        {"type": "response.refusal.done", "refusal": "Unable"},
        {"type": "response.function_call_arguments.done", "arguments": "{}"},
        {"type": "response.custom_tool_call_input.done", "input": "pwd"},
        {"type": "response.output_item.done", "item": {"type": "function_call", "arguments": "{}"}},
        {"type": "response.custom_tool_call_input.delta", "delta": "pwd"},
        {"type": "response.refusal.delta", "delta": "Unable"},
        {
            "type": "response.output_item.done",
            "item": {"type": "apply_patch_call", "operation": {"type": "delete_file", "path": "example.txt"}},
        },
    ],
)
def test_first_content_establishes_ttft_and_output_at_same_observed_time(event):
    timing = ResponseTiming(started_at=100.0)
    observe_output_timing(timing, event["type"], event, observed_at=100.25)
    assert timing.latency_first_output_ms == 250
    assert timing.output_delta_count == 1
    assert _ttft_event_visible_at(event["type"], event, now=100.25) == 100.25


@pytest.mark.parametrize(
    "event",
    [
        {"type": "response.output_text.delta", "delta": ""},
        {"type": "response.refusal.done", "refusal": ""},
        {"type": "response.function_call_arguments.done", "arguments": ""},
        {"type": "response.custom_tool_call_input.done", "input": ""},
        {"type": "response.output_item.done", "item": {"type": "apply_patch_call", "operation": True}},
        {"type": "response.completed", "response": {"output": [{"type": {"invalid": "metadata"}}]}},
        {"type": "response.output_tool_call.delta", "call_id": "call"},
        {"type": "response.output_item.added", "item": {"type": "custom_tool_call", "input": ""}},
        {"type": "response.reasoning_summary_text.delta", "delta": "plan"},
        {"type": "response.completed", "response": {"usage": {"output_tokens": 100}}},
    ],
)
def test_metadata_reasoning_and_usage_do_not_create_output_samples(event):
    timing = ResponseTiming(started_at=100.0)
    observe_output_timing(timing, event["type"], event, observed_at=100.25)
    assert timing.latency_first_output_ms is None
    assert timing.output_delta_count == 0


@pytest.mark.parametrize(
    "snapshot",
    [
        {"type": "response.output_text.done", "text": "hello"},
        {"type": "response.refusal.done", "refusal": "Unable"},
        {"type": "response.function_call_arguments.done", "arguments": "{}"},
        {"type": "response.custom_tool_call_input.done", "input": "pwd"},
    ],
)
def test_full_snapshot_does_not_double_count_already_streamed_content(snapshot):
    timing = ResponseTiming(started_at=100.0)
    observe_output_timing(timing, "response.output_text.delta", {"delta": "hello"}, observed_at=100.25)
    observe_output_timing(timing, snapshot["type"], snapshot, observed_at=100.5)
    observe_output_timing(
        timing,
        "response.completed",
        {"response": {"output": [{"type": "message", "content": [{"type": "output_text", "text": "hello"}]}]}},
        observed_at=101.0,
    )
    assert timing.output_delta_count == 1
    assert timing.latency_first_output_ms == 250
