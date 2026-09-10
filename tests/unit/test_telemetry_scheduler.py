from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.modules.telemetry.scheduler as scheduler_module
from app.modules.telemetry.scheduler import TelemetryScheduler

pytestmark = pytest.mark.unit


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _FakeSchedulerState:
    def __init__(self, days: list[SimpleNamespace]) -> None:
        self.days = days
        self.row = SimpleNamespace(telemetry_day_acknowledged_date=None)
        self.commit_refresh = AsyncMock()

    async def get_or_create(self):
        return self.row

    async def completed_days(self, instance_id: str, *, acknowledged: date | None):
        del instance_id
        return [day for day in self.days if acknowledged is None or day.utc_date > acknowledged]


def _install_fakes(monkeypatch, days: list[SimpleNamespace]):
    state = _FakeSchedulerState(days)

    class FakeStore:
        def __init__(self, session) -> None:
            del session
            self._repository = state

        async def resolve(self):
            return SimpleNamespace(active=True, state="enabled", source="persisted")

        async def get_or_create_identity(self):
            return SimpleNamespace(instance_id="instance")

    class FakeBuilder:
        def __init__(self, session) -> None:
            del session

        async def build(self, instance_id: str, *, consent: str):
            return SimpleNamespace(instance_id=instance_id, consent=consent)

        async def completed_days(self, instance_id: str, *, acknowledged: date | None):
            return await state.completed_days(instance_id, acknowledged=acknowledged)

    monkeypatch.setattr(scheduler_module, "get_background_session", lambda: _SessionContext())
    monkeypatch.setattr(scheduler_module, "TelemetryConsentStore", FakeStore)
    monkeypatch.setattr(scheduler_module, "TelemetrySnapshotBuilder", FakeBuilder)
    return state


def _days(count: int) -> list[SimpleNamespace]:
    return [SimpleNamespace(utc_date=date(2026, 9, day)) for day in range(count, 0, -1)]


@pytest.mark.asyncio
async def test_scheduler_sends_newest_seven_and_acknowledges_newest_on_success(monkeypatch) -> None:
    days = _days(9)
    state = _install_fakes(monkeypatch, days)
    sender = AsyncMock()
    sender.send_day.side_effect = lambda day: True

    await TelemetryScheduler(sender=sender)._tick_as_leader()

    assert [call.args[0].utc_date.day for call in sender.send_day.await_args_list] == list(range(9, 2, -1))
    assert state.row.telemetry_day_acknowledged_date.date() == date(2026, 9, 9)
    assert await state.completed_days("instance", acknowledged=date(2026, 9, 9)) == []


@pytest.mark.asyncio
async def test_scheduler_resends_from_second_oldest_failure_without_skipping_it(monkeypatch) -> None:
    days = _days(9)
    state = _install_fakes(monkeypatch, days)
    sender = AsyncMock()
    sender.send_day.side_effect = lambda day: day.utc_date.day != 4
    scheduler = TelemetryScheduler(sender=sender)

    await scheduler._tick_as_leader()

    assert state.row.telemetry_day_acknowledged_date.date() == date(2026, 9, 3)
    assert sender.send_day.await_count == 7
    sender.send_day.reset_mock()
    sender.send_day.side_effect = lambda day: True
    await scheduler._tick_as_leader()

    assert [call.args[0].utc_date.day for call in sender.send_day.await_args_list] == list(range(9, 3, -1))
    assert state.row.telemetry_day_acknowledged_date.date() == date(2026, 9, 9)


@pytest.mark.asyncio
async def test_scheduler_oldest_failure_only_acknowledges_day_before_window(monkeypatch) -> None:
    days = _days(9)
    state = _install_fakes(monkeypatch, days)
    sender = AsyncMock()
    sender.send_day.side_effect = lambda day: day.utc_date.day != 3

    await TelemetryScheduler(sender=sender)._tick_as_leader()

    assert state.row.telemetry_day_acknowledged_date.date() == date(2026, 9, 2)
    assert sender.send_day.await_count == 7


@pytest.mark.asyncio
async def test_scheduler_three_successful_days_acknowledges_newest(monkeypatch) -> None:
    days = _days(3)
    state = _install_fakes(monkeypatch, days)
    sender = AsyncMock()
    sender.send_day.side_effect = lambda day: True

    await TelemetryScheduler(sender=sender)._tick_as_leader()

    assert state.row.telemetry_day_acknowledged_date.date() == date(2026, 9, 3)
    assert sender.send_day.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("heartbeat_fails", [False, True])
async def test_scheduler_heartbeat_and_day_failures_are_isolated(monkeypatch, heartbeat_fails: bool) -> None:
    days = _days(3)
    state = _install_fakes(monkeypatch, days)
    sender = AsyncMock()
    if heartbeat_fails:
        sender.send_snapshot.side_effect = RuntimeError("heartbeat failed")
        sender.send_day.return_value = True
    else:
        sender.send_day.side_effect = [True, RuntimeError("day failed"), True]

    await TelemetryScheduler(sender=sender)._tick_as_leader()

    sender.send_snapshot.assert_awaited_once()
    assert sender.send_day.await_count == 3
    assert state.row.telemetry_day_acknowledged_date.date() == date(2026, 9, 3 if heartbeat_fails else 1)
    assert sender.mock_calls[0][0] == "send_snapshot"
