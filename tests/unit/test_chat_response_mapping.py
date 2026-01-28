from __future__ import annotations

import json
from typing import cast

import pytest

from app.core.openai.chat_responses import collect_chat_completion, iter_chat_chunks
from app.core.types import JsonValue


def test_output_text_delta_to_chat_chunk():
    lines = [
        'data: {"type":"response.output_text.delta","delta":"hi"}\n\n',
        'data: {"type":"response.completed","response":{"id":"r1"}}\n\n',
    ]
    chunks = list(iter_chat_chunks(lines, model="gpt-5.2"))
    assert any("chat.completion.chunk" in chunk for chunk in chunks)


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
    choices = cast(list[dict[str, JsonValue]], result.get("choices"))
    choice = choices[0]
    assert choice.get("finish_reason") == "tool_calls"
    message = cast(dict[str, JsonValue], choice.get("message"))
    tool_calls = cast(list[dict[str, JsonValue]], message.get("tool_calls"))
    tool_call = tool_calls[0]
    assert tool_call["id"] == "call_1"
    function = cast(dict[str, JsonValue], tool_call.get("function"))
    assert function.get("arguments") == '{"a":1}'
