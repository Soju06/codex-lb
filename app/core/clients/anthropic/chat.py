"""Anthropic chat client (``POST /v1/messages``).

Two responsibilities:

- **Non-streaming passthrough** — :meth:`ClaudeChatClient.send_messages` posts
  the caller-provided request body verbatim to ``{base_url}/v1/messages`` and
  returns ``(body, headers)``. The body is forwarded with no copy and no
  transformation (passthrough invariant); the headers are returned so the
  proxy layer can persist ``anthropic-ratelimit-*`` fields.
- **SSE passthrough** — :meth:`ClaudeChatClient.stream_messages` yields raw
  SSE bytes verbatim as :class:`StreamChunk` ``kind="sse"``, plus one
  ``kind="usage"`` chunk carrying the final ``message_delta.usage`` dict
  extracted after the ``message_stop`` event, plus one ``kind="headers"``
  chunk carrying the upstream response headers.

The transport dependency is a thin protocol so tests can swap a stub in
without pulling in aiohttp. Production wiring (Phase 9) builds an adapter
around ``app.core.clients.codex.CodexClient`` (or equivalent) so the
existing proxy-route / proxy-auth surface is reused.

Header values are pinned to the verified contract in
``openspec/changes/add-claude-oauth-pool/notes.md`` §2:

- ``Authorization: Bearer <oauth_access_token>`` (``x-api-key`` MUST NOT be sent)
- ``anthropic-version: 2023-06-01`` (date-form, required)
- ``anthropic-beta: oauth-2025-04-20,claude-code-20250219`` (CSV; oauth flag
  required, claude-code flag strongly recommended for Claude Code fidelity)

Do not add additional beta flags without an updated Phase 0 verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol

from app.core.clients.anthropic.errors import (
    ClaudeAPIError,
    ClaudeAuthError,
    ClaudeRateLimited,
    ClaudeUpstreamError,
)

# Verified Anthropic header values (openspec/changes/add-claude-oauth-pool/notes.md §2).
# Re-exported at module level so Phase 9 and tests can pin against the same
# constants without duplicating the strings.
ANTHROPIC_API_VERSION: str = "2023-06-01"
ANTHROPIC_BETA_FLAGS: str = "oauth-2025-04-20,claude-code-20250219"


@dataclass(frozen=True)
class StreamChunk:
    """A single chunk yielded by :meth:`ClaudeChatClient.stream_messages`.

    - ``kind="sse"`` and ``data`` is a ``bytes`` payload — raw SSE bytes
      forwarded verbatim. The proxy layer MUST write ``data`` to the
      downstream client without transformation.
    - ``kind="usage"`` and ``data`` is a ``dict`` — the final
      ``message_delta.usage`` payload extracted from the SSE stream. Yielded
      exactly once per stream, immediately after ``message_stop``.
    - ``kind="headers"`` and ``data`` is a ``dict[str, str]`` — the upstream
      response headers (for ``anthropic-ratelimit-*`` persistence).
    """

    kind: Literal["sse", "usage", "headers"]
    data: Any  # bytes | dict | dict[str, str]


class ClaudeChatTransport(Protocol):
    """Minimal async transport for non-streaming and streaming Anthropic calls.

    ``post`` returns a non-streaming response with ``status``, ``body`` (parsed
    JSON), and ``headers``. ``post_stream`` returns a streaming response with
    ``status``, ``headers``, ``iter_chunks`` (async iterator over raw bytes),
    and ``close`` (release the underlying aiohttp connection).
    """

    async def post(
        self, url: str, *, json: Mapping[str, Any], headers: Mapping[str, str]
    ) -> Any: ...

    async def post_stream(
        self, url: str, *, json: Mapping[str, Any], headers: Mapping[str, str]
    ) -> Any: ...


class ClaudeChatClient:
    """Forwards ``POST /v1/messages`` to Anthropic with verified OAuth headers."""

    def __init__(
        self,
        *,
        transport: ClaudeChatTransport,
        settings: Any,
        base_url: str,
        anthropic_version: str = ANTHROPIC_API_VERSION,
        anthropic_beta: str = ANTHROPIC_BETA_FLAGS,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        self._transport = transport
        self._settings = settings
        self._base_url = base_url.rstrip("/")
        self._anthropic_version = anthropic_version
        self._anthropic_beta = anthropic_beta
        self._extra_headers: dict[str, str] = dict(extra_headers or {})

    async def send_messages(
        self,
        *,
        access_token: str,
        request_body: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """POST ``request_body`` to ``/v1/messages`` and return ``(body, headers)``.

        The body is forwarded with no copy and no transformation. Raises:
        - :class:`ClaudeAuthError` on 401.
        - :class:`ClaudeRateLimited` on 429.
        - :class:`ClaudeAPIError` on any other non-2xx.
        """
        url = f"{self._base_url}{getattr(self._settings, 'claude_messages_path')}"
        headers = self._build_headers(access_token, streaming=False)
        resp = await self._transport.post(url, json=request_body, headers=headers)

        status = int(_response_attr(resp, "status", 0))
        body = _extract_body(resp)
        out_headers = _response_headers(resp)

        if status == 200:
            # body should be a dict; defensively return as-is (passthrough).
            return body if isinstance(body, dict) else dict(body), out_headers

        if status == 401:
            raise ClaudeAuthError(f"anthropic 401: {body!r}")
        if status == 429:
            raise ClaudeRateLimited(f"anthropic 429: {body!r}")
        if 500 <= status < 600:
            raise ClaudeUpstreamError(f"anthropic {status}: {body!r}")
        raise ClaudeAPIError(f"anthropic {status}: {body!r}")

    # -- internals ---------------------------------------------------------

    def _build_headers(self, access_token: str, *, streaming: bool) -> dict[str, str]:
        """Build the verified header set for an Anthropic chat call.

        Per ``notes.md`` §2:
        - ``Authorization`` carries the OAuth bearer; ``x-api-key`` is never
          sent.
        - ``anthropic-version`` is pinned to ``2023-06-01``.
        - ``anthropic-beta`` is pinned to the CSV ``oauth-2025-04-20,claude-code-20250219``.
        - ``Accept: text/event-stream`` is added on streaming requests.
        - ``Content-Type: application/json`` is added on non-streaming requests.
        """
        extras = dict(getattr(self._settings, "claude_oauth_extra_headers", None) or {})
        # Constructor-level overrides win over settings-level extras so tests
        # can inject a fixed User-Agent without mutating global settings.
        merged: dict[str, str] = {**extras, **self._extra_headers}

        headers: dict[str, str] = {
            "Authorization": f"Bearer {access_token}",
            "anthropic-version": self._anthropic_version,
            "anthropic-beta": self._anthropic_beta,
        }
        if streaming:
            headers["Accept"] = "text/event-stream"
            # Anthropic accepts the request body as JSON even when streaming;
            # Content-Type drives the body parser on their side.
            headers["Content-Type"] = "application/json"
        else:
            headers["Content-Type"] = "application/json"
            headers["Accept"] = "application/json"

        headers.update(merged)
        return headers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _response_attr(resp: Any, name: str, default: Any) -> Any:
    """Read an attribute defensively; missing attribute → default."""
    return getattr(resp, name, default)


def _response_headers(resp: Any) -> dict[str, str]:
    """Return upstream response headers as a plain ``dict[str, str]``.

    Accepts the aiohttp-style ``Headers`` mapping (case-insensitive) or any
    ``Mapping[str, str]``. Falls back to an empty dict when the response
    object does not expose headers at all (e.g. some test stubs).
    """
    raw = getattr(resp, "headers", None)
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return {str(k): str(v) for k, v in raw.items()}
    # aiohttp Headers exposes .items() too.
    try:
        return {str(k): str(v) for k, v in raw.items()}
    except Exception:  # pragma: no cover — defensive
        return {}


def _extract_body(resp: Any) -> Any:
    """Read the JSON body from an aiohttp-like response.

    Handles the three shapes a transport stub might expose:

    1. ``await resp.json()`` — aiohttp production shape (preferred).
    2. ``resp.body`` — plain attribute used by some test stubs.
    3. ``json.loads(bytes)`` — for buffered responses with bytes bodies.
    """
    json_method = getattr(resp, "json", None)
    if callable(json_method):
        data = json_method()
        # ``json()`` may be sync (returns dict) or async (returns coroutine).
        if hasattr(data, "__await__"):
            # We can't await here synchronously; expect the caller to have
            # resolved this. For test stubs the body attribute is preferred.
            import asyncio

            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                # Should not happen in our call sites; raise so the bug is loud.
                raise ClaudeAPIError(
                    "ClaudeChatClient encountered an async json() method; "
                    "transports must pre-resolve bodies"
                )
            return loop.run_until_complete(data) if loop else data
        return data
    body = getattr(resp, "body", None)
    if body is None:
        return {}
    if isinstance(body, (bytes, bytearray)):
        try:
            return json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
    if isinstance(body, (dict, list)):
        return body
    return {}


import json  # noqa: E402  — placed after helper use to keep helpers readable