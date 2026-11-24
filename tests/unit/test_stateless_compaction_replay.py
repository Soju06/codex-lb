from copy import deepcopy

import pytest

from app.core.openai.requests import ResponsesRequest
from app.modules.proxy.stateless_replay import quota_replay, quota_replay_block_reason
from tests.integration.test_compacted_quota_replay import compacted_input


def request():
    return ResponsesRequest.model_validate(
        {"model": "gpt-6.1-terra", "instructions": "work", "input": compacted_input(True)}
    )


def test_every_original_item_and_opaque_byte_is_preserved():
    payload = request()
    payload.input.append({"type": "future_upstream_item", "content": "new inline format"})
    payload.input.append(
        {
            "type": "function_call_output",
            "namespace": "codex_tui",
            "name": "create_thread",
            "output": "local host event",
        }
    )
    payload.input.append(
        {
            "type": "agent_message",
            "author": "/child",
            "recipient": "/root",
            "content": [{"type": "encrypted_content", "encrypted_content": "opaque-agent-state"}],
        }
    )
    payload.input.append(
        {"type": "custom_tool_call_output", "call_id": "c1", "output": [{"type": "input_text", "text": ""}]}
    )
    before = deepcopy(payload.to_payload())
    replay = quota_replay(payload)
    assert replay is not None
    assert replay.to_payload() == before == payload.to_payload()
    assert replay is not payload and replay.input is not payload.input


@pytest.mark.parametrize(
    "owner",
    [
        "previous",
        "conversation",
        "prompt",
        "file",
        "file_list",
        "container",
        "vector",
        "hosted",
        "mcp",
        "image",
        "file_tool",
    ],
)
def test_explicit_upstream_resources_cannot_cross_accounts(owner):
    p = request()
    if owner == "previous":
        p.previous_response_id = "resp_owner"
    elif owner == "conversation":
        p.conversation = "conv_owner"
    elif owner == "prompt":
        p.prompt = {"id": "pmpt_owner"}
    elif owner == "file":
        p.input.append({"type": "input_file", "file_id": "file_owner"})
    elif owner == "file_list":
        p.input.append({"type": "future_item", "file_ids": ["file_owner"]})
    elif owner == "container":
        p.input.append({"type": "future_item", "container_id": "cntr_owner"})
    elif owner == "vector":
        p.input.append({"type": "future_item", "vector_store_id": "vs_owner"})
    elif owner == "hosted":
        p.input.append({"type": "item_reference", "id": "item_owner"})
    elif owner == "mcp":
        p.input.append({"type": "mcp_approval_response", "approval_request_id": "mcpr_owner", "approve": True})
    elif owner == "image":
        p.input.append({"type": "input_image", "image_url": "file-owner"})
    elif owner == "file_tool":
        p.tools = [{"type": "file_search", "vector_store_ids": ["vs_owner"]}]
    assert quota_replay_block_reason(p) is not None
    assert quota_replay(p) is None


def test_function_schema_named_file_id_is_not_a_stored_resource():
    p = request()
    p.tools = [
        {
            "type": "function",
            "name": "lookup",
            "parameters": {"type": "object", "properties": {"file_id": {"type": "string"}}},
        }
    ]
    assert quota_replay_block_reason(p) is None
    assert quota_replay(p) is not None


@pytest.mark.parametrize(
    "item",
    [
        {"type": "computer_call", "call_id": "call_1", "action": {"type": "click", "x": 1, "y": 2}},
        {
            "type": "computer_call_output",
            "call_id": "call_1",
            "output": {"type": "computer_screenshot", "image_url": "data:image/png;base64,abc"},
        },
        {"type": "mcp_call", "server_label": "service", "arguments": "{}", "output": "result"},
    ],
)
def test_inline_computer_and_mcp_calls_do_not_block_migration(item):
    p = request()
    p.input.append(item)
    p.tools = [{"type": "mcp", "server_label": "service", "server_url": "https://example.com/mcp"}]
    assert quota_replay_block_reason(p) is None
    assert quota_replay(p).to_payload() == p.to_payload()
