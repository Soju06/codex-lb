from __future__ import annotations

import pytest

from app.modules.agent_provider_runtime.gemini import (
    GeminiAdapterError,
    GeminiChatRequest,
    build_generate_content_payload,
    build_generate_content_url,
    generate_content_to_chat_completion,
    generate_content_to_chat_completion_chunk,
    parse_gemini_sse_data_lines,
)


def test_build_generate_content_payload_maps_chat_messages_and_config() -> None:
    payload = build_generate_content_payload(
        GeminiChatRequest(
            model="gemini-2.5-flash",
            messages=[
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
                {"role": "assistant", "content": "Hi"},
            ],
            temperature=0.2,
            top_p=0.9,
            max_tokens=64,
            stop=["END"],
            response_format={"type": "json_object"},
        )
    )

    assert payload == {
        "contents": [
            {"role": "user", "parts": [{"text": "Hello"}]},
            {"role": "model", "parts": [{"text": "Hi"}]},
        ],
        "systemInstruction": {"parts": [{"text": "Be concise."}]},
        "generationConfig": {
            "temperature": 0.2,
            "topP": 0.9,
            "maxOutputTokens": 64,
            "stopSequences": ["END"],
            "responseMimeType": "application/json",
        },
    }


def test_build_generate_content_payload_maps_function_tools() -> None:
    payload = build_generate_content_payload(
        GeminiChatRequest(
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "Weather?"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Gets weather.",
                        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                    },
                }
            ],
        )
    )

    assert payload["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "get_weather",
                    "description": "Gets weather.",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                }
            ]
        }
    ]


def test_build_generate_content_payload_maps_tool_call_history() -> None:
    payload = build_generate_content_payload(
        GeminiChatRequest(
            model="gemini-2.5-flash",
            messages=[
                {"role": "user", "content": "Weather?"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_weather",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_weather", "content": '{"temperature":21}'},
            ],
        )
    )

    assert payload["contents"] == [
        {"role": "user", "parts": [{"text": "Weather?"}]},
        {
            "role": "model",
            "parts": [{"functionCall": {"name": "get_weather", "args": {"city": "Paris"}, "id": "call_weather"}}],
        },
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "get_weather",
                        "response": {"temperature": 21},
                        "id": "call_weather",
                    }
                }
            ],
        },
    ]


def test_build_generate_content_payload_preserves_tool_call_thought_signature() -> None:
    payload = build_generate_content_payload(
        GeminiChatRequest(
            model="gemini-2.5-flash",
            messages=[
                {"role": "user", "content": "Weather?"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_weather",
                            "type": "function",
                            "gemini_thought_signature": "sig-abc",
                            "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
                        }
                    ],
                },
            ],
        )
    )

    assert payload["contents"] == [
        {"role": "user", "parts": [{"text": "Weather?"}]},
        {
            "role": "model",
            "parts": [
                {
                    "functionCall": {"name": "get_weather", "args": {"city": "Paris"}, "id": "call_weather"},
                    "thoughtSignature": "sig-abc",
                }
            ],
        },
    ]


def test_build_generate_content_url_uses_native_endpoints() -> None:
    assert (
        build_generate_content_url("gemini-2.5-flash")
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    )
    assert build_generate_content_url("gemini 2.5 flash", stream=True).endswith(
        "/models/gemini%202.5%20flash:streamGenerateContent?alt=sse"
    )


def test_generate_content_to_chat_completion_maps_text_finish_and_usage() -> None:
    response = generate_content_to_chat_completion(
        {
            "responseId": "resp_1",
            "candidates": [
                {
                    "content": {"parts": [{"text": "Hel"}, {"text": "lo"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2, "totalTokenCount": 5},
        },
        model="gemini-2.5-flash",
        created=123,
    )

    assert response["choices"][0]["message"]["content"] == "Hello"
    assert response["choices"][0]["finish_reason"] == "stop"
    assert response.get("usage") == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}


def test_generate_content_to_chat_completion_preserves_function_calls() -> None:
    response = generate_content_to_chat_completion(
        {
            "responseId": "resp_tool",
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "get_weather",
                                    "args": {"city": "Paris", "unit": "c"},
                                }
                            }
                        ]
                    },
                    "finishReason": "STOP",
                }
            ],
        },
        model="gemini-3.5-flash",
        created=123,
    )

    choice = response["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    assert choice["message"]["tool_calls"] == [
        {
            "id": "call_0_get_weather",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":"Paris","unit":"c"}'},
        }
    ]


def test_generate_content_to_chat_completion_preserves_function_call_thought_signature() -> None:
    response = generate_content_to_chat_completion(
        {
            "responseId": "resp_tool",
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {"name": "get_weather", "args": {"city": "Paris"}},
                                "thoughtSignature": "sig-xyz",
                            }
                        ]
                    },
                }
            ],
        },
        model="gemini-3.5-flash",
        created=123,
    )

    tool_calls = response["choices"][0]["message"]["tool_calls"]
    assert tool_calls[0]["gemini_thought_signature"] == "sig-xyz"


def test_parse_gemini_sse_data_lines_and_chunk_mapping() -> None:
    events = parse_gemini_sse_data_lines(
        [
            "event: message",
            'data: {"responseId":"resp_2","candidates":[{"content":{"parts":[{"text":"Hi"}]},"finishReason":"STOP"}]}',
            "data: [DONE]",
        ]
    )

    assert len(events) == 1
    chunk = generate_content_to_chat_completion_chunk(events[0], model="gemini-2.5-flash", created=456)
    assert chunk["object"] == "chat.completion.chunk"
    assert chunk["choices"][0]["delta"]["content"] == "Hi"
    assert chunk["choices"][0]["finish_reason"] == "stop"


def test_adapter_rejects_non_text_content_for_now() -> None:
    with pytest.raises(GeminiAdapterError):
        build_generate_content_payload(
            GeminiChatRequest(
                model="gemini-2.5-flash",
                messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://x"}}]}],
            )
        )
