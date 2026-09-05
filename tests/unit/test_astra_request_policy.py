from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

import pytest

from app.core.exceptions import ProxyInvalidRequestError, ProxyReasoningEffortNotAllowed
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.types import JsonValue
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.request_policy import apply_api_key_enforcement, validate_astra_request


def _apply_subscription_policy(payload, api_key):
    apply_api_key_enforcement(payload, api_key)
    validate_astra_request(payload, api_key)


def _key(*, allowed: list[str] | None = None, enforced: str | None = None, model: str | None = None) -> ApiKeyData:
    return ApiKeyData(
        id="astra-policy",
        name="Astra policy",
        key_prefix="astra",
        allowed_models=None,
        enforced_model=model,
        enforced_service_tier=None,
        enforced_reasoning_effort=enforced,
        allowed_reasoning_efforts=allowed,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
        last_used_at=None,
    )


def _configuration_effort(input_items: JsonValue) -> str:
    assert isinstance(input_items, list)
    update = input_items[1]
    assert isinstance(update, Mapping)
    reasoning = update["reasoning"]
    assert isinstance(reasoning, Mapping)
    effort = reasoning["effort"]
    assert isinstance(effort, str)
    return effort


def _request(effort: str = "high", **extra) -> ResponsesRequest:
    return ResponsesRequest.model_validate(
        {
            "model": "gpt-6-astra",
            "instructions": "",
            "reasoning": {"effort": "low"},
            "input": [
                {"role": "user", "content": "First task"},
                {"type": "configuration_update", "reasoning": {"effort": effort}},
                {"role": "user", "content": "Continue"},
            ],
            **extra,
        }
    )


def test_astra_update_preserves_request_prefix_and_history():
    request = _request()
    _apply_subscription_policy(request, None)
    forwarded = request.to_payload()
    assert forwarded["reasoning"] == {"effort": "low"}
    assert forwarded["input"] == request.input


def test_astra_ultra_update_survives_repeated_policy_and_owner_serialization():
    request = _request("ultra")
    key = _key(allowed=["low", "ultra"])
    _apply_subscription_policy(request, key)
    _apply_subscription_policy(request, key)
    forwarded_owner = ResponsesRequest.model_validate(request.model_dump_for_forwarding())
    _apply_subscription_policy(forwarded_owner, key)
    assert _configuration_effort(request.input) == "ultra"
    assert _configuration_effort(forwarded_owner.to_payload()["input"]) == "max"
    assert _configuration_effort(forwarded_owner.to_replay_safety_payload()["input"]) == "max"


def test_astra_ultra_continuation_keeps_client_identity_across_owner_hops():
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-6-astra",
            "instructions": "",
            "previous_response_id": "resp_owner",
            "reasoning": {"effort": "ultra"},
            "input": [{"role": "user", "content": "Continue"}],
        }
    )
    key = _key(allowed=["ultra"])
    _apply_subscription_policy(request, key)
    assert request.reasoning is not None
    assert request.reasoning.effort == "ultra"
    assert request.input == [
        {"type": "configuration_update", "reasoning": {"effort": "ultra"}},
        {"role": "user", "content": "Continue"},
    ]

    forwarded_owner = ResponsesRequest.model_validate(request.model_dump_for_forwarding())
    _apply_subscription_policy(forwarded_owner, key)
    assert forwarded_owner.reasoning is not None
    assert forwarded_owner.reasoning.effort == "ultra"
    assert forwarded_owner.input == request.input
    forwarded = forwarded_owner.to_payload()
    assert forwarded["reasoning"] == {"effort": "max"}
    forwarded_input = forwarded["input"]
    assert isinstance(forwarded_input, list)
    assert forwarded_input[0] == {"type": "configuration_update", "reasoning": {"effort": "max"}}


@pytest.mark.parametrize("effort", ["none", "minimal", "invalid"])
def test_astra_rejects_unsupported_configuration_effort(effort):
    with pytest.raises(ProxyInvalidRequestError):
        _apply_subscription_policy(_request(effort), None)


def test_astra_keeps_top_level_minimal_compatibility():
    request = _request(reasoning={"effort": "minimal"})
    _apply_subscription_policy(request, None)
    assert request.reasoning is not None
    assert request.reasoning.effort == "low"


@pytest.mark.parametrize("request_type", [ResponsesRequest, ResponsesCompactRequest])
def test_astra_rejects_none_after_enforced_model_selection(request_type):
    request = request_type.model_validate(
        {
            "model": "gpt-5.6-terra",
            "instructions": "",
            "input": [],
            "reasoning": {"effort": "none"},
        }
    )
    key = _key(model="gpt-6-astra")
    with pytest.raises(ProxyInvalidRequestError):
        _apply_subscription_policy(request, key)


def test_astra_history_update_obeys_allowed_efforts():
    with pytest.raises(ProxyReasoningEffortNotAllowed):
        _apply_subscription_policy(_request(), _key(allowed=["low"]))


def test_astra_history_update_obeys_enforced_effort():
    with pytest.raises(ProxyReasoningEffortNotAllowed):
        _apply_subscription_policy(_request(), _key(enforced="low"))


def test_astra_history_update_preserves_raw_enforcement_distinction():
    with pytest.raises(ProxyReasoningEffortNotAllowed):
        _apply_subscription_policy(_request("ultra"), _key(enforced="max"))

    request = _request("ultra")
    _apply_subscription_policy(request, _key(enforced="ultra"))
    assert _configuration_effort(request.to_payload()["input"]) == "max"


@pytest.mark.parametrize(
    "extra",
    [
        {"truncation": "auto"},
        {"context_management": [{"type": "compaction", "compact_threshold": 200000}]},
        {"reasoning": {"effort": "low", "mode": "pro"}},
    ],
)
def test_astra_rejects_incompatible_update_controls(extra):
    with pytest.raises(ProxyInvalidRequestError):
        _apply_subscription_policy(_request(**extra), None)


def test_astra_standalone_compact_rejects_updates():
    request = ResponsesCompactRequest.model_validate(_request().model_dump())
    with pytest.raises(ProxyInvalidRequestError, match="compact endpoint"):
        _apply_subscription_policy(request, None)


def test_astra_compact_ultra_maps_to_max_at_serialization():
    request = ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-6-astra",
            "instructions": "",
            "input": "Summarize this",
            "reasoning": {"effort": "ultra"},
        }
    )
    _apply_subscription_policy(request, _key(allowed=["ultra"]))
    assert request.reasoning is not None
    assert request.reasoning.effort == "ultra"
    assert request.to_payload()["reasoning"] == {"effort": "max"}


def test_astra_rejects_adjacent_updates():
    request = _request()
    assert isinstance(request.input, list)
    request.input.insert(2, {"type": "configuration_update", "reasoning": {"effort": "low"}})
    with pytest.raises(ProxyInvalidRequestError, match="Adjacent"):
        _apply_subscription_policy(request, None)


@pytest.mark.parametrize(
    "update",
    [
        {"type": "configuration_update"},
        {"type": "configuration_update", "reasoning": {"effort": 3}},
        {"type": "configuration_update", "reasoning": {"effort": "high", "mode": "pro"}},
        {"type": "configuration_update", "reasoning": {"effort": "high"}, "model": "other"},
    ],
)
def test_astra_rejects_malformed_updates(update):
    request = _request()
    assert isinstance(request.input, list)
    request.input[1] = update
    with pytest.raises(ProxyInvalidRequestError):
        _apply_subscription_policy(request, None)


@pytest.mark.parametrize(
    "extra",
    [
        {"top_logprobs": 0},
        {"logprobs": False},
        {"include": ["message.output_text.logprobs"]},
    ],
)
def test_astra_rejects_unsupported_logprob_controls(extra):
    with pytest.raises(ProxyInvalidRequestError):
        _apply_subscription_policy(_request(**extra), None)


def test_other_models_keep_their_existing_control_contract():
    request = _request(model="custom-model", reasoning={"effort": "none"}, top_logprobs=3)
    _apply_subscription_policy(request, None)
    assert request.reasoning is not None
    assert request.reasoning.effort == "none"
    assert request.model_extra is not None
    assert request.model_extra["top_logprobs"] == 3


def test_astra_sampling_parameters_keep_existing_subscription_normalization():
    request = _request(temperature=0.3, top_p=0.8)
    _apply_subscription_policy(request, None)
    assert "temperature" not in request.to_payload()
    assert "top_p" not in request.to_payload()
