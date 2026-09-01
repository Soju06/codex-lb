"""Reconstruct a bounded, self-contained Responses replay from durable turns."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import cast

from app.core.types import JsonValue
from app.core.utils.sse import parse_sse_data_json
from app.modules.proxy.durable_bridge_repository import DurableBridgeTranscriptTurn
from app.modules.proxy.replay_safety import (
    responses_input_items_are_self_contained_fresh_replay,
    responses_payload_is_account_neutral_fresh_replay,
)

_OMIT_OUTPUT_TYPES = frozenset({"reasoning", "tool_search_call", "tool_search_output", "web_search_call"})
_REPLAY_TOOL_CALL_TYPES = frozenset({"function_call", "custom_tool_call", "apply_patch_call"})
_REPLAY_TOOL_OUTPUT_TYPES = frozenset({"function_call_output", "custom_tool_call_output", "apply_patch_call_output"})
_LEGACY_TURN_METADATA_FIELDS = frozenset({"turn_id"})
_LEGACY_INTERNAL_METADATA_FIELDS = frozenset({"create_time", "turn_id"})


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
            if type(output_index) is not int or output_index < 0 or not isinstance(item, dict):
                return None
            if event_type == "response.output_item.added":
                if output_index in added_indexes:
                    return None
                added_indexes.add(output_index)
                continue
            if output_index in done_indexes:
                return None
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
        output_indexes = sorted(output_items)
        if output_indexes != list(range(len(output_indexes))):
            return None
        return [output_items[index] for index in sorted(output_items)]
    return completed_output


def build_complete_replay_payload(
    turns: Iterable[DurableBridgeTranscriptTurn],
    *,
    continuation_request_text: str | None = None,
    allow_unanchored_continuation: bool = False,
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
        turn_input = _sanitize_replay_items(turn_input)
        if not _items_are_json_values(turn_input):
            return None

        # Full-history clients may resend the already persisted input prefix.
        # Accept that only when the prefix matches byte-for-byte after IDs are
        # removed; otherwise this is a delta and is appended as-is.
        fresh_input = turn_input
        prior_turn_includes_output = bool(
            index > 0 and getattr(materialized[index - 1], "replay_input_includes_response_output", False)
        )
        include_prior_output = index > 0 and not prior_turn_includes_output
        prior_output = _sanitize_output_items(materialized[index - 1].response_output_items_json) if index > 0 else []
        if index > 0 and prior_output is None:
            return None
        canonical_with_prior = canonical_input + (prior_output or []) if include_prior_output else canonical_input
        if (
            canonical_with_prior
            and (matched_input := _strip_omitted_output_prefix(turn_input, canonical_with_prior)) is not None
        ):
            fresh_input = matched_input
            include_prior_output = False
        elif (
            index > 0
            and prior_output
            and (matched_input := _strip_omitted_output_prefix(turn_input, prior_output)) is not None
        ):
            # Responses clients may send the immediately preceding output as
            # the first part of a delta (for example a function_call followed
            # by its function_call_output).  It is already inserted below;
            # strip the echoed prefix so tool call IDs are not duplicated.
            fresh_input = matched_input
        elif (
            index > 0
            and prior_output
            and (matched_input := _strip_omitted_output_subsequence(turn_input, prior_output)) is not None
        ):
            # Some clients place a short fresh prefix before echoing the
            # immediately preceding response. Remove only that exact echoed
            # subsequence so the stored output is inserted once below.
            fresh_input = matched_input
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
        continuation_previous_response_id = continuation_payload.get("previous_response_id")
        if (
            materialized[-1].operation.response_id is not None
            and continuation_previous_response_id != materialized[-1].operation.response_id
            and not (allow_unanchored_continuation and continuation_previous_response_id in (None, ""))
        ):
            return None
        continuation_input = _normalize_input(continuation_payload.get("input"))
        if continuation_input is None:
            return None
        continuation_input = _sanitize_replay_items(continuation_input)
        latest_prior_output = _sanitize_output_items(materialized[-1].response_output_items_json)
        if latest_prior_output is None:
            return None
        replay_input_includes_latest_output = bool(
            getattr(materialized[-1], "replay_input_includes_response_output", False)
        )
        # Synthetic snapshot roots already contain their terminal output in
        # ``canonical_input``.  A continuation may send only the new tool
        # output (without echoing the preceding function call), so default to
        # not appending that output a second time.  Ordinary turns retain the
        # historical behavior and insert their stored output once.
        include_prior_output = not replay_input_includes_latest_output
        canonical_with_latest_output = (
            canonical_input if replay_input_includes_latest_output else canonical_input + latest_prior_output
        )
        if (
            allow_unanchored_continuation
            and continuation_previous_response_id in (None, "")
            and _items_prefix_matches(continuation_input, canonical_input)
        ):
            # A full-history Codex request already contains the durable
            # transcript, including the latest response output. Keep only its
            # new suffix; appending ``latest_prior_output`` again would drop
            # the assistant turn from the reconstructed ordering.
            continuation_input = continuation_input[len(canonical_input) :]
            include_prior_output = False
        elif (
            canonical_with_latest_output
            and (matched_input := _strip_omitted_output_prefix(continuation_input, canonical_with_latest_output))
            is not None
        ):
            continuation_input = matched_input
            include_prior_output = False
        elif (
            latest_prior_output
            and (matched_input := _strip_omitted_output_prefix(continuation_input, latest_prior_output)) is not None
        ):
            # A delta may echo only the immediately preceding output rather
            # than the full canonical history.  Strip that output; synthetic
            # snapshot roots already contain it, while ordinary turns need it
            # inserted once below.
            continuation_input = matched_input
            include_prior_output = not replay_input_includes_latest_output
        elif (
            latest_prior_output
            and (matched_input := _strip_omitted_output_subsequence(continuation_input, latest_prior_output))
            is not None
        ):
            # A continuation may echo the prior response after a short
            # developer/user prefix rather than at index zero.
            continuation_input = matched_input
            include_prior_output = not replay_input_includes_latest_output
        elif (
            continuation_previous_response_id in (None, "")
            and client_input_history
            and _items_prefix_matches(continuation_input, client_input_history)
        ):
            continuation_input = continuation_input[len(client_input_history) :]
        elif (
            continuation_previous_response_id in (None, "")
            and client_input_history
            and _items_prefix_matches(client_input_history, continuation_input)
        ):
            return None
        elif allow_unanchored_continuation and continuation_previous_response_id in (None, ""):
            # An unanchored continuation must prove how its input relates to
            # the durable prefix. If the client compacted or otherwise
            # diverged from that prefix, concatenating both histories would
            # silently duplicate or contradict context. Fail closed rather
            # than dispatching an unproven replay.
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
    deduplicated_input = _deduplicate_exact_replayed_tool_items(canonical_input)
    if deduplicated_input is None:
        return None
    canonical_input = deduplicated_input
    if not responses_input_items_are_self_contained_fresh_replay(canonical_input):
        return None
    latest_payload["input"] = canonical_input
    validation_payload = dict(latest_payload)
    # ``type`` is a transport discriminator, not part of the Responses
    # request payload accepted by the account-neutral replay validator.
    validation_payload.pop("type", None)
    if not responses_payload_is_account_neutral_fresh_replay(validation_payload):
        return None
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
        input_items = _sanitize_replay_items(input_items)
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


def build_unanchored_root_replay_payload(
    request_text: str,
    *,
    max_input_items: int = 4096,
    max_bytes: int = 8 * 1024 * 1024,
) -> str | None:
    """Sanitize a client-supplied full-history root for explicit recovery.

    Older sessions can predate the durable operation transcript, leaving no
    completed turns from which :func:`build_complete_replay_payload` can
    reconstruct a chain.  When the client has nevertheless supplied a
    self-contained history, the opt-in recovery path can use that body as the
    replay source.  This helper deliberately accepts only an unanchored,
    account-neutral payload and strips the same response-owned fields as the
    durable transcript builder.
    """

    try:
        payload = json.loads(request_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("previous_response_id") not in (None, ""):
        return None
    if payload.get("conversation") not in (None, ""):
        return None
    input_items = _normalize_input(payload.get("input"))
    if input_items is None:
        return None
    input_items = _sanitize_replay_items(input_items)
    if not input_items or len(input_items) > max_input_items:
        return None
    if not _items_are_json_values(input_items):
        return None
    if not responses_input_items_are_self_contained_fresh_replay(input_items):
        return None
    replay_payload = dict(payload)
    _drop_bridge_operation_metadata(replay_payload)
    replay_payload.pop("previous_response_id", None)
    replay_payload.pop("stream", None)
    replay_payload["type"] = "response.create"
    replay_payload["input"] = input_items
    validation_payload = dict(replay_payload)
    # ``type`` is a transport discriminator, not part of the Responses
    # request payload accepted by the account-neutral replay validator.
    validation_payload.pop("type", None)
    if not responses_payload_is_account_neutral_fresh_replay(validation_payload):
        return None
    replay_bytes = json.dumps(replay_payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(replay_bytes) > max_bytes:
        return None
    return replay_bytes.decode("utf-8")


def derive_replay_input_turn_count(request_text: str) -> int | None:
    """Derive conversation turns represented by a self-contained root body.

    Ordinary roots represent one user request.  A client may also submit a
    self-contained full history without ``previous_response_id``; count user
    messages as conversational turn boundaries.  Shapes that cannot be
    validated as account-neutral replay remain unknown so callers fail closed
    instead of undercounting arbitrary history.
    """

    try:
        payload = json.loads(request_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("previous_response_id") not in (None, ""):
        return None
    raw_input = payload.get("input")
    input_items = _normalize_input(raw_input)
    if input_items is None:
        return None
    input_items = _sanitize_replay_items(input_items)
    if not input_items or not _items_are_json_values(input_items):
        return None
    if not responses_input_items_are_self_contained_fresh_replay(input_items):
        return None
    validation_payload = dict(payload)
    validation_payload.pop("type", None)
    _drop_bridge_operation_metadata(validation_payload)
    validation_payload["input"] = input_items
    if not responses_payload_is_account_neutral_fresh_replay(validation_payload):
        return None
    if isinstance(raw_input, str):
        return 1
    user_turn_count = sum(
        1
        for item in input_items
        if isinstance(item, dict) and item.get("type") in (None, "message") and item.get("role") == "user"
    )
    # A settled tool call/output pair is a subsequent Responses turn even
    # when the client keeps the original user message in the same root body.
    # The validator above proves that every output settles a distinct call, so
    # counting outputs is conservative for parallel tool batches and avoids
    # admitting arbitrarily deep tool continuations under a small turn bound.
    tool_continuation_count = sum(
        1 for item in input_items if isinstance(item, dict) and item.get("type") in _REPLAY_TOOL_OUTPUT_TYPES
    )
    if user_turn_count > 0 or tool_continuation_count > 0:
        return user_turn_count + tool_continuation_count
    # A single validated non-message item can still be the current request;
    # multiple such items do not expose a trustworthy conversational boundary.
    return 1 if len(input_items) == 1 else None


def _normalize_input(value: JsonValue | None) -> list[JsonValue] | None:
    if isinstance(value, list):
        return cast(list[JsonValue], list(value))
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


def _sanitize_replay_items(items: list[JsonValue]) -> list[JsonValue]:
    """Remove only response-owned fields from persisted replay items.

    Older snapshots captured provider bookkeeping that the account-neutral
    validator correctly rejects today.  The exact legacy shapes handled here
    are generated metadata, not user input; unknown fields remain untouched so
    the validator still fails closed for anything ambiguous.
    """

    return [_sanitize_replay_item(item) for item in items if not _is_omitted_output_item(item)]


def _sanitize_replay_item(item: JsonValue) -> JsonValue:
    if not isinstance(item, dict):
        return item
    sanitized = cast(dict[str, JsonValue], _strip_item_id(item))
    metadata = sanitized.get("metadata")
    if (
        isinstance(metadata, dict)
        and set(metadata) == _LEGACY_TURN_METADATA_FIELDS
        and _is_nonblank_string(metadata.get("turn_id"))
    ):
        sanitized.pop("metadata", None)
    internal_metadata = sanitized.get("internal_chat_message_metadata_passthrough")
    if (
        isinstance(internal_metadata, dict)
        and set(internal_metadata) == _LEGACY_INTERNAL_METADATA_FIELDS
        and _is_nonblank_string(internal_metadata.get("turn_id"))
        and isinstance(internal_metadata.get("create_time"), (int, float))
        and not isinstance(internal_metadata.get("create_time"), bool)
    ):
        sanitized["internal_chat_message_metadata_passthrough"] = {
            "turn_id": internal_metadata["turn_id"],
        }
    content = sanitized.get("content")
    if isinstance(content, list):
        sanitized["content"] = [_sanitize_replay_content_part(part) for part in content]
    if sanitized.get("type") in {"function_call_output", "custom_tool_call_output"}:
        output = sanitized.get("output")
        if isinstance(output, list):
            sanitized["output"] = [
                part
                for part in output
                if not (
                    isinstance(part, dict) and part.get("type") in {"input_text", "text"} and part.get("text") == ""
                )
            ]
    return sanitized


def _sanitize_replay_content_part(part: JsonValue) -> JsonValue:
    if not isinstance(part, dict) or part.get("type") != "output_text":
        return part
    sanitized = dict(part)
    # Responses output annotations and logprobs are provider-owned metadata;
    # neither is accepted when the same text is sent back as fresh input.
    sanitized.pop("annotations", None)
    sanitized.pop("logprobs", None)
    return sanitized


def _is_nonblank_string(value: JsonValue | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _items_prefix_matches(items: list[JsonValue], prefix: list[JsonValue]) -> bool:
    if len(items) < len(prefix):
        return False
    return all(_items_match_for_echo(left, right) for left, right in zip(items, prefix))


def _strip_omitted_output_prefix(items: list[JsonValue], prefix: list[JsonValue]) -> list[JsonValue] | None:
    """Strip a stored output prefix while tolerating omitted output echoes.

    Reasoning and hosted search/tool envelopes are intentionally removed from
    account-neutral replay snapshots. Codex may still echo those items before
    the retained tool call in a continuation request. They are safe to drop,
    but only after every non-omitted item matches the durable prefix.
    """
    item_index = 0
    for expected in prefix:
        while item_index < len(items) and _is_omitted_output_item(items[item_index]):
            item_index += 1
        if item_index >= len(items) or not _items_match_for_echo(items[item_index], expected):
            return None
        item_index += 1
    while item_index < len(items) and _is_omitted_output_item(items[item_index]):
        item_index += 1
    return items[item_index:]


def _strip_omitted_output_subsequence(items: list[JsonValue], subsequence: list[JsonValue]) -> list[JsonValue] | None:
    """Remove one exact echoed response sequence after fresh input.

    Prefix-only matching misses clients that send a short new message before
    echoing the immediately preceding assistant/tool output. This helper is
    used only after stricter prefix matching fails and preserves all items
    around the one canonical subsequence it removes.
    """
    if not subsequence or len(items) < len(subsequence):
        return None
    for start in range(len(items) - len(subsequence) + 1):
        if all(_items_match_for_echo(items[start + offset], expected) for offset, expected in enumerate(subsequence)):
            return items[:start] + items[start + len(subsequence) :]
    return None


def _is_omitted_output_item(item: JsonValue) -> bool:
    return isinstance(item, dict) and item.get("type") in _OMIT_OUTPUT_TYPES


def _canonical_item(item: JsonValue) -> str:
    sanitized = _sanitize_replay_item(item)
    if isinstance(sanitized, dict):
        sanitized = dict(sanitized)
        sanitized.pop("internal_chat_message_metadata_passthrough", None)
    return json.dumps(sanitized, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _canonical_item_without_status(item: JsonValue) -> str:
    """Canonicalize an echoed item while tolerating an omitted status."""
    sanitized = _sanitize_replay_item(item)
    if isinstance(sanitized, dict):
        sanitized = dict(sanitized)
        sanitized.pop("status", None)
        sanitized.pop("internal_chat_message_metadata_passthrough", None)
    return json.dumps(sanitized, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _items_match_for_echo(left: JsonValue, right: JsonValue) -> bool:
    """Match echoed items while rejecting explicit status conflicts.

    Upstream echoes may omit ``status`` even when the stored item has it, but
    two explicit, different statuses describe different tool execution state
    and must not be stripped as if they were the same item.
    """
    left_sanitized = _sanitize_replay_item(left)
    right_sanitized = _sanitize_replay_item(right)
    if isinstance(left_sanitized, dict) and isinstance(right_sanitized, dict):
        if "status" in left_sanitized and "status" in right_sanitized:
            if left_sanitized["status"] != right_sanitized["status"]:
                return False
    return _canonical_item_without_status(left) == _canonical_item_without_status(right)


def _canonical_tool_echo_item(item: JsonValue) -> str:
    """Canonicalize a tool echo without erasing execution status conflicts."""
    return json.dumps(_sanitize_replay_item(item), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


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
        sanitized_item = _sanitize_replay_item(item)
        sanitized.append(sanitized_item)
    return sanitized


def _items_are_json_values(items: list[JsonValue]) -> bool:
    return all(isinstance(item, (dict, list, str, int, float, bool)) or item is None for item in items)


def _deduplicate_exact_replayed_tool_items(items: list[JsonValue]) -> list[JsonValue] | None:
    """Drop exact duplicate tool echoes while rejecting conflicting IDs.

    A compacted client request can contain a tool call/output pair that is
    already present in the durable snapshot. Repeating the same call ID would
    fail fresh-replay validation even though it does not represent a second
    execution. Only byte-equivalent call/output duplicates are removed;
    conflicting shapes remain ineligible.
    """
    first_calls: dict[str, tuple[str, str, int]] = {}
    first_outputs: dict[str, tuple[str, str, int]] = {}
    drop: set[int] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type not in _REPLAY_TOOL_CALL_TYPES and item_type not in _REPLAY_TOOL_OUTPUT_TYPES:
            continue
        call_id = item.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            continue
        canonical = _canonical_tool_echo_item(item)
        target = first_calls if item_type in _REPLAY_TOOL_CALL_TYPES else first_outputs
        previous = target.get(call_id)
        if previous is None:
            target[call_id] = (cast(str, item_type), canonical, index)
            continue
        previous_type, previous_canonical, _previous_index = previous
        if previous_type != item_type or previous_canonical != canonical:
            return None
        drop.add(index)
    return [item for index, item in enumerate(items) if index not in drop]
