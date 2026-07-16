from __future__ import annotations

from app.core.openai.requests import ResponsesRequest
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
            {"role": "assistant", "content": [{"type": "output_text", "text": "first reply"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "second turn"}]},
        ],
        prompt_cache_key="client-cache-key",
        previous_response_id=previous_response_id,
    )


def test_shared_instruction_cache_skips_anchored_responses() -> None:
    request = _cacheable_lite_request(previous_response_id="resp_previous")

    assert request.enable_shared_instruction_cache() is False

    payload = request.to_payload()
    assert payload["prompt_cache_key"] == "client-cache-key"
    assert all(
        "prompt_cache_breakpoint" not in part
        for item in payload["input"]
        if isinstance(item, dict)
        for part in item.get("content", [])
        if isinstance(part, dict)
    )


def test_legacy_owner_forward_without_sdk_header_stays_sdk(monkeypatch) -> None:
    monkeypatch.setattr(bridge, "_sign_bridge_payload", lambda _payload: "signature")
    payload = _cacheable_lite_request()
    context = bridge.HTTPBridgeForwardContext(
        origin_instance="old-origin",
        target_instance="owner",
        codex_session_affinity=False,
        downstream_turn_state=None,
        openai_sdk_request=True,
    )
    headers = bridge.build_owner_forward_headers(headers={}, payload=payload, context=context)
    headers.pop(bridge.HTTP_BRIDGE_OPENAI_SDK_HEADER)
    headers.pop(bridge.HTTP_BRIDGE_SIGNATURE_V2_HEADER)

    forwarded, error = bridge.parse_forwarded_request(
        headers,
        payload=payload,
        current_instance="owner",
    )

    assert error is None
    assert forwarded is not None
    assert forwarded.context.openai_sdk_request is True
