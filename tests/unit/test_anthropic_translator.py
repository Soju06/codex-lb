from __future__ import annotations

import pytest

from app.modules.anthropic_compat.schemas import AnthropicMessagesRequest
from app.modules.anthropic_compat.translator import (
    AnthropicTranslationError,
    resolve_prompt_cache_key,
    to_responses_request,
)

pytestmark = pytest.mark.unit


def test_anthropic_request_maps_tool_use_and_tool_result():
    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "system": "You are helpful",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "weather?"}]},
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"city": "NYC"}}],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "sunny"}],
                },
            ],
        }
    )

    translated = to_responses_request(payload)

    assert translated.instructions == "You are helpful"
    assert translated.input == [
        {"role": "user", "content": [{"type": "input_text", "text": "weather?"}]},
        {"type": "function_call", "call_id": "call_1", "name": "get_weather", "arguments": '{"city":"NYC"}'},
        {"type": "function_call_output", "call_id": "call_1", "output": "sunny"},
    ]


def test_anthropic_request_accepts_system_role_and_cache_control_blocks():
    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "messages": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": "Follow system instruction",
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "hi",
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                },
            ],
        }
    )

    translated = to_responses_request(payload)

    assert translated.instructions == "Follow system instruction"
    assert translated.input == [
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
    ]


def test_anthropic_request_strips_anthropic_only_text_block_fields():
    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "hi",
                            "cache_control": {"type": "ephemeral"},
                            "metadata": {"segment": "stable-prefix"},
                        }
                    ],
                }
            ],
        }
    )

    translated = to_responses_request(payload)

    assert translated.input == [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "hi"}],
        }
    ]


def test_anthropic_request_uses_explicit_prompt_cache_key():
    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "prompt_cache_key": "thread_123",
            "messages": [{"role": "user", "content": "hi"}],
        }
    )

    translated = to_responses_request(payload)
    resolution = resolve_prompt_cache_key(payload)

    assert translated.prompt_cache_key == "thread_123"
    assert resolution.key == "thread_123"
    assert resolution.source == "explicit"


def test_anthropic_request_uses_metadata_for_cache_key_without_forwarding_metadata():
    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "metadata": {"conversation_id": "conversation_from_metadata"},
            "messages": [{"role": "user", "content": "hi"}],
        }
    )

    translated = to_responses_request(payload)
    resolution = resolve_prompt_cache_key(payload)
    dumped = translated.model_dump(mode="python", exclude_none=True)

    assert translated.prompt_cache_key == "conversation_from_metadata"
    assert resolution.key == "conversation_from_metadata"
    assert resolution.source == "metadata"
    assert "metadata" not in dumped


def test_anthropic_request_forwards_prompt_cache_retention():
    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "prompt_cache_retention": "24h",
            "messages": [{"role": "user", "content": "hi"}],
        }
    )

    translated = to_responses_request(payload)

    assert translated.prompt_cache_retention == "24h"


def test_anthropic_request_rejects_non_string_prompt_cache_retention():
    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "prompt_cache_retention": 24,
            "messages": [{"role": "user", "content": "hi"}],
        }
    )

    with pytest.raises(AnthropicTranslationError, match="prompt_cache_retention must be a string"):
        to_responses_request(payload)


def test_anthropic_request_derives_prompt_cache_key_from_cache_control_blocks():
    payload_a = AnthropicMessagesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "system": [
                {
                    "type": "text",
                    "text": "Stable prefix",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": "first"}],
        }
    )
    payload_b = AnthropicMessagesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "system": [
                {
                    "type": "text",
                    "text": "Stable prefix",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": "second turn text"}],
        }
    )

    translated_a = to_responses_request(payload_a)
    translated_b = to_responses_request(payload_b)
    resolution_a = resolve_prompt_cache_key(payload_a)
    resolution_b = resolve_prompt_cache_key(payload_b)

    assert translated_a.prompt_cache_key is not None
    assert translated_a.prompt_cache_key.startswith("anthropic-cache:")
    assert translated_a.prompt_cache_key == translated_b.prompt_cache_key
    assert resolution_a.source == "cache_control"
    assert resolution_b.source == "cache_control"


def test_anthropic_request_derives_anchor_prompt_cache_key_without_cache_control():
    payload_a = AnthropicMessagesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "system": "You are Claude Code harness",
            "messages": [
                {"role": "user", "content": "Initial task"},
                {"role": "assistant", "content": "Thinking..."},
                {"role": "user", "content": "follow-up one"},
            ],
        }
    )
    payload_b = AnthropicMessagesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "system": "You are Claude Code harness",
            "messages": [
                {"role": "user", "content": "Initial task"},
                {"role": "assistant", "content": "Thinking..."},
                {"role": "user", "content": "follow-up two changed"},
            ],
        }
    )

    translated_a = to_responses_request(payload_a)
    translated_b = to_responses_request(payload_b)
    resolution_a = resolve_prompt_cache_key(payload_a)
    resolution_b = resolve_prompt_cache_key(payload_b)

    assert translated_a.prompt_cache_key is not None
    assert translated_a.prompt_cache_key.startswith("anthropic-anchor:")
    assert translated_a.prompt_cache_key == translated_b.prompt_cache_key
    assert resolution_a.source == "anchor"
    assert resolution_b.source == "anchor"


def test_anthropic_request_has_no_prompt_cache_key_when_anchor_is_empty():
    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "messages": [{"role": "user", "content": ""}],
        }
    )

    translated = to_responses_request(payload)
    resolution = resolve_prompt_cache_key(payload)

    assert translated.prompt_cache_key is None
    assert resolution.key is None
    assert resolution.source == "none"


def test_anthropic_tool_choice_any_maps_to_required_and_disables_parallel():
    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "messages": [{"role": "user", "content": "hi"}],
            "tool_choice": {"type": "any", "disable_parallel_tool_use": True},
        }
    )

    translated = to_responses_request(payload)

    assert translated.tool_choice == "required"
    assert translated.parallel_tool_calls is False


def test_anthropic_invalid_user_tool_use_block_rejected():
    payload = AnthropicMessagesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "tool_use", "id": "call_1", "name": "bad", "input": {}}],
                }
            ],
        }
    )

    with pytest.raises(AnthropicTranslationError):
        to_responses_request(payload)
