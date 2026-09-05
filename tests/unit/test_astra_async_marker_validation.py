from __future__ import annotations

import pytest

from app.core.types import JsonValue
from app.modules.proxy.replay_safety import (
    responses_input_suffix_matches_pending_tool_calls,
    responses_input_suffix_retains_prior_output,
    responses_payload_is_account_neutral_fresh_replay,
)

pytestmark = pytest.mark.unit


@pytest.fixture(params=["function_call", "custom_tool_call"])
def call(request: pytest.FixtureRequest) -> dict[str, JsonValue]:
    call_type = request.param
    return {
        "type": call_type,
        "call_id": "marker_1",
        "name": "work",
        "arguments" if call_type == "function_call" else "input": "{}",
    }


@pytest.fixture(
    params=[
        pytest.param(({}, True), id="omitted"),
        pytest.param(({"async": False}, True), id="false"),
        pytest.param(({"async": True}, True), id="true"),
        pytest.param(({"async": "true"}, False), id="string"),
        pytest.param(({"async": []}, False), id="list"),
        pytest.param(({"async": None}, False), id="null"),
        pytest.param(({"async": 0}, False), id="zero"),
        pytest.param(({"async": 1}, False), id="one"),
        pytest.param(({"async": {}}, False), id="dict"),
    ]
)
def marked_call(call, request):
    fields, valid = request.param
    return {**call, **fields}, valid


def test_account_neutral_replay_validates_async_marker(marked_call) -> None:
    call, valid = marked_call
    items = [call, {"type": f"{call['type']}_output", "call_id": "marker_1", "output": "done"}]

    assert responses_payload_is_account_neutral_fresh_replay({"input": items}) is valid


@pytest.mark.parametrize("call_in_prefix", [False, True], ids=["suffix-call", "prefix-call"])
@pytest.mark.parametrize("proof", ["manifest", "retained-output"])
def test_durable_replay_validates_async_marker(marked_call, call_in_prefix, proof) -> None:
    call, valid = marked_call
    items: list[JsonValue] = [
        {"role": "user", "content": "first"},
        call,
        {"type": f"{call['type']}_output", "call_id": "marker_1", "output": "done"},
    ]
    stored_count = len(items) if call_in_prefix else 1
    if proof == "manifest":
        items.extend(
            [
                {"type": "function_call", "call_id": "sync_1", "name": "now", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "sync_1", "output": "ok"},
            ]
        )
        expected_calls = {"sync_1": "function_call"}
        if not call_in_prefix and call.get("async") is not True:
            expected_calls["marker_1"] = call["type"]
        assert (
            responses_input_suffix_matches_pending_tool_calls(
                items, stored_count=stored_count, pending_tool_calls=expected_calls
            )
            is valid
        )
    else:
        items.extend(
            [
                {"role": "assistant", "content": [{"type": "output_text", "text": "finished"}]},
                {"role": "user", "content": "continue"},
            ]
        )
        assert responses_input_suffix_retains_prior_output(items, stored_count=stored_count) is valid


@pytest.mark.parametrize("fields,expected", [({}, False), ({"async": False}, False), ({"async": True}, True)])
def test_only_true_async_marker_allows_unsettled_call(call, fields, expected) -> None:
    assert responses_payload_is_account_neutral_fresh_replay({"input": [{**call, **fields}]}) is expected


@pytest.mark.parametrize("call_id", ["", " ", "\t\n", "valid-id"])
@pytest.mark.parametrize("settled", [False, True])
def test_async_replay_requires_nonblank_identity(call, call_id, settled) -> None:
    items: list[JsonValue] = [{**call, "call_id": call_id, "async": True}]
    if settled:
        items.append({"type": f"{call['type']}_output", "call_id": call_id, "output": "done"})
    assert responses_payload_is_account_neutral_fresh_replay({"input": items}) is bool(call_id.strip())


@pytest.mark.parametrize("call_id", [" ", "\t\n", "valid-id"])
@pytest.mark.parametrize("call_in_prefix", [False, True])
def test_durable_async_replay_requires_nonblank_identity(call, call_id, call_in_prefix) -> None:
    first = {"role": "user", "content": "first"}
    async_call = {**call, "call_id": call_id, "async": True}
    items: list[JsonValue] = [first, async_call]
    stored_count = 2 if call_in_prefix else 1
    sync = {"type": "function_call", "call_id": "sync_1", "name": "now", "arguments": "{}"}
    output = {"type": "function_call_output", "call_id": "sync_1", "output": "ok"}
    assert responses_input_suffix_matches_pending_tool_calls(
        [*items, sync, output], stored_count=stored_count, pending_tool_calls={"sync_1": "function_call"}
    ) is bool(call_id.strip())
    answer: JsonValue = {"role": "assistant", "content": [{"type": "output_text", "text": "done"}]}
    assert responses_input_suffix_retains_prior_output(
        [*items, answer, {"role": "user", "content": "next"}], stored_count=stored_count
    ) is bool(call_id.strip())
