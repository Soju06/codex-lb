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

import json
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal, Mapping, Protocol

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

# Anthropic emits one SSE event per chunk; a single chunk can carry multiple
# ``data:`` lines. We split on a normalized separator to recover individual
# events without doing JSON parsing on the wire format. The pattern matches
# the conventional ``event: ...\\r\\ndata: ...\\r\\n\\r\\n`` shape.
_SSE_EVENT_PATTERN = re.compile(rb"\r?\n\r?\n")
_SSE_DATA_PREFIX = b"data: "


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

    async def stream_messages(
        self,
        *,
        access_token: str,
        request_body: Mapping[str, Any],
    ) -> AsyncIterator[StreamChunk]:
        """Open a streaming POST to ``/v1/messages`` and yield SSE chunks.

        Returns an :class:`_StreamingChatIterator` (an async iterator) that
        drives the upstream read loop. The iterator's lifecycle is managed
        by the caller's ``async for``; if the caller breaks out of the loop,
        the iterator's explicit ``aclose`` (or garbage-collection safety
        net) cleans up the aiohttp response.

        Yields:
        - ``StreamChunk(kind="sse", data=bytes)`` for each upstream byte chunk.
        - ``StreamChunk(kind="usage", data=dict)`` exactly once after the
          ``message_stop`` event (or skipped if no ``message_delta`` observed).
        - ``StreamChunk(kind="headers", data=dict[str, str])`` exactly once
          when the upstream response carried headers worth persisting.

        On 401, raises :class:`ClaudeAuthError` to the caller. On any other
        non-2xx before the first byte, raises the corresponding typed error.
        """
        url = f"{self._base_url}{getattr(self._settings, 'claude_messages_path')}"
        headers = self._build_headers(access_token, streaming=True)
        resp = await self._transport.post_stream(url, json=request_body, headers=headers)
        return _StreamingChatIterator(resp)

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


async def _safe_close(resp: Any) -> None:
    """Best-effort release of an aiohttp-style response.

    Tolerates objects that do not implement ``close`` (test stubs) and
    accepts both sync and async ``close`` methods.
    """
    close = getattr(resp, "close", None)
    if not callable(close):
        return
    try:
        result = close()
    except Exception:  # pragma: no cover — defensive
        return
    if hasattr(result, "__await__"):
        try:
            await result
        except Exception:  # pragma: no cover — defensive
            return


def _safe_close_sync(resp: Any) -> None:
    """Synchronous best-effort release for use from ``__del__``.

    Only invokes the response's ``close`` method if it returns a value
    synchronously. Async ``close`` methods are scheduled on the running
    loop if available, else dropped.
    """
    close = getattr(resp, "close", None)
    if not callable(close):
        return
    try:
        result = close()
    except Exception:  # pragma: no cover — defensive
        return
    if hasattr(result, "__await__"):
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_await_silently(result))
        except Exception:  # pragma: no cover — defensive
            return


async def _await_silently(coro: Any) -> None:
    """Await a coroutine, swallowing any exception."""
    try:
        await coro
    except Exception:  # pragma: no cover — defensive
        return


async def _safe_aclose(iterator: Any) -> None:
    """Best-effort close of an async iterator.

    Calls ``aclose`` so the iterator's own ``finally`` blocks run.
    """
    aclose = getattr(iterator, "aclose", None)
    if not callable(aclose):
        return
    try:
        result = aclose()
    except Exception:  # pragma: no cover — defensive
        return
    if hasattr(result, "__await__"):
        try:
            await result
        except Exception:  # pragma: no cover — defensive
            return


async def _iter_chunks(resp: Any) -> AsyncIterator[bytes]:
    """Return an async iterator over raw bytes from an aiohttp response.

    Handles three shapes:

    1. ``resp.content.iter_chunked(n)`` — aiohttp production shape.
    2. ``resp.iter_chunks()`` — direct async iterator method.
    3. ``resp.content`` — an async-iterable attribute.
    """
    iter_chunks = getattr(resp, "iter_chunks", None)
    if callable(iter_chunks):
        result = iter_chunks()
        if hasattr(result, "__aiter__"):
            async for chunk in result:  # type: ignore[union-attr]
                yield chunk
            return
    content = getattr(resp, "content", None)
    if content is not None and hasattr(content, "__aiter__"):
        async for chunk in content:
            if isinstance(chunk, (bytes, bytearray)):
                yield bytes(chunk)
            else:
                # Some transports wrap bytes in a stream-reader object.
                read = getattr(chunk, "read", None)
                if callable(read):
                    data = read()
                    if hasattr(data, "__await__"):
                        data = await data
                    if isinstance(data, (bytes, bytearray)):
                        yield bytes(data)
        return
    # Last-resort fallback: read everything in one shot.
    read = getattr(resp, "read", None)
    if callable(read):
        data = read()
        if hasattr(data, "__await__"):
            data = await data
        if isinstance(data, (bytes, bytearray)) and data:
            yield bytes(data)


def _scan_buffer_for_usage(
    buffer: bytearray,
    prior_usage: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Scan the accumulated SSE byte buffer for a ``message_delta`` event.

    The buffer is consumed up to the last complete ``\\n\\n`` separator; any
    tail bytes that do not yet form a complete event are preserved. Only
    the first observed ``message_delta`` event contributes its usage payload.
    """
    while True:
        match = _SSE_EVENT_PATTERN.search(buffer)
        if match is None:
            # Trim the tail to avoid unbounded buffer growth.
            if len(buffer) > 4096:
                del buffer[:-2048]
            return prior_usage
        end = match.end()
        event_bytes = bytes(buffer[: match.start()])
        del buffer[:end]
        if not event_bytes:
            continue
        usage_payload = _extract_message_delta_usage(event_bytes)
        if usage_payload is not None and prior_usage is None:
            prior_usage = usage_payload


def _extract_message_delta_usage(event_bytes: bytes) -> dict[str, Any] | None:
    """Return ``usage`` from a single ``message_delta`` SSE event, or ``None``.

    Parses only the minimal JSON needed to extract the ``usage`` field; other
    fields are ignored. This is the ONE place we decode upstream JSON during
    streaming — the bytes themselves remain untouched for downstream clients.
    """
    for line in event_bytes.splitlines():
        if not line.startswith(_SSE_DATA_PREFIX):
            continue
        payload = line[len(_SSE_DATA_PREFIX) :]
        try:
            data = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if isinstance(data, dict) and data.get("type") == "message_delta":
            usage = data.get("usage")
            if isinstance(usage, dict):
                return dict(usage)
    return None


class _StreamingChatIterator:
    """Async iterator wrapping the SSE read loop for a single request.

    Separating the iterator from :meth:`ClaudeChatClient.stream_messages`
    lets the iterator's explicit ``aclose`` (and a ``__del__`` safety net)
    release the underlying aiohttp response even when the consumer abandons
    the iterator via ``break`` — something an outer async-generator
    implementation cannot guarantee because ``async for break`` does not
    call ``aclose`` automatically in Python.
    """

    def __init__(self, resp: Any) -> None:
        self._resp = resp
        self._status = int(_response_attr(resp, "status", 0))
        self._response_headers = _response_headers(resp)
        self._closed = False
        self._initial_error: Exception | None = self._initial_status_error()
        self._first_chunk_pending: bool = True
        self._inner_iter: Any | None = None
        self._buffer: bytearray = bytearray()
        self._usage_payload: dict[str, Any] | None = None

    def _initial_status_error(self) -> Exception | None:
        if self._status == 401:
            return ClaudeAuthError("anthropic 401: status only; no body available")
        if self._status == 429:
            return ClaudeRateLimited("anthropic 429: status only; no body available")
        if self._status and (self._status < 200 or self._status >= 300):
            if 500 <= self._status < 600:
                return ClaudeUpstreamError(
                    f"anthropic {self._status}: status only; no body available"
                )
            return ClaudeAPIError(
                f"anthropic {self._status}: status only; no body available"
            )
        return None

    def __aiter__(self) -> _StreamingChatIterator:
        return self

    async def __anext__(self) -> StreamChunk:
        if self._initial_error is not None:
            err = self._initial_error
            self._initial_error = None
            await self._release()
            raise err

        if self._first_chunk_pending:
            self._first_chunk_pending = False
            return StreamChunk(kind="headers", data=self._response_headers)

        if self._inner_iter is None:
            self._inner_iter = _iter_chunks(self._resp)

        try:
            chunk_bytes = await self._inner_iter.__anext__()
        except StopAsyncIteration:
            await self._release()
            if self._usage_payload is not None:
                usage = self._usage_payload
                self._usage_payload = None
                return StreamChunk(kind="usage", data=usage)
            raise
        if not chunk_bytes:
            return await self.__anext__()
        self._buffer.extend(chunk_bytes)
        self._usage_payload = _scan_buffer_for_usage(self._buffer, self._usage_payload)
        return StreamChunk(kind="sse", data=chunk_bytes)

    async def aclose(self) -> None:
        await self._release()

    def __del__(self) -> None:
        # Safety net for the consumer-break case where ``aclose`` is never
        # invoked. We can only synchronously call ``close`` here; the
        # async path is best-effort.
        if self._closed:
            return
        self._closed = True
        _safe_close_sync(self._resp)

    async def _release(self) -> None:
        if self._closed:
            return
        self._closed = True
        await _safe_aclose(self._inner_iter)
        await _safe_close(self._resp)