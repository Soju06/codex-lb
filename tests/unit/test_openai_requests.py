from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.openai.v1_requests import V1ResponsesCompactRequest, V1ResponsesRequest


def test_responses_requires_instructions():
    with pytest.raises(ValidationError):
        ResponsesRequest.model_validate({"model": "gpt-5.1", "input": []})


def test_responses_requires_input():
    with pytest.raises(ValidationError):
        ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi"})


def test_store_true_is_rejected():
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "store": True}
    with pytest.raises(ValueError, match="store must be false"):
        ResponsesRequest.model_validate(payload)


def test_store_omitted_defaults_to_false():
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    request = ResponsesRequest.model_validate(payload)

    assert request.store is False
    assert request.to_payload()["store"] is False


def test_store_false_is_preserved():
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "store": False}
    request = ResponsesRequest.model_validate(payload)

    assert request.to_payload()["store"] is False


def test_extra_fields_are_preserved():
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "max_output_tokens": 32000}
    request = ResponsesRequest.model_validate(payload)

    dumped = request.to_payload()
    assert "max_output_tokens" not in dumped


def test_openai_compatible_reasoning_aliases_are_normalized():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "reasoningEffort": "high",
        "reasoningSummary": "auto",
    }
    request = ResponsesRequest.model_validate(payload)

    dumped = request.to_payload()
    assert dumped["reasoning"] == {"effort": "high", "summary": "auto"}
    assert "reasoningEffort" not in dumped
    assert "reasoningSummary" not in dumped


def test_openai_compatible_text_verbosity_alias_is_normalized():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "textVerbosity": "low",
    }
    request = ResponsesRequest.model_validate(payload)

    dumped = request.to_payload()
    assert dumped["text"] == {"verbosity": "low"}
    assert "textVerbosity" not in dumped


def test_openai_compatible_top_level_verbosity_is_normalized():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "verbosity": "medium",
    }
    request = ResponsesRequest.model_validate(payload)

    dumped = request.to_payload()
    assert dumped["text"] == {"verbosity": "medium"}
    assert "verbosity" not in dumped


def test_interleaved_reasoning_fields_are_sanitized_from_input():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [
            {
                "role": "user",
                "reasoning_content": "hidden",
                "tool_calls": [{"id": "call_1"}],
                "function_call": {"name": "noop", "arguments": "{}"},
                "content": [
                    {"type": "input_text", "text": "hello"},
                    {"type": "reasoning", "reasoning_content": "drop"},
                    {"type": "input_text", "text": "world", "reasoning_details": {"tokens": 1}},
                ],
            }
        ],
    }
    request = ResponsesRequest.model_validate(payload)

    dumped = request.to_payload()
    assert dumped["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "hello"},
                {"type": "input_text", "text": "world"},
            ],
        }
    ]


def test_interleaved_reasoning_sanitization_preserves_top_level_reasoning():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "reasoning": {"effort": "high", "summary": "auto"},
        "input": [
            {
                "role": "user",
                "reasoning_details": {"tokens": 2},
                "content": [{"type": "input_text", "text": "hello", "reasoning_content": "drop"}],
            }
        ],
    }
    request = ResponsesRequest.model_validate(payload)

    dumped = request.to_payload()
    assert dumped["reasoning"] == {"effort": "high", "summary": "auto"}
    assert dumped["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}]


def test_interleaved_reasoning_sanitization_preserves_nested_function_call_arguments():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "lookup",
                "arguments": {
                    "tool_calls": [{"id": "nested_1"}],
                    "function_call": {"name": "nested_fn"},
                    "reasoning_details": {"tokens": 3},
                },
            }
        ],
    }
    request = ResponsesRequest.model_validate(payload)

    dumped = request.to_payload()
    assert dumped["input"] == payload["input"]


def test_responses_accepts_string_input():
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": "hello"}
    request = ResponsesRequest.model_validate(payload)

    assert request.input == [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}]


@pytest.mark.parametrize(
    ("tool_type", "expected"),
    [
        ("web_search", "web_search"),
        ("web_search_preview", "web_search"),
    ],
)
def test_responses_accepts_builtin_tools(tool_type, expected):
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "tools": [{"type": tool_type}],
    }
    request = ResponsesRequest.model_validate(payload)

    assert request.tools == [{"type": expected}]


@pytest.mark.parametrize("tool_choice", [{"type": "web_search"}, {"type": "web_search_preview"}])
def test_responses_normalizes_tool_choice_web_search_preview(tool_choice):
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "tool_choice": tool_choice,
    }
    request = ResponsesRequest.model_validate(payload)

    assert request.tool_choice == {"type": "web_search"}


def test_responses_rejects_invalid_include_value():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "include": ["message.output_text.logprobs", "bad.include.value"],
    }
    with pytest.raises(ValueError, match="Unsupported include value"):
        ResponsesRequest.model_validate(payload)


def test_responses_accepts_known_include_values():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "include": ["reasoning.encrypted_content", "web_search_call.action.sources"],
    }
    request = ResponsesRequest.model_validate(payload)
    assert request.include == ["reasoning.encrypted_content", "web_search_call.action.sources"]


def test_responses_rejects_conversation_previous_response_id():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "conversation": "conv_1",
        "previous_response_id": "resp_1",
    }
    with pytest.raises(ValueError, match="previous_response_id is not supported"):
        ResponsesRequest.model_validate(payload)


def test_v1_messages_convert_to_responses_input():
    payload = {
        "model": "gpt-5.1",
        "messages": [{"role": "user", "content": "hi"}],
    }
    request = V1ResponsesRequest.model_validate(payload).to_responses_request()

    assert request.instructions == ""
    assert request.input == [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]


def test_v1_system_message_moves_to_instructions():
    payload = {
        "model": "gpt-5.1",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ],
    }
    request = V1ResponsesRequest.model_validate(payload).to_responses_request()

    assert request.instructions == "sys"
    assert request.input == [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]


def test_v1_instructions_merge():
    payload = {
        "model": "gpt-5.1",
        "instructions": "primary",
        "messages": [{"role": "developer", "content": "secondary"}],
    }
    request = V1ResponsesRequest.model_validate(payload).to_responses_request()

    assert request.instructions == "primary\nsecondary"


def test_v1_messages_and_input_conflict():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [{"role": "user", "content": "hi"}],
        "messages": [{"role": "user", "content": "hi"}],
    }
    with pytest.raises(ValueError, match="either 'input' or 'messages'"):
        V1ResponsesRequest.model_validate(payload)


def test_v1_input_string_passthrough():
    payload = {"model": "gpt-5.1", "input": "hello"}
    request = V1ResponsesRequest.model_validate(payload).to_responses_request()

    assert request.input == [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}]


def test_v1_rejects_builtin_tools():
    payload = {"model": "gpt-5.1", "input": [], "tools": [{"type": "image_generation"}]}
    with pytest.raises(ValidationError, match="Unsupported tool type"):
        V1ResponsesRequest.model_validate(payload)


def test_v1_compact_messages_convert():
    payload = {
        "model": "gpt-5.1",
        "messages": [{"role": "user", "content": "hi"}],
    }
    request = V1ResponsesCompactRequest.model_validate(payload).to_compact_request()

    assert isinstance(request, ResponsesCompactRequest)
    assert request.instructions == ""
    assert request.input == [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]


def test_v1_compact_input_string_passthrough():
    payload = {"model": "gpt-5.1", "input": "hello"}
    request = V1ResponsesCompactRequest.model_validate(payload).to_compact_request()

    assert request.input == [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}]


def test_responses_normalizes_assistant_input_text_to_output_text():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [
            {"role": "assistant", "content": [{"type": "input_text", "text": "Prior answer"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "Continue"}]},
        ],
    }
    request = ResponsesRequest.model_validate(payload)

    assert request.input == [
        {"role": "assistant", "content": [{"type": "output_text", "text": "Prior answer"}]},
        {"role": "user", "content": [{"type": "input_text", "text": "Continue"}]},
    ]


def test_v1_assistant_messages_normalize_to_output_text():
    payload = {
        "model": "gpt-5.1",
        "messages": [
            {"role": "assistant", "content": "Prior answer"},
            {"role": "user", "content": "Continue"},
        ],
    }
    request = V1ResponsesRequest.model_validate(payload).to_responses_request()

    assert request.input == [
        {"role": "assistant", "content": [{"type": "output_text", "text": "Prior answer"}]},
        {"role": "user", "content": [{"type": "input_text", "text": "Continue"}]},
    ]


def test_responses_normalizes_assistant_object_content_to_array():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [{"role": "assistant", "content": {"type": "input_text", "text": "Prior answer"}}],
    }
    request = ResponsesRequest.model_validate(payload)

    assert request.input == [{"role": "assistant", "content": [{"type": "output_text", "text": "Prior answer"}]}]


def test_responses_normalizes_tool_role_input_item_to_function_call_output():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": [{"type": "input_text", "text": '{"ok":true}'}],
            }
        ],
    }
    request = ResponsesRequest.model_validate(payload)

    assert request.input == [{"type": "function_call_output", "call_id": "call_1", "output": '{"ok":true}'}]


def test_responses_normalizes_tool_role_input_item_with_camel_call_id():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [
            {
                "role": "tool",
                "toolCallId": "call_1",
                "content": [{"type": "input_text", "text": '{"ok":true}'}],
            }
        ],
    }
    request = ResponsesRequest.model_validate(payload)

    assert request.input == [{"type": "function_call_output", "call_id": "call_1", "output": '{"ok":true}'}]


def test_responses_normalizes_tool_role_input_item_preserves_part_order_without_delimiters():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": [
                    {"type": "input_text", "text": '{"a":'},
                    {"type": "input_text", "text": ""},
                    {"type": "input_text", "text": "1}"},
                ],
            }
        ],
    }
    request = ResponsesRequest.model_validate(payload)

    assert request.input == [{"type": "function_call_output", "call_id": "call_1", "output": '{"a":1}'}]


def test_responses_normalizes_tool_role_input_item_preserves_output_field():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [
            {
                "role": "tool",
                "call_id": "call_1",
                "output": '{"ok":true}',
            }
        ],
    }
    request = ResponsesRequest.model_validate(payload)

    assert request.input == [{"type": "function_call_output", "call_id": "call_1", "output": '{"ok":true}'}]


def test_responses_normalizes_tool_role_input_item_uses_content_when_output_is_null():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [
            {
                "role": "tool",
                "call_id": "call_1",
                "output": None,
                "content": '{"ok":true}',
            }
        ],
    }
    request = ResponsesRequest.model_validate(payload)

    assert request.input == [{"type": "function_call_output", "call_id": "call_1", "output": '{"ok":true}'}]


def test_v1_tool_messages_normalize_to_function_call_output():
    payload = {
        "model": "gpt-5.1",
        "messages": [
            {"role": "assistant", "content": "Running tool."},
            {"role": "tool", "tool_call_id": "call_1", "content": '{"ok":true}'},
            {"role": "user", "content": "Continue"},
        ],
    }
    request = V1ResponsesRequest.model_validate(payload).to_responses_request()

    assert request.input == [
        {"role": "assistant", "content": [{"type": "output_text", "text": "Running tool."}]},
        {"type": "function_call_output", "call_id": "call_1", "output": '{"ok":true}'},
        {"role": "user", "content": [{"type": "input_text", "text": "Continue"}]},
    ]


def test_v1_assistant_tool_calls_normalize_to_function_call():
    payload = {
        "model": "gpt-5.1",
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
            {"role": "user", "content": "Continue"},
        ],
    }
    request = V1ResponsesRequest.model_validate(payload).to_responses_request()

    assert request.input == [
        {"role": "assistant", "content": [{"type": "output_text", "text": ""}]},
        {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": '{"q":"abc"}'},
        {"type": "function_call_output", "call_id": "call_1", "output": '{"ok":true}'},
        {"role": "user", "content": [{"type": "input_text", "text": "Continue"}]},
    ]


def test_v1_tool_message_accepts_tool_call_id_camel_case():
    payload = {
        "model": "gpt-5.1",
        "messages": [
            {"role": "tool", "toolCallId": "call_1", "content": '{"ok":true}'},
            {"role": "user", "content": "Continue"},
        ],
    }
    request = V1ResponsesRequest.model_validate(payload).to_responses_request()

    assert request.input == [
        {"type": "function_call_output", "call_id": "call_1", "output": '{"ok":true}'},
        {"role": "user", "content": [{"type": "input_text", "text": "Continue"}]},
    ]


def test_v1_tool_message_requires_tool_call_id():
    payload = {
        "model": "gpt-5.1",
        "messages": [
            {"role": "tool", "content": '{"ok":true}'},
            {"role": "user", "content": "Continue"},
        ],
    }
    with pytest.raises(ClientPayloadError, match="tool messages must include 'tool_call_id'"):
        V1ResponsesRequest.model_validate(payload).to_responses_request()


def test_v1_rejects_unknown_message_role():
    payload = {
        "model": "gpt-5.1",
        "messages": [
            {"role": "moderator", "content": "Nope"},
            {"role": "user", "content": "Continue"},
        ],
    }
    with pytest.raises(ClientPayloadError, match="Unsupported message role"):
        V1ResponsesRequest.model_validate(payload).to_responses_request()
