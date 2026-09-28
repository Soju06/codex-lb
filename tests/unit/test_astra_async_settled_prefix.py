from __future__ import annotations

import pytest

from app.core.types import JsonValue
from app.modules.proxy.replay_safety import (
    responses_input_suffix_matches_pending_tool_calls,
    responses_input_suffix_retains_prior_output,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("call_type", ["function_call", "custom_tool_call"])
@pytest.mark.parametrize("intervening_turn", [False, True])
@pytest.mark.parametrize("proof", ["manifest", "retained-output"])
@pytest.mark.parametrize(
    "defect",
    [
        "valid",
        "blank-id",
        "blank-name",
        "missing-body",
        "missing-output",
        "unknown-call-field",
        "unknown-output-field",
        "hosted-caller",
        "account-field",
    ],
)
def test_settled_async_prefix_is_validated(call_type, intervening_turn, proof, defect) -> None:
    call: dict[str, JsonValue] = {
        "type": call_type,
        "call_id": "async_1",
        "name": "work",
        "arguments" if call_type == "function_call" else "input": "{}",
        "async": True,
    }
    output: dict[str, JsonValue] = {"type": f"{call_type}_output", "call_id": "async_1", "output": "done"}
    if defect == "blank-id":
        call["call_id"] = output["call_id"] = " \t"
    elif defect == "blank-name":
        call["name"] = ""
    elif defect == "missing-body":
        call.pop("arguments" if call_type == "function_call" else "input")
    elif defect == "missing-output":
        output.pop("output")
    elif defect == "unknown-call-field":
        call["unknown"] = True
    elif defect == "unknown-output-field":
        output["unknown"] = True
    elif defect == "hosted-caller":
        call["caller"] = {"type": "hosted"}
    elif defect == "account-field":
        output["container_id"] = "container_example"
    items: list[JsonValue] = [{"role": "user", "content": "first"}, call]
    if intervening_turn:
        items.append({"role": "user", "content": "intervening"})
    items.append(output)
    stored_count = len(items)
    if proof == "manifest":
        items.extend(
            [
                {"type": "function_call", "call_id": "sync_1", "name": "now", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "sync_1", "output": "ok"},
            ]
        )
        valid = responses_input_suffix_matches_pending_tool_calls(
            items, stored_count=stored_count, pending_tool_calls={"sync_1": "function_call"}
        )
    else:
        items.extend(
            [
                {"role": "assistant", "content": [{"type": "output_text", "text": "finished"}]},
                {"role": "user", "content": "continue"},
            ]
        )
        valid = responses_input_suffix_retains_prior_output(items, stored_count=stored_count)
    assert valid is (defect == "valid")
