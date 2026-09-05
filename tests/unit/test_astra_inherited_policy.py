from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import app.modules.proxy._service.http_bridge.request_submit as request_submit_module
import app.modules.proxy.service as proxy_service
from app.core.exceptions import ProxyInvalidRequestError, ProxyReasoningEffortNotAllowed
from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.modules.api_keys.service import ApiKeyData, ApiKeyUsageReservationData
from app.modules.proxy.request_policy import (
    apply_api_key_enforcement,
    prepare_astra_reasoning_policy_continuation,
    validate_astra_request,
)
from tests.unit.test_proxy_utils import _repo_factory, _RequestLogsRecorder


def _key(*, allowed: list[str] | None = None, enforced: str | None = None) -> ApiKeyData:
    return cast(
        ApiKeyData,
        SimpleNamespace(
            id="astra-inherited-policy",
            enforced_model=None,
            enforced_service_tier=None,
            enforced_reasoning_effort=enforced,
            allowed_reasoning_efforts=allowed,
            allowed_models=None,
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

    assert request.reasoning is not None
    if client_effort == "ultra":
        assert request.reasoning.effort == "ultra"
    assert request.input == [
        {"type": "configuration_update", "reasoning": {"effort": wire_effort}},
        {"role": "user", "content": "Continue"},
    ]
    forwarded_effort = "max" if client_effort == "ultra" else wire_effort
    forwarded = request.to_payload()
    assert forwarded["reasoning"] == {"effort": forwarded_effort}
    assert forwarded["input"] == [
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
    assert request_state.input_full_fingerprint == request_submit_module._fingerprint_input_items(
        cast(list[JsonValue], request.input)
    )
    assert request_state.request_text == text_data


def test_http_bridge_injected_anchor_resets_before_derived_state() -> None:
    text_data = json.dumps(
        {
            "type": "response.create",
            "model": "gpt-6-astra",
            "instructions": "",
            "reasoning": {"effort": "high"},
            "input": [{"role": "user", "content": "Continue"}],
            "client_metadata": {"caller": "test"},
        }
    )

    request_state = SimpleNamespace(
        input_item_count=1,
        input_full_fingerprint=None,
        request_usage_budget=None,
    )
    updated_text = request_submit_module._text_with_previous_response_id(
        text_data,
        "resp_injected",
        api_key=_key(allowed=["high"]),
        request_state=cast(Any, request_state),
    )

    wire_payload = json.loads(updated_text)
    assert wire_payload["previous_response_id"] == "resp_injected"
    assert wire_payload["input"][0] == {
        "type": "configuration_update",
        "reasoning": {"effort": "high"},
    }
    assert wire_payload["client_metadata"] == {"caller": "test"}
    assert request_state.input_item_count == 2
    assert request_state.input_full_fingerprint == request_submit_module._fingerprint_input_items(wire_payload["input"])
    repeated_text = request_submit_module._text_with_previous_response_id(
        updated_text,
        "resp_injected",
        api_key=_key(allowed=["high"]),
        request_state=cast(Any, request_state),
    )
    assert json.loads(repeated_text) == wire_payload


def test_http_bridge_injected_anchor_preserves_ultra_from_request_state() -> None:
    text_data = json.dumps(
        {
            "type": "response.create",
            "model": "gpt-6-astra",
            "instructions": "",
            "reasoning": {"effort": "max"},
            "input": [{"role": "user", "content": "Continue"}],
        }
    )
    request_state = SimpleNamespace(
        reasoning_effort="ultra",
        input_item_count=1,
        input_full_fingerprint=None,
        request_usage_budget=None,
    )

    updated_text = request_submit_module._text_with_previous_response_id(
        text_data,
        "resp_injected",
        api_key=_key(allowed=["ultra"]),
        request_state=cast(Any, request_state),
    )

    wire_payload = json.loads(updated_text)
    assert wire_payload["previous_response_id"] == "resp_injected"
    assert wire_payload["reasoning"] == {"effort": "max"}
    assert wire_payload["input"][0] == {
        "type": "configuration_update",
        "reasoning": {"effort": "max"},
    }


@pytest.mark.asyncio
async def test_websocket_create_rejects_disallowed_configuration_update(monkeypatch) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    key = _key(allowed=["low"])
    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_refresh_websocket_api_key_policy", AsyncMock(return_value=key))

    with pytest.raises(ProxyReasoningEffortNotAllowed):
        await service._prepare_websocket_response_create_request(
            {
                "type": "response.create",
                "model": "gpt-6-astra",
                "instructions": "",
                "reasoning": {"effort": "low"},
                "input": [
                    {"role": "user", "content": "First task"},
                    {"type": "configuration_update", "reasoning": {"effort": "high"}},
                    {"role": "user", "content": "Continue"},
                ],
            },
            headers={},
            codex_session_affinity=False,
            openai_cache_affinity=False,
            sticky_threads_enabled=False,
            openai_cache_affinity_max_age_seconds=300,
            api_key=key,
        )


@pytest.mark.asyncio
async def test_websocket_trims_full_resend_before_astra_reset(monkeypatch) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    key = _key(allowed=["high"])
    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_refresh_websocket_api_key_policy", AsyncMock(return_value=key))
    prepared = await service._prepare_websocket_response_create_request(
        {
            "type": "response.create",
            "model": "gpt-6-astra",
            "instructions": "",
            "reasoning": {"effort": "high"},
            "previous_response_id": "resp_stored",
            "input": [
                {"role": "assistant", "content": "prior answer"},
                {"type": "function_call", "name": "shell", "call_id": "call_old", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_old", "output": "done"},
                {"role": "user", "content": "next"},
            ],
        },
        headers={},
        codex_session_affinity=True,
        openai_cache_affinity=False,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=300,
        api_key=key,
    )
    forwarded = json.loads(prepared.text_data)["input"]
    assert forwarded[0] == {"type": "configuration_update", "reasoning": {"effort": "high"}}
    assert forwarded[1]["type"] == "function_call_output"
    assert all(item.get("role") != "assistant" for item in forwarded if isinstance(item, dict))


@pytest.mark.asyncio
async def test_websocket_session_anchor_releases_reservation_on_astra_policy_error(monkeypatch) -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    key = _key(allowed=["low"])
    reservation = ApiKeyUsageReservationData(
        reservation_id="res_ws_policy",
        key_id="astra-inherited-policy",
        model="gpt-6-astra",
    )
    historical = [{"role": "user", "content": "First"}]
    continuity = proxy_service._WebSocketContinuityState(
        last_completed_input_count=1,
        last_completed_response_id="resp_session",
        last_completed_input_prefix_fingerprint=proxy_service._fingerprint_input_items(historical),
    )
    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", AsyncMock(return_value=reservation))
    monkeypatch.setattr(service, "_refresh_websocket_api_key_policy", AsyncMock(return_value=key))
    release = AsyncMock()
    monkeypatch.setattr(service, "_release_websocket_reservation", release)

    with pytest.raises(ProxyReasoningEffortNotAllowed):
        await service._prepare_websocket_response_create_request(
            {
                "type": "response.create",
                "model": "gpt-6-astra",
                "instructions": "",
                "input": [*historical, {"role": "user", "content": "Continue"}],
            },
            headers={"session_id": "sid-astra-policy"},
            codex_session_affinity=True,
            openai_cache_affinity=False,
            sticky_threads_enabled=False,
            openai_cache_affinity_max_age_seconds=300,
            api_key=key,
            continuity_state=continuity,
        )
    release.assert_awaited_once_with(reservation)
