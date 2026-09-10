"""``strip_input_item_ids``: the one body mutation of the neutral release (#2123 WP-C2, design v3 §7.2).

Property (hypothesis over arbitrary JSON arrays): the result equals the input
modulo the top-level ``id`` of object items -- nothing else is touched, added or
reordered; non-object items come back as the same objects; the projection is
idempotent and never mutates its argument.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.types import JsonValue
from app.modules.proxy.replay_safety import strip_input_item_ids
from tests.unit.hypothesis_strategies import json_arrays, json_values

pytestmark = pytest.mark.unit


def _without_top_level_id(item: JsonValue) -> JsonValue:
    if isinstance(item, dict):
        return {key: value for key, value in item.items() if key != "id"}
    return item


_items_with_ids = st.lists(
    st.one_of(
        json_values,
        st.dictionaries(st.text(max_size=8), json_values, max_size=4).map(lambda d: {**d, "id": "rs_source_1"}),
    ),
    max_size=8,
)


@settings(max_examples=150, deadline=None)
@given(items=st.one_of(json_arrays, _items_with_ids))
def test_round_trip_equals_input_modulo_top_level_ids(items: list[JsonValue]) -> None:
    original = copy.deepcopy(items)
    stripped = strip_input_item_ids(items)

    assert stripped == [_without_top_level_id(item) for item in original]
    assert items == original, "the argument is never mutated"
    assert strip_input_item_ids(stripped) == stripped, "idempotent"
    assert all(not (isinstance(item, dict) and "id" in item) for item in stripped)
    for before, after in zip(items, stripped, strict=True):
        if not isinstance(before, dict):
            assert after is before, "non-object items come back as the same object"
        elif "id" not in before:
            assert after is before, "an object without a top-level id is not copied"
        else:
            assert isinstance(after, dict) and set(after) == set(before) - {"id"}


def test_nested_ids_and_other_fields_are_untouched() -> None:
    items: list[JsonValue] = [
        {
            "id": "rs_1",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hi", "id": "nested-stays"}],
        },
        {"type": "function_call", "call_id": "call_1", "name": "shell", "arguments": "{}", "id": "fc_1"},
        {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
        "plain string item",
        42,
        None,
    ]
    stripped = strip_input_item_ids(items)
    assert stripped == [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hi", "id": "nested-stays"}],
        },
        {"type": "function_call", "call_id": "call_1", "name": "shell", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
        "plain string item",
        42,
        None,
    ]
    assert stripped[2] is items[2]
    assert strip_input_item_ids([]) == []
