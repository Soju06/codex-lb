from __future__ import annotations

from typing import Mapping, cast

import pytest
from pydantic import ValidationError

from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.requests import (
    ResponsesCompactRequest,
    ResponsesRequest,
    _input_image_file_reference,
    extract_input_file_ids,
    extract_input_image_file_references,
)
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


def test_compact_client_metadata_is_stripped():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "client_metadata": {"x-codex-installation-id": "client-installation"},
    }
    request = ResponsesCompactRequest.model_validate(payload)

    assert "client_metadata" not in request.to_payload()


def test_known_unsupported_upstream_fields_are_stripped():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "max_output_tokens": 32000,
        "metadata": {"client": "cursor"},
        "prompt_cache_retention": "4h",
        "safety_identifier": "safe_123",
        "temperature": 0.2,
        "top_p": 0.9,
        "truncation": "auto",
        "user": "cursor-user",
        "custom_field": "kept",
    }
    request = ResponsesRequest.model_validate(payload)

    dumped = request.to_payload()
    assert "max_output_tokens" not in dumped
    assert "metadata" not in dumped
    assert "prompt_cache_retention" not in dumped
    assert "safety_identifier" not in dumped
    assert "temperature" not in dumped
    assert "top_p" not in dumped
    assert "truncation" not in dumped
    assert "user" not in dumped
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
        "metadata": {"client": "cursor"},
        "prompt_cache_retention": "4h",
        "safety_identifier": "safe_123",
        "temperature": 0.2,
        "top_p": 0.9,
        "user": "cursor-user",
    }
    request = ResponsesCompactRequest.model_validate(payload)

    dumped = request.to_payload()
    assert "metadata" not in dumped
    assert "prompt_cache_retention" not in dumped
    assert "safety_identifier" not in dumped
    assert "temperature" not in dumped
    assert "top_p" not in dumped
    assert "user" not in dumped


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


def test_provider_thinking_string_alias_accepts_catalog_advertised_efforts():
    # GPT-5.6 catalog entries advertise ``max`` and ``ultra``
    # (codex-rs/models-manager/models.json at rust-v0.144.1); the string-form
    # thinking alias must accept every catalog-advertised effort.
    for effort in ("low", "medium", "high", "xhigh", "max", "ultra"):
        payload = {
            "model": "gpt-5.6-sol",
            "instructions": "hi",
            "input": [],
            "thinking": effort,
        }
        request = ResponsesRequest.model_validate(payload)

        dumped = request.to_payload()
        assert dumped["reasoning"] == {"effort": effort}
        assert "thinking" not in dumped


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


def test_responses_input_system_message_moves_to_instructions():
    payload = {
        "model": "gpt-5.1",
        "instructions": "primary",
        "input": [
            {"type": "message", "role": "system", "content": [{"type": "input_text", "text": "sys"}]},
            {"type": "message", "role": "developer", "content": "dev"},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        ],
    }
    request = ResponsesRequest.model_validate(payload)

    assert request.instructions == "primary\nsys\ndev"
    assert request.input == [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}]


@pytest.mark.parametrize("request_type", [ResponsesRequest, ResponsesCompactRequest])
def test_responses_input_additional_tools_item_is_preserved(request_type):
    additional_tools = {
        "type": "additional_tools",
        "role": "developer",
        "tools": [
            {
                "type": "custom",
                "name": "shell",
                "description": "Run shell commands",
                "format": {"type": "grammar", "syntax": "lark"},
            }
        ],
    }
    custom_tool_call = {
        "type": "custom_tool_call",
        "call_id": "call_shell_1",
        "name": "shell",
        "input": "pwd",
    }
    custom_tool_output = {
        "type": "custom_tool_call_output",
        "call_id": "call_shell_1",
        "output": "/repo",
    }
    payload = {
        "model": "gpt-5.6-sol",
        "instructions": "",
        "input": [
            additional_tools,
            {"type": "message", "role": "developer", "content": "dev"},
            custom_tool_call,
            custom_tool_output,
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "inspect"}]},
        ],
    }

    request = request_type.model_validate(payload)

    assert request.instructions == ""
    assert request.input == [
        additional_tools,
        {"type": "message", "role": "developer", "content": "dev"},
        custom_tool_call,
        custom_tool_output,
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "inspect"}]},
    ]
    assert request.to_payload()["input"] == request.input


def test_responses_input_system_message_keeps_user_text_parts():
    payload = {
        "model": "gpt-5.1",
        "instructions": "primary",
        "input": [
            {
                "type": "message",
                "role": "system",
                "content": [{"type": "input_text", "text": "sys"}],
            },
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "hello"},
                    {"type": "input_file", "file_id": "file_123"},
                ],
            },
        ],
    }
    request = ResponsesRequest.model_validate(payload)

    assert request.instructions == "primary\nsys"
    assert request.input == [
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "hello"},
                {"type": "input_file", "file_id": "file_123"},
            ],
        }
    ]


def test_responses_input_system_message_preserves_non_text_parts():
    payload = {
        "model": "gpt-5.1",
        "instructions": "primary",
        "input": [
            {
                "type": "message",
                "role": "system",
                "content": [
                    {"type": "input_text", "text": "sys"},
                    {"type": "input_file", "file_id": "file_123"},
                    {"type": "input_image", "image_url": "sediment://file_456"},
                ],
            },
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        ],
    }
    request = ResponsesRequest.model_validate(payload)

    assert request.instructions == "primary\nsys"
    assert request.input == [
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_file", "file_id": "file_123"},
                {"type": "input_image", "image_url": "sediment://file_456"},
            ],
        },
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
    ]
    assert extract_input_file_ids(request.input) == {"file_123", "file_456"}
    assert [ref.file_id for ref in extract_input_image_file_references(request.input)] == ["file_456"]


def test_responses_input_developer_message_preserves_single_non_text_part():
    payload = {
        "model": "gpt-5.1",
        "instructions": "primary",
        "input": [
            {
                "type": "message",
                "role": "developer",
                "content": {"type": "input_file", "file_id": "file_123"},
            }
        ],
    }
    request = ResponsesRequest.model_validate(payload)

    assert request.instructions == "primary"
    assert request.input == [
        {
            "type": "message",
            "role": "user",
            "content": {"type": "input_file", "file_id": "file_123"},
        }
    ]
    assert extract_input_file_ids(request.input) == {"file_123"}


def test_responses_compact_input_system_message_moves_to_instructions():
    payload = {
        "model": "gpt-5.1",
        "instructions": "primary",
        "input": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "compact me"},
        ],
    }
    request = ResponsesCompactRequest.model_validate(payload)

    assert request.instructions == "primary\nsys"
    assert request.input == [{"role": "user", "content": "compact me"}]


def test_responses_compact_to_payload_strips_late_system_message():
    request = ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "primary",
            "input": [{"role": "user", "content": "compact me"}],
        }
    )
    request.input = [
        {"role": "system", "content": "late sys"},
        {"role": "user", "content": "compact me"},
    ]

    dumped = request.to_payload()

    assert dumped["instructions"] == "primary\nlate sys"
    assert dumped["input"] == [{"role": "user", "content": "compact me"}]


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
        "text": {"verbosity": "low"},
    }
    request = ResponsesCompactRequest.model_validate(payload)

    dumped = request.to_payload()
    assert "tools" not in dumped
    assert "tool_choice" not in dumped
    assert dumped["parallel_tool_calls"] is False
    assert "text" not in dumped


def test_responses_strips_poisoned_local_compact_fallback_items():
    poisoned_message = {
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "output_text",
                "text": "Local compact fallback preserved the latest encrypted reasoning state.",
            }
        ],
    }
    poisoned_compaction = {"type": "compaction", "encrypted_content": "bad-local-summary"}
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [
            {"role": "user", "content": "before"},
            poisoned_message,
            poisoned_compaction,
            {"role": "user", "content": "after"},
        ],
    }

    request = ResponsesRequest.model_validate(payload)

    assert request.to_payload()["input"] == [
        {"role": "user", "content": "before"},
        {"role": "user", "content": "after"},
    ]


def test_compact_strips_poisoned_local_compact_fallback_items():
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Local compact fallback preserved the latest encrypted reasoning state.",
                    }
                ],
            },
            {"type": "compaction", "encrypted_content": "bad-local-summary"},
            {"role": "user", "content": "continue"},
        ],
    }

    request = ResponsesCompactRequest.model_validate(payload)

    assert request.to_payload()["input"] == [{"role": "user", "content": "continue"}]


def test_compact_does_not_trim_many_small_input_items_for_upstream():
    input_items = [{"role": "user", "content": f"item {idx}"} for idx in range(356)]
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": input_items,
    }

    request = ResponsesCompactRequest.model_validate(payload)
    dumped = request.to_payload()
    dumped_input = dumped["input"]

    assert isinstance(dumped_input, list)
    assert dumped_input == input_items


def test_compact_trims_oversized_input_by_estimated_tokens_with_head_tail_and_marker():
    input_items = [
        {"role": "user", "content": "initial goal and instructions"},
        {"role": "assistant", "content": "x" * 500_000},
        {"role": "user", "content": "current plan"},
        {"role": "user", "content": "latest request"},
    ]
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": input_items,
    }

    request = ResponsesCompactRequest.model_validate(payload)
    dumped = request.to_payload()
    dumped_input = dumped["input"]

    assert isinstance(dumped_input, list)
    assert dumped_input[0] == input_items[0]
    assert dumped_input[-2:] == input_items[-2:]
    marker = cast(Mapping[str, object], dumped_input[1])
    assert marker["type"] == "message"
    assert marker["role"] == "user"
    content = cast(list[Mapping[str, str]], marker["content"])
    marker_text = content[0]["text"]
    assert marker_text.startswith("[compact trim] Omitted 1 input items")
    assert "estimated tokens" in marker_text
    assert "initial context, most recent context, and compact state anchors were preserved" in marker_text
    assert "codex-lb" not in marker_text


def test_compact_trimming_preserves_oversized_responses_lite_prefix():
    additional_tools = {
        "type": "additional_tools",
        "role": "developer",
        "tools": [
            {
                "type": "custom",
                "name": "shell",
                "description": "x" * 60_000,
                "format": {"type": "grammar", "syntax": "lark"},
            }
        ],
    }
    developer_instructions = {
        "type": "message",
        "role": "developer",
        "content": "preserve these base instructions",
    }
    input_items = [
        additional_tools,
        developer_instructions,
        {"role": "assistant", "content": "middle context " + "y" * 500_000},
        {"role": "user", "content": "latest request"},
    ]

    request = ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "",
            "input": input_items,
        }
    )

    dumped_input = request.to_payload()["input"]
    assert isinstance(dumped_input, list)
    assert dumped_input[0:2] == [additional_tools, developer_instructions]
    assert input_items[2] not in dumped_input
    assert dumped_input[-1] == input_items[-1]


def test_compact_trimming_drops_oversized_leading_item():
    input_items = [
        {"role": "assistant", "content": "x" * 500_000},
        {"role": "user", "content": "latest request"},
    ]
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": input_items,
    }

    request = ResponsesCompactRequest.model_validate(payload)
    dumped = request.to_payload()
    dumped_input = dumped["input"]

    assert isinstance(dumped_input, list)
    assert dumped_input[-1] == input_items[-1]
    assert input_items[0] not in dumped_input
    marker = cast(Mapping[str, object], dumped_input[0])
    assert marker["type"] == "message"
    content = cast(list[Mapping[str, str]], marker["content"])
    assert content[0]["text"].startswith("[compact trim] Omitted 1 input items")


def test_compact_trimming_preserves_oversized_latest_item():
    input_items = [
        {"role": "user", "content": "initial instructions"},
        {"role": "assistant", "content": "middle context " + "y" * 500_000},
        {"role": "user", "content": "latest request " + "x" * 500_000},
    ]
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": input_items,
    }

    request = ResponsesCompactRequest.model_validate(payload)
    dumped = request.to_payload()
    dumped_input = dumped["input"]

    assert isinstance(dumped_input, list)
    assert dumped_input[0] == input_items[0]
    assert dumped_input[-1] == input_items[-1]
    assert input_items[1] not in dumped_input
    marker = cast(Mapping[str, object], dumped_input[1])
    assert marker["type"] == "message"
    content = cast(list[Mapping[str, str]], marker["content"])
    assert content[0]["text"].startswith("[compact trim] Omitted 1 input items")


def test_compact_trimming_preserves_codex_goal_context_anchor_from_middle():
    goal_context = {
        "type": "message",
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": (
                    '<codex_internal_context source="goal">\n'
                    "Continue working toward the active thread goal.\n"
                    "<objective>fix the live incident</objective>\n"
                    "</codex_internal_context>"
                ),
            }
        ],
    }
    input_items = [
        {"role": "user", "content": "initial instructions"},
        {"role": "assistant", "content": "x" * 300_000},
        goal_context,
        {"role": "assistant", "content": "y" * 300_000},
        {"role": "user", "content": "latest request"},
    ]
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": input_items,
    }

    request = ResponsesCompactRequest.model_validate(payload)
    dumped = request.to_payload()
    dumped_input = dumped["input"]

    assert isinstance(dumped_input, list)
    assert dumped_input[0] == input_items[0]
    assert goal_context in dumped_input
    assert dumped_input[-1] == input_items[-1]


def test_compact_trimming_preserves_plan_and_goal_tool_call_outputs():
    update_plan_call = {
        "type": "function_call",
        "name": "update_plan",
        "call_id": "call_plan",
        "arguments": '{"plan":[{"step":"keep state","status":"in_progress"}]}',
    }
    update_plan_output = {
        "type": "function_call_output",
        "call_id": "call_plan",
        "output": "Plan updated",
    }
    unrelated_output = {
        "type": "function_call_output",
        "call_id": "call_other",
        "output": "large unrelated output " + "z" * 500_000,
    }
    input_items = [
        {"role": "user", "content": "initial instructions"},
        {"role": "assistant", "content": "x" * 300_000},
        update_plan_call,
        unrelated_output,
        update_plan_output,
        {"role": "user", "content": "latest request"},
    ]
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": input_items,
    }

    request = ResponsesCompactRequest.model_validate(payload)
    dumped = request.to_payload()
    dumped_input = dumped["input"]

    assert isinstance(dumped_input, list)
    assert update_plan_call in dumped_input
    assert update_plan_output in dumped_input
    assert unrelated_output not in dumped_input


def test_compact_trimming_keeps_selected_tool_outputs_with_matching_calls():
    tool_call = {
        "type": "function_call",
        "name": "lookup",
        "call_id": "call_tail",
        "arguments": "{}",
    }
    tool_output = {
        "type": "function_call_output",
        "call_id": "call_tail",
        "output": "tail output",
    }
    input_items = [
        {"role": "user", "content": "initial instructions"},
        {"role": "assistant", "content": "x" * 500_000},
        tool_call,
        {"role": "assistant", "content": "y" * 500_000},
        tool_output,
        {"role": "user", "content": "latest request"},
    ]
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": input_items,
    }

    request = ResponsesCompactRequest.model_validate(payload)
    dumped = request.to_payload()
    dumped_input = dumped["input"]

    assert isinstance(dumped_input, list)
    assert tool_call in dumped_input
    assert tool_output in dumped_input


def test_compact_trimming_keeps_selected_tool_calls_with_matching_outputs():
    tool_call = {
        "type": "function_call",
        "name": "lookup",
        "call_id": "call_head",
        "arguments": "{}",
    }
    tool_output = {
        "type": "function_call_output",
        "call_id": "call_head",
        "output": "head output",
    }
    input_items = [
        tool_call,
        {"role": "assistant", "content": "x" * 500_000},
        tool_output,
        {"role": "assistant", "content": "y" * 500_000},
        {"role": "user", "content": "latest request"},
    ]
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": input_items,
    }

    request = ResponsesCompactRequest.model_validate(payload)
    dumped = request.to_payload()
    dumped_input = dumped["input"]

    assert isinstance(dumped_input, list)
    assert tool_call in dumped_input
    assert tool_output in dumped_input


def test_compact_trimming_reconciles_duplicate_tool_call_ids_by_occurrence():
    first_tool_call = {
        "type": "function_call",
        "name": "shell",
        "call_id": "call_reused",
        "arguments": '{"cmd":"long-running"}',
    }
    first_tool_output = {
        "type": "function_call_output",
        "call_id": "call_reused",
        "output": "huge historical output " + "z" * 500_000,
    }
    latest_tool_call = {
        "type": "function_call",
        "name": "shell",
        "call_id": "call_reused",
        "arguments": '{"cmd":"status"}',
    }
    latest_tool_output = {
        "type": "function_call_output",
        "call_id": "call_reused",
        "output": "Process exited",
    }
    input_items = [
        first_tool_call,
        first_tool_output,
        {"role": "assistant", "content": "x" * 500_000},
        latest_tool_call,
        latest_tool_output,
        {"role": "user", "content": "latest request"},
    ]
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": input_items,
    }

    request = ResponsesCompactRequest.model_validate(payload)
    dumped = request.to_payload()
    dumped_input = dumped["input"]

    assert isinstance(dumped_input, list)
    assert first_tool_call not in dumped_input
    assert first_tool_output not in dumped_input
    assert latest_tool_call in dumped_input
    assert latest_tool_output in dumped_input


def test_compact_trimming_drops_head_tool_call_when_output_exceeds_budget():
    tool_call = {
        "type": "function_call",
        "name": "lookup",
        "call_id": "call_head_large_output",
        "arguments": "{}",
    }
    tool_output = {
        "type": "function_call_output",
        "call_id": "call_head_large_output",
        "output": "huge historical tool output " + "z" * 500_000,
    }
    input_items = [
        tool_call,
        {"role": "assistant", "content": "x" * 500_000},
        tool_output,
        {"role": "user", "content": "latest request"},
    ]
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": input_items,
    }

    request = ResponsesCompactRequest.model_validate(payload)
    dumped = request.to_payload()
    dumped_input = dumped["input"]

    assert isinstance(dumped_input, list)
    assert tool_call not in dumped_input
    assert tool_output not in dumped_input
    assert dumped_input[-1] == input_items[-1]


def test_compact_trimming_drops_selected_tool_outputs_without_matching_calls():
    orphan_output = {
        "type": "function_call_output",
        "call_id": "call_missing",
        "output": "tail output",
    }
    input_items = [
        {"role": "user", "content": "initial instructions"},
        {"role": "assistant", "content": "x" * 500_000},
        {"role": "assistant", "content": "y" * 500_000},
        orphan_output,
        {"role": "user", "content": "latest request"},
    ]
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": input_items,
    }

    request = ResponsesCompactRequest.model_validate(payload)
    dumped = request.to_payload()
    dumped_input = dumped["input"]

    assert isinstance(dumped_input, list)
    assert orphan_output not in dumped_input


def test_compact_trimming_drops_selected_tool_calls_without_matching_outputs():
    orphan_call = {
        "type": "function_call",
        "name": "lookup",
        "call_id": "call_missing_output",
        "arguments": "{}",
    }
    input_items = [
        orphan_call,
        {"role": "assistant", "content": "x" * 500_000},
        {"role": "assistant", "content": "y" * 500_000},
        {"role": "user", "content": "latest request"},
    ]
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": input_items,
    }

    request = ResponsesCompactRequest.model_validate(payload)
    dumped = request.to_payload()
    dumped_input = dumped["input"]

    assert isinstance(dumped_input, list)
    assert orphan_call not in dumped_input


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
    assert dumped["parallel_tool_calls"] is False


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
        {"type": "input_image", "file_id": "file_c"},
        # Duplicates and missing/blank ids are filtered out.
        {"type": "input_file", "file_id": "file_a"},
        {"type": "input_file", "file_id": ""},
        {"type": "input_file"},
    ]
    assert extract_input_file_ids(input_value) == {"file_a", "file_b", "file_c"}


def test_input_image_file_reference_returns_file_id_from_input_image_file_id():
    assert _input_image_file_reference({"type": "input_image", "file_id": "file_img"}) == "file_img"


def test_input_image_file_reference_returns_file_id_from_sediment_url():
    assert _input_image_file_reference({"type": "input_image", "image_url": "sediment://file_img"}) == "file_img"


def test_input_image_file_reference_ignores_data_url():
    assert _input_image_file_reference({"type": "input_image", "image_url": "data:image/png;base64,AAAA"}) is None


def test_input_image_file_reference_ignores_https_url():
    assert _input_image_file_reference({"type": "input_image", "image_url": "https://example.com/a.png"}) is None


def test_extract_input_image_file_references_collects_multi_message_paths():
    input_value: list[JsonValue] = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "ignore"},
                {"type": "input_image", "file_id": "file_a"},
            ],
        },
        {"type": "input_image", "image_url": "sediment://file_b"},
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": [
                {"type": "input_text", "text": "tool image"},
                {"type": "input_image", "file_id": "file_tool"},
            ],
        },
    ]

    references = extract_input_image_file_references(input_value)

    assert [(reference.item_index, reference.content_index, reference.file_id) for reference in references] == [
        (0, 1, "file_a"),
        (1, None, "file_b"),
        (2, None, "file_tool"),
    ]


def test_extract_input_image_file_references_collects_tool_output_paths():
    input_value: list[JsonValue] = [
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": [
                {"type": "input_text", "text": "ignore"},
                {"type": "input_image", "file_id": "file_tool"},
                {"type": "input_image", "image_url": "sediment://file_nested"},
            ],
        }
    ]

    references = extract_input_image_file_references(input_value)

    assert [(reference.item_index, reference.content_index, reference.file_id) for reference in references] == [
        (0, None, "file_tool"),
        (0, None, "file_nested"),
    ]
