"""Inline agent task content is distinct from retained upstream state."""

from __future__ import annotations

import copy

import pytest

from app.modules.model_sources.projection import PortabilityView
from app.modules.proxy.replay_safety import (
    inline_agent_message_is_source_neutral,
    responses_payload_is_account_neutral_fresh_replay,
    transcript_is_source_free,
)
from app.modules.proxy.source_ownership import OwnershipScope

pytestmark = pytest.mark.unit
SCOPE = OwnershipScope("client", "custom/model")


def agent_message(**extra):
    return {
        "type": "agent_message",
        "id": "local-agent-task",
        "author": "/root",
        "recipient": "/root/probe",
        "content": [
            {"type": "input_text", "text": "Message Type: NEW_TASK\nPayload:\n"},
            {"type": "encrypted_content", "encrypted_content": "encrypted-agent-task"},
        ],
        **extra,
    }


@pytest.mark.parametrize(
    "content",
    [
        [{"type": "input_text", "text": "Do the task."}],
        [{"type": "input_text", "text": ""}],
        [{"type": "encrypted_content", "encrypted_content": "encrypted-agent-task"}],
        agent_message()["content"],
    ],
)
@pytest.mark.parametrize("with_id", [False, True])
def test_inline_agent_content_keeps_wire_and_subscription_policy(content, with_id):
    item = agent_message(content=content, internal_chat_message_metadata_passthrough={"turn_id": "turn"})
    if not with_id:
        item.pop("id")
    payload = {"input": [item]}
    before = copy.deepcopy(payload)
    assert inline_agent_message_is_source_neutral(item)
    assert not SCOPE.request_keys(payload)
    assert transcript_is_source_free(PortabilityView(payload), allow_direct_source_tools=True)
    assert not transcript_is_source_free(PortabilityView(payload))
    assert not responses_payload_is_account_neutral_fresh_replay(payload)
    assert SCOPE.response_keys({"output": [item]}) == ({SCOPE.key("item", "local-agent-task")} if with_id else set())
    assert payload == before


@pytest.mark.parametrize(
    "extra",
    [
        {"type": "message"},
        {"type": []},
        {"author": " "},
        {"recipient": None},
        {"id": ""},
        {"id": None},
        {"id": []},
        {"previous_response_id": "owned"},
        {"call_id": "owned"},
        {"encrypted_content": "reasoning-state"},
        {"opaque_state": "unknown"},
        {"role": "developer"},
        {"internal_chat_message_metadata_passthrough": {}},
        {"internal_chat_message_metadata_passthrough": {"turn_id": "t", "conversation_id": "owned"}},
        {"content": []},
        {"content": None},
        {"content": "text"},
        {"content": [None]},
        {"content": [{"type": "input_text", "text": 1}]},
        {"content": [{"type": "input_text", "text": "ok", "id": "owned"}]},
        {"content": [{"type": "input_text", "text": "ok", "encrypted_content": "opaque"}]},
        {"content": [{"type": "input_file", "file_id": "owned"}]},
        {"content": [{"type": "input_image", "file_id": "owned"}]},
        {"content": [{"type": "encrypted_content", "encrypted_content": " "}]},
        {"content": [{"type": "encrypted_content", "encrypted_content": {"id": "owned"}}]},
        {"content": [{"type": "encrypted_content", "encrypted_content": "cipher", "file_id": "owned"}]},
        {"content": [{"type": "reasoning", "encrypted_content": "cipher"}]},
        {"content": [{"type": "encrypted_content"}]},
    ],
)
def test_malformed_or_scoped_agent_content_is_not_portable(extra):
    item = agent_message(**extra)
    assert not inline_agent_message_is_source_neutral(item)
    assert not transcript_is_source_free(PortabilityView({"input": [item]}), allow_direct_source_tools=True)
    if item.get("id") == "local-agent-task":
        assert SCOPE.key("item", "local-agent-task") in SCOPE.request_keys({"input": [item]})


@pytest.mark.parametrize("field", ["type", "author", "recipient", "content"])
def test_agent_message_required_fields(field):
    item = agent_message()
    item.pop(field)
    assert not inline_agent_message_is_source_neutral(item)


@pytest.mark.parametrize(
    "retained",
    [
        {"type": "reasoning", "encrypted_content": "cipher"},
        {"type": "compaction", "encrypted_content": "cipher"},
        {"type": "function_call_output", "call_id": "owned-call", "output": "done"},
        {"type": "item_reference", "id": "local-agent-task"},
    ],
)
def test_inline_agent_message_cannot_erase_retained_state(retained):
    payload = {"input": [agent_message(), retained]}
    assert SCOPE.request_keys(payload) == SCOPE.request_keys({"input": [retained]})
    assert SCOPE.request_keys(payload)
    assert not transcript_is_source_free(PortabilityView(payload), allow_direct_source_tools=True)
