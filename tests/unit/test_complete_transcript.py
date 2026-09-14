from __future__ import annotations

from app.core.types import JsonValue
from app.modules.proxy.complete_transcript import (
    _deduplicate_exact_replayed_tool_items,
    _items_match_for_echo,
    _output_item_identities_match,
    _output_item_identity,
    _output_item_identity_is_valid,
)


def test_output_item_identity_collects_stable_fields_and_validates_tool_shapes() -> None:
    call = {"id": "item_1", "call_id": "call_1", "type": "function_call", "name": "shell"}
    identity = _output_item_identity(call)

    assert identity == {"id": "item_1", "call_id": "call_1", "type": "function_call"}
    assert _output_item_identity_is_valid(identity)
    assert _output_item_identity_is_valid({"type": "message", "id": "msg_1"})
    assert _output_item_identity_is_valid({"type": "compaction"})
    assert not _output_item_identity_is_valid({"type": "message"})
    assert not _output_item_identity_is_valid({"type": "function_call", "call_id": "call_1"})
    assert not _output_item_identity_is_valid({"type": "function_call_output"})


def test_output_item_identity_matching_requires_fields_seen_on_added_item() -> None:
    expected = {"id": "item_1", "call_id": "call_1", "type": "function_call"}

    assert _output_item_identities_match(expected, {**expected, "status": "completed"})
    assert not _output_item_identities_match(expected, {"call_id": "call_1", "type": "function_call"})
    assert not _output_item_identities_match(expected, {**expected, "id": "item_2"})


def test_echo_matching_ignores_provider_item_id_and_omitted_status() -> None:
    stored: JsonValue = {
        "id": "provider_item_1",
        "type": "function_call",
        "call_id": "call_1",
        "name": "shell",
        "arguments": "{}",
        "status": "completed",
    }
    echoed: JsonValue = {
        "id": "provider_item_2",
        "type": "function_call",
        "call_id": "call_1",
        "name": "shell",
        "arguments": "{}",
    }

    assert _items_match_for_echo(stored, echoed)
    assert not _items_match_for_echo(stored, {**echoed, "status": "failed"})
    assert not _items_match_for_echo(stored, {**echoed, "arguments": '{"path":"different"}'})


def test_exact_tool_call_and_output_echoes_are_deduplicated() -> None:
    call = {"id": "item_1", "type": "function_call", "call_id": "call_1", "name": "shell", "arguments": "{}"}
    output = {"type": "function_call_output", "call_id": "call_1", "output": "done"}
    items: list[JsonValue] = [
        {"type": "message", "role": "user", "content": "run"},
        call,
        {**call, "id": "echoed_item"},
        output,
        {**output},
    ]

    assert _deduplicate_exact_replayed_tool_items(items) == [items[0], call, output]


def test_tool_echo_deduplication_fails_closed_on_conflicting_content() -> None:
    call = {"type": "function_call", "call_id": "call_1", "name": "shell", "arguments": "{}"}
    conflicting = {**call, "arguments": '{"cmd":"rm -rf /"}'}

    assert _deduplicate_exact_replayed_tool_items([call, conflicting]) is None


def test_tool_echo_deduplication_preserves_unhashable_malformed_types() -> None:
    malformed: JsonValue = {"type": [], "call_id": "call_1"}

    assert _deduplicate_exact_replayed_tool_items([malformed]) == [malformed]
