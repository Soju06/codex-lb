from __future__ import annotations

from datetime import datetime, timezone

from app.core.openai.models import OpenAIError, OpenAIErrorEnvelope
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy import api as proxy_api


def _api_key(name: str) -> ApiKeyData:
    return ApiKeyData(
        id="key_cursor_compat",
        name=name,
        key_prefix="sk-test",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_used_at=None,
    )


def _error(code: str, message: str = "Upstream error") -> OpenAIErrorEnvelope:
    return OpenAIErrorEnvelope(
        error=OpenAIError(
            message=message,
            type="invalid_request_error",
            code=code,
            param="messages",
        )
    )


def test_cursor_context_length_error_is_rendered_as_chat_compat_message():
    envelope = proxy_api._cursor_context_length_error_envelope(
        _api_key("cursor"),
        _error("context_length_exceeded", "Your input exceeds the context window of this model."),
    )

    assert envelope is not None
    assert envelope.error is not None
    assert envelope.error.code == "context_length_exceeded"


def test_non_cursor_context_length_error_uses_normal_error_response_path():
    envelope = proxy_api._cursor_context_length_error_envelope(
        _api_key("local-clients"),
        _error("context_length_exceeded", "Your input exceeds the context window of this model."),
    )

    assert envelope is None


def test_cursor_non_context_error_uses_normal_error_response_path():
    envelope = proxy_api._cursor_context_length_error_envelope(
        _api_key("cursor"),
        _error("invalid_request_error", "Invalid request payload."),
    )

    assert envelope is None
