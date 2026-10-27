"""Synthetic opaque output shared by Responses transport and replay tests."""

from collections.abc import Iterator

from app.core.types import JsonValue


def encrypted_output_items() -> list[dict[str, JsonValue]]:
    return [
        {
            "type": "message",
            "id": "msg_encrypted",
            "role": "assistant",
            "status": "completed",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": "OK"}],
            "encrypted_content": "synthetic-message+/=\nÅ",
        },
        {
            "type": "function_call",
            "id": "fc_encrypted",
            "call_id": "call_encrypted",
            "name": "check",
            "arguments": "{}",
            "status": "completed",
            "encrypted_content": "synthetic-function+/=",
            "encrypted_function_args": ["synthetic-arg+/="],
        },
        {
            "type": "custom_tool_call",
            "id": "ct_encrypted",
            "call_id": "custom_encrypted",
            "name": "check",
            "input": "test",
            "status": "completed",
            "encrypted_content": "synthetic-custom+/=",
        },
        {
            "type": "web_search_call",
            "id": "ws_encrypted",
            "status": "completed",
            "action": {"type": "search", "query": "synthetic"},
            "encrypted_content": "synthetic-web+/=",
        },
        {"type": "reasoning", "id": "rs_encrypted", "summary": [], "encrypted_content": "synthetic-reasoning+/="},
        {"type": "compaction", "id": "cmp_encrypted", "encrypted_content": "synthetic-compaction+/="},
    ]


def encrypted_output_replay_input() -> list[JsonValue]:
    return [
        *encrypted_output_items(),
        {"type": "function_call_output", "call_id": "call_encrypted", "output": "OK"},
        {"type": "custom_tool_call_output", "call_id": "custom_encrypted", "output": "OK"},
        {"role": "user", "content": [{"type": "input_text", "text": "continue"}]},
    ]


def encrypted_output_events(
    response_id: str = "resp_encrypted", *, terminal_output: bool = True
) -> Iterator[dict[str, JsonValue]]:
    items = encrypted_output_items()
    yield {
        "type": "response.created",
        "sequence_number": 0,
        "response": {"id": response_id, "object": "response", "status": "in_progress", "output": []},
    }
    for index, item in enumerate(items):
        for offset, event_type in enumerate(("response.output_item.added", "response.output_item.done")):
            yield {"type": event_type, "sequence_number": 1 + index * 2 + offset, "output_index": index, "item": item}
    terminal_items: list[JsonValue] = [*items] if terminal_output else []
    yield {
        "type": "response.completed",
        "sequence_number": 1 + len(items) * 2,
        "response": {
            "id": response_id,
            "object": "response",
            "status": "completed",
            "output": terminal_items,
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        },
    }
