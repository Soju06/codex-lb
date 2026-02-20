from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest
from pydantic import ValidationError

from app.core.openai.chat_requests import ChatCompletionsRequest
from app.core.types import JsonValue


def test_chat_messages_to_responses_mapping():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ],
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    assert responses.instructions == "sys"
    assert responses.input == [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]


def test_chat_messages_require_objects():
    payload = {"model": "gpt-5.2", "messages": ["hi"]}
    with pytest.raises(ValidationError):
        ChatCompletionsRequest.model_validate(payload)


def test_chat_system_message_rejects_non_text_content():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "system", "content": [{"type": "image_url", "image_url": {"url": "https://example.com"}}]},
            {"role": "user", "content": "hi"},
        ],
    }
    with pytest.raises(ValidationError):
        ChatCompletionsRequest.model_validate(payload)


def test_chat_user_audio_rejects_invalid_format():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "user", "content": [{"type": "input_audio", "input_audio": {"format": "flac", "data": "..."}}]},
        ],
    }
    with pytest.raises(ValidationError):
        ChatCompletionsRequest.model_validate(payload)


def test_chat_store_true_is_ignored():
    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "hi"}],
        "store": True,
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    assert responses.store is False


def test_chat_max_tokens_are_stripped():
    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 128,
        "max_completion_tokens": 256,
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    dumped = responses.to_payload()
    assert "max_tokens" not in dumped
    assert "max_completion_tokens" not in dumped


def test_chat_reasoning_effort_maps_to_responses_reasoning():
    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "hi"}],
        "reasoning_effort": "high",
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    dumped = responses.to_payload()
    assert "reasoning_effort" not in dumped
    reasoning = dumped.get("reasoning")
    assert isinstance(reasoning, Mapping)
    reasoning_map = cast(Mapping[str, JsonValue], reasoning)
    assert reasoning_map.get("effort") == "high"


def test_chat_tools_are_normalized():
    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "do_thing",
                    "description": "desc",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    dumped = responses.to_payload()
    tools = dumped.get("tools")
    assert isinstance(tools, list)
    assert tools
    first_tool = cast(Mapping[str, JsonValue], tools[0])
    assert first_tool.get("name") == "do_thing"
    assert first_tool.get("type") == "function"
    assert "function" not in first_tool


def test_chat_tool_choice_object_passes_through():
    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "hi"}],
        "tool_choice": {"type": "function", "function": {"name": "do_thing"}},
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    dumped = responses.to_payload()
    tool_choice = dumped.get("tool_choice")
    assert tool_choice == {"type": "function", "name": "do_thing"}


def test_chat_response_format_json_object_maps_to_text_format():
    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "hi"}],
        "response_format": {"type": "json_object"},
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    dumped = responses.to_payload()
    text = dumped.get("text")
    assert isinstance(text, dict)
    assert text.get("format") == {"type": "json_object"}


def test_chat_response_format_json_schema_maps_schema_fields():
    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "hi"}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "output",
                "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
                "strict": True,
            },
        },
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    dumped = responses.to_payload()
    text = dumped.get("text")
    assert isinstance(text, dict)
    fmt = text.get("format")
    assert isinstance(fmt, dict)
    assert fmt.get("type") == "json_schema"
    assert fmt.get("name") == "output"
    assert fmt.get("schema") == {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    assert fmt.get("strict") is True


def test_chat_stream_options_include_obfuscation_passthrough():
    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "hi"}],
        "stream_options": {"include_obfuscation": True, "include_usage": True},
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    dumped = responses.to_payload()
    assert dumped.get("stream_options") == {"include_obfuscation": True}


def test_chat_oversized_image_is_dropped():
    oversized_data = "A" * (11 * 1024 * 1024)
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{oversized_data}"}},
                    {"type": "text", "text": "hi"},
                ],
            }
        ],
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    assert responses.input == [
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
    ]


def test_chat_assistant_tool_calls_decomposed():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "user", "content": "weather?"},
            {
                "role": "assistant",
                "content": "Let me check",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"loc":"NYC"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "72F"},
            {"role": "user", "content": "thanks"},
        ],
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    items = responses.input
    assert isinstance(items, list)
    assert items[0] == {"role": "user", "content": [{"type": "input_text", "text": "weather?"}]}
    assert items[1] == {"role": "assistant", "content": [{"type": "output_text", "text": "Let me check"}]}
    assert items[2] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "get_weather",
        "arguments": '{"loc":"NYC"}',
    }
    assert items[3] == {"type": "function_call_output", "call_id": "call_1", "output": "72F"}
    assert items[4] == {"role": "user", "content": [{"type": "input_text", "text": "thanks"}]}


def test_chat_assistant_tool_calls_no_content():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "user", "content": "weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": "{}"},
                    }
                ],
            },
        ],
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    items = responses.input
    assert isinstance(items, list)
    assert len(items) == 2
    assert items[0] == {"role": "user", "content": [{"type": "input_text", "text": "weather?"}]}
    assert items[1] == {"type": "function_call", "call_id": "call_1", "name": "get_weather", "arguments": "{}"}


def test_chat_tool_message_to_function_call_output():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "call_1", "content": "result data"},
        ],
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    items = responses.input
    assert isinstance(items, list)
    assert items[1] == {"type": "function_call_output", "call_id": "call_1", "output": "result data"}


def test_chat_tool_message_array_content():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "user", "content": "hi"},
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": [{"type": "text", "text": "result"}],
            },
        ],
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    items = responses.input
    assert isinstance(items, list)
    assert items[1] == {"type": "function_call_output", "call_id": "call_1", "output": "result"}


def test_chat_tool_message_missing_tool_call_id():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": "result"},
        ],
    }
    with pytest.raises(ValueError):
        ChatCompletionsRequest.model_validate(payload).to_responses_request()


def test_chat_tool_message_invalid_content_type_rejected():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "call_1", "content": 42},
        ],
    }
    with pytest.raises(ValueError, match="must be a string or array"):
        ChatCompletionsRequest.model_validate(payload).to_responses_request()


def test_chat_tool_message_null_content_rejected():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "call_1", "content": None},
        ],
    }
    with pytest.raises(ValueError, match="content is required"):
        ChatCompletionsRequest.model_validate(payload).to_responses_request()


def test_chat_tool_message_malformed_text_parts_rejected():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "user", "content": "hi"},
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": [{"type": "text"}],
            },
        ],
    }
    with pytest.raises(ValueError, match="no valid text parts"):
        ChatCompletionsRequest.model_validate(payload).to_responses_request()


def test_chat_assistant_non_string_tool_call_arguments_rejected():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "fn", "arguments": {"a": 1}},
                    }
                ],
            },
        ],
    }
    with pytest.raises(ValueError, match="arguments must be a string"):
        ChatCompletionsRequest.model_validate(payload).to_responses_request()


@pytest.mark.parametrize(
    ("field", "tool_call"),
    [
        ("id", {"type": "function", "function": {"name": "fn", "arguments": "{}"}}),
        ("function", {"id": "call_1", "type": "function"}),
        ("function.name", {"id": "call_1", "type": "function", "function": {"arguments": "{}"}}),
    ],
)
def test_chat_assistant_malformed_tool_call_rejected(field: str, tool_call: dict):
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None, "tool_calls": [tool_call]},
        ],
    }
    with pytest.raises(ValueError):
        ChatCompletionsRequest.model_validate(payload).to_responses_request()


def test_chat_tool_message_multi_part_content_concatenated():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "user", "content": "hi"},
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": [
                    {"type": "text", "text": '{"a":1'},
                    {"type": "text", "text": "}"},
                ],
            },
        ],
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    items = responses.input
    assert isinstance(items, list)
    assert items[1] == {"type": "function_call_output", "call_id": "call_1", "output": '{"a":1}'}


@pytest.mark.parametrize("n", [0, -1, 2])
def test_chat_n_not_1_rejected(n: int):
    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "hi"}],
        "n": n,
    }
    with pytest.raises(ValidationError):
        ChatCompletionsRequest.model_validate(payload)


def test_chat_n_equals_1_accepted():
    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "hi"}],
        "n": 1,
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    dumped = responses.to_payload()
    assert "n" not in dumped


def test_chat_image_detail_is_preserved_when_mapping_to_input_image():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/a.png", "detail": "high"},
                    }
                ],
            }
        ],
    }

    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()

    assert responses.input == [
        {
            "role": "user",
            "content": [{"type": "input_image", "image_url": "https://example.com/a.png", "detail": "high"}],
        }
    ]


def test_chat_assistant_refusal_converts_to_content_part():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "user", "content": "do something bad"},
            {"role": "assistant", "content": None, "refusal": "I can't help with that"},
            {"role": "user", "content": "ok"},
        ],
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    items = responses.input
    assert isinstance(items, list)
    assert items[1] == {
        "role": "assistant",
        "content": [{"type": "output_text", "text": "I can't help with that"}],
    }


def test_chat_assistant_content_and_refusal_both_converted():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "partial", "refusal": "but I must refuse"},
        ],
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    items = responses.input
    assert isinstance(items, list)
    assert items[1] == {
        "role": "assistant",
        "content": [
            {"type": "output_text", "text": "partial"},
            {"type": "output_text", "text": "but I must refuse"},
        ],
    }


def test_chat_assistant_tool_calls_with_refusal():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "refusal": "nope",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "fn", "arguments": "{}"},
                    }
                ],
            },
        ],
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    items = responses.input
    assert isinstance(items, list)
    assert items[1] == {
        "role": "assistant",
        "content": [{"type": "output_text", "text": "nope"}],
    }
    assert items[2] == {"type": "function_call", "call_id": "call_1", "name": "fn", "arguments": "{}"}


def test_chat_tool_message_maps_to_function_call_output():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "assistant", "content": "Running tool."},
            {"role": "tool", "tool_call_id": "call_1", "content": '{"ok":true}'},
            {"role": "user", "content": "continue"},
        ],
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()

    assert responses.input == [
        {"role": "assistant", "content": [{"type": "output_text", "text": "Running tool."}]},
        {"type": "function_call_output", "call_id": "call_1", "output": '{"ok":true}'},
        {"role": "user", "content": [{"type": "input_text", "text": "continue"}]},
    ]


def test_chat_tool_calls_history_maps_to_function_call_and_output():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": '{"q":"abc"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": '{"ok":true}'},
            {"role": "user", "content": "continue"},
        ],
    }

    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()

    assert responses.input == [
        {"role": "assistant", "content": [{"type": "output_text", "text": ""}]},
        {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": '{"q":"abc"}'},
        {"type": "function_call_output", "call_id": "call_1", "output": '{"ok":true}'},
        {"role": "user", "content": [{"type": "input_text", "text": "continue"}]},
    ]


def test_chat_assistant_tool_calls_require_function_name():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"arguments": "{}"}}],
            },
            {"role": "user", "content": "continue"},
        ],
    }
    with pytest.raises(ValidationError, match=r"assistant tool_calls\[0\]\.function must include a non-empty 'name'"):
        ChatCompletionsRequest.model_validate(payload)


def test_chat_tool_message_requires_tool_call_id():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "tool", "content": '{"ok":true}'},
            {"role": "user", "content": "continue"},
        ],
    }
    with pytest.raises(ValidationError, match="tool messages must include 'tool_call_id'"):
        ChatCompletionsRequest.model_validate(payload)


def test_chat_rejects_unknown_message_role():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "moderator", "content": "blocked"},
            {"role": "user", "content": "continue"},
        ],
    }
    with pytest.raises(ValidationError, match="Unsupported message role"):
        ChatCompletionsRequest.model_validate(payload)
