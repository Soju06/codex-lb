"""Direct-source metadata changes neither wire input nor subscription replay."""

from __future__ import annotations

import copy

import pytest

from app.core.types import JsonValue
from app.modules.model_sources.projection import PortabilityView
from app.modules.proxy.replay_safety import (
    project_direct_source_input_metadata,
    responses_payload_is_account_neutral_fresh_replay,
    transcript_is_source_free,
)
from app.modules.proxy.source_ownership import OwnershipScope

META: dict[str, JsonValue] = {"turn_id": "turn_client", "create_time": 1790503200.25, "content_item_kinds": ["text"]}


def message(metadata, *, role="user", with_id=True):
    item = {
        "type": "message",
        "role": role,
        "content": [{"type": "input_text", "text": "hello"}],
        "internal_chat_message_metadata_passthrough": metadata,
    }
    if with_id:
        item["id"] = "client_message"
    return item


@pytest.mark.parametrize("role", ["user", "system", "developer"])
@pytest.mark.parametrize("with_id", [True, False])
@pytest.mark.parametrize(
    "metadata",
    [META, {"turn_id": "turn_client", "create_time": 1790503200}, {"turn_id": "turn_client", "content_item_kinds": []}],
)
def test_client_metadata_is_direct_source_bookkeeping(role, with_id, metadata):
    payload = {"input": [message(metadata, role=role, with_id=with_id)]}
    original = copy.deepcopy(payload)
    assert OwnershipScope("key", "model").request_keys(payload) == set()
    assert transcript_is_source_free(PortabilityView(payload), allow_direct_source_tools=True)
    assert not transcript_is_source_free(PortabilityView(payload))
    assert not responses_payload_is_account_neutral_fresh_replay(payload)
    assert payload == original


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"create_time": 1},
        {**META, "turn_id": " "},
        {**META, "turn_id": 1},
        {**META, "create_time": True},
        {**META, "create_time": "1"},
        {**META, "create_time": None},
        {**META, "create_time": float("nan")},
        {**META, "create_time": float("inf")},
        {**META, "content_item_kinds": "text"},
        {**META, "content_item_kinds": [1]},
        {**META, "content_item_kinds": [""]},
        {**META, "content_item_kinds": [{"file_id": "file_unknown"}]},
        {**META, "opaque_owner": "unknown"},
    ],
)
def test_unproven_metadata_remains_nonportable(metadata):
    payload = {"input": [message(metadata)]}
    scope = OwnershipScope("key", "model")
    assert scope.request_keys(payload) == {scope.key("item", "client_message")}
    assert not transcript_is_source_free(PortabilityView(payload), allow_direct_source_tools=True)
    assert project_direct_source_input_metadata(payload["input"])[0] is payload["input"][0]


@pytest.mark.parametrize(
    "item",
    [
        {"type": "reasoning", "id": "rs_owned", "encrypted_content": "cipher_owned", "summary": []},
        {"type": "compaction", "id": "compaction_owned", "encrypted_content": "cipher_owned"},
        {"type": "item_reference", "id": "item_owned"},
        {"type": "message", "id": "assistant_owned", "role": "assistant", "content": "hello"},
        {"type": "function_call_output", "id": "result_local", "call_id": "call_owned", "output": "done"},
    ],
)
def test_valid_metadata_does_not_erase_upstream_references(item):
    original = copy.deepcopy(item)
    decorated: dict[str, JsonValue] = {**item, "internal_chat_message_metadata_passthrough": META}
    scope = OwnershipScope("key", "model")
    assert scope.request_keys({"input": [decorated]}) == scope.request_keys({"input": [item]})
    assert not transcript_is_source_free(PortabilityView({"input": [decorated]}), allow_direct_source_tools=True)
    assert item == original
