"""Strict client result bookkeeping keeps call ownership and replay boundaries."""

from __future__ import annotations

import copy

import pytest

from app.modules.proxy.replay_safety import (
    PortabilityView,
    client_tool_output_id_is_account_neutral,
    responses_payload_is_account_neutral_fresh_replay,
    transcript_is_source_free,
)
from app.modules.proxy.source_ownership import OwnershipScope

pytestmark = pytest.mark.unit
SCOPE = OwnershipScope("client", "custom/model")


def output_item(**changes):
    return {"type": "function_call_output", "id": "client-result", "call_id": "owned-call", "output": "done", **changes}


@pytest.mark.parametrize("kind", ["function_call_output", "custom_tool_call_output"])
@pytest.mark.parametrize(
    "content",
    [
        "done",
        [{"type": "input_text", "text": "done"}],
        [{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}],
    ],
)
def test_result_id_is_bookkeeping_but_unpaired_call_is_not_portable(kind, content):
    item = output_item(type=kind, output=content)
    payload = {"input": [item]}
    original = copy.deepcopy(payload)
    assert client_tool_output_id_is_account_neutral(item)
    assert SCOPE.request_keys(payload) == {SCOPE.key("call", "owned-call")}
    assert SCOPE.response_keys({"output": [item]}) == {
        SCOPE.key("call", "owned-call"),
        SCOPE.key("item", "client-result"),
    }
    assert not transcript_is_source_free(PortabilityView(payload), allow_direct_source_tools=True)
    assert not responses_payload_is_account_neutral_fresh_replay(payload)
    assert payload == original


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_patch_result_can_have_status_without_output(status):
    item = output_item(type="apply_patch_call_output", output=None, status=status)
    assert client_tool_output_id_is_account_neutral(item)
    assert SCOPE.request_keys({"input": [item]}) == {SCOPE.key("call", "owned-call")}


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "mcp_call_output"},
        {"type": "computer_call_output"},
        {"type": "function_call"},
        {"type": []},
        {"call_id": ""},
        {"call_id": " "},
        {"call_id": None},
        {"call_id": []},
        {"status": "in_progress"},
        {"status": {}},
        {"caller": {"type": "tool", "tool_id": "owned"}},
        {"output": None},
        {"output": []},
        {"output": {}},
        {"output": [{"type": "input_text", "text": ""}]},
        {"output": [{"type": "input_file", "file_id": "file-owned"}]},
        {"output": [{"type": "input_image", "file_id": "file-owned"}]},
        {"output": [{"type": "input_image", "image_url": "sediment://owned"}]},
        {"output": [{"type": "input_text", "text": "done", "encrypted_content": "opaque"}]},
        {"output": [{"type": "item_reference", "id": "owned"}]},
        {"encrypted_content": "opaque"},
        {"container_id": "owned"},
        {"opaque_owner": "unknown"},
        {"internal_chat_message_metadata_passthrough": {"conversation_id": "owned"}},
    ],
)
def test_unvalidated_or_scoped_result_keeps_item_reference(changes):
    item = output_item(**changes)
    assert not client_tool_output_id_is_account_neutral(item)
    assert SCOPE.key("item", "client-result") in SCOPE.request_keys({"input": [item]})


@pytest.mark.parametrize("item_id", [None, "", " ", [], 42])
def test_malformed_local_id_is_not_exempted(item_id):
    assert not client_tool_output_id_is_account_neutral(output_item(id=item_id))
