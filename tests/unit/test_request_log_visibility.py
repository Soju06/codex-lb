from __future__ import annotations

from app.modules.request_logs.visibility import (
    MAX_REQUEST_VISIBILITY_BYTES,
    build_request_visibility_document,
)


def test_build_request_visibility_document_keeps_allowlisted_headers_only():
    document = build_request_visibility_document(
        {
            "Authorization": "Bearer secret",
            "Content-Type": "application/json",
            "Session_Id": "sid_123",
            "User-Agent": "codex-test",
            "X-Codex-Session-Id": "codex-session",
            "X-OpenAI-Client-Version": "1.2.3",
            "Cookie": "session=secret",
        },
        {"input": "hello"},
    )

    assert document is not None
    assert document["headers"] == {
        "content-type": "application/json",
        "user-agent": "codex-test",
        "x-openai-client-version": "1.2.3",
    }


def test_build_request_visibility_document_redacts_nested_secret_fields():
    document = build_request_visibility_document(
        {"Content-Type": "application/json"},
        {
            "input": "hello",
            "apiKey": "sk-live",
            "metadata": {
                "sessionToken": "tok_123",
                "nested": [{"password": "pw"}, {"safe": "ok"}],
            },
        },
    )

    assert document is not None
    assert document["truncated"] is False
    assert document["body"] == {
        "input": "hello",
        "apiKey": "[REDACTED]",
        "metadata": {
            "sessionToken": "[REDACTED]",
            "nested": [{"password": "[REDACTED]"}, {"safe": "ok"}],
        },
    }


def test_build_request_visibility_document_preserves_non_sensitive_strings_and_unsupported_nested_values():
    document = build_request_visibility_document(
        {"Content-Type": "application/json"},
        {
            "count": 3,
            "enabled": True,
            "note": "hello",
            "nested": {"safe": "still-hidden", "blob": object()},
        },
    )

    assert document is not None
    assert document["body"] == {
        "count": 3,
        "enabled": True,
        "note": "hello",
        "nested": {"safe": "still-hidden", "blob": "[UNSUPPORTED]"},
    }


def test_build_request_visibility_document_preserves_safe_request_metadata_strings():
    document = build_request_visibility_document(
        {"Content-Type": "application/json"},
        {
            "service_tier": "priority",
            "reasoning": {"effort": "high", "summary": "detailed"},
            "metadata": {"apiKey": "sk-live"},
        },
    )

    assert document is not None
    assert document["body"] == {
        "service_tier": "priority",
        "reasoning": {"effort": "high", "summary": "detailed"},
        "metadata": {"apiKey": "[REDACTED]"},
    }


def test_build_request_visibility_document_marks_truncation_explicitly():
    document = build_request_visibility_document(
        {"Content-Type": "application/json"},
        {
            "service_tier": "priority",
            "reasoning": {"effort": "high", "summary": "detailed"},
            "input": [{"role": "user", "content": "x" * 400} for _ in range(24)],
        },
        max_bytes=512,
    )

    assert document is not None
    assert document["truncated"] is True
    assert document["headers"] == {"content-type": "application/json"}
    body = document["body"]
    assert isinstance(body, dict)
    assert body["service_tier"] == "priority"
    assert body["reasoning"] == {"effort": "high", "summary": "detailed"}
    assert body["input"] == {
        "_truncated": True,
        "kind": "array",
        "items": 24,
        "sample": {"role": "user", "content": "x" * 80 + "… [320 chars truncated]"},
    }


def test_build_request_visibility_document_skips_unsupported_binary_bodies():
    document = build_request_visibility_document(
        {"Content-Type": "multipart/form-data"},
        b"binary-audio",
    )

    assert document is None
