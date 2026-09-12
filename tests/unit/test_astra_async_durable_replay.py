from __future__ import annotations

import pytest

from app.core.types import JsonValue
from app.modules.proxy.replay_safety import responses_input_suffix_retains_prior_output

pytestmark = pytest.mark.unit


@pytest.fixture(params=["function_call", "custom_tool_call"])
def async_call(request: pytest.FixtureRequest) -> dict[str, JsonValue]:
    call_type = request.param
    return {
        "type": call_type,
        "call_id": "async_1",
        "name": "slow",
        "arguments" if call_type == "function_call" else "input": "{}",
        "async": True,
    }


@pytest.fixture
def history(async_call: dict[str, JsonValue]) -> list[JsonValue]:
    return [
        {"role": "user", "content": "first"},
        async_call,
        {"role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "started"}]},
    ]


@pytest.mark.parametrize("stored_count", [1, 2], ids=["suffix-call", "prefix-call"])
@pytest.mark.parametrize("result_position", ["unresolved", "before-answer", "after-answer"])
def test_no_manifest_retains_async_history(history, async_call, stored_count, result_position) -> None:
    output = {"type": f"{async_call['type']}_output", "call_id": "async_1", "output": "done"}
    if result_position == "before-answer":
        history.insert(2, output)
    if result_position == "after-answer":
        history.append(output)
    else:
        history.append({"role": "user", "content": "continue"})

    assert responses_input_suffix_retains_prior_output(history, stored_count=stored_count)


@pytest.mark.parametrize("stored_count", [1, 2], ids=["suffix-call", "prefix-call"])
@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"call_id": None}, id="null-id"),
        pytest.param({"call_id": []}, id="list-id"),
        pytest.param({"call_id": " \t"}, id="blank-id"),
        pytest.param({"name": ""}, id="blank-name"),
        pytest.param({"caller": {"type": "hosted"}}, id="hosted-caller"),
        pytest.param({"unknown": True}, id="unknown-field"),
    ],
)
def test_no_manifest_rejects_malformed_async_call(history, async_call, stored_count, fields) -> None:
    async_call.update(fields)
    history.append({"role": "user", "content": "continue"})

    assert not responses_input_suffix_retains_prior_output(history, stored_count=stored_count)


@pytest.mark.parametrize("stored_count", [1, 2], ids=["suffix-call", "prefix-call"])
@pytest.mark.parametrize(
    "defect",
    ["missing-output", "empty-parts", "wrong-type", "wrong-id", "duplicate", "unknown-field", "hosted-caller"],
)
def test_no_manifest_rejects_invalid_async_output(history, async_call, stored_count, defect) -> None:
    output = {"type": f"{async_call['type']}_output", "call_id": "async_1", "output": "done"}
    if defect == "missing-output":
        output.pop("output")
    elif defect == "empty-parts":
        output["output"] = []
    elif defect == "wrong-type":
        output["type"] = "custom_tool_call_output" if async_call["type"] == "function_call" else "function_call_output"
    elif defect == "wrong-id":
        output["call_id"] = "other"
    elif defect == "unknown-field":
        output["unknown"] = True
    elif defect == "hosted-caller":
        output["caller"] = {"type": "hosted"}
    history.append(output)
    if defect == "duplicate":
        history.append(output)
    history.append({"role": "user", "content": "continue"})

    assert not responses_input_suffix_retains_prior_output(history, stored_count=stored_count)


@pytest.mark.parametrize("settled", [False, True])
@pytest.mark.parametrize("earlier_answer", [False, True], ids=["no-answer", "stale-answer"])
def test_no_manifest_async_items_do_not_replace_assistant_boundary(async_call, settled, earlier_answer) -> None:
    items: list[JsonValue] = [{"role": "user", "content": "first"}, async_call]
    if earlier_answer:
        items.insert(1, {"role": "assistant", "content": [{"type": "output_text", "text": "earlier"}]})
    if settled:
        items.append({"type": f"{async_call['type']}_output", "call_id": "async_1", "output": "done"})
    items.append({"role": "user", "content": "continue"})

    assert not responses_input_suffix_retains_prior_output(items, stored_count=1)


@pytest.mark.parametrize("defect", ["pending-sync", "duplicate-call", "prefix-id-collision"])
def test_no_manifest_async_call_does_not_waive_sync_or_identity_proof(history, async_call, defect) -> None:
    if defect == "pending-sync":
        history.insert(1, {"type": "function_call", "call_id": "sync_1", "name": "now", "arguments": "{}"})
    else:
        history.insert(2, dict(async_call))
    history.append({"role": "user", "content": "continue"})

    assert not responses_input_suffix_retains_prior_output(
        history, stored_count=2 if defect == "prefix-id-collision" else 1
    )
