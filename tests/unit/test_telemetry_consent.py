from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.config.settings import get_settings
from app.db.models import DashboardSettings
from app.db.session import SessionLocal
from app.modules.telemetry.consent import TelemetryConsentStore, resolve_consent
from app.modules.telemetry.scheduler import TELEMETRY_INTERVAL_SECONDS, TelemetryScheduler

pytestmark = pytest.mark.unit


class _GateLeader:
    def __init__(self, *, leader: bool) -> None:
        self.leader = leader
        self.run_if_leader_calls = 0

    async def run_if_leader(self, fn: Callable[[], Awaitable[object]]) -> object | None:
        self.run_if_leader_calls += 1
        if not self.leader:
            return None
        return await fn()


def test_consent_precedence_and_default_activation() -> None:
    assert resolve_consent(False, "enabled").state == "disabled"
    assert resolve_consent(False, "enabled").source == "env"
    assert resolve_consent(False, "enabled").active is False

    env_enabled = resolve_consent(True, "undecided")
    assert env_enabled.state == "enabled"
    assert env_enabled.source == "env"
    assert env_enabled.active is True

    persisted_disabled = resolve_consent(None, "disabled")
    assert persisted_disabled.state == "disabled"
    assert persisted_disabled.source == "persisted"
    assert persisted_disabled.active is False

    undecided = resolve_consent(None, "undecided")
    assert undecided.state == "undecided"
    assert undecided.source == "default"
    assert undecided.active is True


@pytest.mark.asyncio
async def test_random_uuid_v4_identity_is_persisted_and_regenerated_after_deletion(db_setup) -> None:
    del db_setup
    async with SessionLocal() as session:
        store = TelemetryConsentStore(session)
        first = await store.get_or_create_identity()
        second = await store.get_or_create_identity()
        assert first.instance_id == second.instance_id
        assert first.public_key_hex == second.public_key_hex
        assert first.instance_id.split("-")[2].startswith("4")

        row = await session.get(DashboardSettings, 1)
        assert row is not None
        row.telemetry_instance_id = None
        await session.commit()
        session.expire_all()

        replacement = await store.get_or_create_identity()
        assert replacement.instance_id != first.instance_id
        assert replacement.public_key_hex != first.public_key_hex


@pytest.mark.asyncio
async def test_disabled_scheduler_tick_makes_zero_sender_calls(db_setup, monkeypatch) -> None:
    del db_setup
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()
    async with SessionLocal() as session:
        store = TelemetryConsentStore(session)
        await store.set_decision(False)

    sender = AsyncMock()
    scheduler = TelemetryScheduler(sender=sender)
    await scheduler._tick()

    sender.send_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_scheduler_snapshot_declares_enabled_consent(db_setup, monkeypatch) -> None:
    del db_setup
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()
    async with SessionLocal() as session:
        store = TelemetryConsentStore(session)
        await store.set_decision(True)

    sender = AsyncMock()
    await TelemetryScheduler(sender=sender)._tick()

    sender.send_snapshot.assert_awaited_once()
    assert sender.send_snapshot.await_args.args[0].consent == "enabled"


@pytest.mark.asyncio
async def test_non_leader_scheduler_tick_builds_and_transmits_nothing(monkeypatch) -> None:
    import app.modules.telemetry.scheduler as scheduler_module

    leader = _GateLeader(leader=False)
    builder = Mock(side_effect=AssertionError("non-leader must not construct a snapshot builder"))
    monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: leader)
    monkeypatch.setattr(scheduler_module, "TelemetrySnapshotBuilder", builder)
    sender = AsyncMock()

    await TelemetryScheduler(sender=sender)._tick(log_undecided_notice=True)

    assert leader.run_if_leader_calls == 1
    builder.assert_not_called()
    sender.send_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_main_lifespan_constructs_starts_and_stops_telemetry_scheduler(app_instance, monkeypatch) -> None:
    import app.main as main_module

    scheduler = Mock()
    scheduler.start = AsyncMock()
    scheduler.stop = AsyncMock()
    factory = Mock(return_value=scheduler)
    monkeypatch.setattr(main_module, "build_telemetry_scheduler", factory)

    async with app_instance.router.lifespan_context(app_instance):
        factory.assert_called_once_with()
        scheduler.start.assert_awaited_once_with()
        scheduler.stop.assert_not_awaited()

    scheduler.stop.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_scheduler_sends_startup_and_interval_snapshots_with_one_undecided_notice(
    db_setup,
    monkeypatch,
    caplog,
) -> None:
    del db_setup
    monkeypatch.delenv("CODEX_LB_TELEMETRY_ENABLED", raising=False)
    get_settings.cache_clear()
    assert TELEMETRY_INTERVAL_SECONDS == 24 * 60 * 60

    sender = AsyncMock()
    scheduler = TelemetryScheduler(sender=sender, interval_seconds=0.01)
    with caplog.at_level(logging.INFO, logger="app.modules.telemetry.scheduler"):
        await scheduler.start()
        for _ in range(50):
            if sender.send_snapshot.await_count >= 2:
                break
            await asyncio.sleep(0.01)
        await scheduler.stop()

    assert sender.send_snapshot.await_count >= 2
    assert all(call.args[0].consent == "undecided" for call in sender.send_snapshot.await_args_list)
    notices = [
        record.getMessage() for record in caplog.records if "Anonymous telemetry is active" in record.getMessage()
    ]
    assert len(notices) == 1
    assert "https://soju06.github.io/codex-lb/telemetry/" in notices[0]
    assert "CODEX_LB_TELEMETRY_ENABLED=false" in notices[0]
    assert scheduler._task is None
