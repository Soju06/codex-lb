"""Capture Codex Responses HTTP, SSE, and WebSocket traffic as JSONL.

Run with mitmproxy or mitmdump and configure it with::

    --set capture_output=/path/to/capture.jsonl
    --set capture_body_mode=metadata
    --set capture_observer_id=controlled-boundary-name
    --set capture_observer_role=intercept
    --set capture_source_hmac_key_file=/secure/source-observer.key
    --set capture_asn_mmdb=/secure/GeoLite2-ASN.mmdb

``capture_body_mode`` is one of ``metadata`` (the safe default), ``full``, or
``none``. Credential-bearing headers are redacted in every mode. This is the
only module in the traffic-analysis package that depends on mitmproxy. ASN
enrichment is optional, offline, and requires ``maxminddb`` in that process.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import ipaddress
import json
import logging
import os
import stat
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mitmproxy import ctx, http  # ty: ignore[unresolved-import]

try:
    from scripts.traffic_analysis.compare import canonical_request_payload
except ModuleNotFoundError:  # Allow mitmdump to load this file directly.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.traffic_analysis.compare import canonical_request_payload

logger = logging.getLogger(__name__)

_TARGET_PATHS = frozenset(
    {
        "/v1/responses",
        "/backend-api/codex/models",
        "/backend-api/codex/responses",
        "/codex/models",
        "/codex/responses",
    }
)
_BODY_MODES = frozenset({"metadata", "full", "none"})
_OBSERVER_ROLES = frozenset({"intercept", "origin"})
_REDACTED = "[REDACTED]"
_METADATA_HASHED_HEADERS = frozenset(
    {
        "chatgpt-account-id",
        "session-id",
        "thread-id",
        "x-client-request-id",
        "x-codex-installation-id",
        "x-codex-turn-metadata",
        "x-codex-window-id",
    }
)

_STRUCTURAL_VALUES = {
    "effort": frozenset({"none", "minimal", "low", "medium", "high", "xhigh"}),
    "reasoning_effort": frozenset({"none", "minimal", "low", "medium", "high", "xhigh"}),
    "role": frozenset({"system", "developer", "user", "assistant", "tool"}),
    "service_tier": frozenset({"auto", "default", "flex", "priority", "fast"}),
    "status": frozenset({"queued", "in_progress", "completed", "failed", "incomplete", "cancelled", "canceled"}),
    "finish_reason": frozenset({"stop", "length", "tool_calls", "content_filter"}),
    "stop_reason": frozenset({"end_turn", "max_tokens", "stop_sequence", "tool_use"}),
}
_STRUCTURAL_TYPES = frozenset(
    {
        "error",
        "message",
        "function",
        "function_call",
        "function_call_output",
        "input_text",
        "output_text",
        "refusal",
        "input_image",
        "input_file",
        "input_audio",
        "image_url",
        "file",
        "additional_tools",
    }
)


def _digest_marker(value: bytes) -> dict[str, str | int]:
    """Return deterministic equality metadata without retaining content."""

    return {"$sha256": hashlib.sha256(value).hexdigest(), "$bytes": len(value)}


def _sanitize_metadata(value: Any, *, key: str | None = None, in_metadata: bool = False) -> Any:
    """Retain Responses structure while replacing content-bearing strings."""

    if isinstance(value, dict):
        nested_metadata = in_metadata or (key is not None and key.lower() == "metadata")
        return {
            item_key: _sanitize_metadata(
                item_value,
                key=str(item_key),
                in_metadata=nested_metadata,
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_metadata(item, key=key, in_metadata=in_metadata) for item in value]
    if isinstance(value, str):
        normalized_key = key.lower() if key is not None else None
        known_value = normalized_key is not None and value in _STRUCTURAL_VALUES.get(normalized_key, ())
        known_type = normalized_key in {"type", "event", "item_type"} and (
            value in _STRUCTURAL_TYPES or value.startswith(("response.", "codex."))
        )
        known_event_namespace = normalized_key in {"type", "event", "item_type"} and value.startswith("responsesapi.")
        if value == "[DONE]" or (not in_metadata and (known_value or known_type or known_event_namespace)):
            return value
        return _digest_marker(value.encode("utf-8"))
    return value


def _header_is_sensitive(name: str) -> bool:
    normalized = name.lower().replace("_", "-")
    return (
        normalized in {"authorization", "cookie", "set-cookie"}
        or "api-key" in normalized
        or normalized.startswith("proxy-auth")
    )


def _redact_headers(headers: Any, *, metadata: bool = False) -> dict[str, str]:
    captured: dict[str, str] = {}
    for name, value in headers.items():
        header_name = str(name)
        normalized = header_name.lower()
        raw_value = str(value)
        if _header_is_sensitive(header_name):
            captured[header_name] = _REDACTED
        elif metadata and (normalized in _METADATA_HASHED_HEADERS or normalized.startswith("x-codex-turn-")):
            digest = _digest_marker(raw_value.encode("utf-8"))
            captured[header_name] = f"[SHA256:{digest['$sha256']}:{digest['$bytes']}]"
        else:
            captured[header_name] = raw_value
    return captured


def _header_name_sequence(headers: Any) -> list[str]:
    """Preserve observed field order/casing without retaining more values."""

    fields = getattr(headers, "fields", None)
    if fields is not None:
        return [
            name.decode("latin-1", errors="replace") if isinstance(name, bytes) else str(name)
            for name, _value in fields
        ]
    return [str(name) for name, _value in headers.items()]


def _redact_url(url: str) -> str:
    """Remove credentials from query strings and URL userinfo."""

    parsed = urlsplit(url)
    query = urlencode(
        [
            (
                name,
                _REDACTED
                if _header_is_sensitive(name)
                or name.lower().replace("-", "_") in {"key", "token", "access_token", "refresh_token"}
                else value,
            )
            for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    if parsed.username is not None:
        netloc = f"{_REDACTED}:{_REDACTED}@{netloc}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))


def _header_value(headers: Any, name: str) -> str:
    for header_name, value in headers.items():
        if str(header_name).lower() == name.lower():
            return str(value)
    return ""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _peer_host(peername: Any) -> str | None:
    if isinstance(peername, (tuple, list)) and peername:
        raw_host = peername[0]
    else:
        raw_host = getattr(peername, "host", None)
    return str(raw_host) if raw_host is not None else None


def _peer_source_observation(peername: Any, hmac_key: bytes) -> dict[str, str] | None:
    """Return equality evidence without retaining the source host or port."""

    host = _peer_host(peername)
    if host is None:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        normalized = host.casefold()
        family = "name"
    else:
        normalized = address.compressed
        family = f"ipv{address.version}"
    digest = hmac.new(hmac_key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()
    return {"family": family, "hmac_sha256": digest}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as database_file:
        for chunk in iter(lambda: database_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class OfflineAsnResolver:
    """Resolve socket peers from a local MaxMind-compatible ASN MMDB."""

    def __init__(self, database_path: str | Path, *, reader_factory: Any | None = None) -> None:
        path = Path(database_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError("capture_asn_mmdb must point to a readable MMDB file")
        if reader_factory is None:
            try:
                maxminddb = importlib.import_module("maxminddb")
            except ImportError as exc:
                raise RuntimeError(
                    "capture_asn_mmdb requires the optional 'maxminddb' package in mitmproxy's Python environment"
                ) from exc
            reader_factory = maxminddb.open_database
        self._reader = reader_factory(str(path))
        metadata_method = getattr(self._reader, "metadata", None)
        metadata = metadata_method() if callable(metadata_method) else None
        self.database = {
            "sha256": _sha256_file(path),
            "build_epoch": getattr(metadata, "build_epoch", None),
            "type": getattr(metadata, "database_type", None),
        }

    def observe(self, host: str | None) -> dict[str, Any]:
        evidence: dict[str, Any] = {"database": dict(self.database)}
        if host is None:
            return {**evidence, "status": "missing_source_address"}
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return {**evidence, "status": "unsupported_source_name"}
        try:
            record = self._reader.get(address.compressed)
        except Exception:  # pragma: no cover - reader-specific failures are deliberately collapsed.
            logger.exception("Offline ASN lookup failed")
            return {**evidence, "status": "lookup_error"}
        if not isinstance(record, dict):
            return {**evidence, "status": "not_found"}
        number = record.get("autonomous_system_number")
        organization = record.get("autonomous_system_organization")
        if not isinstance(number, int):
            return {**evidence, "status": "not_found"}
        return {
            **evidence,
            "status": "observed",
            "number": number,
            "organization_sha256": _sha256_text(organization) if isinstance(organization, str) else None,
        }

    def close(self) -> None:
        close = getattr(self._reader, "close", None)
        if callable(close):
            close()


def _observer_role() -> str:
    role = str(getattr(ctx.options, "capture_observer_role", "intercept")).lower()
    if role not in _OBSERVER_ROLES:
        raise ValueError(f"capture_observer_role must be one of {sorted(_OBSERVER_ROLES)}, got {role!r}")
    return role


def _source_observer(
    flow: Any,
    asn_resolver: OfflineAsnResolver | None = None,
    source_hmac_key: bytes | None = None,
) -> dict[str, Any]:
    observer_id = str(getattr(ctx.options, "capture_observer_id", "")).strip()
    if observer_id and source_hmac_key is None:
        raise ValueError("capture_source_hmac_key_file is required when capture_observer_id is set")
    client_conn = getattr(flow, "client_conn", None)
    peername = getattr(client_conn, "peername", None)
    observation = {
        "observer_id_sha256": _sha256_text(observer_id) if observer_id else None,
        "role": _observer_role() if observer_id else None,
        "source_host": _peer_source_observation(peername, source_hmac_key) if source_hmac_key is not None else None,
    }
    if asn_resolver is not None:
        observation["asn"] = asn_resolver.observe(_peer_host(peername))
    return observation


def _wire_observation(
    flow: Any,
    asn_resolver: OfflineAsnResolver | None = None,
    source_hmac_key: bytes | None = None,
) -> dict[str, Any]:
    """Capture server-observable negotiated transport facts, when exposed.

    mitmproxy terminates the client TLS leg, so these values describe the
    direct-Codex/LB client-to-capture handshake rather than mitmproxy's own
    upstream connection. Missing fields remain explicit nulls in comparison.
    """

    client_conn = getattr(flow, "client_conn", None)
    alpn = getattr(client_conn, "alpn", None)
    if isinstance(alpn, bytes):
        alpn = alpn.decode("ascii", errors="replace")
    cipher = getattr(client_conn, "cipher", None)
    if isinstance(cipher, tuple):
        cipher = cipher[0] if cipher else None
    return {
        "http_version": getattr(getattr(flow, "request", None), "http_version", None),
        "source_observer": _source_observer(flow, asn_resolver, source_hmac_key),
        "tls": {
            "alpn": alpn,
            "version": getattr(client_conn, "tls_version", None),
            "selected_cipher": cipher,
        },
    }


def _u16_values(raw: bytes, *, offset: int = 0) -> list[int]:
    return [int.from_bytes(raw[index : index + 2], "big") for index in range(offset, len(raw) - 1, 2)]


def _client_hello_observation(client_hello: Any) -> dict[str, Any]:
    """Return credential-free, server-observable ClientHello metadata."""

    raw = bytes(client_hello.raw_bytes(wrap_in_record=False))
    record = bytes(client_hello.raw_bytes(wrap_in_record=True))
    extensions = [(int(kind), bytes(body)) for kind, body in client_hello.extensions]
    extension_types = [kind for kind, _ in extensions]
    extension_lengths = [{"type": kind, "bytes": len(body)} for kind, body in extensions]
    extension_map = {kind: body for kind, body in extensions}

    supported_groups_raw = extension_map.get(10, b"")
    supported_groups = _u16_values(supported_groups_raw, offset=2) if len(supported_groups_raw) >= 2 else []
    point_formats_raw = extension_map.get(11, b"")
    point_formats = list(point_formats_raw[1:]) if point_formats_raw else []
    signature_raw = extension_map.get(13, b"")
    signature_algorithms = _u16_values(signature_raw, offset=2) if len(signature_raw) >= 2 else []
    key_share_raw = extension_map.get(51, b"")
    key_share_groups: list[int] = []
    if len(key_share_raw) >= 2:
        cursor = 2
        while cursor + 4 <= len(key_share_raw):
            key_share_groups.append(int.from_bytes(key_share_raw[cursor : cursor + 2], "big"))
            value_length = int.from_bytes(key_share_raw[cursor + 2 : cursor + 4], "big")
            cursor += 4 + value_length

    ciphers = [int(value) for value in client_hello.cipher_suites]
    legacy_version = int.from_bytes(raw[:2], "big") if len(raw) >= 2 else None
    ja3 = ",".join(
        (
            str(legacy_version or ""),
            "-".join(map(str, ciphers)),
            "-".join(map(str, extension_types)),
            "-".join(map(str, supported_groups)),
            "-".join(map(str, point_formats)),
        )
    )
    return {
        "sni": client_hello.sni,
        "offered_alpn": [value.decode("ascii", errors="replace") for value in client_hello.alpn_protocols],
        "legacy_version": legacy_version,
        "ciphers": ciphers,
        "extensions": extension_types,
        "extension_lengths": extension_lengths,
        "supported_groups": supported_groups,
        "point_formats": point_formats,
        "signature_algorithms": signature_algorithms,
        "key_share_groups": key_share_groups,
        "ja3": ja3,
        "ja3_md5": hashlib.md5(ja3.encode("ascii"), usedforsecurity=False).hexdigest(),
        "client_hello_bytes": len(raw),
        "client_hello_sha256": hashlib.sha256(raw).hexdigest(),
        "synthetic_record_bytes": len(record),
        "synthetic_record_sha256": hashlib.sha256(record).hexdigest(),
    }


def _content_bytes(message: Any) -> bytes:
    content = message.content
    if content is None:
        return b""
    if isinstance(content, bytes):
        return content
    return str(content).encode("utf-8")


def _decode_utf8(raw: bytes, parse_errors: list[str], *, label: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        parse_errors.append(f"{label} is not valid UTF-8: {exc}")
        return raw.decode("utf-8", errors="replace")


def _capture_json_body(raw: bytes, mode: str, parse_errors: list[str], *, label: str) -> Any:
    if not raw:
        return None

    text = _decode_utf8(raw, parse_errors, label=label)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        parse_errors.append(f"{label} is not valid JSON: {exc}")
        if mode == "full":
            return text
        if mode == "metadata":
            return _digest_marker(raw)
        return None

    if mode == "full":
        return value
    if mode == "metadata":
        return _sanitize_metadata(value)
    return None


def _capture_request_semantics(raw: bytes, mode: str) -> Any:
    if mode == "none" or not raw:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    projection = canonical_request_payload(value)
    return projection if mode == "full" else _sanitize_metadata(projection)


def _capture_sse_data(
    data: str,
    mode: str,
    parse_errors: list[str],
    *,
    event_index: int,
) -> tuple[Any, str | None]:
    stripped = data.strip()
    if stripped == "[DONE]":
        return ("[DONE]" if mode != "none" else None), None

    try:
        value = json.loads(data)
    except json.JSONDecodeError as exc:
        parse_errors.append(f"SSE event {event_index} data is not valid JSON: {exc}")
        if mode == "full":
            return data, None
        if mode == "metadata":
            return _digest_marker(data.encode("utf-8")), None
        return None, None

    event_type = value.get("type") if isinstance(value, dict) else None
    if not isinstance(event_type, str):
        event_type = None

    if mode == "full":
        return value, event_type
    if mode == "metadata":
        return _sanitize_metadata(value), event_type
    return None, event_type


def _parse_sse(raw: bytes, mode: str) -> tuple[list[dict[str, Any]], bool, list[str]]:
    parse_errors: list[str] = []
    text = _decode_utf8(raw, parse_errors, label="SSE response body")
    events: list[dict[str, Any]] = []
    event_name: str | None = None
    data_lines: list[str] = []
    done_seen = False

    def dispatch() -> None:
        nonlocal event_name, data_lines, done_seen
        if event_name is None and not data_lines:
            return

        data_text = "\n".join(data_lines)
        if data_text.strip() == "[DONE]":
            done_seen = True

        event_index = len(events) + 1
        captured_data, parsed_event_type = (
            _capture_sse_data(data_text, mode, parse_errors, event_index=event_index) if data_lines else (None, None)
        )
        derived_name = event_name or parsed_event_type or "message"
        events.append({"event": derived_name, "data": captured_data})
        event_name = None
        data_lines = []

    for line in text.splitlines():
        if line == "":
            dispatch()
            continue
        if line.startswith(":"):
            continue

        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value or "message"
        elif field == "data":
            data_lines.append(value)

    dispatch()
    return events, done_seen, parse_errors


def _looks_like_sse(raw: bytes) -> bool:
    """Recognize an SSE body when an upstream omits ``Content-Type``.

    Require a real SSE field at the start so a JSON error response requested
    with ``Accept: text/event-stream`` remains classified as HTTP JSON.
    """

    for line in raw.splitlines():
        stripped = line.lstrip()
        if not stripped:
            continue
        return stripped.startswith((b"data:", b"event:", b":"))
    return False


def _requested_http_transport(request_raw: bytes) -> str:
    """Infer the intended Responses HTTP transport before headers arrive."""

    try:
        payload = json.loads(request_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "http_json"
    return "http_sse" if isinstance(payload, dict) and payload.get("stream") is True else "http_json"


def _flow_error_category(error: Any) -> str:
    """Collapse transport errors without retaining address-bearing messages."""

    message = str(getattr(error, "msg", error) or "").casefold()
    if "certificate" in message or "tls" in message or "ssl" in message:
        return "tls"
    if "timeout" in message or "timed out" in message:
        return "timeout"
    if "name resolution" in message or "dns" in message or "name or service not known" in message:
        return "dns"
    if "refused" in message:
        return "connection_refused"
    if any(marker in message for marker in ("reset", "disconnect", "closed", "eof", "broken pipe")):
        return "connection_closed"
    if "proxy" in message:
        return "proxy"
    return "other"


def _request_path(flow: http.HTTPFlow) -> str:
    return (flow.request.path or "").partition("?")[0].rstrip("/") or "/"


def _is_target(flow: http.HTTPFlow) -> bool:
    return flow.request is not None and _request_path(flow) in _TARGET_PATHS


def _duration_ms(flow: http.HTTPFlow) -> float | None:
    started_at = flow.request.timestamp_start
    ended_at = flow.response.timestamp_end if flow.response is not None else None
    if started_at is None or ended_at is None:
        return None
    return round(max(0.0, ended_at - started_at) * 1000, 3)


def _body_mode() -> str:
    mode = str(ctx.options.capture_body_mode).lower()
    if mode not in _BODY_MODES:
        raise ValueError(f"capture_body_mode must be one of {sorted(_BODY_MODES)}, got {mode!r}")
    return mode


class CaptureAddon:
    """mitmproxy event hooks for transport-aware Responses capture."""

    def __init__(self) -> None:
        self._websocket_message_indexes: dict[str, int] = {}
        self._client_hellos: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._captured_http_flows: OrderedDict[str, None] = OrderedDict()
        self._asn_resolver: OfflineAsnResolver | None = None
        self._source_hmac_key: bytes | None = None

    def _mark_http_captured(self, flow_id: str) -> None:
        self._captured_http_flows[flow_id] = None
        self._captured_http_flows.move_to_end(flow_id)
        while len(self._captured_http_flows) > 4096:
            self._captured_http_flows.popitem(last=False)

    def tls_clienthello(self, data: Any) -> None:
        """Retain a bounded fingerprint for later HTTP/WebSocket records."""

        connection_id = str(data.context.client.id)
        self._client_hellos[connection_id] = _client_hello_observation(data.client_hello)
        self._client_hellos.move_to_end(connection_id)
        while len(self._client_hellos) > 1024:
            self._client_hellos.popitem(last=False)

    def _network_observation(self, flow: Any) -> dict[str, Any]:
        observation = _wire_observation(flow, self._asn_resolver, self._source_hmac_key)
        connection_id = getattr(getattr(flow, "client_conn", None), "id", None)
        client_hello = self._client_hellos.get(str(connection_id)) if connection_id is not None else None
        if client_hello is not None:
            observation["tls"]["client_hello"] = client_hello
        return observation

    def load(self, loader: Any) -> None:
        loader.add_option(
            name="capture_output",
            typespec=str,
            default="",
            help="Path to the append-only Codex Responses capture JSONL file",
        )
        loader.add_option(
            name="capture_body_mode",
            typespec=str,
            default="metadata",
            help="Body capture mode: metadata (default), full, or none",
        )
        loader.add_option(
            name="capture_observer_id",
            typespec=str,
            default="",
            help="Optional stable id for the source-address observation boundary",
        )
        loader.add_option(
            name="capture_observer_role",
            typespec=str,
            default="intercept",
            help="Observer role: intercept (default) or origin",
        )
        loader.add_option(
            name="capture_source_hmac_key_file",
            typespec=str,
            default="",
            help="Mode-0600 key file used to HMAC source hosts for one A/C comparison",
        )
        loader.add_option(
            name="capture_asn_mmdb",
            typespec=str,
            default="",
            help="Optional local ASN MMDB path; performs no network lookup",
        )

    def configure(self, updated: set[str]) -> None:
        if "capture_source_hmac_key_file" in updated:
            configured_key_path = str(getattr(ctx.options, "capture_source_hmac_key_file", "")).strip()
            if configured_key_path:
                key_path = Path(configured_key_path).expanduser().resolve(strict=True)
                if not key_path.is_file() or stat.S_IMODE(key_path.stat().st_mode) != 0o600:
                    raise ValueError("capture_source_hmac_key_file must be a mode-0600 file")
                key = key_path.read_bytes()
                if len(key) < 32:
                    raise ValueError("capture_source_hmac_key_file must contain at least 32 bytes")
                self._source_hmac_key = key
            else:
                self._source_hmac_key = None
        if "capture_asn_mmdb" in updated:
            configured_path = str(getattr(ctx.options, "capture_asn_mmdb", "")).strip()
            replacement = OfflineAsnResolver(configured_path) if configured_path else None
            previous = self._asn_resolver
            self._asn_resolver = replacement
            if previous is not None:
                previous.close()

    def done(self) -> None:
        if self._asn_resolver is not None:
            self._asn_resolver.close()
            self._asn_resolver = None

    def response(self, flow: http.HTTPFlow) -> None:
        """Write one record for a completed non-WebSocket HTTP exchange."""

        if not _is_target(flow) or flow.response is None:
            return
        # mitmproxy invokes the HTTP response hook for a WebSocket handshake.
        # Frames are emitted by websocket_message and must not be misclassified.
        if flow.websocket is not None or flow.response.status_code == 101:
            return

        mode = _body_mode()
        request_raw = _content_bytes(flow.request)
        response_raw = _content_bytes(flow.response)
        request_parse_errors: list[str] = []
        request_body = _capture_json_body(
            request_raw,
            mode,
            request_parse_errors,
            label="HTTP request body",
        )

        content_type = _header_value(flow.response.headers, "content-type").lower()
        if content_type.split(";", 1)[0].strip() == "text/event-stream" or _looks_like_sse(response_raw):
            transport = "http_sse"
            events, done_seen, response_parse_errors = _parse_sse(response_raw, mode)
            response_payload: dict[str, Any] = {
                "status": flow.response.status_code,
                "headers": _redact_headers(flow.response.headers, metadata=mode == "metadata"),
                "events": events,
                "body_bytes": len(response_raw),
                "done_seen": done_seen,
                "parse_errors": response_parse_errors,
            }
        else:
            transport = "http_json"
            response_parse_errors: list[str] = []
            response_body = _capture_json_body(
                response_raw,
                mode,
                response_parse_errors,
                label="HTTP response body",
            )
            response_payload = {
                "status": flow.response.status_code,
                "headers": _redact_headers(flow.response.headers, metadata=mode == "metadata"),
                "body": response_body,
                "body_bytes": len(response_raw),
                "done_seen": False,
                "parse_errors": response_parse_errors,
            }

        request_timestamp = flow.request.timestamp_start
        record = {
            "kind": "http",
            "transport": transport,
            "capture_body_mode": mode,
            "flow_id": str(flow.id),
            "timestamp": request_timestamp if request_timestamp is not None else time.time(),
            "duration_ms": _duration_ms(flow),
            "network": self._network_observation(flow),
            "request": {
                "method": flow.request.method,
                "url": _redact_url(flow.request.pretty_url),
                "headers": _redact_headers(flow.request.headers, metadata=mode == "metadata"),
                "header_names": _header_name_sequence(flow.request.headers),
                "body": request_body,
                "semantic_body": _capture_request_semantics(request_raw, mode),
                "body_bytes": len(request_raw),
            },
            "response": response_payload,
        }
        if request_parse_errors:
            response_payload["parse_errors"] = [
                *(f"request: {error}" for error in request_parse_errors),
                *response_payload["parse_errors"],
            ]
        self._write(record)
        self._mark_http_captured(str(flow.id))

    def error(self, flow: http.HTTPFlow) -> None:
        """Write a privacy-safe record when targeted HTTP transport fails."""

        flow_id = str(flow.id)
        if not _is_target(flow) or flow_id in self._captured_http_flows or getattr(flow, "websocket", None) is not None:
            return

        mode = _body_mode()
        request_raw = _content_bytes(flow.request)
        request_parse_errors: list[str] = []
        request_body = _capture_json_body(
            request_raw,
            mode,
            request_parse_errors,
            label="HTTP request body",
        )
        response = getattr(flow, "response", None)
        response_raw = _content_bytes(response) if response is not None else b""
        response_headers = getattr(response, "headers", {}) if response is not None else {}
        content_type = _header_value(response_headers, "content-type").lower()
        transport = (
            "http_sse"
            if content_type.split(";", 1)[0].strip() == "text/event-stream" or _looks_like_sse(response_raw)
            else _requested_http_transport(request_raw)
        )
        response_parse_errors: list[str] = []
        response_payload: dict[str, Any] = {
            "status": getattr(response, "status_code", None),
            "headers": _redact_headers(response_headers, metadata=mode == "metadata"),
            "body_bytes": len(response_raw),
            "done_seen": False,
            "parse_errors": response_parse_errors,
            "network_error": {"category": _flow_error_category(getattr(flow, "error", None))},
        }
        if transport == "http_sse":
            events, done_seen, response_parse_errors = _parse_sse(response_raw, mode)
            response_payload.update(events=events, done_seen=done_seen, parse_errors=response_parse_errors)
        else:
            response_payload["body"] = _capture_json_body(
                response_raw,
                mode,
                response_parse_errors,
                label="HTTP response body",
            )

        if request_parse_errors:
            response_payload["parse_errors"] = [
                *(f"request: {error}" for error in request_parse_errors),
                *response_payload["parse_errors"],
            ]
        request_timestamp = flow.request.timestamp_start
        self._write(
            {
                "kind": "http",
                "transport": transport,
                "capture_body_mode": mode,
                "flow_id": flow_id,
                "timestamp": request_timestamp if request_timestamp is not None else time.time(),
                "duration_ms": _duration_ms(flow),
                "network": self._network_observation(flow),
                "request": {
                    "method": flow.request.method,
                    "url": _redact_url(flow.request.pretty_url),
                    "headers": _redact_headers(flow.request.headers, metadata=mode == "metadata"),
                    "header_names": _header_name_sequence(flow.request.headers),
                    "body": request_body,
                    "semantic_body": _capture_request_semantics(request_raw, mode),
                    "body_bytes": len(request_raw),
                },
                "response": response_payload,
            }
        )
        self._mark_http_captured(flow_id)

    def websocket_message(self, flow: http.HTTPFlow) -> None:
        """Write the newest WebSocket frame while retaining flow and direction."""

        if not _is_target(flow) or flow.websocket is None or not flow.websocket.messages:
            return

        message = flow.websocket.messages[-1]
        flow_id = str(flow.id)
        message_index = self._websocket_message_indexes.get(flow_id, 0) + 1
        self._websocket_message_indexes[flow_id] = message_index

        raw_content = message.content
        if isinstance(raw_content, str):
            raw = raw_content.encode("utf-8")
        else:
            raw = bytes(raw_content)
        parse_errors: list[str] = []
        data = _capture_json_body(raw, _body_mode(), parse_errors, label="WebSocket message")
        semantic_data = _capture_request_semantics(raw, _body_mode()) if message.from_client else None

        record = {
            "kind": "websocket_message",
            "transport": "websocket",
            "capture_body_mode": _body_mode(),
            "flow_id": flow_id,
            "direction": "client_to_server" if message.from_client else "server_to_client",
            "message_index": message_index,
            "timestamp": message.timestamp,
            "network": self._network_observation(flow),
            "request": {
                "method": flow.request.method,
                "url": _redact_url(flow.request.pretty_url),
                "headers": _redact_headers(flow.request.headers, metadata=_body_mode() == "metadata"),
                "header_names": _header_name_sequence(flow.request.headers),
            },
            "data": data,
            "semantic_data": semantic_data,
            "data_bytes": len(raw),
            "parse_errors": parse_errors,
        }
        self._write(record)

    def websocket_end(self, flow: http.HTTPFlow) -> None:
        self._websocket_message_indexes.pop(str(flow.id), None)
        connection_id = getattr(getattr(flow, "client_conn", None), "id", None)
        if connection_id is not None:
            self._client_hellos.pop(str(connection_id), None)

    @staticmethod
    def _write(record: dict[str, Any]) -> None:
        configured_output = str(ctx.options.capture_output).strip()
        if not configured_output:
            raise RuntimeError("capture_output must be configured explicitly")
        output_path = Path(configured_output).expanduser()
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(output_path, flags, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
            except OSError:
                os.close(descriptor)
                raise
            with os.fdopen(descriptor, "a", encoding="utf-8") as output_file:
                output_file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            logger.exception("Failed to append traffic capture to %s", output_path)


addons = [CaptureAddon()]
