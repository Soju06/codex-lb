from __future__ import annotations

import json

import pytest

from app.core.openai.chat_responses import (
    ChatCompletion,
    ChatMessageToolCall,
    collect_chat_completion,
    iter_chat_chunks,
    stream_chat_chunks,
)
from app.core.openai.models import OpenAIErrorEnvelope


def _tool_call_args(tool_call: ChatMessageToolCall) -> str:
    """Type-narrowing accessor for ``tool_call.function.arguments`` in tests.

    The pydantic model declares both ``function`` and ``arguments`` as
    ``Optional`` for upstream compatibility, but the streaming/non-streaming
    adapters always populate them by the time a tool call is surfaced to the
    client. The helper makes that invariant explicit so ``ty`` does not flag
    every ``tool_call.function.arguments`` access as a possibly-missing
    attribute, without weakening the production model definitions.
    """

    assert tool_call.function is not None
    assert tool_call.function.arguments is not None
    return tool_call.function.arguments


def _tool_call_name(tool_call: ChatMessageToolCall) -> str:
    """Type-narrowing accessor for ``tool_call.function.name`` in tests."""

    assert tool_call.function is not None
    assert tool_call.function.name is not None
    return tool_call.function.name


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
    collected_arguments = "".join(
        (
            (
                (((chunk["choices"][0]["delta"].get("tool_calls") or [{}])[0]).get("function") or {}).get("arguments")
                or ""
            )
            for chunk in tool_chunks
        )
    )
    assert collected_arguments == '{"a":1}'
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
    assert indices[:2] == [0, 1]
    assert set(indices) == {0, 1}
    done_chunks = [chunk for chunk in parsed_chunks if chunk["choices"][0].get("finish_reason") is not None]
    assert done_chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


@pytest.mark.asyncio
async def test_stream_chat_chunks_does_not_duplicate_tool_call_snapshots():
    lines = [
        (
            'data: {"type":"response.output_tool_call.delta","call_id":"call_1",'
            '"name":"get_weather","arguments":"{\\"city\\":\\"Zur"}\n\n'
        ),
        (
            'data: {"type":"response.function_call_arguments.done","call_id":"call_1",'
            '"name":"get_weather","arguments":"{\\"city\\":\\"Zurich\\",\\"unit\\":\\"C\\"}"}\n\n'
        ),
        (
            'data: {"type":"response.output_item.done","output_index":0,"item":{"id":"call_1",'
            '"type":"function_call","name":"get_weather","arguments":"{\\"city\\":\\"Zurich\\",\\"unit\\":\\"C\\"}"}}\n\n'
        ),
        'data: {"type":"response.completed","response":{"id":"r1"}}\n\n',
    ]

    async def _stream():
        for line in lines:
            yield line

    chunks = [
        json.loads(chunk[5:].strip())
        for chunk in [c async for c in stream_chat_chunks(_stream(), model="gpt-5.2")]
        if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]"
    ]

    collected_arguments = ""
    for chunk in chunks:
        tool_calls = chunk["choices"][0]["delta"].get("tool_calls")
        if not tool_calls:
            continue
        function = tool_calls[0].get("function") or {}
        arguments = function.get("arguments")
        if arguments:
            collected_arguments += arguments

    assert collected_arguments == '{"city":"Zurich","unit":"C"}'


@pytest.mark.asyncio
async def test_stream_chat_chunks_skips_incompatible_snapshot_rewrites():
    lines = [
        (
            'data: {"type":"response.output_tool_call.delta","call_id":"call_1",'
            '"name":"get_weather","arguments":"{\\"city\\":\\"Zur"}\n\n'
        ),
        (
            'data: {"type":"response.function_call_arguments.done","call_id":"call_1",'
            '"name":"get_weather","arguments":"{\\"city\\": \\"Zurich\\", \\"unit\\": \\"C\\"}"}\n\n'
        ),
        'data: {"type":"response.completed","response":{"id":"r1"}}\n\n',
    ]

    async def _stream():
        for line in lines:
            yield line

    chunks = [
        json.loads(chunk[5:].strip())
        for chunk in [c async for c in stream_chat_chunks(_stream(), model="gpt-5.2")]
        if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]"
    ]

    collected_arguments = ""
    for chunk in chunks:
        tool_calls = chunk["choices"][0]["delta"].get("tool_calls")
        if not tool_calls:
            continue
        function = tool_calls[0].get("function") or {}
        arguments = function.get("arguments")
        if arguments:
            collected_arguments += arguments

    assert collected_arguments == '{"city":"Zur'


def test_tool_call_delta_is_preserved_before_response_failed():
    lines = [
        (
            'data: {"type":"response.output_tool_call.delta","call_id":"call_1",'
            '"name":"do_thing","arguments":"{\\"a\\":1"}\n\n'
        ),
        (
            'data: {"type":"response.failed","response":{"id":"r1","status":"failed","error":'
            '{"message":"bad","type":"server_error","code":"no_accounts"}}}\n\n'
        ),
    ]

    chunks = list(iter_chat_chunks(lines, model="gpt-5.2"))
    tool_chunks = [
        json.loads(chunk[5:].strip()) for chunk in chunks if chunk.startswith("data: ") and "tool_calls" in chunk
    ]
    assert tool_chunks
    arguments = (tool_chunks[0]["choices"][0]["delta"]["tool_calls"][0].get("function") or {}).get("arguments")
    assert arguments == '{"a":1'
    assert any('"error"' in chunk for chunk in chunks)
    assert chunks[-1].strip() == "data: [DONE]"


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
async def test_stream_chat_chunks_include_usage_chunk_supports_details():
    lines = [
        'data: {"type":"response.output_text.delta","delta":"hi"}\n\n',
        (
            'data: {"type":"response.completed","response":{"id":"r1","usage":'
            '{"input_tokens":2,"output_tokens":3,"total_tokens":5,'
            '"input_tokens_details":{"cached_tokens":1},'
            '"output_tokens_details":{"reasoning_tokens":2}}}}\n\n'
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

    usage = chunks[-1]["usage"]
    assert usage["total_tokens"] == 5
    assert usage["prompt_tokens_details"]["cached_tokens"] == 1
    assert usage["completion_tokens_details"]["reasoning_tokens"] == 2


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
async def test_collect_completion_prefers_final_tool_call_snapshot_without_duplication():
    lines = [
        (
            'data: {"type":"response.output_tool_call.delta","call_id":"call_1",'
            '"name":"get_weather","arguments":"{\\"city\\":\\"Zur"}\n\n'
        ),
        (
            'data: {"type":"response.function_call_arguments.done","call_id":"call_1",'
            '"name":"get_weather","arguments":"{\\"city\\":\\"Zurich\\",\\"unit\\":\\"C\\"}"}\n\n'
        ),
        (
            'data: {"type":"response.output_item.done","output_index":0,"item":{"id":"call_1",'
            '"type":"function_call","name":"get_weather","arguments":"{\\"city\\":\\"Zurich\\",\\"unit\\":\\"C\\"}"}}\n\n'
        ),
        'data: {"type":"response.completed","response":{"id":"r1"}}\n\n',
    ]

    async def _stream():
        for line in lines:
            yield line

    result = await collect_chat_completion(_stream(), model="gpt-5.2")
    assert isinstance(result, ChatCompletion)
    tool_calls = result.choices[0].message.tool_calls
    assert tool_calls is not None
    function = tool_calls[0].function
    assert function is not None
    assert function.arguments == '{"city":"Zurich","unit":"C"}'


@pytest.mark.asyncio
async def test_collect_completion_uses_snapshot_only_tool_call_arguments():
    lines = [
        (
            'data: {"type":"response.output_item.done","output_index":0,"item":{"id":"call_1",'
            '"type":"function_call","name":"get_weather","arguments":"{\\"city\\":\\"Zurich\\",\\"unit\\":\\"C\\"}"}}\n\n'
        ),
        'data: {"type":"response.completed","response":{"id":"r1"}}\n\n',
    ]

    async def _stream():
        for line in lines:
            yield line

    result = await collect_chat_completion(_stream(), model="gpt-5.2")
    assert isinstance(result, ChatCompletion)
    tool_calls = result.choices[0].message.tool_calls
    assert tool_calls is not None
    function = tool_calls[0].function
    assert function is not None
    assert function.arguments == '{"city":"Zurich","unit":"C"}'


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


@pytest.mark.asyncio
async def test_collect_completion_zero_token_preserves_empty_content():
    lines = [
        'data: {"type":"response.completed","response":{"id":"r1"}}\n\n',
    ]

    async def _stream():
        for line in lines:
            yield line

    result = await collect_chat_completion(_stream(), model="gpt-5.2")
    assert isinstance(result, ChatCompletion)
    message = result.choices[0].message
    assert message.content == ""
    assert message.refusal is None


# ──────────────────────────────────────────────────────────────────────────────
# Parallel tool-call tests (item_id-based routing, bug fix verification)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_completion_parallel_tool_calls_distinct_arguments():
    """Two parallel calls to the SAME function with different args.

    Verifies that function_call_arguments.delta/done events routed via
    item_id produce distinct arguments per tool call.
    """
    lines = [
        (
            'data: {"type":"response.output_item.added","output_index":0,'
            '"item":{"id":"fc_001","type":"function_call","call_id":"call_aaa",'
            '"name":"record_observation","arguments":""}}\n\n'
        ),
        (
            'data: {"type":"response.output_item.added","output_index":1,'
            '"item":{"id":"fc_002","type":"function_call","call_id":"call_bbb",'
            '"name":"record_observation","arguments":""}}\n\n'
        ),
        (
            'data: {"type":"response.function_call_arguments.delta","output_index":0,'
            '"item_id":"fc_001","delta":"{\\"category\\":\\"activity\\"}"}\n\n'
        ),
        (
            'data: {"type":"response.function_call_arguments.delta","output_index":1,'
            '"item_id":"fc_002","delta":"{\\"category\\":\\"food\\"}"}\n\n'
        ),
        (
            'data: {"type":"response.function_call_arguments.done","output_index":0,'
            '"item_id":"fc_001","arguments":"{\\"category\\":\\"activity\\",\\"content\\":\\"Biked 20km\\"}"}\n\n'
        ),
        (
            'data: {"type":"response.function_call_arguments.done","output_index":1,'
            '"item_id":"fc_002","arguments":"{\\"category\\":\\"food\\",\\"content\\":\\"Had pasta\\"}"}\n\n'
        ),
        (
            'data: {"type":"response.output_item.done","output_index":0,'
            '"item":{"id":"fc_001","type":"function_call","call_id":"call_aaa",'
            '"name":"record_observation",'
            '"arguments":"{\\"category\\":\\"activity\\",\\"content\\":\\"Biked 20km\\"}"}}\n\n'
        ),
        (
            'data: {"type":"response.output_item.done","output_index":1,'
            '"item":{"id":"fc_002","type":"function_call","call_id":"call_bbb",'
            '"name":"record_observation",'
            '"arguments":"{\\"category\\":\\"food\\",\\"content\\":\\"Had pasta\\"}"}}\n\n'
        ),
        'data: {"type":"response.completed","response":{"id":"resp_001"}}\n\n',
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
    assert len(tool_calls) == 2

    args_0 = json.loads(_tool_call_args(tool_calls[0]))
    args_1 = json.loads(_tool_call_args(tool_calls[1]))
    assert args_0["category"] == "activity"
    assert args_0["content"] == "Biked 20km"
    assert args_1["category"] == "food"
    assert args_1["content"] == "Had pasta"
    assert tool_calls[0].id == "call_aaa"
    assert tool_calls[1].id == "call_bbb"


@pytest.mark.asyncio
async def test_collect_completion_three_parallel_tool_calls():
    """Three parallel tool calls to verify indexing beyond 2."""
    lines = [
        (
            'data: {"type":"response.output_item.added","output_index":0,'
            '"item":{"id":"fc_A","type":"function_call","call_id":"call_1",'
            '"name":"record_observation","arguments":""}}\n\n'
        ),
        (
            'data: {"type":"response.output_item.added","output_index":1,'
            '"item":{"id":"fc_B","type":"function_call","call_id":"call_2",'
            '"name":"record_observation","arguments":""}}\n\n'
        ),
        (
            'data: {"type":"response.output_item.added","output_index":2,'
            '"item":{"id":"fc_C","type":"function_call","call_id":"call_3",'
            '"name":"record_observation","arguments":""}}\n\n'
        ),
        (
            'data: {"type":"response.function_call_arguments.done","output_index":0,'
            '"item_id":"fc_A","arguments":"{\\"cat\\":\\"activity\\"}"}\n\n'
        ),
        (
            'data: {"type":"response.function_call_arguments.done","output_index":1,'
            '"item_id":"fc_B","arguments":"{\\"cat\\":\\"food\\"}"}\n\n'
        ),
        (
            'data: {"type":"response.function_call_arguments.done","output_index":2,'
            '"item_id":"fc_C","arguments":"{\\"cat\\":\\"mood\\"}"}\n\n'
        ),
        (
            'data: {"type":"response.output_item.done","output_index":0,'
            '"item":{"id":"fc_A","type":"function_call","call_id":"call_1",'
            '"name":"record_observation","arguments":"{\\"cat\\":\\"activity\\"}"}}\n\n'
        ),
        (
            'data: {"type":"response.output_item.done","output_index":1,'
            '"item":{"id":"fc_B","type":"function_call","call_id":"call_2",'
            '"name":"record_observation","arguments":"{\\"cat\\":\\"food\\"}"}}\n\n'
        ),
        (
            'data: {"type":"response.output_item.done","output_index":2,'
            '"item":{"id":"fc_C","type":"function_call","call_id":"call_3",'
            '"name":"record_observation","arguments":"{\\"cat\\":\\"mood\\"}"}}\n\n'
        ),
        'data: {"type":"response.completed","response":{"id":"resp_003"}}\n\n',
    ]

    async def _stream():
        for line in lines:
            yield line

    result = await collect_chat_completion(_stream(), model="gpt-5.2")
    assert isinstance(result, ChatCompletion)
    tool_calls = result.choices[0].message.tool_calls
    assert tool_calls is not None
    assert len(tool_calls) == 3
    assert json.loads(_tool_call_args(tool_calls[0])) == {"cat": "activity"}
    assert json.loads(_tool_call_args(tool_calls[1])) == {"cat": "food"}
    assert json.loads(_tool_call_args(tool_calls[2])) == {"cat": "mood"}
    assert tool_calls[0].id == "call_1"
    assert tool_calls[1].id == "call_2"
    assert tool_calls[2].id == "call_3"


@pytest.mark.asyncio
async def test_collect_completion_parallel_different_functions():
    """Two parallel calls with DIFFERENT function names via item_id routing."""
    lines = [
        (
            'data: {"type":"response.output_item.added","output_index":0,'
            '"item":{"id":"fc_w","type":"function_call","call_id":"call_weather",'
            '"name":"get_weather","arguments":""}}\n\n'
        ),
        (
            'data: {"type":"response.output_item.added","output_index":1,'
            '"item":{"id":"fc_r","type":"function_call","call_id":"call_record",'
            '"name":"record_observation","arguments":""}}\n\n'
        ),
        (
            'data: {"type":"response.function_call_arguments.done","output_index":0,'
            '"item_id":"fc_w","arguments":"{\\"city\\":\\"Zurich\\"}"}\n\n'
        ),
        (
            'data: {"type":"response.function_call_arguments.done","output_index":1,'
            '"item_id":"fc_r","arguments":"{\\"category\\":\\"activity\\"}"}\n\n'
        ),
        (
            'data: {"type":"response.output_item.done","output_index":0,'
            '"item":{"id":"fc_w","type":"function_call","call_id":"call_weather",'
            '"name":"get_weather","arguments":"{\\"city\\":\\"Zurich\\"}"}}\n\n'
        ),
        (
            'data: {"type":"response.output_item.done","output_index":1,'
            '"item":{"id":"fc_r","type":"function_call","call_id":"call_record",'
            '"name":"record_observation","arguments":"{\\"category\\":\\"activity\\"}"}}\n\n'
        ),
        'data: {"type":"response.completed","response":{"id":"resp_diff"}}\n\n',
    ]

    async def _stream():
        for line in lines:
            yield line

    result = await collect_chat_completion(_stream(), model="gpt-5.2")
    assert isinstance(result, ChatCompletion)
    tool_calls = result.choices[0].message.tool_calls
    assert tool_calls is not None
    assert len(tool_calls) == 2
    assert _tool_call_name(tool_calls[0]) == "get_weather"
    assert json.loads(_tool_call_args(tool_calls[0])) == {"city": "Zurich"}
    assert _tool_call_name(tool_calls[1]) == "record_observation"
    assert json.loads(_tool_call_args(tool_calls[1])) == {"category": "activity"}


@pytest.mark.asyncio
async def test_collect_completion_parallel_calls_item_id_equals_call_id():
    """Edge case: item.id == item.call_id (some models emit identical values)."""
    lines = [
        (
            'data: {"type":"response.output_item.added","output_index":0,'
            '"item":{"id":"call_same_1","type":"function_call","call_id":"call_same_1",'
            '"name":"get_weather","arguments":""}}\n\n'
        ),
        (
            'data: {"type":"response.output_item.added","output_index":1,'
            '"item":{"id":"call_same_2","type":"function_call","call_id":"call_same_2",'
            '"name":"get_weather","arguments":""}}\n\n'
        ),
        (
            'data: {"type":"response.function_call_arguments.done","output_index":0,'
            '"item_id":"call_same_1","arguments":"{\\"city\\":\\"Paris\\"}"}\n\n'
        ),
        (
            'data: {"type":"response.function_call_arguments.done","output_index":1,'
            '"item_id":"call_same_2","arguments":"{\\"city\\":\\"London\\"}"}\n\n'
        ),
        (
            'data: {"type":"response.output_item.done","output_index":0,'
            '"item":{"id":"call_same_1","type":"function_call","call_id":"call_same_1",'
            '"name":"get_weather","arguments":"{\\"city\\":\\"Paris\\"}"}}\n\n'
        ),
        (
            'data: {"type":"response.output_item.done","output_index":1,'
            '"item":{"id":"call_same_2","type":"function_call","call_id":"call_same_2",'
            '"name":"get_weather","arguments":"{\\"city\\":\\"London\\"}"}}\n\n'
        ),
        'data: {"type":"response.completed","response":{"id":"resp_same"}}\n\n',
    ]

    async def _stream():
        for line in lines:
            yield line

    result = await collect_chat_completion(_stream(), model="gpt-5.2")
    assert isinstance(result, ChatCompletion)
    tool_calls = result.choices[0].message.tool_calls
    assert tool_calls is not None
    assert len(tool_calls) == 2
    args_paris = _tool_call_args(tool_calls[0])
    args_london = _tool_call_args(tool_calls[1])
    assert json.loads(args_paris) == {"city": "Paris"}
    assert json.loads(args_london) == {"city": "London"}
    assert args_paris != args_london


@pytest.mark.asyncio
async def test_collect_completion_registers_call_id_when_output_index_already_mapped():
    """Regression: a stream that first routes a tool call via item_id + output_index
    (no call_id yet) and later emits a call_id-only event without output_index
    must NOT split the call into two tool_calls[] slots.

    Mirrors codex review P2 (chat_responses.py:139): index_for_output_index
    used to return early on a known output_index without registering the
    newly observed call_id/name key, so a follow-up call_id-only event was
    assigned a fresh index, fragmenting the arguments.
    """

    lines = [
        'data: {"type":"response.created","response":{"id":"resp_split"}}\n\n',
        # First: an output_item.added that exposes both item.id and item.call_id.
        # This is enough for the indexer to associate output_index=0 with the
        # call_id-keyed slot.
        'data: {"type":"response.output_item.added","output_index":0,'
        '"item":{"id":"fc_001","call_id":"call_001","type":"function_call",'
        '"name":"get_weather","arguments":""}}\n\n',
        # Then: an argument event that carries item_id + output_index (no call_id).
        # This locks in output_index_map[0] -> the same slot.
        'data: {"type":"response.function_call_arguments.delta",'
        '"item_id":"fc_001","output_index":0,"delta":"{\\"city\\":\\"Paris\\"}"}\n\n',
        # Then: a legacy-style tool_call delta event that carries only the
        # call_id+name (no output_index, no item_id). Previously this fell into
        # `if key not in self.indexes:` and got a fresh next_index, splitting
        # the call into a second tool_calls[] slot.
        'data: {"type":"response.output_tool_call.delta","call_id":"call_001","name":"get_weather","delta":""}\n\n',
        # Final completion.
        'data: {"type":"response.function_call_arguments.done",'
        '"item_id":"fc_001","output_index":0,'
        '"arguments":"{\\"city\\":\\"Paris\\"}"}\n\n',
        'data: {"type":"response.output_item.done","output_index":0,'
        '"item":{"id":"fc_001","call_id":"call_001","type":"function_call",'
        '"name":"get_weather","arguments":"{\\"city\\":\\"Paris\\"}"}}\n\n',
        'data: {"type":"response.completed","response":{"id":"resp_split"}}\n\n',
    ]

    async def _stream():
        for line in lines:
            yield line

    result = await collect_chat_completion(_stream(), model="gpt-5.2")
    assert isinstance(result, ChatCompletion)
    tool_calls = result.choices[0].message.tool_calls
    assert tool_calls is not None
    # The call_id-only legacy event must not have allocated a new slot.
    assert len(tool_calls) == 1
    assert _tool_call_name(tool_calls[0]) == "get_weather"
    assert json.loads(_tool_call_args(tool_calls[0])) == {"city": "Paris"}
