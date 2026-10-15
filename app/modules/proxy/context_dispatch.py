from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.core.types import JsonValue
from app.db.session import SessionLocal, sqlite_writer_section
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.context_codec import context_error, context_session_id
from app.modules.proxy.context_repository import ContextRepository

if TYPE_CHECKING:
    from app.core.openai.requests import ResponsesRequest


@dataclass(frozen=True, slots=True)
class ContextDispatchIdentity:
    session_id: str
    enabled: bool


@dataclass(slots=True)
class _ContextDispatchEntry:
    api_key_id: str
    participants: set[str] = field(default_factory=set)


class ContextDispatchCache:
    """Bounded, positive-only cache of committed context ownership and participation."""

    def __init__(self, max_entries: int = 4096) -> None:
        self._max_entries = max_entries
        self._entries: OrderedDict[str, _ContextDispatchEntry] = OrderedDict()

    def get(self, session_id: str, api_key_id: str) -> _ContextDispatchEntry | None:
        entry = self._entries.get(session_id)
        if entry is not None:
            if entry.api_key_id != api_key_id:
                raise context_error("context_scope_mismatch", 403)
            self._entries.move_to_end(session_id)
        return entry

    def remember(self, session_id: str, api_key_id: str, participant: str | None = None) -> None:
        entry = self.get(session_id, api_key_id)
        if entry is None:
            entry = self._entries[session_id] = _ContextDispatchEntry(api_key_id)
        if participant is not None:
            entry.participants.add(participant)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()


_dispatch_cache = ContextDispatchCache()


def get_context_dispatch_cache() -> ContextDispatchCache:
    return _dispatch_cache


def context_dispatch_identity(reasoning: JsonValue, metadata: JsonValue) -> ContextDispatchIdentity | None:
    """Read only the two already-parsed fields needed by dispatch bookkeeping."""
    if not isinstance(metadata, dict):
        return None
    session_id = context_session_id(metadata.get("session_id"))
    if session_id is None:
        return None
    return ContextDispatchIdentity(
        session_id=session_id,
        enabled=isinstance(reasoning, dict) and reasoning.get("context") == "all_turns",
    )


def context_dispatch_identity_for_request(payload: ResponsesRequest) -> ContextDispatchIdentity | None:
    return context_dispatch_identity(
        payload.reasoning.model_extra if payload.reasoning is not None else None,
        (payload.model_extra or {}).get("client_metadata"),
    )


async def record_context_dispatch(
    payload: Mapping[str, JsonValue] | ContextDispatchIdentity | ResponsesRequest | None,
    api_key: ApiKeyData | None,
    account_id: str,
    *,
    record_participant: bool = True,
) -> None:
    """Fence session ownership before dispatch, optionally recording participation."""
    if api_key is None or payload is None:
        return
    if isinstance(payload, ContextDispatchIdentity):
        identity = payload
    elif isinstance(payload, Mapping):
        identity = context_dispatch_identity(payload.get("reasoning"), payload.get("client_metadata"))
    else:
        identity = context_dispatch_identity_for_request(payload)
    if identity is None:
        return
    session_id = identity.session_id
    cached = _dispatch_cache.get(session_id, api_key.id)
    if not identity.enabled and cached is None:
        # Ordinary native requests also carry session_id; that alone is not opt-in.
        return
    if cached is not None and (not record_participant or account_id in cached.participants):
        return
    async with sqlite_writer_section(), SessionLocal() as session:
        repository = ContextRepository(session)
        if cached is None:
            await repository.bind(session_id, api_key.id, account_id)
        if record_participant:
            await repository.add_participant(session_id, account_id)
        await session.commit()
        # No await between commit acknowledgement and publishing positive cache state.
        _dispatch_cache.remember(session_id, api_key.id, account_id if record_participant else None)
