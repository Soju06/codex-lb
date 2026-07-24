from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from app.core.types import JsonValue

PARALLEL_TOOL_CALL_NAME = "multi_tool_use.parallel"
HISTORY_DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES = frozenset(
    {
        "apply_patch",
        "close_agent",
        "create_goal",
        "exec_command",
        "request_user_input",
        "resume_agent",
        "send_input",
        "spawn_agent",
        "update_goal",
        "update_plan",
        "wait_agent",
        "write_stdin",
    }
)
CODE_MODE_DOWNSTREAM_SIDE_EFFECT_TOOL_CALL_NAMES = frozenset({"collaboration", "exec"})
DOWNSTREAM_DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES = frozenset(
    {*HISTORY_DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES, *CODE_MODE_DOWNSTREAM_SIDE_EFFECT_TOOL_CALL_NAMES}
)
HISTORY_SIDE_EFFECT_TOOL_CALL_NAMES = frozenset(
    {
        PARALLEL_TOOL_CALL_NAME,
        *HISTORY_DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES,
        *(f"functions.{name}" for name in HISTORY_DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES),
    }
)
DOWNSTREAM_SIDE_EFFECT_TOOL_CALL_NAMES = frozenset(
    {
        PARALLEL_TOOL_CALL_NAME,
        *DOWNSTREAM_DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES,
        *(f"functions.{name}" for name in DOWNSTREAM_DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES),
    }
)
SIDE_EFFECT_TOOL_CALL_ITEM_TYPES = frozenset({"apply_patch_call"})
PARALLEL_TOOL_USE_DEDUPE_RECIPIENT_NAMES = frozenset(
    {
        *(f"functions.{name}" for name in HISTORY_DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES),
        PARALLEL_TOOL_CALL_NAME,
    }
)
PARALLEL_TOOL_USE_SIDE_EFFECT_RECIPIENT_NAMES = frozenset(
    {
        *(f"functions.{name}" for name in DOWNSTREAM_DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES),
        PARALLEL_TOOL_CALL_NAME,
    }
)


def is_downstream_side_effect_tool_call(name: str | None, argument_value: str) -> bool:
    """Return whether a named tool call may perform a side effect downstream."""

    if name != PARALLEL_TOOL_CALL_NAME:
        return name in DOWNSTREAM_SIDE_EFFECT_TOOL_CALL_NAMES
    try:
        decoded_arguments = json.loads(argument_value)
    except json.JSONDecodeError:
        return False
    if not isinstance(decoded_arguments, dict):
        return False
    tool_uses = decoded_arguments.get("tool_uses")
    if not isinstance(tool_uses, list):
        return False
    for tool_use in cast(list[JsonValue], tool_uses):
        if not isinstance(tool_use, dict):
            continue
        recipient_name = tool_use.get("recipient_name")
        if isinstance(recipient_name, str) and recipient_name in PARALLEL_TOOL_USE_SIDE_EFFECT_RECIPIENT_NAMES:
            return True
    return False


def is_downstream_side_effect_tool_call_item(item: Mapping[str, JsonValue]) -> bool:
    """Classify a Responses history item without depending on proxy modules."""

    item_type = item.get("type")
    if item_type in SIDE_EFFECT_TOOL_CALL_ITEM_TYPES:
        return True
    if item_type == "function_call":
        argument_value = item.get("arguments")
    elif item_type == "custom_tool_call":
        argument_value = item.get("input")
    else:
        return False
    item_name = item.get("name")
    return (
        isinstance(item_name, str)
        and isinstance(argument_value, str)
        and is_downstream_side_effect_tool_call(item_name, argument_value)
    )
