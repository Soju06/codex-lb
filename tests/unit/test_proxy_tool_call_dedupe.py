from __future__ import annotations

import json
from typing import cast

import pytest

from app.core.types import JsonValue
from app.core.utils.sse import format_sse_event
from app.modules.proxy import service as proxy_service
from app.modules.proxy import tool_call_dedupe

pytestmark = pytest.mark.unit


def test_mark_duplicate_tool_call_downstream_event_keeps_distinct_call_ids_with_same_arguments():
    upstream_control = proxy_service._WebSocketUpstreamControl()
    first_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_dupe",
        "item": {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": '{"session_id":1,"chars":"","yield_time_ms":1000}',
            "call_id": "call_a",
        },
    }
    second_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_dupe",
        "item": {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": '{"session_id":1,"chars":"","yield_time_ms":1000}',
            "call_id": "call_b",
        },
    }
    different_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_dupe",
        "item": {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": '{"session_id":1,"chars":"x","yield_time_ms":1000}',
            "call_id": "call_c",
        },
    }

    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            first_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_dupe",
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            second_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_dupe",
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            different_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_dupe",
        )
        is False
    )


def test_mark_duplicate_tool_call_downstream_event_suppresses_exec_command_with_volatile_differences():
    upstream_control = proxy_service._WebSocketUpstreamControl()
    first_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_dupe",
        "item": {
            "type": "function_call",
            "name": "exec_command",
            "arguments": '{"cmd":"echo hi","yield_time_ms":1000,"max_output_tokens":2000}',
            "call_id": "call_a",
        },
    }
    replay_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_dupe",
        "item": {
            "type": "function_call",
            "name": "exec_command",
            "arguments": '{"max_output_tokens":9000,"cmd":"echo hi","yield_time_ms":30000}',
            "call_id": "call_a",
        },
    }

    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            first_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_dupe",
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            replay_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_dupe",
        )
        is True
    )


def test_mark_duplicate_tool_call_downstream_event_suppresses_direct_wait_agent_replay():
    upstream_control = proxy_service._WebSocketUpstreamControl()
    first_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_wait",
        "item": {
            "type": "function_call",
            "name": "wait_agent",
            "arguments": '{"targets":["agent_b","agent_a"],"timeout_ms":30000}',
            "call_id": "call_a",
        },
    }
    replay_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_wait",
        "item": {
            "type": "function_call",
            "name": "wait_agent",
            "arguments": '{"targets":["agent_a","agent_b"],"timeout_ms":60000}',
            "call_id": "call_a",
        },
    }

    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            first_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_wait",
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            replay_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_wait",
        )
        is True
    )


def test_mark_duplicate_tool_call_downstream_event_suppresses_namespaced_write_stdin_replay():
    upstream_control = proxy_service._WebSocketUpstreamControl()
    first_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_namespaced",
        "item": {
            "type": "function_call",
            "name": "functions.write_stdin",
            "arguments": '{"session_id":17,"chars":"","yield_time_ms":1000,"max_output_tokens":4000}',
            "call_id": "call_a",
        },
    }
    replay_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_namespaced",
        "item": {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": '{"session_id":17,"chars":"","yield_time_ms":1000,"max_output_tokens":24000}',
            "call_id": "call_a",
        },
    }

    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            first_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_namespaced",
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            replay_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_namespaced",
        )
        is True
    )


def test_mark_duplicate_tool_call_downstream_event_suppresses_write_stdin_with_volatile_differences():
    upstream_control = proxy_service._WebSocketUpstreamControl()
    first_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_write",
        "item": {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": '{"session_id":17,"chars":"","yield_time_ms":1000,"max_output_tokens":4000}',
            "call_id": "call_a",
        },
    }
    replay_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_write",
        "item": {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": '{"session_id":17,"chars":"","yield_time_ms":30000,"max_output_tokens":24000}',
            "call_id": "call_a",
        },
    }

    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            first_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_write",
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            replay_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_write",
        )
        is True
    )


def test_rewrite_parallel_tool_call_payload_removes_duplicate_side_effect_tool_uses():
    arguments = {
        "tool_uses": [
            {
                "recipient_name": "functions.exec_command",
                "parameters": {
                    "cmd": "gh pr create --repo Komzpa/evince",
                    "yield_time_ms": 1000,
                },
            },
            {
                "recipient_name": "functions.exec_command",
                "parameters": {
                    "cmd": "gh pr create --repo Komzpa/evince",
                    "yield_time_ms": 30000,
                },
            },
            {
                "recipient_name": "functions.exec_command",
                "parameters": {
                    "cmd": "gh pr view --repo Komzpa/evince",
                    "yield_time_ms": 1000,
                },
            },
        ]
    }
    payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_parallel",
        "item": {
            "type": "function_call",
            "name": "multi_tool_use.parallel",
            "arguments": json.dumps(arguments),
            "call_id": "call_parallel",
        },
    }

    rewritten_payload, changed, removed_count = tool_call_dedupe.rewrite_parallel_tool_call_payload(payload)

    assert changed is True
    assert removed_count == 1
    assert isinstance(rewritten_payload, dict)
    item = rewritten_payload["item"]
    assert isinstance(item, dict)
    rewritten_arguments = json.loads(item["arguments"])
    assert len(rewritten_arguments["tool_uses"]) == 2
    commands = [tool_use["parameters"]["cmd"] for tool_use in rewritten_arguments["tool_uses"]]
    assert commands == [
        "gh pr create --repo Komzpa/evince",
        "gh pr view --repo Komzpa/evince",
    ]


def test_rewrite_parallel_tool_call_payload_removes_duplicate_write_stdin_owner():
    arguments = {
        "tool_uses": [
            {
                "recipient_name": "functions.write_stdin",
                "parameters": {
                    "session_id": 41288,
                    "chars": "",
                    "yield_time_ms": 30000,
                    "max_output_tokens": 6000,
                },
            },
            {
                "recipient_name": "functions.write_stdin",
                "parameters": {
                    "session_id": 41288,
                    "chars": "",
                    "yield_time_ms": 30000,
                    "max_output_tokens": 2000,
                },
            },
            {
                "recipient_name": "functions.write_stdin",
                "parameters": {
                    "session_id": 41288,
                    "chars": "y",
                    "yield_time_ms": 1000,
                },
            },
        ]
    }
    payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_parallel",
        "item": {
            "type": "function_call",
            "name": "multi_tool_use.parallel",
            "arguments": json.dumps(arguments),
            "call_id": "call_parallel",
        },
    }

    rewritten_payload, changed, removed_count = tool_call_dedupe.rewrite_parallel_tool_call_payload(payload)

    assert changed is True
    assert removed_count == 1
    assert isinstance(rewritten_payload, dict)
    item = rewritten_payload["item"]
    assert isinstance(item, dict)
    rewritten_arguments = json.loads(item["arguments"])
    chars = [tool_use["parameters"]["chars"] for tool_use in rewritten_arguments["tool_uses"]]
    assert chars == ["", "y"]


def test_rewrite_parallel_tool_call_payload_removes_duplicate_wait_agent_targets():
    arguments = {
        "tool_uses": [
            {
                "recipient_name": "functions.wait_agent",
                "parameters": {
                    "targets": ["agent_b", "agent_a"],
                    "timeout_ms": 30000,
                },
            },
            {
                "recipient_name": "functions.wait_agent",
                "parameters": {
                    "targets": ["agent_a", "agent_b"],
                    "timeout_ms": 60000,
                },
            },
        ]
    }
    payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_parallel",
        "item": {
            "type": "function_call",
            "name": "multi_tool_use.parallel",
            "arguments": json.dumps(arguments),
            "call_id": "call_parallel",
        },
    }

    rewritten_payload, changed, removed_count = tool_call_dedupe.rewrite_parallel_tool_call_payload(payload)

    assert changed is True
    assert removed_count == 1
    assert isinstance(rewritten_payload, dict)
    item = rewritten_payload["item"]
    assert isinstance(item, dict)
    rewritten_arguments = json.loads(item["arguments"])
    assert len(rewritten_arguments["tool_uses"]) == 1
    assert rewritten_arguments["tool_uses"][0]["parameters"]["targets"] == ["agent_b", "agent_a"]


def test_rewrite_parallel_tool_call_payload_tolerates_mixed_wait_agent_targets():
    arguments = {
        "tool_uses": [
            {
                "recipient_name": "functions.wait_agent",
                "parameters": {
                    "targets": [1, "agent_a", {}],
                    "timeout_ms": 30000,
                },
            },
            {
                "recipient_name": "functions.wait_agent",
                "parameters": {
                    "targets": ["agent_a"],
                    "timeout_ms": 30000,
                },
            },
        ]
    }
    payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_parallel",
        "item": {
            "type": "function_call",
            "name": "multi_tool_use.parallel",
            "arguments": json.dumps(arguments),
            "call_id": "call_parallel",
        },
    }

    rewritten_payload, changed, removed_count = tool_call_dedupe.rewrite_parallel_tool_call_payload(payload)

    assert changed is False
    assert removed_count == 0
    assert rewritten_payload is payload


def test_rewrite_parallel_tool_call_payload_sorts_wait_agent_mapping_targets_canonically():
    arguments = {
        "tool_uses": [
            {
                "recipient_name": "functions.wait_agent",
                "parameters": {
                    "targets": [{"agent": "b", "kind": "spark"}, {"kind": "spark", "agent": "a"}],
                    "timeout_ms": 30000,
                },
            },
            {
                "recipient_name": "functions.wait_agent",
                "parameters": {
                    "targets": [{"kind": "spark", "agent": "a"}, {"kind": "spark", "agent": "b"}],
                    "timeout_ms": 60000,
                },
            },
        ]
    }
    payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_parallel",
        "item": {
            "type": "function_call",
            "name": "multi_tool_use.parallel",
            "arguments": json.dumps(arguments),
            "call_id": "call_parallel",
        },
    }

    rewritten_payload, changed, removed_count = tool_call_dedupe.rewrite_parallel_tool_call_payload(payload)

    assert changed is True
    assert removed_count == 1
    assert isinstance(rewritten_payload, dict)


def test_rewrite_parallel_tool_call_payload_keeps_duplicate_read_only_connector_uses():
    arguments = {
        "tool_uses": [
            {
                "recipient_name": "github.read_file",
                "parameters": {
                    "repo": "Soju06/codex-lb",
                    "path": "README.md",
                },
            },
            {
                "recipient_name": "github.read_file",
                "parameters": {
                    "repo": "Soju06/codex-lb",
                    "path": "README.md",
                },
            },
        ]
    }
    payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_parallel",
        "item": {
            "type": "function_call",
            "name": "multi_tool_use.parallel",
            "arguments": json.dumps(arguments),
            "call_id": "call_parallel",
        },
    }

    rewritten_payload, changed, removed_count = tool_call_dedupe.rewrite_parallel_tool_call_payload(payload)

    assert changed is False
    assert removed_count == 0
    assert rewritten_payload is payload


def test_mark_duplicate_tool_call_downstream_event_suppresses_parallel_wrapper_replay():
    upstream_control = proxy_service._WebSocketUpstreamControl()
    arguments = json.dumps(
        {
            "tool_uses": [
                {
                    "recipient_name": "functions.exec_command",
                    "parameters": {"cmd": "gh pr create --repo Komzpa/evince"},
                }
            ]
        },
        separators=(",", ":"),
    )
    first_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_parallel",
        "item": {
            "type": "function_call",
            "name": "multi_tool_use.parallel",
            "arguments": arguments,
            "call_id": "call_first",
        },
    }
    replay_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_parallel",
        "item": {
            "type": "function_call",
            "name": "multi_tool_use.parallel",
            "arguments": arguments,
            "call_id": "call_replayed",
        },
    }

    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            first_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_parallel",
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            replay_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_parallel",
        )
        is True
    )


def test_mark_duplicate_tool_call_downstream_event_trims_overlapping_parallel_replay():
    upstream_control = proxy_service._WebSocketUpstreamControl()
    first_arguments = json.dumps(
        {
            "tool_uses": [
                {
                    "recipient_name": "functions.exec_command",
                    "parameters": {"cmd": "gh pr view --repo Soju06/codex-lb"},
                },
                {
                    "recipient_name": "functions.exec_command",
                    "parameters": {"cmd": "gh pr checks --repo Soju06/codex-lb"},
                },
            ]
        },
        separators=(",", ":"),
    )
    replay_arguments = json.dumps(
        {
            "tool_uses": [
                {
                    "recipient_name": "functions.exec_command",
                    "parameters": {"cmd": "gh pr view --repo Soju06/codex-lb"},
                },
                {
                    "recipient_name": "github.read_file",
                    "parameters": {"repo": "Soju06/codex-lb", "path": "README.md"},
                },
            ]
        },
        separators=(",", ":"),
    )
    first_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_parallel_overlap",
        "item": {
            "type": "function_call",
            "name": "multi_tool_use.parallel",
            "arguments": first_arguments,
            "call_id": "call_first",
        },
    }
    replay_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_parallel_overlap",
        "item": {
            "type": "function_call",
            "name": "multi_tool_use.parallel",
            "arguments": replay_arguments,
            "call_id": "call_replayed",
        },
    }

    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            first_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_parallel_overlap",
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            replay_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_parallel_overlap",
        )
        is False
    )
    replay_item = replay_payload["item"]
    assert isinstance(replay_item, dict)
    replay_item_arguments = replay_item["arguments"]
    assert isinstance(replay_item_arguments, str)
    rewritten_replay_arguments = json.loads(replay_item_arguments)
    assert rewritten_replay_arguments["tool_uses"] == [
        {
            "recipient_name": "github.read_file",
            "parameters": {"repo": "Soju06/codex-lb", "path": "README.md"},
        }
    ]


def test_mark_duplicate_tool_call_downstream_event_can_trim_parallel_replay_across_response_ids():
    upstream_control = proxy_service._WebSocketUpstreamControl()
    first_arguments = json.dumps(
        {
            "tool_uses": [
                {
                    "recipient_name": "functions.exec_command",
                    "parameters": {"cmd": "gh pr view --repo Soju06/codex-lb"},
                },
                {
                    "recipient_name": "functions.write_stdin",
                    "parameters": {"session_id": 1, "chars": ""},
                },
            ]
        },
        separators=(",", ":"),
    )
    replay_arguments = json.dumps(
        {
            "tool_uses": [
                {
                    "recipient_name": "functions.exec_command",
                    "parameters": {"cmd": "gh pr view --repo Soju06/codex-lb"},
                },
                {
                    "recipient_name": "github.read_file",
                    "parameters": {"repo": "Soju06/codex-lb", "path": "README.md"},
                },
            ]
        },
        separators=(",", ":"),
    )
    first_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_parallel_first",
        "item": {
            "type": "function_call",
            "name": "multi_tool_use.parallel",
            "arguments": first_arguments,
            "call_id": "call_first",
        },
    }
    replay_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_parallel_replay",
        "item": {
            "type": "function_call",
            "name": "multi_tool_use.parallel",
            "arguments": replay_arguments,
            "call_id": "call_replayed",
        },
    }

    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            first_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_parallel_first",
            scope_side_effects_by_response_id=False,
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            replay_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_parallel_replay",
            scope_side_effects_by_response_id=False,
        )
        is False
    )
    replay_item = replay_payload["item"]
    assert isinstance(replay_item, dict)
    replay_item_arguments = replay_item["arguments"]
    assert isinstance(replay_item_arguments, str)
    rewritten_replay_arguments = json.loads(replay_item_arguments)
    assert rewritten_replay_arguments["tool_uses"] == [
        {
            "recipient_name": "github.read_file",
            "parameters": {"repo": "Soju06/codex-lb", "path": "README.md"},
        }
    ]


def test_mark_duplicate_tool_call_downstream_event_keeps_read_only_parallel_wrapper_replay():
    upstream_control = proxy_service._WebSocketUpstreamControl()
    arguments = json.dumps(
        {
            "tool_uses": [
                {
                    "recipient_name": "github.read_file",
                    "parameters": {
                        "repo": "Soju06/codex-lb",
                        "path": "README.md",
                    },
                }
            ]
        },
        separators=(",", ":"),
    )
    first_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_parallel_read",
        "item": {
            "type": "function_call",
            "name": "multi_tool_use.parallel",
            "arguments": arguments,
            "call_id": "call_first",
        },
    }
    replay_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_parallel_read",
        "item": {
            "type": "function_call",
            "name": "multi_tool_use.parallel",
            "arguments": arguments,
            "call_id": "call_replayed",
        },
    }

    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            first_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_parallel_read",
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            replay_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_parallel_read",
        )
        is False
    )


def test_mark_duplicate_tool_call_downstream_event_keeps_distinct_read_only_call_ids():
    upstream_control = proxy_service._WebSocketUpstreamControl()
    first_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_dupe",
        "item": {
            "type": "function_call",
            "name": "read",
            "arguments": '{"path":"Intermediate/info/Heartbeat Prep Status.json","limit":200}',
            "call_id": "call_a",
        },
    }
    second_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_dupe",
        "item": {
            "type": "function_call",
            "name": "read",
            "arguments": '{"path":"Intermediate/info/Heartbeat Prep Status.json","limit":200}',
            "call_id": "call_b",
        },
    }

    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            first_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_dupe",
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            second_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_dupe",
        )
        is False
    )


def test_mark_duplicate_tool_call_downstream_event_resets_after_non_side_effect_item():
    upstream_control = proxy_service._WebSocketUpstreamControl()
    first_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_chain",
        "item": {
            "type": "function_call",
            "name": "exec_command",
            "arguments": '{"cmd":"echo hi","yield_time_ms":1000}',
            "call_id": "call_a",
        },
    }
    reasoning_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_chain",
        "item": {
            "type": "reasoning",
            "summary": [],
        },
    }
    repeated_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_chain",
        "item": {
            "type": "function_call",
            "name": "exec_command",
            "arguments": '{"cmd":"echo hi","yield_time_ms":30000}',
            "call_id": "call_b",
        },
    }

    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            first_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_chain",
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            reasoning_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_chain",
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            repeated_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_chain",
        )
        is False
    )


def test_mark_duplicate_tool_call_downstream_event_retains_intervening_side_effects():
    upstream_control = proxy_service._WebSocketUpstreamControl()
    exec_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_chain",
        "item": {
            "type": "function_call",
            "name": "exec_command",
            "arguments": '{"cmd":"pytest","yield_time_ms":1000}',
            "call_id": "call_exec_a",
        },
    }
    patch_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_chain",
        "item": {
            "type": "apply_patch_call",
            "operation": {"type": "update_file", "path": "app.py", "diff": "@@\n- old\n+ new\n"},
            "call_id": "call_patch",
        },
    }
    exec_repeat_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_chain",
        "item": {
            "type": "function_call",
            "name": "exec_command",
            "arguments": '{"cmd":"pytest","yield_time_ms":30000}',
            "call_id": "call_exec_b",
        },
    }

    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            exec_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_chain",
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            patch_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_chain",
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            exec_repeat_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_chain",
        )
        is False
    )


def test_mark_duplicate_tool_call_downstream_event_suppresses_side_effect_replay_bursts_across_response_ids():
    upstream_control = proxy_service._WebSocketUpstreamControl()
    exec_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_first",
        "item": {
            "type": "function_call",
            "name": "exec_command",
            "arguments": '{"cmd":"pytest","yield_time_ms":1000}',
            "call_id": "call_exec_a",
        },
    }
    write_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_first",
        "item": {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": '{"session_id":1,"chars":"","yield_time_ms":1000}',
            "call_id": "call_write_a",
        },
    }
    exec_replay_payload: dict[str, JsonValue] = {
        **exec_payload,
        "response_id": "resp_replay",
        "item": {
            **cast(dict[str, JsonValue], exec_payload["item"]),
            "call_id": "call_exec_b",
        },
    }
    write_replay_payload: dict[str, JsonValue] = {
        **write_payload,
        "response_id": "resp_replay",
        "item": {
            **cast(dict[str, JsonValue], write_payload["item"]),
            "call_id": "call_write_b",
        },
    }

    for payload in (exec_payload, write_payload):
        assert (
            tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
                payload,
                seen_tool_call_keys=upstream_control.seen_tool_call_keys,
                response_id=tool_call_dedupe.response_id_from_payload(payload),
                scope_side_effects_by_response_id=False,
            )
            is False
        )

    for payload in (exec_replay_payload, write_replay_payload):
        assert (
            tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
                payload,
                seen_tool_call_keys=upstream_control.seen_tool_call_keys,
                response_id=tool_call_dedupe.response_id_from_payload(payload),
                scope_side_effects_by_response_id=False,
            )
            is True
        )


def test_mark_duplicate_tool_call_downstream_event_suppresses_apply_patch_call_replay():
    upstream_control = proxy_service._WebSocketUpstreamControl()
    first_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_dupe",
        "item": {
            "type": "apply_patch_call",
            "operation": {"type": "update_file", "path": "app.py", "diff": "@@\n- old\n+ new\n"},
            "call_id": "call_a",
        },
    }
    second_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_dupe",
        "item": {
            "type": "apply_patch_call",
            "operation": {"path": "app.py", "diff": "@@\n- old\n+ new\n", "type": "update_file"},
            "call_id": "call_b",
        },
    }

    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            first_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_dupe",
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            second_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_dupe",
        )
        is False
    )


def test_mark_duplicate_tool_call_downstream_event_scopes_by_response_id():
    upstream_control = proxy_service._WebSocketUpstreamControl()
    first_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_first",
        "item": {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": '{"session_id":1,"chars":"","yield_time_ms":1000}',
        },
    }
    replay_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_replay",
        "item": {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": '{"session_id":1,"chars":"","yield_time_ms":1000}',
        },
    }

    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            first_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id=tool_call_dedupe.response_id_from_payload(first_payload),
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            replay_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id=tool_call_dedupe.response_id_from_payload(replay_payload),
        )
        is False
    )


def test_mark_duplicate_tool_call_downstream_event_can_suppress_one_stream_replay_across_response_ids():
    upstream_control = proxy_service._WebSocketUpstreamControl()
    first_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_first",
        "item": {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": '{"session_id":1,"chars":"","yield_time_ms":1000}',
        },
    }
    replay_payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_replay",
        "item": {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": '{"session_id":1,"chars":"","yield_time_ms":1000}',
        },
    }

    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            first_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id=tool_call_dedupe.response_id_from_payload(first_payload),
            scope_side_effects_by_response_id=False,
        )
        is False
    )
    assert (
        tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
            replay_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id=tool_call_dedupe.response_id_from_payload(replay_payload),
            scope_side_effects_by_response_id=False,
        )
        is True
    )


def test_mark_duplicate_tool_call_downstream_event_bounds_side_effect_history():
    upstream_control = proxy_service._WebSocketUpstreamControl()

    for index in range(tool_call_dedupe._TOOL_CALL_DEDUPE_CACHE_LIMIT + 3):
        assert (
            tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "name": "exec_command",
                        "call_id": f"call_{index}",
                        "arguments": json.dumps({"cmd": f"echo {index}"}),
                    },
                },
                seen_tool_call_keys=upstream_control.seen_tool_call_keys,
                response_id="resp_1",
            )
            is False
        )

    assert len(upstream_control.seen_tool_call_keys) == tool_call_dedupe._TOOL_CALL_DEDUPE_CACHE_LIMIT


def test_dedupe_replayed_side_effect_input_items_removes_duplicate_call_but_preserves_outputs():
    input_items: list[JsonValue] = [
        {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": json.dumps(
                {"session_id": 75180, "chars": "", "yield_time_ms": 30000, "max_output_tokens": 22000}
            ),
            "call_id": "call_first",
        },
        {
            "type": "function_call_output",
            "call_id": "call_first",
            "output": "Process running with session ID 75180",
        },
        {"type": "reasoning", "summary": []},
        {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": json.dumps(
                {"session_id": 75180, "chars": "", "yield_time_ms": 30000, "max_output_tokens": 4000}
            ),
            "call_id": "call_replay",
        },
        {
            "type": "function_call_output",
            "call_id": "call_replay",
            "output": "Process exited with code 0",
        },
    ]

    deduped_items, removed_count = tool_call_dedupe.dedupe_replayed_side_effect_input_items(input_items)

    assert removed_count == 1
    assert [item.get("type") for item in deduped_items if isinstance(item, dict)] == [
        "function_call",
        "function_call_output",
        "reasoning",
        "message",
    ]
    first_call = cast(dict[str, JsonValue], deduped_items[0])
    first_output = cast(dict[str, JsonValue], deduped_items[1])
    replay_output_message = cast(dict[str, JsonValue], deduped_items[-1])
    assert first_call["call_id"] == "call_first"
    assert first_output["output"] == "Process running with session ID 75180"
    assert replay_output_message["role"] == "assistant"
    assert replay_output_message["content"] == [{"type": "output_text", "text": "Process exited with code 0"}]


def test_dedupe_replayed_side_effect_input_items_keeps_distinct_write_payloads():
    input_items: list[JsonValue] = [
        {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": json.dumps({"session_id": 75180, "chars": "", "yield_time_ms": 30000}),
            "call_id": "call_poll",
        },
        {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": json.dumps({"session_id": 75180, "chars": "y", "yield_time_ms": 30000}),
            "call_id": "call_input",
        },
    ]

    deduped_items, removed_count = tool_call_dedupe.dedupe_replayed_side_effect_input_items(input_items)

    assert removed_count == 0
    assert deduped_items == input_items


def test_dedupe_replayed_side_effect_input_items_keeps_distinct_write_waits():
    input_items: list[JsonValue] = [
        {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": json.dumps({"session_id": 75180, "chars": "", "yield_time_ms": 30000}),
            "call_id": "call_long_poll",
        },
        {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": json.dumps({"session_id": 75180, "chars": "", "yield_time_ms": 1000}),
            "call_id": "call_short_poll",
        },
    ]

    deduped_items, removed_count = tool_call_dedupe.dedupe_replayed_side_effect_input_items(input_items)

    assert removed_count == 0
    assert deduped_items == input_items


def test_dedupe_replayed_side_effect_input_items_resets_across_user_turns():
    repeated_arguments = json.dumps({"session_id": 75180, "chars": "", "yield_time_ms": 30000})
    input_items: list[JsonValue] = [
        {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": repeated_arguments,
            "call_id": "call_first",
        },
        {
            "type": "function_call_output",
            "call_id": "call_first",
            "output": "first result",
        },
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "poll again"}]},
        {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": repeated_arguments,
            "call_id": "call_second_turn",
        },
        {
            "type": "function_call_output",
            "call_id": "call_second_turn",
            "output": "second result",
        },
    ]

    deduped_items, removed_count = tool_call_dedupe.dedupe_replayed_side_effect_input_items(input_items)

    assert removed_count == 0
    assert deduped_items == input_items


def test_dedupe_replayed_side_effect_input_items_resets_across_role_only_user_turns():
    repeated_arguments = json.dumps({"session_id": 75180, "chars": "", "yield_time_ms": 30000})
    input_items: list[JsonValue] = [
        {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": repeated_arguments,
            "call_id": "call_first",
        },
        {"type": "function_call_output", "call_id": "call_first", "output": "first result"},
        {"role": "user", "content": [{"type": "input_text", "text": "poll again"}]},
        {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": repeated_arguments,
            "call_id": "call_second_turn",
        },
        {"type": "function_call_output", "call_id": "call_second_turn", "output": "second result"},
    ]

    deduped_items, removed_count = tool_call_dedupe.dedupe_replayed_side_effect_input_items(input_items)

    assert removed_count == 0
    assert deduped_items == input_items


def test_dedupe_replayed_side_effect_input_items_preserves_all_outputs_when_call_id_repeats():
    repeated_arguments = json.dumps({"session_id": 75180, "chars": "", "yield_time_ms": 30000})
    input_items: list[JsonValue] = [
        {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": repeated_arguments,
            "call_id": "call_same",
        },
        {"type": "function_call_output", "call_id": "call_same", "output": "Process running"},
        {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": repeated_arguments,
            "call_id": "call_same",
        },
        {"type": "function_call_output", "call_id": "call_same", "output": "Process exited"},
    ]

    deduped_items, removed_count = tool_call_dedupe.dedupe_replayed_side_effect_input_items(input_items)

    assert removed_count == 1
    assert len(deduped_items) == 3
    first_output = cast(dict[str, JsonValue], deduped_items[1])
    replay_output_message = cast(dict[str, JsonValue], deduped_items[-1])
    assert first_output["output"] == "Process running"
    assert replay_output_message["content"] == [{"type": "output_text", "text": "Process exited"}]


def test_dedupe_replayed_side_effect_input_items_keeps_repeat_after_intervening_side_effect():
    repeated_arguments = json.dumps({"cmd": "pytest"})
    input_items: list[JsonValue] = [
        {
            "type": "function_call",
            "name": "exec_command",
            "arguments": repeated_arguments,
            "call_id": "call_pytest_first",
        },
        {"type": "function_call_output", "call_id": "call_pytest_first", "output": "failed"},
        {
            "type": "apply_patch_call",
            "operation": {"type": "update", "path": "app.py"},
            "call_id": "call_patch",
        },
        {"type": "apply_patch_call_output", "call_id": "call_patch", "output": "patched"},
        {
            "type": "function_call",
            "name": "exec_command",
            "arguments": repeated_arguments,
            "call_id": "call_pytest_second",
        },
        {"type": "function_call_output", "call_id": "call_pytest_second", "output": "passed"},
    ]

    deduped_items, removed_count = tool_call_dedupe.dedupe_replayed_side_effect_input_items(input_items)

    assert removed_count == 0
    assert deduped_items == input_items


def test_dedupe_replayed_side_effect_input_items_keeps_read_only_custom_tool_calls():
    input_items: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "name": "read_context",
            "input": json.dumps({"path": "README.md"}),
            "call_id": "call_custom_a",
        },
        {
            "type": "custom_tool_call",
            "name": "read_context",
            "input": json.dumps({"path": "README.md"}),
            "call_id": "call_custom_b",
        },
    ]

    deduped_items, removed_count = tool_call_dedupe.dedupe_replayed_side_effect_input_items(input_items)

    assert removed_count == 0
    assert deduped_items == input_items


def test_dedupe_replayed_side_effect_input_items_resets_across_read_only_tool_call():
    repeated_arguments = json.dumps({"cmd": "pytest"})
    input_items: list[JsonValue] = [
        {
            "type": "function_call",
            "name": "exec_command",
            "arguments": repeated_arguments,
            "call_id": "call_pytest_first",
        },
        {"type": "function_call_output", "call_id": "call_pytest_first", "output": "failed"},
        {
            "type": "custom_tool_call",
            "name": "read_context",
            "input": json.dumps({"path": "README.md"}),
            "call_id": "call_read",
        },
        {"type": "custom_tool_call_output", "call_id": "call_read", "output": "context"},
        {
            "type": "function_call",
            "name": "exec_command",
            "arguments": repeated_arguments,
            "call_id": "call_pytest_second",
        },
        {"type": "function_call_output", "call_id": "call_pytest_second", "output": "passed"},
    ]

    deduped_items, removed_count = tool_call_dedupe.dedupe_replayed_side_effect_input_items(input_items)

    assert removed_count == 0
    assert deduped_items == input_items


def test_rewrite_parallel_tool_call_text_preserves_sse_event_name():
    arguments = {
        "tool_uses": [
            {
                "recipient_name": "functions.exec_command",
                "parameters": {"cmd": "gh pr merge"},
            },
            {
                "recipient_name": "functions.exec_command",
                "parameters": {"cmd": "gh pr merge"},
            },
        ]
    }
    payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_parallel",
        "item": {
            "type": "function_call",
            "name": "multi_tool_use.parallel",
            "arguments": json.dumps(arguments),
            "call_id": "call_parallel",
        },
    }

    _text, rewritten_payload, rewritten_event, rewritten_event_type, rewritten_event_block = (
        tool_call_dedupe.rewrite_parallel_tool_call_text(
            json.dumps(payload),
            payload,
            event_block=format_sse_event(payload),
        )
    )

    assert rewritten_event_block.startswith("event: response.output_item.done\n")
    assert rewritten_event_type == "response.output_item.done"
    assert rewritten_event is not None
    assert rewritten_payload is not None


def test_rewrite_parallel_tool_call_text_preserves_raw_error_type():
    payload: dict[str, JsonValue] = {
        "error": {
            "type": "invalid_request_error",
            "message": "Upstream rejected the shared websocket request.",
        },
        "status": 400,
    }

    text, rewritten_payload, rewritten_event, rewritten_event_type, rewritten_event_block = (
        tool_call_dedupe.rewrite_parallel_tool_call_text(
            json.dumps(payload, separators=(",", ":")),
            payload,
            event_block=f"data: {json.dumps(payload, separators=(',', ':'))}\n\n",
        )
    )

    assert rewritten_event_type == "error"
    assert rewritten_event is None
    assert rewritten_payload == payload
    assert text == json.dumps(payload, separators=(",", ":"))
    assert rewritten_event_block == f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


def test_rewrite_parallel_tool_call_payload_removes_duplicate_goal_side_effects():
    arguments = {
        "tool_uses": [
            {
                "recipient_name": "functions.update_plan",
                "parameters": {"plan": [{"step": "repair", "status": "in_progress"}]},
            },
            {
                "recipient_name": "functions.update_plan",
                "parameters": {"plan": [{"step": "repair", "status": "in_progress"}]},
            },
            {
                "recipient_name": "functions.request_user_input",
                "parameters": {"questions": [{"id": "choice", "question": "Pick one", "options": []}]},
            },
            {
                "recipient_name": "functions.request_user_input",
                "parameters": {"questions": [{"id": "choice", "question": "Pick one", "options": []}]},
            },
        ]
    }
    payload: dict[str, JsonValue] = {
        "type": "response.output_item.done",
        "response_id": "resp_parallel_goal",
        "item": {
            "type": "function_call",
            "name": "multi_tool_use.parallel",
            "arguments": json.dumps(arguments),
            "call_id": "call_parallel",
        },
    }

    rewritten_payload, changed, removed_count = tool_call_dedupe.rewrite_parallel_tool_call_payload(payload)

    assert changed is True
    assert removed_count == 2
    assert isinstance(rewritten_payload, dict)
    item = rewritten_payload["item"]
    assert isinstance(item, dict)
    rewritten_arguments = json.loads(item["arguments"])
    assert [tool_use["recipient_name"] for tool_use in rewritten_arguments["tool_uses"]] == [
        "functions.update_plan",
        "functions.request_user_input",
    ]
