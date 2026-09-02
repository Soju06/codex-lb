"""Load traffic JSONL and assemble transport-neutral Codex turns."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO, cast
from urllib.parse import urlsplit

try:
    from scripts.traffic_analysis.protocol import (
        WEBSOCKET,
        ProtocolEvent,
        Transport,
        classify_http_record,
        is_response_create,
        is_terminal_event,
        parse_http_response,
        parse_websocket_data,
    )
except ModuleNotFoundError:  # pragma: no cover - permits direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.traffic_analysis.protocol import (  # type: ignore[no-redef]
        WEBSOCKET,
        ProtocolEvent,
        Transport,
        classify_http_record,
        is_response_create,
        is_terminal_event,
        parse_http_response,
        parse_websocket_data,
    )


class CaptureFormatError(ValueError):
    """A strict JSONL load encountered an invalid capture record."""


@dataclass(slots=True)
class Turn:
    """One request and its logical response events."""

    index: int
    transport: Transport
    request: dict[str, Any]
    response: dict[str, Any] | None
    events: list[ProtocolEvent]
    flow_id: str | None = None
    request_event: ProtocolEvent | None = None
    terminal_event: str | None = None
    complete: bool = True
    incomplete_reason: str | None = None
    source_records: list[dict[str, Any]] = field(default_factory=list, repr=False)

    @property
    def turn_index(self) -> int:
        return self.index

    @property
    def request_body(self) -> Any:
        # HTTP ``request`` is a wire envelope while the websocket request is
        # itself the decoded response.create payload.
        return self.request if self.transport == WEBSOCKET else self.request.get("body")

    @property
    def response_body(self) -> Any:
        return self.response.get("body") if self.response is not None else None

    @property
    def event_types(self) -> list[str]:
        return [event.type for event in self.events]

    @property
    def terminal(self) -> ProtocolEvent | None:
        return next((event for event in reversed(self.events) if is_terminal_event(event)), None)


@dataclass(frozen=True, slots=True)
class OrphanWebSocketMessage:
    """A websocket frame which could not be attached to a response.create."""

    flow_id: str | None
    direction: str | None
    message_index: int | None
    event: ProtocolEvent
    reason: str
    record: dict[str, Any] = field(repr=False)


@dataclass(slots=True)
class TurnExtraction:
    turns: list[Turn]
    orphan_websocket_messages: list[OrphanWebSocketMessage] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)

    @property
    def incomplete_websocket_turns(self) -> list[Turn]:
        return [turn for turn in self.turns if turn.transport == WEBSOCKET and not turn.complete]

    @property
    def incomplete_http_turns(self) -> list[Turn]:
        return [turn for turn in self.turns if turn.transport != WEBSOCKET and not turn.complete]


def _jsonl_lines(source: str | Path | TextIO | Iterable[str]) -> Iterator[str]:
    if isinstance(source, (str, Path)):
        with Path(source).open("r", encoding="utf-8") as stream:
            yield from stream
        return
    if hasattr(source, "read"):
        yield from cast(TextIO, source)
        return
    yield from cast(Iterable[str], source)


def iter_capture_records(
    source: str | Path | TextIO | Iterable[str], *, strict: bool = False
) -> Iterator[dict[str, Any]]:
    """Yield object records from a capture JSONL source.

    Blank/malformed/non-object lines are skipped by default so a partially
    written live capture remains inspectable.  ``strict=True`` raises a
    line-numbered :class:`CaptureFormatError` instead.
    """

    for line_number, line in enumerate(_jsonl_lines(source), 1):
        if not isinstance(line, str):
            if strict:
                raise CaptureFormatError(f"line {line_number}: expected text")
            continue
        raw = line.strip()
        if not raw:
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            if strict:
                raise CaptureFormatError(f"line {line_number}: {exc.msg}") from exc
            continue
        if not isinstance(value, dict):
            if strict:
                raise CaptureFormatError(f"line {line_number}: expected a JSON object")
            continue
        yield value


def load_capture_records(source: str | Path | TextIO | Iterable[str], *, strict: bool = False) -> list[dict[str, Any]]:
    return list(iter_capture_records(source, strict=strict))


read_capture = load_capture_records
load_capture = load_capture_records


def _copy_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _message_index(record: Mapping[str, Any]) -> int | None:
    value = record.get("message_index")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _timestamp_key(record: Mapping[str, Any], ordinal: int) -> tuple[int, float | int]:
    index = _message_index(record)
    if index is not None:
        return (0, index)
    timestamp = record.get("timestamp")
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        return (1, timestamp)
    return (2, ordinal)


def _identifier_token(value: Any) -> str | None:
    if isinstance(value, str):
        return f"raw:{value}"
    if isinstance(value, Mapping) and isinstance(value.get("$sha256"), str) and isinstance(value.get("$bytes"), int):
        return f"sha256:{value['$sha256']}:{value['$bytes']}"
    return None


def _event_response_id(event: ProtocolEvent) -> str | None:
    data = event.data
    if not isinstance(data, Mapping):
        return None
    response = data.get("response")
    if isinstance(response, Mapping):
        nested = _identifier_token(response.get("id"))
        if nested is not None:
            return nested
    return _identifier_token(data.get("response_id"))


def _event_item_ids(event: ProtocolEvent) -> list[str]:
    data = event.data
    if not isinstance(data, Mapping):
        return []
    values: list[Any] = [data.get("item_id")]
    item = data.get("item")
    if isinstance(item, Mapping):
        values.append(item.get("id"))
    return [token for value in values if (token := _identifier_token(value)) is not None]


def extract_turns_with_diagnostics(records: Iterable[Mapping[str, Any]]) -> TurnExtraction:
    """Group HTTP records and websocket messages into common logical turns.

    Each HTTP record is exactly one turn.  Within each websocket flow, a client
    ``response.create`` begins a turn and server messages are appended through
    the first terminal event.  Interleaved flow IDs are handled independently.
    """

    materialized: list[tuple[int, dict[str, Any]]] = []
    errors: list[str] = []
    for ordinal, value in enumerate(records):
        if not isinstance(value, Mapping):
            errors.append(f"record {ordinal + 1}: expected a mapping")
            continue
        materialized.append((ordinal, dict(value)))

    pending_turns: list[tuple[int, Turn]] = []
    websocket_by_flow: dict[str | None, list[tuple[int, dict[str, Any]]]] = defaultdict(list)

    for ordinal, record in materialized:
        kind = record.get("kind")
        if kind == "http":
            request = _copy_mapping(record.get("request"))
            request_url = request.get("url")
            if isinstance(request_url, str) and urlsplit(request_url).path.rstrip("/").endswith("/models"):
                # Model discovery is an ancillary client-to-LB exchange.  The
                # LB may satisfy it locally, so it is not a Responses turn.
                continue
            request_errors = request.get("parse_errors")
            if isinstance(request_errors, list):
                errors.extend(
                    f"record {ordinal + 1}: {message}" for message in request_errors if isinstance(message, str)
                )
            response_value = record.get("response")
            response = _copy_mapping(response_value) if isinstance(response_value, Mapping) else None
            events = parse_http_response(record)
            if isinstance(response_value, Mapping):
                capture_errors = response_value.get("parse_errors")
                if isinstance(capture_errors, list):
                    errors.extend(
                        f"record {ordinal + 1}: {message}" for message in capture_errors if isinstance(message, str)
                    )
            terminal = next((event.type for event in reversed(events) if is_terminal_event(event)), None)
            transport = classify_http_record(record)
            network_error = response.get("network_error") if response is not None else None
            complete = network_error is None and (transport != "http_sse" or terminal is not None)
            incomplete_reason = None
            if not complete:
                incomplete_reason = "network_error" if isinstance(network_error, Mapping) else "missing_terminal_event"
            pending_turns.append(
                (
                    ordinal,
                    Turn(
                        index=0,
                        transport=transport,
                        request=request,
                        response=response,
                        events=events,
                        flow_id=record.get("flow_id") if isinstance(record.get("flow_id"), str) else None,
                        terminal_event=terminal,
                        complete=complete,
                        incomplete_reason=incomplete_reason,
                        source_records=[record],
                    ),
                )
            )
        elif kind == "websocket_message":
            capture_errors = record.get("parse_errors")
            if isinstance(capture_errors, list):
                errors.extend(
                    f"record {ordinal + 1}: {message}" for message in capture_errors if isinstance(message, str)
                )
            flow_id = record.get("flow_id")
            websocket_by_flow[flow_id if isinstance(flow_id, str) else None].append((ordinal, record))
        else:
            errors.append(f"record {ordinal + 1}: unknown kind {kind!r}")

    orphans: list[OrphanWebSocketMessage] = []
    for flow_id, flow_records in websocket_by_flow.items():
        flow_records.sort(key=lambda item: _timestamp_key(item[1], item[0]))
        open_turns: list[Turn] = []
        turn_ordinals: dict[int, int] = {}
        response_ids: dict[int, str] = {}
        item_owners: dict[str, Turn] = {}
        last_routed_turn: Turn | None = None

        def finish_turn(turn: Turn, reason: str | None = None) -> None:
            if reason is not None:
                turn.complete = False
                turn.incomplete_reason = reason
            pending_turns.append((turn_ordinals.pop(id(turn)), turn))
            response_ids.pop(id(turn), None)
            for item_id, owner in list(item_owners.items()):
                if owner is turn:
                    item_owners.pop(item_id, None)
            open_turns.remove(turn)

        def target_for_server_event(event: ProtocolEvent) -> Turn | None:
            response_id = _event_response_id(event)
            if response_id is not None:
                for turn in open_turns:
                    if response_ids.get(id(turn)) == response_id:
                        return turn

            for item_id in _event_item_ids(event):
                owner = item_owners.get(item_id)
                if owner in open_turns:
                    return owner

            unassigned = [turn for turn in open_turns if id(turn) not in response_ids]
            if event.type == "response.created" and unassigned:
                target = unassigned[0]
                if response_id is not None:
                    response_ids[id(target)] = response_id
                return target
            if response_id is not None and len(unassigned) == 1:
                target = unassigned[0]
                response_ids[id(target)] = response_id
                return target
            assigned = [turn for turn in open_turns if id(turn) in response_ids]
            if len(assigned) == 1:
                return assigned[0]
            if last_routed_turn in open_turns:
                return last_routed_turn
            if len(open_turns) == 1:
                return open_turns[0]
            return None

        for ordinal, record in flow_records:
            direction = record.get("direction")
            event = parse_websocket_data(record.get("data"))

            if direction == "client_to_server":
                if is_response_create(event):
                    request_payload = event.data if isinstance(event.data, Mapping) else {}
                    turn = Turn(
                        index=0,
                        transport=WEBSOCKET,
                        request=dict(request_payload),
                        response=None,
                        events=[],
                        flow_id=flow_id,
                        request_event=event,
                        complete=False,
                        incomplete_reason="end_of_capture",
                        source_records=[record],
                    )
                    open_turns.append(turn)
                    turn_ordinals[id(turn)] = ordinal
                # Client control frames belong to no response turn and are not
                # response events.  Preserve them as diagnostics.
                else:
                    orphans.append(
                        OrphanWebSocketMessage(
                            flow_id=flow_id,
                            direction=direction,
                            message_index=_message_index(record),
                            event=event,
                            reason="client_message_without_response.create",
                            record=record,
                        )
                    )
                continue

            if direction != "server_to_client":
                orphans.append(
                    OrphanWebSocketMessage(
                        flow_id=flow_id,
                        direction=direction if isinstance(direction, str) else None,
                        message_index=_message_index(record),
                        event=event,
                        reason="invalid_direction",
                        record=record,
                    )
                )
                continue

            target = target_for_server_event(event)
            if target is None:
                orphans.append(
                    OrphanWebSocketMessage(
                        flow_id=flow_id,
                        direction=direction,
                        message_index=_message_index(record),
                        event=event,
                        reason="server_message_without_active_turn",
                        record=record,
                    )
                )
                continue

            last_routed_turn = target
            for item_id in _event_item_ids(event):
                item_owners[item_id] = target
            target.events.append(event)
            target.source_records.append(record)
            if is_terminal_event(event):
                target.terminal_event = event.type
                target.complete = True
                target.incomplete_reason = None
                finish_turn(target)
                if last_routed_turn is target:
                    last_routed_turn = None

        for turn in list(open_turns):
            finish_turn(turn, "end_of_capture")

    pending_turns.sort(key=lambda item: item[0])
    turns = [turn for _, turn in pending_turns]
    for index, turn in enumerate(turns, 1):
        turn.index = index
    return TurnExtraction(turns=turns, orphan_websocket_messages=orphans, parse_errors=errors)


def extract_turns(records: Iterable[Mapping[str, Any]]) -> list[Turn]:
    """Return logical turns, discarding the optional diagnostic side channel."""

    return extract_turns_with_diagnostics(records).turns


def load_turns(source: str | Path | TextIO | Iterable[str], *, strict: bool = False) -> list[Turn]:
    return extract_turns(iter_capture_records(source, strict=strict))


parse_turns = extract_turns


__all__ = [
    "CaptureFormatError",
    "OrphanWebSocketMessage",
    "Turn",
    "TurnExtraction",
    "extract_turns",
    "extract_turns_with_diagnostics",
    "iter_capture_records",
    "load_capture_records",
    "load_capture",
    "load_turns",
    "parse_turns",
    "read_capture",
]
