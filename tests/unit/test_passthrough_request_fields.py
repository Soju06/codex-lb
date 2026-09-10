"""Passthrough request fields (``input``/``tools``/``messages``/``schema``) skip
pydantic's deep ``JsonValue`` validation.

The golden corpus in ``tests/fixtures/passthrough_request_corpus.json`` was
frozen from the pre-change models (deep-validated, deep-dumped) so the
forwarded bytes are proven identical, and the shape checks that pydantic's
``list`` validation used to provide are pinned at field level so the error
``param`` still names the offending field.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel, ValidationError

from app.core.openai.chat_requests import ChatCompletionsRequest
from app.core.openai.requests import (
    PASSTHROUGH_MAX_DEPTH,
    ResponsesCompactRequest,
    ResponsesRequest,
    ResponsesTextFormat,
    validate_passthrough_depth,
    validate_tool_types,
)
from app.core.openai.v1_requests import V1ResponsesCompactRequest, V1ResponsesRequest
from app.core.types import JsonValue
from app.modules.proxy.request_policy import normalize_responses_request_payload, openai_validation_error

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORPUS_PATH = _REPO_ROOT / "tests" / "fixtures" / "passthrough_request_corpus.json"
_CORPUS: list[dict[str, Any]] = json.loads(_CORPUS_PATH.read_text())
_BAD_ARRAYS: list[JsonValue] = [None, "abc", 123, {}]


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _build(case: dict[str, Any]) -> BaseModel:
    kind = case["kind"]
    payload = case["payload"]
    if kind == "responses":
        return ResponsesRequest.model_validate(payload)
    if kind == "v1":
        return V1ResponsesRequest.model_validate(payload).to_responses_request()
    if kind == "chat":
        return ChatCompletionsRequest.model_validate(payload).to_responses_request()
    if kind == "compact":
        return ResponsesCompactRequest.model_validate(payload)
    if kind == "v1compact":
        return V1ResponsesCompactRequest.model_validate(payload).to_compact_request()
    raise AssertionError(f"unknown corpus kind {kind!r}")


@pytest.mark.parametrize("case", _CORPUS, ids=[case["name"] for case in _CORPUS])
def test_forwarded_payload_bytes_match_pre_change_golden(case: dict[str, Any]) -> None:
    request = _build(case)
    to_payload = getattr(request, "to_payload")
    assert _dumps(to_payload()) == case["expected_to_payload"]
    assert _dumps(request.model_dump(mode="json", exclude_none=True)) == case["expected_model_dump_json"]
    assert sorted(request.model_fields_set) == case["expected_fields_set"]


def test_corpus_covers_every_request_model() -> None:
    assert {case["kind"] for case in _CORPUS} == {"responses", "v1", "chat", "compact", "v1compact"}


def test_passthrough_nested_values_are_forwarded_verbatim() -> None:
    nested: JsonValue = {"deep": [True, False, None, 0.0, -1, 1e21, 1.0, "s", {"k": []}]}
    tools: list[JsonValue] = [{"type": "function", "name": "f", "parameters": nested, "strict": False}]
    request = ResponsesRequest.model_validate(
        {"model": "gpt-5", "instructions": "i", "input": [{"role": "user", "content": nested}], "tools": tools}
    )
    payload = request.to_payload()
    assert _dumps(payload["tools"]) == _dumps(tools)
    assert _dumps(cast(list[JsonValue], payload["input"])[0]) == _dumps({"role": "user", "content": nested})


@pytest.mark.parametrize("bad", _BAD_ARRAYS, ids=["null", "string", "number", "object"])
def test_responses_request_rejects_non_array_tools_with_tools_param(bad: JsonValue) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ResponsesRequest.model_validate({"model": "gpt-5", "instructions": "i", "input": "hi", "tools": bad})
    assert [error["loc"] for error in exc_info.value.errors()] == [("tools",)]
    assert openai_validation_error(exc_info.value)["error"]["param"] == "tools"


@pytest.mark.parametrize("bad", _BAD_ARRAYS, ids=["null", "string", "number", "object"])
def test_v1_request_rejects_non_array_tools_with_tools_param(bad: JsonValue) -> None:
    with pytest.raises(ValidationError) as exc_info:
        V1ResponsesRequest.model_validate({"model": "gpt-5", "input": "hi", "tools": bad})
    assert [error["loc"] for error in exc_info.value.errors()] == [("tools",)]


@pytest.mark.parametrize("bad", _BAD_ARRAYS, ids=["null", "string", "number", "object"])
def test_chat_request_rejects_non_array_tools_with_tools_param(bad: JsonValue) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ChatCompletionsRequest.model_validate(
            {"model": "gpt-5", "messages": [{"role": "user", "content": "hi"}], "tools": bad}
        )
    assert [error["loc"] for error in exc_info.value.errors()] == [("tools",)]


@pytest.mark.parametrize("bad", ["abc", 123, {}], ids=["string", "number", "object"])
def test_v1_and_chat_reject_non_array_messages_with_messages_param(bad: JsonValue) -> None:
    for model_cls in (V1ResponsesRequest, V1ResponsesCompactRequest, ChatCompletionsRequest):
        with pytest.raises(ValidationError) as exc_info:
            model_cls.model_validate({"model": "gpt-5", "messages": bad})
        assert [error["loc"] for error in exc_info.value.errors()] == [("messages",)], model_cls.__name__


def test_validate_tool_types_rejects_non_list() -> None:
    with pytest.raises(ValueError, match="tools must be an array"):
        validate_tool_types(cast(list[JsonValue], "abc"))


@pytest.mark.parametrize("bad", [123, {"a": 1}, True], ids=["number", "object", "bool"])
def test_input_type_check_still_runs(bad: JsonValue) -> None:
    for model_cls in (ResponsesRequest, ResponsesCompactRequest):
        with pytest.raises(ValidationError) as exc_info:
            model_cls.model_validate({"model": "gpt-5", "instructions": "i", "input": bad})
        assert [error["loc"] for error in exc_info.value.errors()] == [("input",)], model_cls.__name__
    for v1_cls in (V1ResponsesRequest, V1ResponsesCompactRequest):
        with pytest.raises(ValidationError) as exc_info:
            v1_cls.model_validate({"model": "gpt-5", "input": bad})
        assert [error["loc"] for error in exc_info.value.errors()] == [("input",)], v1_cls.__name__


def test_field_validators_still_normalize_passthrough_fields() -> None:
    payload: dict[str, JsonValue] = {
        "model": "gpt-5",
        "instructions": "i",
        "input": [{"role": "user", "content": "hi", "reasoning_content": "x"}],
        "tools": [{"type": "web_search_preview"}],
    }
    native = ResponsesRequest.model_validate(payload)
    assert native.tools == [{"type": "web_search"}]
    assert native.input == [{"role": "user", "content": "hi"}]
    compat = V1ResponsesRequest.model_validate({k: v for k, v in payload.items() if k != "instructions"})
    assert compat.tools == [{"type": "web_search"}]
    assert compat.to_responses_request().tools == [{"type": "web_search"}]


def test_v1_omitted_tools_stay_omitted_and_explicit_empty_tools_stay() -> None:
    omitted = V1ResponsesRequest.model_validate({"model": "gpt-5", "input": "hi"}).to_responses_request()
    assert "tools" not in omitted.model_fields_set
    assert "tools" not in omitted.to_payload()
    explicit = V1ResponsesRequest.model_validate({"model": "gpt-5", "input": "hi", "tools": []}).to_responses_request()
    assert explicit.to_payload()["tools"] == []


def test_text_format_schema_is_forwarded_verbatim() -> None:
    schema: JsonValue = {
        "type": "object",
        "properties": {"a": {"type": ["string", "null"]}},
        "additionalProperties": False,
    }
    text_format = ResponsesTextFormat.model_validate({"type": "json_schema", "name": "n", "schema": schema})
    assert text_format.schema_ is schema
    dumped = text_format.model_dump(mode="json", exclude_none=True)
    assert dumped == {"type": "json_schema", "name": "n", "schema": schema}


def test_chat_message_shape_errors_still_reject_with_messages_param() -> None:
    cases: list[JsonValue] = [
        {"role": "assistant", "content": "x", "tool_calls": "nope"},
        {"role": 5, "content": "x"},
        {"role": "tool", "content": "x", "tool_call_id": 7},
        "not-an-object",
    ]
    for message in cases:
        with pytest.raises(ValidationError) as exc_info:
            ChatCompletionsRequest.model_validate({"model": "gpt-5", "messages": [message]})
        # The message list is opaque to pydantic now: these shapes are caught
        # by the mapping's model-level validator, so the envelope carries no
        # item path (pre-change pydantic reported ``messages.0...``).
        assert "param" not in openai_validation_error(exc_info.value)["error"], message


@pytest.mark.parametrize(
    "extra",
    [
        {"refusal": None},
        {"refusal": 5},
        {"refusal": True},
        {"refusal": []},
        {"refusal": {"k": 1}},
        {"tool_calls": None},
    ],
    ids=["refusal-null", "refusal-number", "refusal-bool", "refusal-array", "refusal-object", "tool_calls-null"],
)
def test_chat_assistant_uninspected_key_types_are_ignored(extra: dict[str, JsonValue]) -> None:
    # Documented leniency: pre-change the ``OpenAIMessage`` TypedDict rejected
    # these with ``string_type``/``list_type``; the mapping only consumes
    # non-empty string refusals and treats ``tool_calls: null`` as omitted,
    # so they map identically to the message without the key.
    base = {"model": "gpt-5", "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}]}
    lenient = json.loads(json.dumps(base))
    lenient["messages"][1].update(extra)
    assert (
        ChatCompletionsRequest.model_validate(lenient).to_responses_request().to_payload()
        == ChatCompletionsRequest.model_validate(base).to_responses_request().to_payload()
    )


def test_non_finite_floats_in_passthrough_fields_serialize_as_null() -> None:
    # ``json.loads("1e400")`` is ``inf``; pydantic serializes it as ``null``
    # (pre-change ``json.dumps`` emitted the non-JSON ``Infinity`` upstream).
    payload = json.loads('{"model":"gpt-5","instructions":"i","input":[{"role":"user","content":"hi","n":1e400}]}')
    forwarded = ResponsesRequest.model_validate(payload).to_payload()
    assert cast(list[JsonValue], forwarded["input"])[0] == {"role": "user", "content": "hi", "n": None}


def test_normalizing_the_same_raw_payload_twice_leaves_it_untouched() -> None:
    # The WebSocket handler normalizes one ``response.create`` payload twice
    # (continuity wait), so normalization must never mutate the client's
    # nested objects even though the model now aliases them.
    payload: dict[str, JsonValue] = {
        "model": "gpt-5",
        "input": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": [{"type": "input_text", "text": "hi"}], "reasoning_content": "x"},
        ],
        "tools": [{"type": "web_search_preview"}, {"type": "function", "name": "f", "parameters": {"a": [1]}}],
        "reasoning_effort": "high",
    }
    before = _dumps(payload)
    first = normalize_responses_request_payload(payload, openai_compat=True).to_payload()
    second = normalize_responses_request_payload(payload, openai_compat=True).to_payload()
    assert _dumps(payload) == before
    assert _dumps(first) == _dumps(second)


def test_normalized_request_aliases_client_tool_objects() -> None:
    # Documented consequence of skipping validation: the request model holds
    # the client's own dict objects instead of validated copies. This is safe
    # only while no caller reads the raw body after normalization, which
    # ``test_raw_payload_is_not_consumed_after_normalization`` pins.
    client_tools: list[JsonValue] = [{"type": "function", "name": "f", "parameters": {"type": "object"}}]
    payload: dict[str, JsonValue] = {
        "model": "gpt-5",
        "instructions": "i",
        "input": [{"role": "user", "content": "hi"}],
        "tools": client_tools,
    }
    native = normalize_responses_request_payload(payload, openai_compat=False)
    assert native.tools[0] is client_tools[0]
    compat = normalize_responses_request_payload(payload, openai_compat=True)
    assert compat.tools[0] is client_tools[0]


def _names_loaded_after_normalization(path: Path, function_name: str) -> set[str]:
    module = ast.parse(path.read_text())
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            normalize_calls = [
                call
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "normalize_responses_request_payload"
            ]
            assert len(normalize_calls) == 1, f"{function_name}: expected exactly one normalize call"
            boundary = normalize_calls[0].end_lineno or normalize_calls[0].lineno
            return {
                name.id
                for name in ast.walk(node)
                if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Load) and name.lineno > boundary
            }
    raise AssertionError(f"{function_name} not found in {path}")


@pytest.mark.parametrize(
    ("relative_path", "function_name"),
    [
        ("app/modules/proxy/api.py", "responses"),
        ("app/modules/proxy/_service/websocket/mixin.py", "_prepare_websocket_response_create_request"),
    ],
)
def test_raw_payload_is_not_consumed_after_normalization(relative_path: str, function_name: str) -> None:
    # Callers may re-normalize the same dict (the WebSocket continuity wait
    # does); ``test_normalizing_the_same_raw_payload_twice_leaves_it_untouched`` covers that.
    loaded = _names_loaded_after_normalization(_REPO_ROOT / relative_path, function_name)
    assert "payload" not in loaded, (
        f"{function_name} reads the raw body after normalize_responses_request_payload(); "
        "the normalized request aliases the raw body's nested objects, so copy before mutating."
    )


def _nested(depth: int) -> JsonValue:
    value: JsonValue = {"leaf": 1}
    for _ in range(depth):
        value = [value]
    return value


def _deep_cases(depth: int) -> dict[str, tuple[type[BaseModel], dict[str, JsonValue], str]]:
    deep = _nested(depth)
    item: JsonValue = {"role": "user", "content": [{"type": "input_text", "text": "x", "n": deep}]}
    message: JsonValue = {"role": "user", "content": [{"type": "text", "text": "x", "n": deep}]}
    tool: JsonValue = {"type": "function", "name": "f", "parameters": deep}
    chat_tool: JsonValue = {"type": "function", "function": {"name": "f", "parameters": deep}}
    hello: JsonValue = [{"role": "user", "content": "hi"}]
    native: dict[str, JsonValue] = {"model": "m", "instructions": ""}
    return {
        "responses.input": (ResponsesRequest, {**native, "input": [item]}, "input"),
        "responses.tools": (ResponsesRequest, {**native, "input": "hi", "tools": [tool]}, "tools"),
        "compact.input": (ResponsesCompactRequest, {**native, "input": [item]}, "input"),
        "v1.input": (V1ResponsesRequest, {"model": "m", "input": [item]}, "input"),
        "v1.messages": (V1ResponsesRequest, {"model": "m", "messages": [message]}, "messages"),
        "v1.tools": (V1ResponsesRequest, {"model": "m", "input": "hi", "tools": [tool]}, "tools"),
        "v1compact.input": (V1ResponsesCompactRequest, {"model": "m", "input": [item]}, "input"),
        "v1compact.messages": (V1ResponsesCompactRequest, {"model": "m", "messages": [message]}, "messages"),
        "chat.messages": (ChatCompletionsRequest, {"model": "m", "messages": [message]}, "messages"),
        "chat.tools": (ChatCompletionsRequest, {"model": "m", "messages": hello, "tools": [chat_tool]}, "tools"),
        "chat.input": (ChatCompletionsRequest, {"model": "m", "input": [item]}, "input"),
        "text.schema": (ResponsesTextFormat, {"type": "json_schema", "name": "n", "schema": deep}, "schema"),
    }


_DEEP_KINDS = list(_deep_cases(1))


def _forward(model: BaseModel) -> object:
    converted = getattr(model, "to_responses_request", None) or getattr(model, "to_compact_request", None)
    request = converted() if converted is not None else model
    to_payload = getattr(request, "to_payload", None)
    return to_payload() if to_payload is not None else request.model_dump(mode="json")


@pytest.mark.parametrize("depth", [300, 5000], ids=["serializer-limit", "python-recursion-limit"])
@pytest.mark.parametrize("kind", _DEEP_KINDS)
def test_deeply_nested_passthrough_fields_are_rejected_with_field_param(kind: str, depth: int) -> None:
    # Past ~250 container levels pydantic-core's serializer raised on
    # ``to_payload`` (a 500); depth 5000 also proves nothing before the guard
    # recurses into the value (no RecursionError).
    model_cls, payload, param = _deep_cases(depth)[kind]
    with pytest.raises(ValidationError) as exc_info:
        _forward(model_cls.model_validate(payload))
    assert [error["loc"] for error in exc_info.value.errors()] == [(param,)]
    assert openai_validation_error(exc_info.value)["error"]["param"] == param


@pytest.mark.parametrize("kind", _DEEP_KINDS)
def test_passthrough_nesting_at_the_limit_is_accepted_and_serializable(kind: str) -> None:
    # The fixtures wrap the value in up to four containers (list, item,
    # content list, part), so ``limit - 5`` keeps the whole field at the limit.
    model_cls, payload, _param = _deep_cases(PASSTHROUGH_MAX_DEPTH - 5)[kind]
    _forward(model_cls.model_validate(payload))


def test_validate_passthrough_depth_counts_container_levels_including_dict_subclasses() -> None:
    # The WebSocket frame parser decodes objects with an ``object_pairs_hook``
    # dict subclass; the guard must not stop at those nodes.
    class _Obj(dict[str, JsonValue]):
        pass

    validate_passthrough_depth("scalar")
    validate_passthrough_depth(_nested(2), limit=3)
    with pytest.raises(ValueError, match="nesting exceeds 3 levels"):
        validate_passthrough_depth(_nested(3), limit=3)
    with pytest.raises(ValueError, match="nesting exceeds 2 levels"):
        validate_passthrough_depth([_Obj(a=[_Obj(b=1)])], limit=2)
