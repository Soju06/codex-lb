from __future__ import annotations

from typing import cast

from app.core.crypto import TokenEncryptor
from app.core.types import JsonValue
from app.db.models import ModelSource
from app.modules.model_sources.forwarding import (
    SourceStreamUsageParser,
    SourceUsageHolder,
    _audio_seconds_from_body,
    _error_payload_from_body,
    _redact_source_error_payload,
    _usage_from_audio_body,
)


class _FakeEncryptor:
    def decrypt(self, token: bytes) -> str:
        assert token == b"encrypted-source-key"
        return "source-secret-token"


def _fake_encryptor() -> TokenEncryptor:
    return cast(TokenEncryptor, _FakeEncryptor())


def test_chat_stream_usage_parser_handles_split_sse_frame() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")

    parser.feed(b'data: {"usage":{"prompt_tokens":12,')
    parser.feed(b'"completion_tokens":5,"prompt_tokens_details":{"cached_tokens":3}}}\n\n')

    assert holder.usage is not None
    assert holder.usage.input_tokens == 12
    assert holder.usage.output_tokens == 5
    assert holder.usage.cached_input_tokens == 3


def test_chat_stream_usage_parser_handles_crlf_frames() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")

    parser.feed(
        b'data: {"usage":{"prompt_tokens":12,"completion_tokens":5,'
        b'"prompt_tokens_details":{"cached_tokens":3}}}\r\n\r\n'
    )

    assert holder.usage is not None
    assert holder.usage.input_tokens == 12
    assert holder.usage.output_tokens == 5
    assert holder.usage.cached_input_tokens == 3


def test_chat_stream_usage_parser_handles_crlf_split_across_chunks() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")

    parser.feed(b'data: {"usage":{"prompt_tokens":2,"completion_tokens":1}}\r')
    parser.feed(b"\n\r\n")

    assert holder.usage is not None
    assert holder.usage.input_tokens == 2
    assert holder.usage.output_tokens == 1


def test_stream_usage_parser_bounds_buffer_without_frame_boundaries() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")

    for _ in range(600):
        parser.feed(b"x" * 4096)

    assert len(parser._buffer) <= SourceStreamUsageParser._MAX_BUFFER_CHARS


def test_chat_stream_usage_parser_rejects_negative_tokens() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="chat")

    parser.feed(b'data: {"usage":{"prompt_tokens":-5,"completion_tokens":3}}\n\n')

    assert holder.usage is None


def test_responses_stream_usage_parser_rejects_negative_tokens() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")

    parser.feed(b'data: {"type":"response.completed","response":{"usage":{"input_tokens":4,"output_tokens":-1}}}\n\n')

    assert holder.usage is None


def test_responses_stream_usage_parser_handles_split_sse_frame() -> None:
    holder = SourceUsageHolder()
    parser = SourceStreamUsageParser(holder, response_shape="responses")

    parser.feed(b'data: {"type":"response.completed","response":{"usage":{"input_tokens":7,')
    parser.feed(b'"output_tokens":4,"input_tokens_details":{"cached_tokens":2}}}}\n\n')

    assert holder.usage is not None
    assert holder.usage.input_tokens == 7
    assert holder.usage.output_tokens == 4
    assert holder.usage.cached_input_tokens == 2


def test_audio_usage_parser_accepts_total_tokens_only_json() -> None:
    usage = _usage_from_audio_body(
        b'{"text":"hello","usage":{"total_tokens":42}}',
        "application/json; charset=utf-8",
    )

    assert usage is not None
    assert usage.input_tokens == 42
    assert usage.output_tokens == 0


def test_audio_usage_parser_ignores_duration_only_usage() -> None:
    usage = _usage_from_audio_body(
        b'{"text":"hello","usage":{"type":"duration","seconds":3.5}}',
        "application/json",
    )

    assert usage is None


def test_audio_seconds_from_top_level_duration() -> None:
    assert _audio_seconds_from_body(b'{"text":"hi","duration":30.464}', "application/json") == 30.464


def test_audio_seconds_from_usage_seconds_fallback() -> None:
    assert _audio_seconds_from_body(b'{"text":"hi","usage":{"seconds":12.5}}', "application/json") == 12.5


def test_audio_seconds_ignores_nonpositive_and_nonjson() -> None:
    assert _audio_seconds_from_body(b'{"duration":0}', "application/json") is None
    assert _audio_seconds_from_body(b'{"duration":-4}', "application/json") is None
    assert _audio_seconds_from_body(b"plain text", "text/plain") is None
    assert _audio_seconds_from_body(b'{"duration":true}', "application/json") is None


def test_audio_error_payload_preserves_text_body() -> None:
    payload = _error_payload_from_body(b"missing required field: file", "text/plain; charset=utf-8")
    error = cast(dict[str, object], payload["error"])
    assert isinstance(error, dict)

    assert error["code"] == "model_source_error"
    assert error["message"] == "missing required field: file"


def test_source_error_payload_redacts_configured_api_key() -> None:
    source = ModelSource(
        id="src_redact",
        name="Redact",
        kind="openai_compatible",
        base_url="http://127.0.0.1:8000/v1",
        api_key_encrypted=b"encrypted-source-key",
    )
    payload: dict[str, JsonValue] = {
        "error": {
            "message": "upstream echoed Authorization: Bearer source-secret-token",
            "details": ["source-secret-token", {"header": "Bearer source-secret-token"}],
            "code": "bad_request",
        }
    }

    redacted = _redact_source_error_payload(payload, source, encryptor=_fake_encryptor())
    error = cast(dict[str, object], redacted["error"])

    assert "source-secret-token" not in str(redacted)
    assert error["message"] == "upstream echoed Authorization: Bearer [REDACTED]"
    assert error["details"] == ["[REDACTED]", {"header": "Bearer [REDACTED]"}]
