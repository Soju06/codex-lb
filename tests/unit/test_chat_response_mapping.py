from __future__ import annotations

from app.core.openai.chat_responses import iter_chat_chunks


def test_output_text_delta_to_chat_chunk():
    lines = [
        'data: {"type":"response.output_text.delta","delta":"hi"}\n\n',
        'data: {"type":"response.completed","response":{"id":"r1"}}\n\n',
    ]
    chunks = list(iter_chat_chunks(lines, model="gpt-5.2"))
    assert any("chat.completion.chunk" in chunk for chunk in chunks)
