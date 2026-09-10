from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta

import pytest

from app.core.usage.live_snapshots import LiveUsageWindow
from app.core.usage.models import UsagePayload
from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import Account, UsageHistory
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.limit_warmup.repository import LimitWarmupRepository
from app.modules.settings.repository import SettingsRepository
from app.modules.usage import repository as usage_repository
from app.modules.usage import updater
from app.modules.usage.live_ingest import LiveUsageIngestor
from app.modules.usage.repository import AdditionalUsageRepository
from tests.integration.live_reset_helpers import (
    prepare_live_reset,
    publish_and_wait,
    record_warmup_sends,
    run_scheduler_ticks,
    usage_payload,
)

pytestmark = [pytest.mark.integration, pytest.mark.usage_refresh_request_path]


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["primary", "secondary", "monthly"])
@pytest.mark.parametrize("prior_status", [None, "pending", "succeeded", "failed", "skipped"])
async def test_live_reset_survives_freshness_skip_and_scheduler_restart(
    db_setup: bool, monkeypatch: pytest.MonkeyPatch, window: str, prior_status: str | None
) -> None:
    account, snapshot, reset_at = await prepare_live_reset(window, prior_status=prior_status)

    sends = record_warmup_sends(monkeypatch)
    polls: list[str] = []

    async def fetch_usage(**kwargs: object) -> UsagePayload:
        polls.append("poll")
        return usage_payload(snapshot, account.plan_type)

    monkeypatch.setattr(updater, "fetch_usage", fetch_usage)
    ingestor = LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0)
    ingestor.start()
    try:
        await publish_and_wait(ingestor, snapshot, account.id, window)
        # More writes must not erase the earlier consecutive reset pair.
        count = 240 if window == "secondary" and prior_status is None else 2
        for index in range(count):
            target = snapshot.secondary if window == "secondary" else snapshot.primary
            assert target is not None
            updated = replace(target, used_percent=(index + 1) / 100)
            snapshot = replace(snapshot, **{"secondary" if window == "secondary" else "primary": updated})
            await publish_and_wait(ingestor, snapshot, account.id, window)
        await run_scheduler_ticks(monkeypatch, workers=2)
        expected_sends = [account.id] if prior_status is None else []
        assert polls == []
        assert sends == expected_sends

        await publish_and_wait(ingestor, snapshot, account.id, window)
        await run_scheduler_ticks(monkeypatch)
        assert sends == expected_sends
        async with SessionLocal() as session:
            original_attempt = (await LimitWarmupRepository(session).latest_by_account([account.id]))[account.id]

        # Expire only the updater's freshness clock, then exercise a real poll.
        poll_time = utcnow() + timedelta(seconds=61)
        monkeypatch.setattr(updater, "utcnow", lambda: poll_time)
        await run_scheduler_ticks(monkeypatch)
        assert polls == ["poll"]
        assert sends == expected_sends
        async with SessionLocal() as session:
            attempt = (await LimitWarmupRepository(session).latest_by_account([account.id]))[account.id]
        assert attempt.id == original_attempt.id
        assert (attempt.window, attempt.reset_at, attempt.status) == (window, reset_at, prior_status or "succeeded")
    finally:
        await ingestor.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "condition",
    [
        "missing",
        "jitter",
        "superseded",
        "exhausted",
        "below_availability",
        "opted_out",
        "disabled",
        "other_window",
        "expired",
    ],
)
async def test_live_reset_recovery_respects_current_eligibility(
    db_setup: bool, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, condition: str
) -> None:
    account, snapshot, reset_at = await prepare_live_reset("secondary", baseline=condition)
    sends = record_warmup_sends(monkeypatch)

    async def fetch_usage(**kwargs: object) -> UsagePayload:
        return usage_payload(snapshot, account.plan_type)

    monkeypatch.setattr(updater, "fetch_usage", fetch_usage)
    ingestor = LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0)
    ingestor.start()
    try:
        await publish_and_wait(ingestor, snapshot, account.id, "secondary")
        secondary = snapshot.secondary
        assert secondary is not None
        if condition == "superseded":
            secondary = replace(secondary, reset_at=reset_at + 3600, used_percent=2.0)
        elif condition in {"exhausted", "below_availability"}:
            secondary = replace(secondary, used_percent=100.0 if condition == "exhausted" else 21.0)
        snapshot = replace(snapshot, secondary=secondary)
        await publish_and_wait(ingestor, snapshot, account.id, "secondary")
        async with SessionLocal() as session:
            await SettingsRepository(session).update(
                limit_warmup_enabled=condition != "disabled",
                limit_warmup_windows="primary" if condition == "other_window" else "secondary",
                limit_warmup_min_available_percent=80.0,
            )
            if condition == "opted_out":
                await AccountsRepository(session).update_limit_warmup_enabled(account.id, False)
        if condition == "expired":
            expired_now = utcnow() + timedelta(days=8)
            monkeypatch.setattr(updater, "utcnow", lambda: expired_now)
        await run_scheduler_ticks(monkeypatch)
        assert sends == []
        async with SessionLocal() as session:
            assert await LimitWarmupRepository(session).latest_by_account([account.id]) == {}
        assert "Usage refresh loop failed" not in caplog.text
    finally:
        await ingestor.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("window_minutes,deadline_offset", [(None, 0), (10_080, 120)])
async def test_live_scheduled_reset_does_not_require_an_exact_new_window_start(
    db_setup: bool, monkeypatch: pytest.MonkeyPatch, window_minutes: int | None, deadline_offset: int
) -> None:
    account, snapshot, reset_at = await prepare_live_reset("secondary")
    secondary = snapshot.secondary
    assert secondary is not None
    snapshot = replace(
        snapshot, secondary=replace(secondary, window_minutes=window_minutes, reset_at=reset_at + deadline_offset)
    )
    sends = record_warmup_sends(monkeypatch)

    async def fetch_usage(**kwargs: object) -> UsagePayload:
        pytest.fail("Fresh live quota must skip polling")

    monkeypatch.setattr(updater, "fetch_usage", fetch_usage)
    ingestor = LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0)
    ingestor.start()
    try:
        await publish_and_wait(ingestor, snapshot, account.id, "secondary")
        await run_scheduler_ticks(monkeypatch)
        assert sends == [account.id]
        async with SessionLocal() as session:
            attempt = (await LimitWarmupRepository(session).latest_by_account([account.id]))[account.id]
        assert attempt.reset_at == reset_at + deadline_offset
    finally:
        await ingestor.stop()


@pytest.mark.asyncio
async def test_live_reset_survives_a_late_restart_before_its_delayed_deadline(
    db_setup: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    account, snapshot, reset_at = await prepare_live_reset("secondary")
    secondary = snapshot.secondary
    assert secondary is not None
    snapshot = replace(snapshot, secondary=replace(secondary, reset_at=reset_at + 120))
    sends = record_warmup_sends(monkeypatch)

    async def fetch_usage(**kwargs: object) -> UsagePayload:
        pytest.fail("The newly ingested snapshot must keep the restarted scheduler's poll fresh")

    monkeypatch.setattr(updater, "fetch_usage", fetch_usage)
    ingestor = LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0)
    ingestor.start()
    try:
        await publish_and_wait(ingestor, snapshot, account.id, "secondary")
        late_now = utcnow() + timedelta(seconds=604_810)
        monkeypatch.setattr(updater, "utcnow", lambda: late_now)
        # Advance the persistence clock as well, so this is an actual fresh live write.
        monkeypatch.setattr(usage_repository, "utcnow", lambda: late_now)
        snapshot = replace(snapshot, primary=LiveUsageWindow(0.0, 300, int(naive_utc_to_epoch(late_now)) + 18_000))
        await publish_and_wait(ingestor, snapshot, account.id, "secondary")
        async with SessionLocal() as session:
            await AdditionalUsageRepository(session).add_entry(
                account.id,
                "codex",
                "codex",
                "secondary",
                0.0,
                reset_at=reset_at + 120,
                window_minutes=10_080,
                recorded_at=late_now,
                quota_key="codex",
            )
        await run_scheduler_ticks(monkeypatch)
        assert sends == [account.id]
        async with SessionLocal() as session:
            attempt = (await LimitWarmupRepository(session).latest_by_account([account.id]))[account.id]
        assert attempt.reset_at == reset_at + 120
    finally:
        await ingestor.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["primary", "monthly"])
async def test_live_snapshot_during_skipped_poll_does_not_enable_non_reset_triggers(
    db_setup: bool, monkeypatch: pytest.MonkeyPatch, window: str
) -> None:
    account, snapshot, _ = await prepare_live_reset(window, baseline="missing")
    sends = record_warmup_sends(monkeypatch)

    async def fetch_usage(**kwargs: object) -> UsagePayload:
        pytest.fail("The real updater must skip fresh live usage")

    monkeypatch.setattr(updater, "fetch_usage", fetch_usage)
    ingestor = LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0)
    original_refresh = updater.UsageUpdater.refresh_accounts

    async def refresh_then_ingest(
        self: updater.UsageUpdater, accounts: list[Account], latest_usage: Mapping[str, UsageHistory]
    ) -> bool:
        written = await original_refresh(self, accounts, latest_usage)
        assert written is False
        await publish_and_wait(ingestor, snapshot, account.id, window)
        return written

    monkeypatch.setattr(updater.UsageUpdater, "refresh_accounts", refresh_then_ingest)
    async with SessionLocal() as session:
        await SettingsRepository(session).update(
            limit_warmup_windows="secondary", limit_warmup_staggered_idle_enabled=window == "primary"
        )
    ingestor.start()
    try:
        await publish_and_wait(ingestor, snapshot, account.id, window)
        await run_scheduler_ticks(monkeypatch)
        assert sends == []
        async with SessionLocal() as session:
            assert await LimitWarmupRepository(session).latest_by_account([account.id]) == {}
    finally:
        await ingestor.stop()
