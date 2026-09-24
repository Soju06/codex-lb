from __future__ import annotations

from typing import cast

import pytest

from app.core.types import JsonValue
from app.modules.proxy.replay_safety import (
    project_responses_input_for_account_neutral_fresh_replay,
    responses_input_items_are_self_contained_fresh_replay,
    responses_payload_is_account_neutral_fresh_replay,
)

# Wire shapes copied from codex-rs: ``ResponseItem::ToolSearchOutput`` carries
# ``execution`` plus ``tools`` (serialized ``LoadableToolSpec`` values) and has
# no ``output`` field; ``tools/src/responses_api_tests.rs`` pins the namespace
# form with a deferred child tool.
_UPSTREAM_FUNCTION_TOOL: dict[str, JsonValue] = {
    "type": "function",
    "name": "read_file",
    "description": "Read a file from the workspace.",
    "strict": False,
    "parameters": {
        "type": "object",
        "properties": {"file_id": {"type": "string"}, "path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    },
}
_UPSTREAM_NAMESPACE_TOOL: dict[str, JsonValue] = {
    "type": "namespace",
    "name": "mcp__codex_apps__calendar",
    "description": "Plan events",
    "tools": [
        {
            "type": "function",
            "name": "create_event",
            "description": "Create a calendar event.",
            "strict": False,
            "defer_loading": True,
            "parameters": {"type": "object", "properties": {}},
        }
    ],
}
_UPSTREAM_TOOL_SEARCH_TOOLS: list[JsonValue] = [_UPSTREAM_FUNCTION_TOOL, _UPSTREAM_NAMESPACE_TOOL]


def _tool_search_pair(
    *,
    tools: JsonValue = None,
    output_extra: dict[str, JsonValue] | None = None,
    call_extra: dict[str, JsonValue] | None = None,
) -> list[JsonValue]:
    call: dict[str, JsonValue] = {
        "type": "tool_search_call",
        "call_id": "call_search",
        "arguments": {"query": "codex-lb"},
        "execution": "client",
        "status": "completed",
        **(call_extra or {}),
    }
    output: dict[str, JsonValue] = {
        "type": "tool_search_output",
        "call_id": "call_search",
        "execution": "client",
        "status": "completed",
        "tools": _UPSTREAM_TOOL_SEARCH_TOOLS if tools is None else tools,
        **(output_extra or {}),
    }
    return [call, output, {"role": "user", "content": [{"type": "input_text", "text": "continue"}]}]


def test_account_neutral_fresh_replay_accepts_upstream_tool_search_pair() -> None:
    input_items = _tool_search_pair()

    assert responses_input_items_are_self_contained_fresh_replay(input_items) is True
    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is True


def test_account_neutral_fresh_replay_accepts_tool_search_pair_without_execution_owner() -> None:
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
            "tools": [],
            "status": "completed",
        },
        {"role": "user", "content": [{"type": "input_text", "text": "continue"}]},
    ]

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is True


def test_account_neutral_fresh_replay_ignores_schema_property_named_like_a_file_reference() -> None:
    # ``parameters.properties.file_id`` is a JSON-schema property, not a live
    # account-scoped handle; the declaration rules skip ``parameters``.
    input_items = _tool_search_pair(tools=[_UPSTREAM_FUNCTION_TOOL])

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is True


def test_account_neutral_fresh_replay_accepts_custom_tool_inside_namespace() -> None:
    namespace: dict[str, JsonValue] = {
        "type": "namespace",
        "name": "grammar_tools",
        "description": "Grammar tools",
        "tools": [
            {
                "type": "custom",
                "name": "apply_edit",
                "description": "Apply an edit.",
                "defer_loading": True,
                "format": {"type": "grammar", "syntax": "lark", "definition": "start: WORD"},
            }
        ],
    }

    assert responses_payload_is_account_neutral_fresh_replay({"input": _tool_search_pair(tools=[namespace])}) is True


def test_account_neutral_fresh_replay_rejects_tool_search_string_output() -> None:
    output: dict[str, JsonValue] = {
        "type": "tool_search_output",
        "call_id": "call_search",
        "execution": "client",
        "output": "completed",
        "status": "completed",
    }
    input_items = [_tool_search_pair()[0], output]

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is False


def test_account_neutral_fresh_replay_rejects_tool_search_output_with_output_and_tools() -> None:
    input_items = _tool_search_pair(tools=[], output_extra={"output": "completed"})

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is False


def test_account_neutral_fresh_replay_rejects_tool_search_output_without_tools() -> None:
    output: dict[str, JsonValue] = {
        "type": "tool_search_output",
        "call_id": "call_search",
        "execution": "client",
        "status": "completed",
    }
    input_items = [_tool_search_pair()[0], output]

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is False


def test_account_neutral_fresh_replay_rejects_failed_tool_search_output() -> None:
    input_items = _tool_search_pair(tools=[], output_extra={"status": "failed"})

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is False


def test_account_neutral_fresh_replay_rejects_server_executed_tool_search_output() -> None:
    input_items = _tool_search_pair(output_extra={"execution": "server"})

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is False


def test_account_neutral_replay_projection_preserves_upstream_tool_search_pair_without_ids() -> None:
    call, output, followup = (cast(dict[str, JsonValue], item) for item in _tool_search_pair())
    input_items: list[JsonValue] = [
        {"role": "user", "content": "old question"},
        {**call, "id": "tsc_owner_a"},
        {**output, "id": "tso_owner_a"},
        followup,
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(input_items, stored_count=1)

    assert projection is not None
    assert projection.input_items == [{"role": "user", "content": "old question"}, call, output, followup]
    assert responses_payload_is_account_neutral_fresh_replay({"input": projection.input_items}) is True


def test_account_neutral_fresh_replay_rejects_tool_search_arguments_with_nested_compaction() -> None:
    input_items = _tool_search_pair(
        tools=[],
        call_extra={
            "arguments": {
                "query": "codex-lb",
                "state": {"type": "compaction", "encrypted_content": "enc_owner_scoped"},
            }
        },
    )

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is False


def test_account_neutral_fresh_replay_rejects_tool_search_arguments_with_mcp_state() -> None:
    input_items = _tool_search_pair(call_extra={"arguments": {"type": "mcp", "server_label": "private"}})

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is False


@pytest.mark.parametrize(
    "tool",
    [
        pytest.param({"type": "mcp", "server_label": "private"}, id="mcp-declaration"),
        pytest.param(
            {
                "type": "mcp",
                "server_label": "x",
                "server_url": "https://x",
                "headers": {"Authorization": "Bearer s"},
            },
            id="mcp-with-headers",
        ),
        pytest.param({"type": "code_interpreter", "container": "cntr_owner_a"}, id="code-interpreter-container"),
        pytest.param({"type": "web_search"}, id="web-search-not-loadable"),
        pytest.param({"name": "workspace-search"}, id="missing-type"),
        pytest.param("workspace-search", id="string-element"),
        pytest.param({**_UPSTREAM_FUNCTION_TOOL, "container": "cntr_owner_a"}, id="function-unknown-field"),
        pytest.param({**_UPSTREAM_FUNCTION_TOOL, "defer_loading": "yes"}, id="function-non-bool-defer-loading"),
        pytest.param({**_UPSTREAM_FUNCTION_TOOL, "name": " "}, id="function-blank-name"),
        pytest.param(
            {**_UPSTREAM_NAMESPACE_TOOL, "tools": [{"type": "mcp", "server_label": "private"}]},
            id="namespace-with-mcp-child",
        ),
        pytest.param({**_UPSTREAM_NAMESPACE_TOOL, "tools": [_UPSTREAM_NAMESPACE_TOOL]}, id="nested-namespace"),
        pytest.param({**_UPSTREAM_NAMESPACE_TOOL, "container": "cntr_owner_a"}, id="namespace-unknown-field"),
        pytest.param({**_UPSTREAM_NAMESPACE_TOOL, "tools": "read_file"}, id="namespace-tools-not-a-list"),
    ],
)
def test_account_neutral_fresh_replay_rejects_non_neutral_discovered_tool(tool: JsonValue) -> None:
    input_items = _tool_search_pair(tools=[_UPSTREAM_FUNCTION_TOOL, tool])

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is False


@pytest.mark.parametrize("tools", [{"type": "function", "name": "read_file"}, "read_file"])
def test_account_neutral_fresh_replay_rejects_malformed_tool_search_tools_field(tools: JsonValue) -> None:
    input_items = _tool_search_pair(tools=tools)

    assert responses_payload_is_account_neutral_fresh_replay({"input": input_items}) is False
