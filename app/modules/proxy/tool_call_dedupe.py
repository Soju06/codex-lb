from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import cast

from app.core.openai.models import OpenAIEvent
from app.core.openai.parsing import parse_sse_event
from app.core.types import JsonValue
from app.core.utils.sse import format_sse_event

logger = logging.getLogger(__name__)

_TOOL_CALL_DEDUPE_CACHE_LIMIT = 1024
_PARALLEL_TOOL_CALL_NAME = "multi_tool_use.parallel"
_DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES = frozenset(
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
_SIDE_EFFECT_TOOL_CALL_NAMES = frozenset(
    {
        _PARALLEL_TOOL_CALL_NAME,
        *_DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES,
        *(f"functions.{name}" for name in _DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES),
    }
)
_SIDE_EFFECT_TOOL_CALL_ITEM_TYPES = frozenset({"apply_patch_call"})
_PARALLEL_TOOL_USE_DEDUPE_RECIPIENT_NAMES = frozenset(
    {
        *(f"functions.{name}" for name in _DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES),
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
    if isinstance(payload.get("error"), dict):
        return "error"
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
    scope_side_effects_by_response_id: bool = True,
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
        seen_tool_call_keys.clear()
        return False
    if not isinstance(argument_value, str):
        return False
    item_name = item.get("name")
    if item_name is not None and not isinstance(item_name, str):
        item_name = None
    call_id = item.get("call_id")
    if call_id is not None and not isinstance(call_id, str):
        call_id = None
    is_side_effect_tool_call = item_type in _SIDE_EFFECT_TOOL_CALL_ITEM_TYPES or (
        item_name in _SIDE_EFFECT_TOOL_CALL_NAMES and _tool_call_has_side_effect_arguments(item_name, argument_value)
    )
    if not is_side_effect_tool_call:
        seen_tool_call_keys.clear()
        return False
    if item_name == _PARALLEL_TOOL_CALL_NAME and is_side_effect_tool_call:
        return _mark_duplicate_parallel_tool_call_downstream_event(
            cast(dict[str, JsonValue], item),
            argument_value,
            seen_tool_call_keys=seen_tool_call_keys,
            response_id=response_id,
            scope_side_effects_by_response_id=scope_side_effects_by_response_id,
        )
    # Same-response replays have shown distinct call_ids for byte-identical shell/edit requests.
    # For local side-effect tools, running the same payload twice is worse than dropping a duplicate-looking call_id.
    dedupe_call_id = None if is_side_effect_tool_call else call_id
    if is_side_effect_tool_call:
        item_name = normalize_tool_call_name(item_name)
        argument_key = canonical_downstream_side_effect_argument_key(item_name, argument_value)
    else:
        argument_key = argument_value
    dedupe_response_id = response_id if scope_side_effects_by_response_id or not is_side_effect_tool_call else None
    key = (dedupe_response_id or "", str(item_type), item_name, dedupe_call_id, argument_key)
    if key in seen_tool_call_keys:
        logger.warning(
            "Suppressed duplicate downstream tool call response_id=%s item_type=%s name=%s",
            response_id,
            item_type,
            item_name,
        )
        return True
    if seen_tool_call_keys:
        seen_tool_call_keys.clear()
    seen_tool_call_keys[key] = None
    while len(seen_tool_call_keys) > _TOOL_CALL_DEDUPE_CACHE_LIMIT:
        seen_tool_call_keys.pop(next(iter(seen_tool_call_keys)))
    return False


def _mark_duplicate_parallel_tool_call_downstream_event(
    item: dict[str, JsonValue],
    argument_value: str,
    *,
    seen_tool_call_keys: dict[tuple[str, str, str | None, str | None, str], None],
    response_id: str | None,
    scope_side_effects_by_response_id: bool,
) -> bool:
    argument = json_object_from_argument(argument_value)
    if argument is None:
        return False
    tool_uses = argument.get("tool_uses")
    if not isinstance(tool_uses, list):
        return False

    candidate_keys: list[tuple[str, str, str | None, str | None, str]] = []
    for tool_use in tool_uses:
        if not isinstance(tool_use, dict):
            continue
        recipient_name = tool_use.get("recipient_name")
        if not isinstance(recipient_name, str) or recipient_name not in _PARALLEL_TOOL_USE_DEDUPE_RECIPIENT_NAMES:
            continue
        dedupe_response_id = response_id if scope_side_effects_by_response_id else None
        candidate_keys.append(
            (
                dedupe_response_id or "",
                "parallel_tool_use",
                recipient_name,
                None,
                canonical_parallel_tool_use_key(cast(dict[str, JsonValue], tool_use)),
            )
        )
    if candidate_keys and seen_tool_call_keys and not any(key in seen_tool_call_keys for key in candidate_keys):
        seen_tool_call_keys.clear()

    kept_tool_uses: list[JsonValue] = []
    removed_count = 0
    for tool_use in tool_uses:
        if not isinstance(tool_use, dict):
            kept_tool_uses.append(cast(JsonValue, tool_use))
            continue
        recipient_name = tool_use.get("recipient_name")
        if not isinstance(recipient_name, str) or recipient_name not in _PARALLEL_TOOL_USE_DEDUPE_RECIPIENT_NAMES:
            kept_tool_uses.append(cast(JsonValue, tool_use))
            continue
        dedupe_response_id = response_id if scope_side_effects_by_response_id else None
        key = (
            dedupe_response_id or "",
            "parallel_tool_use",
            recipient_name,
            None,
            canonical_parallel_tool_use_key(cast(dict[str, JsonValue], tool_use)),
        )
        if key in seen_tool_call_keys:
            removed_count += 1
            continue
        seen_tool_call_keys[key] = None
        kept_tool_uses.append(cast(JsonValue, tool_use))

    while len(seen_tool_call_keys) > _TOOL_CALL_DEDUPE_CACHE_LIMIT:
        seen_tool_call_keys.pop(next(iter(seen_tool_call_keys)))

    if removed_count == 0:
        return False
    logger.warning(
        "Suppressed duplicate downstream parallel tool uses response_id=%s removed=%s",
        response_id,
        removed_count,
    )
    if not kept_tool_uses:
        return True

    rewritten_argument = dict(argument)
    rewritten_argument["tool_uses"] = kept_tool_uses
    item["arguments"] = json.dumps(rewritten_argument, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
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
    return cast(
        JsonValue,
        sorted(
            targets,
            key=lambda target: (
                target.__class__.__name__,
                canonical_json_key(cast(JsonValue, target)),
            ),
        ),
    )


def canonical_side_effect_argument_key(item_name: str | None, argument_value: str) -> str:
    argument = json_object_from_argument(argument_value)
    if argument is None:
        return argument_value
    normalized_item_name = normalize_tool_call_name(item_name)
    if normalized_item_name == "write_stdin":
        return canonical_json_key(
            {
                "name": normalized_item_name,
                "session_id": argument.get("session_id"),
                "chars": argument.get("chars"),
                "yield_time_ms": argument.get("yield_time_ms"),
            }
        )
    if normalized_item_name == "wait_agent":
        return canonical_json_key(
            {
                "name": normalized_item_name,
                "targets": canonical_wait_agent_targets(argument.get("targets")),
            }
        )
    if normalized_item_name == "exec_command":
        return canonical_parameters_key(normalized_item_name, argument)
    if item_name != _PARALLEL_TOOL_CALL_NAME:
        return canonical_json_key({"name": normalized_item_name or item_name, "parameters": cast(JsonValue, argument)})

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


def canonical_downstream_side_effect_argument_key(item_name: str | None, argument_value: str) -> str:
    argument = json_object_from_argument(argument_value)
    if argument is None:
        return argument_value
    normalized_item_name = normalize_tool_call_name(item_name)
    if normalized_item_name == "write_stdin":
        return canonical_parameters_key(
            normalized_item_name,
            {
                "session_id": argument.get("session_id"),
                "chars": argument.get("chars"),
                "yield_time_ms": argument.get("yield_time_ms"),
                "max_output_tokens": argument.get("max_output_tokens"),
            },
        )
    if normalized_item_name == "exec_command":
        return canonical_parameters_key(normalized_item_name, argument)
    return canonical_side_effect_argument_key(item_name, argument_value)


def dedupe_replayed_side_effect_input_items(
    input_items: list[JsonValue],
    *,
    sanitize_missing_outputs: bool = False,
) -> tuple[list[JsonValue], int]:
    call_keys: dict[int, tuple[str, str | None, str]] = {}
    call_ids: dict[int, str] = {}
    output_indices_by_call_id: dict[str, list[int]] = {}
    for index, item in enumerate(input_items):
        if not isinstance(item, dict):
            continue
        tool_call_key = replayed_side_effect_tool_call_key(item)
        if tool_call_key is not None:
            call_keys[index] = tool_call_key
            call_id = item.get("call_id")
            if isinstance(call_id, str) and call_id:
                call_ids[index] = call_id
            continue
        output_call_id = replayed_tool_output_call_id(item)
        if output_call_id is not None:
            output_indices_by_call_id.setdefault(output_call_id, []).append(index)

    if not call_keys:
        return input_items, 0

    kept = [True] * len(input_items)
    rewritten: dict[int, JsonValue] = {}
    first_call_id_by_key: dict[tuple[str, str | None, str], str | None] = {}
    first_call_index_by_key: dict[tuple[str, str | None, str], int] = {}
    output_index_by_call_index: dict[int, int | None] = {}
    next_output_cursor_by_call_id: dict[str, int] = {}
    last_side_effect_key: tuple[str, str | None, str] | None = None
    removed_count = 0
    for index, item in enumerate(input_items):
        if isinstance(item, dict) and replayed_input_segment_boundary(item):
            first_call_id_by_key.clear()
            first_call_index_by_key.clear()
            last_side_effect_key = None
        key = call_keys.get(index)
        if key is None:
            output_call_id = replayed_tool_output_call_id(item) if isinstance(item, dict) else None
            if (
                last_side_effect_key is not None
                and output_call_id is not None
                and first_call_id_by_key.get(last_side_effect_key) == output_call_id
            ):
                continue
            if output_call_id is not None or (isinstance(item, dict) and replayed_tool_call_segment_boundary(item)):
                first_call_id_by_key.clear()
                first_call_index_by_key.clear()
                last_side_effect_key = None
            continue
        if last_side_effect_key is not None and key != last_side_effect_key:
            first_call_id_by_key.clear()
        last_side_effect_key = key
        call_id = call_ids.get(index)
        output_index = (
            replayed_tool_output_index_for_call(
                output_indices_by_call_id,
                next_output_cursor_by_call_id,
                call_index=index,
                call_id=call_id,
            )
            if call_id is not None
            else None
        )
        output_index_by_call_index[index] = output_index
        if key not in first_call_id_by_key:
            first_call_id_by_key[key] = call_id
            first_call_index_by_key[key] = index
            continue

        first_call_index = first_call_index_by_key.get(key)
        if first_call_index is not None and output_index_by_call_index.get(first_call_index) is None:
            rewritten[first_call_index] = replayed_tool_call_without_output_as_assistant_message(
                cast(Mapping[str, JsonValue], input_items[first_call_index])
            )
        removed_count += 1
        kept[index] = False
        if output_index is not None:
            output_item = input_items[output_index]
            if isinstance(output_item, dict):
                rewritten[output_index] = replayed_tool_output_as_assistant_message(output_item)

    missing_output_rewrites = 0
    if sanitize_missing_outputs:
        for index, key in call_keys.items():
            del key
            if not kept[index] or index in rewritten:
                continue
            if output_index_by_call_index.get(index) is not None:
                continue
            item = input_items[index]
            if not isinstance(item, dict):
                continue
            rewritten[index] = replayed_tool_call_without_output_as_assistant_message(item)
            missing_output_rewrites += 1

    if removed_count == 0:
        if missing_output_rewrites == 0:
            return input_items, 0

    deduped_items: list[JsonValue] = []
    for index, item in enumerate(input_items):
        if kept[index]:
            deduped_items.append(rewritten.get(index, item))
    return deduped_items, removed_count + missing_output_rewrites


def replayed_side_effect_tool_call_key(item: Mapping[str, JsonValue]) -> tuple[str, str | None, str] | None:
    item_type_value = item.get("type")
    item_type = item_type_value if isinstance(item_type_value, str) else None
    if item_type == "function_call":
        item_name_value = item.get("name")
        item_name = item_name_value if isinstance(item_name_value, str) else None
        argument_value = item.get("arguments")
        if not isinstance(argument_value, str):
            return None
        is_side_effect_tool_call = item_name in _SIDE_EFFECT_TOOL_CALL_NAMES and _tool_call_has_side_effect_arguments(
            item_name,
            argument_value,
        )
        if not is_side_effect_tool_call:
            return None
        argument_key = canonical_side_effect_argument_key(item_name, argument_value)
    elif item_type == "custom_tool_call":
        item_name_value = item.get("name")
        item_name = item_name_value if isinstance(item_name_value, str) else None
        if item_name not in _SIDE_EFFECT_TOOL_CALL_NAMES:
            return None
        argument_value = item.get("input")
        if not isinstance(argument_value, str):
            return None
        argument_key = canonical_side_effect_argument_key(item_name, argument_value)
    elif item_type in _SIDE_EFFECT_TOOL_CALL_ITEM_TYPES:
        item_name = item_type
        operation_value = item.get("operation")
        argument_key = canonical_json_key(operation_value)
    else:
        return None
    return (cast(str, item_type), item_name, argument_key)


def replayed_input_segment_boundary(item: Mapping[str, JsonValue]) -> bool:
    role = item.get("role")
    if role not in {"developer", "system", "user"}:
        return False
    item_type = item.get("type")
    return item_type in {None, "message"}


def replayed_tool_call_segment_boundary(item: Mapping[str, JsonValue]) -> bool:
    item_type = item.get("type")
    return item_type in {"function_call", "custom_tool_call"} | _SIDE_EFFECT_TOOL_CALL_ITEM_TYPES


def replayed_tool_output_call_id(item: Mapping[str, JsonValue]) -> str | None:
    if item.get("type") not in {
        "function_call_output",
        "custom_tool_call_output",
        "apply_patch_call_output",
    }:
        return None
    call_id = item.get("call_id")
    if isinstance(call_id, str) and call_id:
        return call_id
    return None


def replayed_tool_output_as_assistant_message(item: Mapping[str, JsonValue]) -> JsonValue:
    output_value = item.get("output")
    if isinstance(output_value, str):
        output_text = output_value
    else:
        output_text = canonical_json_key(cast(JsonValue, output_value))
    return {
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "output_text",
                "text": output_text,
            }
        ],
    }


def replayed_tool_call_without_output_as_assistant_message(item: Mapping[str, JsonValue]) -> JsonValue:
    name_value = item.get("name")
    name = name_value if isinstance(name_value, str) and name_value else "tool"
    return {
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "output_text",
                "text": f"Omitted replayed side-effect tool call without matching output: {name}.",
            }
        ],
    }


def replayed_tool_output_index_for_call(
    output_indices_by_call_id: dict[str, list[int]],
    next_output_cursor_by_call_id: dict[str, int],
    *,
    call_index: int,
    call_id: str,
) -> int | None:
    output_indices = output_indices_by_call_id.get(call_id)
    if not output_indices:
        return None
    cursor = next_output_cursor_by_call_id.get(call_id, 0)
    while cursor < len(output_indices) and output_indices[cursor] < call_index:
        cursor += 1
    if cursor >= len(output_indices):
        next_output_cursor_by_call_id[call_id] = cursor
        return None
    output_index = output_indices[cursor]
    next_output_cursor_by_call_id[call_id] = cursor + 1
    return output_index


def _tool_call_has_side_effect_arguments(item_name: str | None, argument_value: str) -> bool:
    if item_name != _PARALLEL_TOOL_CALL_NAME:
        return item_name in _SIDE_EFFECT_TOOL_CALL_NAMES

    argument = json_object_from_argument(argument_value)
    if argument is None:
        return False
    tool_uses = argument.get("tool_uses")
    if not isinstance(tool_uses, list):
        return False
    for tool_use in tool_uses:
        if not isinstance(tool_use, dict):
            continue
        recipient_name = tool_use.get("recipient_name")
        if isinstance(recipient_name, str) and recipient_name in _PARALLEL_TOOL_USE_DEDUPE_RECIPIENT_NAMES:
            return True
    return False


def canonical_parallel_tool_use_key(tool_use: dict[str, JsonValue]) -> str:
    recipient_name = tool_use.get("recipient_name")
    parameters = tool_use.get("parameters")
    if not isinstance(recipient_name, str):
        return json.dumps(tool_use, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    normalized_recipient_name = normalize_tool_call_name(recipient_name)
    if normalized_recipient_name == "write_stdin" and isinstance(parameters, dict):
        return canonical_parameters_key(
            normalized_recipient_name,
            {
                "session_id": parameters.get("session_id"),
                "chars": parameters.get("chars"),
                "yield_time_ms": parameters.get("yield_time_ms"),
                "max_output_tokens": parameters.get("max_output_tokens"),
            },
        )
    if normalized_recipient_name == "wait_agent" and isinstance(parameters, dict):
        targets = parameters.get("targets")
        return json.dumps(
            {
                "recipient_name": normalized_recipient_name,
                "targets": canonical_wait_agent_targets(targets),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    if normalized_recipient_name == "exec_command" and isinstance(parameters, dict):
        return canonical_parameters_key(normalized_recipient_name, cast(dict[str, JsonValue], parameters))
    return json.dumps(tool_use, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def normalize_tool_call_name(name: str | None) -> str | None:
    if name is None:
        return None
    if name.startswith("functions."):
        return name.removeprefix("functions.")
    return name


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
    rewritten_event_block = format_sse_event(rewritten_payload)
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
