"""Reconstruct a bounded, self-contained Responses replay from durable turns."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import cast

from app.core.types import JsonValue
from app.core.utils.sse import parse_sse_data_json
from app.modules.proxy.durable_bridge_repository import DurableBridgeTranscriptTurn
from app.modules.proxy.replay_safety import responses_input_items_are_self_contained_fresh_replay

_OMIT_OUTPUT_TYPES = frozenset({"reasoning", "tool_search_call", "tool_search_output", "web_search_call"})


def materialize_output_items_from_events(events: Iterable[str]) -> list[JsonValue] | None:
    """Recover terminal output items from the durable SSE event spool.

    Codex commonly sends the complete response with an empty
    ``response.completed.response.output`` and carries the actual assistant or
    tool items in the preceding ``response.output_item.done`` events.  Those
    events are the canonical replay material; treating the empty terminal
    array as authoritative silently produces an unusable transcript.

    The materializer is deliberately fail-closed: malformed items, duplicate
    output indexes, an unfinished added item, or a missing terminal event make
    the operation ineligible for complete-transcript recovery.
    """

    completed_output: list[JsonValue] | None = None
    output_items: dict[int, JsonValue] = {}
    added_indexes: set[int] = set()
    done_indexes: set[int] = set()
    saw_completed = False

    for event_text in events:
        payload = parse_sse_data_json(event_text)
        if not isinstance(payload, dict):
            continue
        event_type = payload.get("type")
        if event_type in {"response.output_item.added", "response.output_item.done"}:
            output_index = payload.get("output_index")
            item = payload.get("item")
            if not isinstance(output_index, int) or output_index < 0 or not isinstance(item, dict):
                return None
            if event_type == "response.output_item.added":
                added_indexes.add(output_index)
                continue
            existing = output_items.get(output_index)
            if existing is not None and _canonical_item(existing) != _canonical_item(item):
                return None
            output_items[output_index] = cast(JsonValue, item)
            done_indexes.add(output_index)
            continue
        if event_type != "response.completed":
            continue
        saw_completed = True
        response = payload.get("response")
        output = response.get("output") if isinstance(response, dict) else None
        if output is not None:
            if not isinstance(output, list) or not all(isinstance(item, dict) for item in output):
                return None
            completed_output = cast(list[JsonValue], output)

    if not saw_completed or added_indexes - done_indexes:
        return None
    if output_items:
        return [output_items[index] for index in sorted(output_items)]
    return completed_output


def build_complete_replay_payload(
    turns: Iterable[DurableBridgeTranscriptTurn],
    *,
    continuation_request_text: str | None = None,
    max_input_items: int = 4096,
    max_bytes: int = 8 * 1024 * 1024,
) -> str | None:
    """Build an unanchored response.create body from a completed turn chain.

    Each durable turn stores the request body sent for that turn and the
    terminal response output.  The client normally sends only the delta after
    ``previous_response_id``; this function inserts the preceding response
    output before each delta and removes the stale anchor.  Ambiguous shapes,
    unsupported output items, or oversized transcripts fail closed.
    """

    materialized = list(turns)
    if not materialized:
        return None

    canonical_input: list[JsonValue] = []
    client_input_history: list[JsonValue] = []
    latest_payload: dict[str, JsonValue] | None = None
    for index, turn in enumerate(materialized):
        try:
            parsed = json.loads(turn.operation.request_text or "")
            _output_items = json.loads(turn.response_output_items_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(parsed, dict) or not isinstance(_output_items, list):
            return None
        if index > 0 and not isinstance(parsed.get("previous_response_id"), str):
            # A missing parent on a non-root turn means the stored chain and
            # request body disagree; do not guess how to join it.
            return None
        latest_payload = dict(parsed)
        _drop_bridge_operation_metadata(latest_payload)
        latest_payload.pop("previous_response_id", None)
        latest_payload.pop("stream", None)
        latest_payload["type"] = "response.create"

        turn_input = _normalize_input(parsed.get("input"))
        if turn_input is None:
            return None
        turn_input = [_strip_item_id(item) for item in turn_input]
        if not _items_are_json_values(turn_input):
            return None

        # Full-history clients may resend the already persisted input prefix.
        # Accept that only when the prefix matches byte-for-byte after IDs are
        # removed; otherwise this is a delta and is appended as-is.
        fresh_input = turn_input
        include_prior_output = index > 0
        prior_output = _sanitize_output_items(materialized[index - 1].response_output_items_json) if index > 0 else []
        if index > 0 and prior_output is None:
            return None
        canonical_with_prior = canonical_input + (prior_output or [])
        if canonical_with_prior and _items_prefix_matches(turn_input, canonical_with_prior):
            fresh_input = turn_input[len(canonical_with_prior) :]
            include_prior_output = False
        elif client_input_history and _items_prefix_matches(turn_input, client_input_history):
            fresh_input = turn_input[len(client_input_history) :]
        elif client_input_history and _items_prefix_matches(client_input_history, turn_input):
            # A shorter resend cannot prove which historical items were
            # intentionally omitted, so it is not a complete transcript.
            return None

        if include_prior_output:
            assert prior_output is not None
            canonical_input.extend(prior_output)
        canonical_input.extend(fresh_input)
        client_input_history.extend(fresh_input)
        if len(canonical_input) > max_input_items:
            return None

    if continuation_request_text is not None:
        try:
            continuation_payload = json.loads(continuation_request_text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(continuation_payload, dict):
            return None
        if materialized[-1].operation.response_id is not None and continuation_payload.get(
            "previous_response_id"
        ) != materialized[-1].operation.response_id:
            return None
        continuation_input = _normalize_input(continuation_payload.get("input"))
        if continuation_input is None:
            return None
        continuation_input = [_strip_item_id(item) for item in continuation_input]
        include_prior_output = True
        latest_prior_output = _sanitize_output_items(materialized[-1].response_output_items_json)
        if latest_prior_output is None:
            return None
        canonical_with_latest_output = canonical_input + latest_prior_output
        if canonical_with_latest_output and _items_prefix_matches(continuation_input, canonical_with_latest_output):
            continuation_input = continuation_input[len(canonical_with_latest_output) :]
            include_prior_output = False
        elif client_input_history and _items_prefix_matches(continuation_input, client_input_history):
            continuation_input = continuation_input[len(client_input_history) :]
        elif client_input_history and _items_prefix_matches(client_input_history, continuation_input):
            return None
        prior_output = latest_prior_output if include_prior_output else []
        if prior_output is None:
            return None
        latest_payload = dict(continuation_payload)
        _drop_bridge_operation_metadata(latest_payload)
        latest_payload.pop("previous_response_id", None)
        latest_payload.pop("stream", None)
        latest_payload["type"] = "response.create"
        latest_payload["input"] = canonical_input + prior_output + continuation_input
        canonical_input = list(latest_payload["input"])
        if len(canonical_input) > max_input_items:
            return None

    if latest_payload is None or not canonical_input:
        return None
    if not responses_input_items_are_self_contained_fresh_replay(canonical_input):
        return None
    latest_payload["input"] = canonical_input
    latest_payload_bytes = json.dumps(latest_payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(latest_payload_bytes) > max_bytes:
        return None
    return latest_payload_bytes.decode("utf-8")


def build_replay_input_snapshot(
    turns: Iterable[DurableBridgeTranscriptTurn],
    *,
    request_text: str,
    response_output_items_json: str,
    max_input_items: int = 4096,
    max_bytes: int = 8 * 1024 * 1024,
) -> str | None:
    """Build a bounded, self-contained input snapshot for a completed turn.

    The snapshot contains the complete replay input *including* the current
    turn's sanitized output.  It is intentionally stored independently from
    ``previous_response_id`` so it can survive upstream response retention
    and let a later continuation start from a fresh response.create body.
    """

    materialized_turns = list(turns)
    if materialized_turns:
        replay_text = build_complete_replay_payload(
            materialized_turns,
            continuation_request_text=request_text,
            max_input_items=max_input_items,
            max_bytes=max_bytes,
        )
    else:
        try:
            parsed = json.loads(request_text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(parsed, dict):
            return None
        input_items = _normalize_input(parsed.get("input"))
        if input_items is None:
            return None
        input_items = [_strip_item_id(item) for item in input_items]
        if not _items_are_json_values(input_items):
            return None
        fresh_payload = dict(parsed)
        _drop_bridge_operation_metadata(fresh_payload)
        fresh_payload.pop("previous_response_id", None)
        fresh_payload.pop("stream", None)
        fresh_payload["type"] = "response.create"
        fresh_payload["input"] = input_items
        replay_text = json.dumps(fresh_payload, ensure_ascii=True, separators=(",", ":"))
        if len(replay_text.encode("utf-8")) > max_bytes:
            return None

    if not replay_text:
        return None
    try:
        replay_payload = json.loads(replay_text)
        output_items = _sanitize_output_items(response_output_items_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(replay_payload, dict) or output_items is None:
        return None
    input_items = _normalize_input(replay_payload.get("input"))
    if input_items is None:
        return None
    snapshot_items = input_items + output_items
    if len(snapshot_items) > max_input_items or not _items_are_json_values(snapshot_items):
        return None
    snapshot_text = json.dumps(snapshot_items, ensure_ascii=True, separators=(",", ":"))
    if len(snapshot_text.encode("utf-8")) > max_bytes:
        return None
    return snapshot_text


def _normalize_input(value: JsonValue | None) -> list[JsonValue] | None:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str) and value:
        return [{"type": "message", "role": "user", "content": value}]
    return None


def _drop_bridge_operation_metadata(payload: dict[str, JsonValue]) -> None:
    metadata = payload.get("client_metadata")
    if not isinstance(metadata, dict) or "codex_lb_operation_id" not in metadata:
        return
    cleaned = dict(metadata)
    cleaned.pop("codex_lb_operation_id", None)
    if cleaned:
        payload["client_metadata"] = cleaned
    else:
        payload.pop("client_metadata", None)


def _strip_item_id(item: JsonValue) -> JsonValue:
    if not isinstance(item, dict):
        return item
    stripped = dict(item)
    stripped.pop("id", None)
    return stripped


def _items_prefix_matches(items: list[JsonValue], prefix: list[JsonValue]) -> bool:
    if len(items) < len(prefix):
        return False
    return all(_canonical_item(left) == _canonical_item(right) for left, right in zip(items, prefix))


def _canonical_item(item: JsonValue) -> str:
    return json.dumps(_strip_item_id(item), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _sanitize_output_items(raw: str) -> list[JsonValue] | None:
    try:
        output = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(output, list):
        return None
    sanitized: list[JsonValue] = []
    for item in output:
        if not isinstance(item, dict):
            return None
        item_type = item.get("type")
        if item_type in _OMIT_OUTPUT_TYPES:
            continue
        if not isinstance(item_type, str):
            return None
        sanitized_item = _strip_item_id(item)
        sanitized.append(sanitized_item)
    return sanitized


def _items_are_json_values(items: list[JsonValue]) -> bool:
    return all(isinstance(item, (dict, list, str, int, float, bool)) or item is None for item in items)
