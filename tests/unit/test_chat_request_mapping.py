from __future__ import annotations

from collections.abc import Mapping
from typing import cast

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
    assert responses.input == [{"role": "user", "content": "hi"}]


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
    assert dumped.get("reasoning", {}).get("effort") == "high"


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
