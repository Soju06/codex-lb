from __future__ import annotations

import pytest

from app.core.types import JsonValue
from app.modules.proxy.replay_safety import (
    project_responses_input_for_account_neutral_fresh_replay,
    responses_input_items_are_self_contained_fresh_replay,
    responses_payload_is_account_neutral_fresh_replay,
)


def test_account_neutral_fresh_replay_accepts_self_contained_tool_search_pair() -> None:
    input_items: list[JsonValue] = [
        {
            "type": "tool_search_call",
            "call_id": "call_search",
            "arguments": {"query": "codex-lb"},
            "status": "completed",
        },
        {
            "type": "tool_search_output",
            "call_id": "call_search",
            "tools": [{"name": "workspace-search"}],
            "status": "completed",
        },
        {"role": "user", "content": [{"type": "input_text", "text": "continue"}]},
    ]

    assert responses_input_items_are_self_contained_fresh_replay(input_items) is True
    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is True


def test_account_neutral_fresh_replay_accepts_tool_search_string_output() -> None:
    input_items: list[JsonValue] = [
        {
            "type": "tool_search_call",
            "call_id": "call_search",
            "arguments": {"query": "codex-lb"},
            "execution": "client",
            "status": "completed",
        },
        {
            "type": "tool_search_output",
            "call_id": "call_search",
            "execution": "client",
            "output": "completed",
            "status": "completed",
        },
    ]

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is True


def test_account_neutral_fresh_replay_rejects_failed_tool_search_output() -> None:
    input_items: list[JsonValue] = [
        {
            "type": "tool_search_call",
            "call_id": "call_search",
            "arguments": {"query": "codex-lb"},
            "execution": "client",
            "status": "completed",
        },
        {
            "type": "tool_search_output",
            "call_id": "call_search",
            "execution": "client",
            "output": "failed",
            "status": "failed",
        },
    ]

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is False


def test_account_neutral_replay_projection_preserves_completed_tool_search_output_without_id() -> None:
    input_items: list[JsonValue] = [
        {"role": "user", "content": "old question"},
        {
            "type": "tool_search_call",
            "id": "tsc_owner_a",
            "call_id": "call_search",
            "arguments": {"query": "codex-lb"},
            "execution": "client",
            "status": "completed",
        },
        {
            "type": "tool_search_output",
            "id": "tso_owner_a",
            "call_id": "call_search",
            "execution": "client",
            "output": "completed",
            "status": "completed",
        },
        {"role": "user", "content": "next question"},
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(input_items, stored_count=1)

    assert projection is not None
    assert projection.input_items == [
        {"role": "user", "content": "old question"},
        {
            "type": "tool_search_call",
            "call_id": "call_search",
            "arguments": {"query": "codex-lb"},
            "execution": "client",
            "status": "completed",
        },
        {
            "type": "tool_search_output",
            "call_id": "call_search",
            "execution": "client",
            "output": "completed",
            "status": "completed",
        },
        {"role": "user", "content": "next question"},
    ]


def test_account_neutral_fresh_replay_rejects_tool_search_arguments_with_nested_compaction() -> None:
    input_items: list[JsonValue] = [
        {
            "type": "tool_search_call",
            "call_id": "call_search",
            "arguments": {
                "query": "codex-lb",
                "state": {"type": "compaction", "encrypted_content": "enc_owner_scoped"},
            },
            "status": "completed",
        },
        {
            "type": "tool_search_output",
            "call_id": "call_search",
            "tools": [],
            "status": "completed",
        },
    ]

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is False


def test_account_neutral_fresh_replay_rejects_server_executed_tool_search_output() -> None:
    input_items: list[JsonValue] = [
        {
            "type": "tool_search_call",
            "call_id": "call_search",
            "arguments": {"query": "codex-lb"},
            "execution": "client",
            "status": "completed",
        },
        {
            "type": "tool_search_output",
            "call_id": "call_search",
            "execution": "server",
            "tools": [{"name": "workspace-search"}],
            "status": "completed",
        },
    ]

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is False


@pytest.mark.parametrize("tools", [{"name": "workspace-search"}, "workspace-search"])
def test_account_neutral_fresh_replay_rejects_malformed_tool_search_tools_field(tools: JsonValue) -> None:
    output: dict[str, JsonValue] = {
        "type": "tool_search_output",
        "call_id": "call_search",
        "status": "completed",
        "tools": tools,
    }
    input_items: list[JsonValue] = [
        {
            "type": "tool_search_call",
            "call_id": "call_search",
            "arguments": {"query": "codex-lb"},
            "execution": "client",
            "status": "completed",
        },
        output,
    ]

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is False


def test_account_neutral_fresh_replay_rejects_ambiguous_tool_search_output_shape() -> None:
    input_items: list[JsonValue] = [
        {
            "type": "tool_search_call",
            "call_id": "call_search",
            "arguments": {"query": "codex-lb"},
            "execution": "client",
            "status": "completed",
        },
        {
            "type": "tool_search_output",
            "call_id": "call_search",
            "output": "completed",
            "tools": [],
            "status": "completed",
        },
    ]

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is False


def test_account_neutral_fresh_replay_rejects_tool_search_output_without_payload() -> None:
    output: dict[str, JsonValue] = {
        "type": "tool_search_output",
        "call_id": "call_search",
        "status": "completed",
    }
    input_items: list[JsonValue] = [
        {
            "type": "tool_search_call",
            "call_id": "call_search",
            "arguments": {"query": "codex-lb"},
            "execution": "client",
            "status": "completed",
        },
        output,
    ]

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is False
