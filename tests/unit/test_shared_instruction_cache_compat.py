from __future__ import annotations

import hashlib

import pytest
from app.core.openai.requests import ResponsesRequest
from app.modules.proxy import affinity
from app.modules.proxy import http_bridge_forwarding as bridge


def _cacheable_lite_request(*, previous_response_id: str | None = None) -> ResponsesRequest:
    return ResponsesRequest(
        model="gpt-5.6",
        instructions="",
        input=[
            {"type": "additional_tools", "role": "developer", "tools": []},
            {
                "role": "developer",
                "content": [{"type": "input_text", "text": "base instructions"}],
            },
            {"role": "user", "content": [{"type": "input_text", "text": "first turn"}]},
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "first reply"}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "second turn"}],
            },
        ],
        prompt_cache_key="client-cache-key",
        previous_response_id=previous_response_id,
    )


def _deterministic_signature(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


def test_shared_instruction_cache_skips_anchored_responses() -> None:
    request = _cacheable_lite_request(previous_response_id="resp_previous")

    assert request.enable_shared_instruction_cache() is False

    payload = request.to_payload()
    assert payload["prompt_cache_key"] == "client-cache-key"
    assert payload["input"] == request.input


def test_shared_instruction_cache_clears_state_for_anchored_model_copy() -> None:
    request = _cacheable_lite_request()
    assert request.enable_shared_instruction_cache() is True

    anchored = request.model_copy(update={"previous_response_id": "resp_previous"})
    affinity._sticky_key_for_responses_request(
        anchored,
        {},
        codex_session_affinity=True,
        openai_cache_affinity=False,
        openai_cache_affinity_max_age_seconds=0,
        sticky_threads_enabled=False,
    )

    payload = anchored.to_payload()
    assert payload["prompt_cache_key"] == "client-cache-key"
    assert payload["input"] == anchored.input


def test_shared_instruction_cache_detects_breakpoint_before_instruction_hoist() -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6",
            "input": [
                {
                    "role": "developer",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "base instructions",
                            "prompt_cache_breakpoint": {"mode": "explicit"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "stable context"}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "current task"}],
                },
            ],
            "prompt_cache_key": "client-cache-key",
        }
    )

    assert request.instructions == "base instructions"
    assert request.enable_shared_instruction_cache() is False
    assert request.to_payload()["prompt_cache_key"] == "client-cache-key"


def test_legacy_v2_owner_forward_without_sdk_header_stays_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bridge, "_sign_bridge_payload", _deterministic_signature)
    payload = _cacheable_lite_request()
    context = bridge.HTTPBridgeForwardContext(
        origin_instance="old-origin",
        target_instance="owner",
        codex_session_affinity=False,
        downstream_turn_state=None,
        original_request_unanchored=True,
    )
    headers = bridge.build_owner_forward_headers(headers={}, payload=payload, context=context)
    headers.pop(bridge.HTTP_BRIDGE_OPENAI_SDK_HEADER)
    headers[bridge.HTTP_BRIDGE_SIGNATURE_V2_HEADER] = bridge._bridge_forward_tools_bound_signature(
        payload=payload,
        context=context,
        signature_version="2",
        include_openai_sdk_request=False,
    )

    forwarded, error = bridge.parse_forwarded_request(
        headers,
        payload=payload,
        current_instance="owner",
    )

    assert error is None
    assert forwarded is not None
    assert forwarded.context.openai_sdk_request is True


def test_owner_forward_rejects_sdk_header_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bridge, "_sign_bridge_payload", _deterministic_signature)
    payload = _cacheable_lite_request()
    context = bridge.HTTPBridgeForwardContext(
        origin_instance="new-origin",
        target_instance="owner",
        codex_session_affinity=False,
        downstream_turn_state=None,
        openai_sdk_request=True,
    )
    headers = bridge.build_owner_forward_headers(headers={}, payload=payload, context=context)
    headers[bridge.HTTP_BRIDGE_OPENAI_SDK_HEADER] = "0"

    forwarded, error = bridge.parse_forwarded_request(
        headers,
        payload=payload,
        current_instance="owner",
    )

    assert forwarded is None
    assert error is not None
    assert error.status_code == 400


def test_owner_forward_rejects_sdk_header_without_v2_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bridge, "_sign_bridge_payload", _deterministic_signature)
    payload = _cacheable_lite_request()
    context = bridge.HTTPBridgeForwardContext(
        origin_instance="new-origin",
        target_instance="owner",
        codex_session_affinity=False,
        downstream_turn_state=None,
        openai_sdk_request=True,
    )
    headers = bridge.build_owner_forward_headers(headers={}, payload=payload, context=context)
    headers[bridge.HTTP_BRIDGE_OPENAI_SDK_HEADER] = "0"
    headers.pop(bridge.HTTP_BRIDGE_SIGNATURE_V2_HEADER)

    forwarded, error = bridge.parse_forwarded_request(
        headers,
        payload=payload,
        current_instance="owner",
    )

    assert forwarded is None
    assert error is not None
    assert error.status_code == 400
