import json
from copy import deepcopy
from typing import cast

import pytest

from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.websocket.helpers import (
    _install_verified_fresh_replay,
    _prepare_websocket_request_state_for_account_switch,
    _project_websocket_full_resend_for_replay,
    _websocket_request_text_is_account_neutral_fresh_replay,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("lite", "reasoning", "expected"),
    [
        (True, {"context": "all_turns", "effort": "high"}, True),
        (False, {"context": "all_turns"}, False),
        (True, {"context": "last_turn"}, False),
        (True, {"context": {"id": "rs_owner"}}, False),
        (True, {"context": "all_turns", "unknown": "value"}, False),
    ],
)
def test_websocket_lite_replay_accepts_only_canonical_context(lite, reasoning, expected):
    items = [{"role": "user", "content": "hello"}]
    if lite:
        items.insert(0, {"type": "additional_tools", "role": "developer", "tools": []})
    payload = {"type": "response.create", "model": "gpt-5.4", "input": items, "reasoning": reasoning}
    assert _websocket_request_text_is_account_neutral_fresh_replay(json.dumps(payload)) is expected


@pytest.mark.parametrize("unsafe", [None, "missing_reply", "unpaired_call", "file", "prompt", "unknown_type"])
def test_full_resend_projection_preserves_content_and_rejects_unsafe_replay(unsafe: str | None) -> None:
    """Project response-owned fields only when the complete payload remains portable."""
    user = {"role": "user", "content": "hello"}
    call = {"type": "function_call", "id": "fc_owner", "call_id": "call_1", "name": "read", "arguments": "{}"}
    output = {"type": "function_call_output", "call_id": "call_1", "output": "file contents"}
    assistant = {
        "type": "message",
        "id": "msg_owner",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Done"}],
    }
    follow_up = {"role": "user", "content": "continue"}
    items = [
        user,
        {"type": "reasoning", "id": "rs_owner", "encrypted_content": "ciphertext", "summary": []},
        call,
        output,
        assistant,
        follow_up,
    ]
    if unsafe == "missing_reply":
        items.remove(assistant)
    elif unsafe == "unpaired_call":
        items.remove(output)
    elif unsafe == "file":
        follow_up["content"] = [{"type": "input_file", "file_id": "file_owner"}]
    payload = ResponsesRequest.model_validate(
        {
            "type": "unknown" if unsafe == "unknown_type" else "response.create",
            "model": "gpt-5.4",
            "instructions": "",
            "input": items,
            **({"prompt": {"id": "pmpt_owner"}} if unsafe == "prompt" else {}),
        }
    )
    payload = dict(payload.to_replay_safety_payload())
    original = deepcopy(payload)

    projected = _project_websocket_full_resend_for_replay(payload, stored_count=1)

    assert payload == original
    if unsafe is not None:
        assert projected is None
    else:
        assert projected is not None
        assert projected["input"] == [
            user,
            {key: value for key, value in call.items() if key != "id"},
            output,
            {key: value for key, value in assistant.items() if key != "id"},
            follow_up,
        ]


def test_size_slimmed_resend_does_not_preserve_client_fingerprint() -> None:
    """A slimmed retry must refresh identity so later turns resend missing context."""
    items: list[JsonValue] = [
        {"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64," + "A" * 6000}]},
        {"type": "reasoning", "encrypted_content": "owner-ciphertext"},
        {"type": "function_call", "call_id": "call_1", "name": "read", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": "historical output " * 1000},
        {"role": "assistant", "content": "Done"},
        {"role": "user", "content": "continue"},
    ]
    payload: dict[str, JsonValue] = {"type": "response.create", "model": "gpt-5.4", "input": items}
    slimmed, stats = proxy_service._slim_response_create_payload_for_upstream(payload, max_bytes=1024)
    assert stats is not None
    assert slimmed["input"] != items
    original_fingerprint = proxy_service._fingerprint_input_items(items)
    state = proxy_service._WebSocketRequestState(
        request_id="slimmed-replay",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        previous_response_id="resp_owner",
        preferred_account_id="account_owner",
        proxy_injected_previous_response_id=True,
        fresh_upstream_request_is_retry_safe=True,
        fresh_upstream_request_stored_input_count=4,
        fresh_upstream_request_text=json.dumps(slimmed),
        input_item_count=len(items),
        input_full_fingerprint=original_fingerprint,
    )

    assert _prepare_websocket_request_state_for_account_switch(state) is None
    assert _install_verified_fresh_replay(state, require_account_neutral=False) is not None
    assert state.input_full_fingerprint != original_fingerprint
    assert state.input_full_fingerprint == proxy_service._fingerprint_input_items(
        cast(list[JsonValue], slimmed["input"])
    )
