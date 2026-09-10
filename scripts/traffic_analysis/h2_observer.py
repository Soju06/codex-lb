"""Privacy-safe raw HTTP/2 observer and deterministic probe origin.

This is an explicitly launched development tool.  It terminates TLS, observes
bounded HTTP/2 frame metadata before handing bytes to hyper-h2, and serves the
same deterministic model/Responses payloads as ``origin_fixture``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import ipaddress
import json
import signal
import ssl
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, RequestReceived, StreamEnded
from h2.exceptions import H2Error

try:
    from scripts.traffic_analysis.origin_fixture import (
        MAX_REQUEST_BYTES,
        _decode_request_body,
        _probe_model,
        _response_events,
    )
except ModuleNotFoundError:  # Allow direct script execution.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.traffic_analysis.origin_fixture import (
        MAX_REQUEST_BYTES,
        _decode_request_body,
        _probe_model,
        _response_events,
    )


CLIENT_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 19443
MAX_FRAME_PAYLOAD = 1024 * 1024
MAX_TRACKED_FRAMES = 512
MAX_ACTIVE_STREAMS = 128
MAX_CONNECTIONS = 32
IDLE_TIMEOUT_SECONDS = 60.0
MODEL_PATHS = frozenset({"/models", "/v1/models", "/codex/models", "/backend-api/codex/models"})
RESPONSE_PATHS = frozenset({"/v1/responses", "/codex/responses", "/backend-api/codex/responses"})
FRAME_TYPES = {
    0: "DATA",
    1: "HEADERS",
    2: "PRIORITY",
    3: "RST_STREAM",
    4: "SETTINGS",
    5: "PUSH_PROMISE",
    6: "PING",
    7: "GOAWAY",
    8: "WINDOW_UPDATE",
    9: "CONTINUATION",
}


class H2ObservationError(ValueError):
    """Raised when bounded raw HTTP/2 observation cannot continue safely."""


@dataclass(frozen=True)
class FrameObservation:
    sequence: int
    frame_type: str
    flags: int
    stream_id: int
    length: int
    settings: tuple[tuple[int, int], ...] = ()
    window_increment: int | None = None
    fragment_sha256: str | None = None

    def as_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "sequence": self.sequence,
            "type": self.frame_type,
            "flags": self.flags,
            "stream_id": self.stream_id,
            "length": self.length,
        }
        if self.settings:
            record["settings"] = [{"id": setting_id, "value": value} for setting_id, value in self.settings]
        if self.window_increment is not None:
            record["window_increment"] = self.window_increment
        if self.fragment_sha256 is not None:
            record["fragment_sha256"] = self.fragment_sha256
        return record


class RawH2FrameParser:
    """Incrementally observe client HTTP/2 bytes without retaining payloads."""

    def __init__(
        self,
        *,
        max_frame_payload: int = MAX_FRAME_PAYLOAD,
        max_tracked_frames: int = MAX_TRACKED_FRAMES,
    ) -> None:
        self.max_frame_payload = max_frame_payload
        self.max_tracked_frames = max_tracked_frames
        self.preface_seen = False
        self.frames: list[FrameObservation] = []
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[FrameObservation]:
        self._buffer.extend(data)
        emitted: list[FrameObservation] = []
        if not self.preface_seen:
            compared = min(len(self._buffer), len(CLIENT_PREFACE))
            if self._buffer[:compared] != CLIENT_PREFACE[:compared]:
                raise H2ObservationError("invalid HTTP/2 client connection preface")
            if len(self._buffer) < len(CLIENT_PREFACE):
                return emitted
            del self._buffer[: len(CLIENT_PREFACE)]
            self.preface_seen = True

        while len(self._buffer) >= 9:
            length = int.from_bytes(self._buffer[:3], "big")
            if length > self.max_frame_payload:
                raise H2ObservationError("HTTP/2 frame exceeds observer payload limit")
            if len(self._buffer) < 9 + length:
                break
            if len(self.frames) >= self.max_tracked_frames:
                raise H2ObservationError("HTTP/2 frame count exceeds observer limit")
            frame_type_id = self._buffer[3]
            flags = self._buffer[4]
            stream_id = int.from_bytes(self._buffer[5:9], "big") & 0x7FFF_FFFF
            payload = bytes(self._buffer[9 : 9 + length])
            del self._buffer[: 9 + length]
            observation = self._observe_frame(frame_type_id, flags, stream_id, payload)
            self.frames.append(observation)
            emitted.append(observation)
        return emitted

    def _observe_frame(self, frame_type_id: int, flags: int, stream_id: int, payload: bytes) -> FrameObservation:
        settings: tuple[tuple[int, int], ...] = ()
        window_increment: int | None = None
        fragment_sha256: str | None = None
        if frame_type_id == 4 and not flags & 0x1:
            if stream_id != 0 or len(payload) % 6:
                raise H2ObservationError("malformed HTTP/2 SETTINGS frame")
            settings = tuple(
                (
                    int.from_bytes(payload[offset : offset + 2], "big"),
                    int.from_bytes(payload[offset + 2 : offset + 6], "big"),
                )
                for offset in range(0, len(payload), 6)
            )
        elif frame_type_id == 8:
            if len(payload) != 4:
                raise H2ObservationError("malformed HTTP/2 WINDOW_UPDATE frame")
            window_increment = int.from_bytes(payload, "big") & 0x7FFF_FFFF
        elif frame_type_id in {1, 9}:
            fragment_sha256 = hashlib.sha256(payload).hexdigest()
        return FrameObservation(
            sequence=len(self.frames) + 1,
            frame_type=FRAME_TYPES.get(frame_type_id, f"UNKNOWN_{frame_type_id}"),
            flags=flags,
            stream_id=stream_id,
            length=len(payload),
            settings=settings,
            window_increment=window_increment,
            fragment_sha256=fragment_sha256,
        )

    @property
    def initial_settings(self) -> list[dict[str, int]] | None:
        for frame in self.frames:
            if frame.frame_type == "SETTINGS" and not frame.flags & 0x1:
                return [{"id": setting_id, "value": value} for setting_id, value in frame.settings]
        return None

    @property
    def connection_control_frames(self) -> list[dict[str, Any]]:
        frames: list[dict[str, Any]] = []
        for frame in self.frames:
            if frame.frame_type == "HEADERS":
                break
            if frame.frame_type in {"SETTINGS", "WINDOW_UPDATE", "PRIORITY", "PING"}:
                frames.append(frame.as_record())
        return frames


@dataclass
class RequestState:
    method: str
    path: str
    header_names: list[str]
    content_encoding: str | None
    body: bytearray = field(default_factory=bytearray)


@dataclass(frozen=True)
class OriginResponse:
    status: int
    content_type: str
    body: bytes
    transport: str


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def build_origin_response(request: RequestState) -> OriginResponse:
    """Return a deterministic response without reflecting request content."""

    if request.method == "GET" and request.path in MODEL_PATHS:
        return OriginResponse(200, "application/json", _json_bytes({"models": [_probe_model()]}), "http_json")
    if request.method != "POST" or request.path not in RESPONSE_PATHS:
        return OriginResponse(404, "application/json", _json_bytes({"error": "not_found"}), "http_json")
    try:
        decoded = _decode_request_body(bytes(request.body), request.content_encoding)
        payload = json.loads(decoded)
        if not isinstance(payload, Mapping):
            raise ValueError("request body must be a JSON object")
    except HTTPException as exc:
        return OriginResponse(
            exc.status_code,
            "application/json",
            _json_bytes({"error": "invalid_request"}),
            "http_json",
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return OriginResponse(400, "application/json", _json_bytes({"error": "invalid_request"}), "http_json")
    events = _response_events()
    if payload.get("stream") is True:
        body = b"".join(f"data: {json.dumps(event, separators=(',', ':'))}\n\n".encode() for event in events)
        return OriginResponse(200, "text/event-stream", body, "http_sse")
    return OriginResponse(200, "application/json", _json_bytes(events[-1]["response"]), "http_json")


class JsonlSink:
    def __init__(self, output: Path) -> None:
        self.output = output
        self._lock = asyncio.Lock()

    async def write(self, record: Mapping[str, Any]) -> None:
        line = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
        async with self._lock:
            self.output.parent.mkdir(parents=True, exist_ok=True)
            with self.output.open("a", encoding="utf-8") as handle:
                handle.write(line)


class H2ObserverSession:
    """One bounded client connection, separated for deterministic tests."""

    def __init__(self, connection_id: str, sink: JsonlSink) -> None:
        self.connection_id = connection_id
        self.sink = sink
        self.parser = RawH2FrameParser()
        self.h2 = H2Connection(H2Configuration(client_side=False, header_encoding="utf-8"))
        self.requests: dict[int, RequestState] = {}
        self.request_sequence = 0

    def initiate(self) -> bytes:
        self.h2.initiate_connection()
        return self.h2.data_to_send()

    async def receive(self, data: bytes) -> tuple[bytes, list[dict[str, Any]]]:
        self.parser.feed(data)
        completed: list[dict[str, Any]] = []
        events = self.h2.receive_data(data)
        for event in events:
            if isinstance(event, RequestReceived):
                if len(self.requests) >= MAX_ACTIVE_STREAMS:
                    raise H2ObservationError("active stream count exceeds observer limit")
                headers = [(str(name), str(value)) for name, value in event.headers]
                pseudo = {name: value for name, value in headers if name.startswith(":")}
                encoding = next((value for name, value in headers if name.casefold() == "content-encoding"), None)
                self.requests[event.stream_id] = RequestState(
                    method=pseudo.get(":method", "").upper(),
                    path=urlsplit(pseudo.get(":path", "")).path,
                    header_names=[name for name, _value in headers],
                    content_encoding=encoding,
                )
            elif isinstance(event, DataReceived):
                request = self.requests.get(event.stream_id)
                if request is None:
                    raise H2ObservationError("DATA received for an untracked request stream")
                request.body.extend(event.data)
                if len(request.body) > MAX_REQUEST_BYTES:
                    raise H2ObservationError("request body exceeds observer limit")
                self.h2.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
            elif isinstance(event, StreamEnded):
                request = self.requests.pop(event.stream_id, None)
                if request is None:
                    raise H2ObservationError("stream ended without a tracked request")
                response = build_origin_response(request)
                self._send_response(event.stream_id, response)
                completed.append(self._build_record(event.stream_id, request, response))
        output = self.h2.data_to_send()
        for record in completed:
            await self.sink.write(record)
        return output, completed

    def _send_response(self, stream_id: int, response: OriginResponse) -> None:
        headers = [
            (":status", str(response.status)),
            ("content-type", response.content_type),
            ("content-length", str(len(response.body))),
            ("cache-control", "no-store"),
        ]
        self.h2.send_headers(stream_id, headers)
        self.h2.send_data(stream_id, response.body, end_stream=True)

    def _build_record(self, stream_id: int, request: RequestState, response: OriginResponse) -> dict[str, Any]:
        self.request_sequence += 1
        request_frames = [frame.as_record() for frame in self.parser.frames if frame.stream_id == stream_id]
        return {
            "schema_version": 1,
            "kind": "http2_wire_request",
            "timestamp": time.time(),
            "connection_id": self.connection_id,
            "request_sequence": self.request_sequence,
            "stream_id": stream_id,
            "connection_reused": self.request_sequence > 1,
            "preface_seen": self.parser.preface_seen,
            "initial_settings": self.parser.initial_settings,
            "connection_control_frames": self.parser.connection_control_frames,
            "request_frames": request_frames,
            "request": {
                "method": request.method,
                "path": request.path,
                "header_names": request.header_names,
                "body_bytes": len(request.body),
                "content_encoded": bool(request.content_encoding),
            },
            "response": {"status": response.status, "transport": response.transport},
        }


def _is_loopback_bind(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, session: H2ObserverSession) -> None:
    ssl_object: ssl.SSLObject | None = writer.get_extra_info("ssl_object")
    if ssl_object is None or ssl_object.selected_alpn_protocol() != "h2":
        writer.close()
        with contextlib.suppress(OSError, ssl.SSLError):
            await writer.wait_closed()
        return
    try:
        writer.write(session.initiate())
        await writer.drain()
        while data := await asyncio.wait_for(reader.read(64 * 1024), timeout=IDLE_TIMEOUT_SECONDS):
            output, _records = await session.receive(data)
            if output:
                writer.write(output)
                await writer.drain()
    except (H2Error, H2ObservationError, OSError, TimeoutError, ssl.SSLError):
        pass
    finally:
        writer.close()
        with contextlib.suppress(OSError, ssl.SSLError):
            await writer.wait_closed()


def tls_context(cert: Path, key: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_alpn_protocols(["h2"])
    context.load_cert_chain(cert, key)
    return context


async def serve(args: argparse.Namespace) -> None:
    if not _is_loopback_bind(args.host) and not args.allow_public_bind:
        raise SystemExit("non-loopback bind requires --allow-public-bind")
    sink = JsonlSink(args.output)
    connection_numbers = iter(range(1, 2**63))
    active_connections = 0

    async def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal active_connections
        if active_connections >= MAX_CONNECTIONS:
            writer.close()
            with contextlib.suppress(OSError, ssl.SSLError):
                await writer.wait_closed()
            return
        active_connections += 1
        try:
            session = H2ObserverSession(f"connection-{next(connection_numbers)}", sink)
            await handle_client(reader, writer, session)
        finally:
            active_connections -= 1

    server = await asyncio.start_server(connected, args.host, args.port, ssl=tls_context(args.cert, args.key))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    addresses = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"HTTP/2 observer listening on {addresses}; records: {args.output}")
    async with server:
        await stop.wait()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert", type=Path, required=True, help="operator-provided TLS leaf certificate")
    parser.add_argument("--key", type=Path, required=True, help="operator-provided TLS private key")
    parser.add_argument("--output", type=Path, required=True, help="privacy-safe JSONL output")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--allow-public-bind", action="store_true")
    return parser


def main() -> None:
    asyncio.run(serve(build_parser().parse_args()))


if __name__ == "__main__":
    main()
