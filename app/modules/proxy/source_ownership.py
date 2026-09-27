"""Scoped direct-source references, published durably before client delivery."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta

from app.core.clock import REAL_SCHEDULER, Scheduler
from app.core.types import JsonValue
from app.core.utils.shared_future import _await_result_deferring_cancellation
from app.core.utils.sse import parse_sse_data_json
from app.core.utils.time import utcnow
from app.db.models import ModelSource
from app.db.session import get_background_session, sqlite_writer_section
from app.modules.model_sources.catalog import source_model_upstream_id
from app.modules.model_sources.forwarding import ModelSourceForwardingError
from app.modules.model_sources.ownership_repository import SourceOwnershipRepository
from app.modules.proxy.replay_safety import (
    client_message_id_is_account_neutral,
    client_tool_output_id_is_account_neutral,
    inline_agent_message_is_source_neutral,
    self_contained_tool_call_ids,
    standalone_function_output_is_account_neutral,
)

OWNERSHIP_TTL = timedelta(days=30)
MAX_OWNED_REFERENCES = 2048


class SourceOwnershipError(ModelSourceForwardingError):
    def __init__(self) -> None:
        super().__init__(
            status_code=502,
            payload={
                "error": {
                    "code": "model_source_ownership_unavailable",
                    "type": "upstream_error",
                    "message": "Unable to record model-source continuity before delivery",
                }
            },
        )


def source_revision(source: ModelSource, model: str) -> str:
    identity = json.dumps([source.base_url, source_model_upstream_id(source, model)], separators=(",", ":"))
    return hashlib.sha256(identity.encode() + b"\0" + (source.api_key_encrypted or b"")).hexdigest()


@dataclass(frozen=True, slots=True)
class OwnershipScope:
    api_key_id: str | None
    model: str

    def key(self, kind: str, value: str) -> str:
        encoded = json.dumps([self.api_key_id, self.model, kind, value], ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    def request_keys(self, payload: Mapping[str, JsonValue]) -> set[str]:
        keys: set[str] = set()
        self._add(keys, "response", payload.get("previous_response_id"))
        conversation = payload.get("conversation")
        self._add(keys, "conversation", conversation.get("id") if isinstance(conversation, dict) else conversation)
        prompt = payload.get("prompt")
        if isinstance(prompt, dict):
            self._add(keys, "prompt", prompt.get("id"))
        tools = payload.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                if isinstance(tool, dict) and tool.get("type") == "code_interpreter":
                    self._add(keys, "container", tool.get("container"))
                if isinstance(tool, dict) and tool.get("type") == "file_search":
                    vector_store_ids = tool.get("vector_store_ids")
                    if isinstance(vector_store_ids, list):
                        for vector_store_id in vector_store_ids:
                            self._add(keys, "vector_store", vector_store_id)
        items = payload.get("input")
        if isinstance(items, list):
            local_call_ids = self_contained_tool_call_ids(items)
            for item in items:
                if isinstance(item, dict):
                    if (
                        client_message_id_is_account_neutral(item)
                        or standalone_function_output_is_account_neutral(item)
                        or inline_agent_message_is_source_neutral(item)
                    ):
                        continue
                    call_id = item.get("call_id")
                    if isinstance(call_id, str) and call_id in local_call_ids:
                        continue
                    if client_tool_output_id_is_account_neutral(item):
                        item = {key: value for key, value in item.items() if key != "id"}
                    keys.update(self.item_keys(item))
        return keys

    def response_keys(self, response: Mapping[str, JsonValue]) -> set[str]:
        keys: set[str] = set()
        self._add(keys, "response", response.get("id"))
        conversation = response.get("conversation")
        self._add(keys, "conversation", conversation.get("id") if isinstance(conversation, dict) else conversation)
        output = response.get("output")
        if isinstance(output, list):
            for item in output:
                if isinstance(item, dict):
                    keys.update(self.item_keys(item))
        return keys

    def item_keys(self, item: Mapping[str, JsonValue]) -> set[str]:
        keys: set[str] = set()
        self._add(keys, "item", item.get("id"))
        if item.get("type") == "mcp_approval_response":
            self._add(keys, "item", item.get("approval_request_id"))
        if item.get("type") == "code_interpreter_call":
            self._add(keys, "container", item.get("container_id"))
        self._add(keys, "encrypted", item.get("encrypted_content"))
        call_id = item.get("call_id")
        if isinstance(call_id, str) and call_id:
            self._add(keys, "call", call_id)
        return keys

    def _add(self, keys: set[str], kind: str, value: JsonValue | None) -> None:
        if isinstance(value, str) and value:
            keys.add(self.key(kind, value))


@dataclass(slots=True)
class SourceOwnershipRecorder:
    scope: OwnershipScope
    source: ModelSource
    input_keys: set[str]
    scheduler: Scheduler = REAL_SCHEDULER
    _recorded: set[str] = field(default_factory=set, init=False)
    _legacy_response_ids: set[str] = field(default_factory=set, init=False)
    _pending_legacy_response_ids: set[str] = field(default_factory=set, init=False)

    async def record_response(self, response: Mapping[str, JsonValue], *, success: bool) -> None:
        keys = self.scope.response_keys(response)
        if success:
            keys.update(self.input_keys)
        response_id = response.get("id")
        legacy_response_ids = {response_id} if isinstance(response_id, str) and response_id else set()
        await self.record(keys, legacy_response_ids=legacy_response_ids)

    async def record_frame(self, frame: str) -> None:
        event = parse_sse_data_json(frame)
        if not isinstance(event, dict):
            return
        keys: set[str] = set()
        legacy_response_ids: set[str] = set()
        # Deltas (including ones synthesized by the public normalizer) may
        # expose references before an output-item or completion envelope.
        for kind, field_name in (("response", "response_id"), ("item", "item_id")):
            value = event.get(field_name)
            if isinstance(value, str) and value:
                keys.add(self.scope.key(kind, value))
                if kind == "response":
                    legacy_response_ids.add(value)
        response = event.get("response")
        if isinstance(response, dict):
            keys.update(self.scope.response_keys(response))
            response_id = response.get("id")
            if isinstance(response_id, str) and response_id:
                legacy_response_ids.add(response_id)
        item = event.get("item")
        if isinstance(item, dict):
            keys.update(self.scope.item_keys(item))
        if event.get("type") in ("response.completed", "response.incomplete"):
            keys.update(self.input_keys)
        await self.record(keys, legacy_response_ids=legacy_response_ids)

    async def record(self, keys: set[str], *, legacy_response_ids: set[str] | None = None) -> None:
        pending = keys - self._recorded
        pending_legacy_response_ids = (legacy_response_ids or set()) - self._legacy_response_ids
        if not pending and not pending_legacy_response_ids:
            return
        if len(self._recorded) + len(pending) > MAX_OWNED_REFERENCES:
            raise SourceOwnershipError()
        try:
            self._pending_legacy_response_ids.update(pending_legacy_response_ids)
            _, cancellation = await _await_result_deferring_cancellation(
                self._commit(sorted(pending)), scheduler=self.scheduler
            )
        except Exception as exc:
            raise SourceOwnershipError() from exc
        self._recorded.update(pending)
        self._legacy_response_ids.update(pending_legacy_response_ids)
        self._pending_legacy_response_ids.difference_update(pending_legacy_response_ids)
        if cancellation is not None:
            raise asyncio.CancelledError

    async def _commit(self, keys: list[str]) -> None:
        async with get_background_session() as session:
            async with sqlite_writer_section():
                await SourceOwnershipRepository(session).claim(
                    keys,
                    source_id=self.source.id,
                    source_revision=source_revision(self.source, self.scope.model),
                    expires_at=utcnow() + OWNERSHIP_TTL,
                    legacy_response_ids={
                        self.scope.key("response", response_id): response_id
                        for response_id in sorted(self._pending_legacy_response_ids)
                    },
                    api_key_id=self.scope.api_key_id,
                    model=self.scope.model,
                )
                await session.commit()
