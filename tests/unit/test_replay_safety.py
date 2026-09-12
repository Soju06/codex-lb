from __future__ import annotations

import json
import pathlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import pytest

from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.modules.proxy.continuity import (
    is_http_bridge_account_neutral_replay,
    make_http_bridge_account_neutral_replay_key,
)
from app.modules.proxy.replay_safety import (
    RELOCATION_TRANSCRIPT_MAX_BYTES,
    RELOCATION_TRANSCRIPT_MAX_TURNS,
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


def _rebuild(
    transcript: Sequence[object],
    current_input: Sequence[JsonValue],
    *,
    anchor_response_id: str | None = None,
    max_turns: int = RELOCATION_TRANSCRIPT_MAX_TURNS,
    max_bytes: int = RELOCATION_TRANSCRIPT_MAX_BYTES,
    **extra: JsonValue,
) -> dict[str, JsonValue] | None:
    anchor = _newest_response_id(transcript) if anchor_response_id is None else anchor_response_id
    return project_durable_transcript_for_account_neutral_fresh_replay(
        transcript,
        anchor_response_id=anchor,
        current_payload=_current_payload(current_input, anchor, **extra),
        max_turns=max_turns,
        max_bytes=max_bytes,
    )


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
    "broken_chain",
    [
        # The oldest turn names a parent the chain does not contain, so the
        # conversation is missing its opening.
        (
            _transcript_turn(
                [_user_item("second")],
                [_assistant_item("second answer", item_id="msg_2")],
                response_id="resp_2",
                parent_response_id="resp_1",
            ),
        ),
        # Two turns that do not link to each other at all.
        (
            _transcript_turn([_user_item("first")], [_assistant_item("a", item_id="msg_1")]),
            _transcript_turn(
                [_user_item("second")],
                [_assistant_item("b", item_id="msg_2")],
                response_id="resp_2",
                parent_response_id="resp_other",
            ),
        ),
        # A turn that names itself as its own parent.
        (
            _transcript_turn(
                [_user_item("first")],
                [_assistant_item("a", item_id="msg_1")],
                parent_response_id="resp_1",
            ),
        ),
        # A turn the repository could not have produced: no response id to link.
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
    ],
)
def test_durable_rebuild_refuses_a_chain_whose_parent_links_do_not_hold(
    broken_chain: tuple[object, ...],
) -> None:
    assert _rebuild(broken_chain, [_user_item("third")]) is None


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


def test_a_self_contained_history_supersedes_the_chain_it_restates() -> None:
    # The client resent the whole thread rather than a delta, and anchored it as
    # well -- a shape this proxy already verifies. The resend is the
    # conversation as of its own position, so it replaces what the walk had
    # accumulated instead of being appended to it.
    transcript = (
        _transcript_turn(
            [_user_item("first")],
            [_reasoning_item("rs_1"), _assistant_item("answer", item_id="msg_1")],
        ),
    )
    client_turn = [_user_item("first"), _replayed_assistant_item("answer"), _user_item("second")]

    rebuilt = _rebuild(transcript, client_turn)

    assert rebuilt is not None
    assert rebuilt["input"] == client_turn


def test_an_input_restating_the_answer_alone_refuses_rather_than_repeating_it() -> None:
    # The client restated the prior answer and nothing before it. Where that
    # restatement ends is recoverable only by matching its content against the
    # chain, so the rebuild refuses rather than carrying the answer twice.
    transcript = (
        _transcript_turn(
            [_user_item("first")],
            [_reasoning_item("rs_1"), _assistant_item("answer", item_id="msg_1")],
        ),
    )

    assert _rebuild(transcript, [_replayed_assistant_item("answer"), _user_item("second")]) is None


def test_a_restatement_that_is_not_self_contained_refuses_even_when_a_later_turn_replaces_it() -> None:
    # The unsettled search bookkeeping keeps this turn from standing on its own,
    # so it is not the conversation as of its own position and must not be read
    # as one. The turn after it supersedes and would carry the corruption out of
    # the assembled body, leaving nothing downstream able to see that a recorded
    # turn was silently discarded.
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


def test_a_chain_turn_that_restated_the_conversation_supersedes_at_its_own_position() -> None:
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

    rebuilt = _rebuild(transcript, [_user_item("third")])

    assert rebuilt is not None
    assert rebuilt["input"] == [
        _user_item("first"),
        _replayed_assistant_item("answer"),
        _user_item("second"),
        _replayed_assistant_item("second answer"),
        _user_item("third"),
    ]


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


@pytest.mark.parametrize("wrong_anchor", ["resp_1", "resp_other", "", "resp_2 "])
def test_durable_rebuild_refuses_a_chain_that_is_not_this_anchors_history(wrong_anchor: str) -> None:
    # Parent links prove only that these turns follow one another. This chain is
    # internally perfect and belongs to somebody else's conversation, or stops
    # short of the turn the client actually anchored on.
    transcript = _linked_pair()

    assert _rebuild(transcript, [_user_item("third")], anchor_response_id=wrong_anchor) is None
    assert _rebuild(transcript, [_user_item("third")], anchor_response_id="resp_2") is not None


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


@pytest.mark.parametrize("annotation", _POPULATED_ANNOTATIONS)
def test_durable_rebuild_refuses_a_populated_annotation_list(annotation: dict[str, JsonValue]) -> None:
    transcript = (
        _transcript_turn(
            [_user_item("first")],
            [_assistant_item("answer", item_id="msg_1", annotations=[annotation])],
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
def test_durable_rebuild_refuses_an_empty_chain(empty_transcript: Sequence[object]) -> None:
    assert _rebuild(empty_transcript, [_user_item("second")]) is None


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
