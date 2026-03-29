from __future__ import annotations

import pytest


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_api_key_lifecycle_create_use_revoke(
    client,
    setup_dashboard_password,
    enable_api_key_auth,
    create_api_key,
    populate_test_registry,
):
    await setup_dashboard_password(client)
    model_ids = await populate_test_registry()
    await enable_api_key_auth(client)
    created = await create_api_key(client, name="e2e-lifecycle-key", allowed_models=model_ids)

    headers = {"Authorization": f"Bearer {created['key']}"}
    allowed = await client.get("/v1/models", headers=headers)
    assert allowed.status_code == 200
    assert [item["id"] for item in allowed.json()["data"]] == model_ids

    revoked = await client.patch(f"/api/api-keys/{created['id']}", json={"isActive": False})
    assert revoked.status_code == 200
    assert revoked.json()["isActive"] is False

    blocked = await client.get("/v1/models", headers=headers)
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "invalid_api_key"
