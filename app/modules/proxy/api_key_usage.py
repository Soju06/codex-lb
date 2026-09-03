from __future__ import annotations

import json
from typing import TypeAlias

from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.types import JsonObject, JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping
from app.modules.api_keys.service import (
    API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET,
    ApiKeyRequestUsageBudget,
)

ApiKeyUsageEstimableRequest: TypeAlias = ResponsesRequest | ResponsesCompactRequest

# Same serialization parameters the budget historically used with ``json.dumps``.
# ``sort_keys`` and ``default`` cannot change the serialized length, but they
# are kept so the streamed byte count stays exactly what the one-shot dump
# produced.
_BUDGET_ENCODER = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)

_OPAQUE_INPUT_ITEM_TYPES = frozenset({"input_file", "input_image"})
_INPUT_BUDGET_EXCLUDED_FIELDS = frozenset(
    {
        "model",
        "service_tier",
        "stream",
        "store",
        "max_output_tokens",
        "max_completion_tokens",
        "max_tokens",
    }
)


def estimate_api_key_request_usage(
    payload: ApiKeyUsageEstimableRequest,
    *,
    upstream_payload: JsonObject | None = None,
) -> ApiKeyRequestUsageBudget:
    """Return a bounded local usage budget for API-key reservation admission.

    ``None`` means the proxy cannot size that side of the request locally, so
    API-key enforcement should use its conservative default for that dimension.

    ``upstream_payload`` lets callers that already hold ``payload.to_payload()``
    share it instead of paying for another full dump. It must be the pristine
    ``to_payload()`` result for ``payload`` (``to_payload`` is deterministic, so
    the budget is identical either way).
    """

    if upstream_payload is None:
        upstream_payload = payload.to_payload()
    return ApiKeyRequestUsageBudget(
        input_tokens=_estimate_request_input_tokens(payload, upstream_payload),
        output_tokens=None,
    )


def _estimate_request_input_tokens(payload: ApiKeyUsageEstimableRequest, upstream_payload: JsonObject) -> int | None:
    if _has_opaque_upstream_context(upstream_payload):
        return None
    if _contains_opaque_input_reference(payload.input):
        return None

    data = _input_budget_payload(upstream_payload)
    # The budget is ``min(serialized_utf8_length, CAP)``. A JSON string literal
    # is never shorter than its Python length (escapes only add bytes), so a
    # long ``instructions`` field alone already proves the cap is reached; this
    # is the common Codex CLI shape and avoids serializing multi-hundred-KB
    # payloads to learn an 8 KiB answer.
    instructions = data.get("instructions")
    if isinstance(instructions, str) and len(instructions) >= API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET:
        return API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET
    # Otherwise stream the serialization and stop as soon as the cap is proven.
    # Only the first ~CAP bytes are ever produced, so a lone surrogate deeper in
    # the payload no longer raises ``UnicodeEncodeError`` here (the full dump
    # used to fail for a surrogate anywhere in the payload).
    serialized_bytes = 0
    for chunk in _BUDGET_ENCODER.iterencode(data):
        # A chunk's UTF-8 length is at least its character count, so a chunk
        # that alone covers the remaining budget (one large string literal)
        # proves the cap without encoding it.
        if len(chunk) >= API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET - serialized_bytes:
            return API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET
        serialized_bytes += len(chunk.encode("utf-8"))
        if serialized_bytes >= API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET:
            return API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET
    return serialized_bytes


def _input_budget_payload(payload: JsonObject) -> dict[str, JsonValue]:
    data = dict(payload.items())
    for field in _INPUT_BUDGET_EXCLUDED_FIELDS:
        data.pop(field, None)
    return data


def _has_opaque_upstream_context(payload: JsonObject) -> bool:
    return payload.get("previous_response_id") is not None or payload.get("conversation") is not None


def _contains_opaque_input_reference(value: JsonValue) -> bool:
    if is_json_mapping(value):
        item_type = value.get("type")
        if isinstance(item_type, str) and item_type in _OPAQUE_INPUT_ITEM_TYPES:
            return True
        if "file_id" in value:
            return True
        return any(_contains_opaque_input_reference(child) for child in value.values())
    if is_json_list(value):
        return any(_contains_opaque_input_reference(item) for item in value)
    return False
