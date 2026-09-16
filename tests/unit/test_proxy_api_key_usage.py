from __future__ import annotations

import pytest

from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.modules.api_keys.service import API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage


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


def test_anthropic_estimate_uses_raw_body_size_without_reserializing() -> None:
    from app.core.anthropic.models import AnthropicMessageRequest
    from app.modules.proxy.api import _estimate_anthropic_request_usage

    payload = AnthropicMessageRequest.model_validate(
        {"model": "claude-fable-5-1", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]}
    )
    calls: list[str] = []
    payload.model_dump = lambda *a, **k: calls.append("dump") or {}  # type: ignore[method-assign]

    small = _estimate_anthropic_request_usage(payload, raw_body=b"x" * 100)
    assert small.input_tokens == 100
    assert small.output_tokens == 64

    huge = _estimate_anthropic_request_usage(payload, raw_body=b"x" * (4 * 1024 * 1024))
    assert huge.input_tokens == API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET
    assert calls == [], "raw-body sizing must not re-serialize the payload"


def test_anthropic_estimate_falls_back_to_serialization_without_raw_body() -> None:
    from app.core.anthropic.models import AnthropicMessageRequest
    from app.modules.proxy.api import _estimate_anthropic_request_usage

    payload = AnthropicMessageRequest.model_validate(
        {"model": "claude-fable-5-1", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]}
    )
    budget = _estimate_anthropic_request_usage(payload)
    assert 0 < budget.input_tokens < API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET
