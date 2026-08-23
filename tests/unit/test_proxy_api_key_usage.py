from __future__ import annotations

import pytest

from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.modules.api_keys.service import (
    API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS,
    API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS,
    API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET,
    ApiKeyRequestUsageBudget,
)
from app.modules.proxy import service as proxy_service
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage


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
