from __future__ import annotations

import json
from collections.abc import Mapping

from app.core.types import JsonValue
from app.db.session import SessionLocal, sqlite_writer_section
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.context_codec import context_session_id
from app.modules.proxy.context_repository import ContextRepository


async def record_context_dispatch(
    payload: Mapping[str, JsonValue] | str,
    api_key: ApiKeyData | None,
    account_id: str,
    *,
    record_participant: bool = True,
) -> None:
    """Fence session ownership before dispatch, optionally recording participation."""
    if api_key is None:
        return
    if isinstance(payload, str):
        payload = json.loads(payload)
    reasoning = payload.get("reasoning")
    metadata = payload.get("client_metadata")
    if not isinstance(metadata, dict):
        return
    session_id = context_session_id(metadata.get("session_id"))
    if session_id is None:
        return
    if not isinstance(reasoning, dict) or reasoning.get("context") != "all_turns":
        # Omitting all_turns must not bypass an existing session's key fence.
        async with SessionLocal() as session:
            if await ContextRepository(session).get(session_id, api_key.id) is None:
                return
    async with sqlite_writer_section(), SessionLocal() as session:
        repository = ContextRepository(session)
        if record_participant:
            await repository.record(session_id, api_key.id, account_id)
        else:
            await repository.bind(session_id, api_key.id, account_id)
        await session.commit()
