from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_settings_api_get_and_update(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["stickyThreadsEnabled"] is False
    assert payload["preferEarlierResetAccounts"] is False
    assert payload["importWithoutOverwrite"] is False
    assert payload["totpRequiredOnLogin"] is False
    assert payload["totpConfigured"] is False
    assert payload["apiKeyAuthEnabled"] is False

    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": True,
            "preferEarlierResetAccounts": True,
            "importWithoutOverwrite": True,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["stickyThreadsEnabled"] is True
    assert updated["preferEarlierResetAccounts"] is True
    assert updated["importWithoutOverwrite"] is True
    assert updated["totpRequiredOnLogin"] is False
    assert updated["totpConfigured"] is False
    assert updated["apiKeyAuthEnabled"] is True

    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["stickyThreadsEnabled"] is True
    assert payload["preferEarlierResetAccounts"] is True
    assert payload["importWithoutOverwrite"] is True
    assert payload["totpRequiredOnLogin"] is False
    assert payload["totpConfigured"] is False
    assert payload["apiKeyAuthEnabled"] is True
