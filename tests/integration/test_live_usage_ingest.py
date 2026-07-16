from __future__ import annotations

import asyncio

import pytest

from app.core.crypto import TokenEncryptor
from app.core.usage import live_hub
from app.core.usage.live_snapshots import LiveRateLimitSnapshot, LiveUsageWindow
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage import live_ingest
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


def _make_account(account_id: str, email: str, *, chatgpt_account_id: str | None = None) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=chatgpt_account_id,
        email=email,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
    )


def _snapshot() -> LiveRateLimitSnapshot:
    now_epoch = int(utcnow().timestamp())
    return LiveRateLimitSnapshot(
        primary=LiveUsageWindow(used_percent=33.0, window_minutes=300, reset_at=now_epoch + 300),
        secondary=LiveUsageWindow(used_percent=44.0, window_minutes=10080, reset_at=now_epoch + 5 * 24 * 3600),
        credits_has=True,
        credits_unlimited=False,
        credits_balance=7.5,
    )


async def _wait_for_rows(account_id: str, *, timeout: float = 5.0) -> tuple[UsageHistory | None, UsageHistory | None]:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        async with SessionLocal() as session:
            repo = UsageRepository(session)
            primary = await repo.latest_entry_for_account(account_id, window="primary")
            secondary = await repo.latest_entry_for_account(account_id, window="secondary")
        if primary is not None and secondary is not None:
            return primary, secondary
        if asyncio.get_event_loop().time() >= deadline:
            return primary, secondary
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_live_ingestor_writes_usage_rows_for_internal_account(db_setup) -> None:
    del db_setup
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("acc_live_internal", "live-internal@example.com"))

    ingestor = live_ingest.LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0.0)
    ingestor.start()
    try:
        ingestor.publish(_snapshot(), account_id="acc_live_internal")
        primary, secondary = await _wait_for_rows("acc_live_internal")
    finally:
        await ingestor.stop()

    assert primary is not None and secondary is not None
    assert primary.used_percent == pytest.approx(33.0)
    assert primary.window_minutes == 300
    assert primary.credits_has is True
    assert primary.credits_balance == pytest.approx(7.5)
    assert secondary.used_percent == pytest.approx(44.0)
    assert secondary.window_minutes == 10080


@pytest.mark.asyncio
async def test_live_ingestor_invalidates_rate_limit_header_cache(monkeypatch, db_setup) -> None:
    del db_setup
    from app.modules.usage import live_ingest as live_ingest_module

    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("acc_live_headers", "live-headers@example.com"))

    invalidations: list[int] = []

    class _SpyHeadersCache:
        async def invalidate(self) -> None:
            invalidations.append(1)

    monkeypatch.setattr(live_ingest_module, "get_rate_limit_headers_cache", lambda: _SpyHeadersCache())

    ingestor = live_ingest.LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0.0)
    ingestor.start()
    try:
        ingestor.publish(_snapshot(), account_id="acc_live_headers")
        primary, secondary = await _wait_for_rows("acc_live_headers")
        # The row write and the cache invalidation are two consecutive steps of
        # the SAME ingest coroutine: the rows commit first, then
        # _invalidate_caches_now runs and appends here. _wait_for_rows only
        # proves the write landed, so on a loaded runner the consumer can still
        # be scheduled between the commit and the invalidation when we observe
        # the rows. Wait for the invalidation itself before stopping so stop()
        # cannot cancel the consumer between the two steps and drop the
        # invalidation (previously flaked as ``assert [] == [1]``). Only one
        # snapshot is published (and a re-publish would be de-duplicated), so
        # exactly one immediate invalidation is expected.
        deadline = asyncio.get_event_loop().time() + 5.0
        while not invalidations and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.05)
    finally:
        await ingestor.stop()

    assert primary is not None and secondary is not None
    assert invalidations == [1]


@pytest.mark.asyncio
async def test_live_ingestor_trailing_invalidation_covers_throttled_writes(monkeypatch, db_setup) -> None:
    del db_setup
    from app.modules.usage import live_ingest as live_ingest_module

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account("acc_live_trail_a", "live-trail-a@example.com"))
        await repo.upsert(_make_account("acc_live_trail_b", "live-trail-b@example.com"))

    invalidations: list[float] = []

    class _SpyHeadersCache:
        async def invalidate(self) -> None:
            invalidations.append(asyncio.get_event_loop().time())

    monkeypatch.setattr(live_ingest_module, "get_rate_limit_headers_cache", lambda: _SpyHeadersCache())
    monkeypatch.setattr(live_ingest_module, "_CACHE_INVALIDATION_MIN_INTERVAL_SECONDS", 0.2)

    ingestor = live_ingest.LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0.0)
    ingestor.start()
    try:
        # Two accounts write inside one throttle window: the second write
        # must still be covered by a trailing invalidation.
        ingestor.publish(_snapshot(), account_id="acc_live_trail_a")
        ingestor.publish(_snapshot(), account_id="acc_live_trail_b")
        deadline = asyncio.get_event_loop().time() + 5.0
        while len(invalidations) < 2 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.05)
    finally:
        await ingestor.stop()

    assert len(invalidations) >= 2


@pytest.mark.asyncio
async def test_live_ingestor_carries_credits_on_secondary_only_snapshots(db_setup) -> None:
    del db_setup
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            _make_account("acc_live_secondary_only", "live-secondary-only@example.com")
        )

    now_epoch = int(utcnow().timestamp())
    snapshot = LiveRateLimitSnapshot(
        primary=None,
        secondary=LiveUsageWindow(used_percent=44.0, window_minutes=10080, reset_at=now_epoch + 5 * 24 * 3600),
        credits_has=True,
        credits_unlimited=False,
        credits_balance=9.25,
    )

    ingestor = live_ingest.LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0.0)
    ingestor.start()
    try:
        ingestor.publish(snapshot, account_id="acc_live_secondary_only")
        deadline = asyncio.get_event_loop().time() + 5.0
        secondary = None
        while secondary is None and asyncio.get_event_loop().time() < deadline:
            async with SessionLocal() as session:
                secondary = await UsageRepository(session).latest_entry_for_account(
                    "acc_live_secondary_only", window="secondary"
                )
            if secondary is None:
                await asyncio.sleep(0.05)
    finally:
        await ingestor.stop()

    assert secondary is not None
    assert secondary.credits_has is True
    assert secondary.credits_balance == pytest.approx(9.25)


@pytest.mark.asyncio
async def test_live_ingestor_normalizes_monthly_only_snapshots(db_setup) -> None:
    del db_setup
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("acc_live_monthly", "live-monthly@example.com"))

    now_epoch = int(utcnow().timestamp())
    # The monthly-only free-plan shape: a lone primary window with the
    # monthly duration must land in the monthly slot like the poller does.
    snapshot = LiveRateLimitSnapshot(
        primary=LiveUsageWindow(used_percent=42.0, window_minutes=43200, reset_at=now_epoch + 30 * 24 * 3600),
        secondary=None,
        credits_has=True,
        credits_unlimited=False,
        credits_balance=8.75,
    )

    ingestor = live_ingest.LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0.0)
    ingestor.start()
    try:
        ingestor.publish(snapshot, account_id="acc_live_monthly")
        deadline = asyncio.get_event_loop().time() + 5.0
        monthly = None
        while monthly is None and asyncio.get_event_loop().time() < deadline:
            async with SessionLocal() as session:
                monthly = await UsageRepository(session).latest_entry_for_account("acc_live_monthly", window="monthly")
            if monthly is None:
                await asyncio.sleep(0.05)
        async with SessionLocal() as session:
            primary = await UsageRepository(session).latest_entry_for_account("acc_live_monthly", window="primary")
    finally:
        await ingestor.stop()

    assert monthly is not None
    assert monthly.used_percent == pytest.approx(42.0)
    assert monthly.credits_has is True
    assert primary is None


@pytest.mark.asyncio
async def test_live_ingestor_resolves_chatgpt_account_id(db_setup) -> None:
    del db_setup
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            _make_account("acc_live_resolved", "live-resolved@example.com", chatgpt_account_id="workspace-live-1")
        )

    ingestor = live_ingest.LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0.0)
    ingestor.start()
    try:
        ingestor.publish(_snapshot(), chatgpt_account_id="workspace-live-1")
        primary, secondary = await _wait_for_rows("acc_live_resolved")
    finally:
        await ingestor.stop()

    assert primary is not None and secondary is not None


@pytest.mark.asyncio
async def test_live_ingestion_kill_switch_disables_publishing(monkeypatch, db_setup) -> None:
    del db_setup
    from app.core.config.settings import get_settings

    monkeypatch.setenv("CODEX_LB_LIVE_USAGE_INGESTION_ENABLED", "false")
    get_settings.cache_clear()
    try:
        assert live_ingest.start_live_usage_ingestor() is None
        captured: list[object] = []
        live_hub.register_live_usage_publisher(None)
        live_hub.publish_live_usage(_snapshot(), account_id="acc-any")
        assert captured == []
    finally:
        await live_ingest.stop_live_usage_ingestor()
        get_settings.cache_clear()
