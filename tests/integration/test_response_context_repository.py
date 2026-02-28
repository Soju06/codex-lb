from __future__ import annotations

from datetime import timedelta

import pytest

import app.modules.proxy.response_context_repository as response_context_repository_module
from app.core.utils.time import utcnow
from app.db.session import SessionLocal
from app.modules.proxy.response_context_repository import ResponseContextRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_response_context_repository_scopes_by_api_key(db_setup):
    now = utcnow()
    expires = now + timedelta(hours=1)

    async with SessionLocal() as session:
        repo = ResponseContextRepository(session)
        await repo.store_response(
            response_payload={
                "id": "rs_scope_a",
                "output": [{"id": "msg_scope_a", "type": "message", "content": [{"type": "output_text", "text": "A"}]}],
            },
            api_key_id="key-a",
            expires_at=expires,
        )

    async with SessionLocal() as session:
        repo = ResponseContextRepository(session)
        resolved_a = await repo.resolve_reference(reference_id="rs_scope_a", api_key_id="key-a")
        resolved_b = await repo.resolve_reference(reference_id="rs_scope_a", api_key_id="key-b")

    assert resolved_a is not None
    assert resolved_a[0]["id"] == "msg_scope_a"
    assert resolved_b is None


@pytest.mark.asyncio
async def test_response_context_repository_cleanup_expired(db_setup):
    now = utcnow()

    async with SessionLocal() as session:
        repo = ResponseContextRepository(session)
        await repo.store_response(
            response_payload={
                "id": "rs_expired",
                "output": [{"id": "msg_expired", "type": "message", "content": [{"type": "output_text", "text": "X"}]}],
            },
            api_key_id=None,
            expires_at=now - timedelta(minutes=1),
        )
        deleted_responses, deleted_items = await repo.delete_expired(now=now)
        resolved = await repo.resolve_reference(reference_id="rs_expired", api_key_id=None)

    assert deleted_responses >= 1
    assert deleted_items >= 1
    assert resolved is None


@pytest.mark.asyncio
async def test_response_context_repository_global_fallback_toggle(db_setup, monkeypatch):
    now = utcnow()
    expires = now + timedelta(hours=1)

    class _Settings:
        response_context_global_fallback_enabled = True

    monkeypatch.setattr(response_context_repository_module, "get_settings", lambda: _Settings())

    async with SessionLocal() as session:
        repo = ResponseContextRepository(session)
        await repo.store_response(
            response_payload={
                "id": "rs_scope_global",
                "output": [
                    {
                        "id": "msg_scope_global",
                        "type": "message",
                        "content": [{"type": "output_text", "text": "GLOBAL"}],
                    }
                ],
            },
            api_key_id=None,
            expires_at=expires,
        )

    async with SessionLocal() as session:
        repo = ResponseContextRepository(session)
        resolved = await repo.resolve_reference(reference_id="rs_scope_global", api_key_id="key-z")

    assert resolved is not None
    assert resolved[0]["id"] == "msg_scope_global"
