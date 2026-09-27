"""Standalone notifications are portable only within the direct-source policy."""

from __future__ import annotations

import copy

import pytest

from app.modules.model_sources.projection import PortabilityView
from app.modules.proxy.replay_safety import (
    responses_payload_is_account_neutral_fresh_replay,
    standalone_function_output_is_account_neutral,
    transcript_is_source_free,
)
from app.modules.proxy.source_ownership import OwnershipScope

pytestmark = pytest.mark.unit
SCOPE = OwnershipScope("client", "custom/model")


def notification(**extra):
    return {
        "type": "function_call_output",
        "id": "fco_client",
        "name": "send_message_to_thread",
        "namespace": "codex_app",
        "output": "Subtask input.",
        **extra,
    }


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"namespace": ""},
        {"output": ""},
        {"output": [{"type": "input_text", "text": "Subtask input."}]},
        {"output": [{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]},
        {"internal_chat_message_metadata_passthrough": {"turn_id": "local-turn"}},
    ],
)
@pytest.mark.parametrize("with_id", [True, False])
def test_standalone_notification_is_neutral_without_mutating_wire_or_subscription(extra, with_id):
    item = notification(**extra)
    if not with_id:
        item.pop("id")
    payload = {"input": [item]}
    original = copy.deepcopy(payload)
    assert standalone_function_output_is_account_neutral(item)
    assert SCOPE.request_keys(payload) == set()
    assert transcript_is_source_free(PortabilityView(payload), allow_direct_source_tools=True)
    assert not transcript_is_source_free(PortabilityView(payload))
    assert not responses_payload_is_account_neutral_fresh_replay(payload)
    assert SCOPE.response_keys({"output": [item]}) == ({SCOPE.key("item", "fco_client")} if with_id else set())
    assert payload == original


@pytest.mark.parametrize(
    "extra",
    [
        {"call_id": "call_unknown"},
        {"call_id": None},
        {"call_id": ""},
        {"call_id": []},
        {"name": " "},
        {"name": None},
        {"name": {}},
        {"namespace": None},
        {"namespace": []},
        {"type": "custom_tool_call_output"},
        {"type": "apply_patch_call_output"},
        {"type": []},
        {"status": "completed"},
        {"caller": {"type": "direct"}},
        {"encrypted_content": "opaque"},
        {"container_id": "owned"},
        {"unknown_field": "opaque"},
        {"internal_chat_message_metadata_passthrough": {}},
        {"internal_chat_message_metadata_passthrough": {"turn_id": "t", "conversation_id": "owned"}},
        {"output": None},
        {"output": {}},
        {"output": []},
        {"output": [{"type": "input_text", "text": "ok", "opaque": "state"}]},
        {"output": [{"type": "input_image", "file_id": "file-owned"}]},
        {"output": [{"type": "input_image", "image_url": "sediment://owned"}]},
        {"output": [{"type": "input_file", "file_id": "file-owned"}]},
        {"output": [{"type": "encrypted_content", "encrypted_content": "opaque"}]},
    ],
)
def test_unknown_or_scoped_notification_stays_protected(extra):
    item = notification(**extra)
    payload = {"input": [item]}
    assert not standalone_function_output_is_account_neutral(item)
    assert SCOPE.key("item", "fco_client") in SCOPE.request_keys(payload)
    assert not transcript_is_source_free(PortabilityView(payload), allow_direct_source_tools=True)


@pytest.mark.parametrize("field", ["type", "name", "output"])
def test_required_fields_cannot_be_omitted(field):
    item = notification()
    item.pop(field)
    assert not standalone_function_output_is_account_neutral(item)


@pytest.mark.parametrize("value", [None, "", " ", 12, []])
def test_optional_id_must_be_valid_if_present(value):
    assert not standalone_function_output_is_account_neutral(notification(id=value))


def test_notification_cannot_settle_an_unpaired_call():
    payload = {
        "input": [
            {"type": "function_call", "name": "exec", "call_id": "call_owned", "arguments": "{}"},
            notification(),
        ]
    }
    assert SCOPE.request_keys(payload) == {SCOPE.key("call", "call_owned")}
    assert not transcript_is_source_free(PortabilityView(payload), allow_direct_source_tools=True)


def test_standalone_notification_can_omit_namespace():
    item = notification()
    item.pop("namespace")
    assert standalone_function_output_is_account_neutral(item)
