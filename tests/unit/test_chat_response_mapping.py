from __future__ import annotations

import json

import pytest

from app.core.openai.chat_responses import (
    ChatCompletion,
    collect_chat_completion,
    iter_chat_chunks,
    stream_chat_chunks,
)
from app.core.openai.models import OpenAIErrorEnvelope


def test_output_text_delta_to_chat_chunk():
    lines = [
        'data: {"type":"response.output_text.delta","delta":"hi"}\n\n',
        'data: {"type":"response.completed","response":{"id":"r1"}}\n\n',
    ]
    chunks = list(iter_chat_chunks(lines, model="gpt-5.2"))
    assert any("chat.completion.chunk" in chunk for chunk in chunks)


def test_output_text_delta_emits_role_once():
    lines = [
        'data: {"type":"response.output_text.delta","delta":"hi"}\n\n',
        'data: {"type":"response.output_text.delta","delta":" there"}\n\n',
        'data: {"type":"response.completed","response":{"id":"r1"}}\n\n',
    ]
    chunks = list(iter_chat_chunks(lines, model="gpt-5.2"))
    parsed = [
        json.loads(chunk[5:].strip())
        for chunk in chunks
        if chunk.startswith("data: ") and "chat.completion.chunk" in chunk
    ]
    content_deltas = [item["choices"][0]["delta"] for item in parsed if "content" in item["choices"][0]["delta"]]
    roles = [delta.get("role") for delta in content_deltas]
    assert roles[0] == "assistant"
    assert all(role is None for role in roles[1:])


def test_error_event_emits_done_chunk():
    lines = [
        'data: {"type":"error","error":{"message":"bad","type":"server_error","code":"no_accounts"}}\n\n',
    ]
    chunks = list(iter_chat_chunks(lines, model="gpt-5.2"))
    assert any('"error"' in chunk for chunk in chunks)
    assert chunks[-1].strip() == "data: [DONE]"


@pytest.mark.asyncio
async def test_collect_completion_parses_event_prefixed_sse_block():
    lines = [
        (
            "event: response.failed\n"
            'data: {"type":"response.failed","response":{"id":"r1","status":"failed","error":'
            '{"message":"bad","type":"server_error","code":"no_accounts"}}}\n\n'
        ),
    ]

    async def _stream():
        for line in lines:
            yield line

    result = await collect_chat_completion(_stream(), model="gpt-5.2")
    assert isinstance(result, OpenAIErrorEnvelope)
    assert result.error is not None
    assert result.error.code == "no_accounts"


def test_tool_call_delta_is_emitted():
    lines = [
        (
            'data: {"type":"response.output_tool_call.delta","call_id":"call_1",'
            '"name":"do_thing","arguments":"{\\"a\\":1"}\n\n'
        ),
        'data: {"type":"response.output_tool_call.delta","call_id":"call_1","arguments":"}"}\n\n',
        'data: {"type":"response.completed","response":{"id":"r1"}}\n\n',
    ]
    chunks = list(iter_chat_chunks(lines, model="gpt-5.2"))
    tool_chunks = [
        json.loads(chunk[5:].strip()) for chunk in chunks if chunk.startswith("data: ") and "tool_calls" in chunk
    ]
    assert tool_chunks
    first = tool_chunks[0]
    delta = first["choices"][0]["delta"]["tool_calls"][0]
    assert delta["id"] == "call_1"
    assert delta["type"] == "function"
    assert delta["function"]["name"] == "do_thing"
    done_chunks = [
        json.loads(chunk[5:].strip()) for chunk in chunks if chunk.startswith("data: ") and '"finish_reason"' in chunk
    ]
    assert done_chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_response_incomplete_maps_finish_reason_length():
    lines = [
        'data: {"type":"response.output_text.delta","delta":"hi"}\n\n',
        (
            'data: {"type":"response.incomplete","response":{"id":"r1",'
            '"incomplete_details":{"reason":"max_output_tokens"}}}\n\n'
        ),
    ]
    chunks = list(iter_chat_chunks(lines, model="gpt-5.2"))
    parsed = [
        json.loads(chunk[5:].strip())
        for chunk in chunks
        if chunk.startswith("data: ") and "chat.completion.chunk" in chunk
    ]
    done_chunks = [chunk for chunk in parsed if chunk["choices"][0].get("finish_reason") is not None]
    assert done_chunks[-1]["choices"][0]["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_stream_chat_chunks_preserves_tool_call_state():
    lines = [
        ('data: {"type":"response.output_tool_call.delta","call_id":"call_1","name":"do_thing","arguments":"{}"}\n\n'),
        ('data: {"type":"response.output_tool_call.delta","call_id":"call_2","name":"do_other","arguments":"{}"}\n\n'),
        'data: {"type":"response.completed","response":{"id":"r1"}}\n\n',
    ]

    async def _stream():
        for line in lines:
            yield line

    chunks = [chunk async for chunk in stream_chat_chunks(_stream(), model="gpt-5.2")]
    parsed_chunks = [
        json.loads(chunk[5:].strip())
        for chunk in chunks
        if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]"
    ]
    indices = []
    for parsed in parsed_chunks:
        delta = parsed["choices"][0]["delta"]
        tool_calls = delta.get("tool_calls")
        if tool_calls:
            indices.extend([tool_call["index"] for tool_call in tool_calls])
    assert indices == [0, 1]
    done_chunks = [chunk for chunk in parsed_chunks if chunk["choices"][0].get("finish_reason") is not None]
    assert done_chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


@pytest.mark.asyncio
async def test_stream_chat_chunks_include_usage_chunk():
    lines = [
        'data: {"type":"response.output_text.delta","delta":"hi"}\n\n',
        (
            'data: {"type":"response.completed","response":{"id":"r1","usage":'
            '{"input_tokens":2,"output_tokens":3,"total_tokens":5}}}\n\n'
        ),
    ]

    async def _stream():
        for line in lines:
            yield line

    chunks = [
        json.loads(chunk[5:].strip())
        for chunk in [c async for c in stream_chat_chunks(_stream(), model="gpt-5.2", include_usage=True)]
        if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]"
    ]
    assert all("usage" in chunk for chunk in chunks)
    assert chunks[0]["usage"] is None
    assert chunks[-1]["usage"]["total_tokens"] == 5


@pytest.mark.asyncio
async def test_collect_completion_merges_tool_call_arguments():
    lines = [
        (
            'data: {"type":"response.output_tool_call.delta","call_id":"call_1",'
            '"name":"do_thing","arguments":"{\\"a\\":1"}\n\n'
        ),
        'data: {"type":"response.output_tool_call.delta","call_id":"call_1","arguments":"}"}\n\n',
        'data: {"type":"response.completed","response":{"id":"r1"}}\n\n',
    ]

    async def _stream():
        for line in lines:
            yield line

    result = await collect_chat_completion(_stream(), model="gpt-5.2")
    assert isinstance(result, ChatCompletion)
    choice = result.choices[0]
    assert choice.finish_reason == "tool_calls"
    tool_calls = choice.message.tool_calls
    assert tool_calls is not None
    tool_call = tool_calls[0]
    assert tool_call.id == "call_1"
    function = tool_call.function
    assert function is not None
    assert function.arguments == '{"a":1}'


@pytest.mark.asyncio
async def test_collect_completion_returns_error_event():
    lines = [
        'data: {"type":"error","error":{"message":"bad","type":"server_error","code":"no_accounts"}}\n\n',
    ]

    async def _stream():
        for line in lines:
            yield line

    result = await collect_chat_completion(_stream(), model="gpt-5.2")
    assert isinstance(result, OpenAIErrorEnvelope)
    assert result.error is not None
    assert result.error.code == "no_accounts"


@pytest.mark.asyncio
async def test_collect_completion_includes_refusal_delta():
    lines = [
        'data: {"type":"response.refusal.delta","delta":"no"}\n\n',
        (
            'data: {"type":"response.incomplete","response":{"id":"r1",'
            '"incomplete_details":{"reason":"content_filter"}}}\n\n'
        ),
    ]

    async def _stream():
        for line in lines:
            yield line

    result = await collect_chat_completion(_stream(), model="gpt-5.2")
    assert isinstance(result, ChatCompletion)
    message = result.choices[0].message
    assert message.refusal == "no"
    assert message.content is None


def test_refusal_delta_populates_refusal_field_streaming():
    lines = [
        'data: {"type":"response.refusal.delta","delta":"I cannot"}\n\n',
        'data: {"type":"response.completed","response":{"id":"r1"}}\n\n',
    ]
    chunks = list(iter_chat_chunks(lines, model="gpt-5.2"))
    parsed = [
        json.loads(chunk[5:].strip())
        for chunk in chunks
        if chunk.startswith("data: ") and "chat.completion.chunk" in chunk
    ]
    refusal_deltas = [
        item["choices"][0]["delta"] for item in parsed if item["choices"][0]["delta"].get("refusal") is not None
    ]
    assert refusal_deltas
    assert refusal_deltas[0]["refusal"] == "I cannot"
    assert refusal_deltas[0].get("content") is None


@pytest.mark.asyncio
async def test_collect_completion_content_and_refusal_both_present():
    lines = [
        'data: {"type":"response.output_text.delta","delta":"hi"}\n\n',
        'data: {"type":"response.refusal.delta","delta":"no"}\n\n',
        'data: {"type":"response.completed","response":{"id":"r1"}}\n\n',
    ]

    async def _stream():
        for line in lines:
            yield line

    result = await collect_chat_completion(_stream(), model="gpt-5.2")
    assert isinstance(result, ChatCompletion)
    message = result.choices[0].message
    assert message.content == "hi"
    assert message.refusal == "no"
