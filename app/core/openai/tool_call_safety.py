from __future__ import annotations

import json
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
PARALLEL_TOOL_USE_DEDUPE_RECIPIENT_NAMES = frozenset(
    {
        *(f"functions.{name}" for name in DOWNSTREAM_DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES),
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
    """Return whether a tool call may perform a side effect downstream."""

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
