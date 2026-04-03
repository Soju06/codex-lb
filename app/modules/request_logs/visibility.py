from __future__ import annotations

import json
import re
from collections.abc import Mapping

from app.core.clients.proxy import _interesting_upstream_header_keys
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping

MAX_REQUEST_VISIBILITY_BYTES = 16 * 1024
_REDACTED_VALUE = "[REDACTED]"
_UNSUPPORTED_VALUE = "[UNSUPPORTED]"
_TRUNCATED_VALUE = "[TRUNCATED]"
_SECRET_KEY_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "cookie",
        "password",
        "secret",
        "session",
        "token",
    }
)
_EXCLUDED_HEADER_KEYS = frozenset({"session_id", "x-codex-session-id", "x-codex-conversation-id"})
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_TRUNCATION_POLICIES = (
    (2000, 24, 24, 8),
    (800, 12, 18, 6),
    (240, 6, 12, 4),
)

type RequestVisibilityDocument = dict[str, JsonValue]


def build_request_visibility_document(
    headers: Mapping[str, str],
    body: object,
    *,
    max_bytes: int = MAX_REQUEST_VISIBILITY_BYTES,
) -> RequestVisibilityDocument | None:
    if not _is_supported_json_body(body):
        return None

    captured_headers = _capture_headers(headers)
    redacted_body = _redact_json_value(body)
    document: RequestVisibilityDocument = {
        "headers": captured_headers,
        "body": redacted_body,
        "truncated": False,
    }
    serialized = json.dumps(document, separators=(",", ":"), sort_keys=True)
    if len(serialized.encode("utf-8")) <= max_bytes:
        return document

    for max_string_chars, max_list_items, max_object_items, max_depth in _TRUNCATION_POLICIES:
        truncated_body = _truncate_json_value(
            redacted_body,
            max_string_chars=max_string_chars,
            max_list_items=max_list_items,
            max_object_items=max_object_items,
            max_depth=max_depth,
        )
        truncated_document: RequestVisibilityDocument = {
            "headers": captured_headers,
            "body": truncated_body,
            "truncated": True,
        }
        truncated_serialized = json.dumps(truncated_document, separators=(",", ":"), sort_keys=True)
        if len(truncated_serialized.encode("utf-8")) <= max_bytes:
            return truncated_document

    summarized_body = _summarize_json_value(redacted_body)
    summarized_document: RequestVisibilityDocument = {
        "headers": captured_headers,
        "body": summarized_body,
        "truncated": True,
    }
    summarized_serialized = json.dumps(summarized_document, separators=(",", ":"), sort_keys=True)
    if len(summarized_serialized.encode("utf-8")) <= max_bytes:
        return summarized_document

    return {
        "headers": captured_headers,
        "body": {
            "_truncated": True,
            "kind": _json_kind(body),
            "message": f"request visibility truncated at {max_bytes} bytes",
        },
        "truncated": True,
    }


def _capture_headers(headers: Mapping[str, str]) -> dict[str, JsonValue]:
    lowered = {key.lower(): value for key, value in headers.items()}
    return {
        key: lowered[key]
        for key in _interesting_upstream_header_keys(headers)
        if key in lowered and key not in _EXCLUDED_HEADER_KEYS
    }


def _is_supported_json_body(body: object) -> bool:
    if is_json_mapping(body) or is_json_list(body):
        return True
    return isinstance(body, str | int | float | bool) or body is None


def _redact_json_value(value: object) -> JsonValue:
    if is_json_mapping(value):
        return {key: _REDACTED_VALUE if _is_secret_key(key) else _redact_json_value(item) for key, item in value.items()}
    if is_json_list(value):
        return [_redact_json_value(item) for item in value]
    if isinstance(value, str):
        return value
    if isinstance(value, int | float | bool) or value is None:
        return value
    return _UNSUPPORTED_VALUE


def _truncate_json_value(
    value: JsonValue,
    *,
    max_string_chars: int,
    max_list_items: int,
    max_object_items: int,
    max_depth: int,
    depth: int = 0,
) -> JsonValue:
    if isinstance(value, str):
        if len(value) <= max_string_chars:
            return value
        return f"{value[:max_string_chars]}… [{len(value) - max_string_chars} chars truncated]"

    if isinstance(value, int | float | bool) or value is None:
        return value

    if depth >= max_depth:
        return {
            "_truncated": True,
            "kind": _json_kind(value),
            "message": _TRUNCATED_VALUE,
        }

    if is_json_list(value):
        items = [
            _truncate_json_value(
                item,
                max_string_chars=max_string_chars,
                max_list_items=max_list_items,
                max_object_items=max_object_items,
                max_depth=max_depth,
                depth=depth + 1,
            )
            for item in value[:max_list_items]
        ]
        if len(value) > max_list_items:
            items.append({"_truncated_items": len(value) - max_list_items})
        return items

    if is_json_mapping(value):
        items = list(value.items())
        truncated: dict[str, JsonValue] = {}
        for key, item in items[:max_object_items]:
            truncated[key] = _truncate_json_value(
                item,
                max_string_chars=max_string_chars,
                max_list_items=max_list_items,
                max_object_items=max_object_items,
                max_depth=max_depth,
                depth=depth + 1,
            )
        if len(items) > max_object_items:
            truncated["_truncated_keys"] = len(items) - max_object_items
        return truncated

    return _UNSUPPORTED_VALUE


def _summarize_json_value(value: JsonValue, *, depth: int = 0) -> JsonValue:
    if isinstance(value, str):
        if len(value) <= 80:
            return value
        return f"{value[:80]}… [{len(value) - 80} chars truncated]"

    if isinstance(value, int | float | bool) or value is None:
        return value

    if is_json_list(value):
        if not value:
            return []
        return {
            "_truncated": True,
            "kind": "array",
            "items": len(value),
            "sample": _summarize_json_value(value[0], depth=depth + 1),
        }

    if is_json_mapping(value):
        if depth >= 3:
            return {
                "_truncated": True,
                "kind": "object",
                "message": _TRUNCATED_VALUE,
            }
        summarized: dict[str, JsonValue] = {}
        for key, item in value.items():
            summarized[key] = _summarize_json_value(item, depth=depth + 1)
        return summarized

    return _UNSUPPORTED_VALUE


def _is_secret_key(key: str) -> bool:
    lowered = key.strip().lower()
    normalized = _NON_ALNUM.sub("", lowered)
    for part in _SECRET_KEY_PARTS:
        normalized_part = _NON_ALNUM.sub("", part)
        if part in lowered or normalized_part in normalized:
            return True
    return False


def _json_kind(value: object) -> str:
    if is_json_mapping(value):
        return "object"
    if is_json_list(value):
        return "array"
    if value is None:
        return "null"
    return type(value).__name__
