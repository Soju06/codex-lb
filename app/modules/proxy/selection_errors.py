from __future__ import annotations

from typing import Protocol

from app.core.errors import OpenAIErrorEnvelope, openai_error

USAGE_LIMIT_REACHED = "usage_limit_reached"
LOCAL_ACCOUNT_CAP_ERROR_CODES = frozenset(
    {
        "account_response_create_cap",
        "account_stream_cap",
    }
)


class SelectionFailure(Protocol):
    error_message: str | None
    error_code: str | None
    resets_at: int | None


def selection_failure_response(selection: SelectionFailure) -> tuple[int, OpenAIErrorEnvelope]:
    code = selection.error_code or "no_accounts"
    message = selection.error_message or "No active accounts available"
    if code == USAGE_LIMIT_REACHED:
        return (
            429,
            openai_error(
                code,
                message,
                error_type=USAGE_LIMIT_REACHED,
                resets_at=selection.resets_at,
            ),
        )
    if code in LOCAL_ACCOUNT_CAP_ERROR_CODES:
        return 429, openai_error(code, message, error_type="rate_limit_error")
    return 503, openai_error(code, message)
