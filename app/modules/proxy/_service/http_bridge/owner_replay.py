"""Context validation for complete HTTP tool resends on their durable owner."""

from __future__ import annotations

from collections.abc import Mapping

from app.core.openai.requests import MutableJsonObject, strip_replayed_tool_call_namespaces_from_payload
from app.core.types import JsonValue
from app.modules.proxy.replay_safety import (
    _input_item_has_only_known_fields,
    _is_retained_response_message,
    _message_has_valid_account_neutral_content,
    project_responses_input_for_account_neutral_fresh_replay,
    responses_input_suffix_matches_pending_tool_calls,
)


def retains_owner_bound_tool_context(
    input_items: list[JsonValue], *, stored_count: int, pending_tool_calls: Mapping[str, str] | None
) -> bool:
    """Check a temporary view; never authorize relocation or replace wire input.

    The caller proves the exact durable prefix and account separately. Metadata
    and historical controls remain in the original request on that account.
    """
    if not pending_tool_calls:
        return False
    wire_shape: MutableJsonObject = {"input": input_items}
    strip_replayed_tool_call_namespaces_from_payload(wire_shape)
    normalized_input = wire_shape["input"]
    assert isinstance(normalized_input, list)
    projection = project_responses_input_for_account_neutral_fresh_replay(
        normalized_input, stored_count=stored_count, preserve_developer_message_ids=True
    )
    if projection is None:
        return False
    items: list[JsonValue] = []
    for item in projection.input_items:
        if isinstance(item, dict) and "internal_chat_message_metadata_passthrough" in item:
            if not isinstance(item["internal_chat_message_metadata_passthrough"], dict):
                return False
            item = {key: value for key, value in item.items() if key != "internal_chat_message_metadata_passthrough"}
        items.append(item)
    boundary = projection.stored_prefix_count
    prefix: list[JsonValue] = []
    leading_controls = True
    for item in items[:boundary]:
        if (
            leading_controls
            and isinstance(item, dict)
            and item.get("role") == "developer"
            and item.get("type") in (None, "message")
        ):
            if not _input_item_has_only_known_fields(item, "message") or not (
                _message_has_valid_account_neutral_content(item)
            ):
                return False
            continue
        if not isinstance(item, dict) or item.get("type") not in ("additional_tools", "compaction"):
            leading_controls = False
        prefix.append(item)
    end = boundary
    while end < len(items):
        item = items[end]
        if not isinstance(item, dict) or item.get("role") != "assistant":
            break
        if not _input_item_has_only_known_fields(item, "message") or not _is_retained_response_message(item):
            return False
        end += 1
    return responses_input_suffix_matches_pending_tool_calls(
        [*prefix, *items[end:]],
        stored_count=len(prefix),
        pending_tool_calls=pending_tool_calls,
    )
