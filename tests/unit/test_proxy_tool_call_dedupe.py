from __future__ import annotations

import json

import pytest

from app.core.types import JsonValue
from app.core.utils.sse import format_sse_event
from app.modules.proxy import service as proxy_service
from app.modules.proxy import tool_call_dedupe

pytestmark = pytest.mark.unit


def test_mark_duplicate_tool_call_downstream_event_suppresses_distinct_call_ids_with_same_arguments():
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
        is True
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
            replay_payload,
            seen_tool_call_keys=upstream_control.seen_tool_call_keys,
            response_id="resp_dupe",
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
                    "yield_time_ms": 1000,
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
        is True
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


def test_mark_duplicate_tool_call_downstream_event_bounds_seen_key_cache():
    upstream_control = proxy_service._WebSocketUpstreamControl()

    for index in range(tool_call_dedupe._TOOL_CALL_DEDUPE_CACHE_LIMIT + 3):
        assert (
            tool_call_dedupe.mark_duplicate_tool_call_downstream_event(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "name": "shell",
                        "call_id": f"call_{index}",
                        "arguments": f'{{"index":{index}}}',
                    },
                },
                seen_tool_call_keys=upstream_control.seen_tool_call_keys,
                response_id="resp_1",
            )
            is False
        )

    assert len(upstream_control.seen_tool_call_keys) == tool_call_dedupe._TOOL_CALL_DEDUPE_CACHE_LIMIT


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
