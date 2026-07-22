"""Fail-closed checks for moving a retained Responses request between accounts."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlsplit

from app.core.openai.requests import extract_input_file_ids
from app.core.types import JsonValue

_TOOL_CALL_TYPE_BY_OUTPUT_TYPE = {
    "function_call_output": "function_call",
    "custom_tool_call_output": "custom_tool_call",
    "apply_patch_call_output": "apply_patch_call",
}
_TOOL_CALL_TYPES = frozenset(_TOOL_CALL_TYPE_BY_OUTPUT_TYPE.values())
_ACCOUNT_NEUTRAL_REPLAY_OMITTED_ITEM_TYPES = frozenset(
    {"reasoning", "tool_search_call", "tool_search_output", "web_search_call"}
)
_INTERNAL_CHAT_MESSAGE_METADATA_FIELD = "internal_chat_message_metadata_passthrough"
_ACCOUNT_NEUTRAL_INTERNAL_CHAT_MESSAGE_METADATA_FIELDS = frozenset({"turn_id"})
_ACCOUNT_NEUTRAL_TOOL_TYPES = frozenset({"custom", "function", "web_search", "web_search_preview"})
_ACCOUNT_NEUTRAL_TOOL_DECLARATION_FIELDS = {
    "custom": frozenset({"description", "format", "name", "type"}),
    "function": frozenset({"description", "name", "parameters", "strict", "type"}),
    "web_search": frozenset({"filters", "search_context_size", "type", "user_location"}),
    "web_search_preview": frozenset({"filters", "search_context_size", "type", "user_location"}),
}
_ACCOUNT_NEUTRAL_TOOL_CHOICE_STRINGS = frozenset({"auto", "none", "required"})
_ACCOUNT_NEUTRAL_WEB_SEARCH_CONTEXT_SIZES = frozenset({"high", "low", "medium"})
_ACCOUNT_NEUTRAL_WEB_SEARCH_FILTER_FIELDS = frozenset({"allowed_domains"})
_ACCOUNT_NEUTRAL_WEB_SEARCH_LOCATION_FIELDS = frozenset({"city", "country", "region", "timezone", "type"})
_ACCOUNT_NEUTRAL_MESSAGE_ROLES = frozenset({"assistant", "developer", "system", "user"})
_ACCOUNT_NEUTRAL_INPUT_ITEM_TYPES = frozenset(
    {
        "additional_tools",
        "apply_patch_call",
        "apply_patch_call_output",
        "custom_tool_call",
        "custom_tool_call_output",
        "function_call",
        "function_call_output",
        "input_file",
        "input_image",
        "input_text",
        "message",
    }
)
_ACCOUNT_NEUTRAL_MESSAGE_CONTENT_TYPES = frozenset(
    {"input_file", "input_image", "input_text", "output_text", "refusal", "text"}
)
_ACCOUNT_NEUTRAL_MESSAGE_FIELDS = frozenset(
    {"content", "id", _INTERNAL_CHAT_MESSAGE_METADATA_FIELD, "phase", "role", "status", "type"}
)
_ACCOUNT_NEUTRAL_CONTENT_FIELDS = {
    "input_file": frozenset({"file_data", "file_id", "file_url", "filename", "type"}),
    "input_image": frozenset({"detail", "file_id", "image_url", "type"}),
    "input_text": frozenset({"text", "type"}),
    "output_text": frozenset({"text", "type"}),
    "refusal": frozenset({"refusal", "type"}),
    "text": frozenset({"text", "type"}),
}
_ACCOUNT_NEUTRAL_INPUT_ITEM_FIELDS = {
    "additional_tools": frozenset({"role", "tools", "type"}),
    "apply_patch_call": frozenset(
        {
            "call_id",
            "caller",
            "id",
            "input",
            _INTERNAL_CHAT_MESSAGE_METADATA_FIELD,
            "operation",
            "patch",
            "status",
            "type",
        }
    ),
    "apply_patch_call_output": frozenset(
        {"call_id", "caller", "id", _INTERNAL_CHAT_MESSAGE_METADATA_FIELD, "output", "status", "type"}
    ),
    "custom_tool_call": frozenset(
        {"call_id", "caller", "id", "input", _INTERNAL_CHAT_MESSAGE_METADATA_FIELD, "name", "status", "type"}
    ),
    "custom_tool_call_output": frozenset(
        {"call_id", "caller", "id", _INTERNAL_CHAT_MESSAGE_METADATA_FIELD, "output", "status", "type"}
    ),
    "function_call": frozenset(
        {"arguments", "call_id", "caller", "id", _INTERNAL_CHAT_MESSAGE_METADATA_FIELD, "name", "status", "type"}
    ),
    "function_call_output": frozenset(
        {"call_id", "caller", "id", _INTERNAL_CHAT_MESSAGE_METADATA_FIELD, "output", "status", "type"}
    ),
}
_ACCOUNT_NEUTRAL_ITEM_STATUSES = frozenset({"completed", "failed"})
_ACCOUNT_NEUTRAL_APPLY_PATCH_OPERATION_FIELDS = {
    "create_file": frozenset({"diff", "path", "type"}),
    "delete_file": frozenset({"path", "type"}),
    "update_file": frozenset({"diff", "path", "type"}),
}
_ACCOUNT_NEUTRAL_REASONING_CONFIG_FIELDS = frozenset({"effort", "summary"})
_ACCOUNT_NEUTRAL_CLIENT_METADATA_FIELDS = frozenset(
    {
        "ws_request_header_x_openai_internal_codex_responses_lite",
        "x-codex-installation-id",
        "x-codex-parent-thread-id",
        "x-codex-turn-metadata",
        "x-codex-window-id",
        "x-openai-subagent",
    }
)
_ACCOUNT_SCOPED_HOSTED_INPUT_TYPES = frozenset(
    {
        "code_interpreter_call",
        "computer_call",
        "computer_call_output",
        "file_search_call",
        "image_generation_call",
        "item_reference",
    }
)
_RESPONSES_PAYLOAD_FIELDS_WITH_DEDICATED_VALIDATION = frozenset(
    {
        "conversation",
        "client_metadata",
        "include",
        "input",
        "instructions",
        "metadata",
        "model",
        "parallel_tool_calls",
        "previous_response_id",
        "prompt",
        "prompt_cache_key",
        "reasoning",
        "service_tier",
        "store",
        "stream",
        "text",
        "tool_choice",
        "tools",
        "truncation",
    }
)


@dataclass(frozen=True, slots=True)
class AccountNeutralReplayProjection:
    input_items: list[JsonValue]
    stored_prefix_count: int


def project_responses_input_for_account_neutral_fresh_replay(
    input_items: list[JsonValue],
    *,
    stored_count: int,
) -> AccountNeutralReplayProjection | None:
    """Remove known response-owned bookkeeping after durable prefix proof."""

    if stored_count <= 0 or stored_count > len(input_items):
        return None

    projected_items: list[JsonValue] = []
    projected_stored_count = 0
    for index, item in enumerate(input_items):
        projected_item = _project_account_neutral_replay_item(item)
        if projected_item is not None:
            projected_items.append(projected_item)
        if index + 1 == stored_count:
            projected_stored_count = len(projected_items)

    return AccountNeutralReplayProjection(
        input_items=projected_items,
        stored_prefix_count=projected_stored_count,
    )


def _project_account_neutral_replay_item(item: JsonValue) -> JsonValue | None:
    if not isinstance(item, dict):
        return item

    item_type = item.get("type")
    if item_type == "reasoning" or (
        item_type in _ACCOUNT_NEUTRAL_REPLAY_OMITTED_ITEM_TYPES and item.get("status") == "completed"
    ):
        return None

    if "id" not in item:
        return item
    projected_item = dict(item)
    projected_item.pop("id")
    return projected_item


def responses_input_items_are_self_contained_fresh_replay(input_items: list[JsonValue]) -> bool:
    unsettled_call_ids_by_type: dict[str, set[str]] = {item_type: set() for item_type in _TOOL_CALL_TYPES}
    seen_call_ids: set[str] = set()
    settled_call_ids: set[str] = set()
    for item in input_items:
        if not isinstance(item, dict):
            return False
        if "type" in item and not _is_nonblank_string(item.get("type")):
            return False
        if item.get("id") not in (None, ""):
            return False
        if not _internal_chat_message_metadata_is_account_neutral(item.get(_INTERNAL_CHAT_MESSAGE_METADATA_FIELD)):
            return False
        item_type_value = item.get("type")
        item_type = item_type_value if isinstance(item_type_value, str) else None
        if not _input_item_has_only_known_fields(item, item_type):
            return False
        call_id_value = item.get("call_id")
        call_id = call_id_value if isinstance(call_id_value, str) and call_id_value else None
        if item_type in _TOOL_CALL_TYPES:
            if (
                call_id is None
                or call_id in seen_call_ids
                or not _caller_is_self_contained(item)
                or not _tool_call_is_self_contained(item_type, item)
            ):
                return False
            seen_call_ids.add(call_id)
            unsettled_call_ids_by_type[item_type].add(call_id)
            continue
        call_item_type = _TOOL_CALL_TYPE_BY_OUTPUT_TYPE.get(item_type or "")
        if call_item_type is not None:
            if (
                call_id is None
                or call_id not in unsettled_call_ids_by_type[call_item_type]
                or call_id in settled_call_ids
                or not _caller_is_self_contained(item)
                or not _tool_output_is_self_contained(item_type or "", item)
            ):
                return False
            unsettled_call_ids_by_type[call_item_type].remove(call_id)
            settled_call_ids.add(call_id)
    return all(not call_ids for call_ids in unsettled_call_ids_by_type.values())


def _internal_chat_message_metadata_is_account_neutral(value: JsonValue | None) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, dict)
        and set(value) == _ACCOUNT_NEUTRAL_INTERNAL_CHAT_MESSAGE_METADATA_FIELDS
        and _is_nonblank_string(value.get("turn_id"))
    )


def responses_input_suffix_retains_prior_output(
    input_items: list[JsonValue],
    *,
    stored_count: int,
) -> bool:
    """Prove that a stored input prefix is followed by prior output and new input."""

    if stored_count <= 0 or len(input_items) <= stored_count:
        return False
    prefix_state = _direct_tool_call_prefix_state(input_items[:stored_count])
    if prefix_state is None:
        return False
    pending_suffix_calls, seen_suffix_call_ids = prefix_state
    retained_output_seen = False
    fresh_followup_seen = False
    for item in input_items[stored_count:]:
        if not isinstance(item, dict):
            return False
        item_type_value = item.get("type")
        item_type = item_type_value if isinstance(item_type_value, str) else None
        if item_type in _TOOL_CALL_TYPES:
            if item.get("status") not in (None, "completed"):
                return False
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id or call_id in seen_suffix_call_ids:
                return False
            seen_suffix_call_ids.add(call_id)
            pending_suffix_calls.append((item_type, call_id))
            # Without a persisted output manifest, a call/output pair cannot
            # prove that an omitted parallel call was not part of the response.
            # Require a later completed assistant message as the turn boundary.
            retained_output_seen = False
            fresh_followup_seen = False
            continue
        call_type = _TOOL_CALL_TYPE_BY_OUTPUT_TYPE.get(item_type or "")
        if call_type is not None:
            if item.get("status") not in (None, "completed", "failed"):
                return False
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not pending_suffix_calls:
                return False
            if pending_suffix_calls[0] != (call_type, call_id):
                return False
            pending_suffix_calls.popleft()
            continue
        if item_type in (None, "message") and item.get("role") == "assistant":
            if pending_suffix_calls or not _is_retained_response_message(item):
                return False
            retained_output_seen = True
            fresh_followup_seen = False
            continue
        if _is_fresh_followup_input(item):
            if not retained_output_seen or pending_suffix_calls:
                return False
            fresh_followup_seen = True
            continue
        return False
    return retained_output_seen and fresh_followup_seen and not pending_suffix_calls


def _direct_tool_call_prefix_state(
    input_items: list[JsonValue],
) -> tuple[deque[tuple[str, str]], set[str]] | None:
    pending_calls: deque[tuple[str, str]] = deque()
    seen_call_ids: set[str] = set()
    for item in input_items:
        if not isinstance(item, dict):
            return None
        item_type_value = item.get("type")
        item_type = item_type_value if isinstance(item_type_value, str) else None
        if item_type in _TOOL_CALL_TYPES:
            if item.get("status") not in (None, "completed"):
                return None
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id or call_id in seen_call_ids:
                return None
            seen_call_ids.add(call_id)
            pending_calls.append((item_type, call_id))
            continue
        call_type = _TOOL_CALL_TYPE_BY_OUTPUT_TYPE.get(item_type or "")
        if call_type is not None:
            if item.get("status") not in (None, "completed", "failed"):
                return None
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not pending_calls:
                return None
            if pending_calls[0] != (call_type, call_id):
                return None
            pending_calls.popleft()
            continue
        if pending_calls and (
            (item_type in (None, "message") and item.get("role") in _ACCOUNT_NEUTRAL_MESSAGE_ROLES)
            or item_type in {"input_file", "input_image", "input_text"}
        ):
            return None
    return pending_calls, seen_call_ids


def _is_retained_response_message(item: Mapping[str, JsonValue]) -> bool:
    item_type = item.get("type")
    if (
        item_type not in (None, "message")
        or item.get("role") != "assistant"
        or item.get("status") not in (None, "completed")
    ):
        return False
    return _message_has_valid_account_neutral_content(item)


def _is_fresh_followup_input(item: Mapping[str, JsonValue]) -> bool:
    item_type = item.get("type")
    if item_type in {"input_file", "input_image", "input_text"}:
        return _input_content_part_is_self_contained(item, allow_output=False)
    return (
        item_type in (None, "message")
        and item.get("role") == "user"
        and _message_has_valid_account_neutral_content(item)
    )


def _tool_call_is_self_contained(item_type: str, item: Mapping[str, JsonValue]) -> bool:
    if item.get("status") not in (None, "completed"):
        return False
    if item_type == "function_call":
        return _is_nonblank_string(item.get("name")) and isinstance(item.get("arguments"), str)
    if item_type == "custom_tool_call":
        return _is_nonblank_string(item.get("name")) and isinstance(item.get("input"), str)
    operation = item.get("operation")
    patch = item.get("patch")
    input_value = item.get("input")
    if sum(field in item for field in ("operation", "patch", "input")) != 1:
        return False
    if "operation" in item:
        return _apply_patch_operation_is_self_contained(operation)
    if "patch" in item:
        return _is_nonblank_string(patch)
    return _is_nonblank_string(input_value)


def _caller_is_self_contained(item: Mapping[str, JsonValue]) -> bool:
    caller = item.get("caller")
    return caller is None or caller == {"type": "direct"}


def _input_item_has_only_known_fields(item: Mapping[str, JsonValue], item_type: str | None) -> bool:
    if item_type in (None, "message"):
        allowed_fields = _ACCOUNT_NEUTRAL_MESSAGE_FIELDS
    elif item_type in _ACCOUNT_NEUTRAL_CONTENT_FIELDS:
        allowed_fields = _ACCOUNT_NEUTRAL_CONTENT_FIELDS[item_type]
    else:
        allowed_fields = _ACCOUNT_NEUTRAL_INPUT_ITEM_FIELDS.get(item_type or "")
        if allowed_fields is None:
            return False
    status = item.get("status")
    return not any(key not in allowed_fields for key in item) and (
        status is None or (isinstance(status, str) and status in _ACCOUNT_NEUTRAL_ITEM_STATUSES)
    )


def _apply_patch_operation_is_self_contained(operation: JsonValue | None) -> bool:
    if not isinstance(operation, dict):
        return False
    operation_type = operation.get("type")
    allowed_fields = (
        _ACCOUNT_NEUTRAL_APPLY_PATCH_OPERATION_FIELDS.get(operation_type) if isinstance(operation_type, str) else None
    )
    if allowed_fields is None or set(operation) != allowed_fields:
        return False
    return _is_nonblank_string(operation.get("path")) and (
        operation_type == "delete_file" or isinstance(operation.get("diff"), str)
    )


def _tool_output_is_self_contained(item_type: str, item: Mapping[str, JsonValue]) -> bool:
    if item.get("status") not in (None, "completed", "failed"):
        return False
    output = item.get("output")
    if isinstance(output, str):
        return True
    if item_type == "apply_patch_call_output":
        return output is None and item.get("status") in {"completed", "failed"}
    return (
        isinstance(output, list)
        and bool(output)
        and all(
            isinstance(part, dict) and _input_content_part_is_self_contained(part, allow_output=False)
            for part in output
        )
    )


def _is_nonblank_string(value: JsonValue | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def responses_payload_is_account_neutral_fresh_replay(payload: Mapping[str, JsonValue]) -> bool:
    """Return whether a full request can move accounts without stored upstream state."""

    if payload.get("conversation") not in (None, ""):
        return False
    if payload.get("previous_response_id") not in (None, ""):
        return False
    if payload.get("prompt") not in (None, ""):
        return False
    if any(key not in _RESPONSES_PAYLOAD_FIELDS_WITH_DEDICATED_VALIDATION for key in payload):
        return False
    if not _reasoning_config_is_account_neutral(payload.get("reasoning")):
        return False
    if not _tool_choice_is_account_neutral(payload.get("tool_choice")):
        return False
    if not _text_controls_are_account_neutral(payload.get("text")):
        return False
    if not _client_metadata_is_account_neutral(payload.get("client_metadata")):
        return False

    input_value = payload.get("input")
    if input_value is None or isinstance(input_value, str):
        input_items: list[JsonValue] = []
    elif isinstance(input_value, list):
        input_items = cast(list[JsonValue], input_value)
    else:
        return False
    if extract_input_file_ids(input_items):
        return False
    if any(
        isinstance(item, dict)
        and isinstance(item.get("type"), str)
        and item.get("type") not in _ACCOUNT_NEUTRAL_INPUT_ITEM_TYPES
        for item in input_items
    ):
        return False
    if not responses_input_items_are_self_contained_fresh_replay(input_items):
        return False
    if not _input_items_have_valid_account_neutral_shape(input_items):
        return False
    if _contains_account_scoped_input_state(input_items):
        return False

    tools = payload.get("tools")
    if tools is None:
        return True
    return _tools_are_account_neutral(tools)


def _reasoning_config_is_account_neutral(reasoning: JsonValue | None) -> bool:
    if reasoning is None:
        return True
    return (
        isinstance(reasoning, dict)
        and all(key in _ACCOUNT_NEUTRAL_REASONING_CONFIG_FIELDS for key in reasoning)
        and all(value is None or isinstance(value, str) for value in reasoning.values())
    )


def _text_controls_are_account_neutral(text: JsonValue | None) -> bool:
    if text is None:
        return True
    if not isinstance(text, dict) or not set(text) <= {"format", "verbosity"}:
        return False
    verbosity = text.get("verbosity")
    if verbosity is not None and verbosity not in {"low", "medium", "high"}:
        return False
    format_value = text.get("format")
    if format_value is None:
        return True
    if not isinstance(format_value, dict):
        return False
    format_type = format_value.get("type")
    if format_type in {"text", "json_object"}:
        return set(format_value) == {"type"}
    if format_type != "json_schema" or not set(format_value) <= {
        "description",
        "name",
        "schema",
        "strict",
        "type",
    }:
        return False
    return (
        _is_nonblank_string(format_value.get("name"))
        and isinstance(format_value.get("schema"), dict)
        and (format_value.get("strict") is None or isinstance(format_value.get("strict"), bool))
        and (format_value.get("description") is None or isinstance(format_value.get("description"), str))
    )


def _client_metadata_is_account_neutral(client_metadata: JsonValue | None) -> bool:
    if client_metadata is None:
        return True
    if not isinstance(client_metadata, dict) or not set(client_metadata) <= _ACCOUNT_NEUTRAL_CLIENT_METADATA_FIELDS:
        return False
    return (
        all(_is_nonblank_string(value) for value in client_metadata.values())
        and client_metadata.get(
            "ws_request_header_x_openai_internal_codex_responses_lite",
            "true",
        )
        == "true"
    )


def _tools_are_account_neutral(tools: JsonValue) -> bool:
    return isinstance(tools, list) and all(
        isinstance(tool, dict) and _tool_declaration_is_account_neutral(tool) for tool in tools
    )


def _tool_declaration_is_account_neutral(tool: Mapping[str, JsonValue]) -> bool:
    tool_type = tool.get("type")
    if not isinstance(tool_type, str) or tool_type not in _ACCOUNT_NEUTRAL_TOOL_TYPES:
        return False
    if any(key not in _ACCOUNT_NEUTRAL_TOOL_DECLARATION_FIELDS[tool_type] for key in tool):
        return False
    if _contains_account_scoped_tool_state(tool):
        return False
    if tool_type in {"custom", "function"} and not _is_nonblank_string(tool.get("name")):
        return False
    if tool.get("description") is not None and not isinstance(tool.get("description"), str):
        return False
    if tool_type == "function":
        return (tool.get("parameters") is None or isinstance(tool.get("parameters"), dict)) and (
            tool.get("strict") is None or isinstance(tool.get("strict"), bool)
        )
    if tool_type == "custom":
        return _custom_tool_format_is_account_neutral(tool.get("format"))
    return _web_search_tool_options_are_account_neutral(tool_type, tool)


def _web_search_tool_options_are_account_neutral(
    tool_type: str,
    tool: Mapping[str, JsonValue],
) -> bool:
    filters = tool.get("filters")
    if filters is not None:
        if not isinstance(filters, dict) or not set(filters) <= _ACCOUNT_NEUTRAL_WEB_SEARCH_FILTER_FIELDS:
            return False
        allowed_domains = filters.get("allowed_domains")
        if allowed_domains is not None and not (
            isinstance(allowed_domains, list) and all(_is_nonblank_string(domain) for domain in allowed_domains)
        ):
            return False

    search_context_size = tool.get("search_context_size")
    if search_context_size is not None and search_context_size not in _ACCOUNT_NEUTRAL_WEB_SEARCH_CONTEXT_SIZES:
        return False

    user_location = tool.get("user_location")
    if user_location is None:
        return True
    if not isinstance(user_location, dict) or not set(user_location) <= _ACCOUNT_NEUTRAL_WEB_SEARCH_LOCATION_FIELDS:
        return False
    location_type = user_location.get("type")
    if location_type not in (None, "approximate") or (
        tool_type == "web_search_preview" and location_type != "approximate"
    ):
        return False
    return all(value is None or isinstance(value, str) for key, value in user_location.items() if key != "type")


def _tool_choice_is_account_neutral(tool_choice: JsonValue | None) -> bool:
    if tool_choice is None:
        return True
    if isinstance(tool_choice, str):
        return tool_choice in _ACCOUNT_NEUTRAL_TOOL_CHOICE_STRINGS
    if not isinstance(tool_choice, dict) or _contains_account_scoped_tool_state(tool_choice):
        return False
    choice_type = tool_choice.get("type")
    if choice_type in {"custom", "function"}:
        return set(tool_choice) <= {"name", "type"} and _is_nonblank_string(tool_choice.get("name"))
    if choice_type in {"web_search", "web_search_preview"}:
        return set(tool_choice) == {"type"}
    if choice_type != "allowed_tools" or set(tool_choice) > {"mode", "tools", "type"}:
        return False
    mode = tool_choice.get("mode")
    allowed = tool_choice.get("tools")
    return (
        mode in {"auto", "required"}
        and isinstance(allowed, list)
        and bool(allowed)
        and all(isinstance(tool, dict) and _tool_choice_reference_is_account_neutral(tool) for tool in allowed)
    )


def _tool_choice_reference_is_account_neutral(tool: Mapping[str, JsonValue]) -> bool:
    tool_type = tool.get("type")
    if tool_type in {"custom", "function"}:
        return set(tool) <= {"name", "type"} and _is_nonblank_string(tool.get("name"))
    return tool_type in {"web_search", "web_search_preview"} and set(tool) == {"type"}


def _custom_tool_format_is_account_neutral(format_value: JsonValue | None) -> bool:
    if format_value is None:
        return True
    if not isinstance(format_value, dict):
        return False
    format_type = format_value.get("type")
    if format_type == "text":
        return set(format_value) == {"type"}
    return (
        format_type == "grammar"
        and set(format_value) == {"definition", "syntax", "type"}
        and format_value.get("syntax") in {"lark", "regex"}
        and isinstance(format_value.get("definition"), str)
    )


def _contains_account_scoped_tool_state(value: JsonValue) -> bool:
    pending = [(value, True)]
    while pending:
        current, is_root = pending.pop()
        if isinstance(current, dict):
            if _mapping_has_account_scoped_reference(current):
                return True
            pending.extend(
                (nested, False)
                for key, nested in current.items()
                if not (is_root and current.get("type") == "function" and key == "parameters")
            )
        elif isinstance(current, list):
            pending.extend((nested, False) for nested in current)
    return False


def _input_items_have_valid_account_neutral_shape(input_items: list[JsonValue]) -> bool:
    for item in input_items:
        if not isinstance(item, dict):
            return False
        item_type = item.get("type")
        if item_type in {"input_file", "input_image", "input_text"}:
            if not _input_content_part_is_self_contained(item, allow_output=False):
                return False
            continue
        if item_type == "additional_tools":
            if item.get("role") != "developer" or not _tools_are_account_neutral(item.get("tools")):
                return False
            continue
        if item_type not in (None, "message"):
            continue
        if not _message_has_valid_account_neutral_content(item):
            return False
    return True


def _message_has_valid_account_neutral_content(item: Mapping[str, JsonValue]) -> bool:
    role = item.get("role")
    if role not in _ACCOUNT_NEUTRAL_MESSAGE_ROLES:
        return False
    phase = item.get("phase")
    if phase is not None and phase not in {"commentary", "final_answer"}:
        return False
    content = item.get("content")
    if role != "assistant" and isinstance(content, str):
        return _is_nonblank_string(content)
    if not isinstance(content, list) or not content:
        return False
    if role == "assistant":
        return all(
            isinstance(part, dict)
            and part.get("type") in {"output_text", "refusal"}
            and _input_content_part_is_self_contained(part, allow_output=True)
            for part in content
        )
    return all(
        isinstance(part, dict) and _input_content_part_is_self_contained(part, allow_output=False) for part in content
    )


def _input_content_part_is_self_contained(
    part: Mapping[str, JsonValue],
    *,
    allow_output: bool,
) -> bool:
    part_type = part.get("type")
    if part_type not in _ACCOUNT_NEUTRAL_MESSAGE_CONTENT_TYPES:
        return False
    if any(key not in _ACCOUNT_NEUTRAL_CONTENT_FIELDS[cast(str, part_type)] for key in part):
        return False
    if part_type in {"input_text", "text"} or (allow_output and part_type == "output_text"):
        return _is_nonblank_string(part.get("text"))
    if allow_output and part_type == "refusal":
        return _is_nonblank_string(part.get("refusal"))
    if part_type == "input_image":
        return (part.get("detail") is None or isinstance(part.get("detail"), str)) and (
            _url_is_account_neutral(part.get("image_url"), allow_data=True) or _is_nonblank_string(part.get("file_id"))
        )
    if part_type == "input_file":
        return (part.get("filename") is None or isinstance(part.get("filename"), str)) and (
            _is_nonblank_string(part.get("file_data"))
            or _is_nonblank_string(part.get("file_id"))
            or _url_is_account_neutral(part.get("file_url"), allow_data=False)
        )
    return False


def _url_is_account_neutral(value: JsonValue | None, *, allow_data: bool) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError:
        return False
    return scheme in ({"data", "http", "https"} if allow_data else {"http", "https"})


def _contains_account_scoped_input_state(value: JsonValue) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            item_type = current.get("type")
            if isinstance(item_type, str) and item_type in _ACCOUNT_SCOPED_HOSTED_INPUT_TYPES:
                return True
            if isinstance(item_type, str) and item_type.startswith("mcp_"):
                return True
            if item_type == "additional_tools" and not _tools_are_account_neutral(current.get("tools")):
                return True
            if (
                isinstance(item_type, str)
                and (item_type.endswith("_call") or item_type.endswith("_call_output"))
                and item_type not in _TOOL_CALL_TYPES
                and item_type not in _TOOL_CALL_TYPE_BY_OUTPUT_TYPE
            ):
                return True
            if _mapping_has_account_scoped_reference(current):
                return True
            pending.extend(
                nested for key, nested in current.items() if not (item_type == "additional_tools" and key == "tools")
            )
        elif isinstance(current, list):
            pending.extend(current)
    return False


def _mapping_has_account_scoped_reference(value: Mapping[str, JsonValue]) -> bool:
    for key in ("file_id", "container_id", "vector_store_id"):
        if value.get(key) not in (None, ""):
            return True
    if value.get("encrypted_content") not in (None, ""):
        return True
    for url_field, allow_data in (("image_url", True), ("file_url", False)):
        url_value = value.get(url_field)
        if url_value not in (None, "") and not _url_is_account_neutral(url_value, allow_data=allow_data):
            return True
    for key in ("file_ids", "vector_store_ids"):
        identifiers = value.get(key)
        if identifiers is not None and identifiers != []:
            return True
    return False
