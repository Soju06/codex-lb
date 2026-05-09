from __future__ import annotations

from typing import Mapping, cast

import pytest
from pydantic import ValidationError

from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest, extract_input_file_ids
from app.core.openai.v1_requests import V1ResponsesCompactRequest, V1ResponsesRequest
from app.core.types import JsonValue


def test_responses_requires_instructions():
    with pytest.raises(ValidationError):
        ResponsesRequest.model_validate({"model": "gpt-5.1", "input": []})


def test_responses_requires_input():
    with pytest.raises(ValidationError):
        ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi"})


def test_store_true_is_coerced_to_false():
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "store": True}
    request = ResponsesRequest.model_validate(payload)
    assert request.store is False


def test_store_omitted_defaults_to_false():
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    request = ResponsesRequest.model_validate(payload)

    assert request.store is False
    assert request.to_payload()["store"] is False


def test_store_false_is_preserved():
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "store": False}
    request = ResponsesRequest.model_validate(payload)

    assert request.to_payload()["store"] is False


def test_compact_store_true_is_coerced_to_false():
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "store": True}
    request = ResponsesCompactRequest.model_validate(payload)
    assert request.store is False


def test_compact_store_omitted_defaults_to_false():
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    request = ResponsesCompactRequest.model_validate(payload)

    assert request.store is False
    assert "store" not in request.to_payload()


def test_compact_store_false_is_preserved():
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "store": False}
    request = ResponsesCompactRequest.model_validate(payload)

    assert request.store is False
    assert "store" not in request.to_payload()


def test_known_unsupported_upstream_fields_are_stripped():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "max_output_tokens": 32000,
        "prompt_cache_retention": "4h",
        "safety_identifier": "safe_123",
        "temperature": 0.2,
        "custom_field": "kept",
    }
    request = ResponsesRequest.model_validate(payload)

    dumped = request.to_payload()
    assert "max_output_tokens" not in dumped
    assert "prompt_cache_retention" not in dumped
    assert "safety_identifier" not in dumped
    assert "temperature" not in dumped
    assert dumped["custom_field"] == "kept"


def test_responses_preserves_service_tier():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "service_tier": "priority",
    }
    request = ResponsesRequest.model_validate(payload)

    dumped = request.to_payload()
    assert dumped["service_tier"] == "priority"


def test_responses_normalizes_fast_service_tier_to_priority_for_upstream():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "service_tier": "fast",
    }
    request = ResponsesRequest.model_validate(payload)

    assert request.service_tier == "priority"
    dumped = request.to_payload()
    assert dumped["service_tier"] == "priority"


def test_compact_known_unsupported_upstream_fields_are_stripped():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "prompt_cache_retention": "4h",
        "safety_identifier": "safe_123",
        "temperature": 0.2,
    }
    request = ResponsesCompactRequest.model_validate(payload)

    dumped = request.to_payload()
    assert "prompt_cache_retention" not in dumped
    assert "safety_identifier" not in dumped
    assert "temperature" not in dumped


def test_compact_normalizes_fast_service_tier_to_priority_for_upstream():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "service_tier": "fast",
    }
    request = ResponsesCompactRequest.model_validate(payload)

    assert request.service_tier == "priority"
    dumped = request.to_payload()
    assert dumped["service_tier"] == "priority"


def test_openai_prompt_cache_aliases_are_normalized():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "promptCacheKey": "thread_123",
        "promptCacheRetention": "4h",
    }
    request = ResponsesRequest.model_validate(payload)

    dumped = request.to_payload()
    assert dumped["prompt_cache_key"] == "thread_123"
    assert "prompt_cache_retention" not in dumped
    assert "promptCacheKey" not in dumped
    assert "promptCacheRetention" not in dumped


def test_settings_default_prompt_cache_affinity_ttl_is_1800():
    from app.core.config.settings import Settings

    settings = Settings()

    assert settings.openai_cache_affinity_max_age_seconds == 1800


def test_responses_to_payload_canonicalizes_tool_order_and_object_keys():
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [],
            "tools": [
                {
                    "type": "function",
                    "name": "zeta",
                    "parameters": {"required": [], "type": "object", "properties": {}},
                    "description": "later",
                },
                {
                    "description": "first",
                    "parameters": {"properties": {}, "required": [], "type": "object"},
                    "type": "function",
                    "name": "alpha",
                },
            ],
        }
    )

    dumped = request.to_payload()
    tools = cast(list[JsonValue], dumped["tools"])
    first_tool = cast(Mapping[str, JsonValue], tools[0])
    parameters = cast(Mapping[str, JsonValue], first_tool["parameters"])
    assert first_tool["name"] == "alpha"
    assert list(first_tool.keys()) == ["description", "name", "parameters", "type"]
    assert list(parameters.keys()) == ["properties", "required", "type"]


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


def test_provider_thinking_aliases_are_normalized():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "thinking": {"type": "enabled", "budget_tokens": 2048},
        "enable_thinking": True,
    }
    request = ResponsesRequest.model_validate(payload)

    dumped = request.to_payload()
    assert dumped["reasoning"] == {"effort": "medium"}
    assert "thinking" not in dumped
    assert "enable_thinking" not in dumped


def test_explicit_reasoning_wins_over_provider_thinking_aliases():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "reasoning": {"effort": "high"},
        "thinking": {"type": "enabled"},
        "enable_thinking": True,
    }
    request = ResponsesRequest.model_validate(payload)

    dumped = request.to_payload()
    assert dumped["reasoning"] == {"effort": "high"}
    assert "thinking" not in dumped
    assert "enable_thinking" not in dumped


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


def test_v1_responses_preserves_service_tier():
    payload = {
        "model": "gpt-5.1",
        "input": "hello",
        "service_tier": "priority",
    }
    request = V1ResponsesRequest.model_validate(payload).to_responses_request()

    dumped = request.to_payload()
    assert dumped["service_tier"] == "priority"


def test_v1_responses_normalizes_fast_service_tier_to_priority_for_upstream():
    payload = {
        "model": "gpt-5.1",
        "input": "hello",
        "service_tier": "fast",
    }
    request = V1ResponsesRequest.model_validate(payload).to_responses_request()

    assert request.service_tier == "priority"
    dumped = request.to_payload()
    assert dumped["service_tier"] == "priority"


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


@pytest.mark.parametrize(
    "tool_payload",
    [
        {"type": "image_generation"},
        {
            "type": "computer_use_preview",
            "display_width": 1024,
            "display_height": 768,
            "environment": "browser",
        },
        {"type": "computer_use", "display_width": 1024, "display_height": 768, "environment": "browser"},
        {"type": "file_search", "vector_store_ids": ["vs_dummy"]},
        {"type": "code_interpreter", "container": {"type": "auto"}},
    ],
)
def test_responses_accepts_builtin_tool_passthrough(tool_payload):
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "tools": [tool_payload],
    }
    request = ResponsesRequest.model_validate(payload)

    assert request.tools == [tool_payload]


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


def test_responses_accepts_previous_response_id_without_conversation():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "previous_response_id": "  resp_1  ",
    }
    request = ResponsesRequest.model_validate(payload)

    assert request.previous_response_id == "resp_1"
    assert request.to_payload()["previous_response_id"] == "resp_1"


def test_responses_rejects_conversation_previous_response_id():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "conversation": "conv_1",
        "previous_response_id": "resp_1",
    }
    with pytest.raises(ValueError, match="either 'conversation' or 'previous_response_id'"):
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


@pytest.mark.parametrize(
    "tool_payload",
    [
        {"type": "image_generation"},
        {
            "type": "computer_use_preview",
            "display_width": 1024,
            "display_height": 768,
            "environment": "browser",
        },
        {"type": "computer_use", "display_width": 1024, "display_height": 768, "environment": "browser"},
        {"type": "file_search", "vector_store_ids": ["vs_dummy"]},
        {"type": "code_interpreter", "container": {"type": "auto"}},
    ],
)
def test_v1_responses_accepts_builtin_tools(tool_payload):
    payload = {"model": "gpt-5.1", "input": [], "tools": [tool_payload]}
    request = V1ResponsesRequest.model_validate(payload).to_responses_request()

    assert request.tools == [tool_payload]


def test_compact_strips_tool_fields():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "tools": [{"type": "image_generation"}],
        "tool_choice": {"type": "image_generation"},
        "parallel_tool_calls": True,
    }
    request = ResponsesCompactRequest.model_validate(payload)

    dumped = request.to_payload()
    assert "tools" not in dumped
    assert "tool_choice" not in dumped
    assert "parallel_tool_calls" not in dumped


def test_v1_compact_strips_tool_fields():
    payload = {
        "model": "gpt-5.1",
        "input": "hello",
        "tools": [{"type": "image_generation"}],
        "tool_choice": {"type": "image_generation"},
        "parallel_tool_calls": True,
    }
    request = V1ResponsesCompactRequest.model_validate(payload).to_compact_request()

    dumped = request.to_payload()
    assert "tools" not in dumped
    assert "tool_choice" not in dumped
    assert "parallel_tool_calls" not in dumped


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


def test_v1_compact_reasoning_passthrough():
    payload = {
        "model": "gpt-5.1",
        "input": "hello",
        "reasoning": {"effort": "high"},
    }
    request = V1ResponsesCompactRequest.model_validate(payload).to_compact_request()

    assert request.reasoning is not None
    assert request.reasoning.effort == "high"


def test_v1_compact_store_omitted_defaults_to_false():
    payload = {"model": "gpt-5.1", "input": "hello"}
    request = V1ResponsesCompactRequest.model_validate(payload).to_compact_request()

    assert request.store is False
    assert "store" not in request.to_payload()


def test_v1_compact_store_true_is_coerced_to_false():
    payload = {"model": "gpt-5.1", "input": "hello", "store": True}
    request = V1ResponsesCompactRequest.model_validate(payload)
    compact = request.to_compact_request()
    assert compact.store is False


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


def test_responses_accepts_input_file_with_file_id_content_item():
    """Regression: ``input_file`` content items with a ``file_id`` were
    previously rejected. They are now allowed and forwarded verbatim so
    callers can reference uploads registered through the
    ``POST /backend-api/files`` upload protocol."""
    content = [
        {"type": "input_text", "text": "Summarize this file."},
        {"type": "input_file", "file_id": "file_abc"},
    ]
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [{"role": "user", "content": content}],
    }
    request = ResponsesRequest.model_validate(payload)
    assert request.input == [{"role": "user", "content": content}]


def test_responses_compact_accepts_input_file_with_file_id_content_item():
    content = [
        {"type": "input_text", "text": "Summarize this file."},
        {"type": "input_file", "file_id": "file_abc"},
    ]
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [{"role": "user", "content": content}],
    }
    request = ResponsesCompactRequest.model_validate(payload)
    assert request.input == [{"role": "user", "content": content}]


def test_responses_accepts_top_level_input_file_with_file_id():
    """Top-level ``input_file`` items (sibling of role messages) were
    also rejected; they should now be forwarded as-is."""
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
            {"type": "input_file", "file_id": "file_root"},
        ],
    }
    request = ResponsesRequest.model_validate(payload)
    forwarded = request.input
    assert isinstance(forwarded, list)
    assert {"type": "input_file", "file_id": "file_root"} in forwarded


def test_extract_input_file_ids_string_input_returns_empty_set():
    assert extract_input_file_ids("Hello world") == set()


def test_extract_input_file_ids_finds_top_level_and_nested_ids():
    input_value: list[JsonValue] = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Summarize."},
                {"type": "input_file", "file_id": "file_a"},
            ],
        },
        {"type": "input_file", "file_id": "file_b"},
        # Duplicates and missing/blank ids are filtered out.
        {"type": "input_file", "file_id": "file_a"},
        {"type": "input_file", "file_id": ""},
        {"type": "input_file"},
    ]
    assert extract_input_file_ids(input_value) == {"file_a", "file_b"}


def test_extract_input_file_ids_ignores_non_input_file_types():
    input_value: list[JsonValue] = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "hello"},
                {"type": "output_text", "text": "ignore"},
                {"type": "input_text", "text": "ignore"},
            ],
        },
    ]
    assert extract_input_file_ids(input_value) == set()


def test_extract_input_file_ids_recognizes_input_image_file_id():
    input_value: list[JsonValue] = [
        {
            "role": "user",
            "content": [
                {"type": "input_image", "file_id": "file_image"},
            ],
        },
    ]

    assert extract_input_file_ids(input_value) == {"file_image"}


def test_extract_input_file_ids_recognizes_input_image_sediment_uri():
    input_value: list[JsonValue] = [
        {
            "role": "user",
            "content": [
                {"type": "input_image", "image_url": "sediment://file_image"},
            ],
        },
    ]

    assert extract_input_file_ids(input_value) == {"file_image"}


def test_extract_input_file_ids_ignores_input_image_non_sediment_url():
    input_value: list[JsonValue] = [
        {
            "role": "user",
            "content": [
                {"type": "input_image", "image_url": "https://example.com/file.png"},
                {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
            ],
        },
    ]

    assert extract_input_file_ids(input_value) == set()


def test_extract_input_file_ids_ignores_input_file_without_file_id():
    input_value: list[JsonValue] = [
        {
            "role": "user",
            "content": [
                {"type": "input_file"},
                {"type": "input_file", "file_id": ""},
            ],
        },
    ]

    assert extract_input_file_ids(input_value) == set()
