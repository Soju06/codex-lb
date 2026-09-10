"""Transport-neutral parsing primitives for Codex traffic captures.

The capture tools deliberately keep wire records simple JSON objects.  This
module is the boundary at which response bodies and websocket frames become a
common event representation suitable for comparison and reporting.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, TypeAlias

Transport: TypeAlias = Literal["http_json", "http_sse", "websocket"]

HTTP_JSON: Final[Transport] = "http_json"
HTTP_SSE: Final[Transport] = "http_sse"
WEBSOCKET: Final[Transport] = "websocket"
TRANSPORTS: Final[tuple[Transport, ...]] = (HTTP_JSON, HTTP_SSE, WEBSOCKET)

TERMINAL_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {"response.completed", "response.incomplete", "response.failed", "error"}
)

_SSE_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_KNOWN_METADATA_EVENT_TYPES = ("responsesapi.websocket_timing",)
_KNOWN_METADATA_EVENT_DIGESTS = {
    (hashlib.sha256(value.encode("utf-8")).hexdigest(), len(value.encode("utf-8"))): value
    for value in _KNOWN_METADATA_EVENT_TYPES
}


@dataclass(frozen=True, slots=True)
class ProtocolEvent:
    """One logical event, independent of its HTTP/SSE/websocket framing."""

    type: str
    data: Any
    raw_data: str | None = None
    event_id: str | None = None
    retry: int | None = None
    done: bool = False

    @property
    def event_type(self) -> str:
        """Compatibility alias useful to callers that avoid ``type``."""

        return self.type

    @property
    def is_terminal(self) -> bool:
        return self.done or self.type in TERMINAL_EVENT_TYPES

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"type": self.type, "data": self.data}
        if self.raw_data is not None:
            result["raw_data"] = self.raw_data
        if self.event_id is not None:
            result["event_id"] = self.event_id
        if self.retry is not None:
            result["retry"] = self.retry
        if self.done:
            result["done"] = True
        return result


# Short name for callers constructing synthetic captures in tests.
Event = ProtocolEvent


def event_type(payload: Any, *, default: str = "message") -> str:
    """Return the event discriminator used by Codex Responses payloads."""

    if isinstance(payload, Mapping):
        value = payload.get("type")
        if isinstance(value, str) and value:
            return value
        if isinstance(value, Mapping):
            digest = value.get("$sha256")
            byte_count = value.get("$bytes")
            if isinstance(digest, str) and isinstance(byte_count, int) and not isinstance(byte_count, bool):
                restored = _KNOWN_METADATA_EVENT_DIGESTS.get((digest, byte_count))
                if restored is not None:
                    return restored
    return default


def decode_json(value: Any) -> Any:
    """Decode a JSON string/bytes value, leaving non-JSON input unchanged."""

    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value).decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _dispatch_sse(
    data_lines: list[str],
    explicit_type: str | None,
    event_id: str | None,
    retry: int | None,
) -> ProtocolEvent | None:
    # Per the SSE algorithm, a comment-only block does not dispatch an event.
    if not data_lines:
        return None

    raw_data = "\n".join(data_lines)
    if raw_data == "[DONE]":
        return ProtocolEvent(
            type="done" if explicit_type in (None, "", "message") else explicit_type,
            data="[DONE]",
            raw_data=raw_data,
            event_id=event_id,
            retry=retry,
            done=True,
        )

    data = decode_json(raw_data)
    resolved_type = explicit_type or event_type(data)
    return ProtocolEvent(
        type=resolved_type,
        data=data,
        raw_data=raw_data,
        event_id=event_id,
        retry=retry,
    )


def parse_sse(text: str | bytes | bytearray | memoryview | None) -> list[ProtocolEvent]:
    """Parse an SSE body according to the event-stream line rules.

    LF, CRLF, and bare CR line endings are accepted.  Repeated ``data`` fields
    are joined with newlines, comments are ignored, the last ``event`` field in
    a block wins, and a final unterminated block is retained (captures are often
    taken immediately after a socket closes).  JSON data is decoded when
    possible; non-JSON data and the OpenAI ``[DONE]`` sentinel are preserved.
    """

    if text is None:
        return []
    if isinstance(text, (bytes, bytearray, memoryview)):
        text = bytes(text).decode("utf-8", errors="replace")
    if not isinstance(text, str):
        raise TypeError("SSE body must be text or bytes")

    events: list[ProtocolEvent] = []
    data_lines: list[str] = []
    explicit_type: str | None = None
    event_id: str | None = None
    retry: int | None = None

    def dispatch() -> None:
        nonlocal data_lines, explicit_type, retry
        parsed = _dispatch_sse(data_lines, explicit_type, event_id, retry)
        if parsed is not None:
            events.append(parsed)
        data_lines = []
        explicit_type = None
        retry = None

    for line in _SSE_LINE_BREAK.split(text):
        if line == "":
            dispatch()
            continue
        if line.startswith(":"):
            continue

        field, separator, value = line.partition(":")
        if not separator:
            value = ""
        elif value.startswith(" "):
            # The wire format removes at most one optional leading space.
            value = value[1:]

        if field == "data":
            data_lines.append(value)
        elif field == "event":
            explicit_type = value
        elif field == "id" and "\x00" not in value:
            event_id = value
        elif field == "retry" and value.isdecimal():
            retry = int(value)

    # ``split`` produces a final empty string for a terminated stream, so this
    # is a no-op in the common case and preserves truncated final blocks.
    dispatch()
    return events


# Descriptive alias matching the terminology used by capture files.
parse_sse_body = parse_sse


def parse_structured_sse_events(items: Any) -> list[ProtocolEvent]:
    """Convert capture-server ``response.events`` entries to common events."""

    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return []

    result: list[ProtocolEvent] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        explicit_type = item.get("event")
        if not isinstance(explicit_type, str) or not explicit_type:
            explicit_type = None
        data = item.get("data")
        data = decode_json(data)
        raw_data = item.get("raw_data")
        if not isinstance(raw_data, str):
            raw_data = item.get("data") if isinstance(item.get("data"), str) else None
        done = data == "[DONE]" or item.get("done") is True
        # Capture writers use SSE's default event name (``message``) even for
        # OpenAI's transport sentinel.  Normalize that default so raw-body and
        # pre-parsed captures compare identically.
        if done and explicit_type in (None, "message"):
            resolved = "done"
        else:
            resolved = explicit_type or event_type(data)
        event_id = item.get("event_id", item.get("id"))
        retry = item.get("retry")
        result.append(
            ProtocolEvent(
                type=resolved,
                data=data,
                raw_data=raw_data,
                event_id=event_id if isinstance(event_id, str) else None,
                retry=retry if isinstance(retry, int) and not isinstance(retry, bool) else None,
                done=done,
            )
        )
    return result


def parse_websocket_data(data: Any) -> ProtocolEvent:
    """Parse one captured websocket ``data`` value into a common event."""

    raw_data: str | None
    if isinstance(data, str):
        raw_data = data
    elif isinstance(data, (bytes, bytearray, memoryview)):
        raw_data = bytes(data).decode("utf-8", errors="replace")
    else:
        raw_data = None
    decoded = decode_json(data)
    done = decoded == "[DONE]"
    return ProtocolEvent(
        type="done" if done else event_type(decoded),
        data=decoded,
        raw_data=raw_data,
        done=done,
    )


parse_websocket_message = parse_websocket_data


def _headers(mapping: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(mapping, Mapping):
        return mapping.items()
    if isinstance(mapping, Sequence) and not isinstance(mapping, (str, bytes, bytearray)):
        return (
            (item[0], item[1])
            for item in mapping
            if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)) and len(item) >= 2
        )
    return ()


def header_value(headers: Any, name: str) -> str | None:
    """Look up a header in mapping or ordered-pair capture representation."""

    wanted = name.casefold()
    values = [str(value) for key, value in _headers(headers) if str(key).casefold() == wanted]
    return ", ".join(values) if values else None


def classify_http_record(record: Mapping[str, Any]) -> Transport:
    """Classify an HTTP capture as JSON or SSE.

    Structured SSE evidence takes precedence over a stale ``http_json`` label.
    This matters for captures from upstreams that omit ``Content-Type``.  A
    declared ``http_sse`` label remains authoritative, followed by content type,
    structured events, and an SSE-looking body.
    """

    declared = record.get("transport")
    if declared == HTTP_SSE:
        return HTTP_SSE

    response = record.get("response")
    if not isinstance(response, Mapping):
        return HTTP_JSON
    content_type = header_value(response.get("headers"), "content-type")
    if content_type and content_type.split(";", 1)[0].strip().casefold() == "text/event-stream":
        return HTTP_SSE
    if isinstance(response.get("events"), list):
        return HTTP_SSE
    body = response.get("body")
    if isinstance(body, str):
        first_nonempty = next((line for line in _SSE_LINE_BREAK.split(body) if line), "")
        if first_nonempty.startswith(("data:", "event:", ":")):
            return HTTP_SSE
    if declared == HTTP_JSON:
        return HTTP_JSON
    return HTTP_JSON


def classify_record(record: Mapping[str, Any]) -> Transport:
    """Return the transport of an HTTP or websocket capture record."""

    if record.get("kind") == "websocket_message":
        return WEBSOCKET
    return classify_http_record(record)


def parse_http_response(record: Mapping[str, Any]) -> list[ProtocolEvent]:
    """Parse the response portion of one HTTP capture record."""

    response = record.get("response")
    if not isinstance(response, Mapping):
        return []

    transport = classify_http_record(record)
    if transport == HTTP_SSE:
        structured = response.get("events")
        if isinstance(structured, list):
            return parse_structured_sse_events(structured)
        return parse_sse(response.get("body"))

    if "body" not in response:
        return []
    body = decode_json(response.get("body"))
    return [ProtocolEvent(type=event_type(body, default="http.response"), data=body)]


def is_response_create(event: ProtocolEvent) -> bool:
    return event.type == "response.create"


def is_terminal_event(event: ProtocolEvent) -> bool:
    return event.is_terminal


__all__ = [
    "Event",
    "HTTP_JSON",
    "HTTP_SSE",
    "ProtocolEvent",
    "TERMINAL_EVENT_TYPES",
    "TRANSPORTS",
    "Transport",
    "WEBSOCKET",
    "classify_http_record",
    "classify_record",
    "decode_json",
    "event_type",
    "header_value",
    "is_response_create",
    "is_terminal_event",
    "parse_http_response",
    "parse_sse",
    "parse_sse_body",
    "parse_structured_sse_events",
    "parse_websocket_data",
    "parse_websocket_message",
]
