"""Identify explicit account-owned state that cannot safely move on retry."""

from __future__ import annotations

from collections.abc import Mapping

from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.db.models import StickySessionKind
from app.modules.proxy.affinity import _AffinityPolicy

# These item types refer to state stored upstream. Inline computer calls,
# tool results, MCP definitions and MCP call results are not references by
# themselves and must not prevent a replay.
_ACCOUNT_OWNED_ITEM_TYPES = frozenset(
    {
        "item_reference",
        "code_interpreter_call",
        "file_search_call",
        "image_generation_call",
        "mcp_approval_request",
        "mcp_approval_response",
    }
)
_SINGLE_RESOURCE_FIELDS = frozenset({"file_id", "container_id", "vector_store_id"})
_MULTI_RESOURCE_FIELDS = frozenset({"file_ids", "vector_store_ids"})


def quota_replay_block_reason(payload: ResponsesRequest) -> str | None:
    """Default to replaying the original stateless body; name known hard owners."""
    data = payload.model_dump_for_forwarding()
    if data.get("previous_response_id") not in (None, ""):
        return "previous_response_id"
    if data.get("conversation") not in (None, ""):
        return "conversation"
    if data.get("prompt") not in (None, ""):
        return "stored_prompt"

    # Inspect actual input and hosted tool references. Function parameter
    # schemas describe fields rather than referencing runtime resources.
    pending: list[JsonValue] = [data.get("input"), data.get("tools")]
    while pending:
        value = pending.pop()
        if isinstance(value, Mapping):
            item_type = value.get("type")
            if isinstance(item_type, str) and item_type in _ACCOUNT_OWNED_ITEM_TYPES:
                return "account_owned_" + item_type
            for field in _SINGLE_RESOURCE_FIELDS:
                if isinstance(value.get(field), str) and value[field]:
                    return "account_owned_" + field
            for field in _MULTI_RESOURCE_FIELDS:
                ids = value.get(field)
                if isinstance(ids, list) and any(isinstance(item, str) and item for item in ids):
                    return "account_owned_" + field
            if item_type == "input_image":
                image_url = value.get("image_url")
                if isinstance(image_url, str) and image_url.startswith("file-"):
                    return "account_owned_image"
            for key, nested in value.items():
                if key == "internal_chat_message_metadata_passthrough":
                    continue
                if (
                    isinstance(item_type, str)
                    and item_type in {"function", "custom"}
                    and key in {"parameters", "format"}
                ):
                    continue
                pending.append(nested)
        elif isinstance(value, list):
            pending.extend(value)
    return None


def quota_replay(payload: ResponsesRequest) -> ResponsesRequest | None:
    """Preserve the entire body, including opaque state and unknown item types."""
    if quota_replay_block_reason(payload) is not None:
        return None
    return payload.model_copy(deep=True)


def movable_affinity(affinity: _AffinityPolicy) -> _AffinityPolicy:
    """Use soft locality after a stateless turn may leave its old owner."""
    key = affinity.selection_key
    return _AffinityPolicy(
        key=key,
        kind=StickySessionKind.PROMPT_CACHE if key is not None else None,
        reallocate_sticky=True,
        max_age_seconds=affinity.max_age_seconds,
    )
