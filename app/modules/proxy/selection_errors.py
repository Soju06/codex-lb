from __future__ import annotations

from typing import Protocol

from app.core.errors import OpenAIErrorEnvelope, openai_error
from app.core.resilience.overload import is_local_overload_error_code

USAGE_LIMIT_REACHED = "usage_limit_reached"


class SelectionFailure(Protocol):
    error_message: str | None
    error_code: str | None
    resets_at: int | None


def selection_failure_response(selection: SelectionFailure) -> tuple[int, OpenAIErrorEnvelope]:
    """Map an account-selection failure to its externally visible HTTP response.

    The ``usage_limit_reached`` mapping is strictly for upstream usage/quota
    exhaustion of the whole eligible pool. Local capacity codes (account caps,
    admission gates, fair-share throttles) resolve against the canonical
    ``LOCAL_OVERLOAD_CODES`` registry so they keep their stable 429
    ``rate_limit_error`` contract and are never reclassified as upstream usage
    exhaustion or collapsed into a generic 503.
    """
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
    if is_local_overload_error_code(code):
        return 429, openai_error(code, message, error_type="rate_limit_error")
    return 503, openai_error(code, message)
