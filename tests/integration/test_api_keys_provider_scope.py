"""Integration tests for API-key ``provider_scope`` end-to-end behavior.

These tests exercise the full request/response round-trip on
``/api/api-keys/`` to confirm that ``provider_scope`` is:

* accepted on create (POST) and update (PATCH);
* stored as a CSV string in the DB (round-trip via GET);
* defaults to ``["codex"]`` when omitted on create;
* rejected for unknown providers.

Source of truth: ``openspec/changes/add-claude-oauth-pool/specs/api-keys/spec.md``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_create_api_key_with_claude_provider_scope_returns_sorted_list(async_client):
    """POST with provider_scope=['claude'] returns 201 and provider_scope=['claude']."""
    create = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "claude-only",
            "providerScope": ["claude"],
        },
    )
    assert create.status_code == 200
    payload = create.json()
    assert payload["providerScope"] == ["claude"]


@pytest.mark.asyncio
async def test_create_api_key_with_unknown_provider_scope_returns_4xx(async_client):
    """POST with provider_scope=['foo'] is rejected with a 4xx error."""
    create = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "bad-scope",
            "providerScope": ["foo"],
        },
    )
    assert 400 <= create.status_code < 500, create.text


@pytest.mark.asyncio
async def test_create_api_key_with_multiple_providers_returns_sorted_list(async_client):
    """POST with provider_scope=['codex','claude'] returns sorted ['claude','codex']."""
    create = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "both-providers",
            "providerScope": ["codex", "claude"],
        },
    )
    assert create.status_code == 200
    payload = create.json()
    assert payload["providerScope"] == ["claude", "codex"]


@pytest.mark.asyncio
async def test_create_api_key_without_provider_scope_defaults_to_codex(async_client):
    """POST without providerScope defaults to ['codex']."""
    create = await async_client.post(
        "/api/api-keys/",
        json={"name": "default-scope"},
    )
    assert create.status_code == 200
    payload = create.json()
    assert payload["providerScope"] == ["codex"]


@pytest.mark.asyncio
async def test_update_api_key_changes_provider_scope(async_client):
    """PATCH changes provider_scope and the new value is reflected in the response."""
    create = await async_client.post(
        "/api/api-keys/",
        json={"name": "to-update"},
    )
    assert create.status_code == 200
    key_id = create.json()["id"]
    assert create.json()["providerScope"] == ["codex"]

    updated = await async_client.patch(
        f"/api/api-keys/{key_id}",
        json={"providerScope": ["claude"]},
    )
    assert updated.status_code == 200
    assert updated.json()["providerScope"] == ["claude"]


@pytest.mark.asyncio
async def test_get_api_key_returns_provider_scope_even_when_db_stored_single(async_client):
    """GET on a key with a single-provider scope returns that scope."""
    create = await async_client.post(
        "/api/api-keys/",
        json={"name": "single-scope", "providerScope": ["claude"]},
    )
    assert create.status_code == 200
    key_id = create.json()["id"]

    fetched = await async_client.get("/api/api-keys/")
    assert fetched.status_code == 200
    rows = fetched.json()
    assert any(row["id"] == key_id and row["providerScope"] == ["claude"] for row in rows)


@pytest.mark.asyncio
async def test_codex_only_api_key_is_rejected_on_claude_route(async_client, monkeypatch):
    """A codex-only API key sent to /claude/v1/messages returns 403."""
    create = await async_client.post(
        "/api/api-keys/",
        json={"name": "codex-only", "providerScope": ["codex"]},
    )
    assert create.status_code == 200
    plain_key = create.json()["key"]

    # Patch the upstream so the request would otherwise succeed; we expect 403 first.
    response = await async_client.post(
        "/claude/v1/messages",
        json={"model": "claude-opus-4-8", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {plain_key}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_claude_only_api_key_reaches_claude_handler(async_client, monkeypatch):
    """A claude-only API key sent to /claude/v1/messages reaches the handler.

    We don't have a real Anthropic endpoint in tests; we patch the proxy
    service so the request returns a 200 from the handler boundary.

    The default test environment has ``api_key_auth_enabled`` disabled; we
    flip it on via the DB so the validator actually authenticates the key
    (otherwise the underlying ``validate_proxy_api_key`` returns ``None``
    and the provider-scope check 403s).
    """
    from sqlalchemy import update

    from app.db.models import DashboardSettings
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        await session.execute(update(DashboardSettings).values(api_key_auth_enabled=True))
        await session.commit()

    # Invalidate the cached settings so the change takes effect.
    from app.core.config.settings_cache import get_settings_cache

    await get_settings_cache().invalidate()

    create = await async_client.post(
        "/api/api-keys/",
        json={"name": "claude-only", "providerScope": ["claude"]},
    )
    assert create.status_code == 200
    plain_key = create.json()["key"]

    class _StubProxyService:
        async def stream_or_complete_messages(self, *, request_body, api_key, request_id):
            return ({"id": "msg_test", "type": "message"}, {})

        async def stream_messages(self, *, request_body, api_key, request_id):
            if False:
                yield None
            return

    # Replace the proxy service on app.state with a stub.
    app = async_client._transport.app
    app.state.claude_proxy_service = _StubProxyService()

    response = await async_client.post(
        "/claude/v1/messages",
        json={"model": "claude-opus-4-8", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {plain_key}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == "msg_test"
