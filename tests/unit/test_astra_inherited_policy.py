from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast

import pytest

import app.modules.proxy._service.http_bridge.request_submit as request_submit_module
import app.modules.proxy.service as proxy_service
from app.core.exceptions import ProxyInvalidRequestError, ProxyReasoningEffortNotAllowed
from app.core.openai.requests import ResponsesRequest
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.request_policy import (
    apply_api_key_enforcement,
    prepare_astra_reasoning_policy_continuation,
    validate_astra_request,
)


def _key(*, allowed: list[str] | None = None, enforced: str | None = None) -> ApiKeyData:
    return cast(
        ApiKeyData,
        SimpleNamespace(
            id="astra-inherited-policy",
            enforced_model=None,
            enforced_service_tier=None,
            enforced_reasoning_effort=enforced,
            allowed_reasoning_efforts=allowed,
        ),
    )


def _request(
    *,
    effort: str | None = None,
    previous_response_id: str | None = "resp_inherited",
    conversation: str | None = None,
    input_items: list[dict] | None = None,
    **extra,
) -> ResponsesRequest:
    data = {
        "model": "gpt-6-astra",
        "instructions": "",
        "input": input_items or [{"role": "user", "content": "Continue"}],
        **extra,
    }
    if effort is not None:
        data["reasoning"] = {"effort": effort}
    if previous_response_id is not None:
        data["previous_response_id"] = previous_response_id
    if conversation is not None:
        data["conversation"] = conversation
    return ResponsesRequest.model_validate(data)


def _apply_and_validate(request: ResponsesRequest, key: ApiKeyData) -> None:
    apply_api_key_enforcement(request, key)
    validate_astra_request(request, key)


def test_enforced_effort_resets_previous_response_before_current_input() -> None:
    request = _request()

    _apply_and_validate(request, _key(enforced="low"))

    assert request.input == [
        {"type": "configuration_update", "reasoning": {"effort": "low"}},
        {"role": "user", "content": "Continue"},
    ]


def test_allowed_explicit_effort_resets_conversation_anchor() -> None:
    request = _request(effort="high", previous_response_id=None, conversation="conv_inherited")

    _apply_and_validate(request, _key(allowed=["high"]))

    assert isinstance(request.input, list)
    assert request.input[0] == {"type": "configuration_update", "reasoning": {"effort": "high"}}


def test_omitted_effort_uses_astra_default_and_obeys_allowlist() -> None:
    accepted = _request()
    _apply_and_validate(accepted, _key(allowed=["medium"]))
    assert isinstance(accepted.input, list)
    assert accepted.input[0] == {"type": "configuration_update", "reasoning": {"effort": "medium"}}

    rejected = _request()
    with pytest.raises(ProxyReasoningEffortNotAllowed):
        _apply_and_validate(rejected, _key(allowed=["low"]))
    assert rejected.input == [{"role": "user", "content": "Continue"}]


@pytest.mark.parametrize(
    ("client_effort", "allowed", "wire_effort"), [("ultra", "ultra", "ultra"), ("minimal", "minimal", "low")]
)
def test_client_plane_alias_reset_is_canonical_and_idempotent(
    client_effort: str,
    allowed: str,
    wire_effort: str,
) -> None:
    request = _request(effort=client_effort)
    key = _key(allowed=[allowed])

    _apply_and_validate(request, key)
    validate_astra_request(request, key)

    assert request.input == [
        {"type": "configuration_update", "reasoning": {"effort": wire_effort}},
        {"role": "user", "content": "Continue"},
    ]
    forwarded_effort = "max" if client_effort == "ultra" else wire_effort
    assert request.to_payload()["input"] == [
        {"type": "configuration_update", "reasoning": {"effort": forwarded_effort}},
        {"role": "user", "content": "Continue"},
    ]


def test_ultra_and_max_remain_distinct_for_configuration_update_policy() -> None:
    request = _request(effort="max")

    with pytest.raises(ProxyReasoningEffortNotAllowed):
        _apply_and_validate(request, _key(allowed=["ultra"]))

    enforced = _request(
        input_items=[
            {"type": "configuration_update", "reasoning": {"effort": "max"}},
            {"role": "user", "content": "Continue"},
        ]
    )
    with pytest.raises(ProxyReasoningEffortNotAllowed):
        _apply_and_validate(enforced, _key(enforced="ultra"))


def test_leading_client_update_is_not_duplicated() -> None:
    request = _request(
        input_items=[
            {"type": "configuration_update", "reasoning": {"effort": "high"}},
            {"role": "user", "content": "Continue"},
        ]
    )

    _apply_and_validate(request, _key(allowed=["high"]))

    assert request.input == [
        {"type": "configuration_update", "reasoning": {"effort": "high"}},
        {"role": "user", "content": "Continue"},
    ]


def test_no_anchor_preserves_full_history() -> None:
    history = [
        {"role": "user", "content": "First"},
        {"type": "configuration_update", "reasoning": {"effort": "high"}},
        {"role": "user", "content": "Continue"},
    ]
    request = _request(effort="low", previous_response_id=None, input_items=history)

    _apply_and_validate(request, _key(allowed=["low", "high"]))

    assert request.input == history


def test_late_proxy_anchor_can_prepare_before_derived_request_state() -> None:
    request = _request(effort="high", previous_response_id=None)
    key = _key(allowed=["high"])
    _apply_and_validate(request, key)
    assert request.input == [{"role": "user", "content": "Continue"}]

    request.previous_response_id = "resp_proxy_injected"
    assert prepare_astra_reasoning_policy_continuation(request, key) is True
    validate_astra_request(request, key)

    assert isinstance(request.input, list)
    assert request.input[0] == {"type": "configuration_update", "reasoning": {"effort": "high"}}


def test_conversation_reset_fails_closed_with_automatic_compaction() -> None:
    request = _request(
        previous_response_id=None,
        conversation="conv_compacting",
        context_management=[{"type": "compaction", "compact_threshold": 200_000}],
    )

    with pytest.raises(ProxyInvalidRequestError, match="automatic compaction"):
        _apply_and_validate(request, _key(enforced="low"))


def test_unrestricted_key_does_not_change_anchored_input() -> None:
    request = _request()

    _apply_and_validate(request, _key())

    assert request.input == [{"role": "user", "content": "Continue"}]


def test_http_bridge_preparation_resets_late_anchor_before_derived_state() -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request = _request(effort="ultra")

    request_state, text_data = service._prepare_response_bridge_request_state(
        request,
        api_key=_key(allowed=["ultra"]),
        api_key_reservation=None,
        include_type_field=True,
        attach_event_queue=False,
        transport="http",
        client_metadata=None,
    )

    wire_payload = json.loads(text_data)
    assert isinstance(request.input, list)
    assert request.input[0] == {
        "type": "configuration_update",
        "reasoning": {"effort": "ultra"},
    }
    assert wire_payload["input"][0] == {
        "type": "configuration_update",
        "reasoning": {"effort": "max"},
    }
    assert request_state.input_item_count == 2
    assert request_state.input_full_fingerprint == request_submit_module._fingerprint_input_items(wire_payload["input"])
    assert request_state.request_text == text_data


def test_http_bridge_hard_turn_anchor_reset_precedes_operation_fingerprint() -> None:
    text_data = json.dumps(
        {
            "type": "response.create",
            "model": "gpt-6-astra",
            "instructions": "",
            "reasoning": {"effort": "max"},
            "input": [{"role": "user", "content": "Continue"}],
            "client_metadata": {"caller": "test"},
        }
    )

    request_state = SimpleNamespace(
        input_item_count=1,
        input_full_fingerprint=None,
        request_usage_budget=None,
        steering_configuration={
            "model": "gpt-6-astra",
            "instructions": "",
            "reasoning": {"effort": "ultra"},
            "input": [{"role": "user", "content": "Continue"}],
        },
    )
    updated_text = request_submit_module._text_with_previous_response_id(
        text_data,
        "resp_hard_turn",
        api_key=_key(allowed=["ultra"]),
        request_state=cast(Any, request_state),
    )

    wire_payload = json.loads(updated_text)
    assert wire_payload["previous_response_id"] == "resp_hard_turn"
    assert wire_payload["input"][0] == {
        "type": "configuration_update",
        "reasoning": {"effort": "max"},
    }
    assert wire_payload["client_metadata"] == {"caller": "test"}
    assert request_state.input_item_count == 2
    assert request_state.input_full_fingerprint == request_submit_module._fingerprint_input_items(wire_payload["input"])
    assert request_state.steering_configuration["input"][0]["reasoning"]["effort"] == "ultra"
    repeated_text = request_submit_module._text_with_previous_response_id(
        updated_text,
        "resp_hard_turn",
        api_key=_key(allowed=["ultra"]),
        request_state=cast(Any, request_state),
    )
    assert json.loads(repeated_text) == wire_payload
