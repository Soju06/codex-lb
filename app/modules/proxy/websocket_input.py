"""Bounded read-ahead while a Responses socket's backend is being resolved."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from fastapi import WebSocket
from starlette.types import Message

from app.core.clock import Clock

_PREPARATION_BUFFER_FRAMES = 16
_PREPARATION_BUFFER_BYTES = 16 * 1024 * 1024


class WebSocketInputOverflow(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _ReceivedMessage:
    message: Message
    received_at: float
    size: int


def _message_size(message: Message) -> int:
    text = message.get("text")
    return len(text.encode("utf-8", errors="replace")) if isinstance(text, str) else len(message.get("bytes") or b"")


class WebSocketInputBuffer:
    """One reader at a time; preserve raw frames and receipt time across handoff.

    Preparation may read ahead to observe disconnect, but cannot consume or
    normalize subscription input. Excess read-ahead closes the socket rather
    than buffering without a bound while routing persistence is unavailable.
    """

    def __init__(self, websocket: WebSocket, *, clock: Clock) -> None:
        self.websocket = websocket
        self.clock = clock
        self.pending: deque[_ReceivedMessage] = deque()
        self.pending_bytes = 0
        self.received_at = clock.monotonic()
        self.received_size = 0

    async def receive(self) -> Message:
        if self.pending:
            received = self.pending.popleft()
            self.pending_bytes -= received.size
            self.received_at = received.received_at
            self.received_size = received.size
            return received.message
        message = await self.websocket.receive()
        self.received_at = self.clock.monotonic()
        self.received_size = _message_size(message)
        return message

    async def observe_disconnect(self) -> None:
        # The regular receiver is suspended while this task owns ASGI receive.
        while True:
            message = await self.websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            size = _message_size(message)
            if len(self.pending) >= _PREPARATION_BUFFER_FRAMES or self.pending_bytes + size > _PREPARATION_BUFFER_BYTES:
                raise WebSocketInputOverflow
            self.pending.append(_ReceivedMessage(message, self.clock.monotonic(), size))
            self.pending_bytes += size
