from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any, Callable, cast

from app.core.config.settings import get_settings
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    continuity_fail_closed_total,
    continuity_owner_resolution_total,
)
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.request_id import get_request_id
from app.modules.proxy.affinity import (
    _extract_model_class,
    _prompt_cache_key_from_request_model,
    _sticky_key_from_session_header,
)

logger = logging.getLogger("app.modules.proxy.service")


def _service_global(name: str, fallback: Any) -> Any:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is None:
        return fallback
    return getattr(service_module, name, fallback)


def _service_get_settings() -> Any:
    return cast(Callable[[], Any], _service_global("get_settings", get_settings))()


def _maybe_log_proxy_request_shape(
    kind: str,
    payload: ResponsesRequest | ResponsesCompactRequest,
    headers: Mapping[str, str],
    *,
    sticky_kind: str | None = None,
    sticky_key_source: str | None = None,
    prompt_cache_key_set: bool | None = None,
) -> None:
    settings = _service_get_settings()
    if not settings.log_proxy_request_shape:
        return

    request_id = get_request_id()
    prompt_cache_key = _prompt_cache_key_from_request_model(payload)
    prompt_cache_key_hash = _hash_identifier(prompt_cache_key) if isinstance(prompt_cache_key, str) else None
    prompt_cache_key_raw = (
        _truncate_identifier(prompt_cache_key)
        if settings.log_proxy_request_shape_raw_cache_key and isinstance(prompt_cache_key, str)
        else None
    )

    extra_keys = sorted(payload.model_extra.keys()) if payload.model_extra else []
    fields_set = sorted(payload.model_fields_set)
    input_summary = _summarize_input(payload.input)
    header_keys = _interesting_header_keys(headers)
    session_header_present = _sticky_key_from_session_header(headers) is not None
    tools_hash = _tools_hash(payload)
    model_class = _extract_model_class(payload.model)

    logger.warning(
        "proxy_request_shape request_id=%s kind=%s model=%s stream=%s input=%s "
        "prompt_cache_key=%s prompt_cache_key_raw=%s fields=%s extra=%s headers=%s "
        "sticky_kind=%s sticky_key_source=%s prompt_cache_key_set=%s"
        " session_header_present=%s tools_hash=%s model_class=%s",
        request_id,
        kind,
        payload.model,
        getattr(payload, "stream", None),
        input_summary,
        prompt_cache_key_hash,
        prompt_cache_key_raw,
        fields_set,
        extra_keys,
        header_keys,
        sticky_kind,
        sticky_key_source,
        prompt_cache_key_set,
        session_header_present,
        tools_hash,
        model_class,
    )


def _maybe_log_proxy_request_payload(
    kind: str,
    payload: ResponsesRequest | ResponsesCompactRequest,
    headers: Mapping[str, str],
) -> None:
    settings = _service_get_settings()
    if not settings.log_proxy_request_payload:
        return

    request_id = get_request_id()
    payload_dict = payload.model_dump(mode="json", exclude_none=True)
    extra = payload.model_extra or {}
    if extra:
        payload_dict = {**payload_dict, "_extra": extra}
    header_keys = _interesting_header_keys(headers)
    payload_json = json.dumps(payload_dict, ensure_ascii=True, separators=(",", ":"))

    logger.warning(
        "proxy_request_payload request_id=%s kind=%s payload=%s headers=%s",
        request_id,
        kind,
        payload_json,
        header_keys,
    )


def _maybe_log_proxy_service_tier_trace(
    kind: str,
    *,
    requested_service_tier: str | None,
    actual_service_tier: str | None,
) -> None:
    settings = _service_get_settings()
    if not settings.log_proxy_service_tier_trace:
        return

    logger.warning(
        "proxy_service_tier_trace request_id=%s kind=%s requested_service_tier=%s actual_service_tier=%s",
        get_request_id(),
        kind,
        requested_service_tier,
        actual_service_tier,
    )


def _hash_identifier_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return _hash_identifier(stripped)


def _record_continuity_owner_resolution(
    *,
    surface: str,
    source: str,
    outcome: str,
    previous_response_id: str | None,
    session_id: str | None,
) -> None:
    prometheus_available = bool(_service_global("PROMETHEUS_AVAILABLE", PROMETHEUS_AVAILABLE))
    counter = _service_global("continuity_owner_resolution_total", continuity_owner_resolution_total)
    if prometheus_available and counter is not None:
        counter.labels(
            surface=surface,
            source=source,
            outcome=outcome,
        ).inc()
    if outcome == "miss" or (outcome == "hit" and source == "request_cache"):
        return
    logger.log(
        logging.WARNING if outcome == "fail_closed" else logging.INFO,
        "continuity_owner_resolution surface=%s source=%s outcome=%s previous_response_id=%s session_id=%s",
        surface,
        source,
        outcome,
        _hash_identifier_or_none(previous_response_id),
        _hash_identifier_or_none(session_id),
    )


def _record_continuity_fail_closed(
    *,
    surface: str,
    reason: str,
    previous_response_id: str | None,
    session_id: str | None = None,
    upstream_error_code: str | None = None,
) -> None:
    prometheus_available = bool(_service_global("PROMETHEUS_AVAILABLE", PROMETHEUS_AVAILABLE))
    counter = _service_global("continuity_fail_closed_total", continuity_fail_closed_total)
    if prometheus_available and counter is not None:
        counter.labels(
            surface=surface,
            reason=reason,
        ).inc()
    logger.warning(
        "continuity_fail_closed surface=%s reason=%s previous_response_id=%s session_id=%s upstream_error_code=%s",
        surface,
        reason,
        _hash_identifier_or_none(previous_response_id),
        _hash_identifier_or_none(session_id),
        upstream_error_code,
    )


def _hash_identifier(value: str) -> str:
    digest = sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def _summarize_input(items: JsonValue) -> str:
    if items is None:
        return "0"
    if isinstance(items, str):
        return "str"
    if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
        if not items:
            return "0"
        type_counts: dict[str, int] = {}
        for item in items:
            type_name = type(item).__name__
            type_counts[type_name] = type_counts.get(type_name, 0) + 1
        summary = ",".join(f"{key}={type_counts[key]}" for key in sorted(type_counts))
        return f"{len(items)}({summary})"
    return type(items).__name__


def _truncate_identifier(value: str, *, max_length: int = 96) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[:48]}...{value[-16:]}"


def _tools_hash(payload: ResponsesRequest | ResponsesCompactRequest) -> str | None:
    payload_tools = payload.to_payload().get("tools")
    if not isinstance(payload_tools, list) or not payload_tools:
        return None
    serialized = json.dumps(payload_tools, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return _hash_identifier(serialized)


def _interesting_header_keys(headers: Mapping[str, str]) -> list[str]:
    allowlist = {
        "user-agent",
        "x-request-id",
        "request-id",
        "session_id",
        "x-openai-client-id",
        "x-openai-client-version",
        "x-openai-client-arch",
        "x-openai-client-os",
        "x-openai-client-user-agent",
        "x-codex-session-id",
        "x-codex-conversation-id",
    }
    return sorted({key.lower() for key in headers.keys() if key.lower() in allowlist})
