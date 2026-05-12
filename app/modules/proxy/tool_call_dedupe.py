from __future__ import annotations

import json
import logging
from typing import cast

from app.core.openai.models import OpenAIEvent
from app.core.openai.parsing import parse_sse_event
from app.core.types import JsonValue
from app.core.utils.sse import format_sse_event

logger = logging.getLogger(__name__)

_TOOL_CALL_DEDUPE_CACHE_LIMIT = 1024
_PARALLEL_TOOL_CALL_NAME = "multi_tool_use.parallel"
_SIDE_EFFECT_TOOL_CALL_NAMES = frozenset(
    {
        "exec_command",
        _PARALLEL_TOOL_CALL_NAME,
        "write_stdin",
    }
)
_SIDE_EFFECT_TOOL_CALL_ITEM_TYPES = frozenset({"apply_patch_call"})
_PARALLEL_TOOL_USE_DEDUPE_RECIPIENT_NAMES = frozenset(
    {
        "functions.apply_patch",
        "functions.close_agent",
        "functions.exec_command",
        "functions.resume_agent",
        "functions.send_input",
        "functions.spawn_agent",
        "functions.wait_agent",
        "functions.write_stdin",
        "multi_tool_use.parallel",
    }
)
_SIDE_EFFECT_VOLATILE_ARG_KEYS = frozenset({"max_output_tokens", "timeout_ms", "yield_time_ms"})


def event_type_from_payload(event: OpenAIEvent | None, payload: dict[str, JsonValue] | None) -> str | None:
    if event is not None:
        return event.type
    if payload is None:
        return None
    payload_type = payload.get("type")
    if isinstance(payload_type, str):
        return payload_type
    return None


def response_id_from_payload(payload: dict[str, JsonValue] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    top_level_response_id = payload.get("response_id")
    if isinstance(top_level_response_id, str):
        stripped = top_level_response_id.strip()
        if stripped:
            return stripped
    response = payload.get("response")
    if not isinstance(response, dict):
        return None
    response_id = response.get("id")
    if not isinstance(response_id, str):
        return None
    stripped = response_id.strip()
    return stripped or None


def mark_duplicate_tool_call_downstream_event(
    payload: dict[str, JsonValue] | None,
    *,
    seen_tool_call_keys: dict[tuple[str, str, str | None, str | None, str], None],
    response_id: str | None,
) -> bool:
    if not isinstance(payload, dict) or payload.get("type") != "response.output_item.done":
        return False
    item = payload.get("item")
    if not isinstance(item, dict):
        return False
    item_type = item.get("type")
    if item_type == "function_call":
        argument_value = item.get("arguments")
    elif item_type == "custom_tool_call":
        argument_value = item.get("input")
    elif item_type == "apply_patch_call":
        operation_value = item.get("operation")
        if isinstance(operation_value, dict):
            argument_value = json.dumps(operation_value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        else:
            argument_value = operation_value
    else:
        return False
    if not isinstance(argument_value, str):
        return False
    item_name = item.get("name")
    if item_name is not None and not isinstance(item_name, str):
        item_name = None
    call_id = item.get("call_id")
    if call_id is not None and not isinstance(call_id, str):
        call_id = None
    is_side_effect_tool_call = (
        item_type in _SIDE_EFFECT_TOOL_CALL_ITEM_TYPES or item_name in _SIDE_EFFECT_TOOL_CALL_NAMES
    )
    # Same-response replays have shown distinct call_ids for byte-identical shell/edit requests.
    # For local side-effect tools, running the same payload twice is worse than dropping a duplicate-looking call_id.
    dedupe_call_id = None if is_side_effect_tool_call else call_id
    if is_side_effect_tool_call:
        argument_key = canonical_side_effect_argument_key(item_name, argument_value)
    else:
        argument_key = argument_value
    key = (response_id or "", str(item_type), item_name, dedupe_call_id, argument_key)
    if key in seen_tool_call_keys:
        logger.warning(
            "Suppressed duplicate downstream tool call response_id=%s item_type=%s name=%s",
            response_id,
            item_type,
            item_name,
        )
        return True
    seen_tool_call_keys[key] = None
    while len(seen_tool_call_keys) > _TOOL_CALL_DEDUPE_CACHE_LIMIT:
        seen_tool_call_keys.pop(next(iter(seen_tool_call_keys)))
    return False


def json_object_from_argument(argument_value: str) -> dict[str, JsonValue] | None:
    try:
        decoded_argument = json.loads(argument_value)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded_argument, dict):
        return None
    return cast(dict[str, JsonValue], decoded_argument)


def canonical_json_key(value: JsonValue) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_parameters_key(recipient_name: str, parameters: dict[str, JsonValue]) -> str:
    canonical_parameters = dict(parameters)
    for key in _SIDE_EFFECT_VOLATILE_ARG_KEYS:
        canonical_parameters.pop(key, None)
    return canonical_json_key({"recipient_name": recipient_name, "parameters": canonical_parameters})


def canonical_wait_agent_targets(targets: JsonValue | None) -> JsonValue | None:
    if not isinstance(targets, list):
        return targets
    try:
        return cast(JsonValue, sorted(targets, key=lambda target: (target.__class__.__name__, str(target))))
    except (TypeError, ValueError):
        return cast(JsonValue, list(targets))


def canonical_side_effect_argument_key(item_name: str | None, argument_value: str) -> str:
    argument = json_object_from_argument(argument_value)
    if argument is None:
        return argument_value
    if item_name in {"exec_command", "write_stdin"}:
        return canonical_parameters_key(str(item_name), argument)
    if item_name != _PARALLEL_TOOL_CALL_NAME:
        return canonical_json_key(cast(JsonValue, argument))

    tool_uses = argument.get("tool_uses")
    if not isinstance(tool_uses, list):
        return canonical_json_key(cast(JsonValue, argument))

    canonical_tool_uses: list[JsonValue] = []
    for tool_use in tool_uses:
        if isinstance(tool_use, dict):
            canonical_tool_use = json_object_from_argument(canonical_parallel_tool_use_key(tool_use))
            canonical_tool_uses.append(canonical_tool_use or tool_use)
        else:
            canonical_tool_uses.append(cast(JsonValue, tool_use))
    canonical_argument = dict(argument)
    canonical_argument["tool_uses"] = canonical_tool_uses
    return canonical_json_key(cast(JsonValue, canonical_argument))


def canonical_parallel_tool_use_key(tool_use: dict[str, JsonValue]) -> str:
    recipient_name = tool_use.get("recipient_name")
    parameters = tool_use.get("parameters")
    if not isinstance(recipient_name, str):
        return json.dumps(tool_use, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if recipient_name == "functions.write_stdin" and isinstance(parameters, dict):
        return json.dumps(
            {
                "recipient_name": recipient_name,
                "session_id": parameters.get("session_id"),
                "chars": parameters.get("chars"),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    if recipient_name == "functions.wait_agent" and isinstance(parameters, dict):
        targets = parameters.get("targets")
        return json.dumps(
            {
                "recipient_name": recipient_name,
                "targets": canonical_wait_agent_targets(targets),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    if recipient_name == "functions.exec_command" and isinstance(parameters, dict):
        return canonical_parameters_key(recipient_name, cast(dict[str, JsonValue], parameters))
    return json.dumps(tool_use, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def dedupe_parallel_tool_uses_argument(argument_value: str) -> tuple[str, bool, int]:
    try:
        decoded_arguments = json.loads(argument_value)
    except json.JSONDecodeError:
        return argument_value, False, 0
    if not isinstance(decoded_arguments, dict):
        return argument_value, False, 0
    tool_uses = decoded_arguments.get("tool_uses")
    if not isinstance(tool_uses, list):
        return argument_value, False, 0

    seen_tool_uses: set[str] = set()
    deduped_tool_uses: list[JsonValue] = []
    removed_count = 0
    for tool_use in tool_uses:
        if not isinstance(tool_use, dict):
            deduped_tool_uses.append(cast(JsonValue, tool_use))
            continue
        recipient_name = tool_use.get("recipient_name")
        if not isinstance(recipient_name, str) or recipient_name not in _PARALLEL_TOOL_USE_DEDUPE_RECIPIENT_NAMES:
            deduped_tool_uses.append(cast(JsonValue, tool_use))
            continue
        tool_use_key = canonical_parallel_tool_use_key(cast(dict[str, JsonValue], tool_use))
        if tool_use_key in seen_tool_uses:
            removed_count += 1
            continue
        seen_tool_uses.add(tool_use_key)
        deduped_tool_uses.append(cast(JsonValue, tool_use))

    if removed_count == 0:
        return argument_value, False, 0

    rewritten_arguments: dict[str, JsonValue] = dict(cast(dict[str, JsonValue], decoded_arguments))
    rewritten_arguments["tool_uses"] = deduped_tool_uses
    return (
        json.dumps(rewritten_arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        True,
        removed_count,
    )


def rewrite_parallel_tool_call_payload(
    payload: dict[str, JsonValue] | None,
) -> tuple[dict[str, JsonValue] | None, bool, int]:
    if not isinstance(payload, dict) or payload.get("type") != "response.output_item.done":
        return payload, False, 0
    item = payload.get("item")
    if not isinstance(item, dict):
        return payload, False, 0
    if item.get("type") != "function_call" or item.get("name") != _PARALLEL_TOOL_CALL_NAME:
        return payload, False, 0
    argument_value = item.get("arguments")
    if not isinstance(argument_value, str):
        return payload, False, 0

    rewritten_arguments, changed, removed_count = dedupe_parallel_tool_uses_argument(argument_value)
    if not changed:
        return payload, False, 0

    rewritten_item: dict[str, JsonValue] = dict(cast(dict[str, JsonValue], item))
    rewritten_item["arguments"] = rewritten_arguments
    rewritten_payload: dict[str, JsonValue] = dict(payload)
    rewritten_payload["item"] = rewritten_item
    logger.warning(
        "Suppressed duplicate nested parallel tool uses response_id=%s removed=%s",
        response_id_from_payload(rewritten_payload),
        removed_count,
    )
    return rewritten_payload, True, removed_count


def rewrite_parallel_tool_call_text(
    text: str,
    payload: dict[str, JsonValue] | None,
    *,
    event_block: str,
) -> tuple[str, dict[str, JsonValue] | None, OpenAIEvent | None, str | None, str]:
    rewritten_payload, changed, _removed_count = rewrite_parallel_tool_call_payload(payload)
    if not changed:
        event = parse_sse_event(event_block)
        return text, payload, event, event_type_from_payload(event, payload), event_block
    assert rewritten_payload is not None
    rewritten_text = json.dumps(rewritten_payload, ensure_ascii=True, separators=(",", ":"))
    rewritten_event_block = f"data: {rewritten_text}\n\n"
    rewritten_event = parse_sse_event(rewritten_event_block)
    return (
        rewritten_text,
        rewritten_payload,
        rewritten_event,
        event_type_from_payload(rewritten_event, rewritten_payload),
        rewritten_event_block,
    )


def rewrite_parallel_tool_call_sse_line(
    line: str,
    payload: dict[str, JsonValue] | None,
) -> tuple[str, dict[str, JsonValue] | None, OpenAIEvent | None, str | None]:
    rewritten_payload, changed, _removed_count = rewrite_parallel_tool_call_payload(payload)
    if not changed:
        event = parse_sse_event(line)
        return line, payload, event, event_type_from_payload(event, payload)
    assert rewritten_payload is not None
    rewritten_line = format_sse_event(rewritten_payload)
    rewritten_event = parse_sse_event(rewritten_line)
    return (
        rewritten_line,
        rewritten_payload,
        rewritten_event,
        event_type_from_payload(rewritten_event, rewritten_payload),
    )
