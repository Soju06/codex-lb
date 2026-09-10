"""Trace whether the SSE terminal frame of a Responses stream reached the downstream writer.

``request_logs.status`` is settled when the proxy *parses* the upstream
terminal frame (``response.completed``/``failed``/``incomplete``/``error``),
not when that frame is *written* to the client. Two paths drop the write
without leaving a signal: uvicorn's ``send`` returns silently once the cycle is
marked disconnected (peer FIN/RST racing the terminal write), and an exception
raised by an ``api.py`` wrapper while formatting the terminal block closes the
inner generators after settlement. Both produce a full-looking body without a
terminal frame and a ``success`` row.

:class:`DeliveryTracedStreamingResponse` wraps the ASGI ``send`` of a
``StreamingResponse`` and reports, once per stream, one bounded ``outcome``
(see :data:`OUTCOMES`) as a Prometheus counter and one structured log line:

- ``terminal_written`` — a terminal frame was handed to the server's writer on
  a live connection (a cancel or exception *after* that does not unwrite it);
- ``terminal_after_disconnect`` — the terminal frame was handed over after the
  connection was already lost, so uvicorn dropped it;
- ``exception_before_terminal`` — the body raised before any terminal frame
  (re-raised unchanged; no synthetic frame is emitted);
- ``cancelled_before_terminal`` — the body ended unfinished without a terminal:
  client disconnect (Starlette cancels the body iteration) or server-side task
  cancellation (``exc=CancelledError`` in the log line), including a cancel
  that lands while ``send`` is still waiting on write backpressure for the
  terminal chunk itself — uvicorn drains *before* it writes, so that chunk never
  reached the writer;
- ``ended_without_terminal`` — the body iterator was exhausted and the final
  ``more_body=False`` sent, yet no terminal frame was seen.

It never alters the forwarded bytes, never emits a synthetic frame, and does
not touch request-log settlement. ``terminal_written`` means the *complete*
terminal block — ``event:`` line through the blank-line terminator — was handed
to the HTTP server's writer on a live connection; the transport buffer and any
reverse proxy in front are not observed. A terminal frame split across chunks
is committed only by the chunk that carries its terminator, so a disconnect or
cancellation between the ``event:`` line and the end of the block is reported
as ``terminal_after_disconnect`` / ``cancelled_before_terminal``, never as a
written terminal (an SSE client discards an unterminated event at EOF).
``chunks``/``bytes`` count the non-empty body chunks whose ``send`` returned
(the empty ``more_body=False`` message is not a chunk).

The log line's ``request_id`` is the ingress request id
(``RequestIdMiddleware`` contextvar, valid for the whole body stream because the
middleware is pure ASGI). The streaming service stores that value in
``request_logs.archive_request_id``; ``request_logs.request_id`` holds the
upstream response id once ``response.created`` was seen. Join on
``archive_request_id``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Mapping

from fastapi.responses import StreamingResponse
from starlette.types import Message, Receive, Scope, Send

from app.core.http_protocol import HTTP_DISCONNECTED_STATE
from app.core.metrics.prometheus import PROMETHEUS_AVAILABLE, stream_terminal_delivery_total
from app.core.utils.request_id import get_request_id

logger = logging.getLogger(__name__)

OUTCOME_TERMINAL_WRITTEN = "terminal_written"
OUTCOME_TERMINAL_AFTER_DISCONNECT = "terminal_after_disconnect"
OUTCOME_EXCEPTION_BEFORE_TERMINAL = "exception_before_terminal"
OUTCOME_CANCELLED_BEFORE_TERMINAL = "cancelled_before_terminal"
OUTCOME_ENDED_WITHOUT_TERMINAL = "ended_without_terminal"
OUTCOMES = frozenset(
    {
        OUTCOME_TERMINAL_WRITTEN,
        OUTCOME_TERMINAL_AFTER_DISCONNECT,
        OUTCOME_EXCEPTION_BEFORE_TERMINAL,
        OUTCOME_CANCELLED_BEFORE_TERMINAL,
        OUTCOME_ENDED_WITHOUT_TERMINAL,
    }
)

# Canonical SSE framing: every wrapper in api.py emits ``format_sse_event``
# blocks or passes the upstream ``event: <type>\ndata: ...\n\n`` line through
# verbatim, so the terminal event type is always on an ``event:`` line and a
# block starts with that line. The pattern is only ever *anchored* (``match``)
# at a line start — never ``search``-ed across a body — so a large non-terminal
# frame (``response.output_item.done`` with big tool-call arguments) costs one
# anchored comparison at offset 0 plus one ``bytes.find`` (memmem) for further
# line starts, not a regex walk over every byte.
_TERMINAL_LINE_RE = re.compile(rb"event: (response\.completed|response\.failed|response\.incomplete|error)(?=\r|\n)")
_EVENT_LINE_START = b"\nevent: "
# Frames are normally one block per ASGI chunk, but nothing pins that, so the
# last bytes of the previous chunk are re-scanned together with the current one
# and a marker split across the boundary is still recognised. The longest marker
# (``\nevent: response.incomplete\n``) is 28 bytes; keep a little more. The
# same tail carries a block terminator split across chunks (``\r\n\r`` | ``\n``).
_TAIL_BYTES = 64


def _find_terminal_event(window: bytes) -> tuple[str, int] | None:
    """Return ``(terminal event type, offset just past the type)`` for an ``event:`` line of ``window``."""
    if window.startswith(b"event: "):
        match = _TERMINAL_LINE_RE.match(window)
        if match is not None:
            return match.group(1).decode("ascii"), match.end()
    position = window.find(_EVENT_LINE_START)
    while position != -1:
        match = _TERMINAL_LINE_RE.match(window, position + 1)
        if match is not None:
            return match.group(1).decode("ascii"), match.end()
        position = window.find(_EVENT_LINE_START, position + 1)
    return None


def _find_block_end(window: bytes) -> int:
    """Return the offset just past the first SSE blank line in ``window``, or ``-1``.

    A block ends at an empty line: a line terminator immediately followed by
    another one. LF and CRLF framing (and either mixed) are recognised through
    ``bytes.find`` on ``\n`` — a terminal ``response.completed`` frame carries
    one very long ``data:`` line, so this loop runs two or three iterations,
    never a regex walk. Bare-CR framing is not emitted or relayed by the proxy
    and is not recognised.
    """
    position = window.find(b"\n")
    while position != -1:
        following = window[position + 1 : position + 3]
        if following.startswith(b"\n"):
            return position + 2
        if following == b"\r\n":
            return position + 3
        position = window.find(b"\n", position + 1)
    return -1


class DeliveryTracedStreamingResponse(StreamingResponse):
    """``StreamingResponse`` that reports whether the SSE terminal frame reached the writer.

    ``outcome`` is ``None`` until the response finishes and one of :data:`OUTCOMES`
    afterwards; tests and callers may read it, the metric/log emission happens
    exactly once in ``__call__``'s ``finally``.
    """

    def __init__(
        self,
        content: AsyncIterator[str] | AsyncIterator[bytes],
        *,
        surface: str,
        media_type: str = "text/event-stream",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(content, media_type=media_type, headers=headers)
        self.surface = surface
        self.outcome: str | None = None
        self._chunks = 0
        self._bytes = 0
        self._tail = b""
        # ``_pending_terminal`` names the terminal whose ``event:`` line was
        # handed over while the rest of its block is still outstanding;
        # ``_terminal_type`` is set only once the block terminator was handed over.
        self._pending_terminal: str | None = None
        self._terminal_type: str | None = None
        self._terminal_dropped = False
        self._final_sent = False

    def _observe_body(self, body: bytes) -> bool:
        """Account a body chunk handed to the writer; return whether it completed the terminal block."""
        if not body:
            return False
        self._chunks += 1
        self._bytes += len(body)
        if self._terminal_type is not None:
            return False
        window = self._tail + body if self._tail else body
        if self._pending_terminal is None:
            found = _find_terminal_event(window)
            if found is None:
                self._tail = window[-_TAIL_BYTES:]
                return False
            self._pending_terminal, block_start = found
            # Only bytes after the event line may terminate its block: the
            # blank line that *preceded* it belongs to the previous frame.
            window = window[block_start:]
        block_end = _find_block_end(window)
        if block_end == -1:
            self._tail = window[-_TAIL_BYTES:]
            return False
        self._terminal_type = self._pending_terminal
        self._tail = b""
        return True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        state = scope.get("state")

        async def traced_send(message: Message) -> None:
            await send(message)
            if message["type"] != "http.response.body":
                return
            # Accounted only after ``send`` returned: uvicorn awaits its
            # write-backpressure drain *before* writing, so a cancel that lands
            # inside ``send`` means this chunk never reached the writer.
            if self._observe_body(bytes(message.get("body", b""))):
                # uvicorn's ``send`` returns without writing once the cycle is
                # disconnected; the protocol stamps that state synchronously in
                # ``connection_lost``, before any task can resume, so a stamp
                # visible here means the chunk that completed the terminal
                # block never reached the writer.
                self._terminal_dropped = isinstance(state, dict) and HTTP_DISCONNECTED_STATE in state
            if not message.get("more_body", False):
                self._final_sent = True

        exc_name: str | None = None
        cancelled = False
        try:
            await super().__call__(scope, receive, traced_send)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except BaseException as exc:
            exc_name = type(exc).__name__
            raise
        finally:
            self._finish(exc_name=exc_name, cancelled=cancelled)

    def _classify(self, *, exc_name: str | None, cancelled: bool) -> str:
        if self._terminal_type is not None:
            # A cancel or exception after the complete terminal block was
            # handed over does not unwrite it; the exception name stays visible
            # in the log line. A block whose ``event:`` line went out but whose
            # terminator did not (``_pending_terminal`` set, ``_terminal_type``
            # unset) falls through to the no-terminal outcomes below.
            return OUTCOME_TERMINAL_AFTER_DISCONNECT if self._terminal_dropped else OUTCOME_TERMINAL_WRITTEN
        if exc_name is not None:
            return OUTCOME_EXCEPTION_BEFORE_TERMINAL
        if cancelled or not self._final_sent:
            # Starlette's ASGI 2.3 path absorbs the disconnect-driven cancel
            # inside its task group, so a client that left mid-stream returns
            # here normally with the body unfinished (no ``more_body=False``).
            # ``exc=CancelledError`` in the log line marks the other shape: the
            # server task itself was cancelled (uvicorn shutdown drain).
            return OUTCOME_CANCELLED_BEFORE_TERMINAL
        return OUTCOME_ENDED_WITHOUT_TERMINAL

    def _finish(self, *, exc_name: str | None, cancelled: bool) -> None:
        outcome = self._classify(exc_name=exc_name, cancelled=cancelled)
        self.outcome = outcome
        if PROMETHEUS_AVAILABLE and stream_terminal_delivery_total is not None:
            stream_terminal_delivery_total.labels(surface=self.surface, outcome=outcome).inc()
        if outcome == OUTCOME_TERMINAL_WRITTEN:
            level = logging.DEBUG
        elif outcome == OUTCOME_CANCELLED_BEFORE_TERMINAL:
            level = logging.INFO
        else:
            level = logging.WARNING
        logger.log(
            level,
            "responses_stream_terminal_delivery request_id=%s surface=%s outcome=%s terminal=%s "
            "chunks=%d bytes=%d exc=%s",
            get_request_id(),
            self.surface,
            outcome,
            self._terminal_type,
            self._chunks,
            self._bytes,
            "CancelledError" if cancelled else exc_name,
        )


__all__ = [
    "OUTCOMES",
    "OUTCOME_CANCELLED_BEFORE_TERMINAL",
    "OUTCOME_ENDED_WITHOUT_TERMINAL",
    "OUTCOME_EXCEPTION_BEFORE_TERMINAL",
    "OUTCOME_TERMINAL_AFTER_DISCONNECT",
    "OUTCOME_TERMINAL_WRITTEN",
    "DeliveryTracedStreamingResponse",
]
