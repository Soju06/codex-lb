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
    ResponsesCompactRequest,
    ResponsesRequest,
    ResponsesTextFormat,
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


def test_chat_assistant_refusal_null_is_accepted_and_mapped_like_omitted() -> None:
    # Pre-change the ``OpenAIMessage`` TypedDict rejected ``refusal: null``
    # (``string_type``); the mapping only ever consumed non-empty strings, so
    # the passthrough form accepts the null and maps it identically.
    base = {"model": "gpt-5", "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}]}
    with_null = json.loads(json.dumps(base))
    with_null["messages"][1]["refusal"] = None
    assert (
        ChatCompletionsRequest.model_validate(with_null).to_responses_request().to_payload()
        == ChatCompletionsRequest.model_validate(base).to_responses_request().to_payload()
    )


def test_chat_message_shape_errors_still_reject_with_messages_param() -> None:
    cases: list[JsonValue] = [
        {"role": "assistant", "content": "x", "tool_calls": "nope"},
        {"role": 5, "content": "x"},
        {"role": "tool", "content": "x", "tool_call_id": 7},
        "not-an-object",
    ]
    for message in cases:
        with pytest.raises(ValidationError):
            ChatCompletionsRequest.model_validate({"model": "gpt-5", "messages": [message]})


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
    loaded = _names_loaded_after_normalization(_REPO_ROOT / relative_path, function_name)
    assert "payload" not in loaded, (
        f"{function_name} reads the raw body after normalize_responses_request_payload(); "
        "the normalized request aliases the raw body's nested objects, so copy before mutating."
    )
