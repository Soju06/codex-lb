"""Direct-source client bookkeeping allowances must not loosen subscription replay."""

from __future__ import annotations

import copy

import pytest

from app.core.types import JsonValue
from app.modules.proxy.replay_safety import (
    PortabilityView,
    client_message_id_is_account_neutral,
    responses_payload_is_account_neutral_fresh_replay,
    transcript_is_source_free,
)
from app.modules.proxy.source_ownership import OwnershipScope

pytestmark = pytest.mark.unit
SCOPE = OwnershipScope("client-key", "custom/model")


@pytest.mark.parametrize("role", ["user", "developer", "system"])
@pytest.mark.parametrize("typed", [True, False])
def test_complete_client_message_id_is_neutral_only_for_direct_source(role: str, typed: bool) -> None:
    message: dict[str, JsonValue] = {
        "id": "msg_client_generated",
        "role": role,
        "content": [{"type": "input_text", "text": "Local instructions"}],
    }
    if typed:
        message["type"] = "message"
    payload: dict[str, JsonValue] = {"input": [message]}
    before = copy.deepcopy(payload)
    assert client_message_id_is_account_neutral(message)
    assert SCOPE.request_keys(payload) == set()
    assert transcript_is_source_free(PortabilityView(payload), allow_direct_source_tools=True)
    assert not transcript_is_source_free(PortabilityView(payload))
    assert not responses_payload_is_account_neutral_fresh_replay(payload)
    assert payload == before
    # Even a client-role message in an upstream output is recorded as output evidence.
    assert SCOPE.response_keys({"output": [message]}) == {SCOPE.key("item", "msg_client_generated")}


@pytest.mark.parametrize(
    "changes",
    [
        {"role": "assistant"},
        {"role": []},
        {"content": None},
        {"content": [{"type": "input_file", "file_id": "file-scoped"}]},
        {"content": [{"type": "input_image", "file_id": "file-scoped"}]},
        {"encrypted_content": "ciphertext"},
        {"call_id": "call-scoped"},
        {"status": "in_progress"},
        {"opaque_owner": "unknown"},
        {"type": "item_reference"},
        {"internal_chat_message_metadata_passthrough": {"conversation_id": "conv-scoped"}},
    ],
)
def test_nonportable_message_keeps_ownership_evidence(changes: dict[str, JsonValue]) -> None:
    item: dict[str, JsonValue] = {"id": "msg_owned", "type": "message", "role": "user", "content": "text", **changes}
    payload: dict[str, JsonValue] = {"input": [item]}
    assert not client_message_id_is_account_neutral(item)
    assert SCOPE.key("item", "msg_owned") in SCOPE.request_keys(payload)
    assert not transcript_is_source_free(PortabilityView(payload), allow_direct_source_tools=True)


def test_developer_tool_bundle_id_is_not_client_message_bookkeeping() -> None:
    item: dict[str, JsonValue] = {
        "type": "additional_tools",
        "role": "developer",
        "id": "bundle-owned",
        "tools": [{"type": "function", "name": "ping", "parameters": {"type": "object"}}],
    }
    payload: dict[str, JsonValue] = {"input": [item]}
    assert not client_message_id_is_account_neutral(item)
    assert SCOPE.request_keys(payload) == {SCOPE.key("item", "bundle-owned")}
    assert not transcript_is_source_free(PortabilityView(payload), allow_direct_source_tools=True)


@pytest.mark.parametrize("content_types", [["text"], ["image"], ["text", "image"]])
def test_search_content_types_are_only_direct_source_neutral(content_types: list[JsonValue]) -> None:
    payload: dict[str, JsonValue] = {
        "input": "hello",
        "tools": [{"type": "web_search", "external_web_access": False, "search_content_types": content_types}],
    }
    original = copy.deepcopy(payload)
    assert transcript_is_source_free(
        PortabilityView(payload), supported_tool_types=frozenset({"web_search"}), allow_direct_source_tools=True
    )
    assert not transcript_is_source_free(PortabilityView(payload), supported_tool_types=frozenset({"web_search"}))
    assert not responses_payload_is_account_neutral_fresh_replay(payload)
    assert payload == original


@pytest.mark.parametrize("value", [None, [], "text", ["video"], [True], [{}], ["text", "file-scoped"]])
def test_invalid_search_content_types_fail_closed(value: JsonValue) -> None:
    payload: dict[str, JsonValue] = {"input": "hello", "tools": [{"type": "web_search", "search_content_types": value}]}
    assert not transcript_is_source_free(
        PortabilityView(payload), supported_tool_types=frozenset({"web_search"}), allow_direct_source_tools=True
    )


@pytest.mark.parametrize("extra", [{"container": "container-scoped"}, {"future_option": True}])
def test_valid_search_content_types_do_not_hide_unknown_fields(extra: dict[str, JsonValue]) -> None:
    payload: dict[str, JsonValue] = {
        "input": "hello",
        "tools": [{"type": "web_search", "search_content_types": ["text", "image"], **extra}],
    }
    assert not transcript_is_source_free(
        PortabilityView(payload), supported_tool_types=frozenset({"web_search"}), allow_direct_source_tools=True
    )
