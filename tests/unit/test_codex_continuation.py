from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest

from app.core.clients.codex_continuation import (
    CodexContinuationConfig,
    fold_responses_stream_with_codex_continuation,
    should_apply_codex_continuation,
)
from app.core.types import JsonObject, JsonValue
from app.core.utils.sse import format_sse_event, parse_sse_data_json

pytestmark = pytest.mark.unit


def _event(payload: dict[str, JsonValue]) -> str:
    return format_sse_event(payload)


def _created(response_id: str) -> dict[str, JsonValue]:
    return {
        "type": "response.created",
        "response": {
            "id": response_id,
            "status": "in_progress",
            "output": [],
        },
    }


def _reasoning_events(*, output_index: int, item_id: str, encrypted_content: str) -> list[dict[str, JsonValue]]:
    item: dict[str, JsonValue] = {
        "id": item_id,
        "type": "reasoning",
        "encrypted_content": encrypted_content,
    }
    return [
        {
            "type": "response.output_item.added",
            "output_index": output_index,
            "item": {"id": item_id, "type": "reasoning"},
        },
        {
            "type": "response.output_item.done",
            "output_index": output_index,
            "item": item,
        },
    ]


def _message_events(*, output_index: int, item_id: str, text: str) -> list[dict[str, JsonValue]]:
    return [
        {
            "type": "response.output_item.added",
            "output_index": output_index,
            "item": {
                "id": item_id,
                "type": "message",
                "role": "assistant",
                "content": [],
            },
        },
        {
            "type": "response.output_text.delta",
            "output_index": output_index,
            "item_id": item_id,
            "content_index": 0,
            "delta": text,
        },
        {
            "type": "response.output_item.done",
            "output_index": output_index,
            "item": {
                "id": item_id,
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            },
        },
    ]


def _completed(
    response_id: str,
    *,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
) -> dict[str, JsonValue]:
    return {
        "type": "response.completed",
        "response": {
            "id": response_id,
            "status": "completed",
            "output": [],
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "input_tokens_details": {"cached_tokens": 25},
                "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
            },
        },
    }


async def _collect_events(chunks: AsyncIterator[str]) -> list[dict[str, JsonValue]]:
    events: list[dict[str, JsonValue]] = []
    async for chunk in chunks:
        payload = parse_sse_data_json(chunk)
        if payload is not None:
            events.append(payload)
    return events


@pytest.mark.asyncio
async def test_fold_responses_stream_continues_truncated_round_and_reuses_payload_shape() -> None:
    base_payload: JsonObject = {
        "model": "gpt-5.5",
        "instructions": "solve",
        "input": [{"role": "user", "content": "question"}],
        "previous_response_id": "resp_previous",
        "stream": True,
    }
    round_events = [
        [
            _created("resp_visible"),
            *_reasoning_events(output_index=0, item_id="rs_1", encrypted_content="enc1"),
            *_message_events(output_index=1, item_id="msg_partial", text="partial answer"),
            _completed("resp_visible", input_tokens=100, output_tokens=600, reasoning_tokens=516),
        ],
        [
            _created("resp_hidden"),
            *_reasoning_events(output_index=0, item_id="rs_2", encrypted_content="enc2"),
            *_message_events(output_index=1, item_id="msg_final", text="final answer"),
            _completed("resp_hidden", input_tokens=120, output_tokens=20, reasoning_tokens=10),
        ],
    ]
    opened_payloads: list[JsonObject] = []

    async def open_round(payload: JsonObject) -> AsyncIterator[str]:
        opened_payloads.append(payload)
        events = round_events[len(opened_payloads) - 1]
        for event in events:
            yield _event(event)

    events = await _collect_events(
        fold_responses_stream_with_codex_continuation(
            base_payload=base_payload,
            open_round=open_round,
            config=CodexContinuationConfig(max_continue=1, rechunk_size=64),
        )
    )

    assert len(opened_payloads) == 2
    assert opened_payloads[0]["previous_response_id"] == "resp_previous"
    assert opened_payloads[0]["include"] == ["reasoning.encrypted_content"]
    assert "previous_response_id" not in opened_payloads[1]
    replay_input = cast(list[JsonValue], opened_payloads[1]["input"])
    assert replay_input[0] == {"role": "user", "content": "question"}
    assert replay_input[1] == {
        "id": "rs_1",
        "type": "reasoning",
        "encrypted_content": "enc1",
    }
    assert replay_input[2] == {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Continue thinking..."}],
        "phase": "commentary",
    }

    event_types = [event["type"] for event in events]
    assert event_types.count("response.created") == 1
    assert event_types.count("response.completed") == 1
    assert event_types.count("response.output_item.done") == 3
    assert [event["sequence_number"] for event in events] == list(range(len(events)))

    deltas = "".join(
        str(event.get("delta", ""))
        for event in events
        if event.get("type") == "response.output_text.delta"
    )
    assert "final answer" in deltas
    assert "partial answer" not in deltas

    terminal = events[-1]
    response = cast(dict[str, JsonValue], terminal["response"])
    assert response["id"] == "resp_visible"
    metadata = cast(dict[str, JsonValue], response["metadata"])
    assert metadata["proxy_rounds"] == [
        {"round": 1, "reasoning_tokens": 516, "n": 1},
        {"round": 2, "reasoning_tokens": 10, "n": None},
    ]
    assert metadata["proxy_billed_usage"] == {
        "input_tokens": 220,
        "output_tokens": 620,
        "total_tokens": 840,
        "input_tokens_details": {"cached_tokens": 50},
        "output_tokens_details": {"reasoning_tokens": 526},
    }
    usage = cast(dict[str, JsonValue], response["usage"])
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 536
    assert usage["total_tokens"] == 636
    assert usage["output_tokens_details"] == {"reasoning_tokens": 526}


@pytest.mark.asyncio
async def test_fold_responses_stream_drains_terminal_round_before_returning() -> None:
    round_drained = False

    async def open_round(payload: JsonObject) -> AsyncIterator[str]:
        nonlocal round_drained
        del payload
        try:
            yield _event(_completed("resp_terminal", input_tokens=5, output_tokens=7, reasoning_tokens=3))
            yield "data: [DONE]\n\n"
        finally:
            round_drained = True

    chunks = [
        chunk
        async for chunk in fold_responses_stream_with_codex_continuation(
            base_payload={"model": "gpt-5.5", "input": [], "stream": True},
            open_round=open_round,
            config=CodexContinuationConfig(),
        )
    ]

    assert round_drained is True
    assert chunks[-1] == "data: [DONE]\n\n"
    terminal = parse_sse_data_json(chunks[-2])
    assert terminal is not None
    assert terminal["type"] == "response.completed"


def test_should_apply_codex_continuation_respects_explicit_reasoning_opt_out() -> None:
    config = CodexContinuationConfig()

    assert should_apply_codex_continuation(
        {"model": "gpt-5.5", "instructions": "hi", "input": [], "stream": True},
        config,
    )
    assert not should_apply_codex_continuation(
        {
            "model": "gpt-5.5",
            "instructions": "hi",
            "input": [],
            "stream": True,
            "reasoning": False,
        },
        config,
    )
