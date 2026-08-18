from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Delete

from app.core.clients import proxy as core_proxy
from app.core.crypto import TokenEncryptor
from app.core.openai.requests import ResponsesRequest
from app.core.usage import live_hub
from app.core.usage.live_snapshots import LiveRateLimitSnapshot, LiveUsageWindow
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.db.session import SessionLocal
from app.modules.accounts import repository as accounts_repository_module
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage import live_ingest
from app.modules.usage import repository as usage_repository_module
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


async def _usage_rows_for(*account_ids: str) -> list[UsageHistory]:
    async with SessionLocal() as session:
        return list(
            (
                await session.execute(
                    select(UsageHistory)
                    .where(UsageHistory.account_id.in_(account_ids))
                    .order_by(UsageHistory.account_id, UsageHistory.window, UsageHistory.id)
                )
            )
            .scalars()
            .all()
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
async def test_live_ingestor_settles_snapshot_after_duplicate_account_consolidation(db_setup) -> None:
    del db_setup
    canonical_id = "acc_live_consolidated"
    duplicate_id = "acc_live_consolidated__copy"
    upstream_id = "workspace-live-consolidated"
    email = "live-consolidated@example.com"

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(
            _make_account(canonical_id, email, chatgpt_account_id=upstream_id),
            merge_by_email=False,
        )
        await repo.upsert(
            _make_account(duplicate_id, email, chatgpt_account_id=upstream_id),
            merge_by_email=False,
        )

    snapshot = _snapshot()
    ingestor = live_ingest.LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0.0)
    ingestor.publish(
        snapshot,
        account_id=duplicate_id,
        chatgpt_account_id=upstream_id,
    )
    queued = ingestor._queue.get_nowait()

    async with SessionLocal() as session:
        saved = await AccountsRepository(session).upsert(
            _make_account("acc_live_consolidated_reauth", email, chatgpt_account_id=upstream_id),
            merge_by_email=False,
            merge_by_chatgpt_identity=True,
        )
        assert saved.id == canonical_id
        assert await session.get(Account, duplicate_id) is None

    await ingestor._ingest(queued)

    rows = await _usage_rows_for(canonical_id, duplicate_id)
    assert [row.account_id for row in rows] == [canonical_id, canonical_id]
    primary_rows = [row for row in rows if row.window == "primary"]
    secondary_rows = [row for row in rows if row.window == "secondary"]
    assert len(primary_rows) == 1
    assert len(secondary_rows) == 1

    primary = primary_rows[0]
    secondary = secondary_rows[0]
    assert snapshot.primary is not None
    assert snapshot.secondary is not None
    assert primary.used_percent == pytest.approx(snapshot.primary.used_percent)
    assert primary.window_minutes == snapshot.primary.window_minutes
    assert primary.reset_at == snapshot.primary.reset_at
    assert primary.credits_has == snapshot.credits_has
    assert primary.credits_unlimited == snapshot.credits_unlimited
    assert primary.credits_balance == pytest.approx(snapshot.credits_balance)
    assert secondary.used_percent == pytest.approx(snapshot.secondary.used_percent)
    assert secondary.window_minutes == snapshot.secondary.window_minutes
    assert secondary.reset_at == snapshot.secondary.reset_at


@pytest.mark.asyncio
async def test_sse_publication_tap_settles_queued_duplicate_snapshot_under_canonical_account(
    monkeypatch: pytest.MonkeyPatch,
    db_setup,
) -> None:
    del db_setup
    canonical_id = "acc_live_sse_canonical"
    duplicate_id = "acc_live_sse_duplicate"
    upstream_id = "workspace-live-sse"
    email = "live-sse@example.com"
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account(canonical_id, email, chatgpt_account_id=upstream_id), merge_by_email=False)
        await repo.upsert(_make_account(duplicate_id, email, chatgpt_account_id=upstream_id), merge_by_email=False)

    rate_limit_event = (
        'data: {"type":"codex.rate_limits","rate_limits":'
        '{"primary":{"used_percent":33,"window_minutes":300,"reset_at":1700000300},'
        '"secondary":{"used_percent":44,"window_minutes":10080,"reset_at":1700604800}}}\n\n'
    )

    @asynccontextmanager
    async def _fake_http_session(_session):
        yield cast(Any, object())

    async def _fake_upstream_stream(**kwargs):
        assert kwargs["account_id"] == upstream_id
        assert kwargs["codex_lb_account_id"] == duplicate_id
        yield rate_limit_event

    monkeypatch.setattr(core_proxy, "lease_http_session", _fake_http_session)
    monkeypatch.setattr(core_proxy, "_stream_responses_with_session", _fake_upstream_stream)

    ingestor = live_ingest.LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0.0)
    ingest_completed = asyncio.Event()
    ingest_snapshot = ingestor._ingest

    async def _observed_ingest(item: live_ingest._QueuedSnapshot) -> None:
        await ingest_snapshot(item)
        ingest_completed.set()

    monkeypatch.setattr(ingestor, "_ingest", _observed_ingest)
    live_hub.register_live_usage_publisher(ingestor.publish)
    try:
        events = [
            event
            async for event in core_proxy.stream_responses(
                ResponsesRequest(model="gpt-5.1", instructions="", input="hello", stream=True),
                {},
                "access-token",
                upstream_id,
                session=cast(Any, object()),
                codex_lb_account_id=duplicate_id,
            )
        ]
        assert events == [rate_limit_event]

        async with SessionLocal() as session:
            saved = await AccountsRepository(session).upsert(
                _make_account("acc_live_sse_reauth", email, chatgpt_account_id=upstream_id),
                merge_by_email=False,
                merge_by_chatgpt_identity=True,
            )
            assert saved.id == canonical_id

        ingestor.start()
        await asyncio.wait_for(ingest_completed.wait(), timeout=5.0)
    finally:
        await ingestor.stop()
        live_hub.register_live_usage_publisher(None)

    rows = await _usage_rows_for(canonical_id, duplicate_id)
    assert [row.account_id for row in rows] == [canonical_id, canonical_id]
    assert {row.window for row in rows} == {"primary", "secondary"}
    assert {row.used_percent for row in rows} == {33.0, 44.0}


@pytest.mark.asyncio
async def test_postgresql_live_ingest_serializes_identity_membership_through_snapshot_commit(
    monkeypatch: pytest.MonkeyPatch,
    db_setup,
) -> None:
    del db_setup
    bind = SessionLocal.kw["bind"]
    if bind.dialect.name != "postgresql":
        pytest.skip("PostgreSQL transaction-lock regression")

    canonical_id = "acc_live_pg_canonical"
    duplicate_id = "acc_live_pg_duplicate"
    upstream_id = "workspace-live-pg-consolidated"
    email = "live-pg-consolidated@example.com"
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(
            _make_account(canonical_id, email, chatgpt_account_id=upstream_id),
            merge_by_email=False,
        )
        await repo.upsert(
            _make_account(duplicate_id, email, chatgpt_account_id=upstream_id),
            merge_by_email=False,
        )

    ingestor = live_ingest.LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0.0)
    ingestor.publish(
        _snapshot(),
        account_id=duplicate_id,
        chatgpt_account_id=upstream_id,
    )
    queued = ingestor._queue.get_nowait()

    settlement_commit_started = asyncio.Event()
    release_settlement_commit = asyncio.Event()
    writer_lock_attempted = asyncio.Event()
    release_writer_delete = asyncio.Event()
    settlement_lock_keys: list[int] = []
    writer_lock_keys: list[int] = []
    settlement_session = SessionLocal()
    writer_session = SessionLocal()
    settlement_task: asyncio.Task[None] | None = None
    writer_task: asyncio.Task[Account] | None = None

    def _lock_key(args: tuple[Any, ...], kwargs: dict[str, Any]) -> int:
        parameters = args[0] if args else kwargs.get("params")
        assert isinstance(parameters, dict)
        lock_key = parameters["lock_key"]
        assert isinstance(lock_key, int)
        return lock_key

    settlement_execute = settlement_session.execute

    async def _settlement_execute(statement: Any, *args: Any, **kwargs: Any):
        if "pg_advisory_xact_lock" in str(statement):
            settlement_lock_keys.append(_lock_key(args, kwargs))
        return await settlement_execute(statement, *args, **kwargs)

    settlement_commit = settlement_session.commit

    async def _settlement_commit() -> None:
        settlement_commit_started.set()
        await asyncio.wait_for(release_settlement_commit.wait(), timeout=5.0)
        await settlement_commit()

    writer_execute = writer_session.execute

    async def _writer_execute(statement: Any, *args: Any, **kwargs: Any):
        if "pg_advisory_xact_lock" in str(statement):
            writer_lock_keys.append(_lock_key(args, kwargs))
            writer_lock_attempted.set()
        if isinstance(statement, Delete) and statement.table.name == Account.__tablename__:
            await asyncio.wait_for(release_writer_delete.wait(), timeout=5.0)
        return await writer_execute(statement, *args, **kwargs)

    monkeypatch.setattr(settlement_session, "execute", _settlement_execute)
    monkeypatch.setattr(settlement_session, "commit", _settlement_commit)
    monkeypatch.setattr(writer_session, "execute", _writer_execute)

    @asynccontextmanager
    async def _settlement_session() -> AsyncIterator[AsyncSession]:
        yield settlement_session

    monkeypatch.setattr(live_ingest, "get_background_session", _settlement_session)

    try:
        settlement_task = asyncio.create_task(ingestor._ingest(queued))
        await asyncio.wait_for(settlement_commit_started.wait(), timeout=5.0)
        assert settlement_lock_keys, "settlement must take the upstream identity lock"

        writer_task = asyncio.create_task(
            AccountsRepository(writer_session).upsert(
                _make_account("acc_live_pg_reauth", email, chatgpt_account_id=upstream_id),
                merge_by_email=False,
                merge_by_chatgpt_identity=True,
            )
        )
        await asyncio.wait_for(writer_lock_attempted.wait(), timeout=5.0)

        assert writer_lock_keys[0] == settlement_lock_keys[0]
        release_settlement_commit.set()
        await asyncio.wait_for(settlement_task, timeout=5.0)
        release_writer_delete.set()
        saved = await asyncio.wait_for(writer_task, timeout=5.0)
        assert saved.id == canonical_id
    finally:
        release_settlement_commit.set()
        release_writer_delete.set()
        for task in (settlement_task, writer_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (settlement_task, writer_task) if task is not None),
            return_exceptions=True,
        )
        await settlement_session.rollback()
        await writer_session.rollback()
        await settlement_session.close()
        await writer_session.close()

    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account).order_by(Account.id))).scalars().all())
        rows = list((await session.execute(select(UsageHistory).order_by(UsageHistory.id))).scalars().all())
    assert [account.id for account in accounts] == [canonical_id]
    assert [row.account_id for row in rows] == [canonical_id, canonical_id]
    assert {row.window for row in rows} == {"primary", "secondary"}


@pytest.mark.asyncio
async def test_postgresql_live_ingest_waits_for_identity_consolidation_commit(
    monkeypatch: pytest.MonkeyPatch,
    db_setup,
) -> None:
    del db_setup
    bind = SessionLocal.kw["bind"]
    if bind.dialect.name != "postgresql":
        pytest.skip("PostgreSQL transaction-lock regression")

    canonical_id = "acc_live_pg_writer_first_canonical"
    duplicate_id = "acc_live_pg_writer_first_duplicate"
    upstream_id = "workspace-live-pg-writer-first"
    email = "live-pg-writer-first@example.com"
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account(canonical_id, email, chatgpt_account_id=upstream_id), merge_by_email=False)
        await repo.upsert(_make_account(duplicate_id, email, chatgpt_account_id=upstream_id), merge_by_email=False)

    ingestor = live_ingest.LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0.0)
    ingestor.publish(_snapshot(), account_id=duplicate_id, chatgpt_account_id=upstream_id)
    queued = ingestor._queue.get_nowait()

    writer_commit_started = asyncio.Event()
    release_writer_commit = asyncio.Event()
    settlement_lock_attempted = asyncio.Event()
    writer_session = SessionLocal()
    writer_commit = writer_session.commit
    real_settlement_lock = usage_repository_module.lock_postgresql_account_identities
    writer_task: asyncio.Task[Account] | None = None
    settlement_task: asyncio.Task[None] | None = None

    async def _writer_commit() -> None:
        writer_commit_started.set()
        await asyncio.wait_for(release_writer_commit.wait(), timeout=5.0)
        await writer_commit()

    async def _observed_settlement_lock(session: AsyncSession, identities):
        settlement_lock_attempted.set()
        return await real_settlement_lock(session, identities)

    monkeypatch.setattr(writer_session, "commit", _writer_commit)
    monkeypatch.setattr(usage_repository_module, "lock_postgresql_account_identities", _observed_settlement_lock)

    try:
        writer_task = asyncio.create_task(
            AccountsRepository(writer_session).upsert(
                _make_account("acc_live_pg_writer_first_reauth", email, chatgpt_account_id=upstream_id),
                merge_by_email=False,
                merge_by_chatgpt_identity=True,
            )
        )
        await asyncio.wait_for(writer_commit_started.wait(), timeout=5.0)

        settlement_task = asyncio.create_task(ingestor._ingest(queued))
        await asyncio.wait_for(settlement_lock_attempted.wait(), timeout=5.0)
        assert not settlement_task.done()

        release_writer_commit.set()
        saved = await asyncio.wait_for(writer_task, timeout=5.0)
        await asyncio.wait_for(settlement_task, timeout=5.0)
        assert saved.id == canonical_id
    finally:
        release_writer_commit.set()
        for task in (writer_task, settlement_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (writer_task, settlement_task) if task is not None),
            return_exceptions=True,
        )
        await writer_session.rollback()
        await writer_session.close()

    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account).order_by(Account.id))).scalars().all())
        rows = list((await session.execute(select(UsageHistory).order_by(UsageHistory.id))).scalars().all())
    assert [account.id for account in accounts] == [canonical_id]
    assert [row.account_id for row in rows] == [canonical_id, canonical_id]
    assert {row.window for row in rows} == {"primary", "secondary"}


@pytest.mark.asyncio
async def test_postgresql_live_ingest_recovers_when_current_identity_reconciliation_wins_owner_lock(
    monkeypatch: pytest.MonkeyPatch,
    db_setup,
) -> None:
    del db_setup
    bind = SessionLocal.kw["bind"]
    if bind.dialect.name != "postgresql":
        pytest.skip("PostgreSQL selected-owner lock regression")

    canonical_id = "acc_live_pg_current_identity_canonical"
    selected_id = "acc_live_pg_current_identity_selected"
    queued_identity = "workspace-live-pg-current-before"
    current_identity = "workspace-live-pg-current-after"
    email = "live-pg-current-identity@example.com"
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(
            _make_account(canonical_id, email, chatgpt_account_id=current_identity),
            merge_by_email=False,
        )
        selected = await repo.upsert(
            _make_account(selected_id, email, chatgpt_account_id=queued_identity),
            merge_by_email=False,
        )

    ingestor = live_ingest.LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0.0)
    ingestor.publish(
        _snapshot(),
        account_id=selected_id,
        chatgpt_account_id=queued_identity,
    )
    queued = ingestor._queue.get_nowait()

    async with SessionLocal() as session:
        moved = await AccountsRepository(session).rotate_tokens(
            selected.id,
            selected.access_token_encrypted,
            selected.refresh_token_encrypted,
            selected.id_token_encrypted,
            utcnow(),
            expected_refresh_token_encrypted=selected.refresh_token_encrypted,
            chatgpt_account_id=current_identity,
        )
        assert moved is True

    reconciliation_commit_started = asyncio.Event()
    release_reconciliation_commit = asyncio.Event()
    settlement_local_lookup_started = asyncio.Event()
    settlement_session = SessionLocal()
    reconciliation_session = SessionLocal()
    settlement_task: asyncio.Task[None] | None = None
    reconciliation_task: asyncio.Task[Account] | None = None
    settlement_execute = settlement_session.execute
    reconciliation_commit = reconciliation_session.commit

    async def _settlement_execute(statement: Any, *args: Any, **kwargs: Any):
        sql = str(statement)
        if sql.startswith("SELECT accounts.id, accounts.chatgpt_account_id") and "WHERE accounts.id =" in sql:
            settlement_local_lookup_started.set()
        return await settlement_execute(statement, *args, **kwargs)

    async def _reconciliation_commit() -> None:
        reconciliation_commit_started.set()
        await asyncio.wait_for(release_reconciliation_commit.wait(), timeout=5.0)
        await reconciliation_commit()

    monkeypatch.setattr(settlement_session, "execute", _settlement_execute)
    monkeypatch.setattr(reconciliation_session, "commit", _reconciliation_commit)

    @asynccontextmanager
    async def _settlement_session() -> AsyncIterator[AsyncSession]:
        yield settlement_session

    monkeypatch.setattr(live_ingest, "get_background_session", _settlement_session)

    try:
        reconciliation_task = asyncio.create_task(
            AccountsRepository(reconciliation_session).upsert(
                _make_account("acc_live_pg_current_identity_reauth", email, chatgpt_account_id=current_identity),
                merge_by_email=False,
                merge_by_chatgpt_identity=True,
            )
        )
        await asyncio.wait_for(reconciliation_commit_started.wait(), timeout=5.0)

        settlement_task = asyncio.create_task(ingestor._ingest(queued))
        await asyncio.wait_for(settlement_local_lookup_started.wait(), timeout=5.0)
        assert not settlement_task.done()

        release_reconciliation_commit.set()
        saved = await asyncio.wait_for(reconciliation_task, timeout=5.0)
        await asyncio.wait_for(settlement_task, timeout=5.0)
        assert saved.id == canonical_id
    finally:
        release_reconciliation_commit.set()
        for task in (settlement_task, reconciliation_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (settlement_task, reconciliation_task) if task is not None),
            return_exceptions=True,
        )
        await settlement_session.rollback()
        await reconciliation_session.rollback()
        await settlement_session.close()
        await reconciliation_session.close()

    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account).order_by(Account.id))).scalars().all())
        rows = list((await session.execute(select(UsageHistory).order_by(UsageHistory.id))).scalars().all())
    assert [account.id for account in accounts] == [canonical_id]
    assert [account.chatgpt_account_id for account in accounts] == [current_identity]
    assert [row.account_id for row in rows] == [canonical_id, canonical_id]
    assert {row.window for row in rows} == {"primary", "secondary"}
    assert {row.used_percent for row in rows} == {33.0, 44.0}


@pytest.mark.asyncio
async def test_postgresql_opposite_identity_moves_use_one_sorted_lock_order(
    monkeypatch: pytest.MonkeyPatch,
    db_setup,
) -> None:
    del db_setup
    bind = SessionLocal.kw["bind"]
    if bind.dialect.name != "postgresql":
        pytest.skip("PostgreSQL transaction-lock regression")

    first = _make_account("acc_identity_move_a", "identity-move-a@example.com", chatgpt_account_id="workspace-a")
    second = _make_account("acc_identity_move_b", "identity-move-b@example.com", chatgpt_account_id="workspace-b")
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(first, merge_by_email=False)
        await repo.upsert(second, merge_by_email=False)

    real_identity_lock = accounts_repository_module.lock_postgresql_account_identities
    arrival_guard = asyncio.Lock()
    both_arrived = asyncio.Event()
    arrival_count = 0

    async def _synchronized_identity_lock(session: AsyncSession, identities):
        nonlocal arrival_count
        async with arrival_guard:
            arrival_count += 1
            if arrival_count == 2:
                both_arrived.set()
        await asyncio.wait_for(both_arrived.wait(), timeout=5.0)
        return await real_identity_lock(session, identities)

    monkeypatch.setattr(accounts_repository_module, "lock_postgresql_account_identities", _synchronized_identity_lock)

    async def _move(account: Account, incoming_identity: str) -> bool:
        async with SessionLocal() as session:
            return await AccountsRepository(session).rotate_tokens(
                account.id,
                account.access_token_encrypted,
                account.refresh_token_encrypted,
                account.id_token_encrypted,
                utcnow(),
                expected_refresh_token_encrypted=account.refresh_token_encrypted,
                chatgpt_account_id=incoming_identity,
            )

    moved_first, moved_second = await asyncio.wait_for(
        asyncio.gather(_move(first, "workspace-b"), _move(second, "workspace-a")),
        timeout=5.0,
    )
    assert moved_first is True
    assert moved_second is True

    async with SessionLocal() as session:
        identities = {
            account_id: chatgpt_account_id
            for account_id, chatgpt_account_id in (
                await session.execute(select(Account.id, Account.chatgpt_account_id))
            ).all()
        }
    assert identities == {
        first.id: "workspace-b",
        second.id: "workspace-a",
    }


@pytest.mark.asyncio
async def test_live_ingestor_prefers_valid_local_owner_over_upstream_fallback(db_setup) -> None:
    del db_setup
    local_id = "acc_live_valid_local"
    sibling_id = "acc_live_valid_local_sibling"
    upstream_id = "workspace-live-shared"
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(
            _make_account(local_id, "live-valid-local@example.com", chatgpt_account_id=upstream_id),
            merge_by_email=False,
        )
        await repo.upsert(
            _make_account(sibling_id, "live-valid-sibling@example.com", chatgpt_account_id=upstream_id),
            merge_by_email=False,
        )

    snapshot = _snapshot()
    ingestor = live_ingest.LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0.0)
    ingestor.publish(snapshot, account_id=local_id, chatgpt_account_id=upstream_id)
    queued = ingestor._queue.get_nowait()

    await ingestor._ingest(queued)

    rows = await _usage_rows_for(local_id, sibling_id)
    assert len(rows) == 2
    assert {row.account_id for row in rows} == {local_id}
    assert {row.window for row in rows} == {"primary", "secondary"}


@pytest.mark.asyncio
async def test_live_ingestor_resolves_chatgpt_account_id(db_setup) -> None:
    del db_setup
    account_id = "acc_live_resolved"
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            _make_account(account_id, "live-resolved@example.com", chatgpt_account_id="workspace-live-1")
        )

    ingestor = live_ingest.LiveUsageIngestor(queue_size=8, write_min_interval_seconds=0.0)
    ingestor.publish(_snapshot(), chatgpt_account_id="workspace-live-1")
    queued = ingestor._queue.get_nowait()

    await ingestor._ingest(queued)

    rows = await _usage_rows_for(account_id)
    assert len(rows) == 2
    assert {row.account_id for row in rows} == {account_id}
    assert {row.window for row in rows} == {"primary", "secondary"}


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


@pytest.mark.asyncio
async def test_nested_lifespan_stop_does_not_orphan_or_kill_the_outer_ingestor(db_setup) -> None:
    del db_setup

    # Two app lifespans can be live in one process: the suite's async_client
    # runs one on the session loop while a test opens a TestClient whose
    # portal runs another. Each lifespan owns the instance start returned and
    # stops exactly that instance. Before instance-scoped stop, the nested
    # startup overwrote the module global, orphaned the outer ingestor as an
    # unreferenced cycle, and the cyclic GC destroyed its consumer mid-await
    # ("cannot reuse already awaited coroutine" — issue #1755's integration
    # signature); the nested shutdown then cleared the global so the outer
    # shutdown stopped nothing. And a nested shutdown that merely cleared the
    # registration would leave the still-running outer ingestor deaf: it must
    # instead restore the outer instance as the current registration.
    def _pending_consumers() -> list[asyncio.Task[object]]:
        return [t for t in asyncio.all_tasks() if not t.done() and t.get_name() == "live-usage-ingestor"]

    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("acc_live_nested", "live-nested@example.com"))

    outer = live_ingest.start_live_usage_ingestor()
    assert outer is not None
    inner = live_ingest.start_live_usage_ingestor()
    assert inner is not None and inner is not outer
    assert live_ingest._ingestor is inner

    # Nested lifespan shutdown: releases the registration it owns and
    # restores the still-running outer instance in its place.
    await live_ingest.stop_live_usage_ingestor(inner)
    assert live_ingest._ingestor is outer
    assert live_ingest._displaced_ingestors == []
    assert outer._consumer is not None and not outer._consumer.done()

    # Outer ingestion RESUMES: a hub publication after the nested exit must
    # flow to the outer instance and be ingested end to end.
    live_hub.publish_live_usage(_snapshot(), account_id="acc_live_nested")
    primary, secondary = await _wait_for_rows("acc_live_nested")
    assert primary is not None and secondary is not None
    assert primary.used_percent == pytest.approx(33.0)

    # Outer lifespan shutdown: stops its own instance, clears the restored
    # registration, and no consumer survives for the suite fence.
    await live_ingest.stop_live_usage_ingestor(outer)
    assert live_ingest._ingestor is None
    assert live_hub._publisher is None
    assert _pending_consumers() == []
