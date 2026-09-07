from __future__ import annotations

import pytest

from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.modules.api_keys.service import API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage


@pytest.mark.parametrize(
    ("request_type", "method"),
    [
        (ResponsesRequest, "to_payload"),
        (ResponsesRequest, "to_replay_safety_payload"),
        (ResponsesCompactRequest, "to_payload"),
    ],
)
@pytest.mark.parametrize(("effort", "wire_effort"), [("low", "low"), (" Ultra ", "max")])
@pytest.mark.parametrize(
    "vendor_fields",
    [
        {"vendor_context": {"notes": ["retain", "值"], "flags": [True, None], "weight": 2}},
        {"vendor_payload": "x" * 10_000},
    ],
    ids=["structured", "budget-cap"],
)
def test_astra_effort_serialization_preserves_other_reasoning_fields(
    request_type, method, effort, wire_effort, vendor_fields
):
    update = {"type": "configuration_update", "vendor_top": True, "reasoning": {"effort": effort, **vendor_fields}}
    request = request_type(model="gpt-6-astra", instructions="", input=[update])
    serialize = getattr(request, method)
    forwarded = serialize()
    assert forwarded["input"] == [{**update, "reasoning": {"effort": wire_effort, **vendor_fields}}]
    assert request.input == [update]
    assert serialize() == forwarded
    assert getattr(request_type.model_validate(forwarded), method)() == forwarded
    if "vendor_payload" in vendor_fields:
        assert estimate_api_key_request_usage(request).input_tokens == API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET
