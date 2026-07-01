from __future__ import annotations


class ClaudeAPIError(Exception):
    """Base class for non-2xx responses from Anthropic."""


class ClaudeAuthError(ClaudeAPIError):
    """401 from Anthropic, or invalid_grant from OAuth refresh."""


class ClaudeRateLimited(ClaudeAPIError):
    """429 from Anthropic, or anthropic-ratelimit-status: rejected."""


class ClaudeUpstreamError(ClaudeAPIError):
    """5xx or transport failure from Anthropic."""