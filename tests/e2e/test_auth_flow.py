from __future__ import annotations

import pytest


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_dashboard_password_auth_flow(client, setup_dashboard_password, login_dashboard):
    public_response = await client.get("/api/settings")
    assert public_response.status_code == 200

    password = await setup_dashboard_password(client)

    logout_response = await client.post("/api/dashboard-auth/logout", json={})
    assert logout_response.status_code == 200

    blocked_response = await client.get("/api/settings")
    assert blocked_response.status_code == 401
    assert blocked_response.json()["error"]["code"] == "authentication_required"

    login_payload = await login_dashboard(client, password=password)
    assert login_payload["passwordRequired"] is True
    assert login_payload["authenticated"] is True

    protected_response = await client.get("/api/settings")
    assert protected_response.status_code == 200
    assert protected_response.json()["totpRequiredOnLogin"] is False

    session_response = await client.get("/api/dashboard-auth/session")
    assert session_response.status_code == 200
    assert session_response.json()["authenticated"] is True
