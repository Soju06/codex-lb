from __future__ import annotations

import pytest

from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.modules.proxy.continuity import (
    is_http_bridge_account_neutral_replay,
    make_http_bridge_account_neutral_replay_key,
)
from app.modules.proxy.replay_safety import (
    project_responses_input_for_account_neutral_fresh_replay,
    responses_input_suffix_retains_prior_output,
    responses_payload_is_account_neutral_fresh_replay,
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
