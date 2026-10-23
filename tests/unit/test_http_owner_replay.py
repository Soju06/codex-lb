from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.db.models import HttpBridgeSessionState
from app.modules.proxy._service.http_bridge.streaming import _verify_durable_full_resend
from app.modules.proxy._service.response_create import _fingerprint_input_items
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeLookup
from app.modules.proxy.replay_safety import responses_payload_is_account_neutral_fresh_replay


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "prefix",
        "missing_output",
        "missing_parallel",
        "unknown_manifest",
        "duplicate",
        "malformed_preamble",
        "fresh_developer",
        "malformed_metadata",
        "unknown_preamble_field",
        "agent_message",
    ],
)
def test_owner_proof_is_complete_immutable_and_separate_from_account_neutral(fault):
    prefix: list[dict[str, JsonValue]] = [
        {"type": "additional_tools", "role": "developer", "tools": [{"type": "function", "name": "sleep"}]},
        {
            "type": "message",
            "id": "msg_control",
            "role": "developer",
            "content": [{"type": "input_text", "text": "Historical control"}],
        },
        {"role": "user", "content": [{"type": "input_text", "text": "Remember everything"}]},
    ]
    suffix: list[dict[str, JsonValue]] = [
        {"type": "reasoning", "encrypted_content": "opaque-context", "id": "rs_native"},
        {
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "phase": "commentary",
            "content": [{"type": "output_text", "text": "Checking"}],
        },
        {
            "type": "function_call",
            "namespace": "clock",
            "name": "sleep",
            "call_id": "call_current",
            "arguments": "{}",
            "internal_chat_message_metadata_passthrough": {"turn_id": "t", "create_time": 1.0},
        },
        {"type": "function_call_output", "call_id": "call_current", "output": "done"},
    ]
    lookup = DurableBridgeLookup(
        session_id="s",
        canonical_kind="session_header",
        canonical_key="k",
        api_key_scope="",
        account_id="owner",
        owner_instance_id="instance",
        owner_epoch=1,
        lease_expires_at=None,
        state=HttpBridgeSessionState.ACTIVE,
        latest_turn_state=None,
        latest_response_id="resp_prior",
        latest_input_item_count=len(prefix),
        latest_input_full_fingerprint=_fingerprint_input_items(prefix),
        latest_pending_tool_calls={"call_current": "function_call"},
    )
    if fault == "prefix":
        prefix[-1]["content"] = "Changed context"
    elif fault == "missing_output":
        suffix.pop()
    elif fault == "missing_parallel":
        lookup = replace(
            lookup, latest_pending_tool_calls={"call_current": "function_call", "call_missing": "function_call"}
        )
    elif fault == "unknown_manifest":
        lookup = replace(lookup, latest_pending_tool_calls=None)
    elif fault == "duplicate":
        suffix.append(deepcopy(suffix[-1]))
    elif fault == "malformed_preamble":
        suffix[1]["status"] = "in_progress"
    elif fault == "fresh_developer":
        suffix.insert(2, {"role": "developer", "content": "new control"})
    elif fault == "malformed_metadata":
        suffix[2]["internal_chat_message_metadata_passthrough"] = "not-an-object"
    elif fault == "unknown_preamble_field":
        suffix[1]["unknown_context"] = "unverified"
    elif fault == "agent_message":
        suffix.append({"type": "agent_message", "encrypted_content": "opaque-agent-input"})
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "keep", "input": [*prefix, *suffix], "prompt_cache_key": "stable"}
    )
    before = deepcopy(payload.model_dump())
    proof = _verify_durable_full_resend(payload, lookup)
    assert payload.model_dump() == before
    assert not responses_payload_is_account_neutral_fresh_replay(payload.to_payload())
    if fault:
        assert proof is None
    else:
        assert proof is not None and proof.matches(payload, lookup)
        assert not proof.matches(payload, replace(lookup, account_id="another_owner"))
        assert not proof.matches(payload, replace(lookup, latest_response_id="resp_other"))
        assert not proof.matches(payload, replace(lookup, latest_pending_tool_calls={"other": "function_call"}))
        changed = payload.model_copy(deep=True)
        assert isinstance(changed.input, list)
        assert isinstance(changed.input[-1], dict)
        changed.input[-1]["output"] = "different"
        assert not proof.matches(changed, lookup)
