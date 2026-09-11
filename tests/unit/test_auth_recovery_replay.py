from __future__ import annotations

from copy import deepcopy

import pytest

from app.core.types import JsonValue
from app.modules.proxy.replay_safety import (
    project_responses_input_for_auth_recovery,
    responses_payload_is_account_neutral_fresh_replay,
)

_REASONING: dict[str, JsonValue] = {
    "type": "reasoning",
    "id": "rs_owner",
    "encrypted_content": "owner-bound",
    "summary": [],
}
_USER: dict[str, JsonValue] = {"role": "user", "content": "Question"}
_ANSWER: dict[str, JsonValue] = {
    "type": "message",
    "role": "assistant",
    "id": "msg_owner",
    "status": "completed",
    "content": [{"type": "output_text", "text": "Complete answer"}],
}
_CALL: dict[str, JsonValue] = {
    "type": "function_call",
    "id": "fc_owner",
    "call_id": "call_read",
    "name": "read",
    "arguments": "{}",
}
_OUTPUT: dict[str, JsonValue] = {"type": "function_call_output", "call_id": "call_read", "output": "Result"}


@pytest.mark.parametrize(
    "input_items",
    [
        pytest.param([_REASONING], id="reasoning-only"),
        pytest.param([_REASONING, _USER], id="reasoning-and-fresh-user"),
        pytest.param([_ANSWER, _REASONING, _USER], id="answer-precedes-reasoning"),
        pytest.param([_REASONING, {**_ANSWER, "phase": "commentary"}, _USER], id="commentary-is-not-an-answer"),
        pytest.param([_REASONING, {**_ANSWER, "status": "failed"}, _USER], id="failed-answer"),
        pytest.param([_REASONING, {**_ANSWER, "status": "in_progress"}, _USER], id="incomplete-answer"),
        pytest.param([_REASONING, {**_ANSWER, "content": []}, _USER], id="empty-answer"),
        pytest.param([_REASONING, _ANSWER, _REASONING, _USER], id="later-reasoning-has-no-answer"),
        pytest.param([_USER, _REASONING, _ANSWER, _USER, _REASONING, _USER], id="second-incomplete-turn"),
        pytest.param([_REASONING, _USER, _ANSWER, _USER], id="answer-belongs-to-another-turn"),
        pytest.param(
            [_REASONING, {"role": "developer", "content": "New instruction"}, _ANSWER, _USER],
            id="instruction-boundary",
        ),
        pytest.param([_REASONING, _CALL, _OUTPUT, _USER], id="tool-pair-is-not-a-complete-answer"),
        pytest.param([_REASONING, _CALL, _ANSWER, _USER], id="unresolved-tool"),
        pytest.param([_REASONING, _CALL, _ANSWER, _OUTPUT, _USER], id="answer-before-tool-settlement"),
        pytest.param(
            [_REASONING, _CALL, {**_OUTPUT, "call_id": "call_other"}, _ANSWER, _USER],
            id="mismatched-tool-output",
        ),
        pytest.param([_REASONING, _OUTPUT, _ANSWER, _USER], id="missing-tool-call"),
        pytest.param(
            [_REASONING, _CALL, _OUTPUT, _CALL, _OUTPUT, _ANSWER, _USER],
            id="duplicate-call-id",
        ),
    ],
)
def test_auth_recovery_preserves_reasoning_without_complete_retained_turn(input_items: list[JsonValue]) -> None:
    original = deepcopy(input_items)

    assert project_responses_input_for_auth_recovery(input_items) is None
    assert input_items == original


@pytest.mark.parametrize(
    "input_items",
    [
        pytest.param([_USER, _REASONING, _ANSWER, _USER], id="complete-text-turn"),
        pytest.param(
            [{"role": "developer", "content": "Instructions"}, _USER, _REASONING, _ANSWER, _USER],
            id="initial-developer-instruction",
        ),
        pytest.param(
            [_USER, _REASONING, {**_REASONING, "id": "rs_second"}, _ANSWER, _USER],
            id="multiple-reasoning-blocks-in-complete-turn",
        ),
        pytest.param(
            [_REASONING, {**_ANSWER, "phase": "commentary"}, {**_ANSWER, "phase": "final_answer"}, _USER],
            id="commentary-then-final-answer",
        ),
        pytest.param([_USER, _REASONING, _CALL, _OUTPUT, _REASONING, _ANSWER, _USER], id="complete-tool-turn"),
        pytest.param([_USER, _REASONING, _ANSWER, _CALL, _OUTPUT, _USER], id="retained-answer-and-tool-result"),
        pytest.param([_USER, _REASONING, _ANSWER, _USER, _REASONING, _ANSWER, _USER], id="two-complete-turns"),
        pytest.param([_USER, _ANSWER, _USER], id="response-ids-without-reasoning"),
    ],
)
def test_auth_recovery_projects_only_redundant_reasoning(input_items: list[JsonValue]) -> None:
    original = deepcopy(input_items)

    projected = project_responses_input_for_auth_recovery(input_items)

    assert projected is not None
    assert projected == [
        {key: value for key, value in item.items() if key != "id"}
        for item in input_items
        if isinstance(item, dict) and item.get("type") != "reasoning"
    ]
    assert responses_payload_is_account_neutral_fresh_replay({"input": projected})
    assert input_items == original
