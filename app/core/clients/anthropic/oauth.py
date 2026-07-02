"""Anthropic OAuth refresh client.

Owns the verified contract documented in
``openspec/changes/add-claude-oauth-pool/notes.md`` §1 and §3:

- POST to ``https://platform.claude.com/v1/oauth/token`` with a JSON body of
  ``{grant_type, refresh_token, client_id}``. No auth header (public client).
- 200 response shape: ``access_token`` (sk-ant-oat01-…), ``refresh_token``
  (always rotated; treated as required but defensively optional), and
  ``expires_in`` (integer seconds).
- 400 with ``error == "invalid_grant"`` means the stored refresh token is
  dead (single-use rotation or revoked). Surface as :class:`ClaudeAuthError`.
- 5xx means the upstream is unhealthy; surface as :class:`ClaudeUpstreamError`.

The transport dependency is a thin protocol so tests can swap a stub in
without pulling in aiohttp. Production wiring reuses the project's
``lease_http_session`` (see Phase 9).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from app.core.clients.anthropic.errors import (
    ClaudeAPIError,
    ClaudeAuthError,
    ClaudeUpstreamError,
)


@dataclass(frozen=True)
class ClaudeRefreshResult:
    """Result of a successful OAuth refresh.

    ``refresh_token`` is ``None`` only when the server's response omits it
    (defensive case). Anthropic always rotates per ``notes.md`` §3; callers
    should treat a ``None`` value as a signal to flag the account for
    re-authorization rather than silently preserve a possibly-stale token.
    """

    access_token: str
    refresh_token: str | None
    expires_in: int


class ClaudeOAuthTransport(Protocol):
    """Minimal async POST transport.

    ``post`` returns an object exposing ``status`` and an awaitable ``json()``
    coroutine — matching the surface of ``aiohttp.ClientResponse`` used
    elsewhere in the codebase.
    """

    async def post(self, url: str, *, json: Mapping[str, Any], headers: Mapping[str, str]) -> Any: ...


# Verified constants from notes.md §1.
# ``client_id`` is the public Claude Code OAuth client_id disclosed in the
# Anthropic public client source; it is NOT a secret.
ANTHROPIC_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
ANTHROPIC_OAUTH_DEFAULT_TOKEN_ENDPOINT = "https://platform.claude.com/v1/oauth/token"


class ClaudeOAuthClient:
    """Refreshes an Anthropic OAuth access token.

    Settings shape (read by attribute, so any object with the right attrs
    works — including the project's Pydantic ``Settings``):
    - ``claude_oauth_token_endpoint: str`` — token endpoint URL.
    - ``claude_oauth_extra_headers: Mapping[str, str] | None`` — optional
      extra headers merged into every request (e.g. ``User-Agent``).
    """

    def __init__(
        self,
        *,
        transport: ClaudeOAuthTransport,
        settings: Any,
        token_endpoint: str | None = None,
        client_id: str | None = None,
    ) -> None:
        self._transport = transport
        self._settings = settings
        self._token_endpoint = (
            token_endpoint or getattr(settings, "claude_oauth_token_endpoint") or ANTHROPIC_OAUTH_DEFAULT_TOKEN_ENDPOINT
        )
        self._client_id = client_id or ANTHROPIC_OAUTH_CLIENT_ID

    async def refresh(self, refresh_token: str) -> ClaudeRefreshResult:
        extras = dict(getattr(self._settings, "claude_oauth_extra_headers", None) or {})
        resp = await self._transport.post(
            self._token_endpoint,
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._client_id,
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                **extras,
            },
        )

        status = int(getattr(resp, "status", 200))
        body = await _extract_json(resp)

        if status == 200:
            return _parse_success_body(body)

        if status == 400 and isinstance(body, dict) and body.get("error") == "invalid_grant":
            raise ClaudeAuthError(f"invalid_grant: {body!r}")

        if 500 <= status < 600:
            raise ClaudeUpstreamError(f"upstream {status}: {body!r}")

        raise ClaudeAPIError(f"refresh failed {status}: {body!r}")


def _parse_success_body(body: Any) -> ClaudeRefreshResult:
    """Extract fields from a 200 refresh response.

    Raises :class:`ClaudeAPIError` on a malformed body so callers do not get
    a ``KeyError`` / ``TypeError`` they have to remember to handle.
    """
    if not isinstance(body, dict):
        raise ClaudeAPIError(f"malformed refresh response: {body!r}")
    access_token = body.get("access_token")
    raw_expires = body.get("expires_in")
    if not isinstance(access_token, str) or access_token == "":
        raise ClaudeAPIError(f"missing access_token in refresh response: {body!r}")
    try:
        expires_in = int(raw_expires)
    except (TypeError, ValueError) as exc:
        raise ClaudeAPIError(f"missing/invalid expires_in in refresh response: {body!r}") from exc
    refresh_token = body.get("refresh_token")
    if refresh_token is not None and not isinstance(refresh_token, str):
        raise ClaudeAPIError(f"refresh_token must be string or null: {body!r}")
    return ClaudeRefreshResult(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


async def _extract_json(resp: Any) -> Any:
    """Pull a JSON body from an aiohttp-style response or a plain dict stub.

    Production responses come from aiohttp (``await resp.json()``); test
    stubs may be plain dicts (e.g. ``SimpleNamespace(body=...)``). Anything
    else is a programming error and should surface as
    :class:`ClaudeAPIError` rather than an opaque ``AttributeError``.
    """
    json_method = getattr(resp, "json", None)
    if callable(json_method):
        data = json_method()
        if hasattr(data, "__await__"):
            data = await data
        return data
    body = getattr(resp, "body", resp)
    if isinstance(body, (dict, list)):
        return body
    raise ClaudeAPIError(f"unexpected response type: {type(resp).__name__}")
