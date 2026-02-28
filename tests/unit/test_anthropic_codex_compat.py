from __future__ import annotations

import json

import pytest

from app.core.openai.chat_responses import ChatCompletion
from app.core.openai.models import OpenAIErrorEnvelope
from app.modules.anthropic.codex_compat import (
    chat_completion_to_anthropic_message,
    openai_error_to_anthropic_error,
    payload_to_responses_request,
    resolve_target_model,
    stream_message_as_anthropic_events,
)

pytestmark = pytest.mark.unit


def test_resolve_target_model_forces_codex() -> None:
    assert resolve_target_model("claude-sonnet-4-20250514") == "gpt-5.3-codex"
    assert resolve_target_model(None) == "gpt-5.3-codex"


def test_payload_to_responses_request_translates_messages_and_tools() -> None:
    request, stream_requested, requested_model = payload_to_responses_request(
        {
            "model": "claude-sonnet-4-20250514",
            "stream": False,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            "tools": [
                {
                    "name": "lookup",
                    "description": "Find records",
                    "input_schema": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                        "required": ["q"],
                    },
                }
            ],
            "tool_choice": {"type": "tool", "name": "lookup"},
            "temperature": 0.2,
        },
        target_model="gpt-5.3-codex",
    )

    assert requested_model == "claude-sonnet-4-20250514"
    assert stream_requested is False
    assert request.model == "gpt-5.3-codex"
    assert request.stream is True
    assert isinstance(request.input, list)
    assert request.tools and isinstance(request.tools[0], dict)
    assert request.tools[0]["name"] == "lookup"
    assert request.tool_choice == {"type": "function", "name": "lookup"}


def test_chat_completion_to_anthropic_message_maps_tool_calls() -> None:
    completion = ChatCompletion.model_validate(
        {
            "id": "chatcmpl_abc",
            "created": 123,
            "model": "gpt-5.3-codex",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": "ok",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": "{\"q\":\"x\"}"},
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        }
    )

    payload = chat_completion_to_anthropic_message(
        completion,
        requested_model="claude-sonnet-4-20250514",
        target_model="gpt-5.3-codex",
    )

    assert payload["type"] == "message"
    assert payload["model"] == "claude-sonnet-4-20250514"
    assert payload["stop_reason"] == "tool_use"
    assert payload["usage"] == {"input_tokens": 10, "output_tokens": 4}
    assert payload["content"][0] == {"type": "text", "text": "ok"}
    assert payload["content"][1]["type"] == "tool_use"
    assert payload["content"][1]["name"] == "lookup"
    assert payload["content"][1]["input"] == {"q": "x"}


def test_openai_error_to_anthropic_error_maps_rate_limit() -> None:
    envelope = OpenAIErrorEnvelope.model_validate(
        {
            "error": {
                "message": "quota exceeded",
                "type": "rate_limit_error",
                "code": "insufficient_quota",
            }
        }
    )

    status_code, payload = openai_error_to_anthropic_error(envelope)

    assert status_code == 429
    assert payload["type"] == "error"
    assert payload["error"]["type"] == "rate_limit_error"
    assert payload["error"]["message"] == "quota exceeded"


@pytest.mark.asyncio
async def test_stream_message_as_anthropic_events_emits_valid_sequence() -> None:
    events = [
        event
        async for event in stream_message_as_anthropic_events(
            {
                "id": "msg_1",
                "model": "claude-sonnet-4-20250514",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "hello"}],
                "usage": {"input_tokens": 3, "output_tokens": 2},
            }
        )
    ]

    payloads = [json.loads(event[6:]) for event in events if event.startswith("data: ")]
    assert payloads[0]["type"] == "message_start"
    assert payloads[-1]["type"] == "message_stop"
    message_delta = [payload for payload in payloads if payload.get("type") == "message_delta"]
    assert message_delta
    assert message_delta[0]["usage"]["output_tokens"] == 2
