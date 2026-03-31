from __future__ import annotations

import pytest


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_all_health_endpoints_respond(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    response = await client.get("/health/live")
    assert response.status_code == 200
    live_payload = response.json()
    assert live_payload["status"] == "ok"
    assert live_payload["checks"] is None
    assert live_payload.get("bridge_ring") is None

    response = await client.get("/health/ready")
    assert response.status_code == 200
    ready_payload = response.json()
    assert ready_payload["status"] == "ok"
    assert "checks" in ready_payload
    assert ready_payload["checks"]["database"] == "ok"

    response = await client.get("/health/startup")
    assert response.status_code in (200, 503)
