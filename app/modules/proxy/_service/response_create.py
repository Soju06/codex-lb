from __future__ import annotations

import gzip
import json
import logging
import re
import sys
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, TypeVar, cast
from uuid import uuid4

from app.core.clients.proxy import (
    CODEX_INSTALLATION_ID_HEADER,
    ImageFetchSession,
    ProxyResponseError,
    _inline_content_images,
    _normalize_responses_lite_websocket_client_metadata,
    apply_codex_installation_metadata,
)
from app.core.config.settings import DEFAULT_HOME_DIR, get_settings
from app.core.errors import OpenAIErrorEnvelope, openai_error
from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.modules.proxy._service.support import (
    _PENDING_TOOL_CALL_ITEM_TYPES,
    _PENDING_TOOL_CALL_OUTPUT_ITEM_TYPE_BY_CALL_TYPE,
    _PENDING_TOOL_CALL_OUTPUT_ITEM_TYPES,
    _WebSocketRequestState,
)

logger = logging.getLogger("app.modules.proxy.service")
T = TypeVar("T")

_UPSTREAM_RESPONSE_CREATE_MAX_BYTES = get_settings().upstream_response_create_max_bytes
_UPSTREAM_RESPONSE_CREATE_WARN_BYTES = int(_UPSTREAM_RESPONSE_CREATE_MAX_BYTES * 0.8)
_OVERSIZED_RESPONSE_CREATE_LARGEST_ITEMS = 10
_RESPONSE_CREATE_HISTORY_OMISSION_NOTICE = (
    "[codex-lb omitted {count} historical input items to fit upstream websocket budget]"
)
_RESPONSE_CREATE_TOOL_OUTPUT_OMISSION_NOTICE = (
    "[codex-lb omitted historical tool output ({bytes} bytes) to fit upstream websocket budget]"
)
_RESPONSE_CREATE_IMAGE_OMISSION_NOTICE = "[codex-lb omitted historical inline image to fit upstream websocket budget]"
_OVERSIZED_RESPONSE_CREATE_DUMP_DIR: Path | None = None
_RESPONSE_CREATE_COMPATIBILITY_METADATA_HEADERS = (
    "x-codex-turn-metadata",
    "x-openai-subagent",
    "x-codex-parent-thread-id",
    "x-codex-window-id",
)


def _service_module() -> Any | None:
    return sys.modules.get("app.modules.proxy.service")


def _service_global_or(name: str, fallback: T) -> T:
    service_module = _service_module()
    if service_module is None:
        return fallback
    return cast(T, getattr(service_module, name, fallback))


def _upstream_response_create_max_bytes() -> int:
    return int(_service_global_or("_UPSTREAM_RESPONSE_CREATE_MAX_BYTES", _UPSTREAM_RESPONSE_CREATE_MAX_BYTES))


def _upstream_response_create_warn_bytes() -> int:
    fallback = int(_upstream_response_create_max_bytes() * 0.8)
    return int(_service_global_or("_UPSTREAM_RESPONSE_CREATE_WARN_BYTES", fallback))


def _oversized_response_create_largest_items() -> int:
    return int(
        _service_global_or(
            "_OVERSIZED_RESPONSE_CREATE_LARGEST_ITEMS",
            _OVERSIZED_RESPONSE_CREATE_LARGEST_ITEMS,
        )
    )


def _oversized_response_create_dump_dir() -> Path:
    configured_dir = _service_global_or("_OVERSIZED_RESPONSE_CREATE_DUMP_DIR", _OVERSIZED_RESPONSE_CREATE_DUMP_DIR)
    if configured_dir is not None:
        return configured_dir
    settings_factory = _service_global_or("get_settings", get_settings)
    data_dir = getattr(settings_factory(), "data_dir", DEFAULT_HOME_DIR)
    return data_dir / "debug" / "response-create-dumps"


def _fingerprint_input_items(items: Sequence[JsonValue]) -> str:
    """Return stable SHA-256 fingerprint for input list canonical JSON."""
    canonical = json.dumps(list(items), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _input_part_is_image(part: JsonValue) -> bool:
    return is_json_mapping(part) and part.get("type") == "input_image"


def _json_value_contains_input_image_part(value: JsonValue) -> bool:
    if _input_part_is_image(value):
        return True
    if isinstance(value, list):
        return any(_json_value_contains_input_image_part(item) for item in value)
    if is_json_mapping(value):
        return any(_json_value_contains_input_image_part(child) for child in value.values())
    return False


def _responses_request_contains_input_image(payload: ResponsesRequest) -> bool:
    """Return whether a Responses request carries any ``input_image`` part."""
    input_value = payload.input
    if not isinstance(input_value, list):
        return False
    return any(_json_value_contains_input_image_part(item) for item in input_value)


def _responses_request_uses_image_generation(payload: ResponsesRequest) -> bool:
    tools = payload.tools
    if not isinstance(tools, list):
        return False
    return any(is_json_mapping(tool) and tool.get("type") == "image_generation" for tool in tools)


def _response_create_text(
    payload: ResponsesRequest,
    *,
    include_type_field: bool,
    client_metadata: Mapping[str, JsonValue] | None,
) -> str:
    upstream_payload = dict(payload.to_payload())
    upstream_payload.pop("stream", None)
    upstream_payload.pop("background", None)
    if include_type_field:
        upstream_payload["type"] = "response.create"
    if client_metadata:
        upstream_payload["client_metadata"] = client_metadata
    return json.dumps(upstream_payload, ensure_ascii=True, separators=(",", ":"))


def _response_create_text_with_size_guard(
    payload: ResponsesRequest,
    *,
    include_type_field: bool,
    client_metadata: Mapping[str, JsonValue] | None,
    request_state: _WebSocketRequestState,
    transport: str,
) -> str | None:
    upstream_payload = dict(payload.to_payload())
    upstream_payload.pop("stream", None)
    upstream_payload.pop("background", None)
    if include_type_field:
        upstream_payload["type"] = "response.create"
    if client_metadata:
        upstream_payload["client_metadata"] = client_metadata
    text_data = json.dumps(upstream_payload, ensure_ascii=True, separators=(",", ":"))
    payload_size = len(text_data.encode("utf-8"))
    max_bytes = _upstream_response_create_max_bytes()
    if payload_size > max_bytes:
        original_payload_size = payload_size
        slim_payload_for_upstream = _service_global_or(
            "_slim_response_create_payload_for_upstream",
            _slim_response_create_payload_for_upstream,
        )
        slimmed_payload, slim_summary = slim_payload_for_upstream(
            upstream_payload,
            max_bytes=max_bytes,
        )
        if slim_summary is not None:
            upstream_payload = slimmed_payload
            text_data = json.dumps(upstream_payload, ensure_ascii=True, separators=(",", ":"))
            payload_size = len(text_data.encode("utf-8"))
            logger.warning(
                (
                    "Slimmed response.create request_id=%s request_log_id=%s transport=%s "
                    "original_bytes=%s slimmed_bytes=%s "
                    "historical_tool_outputs_slimmed=%s historical_images_slimmed=%s"
                ),
                request_state.request_id,
                request_state.request_log_id,
                transport,
                original_payload_size,
                payload_size,
                slim_summary["historical_tool_outputs_slimmed"],
                slim_summary["historical_images_slimmed"],
            )
        if payload_size > max_bytes:
            logger.warning(
                (
                    "Skipping oversized response.create retry body request_id=%s request_log_id=%s "
                    "transport=%s bytes=%s max_bytes=%s"
                ),
                request_state.request_id,
                request_state.request_log_id,
                transport,
                payload_size,
                max_bytes,
            )
            return None
    return text_data


def _response_create_text_with_account_installation_id(
    text_data: str,
    *,
    account: Any,
) -> str:
    installation_id = getattr(account, "codex_installation_id", None)
    if not isinstance(installation_id, str) or not installation_id.strip():
        installation_id = str(uuid4())
        try:
            setattr(account, "codex_installation_id", installation_id)
        except Exception:
            pass

    try:
        raw_payload = json.loads(text_data)
    except json.JSONDecodeError:
        return text_data
    if not isinstance(raw_payload, dict) or raw_payload.get("type") != "response.create":
        return text_data
    upstream_payload = cast(dict[str, JsonValue], raw_payload)

    apply_codex_installation_metadata(upstream_payload, installation_id)
    return json.dumps(upstream_payload, ensure_ascii=True, separators=(",", ":"))


def _slim_response_create_payload_for_upstream(
    payload: dict[str, JsonValue],
    *,
    max_bytes: int,
) -> tuple[dict[str, JsonValue], dict[str, int] | None]:
    input_value = payload.get("input")
    if not isinstance(input_value, list) or not input_value:
        return payload, None

    input_items = cast(list[JsonValue], deepcopy(input_value))
    preserve_from = _response_create_recent_suffix_start(input_items)
    historical = input_items[:preserve_from]
    recent = input_items[preserve_from:]

    tool_outputs_slimmed = 0
    images_slimmed = 0

    slimmed_historical: list[JsonValue] = []
    for item in historical:
        (
            slimmed_item,
            item_tool_outputs_slimmed,
            item_images_slimmed,
        ) = _slim_historical_response_input_item(item)
        tool_outputs_slimmed += item_tool_outputs_slimmed
        images_slimmed += item_images_slimmed
        slimmed_historical.append(slimmed_item)

    candidate_payload = dict(payload)
    candidate_payload["input"] = slimmed_historical + recent

    if tool_outputs_slimmed == 0 and images_slimmed == 0:
        return payload, None

    return candidate_payload, {
        "historical_tool_outputs_slimmed": tool_outputs_slimmed,
        "historical_images_slimmed": images_slimmed,
    }


def _function_call_output_call_ids(input_items: list[JsonValue]) -> set[str]:
    call_ids: set[str] = set()
    for item in input_items:
        if not isinstance(item, dict) or item.get("type") not in _PENDING_TOOL_CALL_OUTPUT_ITEM_TYPES:
            continue
        call_id = item.get("call_id")
        if isinstance(call_id, str) and call_id:
            call_ids.add(call_id)
    return call_ids


def _missing_function_call_outputs_for_previous_response(
    input_items: list[JsonValue],
    *,
    pending_call_ids: list[str],
) -> list[str]:
    if not pending_call_ids:
        return []
    present_call_ids = _function_call_output_call_ids(input_items)
    return [call_id for call_id in pending_call_ids if call_id not in present_call_ids]


def _synthetic_interrupted_function_call_output(
    call_id: str,
    *,
    call_type: str = "function_call",
) -> dict[str, JsonValue]:
    output_type = _PENDING_TOOL_CALL_OUTPUT_ITEM_TYPE_BY_CALL_TYPE.get(call_type, "function_call_output")
    item: dict[str, JsonValue] = {
        "type": output_type,
        "call_id": call_id,
        "output": (
            "Tool call was not executed because the previous turn was interrupted before tool output was available."
        ),
    }
    if output_type == "apply_patch_call_output":
        item["status"] = "failed"
    return item


def _inject_missing_interrupted_function_call_outputs(
    input_items: list[JsonValue],
    *,
    missing_call_ids: list[str],
    pending_call_types: Mapping[str, str] | None = None,
) -> list[JsonValue]:
    if not missing_call_ids:
        return input_items
    call_types = pending_call_types or {}
    return [
        *[
            _synthetic_interrupted_function_call_output(
                call_id,
                call_type=call_types.get(call_id, "function_call"),
            )
            for call_id in missing_call_ids
        ],
        *input_items,
    ]


def _response_output_item_done_tool_call(payload: dict[str, JsonValue] | None) -> tuple[str, str] | None:
    """Return ``(call_id, item_type)`` for a completed tool-call output item.

    Covers every tool-call item type that upstream requires a matching output
    item for on the next anchored request: ``function_call``,
    ``custom_tool_call``, and ``apply_patch_call``.
    """
    if not isinstance(payload, dict) or payload.get("type") != "response.output_item.done":
        return None
    item = payload.get("item")
    if not isinstance(item, dict):
        return None
    item_type = item.get("type")
    if not isinstance(item_type, str) or item_type not in _PENDING_TOOL_CALL_ITEM_TYPES:
        return None
    call_id = item.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        return None
    return call_id, item_type


def _response_create_too_large_error_envelope(
    actual_bytes: int,
    max_bytes: int,
) -> OpenAIErrorEnvelope:
    payload = openai_error(
        "payload_too_large",
        (
            "response.create is too large for upstream websocket "
            f"({actual_bytes} bytes > {max_bytes} bytes). "
            "Reduce historical images/screenshots or compact the thread."
        ),
        error_type="invalid_request_error",
    )
    payload["error"]["param"] = "input"
    return payload


def _response_create_recent_suffix_start(input_items: list[JsonValue]) -> int:
    last_user_index: int | None = None
    for index, item in enumerate(input_items):
        if not is_json_mapping(item):
            continue
        if item.get("role") == "user":
            last_user_index = index
    if last_user_index is not None:
        return last_user_index
    return 0


def _slim_historical_response_input_item(item: JsonValue) -> tuple[JsonValue, int, int]:
    if not is_json_mapping(item):
        return item, 0, 0

    item_mapping = dict(cast(dict[str, JsonValue], deepcopy(item)))
    tool_outputs_slimmed = 0
    images_slimmed = 0

    item_type = item_mapping.get("type")
    if isinstance(item_type, str) and item_type in _PENDING_TOOL_CALL_OUTPUT_ITEM_TYPES:
        output = item_mapping.get("output")
        if isinstance(output, str):
            if _should_slim_historical_tool_output(output):
                item_mapping["output"] = _RESPONSE_CREATE_TOOL_OUTPUT_OMISSION_NOTICE.format(
                    bytes=len(output.encode("utf-8"))
                )
                tool_outputs_slimmed += 1
        else:
            slimmed_output, output_images_slimmed = _slim_historical_response_content(output)
            if output_images_slimmed > 0:
                item_mapping["output"] = slimmed_output
                images_slimmed += output_images_slimmed

    content = item_mapping.get("content")
    slimmed_content, content_images_slimmed = _slim_historical_response_content(content)
    if content_images_slimmed > 0:
        item_mapping["content"] = slimmed_content
        images_slimmed += content_images_slimmed

    if item_type == "input_image" and _is_inline_image_reference(item_mapping.get("image_url")):
        return _response_create_inline_image_notice_item(), tool_outputs_slimmed, images_slimmed + 1

    return item_mapping, tool_outputs_slimmed, images_slimmed


def _slim_historical_response_content(content: JsonValue) -> tuple[JsonValue, int]:
    if is_json_mapping(content):
        return _slim_historical_response_content_part(content)
    if not isinstance(content, list):
        return content, 0

    slimmed_parts: list[JsonValue] = []
    images_slimmed = 0
    for part in content:
        slimmed_part, part_images_slimmed = _slim_historical_response_content_part(part)
        slimmed_parts.append(slimmed_part)
        images_slimmed += part_images_slimmed
    return slimmed_parts, images_slimmed


def _slim_historical_response_content_part(part: JsonValue) -> tuple[JsonValue, int]:
    if not is_json_mapping(part):
        return part, 0

    part_mapping = dict(cast(dict[str, JsonValue], deepcopy(part)))
    part_type = part_mapping.get("type")
    if part_type == "input_image" and _is_inline_image_reference(part_mapping.get("image_url")):
        return _response_create_inline_image_notice_part(), 1

    if part_type == "image_url":
        image_url_value = part_mapping.get("image_url")
        if is_json_mapping(image_url_value):
            image_url = image_url_value.get("url")
        else:
            image_url = image_url_value
        if _is_inline_image_reference(image_url):
            return _response_create_inline_image_notice_part(), 1

    return part_mapping, 0


def _response_create_inline_image_notice_part() -> dict[str, JsonValue]:
    return {"type": "input_text", "text": _RESPONSE_CREATE_IMAGE_OMISSION_NOTICE}


def _response_create_inline_image_notice_item() -> dict[str, JsonValue]:
    return {"role": "user", "content": [_response_create_inline_image_notice_part()]}


def _response_create_history_omission_notice_item(count: int) -> dict[str, JsonValue]:
    return {
        "role": "assistant",
        "content": [
            {
                "type": "output_text",
                "text": _RESPONSE_CREATE_HISTORY_OMISSION_NOTICE.format(count=count),
            }
        ],
    }


def _is_inline_image_reference(value: JsonValue) -> bool:
    return isinstance(value, str) and value.startswith("data:image/")


async def _inline_top_level_input_image_urls(
    payload: dict[str, JsonValue],
    session: ImageFetchSession,
    connect_timeout: float,
) -> dict[str, JsonValue]:
    input_value = payload.get("input")
    if not isinstance(input_value, list):
        return payload

    updated_input: list[JsonValue] = []
    changed = False
    for item in input_value:
        if not isinstance(item, dict) or item.get("type") != "input_image":
            updated_input.append(item)
            continue
        inline_content_images = _service_global_or("_inline_content_images", _inline_content_images)
        updated_item, item_changed = await inline_content_images(item, session, connect_timeout)
        updated_input.append(updated_item)
        changed = changed or item_changed
    if not changed:
        return payload

    updated_payload = dict(payload)
    updated_payload["input"] = updated_input
    return updated_payload


def _count_external_image_urls(payload: dict[str, JsonValue]) -> int:
    """Count input_image items that still reference an external (non data:) URL."""
    input_value = payload.get("input")
    if not isinstance(input_value, list):
        return 0
    count = 0
    for item in input_value:
        if not isinstance(item, dict):
            continue
        content_value = item.get("content")
        content_parts = content_value if isinstance(content_value, list) else [content_value]
        if item.get("type") == "input_image":
            content_parts = [item, *content_parts]
        for part in content_parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") != "input_image":
                continue
            image_url = part.get("image_url")
            if isinstance(image_url, str) and image_url.startswith(("http://", "https://")):
                count += 1
    return count


def _should_slim_historical_tool_output(output: str) -> bool:
    return "data:image/" in output or len(output.encode("utf-8")) > 32 * 1024


def _enforce_response_create_size_limit(request_state: _WebSocketRequestState) -> None:
    request_text = request_state.request_text
    if not request_text:
        return

    payload_bytes = request_text.encode("utf-8")
    payload_size = len(payload_bytes)
    warn_bytes = _upstream_response_create_warn_bytes()
    max_bytes = _upstream_response_create_max_bytes()
    if payload_size > warn_bytes:
        logger.warning(
            (
                "Large response.create prepared request_id=%s request_log_id=%s "
                "transport=%s bytes=%s previous_response_id=%s"
            ),
            request_state.request_id,
            request_state.request_log_id,
            request_state.transport,
            payload_size,
            request_state.previous_response_id,
        )
    if payload_size <= max_bytes:
        return

    payload = _response_create_too_large_error_envelope(payload_size, max_bytes)
    error = payload["error"]
    write_response_create_dump = _service_global_or("_write_response_create_dump", _write_response_create_dump)
    write_response_create_dump(
        request_state,
        account_id_value=None,
        error_code=cast(str, error.get("code") or "payload_too_large"),
        error_message=error.get("message"),
        log_prefix="guarded",
    )
    # 400, not 413: the Codex client surfaces 400 immediately as a non-retryable
    # invalid request, while 413 burns five full-payload retries and then pins the
    # session to HTTP transport.
    raise ProxyResponseError(
        400,
        payload,
        failure_phase="validation",
        failure_detail=f"response.create_bytes={payload_size}",
    )


def _maybe_dump_oversized_response_create_request(
    request_state: _WebSocketRequestState,
    *,
    account_id_value: str | None,
    error_code: str,
    error_message: str | None,
) -> None:
    should_dump_oversized_response_create = _service_global_or(
        "_should_dump_oversized_response_create",
        _should_dump_oversized_response_create,
    )
    if not should_dump_oversized_response_create(error_code, error_message):
        return
    write_response_create_dump = _service_global_or("_write_response_create_dump", _write_response_create_dump)
    write_response_create_dump(
        request_state,
        account_id_value=account_id_value,
        error_code=error_code,
        error_message=error_message,
        log_prefix="oversized",
    )


def _write_response_create_dump(
    request_state: _WebSocketRequestState,
    *,
    account_id_value: str | None,
    error_code: str,
    error_message: str | None,
    log_prefix: str,
) -> bool:
    request_text = request_state.request_text
    if not request_text:
        return False

    payload_bytes = request_text.encode("utf-8")
    request_sha = sha256(payload_bytes).hexdigest()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    dump_id = "-".join(
        (
            timestamp,
            _safe_dump_slug(request_state.transport, fallback="transport"),
            _safe_dump_slug(request_state.model, fallback="model"),
            _safe_dump_slug(
                request_state.request_log_id or request_state.response_id or request_state.request_id,
                fallback="request",
            ),
        )
    )
    dump_dir = _oversized_response_create_dump_dir()
    dump_path = dump_dir / f"{dump_id}.response-create.json.gz"
    meta_path = dump_dir / f"{dump_id}.meta.json"

    meta: dict[str, JsonValue] = {
        "dump_id": dump_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "reason": {
            "error_code": error_code,
            "error_message": error_message,
        },
        "request": {
            "account_id": account_id_value,
            "request_id": request_state.request_id,
            "request_log_id": request_state.request_log_id,
            "response_id": request_state.response_id,
            "transport": request_state.transport,
            "model": request_state.model,
            "reasoning_effort": request_state.reasoning_effort,
            "service_tier": request_state.service_tier,
            "requested_service_tier": request_state.requested_service_tier,
            "actual_service_tier": request_state.actual_service_tier,
            "previous_response_id": request_state.previous_response_id,
            "awaiting_response_created": request_state.awaiting_response_created,
            "replay_count": request_state.replay_count,
            "request_text_bytes": len(payload_bytes),
            "request_text_chars": len(request_text),
            "request_text_sha256": request_sha,
        },
        "paths": {
            "dump_path": str(dump_path),
            "meta_path": str(meta_path),
        },
    }

    try:
        parsed_payload = json.loads(request_text)
    except json.JSONDecodeError as exc:
        meta["parse_error"] = str(exc)
    else:
        if isinstance(parsed_payload, dict):
            meta["summary"] = _summarize_response_create_payload(parsed_payload)
        else:
            meta["summary"] = {"payload_type": type(parsed_payload).__name__}

    try:
        dump_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(dump_path, "wt", encoding="utf-8") as handle:
            handle.write(request_text)
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        logger.exception(
            "Failed to dump %s response.create payload request_id=%s request_log_id=%s",
            log_prefix,
            request_state.request_id,
            request_state.request_log_id,
        )
        return False

    logger.warning(
        "Saved %s response.create dump request_id=%s request_log_id=%s dump_path=%s meta_path=%s bytes=%s",
        log_prefix,
        request_state.request_id,
        request_state.request_log_id,
        dump_path,
        meta_path,
        len(payload_bytes),
    )
    return True


def _should_dump_oversized_response_create(error_code: str, error_message: str | None) -> bool:
    if error_code != "stream_incomplete" or not error_message:
        return False
    normalized = error_message.lower()
    return "1009" in normalized or "message too big" in normalized


def _safe_dump_slug(value: str | None, *, fallback: str) -> str:
    if not value:
        return fallback
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    if not normalized:
        return fallback
    return normalized[:80]


def _summarize_response_create_payload(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    field_sizes = sorted(
        (
            {
                "key": key,
                "size_bytes": _json_size_bytes(value),
            }
            for key, value in payload.items()
        ),
        key=lambda item: int(item["size_bytes"]),
        reverse=True,
    )
    summary: dict[str, JsonValue] = {
        "top_level_keys": list(payload.keys()),
        "top_level_field_sizes": cast(JsonValue, field_sizes),
    }
    input_summary = _summarize_response_create_input(payload.get("input"))
    if input_summary is not None:
        summary["input"] = input_summary
    return summary


def _summarize_response_create_input(input_value: JsonValue) -> dict[str, JsonValue] | None:
    if not isinstance(input_value, list):
        return None

    input_items = cast(list[JsonValue], input_value)
    role_counts: dict[str, int] = {}
    item_type_counts: dict[str, int] = {}
    content_part_type_counts: dict[str, int] = {}
    largest_items: list[dict[str, JsonValue]] = []

    for index, item in enumerate(input_items):
        item_summary: dict[str, JsonValue] = {
            "index": index,
            "size_bytes": _json_size_bytes(item),
        }
        if isinstance(item, dict):
            item_object = cast(dict[str, JsonValue], item)
            role = item_object.get("role")
            if isinstance(role, str):
                item_summary["role"] = role
                role_counts[role] = role_counts.get(role, 0) + 1
            item_type = item_object.get("type")
            if isinstance(item_type, str):
                item_summary["type"] = item_type
                item_type_counts[item_type] = item_type_counts.get(item_type, 0) + 1
            content = item_object.get("content")
            if isinstance(content, list):
                item_summary["content_parts"] = len(content)
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    part_object = cast(dict[str, JsonValue], part)
                    part_type = part_object.get("type")
                    if isinstance(part_type, str):
                        content_part_type_counts[part_type] = content_part_type_counts.get(part_type, 0) + 1
        largest_items.append(item_summary)

    largest_items.sort(key=lambda item: cast(int, item["size_bytes"]), reverse=True)
    summary: dict[str, JsonValue] = {
        "count": len(input_value),
        "role_counts": cast(JsonValue, role_counts),
        "item_type_counts": cast(JsonValue, item_type_counts),
        "content_part_type_counts": cast(JsonValue, content_part_type_counts),
        "largest_items": cast(JsonValue, largest_items[: _oversized_response_create_largest_items()]),
    }
    return summary


def _json_size_bytes(value: JsonValue) -> int:
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))


def _response_create_client_metadata(
    payload: Mapping[str, JsonValue],
    *,
    headers: Mapping[str, str],
    codex_installation_id: str | None = None,
    preserve_existing_responses_lite: bool = False,
) -> Mapping[str, JsonValue] | None:
    raw_value = payload.get("client_metadata")
    client_metadata: dict[str, JsonValue] = {}
    if is_json_mapping(raw_value):
        for key, value in raw_value.items():
            if isinstance(key, str) and key.lower() != CODEX_INSTALLATION_ID_HEADER:
                client_metadata[key] = value

    normalized_headers = {key.lower(): value for key, value in headers.items()}
    for header_name in _RESPONSE_CREATE_COMPATIBILITY_METADATA_HEADERS:
        metadata_value = normalized_headers.get(header_name)
        if isinstance(metadata_value, str) and metadata_value.strip():
            client_metadata.setdefault(header_name, metadata_value)

    metadata_container: dict[str, JsonValue] = {"client_metadata": client_metadata}
    apply_codex_installation_metadata(metadata_container, codex_installation_id)
    normalized_metadata = metadata_container.get("client_metadata")
    client_metadata = dict(normalized_metadata) if is_json_mapping(normalized_metadata) else {}
    client_metadata = _normalize_responses_lite_websocket_client_metadata(
        payload,
        client_metadata,
        preserve_existing=preserve_existing_responses_lite,
    )
    return client_metadata or None
