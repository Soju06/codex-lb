from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.types import JsonObject, JsonValue
from app.modules.api_keys.service import (
    API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS,
    API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS,
    API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET,
    ApiKeyRequestUsageBudget,
)
from app.modules.proxy import api_key_usage
from app.modules.proxy import service as proxy_service
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from tests.unit.hypothesis_strategies import json_arrays, json_objects


@pytest.mark.parametrize(
    ("budget", "expected"),
    [
        pytest.param(None, 0.0, id="no-budget"),
        pytest.param(
            ApiKeyRequestUsageBudget(input_tokens=None, output_tokens=None),
            float(API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS + API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS),
            id="defaults",
        ),
        pytest.param(
            ApiKeyRequestUsageBudget(input_tokens=-1, output_tokens=API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET + 1),
            float(API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET),
            id="bounded",
        ),
    ],
)
def test_estimated_lease_tokens_preserves_service_facade_contract(
    budget: ApiKeyRequestUsageBudget | None,
    expected: float,
) -> None:
    assert proxy_service._estimated_lease_tokens_from_request_usage_budget(budget) == expected


def test_bounded_lease_token_estimate_remains_available_from_service_facade() -> None:
    assert (
        proxy_service._bounded_lease_token_estimate(
            API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET + 1,
            default=0,
        )
        == API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET
    )


def test_estimate_api_key_request_usage_does_not_trust_unsupported_output_caps() -> None:
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.5",
            "instructions": "be brief",
            "input": "hello",
            "max_output_tokens": 128,
        }
    )

    budget = estimate_api_key_request_usage(payload)

    assert budget.input_tokens is not None
    assert 0 < budget.input_tokens < API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET
    assert budget.output_tokens is None


def test_estimate_api_key_request_usage_accepts_compact_request_shape() -> None:
    payload = ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-5.5",
            "instructions": "compress",
            "input": "hello",
            "service_tier": "priority",
        }
    )

    budget = estimate_api_key_request_usage(payload)

    assert budget.input_tokens is not None
    assert 0 < budget.input_tokens < API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET
    assert budget.output_tokens is None


@pytest.mark.parametrize("opaque_field", ["previous_response_id", "conversation"])
def test_estimate_api_key_request_usage_uses_conservative_input_for_compact_opaque_context(
    opaque_field: str,
) -> None:
    payload = ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-5.5",
            "instructions": "compress",
            "input": "hello",
            opaque_field: "opaque_123",
        }
    )

    budget = estimate_api_key_request_usage(payload)

    assert budget.input_tokens is None
    assert budget.output_tokens is None


def test_estimate_api_key_request_usage_uses_conservative_input_for_previous_response() -> None:
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.5",
            "instructions": "continue",
            "input": "next",
            "previous_response_id": "resp_123",
        }
    )

    budget = estimate_api_key_request_usage(payload)

    assert budget.input_tokens is None
    assert budget.output_tokens is None


def test_estimate_api_key_request_usage_uses_conservative_input_for_file_reference() -> None:
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.5",
            "instructions": "summarize",
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_file", "file_id": "file_123"}],
                }
            ],
        }
    )

    budget = estimate_api_key_request_usage(payload)

    assert budget.input_tokens is None


def test_estimate_api_key_request_usage_allows_structured_content_type_values() -> None:
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.5",
            "instructions": "continue",
            "input": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": {
                                "namespace": "multi_agent_v1",
                                "name": "tool_search_output",
                            },
                            "text": "deferred tool metadata",
                        }
                    ],
                }
            ],
        }
    )

    budget = estimate_api_key_request_usage(payload)

    assert budget.input_tokens is not None


# --- Estimate byte-compat: shared dump + early-exit serialization -----------


def _legacy_estimate_request_input_tokens(input_value: JsonValue, upstream_payload: JsonObject) -> int | None:
    """Reference implementation: the pre-early-exit full sorted dump."""
    if api_key_usage._has_opaque_upstream_context(upstream_payload):
        return None
    if api_key_usage._contains_opaque_input_reference(input_value):
        return None
    data = api_key_usage._input_budget_payload(upstream_payload)
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    if not serialized:
        return 0
    return min(len(serialized.encode("utf-8")), API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET)


def _estimate_for(upstream_payload: JsonObject) -> int | None:
    request = cast(Any, SimpleNamespace(input=upstream_payload.get("input")))
    return api_key_usage._estimate_request_input_tokens(request, upstream_payload)


def _payload_with_serialized_length(target_bytes: int, *, filler: str = "a") -> dict[str, JsonValue]:
    """Build ``{"input": ..., "instructions": filler*k}`` whose sorted dump is exactly ``target_bytes``."""
    overhead = len(json.dumps({"input": "", "instructions": ""}, ensure_ascii=False, separators=(",", ":")).encode())
    filler_bytes = len(filler.encode("utf-8"))
    filler_count, ascii_padding = divmod(target_bytes - overhead, filler_bytes)
    payload: dict[str, JsonValue] = {"input": "x" * ascii_padding, "instructions": filler * filler_count}
    assert len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()) == target_bytes
    return payload


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"model": "gpt-5.5", "instructions": "be brief", "input": "héllo wörld"}, id="small-non-ascii"),
        pytest.param(
            {"model": "gpt-5.5", "instructions": "x" * 20_000, "input": [{"role": "user", "content": "hi"}]},
            id="long-instructions",
        ),
        pytest.param(
            {
                "model": "gpt-5.5",
                "instructions": "short",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": "한글 " * 3000}]}],
                "reasoning": {"effort": "high"},
                "tools": [{"type": "function", "name": "shell", "parameters": {"type": "object"}}],
            },
            id="large-multibyte-input",
        ),
        pytest.param(
            {"model": "gpt-5.5", "instructions": "next", "input": "x", "previous_response_id": "resp_1"}, id="opaque"
        ),
    ],
)
def test_estimate_api_key_request_usage_shared_dump_matches_default_path(body: dict[str, JsonValue]) -> None:
    payload = ResponsesRequest.model_validate(body)
    upstream_payload = payload.to_payload()
    calls = {"count": 0}
    original_to_payload = ResponsesRequest.to_payload

    def counting_to_payload(self: ResponsesRequest) -> JsonObject:
        calls["count"] += 1
        return original_to_payload(self)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(ResponsesRequest, "to_payload", counting_to_payload)
        default_budget = estimate_api_key_request_usage(payload)
        shared_budget = estimate_api_key_request_usage(payload, upstream_payload=upstream_payload)

    assert shared_budget == default_budget
    assert shared_budget.input_tokens == _legacy_estimate_request_input_tokens(payload.input, upstream_payload)
    assert calls["count"] == 1, "the shared-dump path must not re-dump the request"


@given(
    payload=json_objects,
    instructions=st.one_of(st.none(), st.text(max_size=200), st.text(min_size=8192, max_size=8300)),
    input_value=st.one_of(st.none(), st.text(max_size=120), json_arrays),
)
@settings(deadline=None, max_examples=600)
def test_estimate_request_input_tokens_matches_full_sorted_dump(
    payload: dict[str, JsonValue],
    instructions: str | None,
    input_value: JsonValue,
) -> None:
    upstream_payload = dict(payload)
    if instructions is not None:
        upstream_payload["instructions"] = instructions
    if input_value is not None:
        upstream_payload["input"] = input_value

    assert _estimate_for(upstream_payload) == _legacy_estimate_request_input_tokens(
        upstream_payload.get("input"), upstream_payload
    )


@pytest.mark.parametrize(
    ("target_bytes", "filler"),
    [
        pytest.param(API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET - 1, "a", id="8191-ascii"),
        pytest.param(API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET, "a", id="8192-ascii"),
        pytest.param(API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET + 1, "a", id="8193-ascii"),
        pytest.param(API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET - 2, "é", id="8190-two-byte"),
        pytest.param(API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET + 2, "é", id="8194-two-byte"),
        pytest.param(API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET - 3, "한", id="8189-three-byte"),
        pytest.param(API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET + 3, "한", id="8195-three-byte"),
    ],
)
def test_estimate_request_input_tokens_cap_boundaries(target_bytes: int, filler: str) -> None:
    upstream_payload = _payload_with_serialized_length(target_bytes, filler=filler)

    estimate = _estimate_for(upstream_payload)

    assert estimate == min(target_bytes, API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET)
    assert estimate == _legacy_estimate_request_input_tokens(upstream_payload["input"], upstream_payload)


def test_estimate_request_input_tokens_instructions_shortcut_stays_behind_opaque_checks() -> None:
    long_instructions = "i" * API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET

    assert (
        _estimate_for({"instructions": long_instructions, "input": "x"}) == API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET
    )
    assert _estimate_for({"instructions": long_instructions, "input": "x", "conversation": "conv_1"}) is None
    assert (
        _estimate_for({"instructions": long_instructions, "input": [{"type": "input_image", "file_id": "file_1"}]})
        is None
    )
    # One character short of the cap must fall through to real serialization.
    assert _estimate_for({"instructions": long_instructions[:-1], "input": ""}) == min(
        len(json.dumps({"input": "", "instructions": long_instructions[:-1]}, separators=(",", ":")).encode()),
        API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET,
    )


def test_estimate_request_input_tokens_large_payload_returns_cap_without_full_serialization() -> None:
    upstream_payload: dict[str, JsonValue] = {
        "instructions": "short",
        "input": [
            {"type": "function_call_output", "call_id": f"call_{index}", "output": "o" * 500} for index in range(2_000)
        ],
    }
    data = api_key_usage._input_budget_payload(upstream_payload)
    total_chunks = sum(1 for _ in api_key_usage._BUDGET_ENCODER.iterencode(data))
    consumed_chunks = 0
    original_iterencode = api_key_usage._BUDGET_ENCODER.iterencode

    def counting_iterencode(value: object, _one_shot: bool = False):  # noqa: FBT001, FBT002
        nonlocal consumed_chunks
        for chunk in original_iterencode(value, _one_shot):
            consumed_chunks += 1
            yield chunk

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(api_key_usage._BUDGET_ENCODER, "iterencode", counting_iterencode)
        assert _estimate_for(upstream_payload) == API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET

    # ~1 MB of output: the early exit stops within the first few items.
    assert 0 < consumed_chunks < total_chunks // 50


def test_estimate_request_input_tokens_single_huge_string_returns_cap_without_encoding_it() -> None:
    huge = "0123456789" * 100_000  # a single 1 MB string literal is one encoder chunk
    upstream_payload: dict[str, JsonValue] = {"instructions": "short", "input": huge}
    encoded: list[int] = []
    original_encode = str.encode

    class _Spy(str):
        __slots__ = ()

        def encode(self, *args: Any, **kwargs: Any) -> bytes:
            encoded.append(len(self))
            return original_encode(self, *args, **kwargs)

    original_iterencode = api_key_usage._BUDGET_ENCODER.iterencode

    def spying_iterencode(value: object, _one_shot: bool = False):  # noqa: FBT001, FBT002
        for chunk in original_iterencode(value, _one_shot):
            yield _Spy(chunk)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(api_key_usage._BUDGET_ENCODER, "iterencode", spying_iterencode)
        assert _estimate_for(upstream_payload) == API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET

    assert all(size < API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET for size in encoded), encoded


def test_estimate_request_input_tokens_lone_surrogate_beyond_cap_no_longer_raises() -> None:
    """Documented edge: a lone surrogate past the first ~8 KiB used to 500 the request."""
    upstream_payload: dict[str, JsonValue] = {
        "instructions": "short",
        "input": [{"role": "user", "content": "p" * 9_000}, {"role": "user", "content": "\ud800"}],
    }
    with pytest.raises(UnicodeEncodeError):
        _legacy_estimate_request_input_tokens(upstream_payload["input"], upstream_payload)

    assert _estimate_for(upstream_payload) == API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET
