from __future__ import annotations

from typing import Any, Mapping


class ClaudeAPIError(Exception):
    """Base class for non-2xx responses from Anthropic."""

    def __init__(
        self,
        message: str = "",
        *,
        headers: Mapping[str, str] | None = None,
        body: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.headers: dict[str, str] = dict(headers) if headers else {}
        self.body: Any = body


class ClaudeAuthError(ClaudeAPIError):
    """401 from Anthropic, or invalid_grant from OAuth refresh."""


class ClaudeRateLimited(ClaudeAPIError):
    """429 from Anthropic, or anthropic-ratelimit-status: rejected."""


class ClaudeUpstreamError(ClaudeAPIError):
    """5xx or transport failure from Anthropic."""