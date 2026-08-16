from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.core.config.settings import get_settings

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_consent_api_get_preview_and_put_persists_without_restart(async_client, monkeypatch) -> None:
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()

    response = await async_client.get("/api/settings/telemetry")
    assert response.status_code == 200
    initial = response.json()
    assert initial["state"] == "undecided"
    assert initial["source"] == "default"
    assert initial["active"] is True
    assert set(initial["preview"]) == {"instance_id", "metrics", "timestamp"}
    assert initial["preview"]["metrics"]["schema_version"] == 1
    assert initial["preview"]["instance_id"] == initial["preview"]["metrics"]["instance_id"]

    response = await async_client.put("/api/settings/telemetry", json={"enabled": False})
    assert response.status_code == 200
    disabled = response.json()
    assert disabled["state"] == "disabled"
    assert disabled["source"] == "persisted"
    assert disabled["active"] is False
    assert disabled["preview"] is None

    builder = Mock(side_effect=AssertionError("decided consent must not build a preview"))
    monkeypatch.setattr("app.modules.telemetry.api.TelemetrySnapshotBuilder", builder)
    response = await async_client.get("/api/settings/telemetry")
    assert response.status_code == 200
    assert response.json()["state"] == "disabled"
    assert response.json()["preview"] is None
    builder.assert_not_called()


@pytest.mark.asyncio
async def test_consent_api_builds_decided_preview_only_when_requested(async_client, monkeypatch) -> None:
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()
    await async_client.put("/api/settings/telemetry", json={"enabled": False})

    response = await async_client.get("/api/settings/telemetry?include_preview=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "disabled"
    assert payload["preview"]["instance_id"] == payload["preview"]["metrics"]["instance_id"]


@pytest.mark.asyncio
async def test_consent_api_env_override_wins_and_suppresses_undecided_state(async_client, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_TELEMETRY_ENABLED", "true")
    get_settings.cache_clear()

    builder = Mock(side_effect=AssertionError("environment override must not build a preview"))
    monkeypatch.setattr("app.modules.telemetry.api.TelemetrySnapshotBuilder", builder)
    response = await async_client.get("/api/settings/telemetry")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "enabled"
    assert payload["source"] == "env"
    assert payload["active"] is True
    assert payload["preview"] is None
    builder.assert_not_called()
