from __future__ import annotations

import json
from copy import deepcopy

import pytest

from app.core.clients import proxy
from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.modules.proxy._service.response_create import _response_create_client_metadata, _response_create_text

pytestmark = pytest.mark.unit

_MISSING = "omitted"


@pytest.mark.parametrize("parallel", [_MISSING, None, True, False])
def test_lite_finalization_disables_parallel_calls_without_mutating_content(parallel: JsonValue) -> None:
    payload: dict[str, JsonValue] = {
        "input": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
        "prompt_cache_key": "stable-series",
        "reasoning": {"effort": "high", "summary": "auto"},
    }
    if parallel != _MISSING:
        payload["parallel_tool_calls"] = parallel
    original = deepcopy(payload)

    proxy._finalize_responses_lite_reasoning_context(payload, responses_lite=True)

    assert payload["parallel_tool_calls"] is False
    assert payload["reasoning"] == {"effort": "high", "summary": "auto", "context": "all_turns"}
    for key in ("input", "tools", "prompt_cache_key"):
        assert payload[key] == original[key]
    once = deepcopy(payload)
    proxy._finalize_responses_lite_reasoning_context(payload, responses_lite=True)
    assert payload == once


@pytest.mark.parametrize("parallel", [_MISSING, None, True, False])
def test_non_lite_finalization_preserves_parallel_setting(parallel: JsonValue) -> None:
    payload: dict[str, JsonValue] = {"input": []}
    if parallel != _MISSING:
        payload["parallel_tool_calls"] = parallel
    original = deepcopy(payload)

    proxy._finalize_responses_lite_reasoning_context(payload, responses_lite=False)

    assert payload == original


@pytest.mark.parametrize("trusted_delta", [False, True])
@pytest.mark.parametrize("parallel", [_MISSING, None, True, False])
def test_final_response_create_enforces_lite_parallel_contract(
    trusted_delta: bool,
    parallel: JsonValue,
) -> None:
    marker = proxy.CODEX_RESPONSES_LITE_WEBSOCKET_METADATA_KEY
    input_items: list[JsonValue] = [{"role": "user", "content": "hello"}]
    if not trusted_delta:
        input_items.insert(0, {"type": "additional_tools", "role": "developer", "tools": []})
    body: dict[str, JsonValue] = {
        "model": "gpt-6-astra",
        "instructions": "",
        "input": input_items,
        "prompt_cache_key": "stable-series",
        "reasoning": {"effort": "high"},
    }
    if parallel != _MISSING:
        body["parallel_tool_calls"] = parallel
    payload = ResponsesRequest.model_validate(body)
    before = deepcopy(payload.to_payload())

    text = _response_create_text(
        payload, include_type_field=True, client_metadata={marker: "true"} if trusted_delta else None
    )
    sent = json.loads(text)

    assert sent["type"] == "response.create"
    assert sent["parallel_tool_calls"] is False
    assert sent["reasoning"]["context"] == "all_turns"
    assert sent["prompt_cache_key"] == "stable-series"
    assert payload.to_payload() == before


def test_untrusted_marker_does_not_disable_parallel_calls() -> None:
    marker = proxy.CODEX_RESPONSES_LITE_WEBSOCKET_METADATA_KEY
    body: dict[str, JsonValue] = {
        "model": "gpt-6-astra",
        "instructions": "",
        "input": [{"role": "user", "content": "hello"}],
        "parallel_tool_calls": True,
        "client_metadata": {marker: "true"},
    }
    metadata = _response_create_client_metadata(body, headers={})
    assert metadata is None
    body.pop("client_metadata")
    text = _response_create_text(
        ResponsesRequest.model_validate(body), include_type_field=True, client_metadata=metadata
    )

    assert json.loads(text)["parallel_tool_calls"] is True
