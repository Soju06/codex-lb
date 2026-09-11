from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.modules.telemetry.scheduler as scheduler_module
from app.db.models import DashboardSettings, RequestLog
from app.db.session import get_background_session
from app.modules.telemetry.consent import TelemetryConsentStore
from app.modules.telemetry.scheduler import TelemetryScheduler
from app.modules.telemetry.snapshot import TelemetrySnapshotBuilder

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("offsets", "failed_offset", "expected_offsets", "watermark_offset"),
    [
        ([1, 40], None, [1], 1),
        ([1, 40], 1, [1], 8),
        ([40], None, [], 8),
        ([], None, [], 8),
        ([0, -1], None, [], 8),
        (list(range(1, 31)), None, list(range(1, 8)), 1),
        (list(range(1, 31)), 6, list(range(1, 8)), 7),
        (list(range(1, 31)), 7, list(range(1, 8)), 8),
    ],
    ids=[
        "sparse",
        "sparse-failure",
        "old-only",
        "empty",
        "in-progress",
        "30-days",
        "partial-failure",
        "oldest-failure",
    ],
)
async def test_scheduler_real_builder_bounds_calendar_window(
    db_setup,
    monkeypatch,
    offsets: list[int],
    failed_offset: int | None,
    expected_offsets: list[int],
    watermark_offset: int,
) -> None:
    now = datetime(2026, 9, 10, 23, 59, 59)
    today = now.date()
    previous = now - timedelta(days=60)
    async with get_background_session() as session:
        store = TelemetryConsentStore(session)
        await store.set_decision(True)
        row = await session.get(DashboardSettings, 1)
        assert row is not None
        row.telemetry_day_acknowledged_date = previous
        session.add_all(
            [
                RequestLog(
                    request_id=f"day-{offset}",
                    requested_at=now - timedelta(days=offset),
                    model="gpt-5.6-sol",
                    status="success",
                    request_kind="normal",
                    latency_ms=1000,
                )
                for offset in offsets
            ]
        )
        await session.commit()

    events: list[str | date] = []
    original_build_day = TelemetrySnapshotBuilder.build_day

    async def record_day_build(builder, instance_id: str, day: date, *, today_utc: date):
        assert today_utc == today
        assert today - timedelta(days=7) <= day < today
        events.append(day)
        return await original_build_day(builder, instance_id, day, today_utc=today_utc)

    monkeypatch.setattr(TelemetrySnapshotBuilder, "build_day", record_day_build)
    tick_clock = Mock(return_value=now)
    # The heartbeat reads its own timestamp; day discovery/builds must reuse the
    # tick date even if the wall clock crosses midnight during transmission.
    snapshot_clock = Mock(side_effect=[now, now + timedelta(days=1)])
    monkeypatch.setattr(scheduler_module, "utcnow", tick_clock)
    monkeypatch.setattr("app.modules.telemetry.snapshot.utcnow", snapshot_clock)
    sender = AsyncMock()
    sender.send_snapshot.side_effect = lambda snapshot: events.append("heartbeat")
    sender.send_day.side_effect = lambda day: (today - day.utc_date).days != failed_offset

    await TelemetryScheduler(sender=sender)._tick_as_leader()

    expected_dates = [today - timedelta(days=offset) for offset in expected_offsets]
    assert events == ["heartbeat", *expected_dates]
    assert [call.args[0].utc_date for call in sender.send_day.await_args_list] == expected_dates
    assert sender.send_day.await_count <= 7
    tick_clock.assert_called_once_with()
    snapshot_clock.assert_called_once_with()
    async with get_background_session() as session:
        row = await session.get(DashboardSettings, 1)
        assert row is not None
        assert row.telemetry_day_acknowledged_date is not None
        assert row.telemetry_day_acknowledged_date.date() == today - timedelta(days=watermark_offset)


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

    async def completed_days(self, instance_id: str, *, acknowledged: date | None, today_utc: date):
        del instance_id
        return [
            day
            for day in self.days
            if today_utc - timedelta(days=7) <= day.utc_date < today_utc
            and (acknowledged is None or day.utc_date > acknowledged)
        ]


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

        async def completed_days(self, instance_id: str, *, acknowledged: date | None, today_utc: date):
            return await state.completed_days(instance_id, acknowledged=acknowledged, today_utc=today_utc)

    monkeypatch.setattr(scheduler_module, "get_background_session", lambda: _SessionContext())
    monkeypatch.setattr(scheduler_module, "TelemetryConsentStore", FakeStore)
    monkeypatch.setattr(scheduler_module, "TelemetrySnapshotBuilder", FakeBuilder)
    monkeypatch.setattr(
        scheduler_module, "utcnow", lambda: datetime.combine(days[0].utc_date + timedelta(days=1), datetime.min.time())
    )
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
    assert await state.completed_days("instance", acknowledged=date(2026, 9, 9), today_utc=date(2026, 9, 10)) == []


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
