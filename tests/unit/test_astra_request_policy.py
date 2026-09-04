from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from app.core.exceptions import ProxyInvalidRequestError, ProxyReasoningEffortNotAllowed
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.request_policy import apply_api_key_enforcement, validate_astra_request


def _apply_subscription_policy(payload, api_key):
    apply_api_key_enforcement(payload, api_key)
    validate_astra_request(payload, api_key)


def _key(*, allowed: list[str] | None = None, enforced: str | None = None) -> ApiKeyData:
    return cast(
        ApiKeyData,
        SimpleNamespace(
            id="astra-policy",
            enforced_model=None,
            enforced_service_tier=None,
            enforced_reasoning_effort=enforced,
            allowed_reasoning_efforts=allowed,
        ),
    )


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
    assert request.input[1]["reasoning"]["effort"] == "ultra"
    assert forwarded_owner.to_payload()["input"][1]["reasoning"]["effort"] == "max"
    assert forwarded_owner.to_replay_safety_payload()["input"][1]["reasoning"]["effort"] == "max"


@pytest.mark.parametrize("effort", ["none", "minimal", "invalid"])
def test_astra_rejects_unsupported_configuration_effort(effort):
    with pytest.raises(ProxyInvalidRequestError):
        _apply_subscription_policy(_request(effort), None)


def test_astra_keeps_top_level_minimal_compatibility():
    request = _request(reasoning={"effort": "minimal"})
    _apply_subscription_policy(request, None)
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
    key = _key()
    key.enforced_model = "gpt-6-astra"
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
    assert request.to_payload()["input"][1]["reasoning"]["effort"] == "max"


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


def test_astra_rejects_adjacent_updates():
    request = _request()
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
    assert request.reasoning.effort == "none"
    assert request.model_extra["top_logprobs"] == 3


def test_astra_sampling_parameters_keep_existing_subscription_normalization():
    request = _request(temperature=0.3, top_p=0.8)
    _apply_subscription_policy(request, None)
    assert "temperature" not in request.to_payload()
    assert "top_p" not in request.to_payload()
