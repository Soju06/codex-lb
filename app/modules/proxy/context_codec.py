"""Carry native encrypted history partitions through Codex's single-result slot."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Literal
from uuid import UUID

from cryptography.fernet import InvalidToken
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.clients.proxy import ProxyResponseError
from app.core.crypto import TokenEncryptor
from app.core.errors import openai_error
from app.core.types import JsonValue
from app.modules.api_keys.service import ApiKeyData

PREFIX = "codex-lb-context-v1:"
MAX_CONTEXT_BYTES = 2_000_000
MAX_HISTORY_ACCOUNTS = 32


def context_error(code: str, status: int = 409) -> ProxyResponseError:
    return ProxyResponseError(
        status, openai_error(code, "Codex context is unavailable", error_type="invalid_request_error")
    )


def context_session_id(value: JsonValue | None) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return value if str(UUID(value)) == value else None
    except ValueError:
        return None


class HistoryPartition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    result: dict[str, JsonValue]


class HistoryEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key_id: str
    session_id: str
    kind: Literal["notes", "history"] = "history"
    partitions: list[HistoryPartition]


def pack_history(
    api_key_id: str,
    session_id: str,
    partitions: Sequence[HistoryPartition],
    *,
    kind: Literal["notes", "history"] = "history",
) -> bytes:
    envelope = HistoryEnvelope(api_key_id=api_key_id, session_id=session_id, partitions=list(partitions), kind=kind)
    serialized = envelope.model_dump_json()
    if len(serialized.encode()) > MAX_CONTEXT_BYTES or not 1 <= len(partitions) <= MAX_HISTORY_ACCOUNTS:
        raise context_error("context_result_too_large", 502)
    token = PREFIX + TokenEncryptor().encrypt(serialized).decode()
    return json.dumps({"encrypted_output": token}, separators=(",", ":")).encode()


def _unpack_history(token: str, api_key: ApiKeyData | None, session_id: str | None) -> list[JsonValue]:
    if len(token) > MAX_CONTEXT_BYTES * 2 or api_key is None:
        raise context_error("context_result_invalid", 400)
    try:
        envelope = HistoryEnvelope.model_validate_json(TokenEncryptor().decrypt(token[len(PREFIX) :].encode()))
    except (InvalidToken, UnicodeError, ValidationError, ValueError):
        raise context_error("context_result_invalid", 400) from None
    if envelope.api_key_id != api_key.id or envelope.session_id != session_id:
        raise context_error("context_scope_mismatch", 403)
    if not 1 <= len(envelope.partitions) <= MAX_HISTORY_ACCOUNTS:
        raise context_error("context_result_invalid", 400)
    content: list[JsonValue] = [
        {
            "type": "input_text",
            "text": "The following are independent history partitions for the same task. Combine their results, "
            "deduplicate matching item/window IDs, and apply the requested ordering and limit across them. "
            "An item absent in one partition may be present in another.",
        }
    ]
    if envelope.kind == "notes" or len(envelope.partitions) == 1:
        content = []
    for index, partition in enumerate(envelope.partitions):
        if api_key.account_assignment_scope_enabled and partition.account_id not in api_key.assigned_account_ids:
            raise context_error("context_scope_mismatch", 403)
        if len(envelope.partitions) > 1:
            content.append({"type": "input_text", "text": f"History partition {index + 1}:"})
        result = dict(partition.result)
        images = result.pop("images", [])
        encrypted = result.get("encrypted_output")
        if isinstance(encrypted, str):
            content.append({"type": "encrypted_content", "encrypted_content": encrypted})
        else:
            content.append({"type": "input_text", "text": json.dumps(result, separators=(",", ":"))})
        if not isinstance(images, list):
            raise context_error("context_result_invalid", 400)
        for image in images:
            if (
                not isinstance(image, dict)
                or not isinstance(image.get("data"), str)
                or not isinstance(image.get("mime_type"), str)
            ):
                raise context_error("context_result_invalid", 400)
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{image['mime_type']};base64,{image['data']}",
                    "detail": image.get("detail"),
                }
            )
    return content


def expand_history_input(
    input_value: JsonValue, metadata: JsonValue | None, api_key: ApiKeyData | None, *, trusted: set[str] | None = None
) -> JsonValue:
    if not isinstance(input_value, list):
        return input_value
    session_id = context_session_id(metadata.get("session_id")) if isinstance(metadata, Mapping) else None
    items: list[JsonValue] = []
    for item in input_value:
        if (
            not isinstance(item, dict)
            or item.get("type") != "function_call_output"
            or not isinstance(item.get("output"), list)
        ):
            items.append(item)
            continue
        parts: list[JsonValue] = []
        output = item["output"]
        assert isinstance(output, list)
        for part in output:
            token = part.get("encrypted_content") if isinstance(part, dict) else None
            if (
                isinstance(part, dict)
                and part.get("type") == "encrypted_content"
                and isinstance(token, str)
                and token.startswith(PREFIX)
            ):
                expanded = _unpack_history(token, api_key, session_id)
                if trusted is not None:
                    for content_part in expanded:
                        if isinstance(content_part, dict) and content_part.get("type") == "encrypted_content":
                            ciphertext = content_part.get("encrypted_content")
                            if isinstance(ciphertext, str):
                                trusted.add(hashlib.sha256(ciphertext.encode()).hexdigest())
                parts.extend(expanded)
            else:
                parts.append(part)
        items.append({**item, "output": parts})
    return items
