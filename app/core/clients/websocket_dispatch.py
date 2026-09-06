from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, cast


@dataclass(slots=True)
class _SendCallback:
    callback: Callable[[], None] | None

    def __call__(self) -> None:
        callback, self.callback = self.callback, None
        if callback is not None:
            callback()


_send_callback: ContextVar[_SendCallback | None] = ContextVar("websocket_send_callback", default=None)


@contextmanager
def websocket_send_context(on_dispatched: Callable[[], None]) -> Iterator[None]:
    """Own one send's handoff, including callbacks retained by native or compression tasks."""
    callback = _SendCallback(on_dispatched)
    token = _send_callback.set(callback)
    try:
        yield
    finally:
        callback.callback = None
        _send_callback.reset(token)


def current_websocket_send_callback() -> Callable[[], None] | None:
    return _send_callback.get()


@dataclass(slots=True)
class _TransportSend:
    transport: WebSocketDispatchTransport
    callback: Callable[[], None] | None


_transport_send: ContextVar[_TransportSend | None] = ContextVar("websocket_transport_send", default=None)


class WebSocketDispatchTransport:
    """Connection-local write observation; all other transport behavior is delegated.

    websockets and aiohttp clients write each text message as one complete
    masked frame. Observe that write, not send return or a control frame.
    aiohttp's compression child inherits the send context; clearing its shared
    binding prevents a cancelled shielded send from notifying a later request.
    """

    def __init__(self, transport: asyncio.Transport) -> None:
        self._transport = transport

    def __getattr__(self, name: str) -> Any:
        return getattr(self._transport, name)

    def write(self, data: bytes | bytearray | memoryview) -> None:
        self._transport.write(data)
        send = _transport_send.get()
        if send is not None and send.transport is self and send.callback is not None and _is_complete_text_frame(data):
            callback, send.callback = send.callback, None
            callback()

    @contextmanager
    def send_context(self) -> Iterator[None]:
        send = _TransportSend(self, current_websocket_send_callback())
        token = _transport_send.set(send)
        try:
            yield
        finally:
            send.callback = None
            _transport_send.reset(token)

    def as_transport(self) -> asyncio.Transport:
        # The proxy intentionally delegates the complete runtime interface:
        # inheriting asyncio.Transport would shadow methods with abstract stubs.
        return cast(asyncio.Transport, self)


def _is_complete_text_frame(data: bytes | bytearray | memoryview) -> bool:
    # FIN + text opcode (RSV bits may indicate compression), with client mask.
    if len(data) < 2 or data[0] & 0x8F != 0x81 or not data[1] & 0x80:
        return False
    length = data[1] & 0x7F
    header_length = 2
    if length in (126, 127):
        length_bytes = 2 if length == 126 else 8
        header_length += length_bytes
        if len(data) < header_length:
            return False
        length = int.from_bytes(data[2:header_length], "big")
    return len(data) == header_length + 4 + length
