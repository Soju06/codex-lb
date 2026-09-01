from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import JsonValue
from app.modules.proxy.complete_transcript import (
    _deduplicate_exact_replayed_tool_items,
    _strip_omitted_output_prefix,
    build_complete_replay_payload,
    build_replay_input_snapshot,
    build_unanchored_root_replay_payload,
    derive_replay_input_turn_count,
    materialize_output_items_from_events,
)
from app.modules.proxy.durable_bridge_repository import (
    DurableBridgeOperationSnapshot,
    DurableBridgeRepository,
    DurableBridgeTranscriptTurn,
)


def _turn(
    number: int,
    *,
    parent_response_id: str | None,
    response_id: str,
    request_input: list[dict[str, object]],
    output: list[dict[str, object]],
) -> DurableBridgeTranscriptTurn:
    operation = DurableBridgeOperationSnapshot(
        operation_id=f"op_{number}",
        session_id="session",
        request_fingerprint=f"fingerprint_{number}",
        account_id="account",
        model="gpt-test",
        parent_response_id=parent_response_id,
        state="completed",
        response_id=response_id,
        request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": parent_response_id,
                "input": request_input,
            }
        ),
    )
    return DurableBridgeTranscriptTurn(operation=operation, events=(), response_output_items_json=json.dumps(output))


def test_build_complete_replay_payload_inserts_prior_assistant_output() -> None:
    turns = [
        _turn(
            1,
            parent_response_id=None,
            response_id="resp_1",
            request_input=[{"type": "message", "role": "user", "content": "first"}],
            output=[
                {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "answer"}],
                }
            ],
        ),
    ]

    payload = build_complete_replay_payload(
        turns,
        continuation_request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": "resp_1",
                "input": [{"type": "message", "role": "user", "content": "follow up"}],
            }
        ),
    )

    assert payload is not None
    parsed = json.loads(payload)
    assert "previous_response_id" not in parsed
    assert [item["role"] for item in parsed["input"]] == ["user", "assistant", "user"]
    assert all("id" not in item for item in parsed["input"])
    parsed = json.loads(payload)
    assert "previous_response_id" not in parsed
    assert [item["role"] for item in parsed["input"]] == ["user", "assistant", "user"]
    assert all("id" not in item for item in parsed["input"])


def test_build_complete_replay_payload_preserves_latest_output_from_full_history() -> None:
    turns = [
        _turn(
            1,
            parent_response_id=None,
            response_id="resp_1",
            request_input=[{"type": "message", "role": "user", "content": "first"}],
            output=[
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "one"}],
                }
            ],
        ),
        _turn(
            2,
            parent_response_id="resp_1",
            response_id="resp_2",
            request_input=[{"type": "message", "role": "user", "content": "second"}],
            output=[
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "two"}],
                }
            ],
        ),
    ]

    payload = build_complete_replay_payload(
        turns,
        continuation_request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": "resp_2",
                "input": [
                    {"type": "message", "role": "user", "content": "first"},
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "one"}],
                    },
                    {"type": "message", "role": "user", "content": "second"},
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "two"}],
                    },
                    {"type": "message", "role": "user", "content": "third"},
                ],
            }
        ),
    )

    assert payload is not None
    input_items = json.loads(payload)["input"]
    assert [item["content"] for item in input_items if item.get("role") == "user"] == ["first", "second", "third"]
    assert [item["content"][0]["text"] for item in input_items if item.get("role") == "assistant"] == ["one", "two"]


def test_build_unanchored_root_replay_payload_sanitizes_legacy_history() -> None:
    payload = build_unanchored_root_replay_payload(
        json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": None,
                "input": [
                    {"type": "message", "role": "user", "content": "first"},
                    {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "answer",
                                "annotations": [],
                                "logprobs": [],
                            }
                        ],
                        "metadata": {"turn_id": "turn_1"},
                    },
                ],
            }
        )
    )

    assert payload is not None
    replay = json.loads(payload)
    assert replay["type"] == "response.create"
    assert "previous_response_id" not in replay
    assert all("id" not in item for item in replay["input"])
    assert "metadata" not in replay["input"][1]
    assert "annotations" not in replay["input"][1]["content"][0]
    assert "logprobs" not in replay["input"][1]["content"][0]


def test_build_unanchored_root_replay_payload_rejects_anchored_body() -> None:
    assert (
        build_unanchored_root_replay_payload(
            json.dumps(
                {
                    "type": "response.create",
                    "previous_response_id": "resp-stale",
                    "input": [{"type": "message", "role": "user", "content": "continue"}],
                }
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "field, value",
    [
        ("prompt", {"id": "pmpt_account_scoped"}),
        ("uploaded_file", {"id": "file_account_scoped"}),
    ],
)
def test_build_unanchored_root_replay_payload_rejects_account_scoped_top_level_fields(
    field: str,
    value: object,
) -> None:
    body = {
        "type": "response.create",
        "previous_response_id": None,
        "input": [{"type": "message", "role": "user", "content": "history"}],
        field: value,
    }

    assert build_unanchored_root_replay_payload(json.dumps(body)) is None


def test_tool_echo_deduplication_rejects_conflicting_status() -> None:
    items: list[JsonValue] = [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": "{}",
            "status": "completed",
        },
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": "{}",
            "status": "failed",
        },
    ]

    assert _deduplicate_exact_replayed_tool_items(items) is None


def test_output_echo_matching_rejects_conflicting_status_but_allows_omitted_status() -> None:
    stored: list[JsonValue] = [{"type": "function_call", "call_id": "call_1", "status": "completed"}]
    conflicting: list[JsonValue] = [{"type": "function_call", "call_id": "call_1", "status": "failed"}]
    omitted: list[JsonValue] = [{"type": "function_call", "call_id": "call_1"}]

    assert _strip_omitted_output_prefix(conflicting, stored) is None
    assert _strip_omitted_output_prefix(omitted, stored) == []


def test_build_complete_replay_payload_accepts_unanchored_full_history() -> None:
    turns = [
        _turn(
            1,
            parent_response_id=None,
            response_id="resp_1",
            request_input=[{"type": "message", "role": "user", "content": "first"}],
            output=[
                {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "answer"}],
                }
            ],
        ),
    ]

    payload = build_complete_replay_payload(
        turns,
        continuation_request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                # Codex full-history requests intentionally omit the anchor.
                "input": [
                    {"id": "msg_user", "type": "message", "role": "user", "content": "first"},
                    {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "answer"}],
                    },
                    {"type": "message", "role": "user", "content": "follow up"},
                ],
            }
        ),
        allow_unanchored_continuation=True,
    )

    assert payload is not None


def test_build_complete_replay_payload_preserves_prior_output_on_full_history_resend() -> None:
    first_output: dict[str, object] = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "answer"}],
    }
    turns = [
        _turn(
            1,
            parent_response_id=None,
            response_id="resp_1",
            request_input=[{"type": "message", "role": "user", "content": "first"}],
            output=[first_output],
        ),
        _turn(
            2,
            parent_response_id="resp_1",
            response_id="resp_2",
            request_input=[
                {"type": "message", "role": "user", "content": "first"},
                first_output,
                {"type": "message", "role": "user", "content": "follow up"},
            ],
            output=[
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "second answer"}],
                }
            ],
        ),
    ]

    payload = build_complete_replay_payload(turns)

    assert payload is not None
    assert [item["role"] for item in json.loads(payload)["input"]] == ["user", "assistant", "user"]


def test_build_complete_replay_payload_rejects_divergent_unanchored_history() -> None:
    turn = _turn(
        1,
        parent_response_id=None,
        response_id="resp_1",
        request_input=[{"type": "message", "role": "user", "content": "durable history"}],
        output=[
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "answer"}],
            }
        ],
    )

    # A compacted or otherwise divergent client history cannot be proven to
    # be a delta. Reject it rather than concatenating contradictory context
    # with the durable transcript during unanchored recovery.
    assert (
        build_complete_replay_payload(
            [turn],
            continuation_request_text=json.dumps(
                {
                    "type": "response.create",
                    "input": [{"type": "message", "role": "user", "content": "compacted history"}],
                }
            ),
            allow_unanchored_continuation=True,
        )
        is None
    )


def test_build_complete_replay_payload_allows_anchored_delta_with_unanchored_mode() -> None:
    turn = _turn(
        1,
        parent_response_id=None,
        response_id="resp_1",
        request_input=[{"type": "message", "role": "user", "content": "durable history"}],
        output=[
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "answer"}],
            }
        ],
    )

    payload = build_complete_replay_payload(
        [turn],
        continuation_request_text=json.dumps(
            {
                "type": "response.create",
                "previous_response_id": "resp_1",
                "input": [{"type": "message", "role": "user", "content": "follow up"}],
            }
        ),
        allow_unanchored_continuation=True,
    )

    assert payload is not None


def test_build_complete_replay_payload_preserves_repeated_anchored_delta() -> None:
    turn = _turn(
        1,
        parent_response_id=None,
        response_id="resp_1",
        request_input=[{"type": "message", "role": "user", "content": "first"}],
        output=[
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "answer"}],
            }
        ],
    )

    payload = build_complete_replay_payload(
        [turn],
        continuation_request_text=json.dumps(
            {
                "type": "response.create",
                "previous_response_id": "resp_1",
                # This is a new anchored message that happens to equal the
                # durable first message; it is not a full-history resend.
                "input": [{"type": "message", "role": "user", "content": "first"}],
            }
        ),
        allow_unanchored_continuation=True,
    )

    assert payload is not None
    assert [item["role"] for item in json.loads(payload)["input"]] == ["user", "assistant", "user"]


def test_build_complete_replay_payload_rejects_account_scoped_top_level_fields() -> None:
    turns = [
        _turn(
            1,
            parent_response_id=None,
            response_id="resp_1",
            request_input=[{"type": "message", "role": "user", "content": "first"}],
            output=[
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "answer"}],
                }
            ],
        )
    ]

    payload = build_complete_replay_payload(
        turns,
        continuation_request_text=json.dumps(
            {
                "type": "response.create",
                "previous_response_id": "resp_1",
                "input": [{"type": "message", "role": "user", "content": "follow up"}],
                "prompt": {"id": "pmpt_account_scoped"},
            }
        ),
    )

    assert payload is None


def test_build_complete_replay_payload_sanitizes_legacy_snapshot_bookkeeping() -> None:
    payload = build_complete_replay_payload(
        [
            _turn(
                1,
                parent_response_id=None,
                response_id="resp_1",
                request_input=[
                    {
                        "id": "msg_user",
                        "type": "message",
                        "role": "user",
                        "content": "run it",
                        "metadata": {"turn_id": "turn_1"},
                        "internal_chat_message_metadata_passthrough": {
                            "create_time": 1.0,
                            "turn_id": "turn_1",
                        },
                    }
                ],
                output=[
                    {
                        "id": "rs_1",
                        "type": "reasoning",
                        "encrypted_content": "provider-owned",
                        "summary": [],
                    },
                    {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "done"}],
                        "metadata": {"turn_id": "turn_1"},
                        "internal_chat_message_metadata_passthrough": {
                            "create_time": 1.0,
                            "turn_id": "turn_1",
                        },
                    },
                ],
            )
        ],
        continuation_request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": "resp_1",
                "input": [{"type": "message", "role": "user", "content": "continue"}],
            }
        ),
    )

    assert payload is not None
    input_items = json.loads(payload)["input"]
    assert [item["role"] for item in input_items] == ["user", "assistant", "user"]
    assert all(item.get("type") != "reasoning" for item in input_items)
    assert all("metadata" not in item for item in input_items)
    assert all(
        item.get("internal_chat_message_metadata_passthrough") in (None, {"turn_id": "turn_1"}) for item in input_items
    )


def test_build_complete_replay_payload_appends_descendants_after_snapshot_root() -> None:
    assistant_message = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [
            {
                "type": "output_text",
                "text": "run complete",
                "annotations": [],
                "logprobs": [],
            }
        ],
    }
    tool_call = {
        "id": "ctc_1",
        "type": "custom_tool_call",
        "status": "completed",
        "name": "exec",
        "input": "{}",
        "call_id": "call_1",
    }
    base = _turn(
        1,
        parent_response_id=None,
        response_id="resp_1",
        request_input=[{"type": "message", "role": "user", "content": "run it"}],
        output=cast(list[dict[str, object]], [assistant_message, tool_call]),
    )
    base = replace(
        base,
        operation=replace(
            base.operation,
            request_text=json.dumps(
                {
                    "type": "response.create",
                    "model": "gpt-test",
                    "input": [
                        {"type": "message", "role": "user", "content": "run it"},
                        assistant_message,
                        tool_call,
                    ],
                }
            ),
        ),
        replay_input_includes_response_output=True,
    )
    child = _turn(
        2,
        parent_response_id="resp_1",
        response_id="resp_2",
        request_input=[
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "run complete"}],
            },
            {key: value for key, value in tool_call.items() if key != "id"},
            {
                "type": "custom_tool_call_output",
                "call_id": "call_1",
                "output": [
                    {"type": "input_text", "text": "result"},
                    {"type": "input_text", "text": ""},
                ],
            },
        ],
        output=cast(
            list[dict[str, object]],
            [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "next"}],
                }
            ],
        ),
    )

    payload = build_complete_replay_payload(
        [base, child],
        continuation_request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": "resp_2",
                "input": [{"type": "message", "role": "user", "content": "continue"}],
            }
        ),
    )

    assert payload is not None
    input_items = json.loads(payload)["input"]
    assert sum(item.get("call_id") == "call_1" for item in input_items) == 2
    assert {item.get("type") for item in input_items} >= {"custom_tool_call", "custom_tool_call_output"}
    assert all(
        not (isinstance(part, dict) and part.get("type") == "input_text" and part.get("text") == "")
        for item in input_items
        if isinstance(item, dict) and isinstance(item.get("output"), list)
        for part in item["output"]
    )


def test_build_complete_replay_payload_deduplicates_echoed_tool_output_delta() -> None:
    call: dict[str, object] = {
        "id": "fc_1",
        "type": "function_call",
        "status": "completed",
        "name": "exec",
        "arguments": "{}",
        "call_id": "call_1",
    }
    payload = build_complete_replay_payload(
        [
            _turn(
                1,
                parent_response_id=None,
                response_id="resp_1",
                request_input=[{"type": "message", "role": "user", "content": "run it"}],
                output=[call],
            ),
        ],
        continuation_request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": "resp_1",
                "input": [
                    {"type": "reasoning", "summary": []},
                    call,
                    {"type": "function_call_output", "call_id": "call_1", "output": "done"},
                ],
            }
        ),
    )

    assert payload is not None
    input_items = json.loads(payload)["input"]
    assert [item["type"] for item in input_items] == ["message", "function_call", "function_call_output"]
    assert [item.get("call_id") for item in input_items] == [None, "call_1", "call_1"]


def test_build_complete_replay_payload_strips_echoed_output_after_fresh_prefix() -> None:
    call: dict[str, object] = {
        "id": "fc_prefix_1",
        "type": "function_call",
        "status": "completed",
        "name": "exec",
        "arguments": "{}",
        "call_id": "call_prefix_1",
    }
    payload = build_complete_replay_payload(
        [
            _turn(
                1,
                parent_response_id=None,
                response_id="resp_prefix_1",
                request_input=[{"type": "message", "role": "user", "content": "run it"}],
                output=[call],
            ),
            _turn(
                2,
                parent_response_id="resp_prefix_1",
                response_id="resp_prefix_2",
                request_input=[
                    {"type": "message", "role": "developer", "content": "continue"},
                    {key: value for key, value in call.items() if key != "id"},
                    {"type": "function_call_output", "call_id": "call_prefix_1", "output": "done"},
                ],
                output=[{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]}],
            ),
        ],
        continuation_request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": "resp_prefix_2",
                "input": [{"type": "message", "role": "user", "content": "next"}],
            }
        ),
    )

    assert payload is not None
    input_items = json.loads(payload)["input"]
    assert [item.get("call_id") for item in input_items].count("call_prefix_1") == 2
    assert [item.get("type") for item in input_items][-3:] == ["function_call_output", "message", "message"]


def test_build_complete_replay_payload_drops_exact_duplicate_tool_echoes() -> None:
    call: dict[str, object] = {
        "type": "function_call",
        "name": "exec",
        "arguments": "{}",
        "call_id": "call_duplicate_echo",
    }
    output: dict[str, object] = {
        "type": "function_call_output",
        "call_id": "call_duplicate_echo",
        "output": "done",
    }
    operation = _turn(
        1,
        parent_response_id=None,
        response_id="resp_duplicate_echo",
        request_input=[{"type": "message", "role": "user", "content": "run it"}],
        output=[call],
    )
    continuation = {
        "type": "response.create",
        "model": "gpt-test",
        "previous_response_id": "resp_duplicate_echo",
        "input": [call, output, call, output],
    }

    payload = build_complete_replay_payload([operation], continuation_request_text=json.dumps(continuation))

    assert payload is not None
    input_items = json.loads(payload)["input"]
    assert [item.get("type") for item in input_items] == [
        "message",
        "function_call",
        "function_call_output",
    ]


def test_build_complete_replay_payload_deduplicates_echoed_tool_output_from_snapshot() -> None:
    call: dict[str, object] = {
        "id": "fc_1",
        "type": "function_call",
        "status": "completed",
        "name": "exec",
        "arguments": "{}",
        "call_id": "call_1",
    }
    operation = DurableBridgeOperationSnapshot(
        operation_id="op_snapshot_tool",
        session_id="session",
        request_fingerprint="fingerprint",
        account_id="account",
        model="gpt-test",
        parent_response_id=None,
        state="completed",
        response_id="resp_snapshot_tool",
        request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "input": [
                    {"type": "message", "role": "user", "content": "run it"},
                    {key: value for key, value in call.items() if key != "id"},
                ],
            }
        ),
        response_replay_input_complete=True,
    )
    turn = DurableBridgeTranscriptTurn(
        operation=operation,
        events=(),
        response_output_items_json=json.dumps([call]),
        replay_input_includes_response_output=True,
    )

    payload = build_complete_replay_payload(
        [turn],
        continuation_request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": "resp_snapshot_tool",
                "input": [
                    {"type": "reasoning", "summary": []},
                    call,
                    {"type": "function_call_output", "call_id": "call_1", "output": "done"},
                ],
            }
        ),
    )

    assert payload is not None
    input_items = json.loads(payload)["input"]
    assert [item["type"] for item in input_items] == ["message", "function_call", "function_call_output"]


def test_build_complete_replay_payload_snapshot_accepts_tool_output_only_delta() -> None:
    call: dict[str, object] = {
        "id": "fc_1",
        "type": "function_call",
        "status": "completed",
        "name": "exec",
        "arguments": "{}",
        "call_id": "call_1",
    }
    operation = DurableBridgeOperationSnapshot(
        operation_id="op_snapshot_tool_delta",
        session_id="session",
        request_fingerprint="fingerprint",
        account_id="account",
        model="gpt-test",
        parent_response_id=None,
        state="completed",
        response_id="resp_snapshot_tool_delta",
        request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "input": [
                    {"type": "message", "role": "user", "content": "run it"},
                    {key: value for key, value in call.items() if key != "id"},
                ],
            }
        ),
        response_replay_input_complete=True,
    )
    turn = DurableBridgeTranscriptTurn(
        operation=operation,
        events=(),
        response_output_items_json=json.dumps([call]),
        replay_input_includes_response_output=True,
    )

    payload = build_complete_replay_payload(
        [turn],
        continuation_request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": "resp_snapshot_tool_delta",
                "input": [{"type": "function_call_output", "call_id": "call_1", "output": "done"}],
            }
        ),
    )

    assert payload is not None
    input_items = json.loads(payload)["input"]
    assert [item["type"] for item in input_items] == ["message", "function_call", "function_call_output"]


def test_build_replay_input_snapshot_is_self_contained_and_strips_ids() -> None:
    snapshot = build_replay_input_snapshot(
        [],
        request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": "stale",
                "input": [{"type": "message", "role": "user", "content": "hello", "id": "in_1"}],
            }
        ),
        response_output_items_json=json.dumps(
            [
                {
                    "type": "message",
                    "id": "msg_1",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "world"}],
                },
                {"type": "reasoning", "id": "rs_1", "summary": []},
            ]
        ),
    )

    assert snapshot is not None
    items = json.loads(snapshot)
    assert [item["role"] for item in items] == ["user", "assistant"]
    assert all("id" not in item for item in items)


def test_build_replay_input_snapshot_rejects_bounds() -> None:
    assert (
        build_replay_input_snapshot(
            [],
            request_text=json.dumps(
                {"type": "response.create", "input": [{"type": "message", "role": "user", "content": "hello"}]}
            ),
            response_output_items_json=json.dumps(
                [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]}]
            ),
            max_input_items=1,
        )
        is None
    )


@pytest.mark.asyncio
async def test_complete_transcript_prefers_snapshot_when_parent_chain_is_missing() -> None:
    row = SimpleNamespace(
        operation_id="op_snapshot",
        session_id="session",
        request_fingerprint="fingerprint",
        account_id="account",
        model="gpt-test",
        parent_response_id="purged-parent",
        state="completed",
        response_id="resp_snapshot",
        recovery_dispatch_count=0,
        request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": "purged-parent",
                "input": [{"type": "message", "role": "user", "content": "old delta"}],
            }
        ),
        event_spool_complete=True,
        transcript_version=1,
        response_output_items_json="[]",
        response_output_items_complete=True,
        response_replay_input_json=json.dumps(
            [
                {"type": "message", "role": "user", "content": "first"},
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "answer"}],
                },
            ]
        ),
        response_replay_input_complete=True,
        response_replay_input_turn_count=3,
    )

    class _Session:
        async def scalar(self, statement: object) -> object:
            return row

    turns = await DurableBridgeRepository(cast(AsyncSession, _Session())).get_complete_transcript(
        response_id="resp_snapshot"
    )

    assert turns is not None
    assert len(turns) == 1
    assert turns[0].replay_input_includes_response_output is True
    assert turns[0].represented_turn_count == 3
    assert (
        await DurableBridgeRepository(cast(AsyncSession, _Session())).get_complete_transcript(
            response_id="resp_snapshot",
            max_turns=2,
        )
        is None
    )
    row.request_text = json.dumps(
        {
            "type": "response.create",
            "model": "gpt-test",
            "previous_response_id": "purged-parent",
            "instructions": "x" * 600,
            "input": [{"type": "message", "role": "user", "content": "old delta"}],
        }
    )
    # The retained snapshot itself is small, but the synthetic request also
    # carries the original payload fields and must stay within max_bytes.
    assert (
        await DurableBridgeRepository(cast(AsyncSession, _Session())).get_complete_transcript(
            response_id="resp_snapshot",
            max_bytes=512,
        )
        is None
    )
    replay = build_complete_replay_payload(
        turns,
        continuation_request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": "resp_snapshot",
                "input": [{"type": "message", "role": "user", "content": "continue"}],
            }
        ),
    )
    assert replay is not None
    assert [item["role"] for item in json.loads(replay)["input"]] == ["user", "assistant", "user"]

    # Rows created before the turn-count migration carry the zero sentinel;
    # their historical depth is unknown and must not be replayed.
    row.response_replay_input_turn_count = 0
    assert (
        await DurableBridgeRepository(cast(AsyncSession, _Session())).get_complete_transcript(
            response_id="resp_snapshot",
        )
        is None
    )


@pytest.mark.parametrize(
    ("persisted_turn_count", "max_turns", "expected_turn_count"),
    [(0, 1, None), (1, 1, 1), (2, 1, None)],
)
@pytest.mark.asyncio
async def test_complete_transcript_bounds_unsnapshotted_root_by_persisted_depth(
    persisted_turn_count: int,
    max_turns: int,
    expected_turn_count: int | None,
) -> None:
    row = SimpleNamespace(
        operation_id="op_root",
        session_id="session",
        request_fingerprint="fingerprint",
        account_id="account",
        model="gpt-test",
        parent_response_id=None,
        state="completed",
        response_id="resp_root",
        recovery_dispatch_count=0,
        request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "input": [
                    {"type": "message", "role": "user", "content": "first"},
                    {"type": "message", "role": "assistant", "content": "answer"},
                    {"type": "message", "role": "user", "content": "second"},
                ],
            }
        ),
        event_spool_complete=False,
        transcript_version=1,
        response_output_items_json=json.dumps(
            [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]}]
        ),
        response_output_items_complete=True,
        response_replay_input_json=None,
        response_replay_input_complete=False,
        response_replay_input_turn_count=persisted_turn_count,
    )

    class _Session:
        async def scalar(self, statement: object) -> object:
            return row

    repository = DurableBridgeRepository(cast(AsyncSession, _Session()))
    turns = await repository.get_complete_transcript(response_id="resp_root", max_turns=max_turns)
    if expected_turn_count is None:
        assert turns is None
    else:
        assert turns is not None
        assert len(turns) == 1
        assert turns[0].represented_turn_count == expected_turn_count


@pytest.mark.parametrize(
    ("request_text", "expected_turn_count"),
    [
        ('{"input":"first"}', 1),
        ('{"input":[{"type":"message","role":"user","content":"first"}]}', 1),
        (
            '{"client_metadata":{"codex_lb_operation_id":"op-root"},'
            '"input":[{"type":"message","role":"user","content":"first"}]}',
            1,
        ),
        (
            '{"input":[{"type":"message","role":"user","content":"first"},'
            '{"type":"message","role":"assistant","content":[{"type":"output_text","text":"answer"}]},'
            '{"type":"message","role":"user","content":"second"}]}',
            2,
        ),
        (
            '{"input":[{"type":"message","role":"user","content":"run it"},'
            '{"type":"function_call","call_id":"call_1","name":"lookup","arguments":"{}"},'
            '{"type":"function_call_output","call_id":"call_1","output":"done"},'
            '{"type":"function_call","call_id":"call_2","name":"lookup","arguments":"{}"},'
            '{"type":"function_call_output","call_id":"call_2","output":"done"}]}',
            3,
        ),
        ('{"input":[{"type":"message"}]}', None),
    ],
)
def test_derive_replay_input_turn_count_validates_root_history(
    request_text: str,
    expected_turn_count: int | None,
) -> None:
    assert derive_replay_input_turn_count(request_text) == expected_turn_count


def test_build_complete_replay_payload_rejects_broken_parent_continuation() -> None:
    turns = [
        _turn(
            1,
            parent_response_id=None,
            response_id="resp_1",
            request_input=[{"type": "message", "role": "user", "content": "first"}],
            output=[
                {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "answer"}],
                }
            ],
        ),
    ]

    assert (
        build_complete_replay_payload(
            turns,
            continuation_request_text=json.dumps(
                {
                    "type": "response.create",
                    "model": "gpt-test",
                    "previous_response_id": "not_resp_1",
                    "input": [{"type": "message", "role": "user", "content": "follow up"}],
                }
            ),
        )
        is None
    )


def test_materialize_output_items_prefers_output_item_done_over_empty_completed_output() -> None:
    events = [
        (
            "event: response.output_item.done\ndata: "
            '{"type":"response.output_item.done","output_index":1,"item":'
            '{"id":"msg_1","type":"message","role":"assistant","status":"completed",'
            '"content":[{"type":"output_text","text":"answer"}]}}\n\n'
        ),
        (
            "event: response.output_item.done\ndata: "
            '{"type":"response.output_item.done","output_index":0,"item":'
            '{"id":"rs_1","type":"reasoning","summary":[]}}\n\n'
        ),
        (
            "event: response.completed\ndata: "
            '{"type":"response.completed","response":{"id":"resp_1","status":"completed",'
            '"output":[]}}\n\n'
        ),
    ]

    output = materialize_output_items_from_events(events)

    assert output is not None
    output_items = cast(list[dict[str, Any]], output)
    assert [item["type"] for item in output_items] == ["reasoning", "message"]


def test_materialize_output_items_rejects_conflicting_terminal_output() -> None:
    events = [
        (
            "event: response.output_item.done\ndata: "
            '{"type":"response.output_item.done","output_index":0,"item":'
            '{"type":"message","role":"assistant","content":[{"type":"output_text","text":"spooled"}]}}\n\n'
        ),
        (
            "event: response.completed\ndata: "
            '{"type":"response.completed","response":{"output":['
            '{"type":"message","role":"assistant","content":[{"type":"output_text","text":"terminal"}]}'
            "]}}\n\n"
        ),
    ]

    assert materialize_output_items_from_events(events) is None


def test_materialize_output_items_requires_terminal_completion() -> None:
    events = [
        (
            "event: response.output_item.done\ndata: "
            '{"type":"response.output_item.done","output_index":0,"item":{"type":"message"}}\n\n'
        ),
    ]

    assert materialize_output_items_from_events(events) is None


def test_materialize_output_items_rejects_sparse_indexes() -> None:
    events = [
        (
            "event: response.output_item.done\ndata: "
            '{"type":"response.output_item.done","output_index":0,"item":{"type":"message"}}\n\n'
        ),
        (
            "event: response.output_item.done\ndata: "
            '{"type":"response.output_item.done","output_index":2,"item":{"type":"message"}}\n\n'
        ),
        ('event: response.completed\ndata: {"type":"response.completed","response":{"output":[]}}\n\n'),
    ]

    assert materialize_output_items_from_events(events) is None


def test_materialize_output_items_rejects_boolean_indexes() -> None:
    events = [
        (
            "event: response.output_item.done\ndata: "
            '{"type":"response.output_item.done","output_index":false,"item":{"type":"message"}}\n\n'
        ),
        ('event: response.completed\ndata: {"type":"response.completed","response":{"output":[]}}\n\n'),
    ]

    assert materialize_output_items_from_events(events) is None


def test_materialize_output_items_rejects_duplicate_added_indexes() -> None:
    item = '{"type":"message"}'
    events = [
        (
            f"event: response.output_item.added\ndata: "
            f'{{"type":"response.output_item.added","output_index":0,"item":{item}}}\n\n'
        ),
        (
            f"event: response.output_item.added\ndata: "
            f'{{"type":"response.output_item.added","output_index":0,"item":{item}}}\n\n'
        ),
        (
            "event: response.output_item.done\ndata: "
            '{"type":"response.output_item.done","output_index":0,"item":{"type":"message"}}\n\n'
        ),
        'event: response.completed\ndata: {"type":"response.completed","response":{"output":[]}}\n\n',
    ]

    assert materialize_output_items_from_events(events) is None


def test_materialize_output_items_rejects_duplicate_done_indexes() -> None:
    done = (
        "event: response.output_item.done\ndata: "
        '{"type":"response.output_item.done","output_index":0,"item":{"type":"message"}}\n\n'
    )
    events = [
        done,
        done,
        'event: response.completed\ndata: {"type":"response.completed","response":{"output":[]}}\n\n',
    ]

    assert materialize_output_items_from_events(events) is None
