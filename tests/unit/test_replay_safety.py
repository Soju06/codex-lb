from __future__ import annotations

import ast
import copy
import inspect
import json
import pathlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import pytest

from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.modules.proxy import replay_safety as replay_safety_module
from app.modules.proxy.continuity import (
    is_http_bridge_account_neutral_replay,
    make_http_bridge_account_neutral_replay_key,
)
from app.modules.proxy.replay_safety import (
    RELOCATION_TRANSCRIPT_MAX_BYTES,
    RELOCATION_TRANSCRIPT_MAX_ITEMS,
    RELOCATION_TRANSCRIPT_MAX_TURNS,
    RelocatedReplayBody,
    _relocation_comparison_key,
    _tail_overlap_length,
    _terminal_response_output_items,
    _transcript_turn_input_items,
    project_durable_transcript_for_account_neutral_fresh_replay,
    project_responses_input_for_account_neutral_fresh_replay,
    responses_input_suffix_matches_pending_tool_calls,
    responses_input_suffix_retains_prior_output,
    responses_payload_is_account_neutral_fresh_replay,
    responses_request_frame_payload,
)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "input": [{"role": "user", "content": "hello"}],
            "tools": [{"type": "function", "name": "lookup"}],
        },
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
            ]
        },
        {
            "input": [],
            "tools": [
                {
                    "type": "function",
                    "name": "inspect_file",
                    "parameters": {
                        "type": "object",
                        "properties": {"file_id": {"type": "string"}},
                    },
                }
            ],
        },
        {
            "input": [
                {
                    "type": "custom_tool_call",
                    "call_id": "call_2",
                    "name": "shell",
                    "input": "pwd",
                    "caller": {"type": "direct"},
                },
                {"type": "custom_tool_call_output", "call_id": "call_2", "output": "/tmp"},
            ]
        },
        {
            "input": [
                {"type": "apply_patch_call", "call_id": "call_3", "patch": "*** Begin Patch\n*** End Patch"},
                {"type": "apply_patch_call_output", "call_id": "call_3", "output": "done"},
            ]
        },
        {
            "input": [
                {"type": "apply_patch_call", "call_id": "call_legacy_input", "input": "*** Begin Patch"},
                {
                    "type": "apply_patch_call_output",
                    "call_id": "call_legacy_input",
                    "output": "done",
                },
            ]
        },
        {
            "input": [
                {
                    "type": "apply_patch_call",
                    "call_id": "call_create",
                    "operation": {"type": "create_file", "path": "new.txt", "diff": "+new\n"},
                },
                {
                    "type": "apply_patch_call",
                    "call_id": "call_delete",
                    "operation": {"type": "delete_file", "path": "old.txt"},
                },
                {
                    "type": "apply_patch_call",
                    "call_id": "call_update",
                    "operation": {"type": "update_file", "path": "app.py", "diff": "@@\n-old\n+new\n"},
                },
                {"type": "apply_patch_call_output", "call_id": "call_create", "output": "created"},
                {"type": "apply_patch_call_output", "call_id": "call_delete", "output": "deleted"},
                {"type": "apply_patch_call_output", "call_id": "call_update", "output": "updated"},
            ]
        },
        {"input": [{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]},
        {"input": [{"type": "input_file", "file_url": "https://example.com/context.txt"}]},
        {"input": [], "reasoning": {"effort": "high", "summary": "auto"}},
        {
            "input": [],
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "answer",
                    "schema": {"type": "object"},
                    "strict": True,
                },
            },
        },
        {
            "input": [],
            "client_metadata": {
                "x-codex-window-id": "child:0",
                "ws_request_header_x_openai_internal_codex_responses_lite": "true",
            },
        },
        {
            "input": [],
            "tools": [{"type": "function", "name": "lookup"}, {"type": "web_search"}],
            "tool_choice": {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "lookup"}, {"type": "web_search"}],
            },
        },
        {
            "input": [],
            "tools": [
                {
                    "type": "web_search",
                    "filters": {"allowed_domains": ["openai.com"]},
                    "search_context_size": "high",
                    "user_location": {
                        "type": "approximate",
                        "city": "San Francisco",
                        "country": "US",
                    },
                }
            ],
        },
    ],
)
def test_account_neutral_fresh_replay_accepts_self_contained_payloads(
    payload: dict[str, JsonValue],
) -> None:
    assert responses_payload_is_account_neutral_fresh_replay(payload) is True


def _corpus_assistant_message_contents() -> list[list[JsonValue]]:
    """Every assistant message body the recorded request corpus carries, as recorded."""

    corpus = json.loads(
        (pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "passthrough_request_corpus.json").read_text()
    )
    return [
        item["content"]
        for case in corpus
        for item in case["payload"].get("input", [])
        if isinstance(item, dict) and item.get("role") == "assistant"
    ]


def test_the_recorded_assistant_content_shape_keeps_the_admission_it_has_today() -> None:
    contents = _corpus_assistant_message_contents()

    assert contents, "the corpus no longer records an assistant message"
    for content in contents:
        # Recorded, not authored. Every ``output_text`` part the Responses API
        # produces carries an annotations array, and this shared predicate has
        # always refused the field. The durable rebuild needs those parts, but
        # it owns the items it assembles and removes the field from them --
        # admitting it here instead would change what the already-shipped
        # fresh-replay paths accept from clients, which is not this change's
        # concern.
        assert [cast(Any, part).get("annotations") for part in content] == [[]]
        message: JsonValue = {"type": "message", "role": "assistant", "status": "completed", "content": content}

        assert responses_payload_is_account_neutral_fresh_replay({"input": [message]}) is False


@pytest.mark.parametrize(
    ("annotations", "expected"),
    [
        # Absent entirely: the pre-annotation shape is the one this predicate takes.
        (None, True),
        ([], False),
        ([{"type": "url_citation", "url": "https://example.test/a", "start_index": 0, "end_index": 1}], False),
        ([{"type": "file_citation", "file_id": "file_owner_scoped", "filename": "notes.md", "index": 3}], False),
    ],
)
def test_account_neutral_fresh_replay_admits_no_annotation_list_at_all(
    annotations: list[JsonValue] | None,
    expected: bool,
) -> None:
    part: dict[str, JsonValue] = {"type": "output_text", "text": "old answer"}
    if annotations is not None:
        part["annotations"] = annotations
    payload: dict[str, JsonValue] = {
        "input": [{"type": "message", "role": "assistant", "status": "completed", "content": [part]}]
    }

    assert responses_payload_is_account_neutral_fresh_replay(payload) is expected


@pytest.mark.parametrize("annotations", [[], [{"type": "url_citation", "url": "https://example.test/a"}]])
def test_full_resend_retained_output_reads_annotations_the_same_way(annotations: list[JsonValue]) -> None:
    # The client-resend path shares the content-part predicate, so anything the
    # rebuild needed must have stayed out of it: an annotated answer is refused
    # on the path that was already live, exactly as it was before.
    input_items: list[JsonValue] = [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "old question"}]},
        {
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "old answer", "annotations": annotations}],
        },
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "next question"}]},
    ]

    assert responses_input_suffix_retains_prior_output(input_items, stored_count=1) is False


def test_account_neutral_replay_projection_removes_response_owned_bookkeeping() -> None:
    metadata = {"turn_id": "turn_owner_a"}
    input_items: list[JsonValue] = [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "first question"}],
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "reasoning",
            "id": "rs_owner_a",
            "encrypted_content": "owner-a-ciphertext",
            "summary": [],
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "custom_tool_call",
            "id": "ctc_owner_a",
            "call_id": "call_boundary",
            "name": "exec",
            "input": "pwd",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "custom_tool_call_output",
            "id": "ctco_owner_a",
            "call_id": "call_boundary",
            "output": "/workspace",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "tool_search_call",
            "id": "tsc_owner_a",
            "call_id": "call_search",
            "arguments": {"query": "github"},
            "execution": "client",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "tool_search_output",
            "call_id": "call_search",
            "execution": "client",
            "status": "completed",
            "tools": [],
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "web_search_call",
            "id": "ws_owner_a",
            "action": {"type": "search", "query": "github"},
            "status": "completed",
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "message",
            "id": "msg_owner_a",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "prior answer"}],
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "next question"}],
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_next"},
        },
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(input_items, stored_count=3)

    assert projection is not None
    assert projection.stored_prefix_count == 2
    assert projection.input_items == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "first question"}],
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "custom_tool_call",
            "call_id": "call_boundary",
            "name": "exec",
            "input": "pwd",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_boundary",
            "output": "/workspace",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "prior answer"}],
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "next question"}],
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_next"},
        },
    ]
    assert (
        responses_input_suffix_retains_prior_output(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
        )
        is True
    )
    assert responses_payload_is_account_neutral_fresh_replay({"input": projection.input_items}) is True


def test_account_neutral_replay_projection_rejects_invalid_stored_boundary() -> None:
    assert (
        project_responses_input_for_account_neutral_fresh_replay(
            [{"role": "user", "content": "hello"}],
            stored_count=2,
        )
        is None
    )


@pytest.mark.parametrize(
    "search_item",
    [
        {
            "type": "web_search_call",
            "id": "ws_in_progress",
            "action": {"type": "search", "query": "pending"},
            "status": "in_progress",
        },
        {
            "type": "tool_search_call",
            "id": "tsc_in_progress",
            "call_id": "call_search",
            "arguments": {"query": "pending"},
            "status": "in_progress",
        },
        {
            "type": "tool_search_output",
            "call_id": "call_search",
            "status": "incomplete",
            "tools": [],
        },
    ],
)
def test_account_neutral_replay_projection_preserves_noncompleted_search_state_to_fail_closed(
    search_item: dict[str, JsonValue],
) -> None:
    input_items: list[JsonValue] = [
        {"role": "user", "content": "old question"},
        {
            "role": "assistant",
            "content": [{"type": "output_text", "text": "old answer"}],
        },
        search_item,
        {"role": "user", "content": "next question"},
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(input_items, stored_count=1)

    assert projection is not None
    assert any(isinstance(item, dict) and item.get("type") == search_item["type"] for item in projection.input_items)
    assert responses_payload_is_account_neutral_fresh_replay({"input": projection.input_items}) is False


@pytest.mark.parametrize(
    "suffix",
    [
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "prior answer"}],
            },
            {"role": "user", "content": "next question"},
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "lookup",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "call_1", "output": "result"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "tool result summarized"}],
            },
            {"role": "user", "content": "next question"},
        ],
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "first retained answer"}],
            },
            {"role": "user", "content": "intermediate question"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "second retained answer"}],
            },
            {"role": "user", "content": "current question"},
        ],
    ],
)
def test_full_resend_suffix_requires_prior_output_before_new_input(
    suffix: list[JsonValue],
) -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]

    assert (
        responses_input_suffix_retains_prior_output(
            [*stored_input, *suffix],
            stored_count=len(stored_input),
        )
        is True
    )


@pytest.mark.parametrize(
    "suffix",
    [
        [{"role": "user", "content": "next question"}],
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "prior answer"}],
            }
        ],
        [
            {"role": "user", "content": "next question"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "late output"}],
            },
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_unresolved",
                "name": "lookup",
                "arguments": "{}",
            },
            {"role": "user", "content": "skip the tool"},
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_late",
                "name": "lookup",
                "arguments": "{}",
            },
            {"role": "user", "content": "answer before the tool result"},
            {"type": "function_call_output", "call_id": "call_late", "output": "late"},
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_new",
                "name": "lookup",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "call_old", "output": "stale"},
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "lookup",
                "arguments": "{}",
            },
            {
                "type": "function_call",
                "call_id": "call_2",
                "name": "lookup",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "call_1", "output": "partial"},
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "lookup",
                "arguments": "{}",
            },
            {
                "type": "function_call",
                "call_id": "call_2",
                "name": "lookup",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "call_2", "output": "second"},
            {"type": "function_call_output", "call_id": "call_1", "output": "first"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "tool results summarized"}],
            },
            {"role": "user", "content": "next question"},
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_only",
                "name": "lookup",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "call_only", "output": "result"},
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_duplicate",
                "name": "lookup",
                "arguments": "{}",
            },
            {
                "type": "function_call",
                "call_id": "call_duplicate",
                "name": "lookup_again",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "call_duplicate", "output": "ambiguous"},
        ],
        [
            {
                "type": "message",
                "role": "assistant",
                "status": "in_progress",
                "content": [{"type": "output_text", "text": "partial answer"}],
            },
            {"role": "user", "content": "continue"},
        ],
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "answer"},
                    {"type": "input_text", "text": "role-invalid"},
                ],
            },
            {"role": "user", "content": "continue"},
        ],
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "answer"}],
            },
            {
                "role": "user",
                "content": [{"type": "output_text", "text": "role-invalid"}],
            },
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_partial",
                "name": "lookup",
                "arguments": "{}",
                "status": "in_progress",
            },
            {"type": "function_call_output", "call_id": "call_partial", "output": "result"},
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_partial_output",
                "name": "lookup",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call_partial_output",
                "output": "partial",
                "status": "in_progress",
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "not settled"}],
            },
            {"role": "user", "content": "continue"},
        ],
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": ""}],
            },
            {"role": "user", "content": "continue"},
        ],
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "refusal", "refusal": "   "}],
            },
            {"role": "user", "content": "continue"},
        ],
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "prior answer"}],
            },
            {"role": "user", "content": ""},
        ],
    ],
)
def test_full_resend_suffix_rejects_missing_or_misordered_context(
    suffix: list[JsonValue],
) -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]

    assert (
        responses_input_suffix_retains_prior_output(
            [*stored_input, *suffix],
            stored_count=len(stored_input),
        )
        is False
    )


@pytest.mark.parametrize(
    ("suffix", "pending_tool_calls", "expected"),
    [
        pytest.param(
            [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {"type": "function_call_output", "call_id": "call_1", "output": "result"},
            ],
            {"call_1": "function_call"},
            True,
            id="complete-tool-loop",
        ),
        pytest.param(
            [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {
                    "type": "custom_tool_call",
                    "call_id": "call_2",
                    "name": "shell",
                    "input": "pwd",
                },
                {"type": "function_call_output", "call_id": "call_1", "output": "result"},
                {"type": "custom_tool_call_output", "call_id": "call_2", "output": "/workspace"},
            ],
            {"call_1": "function_call", "call_2": "custom_tool_call"},
            True,
            id="complete-parallel-tool-loop",
        ),
        pytest.param(
            [{"role": "user", "content": "revise that answer"}],
            {"call_1": "function_call"},
            False,
            id="missing-output",
        ),
        pytest.param(
            [{"type": [], "call_id": "call_1"}],
            {"call_1": "function_call"},
            False,
            id="malformed-item-type",
        ),
        pytest.param(
            [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                }
            ],
            {"call_1": "function_call"},
            False,
            id="unsettled-call",
        ),
        pytest.param(
            [{"type": "function_call_output", "call_id": "call_1", "output": "orphan"}],
            {"call_1": "function_call"},
            False,
            id="orphan-output",
        ),
        pytest.param(
            [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {"type": "function_call_output", "call_id": "call_1", "output": "partial"},
            ],
            {"call_1": "function_call", "call_2": "function_call"},
            False,
            id="omitted-parallel-call",
        ),
        pytest.param(
            [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {"role": "developer", "content": "new control message"},
                {"type": "function_call_output", "call_id": "call_1", "output": "result"},
            ],
            {"call_1": "function_call"},
            False,
            id="inline-developer-message-in-fresh-suffix",
        ),
    ],
)
def test_full_resend_suffix_accepts_only_self_contained_tool_loops(
    suffix: list[JsonValue],
    pending_tool_calls: dict[str, str],
    expected: bool,
) -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
    )

    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls=pending_tool_calls,
        )
        is expected
    )


def test_full_resend_tool_loop_manifest_tolerates_fresh_developer_interleave_after_historical_one() -> None:
    stored_input: list[JsonValue] = [
        {"role": "user", "content": "first question"},
        {
            "type": "custom_tool_call",
            "id": "ctc_old",
            "call_id": "call_old",
            "name": "shell",
            "input": "pwd",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_old"},
        },
        {
            "type": "message",
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_old"},
            "content": [{"type": "input_text", "text": "historical control message"}],
        },
        {
            "type": "custom_tool_call_output",
            "id": "ctco_old",
            "call_id": "call_old",
            "output": "/workspace",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_old"},
        },
    ]
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "id": "ctc_current",
            "call_id": "call_current",
            "name": "shell",
            "input": "pwd",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
        },
        {
            "type": "message",
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
            "content": [{"type": "input_text", "text": "new control message"}],
        },
        {
            "type": "custom_tool_call_output",
            "id": "ctco_current",
            "call_id": "call_current",
            "output": "/workspace",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
        },
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )
    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls={"call_current": "custom_tool_call"},
        )
        is True
    )


def test_full_resend_tool_loop_manifest_rejects_response_owned_historical_developer() -> None:
    stored_input: list[JsonValue] = [
        {"role": "user", "content": "first question"},
        {
            "type": "custom_tool_call",
            "id": "ctc_old",
            "call_id": "call_old",
            "name": "shell",
            "input": "pwd",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_old"},
        },
        {
            "type": "message",
            "id": "msg_old_control",
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_old"},
            "content": [{"type": "input_text", "text": "historical control message"}],
        },
        {
            "type": "custom_tool_call_output",
            "id": "ctco_old",
            "call_id": "call_old",
            "output": "/workspace",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_old"},
        },
    ]
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "id": "ctc_current",
            "call_id": "call_current",
            "name": "shell",
            "input": "pwd",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
        },
        {
            "type": "custom_tool_call_output",
            "id": "ctco_current",
            "call_id": "call_current",
            "output": "/workspace",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
        },
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )
    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls={"call_current": "custom_tool_call"},
        )
        is False
    )


def test_full_resend_tool_loop_manifest_rejects_response_owned_fresh_developer() -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "call_id": "call_current",
            "name": "shell",
            "input": "pwd",
        },
        {
            "type": "message",
            "id": "msg_response_owned",
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
            "content": [{"type": "input_text", "text": "new control message"}],
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_current",
            "output": "/workspace",
        },
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert not responses_input_suffix_matches_pending_tool_calls(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        pending_tool_calls={"call_current": "custom_tool_call"},
    )


def test_full_resend_tool_loop_manifest_rejects_fresh_developer_inside_function_call_pair() -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    suffix: list[JsonValue] = [
        {
            "type": "function_call",
            "call_id": "call_current",
            "name": "lookup",
            "arguments": "{}",
        },
        {
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
            "content": [{"type": "input_text", "text": "unobserved function control"}],
        },
        {
            "type": "function_call_output",
            "call_id": "call_current",
            "output": "ok",
        },
    ]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
    )

    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls={"call_current": "function_call"},
        )
        is False
    )


def test_full_resend_tool_loop_manifest_rejects_adjacent_pair_nested_in_parallel_batch() -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    suffix: list[JsonValue] = [
        {"type": "custom_tool_call", "call_id": "call_1", "name": "shell", "input": "pwd"},
        {"type": "custom_tool_call", "call_id": "call_2", "name": "shell", "input": "whoami"},
        {
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
            "content": [{"type": "input_text", "text": "control inside one matching parallel pair"}],
        },
        {"type": "custom_tool_call_output", "call_id": "call_2", "output": "worker"},
        {"type": "custom_tool_call_output", "call_id": "call_1", "output": "/workspace"},
    ]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
    )

    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls={"call_1": "custom_tool_call", "call_2": "custom_tool_call"},
        )
        is False
    )


@pytest.mark.parametrize(
    "suffix",
    [
        pytest.param(
            [
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control before call"}],
                },
                {"type": "custom_tool_call", "call_id": "call_current", "name": "shell", "input": "pwd"},
                {"type": "custom_tool_call_output", "call_id": "call_current", "output": "/workspace"},
            ],
            id="developer-before-call",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_current", "name": "shell", "input": "pwd"},
                {"type": "custom_tool_call_output", "call_id": "call_current", "output": "/workspace"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control after output"}],
                },
            ],
            id="developer-after-output",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_current", "name": "shell", "input": "pwd"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "first control"}],
                },
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "second control"}],
                },
                {"type": "custom_tool_call_output", "call_id": "call_current", "output": "/workspace"},
            ],
            id="multiple-developer-messages",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_current", "name": "shell", "input": "pwd"},
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "control without turn id"}],
                },
                {"type": "custom_tool_call_output", "call_id": "call_current", "output": "/workspace"},
            ],
            id="developer-without-turn-id",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_current", "name": "shell", "input": "pwd"},
                {
                    "type": None,
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control with null type"}],
                },
                {"type": "custom_tool_call_output", "call_id": "call_current", "output": "/workspace"},
            ],
            id="developer-with-null-type",
        ),
        pytest.param(
            [
                {
                    "type": "message",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "working"}],
                },
                {"type": "custom_tool_call", "call_id": "call_current", "name": "shell", "input": "pwd"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control"}],
                },
                {"type": "custom_tool_call_output", "call_id": "call_current", "output": "/workspace"},
            ],
            id="developer-with-leading-assistant-exception",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_current", "name": "shell", "input": "pwd"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control"}],
                },
                {"type": "custom_tool_call_output", "call_id": "call_current", "output": "/workspace"},
                {"role": "user", "content": "follow up"},
            ],
            id="developer-with-trailing-user-exception",
        ),
    ],
)
def test_full_resend_tool_loop_manifest_rejects_unproven_fresh_developer_positions(
    suffix: list[JsonValue],
) -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
    )

    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls={"call_current": "custom_tool_call"},
        )
        is False
    )


@pytest.mark.parametrize(
    ("interleaved_item", "expected"),
    [
        pytest.param(
            {"role": "developer", "content": "historical control"},
            True,
            id="implicit-developer-message",
        ),
        pytest.param(
            {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "historical control"}],
            },
            True,
            id="explicit-developer-message",
        ),
        pytest.param(
            {"type": None, "role": "developer", "content": "historical control"},
            False,
            id="null-type-developer-message",
        ),
        pytest.param(
            {
                "type": "bogus",
                "role": "developer",
                "id": "msg_owned",
                "content": "historical control",
            },
            False,
            id="malformed-type-response-owned-developer-message",
        ),
        pytest.param(
            {
                "type": "reasoning",
                "role": "developer",
                "content": "projection-omitted control",
            },
            False,
            id="projection-omitted-developer-message",
        ),
        pytest.param(
            {
                "role": "developer",
                "status": "completed",
                "internal_chat_message_metadata_passthrough": {"turn_id": "turn_historical"},
                "content": "historical control",
            },
            True,
            id="completed-developer-message-with-neutral-metadata",
        ),
        pytest.param(
            {"role": "developer", "id": "msg_owned", "content": "historical control"},
            False,
            id="response-owned-developer-message",
        ),
        pytest.param(
            {"role": "developer", "id": "", "content": "historical control"},
            False,
            id="empty-response-owned-developer-message",
        ),
        pytest.param(
            {"role": "developer", "content": "   "},
            False,
            id="malformed-developer-message",
        ),
        pytest.param(
            {"role": "developer", "phase": "commentary", "content": "historical control"},
            False,
            id="phased-developer-message",
        ),
        pytest.param(
            {"role": "developer", "status": "failed", "content": "historical control"},
            False,
            id="failed-developer-message",
        ),
        pytest.param(
            {"role": "developer", "content": "historical control", "account_id": "account-scoped"},
            False,
            id="unknown-developer-field",
        ),
        pytest.param(
            {
                "role": "developer",
                "content": "historical control",
                "internal_chat_message_metadata_passthrough": {"turn_id": ""},
            },
            False,
            id="invalid-developer-metadata",
        ),
        pytest.param(
            {"role": "user", "content": "new user input"},
            False,
            id="user-message",
        ),
        pytest.param(
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "assistant output"}],
            },
            False,
            id="assistant-message",
        ),
        pytest.param(
            {"role": "system", "content": "system control"},
            False,
            id="system-message",
        ),
    ],
)
def test_full_resend_exact_manifest_only_allows_historical_developer_interleaving(
    interleaved_item: JsonValue,
    expected: bool,
) -> None:
    stored_input: list[JsonValue] = [
        {"role": "user", "content": "first question"},
        {
            "type": "custom_tool_call",
            "call_id": "call_old",
            "name": "shell",
            "input": "pwd",
        },
        interleaved_item,
        {
            "type": "custom_tool_call_output",
            "call_id": "call_old",
            "output": "/workspace",
        },
    ]
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "call_id": "call_current",
            "name": "shell",
            "input": "git status --short",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_current",
            "output": "",
        },
    ]

    input_items = [*stored_input, *suffix]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        input_items,
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls={"call_current": "custom_tool_call"},
        )
        is expected
    )


@pytest.mark.parametrize(
    "stored_prefix_tail",
    [
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_first", "name": "shell", "input": "pwd"},
                {"type": "custom_tool_call", "call_id": "call_second", "name": "shell", "input": "ls"},
                {"type": "message", "role": "developer", "content": "historical control message"},
                {"type": "custom_tool_call_output", "call_id": "call_first", "output": "/workspace"},
                {"type": "custom_tool_call_output", "call_id": "call_second", "output": "README.md"},
            ],
            id="parallel-batch-around-interleaved-developer",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_first", "name": "shell", "input": "pwd"},
                {"type": "custom_tool_call", "call_id": "call_second", "name": "shell", "input": "ls"},
                {"type": "custom_tool_call_output", "call_id": "call_first", "output": "/workspace"},
                {"type": "message", "role": "developer", "content": "historical control message"},
                {"type": "custom_tool_call_output", "call_id": "call_second", "output": "README.md"},
            ],
            id="developer-inside-window-that-held-parallel-calls",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_first", "name": "shell", "input": "pwd"},
                {"type": "message", "role": "developer", "content": "first historical control message"},
                {"type": "message", "role": "developer", "content": "second historical control message"},
                {"type": "custom_tool_call_output", "call_id": "call_first", "output": "/workspace"},
            ],
            id="duplicate-interleaved-developer-messages",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_first", "name": "shell", "input": "pwd"},
                {"type": "message", "role": "developer", "content": "historical control message"},
                {"type": "custom_tool_call", "call_id": "call_second", "name": "shell", "input": "ls"},
                {"type": "custom_tool_call_output", "call_id": "call_first", "output": "/workspace"},
                {"type": "custom_tool_call_output", "call_id": "call_second", "output": "README.md"},
            ],
            id="parallel-call-opened-after-interleaved-developer",
        ),
    ],
)
def test_full_resend_exact_manifest_rejects_unbounded_historical_developer_interleave(
    stored_prefix_tail: list[JsonValue],
) -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}, *stored_prefix_tail]
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "call_id": "call_current",
            "name": "shell",
            "input": "git status --short",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_current",
            "output": "",
        },
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls={"call_current": "custom_tool_call"},
        )
        is False
    )


@pytest.mark.parametrize(
    "stored_prefix_tail",
    [
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_first", "name": "shell", "input": "pwd"},
                {"type": "message", "role": "developer", "content": "first historical control message"},
                {"type": "custom_tool_call_output", "call_id": "call_first", "output": "/workspace"},
                {"type": "custom_tool_call", "call_id": "call_second", "name": "shell", "input": "ls"},
                {"type": "message", "role": "developer", "content": "second historical control message"},
                {"type": "custom_tool_call_output", "call_id": "call_second", "output": "README.md"},
            ],
            id="sequential-single-call-windows",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_first", "name": "shell", "input": "pwd"},
                {"type": "custom_tool_call", "call_id": "call_second", "name": "shell", "input": "ls"},
                {"type": "custom_tool_call_output", "call_id": "call_first", "output": "/workspace"},
                {"type": "custom_tool_call_output", "call_id": "call_second", "output": "README.md"},
                {"type": "custom_tool_call", "call_id": "call_third", "name": "shell", "input": "git status"},
                {"type": "message", "role": "developer", "content": "historical control message"},
                {"type": "custom_tool_call_output", "call_id": "call_third", "output": ""},
            ],
            id="clean-window-after-drained-parallel-window",
        ),
    ],
)
def test_full_resend_exact_manifest_accepts_bounded_historical_developer_windows(
    stored_prefix_tail: list[JsonValue],
) -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}, *stored_prefix_tail]
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "call_id": "call_current",
            "name": "shell",
            "input": "git status --short",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_current",
            "output": "",
        },
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls={"call_current": "custom_tool_call"},
        )
        is True
    )


def test_full_resend_exact_manifest_accepts_canonical_lite_prefix_developer_without_pending_call() -> None:
    stored_input: list[JsonValue] = [
        {
            "type": "additional_tools",
            "role": "developer",
            "tools": [{"type": "custom", "name": "shell"}],
        },
        {"type": "message", "role": "developer", "content": "stored control"},
        {"role": "user", "content": "first question"},
    ]
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "call_id": "call_current",
            "name": "shell",
            "input": "pwd",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_current",
            "output": "/workspace",
        },
    ]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert projection.canonical_lite_developer_index == 1
    assert responses_input_suffix_matches_pending_tool_calls(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        pending_tool_calls={"call_current": "custom_tool_call"},
        canonical_lite_developer_index=projection.canonical_lite_developer_index,
    )


@pytest.mark.parametrize(
    "stored_input",
    [
        pytest.param(
            [
                {"role": "user", "content": "first question"},
                {"type": "message", "role": "developer", "content": "stored control"},
            ],
            id="nonadjacent-unowned-developer",
        ),
        pytest.param(
            [
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [{"type": "custom", "name": "shell"}],
                },
                {
                    "type": "message",
                    "id": "msg_owned",
                    "role": "developer",
                    "content": "stored control",
                },
                {"role": "user", "content": "first question"},
            ],
            id="response-owned-canonical-position",
        ),
        pytest.param(
            [
                {"role": "user", "content": "first question"},
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [{"type": "custom", "name": "shell"}],
                },
                {"type": "message", "role": "developer", "content": "stored control"},
            ],
            id="bundle-not-at-prefix-start",
        ),
        pytest.param(
            [
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [{"type": "custom", "name": "shell"}],
                },
                {"type": "reasoning", "id": "rs_hidden", "summary": []},
                {"type": "message", "role": "developer", "content": "stored control"},
                {"role": "user", "content": "first question"},
            ],
            id="projection-collapsed-adjacency",
        ),
    ],
)
def test_full_resend_exact_manifest_rejects_unverified_stored_developer_without_pending_call(
    stored_input: list[JsonValue],
) -> None:
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "call_id": "call_current",
            "name": "shell",
            "input": "pwd",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_current",
            "output": "/workspace",
        },
    ]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert not responses_input_suffix_matches_pending_tool_calls(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        pending_tool_calls={"call_current": "custom_tool_call"},
        canonical_lite_developer_index=projection.canonical_lite_developer_index,
    )


@pytest.mark.parametrize(
    "stored_input",
    [
        pytest.param(
            [
                {"role": "user", "content": "first question"},
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [{"type": "custom", "name": "shell"}],
                },
                {"type": "message", "role": "developer", "content": "stored control"},
            ],
            id="bundle-not-at-prefix-start",
        ),
        pytest.param(
            [
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [{"type": "custom", "name": "shell"}],
                },
                {"type": "reasoning", "id": "rs_hidden", "summary": []},
                {"type": "message", "role": "developer", "content": "stored control"},
                {"role": "user", "content": "first question"},
            ],
            id="projection-collapsed-adjacency",
        ),
    ],
)
def test_full_resend_retained_output_rejects_noncanonical_stored_lite_developer(
    stored_input: list[JsonValue],
) -> None:
    suffix: list[JsonValue] = [
        {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "status": "completed",
            "content": [{"type": "output_text", "text": "prior answer"}],
        },
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "next question"}]},
    ]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert projection.canonical_lite_developer_index is None
    assert not responses_input_suffix_retains_prior_output(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        canonical_lite_developer_index=projection.canonical_lite_developer_index,
    )


@pytest.mark.parametrize(
    "historical_output",
    [
        pytest.param(None, id="missing-output"),
        pytest.param(
            {
                "type": "custom_tool_call_output",
                "call_id": "call_other",
                "output": "/workspace",
            },
            id="mismatched-output",
        ),
    ],
)
def test_full_resend_exact_manifest_requires_historical_interleaved_call_output(
    historical_output: JsonValue | None,
) -> None:
    stored_input: list[JsonValue] = [
        {"role": "user", "content": "first question"},
        {
            "type": "custom_tool_call",
            "call_id": "call_old",
            "name": "shell",
            "input": "pwd",
        },
        {"role": "developer", "content": "historical control"},
    ]
    if historical_output is not None:
        stored_input.append(historical_output)
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "call_id": "call_current",
            "name": "shell",
            "input": "git status --short",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_current",
            "output": "",
        },
    ]

    assert not responses_input_suffix_matches_pending_tool_calls(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        pending_tool_calls={"call_current": "custom_tool_call"},
    )


def test_full_resend_retained_output_tolerates_fresh_developer_after_user() -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    suffix: list[JsonValue] = [
        {
            "type": "message",
            "id": "msg_answer",
            "role": "assistant",
            "phase": "final_answer",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_previous"},
            "content": [{"type": "output_text", "text": "prior answer"}],
        },
        {
            "type": "message",
            "id": "msg_user",
            "role": "user",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
            "content": [{"type": "input_text", "text": "next question"}],
        },
        {
            "type": "message",
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
            "content": [{"type": "input_text", "text": "new control message"}],
        },
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )
    assert projection is not None
    assert (
        responses_input_suffix_retains_prior_output(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
        )
        is True
    )


def test_full_resend_retained_output_accepts_canonical_lite_prefix_developer_without_pending_call() -> None:
    stored_input: list[JsonValue] = [
        {
            "type": "additional_tools",
            "role": "developer",
            "tools": [{"type": "custom", "name": "shell"}],
        },
        {"type": "message", "role": "developer", "content": "stored control"},
        {"role": "user", "content": "first question"},
    ]
    suffix: list[JsonValue] = [
        {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "status": "completed",
            "content": [{"type": "output_text", "text": "prior answer"}],
        },
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "next question"}]},
    ]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert projection.canonical_lite_developer_index == 1
    assert responses_input_suffix_retains_prior_output(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        canonical_lite_developer_index=projection.canonical_lite_developer_index,
    )


@pytest.mark.parametrize(
    "stored_input",
    [
        pytest.param(
            [
                {"role": "user", "content": "first question"},
                {"type": "message", "role": "developer", "content": "stored control"},
            ],
            id="nonadjacent-unowned-developer",
        ),
        pytest.param(
            [
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [{"type": "custom", "name": "shell"}],
                },
                {
                    "type": "message",
                    "id": "msg_owned",
                    "role": "developer",
                    "content": "stored control",
                },
                {"role": "user", "content": "first question"},
            ],
            id="response-owned-canonical-position",
        ),
    ],
)
def test_full_resend_retained_output_rejects_unverified_stored_developer_without_pending_call(
    stored_input: list[JsonValue],
) -> None:
    suffix: list[JsonValue] = [
        {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "status": "completed",
            "content": [{"type": "output_text", "text": "prior answer"}],
        },
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "next question"}]},
    ]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert not responses_input_suffix_retains_prior_output(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        canonical_lite_developer_index=projection.canonical_lite_developer_index,
    )


def test_full_resend_retained_output_rejects_response_owned_fresh_developer() -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    suffix: list[JsonValue] = [
        {
            "type": "message",
            "id": "msg_answer",
            "role": "assistant",
            "phase": "final_answer",
            "status": "completed",
            "content": [{"type": "output_text", "text": "prior answer"}],
        },
        {
            "type": "message",
            "id": "msg_user",
            "role": "user",
            "content": [{"type": "input_text", "text": "next question"}],
        },
        {
            "type": "message",
            "id": "msg_response_owned",
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
            "content": [{"type": "input_text", "text": "new control message"}],
        },
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert not responses_input_suffix_retains_prior_output(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
    )


@pytest.mark.parametrize(
    "suffix",
    [
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control without user"}],
                },
            ],
            id="developer-without-user",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"role": "user", "content": "next question"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control"}],
                },
                {"role": "user", "content": "later question"},
            ],
            id="developer-not-terminal",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"role": "user", "content": "next question"},
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "control without turn id"}],
                },
            ],
            id="developer-without-turn-id",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"role": "user", "content": "next question"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {
                        "turn_id": "turn-current",
                        "account_id": "acc-1",
                    },
                    "content": [{"type": "input_text", "text": "account-bound control"}],
                },
            ],
            id="developer-with-account-metadata",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"role": "user", "content": "next question"},
                {
                    "type": 7,
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control with malformed type"}],
                },
            ],
            id="developer-with-nonstring-type",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"role": "user", "content": "next question"},
                {
                    "type": None,
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control with null type"}],
                },
            ],
            id="developer-with-null-type",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"type": "input_text", "text": "raw next input"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control after raw input"}],
                },
            ],
            id="developer-after-raw-input-part",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"type": "input_text", "text": "raw next input"},
                {"role": "user", "content": "next question"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control after raw and user"}],
                },
            ],
            id="developer-after-raw-input-and-user",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"role": "user", "content": "first next question"},
                {"role": "user", "content": "second next question"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control after repeated users"}],
                },
            ],
            id="developer-after-repeated-user-messages",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"role": "user", "content": "next question"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_file", "file_id": "file-account-bound"}],
                },
            ],
            id="developer-with-account-bound-file-id",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "still working"}],
                },
                {"role": "user", "content": "next question"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control after commentary"}],
                },
            ],
            id="developer-after-assistant-commentary",
        ),
    ],
)
def test_full_resend_retained_output_rejects_unproven_fresh_developer_followup(
    suffix: list[JsonValue],
) -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]

    assert (
        responses_input_suffix_retains_prior_output(
            [*stored_input, *suffix],
            stored_count=len(stored_input),
        )
        is False
    )


def test_full_resend_retained_output_rejects_complete_tool_pair_between_output_and_user() -> None:
    stored_prefix: list[JsonValue] = [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "old"}],
        }
    ]
    full_input: list[JsonValue] = [
        *stored_prefix,
        {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "status": "completed",
            "content": [{"type": "output_text", "text": "done"}],
        },
        {
            "type": "custom_tool_call",
            "call_id": "call_extra",
            "name": "shell",
            "input": "pwd",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_extra",
            "output": "ok",
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "new"}],
        },
        {
            "type": "message",
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_new"},
            "content": [{"type": "input_text", "text": "control"}],
        },
    ]
    projected_full_input = project_responses_input_for_account_neutral_fresh_replay(
        full_input,
        stored_count=len(stored_prefix),
    )

    assert projected_full_input is not None
    assert (
        responses_input_suffix_retains_prior_output(
            projected_full_input.input_items,
            stored_count=projected_full_input.stored_prefix_count,
        )
        is False
    )


def test_full_resend_retained_output_rejects_historical_developer_interleaving() -> None:
    stored_input: list[JsonValue] = [
        {"role": "user", "content": "first question"},
        {
            "type": "custom_tool_call",
            "call_id": "call_old",
            "name": "shell",
            "input": "pwd",
        },
        {"role": "developer", "content": "historical control"},
        {
            "type": "custom_tool_call_output",
            "call_id": "call_old",
            "output": "/workspace",
        },
    ]
    suffix: list[JsonValue] = [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "prior answer"}],
        },
        {"role": "user", "content": "next question"},
    ]

    assert not responses_input_suffix_retains_prior_output(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
    )


def test_full_resend_tool_loop_manifest_rejects_call_id_reused_from_stored_prefix() -> None:
    stored_input: list[JsonValue] = [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "old"},
    ]
    suffix: list[JsonValue] = [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup_again",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "new"},
    ]

    assert (
        responses_input_suffix_matches_pending_tool_calls(
            [*stored_input, *suffix],
            stored_count=len(stored_input),
            pending_tool_calls={"call_1": "function_call"},
        )
        is False
    )


def test_full_resend_tool_loop_manifest_rejects_call_id_reused_from_unsupported_prefix_item() -> None:
    stored_input: list[JsonValue] = [
        {"role": "user", "content": "first question"},
        {
            "type": "computer_call",
            "call_id": "call_1",
            "action": {"type": "screenshot"},
            "status": "completed",
        },
    ]
    suffix: list[JsonValue] = [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "new"},
    ]

    assert (
        responses_input_suffix_matches_pending_tool_calls(
            [*stored_input, *suffix],
            stored_count=len(stored_input),
            pending_tool_calls={"call_1": "function_call"},
        )
        is False
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"conversation": "conv_1", "input": []},
        {"previous_response_id": "resp_1", "input": []},
        {"prompt": {"id": "pmpt_1"}, "input": []},
        {"input": [{"type": "reasoning", "encrypted_content": "ciphertext"}]},
        {"input": [{"type": "reasoning", "encrypted_content": 123}]},
        {"input": [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "plan"}]}]},
        {"input": [{"type": "input_file", "file_id": "file_1"}]},
        {"input": [{"type": "input_image", "image_url": "sediment://file_1"}]},
        {"input": [{"type": "input_file", "file_url": "sediment://file_1"}]},
        {"input": [{"type": "input_image", "image_url": "file:///tmp/account-a.png"}]},
        {"input": [{"type": "message", "file_ids": "file_1"}]},
        {"input": [{"type": "function_call_output", "call_id": "missing", "output": "orphan"}]},
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "duplicate",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {
                    "type": "custom_tool_call",
                    "call_id": "duplicate",
                    "name": "shell",
                    "input": "pwd",
                },
                {"type": "function_call_output", "call_id": "duplicate", "output": "ambiguous"},
            ]
        },
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_duplicate_output",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_duplicate_output",
                    "output": "first",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_duplicate_output",
                    "output": "duplicate",
                },
            ]
        },
        {
            "input": [
                {"type": "function_call", "call_id": "call_1"},
                {"type": "function_call_output", "call_id": "call_1", "output": "underspecified"},
            ]
        },
        {"input": [{"type": "custom_tool_call", "call_id": "call_1", "name": "shell"}]},
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                    "caller": {"type": "program", "caller_id": "program_1"},
                },
                {"type": "function_call_output", "call_id": "call_1", "output": "ignored"},
            ]
        },
        {
            "input": [
                {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"},
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "ignored",
                    "caller": {"type": "program", "caller_id": "program_1"},
                },
            ]
        },
        {
            "input": [
                {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"},
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": [{"type": "input_file", "file_url": "sediment://file_account_a"}],
                },
            ]
        },
        {
            "input": [
                {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"},
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": [{"type": "input_image", "image_url": "sediment://file_account_a"}],
                },
            ]
        },
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_in_progress",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_in_progress",
                    "output": "partial",
                    "status": "in_progress",
                },
            ]
        },
        {
            "input": [
                {
                    "type": "apply_patch_call",
                    "call_id": "call_mixed_patch",
                    "patch": "*** Begin Patch",
                    "input": "*** Begin Patch",
                }
            ]
        },
        {"input": [{"role": "user", "content": ""}]},
        {"input": [{"role": "user", "content": [{"type": "input_text", "text": "   "}]}]},
        {"input": [{"type": "apply_patch_call", "call_id": "call_1", "status": "completed"}]},
        {"input": [{"type": "reasoning", "id": "rs_1", "status": "completed"}]},
        {
            "input": [
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "summary": [{"type": "summary_text", "text": "stored reasoning"}],
                }
            ]
        },
        {
            "input": [
                {
                    "type": "message",
                    "id": "msg_1",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "stored output"}],
                }
            ]
        },
        {
            "input": [
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                }
            ]
        },
        {"input": [{"type": "item_reference", "id": "msg_1"}]},
        {"input": [{"type": "file_search_call", "id": "search_1"}]},
        {"input": [{"type": "mcp_approval_request", "id": "approval_1"}]},
        {"input": [{"type": "future_hosted_state", "id": "future_1"}]},
        {"input": [{"role": "future", "content": "hello"}]},
        {"input": [{"type": "message", "content": "hello"}]},
        {"input": [{"content": "hello"}]},
        {
            "input": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "answer"},
                        {"type": "input_text", "text": "role-invalid"},
                    ],
                }
            ]
        },
        {
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "output_text", "text": "role-invalid"}],
                }
            ]
        },
        {"input": [{"type": 1, "content": "hello"}]},
        {"input": [{"type": "input_text"}]},
        {"input": [{"type": "input_image"}]},
        {"input": [{"role": "user", "content": [{"type": "input_text"}]}]},
        {"input": ["malformed-list-item"]},
        {"input": [None]},
        {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "future_hosted_state", "id": "upstream_1"}],
                }
            ]
        },
        {"input": [{"role": "user", "content": "hello", "future_account_handle": "acct-A"}]},
        {
            "input": [
                {
                    "role": "user",
                    "content": "hello",
                    "internal_chat_message_metadata_passthrough": {"turn_id": 123},
                }
            ]
        },
        {
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello", "future_account_handle": "acct-A"}],
                }
            ]
        },
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_future",
                    "name": "lookup",
                    "arguments": "{}",
                    "future_account_handle": "acct-A",
                }
            ]
        },
        {
            "input": [
                {
                    "type": "apply_patch_call",
                    "call_id": "call_patch_future",
                    "operation": {
                        "type": "update_file",
                        "path": "app.py",
                        "future_account_handle": "acct-A",
                    },
                }
            ]
        },
        {
            "input": [
                {
                    "type": "apply_patch_call",
                    "call_id": "call_patch_unknown",
                    "operation": {"type": "future_file_operation", "path": "app.py", "diff": "@@\n"},
                }
            ]
        },
        {
            "input": [
                {
                    "type": "apply_patch_call",
                    "call_id": "call_patch_malformed_create",
                    "operation": {"type": "create_file", "path": "new.txt"},
                }
            ]
        },
        {
            "input": [
                {
                    "type": "apply_patch_call",
                    "call_id": "call_patch_malformed_delete",
                    "operation": {"type": "delete_file", "path": "old.txt", "diff": "unexpected"},
                }
            ]
        },
        {"input": [], "text": {"format": {"type": "stored_template", "template_id": "tmpl_account_a"}}},
        {"input": [], "client_metadata": {"future_account_handle": "acct-A"}},
        {"input": [], "container_id": "container_1"},
        {"input": [], "future_state": {"vector_store_id": "vs_1"}},
        {"input": [], "future": {"file_id": "file_1"}},
        {"input": [], "future": {"encrypted_content": "ciphertext"}},
        {"input": [], "reasoning": {"encrypted_content": "ciphertext"}},
        {"input": [], "reasoning": {"encrypted_content": 123}},
        {"input": [], "reasoning": {"future": {"file_id": "file_1"}}},
        {"input": [], "reasoning": {"effort": {"future": "high"}}},
        {"input": [], "agent_id": "agent_1"},
        {"input": [], "tools": [{"type": "file_search", "vector_store_ids": ["vs_1"]}]},
        {"input": [], "tools": [{}]},
        {"input": [], "tools": [{"type": "function", "name": "lookup", "container_id": "ctr_1"}]},
        {"input": [], "tools": [{"type": "web_search", "vector_store_ids": ["vs_1"]}]},
        {
            "input": [],
            "tools": [{"type": "web_search", "filters": {"future_account_handle": "acct-A"}}],
        },
        {
            "input": [],
            "tools": [{"type": "web_search", "filters": {"allowed_domains": "openai.com"}}],
        },
        {
            "input": [],
            "tools": [{"type": "web_search", "search_context_size": "unbounded"}],
        },
        {
            "input": [],
            "tools": [{"type": "web_search", "user_location": {"container_id": "ctr-A"}}],
        },
        {
            "input": [],
            "tools": [
                {
                    "type": "custom",
                    "name": "shell",
                    "format": {
                        "type": "grammar",
                        "parameters": {"container_id": "ctr_account_a"},
                    },
                }
            ],
        },
        {
            "input": [],
            "tool_choice": {"type": "file_search", "vector_store_id": "vs_account_a"},
        },
        {
            "input": [],
            "tool_choice": {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "mcp", "server_label": "private"}],
            },
        },
        {
            "input": [
                {
                    "type": "additional_tools",
                    "tools": [{"type": "mcp", "server_label": "private"}],
                }
            ]
        },
    ],
)
def test_account_neutral_fresh_replay_rejects_owner_scoped_or_unknown_state(
    payload: dict[str, JsonValue],
) -> None:
    assert responses_payload_is_account_neutral_fresh_replay(payload) is False


@pytest.mark.parametrize(
    "reasoning",
    [
        {"encrypted_content": "ciphertext"},
        {"future": {"file_id": "file_1"}},
    ],
)
def test_account_neutral_fresh_replay_rejects_reasoning_extras_preserved_by_request_model(
    reasoning: dict[str, JsonValue],
) -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [],
            "reasoning": reasoning,
        }
    )

    assert responses_payload_is_account_neutral_fresh_replay(request.to_payload()) is False


@pytest.mark.parametrize(
    "unsafe_tool_state",
    [
        {"tool_choice": {"type": "file_search", "vector_store_id": "vs_account_a"}},
        {
            "tool_choice": {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "mcp", "server_label": "private"}],
            }
        },
        {"tools": [{"type": "function", "name": "lookup", "container_id": "ctr_account_a"}]},
        {"tools": [{"type": "web_search", "vector_store_ids": ["vs_account_a"]}]},
        {"tools": [{"type": "web_search", "filters": {"future_account_handle": "acct-A"}}]},
        {"tools": [{"type": "web_search", "search_context_size": "unbounded"}]},
        {"tools": [{"type": "web_search", "user_location": {"future_region_id": "region-A"}}]},
        {
            "tools": [
                {
                    "type": "custom",
                    "name": "shell",
                    "format": {
                        "type": "grammar",
                        "parameters": {"container_id": "ctr_account_a"},
                    },
                }
            ]
        },
    ],
)
def test_account_neutral_fresh_replay_rejects_tool_state_preserved_by_request_model(
    unsafe_tool_state: dict[str, JsonValue],
) -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [],
            **unsafe_tool_state,
        }
    )

    assert responses_payload_is_account_neutral_fresh_replay(request.to_payload()) is False


@pytest.mark.parametrize(
    "unsafe_state",
    [
        {"text": {"format": {"type": "stored_template", "template_id": "tmpl_account_a"}}},
        {"client_metadata": {"future_account_handle": "acct-A"}},
    ],
)
def test_account_neutral_fresh_replay_rejects_unknown_controls_preserved_by_request_model(
    unsafe_state: dict[str, JsonValue],
) -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [],
            **unsafe_state,
        }
    )

    assert responses_payload_is_account_neutral_fresh_replay(request.to_payload()) is False


@pytest.mark.parametrize(
    "input_item",
    [
        {"role": "user", "content": "hello", "future_account_handle": "acct-A"},
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "hello", "future_account_handle": "acct-A"}],
        },
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": "{}",
            "future_account_handle": "acct-A",
        },
        {
            "type": "apply_patch_call",
            "call_id": "call_1",
            "patch": "*** Begin Patch",
            "operation": {
                "type": "future",
                "path": "app.py",
                "future_account_handle": "acct-A",
            },
        },
    ],
)
def test_account_neutral_fresh_replay_rejects_nested_extras_preserved_by_request_model(
    input_item: dict[str, JsonValue],
) -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [input_item],
        }
    )

    assert responses_payload_is_account_neutral_fresh_replay(request.to_payload()) is False


def test_account_neutral_replay_marker_requires_tagged_existing_hard_kind() -> None:
    kind, key = make_http_bridge_account_neutral_replay_key("nonce")

    assert is_http_bridge_account_neutral_replay(kind=kind, key=key) is True
    assert (
        is_http_bridge_account_neutral_replay(
            kind=kind,
            key="account-neutral-replay:v1:",
        )
        is False
    )
    assert is_http_bridge_account_neutral_replay(kind=kind, key="a" * 64) is False
    assert is_http_bridge_account_neutral_replay(kind="turn_state_header", key=key) is False


def test_account_neutral_replay_marker_rejects_empty_nonce() -> None:
    with pytest.raises(ValueError, match="nonce"):
        make_http_bridge_account_neutral_replay_key("")


@dataclass(frozen=True)
class _TranscriptOperation:
    request_text: str | None
    response_id: str | None = "resp_1"
    parent_response_id: str | None = None
    event_spool_complete: bool = True


@dataclass(frozen=True)
class _TranscriptTurn:
    operation: _TranscriptOperation
    events: tuple[str, ...]


def _request_frame(body: dict[str, JsonValue]) -> str:
    return json.dumps({"type": "response.create", **body}, separators=(",", ":"))


def _sse_block(payload: dict[str, JsonValue]) -> str:
    return f"event: {payload['type']}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _user_item(text: str) -> dict[str, JsonValue]:
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}


def _assistant_item(
    text: str,
    *,
    item_id: str | None = None,
    annotations: list[JsonValue] | None = None,
    phase: str | None = None,
) -> dict[str, JsonValue]:
    # ``annotations`` is on every ``output_text`` part the Responses API emits
    # (see tests/fixtures/passthrough_request_corpus.json), so it is present by
    # default here rather than being an opt-in variant.
    part: dict[str, JsonValue] = {
        "type": "output_text",
        "text": text,
        "annotations": [] if annotations is None else annotations,
    }
    item: dict[str, JsonValue] = {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [part],
    }
    if phase is not None:
        item["phase"] = phase
    return item if item_id is None else {"id": item_id, **item}


def _replayed_assistant_item(text: str, *, phase: str | None = None) -> dict[str, JsonValue]:
    """The same answer as the rebuild dispatches it: no owner id, no annotations."""

    item = _assistant_item(text, phase=phase)
    return {**item, "content": cast(JsonValue, [{"type": "output_text", "text": text}])}


def _wire_user_item(text: str) -> dict[str, JsonValue]:
    """A user message in the shape tests/fixtures/passthrough_request_corpus.json records.

    The wire omits ``type`` on user input and nothing in this proxy adds one, so
    a chain turn's stored request and a client restating that turn both look
    like this -- unlike ``_user_item``, which is this file's own tidier form.
    """

    return {"role": "user", "content": [{"type": "input_text", "text": text}]}


def _restated_assistant_item(text: str, *, status: str | None = None) -> dict[str, JsonValue]:
    """A prior answer as a client restates it, not as this proxy projected it.

    The corpus records the assistant item a client holds carrying the owner's
    ``id``, the ``annotations`` array and ``reasoning_content``. The strict
    predicate refuses all three, so a restatement that can relocate at all
    carries none of them, and ``status`` is the one difference left -- present
    in what upstream spooled, optional in what the client sends back. Building
    these rows from the projected form instead is what let a join that read
    ``status`` as content look correct while doubling every conversation.
    """

    item: dict[str, JsonValue] = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }
    return item if status is None else {**item, "status": status}


def _reasoning_item(item_id: str) -> dict[str, JsonValue]:
    return {"id": item_id, "type": "reasoning", "summary": []}


def _transcript_turn(
    input_items: Sequence[JsonValue],
    output_items: Sequence[JsonValue],
    *,
    response_id: str = "resp_1",
    parent_response_id: str | None = None,
    terminal_type: str = "response.completed",
    include_terminal_output: bool = True,
    include_item_events: bool = False,
) -> _TranscriptTurn:
    events: list[str] = []
    if include_item_events:
        events.extend(_sse_block({"type": "response.output_item.done", "item": item}) for item in output_items)
    terminal_response: dict[str, JsonValue] = {"id": response_id}
    if include_terminal_output:
        terminal_response["output"] = list(output_items)
    events.append(_sse_block({"type": terminal_type, "response": terminal_response}))
    return _TranscriptTurn(
        operation=_TranscriptOperation(
            request_text=_request_frame({"model": "gpt-5.4", "input": list(input_items)}),
            response_id=response_id,
            parent_response_id=parent_response_id,
        ),
        events=tuple(events),
    )


def _newest_response_id(transcript: Sequence[object]) -> str:
    """The anchor a client continuing this chain would send."""

    newest = cast(_TranscriptTurn, transcript[-1]) if transcript else None
    return newest.operation.response_id or "" if newest is not None else ""


def _current_payload(input_items: Sequence[JsonValue], anchor: str, **extra: JsonValue) -> dict[str, JsonValue]:
    return {"model": "gpt-5.4", "input": list(input_items), "previous_response_id": anchor, **extra}


def _rebuild_result(
    transcript: Sequence[object],
    current_input: Sequence[JsonValue],
    *,
    anchor_response_id: str | None = None,
    max_turns: int = RELOCATION_TRANSCRIPT_MAX_TURNS,
    max_bytes: int = RELOCATION_TRANSCRIPT_MAX_BYTES,
    max_items: int = RELOCATION_TRANSCRIPT_MAX_ITEMS,
    **extra: JsonValue,
) -> RelocatedReplayBody | None:
    anchor = _newest_response_id(transcript) if anchor_response_id is None else anchor_response_id
    return project_durable_transcript_for_account_neutral_fresh_replay(
        transcript,
        anchor_response_id=anchor,
        current_payload=_current_payload(current_input, anchor, **extra),
        max_turns=max_turns,
        max_bytes=max_bytes,
        max_items=max_items,
    )


def _rebuild(
    transcript: Sequence[object],
    current_input: Sequence[JsonValue],
    *,
    anchor_response_id: str | None = None,
    max_turns: int = RELOCATION_TRANSCRIPT_MAX_TURNS,
    max_bytes: int = RELOCATION_TRANSCRIPT_MAX_BYTES,
    max_items: int = RELOCATION_TRANSCRIPT_MAX_ITEMS,
    **extra: JsonValue,
) -> dict[str, JsonValue] | None:
    rebuilt = _rebuild_result(
        transcript,
        current_input,
        anchor_response_id=anchor_response_id,
        max_turns=max_turns,
        max_bytes=max_bytes,
        max_items=max_items,
        **extra,
    )
    return None if rebuilt is None else rebuilt.payload


def _answerless_turn(
    prompt: str,
    *,
    response_id: str = "resp_1",
    parent_response_id: str | None = None,
) -> _TranscriptTurn:
    """A turn whose whole answer is bookkeeping the owner account minted.

    The terminal reports an answer and the spool holds one, so every check that
    runs before projection passes. The projection then removes owner-scoped
    reasoning and nothing is left.
    """

    return _transcript_turn(
        [_user_item(prompt)],
        [_reasoning_item(f"rs_{response_id}")],
        response_id=response_id,
        parent_response_id=parent_response_id,
        terminal_type="response.incomplete",
    )


def test_a_turn_whose_whole_answer_projects_away_fails_closed() -> None:
    # Emptiness is only real after projection. Admitting the turn here leaves
    # its question standing with no reply, which reads to the replacement
    # account as a turn the assistant declined to answer.
    assert _rebuild((_answerless_turn("go"),), [_user_item("and again")]) is None


def test_a_rebuilt_conversation_keeps_every_item_the_client_sent() -> None:
    # The user asked "go", was answered, and sent "go" again with a follow-up.
    # The repeat is a message the user just wrote that happens to read exactly
    # like the one the thread opens with.
    #
    # Deleting it produces a conversation that satisfies every remaining
    # predicate, relocates, and answers something the user never said. Carrying
    # it twice costs tokens. Only one of those is recoverable.
    transcript = (_transcript_turn([_user_item("go")], [_assistant_item("ok", item_id="msg_1")]),)

    rebuilt = _rebuild(transcript, [_user_item("go"), _user_item("and again")])

    assert rebuilt is not None
    assert rebuilt["input"] == [
        _user_item("go"),
        _replayed_assistant_item("ok"),
        _user_item("go"),
        _user_item("and again"),
    ]


@pytest.mark.parametrize(
    "client_turn",
    [
        [_user_item("go")],
        [_user_item("go"), _user_item("and again")],
        [_user_item("go"), _user_item("go")],
        [{"type": "input_text", "text": "go"}],
        [{"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "go"}]}],
    ],
)
def test_no_item_the_client_sent_is_absent_from_the_rebuilt_conversation(
    client_turn: list[JsonValue],
) -> None:
    # The chain opens on the same message each of these turns starts with, which
    # is the coincidence every boundary search this rebuild has had was fooled
    # by. The client's turn must arrive whole and in order, whatever it holds.
    chain = (
        _transcript_turn([_user_item("go")], [_assistant_item("an answer", item_id="msg_1")]),
        _transcript_turn(
            [_user_item("next")],
            [_assistant_item("another answer", item_id="msg_2")],
            response_id="resp_2",
            parent_response_id="resp_1",
        ),
    )

    rebuilt = _rebuild(chain, client_turn)

    assert rebuilt is not None
    rebuilt_input = cast(list[JsonValue], rebuilt["input"])
    assert rebuilt_input[-len(client_turn) :] == client_turn
    assert len(rebuilt_input) == 4 + len(client_turn)


def test_a_delta_that_settles_the_last_turns_tool_call_is_joined_whole() -> None:
    # The shape a Codex continuation actually takes when the previous turn asked
    # for a tool: the client ran it and sends back only the output, anchored on
    # the response that asked. The call lives in the chain, its output in the
    # delta, and the join has to leave the pair settled.
    call: JsonValue = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "lookup", "arguments": "{}"}
    output: JsonValue = {"type": "function_call_output", "call_id": "call_1", "output": "ok"}

    rebuilt = _rebuild((_transcript_turn([_user_item("first")], [call]),), [output])

    assert rebuilt is not None
    assert rebuilt["input"] == [
        _user_item("first"),
        {key: value for key, value in cast(Any, call).items() if key != "id"},
        output,
    ]


def test_durable_rebuild_joins_the_parent_chain_oldest_turn_first() -> None:
    transcript = (
        _transcript_turn(
            [_user_item("first")],
            [_reasoning_item("rs_1"), _assistant_item("first answer", item_id="msg_1")],
            response_id="resp_1",
        ),
        _transcript_turn(
            [_user_item("second")],
            [_assistant_item("second answer", item_id="msg_2")],
            response_id="resp_2",
            parent_response_id="resp_1",
        ),
    )

    rebuilt = _rebuild(transcript, [_user_item("third")])

    assert rebuilt is not None
    assert rebuilt["input"] == [
        _user_item("first"),
        _replayed_assistant_item("first answer"),
        _user_item("second"),
        _replayed_assistant_item("second answer"),
        _user_item("third"),
    ]


def _linked_pair() -> tuple[_TranscriptTurn, _TranscriptTurn]:
    return (
        _transcript_turn([_user_item("first")], [_assistant_item("first answer", item_id="msg_1")]),
        _transcript_turn(
            [_user_item("second")],
            [_assistant_item("second answer", item_id="msg_2")],
            response_id="resp_2",
            parent_response_id="resp_1",
        ),
    )


def test_durable_rebuild_refuses_a_chain_assembled_newest_turn_first() -> None:
    # A reversed conversation satisfies every structural predicate: each item is
    # account-neutral, every tool call is settled, the strict projection accepts
    # it. The only thing wrong with it is that the answers precede their
    # questions, and nothing downstream of here can see that.
    older, newer = _linked_pair()

    assert _rebuild((newer, older), [_user_item("third")]) is None


@pytest.mark.parametrize(
    ("broken_chain", "anchor"),
    [
        # The oldest turn names a parent the chain does not contain, so the
        # conversation is missing its opening.
        (
            (
                _transcript_turn(
                    [_user_item("second")],
                    [_assistant_item("second answer", item_id="msg_2")],
                    response_id="resp_2",
                    parent_response_id="resp_1",
                ),
            ),
            "resp_2",
        ),
        # Two turns that do not link to each other at all.
        (
            (
                _transcript_turn([_user_item("first")], [_assistant_item("a", item_id="msg_1")]),
                _transcript_turn(
                    [_user_item("second")],
                    [_assistant_item("b", item_id="msg_2")],
                    response_id="resp_2",
                    parent_response_id="resp_other",
                ),
            ),
            "resp_2",
        ),
        # A turn that names itself as its own parent.
        (
            (
                _transcript_turn(
                    [_user_item("first")],
                    [_assistant_item("a", item_id="msg_1")],
                    parent_response_id="resp_1",
                ),
            ),
            "resp_1",
        ),
        # A turn the repository could not have produced: no response id to link,
        # though the client is anchored on the one upstream minted.
        (
            (
                _TranscriptTurn(
                    operation=_TranscriptOperation(
                        request_text=_request_frame({"model": "gpt-5.4", "input": [_user_item("first")]}),
                        response_id=None,
                    ),
                    events=(
                        _sse_block(
                            {
                                "type": "response.completed",
                                "response": {"id": "resp_1", "output": [_assistant_item("a", item_id="msg_1")]},
                            }
                        ),
                    ),
                ),
            ),
            "resp_1",
        ),
    ],
)
def test_durable_rebuild_refuses_a_chain_whose_parent_links_do_not_hold(
    broken_chain: tuple[object, ...],
    anchor: str,
) -> None:
    assert _rebuild(broken_chain, [_user_item("third")], anchor_response_id=anchor) is None


def test_durable_rebuild_refuses_a_chain_that_returns_to_a_turn_it_already_walked() -> None:
    # Each step is a well-formed parent link and the walk still never leaves the
    # cycle: the turn is spent twice and the anchor it ends on is one the
    # conversation already passed through.
    cycled = (
        _transcript_turn([_user_item("first")], [_assistant_item("a", item_id="msg_1")]),
        _transcript_turn(
            [_user_item("again")],
            [_assistant_item("b", item_id="msg_2")],
            response_id="resp_1",
            parent_response_id="resp_1",
        ),
    )

    assert _rebuild(cycled, [_user_item("third")], anchor_response_id="resp_1") is None


def test_durable_rebuild_refuses_a_cycle_that_runs_through_an_intermediate_turn() -> None:
    # The cycle this rebuild has to survive is not only the one-step kind. Here
    # the walk goes resp_1 -> resp_2 -> resp_1: no turn is its own parent, every
    # link is well formed, and the chain still ends where it began, spending the
    # opening turn twice in the conversation it assembles.
    cycled = (
        _transcript_turn([_user_item("first")], [_assistant_item("a", item_id="msg_1")]),
        _transcript_turn(
            [_user_item("second")],
            [_assistant_item("b", item_id="msg_2")],
            response_id="resp_2",
            parent_response_id="resp_1",
        ),
        _transcript_turn(
            [_user_item("again")],
            [_assistant_item("c", item_id="msg_3")],
            response_id="resp_1",
            parent_response_id="resp_2",
        ),
    )

    assert _rebuild(cycled, [_user_item("third")], anchor_response_id="resp_1") is None


def test_durable_rebuild_accepts_a_turn_in_the_shape_the_bridge_actually_spools() -> None:
    # Every part of this is the shape the session bridge stores, not a reduced
    # stand-in: the owner's turn metadata on each item, its minted reasoning,
    # function-call and message ids, the settled tool pair, the final-answer
    # phase, and the annotations array on the answer's output_text part.
    owner_metadata: JsonValue = {"turn_id": "turn-owner"}
    stored_input: list[JsonValue] = [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "old question"}],
            "internal_chat_message_metadata_passthrough": owner_metadata,
        },
    ]
    terminal_output: list[JsonValue] = [
        {
            "type": "reasoning",
            "id": "rs_owner",
            "encrypted_content": "encrypted-owner-scoped-reasoning",
            "summary": [],
            "internal_chat_message_metadata_passthrough": owner_metadata,
        },
        {
            "type": "function_call",
            "id": "fc_owner",
            "call_id": "call_old",
            "name": "lookup",
            "arguments": "{}",
            "internal_chat_message_metadata_passthrough": owner_metadata,
        },
        {
            "type": "function_call_output",
            "call_id": "call_old",
            "output": "old output",
            "internal_chat_message_metadata_passthrough": owner_metadata,
        },
        {
            "type": "message",
            "id": "msg_owner",
            "role": "assistant",
            "status": "completed",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": "old answer", "annotations": []}],
            "internal_chat_message_metadata_passthrough": owner_metadata,
        },
    ]

    rebuilt = _rebuild(
        (_transcript_turn(stored_input, terminal_output),),
        [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "next question"}],
                "internal_chat_message_metadata_passthrough": {"turn_id": "turn-next"},
            }
        ],
    )

    assert rebuilt is not None
    assert rebuilt["input"] == [
        stored_input[0],
        {key: value for key, value in cast(Any, terminal_output[1]).items() if key != "id"},
        terminal_output[2],
        {
            **{key: value for key, value in cast(Any, terminal_output[3]).items() if key != "id"},
            "content": [{"type": "output_text", "text": "old answer"}],
        },
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "next question"}],
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn-next"},
        },
    ]


def test_durable_rebuild_drops_the_anchor_and_keeps_the_clients_other_fields() -> None:
    transcript = (_transcript_turn([_user_item("first")], [_assistant_item("answer", item_id="msg_1")]),)

    rebuilt = _rebuild(transcript, [_user_item("second")], instructions="be brief")

    assert rebuilt is not None
    assert "previous_response_id" not in rebuilt
    assert rebuilt["model"] == "gpt-5.4"
    assert rebuilt["instructions"] == "be brief"


def test_durable_rebuild_expands_a_stored_scalar_turn_input() -> None:
    transcript = (
        _TranscriptTurn(
            operation=_TranscriptOperation(
                request_text=_request_frame({"model": "gpt-5.4", "input": "first"}),
            ),
            events=(
                _sse_block(
                    {
                        "type": "response.completed",
                        "response": {"id": "resp_1", "output": [_assistant_item("answer", item_id="msg_1")]},
                    }
                ),
            ),
        ),
    )

    rebuilt = _rebuild(transcript, [_user_item("second")])

    assert rebuilt is not None
    assert rebuilt["input"] == [_user_item("first"), _replayed_assistant_item("answer"), _user_item("second")]


def test_a_scalar_message_restates_the_turn_the_chain_holds_as_one_text_part() -> None:
    # The wire spells one stretch of text either as the string itself or as a
    # single part carrying it. The chain holds that turn in the second spelling,
    # because a stored scalar has to become an item to sit behind others; a
    # client restating it in the first is restating the same turn, and reading
    # the two as different material dispatches the whole thread twice.
    transcript = (
        _TranscriptTurn(
            operation=_TranscriptOperation(request_text=_request_frame({"model": "gpt-5.4", "input": "first"})),
            events=(
                _sse_block(
                    {
                        "type": "response.completed",
                        "response": {"id": "resp_1", "output": [_assistant_item("answer", item_id="msg_1")]},
                    }
                ),
            ),
        ),
    )
    client_turn: list[JsonValue] = [
        {"role": "user", "content": "first"},
        _restated_assistant_item("answer"),
        _wire_user_item("second"),
    ]

    rebuilt = _rebuild(transcript, client_turn)

    assert rebuilt is not None
    assert rebuilt["input"] == client_turn


def test_a_bare_content_part_restates_the_message_the_chain_holds() -> None:
    # The corpus records clients sending a turn as a content part in its own
    # right, with no message around it, and this rebuild manufactures the other
    # spelling itself: a stored scalar becomes a one-part user message, because a
    # scalar cannot sit behind other items. So the two spellings meet on a shape
    # the proxy made, and reading them as different material dispatches the
    # thread twice on material the client did resend.
    transcript = (
        _TranscriptTurn(
            operation=_TranscriptOperation(request_text=_request_frame({"model": "gpt-5.4", "input": "first"})),
            events=(
                _sse_block(
                    {
                        "type": "response.completed",
                        "response": {"id": "resp_1", "output": [_assistant_item("answer", item_id="msg_1")]},
                    }
                ),
            ),
        ),
    )
    client_turn: list[JsonValue] = [
        {"type": "input_text", "text": "first"},
        _restated_assistant_item("answer"),
        _wire_user_item("second"),
    ]

    rebuilt = _rebuild(transcript, client_turn)

    assert rebuilt is not None
    assert rebuilt["input"] == client_turn


_DECLARED_TOOL: dict[str, JsonValue] = {
    "type": "function",
    "name": "lookup",
    "parameters": {"type": "object", "properties": {}},
}


def _tool_bundle_item(**declared: JsonValue) -> dict[str, JsonValue]:
    return {"type": "additional_tools", "role": "developer", "tools": [{**_DECLARED_TOOL, **declared}]}


@pytest.mark.parametrize(
    "reworded",
    [
        {"description": "look something up, but differently worded"},
        {"strict": True},
        {"parameters": {"type": "object", "properties": {"q": {"type": "string"}}}},
    ],
)
def test_rewording_a_tool_declaration_does_not_resend_the_conversation(reworded: dict[str, JsonValue]) -> None:
    # A Codex turn opens on the tool bundle it declares, so every restatement of
    # the thread carries one. The client is free to reword a description, tighten
    # a schema or flip strictness between turns while declaring the same tools,
    # and the recording keeps whichever wording it saw first -- so a key that
    # read the declaration whole would find no overlap at all and dispatch every
    # exchange twice, with each copy individually valid.
    stored_bundle = _tool_bundle_item(description="look something up")
    transcript = (
        _transcript_turn(
            [stored_bundle, _wire_user_item("q1")],
            [_assistant_item("a1", item_id="msg_1")],
        ),
    )
    client_input: list[JsonValue] = [
        _tool_bundle_item(**reworded),
        _wire_user_item("q1"),
        _restated_assistant_item("a1"),
        _wire_user_item("q2"),
    ]

    rebuilt = _rebuild(transcript, client_input)

    assert rebuilt is not None
    assert rebuilt["input"] == client_input


def test_a_restated_answer_is_measured_against_the_item_it_belongs_to() -> None:
    # A turn whose answer is more than one item: commentary, then the answer.
    # The client restates the last of them, so exactly one item comes off the
    # accumulation. An accumulation that paired any item with another's key
    # measures this overlap at the wrong offset -- here it finds none, and the
    # commentary and the answer are both dispatched twice -- while every list
    # stays the same length and every structural check downstream still passes.
    transcript = (
        _transcript_turn(
            [_wire_user_item("q1")],
            [
                _assistant_item("thinking about it", item_id="msg_1", phase="commentary"),
                _assistant_item("a1", item_id="msg_2"),
            ],
        ),
    )
    client_input: list[JsonValue] = [_restated_assistant_item("a1"), _wire_user_item("q2")]

    rebuilt = _rebuild(transcript, client_input)

    assert rebuilt is not None
    assert rebuilt["input"] == [
        _wire_user_item("q1"),
        _replayed_assistant_item("thinking about it", phase="commentary"),
        *client_input,
    ]


@pytest.mark.parametrize(
    "client_turn",
    [
        # The ordinary delta.
        [_user_item("second")],
        # A delta holding no message item at all, which the corpus records.
        [{"type": "input_text", "text": "second"}],
    ],
)
def test_an_input_that_restates_nothing_is_that_turns_delta(client_turn: list[JsonValue]) -> None:
    transcript = (
        _transcript_turn(
            [_user_item("first")],
            [_reasoning_item("rs_1"), _assistant_item("answer", item_id="msg_1")],
        ),
    )

    rebuilt = _rebuild(transcript, client_turn)

    assert rebuilt is not None
    assert rebuilt["input"] == [_user_item("first"), _replayed_assistant_item("answer"), *client_turn]


# --- The join, row by row over the shapes a client actually sends -------------
#
# Every row is built from wire shapes -- the user item the corpus records, the
# assistant item the bridge spools, and the restatement a client sends back --
# rather than from the proxy's own post-projection normal form. A table written
# in that normal form only ever proves the join works on input already shaped
# like the chain, which is the one case where a comparison that reads recording
# bookkeeping as content still looks correct.
#
# Each row dispatches ``expected_chain_prefix + client_input``: what the walk
# accumulated, minus the tail the client is restating, then every item the
# client sent, verbatim and last. The invariants are asserted for every row
# rather than per row, because the rows that look harmless are exactly the ones
# a boundary rule deletes a message from.

_DEVELOPER_INSTRUCTION: dict[str, JsonValue] = {
    "type": "message",
    "role": "developer",
    "content": [{"type": "input_text", "text": "be concise"}],
}
_FRESH_TOOL_CALL: dict[str, JsonValue] = {
    "type": "function_call",
    "call_id": "call_new",
    "name": "lookup",
    "arguments": "{}",
}
_FRESH_TOOL_OUTPUT: dict[str, JsonValue] = {"type": "function_call_output", "call_id": "call_new", "output": "ok"}


def _four_turn_chain(*opening_items: JsonValue) -> tuple[object, ...]:
    """``q1/a1 q2/a2 q3/a3 q4/a4``, led by whatever the opening request carried."""

    return tuple(
        _transcript_turn(
            [*(opening_items if index == 1 else ()), _wire_user_item(f"q{index}")],
            [_assistant_item(f"a{index}", item_id=f"msg_{index}")],
            response_id=f"resp_{index}",
            parent_response_id=None if index == 1 else f"resp_{index - 1}",
        )
        for index in range(1, 5)
    )


def _four_turn_accumulation(*opening_items: JsonValue) -> list[JsonValue]:
    """That chain as the walk accumulates it, before the client's turn joins."""

    accumulated: list[JsonValue] = list(opening_items)
    for index in range(1, 5):
        accumulated.extend((_wire_user_item(f"q{index}"), _replayed_assistant_item(f"a{index}")))
    return accumulated


def _restated_exchange(index: int) -> list[JsonValue]:
    """One exchange as the client sends it back: the question, then the answer without its ``status``."""

    return [_wire_user_item(f"q{index}"), _restated_assistant_item(f"a{index}")]


_CHAIN = _four_turn_chain()
_ACCUMULATED = _four_turn_accumulation()
_DEVELOPER_LED_CHAIN = _four_turn_chain(_DEVELOPER_INSTRUCTION)
_DEVELOPER_LED_ACCUMULATION = _four_turn_accumulation(_DEVELOPER_INSTRUCTION)
_RESTATED_THREAD = [item for index in range(1, 5) for item in _restated_exchange(index)]

_JOIN_TABLE: list[tuple[str, tuple[object, ...], list[JsonValue], list[JsonValue], list[JsonValue]]] = [
    (
        "plain delta",
        _CHAIN,
        _ACCUMULATED,
        [_wire_user_item("q5")],
        _ACCUMULATED,
    ),
    (
        "rolling window of one exchange",
        _CHAIN,
        _ACCUMULATED,
        [*_restated_exchange(4), _wire_user_item("q5")],
        _ACCUMULATED[:6],
    ),
    (
        "rolling window of two exchanges",
        _CHAIN,
        _ACCUMULATED,
        [*_restated_exchange(3), *_restated_exchange(4), _wire_user_item("q5")],
        _ACCUMULATED[:4],
    ),
    (
        # The same window, restated with the ``status`` the spool carries. Both
        # spellings are legal on the wire and both name the same exchange.
        "rolling window restated with the recorded status",
        _CHAIN,
        _ACCUMULATED,
        [
            _wire_user_item("q4"),
            _restated_assistant_item("a4", status="completed"),
            _wire_user_item("q5"),
        ],
        _ACCUMULATED[:6],
    ),
    (
        "full resend",
        _CHAIN,
        _ACCUMULATED,
        [*_RESTATED_THREAD, _wire_user_item("q5")],
        [],
    ),
    (
        "developer-instruction-led full resend",
        _DEVELOPER_LED_CHAIN,
        _DEVELOPER_LED_ACCUMULATION,
        [_DEVELOPER_INSTRUCTION, *_RESTATED_THREAD, _wire_user_item("q5")],
        [],
    ),
    (
        # No new question at the end: the client restated the last exchange and
        # stopped, which is what a "carry on from here" turn looks like. Every
        # other row ends on a fresh message, and a join that mishandles the final
        # item survives all of them.
        "restatement that ends the conversation",
        _CHAIN,
        _ACCUMULATED,
        _restated_exchange(4),
        _ACCUMULATED[:6],
    ),
    (
        "full resend that ends the conversation",
        _CHAIN,
        _ACCUMULATED,
        list(_RESTATED_THREAD),
        [],
    ),
    (
        # No model-authored item anywhere in it, and its first item reads exactly
        # like the message the thread opens with. Neither fact is at the
        # accumulated tail, so neither shortens the chain.
        "tool-only history",
        _CHAIN,
        _ACCUMULATED,
        [_wire_user_item("q1"), _FRESH_TOOL_CALL, _FRESH_TOOL_OUTPUT, _wire_user_item("q5")],
        _ACCUMULATED,
    ),
    (
        "window from the middle of the chain",
        _CHAIN,
        _ACCUMULATED,
        [*_restated_exchange(2), _wire_user_item("q4")],
        _ACCUMULATED,
    ),
    (
        # The client's new message repeats the conversation's opening. Two items
        # being equal is not a turn arriving twice: both copies are dispatched.
        "coincidental opening item",
        _CHAIN,
        _ACCUMULATED,
        [_wire_user_item("q1"), _restated_assistant_item("a5"), _wire_user_item("q5")],
        _ACCUMULATED,
    ),
]


@dataclass(frozen=True, slots=True)
class _Position:
    """One place a structure sits inside an input item, and what identifies it there.

    ``wire`` is every field the strict predicate admits at that place, taken from
    the predicate's own lists rather than hand-written, so a field admitted there
    later arrives here without a legal value and fails until somebody decides
    whether it identifies anything. ``identity`` is this file's reading of the
    requirement -- "a message's role and content, a tool call's identity and
    arguments, a tool output's call and result", plus a bundle's declared tools
    -- and it is written out here rather than imported from the module, so the
    sweep below, the distinctness rows and the quadratic checker can all
    disagree with the implementation.

    ``item`` is a whole input item with this position filled in and ``path``
    says where inside it the structure sits, so a nested position is exercised
    through the same public key the join uses rather than through a helper.
    """

    name: str
    item: dict[str, JsonValue]
    path: tuple[str | int, ...]
    wire: frozenset[str]
    identity: frozenset[str]


_MESSAGE_FIELDS = replay_safety_module._ACCOUNT_NEUTRAL_MESSAGE_FIELDS
_CONTENT_FIELDS = replay_safety_module._ACCOUNT_NEUTRAL_CONTENT_FIELDS
_ITEM_FIELDS = replay_safety_module._ACCOUNT_NEUTRAL_INPUT_ITEM_FIELDS
_TOOL_FIELDS = replay_safety_module._ACCOUNT_NEUTRAL_TOOL_DECLARATION_FIELDS
_OPERATION_FIELDS = replay_safety_module._ACCOUNT_NEUTRAL_APPLY_PATCH_OPERATION_FIELDS
# What identifies a content part, wherever it sits: what was said, which
# attachment was attached. Not ``detail``, which asks how an image should be
# rendered, and not ``filename``, which names a file the ``file_id`` or the
# inline ``file_data`` already is.
_PART_IDENTITY_BY_TYPE: dict[str, frozenset[str]] = {
    "input_file": frozenset({"file_data", "file_id", "file_url"}),
    "input_image": frozenset({"file_id", "image_url"}),
    "input_text": frozenset({"text"}),
    "output_text": frozenset({"text"}),
    "refusal": frozenset({"refusal"}),
    "text": frozenset({"text"}),
}
_TEXT_PART: dict[str, JsonValue] = {"type": "input_text", "text": "go"}
_PLAIN_TEXT_PART: dict[str, JsonValue] = {"type": "text", "text": "go"}
_IMAGE_PART: dict[str, JsonValue] = {"type": "input_image", "image_url": "https://example.test/a.png"}
_FILE_PART: dict[str, JsonValue] = {"type": "input_file", "file_url": "https://example.test/a.txt"}
_ANSWER_PART: dict[str, JsonValue] = {"type": "output_text", "text": "hi"}
_REFUSAL_PART: dict[str, JsonValue] = {"type": "refusal", "refusal": "no"}
_FUNCTION_TOOL: dict[str, JsonValue] = {"type": "function", "name": "lookup"}
_CUSTOM_TOOL: dict[str, JsonValue] = {"type": "custom", "name": "shell"}


def _message_of(role: str, part: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {"type": "message", "role": role, "content": [dict(part)]}


def _bundle_of(tool: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {"type": "additional_tools", "role": "developer", "tools": [dict(tool)]}


def _call_of(item_type: str, **slots: JsonValue) -> dict[str, JsonValue]:
    return {"type": item_type, "call_id": "call_1", **slots}


def _part_position(name: str, item: dict[str, JsonValue], path: tuple[str | int, ...], part_type: str) -> _Position:
    return _Position(name, item, path, _CONTENT_FIELDS[part_type], _PART_IDENTITY_BY_TYPE[part_type])


_POSITIONS: tuple[_Position, ...] = (
    _Position("user message", _message_of("user", _TEXT_PART), (), _MESSAGE_FIELDS, frozenset({"content", "role"})),
    _Position(
        "assistant message", _message_of("assistant", _ANSWER_PART), (), _MESSAGE_FIELDS, frozenset({"content", "role"})
    ),
    _part_position("text part in a message", _message_of("user", _TEXT_PART), ("content", 0), "input_text"),
    _part_position("plain text part in a message", _message_of("user", _PLAIN_TEXT_PART), ("content", 0), "text"),
    _part_position("image part in a message", _message_of("user", _IMAGE_PART), ("content", 0), "input_image"),
    _part_position("file part in a message", _message_of("user", _FILE_PART), ("content", 0), "input_file"),
    _part_position("answer part in a message", _message_of("assistant", _ANSWER_PART), ("content", 0), "output_text"),
    _part_position("refusal part in a message", _message_of("assistant", _REFUSAL_PART), ("content", 0), "refusal"),
    _part_position("text part sent on its own", dict(_TEXT_PART), (), "input_text"),
    _part_position("image part sent on its own", dict(_IMAGE_PART), (), "input_image"),
    _part_position("file part sent on its own", dict(_FILE_PART), (), "input_file"),
    _Position(
        "tool bundle", _bundle_of(_FUNCTION_TOOL), (), _ITEM_FIELDS["additional_tools"], frozenset({"role", "tools"})
    ),
    _Position(
        "declared function", _bundle_of(_FUNCTION_TOOL), ("tools", 0), _TOOL_FIELDS["function"], frozenset({"name"})
    ),
    _Position(
        "declared custom tool", _bundle_of(_CUSTOM_TOOL), ("tools", 0), _TOOL_FIELDS["custom"], frozenset({"name"})
    ),
    _Position(
        "declared custom tool grammar",
        _bundle_of({**_CUSTOM_TOOL, "format": {"type": "grammar", "syntax": "lark", "definition": "start: A"}}),
        ("tools", 0, "format"),
        frozenset({"definition", "syntax"}),
        frozenset(),
    ),
    _Position(
        "declared web search", _bundle_of({"type": "web_search"}), ("tools", 0), _TOOL_FIELDS["web_search"], frozenset()
    ),
    _Position(
        "declared web search preview",
        _bundle_of({"type": "web_search_preview"}),
        ("tools", 0),
        _TOOL_FIELDS["web_search_preview"],
        frozenset(),
    ),
    _Position(
        "web search domain filters",
        _bundle_of({"type": "web_search", "filters": {"allowed_domains": ["a.test"]}}),
        ("tools", 0, "filters"),
        replay_safety_module._ACCOUNT_NEUTRAL_WEB_SEARCH_FILTER_FIELDS,
        frozenset(),
    ),
    _Position(
        "web search user location",
        _bundle_of({"type": "web_search", "user_location": {"type": "approximate", "city": "Seoul"}}),
        ("tools", 0, "user_location"),
        replay_safety_module._ACCOUNT_NEUTRAL_WEB_SEARCH_LOCATION_FIELDS,
        frozenset(),
    ),
    _Position(
        "function call",
        _call_of("function_call", name="lookup", arguments="{}"),
        (),
        _ITEM_FIELDS["function_call"],
        frozenset({"arguments", "call_id", "name"}),
    ),
    _Position(
        "custom tool call",
        _call_of("custom_tool_call", name="shell", input="ls"),
        (),
        _ITEM_FIELDS["custom_tool_call"],
        frozenset({"call_id", "input", "name"}),
    ),
    _Position(
        "apply patch call",
        _call_of("apply_patch_call", patch="*** Begin Patch"),
        (),
        _ITEM_FIELDS["apply_patch_call"],
        # An apply-patch call spells its arguments in exactly one of the three,
        # so all three identify it and which one is used does not.
        frozenset({"call_id", "input", "operation", "patch"}),
    ),
    _Position(
        "created file",
        _call_of("apply_patch_call", operation={"type": "create_file", "path": "a.py", "diff": "@@"}),
        ("operation",),
        _OPERATION_FIELDS["create_file"],
        frozenset({"diff", "path"}),
    ),
    _Position(
        "deleted file",
        _call_of("apply_patch_call", operation={"type": "delete_file", "path": "a.py"}),
        ("operation",),
        _OPERATION_FIELDS["delete_file"],
        frozenset({"path"}),
    ),
    _Position(
        "updated file",
        _call_of("apply_patch_call", operation={"type": "update_file", "path": "a.py", "diff": "@@"}),
        ("operation",),
        _OPERATION_FIELDS["update_file"],
        frozenset({"diff", "path"}),
    ),
    _Position(
        "function call output",
        _call_of("function_call_output", output="ok"),
        (),
        _ITEM_FIELDS["function_call_output"],
        frozenset({"call_id", "output"}),
    ),
    _Position(
        "custom tool call output",
        _call_of("custom_tool_call_output", output="ok"),
        (),
        _ITEM_FIELDS["custom_tool_call_output"],
        frozenset({"call_id", "output"}),
    ),
    _Position(
        "apply patch call output",
        _call_of("apply_patch_call_output", output="done"),
        (),
        _ITEM_FIELDS["apply_patch_call_output"],
        frozenset({"call_id", "output"}),
    ),
    _part_position(
        "text part in a tool result", _call_of("function_call_output", output=[_TEXT_PART]), ("output", 0), "input_text"
    ),
    _part_position(
        "image part in a tool result",
        _call_of("function_call_output", output=[_IMAGE_PART]),
        ("output", 0),
        "input_image",
    ),
    _part_position(
        "file part in a tool result", _call_of("function_call_output", output=[_FILE_PART]), ("output", 0), "input_file"
    ),
)
# Two legal values per field: the first says whether a field the identity omits
# can change a key, and the pair together says whether a field the identity
# names does. Keyed by field name and shared across positions, so a field that
# means one thing in two places cannot be given two readings here either.
_LEGAL_VALUES: dict[str, tuple[JsonValue, JsonValue]] = {
    "allowed_domains": (["a.test"], ["b.test"]),
    "arguments": ('{"q":1}', '{"q":2}'),
    "call_id": ("call_one", "call_two"),
    "caller": ({"type": "direct"}, {"type": "direct"}),
    "city": ("Seoul", "Tokyo"),
    "content": ([{"type": "input_text", "text": "one"}], [{"type": "input_text", "text": "other"}]),
    "country": ("KR", "JP"),
    "definition": ("start: A", "start: B"),
    "description": ("does a thing", "does another thing"),
    "detail": ("low", "high"),
    "diff": ("@@ -1 +1 @@", "@@ -2 +2 @@"),
    "file_data": ("AAA", "BBB"),
    "file_id": ("file_one", "file_two"),
    "file_url": ("https://example.test/one.txt", "https://example.test/two.txt"),
    "filename": ("one.txt", "two.txt"),
    "filters": ({"allowed_domains": ["a.test"]}, {"allowed_domains": ["b.test"]}),
    "format": ({"type": "text"}, {"type": "grammar", "syntax": "lark", "definition": "start: A"}),
    "id": ("item_one", "item_two"),
    "image_url": ("https://example.test/one.png", "https://example.test/two.png"),
    "input": ("ls", "pwd"),
    "internal_chat_message_metadata_passthrough": ({"turn_id": "turn-one"}, {"turn_id": "turn-two"}),
    "name": ("lookup", "search"),
    "operation": (
        {"type": "update_file", "path": "one.py", "diff": "@@"},
        {"type": "update_file", "path": "two.py", "diff": "@@"},
    ),
    "output": ("yes", "no"),
    "parameters": ({"type": "object", "properties": {}}, {"type": "object", "properties": {"q": {"type": "string"}}}),
    "patch": ("*** Begin Patch one", "*** Begin Patch two"),
    "path": ("one.py", "two.py"),
    "phase": ("commentary", "final_answer"),
    "refusal": ("no", "never"),
    "region": ("Seoul", "Kanto"),
    "role": ("user", "developer"),
    "search_context_size": ("low", "high"),
    "status": ("completed", "failed"),
    "strict": (True, False),
    "syntax": ("lark", "regex"),
    "text": ("one", "other"),
    "timezone": ("Asia/Seoul", "Asia/Tokyo"),
    "tools": ([{"type": "function", "name": "lookup"}], [{"type": "function", "name": "search"}]),
    "user_location": ({"type": "approximate", "city": "Seoul"}, {"type": "approximate", "city": "Tokyo"}),
}
_POSITION_BY_NAME = {position.name: position for position in _POSITIONS}
# ``type`` is what selects the position rather than a field swept inside it: a
# row that changed it would be describing some other structure.
_SELECTOR_FIELD = "type"
_IGNORED_FIELD_POSITIONS: list[tuple[str, str]] = sorted(
    (position.name, field) for position in _POSITIONS for field in position.wire - position.identity - {_SELECTOR_FIELD}
)
_IDENTITY_FIELD_POSITIONS: list[tuple[str, str]] = sorted(
    (position.name, field) for position in _POSITIONS for field in position.identity - {_SELECTOR_FIELD}
)


def _fragment_at(item: JsonValue, path: tuple[str | int, ...]) -> dict[str, JsonValue]:
    fragment: Any = item
    for step in path:
        fragment = fragment[step]
    return cast(dict[str, JsonValue], fragment)


def _item_with(position: _Position, fragment: dict[str, JsonValue]) -> JsonValue:
    """``position.item``, with the structure at ``position.path`` replaced."""

    if not position.path:
        return fragment
    item = copy.deepcopy(position.item)
    cast(Any, _fragment_at(item, position.path[:-1]))[position.path[-1]] = fragment
    return item


def _identity_fields_by_kind() -> dict[str, frozenset[str]]:
    """The requirement's identity sets, keyed by the ``type`` that selects them.

    A kind identifies the same thing wherever it sits: a file part attached to a
    message, sent on its own and returned by a tool are one kind of thing said
    in three places. Building the table this way is what makes the two readings
    impossible rather than merely untested.
    """

    fields: dict[str, frozenset[str]] = {}
    for position in _POSITIONS:
        # A tool's search filters are the one swept structure the wire gives no
        # ``type``; nothing inside one identifies anything, so there is no kind
        # for the checker to look up either.
        kind = _fragment_at(position.item, position.path).get(_SELECTOR_FIELD)
        if not isinstance(kind, str):
            continue
        if fields.setdefault(kind, position.identity) != position.identity:
            raise AssertionError(f"{kind} would be identified differently depending on where it sits")
    return fields


_RULE_IDENTITY_BY_KIND = _identity_fields_by_kind()
# The wire spells one stretch of text three ways depending on where it sits, and
# the requirement identifies a part by what was said in it.
_RULE_TEXT_KINDS = frozenset({"input_text", "output_text", "text"})
_RULE_BARE_PART_KINDS = frozenset({"input_file", "input_image", "input_text"})


def _rule_kind(fragment: dict[str, JsonValue]) -> str | None:
    kind = fragment.get(_SELECTOR_FIELD)
    if kind is None:
        return "message"
    return kind if isinstance(kind, str) else None


def _rule_as_sent(item: JsonValue) -> JsonValue:
    """One spelling for the two the wire allows.

    A content part sent as an input item in its own right is the user's content,
    which is what a one-part user message is -- and this rebuild turns a turn
    stored as a scalar into exactly that message, so a client restating such a
    turn in the bare spelling has to be recognised as restating it.
    """

    if isinstance(item, dict) and _rule_kind(item) in _RULE_BARE_PART_KINDS:
        return {"type": "message", "role": "user", "content": [item]}
    return item


def _rule_fragment_identity(fragment: JsonValue) -> object:
    if isinstance(fragment, list):
        return tuple(_rule_fragment_identity(entry) for entry in fragment)
    if not isinstance(fragment, dict):
        return fragment
    kind = _rule_kind(fragment)
    identity = _RULE_IDENTITY_BY_KIND.get(kind) if kind is not None else None
    if identity is None:
        return object()
    if kind == "message":
        content = fragment.get("content")
        if isinstance(content, str):
            fragment = {**fragment, "content": [{"type": "input_text", "text": content}]}
        elif not isinstance(content, list):
            return object()
    return (
        "wrote" if kind in _RULE_TEXT_KINDS else kind,
        *((field, _rule_fragment_identity(fragment.get(field))) for field in sorted(identity)),
    )


def _rule_identity(item: JsonValue) -> object:
    """What the requirement says identifies an input item, read off the position table.

    Written as a projection that knows only which fields identify which kind, so
    it shares no branch, no tuple shape and no helper with the module: a checker
    built on the implementation's own pieces checks the fast path against itself,
    and a defect in a shared piece passes both.
    """

    return _rule_fragment_identity(_rule_as_sent(item))


def _restated_tail_length(accumulated: Sequence[JsonValue], client_input: Sequence[JsonValue]) -> int:
    """The rule stated as a search: the longest tail of the chain the client restates.

    Deliberately the slow reading of the requirement, and deliberately built on
    its own identity, so the fast one in the module is checked against the rule
    rather than against itself.
    """

    return _restated_tail_from_keys(
        [_rule_identity(item) for item in accumulated], [_rule_identity(item) for item in client_input]
    )


def _restated_tail_from_keys(chain_keys: Sequence[object], client_keys: Sequence[object]) -> int:
    return next(
        (
            length
            for length in range(min(len(chain_keys), len(client_keys)), 0, -1)
            if list(chain_keys[len(chain_keys) - length :]) == list(client_keys[:length])
        ),
        0,
    )


def test_the_overlap_is_the_longest_restated_tail_not_the_shortest() -> None:
    # The accumulation ends on the item the incoming input opens with, so a
    # one-item match exists as well as the three-item one. Stopping at the short
    # match leaves the first two of those three items behind and dispatches
    # them again in front of the input that restated them.
    accumulated: list[JsonValue] = [_wire_user_item("a"), _replayed_assistant_item("b"), _wire_user_item("a")]
    incoming: list[JsonValue] = [*accumulated, _wire_user_item("c")]

    assert _tail_overlap_length(*_paired(accumulated, incoming)) == 3


def _paired(*item_lists: Sequence[JsonValue]) -> list[list[tuple[JsonValue, object]]]:
    return [[(item, _relocation_comparison_key(item)) for item in items] for items in item_lists]


_SAMPLE_ITEM_BY_TYPE: dict[str, dict[str, JsonValue]] = {
    "additional_tools": {
        "type": "additional_tools",
        "role": "developer",
        "tools": [{"type": "function", "name": "lookup"}],
    },
    "apply_patch_call": {"type": "apply_patch_call", "call_id": "call_p", "patch": "*** Begin Patch"},
    "apply_patch_call_output": {"type": "apply_patch_call_output", "call_id": "call_p", "output": "done"},
    "custom_tool_call": {"type": "custom_tool_call", "call_id": "call_c", "name": "shell", "input": "ls"},
    "custom_tool_call_output": {"type": "custom_tool_call_output", "call_id": "call_c", "output": "ok"},
    "function_call": {"type": "function_call", "call_id": "call_f", "name": "lookup", "arguments": "{}"},
    "function_call_output": {"type": "function_call_output", "call_id": "call_f", "output": "ok"},
    "input_file": {"type": "input_file", "file_url": "https://example.test/bare.txt", "filename": "bare.txt"},
    "input_image": {"type": "input_image", "image_url": "https://example.test/bare.png", "detail": "low"},
    "input_text": {"type": "input_text", "text": "sent on its own"},
    "message": _user_item("go"),
}


def test_the_key_identifies_every_item_type_a_dispatched_body_may_carry() -> None:
    # An item type the key does not enumerate gets a key equal to nothing, itself
    # included, so no overlap is ever claimed across it and a client restating a
    # turn containing one has its whole conversation dispatched twice. The strict
    # predicate's own list of admitted types is the closed set this has to cover.
    assert set(_SAMPLE_ITEM_BY_TYPE) == replay_safety_module._ACCOUNT_NEUTRAL_INPUT_ITEM_TYPES
    for item_type, item in sorted(_SAMPLE_ITEM_BY_TYPE.items()):
        assert _relocation_comparison_key(dict(item)) == _relocation_comparison_key(dict(item)), item_type


def test_the_sweep_reaches_every_item_type_and_every_structure_under_one() -> None:
    # The sweep below is only as wide as this table. Two earlier readings of this
    # key were defeated by a field nobody had listed, and a third by a field nobody
    # had swept, because the table stopped at an item's top level -- so the table's
    # own coverage is asserted rather than assumed: every admitted item type, and
    # every structure the strict predicate admits underneath one.
    swept_item_types = {cast(str, position.item[_SELECTOR_FIELD]) for position in _POSITIONS}
    nested_paths = {position.path for position in _POSITIONS if position.path}

    assert swept_item_types == replay_safety_module._ACCOUNT_NEUTRAL_INPUT_ITEM_TYPES
    assert nested_paths == {
        ("content", 0),
        ("operation",),
        ("output", 0),
        ("tools", 0),
        ("tools", 0, "format"),
        ("tools", 0, "filters"),
        ("tools", 0, "user_location"),
    }
    assert set(_RULE_IDENTITY_BY_KIND) >= replay_safety_module._ACCOUNT_NEUTRAL_MESSAGE_CONTENT_TYPES
    assert {field for _, field in [*_IGNORED_FIELD_POSITIONS, *_IDENTITY_FIELD_POSITIONS]} <= set(_LEGAL_VALUES)


@pytest.mark.parametrize(
    ("position", "field"),
    _IGNORED_FIELD_POSITIONS,
    ids=[f"{position}.{field}" for position, field in _IGNORED_FIELD_POSITIONS],
)
def test_no_legal_field_outside_the_identity_set_changes_an_items_key(position: str, field: str) -> None:
    # Every field the wire admits somewhere inside a restatable item and the key
    # does not enumerate, one per row. Two earlier readings built this key by
    # subtracting a hand-listed set instead, and each was defeated by a field
    # nobody had listed -- first ``status``, then ``phase``; a third serialized a
    # tool bundle whole, which is the same subtraction one level lower and made a
    # reworded description dispatch the conversation twice. The list cannot be finished,
    # so the key reads what identifies a turn, at every depth, and a field nobody
    # thought of is ignored by default.
    row = _POSITION_BY_NAME[position]
    fragment = _fragment_at(row.item, row.path)
    unchanged = _relocation_comparison_key(_item_with(row, fragment))

    for value in _LEGAL_VALUES[field]:
        assert _relocation_comparison_key(_item_with(row, {**fragment, field: value})) == unchanged


@pytest.mark.parametrize(
    ("position", "field"),
    _IDENTITY_FIELD_POSITIONS,
    ids=[f"{position}.{field}" for position, field in _IDENTITY_FIELD_POSITIONS],
)
def test_a_field_that_identifies_an_item_decides_whether_it_was_restated(position: str, field: str) -> None:
    # The other half of a positive key: a field that does identify an item has to
    # be in it, at every depth. An identity missing one of these makes two
    # different items read as restatements of each other, and the chain's copy is
    # then discarded for a client item that does not stand in for it. One row per
    # component, so no component can be deleted with the suite green.
    row = _POSITION_BY_NAME[position]
    fragment = _fragment_at(row.item, row.path)
    first, second = _LEGAL_VALUES[field]

    assert _relocation_comparison_key(_item_with(row, {**fragment, field: first})) != _relocation_comparison_key(
        _item_with(row, {**fragment, field: second})
    )


def test_item_types_saying_different_things_do_not_share_a_key() -> None:
    # The sweep varies one field inside one kind; this is the cross-kind half.
    # A call and the output that answers it carry the same ``call_id``, and the
    # three tool vocabularies spell their calls with the same fields, so a key
    # that dropped the item type would read any of them as a restatement of
    # another and discard a chain item for something that does not stand in.
    keys = {item_type: _relocation_comparison_key(item) for item_type, item in _SAMPLE_ITEM_BY_TYPE.items()}

    for item_type, key in keys.items():
        others = [other for other_type, other in keys.items() if other_type != item_type]
        assert all(key != other for other in others), item_type


def test_a_part_sent_on_its_own_is_the_message_that_holds_it() -> None:
    # The one equivalence the row above must not read as a collision. This proxy
    # rebuilds a turn stored as a scalar into a one-part user message, so a
    # client restating that turn in the bare spelling the wire also allows has to
    # be recognised -- otherwise the rebuild fails to see a restatement of a
    # shape it manufactured itself and dispatches the conversation twice.
    for part in (_TEXT_PART, _IMAGE_PART, _FILE_PART):
        assert _relocation_comparison_key(dict(part)) == _relocation_comparison_key(_message_of("user", part))
        assert _relocation_comparison_key(dict(part)) != _relocation_comparison_key(_message_of("assistant", part))


_ITEM_TYPE_PROBE: dict[str, JsonValue] = {
    "arguments": "{}",
    "call_id": "call_1",
    "content": [{"type": "input_text", "text": "content text"}],
    "file_url": "https://example.test/probe.txt",
    "image_url": "https://example.test/probe.png",
    "input": "ls",
    "name": "lookup",
    "operation": {"type": "update_file", "path": "a.py", "diff": "@@"},
    "output": "ok",
    "patch": "*** Begin Patch",
    "role": "user",
    "text": "part text",
    "tools": [{"type": "function", "name": "lookup"}],
}


def test_two_items_differing_only_in_their_type_are_different_items() -> None:
    # One item filled in for every slot any admitted kind reads, dispatched
    # eleven times under eleven types. The three tool vocabularies spell a call
    # with the same fields and a call's output carries the call's own id, so a
    # key that left the item type out would read a shell command as a restatement
    # of the patch that followed it -- and discard the chain's copy of a turn the
    # client never restated.
    keys = {
        item_type: _relocation_comparison_key({**_ITEM_TYPE_PROBE, _SELECTOR_FIELD: item_type})
        for item_type in sorted(replay_safety_module._ACCOUNT_NEUTRAL_INPUT_ITEM_TYPES)
    }

    for item_type, key in keys.items():
        assert all(key != other for other_type, other in keys.items() if other_type != item_type), item_type


_UNREADABLE_STRUCTURES: list[tuple[str, JsonValue]] = [
    ("an item type the predicate does not admit", {"type": "compaction", "content": []}),
    ("a content part of a type nothing enumerates", _message_of("user", {"type": "input_audio", "audio": "..."})),
    ("a tools slot that is not a list", {"type": "additional_tools", "role": "developer", "tools": _FUNCTION_TOOL}),
    ("a tool the predicate does not admit", _bundle_of({"type": "mcp", "name": "lookup"})),
    (
        "an apply-patch operation of a kind nothing enumerates",
        _call_of("apply_patch_call", operation={"type": "move_file", "path": "a.py"}),
    ),
    ("an apply-patch operation that is not an object", _call_of("apply_patch_call", operation=["move", "a.py"])),
    (
        "call arguments sent as an object rather than a blob",
        _call_of("function_call", name="lookup", arguments={"q": 1}),
    ),
    ("a tool result sent as an object rather than a blob", _call_of("function_call_output", output={"rows": []})),
    (
        "a tool result part of a type nothing enumerates",
        _call_of("function_call_output", output=[{"type": "input_audio", "audio": "..."}]),
    ),
]


@pytest.mark.parametrize(
    "item",
    [row[1] for row in _UNREADABLE_STRUCTURES],
    ids=[row[0] for row in _UNREADABLE_STRUCTURES],
)
def test_a_structure_the_enumeration_cannot_read_leaves_the_item_unidentifiable(item: JsonValue) -> None:
    # Failing closed at depth, which is the whole reason the enumeration is
    # positive: a structure nothing here can read gets a key equal to nothing,
    # itself included, so no overlap is ever claimed across it. Comparing one on
    # its raw form instead would have the rebuild guess -- and a guess here
    # discards a chain item for a client item that does not stand in for it.
    assert _relocation_comparison_key(item) != _relocation_comparison_key(copy.deepcopy(item))


@pytest.mark.parametrize("unhashable_type", [[], {}, ["input_text"]])
def test_a_content_part_whose_type_is_a_structure_is_unidentifiable(unhashable_type: JsonValue) -> None:
    # The key runs on the client's raw input, before the strict predicate has
    # refused anything, so a ``type`` slot holding a list or an object reaches it
    # -- and a set membership test on one raises rather than returning False,
    # which would take the whole relocation down instead of declining it.
    part: dict[str, JsonValue] = {"type": unhashable_type, "text": "go"}

    assert _relocation_comparison_key(_message_of("user", part)) != _relocation_comparison_key(
        _message_of("user", part)
    )


def test_no_items_key_equals_the_marker_that_separates_the_two_halves() -> None:
    # The search reads the longest border of "the client's input, a marker, the
    # chain's tail", and a marker some item's key can equal is a border allowed
    # to straddle the two halves -- a restatement claimed where none was made.
    # The degenerate shapes below are what a marker spelled as a value would
    # most plausibly collide with.
    degenerate: list[JsonValue] = [{}, {"content": []}, {"role": None, "content": []}, {"content": 7}, "not an item"]

    for item in [*_SAMPLE_ITEM_BY_TYPE.values(), *degenerate]:
        assert _relocation_comparison_key(item) != replay_safety_module._OVERLAP_SENTINEL


def _strings_in(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return _strings_in(list(value)) | _strings_in(list(value.values()))
    if isinstance(value, (frozenset, set, tuple, list)):
        return {name for entry in value for name in _strings_in(entry)}
    return set()


def _key_builder_closure(module_functions: dict[str, ast.FunctionDef]) -> set[str]:
    """Every module function the key builder reaches, however many hops away.

    Following the calls rather than naming the functions is the point: the same
    reading that stopped at an item's top level would let a helper one call away
    read whatever it liked while the proof above it stayed green.
    """

    reached: set[str] = set()
    pending = ["_relocation_comparison_key"]
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        pending.extend(
            child.func.id
            for child in ast.walk(module_functions[name])
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id in module_functions
        )
    return reached


def _key_builder_field_names() -> set[str]:
    """Every field or type name the key builder can read, literal or via a constant it names.

    Its prose names the fields it promises to ignore, so a grep over the source as
    written matches the explanation rather than the code; and a name read out of a
    module-level set is as much a field the key reads as one spelled inline.
    """

    parsed = ast.parse(pathlib.Path(inspect.getfile(replay_safety_module)).read_text(encoding="utf-8"))
    module_functions = {node.name: node for node in parsed.body if isinstance(node, ast.FunctionDef)}
    reached = _key_builder_closure(module_functions)
    # Every identity helper the module has is reached from the one entry point,
    # so none of them can become a side door the proof does not walk into.
    assert {name for name in module_functions if name.startswith("_relocation_")} <= reached
    names: set[str] = set()
    for name in reached:
        node = module_functions[name]
        node.body = node.body[1:] if ast.get_docstring(node) is not None else node.body
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                names.add(child.value)
            elif isinstance(child, ast.Name):
                names |= _strings_in(getattr(replay_safety_module, child.id, None))
    return names


def test_the_key_builder_names_no_field_outside_the_identity_set() -> None:
    # The behavioural rows above pass for a subtractive key too, as long as its
    # list happens to be complete today. This is the structural half: the code
    # that builds a key cannot name a bookkeeping field at all, at any depth, so
    # there is no list left to be incomplete.
    names = _key_builder_field_names()

    assert {
        "arguments",
        "call_id",
        "content",
        "diff",
        "file_data",
        "file_id",
        "file_url",
        "image_url",
        "input",
        "name",
        "operation",
        "output",
        "patch",
        "path",
        "refusal",
        "role",
        "text",
        "tools",
    } <= names
    assert not names & {field for _, field in _IGNORED_FIELD_POSITIONS}


_RESTATABLE_MESSAGE_BOOKKEEPING = sorted(
    field for position, field in _IGNORED_FIELD_POSITIONS if position == "assistant message" and field != "id"
)


@pytest.mark.parametrize("field", _RESTATABLE_MESSAGE_BOOKKEEPING)
def test_an_unenumerated_legal_field_does_not_double_the_conversation(field: str) -> None:
    # The public entry point, on the four-turn thread, with a field the wire
    # permits on every restated answer and the key has never been told about.
    # Reading it as a difference collapses the overlap to zero and dispatches
    # all four exchanges twice -- on the fenced lane at the cost of the one
    # relocation that operation will ever get.
    restated = [
        {**cast(dict[str, JsonValue], item), field: _LEGAL_VALUES[field][0]}
        if cast(dict[str, JsonValue], item).get("role") == "assistant"
        else item
        for item in _RESTATED_THREAD
    ]
    client_input: list[JsonValue] = [*restated, _wire_user_item("q5")]

    rebuilt = _rebuild(_CHAIN, client_input)

    assert rebuilt is not None
    assert rebuilt["input"] == client_input


def test_the_owner_item_id_is_the_one_wire_field_a_dispatched_body_may_not_carry() -> None:
    # ``id`` is why the row above cannot simply run over every field the wire
    # admits: an item carrying one fails the strict predicate wherever it sits,
    # so a restatement that keeps the owner's ids relocates nowhere at all. The
    # key ignoring it is pinned by the unit row instead of here.
    restated: list[JsonValue] = [{**cast(dict[str, JsonValue], item), "id": "msg_owner"} for item in _RESTATED_THREAD]

    assert _rebuild(_CHAIN, [*restated, _wire_user_item("q5")]) is None


_OVERLAP_ALPHABET: dict[str, JsonValue] = {
    "a": _wire_user_item("a"),
    "b": _replayed_assistant_item("b"),
    "c": _wire_user_item("c"),
    "x": _wire_user_item("x"),
}


def _overlap_items(spelling: str) -> list[JsonValue]:
    return [_OVERLAP_ALPHABET[letter] for letter in spelling]


@pytest.mark.parametrize(
    ("accumulated", "incoming", "expected"),
    [
        # The overlap is the whole accumulation: the client restated everything
        # the walk holds, and every item of it repeats.
        ("aa", "aa", 2),
        ("aaa", "aaaa", 3),
        # A restatement whose own prefix recurs inside it. The search meets a
        # mismatch after a partial match and has to fall back to the next
        # shorter border rather than starting over or giving up.
        ("aab", "aaba", 3),
        ("aaab", "aab", 3),
        ("abab", "ababc", 4),
        ("aabaa", "aabaab", 5),
        ("abaab", "aabab", 3),
        ("aba", "abab", 3),
        # Partial matches that are not the answer: one item, then none.
        ("ba", "aa", 1),
        ("xaa", "aax", 2),
    ],
)
def test_the_overlap_search_reads_a_restatement_that_repeats_inside_itself(
    accumulated: str,
    incoming: str,
    expected: int,
) -> None:
    # Repeats are where a prefix function goes wrong while staying green on
    # fixtures whose items all differ: the fallback loop, the separator between
    # the two halves and the window each only matter here.
    assert _tail_overlap_length(*_paired(_overlap_items(accumulated), _overlap_items(incoming))) == expected


def _shapes(alphabet: Sequence[JsonValue], max_length: int) -> list[tuple[int, ...]]:
    """Every arrangement of ``alphabet`` up to ``max_length`` items, as index tuples."""

    return [
        tuple((index // len(alphabet) ** position) % len(alphabet) for position in range(length))
        for length in range(1, max_length + 1)
        for index in range(len(alphabet) ** length)
    ]


def _assert_overlap_agrees_with_the_rule(alphabet: Sequence[JsonValue], *, max_length: int) -> int:
    """Check the module's search against the quadratic rule on every shape; return the pairs read."""

    module_keys = [_relocation_comparison_key(item) for item in alphabet]
    rule_keys = [_rule_identity(item) for item in alphabet]
    shapes = _shapes(alphabet, max_length)
    paired = {shape: [(alphabet[index], module_keys[index]) for index in shape] for shape in shapes}
    ruled = {shape: [rule_keys[index] for index in shape] for shape in shapes}

    for accumulated in shapes:
        for incoming in shapes:
            assert _tail_overlap_length(paired[accumulated], paired[incoming]) == _restated_tail_from_keys(
                ruled[accumulated], ruled[incoming]
            ), f"{accumulated} / {incoming}"
    return len(shapes) ** 2


_CHECKED_ITEM_ALPHABET: tuple[JsonValue, ...] = tuple(item for _, item in sorted(_SAMPLE_ITEM_BY_TYPE.items()))


def test_the_overlap_search_agrees_with_the_rule_on_every_short_shape() -> None:
    # Exhaustive over repeats, borders and self-repeating prefixes up to five
    # items, each answer checked against the quadratic reading of the
    # requirement. A plausible-looking edit to the search survives any fixture
    # whose items happen to differ; it does not survive all of these. Run over
    # each pair of admitted item types in turn rather than over text messages
    # alone: fed one item type, the checker agrees with the module about the
    # other ten by never being shown them.
    alphabets = zip(_CHECKED_ITEM_ALPHABET, [*_CHECKED_ITEM_ALPHABET[1:], _CHECKED_ITEM_ALPHABET[0]], strict=True)
    for left, right in alphabets:
        _assert_overlap_agrees_with_the_rule((left, right), max_length=5)


def test_the_overlap_search_agrees_with_the_rule_on_every_item_type_it_may_meet() -> None:
    # The same agreement across all eleven admitted types at once, at the two
    # lengths that fit exhaustively. Depth is the row above; this is breadth --
    # every ordered pair of item types, including a call beside the output that
    # answers it and a bare part beside the message holding the same content.
    _assert_overlap_agrees_with_the_rule(_CHECKED_ITEM_ALPHABET, max_length=2)


def test_a_later_fold_measures_its_overlap_against_what_the_earlier_one_left() -> None:
    # Two truncating folds in a row. The chain's second turn restates the first,
    # and the client then restates the second -- so the client's overlap is
    # measured against an accumulation the previous fold already shortened. An
    # accumulation that keeps a stale view of what it holds finds the overlap at
    # the wrong offset and drops items the walk never restated.
    transcript = (
        _transcript_turn([_wire_user_item("q1")], [_assistant_item("a1", item_id="msg_1")]),
        _transcript_turn(
            [_wire_user_item("q1"), _replayed_assistant_item("a1"), _wire_user_item("q2")],
            [_assistant_item("a2", item_id="msg_2")],
            response_id="resp_2",
            parent_response_id="resp_1",
        ),
    )
    client_input: list[JsonValue] = [*_restated_exchange(2), _wire_user_item("q3")]
    accumulated: list[JsonValue] = [
        _wire_user_item("q1"),
        _replayed_assistant_item("a1"),
        _wire_user_item("q2"),
        _replayed_assistant_item("a2"),
    ]

    rebuilt = _rebuild(transcript, client_input)

    assert rebuilt is not None
    dispatched = cast(list[JsonValue], rebuilt["input"])
    assert dispatched == [*accumulated[:2], *client_input]
    _assert_join_invariants(dispatched, accumulated=accumulated, client_input=client_input)


def _assert_join_invariants(
    dispatched: list[JsonValue],
    *,
    accumulated: Sequence[JsonValue],
    client_input: Sequence[JsonValue],
) -> None:
    """Every client item is the body's final contiguous slice; the join left no turn in it twice.

    "No turn twice" cannot be spelled as "the retained chain shares no tail with
    the client's input": a chain ending ``[x, x]`` against an input opening
    ``[x, y]`` legitimately retains one ``x``, and a helper that forbids it
    rejects a correct join. What the requirement actually asks is that the join
    shortens the chain by the whole of what the client restated and by nothing
    else, so that is what is asserted.
    """

    boundary = len(dispatched) - len(client_input)
    assert dispatched[boundary:] == list(client_input)
    assert dispatched[:boundary] == list(accumulated[:boundary])
    assert boundary == len(accumulated) - _restated_tail_length(accumulated, client_input)


@pytest.mark.parametrize(
    ("transcript", "accumulated", "client_input", "expected_chain_prefix"),
    [row[1:] for row in _JOIN_TABLE],
    ids=[row[0] for row in _JOIN_TABLE],
)
def test_the_join_drops_the_restated_tail_from_the_chain_and_keeps_the_clients_turn_whole(
    transcript: tuple[object, ...],
    accumulated: list[JsonValue],
    client_input: list[JsonValue],
    expected_chain_prefix: list[JsonValue],
) -> None:
    rebuilt = _rebuild(transcript, client_input)

    assert rebuilt is not None
    dispatched = cast(list[JsonValue], rebuilt["input"])
    assert dispatched == [*expected_chain_prefix, *client_input]
    _assert_join_invariants(dispatched, accumulated=accumulated, client_input=client_input)


def test_a_full_resend_leaves_the_chain_with_nothing_to_contribute() -> None:
    # The client resent the whole thread rather than a delta, and anchored it as
    # well -- a shape this proxy already verifies. The overlap is the entire
    # accumulation, so what is dispatched is the client's own request and the
    # verdict must not describe it as a rebuilt transcript.
    transcript = (
        _transcript_turn(
            [_user_item("first")],
            [_reasoning_item("rs_1"), _assistant_item("answer", item_id="msg_1")],
        ),
    )
    client_turn = [_user_item("first"), _replayed_assistant_item("answer"), _user_item("second")]

    rebuilt = _rebuild_result(transcript, client_turn)

    assert rebuilt is not None
    assert rebuilt.payload["input"] == client_turn
    assert rebuilt.carries_durable_items is False


def test_an_input_restating_the_answer_alone_replaces_the_chains_copy_of_it() -> None:
    # The client restated the prior answer and nothing before it. That answer is
    # the tail of what the walk accumulated, so the chain's copy goes and the
    # client's stands in its place -- the same conversation, in the client's own
    # words, with the question that produced it still in front of it.
    transcript = (
        _transcript_turn(
            [_user_item("first")],
            [_reasoning_item("rs_1"), _assistant_item("answer", item_id="msg_1")],
        ),
    )
    client_turn = [_replayed_assistant_item("answer"), _user_item("second")]

    rebuilt = _rebuild_result(transcript, client_turn)

    assert rebuilt is not None
    assert rebuilt.payload["input"] == [_user_item("first"), *client_turn]
    assert rebuilt.carries_durable_items is True


def test_a_chain_turn_carrying_state_no_account_can_serve_still_fails_closed() -> None:
    # The unsettled search bookkeeping is in the oldest turn, and the turn after
    # it restates the tail rather than the whole thread, so the join leaves it
    # standing. It reaches the strict predicate, which is where a body no
    # replacement account can serve has to stop.
    unsettled_search: JsonValue = {
        "type": "tool_search_output",
        "call_id": "call_search",
        "execution": "client",
        "status": "failed",
        "tools": [],
    }
    transcript = (
        _transcript_turn(
            [unsettled_search, _assistant_item("answer", item_id="msg_0"), _user_item("first")],
            [_assistant_item("first answer", item_id="msg_1")],
        ),
        _transcript_turn(
            [_user_item("first"), _assistant_item("first answer", item_id="msg_1"), _user_item("second")],
            [_assistant_item("second answer", item_id="msg_2")],
            response_id="resp_2",
            parent_response_id="resp_1",
        ),
    )

    assert _rebuild(transcript, [_user_item("third")]) is None


def test_a_chain_turn_that_restated_the_conversation_replaces_what_it_restates() -> None:
    # The thread's second recorded turn carries everything before it as well as
    # its own new message, which is what a client sends the first time this
    # proxy sees a conversation it did not start. Concatenating it onto the
    # history it restates is how that thread's opening turn ends up twice in
    # every relocation it ever takes.
    transcript = (
        _transcript_turn(
            [_user_item("first")],
            [_reasoning_item("rs_1"), _assistant_item("answer", item_id="msg_1")],
        ),
        _transcript_turn(
            [
                _user_item("first"),
                _reasoning_item("rs_1"),
                _assistant_item("answer", item_id="msg_1"),
                _user_item("second"),
            ],
            [_assistant_item("second answer", item_id="msg_2")],
            response_id="resp_2",
            parent_response_id="resp_1",
        ),
    )

    client_turn = [_user_item("third")]
    accumulated: list[JsonValue] = [
        _user_item("first"),
        _replayed_assistant_item("answer"),
        _user_item("second"),
        _replayed_assistant_item("second answer"),
    ]
    rebuilt = _rebuild(transcript, client_turn)

    assert rebuilt is not None
    dispatched = cast(list[JsonValue], rebuilt["input"])
    assert dispatched == [*accumulated, *client_turn]
    assert dispatched.count(_user_item("first")) == 1
    _assert_join_invariants(dispatched, accumulated=accumulated, client_input=client_turn)


def test_durable_rebuild_keeps_a_repeated_message_the_chain_already_holds() -> None:
    transcript = (_transcript_turn([_user_item("ping")], [_assistant_item("pong", item_id="msg_1")]),)

    rebuilt = _rebuild(transcript, [_user_item("ping")])

    assert rebuilt is not None
    assert rebuilt["input"] == [_user_item("ping"), _replayed_assistant_item("pong"), _user_item("ping")]


def test_durable_rebuild_keeps_a_new_turn_whose_first_message_repeats_the_thread_opening() -> None:
    # "go" here is the user's own new message; it only happens to read the same
    # as the message that opened the thread. Deleting it would send the
    # replacement account a conversation the user never wrote.
    transcript = (
        _transcript_turn([_user_item("go")], [_assistant_item("ok", item_id="msg_1")]),
        _transcript_turn(
            [_user_item("next")],
            [_assistant_item("done", item_id="msg_2")],
            response_id="resp_2",
            parent_response_id="resp_1",
        ),
    )

    rebuilt = _rebuild(transcript, [_user_item("go"), _user_item("again")])

    assert rebuilt is not None
    assert rebuilt["input"] == [
        _user_item("go"),
        _replayed_assistant_item("ok"),
        _user_item("next"),
        _replayed_assistant_item("done"),
        _user_item("go"),
        _user_item("again"),
    ]


@pytest.mark.parametrize("client_turn_length", [1, 2, 3])
def test_durable_rebuild_keeps_a_repeated_opening_whatever_the_new_turn_is_shaped_like(
    client_turn_length: int,
) -> None:
    # The same coincidence at three lengths: a boundary rule anchored anywhere
    # deletes the repeat for some of them and not others, which is how the
    # deletion stayed invisible.
    transcript = (
        _transcript_turn([_user_item("go")], [_assistant_item("ok", item_id="msg_1")]),
        _transcript_turn(
            [_user_item("next")],
            [_assistant_item("done", item_id="msg_2")],
            response_id="resp_2",
            parent_response_id="resp_1",
        ),
    )
    client_turn = [_user_item("go"), _user_item("again"), _user_item("and again")][:client_turn_length]

    rebuilt = _rebuild(transcript, client_turn)

    assert rebuilt is not None
    assert cast(list[JsonValue], rebuilt["input"])[4:] == client_turn


@pytest.mark.parametrize(
    "delta_item",
    [
        # Response-owned bookkeeping the projection would silently remove.
        _reasoning_item("rs_owner"),
        {"type": "tool_search_output", "call_id": "call_s", "execution": "client", "status": "completed", "tools": []},
        # An id minted by the owner account, which the projection would strip.
        {"id": "msg_owner", **_user_item("next question")},
    ],
)
def test_a_client_turn_is_never_edited_to_make_the_rebuild_succeed(delta_item: JsonValue) -> None:
    # Projecting the client's own turn is the same harm as trimming it: the
    # items it removes are messages that never reach the replacement account
    # while the rebuild still reports success. Refuse instead.
    transcript = (_transcript_turn([_user_item("first")], [_assistant_item("answer", item_id="msg_1")]),)

    assert _rebuild(transcript, [delta_item, _user_item("next question")]) is None


def test_durable_rebuild_falls_back_to_item_events_when_an_incomplete_terminal_omits_output() -> None:
    transcript = (
        _transcript_turn(
            [_user_item("first")],
            [_reasoning_item("rs_1"), _assistant_item("partial", item_id="msg_1")],
            terminal_type="response.incomplete",
            include_terminal_output=False,
            include_item_events=True,
        ),
    )

    rebuilt = _rebuild(transcript, [_user_item("second")])

    assert rebuilt is not None
    assert rebuilt["input"] == [_user_item("first"), _replayed_assistant_item("partial"), _user_item("second")]


def test_durable_rebuild_prefers_the_terminal_output_over_accumulated_items() -> None:
    transcript = (
        _transcript_turn(
            [_user_item("first")],
            [_assistant_item("answer", item_id="msg_1")],
            include_item_events=True,
        ),
    )

    rebuilt = _rebuild(transcript, [_user_item("second")])

    assert rebuilt is not None
    assert rebuilt["input"] == [_user_item("first"), _replayed_assistant_item("answer"), _user_item("second")]


def test_durable_rebuild_prefers_the_terminal_output_when_an_item_frame_never_reached_the_spool() -> None:
    # The two sources are not interchangeable. An item frame the bridge
    # suppressed downstream (a duplicated tool call, a deferred reasoning
    # prelude) never reaches the spool, while the terminal response.output still
    # lists the item -- so the accumulated frames can be a strict subset and are
    # a fallback for terminals that omit output, never a competing answer.
    commentary = _assistant_item("thinking out loud", item_id="msg_1", phase="commentary")
    final_answer = _assistant_item("answer", item_id="msg_2", phase="final_answer")
    transcript = (
        _TranscriptTurn(
            operation=_TranscriptOperation(
                request_text=_request_frame({"model": "gpt-5.4", "input": [_user_item("first")]}),
            ),
            events=(
                _sse_block({"type": "response.output_item.done", "item": final_answer}),
                _sse_block(
                    {
                        "type": "response.completed",
                        "response": {"id": "resp_1", "output": [commentary, final_answer]},
                    }
                ),
            ),
        ),
    )

    rebuilt = _rebuild(transcript, [_user_item("second")])

    assert rebuilt is not None
    assert rebuilt["input"] == [
        _user_item("first"),
        _replayed_assistant_item("thinking out loud", phase="commentary"),
        _replayed_assistant_item("answer", phase="final_answer"),
        _user_item("second"),
    ]


def test_durable_rebuild_refuses_a_turn_with_no_terminal_marker() -> None:
    transcript = (
        _TranscriptTurn(
            operation=_TranscriptOperation(
                request_text=_request_frame({"model": "gpt-5.4", "input": [_user_item("first")]}),
            ),
            events=(
                _sse_block({"type": "response.output_item.done", "item": _assistant_item("answer", item_id="msg_1")}),
            ),
        ),
    )

    assert _rebuild(transcript, [_user_item("second")]) is None


@pytest.mark.parametrize("failure_terminal", ["response.failed", "error"])
def test_a_terminal_reporting_failure_does_not_settle_a_turn(failure_terminal: str) -> None:
    # The failure frame carries a response.output the model had already
    # streamed. Counting it as settled would put the abandoned half of a failed
    # turn into the rebuilt conversation as the assistant's reply -- a turn that
    # was never answered, reading as answered, on a body every later predicate
    # accepts.
    transcript = (
        _transcript_turn(
            [_user_item("first")],
            [_assistant_item("half an answer", item_id="msg_1")],
            terminal_type=failure_terminal,
            include_item_events=True,
        ),
    )

    assert _terminal_response_output_items(cast(_TranscriptTurn, transcript[0]).events) is None
    assert _rebuild(transcript, [_user_item("second")]) is None


def test_the_first_terminal_in_a_spool_decides_the_turns_answer() -> None:
    # Two attempts in one spool: the abandoned one settled, then a replacement
    # streamed its own frames. Reading past the first terminal concatenates the
    # second attempt onto the first and presents the joined text as one answer.
    first_attempt = _assistant_item("first attempt", item_id="msg_1")
    second_attempt = _assistant_item("second attempt", item_id="msg_2")
    events = (
        _sse_block({"type": "response.output_item.done", "item": first_attempt}),
        _sse_block({"type": "response.completed", "response": {"id": "resp_1"}}),
        _sse_block({"type": "response.output_item.done", "item": second_attempt}),
        _sse_block(
            {"type": "response.completed", "response": {"id": "resp_1", "output": [first_attempt, second_attempt]}}
        ),
    )

    assert _terminal_response_output_items(events) == [first_attempt]


@pytest.mark.parametrize(
    "answerless_events",
    [
        # A terminal that carries neither a response object nor item frames.
        ({"type": "response.completed"},),
        # A terminal whose response holds no output at all.
        ({"type": "response.completed", "response": {"id": "resp_1"}},),
        # A terminal that explicitly reports an empty output.
        ({"type": "response.completed", "response": {"id": "resp_1", "output": []}},),
    ],
)
def test_a_terminal_that_carries_no_answer_settles_nothing(answerless_events: tuple[dict[str, JsonValue], ...]) -> None:
    # An empty answer is not an answer. Returning the empty accumulator instead
    # makes the turn look settled and drops it from the rebuilt conversation
    # while the rebuild still succeeds.
    events = tuple(_sse_block(payload) for payload in answerless_events)

    assert _terminal_response_output_items(events) is None
    assert (
        _rebuild(
            (
                _TranscriptTurn(
                    operation=_TranscriptOperation(
                        request_text=_request_frame({"model": "gpt-5.4", "input": [_user_item("first")]}),
                    ),
                    events=events,
                ),
            ),
            [_user_item("second")],
        )
        is None
    )


def test_a_terminal_with_no_response_object_fails_closed_over_the_accumulator() -> None:
    # The item frames arrived, then a terminal that carries no response object
    # at all. Falling back to the accumulated items would report a turn whose
    # settlement nothing describes as answered.
    events = (
        _sse_block({"type": "response.output_item.done", "item": _assistant_item("answer", item_id="msg_1")}),
        _sse_block({"type": "response.completed", "id": "resp_1"}),
    )

    assert _terminal_response_output_items(events) is None
    assert (
        _rebuild(
            (
                _TranscriptTurn(
                    operation=_TranscriptOperation(
                        request_text=_request_frame({"model": "gpt-5.4", "input": [_user_item("first")]}),
                    ),
                    events=events,
                ),
            ),
            [_user_item("second")],
        )
        is None
    )


@pytest.mark.parametrize("end_of_attempt", ["response.failed", "error"])
def test_an_explicitly_abandoned_attempt_leaves_nothing_in_the_next_ones_answer(end_of_attempt: str) -> None:
    # One spool, two attempts. The first streamed an item and then failed; the
    # replacement streamed its own and completed without a response.output.
    # Carrying the accumulator across the failure reports the abandoned
    # generation as part of this turn's reply.
    events = (
        _sse_block({"type": "response.output_item.done", "item": _assistant_item("abandoned", item_id="msg_1")}),
        _sse_block({"type": end_of_attempt, "response": {"id": "resp_1"}}),
        _sse_block({"type": "response.output_item.done", "item": _assistant_item("replacement", item_id="msg_2")}),
        _sse_block({"type": "response.completed", "response": {"id": "resp_1"}}),
    )

    assert _terminal_response_output_items(events) == [_assistant_item("replacement", item_id="msg_2")]


def test_an_attempt_that_died_mid_stream_leaves_nothing_in_the_next_ones_answer() -> None:
    # The realistic shape: the abandoned attempt reaches no terminal at all, it
    # simply stops, and the only marker that a new one began is the
    # replacement's own ``response.created``.
    events = (
        _sse_block({"type": "response.created", "response": {"id": "resp_1"}}),
        _sse_block({"type": "response.output_item.done", "item": _assistant_item("abandoned", item_id="msg_1")}),
        _sse_block({"type": "response.created", "response": {"id": "resp_1"}}),
        _sse_block({"type": "response.output_item.done", "item": _assistant_item("replacement", item_id="msg_2")}),
        _sse_block({"type": "response.completed", "response": {"id": "resp_1"}}),
    )

    assert _terminal_response_output_items(events) == [_assistant_item("replacement", item_id="msg_2")]


def test_an_abandoned_attempt_whose_replacement_answers_nothing_settles_nothing() -> None:
    events = (
        _sse_block({"type": "response.output_item.done", "item": _assistant_item("abandoned", item_id="msg_1")}),
        _sse_block({"type": "response.failed", "response": {"id": "resp_1"}}),
        _sse_block({"type": "response.completed", "response": {"id": "resp_1"}}),
    )

    assert _terminal_response_output_items(events) is None


@pytest.mark.parametrize(
    "unreadable_events",
    [
        None,
        7,
        {"type": "response.completed"},
        # A single string is a Sequence of characters, and iterating it yields
        # no event at all rather than the one frame it looks like.
        'event: response.completed\ndata: {"type":"response.completed"}\n\n',
        b"bytes",
    ],
)
def test_a_turn_whose_events_are_not_a_sequence_of_frames_settles_nothing(unreadable_events: object) -> None:
    assert _terminal_response_output_items(unreadable_events) is None
    assert (
        _rebuild(
            (
                _TranscriptTurn(
                    operation=_TranscriptOperation(
                        request_text=_request_frame({"model": "gpt-5.4", "input": [_user_item("first")]}),
                    ),
                    events=cast(Any, unreadable_events),
                ),
            ),
            [_user_item("second")],
        )
        is None
    )


def test_only_the_item_done_frame_contributes_an_item_to_the_accumulator() -> None:
    # ``response.output_item.added`` carries the same ``item`` key and announces
    # a skeleton the model has not written yet; the ``.done`` frame that follows
    # carries the finished one. Accumulating on the presence of ``item`` rather
    # than on the frame's own type puts both in the answer, so every item in
    # every turn arrives twice -- once empty.
    added = {"id": "msg_1", "type": "message", "role": "assistant", "status": "in_progress", "content": []}
    events = (
        _sse_block({"type": "response.output_item.added", "item": cast(JsonValue, added)}),
        _sse_block({"type": "response.output_item.done", "item": _assistant_item("answer", item_id="msg_1")}),
        _sse_block({"type": "response.completed", "response": {"id": "resp_1"}}),
    )

    assert _terminal_response_output_items(events) == [_assistant_item("answer", item_id="msg_1")]


def test_a_frame_with_no_readable_payload_is_skipped_rather_than_failing_the_turn() -> None:
    # A spool holds whatever crossed the wire, and comment keepalives and the
    # ``[DONE]`` sentinel both decode to nothing. Failing the turn on one refuses
    # a rebuild the material fully supports, on the turns most likely to have
    # needed a keepalive: the slow ones.
    events = (
        ": keepalive\n\n",
        _sse_block({"type": "response.output_item.done", "item": _assistant_item("answer", item_id="msg_1")}),
        "data: [DONE]\n\n",
        _sse_block({"type": "response.completed", "response": {"id": "resp_1"}}),
    )

    assert _terminal_response_output_items(events) == [_assistant_item("answer", item_id="msg_1")]
    rebuilt = _rebuild(
        (
            _TranscriptTurn(
                operation=_TranscriptOperation(
                    request_text=_request_frame({"model": "gpt-5.4", "input": [_user_item("first")]}),
                ),
                events=events,
            ),
        ),
        [_user_item("second")],
    )

    assert rebuilt is not None
    assert rebuilt["input"] == [_user_item("first"), _replayed_assistant_item("answer"), _user_item("second")]


def test_a_malformed_item_frame_does_not_become_a_rebuilt_item() -> None:
    # The frame's ``item`` is not an object, so there is no item in it. Letting
    # it through puts a scalar where an input item belongs and costs the turn a
    # rebuild the rest of its material fully supports.
    events = (
        _sse_block({"type": "response.output_item.done", "item": cast(JsonValue, "not an item")}),
        _sse_block({"type": "response.output_item.done", "item": _assistant_item("answer", item_id="msg_1")}),
        _sse_block({"type": "response.completed", "response": {"id": "resp_1"}}),
    )

    assert _terminal_response_output_items(events) == [_assistant_item("answer", item_id="msg_1")]
    rebuilt = _rebuild(
        (
            _TranscriptTurn(
                operation=_TranscriptOperation(
                    request_text=_request_frame({"model": "gpt-5.4", "input": [_user_item("first")]}),
                ),
                events=events,
            ),
        ),
        [_user_item("second")],
    )

    assert rebuilt is not None
    assert rebuilt["input"] == [_user_item("first"), _replayed_assistant_item("answer"), _user_item("second")]


@pytest.mark.parametrize("wrong_anchor", ["resp_1", "resp_other", "resp_2 "])
def test_durable_rebuild_refuses_a_chain_that_is_not_this_anchors_history(wrong_anchor: str) -> None:
    # Parent links prove only that these turns follow one another. This chain is
    # internally perfect and belongs to somebody else's conversation, or stops
    # short of the turn the client actually anchored on.
    transcript = _linked_pair()

    assert _rebuild(transcript, [_user_item("third")], anchor_response_id=wrong_anchor) is None
    assert _rebuild(transcript, [_user_item("third")], anchor_response_id="resp_2") is not None


@pytest.mark.parametrize("no_anchor", ["", "   "])
def test_a_request_naming_no_prior_response_is_not_joined_to_a_chain(no_anchor: str) -> None:
    # The dispatched frame carries no anchor, so the replacement account sees
    # exactly what the owner account would have seen: this body and nothing
    # else. A chain loaded for some other turn is not this request's history,
    # and prepending it would answer a conversation the client never named.
    client_turn = [_user_item("third")]

    rebuilt = _rebuild_result(_linked_pair(), client_turn, anchor_response_id=no_anchor)

    assert rebuilt is not None
    assert rebuilt.payload["input"] == client_turn
    assert rebuilt.carries_durable_items is False


def test_durable_rebuild_refuses_a_turn_whose_event_spool_never_finished() -> None:
    # An unfinished spool holds a fragment of an answer, not an answer, and the
    # fragment reads exactly like a complete one once it is in the chain.
    settled, unsettled = _linked_pair()
    partial = _TranscriptTurn(
        operation=_TranscriptOperation(
            request_text=unsettled.operation.request_text,
            response_id="resp_2",
            parent_response_id="resp_1",
            event_spool_complete=False,
        ),
        events=unsettled.events,
    )

    assert _rebuild((settled, partial), [_user_item("third")]) is None


# Characters ``str.splitlines`` breaks on that the SSE wire format does not, and
# that a JSON serializer leaves unescaped because they sit above U+001F: NEL,
# LINE SEPARATOR and PARAGRAPH SEPARATOR. These are the ones that actually reach
# the spool inside a model's answer -- an unescaped U+2028 in a response the
# proxy relays is the shape tests/unit/test_proxy_api_responses_contract.py
# records -- so one of them must not cost the turn its terminal. Vertical tab,
# form feed and the file/group/record separators break ``splitlines`` too, but a
# JSON string escapes them, so they never appear raw in a ``data:`` field.
_NON_SSE_LINE_BREAKS = ("\x85", " ", " ")


@pytest.mark.parametrize("line_break", _NON_SSE_LINE_BREAKS)
def test_durable_rebuild_reads_a_terminal_whose_answer_contains_a_non_sse_line_break(line_break: str) -> None:
    answer = f"café{line_break}done"
    transcript = (
        _TranscriptTurn(
            operation=_TranscriptOperation(
                request_text=_request_frame({"model": "gpt-5.4", "input": [_user_item("first")]}),
            ),
            # The spool stores the upstream frame as written, so a non-ASCII
            # answer keeps its UTF-8 and its separators unescaped.
            events=(
                "event: response.completed\ndata: "
                + json.dumps(
                    {
                        "type": "response.completed",
                        "response": {"id": "resp_1", "output": [_assistant_item(answer, item_id="msg_1")]},
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n\n",
            ),
        ),
    )

    rebuilt = _rebuild(transcript, [_user_item("second")])

    assert rebuilt is not None
    assert rebuilt["input"] == [_user_item("first"), _replayed_assistant_item(answer), _user_item("second")]


@pytest.mark.parametrize("terminator", ["\n", "\r\n", "\r"])
def test_terminal_output_joins_a_multi_line_data_field_with_line_feeds(terminator: str) -> None:
    payload = json.dumps(
        {"type": "response.completed", "response": {"id": "resp_1", "output": [_assistant_item("answer")]}},
        separators=(",", ":"),
    )
    split_at = payload.index('"response"')
    block = (
        f"event: response.completed{terminator}"
        f"data: {payload[:split_at]}{terminator}"
        f"data: {payload[split_at:]}{terminator}{terminator}"
    )

    # Only a parser that rejoins the field can decode it, and only one that
    # honours CR-only framing can find the second line at all.
    assert _terminal_response_output_items((block,)) == [_assistant_item("answer")]


def test_durable_rebuild_drops_the_empty_annotation_list_every_output_text_part_has() -> None:
    # The spooled answer carries the field; the dispatched body must not, because
    # the strict predicate refuses it and widening the predicate would change
    # what the already-shipped client-resend admission accepts.
    transcript = (_transcript_turn([_user_item("first")], [_assistant_item("answer", item_id="msg_1")]),)

    rebuilt = _rebuild(transcript, [_user_item("second")])

    assert rebuilt is not None
    answer = cast(Any, cast(list[JsonValue], rebuilt["input"])[1])
    assert answer["content"] == [{"type": "output_text", "text": "answer"}]


_POPULATED_ANNOTATIONS: list[dict[str, JsonValue]] = [
    {"type": "file_citation", "file_id": "file_owner_scoped", "filename": "notes.md", "index": 3},
    {"type": "container_file_citation", "container_id": "cntr_owner", "file_id": "cfile_owner"},
    # No identifier the account-scoped walk would recognize: only the
    # annotation check itself stands between this and a relocated body.
    {"type": "url_citation", "url": "https://example.test/a", "title": "a", "start_index": 0, "end_index": 1},
]


@pytest.mark.parametrize("annotations", [[], *[[annotation] for annotation in _POPULATED_ANNOTATIONS]])
def test_the_annotations_on_a_recorded_answer_do_not_change_its_key(annotations: list[JsonValue]) -> None:
    # The chain's copy of an answer has ``annotations`` removed so the dispatched
    # body can pass the strict predicate, which does not admit the field on a
    # content part at all; the client's copy is joined as written. Neither side's
    # key reads it, so the two name the same answer whichever of them carries it
    # -- the stripping decides what may be dispatched, not what restates what.
    assert _relocation_comparison_key(_assistant_item("answer", annotations=annotations)) == (
        _relocation_comparison_key(_replayed_assistant_item("answer"))
    )


@pytest.mark.parametrize("annotation", _POPULATED_ANNOTATIONS)
def test_durable_rebuild_refuses_a_populated_annotation_list(annotation: dict[str, JsonValue]) -> None:
    transcript = (
        _transcript_turn(
            [_user_item("first")],
            [_assistant_item("answer", item_id="msg_1", annotations=[annotation])],
        ),
    )

    assert _rebuild(transcript, [_user_item("second")]) is None


def test_a_populated_annotation_list_fails_closed_wherever_it_sits_in_the_answer() -> None:
    # The refusal must not depend on the citation being the whole of that turn's
    # answer. Skipping the part instead of refusing leaves an answer standing
    # with one of its messages gone, and every remaining item still projects
    # cleanly, so the rebuild reports success on a conversation with a hole.
    transcript = (
        _transcript_turn(
            [_user_item("first")],
            [
                _assistant_item("cited", item_id="msg_1", annotations=[_POPULATED_ANNOTATIONS[0]]),
                _assistant_item("answer", item_id="msg_2"),
            ],
        ),
    )

    assert _rebuild(transcript, [_user_item("second")]) is None


@pytest.mark.parametrize("annotations", [[], *[[annotation] for annotation in _POPULATED_ANNOTATIONS]])
@pytest.mark.parametrize("role", ["assistant", "user", "developer", "system"])
def test_the_shipped_fresh_replay_admission_refuses_annotations_in_every_position(
    role: str,
    annotations: list[JsonValue],
) -> None:
    # The content-part field set is shared with the transparent fresh-replay
    # admission that is already live, in every message position and standing
    # alone as an input item. What the rebuild needed it took from the items it
    # assembles itself, so none of these moved.
    part_type = "output_text" if role == "assistant" else "input_text"
    part: dict[str, JsonValue] = {"type": part_type, "text": "a line", "annotations": cast(JsonValue, annotations)}
    message: JsonValue = {"type": "message", "role": role, "status": "completed", "content": [part]}

    assert responses_payload_is_account_neutral_fresh_replay({"input": [message]}) is False
    assert responses_payload_is_account_neutral_fresh_replay({"input": [part]}) is False
    assert (
        responses_input_suffix_retains_prior_output(
            [_user_item("old question"), message, _user_item("next question")],
            stored_count=1,
        )
        is False
    )


def test_durable_rebuild_strips_a_response_owned_developer_id_from_the_chain() -> None:
    # The Responses-Lite bundle and the developer instruction the owner account
    # minted an id for. The rebuild is the dispatched body, so nothing in it may
    # keep a response-owned id -- the strict predicate refuses every one.
    lite_bundle: dict[str, JsonValue] = {
        "type": "additional_tools",
        "role": "developer",
        "tools": [{"type": "custom", "name": "shell"}],
    }
    stored_developer: dict[str, JsonValue] = {
        "type": "message",
        "id": "msg_stored_response_owned",
        "role": "developer",
        "content": [{"type": "input_text", "text": "stored response-owned control"}],
    }
    transcript = (
        _transcript_turn(
            [lite_bundle, stored_developer, _user_item("first")],
            [_assistant_item("answer", item_id="msg_1")],
        ),
    )

    rebuilt = _rebuild(transcript, [_user_item("second")])

    assert rebuilt is not None
    assert rebuilt["input"] == [
        lite_bundle,
        {key: value for key, value in stored_developer.items() if key != "id"},
        _user_item("first"),
        _replayed_assistant_item("answer"),
        _user_item("second"),
    ]


@pytest.mark.parametrize(
    "incomplete_turn",
    [
        # What a chain the repository could not complete hands back: nothing at
        # all for a broken or cyclic parent walk, or a turn whose durable row is
        # missing its request body or its event spool.
        _TranscriptTurn(operation=_TranscriptOperation(request_text=None), events=()),
        _TranscriptTurn(
            operation=_TranscriptOperation(request_text=_request_frame({"input": [_user_item("first")]})),
            events=(),
        ),
        _TranscriptTurn(operation=_TranscriptOperation(request_text="{not json"), events=()),
        _TranscriptTurn(operation=_TranscriptOperation(request_text="[]"), events=()),
        _TranscriptTurn(
            operation=_TranscriptOperation(request_text=json.dumps({"type": "response.cancel", "input": []})),
            events=(),
        ),
    ],
)
def test_durable_rebuild_refuses_an_incomplete_chain(incomplete_turn: object) -> None:
    assert _rebuild((incomplete_turn,), [_user_item("second")]) is None


@pytest.mark.parametrize(
    "unusable_input",
    [
        # A body that parses as a request frame but names no turn to replay.
        None,
        7,
        {},
        [],
        "",
        "   ",
    ],
)
def test_durable_rebuild_refuses_a_stored_turn_with_no_usable_input(unusable_input: JsonValue) -> None:
    stored_body: dict[str, JsonValue] = {"model": "gpt-5.4"}
    if unusable_input is not None:
        stored_body["input"] = unusable_input
    operation = _TranscriptOperation(request_text=_request_frame(stored_body))
    turn = _TranscriptTurn(
        operation=operation,
        events=(
            _sse_block(
                {
                    "type": "response.completed",
                    "response": {"id": "resp_1", "output": [_assistant_item("answer", item_id="msg_1")]},
                }
            ),
        ),
    )

    # Named at the source as well as at the outcome: a turn with nothing to
    # replay must read as absent, not as a turn that contributed nothing. The
    # two are indistinguishable in the assembled body, and only one of them is
    # a conversation whose middle is missing.
    assert _transcript_turn_input_items(operation) is None
    assert _rebuild((turn,), [_user_item("second")]) is None


def test_a_turn_whose_whole_question_projects_away_fails_closed() -> None:
    # The mirror of an answer that projects away: the stored row names a turn,
    # and after projection the answer stands with nothing that asked for it.
    transcript = (_transcript_turn([_reasoning_item("rs_owner")], [_assistant_item("answer", item_id="msg_1")]),)

    assert _rebuild(transcript, [_user_item("second")]) is None


@pytest.mark.parametrize("empty_transcript", [(), []])
def test_durable_rebuild_refuses_an_empty_chain_for_an_anchored_turn(empty_transcript: Sequence[object]) -> None:
    # The anchor names state the replacement account will not have and the
    # client is not supplying, and an empty chain cannot end on it.
    assert _rebuild(empty_transcript, [_user_item("second")], anchor_response_id="resp_1") is None


@pytest.mark.parametrize("empty_transcript", [(), []])
def test_an_empty_chain_dispatches_an_unanchored_request_as_the_client_wrote_it(
    empty_transcript: Sequence[object],
) -> None:
    client_turn = [_user_item("second")]

    rebuilt = _rebuild_result(empty_transcript, client_turn, anchor_response_id="")

    assert rebuilt is not None
    assert rebuilt.payload["input"] == client_turn
    assert rebuilt.carries_durable_items is False


@pytest.mark.parametrize(
    "current_input",
    [
        # A scalar input is the new prompt alone and cannot stand in for the
        # conversation the dropped anchor represented.
        "second",
        None,
        7,
        [],
        {},
    ],
)
def test_durable_rebuild_refuses_an_unusable_current_request(current_input: JsonValue) -> None:
    transcript = (_transcript_turn([_user_item("first")], [_assistant_item("answer", item_id="msg_1")]),)
    current_payload: dict[str, JsonValue] = {"model": "gpt-5.4", "previous_response_id": "resp_1"}
    if current_input is not None:
        current_payload["input"] = current_input

    assert (
        project_durable_transcript_for_account_neutral_fresh_replay(
            transcript,
            anchor_response_id="resp_1",
            current_payload=current_payload,
        )
        is None
    )


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        # The session bridge spools the exact upstream frame, envelope and all.
        (json.dumps({"type": "response.create", "model": "gpt-5.4"}), {"model": "gpt-5.4"}),
        # The direct paths store the body alone.
        (json.dumps({"model": "gpt-5.4"}), {"model": "gpt-5.4"}),
        # Any other envelope is a frame whose body is not a turn to replay.
        (json.dumps({"type": "response.cancel", "model": "gpt-5.4"}), None),
        (json.dumps({"type": "response.created", "model": "gpt-5.4"}), None),
        ("{not json", None),
        ("[]", None),
        ('"a string"', None),
    ],
)
def test_a_request_frame_yields_a_body_only_for_the_create_envelope(
    request_text: str,
    expected: dict[str, JsonValue] | None,
) -> None:
    assert responses_request_frame_payload(request_text) == expected


@pytest.mark.parametrize(
    ("current_input", "extra_fields"),
    [
        # A tool declaration no other account can serve.
        ([_user_item("second")], {"tools": [{"type": "code_interpreter"}]}),
        # Account-scoped state the projection does not remove.
        ([{"type": "input_file", "file_id": "file_owner"}], {}),
        ([_user_item("second")], {"conversation": "conv_owner"}),
    ],
)
def test_durable_rebuild_refuses_a_body_the_strict_predicate_declines(
    current_input: Sequence[JsonValue],
    extra_fields: dict[str, JsonValue],
) -> None:
    transcript = (_transcript_turn([_user_item("first")], [_assistant_item("answer", item_id="msg_1")]),)

    assert _rebuild(transcript, current_input, **extra_fields) is None


def test_durable_rebuild_refuses_a_chain_whose_final_turn_left_a_tool_call_unsettled() -> None:
    # The owner account asked for a tool and the turn ended there. Handing the
    # replacement account a call nobody holds the output for asks it to continue
    # work it cannot see.
    transcript = (
        _transcript_turn(
            [_user_item("first")],
            [{"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "lookup", "arguments": "{}"}],
        ),
    )

    assert _rebuild(transcript, [_user_item("second")]) is None


def test_durable_rebuild_refuses_account_owned_state_that_survives_projection() -> None:
    transcript = (
        _transcript_turn(
            [_user_item("first")],
            [{"id": "ir_1", "type": "item_reference"}, _assistant_item("answer", item_id="msg_1")],
        ),
    )

    assert _rebuild(transcript, [_user_item("second")]) is None


def test_durable_rebuild_result_satisfies_the_strict_predicate() -> None:
    transcript = (_transcript_turn([_user_item("first")], [_assistant_item("answer", item_id="msg_1")]),)

    rebuilt = _rebuild(transcript, [_user_item("second")])

    assert rebuilt is not None
    assert responses_payload_is_account_neutral_fresh_replay(rebuilt) is True


def _chain_of_turns(turn_count: int) -> tuple[object, ...]:
    return tuple(
        _transcript_turn(
            [_user_item(f"question {index}")],
            [_assistant_item(f"answer {index}", item_id=f"msg_{index}")],
            response_id=f"resp_{index}",
            parent_response_id=None if index == 0 else f"resp_{index - 1}",
        )
        for index in range(turn_count)
    )


def test_the_relocation_transcript_caps_are_the_values_the_change_states() -> None:
    # Written out rather than derived: every other cap assertion builds its
    # fixture from these constants, so without a literal here the caps only
    # ever assert themselves and could be widened without a failing test.
    assert RELOCATION_TRANSCRIPT_MAX_TURNS == 128
    assert RELOCATION_TRANSCRIPT_MAX_BYTES == 8 * 1024 * 1024
    assert RELOCATION_TRANSCRIPT_MAX_ITEMS == 32768


def test_durable_rebuild_accepts_a_chain_at_the_turn_cap() -> None:
    rebuilt = _rebuild(_chain_of_turns(128), [_user_item("next")])

    assert rebuilt is not None
    assert len(cast(list[JsonValue], rebuilt["input"])) == 128 * 2 + 1


def test_durable_rebuild_refuses_a_chain_past_the_turn_cap() -> None:
    assert _rebuild(_chain_of_turns(129), [_user_item("next")]) is None


@pytest.mark.parametrize(("max_turns", "expected_rebuild"), [(2, True), (1, False)])
def test_durable_rebuild_honours_an_explicit_turn_cap(max_turns: int, expected_rebuild: bool) -> None:
    rebuilt = _rebuild(_chain_of_turns(2), [_user_item("next")], max_turns=max_turns)

    assert (rebuilt is not None) is expected_rebuild


def _chain_of_sized_turns(turn_count: int, *, turn_bytes: int) -> tuple[object, ...]:
    """A chain whose turns are each well inside the byte cap on their own."""

    filler = "x" * (turn_bytes // 2)
    return tuple(
        _transcript_turn(
            [_user_item(f"{filler} {index}")],
            [_assistant_item(f"{filler} answer {index}", item_id=f"msg_{index}")],
            response_id=f"resp_{index}",
            parent_response_id=None if index == 0 else f"resp_{index - 1}",
        )
        for index in range(turn_count)
    )


def test_the_byte_bound_is_measured_across_the_whole_transcript() -> None:
    # Three turns of roughly 3 MiB. Each one is comfortably inside the 8 MiB
    # cap; together they are not. A cap re-read per turn admits all three -- and
    # at the 128-turn cap the same reading admits a gigabyte.
    transcript = _chain_of_sized_turns(3, turn_bytes=3 * 1024 * 1024)
    for turn in transcript:
        stored = cast(_TranscriptTurn, turn)
        turn_bytes = len(stored.operation.request_text or "") + sum(len(event) for event in stored.events)

        assert turn_bytes < RELOCATION_TRANSCRIPT_MAX_BYTES

    assert _rebuild(transcript, [_user_item("next")]) is None


def test_durable_rebuild_admits_a_whole_transcript_inside_the_byte_bound() -> None:
    assert _rebuild(_chain_of_sized_turns(2, turn_bytes=3 * 1024 * 1024), [_user_item("next")]) is not None


def test_durable_rebuild_refuses_material_past_an_explicit_byte_cap() -> None:
    transcript = _chain_of_turns(1)
    stored_bytes = len(cast(_TranscriptTurn, transcript[0]).operation.request_text or "")

    assert _rebuild(transcript, [_user_item("next")], max_bytes=stored_bytes) is None


def test_durable_rebuild_admits_material_inside_an_explicit_byte_cap() -> None:
    transcript = _chain_of_turns(1)
    turn = cast(_TranscriptTurn, transcript[0])
    turn_bytes = len(turn.operation.request_text or "") + sum(len(event) for event in turn.events)

    assert _rebuild(transcript, [_user_item("next")], max_bytes=turn_bytes) is not None


def test_durable_rebuild_refuses_material_one_byte_past_the_cap() -> None:
    # The exact-fit case above and this one are the pair that pin the boundary:
    # with only the first, a bound that admits one byte too many reads as
    # correct, and a transcript is admitted at every cap the proxy sets.
    transcript = _chain_of_turns(1)
    turn = cast(_TranscriptTurn, transcript[0])
    turn_bytes = len(turn.operation.request_text or "") + sum(len(event) for event in turn.events)

    assert _rebuild(transcript, [_user_item("next")], max_bytes=turn_bytes - 1) is None


def _chain_of_item_heavy_turns(turn_count: int, *, items_per_turn: int) -> tuple[object, ...]:
    """A chain whose turns each carry many items while staying inside every cap on their own."""

    return tuple(
        _transcript_turn(
            [_user_item(f"q{index}-{position}") for position in range(items_per_turn)],
            [_assistant_item(f"answer {index}", item_id=f"msg_{index}")],
            response_id=f"resp_{index}",
            parent_response_id=None if index == 0 else f"resp_{index - 1}",
        )
        for index in range(turn_count)
    )


def test_the_item_bound_is_measured_across_the_whole_transcript() -> None:
    # Three turns of 12,000 items. Each one is comfortably inside the 32,768
    # bound and the whole transcript is inside the byte bound, because a legal
    # item is about fifty bytes; together they pass the item bound. A bound
    # re-read per turn admits all three, and at the turn cap the same reading
    # admits a million and a half items -- which is what canonicalization, the
    # strict predicate and the join each scale with.
    transcript = _chain_of_item_heavy_turns(3, items_per_turn=12_000)
    for turn in transcript:
        stored = cast(_TranscriptTurn, turn)
        turn_items = len(cast(Any, json.loads(stored.operation.request_text or "{}"))["input"]) + 1

        assert turn_items < RELOCATION_TRANSCRIPT_MAX_ITEMS

    assert _rebuild(transcript, [_user_item("next")]) is None


def test_durable_rebuild_admits_a_transcript_inside_an_explicit_item_cap() -> None:
    # Two stored questions, one answer, one client message: four items exactly.
    transcript = _chain_of_item_heavy_turns(1, items_per_turn=2)

    assert _rebuild(transcript, [_user_item("next")], max_items=4) is not None


@pytest.mark.parametrize("max_items", [3, 2])
def test_durable_rebuild_refuses_a_transcript_past_an_explicit_item_cap(max_items: int) -> None:
    # Three at the client's own items, two inside the chain walk: the same
    # budget, spent in the two places items arrive from.
    transcript = _chain_of_item_heavy_turns(1, items_per_turn=2)

    assert _rebuild(transcript, [_user_item("next")], max_items=max_items) is None


def test_the_clients_own_items_count_against_the_item_bound() -> None:
    # The client's input costs the same canonicalization, join and strict-predicate
    # work the chain's does, and it is the one input nobody rebuilt -- so a bound
    # that exempts it leaves the work it exists to bound unbounded.
    transcript = _chain_of_item_heavy_turns(1, items_per_turn=2)
    client_input = [_user_item("next"), _user_item("and one more")]

    assert _rebuild(transcript, client_input, max_items=5) is not None
    assert _rebuild(transcript, client_input, max_items=4) is None


def _utf8_turn(prompt: str, answer: str) -> _TranscriptTurn:
    """A turn spooled the way upstream writes it: UTF-8 on the wire, not ``\\u`` escapes."""

    return _TranscriptTurn(
        operation=_TranscriptOperation(
            request_text=json.dumps(
                {"type": "response.create", "model": "gpt-5.4", "input": [_user_item(prompt)]},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        ),
        events=(
            "event: response.completed\ndata: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {"id": "resp_1", "output": [_assistant_item(answer, item_id="msg_1")]},
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n\n",
        ),
    )


@pytest.mark.parametrize(
    ("prompt", "answer"),
    [
        # One side non-ASCII at a time: with both, a bound that counts the
        # request in characters and the spool in bytes still refuses, and the
        # half that is wrong goes unseen.
        ("처음 질문입니다", "answer"),
        ("first question", "첫 번째 답변입니다"),
    ],
)
def test_the_byte_bound_counts_the_bytes_the_spool_holds_not_the_characters(prompt: str, answer: str) -> None:
    # A cap measured in characters is the cap it claims to be only for ASCII. A
    # Korean or Japanese thread is three bytes per character, so the same
    # transcript passes a bound it exceeds threefold -- and the 8 MiB ceiling
    # that keeps a rebuild off the event loop becomes 24 MiB for the
    # conversations most likely to be long.
    transcript = (_utf8_turn(prompt, answer),)
    stored = cast(_TranscriptTurn, transcript[0])
    material = [stored.operation.request_text or "", *stored.events]
    characters = sum(len(text) for text in material)
    stored_bytes = sum(len(text.encode("utf-8")) for text in material)

    assert characters < stored_bytes
    assert _rebuild(transcript, [_user_item("next")], max_bytes=characters) is None
    assert _rebuild(transcript, [_user_item("next")], max_bytes=stored_bytes) is not None


@pytest.mark.parametrize("scalar_input", ["just this", "  padded  "])
def test_an_unanchored_scalar_request_is_dispatched_as_the_client_wrote_it(scalar_input: str) -> None:
    # The documented passthrough, and the one place a scalar survives as a
    # scalar. Nothing precedes it, so there is nothing it has to become an item
    # to sit behind -- and rewriting it anyway would edit the one body this
    # rebuild promises to dispatch unaltered.
    rebuilt = project_durable_transcript_for_account_neutral_fresh_replay(
        (),
        anchor_response_id=None,
        current_payload={"model": "gpt-5.4", "input": scalar_input},
    )

    assert rebuilt is not None
    assert rebuilt.payload["input"] == scalar_input
    assert rebuilt.carries_durable_items is False


@pytest.mark.parametrize("empty_input", [[], "", "   "])
def test_a_request_with_nothing_to_send_is_not_relocatable(empty_input: JsonValue) -> None:
    # An empty body is account-neutral by every structural reading, so it
    # relocates unless it is refused here -- and the dispatch can only come back
    # as an invalid request. On the definitive lane that wastes an attempt; on
    # the ambiguous one it spends the one-shot budget for that operation's whole
    # retention, so the conversation's real recovery is gone before it is asked
    # for.
    assert (
        project_durable_transcript_for_account_neutral_fresh_replay(
            (),
            anchor_response_id=None,
            current_payload={"model": "gpt-5.4", "input": empty_input},
        )
        is None
    )


def test_a_turns_answer_is_appended_whole_even_when_it_opens_on_the_accumulated_tail() -> None:
    # The join belongs to stored requests: a request can restate the
    # conversation, an answer cannot, because an answer is what its turn
    # produced. So an answer whose first item reads like the accumulated tail is
    # a coincidence, and shortening on it deletes the earlier answer the thread
    # already holds. Here the second turn's stored request ends on the first
    # answer and the model opens its reply by repeating that answer -- both
    # copies belong in the conversation.
    transcript = (
        _transcript_turn([_user_item("first")], [_assistant_item("same answer", item_id="msg_1")]),
        _transcript_turn(
            [_user_item("first"), _replayed_assistant_item("same answer")],
            [
                _assistant_item("same answer", item_id="msg_2"),
                _assistant_item("and more", item_id="msg_3"),
            ],
            response_id="resp_2",
            parent_response_id="resp_1",
        ),
    )

    rebuilt = _rebuild(transcript, [_user_item("third")])

    assert rebuilt is not None
    assert rebuilt["input"] == [
        _user_item("first"),
        _replayed_assistant_item("same answer"),
        _replayed_assistant_item("same answer"),
        _replayed_assistant_item("and more"),
        _user_item("third"),
    ]


class _CountedItems(Sequence[tuple[JsonValue, object]]):
    """An item sequence that records how many elements the search asked it for.

    Reads are the honest measure of this search's cost. Comparisons are not: the
    nested scan this replaced failed at the first element of nearly every
    candidate length, so it compared O(n) times while copying a slice per length
    -- quadratic work that a comparison counter cannot see and a clock reports
    differently on every machine.
    """

    def __init__(self, entries: list[tuple[JsonValue, object]]) -> None:
        self._entries = entries
        self.reads = 0

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, index: Any) -> Any:
        if isinstance(index, slice):
            self.reads += len(range(*index.indices(len(self._entries))))
        else:
            self.reads += 1
        return self._entries[index]


def _overlap_search_reads(item_count: int) -> int:
    """Elements read finding the overlap between a chain and a resend that restates none of it.

    The doubling case, which is where the search does its most work: nothing
    matches, so no candidate length can end it early.
    """

    accumulated, incoming = (
        _CountedItems(entries)
        for entries in _paired(
            [_user_item(f"q{index}") for index in range(item_count)],
            [_user_item(f"other {index}") for index in range(item_count)],
        )
    )

    assert _tail_overlap_length(accumulated, incoming) == 0
    return accumulated.reads + incoming.reads


def test_the_overlap_search_is_linear_in_the_number_of_items() -> None:
    # The caps bound turns and bytes; neither bounds items, and a nested scan is
    # quadratic in exactly what they do not bound. At the item count an in-cap
    # transcript reaches, that is minutes of blocking CPU on a single-worker
    # event loop, inside the failover path whose whole purpose is to be quicker
    # than losing the conversation.
    small_reads = _overlap_search_reads(500)
    large_reads = _overlap_search_reads(2000)

    # Fourfold the items, so linear work grows fourfold and quadratic work
    # sixteenfold; the allowance between them separates the two without being
    # tight. The absolute bound is the same statement without the arithmetic:
    # each side is read a bounded number of times, not once per candidate.
    assert large_reads <= 6 * small_reads
    assert large_reads <= 4 * 2 * 2000


def test_one_fold_reads_only_as_much_of_the_accumulation_as_could_overlap() -> None:
    # Every fold is handed the whole conversation so far and a turn that can
    # restate at most its own length of it, so a fold that scans the whole
    # accumulation is linear per fold and quadratic over the walk. That is the
    # same cost the nested scan had, reached from the other direction, and no cap
    # bounds it: the caps count turns and bytes, and this counts items.
    accumulated, incoming = (
        _CountedItems(entries)
        for entries in _paired(
            [_user_item(f"q{index}") for index in range(2000)],
            [_user_item("one new message")],
        )
    )

    assert _tail_overlap_length(accumulated, incoming) == 0
    assert accumulated.reads <= 4 * len(incoming)


def test_the_join_canonicalizes_each_item_once_rather_than_once_per_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A linear search over keys rebuilt at every step is quadratic again, and in
    # the expensive direction: canonicalizing is a JSON serialization per item,
    # far dearer than the comparison it feeds.
    canonical = replay_safety_module._relocation_comparison_key
    keys_built = 0

    def _counted(item: JsonValue) -> str:
        nonlocal keys_built
        keys_built += 1
        return canonical(item)

    questions: list[JsonValue] = [_user_item(f"q{index}") for index in range(500)]
    transcript = (_transcript_turn(questions, [_assistant_item("answer", item_id="msg_1")]),)
    client_input: list[JsonValue] = [*questions, _replayed_assistant_item("answer"), _user_item("next")]
    monkeypatch.setattr(replay_safety_module, "_relocation_comparison_key", _counted)

    rebuilt = _rebuild(transcript, client_input)

    assert rebuilt is not None
    assert rebuilt["input"] == client_input
    # The chain's questions and its answer, then the client's copy of both plus
    # the new question: one key each, and not one more.
    assert keys_built == (500 + 1) + (500 + 2)
