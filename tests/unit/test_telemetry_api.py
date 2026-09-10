from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.auth.dashboard_access import guest_principal
from app.core.auth.dependencies import validate_dashboard_session
from app.core.config.settings import get_settings
from app.core.exceptions import DashboardSettingsConflictError
from app.db.models import DashboardSettings
from app.db.session import get_background_session
from app.modules.settings.repository import SettingsRepository
from app.modules.telemetry.consent import TELEMETRY_NOTICE_VERSION


async def _notice_version() -> int:
    async with get_background_session() as session:
        row = await session.get(DashboardSettings, 1)
        assert row is not None
        return int(row.telemetry_notice_version or 0)


pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "winning_version", [TELEMETRY_NOTICE_VERSION - 1, TELEMETRY_NOTICE_VERSION, TELEMETRY_NOTICE_VERSION + 1]
)
async def test_concurrent_notice_acknowledgement_only_swallows_completed_notice_conflict(
    async_client, monkeypatch, winning_version: int
) -> None:
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()
    # Prepare identity and preview without consuming the notice before simulating a race.
    response = await async_client.get("/api/settings/telemetry?include_preview=true")
    assert response.status_code == 200
    conflicts = 0

    async def concurrent_acknowledgement(repository, row, **kwargs):
        nonlocal conflicts
        conflicts += 1
        assert row.telemetry_notice_version == TELEMETRY_NOTICE_VERSION
        await repository._session.rollback()
        async with get_background_session() as other_session:
            other_row = await other_session.get(DashboardSettings, 1)
            assert other_row is not None
            other_row.telemetry_notice_version = winning_version
            await other_session.commit()
        raise DashboardSettingsConflictError()

    monkeypatch.setattr(SettingsRepository, "commit_refresh", concurrent_acknowledgement)
    response = await async_client.get("/api/settings/telemetry")

    assert conflicts == 1
    if winning_version >= TELEMETRY_NOTICE_VERSION:
        assert response.status_code == 200
        assert response.json()["preview"] is not None
    else:
        assert response.status_code == 409
    assert await _notice_version() == winning_version


@pytest.fixture(autouse=True)
def opt_out_sender(monkeypatch):
    sender = Mock()
    sender.send_opt_out = AsyncMock()
    factory = Mock(return_value=sender)
    monkeypatch.setattr("app.modules.telemetry.api.TelemetrySender", factory)
    return sender


@pytest.mark.asyncio
async def test_consent_api_get_preview_and_put_persists_without_restart(
    async_client,
    monkeypatch,
    opt_out_sender,
) -> None:
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()

    response = await async_client.get("/api/settings/telemetry")
    assert response.status_code == 200
    initial = response.json()
    assert initial["state"] == "undecided"
    assert initial["source"] == "default"
    assert initial["active"] is True
    assert set(initial["preview"]) == {"heartbeat", "day"}
    assert initial["preview"]["heartbeat"]["metrics"]["schema_version"] == 2
    assert initial["preview"]["heartbeat"]["metrics"]["consent"] == "undecided"
    assert initial["preview"]["heartbeat"]["instance_id"] == initial["preview"]["heartbeat"]["metrics"]["instance_id"]

    response = await async_client.put("/api/settings/telemetry", json={"enabled": False})
    assert response.status_code == 200
    disabled = response.json()
    assert disabled["state"] == "disabled"
    assert disabled["source"] == "persisted"
    assert disabled["active"] is False
    assert disabled["preview"] is None
    await asyncio.sleep(0)
    opt_out_sender.send_opt_out.assert_awaited_once()

    builder = Mock(side_effect=AssertionError("decided consent must not build a preview"))
    monkeypatch.setattr("app.modules.telemetry.api.TelemetrySnapshotBuilder", builder)
    response = await async_client.get("/api/settings/telemetry")
    assert response.status_code == 200
    assert response.json()["state"] == "disabled"
    assert response.json()["preview"] is None
    builder.assert_not_called()


@pytest.mark.asyncio
async def test_consent_api_builds_decided_preview_only_when_requested(
    async_client,
    monkeypatch,
) -> None:
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()
    await async_client.put("/api/settings/telemetry", json={"enabled": False})

    response = await async_client.get("/api/settings/telemetry?include_preview=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "disabled"
    assert payload["preview"]["heartbeat"]["instance_id"] == payload["preview"]["heartbeat"]["metrics"]["instance_id"]
    assert payload["preview"]["heartbeat"]["metrics"]["consent"] == "enabled"
    assert await _notice_version() == 0


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
    assert await _notice_version() == 0


@pytest.mark.asyncio
async def test_dashboard_active_to_inactive_transitions_each_send_exactly_once(
    async_client,
    monkeypatch,
    opt_out_sender,
) -> None:
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()

    first = await async_client.put("/api/settings/telemetry", json={"enabled": False})
    assert first.status_code == 200
    await asyncio.sleep(0)
    assert opt_out_sender.send_opt_out.await_count == 1

    repeated = await async_client.put("/api/settings/telemetry", json={"enabled": False})
    assert repeated.status_code == 200
    await asyncio.sleep(0)
    assert opt_out_sender.send_opt_out.await_count == 1

    enabled = await async_client.put("/api/settings/telemetry", json={"enabled": True})
    assert enabled.status_code == 200
    await asyncio.sleep(0)
    assert opt_out_sender.send_opt_out.await_count == 1

    second = await async_client.put("/api/settings/telemetry", json={"enabled": False})
    assert second.status_code == 200
    await asyncio.sleep(0)
    assert opt_out_sender.send_opt_out.await_count == 2

    call = opt_out_sender.send_opt_out.await_args_list[-1]
    assert call.args[0].instance_id
    assert call.kwargs["app_version"]
    assert call.kwargs["deployment_mode"] in {"docker", "k8s", "pip", "bare"}
    assert "/" in call.kwargs["os_arch"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("env_value", "enabled", "expected_state", "expected_opt_outs"),
    [("true", False, "disabled", 1), ("false", True, "enabled", 0)],
)
async def test_persisted_dashboard_decision_wins_over_environment(
    async_client,
    monkeypatch,
    opt_out_sender,
    env_value: str,
    enabled: bool,
    expected_state: str,
    expected_opt_outs: int,
) -> None:
    monkeypatch.setenv("CODEX_LB_TELEMETRY_ENABLED", env_value)
    get_settings.cache_clear()

    # While undecided the environment decides and is reported as the source.
    before = await async_client.get("/api/settings/telemetry")
    assert before.status_code == 200
    assert before.json()["source"] == "env"
    assert before.json()["active"] is (env_value == "true")

    response = await async_client.put("/api/settings/telemetry", json={"enabled": enabled})

    assert response.status_code == 200
    assert response.json()["state"] == expected_state
    assert response.json()["source"] == "persisted"
    assert response.json()["active"] is enabled
    # The env value is still set; the saved decision must keep winning on read.
    after = await async_client.get("/api/settings/telemetry")
    assert after.status_code == 200
    assert after.json()["state"] == expected_state
    assert after.json()["source"] == "persisted"
    assert after.json()["active"] is enabled
    await asyncio.sleep(0)
    # An env-active -> dashboard-disabled transition is a dashboard-driven
    # opt-out and sends exactly one notice; enabling never does.
    assert opt_out_sender.send_opt_out.await_count == expected_opt_outs


@pytest.mark.asyncio
async def test_opt_out_background_send_does_not_block_settings_response(
    async_client,
    monkeypatch,
    opt_out_sender,
) -> None:
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_send(*args, **kwargs) -> None:
        del args, kwargs
        started.set()
        await release.wait()

    opt_out_sender.send_opt_out.side_effect = blocked_send

    response = await async_client.put("/api/settings/telemetry", json={"enabled": False})

    assert response.status_code == 200
    await asyncio.wait_for(started.wait(), timeout=1)
    from app.modules.telemetry import api as telemetry_api

    assert telemetry_api._OPT_OUT_TASKS
    release.set()
    await asyncio.gather(*tuple(telemetry_api._OPT_OUT_TASKS))
    await asyncio.sleep(0)
    assert not telemetry_api._OPT_OUT_TASKS


@pytest.mark.asyncio
async def test_opt_out_identity_failure_is_debug_only_and_preserves_disabled_state(
    async_client,
    monkeypatch,
    opt_out_sender,
    caplog,
) -> None:
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()

    async def fail_identity(_store) -> None:
        raise RuntimeError("identity decryption failed")

    monkeypatch.setattr(
        "app.modules.telemetry.api.TelemetryConsentStore.get_or_create_identity",
        fail_identity,
    )

    with caplog.at_level(logging.DEBUG, logger="app.modules.telemetry.api"):
        response = await async_client.put("/api/settings/telemetry", json={"enabled": False})

    assert response.status_code == 200
    assert response.json()["state"] == "disabled"
    async with get_background_session() as session:
        row = await session.get(DashboardSettings, 1)
        assert row is not None
        row.telemetry_notice_version = 2
        await session.commit()
    persisted = await async_client.get("/api/settings/telemetry")
    assert persisted.status_code == 200
    assert persisted.json()["state"] == "disabled"
    opt_out_sender.send_opt_out.assert_not_awaited()
    assert "Unable to schedule anonymous telemetry opt-out" in caplog.messages
    assert all(record.levelno == logging.DEBUG for record in caplog.records)


@pytest.mark.asyncio
async def test_unexpected_opt_out_task_failure_is_debug_only_and_does_not_change_response(
    async_client,
    monkeypatch,
    opt_out_sender,
    caplog,
) -> None:
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()
    opt_out_sender.send_opt_out.side_effect = RuntimeError("unexpected sender failure")

    with caplog.at_level(logging.DEBUG, logger="app.modules.telemetry.api"):
        response = await async_client.put("/api/settings/telemetry", json={"enabled": False})
        await asyncio.sleep(0)

    assert response.status_code == 200
    assert caplog.records
    assert all(record.levelno == logging.DEBUG for record in caplog.records)


@pytest.mark.asyncio
async def test_read_only_notice_preview_does_not_acknowledge_and_write_principal_can_later_ack(
    async_client, app_instance, monkeypatch
) -> None:
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()
    await async_client.put("/api/settings/telemetry", json={"enabled": False})
    app_instance.dependency_overrides[validate_dashboard_session] = lambda: guest_principal()
    try:
        response = await async_client.get("/api/settings/telemetry")
        assert response.status_code == 200
        assert response.json()["state"] == "disabled"
        assert response.json()["preview"] is not None
        assert await _notice_version() == 0

        response = await async_client.get("/api/settings/telemetry?include_preview=true")
        assert response.status_code == 200
        assert response.json()["state"] == "disabled"
        assert response.json()["preview"] is not None
        assert await _notice_version() == 0
    finally:
        app_instance.dependency_overrides.pop(validate_dashboard_session, None)

    response = await async_client.get("/api/settings/telemetry")
    assert response.status_code == 200
    assert response.json()["state"] == "disabled"
    assert response.json()["preview"] is not None
    assert await _notice_version() == 2

    response = await async_client.get("/api/settings/telemetry")
    assert response.status_code == 200
    assert response.json()["state"] == "disabled"
    assert response.json()["preview"] is None


@pytest.mark.asyncio
async def test_explicit_preview_never_acknowledges_unacknowledged_notice(async_client, monkeypatch) -> None:
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()
    await async_client.put("/api/settings/telemetry", json={"enabled": False})
    assert await _notice_version() == 0

    response = await async_client.get("/api/settings/telemetry?include_preview=true")
    assert response.status_code == 200
    assert response.json()["preview"] is not None
    assert await _notice_version() == 0

    response = await async_client.get("/api/settings/telemetry")
    assert response.status_code == 200
    assert response.json()["preview"] is not None
    assert await _notice_version() == 2


@pytest.mark.asyncio
async def test_explicit_preview_after_acknowledgement_keeps_watermark(async_client, monkeypatch) -> None:
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()
    await async_client.get("/api/settings/telemetry")
    assert await _notice_version() == 2

    response = await async_client.get("/api/settings/telemetry?include_preview=true")
    assert response.status_code == 200
    assert response.json()["preview"] is not None
    assert await _notice_version() == 2


@pytest.mark.asyncio
async def test_undecided_default_dialog_remains_available_after_notice_acknowledgement(
    async_client, monkeypatch
) -> None:
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()
    async with get_background_session() as session:
        row = await SettingsRepository(session).get_or_create()
        assert row.telemetry_consent == "undecided"
        assert row.telemetry_notice_version is None
    assert await _notice_version() == 0

    # Dismissal saves no decision, so another dashboard entry must still have a preview.
    for _ in range(2):
        response = await async_client.get("/api/settings/telemetry")
        assert response.status_code == 200
        assert response.json()["state"] == "undecided"
        assert response.json()["source"] == "default"
        assert response.json()["preview"] is not None
        assert await _notice_version() == TELEMETRY_NOTICE_VERSION


@pytest.mark.asyncio
async def test_undecided_operator_enabling_after_preview_does_not_owe_another_notice(async_client, monkeypatch) -> None:
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()
    preview = await async_client.get("/api/settings/telemetry")
    assert preview.status_code == 200
    assert preview.json()["state"] == "undecided"
    assert preview.json()["preview"] is not None
    assert await _notice_version() == TELEMETRY_NOTICE_VERSION

    decision = await async_client.put("/api/settings/telemetry", json={"enabled": True})
    assert decision.status_code == 200
    assert decision.json()["state"] == "enabled"
    assert decision.json()["source"] == "persisted"

    builder = Mock(side_effect=AssertionError("a decision at the current notice version must not build a preview"))
    monkeypatch.setattr("app.modules.telemetry.api.TelemetrySnapshotBuilder", builder)
    response = await async_client.get("/api/settings/telemetry")
    assert response.status_code == 200
    assert response.json()["state"] == "enabled"
    assert response.json()["preview"] is None
    assert await _notice_version() == TELEMETRY_NOTICE_VERSION
    builder.assert_not_called()


@pytest.mark.asyncio
async def test_enabled_operator_with_older_notice_sees_preview_once_without_resetting_decision(
    async_client, monkeypatch
) -> None:
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()
    decision = await async_client.put("/api/settings/telemetry", json={"enabled": True})
    assert decision.status_code == 200
    async with get_background_session() as session:
        row = await session.get(DashboardSettings, 1)
        assert row is not None
        row.telemetry_notice_version = TELEMETRY_NOTICE_VERSION - 1
        await session.commit()

    response = await async_client.get("/api/settings/telemetry")
    assert response.status_code == 200
    assert response.json()["state"] == "enabled"
    assert response.json()["source"] == "persisted"
    assert response.json()["preview"] is not None
    assert await _notice_version() == TELEMETRY_NOTICE_VERSION

    response = await async_client.get("/api/settings/telemetry")
    assert response.status_code == 200
    assert response.json()["state"] == "enabled"
    assert response.json()["source"] == "persisted"
    assert response.json()["preview"] is None
