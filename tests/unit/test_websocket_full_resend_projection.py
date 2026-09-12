from copy import deepcopy

import pytest

from app.core.openai.requests import ResponsesRequest
from app.modules.proxy._service.websocket.helpers import _project_websocket_full_resend_for_replay

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("unsafe", [None, "missing_reply", "unpaired_call", "file", "prompt"])
def test_full_resend_projection_preserves_content_and_rejects_unsafe_replay(unsafe: str | None) -> None:
    user = {"role": "user", "content": "hello"}
    call = {"type": "function_call", "id": "fc_owner", "call_id": "call_1", "name": "read", "arguments": "{}"}
    output = {"type": "function_call_output", "call_id": "call_1", "output": "file contents"}
    assistant = {
        "type": "message",
        "id": "msg_owner",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Done"}],
    }
    follow_up = {"role": "user", "content": "continue"}
    items = [
        user,
        {"type": "reasoning", "id": "rs_owner", "encrypted_content": "ciphertext", "summary": []},
        call,
        output,
        assistant,
        follow_up,
    ]
    if unsafe == "missing_reply":
        items.remove(assistant)
    elif unsafe == "unpaired_call":
        items.remove(output)
    elif unsafe == "file":
        follow_up["content"] = [{"type": "input_file", "file_id": "file_owner"}]
    payload = ResponsesRequest.model_validate(
        {
            "type": "response.create",
            "model": "gpt-5.4",
            "instructions": "",
            "input": items,
            **({"prompt": {"id": "pmpt_owner"}} if unsafe == "prompt" else {}),
        }
    )
    original = deepcopy(payload)

    projected = _project_websocket_full_resend_for_replay(payload, stored_count=1)

    assert payload == original
    if unsafe is not None:
        assert projected is payload
    else:
        assert projected.input == [
            user,
            {key: value for key, value in call.items() if key != "id"},
            output,
            {key: value for key, value in assistant.items() if key != "id"},
            follow_up,
        ]
