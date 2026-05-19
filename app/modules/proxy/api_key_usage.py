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


def estimate_api_key_request_usage(payload: ApiKeyUsageEstimableRequest) -> ApiKeyRequestUsageBudget:
    """Return a bounded local usage budget for API-key reservation admission.

    ``None`` means the proxy cannot size that side of the request locally, so
    API-key enforcement should use its conservative default for that dimension.
    """

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
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    if not serialized:
        return 0
    return min(len(serialized.encode("utf-8")), API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET)


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
