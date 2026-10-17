"""Compare Codex traffic observed on the two sides of codex-lb.

Path B is the client-facing leg and path C is the upstream-facing leg from the
same run. Path A, when supplied, is a separately generated direct-upstream
baseline. B/C differences form the strict parity gate; A/B failure outcomes
are reported as the end-to-end behavioral baseline.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

try:
    from scripts.traffic_analysis.tls_randomization import (
        DEFAULT_MIN_SAMPLES,
        TLS_TRANSPORTS,
        analyze_tls_randomization_paths,
        stable_tls_profile,
    )
    from scripts.traffic_analysis.turns import (
        Turn,
        TurnExtraction,
        extract_turns_with_diagnostics,
        load_capture,
    )
except ModuleNotFoundError:  # Allow ``python scripts/traffic_analysis/compare.py``.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.traffic_analysis.tls_randomization import (
        DEFAULT_MIN_SAMPLES,
        TLS_TRANSPORTS,
        analyze_tls_randomization_paths,
        stable_tls_profile,
    )
    from scripts.traffic_analysis.turns import (
        Turn,
        TurnExtraction,
        extract_turns_with_diagnostics,
        load_capture,
    )


TRANSPORTS = frozenset({"http_json", "http_sse", "websocket"})
STREAMING_TRANSPORTS = frozenset({"http_sse", "websocket"})
TERMINAL_TYPES = {
    "error": "failed",
    "response.completed": "completed",
    "response.failed": "failed",
    "response.incomplete": "incomplete",
    "response.cancelled": "cancelled",
    "response.canceled": "cancelled",
}
SEMANTIC_REQUEST_FIELDS = (
    "model",
    "service_tier",
    "reasoning",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "previous_response_id",
    "conversation",
    "prompt_cache_key",
    "include",
    "text",
)
CONDITIONAL_REQUEST_FIELDS = (
    "max_output_tokens",
    "metadata",
    "prompt_cache_retention",
    "safety_identifier",
    "temperature",
    "top_p",
    "truncation",
    "user",
    "background",
    "store",
)
SEMANTIC_RESPONSE_FIELDS = (
    "status",
    "model",
    "service_tier",
    "output",
    "error",
    "incomplete_details",
    "usage",
    "parallel_tool_calls",
)
_CREDENTIAL_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "x-api-key",
    }
)
_TIMESTAMP_KEYS = frozenset(
    {
        "created",
        "created_at",
        "completed_at",
        "expires_at",
        "timestamp",
        "time",
    }
)
_SEQUENCE_KEYS = frozenset({"sequence", "sequence_id", "sequence_number", "seq"})
_IGNORED_EVENT_TYPES = frozenset(
    {
        "done",
        "[done]",
        "response.created",
        "responsesapi.websocket_timing",
    }
)
_MAX_DIFFS = 20
_IDENTITY_HEADER_NAMES = (
    "accept",
    "accept-encoding",
    "originator",
    "user-agent",
    "version",
    "x-codex-installation-id",
    "x-codex-routing-hint",
    "x-codex-turn-state",
)
_WEBSOCKET_HEADER_NAMES = (
    "sec-websocket-extensions",
    "sec-websocket-protocol",
    "sec-websocket-version",
)


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    return value


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


class _Normalizer:
    """Normalize volatile values while retaining the ID equality graph.

    IDs are replaced in first-seen order.  Repeated references therefore keep
    the same placeholder, so a broken response/item/call correlation remains a
    detectable difference.
    """

    def __init__(self) -> None:
        self._ids: dict[str, str] = {}

    def value(self, value: Any, *, key: str | None = None) -> Any:
        value = _plain(value)
        normalized_key = key.lower().replace("-", "_") if isinstance(key, str) else None

        if normalized_key in _TIMESTAMP_KEYS or (normalized_key is not None and normalized_key.endswith("_timestamp")):
            return "<timestamp>"
        if normalized_key in _SEQUENCE_KEYS:
            return "<sequence>"
        if self._is_id_key(normalized_key) and value is not None:
            token = self._id_token(value)
            if token is not None:
                return token

        if isinstance(value, Mapping):
            return {
                str(child_key): self.value(child_value, key=str(child_key))
                for child_key, child_value in sorted(value.items(), key=lambda item: str(item[0]))
            }
        if isinstance(value, (list, tuple)):
            return [self.value(item) for item in value]
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return repr(value)

    @staticmethod
    def _is_id_key(key: str | None) -> bool:
        return bool(key and (key == "id" or key.endswith("_id") or key in {"call_id", "item_id"}))

    def _id_token(self, value: Any) -> str | None:
        if not isinstance(value, (str, int)):
            return None
        raw = str(value)
        token = self._ids.get(raw)
        if token is None:
            token = f"<id:{len(self._ids) + 1}>"
            self._ids[raw] = token
        return token


def normalize_payload(value: Any) -> Any:
    """Return a JSON-safe payload with volatile values placeholder-normalized."""

    return _Normalizer().value(value)


def _body(turn: Turn, side: str) -> Any:
    property_name = f"{side}_body"
    body = _get(turn, property_name)
    if body is not None:
        return body
    envelope = _get(turn, side)
    if isinstance(envelope, Mapping):
        for key in ("body", f"{side}_body", "json", "data", "payload"):
            if key in envelope:
                return envelope[key]
    return envelope


def _unwrap_request(body: Any) -> dict[str, Any]:
    """Remove known transport wrappers around a Responses API request."""

    if not isinstance(body, Mapping):
        return {}
    current: Mapping[str, Any] = body
    if current.get("type") == "response.create" and isinstance(current.get("response"), Mapping):
        current = current["response"]
    elif isinstance(current.get("request"), Mapping) and current.get("type") in {
        "response.create",
        "codex.response.create",
    }:
        current = current["request"]
    return dict(current)


def canonical_request_payload(body: Any) -> dict[str, Any]:
    """Return the adapter-aware, still-unsanitized request projection."""

    request = _unwrap_request(body)
    if "prompt_cache_key" not in request and "promptCacheKey" in request:
        request["prompt_cache_key"] = request["promptCacheKey"]
    selected = {field: request[field] for field in SEMANTIC_REQUEST_FIELDS if field in request}
    selected["include"] = request.get("include", [])
    if selected.get("service_tier") == "fast":
        selected["service_tier"] = "priority"
    selected["content"] = _request_content_projection(request)
    return selected


def _captured_request_semantics(turn: Turn) -> Any:
    request = _get(turn, "request")
    if isinstance(request, Mapping) and "semantic_body" in request:
        return request["semantic_body"]
    source_records = _get(turn, "source_records", []) or []
    if source_records and isinstance(source_records[0], Mapping):
        return source_records[0].get("semantic_data")
    return None


def _semantic_request(turn: Turn) -> dict[str, Any]:
    captured = _captured_request_semantics(turn)
    selected = captured if isinstance(captured, Mapping) else canonical_request_payload(_body(turn, "request"))
    normalized = _Normalizer().value(selected)
    for continuity_field in ("previous_response_id", "conversation"):
        if continuity_field in selected:
            # Same-run continuity anchors must be byte-identical. Treating
            # them like generated response IDs hides owner/anchor rewrites.
            normalized[continuity_field] = selected[continuity_field]
    return normalized


def _conditional_request_values(turn: Turn) -> dict[str, Any]:
    request = _unwrap_request(_body(turn, "request"))
    return _Normalizer().value({field: request[field] for field in CONDITIONAL_REQUEST_FIELDS if field in request})


def _is_digest_marker(value: Any) -> bool:
    return isinstance(value, Mapping) and set(value) == {"$sha256", "$bytes"}


def _preserve_request_identifiers(value: Any) -> Any:
    """Keep same-run request-history IDs exact through payload normalization."""

    if _is_digest_marker(value):
        return dict(value)
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for key, child in value.items():
            name = str(key)
            if name.lower().replace("-", "_") in {"id", "item_id", "call_id"}:
                # The outer marker prevents _Normalizer from replacing the
                # identifier with a first-seen placeholder. Metadata captures
                # still contain only the digest marker produced by the addon.
                projected[name] = {"$exact_identifier": _preserve_request_identifiers(child)}
            else:
                projected[name] = _preserve_request_identifiers(child)
        return projected
    if isinstance(value, (list, tuple)):
        return [_preserve_request_identifiers(child) for child in value]
    return value


def _canonical_user_text_item(value: Any) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "input_text", "text": value}]}


def _canonical_text_content(value: Any, *, role: str) -> Any:
    text_type = "output_text" if role == "assistant" else "input_text"
    if _is_digest_marker(value) or isinstance(value, (str, int, float, bool)) or value is None:
        return [{"type": text_type, "text": value}]
    items = value if isinstance(value, list) else [value]
    normalized: list[Any] = []
    for item in items:
        if _is_digest_marker(item) or isinstance(item, (str, int, float, bool)) or item is None:
            normalized.append({"type": text_type, "text": item})
        elif isinstance(item, Mapping) and item.get("type") in {None, "text", "input_text", "output_text"}:
            updated = dict(item)
            updated["type"] = text_type
            normalized.append(_preserve_request_identifiers(updated))
        elif role == "assistant" and isinstance(item, Mapping) and item.get("type") == "refusal":
            normalized.append({"type": "output_text", "text": item.get("refusal")})
        elif role != "assistant" and isinstance(item, Mapping) and item.get("type") == "image_url":
            image_value = item.get("image_url")
            if isinstance(image_value, Mapping):
                image_url = image_value.get("url")
                detail = image_value.get("detail")
            else:
                image_url = image_value
                detail = None
            normalized.append(
                _preserve_request_identifiers(
                    {
                        "type": "input_image",
                        "image_url": image_url,
                        **({"detail": detail} if detail is not None else {}),
                    }
                )
            )
        elif role != "assistant" and isinstance(item, Mapping) and item.get("type") == "file":
            file_value = item.get("file")
            file_info = file_value if isinstance(file_value, Mapping) else {}
            converted: dict[str, Any] = {"type": "input_file"}
            if file_info.get("file_id"):
                converted["file_id"] = file_info["file_id"]
            elif file_info.get("file_url"):
                converted["file_url"] = file_info["file_url"]
            elif isinstance(file_info.get("file_data", file_info.get("data")), str):
                mime = file_info.get("mime_type", file_info.get("content_type", "application/octet-stream"))
                converted["file_url"] = f"data:{mime};base64,{file_info.get('file_data', file_info.get('data'))}"
            normalized.append(_preserve_request_identifiers(converted))
        elif role != "assistant" and isinstance(item, Mapping) and item.get("type") == "input_audio":
            audio_value = item.get("input_audio")
            audio = audio_value if isinstance(audio_value, Mapping) else {}
            audio_format = audio.get("format")
            mime = "audio/mpeg" if audio_format == "mp3" else f"audio/{audio_format}"
            normalized.append(
                _preserve_request_identifiers(
                    {"type": "input_file", "file_url": f"data:{mime};base64,{audio.get('data')}"}
                )
            )
        else:
            normalized.append(_preserve_request_identifiers(item))
    return normalized


def _instruction_values(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if _is_digest_marker(value):
        return [dict(value)]
    if isinstance(value, str):
        return value.split("\n")
    items = value if isinstance(value, list) else [value]
    result: list[Any] = []
    for item in items:
        if isinstance(item, str):
            result.extend(item.split("\n"))
        elif _is_digest_marker(item):
            result.append(dict(item))
        elif isinstance(item, Mapping) and "text" in item:
            result.extend(_instruction_values(item["text"]))
        else:
            result.append(_preserve_request_identifiers(item))
    return result


def _canonical_tool_output(message: Mapping[str, Any]) -> dict[str, Any]:
    call_id = message.get("tool_call_id", message.get("toolCallId", message.get("call_id")))
    content = message.get("output", message.get("content", ""))
    if content is None:
        content = ""
    elif isinstance(content, list):
        text_parts = [_tool_output_text(item) for item in content]
        present = [item for item in text_parts if item is not None]
        content = "".join(present) if present else json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    elif isinstance(content, Mapping):
        extracted = _tool_output_text(content)
        content = extracted if extracted is not None else json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    elif not isinstance(content, str):
        content = str(content)
    return _preserve_request_identifiers({"type": "function_call_output", "call_id": call_id, "output": content})


def _tool_output_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, Mapping):
        return None
    item_type = value.get("type")
    text = value.get("text")
    if item_type in {None, "text", "input_text", "output_text"} and isinstance(text, str):
        return text
    refusal = value.get("refusal")
    if item_type == "refusal" and isinstance(refusal, str):
        return refusal
    return None


def _canonical_message_items(value: Mapping[str, Any]) -> tuple[list[Any], list[Any]]:
    role = value.get("role")
    if role in {"system", "developer"} and value.get("type") in {None, "message"}:
        return _instruction_values(value.get("content")), []
    if role == "tool":
        return [], [_canonical_tool_output(value)]
    if role == "assistant" and isinstance(value.get("tool_calls"), list):
        items: list[Any] = []
        assistant_content = value.get("content")
        refusal = value.get("refusal")
        if isinstance(refusal, str) and refusal:
            content_parts = assistant_content if isinstance(assistant_content, list) else []
            if assistant_content is not None and not isinstance(assistant_content, list):
                content_parts = [assistant_content]
            assistant_content = [*content_parts, {"type": "refusal", "refusal": refusal}]
        if assistant_content is not None:
            items.append({"role": "assistant", "content": _canonical_text_content(assistant_content, role="assistant")})
        for tool_call in value["tool_calls"]:
            if not isinstance(tool_call, Mapping):
                items.append(_preserve_request_identifiers(tool_call))
                continue
            function_value = tool_call.get("function")
            function = function_value if isinstance(function_value, Mapping) else {}
            items.append(
                _preserve_request_identifiers(
                    {
                        "type": "function_call",
                        "call_id": tool_call.get("id", tool_call.get("call_id")),
                        "name": function.get("name", tool_call.get("name")),
                        "arguments": function.get("arguments", tool_call.get("arguments")),
                    }
                )
            )
        return [], items
    if role in {"user", "assistant"}:
        content = value.get("content")
        refusal = value.get("refusal")
        if role == "assistant" and isinstance(refusal, str) and refusal:
            content_parts = content if isinstance(content, list) else []
            if content is not None and not isinstance(content, list):
                content_parts = [content]
            content = [*content_parts, {"type": "refusal", "refusal": refusal}]
        return [], [{"role": role, "content": _canonical_text_content(content, role=str(role))}]
    return [], [_preserve_request_identifiers(value)]


def _canonical_input_item(value: Any) -> Any:
    """Canonicalize established Responses adapter input rewrites."""

    if _is_digest_marker(value) or isinstance(value, (str, int, float, bool)) or value is None:
        return _canonical_user_text_item(value)
    if not isinstance(value, Mapping):
        return _preserve_request_identifiers(value)

    if value.get("role") in {"user", "assistant", "tool"}:
        _, items = _canonical_message_items(value)
        if len(items) == 1:
            return items[0]

    return _preserve_request_identifiers(value)


def _request_content_projection(request: Mapping[str, Any]) -> dict[str, Any]:
    """Model known public/native adapter rewrites without erasing semantics."""

    instruction_parts = _instruction_values(request.get("instructions"))
    input_items: list[Any] = []
    if "messages" in request:
        messages = request["messages"] if isinstance(request["messages"], list) else [request["messages"]]
        for message in messages:
            if isinstance(message, Mapping):
                extra_instructions, extra_items = _canonical_message_items(message)
                instruction_parts.extend(extra_instructions)
                input_items.extend(extra_items)
            else:
                input_items.append(_preserve_request_identifiers(message))
    elif "input" in request:
        raw_items = request["input"] if isinstance(request["input"], list) else [request["input"]]
        uses_lite_tools = any(
            isinstance(item, Mapping) and item.get("type") == "additional_tools" for item in raw_items
        )
        for item in raw_items:
            if isinstance(item, Mapping) and not uses_lite_tools:
                extra_instructions, extra_items = _canonical_message_items(item)
                if extra_instructions:
                    instruction_parts.extend(extra_instructions)
                    input_items.extend(extra_items)
                    continue
            input_items.append(_canonical_input_item(item))
    return {"instructions": instruction_parts, "input": input_items}


def _request_snapshot(turn: Turn) -> dict[str, Any]:
    request = _get(turn, "request")
    headers = _get(request, "headers", {})
    method = _get(request, "method")
    path = _get(request, "path", _get(request, "url"))
    if isinstance(path, str):
        parsed = urlsplit(path)
        path = parsed.path if parsed.scheme or parsed.netloc else path.partition("?")[0]
    normalizer = _Normalizer()
    return {
        "method": method,
        "path": path,
        "headers": _normalize_headers(headers, normalizer),
        "body": normalizer.value(_body(turn, "request")),
    }


def _normalize_headers(headers: Any, normalizer: _Normalizer | None = None) -> dict[str, Any]:
    if not isinstance(headers, Mapping):
        return {}
    norm = normalizer or _Normalizer()
    result: dict[str, Any] = {}
    for key, value in sorted(headers.items(), key=lambda item: str(item[0]).lower()):
        name = str(key).lower()
        sensitive = name in _CREDENTIAL_HEADERS or "api-key" in name or name.startswith("proxy-auth")
        result[name] = "<redacted>" if sensitive else norm.value(value, key=name)
    return result


def _events(turn: Turn, *, material: bool) -> list[dict[str, Any]]:
    raw_events = _get(turn, "events", []) or []
    selected: list[tuple[str, Any]] = []
    for event in raw_events:
        event_type = _get(event, "event_type", _get(event, "type", _get(event, "event")))
        data = _get(event, "data", event)
        if not isinstance(event_type, str) and isinstance(data, Mapping):
            event_type = data.get("type")
        event_type = str(event_type or "unknown")
        lower_type = event_type.lower()
        if material and (lower_type.startswith("codex.") or lower_type in _IGNORED_EVENT_TYPES):
            continue
        if material and event_type in TERMINAL_TYPES and isinstance(data, Mapping):
            data = dict(data)
            response = data.get("response")
            if isinstance(response, Mapping):
                response = dict(response)
                # Public normalization may backfill terminal output from
                # output_item.done while upstream leaves it empty. Aggregate
                # response_semantics compares the reconstructed output.
                response.pop("output", None)
                for nullable_field in ("error", "incomplete_details"):
                    if response.get(nullable_field) is None:
                        response.pop(nullable_field, None)
                data["response"] = response
        selected.append((event_type, data))

    # Normalize after removing non-material events so a synthetic created event
    # cannot shift the first-seen placeholder numbering for all later IDs.
    normalizer = _Normalizer()
    return [{"type": event_type, "data": normalizer.value(data)} for event_type, data in selected]


def _walk_dicts(value: Any) -> Iterable[Mapping[str, Any]]:
    value = _plain(value)
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_dicts(child)


def _usage(turn: Turn) -> Any:
    candidates: list[Any] = []
    for source in (_body(turn, "response"), [_get(event, "data", event) for event in _get(turn, "events", []) or []]):
        for item in _walk_dicts(source):
            usage = item.get("usage")
            if isinstance(usage, Mapping):
                candidates.append(usage)
    return _Normalizer().value(candidates[-1]) if candidates else None


def _tool_calls(turn: Turn) -> list[Any]:
    calls: list[Any] = []
    sources = [_body(turn, "response")]
    sources.extend(_get(event, "data", event) for event in _get(turn, "events", []) or [])
    seen_objects: set[int] = set()
    for source in sources:
        for item in _walk_dicts(source):
            object_id = id(item)
            if object_id in seen_objects:
                continue
            seen_objects.add(object_id)
            item_type = item.get("type")
            if isinstance(item_type, str) and item_type in {
                "computer_call",
                "custom_tool_call",
                "file_search_call",
                "function_call",
                "local_shell_call",
                "mcp_call",
                "web_search_call",
            }:
                calls.append(item)
    normalized = _Normalizer().value(calls)
    unique: list[Any] = []
    for call in normalized:
        if call not in unique:
            unique.append(call)
    return unique


def _terminal_class(turn: Turn) -> str | None:
    explicit = _get(turn, "terminal_event")
    if explicit:
        event_type = _get(explicit, "event_type", _get(explicit, "type", explicit))
        if isinstance(event_type, str):
            return TERMINAL_TYPES.get(event_type, event_type.removeprefix("response."))

    for event in reversed(_events(turn, material=False)):
        event_type = event["type"]
        if event_type in TERMINAL_TYPES:
            return TERMINAL_TYPES[event_type]

    response = _body(turn, "response")
    response_envelope = _get(turn, "response")
    if isinstance(response_envelope, Mapping) and isinstance(response_envelope.get("network_error"), Mapping):
        return "failed"
    for item in _walk_dicts(response):
        status = item.get("status")
        if isinstance(status, str) and status in {"completed", "failed", "incomplete", "cancelled", "canceled"}:
            return "cancelled" if status in {"cancelled", "canceled"} else status
        if "error" in item and item.get("error") is not None:
            return "failed"
    if str(_get(turn, "transport", "")) == "http_json":
        status_code = _get(response_envelope, "status")
        if isinstance(status_code, int) and not isinstance(status_code, bool):
            return "completed" if 200 <= status_code < 400 else "failed"
        if response is not None:
            return "completed"
    return None


def _header_casefold(headers: Any, name: str) -> Any:
    if not isinstance(headers, Mapping):
        return None
    wanted = name.casefold()
    return next((value for key, value in headers.items() if str(key).casefold() == wanted), None)


def _normalized_retry_after(headers: Any) -> str | None:
    value = _header_casefold(headers, "retry-after")
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _failure_observation(turn: Turn | None) -> dict[str, Any] | None:
    if turn is None:
        return None
    response = _get(turn, "response")
    response_mapping = response if isinstance(response, Mapping) else {}
    status = response_mapping.get("status")
    if not isinstance(status, int) or isinstance(status, bool):
        status = None
    network_error = response_mapping.get("network_error")
    network_category = network_error.get("category") if isinstance(network_error, Mapping) else None
    if not isinstance(network_category, str):
        network_category = None
    complete = bool(_get(turn, "complete", False))
    terminal_class = _terminal_class(turn)
    if network_category is not None:
        outcome_class = "network_error"
    elif not complete:
        outcome_class = "transport_incomplete"
    elif status is not None and status >= 400:
        outcome_class = "http_rejection"
    elif terminal_class in {"failed", "incomplete", "cancelled"}:
        outcome_class = "failure_terminal"
    elif terminal_class == "completed":
        outcome_class = "success_terminal"
    else:
        outcome_class = "unknown"
    return {
        "class": outcome_class,
        "http_status": status,
        "retry_after": _normalized_retry_after(response_mapping.get("headers")),
        "terminal_class": terminal_class,
        "complete": complete,
        "incomplete_reason": _get(turn, "incomplete_reason"),
        "network_error_category": network_category,
    }


def _failure_relation(
    turn_left: Turn | None,
    turn_right: Turn | None,
    *,
    left_key: str,
    right_key: str,
) -> dict[str, Any]:
    path_left = _failure_observation(turn_left)
    path_right = _failure_observation(turn_right)
    if path_left is None or path_right is None:
        relation = "missing_turn"
        compatible: bool | None = False
    elif path_left["class"] == "unknown" or path_right["class"] == "unknown":
        relation = "unobserved"
        compatible = None
    elif path_left == path_right:
        relation = "exact"
        compatible = True
    else:
        failure_classes = {"network_error", "transport_incomplete", "http_rejection", "failure_terminal"}
        if path_left["class"] in failure_classes and path_right["class"] in failure_classes:
            relation = "failure_translation"
            compatible = True
        elif path_left["class"] == "success_terminal" and path_right["class"] == "success_terminal":
            relation = "success_metadata_difference"
            compatible = True
        else:
            relation = "success_failure_mismatch"
            compatible = False
    return {left_key: path_left, right_key: path_right, "relation": relation, "compatible": compatible}


def _response_json(turn: Turn) -> Any:
    return _Normalizer().value(_body(turn, "response"))


def _decoded_json(value: Any) -> Any:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _response_semantics(turn: Turn) -> dict[str, Any]:
    """Build the response object common to JSON and streaming transports."""

    candidate = _decoded_json(_body(turn, "response"))
    if isinstance(candidate, Mapping) and isinstance(candidate.get("response"), Mapping):
        candidate = candidate["response"]

    terminal_response: Mapping[str, Any] | None = None
    done_items: list[tuple[int, Any]] = []
    for ordinal, event in enumerate(_get(turn, "events", []) or []):
        event_type = _get(event, "event_type", _get(event, "type", ""))
        data = _get(event, "data", {})
        if event_type in TERMINAL_TYPES and isinstance(data, Mapping):
            nested = data.get("response")
            terminal_response = nested if isinstance(nested, Mapping) else data
        if event_type == "response.output_item.done" and isinstance(data, Mapping) and "item" in data:
            index = data.get("output_index")
            done_items.append((index if isinstance(index, int) else ordinal, data["item"]))

    if terminal_response is not None:
        candidate = dict(terminal_response)
    if not isinstance(candidate, Mapping):
        return {}

    response = dict(candidate)
    if done_items and not response.get("output"):
        response["output"] = [item for _, item in sorted(done_items, key=lambda entry: entry[0])]
    # Public JSON adapters may omit nullable envelope fields that native
    # Responses terminals spell explicitly as null. Neither form carries a
    # material response value.
    selected = {
        field: response[field]
        for field in SEMANTIC_RESPONSE_FIELDS
        if field in response and response[field] is not None
    }
    return _Normalizer().value(selected)


def _shape(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _shape(child) for key, child in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [_shape(child) for child in value]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _first_differences(left: Any, right: Any, path: str = "$", limit: int = _MAX_DIFFS) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []

    def visit(a: Any, b: Any, location: str) -> None:
        if len(differences) >= limit or a == b:
            return
        if isinstance(a, Mapping) and isinstance(b, Mapping):
            for key in sorted(set(a) | set(b), key=str):
                if len(differences) >= limit:
                    return
                child_path = f"{location}.{key}"
                if key not in a:
                    differences.append({"path": child_path, "path_b": "<missing>", "path_c": b[key]})
                elif key not in b:
                    differences.append({"path": child_path, "path_b": a[key], "path_c": "<missing>"})
                else:
                    visit(a[key], b[key], child_path)
            return
        if isinstance(a, list) and isinstance(b, list):
            for index in range(max(len(a), len(b))):
                if len(differences) >= limit:
                    return
                child_path = f"{location}[{index}]"
                if index >= len(a):
                    differences.append({"path": child_path, "path_b": "<missing>", "path_c": b[index]})
                elif index >= len(b):
                    differences.append({"path": child_path, "path_b": a[index], "path_c": "<missing>"})
                else:
                    visit(a[index], b[index], child_path)
            return
        differences.append({"path": location, "path_b": a, "path_c": b})

    visit(left, right, path)
    return differences


def _turn_summary(turn: Turn | None) -> dict[str, Any] | None:
    if turn is None:
        return None
    events = _events(turn, material=False)
    semantic_request = _semantic_request(turn)
    return {
        "transport": str(_get(turn, "transport", "unknown")),
        "capture_body_modes": _capture_body_modes(turn),
        "complete": bool(_get(turn, "complete", False)),
        "model": semantic_request.get("model"),
        "service_tier": semantic_request.get("service_tier"),
        "reasoning": semantic_request.get("reasoning"),
        "event_types": [event["type"] for event in events],
        "material_event_types": [event["type"] for event in _events(turn, material=True)],
        "terminal_class": _terminal_class(turn),
        "failure_outcome": _failure_observation(turn),
        "usage": _usage(turn),
        "tool_calls": _tool_calls(turn),
    }


def _capture_body_modes(turn: Turn) -> list[str]:
    modes = {
        mode
        for record in (_get(turn, "source_records", []) or [])
        if isinstance(record, Mapping) and isinstance((mode := record.get("capture_body_mode")), str)
    }
    return sorted(modes)


def _add_mismatch(
    mismatches: list[dict[str, Any]],
    category: str,
    turn_number: int | None,
    path_b: Any,
    path_c: Any,
    *,
    detail: str,
) -> None:
    mismatch: dict[str, Any] = {"category": category, "detail": detail, "path_b": path_b, "path_c": path_c}
    if turn_number is not None:
        mismatch["turn"] = turn_number
    mismatches.append(mismatch)


def _compare_bc_turn(turn_number: int, turn_b: Turn | None, turn_c: Turn | None) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    mismatches: list[dict[str, Any]] = []
    row: dict[str, Any] = {
        "turn": turn_number,
        "path_b": _turn_summary(turn_b),
        "path_c": _turn_summary(turn_c),
        "checks": checks,
        "hard_mismatches": mismatches,
    }

    if turn_b is None or turn_c is None:
        checks["turn_present"] = False
        _add_mismatch(
            mismatches,
            "missing_turn",
            turn_number,
            turn_b is not None,
            turn_c is not None,
            detail="A same-run turn is absent from one capture leg.",
        )
        row["passed"] = False
        return row
    checks["turn_present"] = True

    body_modes_b = _capture_body_modes(turn_b)
    body_modes_c = _capture_body_modes(turn_c)
    checks["body_capture"] = {"path_b": body_modes_b, "path_c": body_modes_c, "sufficient": True}
    if "none" in body_modes_b or "none" in body_modes_c:
        checks["body_capture"]["sufficient"] = False
        _add_mismatch(
            mismatches,
            "insufficient_capture_body",
            turn_number,
            body_modes_b,
            body_modes_c,
            detail="Body mode 'none' cannot establish request/response semantic fidelity.",
        )

    transport_b = str(_get(turn_b, "transport", "unknown"))
    transport_c = str(_get(turn_c, "transport", "unknown"))
    checks["transport"] = {
        "same": transport_b == transport_c,
        "translated": transport_b != transport_c,
        "supported": transport_b in TRANSPORTS and transport_c in TRANSPORTS,
    }
    if not checks["transport"]["supported"]:
        _add_mismatch(
            mismatches,
            "malformed_transport",
            turn_number,
            transport_b,
            transport_c,
            detail="The turn has an unknown transport classification.",
        )

    complete_b = bool(_get(turn_b, "complete", False))
    complete_c = bool(_get(turn_c, "complete", False))
    checks["complete"] = complete_b and complete_c
    if not checks["complete"]:
        _add_mismatch(
            mismatches,
            "malformed_turn",
            turn_number,
            complete_b,
            complete_c,
            detail="A same-run turn is incomplete or lacks a terminal response.",
        )

    retry_after_b = (_failure_observation(turn_b) or {}).get("retry_after")
    retry_after_c = (_failure_observation(turn_c) or {}).get("retry_after")
    checks["retry_after"] = retry_after_b == retry_after_c
    if not checks["retry_after"]:
        _add_mismatch(
            mismatches,
            "retry_after",
            turn_number,
            retry_after_b,
            retry_after_c,
            detail="The client-visible Retry-After hint changed across the LB.",
        )

    request_b = _semantic_request(turn_b)
    request_c = _semantic_request(turn_c)
    request_c_for_comparison = dict(request_c)
    if "prompt_cache_key" not in request_b:
        # codex-lb may derive a cache key for a public request that did not
        # supply one. A client-supplied key remains material and must survive.
        request_c_for_comparison.pop("prompt_cache_key", None)
    checks["semantic_request"] = request_b == request_c_for_comparison
    if request_b != request_c_for_comparison:
        _add_mismatch(
            mismatches,
            "semantic_request",
            turn_number,
            request_b,
            request_c_for_comparison,
            detail="Preserved request fields changed across the LB.",
        )

    conditional_b = _conditional_request_values(turn_b)
    conditional_c = _conditional_request_values(turn_c)
    shared_conditional_fields = sorted(set(conditional_b) & set(conditional_c))

    def conditional_values_match(field: str) -> bool:
        if field == "store" and conditional_c[field] is False:
            # Public Responses accepts the spelling but canonicalizes the
            # upstream contract to store=false.
            return True
        return conditional_b[field] == conditional_c[field]

    changed_conditional_fields = {
        field: {"path_b": conditional_b[field], "path_c": conditional_c[field]}
        for field in shared_conditional_fields
        if not conditional_values_match(field)
    }
    checks["conditional_request_fields"] = {
        "shared": shared_conditional_fields,
        "changed": changed_conditional_fields,
        "path_b_only": sorted(set(conditional_b) - set(conditional_c)),
        "path_c_only": sorted(set(conditional_c) - set(conditional_b)),
    }
    if changed_conditional_fields:
        _add_mismatch(
            mismatches,
            "conditional_request_field",
            turn_number,
            {field: values["path_b"] for field, values in changed_conditional_fields.items()},
            {field: values["path_c"] for field, values in changed_conditional_fields.items()},
            detail="A request field present on both wire legs changed value across the LB.",
        )

    raw_request_b = _request_snapshot(turn_b)
    raw_request_c = _request_snapshot(turn_c)
    row["raw_request_differences"] = _first_differences(raw_request_b, raw_request_c)

    terminal_b = _terminal_class(turn_b)
    terminal_c = _terminal_class(turn_c)
    checks["terminal_class"] = terminal_b == terminal_c and terminal_b is not None
    if not checks["terminal_class"]:
        _add_mismatch(
            mismatches,
            "terminal_class",
            turn_number,
            terminal_b,
            terminal_c,
            detail="Completion/failure/incomplete terminal semantics differ.",
        )

    usage_b = _usage(turn_b)
    usage_c = _usage(turn_c)
    checks["usage"] = usage_b == usage_c
    if not checks["usage"]:
        _add_mismatch(
            mismatches,
            "usage",
            turn_number,
            usage_b,
            usage_c,
            detail="Final Responses API usage changed across the LB.",
        )

    tool_calls_b = _tool_calls(turn_b)
    tool_calls_c = _tool_calls(turn_c)
    checks["tool_calls"] = tool_calls_b == tool_calls_c
    if not checks["tool_calls"]:
        _add_mismatch(
            mismatches,
            "tool_calls",
            turn_number,
            tool_calls_b,
            tool_calls_c,
            detail="Tool identity, arguments, order, or call correlation changed.",
        )

    response_b = _response_semantics(turn_b)
    response_c = _response_semantics(turn_c)
    checks["response_semantics"] = response_b == response_c
    if response_b != response_c:
        _add_mismatch(
            mismatches,
            "response_semantics",
            turn_number,
            _first_differences(response_b, response_c),
            None,
            detail="Aggregate response structure or output changed across the LB.",
        )

    material_b = _events(turn_b, material=True)
    material_c = _events(turn_c, material=True)
    types_b = [event["type"] for event in material_b]
    types_c = [event["type"] for event in material_c]
    comparable_streams = transport_b in STREAMING_TRANSPORTS and transport_c in STREAMING_TRANSPORTS
    checks["material_events"] = {
        "comparable": comparable_streams,
        "order_matches": types_b == types_c if comparable_streams else None,
        "payloads_match": material_b == material_c if comparable_streams else None,
    }
    if comparable_streams and types_b != types_c:
        _add_mismatch(
            mismatches,
            "material_event_order",
            turn_number,
            types_b,
            types_c,
            detail="Ordered material events were added, removed, or reordered.",
        )
    elif comparable_streams and material_b != material_c:
        _add_mismatch(
            mismatches,
            "material_event_payload",
            turn_number,
            _first_differences(material_b, material_c),
            None,
            detail="A material event payload changed after volatile-field normalization.",
        )

    if transport_b == transport_c == "http_json":
        response_b = _response_json(turn_b)
        response_c = _response_json(turn_c)
        checks["json_response"] = response_b == response_c
        if response_b != response_c:
            _add_mismatch(
                mismatches,
                "json_response",
                turn_number,
                _first_differences(response_b, response_c),
                None,
                detail="The same-run JSON response changed across the LB.",
            )
    else:
        checks["json_response"] = None

    row["passed"] = not mismatches
    return row


def _baseline_turn(turn_number: int, turn_a: Turn | None, turn_c: Turn | None) -> dict[str, Any]:
    if turn_a is None or turn_c is None:
        return {
            "turn": turn_number,
            "path_a": _turn_summary(turn_a),
            "path_c": _turn_summary(turn_c),
            "missing": "path_a" if turn_a is None else "path_c",
        }

    transport_a = str(_get(turn_a, "transport", "unknown"))
    transport_c = str(_get(turn_c, "transport", "unknown"))
    events_a = [event["type"] for event in _events(turn_a, material=True)]
    events_c = [event["type"] for event in _events(turn_c, material=True)]
    request_a = _semantic_request(turn_a)
    request_c = _semantic_request(turn_c)
    return {
        "turn": turn_number,
        "path_a": _turn_summary(turn_a),
        "path_c": _turn_summary(turn_c),
        "transport": {
            "same": transport_a == transport_c,
            "path_a": transport_a,
            "path_c": transport_c,
        },
        "protocol_observations": {
            "semantic_request_matches": request_a == request_c,
            "terminal_class_matches": _terminal_class(turn_a) == _terminal_class(turn_c),
            "material_event_order_matches": events_a == events_c,
            "usage_delta": _usage_delta(_usage(turn_a), _usage(turn_c)),
        },
        "server_observable": _compare_server_observable(turn_a, turn_c),
    }


def _first_source_record(turn: Turn) -> Mapping[str, Any]:
    records = _get(turn, "source_records", []) or []
    return records[0] if records and isinstance(records[0], Mapping) else {}


def _selected_headers(headers: Any, names: Sequence[str]) -> dict[str, Any]:
    normalized = _normalize_headers(headers)
    return {name: normalized[name] for name in names if name in normalized}


def _server_observable_profile(turn: Turn) -> dict[str, Any]:
    record = _first_source_record(turn)
    raw_request = record.get("request")
    request: Mapping[str, Any] = raw_request if isinstance(raw_request, Mapping) else _get(turn, "request", {})
    headers = request.get("headers", {}) if isinstance(request, Mapping) else {}
    raw_network = record.get("network")
    network: Mapping[str, Any] = raw_network if isinstance(raw_network, Mapping) else {}
    raw_tls = network.get("tls")
    tls: Mapping[str, Any] = raw_tls if isinstance(raw_tls, Mapping) else {}
    raw_source_observer = network.get("source_observer")
    source_observer = dict(raw_source_observer) if isinstance(raw_source_observer, Mapping) else None
    transport = str(_get(turn, "transport", "unknown"))
    raw_response = record.get("response")
    response: Mapping[str, Any] = raw_response if isinstance(raw_response, Mapping) else {}
    raw_header_names = request.get("header_names") if isinstance(request, Mapping) else None
    header_names = (
        list(raw_header_names)
        if isinstance(raw_header_names, list) and all(isinstance(name, str) for name in raw_header_names)
        else None
    )
    return {
        "protocol": {
            "transport": transport,
            "http_version": network.get("http_version"),
            "alpn": tls.get("alpn"),
        },
        "tls": dict(tls),
        "observed_source": source_observer,
        "identity": _selected_headers(headers, _IDENTITY_HEADER_NAMES),
        "header_names": header_names,
        "sse": (
            {
                "content_type": _normalize_headers(response.get("headers", {})).get("content-type"),
                "done_seen": response.get("done_seen"),
            }
            if transport == "http_sse"
            else None
        ),
        "websocket": _selected_headers(headers, _WEBSOCKET_HEADER_NAMES) if transport == "websocket" else None,
    }


def _stable_tls_profile(value: Any) -> Any:
    """Remove per-handshake wire entropy while preserving TLS capabilities.

    rustls deliberately varies ClientHello extension order.  Raw JA3 and the
    ClientHello hashes remain in the report as wire observations, but ordering
    alone is not evidence that two clients use different TLS capabilities.
    """

    return stable_tls_profile(value)


def _compare_observed_source(left: Any, right: Any) -> dict[str, Any]:
    base = {"path_a": left, "path_c": right}
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return {**base, "matches": None, "status": "unobserved", "reason": "missing_observer_evidence"}

    observer_a = left.get("observer_id_sha256")
    observer_c = right.get("observer_id_sha256")
    role_a = left.get("role")
    role_c = right.get("role")
    if not observer_a or not observer_c:
        return {**base, "matches": None, "status": "unobserved", "reason": "missing_observer_attestation"}
    if observer_a != observer_c or role_a != role_c:
        return {**base, "matches": None, "status": "unobserved", "reason": "different_observer_boundary"}

    source_a = left.get("source_host")
    source_c = right.get("source_host")
    if not isinstance(source_a, Mapping) or not isinstance(source_c, Mapping):
        return {**base, "matches": None, "status": "unobserved", "reason": "missing_source_address"}
    if not all(source.get("family") and source.get("hmac_sha256") for source in (source_a, source_c)):
        return {**base, "matches": None, "status": "unobserved", "reason": "incomplete_source_address"}

    matches = source_a == source_c
    return {
        **base,
        "matches": matches,
        "status": "match" if matches else "mismatch",
        "claim_scope": "controlled_origin" if role_a == "origin" else "intercept_boundary",
        "public_source_ip_evidence": role_a == "origin",
    }


def _compare_asn(left: Any, right: Any) -> dict[str, Any]:
    asn_a = left.get("asn") if isinstance(left, Mapping) else None
    asn_c = right.get("asn") if isinstance(right, Mapping) else None
    base = {"path_a": asn_a, "path_c": asn_c}
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return {**base, "matches": None, "status": "unobserved", "reason": "missing_observer_evidence"}
    observer_a = left.get("observer_id_sha256")
    observer_c = right.get("observer_id_sha256")
    role_a = left.get("role")
    role_c = right.get("role")
    if not observer_a or not observer_c:
        return {**base, "matches": None, "status": "unobserved", "reason": "missing_observer_attestation"}
    if observer_a != observer_c or role_a != role_c:
        return {**base, "matches": None, "status": "unobserved", "reason": "different_observer_boundary"}
    if not isinstance(asn_a, Mapping) or not isinstance(asn_c, Mapping):
        return {**base, "matches": None, "status": "unobserved", "reason": "missing_asn_evidence"}
    if asn_a.get("status") != "observed" or asn_c.get("status") != "observed":
        return {**base, "matches": None, "status": "unobserved", "reason": "asn_lookup_unavailable"}
    database_a = asn_a.get("database")
    database_c = asn_c.get("database")
    digest_a = database_a.get("sha256") if isinstance(database_a, Mapping) else None
    digest_c = database_c.get("sha256") if isinstance(database_c, Mapping) else None
    if not digest_a or not digest_c or digest_a != digest_c:
        return {**base, "matches": None, "status": "unobserved", "reason": "different_asn_database"}
    organization_a = asn_a.get("organization_sha256")
    organization_c = asn_c.get("organization_sha256")
    if not organization_a or not organization_c:
        return {**base, "matches": None, "status": "unobserved", "reason": "missing_asn_organization"}
    matches = (asn_a.get("number"), asn_a.get("organization_sha256")) == (
        asn_c.get("number"),
        asn_c.get("organization_sha256"),
    )
    return {
        **base,
        "matches": matches,
        "status": "match" if matches else "mismatch",
        "claim_scope": "controlled_origin" if role_a == "origin" else "intercept_boundary",
        "public_egress_asn_evidence": role_a == "origin",
    }


def _compare_server_observable(turn_a: Turn, turn_c: Turn) -> dict[str, Any]:
    profile_a = _server_observable_profile(turn_a)
    profile_c = _server_observable_profile(turn_c)
    dimensions: dict[str, Any] = {}
    for name in ("protocol", "tls", "identity", "sse", "websocket"):
        left = profile_a[name]
        right = profile_c[name]
        matches = _stable_tls_profile(left) == _stable_tls_profile(right) if name == "tls" else left == right
        dimensions[name] = {
            "matches": matches,
            "path_a": left,
            "path_c": right,
        }
        if name == "tls":
            dimensions[name]["wire_exact_matches"] = left == right
    dimensions["observed_source"] = _compare_observed_source(profile_a["observed_source"], profile_c["observed_source"])
    dimensions["asn"] = _compare_asn(profile_a["observed_source"], profile_c["observed_source"])
    header_names_a = profile_a["header_names"]
    header_names_c = profile_c["header_names"]
    if not isinstance(header_names_a, list) or not isinstance(header_names_c, list):
        for name in ("header_order", "header_casing"):
            dimensions[name] = {
                "matches": None,
                "status": "unobserved",
                "reason": "missing_header_sequence_evidence",
                "path_a": header_names_a,
                "path_c": header_names_c,
            }
    else:
        normalized_a = [name.casefold() for name in header_names_a]
        normalized_c = [name.casefold() for name in header_names_c]
        dimensions["header_order"] = {
            "matches": normalized_a == normalized_c,
            "path_a": normalized_a,
            "path_c": normalized_c,
        }
        dimensions["header_casing"] = {
            "matches": header_names_a == header_names_c,
            "path_a": header_names_a,
            "path_c": header_names_c,
        }
    observed_matches = [item["matches"] for item in dimensions.values() if item["matches"] is not None]
    return {
        "all_observed_dimensions_match": all(observed_matches),
        "unobserved_dimensions": [name for name, item in dimensions.items() if item["matches"] is None],
        "dimensions": dimensions,
    }


def _usage_delta(usage_a: Any, usage_c: Any) -> dict[str, Any] | None:
    if not isinstance(usage_a, Mapping) or not isinstance(usage_c, Mapping):
        return None
    delta: dict[str, Any] = {}
    for key in sorted(set(usage_a) | set(usage_c)):
        left = usage_a.get(key)
        right = usage_c.get(key)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            delta[key] = right - left
    return delta


def _transport_counts(turns: Sequence[Turn]) -> dict[str, int]:
    return dict(sorted(Counter(str(_get(turn, "transport", "unknown")) for turn in turns).items()))


def compare_turns(
    turns_b: Sequence[Turn],
    turns_c: Sequence[Turn],
    turns_a: Sequence[Turn] | None = None,
    *,
    paths: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """Compare extracted turns.  B/C must originate from the same run."""

    bc_rows = [
        _compare_bc_turn(
            index + 1,
            turns_b[index] if index < len(turns_b) else None,
            turns_c[index] if index < len(turns_c) else None,
        )
        for index in range(max(len(turns_b), len(turns_c)))
    ]
    hard_mismatches = [mismatch for row in bc_rows for mismatch in row["hard_mismatches"]]
    if not turns_b or not turns_c:
        _add_mismatch(
            hard_mismatches,
            "empty_capture",
            None,
            len(turns_b),
            len(turns_c),
            detail="Both same-run legs must contain at least one extracted turn.",
        )

    baseline_rows: list[dict[str, Any]] = []
    if turns_a is not None:
        baseline_rows = [
            _baseline_turn(
                index + 1,
                turns_a[index] if index < len(turns_a) else None,
                turns_c[index] if index < len(turns_c) else None,
            )
            for index in range(max(len(turns_a), len(turns_c)))
        ]

    passed = not hard_mismatches
    failure_rows = [
        {
            "turn": index + 1,
            **_failure_relation(
                turns_b[index] if index < len(turns_b) else None,
                turns_c[index] if index < len(turns_c) else None,
                left_key="path_b",
                right_key="path_c",
            ),
        }
        for index in range(max(len(turns_b), len(turns_c)))
    ]
    observed_failure_compatibility = [row["compatible"] for row in failure_rows if row["compatible"] is not None]
    failure_rows_a_vs_b: list[dict[str, Any]] = []
    final_failure_a_vs_b: dict[str, Any] | None = None
    if turns_a is not None:
        failure_rows_a_vs_b = [
            {
                "turn": index + 1,
                **_failure_relation(
                    turns_a[index] if index < len(turns_a) else None,
                    turns_b[index] if index < len(turns_b) else None,
                    left_key="path_a",
                    right_key="path_b",
                ),
            }
            for index in range(max(len(turns_a), len(turns_b)))
        ]
        final_failure_a_vs_b = _failure_relation(
            turns_a[-1] if turns_a else None,
            turns_b[-1] if turns_b else None,
            left_key="path_a",
            right_key="path_b",
        )
    observed_a_vs_b_compatibility = [row["compatible"] for row in failure_rows_a_vs_b if row["compatible"] is not None]
    result: dict[str, Any] = {
        "schema_version": 1,
        "paths": dict(paths or {"path_a": None, "path_b": None, "path_c": None}),
        "policy": {
            "path_b_vs_c": "same-run strict semantic parity",
            "path_a": "informational direct-upstream protocol baseline",
            "ignored_event_types": [
                "codex.*",
                "response.created",
                "responsesapi.websocket_timing",
                "done/[DONE]",
            ],
            "normalized_fields": ["volatile IDs", "timestamps", "sequence fields", "credential headers"],
        },
        "turn_counts": {
            "path_a": len(turns_a) if turns_a is not None else None,
            "path_b": len(turns_b),
            "path_c": len(turns_c),
        },
        "transports": {
            "path_a": _transport_counts(turns_a) if turns_a is not None else None,
            "path_b": _transport_counts(turns_b),
            "path_c": _transport_counts(turns_c),
        },
        "path_b_vs_c": {
            "passed": passed,
            "hard_mismatch_count": len(hard_mismatches),
            "hard_mismatches": hard_mismatches,
            "turns": bc_rows,
        },
        "failure_path_b_vs_c": {
            "available": bool(failure_rows),
            "informational_only": True,
            "all_observed_outcomes_compatible": (
                all(observed_failure_compatibility) if observed_failure_compatibility else None
            ),
            "turns": failure_rows,
        },
        "failure_path_a_vs_b": {
            "available": turns_a is not None,
            "informational_only": True,
            "attempt_counts": {
                "path_a": len(turns_a) if turns_a is not None else None,
                "path_b": len(turns_b),
            },
            "all_observed_outcomes_compatible": (
                all(observed_a_vs_b_compatibility) if observed_a_vs_b_compatibility else None
            ),
            "final_outcome": final_failure_a_vs_b,
            "turns": failure_rows_a_vs_b,
        },
        "path_a_baseline": {
            "available": turns_a is not None,
            "informational_only": True,
            "turns": baseline_rows,
        },
        "server_observable_a_vs_c": {
            "available": turns_a is not None,
            "informational_only": True,
            "turns": [
                {
                    "turn": row["turn"],
                    "comparison": row.get("server_observable"),
                }
                for row in baseline_rows
                if row.get("server_observable") is not None
            ],
        },
        "summary": {
            "overall_pass": passed,
            "strict_exit_code": 0 if passed else 2,
            "hard_mismatch_count": len(hard_mismatches),
        },
    }
    return result


def _load_extraction(path: str) -> TurnExtraction:
    records = load_capture(path, strict=True)
    return extract_turns_with_diagnostics(records)


def _diagnostics(extraction: TurnExtraction | None) -> dict[str, Any] | None:
    if extraction is None:
        return None
    return {
        "parse_errors": list(extraction.parse_errors),
        "orphan_websocket_messages": [
            {
                "flow_id": orphan.flow_id,
                "direction": orphan.direction,
                "message_index": orphan.message_index,
                "event_type": orphan.event.type,
                "reason": orphan.reason,
            }
            for orphan in extraction.orphan_websocket_messages
        ],
        "incomplete_websocket_turns": [turn.index for turn in extraction.incomplete_websocket_turns],
        "incomplete_http_turns": [turn.index for turn in extraction.incomplete_http_turns],
    }


def compare_paths(
    path_b: str,
    path_c: str,
    path_a: str | None = None,
    *,
    path_a_reference: str | None = None,
    tls_min_samples: int = DEFAULT_MIN_SAMPLES,
) -> dict[str, Any]:
    """Load capture files and compare client, upstream, and optional direct legs."""

    errors: list[dict[str, str]] = []

    def load(label: str, path: str | None) -> TurnExtraction | None:
        if path is None:
            return None
        try:
            return _load_extraction(path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            errors.append({"path": label, "file": path, "error": str(exc)})
            return TurnExtraction(turns=[])

    extraction_b = load("path_b", path_b) or TurnExtraction(turns=[])
    extraction_c = load("path_c", path_c) or TurnExtraction(turns=[])
    extraction_a = load("path_a", path_a) if path_a is not None else None
    result = compare_turns(
        extraction_b.turns,
        extraction_c.turns,
        extraction_a.turns if extraction_a is not None else None,
        paths={"path_a_reference": path_a_reference, "path_a": path_a, "path_b": path_b, "path_c": path_c},
    )
    if path_a_reference is not None and path_a is not None:
        result["tls_randomization_a_vs_c"] = analyze_tls_randomization_paths(
            path_a_reference,
            path_a,
            path_c,
            min_samples=tls_min_samples,
        )
    else:
        result["tls_randomization_a_vs_c"] = {
            "available": False,
            "informational_only": True,
            "reason": "path_a_reference_not_supplied" if path_a_reference is None else "path_a_not_supplied",
            "all_observed_transports_match": None,
            "unobserved_transports": list(TLS_TRANSPORTS),
            "transports": {},
        }
    result["diagnostics"] = {
        "path_a": _diagnostics(extraction_a),
        "path_b": _diagnostics(extraction_b),
        "path_c": _diagnostics(extraction_c),
    }

    diagnostic_mismatches: list[dict[str, Any]] = []
    for label, extraction in (("path_b", extraction_b), ("path_c", extraction_c)):
        for error in extraction.parse_errors:
            diagnostic_mismatches.append(
                {
                    "category": "capture_parse_error",
                    "detail": error,
                    "path": label,
                }
            )
        for orphan in extraction.orphan_websocket_messages:
            if orphan.direction == "server_to_client" or orphan.reason == "invalid_direction":
                diagnostic_mismatches.append(
                    {
                        "category": "orphan_websocket_response",
                        "detail": orphan.reason,
                        "path": label,
                        "flow_id": orphan.flow_id,
                        "message_index": orphan.message_index,
                        "event_type": orphan.event.type,
                    }
                )
    result["path_b_vs_c"]["hard_mismatches"].extend(diagnostic_mismatches)
    if errors:
        result["capture_errors"] = errors
        bc_errors = [error for error in errors if error["path"] in {"path_b", "path_c"}]
        for error in bc_errors:
            result["path_b_vs_c"]["hard_mismatches"].append(
                {
                    "category": "capture_error",
                    "detail": error["error"],
                    "path": error["path"],
                    "file": error["file"],
                }
            )
    if diagnostic_mismatches or errors:
        count = len(result["path_b_vs_c"]["hard_mismatches"])
        result["path_b_vs_c"]["passed"] = count == 0
        result["path_b_vs_c"]["hard_mismatch_count"] = count
        result["summary"].update(
            overall_pass=count == 0,
            strict_exit_code=0 if count == 0 else 2,
            hard_mismatch_count=count,
        )
    return result


def compare_captures(path_a: str | None, path_b: str, path_c: str) -> dict[str, Any]:
    """Compatibility wrapper using the traditional A, B, C argument order."""

    return compare_paths(path_b=path_b, path_c=path_c, path_a=path_a)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Codex client/LB/upstream traffic captures.")
    parser.add_argument("--path-a", help="Optional direct-upstream baseline capture")
    parser.add_argument(
        "--path-a-reference",
        help="Optional second direct capture used to calibrate TLS extension-order variance",
    )
    parser.add_argument("--path-b", required=True, help="Required client-to-LB capture")
    parser.add_argument("--path-c", required=True, help="Required LB-to-upstream capture")
    parser.add_argument("--json-output", help="Also write the JSON result to this file")
    parser.add_argument(
        "--tls-min-samples",
        type=int,
        default=DEFAULT_MIN_SAMPLES,
        help=f"Minimum deduplicated ClientHellos per TLS cohort (default: {DEFAULT_MIN_SAMPLES})",
    )
    parser.add_argument("--strict", action="store_true", help="Exit nonzero on a hard B/C mismatch")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    result = compare_paths(
        args.path_b,
        args.path_c,
        args.path_a,
        path_a_reference=args.path_a_reference,
        tls_min_samples=args.tls_min_samples,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.json_output:
        output = Path(args.json_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return int(result["summary"]["strict_exit_code"]) if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
