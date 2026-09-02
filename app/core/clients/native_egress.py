from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import shutil
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol, cast

from multidict import CIMultiDict

logger = logging.getLogger(__name__)

_NATIVE_EGRESS_EXECUTABLE = "codex-lb-native-egress"
_NATIVE_PROTOCOL_VERSION = 1
_NATIVE_PROTOCOL_HANDSHAKE_TIMEOUT_SECONDS = 2.0
_REQUIRED_NATIVE_CAPABILITIES = frozenset(
    {
        "failure_provenance_v1",
        "http",
        "http2_profile_v1",
        "websocket",
        "websocket_send_ack",
    }
)
_NATIVE_EVENT_LINE_LIMIT = 24 * 1024 * 1024
_NATIVE_STREAM_QUEUE_LIMIT = 64
_NATIVE_WEBSOCKET_MESSAGE_QUEUE_LIMIT = 64
_NATIVE_CANCEL_TIMEOUT_SECONDS = 2.0
_NATIVE_WEBSOCKET_COMMAND_TIMEOUT_SECONDS = 30.0


class NativeEgressError(Exception):
    """Base error for the optional native Codex egress boundary."""


class NativeEgressUnavailable(NativeEgressError):
    """Raised when the optional native helper is not installed."""


class NativeEgressProtocolError(NativeEgressError):
    """Raised when the helper violates the framed stream contract."""


class NativeEgressTransportError(NativeEgressError):
    """Raised when the helper cannot complete the upstream request."""

    def __init__(
        self,
        message: str,
        *,
        failure_phase: str = "request",
        retryable_same_contract: bool = False,
        is_tls_verification_failure: bool = False,
        status_code: int | None = None,
        headers: tuple[tuple[str, str], ...] = (),
        body: bytes | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_phase = failure_phase
        self.retryable_same_contract = retryable_same_contract
        self.is_tls_verification_failure = is_tls_verification_failure
        self.status_code = status_code
        self.headers = headers
        self.body = body


@dataclass(frozen=True, slots=True)
class NativeEgressRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None = None
    timeout_seconds: float = 60.0
    connect_timeout_seconds: float | None = None
    response_head_timeout_seconds: float | None = None
    proxy_url: str | None = None


@dataclass(frozen=True, slots=True)
class NativeWebSocketRequest:
    url: str
    headers: Mapping[str, str]
    connect_timeout_seconds: float
    max_message_bytes: int
    ping_interval_seconds: float | None = 20.0
    ping_timeout_seconds: float | None = None
    proxy_url: str | None = None


@dataclass(frozen=True, slots=True)
class NativeWebSocketMessage:
    kind: str
    text: str | None = None
    data: bytes | None = None
    close_code: int | None = None
    close_reason: str | None = None


class NativeEgressClient(Protocol):
    async def request(self, request: NativeEgressRequest) -> NativeEgressResponse: ...

    async def websocket(self, request: NativeWebSocketRequest) -> NativeEgressWebSocket: ...


class NativeEgressResponse:
    """One response stream multiplexed through a persistent native helper."""

    reason: str | None = None

    def __init__(
        self,
        *,
        status: int,
        http_version: str,
        headers: tuple[tuple[str, str], ...],
        client: SubprocessNativeEgressClient,
        request_id: str,
        generation: int,
        events: asyncio.Queue[dict[str, object] | BaseException],
    ) -> None:
        self.status = status
        self.http_version = http_version
        self.raw_headers = headers
        self.headers: CIMultiDict[str] = CIMultiDict(headers)
        self.content = _NativeEgressContent(self)
        self._client = client
        self._request_id = request_id
        self._generation = generation
        self._events = events
        self._iterated = False
        self._completed = False
        self._body_cache: bytes | None = None

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        if self._iterated:
            raise NativeEgressProtocolError("native response body can only be consumed once")
        self._iterated = True
        try:
            while True:
                item = await self._events.get()
                if isinstance(item, BaseException):
                    self._completed = True
                    self._client._finish_request(self._request_id, self._generation, self._events)
                    raise item
                event = item
                event_type = event.get("type")
                if event_type == "chunk":
                    encoded = event.get("data")
                    if not isinstance(encoded, str):
                        raise NativeEgressProtocolError("native chunk is missing base64 data")
                    try:
                        yield base64.b64decode(encoded, validate=True)
                    except ValueError as exc:
                        raise NativeEgressProtocolError("native chunk contains invalid base64 data") from exc
                    continue
                if event_type == "end":
                    self._completed = True
                    self._client._finish_request(self._request_id, self._generation, self._events)
                    return
                if event_type == "error":
                    self._completed = True
                    self._client._finish_request(self._request_id, self._generation, self._events)
                    raise _transport_error_from_event(event)
                if event_type == "cancelled":
                    self._completed = True
                    self._client._finish_request(self._request_id, self._generation, self._events)
                    raise NativeEgressTransportError(
                        "native request was cancelled",
                        failure_phase="cancelled",
                    )
                raise NativeEgressProtocolError(f"unexpected native stream event: {event_type!r}")
        finally:
            if not self._completed:
                await self.aclose()

    async def read(self) -> bytes:
        if self._body_cache is None:
            self._body_cache = b"".join([chunk async for chunk in self.iter_bytes()])
        return self._body_cache

    async def text(self, *, encoding: str = "utf-8", errors: str = "replace") -> str:
        return (await self.read()).decode(encoding, errors=errors)

    async def json(self, *, content_type: str | None = None) -> object:
        del content_type
        return json.loads(await self.read())

    async def aclose(self) -> None:
        if self._completed:
            return
        self._completed = True
        await self._client._cancel_request(self._request_id, self._generation, self._events)

    async def __aenter__(self) -> NativeEgressResponse:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()


class NativeEgressWebSocket:
    """Bidirectional WebSocket multiplexed through one native helper stream."""

    def __init__(
        self,
        *,
        status: int,
        headers: tuple[tuple[str, str], ...],
        client: SubprocessNativeEgressClient,
        process: asyncio.subprocess.Process,
        request_id: str,
        generation: int,
        events: asyncio.Queue[dict[str, object] | BaseException],
    ) -> None:
        self.status = status
        self.raw_headers = headers
        self.headers: CIMultiDict[str] = CIMultiDict(headers)
        self._client = client
        self._process = process
        self._request_id = request_id
        self._generation = generation
        self._events = events
        self._messages: asyncio.Queue[NativeWebSocketMessage | BaseException] = asyncio.Queue(
            maxsize=_NATIVE_WEBSOCKET_MESSAGE_QUEUE_LIMIT
        )
        self._pending: dict[str, asyncio.Future[None]] = {}
        self._command_sequence = 0
        self._completed = False
        self._closing = False
        self._remote_closed = False
        self._terminal_failure: BaseException | None = None
        self._pump_task = asyncio.create_task(
            self._pump(),
            name=f"native-websocket-pump-{request_id}",
        )

    async def send_text(self, text: str) -> None:
        await self._send("websocket_send_text", text=text)

    async def send_bytes(self, data: bytes) -> None:
        await self._send(
            "websocket_send_binary",
            data=base64.b64encode(data).decode("ascii"),
        )

    async def receive(self) -> NativeWebSocketMessage:
        if self._completed and self._messages.empty():
            raise self._terminal_failure or NativeEgressTransportError(
                "native websocket is closed",
                failure_phase="websocket_receive",
            )
        item = await self._messages.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self._completed or self._closing:
            return
        self._closing = True
        try:
            try:
                await self._send(
                    "websocket_close",
                    allow_closing=True,
                    code=code,
                    reason=reason,
                )
                await asyncio.wait_for(
                    asyncio.shield(self._pump_task),
                    timeout=_NATIVE_CANCEL_TIMEOUT_SECONDS,
                )
            except NativeEgressTransportError:
                # A peer close can race with a local close command. The helper
                # removes the socket immediately after forwarding the peer's
                # close frame, so the command may receive "not active" even
                # though the connection already ended cleanly.
                if self._remote_closed:
                    return
                if not self._pump_task.done():
                    self._pump_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._pump_task
                await self._client._cancel_request(
                    self._request_id,
                    self._generation,
                    self._events,
                )
                raise
            except BaseException:
                if not self._pump_task.done():
                    self._pump_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._pump_task
                await self._client._cancel_request(
                    self._request_id,
                    self._generation,
                    self._events,
                )
                raise
        finally:
            if not self._pump_task.done():
                self._pump_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._pump_task
            self._finish()

    def response_header(self, name: str) -> str | None:
        return self.headers.get(name)

    async def _send(self, event_type: str, *, allow_closing: bool = False, **payload: object) -> None:
        if self._completed or (self._closing and not allow_closing):
            raise NativeEgressTransportError(
                "native websocket is closed",
                failure_phase="websocket_send",
            )
        self._command_sequence += 1
        command_id = f"{self._request_id}:{self._command_sequence}"
        future = asyncio.get_running_loop().create_future()
        self._pending[command_id] = future
        try:
            await self._client._send_command(
                self._process,
                self._generation,
                {
                    "type": event_type,
                    "request_id": self._request_id,
                    "command_id": command_id,
                    **payload,
                },
            )
            await asyncio.wait_for(
                asyncio.shield(future),
                timeout=_NATIVE_WEBSOCKET_COMMAND_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise NativeEgressTransportError(
                "native websocket send acknowledgement timed out",
                failure_phase="websocket_send",
            ) from exc
        finally:
            self._pending.pop(command_id, None)

    async def _pump(self) -> None:
        terminal_failure: BaseException | None = None
        try:
            while True:
                item = await self._events.get()
                if isinstance(item, BaseException):
                    terminal_failure = item
                    return
                event_type = item.get("type")
                if event_type == "websocket_sent":
                    command_id = item.get("command_id")
                    if not isinstance(command_id, str):
                        raise NativeEgressProtocolError("native websocket acknowledgement is missing command_id")
                    future = self._pending.get(command_id)
                    if future is not None and not future.done():
                        future.set_result(None)
                    continue
                if event_type == "websocket_text":
                    text = item.get("text")
                    if not isinstance(text, str):
                        raise NativeEgressProtocolError("native websocket text event is invalid")
                    self._queue_message(NativeWebSocketMessage(kind="text", text=text))
                    continue
                if event_type == "websocket_binary":
                    encoded = item.get("data")
                    if not isinstance(encoded, str):
                        raise NativeEgressProtocolError("native websocket binary event is invalid")
                    try:
                        data = base64.b64decode(encoded, validate=True)
                    except ValueError as exc:
                        raise NativeEgressProtocolError("native websocket binary event is not base64") from exc
                    self._queue_message(NativeWebSocketMessage(kind="binary", data=data))
                    continue
                if event_type == "websocket_close":
                    code = item.get("code")
                    reason = item.get("reason")
                    if code is not None and not isinstance(code, int):
                        raise NativeEgressProtocolError("native websocket close code is invalid")
                    if reason is not None and not isinstance(reason, str):
                        raise NativeEgressProtocolError("native websocket close reason is invalid")
                    self._remote_closed = True
                    self._queue_message(
                        NativeWebSocketMessage(
                            kind="close",
                            close_code=code,
                            close_reason=reason,
                        )
                    )
                    terminal_failure = NativeEgressTransportError(
                        "native websocket closed",
                        failure_phase="websocket_receive",
                    )
                    return
                if event_type == "websocket_error":
                    terminal_failure = _websocket_error_from_event(item)
                    return
                if event_type == "cancelled":
                    terminal_failure = NativeEgressTransportError(
                        "native websocket was cancelled",
                        failure_phase="cancelled",
                    )
                    return
                raise NativeEgressProtocolError(f"unexpected native websocket event: {event_type!r}")
        except asyncio.CancelledError:
            terminal_failure = NativeEgressTransportError(
                "native websocket was closed locally",
                failure_phase="cancelled",
            )
            raise
        except BaseException as exc:
            terminal_failure = exc
            await self._client._abort_request(
                self._request_id,
                self._generation,
                self._events,
            )
        finally:
            if terminal_failure is None and not self._closing:
                terminal_failure = NativeEgressTransportError(
                    "native websocket closed",
                    failure_phase="websocket_receive",
                )
            if terminal_failure is not None:
                self._fail_pending(terminal_failure)
                self._queue_terminal(terminal_failure)
            self._finish()

    def _queue_message(self, message: NativeWebSocketMessage) -> None:
        try:
            self._messages.put_nowait(message)
        except asyncio.QueueFull as exc:
            while not self._messages.empty():
                with contextlib.suppress(asyncio.QueueEmpty):
                    self._messages.get_nowait()
            raise NativeEgressTransportError(
                "native websocket consumer exceeded the bounded message queue",
                failure_phase="consumer_backpressure",
            ) from exc

    def _queue_terminal(self, failure: BaseException) -> None:
        self._terminal_failure = failure
        with contextlib.suppress(asyncio.QueueFull):
            self._messages.put_nowait(failure)

    def _fail_pending(self, failure: BaseException) -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(failure)

    def _finish(self) -> None:
        if self._completed:
            return
        self._completed = True
        self._client._finish_request(
            self._request_id,
            self._generation,
            self._events,
        )


class _NativeEgressContent:
    def __init__(self, response: NativeEgressResponse) -> None:
        self._response = response

    def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        del size
        return self._response.iter_bytes()


class SubprocessNativeEgressClient:
    """Persistent multiplexed adapter for the pinned Rust Codex helper.

    The helper is deliberately discovered by an explicit executable path. Merely
    importing codex-lb never builds, installs, or requires a Rust component.
    """

    def __init__(self, executable: str | os.PathLike[str]) -> None:
        self.executable = Path(executable)
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._generation = 0
        self._request_sequence = 0
        self._streams: dict[
            str,
            tuple[int, asyncio.Queue[dict[str, object] | BaseException]],
        ] = {}
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._closed = False

    @property
    def available(self) -> bool:
        return self.executable.is_file() and os.access(self.executable, os.X_OK)

    async def request(self, request: NativeEgressRequest) -> NativeEgressResponse:
        if not self.available:
            raise NativeEgressUnavailable(f"native egress helper is unavailable: {self.executable}")
        if request.timeout_seconds <= 0:
            raise ValueError("native egress timeout_seconds must be positive")
        if request.connect_timeout_seconds is not None and request.connect_timeout_seconds <= 0:
            raise ValueError("native egress connect_timeout_seconds must be positive")
        if request.response_head_timeout_seconds is not None and request.response_head_timeout_seconds <= 0:
            raise ValueError("native egress response_head_timeout_seconds must be positive")

        process, generation = await self._ensure_process()
        self._request_sequence += 1
        request_id = f"{generation}:{self._request_sequence}"
        events: asyncio.Queue[dict[str, object] | BaseException] = asyncio.Queue(maxsize=_NATIVE_STREAM_QUEUE_LIMIT)
        self._streams[request_id] = (generation, events)
        request_event = {
            "type": "request",
            "request_id": request_id,
            "method": request.method,
            "url": request.url,
            "headers": list(request.headers.items()),
            "body": base64.b64encode(request.body).decode("ascii") if request.body is not None else None,
            "timeout_ms": max(1, round(request.timeout_seconds * 1000)),
            "connect_timeout_ms": (
                max(1, round(request.connect_timeout_seconds * 1000))
                if request.connect_timeout_seconds is not None
                else None
            ),
            "proxy_url": request.proxy_url,
        }
        try:
            await self._send_command(process, generation, request_event)
            head_timeout = request.response_head_timeout_seconds or request.timeout_seconds
            item = await asyncio.wait_for(events.get(), timeout=min(head_timeout, request.timeout_seconds))
            if isinstance(item, BaseException):
                self._finish_request(request_id, generation, events)
                raise item
            head = item
            if head.get("type") == "error":
                self._finish_request(request_id, generation, events)
                raise _transport_error_from_event(head)
            if head.get("type") != "head":
                raise NativeEgressProtocolError("native response did not begin with a head event")
            status = head.get("status")
            http_version = head.get("http_version")
            raw_headers = head.get("headers")
            if not isinstance(status, int) or not isinstance(http_version, str) or not isinstance(raw_headers, list):
                raise NativeEgressProtocolError("native response head has an invalid shape")
            headers = tuple(_decode_header_pair(pair) for pair in raw_headers)
        except TimeoutError as exc:
            await self._cancel_request(request_id, generation, events)
            raise NativeEgressTransportError(
                "native upstream response head timed out",
                failure_phase="timeout",
            ) from exc
        except BaseException:
            await self._cancel_request(request_id, generation, events)
            raise

        return NativeEgressResponse(
            status=status,
            http_version=http_version,
            headers=headers,
            client=self,
            request_id=request_id,
            generation=generation,
            events=events,
        )

    async def websocket(self, request: NativeWebSocketRequest) -> NativeEgressWebSocket:
        if not self.available:
            raise NativeEgressUnavailable(f"native egress helper is unavailable: {self.executable}")
        if request.connect_timeout_seconds <= 0:
            raise ValueError("native websocket connect_timeout_seconds must be positive")
        if request.max_message_bytes <= 0:
            raise ValueError("native websocket max_message_bytes must be positive")
        if request.ping_interval_seconds is not None and request.ping_interval_seconds <= 0:
            raise ValueError("native websocket ping_interval_seconds must be positive")
        if request.ping_timeout_seconds is not None and request.ping_timeout_seconds <= 0:
            raise ValueError("native websocket ping_timeout_seconds must be positive")

        process, generation = await self._ensure_process()
        self._request_sequence += 1
        request_id = f"{generation}:{self._request_sequence}"
        events: asyncio.Queue[dict[str, object] | BaseException] = asyncio.Queue(maxsize=_NATIVE_STREAM_QUEUE_LIMIT)
        self._streams[request_id] = (generation, events)
        try:
            await self._send_command(
                process,
                generation,
                {
                    "type": "websocket_connect",
                    "request_id": request_id,
                    "url": request.url,
                    "headers": list(request.headers.items()),
                    "connect_timeout_ms": max(1, round(request.connect_timeout_seconds * 1000)),
                    "max_message_bytes": request.max_message_bytes,
                    "ping_interval_ms": (
                        max(1, round(request.ping_interval_seconds * 1000))
                        if request.ping_interval_seconds is not None
                        else None
                    ),
                    "ping_timeout_ms": (
                        max(1, round(request.ping_timeout_seconds * 1000))
                        if request.ping_timeout_seconds is not None
                        else None
                    ),
                    "proxy_url": request.proxy_url,
                },
            )
            item = await asyncio.wait_for(events.get(), timeout=request.connect_timeout_seconds)
            if isinstance(item, BaseException):
                raise item
            if item.get("type") == "websocket_error":
                raise _websocket_error_from_event(item)
            if item.get("type") != "websocket_open":
                raise NativeEgressProtocolError("native websocket did not begin with an open event")
            status = item.get("status")
            raw_headers = item.get("headers")
            if not isinstance(status, int) or not isinstance(raw_headers, list):
                raise NativeEgressProtocolError("native websocket open event has an invalid shape")
            headers = tuple(_decode_header_pair(pair) for pair in raw_headers)
        except TimeoutError as exc:
            await self._cancel_request(request_id, generation, events)
            raise NativeEgressTransportError(
                "native websocket handshake timed out",
                failure_phase="connect",
                retryable_same_contract=True,
            ) from exc
        except BaseException:
            await self._cancel_request(request_id, generation, events)
            raise

        return NativeEgressWebSocket(
            status=status,
            headers=headers,
            client=self,
            process=process,
            request_id=request_id,
            generation=generation,
            events=events,
        )

    async def aclose(self) -> None:
        """Stop the current helper generation and fail any remaining streams."""

        async with self._start_lock:
            if self._closed:
                return
            self._closed = True
            process = self._process
            reader_task = self._reader_task
            generation = self._generation
            if process is not None and process.stdin is not None:
                process.stdin.close()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    await process.stdin.wait_closed()
            if process is not None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=_NATIVE_CANCEL_TIMEOUT_SECONDS)
                except TimeoutError:
                    await _stop_process(process)
            if reader_task is not None and reader_task is not asyncio.current_task():
                if not reader_task.done():
                    reader_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, NativeEgressError):
                    await reader_task
            self._fail_generation(
                generation,
                NativeEgressTransportError("native helper closed", failure_phase="shutdown"),
            )
            self._process = None
            self._reader_task = None

    async def _ensure_process(self) -> tuple[asyncio.subprocess.Process, int]:
        async with self._start_lock:
            if self._closed:
                raise NativeEgressUnavailable("native egress client is closed")
            if self._process is not None and self._process.returncode is None:
                return self._process, self._generation
            try:
                process = await asyncio.create_subprocess_exec(
                    str(self.executable),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    limit=_NATIVE_EVENT_LINE_LIMIT,
                )
            except OSError as exc:
                raise NativeEgressUnavailable(f"native egress helper could not start: {self.executable}") from exc
            if process.stdin is None or process.stdout is None:
                await _stop_process(process)
                raise NativeEgressUnavailable("native helper pipes are unavailable")
            try:
                await self._negotiate_process(process)
            except BaseException:
                await _stop_process(process)
                raise
            self._generation += 1
            generation = self._generation
            self._process = process
            self._reader_task = asyncio.create_task(
                self._read_process(process, generation),
                name=f"native-egress-reader-{generation}",
            )
            return process, generation

    async def _negotiate_process(self, process: asyncio.subprocess.Process) -> None:
        """Fail closed before dispatch when the installed worker is incompatible."""

        stdin = process.stdin
        stdout = process.stdout
        if stdin is None or stdout is None:
            raise NativeEgressProtocolError("native helper handshake pipes are unavailable")
        hello = {
            "type": "client_hello",
            "min_protocol_version": _NATIVE_PROTOCOL_VERSION,
            "max_protocol_version": _NATIVE_PROTOCOL_VERSION,
        }
        try:
            stdin.write(json.dumps(hello, separators=(",", ":")).encode("utf-8") + b"\n")
            await stdin.drain()
            event = await asyncio.wait_for(
                _read_event(stdout),
                timeout=_NATIVE_PROTOCOL_HANDSHAKE_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise NativeEgressProtocolError("native helper protocol handshake timed out") from exc
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise NativeEgressProtocolError("native helper closed during protocol handshake") from exc

        if event.get("type") != "server_hello":
            raise NativeEgressProtocolError("native helper did not acknowledge the protocol handshake")
        version = event.get("protocol_version")
        if version != _NATIVE_PROTOCOL_VERSION:
            raise NativeEgressProtocolError(f"native helper selected unsupported protocol version: {version!r}")
        capabilities = event.get("capabilities")
        if not isinstance(capabilities, list) or not all(isinstance(value, str) for value in capabilities):
            raise NativeEgressProtocolError("native helper capabilities have an invalid shape")
        missing = sorted(_REQUIRED_NATIVE_CAPABILITIES.difference(capabilities))
        if missing:
            raise NativeEgressProtocolError(f"native helper is missing required capabilities: {', '.join(missing)}")

    async def _send_command(
        self,
        process: asyncio.subprocess.Process,
        generation: int,
        command: Mapping[str, object],
    ) -> None:
        async with self._write_lock:
            if process is not self._process or generation != self._generation or process.returncode is not None:
                raise NativeEgressTransportError("native helper exited before command dispatch")
            stdin = process.stdin
            if stdin is None:
                raise NativeEgressTransportError("native helper stdin is unavailable")
            try:
                stdin.write(json.dumps(command, separators=(",", ":")).encode("utf-8") + b"\n")
                await stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise NativeEgressTransportError("native helper closed during command dispatch") from exc

    async def _read_process(self, process: asyncio.subprocess.Process, generation: int) -> None:
        stdout = process.stdout
        if stdout is None:
            self._fail_generation(generation, NativeEgressProtocolError("native helper stdout is unavailable"))
            return
        failure: NativeEgressError = NativeEgressTransportError(
            "native helper exited with active requests",
            failure_phase="helper_exit",
        )
        try:
            while True:
                event = await _read_event(stdout)
                request_id = event.get("request_id")
                if not isinstance(request_id, str):
                    raise NativeEgressProtocolError("native helper event is missing request_id")
                state = self._streams.get(request_id)
                if state is None or state[0] != generation:
                    continue
                events = state[1]
                try:
                    events.put_nowait(event)
                    if event.get("type") in {"head", "websocket_open"}:
                        # Hand the accepted response to its consumer before a
                        # helper with already-buffered output can fill the body
                        # queue in this reader task's scheduling turn.
                        await asyncio.sleep(0)
                except asyncio.QueueFull:
                    overflow_failure = NativeEgressTransportError(
                        "native stream consumer exceeded the bounded event queue",
                        failure_phase="consumer_backpressure",
                    )
                    self._finish_request(request_id, generation, events)
                    while not events.empty():
                        with contextlib.suppress(asyncio.QueueEmpty):
                            events.get_nowait()
                    events.put_nowait(overflow_failure)
                    asyncio.create_task(
                        self._cancel_orphaned_request(process, generation, request_id),
                        name=f"native-egress-overflow-cancel-{request_id}",
                    )
        except NativeEgressProtocolError as exc:
            failure = exc
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            failure = NativeEgressTransportError(
                "native helper reader failed",
                failure_phase="helper_read",
            )
        finally:
            self._fail_generation(generation, failure)
            if process.returncode is None:
                await _stop_process(process)

    async def _cancel_request(
        self,
        request_id: str,
        generation: int,
        events: asyncio.Queue[dict[str, object] | BaseException],
    ) -> None:
        state = self._streams.get(request_id)
        if state != (generation, events):
            return
        process = self._process
        if process is not None and generation == self._generation and process.returncode is None:
            try:
                await self._send_command(
                    process,
                    generation,
                    {"type": "cancel", "request_id": request_id},
                )
                while True:
                    item = await asyncio.wait_for(events.get(), timeout=_NATIVE_CANCEL_TIMEOUT_SECONDS)
                    if isinstance(item, BaseException) or item.get("type") in {"cancelled", "end", "error"}:
                        break
            except (TimeoutError, NativeEgressError):
                pass
        self._finish_request(request_id, generation, events)

    async def _cancel_orphaned_request(
        self,
        process: asyncio.subprocess.Process,
        generation: int,
        request_id: str,
    ) -> None:
        with contextlib.suppress(NativeEgressError):
            await self._send_command(
                process,
                generation,
                {"type": "cancel", "request_id": request_id},
            )

    async def _abort_request(
        self,
        request_id: str,
        generation: int,
        events: asyncio.Queue[dict[str, object] | BaseException],
    ) -> None:
        """Best-effort cancel when the stream protocol itself is unusable."""

        state = self._streams.get(request_id)
        if state != (generation, events):
            return
        process = self._process
        if process is not None and generation == self._generation and process.returncode is None:
            with contextlib.suppress(NativeEgressError):
                await self._send_command(
                    process,
                    generation,
                    {"type": "cancel", "request_id": request_id},
                )
        self._finish_request(request_id, generation, events)

    def _finish_request(
        self,
        request_id: str,
        generation: int,
        events: asyncio.Queue[dict[str, object] | BaseException],
    ) -> None:
        if self._streams.get(request_id) == (generation, events):
            self._streams.pop(request_id, None)

    def _fail_generation(self, generation: int, failure: BaseException) -> None:
        if self._generation == generation:
            self._process = None
            self._reader_task = None
        failed = [
            (request_id, events)
            for request_id, (stream_generation, events) in self._streams.items()
            if stream_generation == generation
        ]
        for request_id, events in failed:
            self._streams.pop(request_id, None)
            while not events.empty():
                with contextlib.suppress(asyncio.QueueEmpty):
                    events.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                events.put_nowait(failure)


@lru_cache(maxsize=1)
def discover_native_egress_client() -> SubprocessNativeEgressClient | None:
    """Resolve only the fixed packaged helper from the runtime PATH."""

    executable = shutil.which(_NATIVE_EGRESS_EXECUTABLE)
    if executable is None:
        return None
    client = SubprocessNativeEgressClient(executable)
    if not client.available:
        return None
    logger.info("Native Codex HTTP egress enabled executable=%s", executable)
    return client


async def close_discovered_native_egress_client() -> None:
    """Close the cached helper generation, then allow clean rediscovery."""

    client = discover_native_egress_client()
    if client is not None:
        await client.aclose()
    discover_native_egress_client.cache_clear()


def _decode_header_pair(value: object) -> tuple[str, str]:
    if not isinstance(value, list) or len(value) != 2 or not all(isinstance(item, str) for item in value):
        raise NativeEgressProtocolError("native response contains an invalid header pair")
    pair = cast(list[str], value)
    return pair[0], pair[1]


def _transport_error_from_event(event: Mapping[str, object]) -> NativeEgressTransportError:
    message = event.get("message")
    failure_phase = event.get("failure_phase")
    retryable_same_contract = event.get("retryable_same_contract")
    is_tls_verification_failure = event.get("is_tls_verification_failure")
    return NativeEgressTransportError(
        message if isinstance(message, str) else "native request failed",
        failure_phase=failure_phase if isinstance(failure_phase, str) else "request",
        retryable_same_contract=retryable_same_contract if isinstance(retryable_same_contract, bool) else False,
        is_tls_verification_failure=(
            is_tls_verification_failure if isinstance(is_tls_verification_failure, bool) else False
        ),
    )


def _websocket_error_from_event(event: Mapping[str, object]) -> NativeEgressTransportError:
    message = event.get("message")
    failure_phase = event.get("failure_phase")
    retryable_same_contract = event.get("retryable_same_contract")
    is_tls_verification_failure = event.get("is_tls_verification_failure")
    status = event.get("status")
    raw_headers = event.get("headers")
    encoded_body = event.get("body")
    headers: tuple[tuple[str, str], ...] = ()
    if isinstance(raw_headers, list):
        headers = tuple(_decode_header_pair(pair) for pair in raw_headers)
    body: bytes | None = None
    if isinstance(encoded_body, str):
        try:
            body = base64.b64decode(encoded_body, validate=True)
        except ValueError as exc:
            raise NativeEgressProtocolError("native websocket error body is not base64") from exc
    return NativeEgressTransportError(
        message if isinstance(message, str) else "native websocket failed",
        failure_phase=failure_phase if isinstance(failure_phase, str) else "websocket",
        retryable_same_contract=retryable_same_contract if isinstance(retryable_same_contract, bool) else False,
        is_tls_verification_failure=(
            is_tls_verification_failure if isinstance(is_tls_verification_failure, bool) else False
        ),
        status_code=status if isinstance(status, int) else None,
        headers=headers,
        body=body,
    )


async def _read_event(stdout: asyncio.StreamReader) -> dict[str, object]:
    line = await stdout.readline()
    if not line:
        raise NativeEgressProtocolError("native helper closed its stream unexpectedly")
    try:
        value = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeEgressProtocolError("native helper emitted invalid JSON") from exc
    if not isinstance(value, dict):
        raise NativeEgressProtocolError("native helper event must be a JSON object")
    return cast(dict[str, object], value)


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=1.0)
    except TimeoutError:
        process.kill()
        await process.wait()
