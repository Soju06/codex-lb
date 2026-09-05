from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.modules.proxy.replay_safety import responses_payload_is_account_neutral_fresh_replay


@pytest.fixture
def context_request():
    return json.loads((Path(__file__).parents[1] / "fixtures/codex_context_replay.json").read_text())


def test_self_contained_context_classification_preserves_exact_payload(context_request):
    original = copy.deepcopy(context_request)
    assert responses_payload_is_account_neutral_fresh_replay(context_request)
    assert context_request == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("previous_response_id", "resp_other_account"),
        ("conversation", "conv_other_account"),
        ("unknown_context", {}),
    ],
)
def test_context_cannot_hide_stored_or_unknown_root_state(context_request, field, value):
    context_request[field] = value
    assert not responses_payload_is_account_neutral_fresh_replay(context_request)


@pytest.mark.parametrize(
    "extra",
    [
        {"type": "reasoning", "encrypted_content": "opaque"},
        {"type": "item_reference", "id": "msg_stored"},
        {"type": "input_file", "file_id": "file_stored"},
        {"type": "function_call", "call_id": "unsettled", "name": "read_file", "arguments": "{}"},
    ],
)
def test_context_does_not_make_retained_or_incomplete_items_portable(context_request, extra):
    context_request["input"].append(extra)
    assert not responses_payload_is_account_neutral_fresh_replay(context_request)


@pytest.mark.parametrize(
    "field,value",
    [
        ("create_time", True),
        ("create_time", float("inf")),
        ("content_item_kinds", [""]),
        ("account_id", "stored_owner"),
    ],
)
def test_context_transcript_metadata_is_closed_and_typed(context_request, field, value):
    context_request["input"][2]["internal_chat_message_metadata_passthrough"][field] = value
    assert not responses_payload_is_account_neutral_fresh_replay(context_request)


@pytest.mark.parametrize("label", ["msg_server_object", "at_00000000-0000-4000-8000-000000000002"])
def test_message_labels_must_be_canonical_local_message_ids(context_request, label):
    context_request["input"][1]["id"] = label
    assert not responses_payload_is_account_neutral_fresh_replay(context_request)


@pytest.mark.parametrize(
    "child",
    [
        {"type": "file_search", "vector_store_ids": ["vs_owner"]},
        {"type": "namespace", "name": "nested", "tools": []},
        {"type": "function", "name": "bad", "container_id": "container_owner"},
    ],
)
def test_namespaces_do_not_hide_hosted_resources(context_request, child):
    context_request["input"][0]["tools"][0]["tools"].append(child)
    assert not responses_payload_is_account_neutral_fresh_replay(context_request)


def test_context_projection_requires_recognized_mode(context_request):
    context_request["reasoning"]["context"] = "unknown"
    assert not responses_payload_is_account_neutral_fresh_replay(context_request)
    del context_request["reasoning"]["context"]
    assert not responses_payload_is_account_neutral_fresh_replay(context_request)
