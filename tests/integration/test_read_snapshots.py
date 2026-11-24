from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select, update

from app.core.scheduling.leader_election import LeaderElection
from app.core.types import JsonValue
from app.core.utils.time import utcnow
from app.db.models import ReadSnapshot, SchedulerLeader
from app.db.session import SessionLocal
from app.modules.dashboard.service import DashboardService
from app.modules.read_snapshots import repository as repository_module
from app.modules.read_snapshots import scheduler as scheduler_module
from app.modules.read_snapshots.keys import report_parameters, resolve_parameters
from app.modules.read_snapshots.repository import (
    KIND_CAPACITY,
    MAX_ENTRIES,
    SCHEMA_VERSION,
    SnapshotCapacityError,
    SnapshotRepository,
    encode_parameters,
)
from app.modules.read_snapshots.scheduler import ReadSnapshotScheduler
from app.modules.read_snapshots.service import SnapshotReader, SnapshotWarmingError
from app.modules.reports.service import ReportsService

pytestmark = pytest.mark.integration


async def _register(kind="dashboard", parameters=None):
    async with SessionLocal() as session:
        return await SnapshotRepository(session).register(kind, parameters or {})


async def _read(key):
    async with SessionLocal() as session:
        return await SnapshotRepository(session).read(key, interval_seconds=300)


@pytest.mark.asyncio
async def test_http_misses_never_run_collectors(async_client, db_setup, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("HTTP read entered an analytical collector")

    for cls, methods in (
        (DashboardService, ("get_overview", "get_projections")),
        (ReportsService, ("get_reports", "get_options", "get_thread_identity")),
    ):
        for method in methods:
            monkeypatch.setattr(cls, method, forbidden)
    for path in (
        "/api/dashboard/overview",
        "/api/dashboard/projections",
        "/api/reports",
        "/api/reports/options",
        "/api/reports/thread-identity",
    ):
        response = await async_client.get(path)
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "read_snapshot_warming"
    async with SessionLocal() as session:
        rows = (await session.execute(select(ReadSnapshot).where(ReadSnapshot.kind != "registry"))).scalars().all()
        assert rows
        assert all(row.payload_json is None and row.generation == 0 for row in rows)


@pytest.mark.asyncio
async def test_proactive_worker_warms_all_ranges_and_presets_before_http(async_client, db_setup, monkeypatch):
    worker = ReadSnapshotScheduler()
    await worker.leader.run_if_leader(worker.refresh_due)
    assert worker.leader.is_leader

    async def forbidden(*args, **kwargs):
        pytest.fail("Published HTTP read recalculated history")

    for cls, methods in (
        (DashboardService, ("get_overview", "get_projections")),
        (ReportsService, ("get_reports", "get_options", "get_thread_identity")),
    ):
        for method in methods:
            monkeypatch.setattr(cls, method, forbidden)
    for timeframe in ("1d", "7d", "30d"):
        for endpoint in ("overview", "projections"):
            response = await async_client.get(f"/api/dashboard/{endpoint}", params={"timeframe": timeframe})
            assert response.status_code == 200, response.text
            assert response.json()["snapshot"]["generation"] == 1
            assert response.json()["snapshot"]["generatedAt"]
    for days in (7, 30, 90):
        parameters = resolve_parameters({"days": days, "timezone": "UTC"})
        query = {"start_date": parameters["start"], "end_date": parameters["end"], "timezone": "UTC"}
        for suffix in ("", "/options", "/thread-identity"):
            response = await async_client.get("/api/reports" + suffix, params=query)
            assert response.status_code == 200, response.text
            assert response.json()["snapshot"]["generation"] == 1
        report = (await async_client.get("/api/reports", params=query)).json()
        assert report["speedMetricsAvailable"] is (days <= 7)
    assert await worker.stop()


@pytest.mark.asyncio
async def test_last_good_is_shared_across_replicas_and_failure_does_not_erase_it(db_setup, monkeypatch):
    worker = ReadSnapshotScheduler()
    assert await worker.leader.try_acquire()
    key = await _register()

    async def collect(session):
        return {"complete": [1, 2, 3]}

    monkeypatch.setattr(scheduler_module, "dashboard_snapshot", collect)
    await worker.refresh_one(key, "dashboard", {})
    initial = await _read(key)
    assert initial.payload == {"complete": [1, 2, 3]}
    async with SessionLocal() as session:
        await session.execute(
            update(ReadSnapshot)
            .where(ReadSnapshot.snapshot_key == key)
            .values(generated_at=utcnow() - timedelta(minutes=7))
        )
        await session.commit()

    async def fail(session):
        raise RuntimeError("private database diagnostic must not leak")

    monkeypatch.setattr(scheduler_module, "dashboard_snapshot", fail)
    await worker.refresh_one(key, "dashboard", {})
    after = await _read(key)
    assert after.payload == initial.payload
    assert after.generation == initial.generation
    assert after.stale and not after.refreshing
    assert after.last_error == "RuntimeError"
    assert await worker.stop()
    restarted = ReadSnapshotScheduler()
    assert await restarted.leader.try_acquire()
    assert (await _read(key)).payload == initial.payload
    assert await restarted.stop()


@pytest.mark.asyncio
async def test_expired_and_superseded_producers_cannot_publish(db_setup):
    old = LeaderElection("snapshot-old", lease_id=100 + SCHEMA_VERSION)
    new = LeaderElection("snapshot-new", lease_id=100 + SCHEMA_VERSION)
    assert await old.try_acquire()
    assert not await new.try_acquire()
    key = await _register()
    async with SessionLocal() as session:
        repository = SnapshotRepository(session)
        generation = await repository.claim(key, old)
        assert generation is not None
        assert generation == 1
        assert await repository.publish(
            key, generation, old, {"value": "last-good"}, interval_seconds=300, duration_ms=1
        )
        abandoned = await repository.claim(key, old)
        assert abandoned is not None
    async with SessionLocal() as session:
        await session.execute(
            update(SchedulerLeader)
            .where(SchedulerLeader.id == 100 + SCHEMA_VERSION)
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.commit()
    async with SessionLocal() as session:
        assert not await SnapshotRepository(session).publish(
            key, abandoned, old, {"value": "expired"}, interval_seconds=300, duration_ms=1
        )
    assert await new.try_acquire()
    async with SessionLocal() as session:
        repository = SnapshotRepository(session)
        generation = await repository.claim(key, new)
        assert generation is not None
        assert generation > abandoned
        assert await repository.publish(key, generation, new, {"value": "new"}, interval_seconds=300, duration_ms=1)
        assert not await repository.publish(
            key, abandoned, old, {"value": "late-old"}, interval_seconds=300, duration_ms=1
        )
        await repository.failed(key, abandoned, old, "old-failure")
    assert (await _read(key)).payload == {"value": "new"}
    assert (await _read(key)).last_error is None
    assert await old.release()
    assert not await old.try_acquire()
    assert await new.release()


@pytest.mark.asyncio
async def test_independent_lease_works_with_default_leader_disabled(db_setup, monkeypatch):
    from app.core.scheduling import leader_election

    monkeypatch.setattr(
        leader_election,
        "get_settings",
        lambda: SimpleNamespace(leader_election_enabled=False, leader_election_ttl_seconds=30),
    )
    async with SessionLocal() as session:
        session.add(
            SchedulerLeader(
                id=1, leader_id="older-replica", acquired_at=utcnow(), expires_at=utcnow() + timedelta(hours=1)
            )
        )
        await session.commit()
    first = LeaderElection("new-first", lease_id=100 + SCHEMA_VERSION)
    second = LeaderElection("new-second", lease_id=100 + SCHEMA_VERSION)
    assert await first.try_acquire()
    assert not await second.try_acquire()
    assert await first.release()
    assert await second.try_acquire()
    assert await second.release()
    async with SessionLocal() as session:
        owner = await session.get(SchedulerLeader, 1)
        assert owner is not None
        assert owner.leader_id == "older-replica"


@pytest.mark.asyncio
async def test_cancellation_joins_collector_and_preserves_payload(db_setup, monkeypatch):
    worker = ReadSnapshotScheduler(tick_seconds=0.01)
    assert await worker.leader.try_acquire()
    key = await _register()
    async with SessionLocal() as session:
        repository = SnapshotRepository(session)
        generation = await repository.claim(key, worker.leader)
        assert generation is not None
        await repository.publish(key, generation, worker.leader, {"value": "good"}, interval_seconds=0, duration_ms=1)
    entered, left = asyncio.Event(), asyncio.Event()

    async def wait(session):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            left.set()

    monkeypatch.setattr(scheduler_module, "dashboard_snapshot", wait)
    await worker.start()
    await asyncio.wait_for(entered.wait(), timeout=5)
    assert await asyncio.wait_for(worker.stop(), timeout=10)
    assert left.is_set()
    assert worker._task is None
    value = await _read(key)
    assert value.payload == {"value": "good"}
    assert value.last_error == "cancelled"
    async with SessionLocal() as session:
        assert await session.get(SchedulerLeader, 100 + SCHEMA_VERSION) is None


@pytest.mark.asyncio
async def test_filter_saturation_reserves_canonical_and_known_timezone_presets(db_setup):
    for index in range(KIND_CAPACITY["report-range"]):
        await _register("report-range", {"model": f"model-{index}"})
    with pytest.raises(SnapshotCapacityError):
        await _register("report-range", {"model": "overflow"})
    zones = (
        "Europe/Prague",
        "America/New_York",
        "Asia/Seoul",
        "Asia/Tokyo",
        "Australia/Sydney",
        "Europe/London",
        "America/Los_Angeles",
    )
    for zone in zones:
        await _register("report-zone", {"timezone": zone})
    worker = ReadSnapshotScheduler()
    assert set(await worker._seed()) == {"UTC", *zones}
    async with SessionLocal() as session:
        assert (await session.execute(select(func.count()).select_from(ReadSnapshot))).scalar_one() == MAX_ENTRIES
        presets = (
            (await session.execute(select(ReadSnapshot).where(ReadSnapshot.kind == "report-preset"))).scalars().all()
        )
        assert len(presets) == 24
        # An evicted non-canonical preset must be restored even in this process.
        victim = next(row for row in presets if not row.pinned)
        key = victim.snapshot_key
        await session.execute(delete(ReadSnapshot).where(ReadSnapshot.snapshot_key == key))
        await session.commit()
    await worker._seed()
    assert await _read(key) is not None


@pytest.mark.asyncio
async def test_stale_report_date_or_scope_is_never_relabelled(db_setup, monkeypatch):
    import app.modules.read_snapshots.keys as keys

    monkeypatch.setattr(keys, "local_today", lambda zone: date(2026, 10, 19))
    worker = ReadSnapshotScheduler()
    assert await worker.leader.try_acquire()
    parameters = {"days": 7, "timezone": "UTC"}
    key = await _register("report-preset", parameters)
    async with SessionLocal() as session:
        repository = SnapshotRepository(session)
        generation = await repository.claim(key, worker.leader)
        assert generation is not None
        await repository.publish(
            key,
            generation,
            worker.leader,
            {
                "range": {"start": "2026-10-12", "end": "2026-10-18", "timezone": "UTC"},
                "report": {"marker": "yesterday"},
            },
            interval_seconds=300,
            duration_ms=1,
        )
    async with SessionLocal() as session:
        with pytest.raises(SnapshotWarmingError):
            await SnapshotReader(session).report(
                "report", start=date(2026, 10, 13), end=date(2026, 10, 19), timezone="UTC"
            )
        with pytest.raises(SnapshotWarmingError):
            await SnapshotReader(session).report(
                "report", start=date(2026, 10, 13), end=date(2026, 10, 19), timezone="UTC", model="different"
            )
    assert (await _read(key)).payload["report"] == {"marker": "yesterday"}
    assert await worker.stop()


def test_normalization_preserves_timezone_and_filter_identity():
    start, end = date(2026, 3, 7), date(2026, 3, 10)
    kind, first, _ = report_parameters(start, end, "America/New_York", ["b", "a", "a"], ["z"])
    _, equivalent, _ = report_parameters(start, end, "America/New_York", ["a", "b"], ["z"])
    _, other_zone, _ = report_parameters(start, end, "UTC", ["a", "b"], ["z"])
    _, other_scope, _ = report_parameters(start, end, "America/New_York", ["a"], ["z"])
    assert encode_parameters(kind, first) == encode_parameters(kind, equivalent)
    assert encode_parameters(kind, first) != encode_parameters(kind, other_zone)
    assert encode_parameters(kind, first) != encode_parameters(kind, other_scope)


@pytest.mark.asyncio
async def test_cold_worker_failure_is_not_disguised_as_sample_collection(async_client, db_setup, monkeypatch):
    worker = ReadSnapshotScheduler()
    key = await _register()
    assert await worker.leader.try_acquire()

    async def fail(session):
        raise RuntimeError("sensitive private database message")

    monkeypatch.setattr(scheduler_module, "dashboard_snapshot", fail)
    await worker.refresh_one(key, "dashboard", {})
    response = await async_client.get("/api/dashboard/projections")
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "read_snapshot_failed"
    assert "sensitive" not in response.text
    assert "automatic retry" in error["message"]
    assert await worker.stop()


@pytest.mark.asyncio
async def test_cached_dashboard_identity_is_redacted_per_request_not_in_storage(db_setup):
    worker = ReadSnapshotScheduler()
    key = await _register()
    assert await worker.leader.try_acquire()
    payload: dict[str, JsonValue] = {
        "overviews": {
            "7d": {
                "accounts": [
                    {
                        "email": "private@example.com",
                        "displayName": "private@example.com",
                        "alias": None,
                        "chatgptAccountId": "private-chatgpt",
                        "workspaceId": "private-workspace",
                        "workspaceLabel": "private-label",
                    }
                ]
            }
        },
        "projections": {"weeklyCreditPace": None},
    }
    async with SessionLocal() as session:
        repository = SnapshotRepository(session)
        generation = await repository.claim(key, worker.leader)
        assert generation is not None
        assert await repository.publish(key, generation, worker.leader, payload, interval_seconds=300, duration_ms=1)
    async with SessionLocal() as session:
        viewer = await SnapshotReader(session).dashboard(timeframe="7d", redact_identity=True)
    viewer_accounts = viewer["accounts"]
    assert isinstance(viewer_accounts, list)
    viewer_account = viewer_accounts[0]
    assert isinstance(viewer_account, dict)
    assert viewer_account["email"] != "private@example.com"
    assert viewer_account["workspaceId"] is None
    async with SessionLocal() as session:
        owner = await SnapshotReader(session).dashboard(timeframe="7d")
    owner_accounts = owner["accounts"]
    assert isinstance(owner_accounts, list)
    owner_account = owner_accounts[0]
    assert isinstance(owner_account, dict)
    assert owner_account["email"] == "private@example.com"
    assert owner_account["workspaceId"] == "private-workspace"
    assert (await _read(key)).payload == payload
    assert await worker.stop()


@pytest.mark.asyncio
async def test_slow_report_does_not_block_dashboard_lane_or_own_sessions(db_setup, monkeypatch):
    worker = ReadSnapshotScheduler(tick_seconds=0.01)
    report_entered, report_left = asyncio.Event(), asyncio.Event()
    report_session = None
    dashboard_sessions = []

    async def dashboard(session):
        dashboard_sessions.append(session)
        assert session is not report_session
        return {"generation": len(dashboard_sessions)}

    async def report(session, parameters):
        nonlocal report_session
        report_session = session
        report_entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            report_left.set()

    monkeypatch.setattr(scheduler_module, "dashboard_snapshot", dashboard)
    monkeypatch.setattr(scheduler_module, "report_snapshot", report)
    monkeypatch.setattr(scheduler_module, "DASHBOARD_INTERVAL_SECONDS", 0)
    await worker.start()
    try:
        async with asyncio.timeout(5):
            await report_entered.wait()
            while len(dashboard_sessions) < 2:
                await asyncio.sleep(0.01)
        key, _ = encode_parameters("dashboard", {})
        assert (await _read(key)).payload is not None
    finally:
        assert await worker.stop()
    assert report_left.is_set()


@pytest.mark.asyncio
async def test_lease_loss_cancels_both_owned_lanes(db_setup, monkeypatch):
    from app.core.scheduling import leader_election

    monkeypatch.setattr(
        leader_election,
        "get_settings",
        lambda: SimpleNamespace(leader_election_enabled=True, leader_election_ttl_seconds=3),
    )
    worker = ReadSnapshotScheduler(tick_seconds=0.01)
    entered = {kind: asyncio.Event() for kind in ("dashboard", "report")}
    left = {kind: asyncio.Event() for kind in entered}

    async def dashboard(session):
        entered["dashboard"].set()
        try:
            await asyncio.Event().wait()
        finally:
            left["dashboard"].set()

    async def report(session, parameters):
        entered["report"].set()
        try:
            await asyncio.Event().wait()
        finally:
            left["report"].set()

    monkeypatch.setattr(scheduler_module, "dashboard_snapshot", dashboard)
    monkeypatch.setattr(scheduler_module, "report_snapshot", report)
    await worker.start()
    try:
        async with asyncio.timeout(8):
            await asyncio.gather(*(event.wait() for event in entered.values()))
            async with SessionLocal() as session:
                await session.execute(
                    update(SchedulerLeader)
                    .where(SchedulerLeader.id == 100 + SCHEMA_VERSION)
                    .values(leader_id="takeover", expires_at=utcnow() + timedelta(minutes=1))
                )
                await session.commit()
            await asyncio.gather(*(event.wait() for event in left.values()))
    finally:
        assert await worker.stop()
    async with SessionLocal() as session:
        owner = await session.get(SchedulerLeader, 100 + SCHEMA_VERSION)
        assert owner is not None
        assert owner.leader_id == "takeover"
        assert (
            await session.execute(
                select(func.count()).select_from(ReadSnapshot).where(ReadSnapshot.payload_json.is_not(None))
            )
        ).scalar_one() == 0


@pytest.mark.asyncio
async def test_long_report_discloses_retained_scope_history_and_http_query_budget(async_client, db_setup):
    from sqlalchemy import event

    from app.db.models import RequestLog
    from app.db.session import engine
    from tests.snapshot_helpers import prepare_read_snapshot

    observed = utcnow().replace(microsecond=0) - timedelta(days=20)
    async with SessionLocal() as session:
        session.add_all(
            [
                RequestLog(request_id="retained-chosen", model="chosen", status="success", requested_at=observed),
                RequestLog(
                    request_id="retained-other",
                    model="other",
                    status="success",
                    requested_at=observed - timedelta(days=40),
                ),
            ]
        )
        await session.commit()
    today = utcnow().date()
    query = {
        "start_date": (today - timedelta(days=89)).isoformat(),
        "end_date": today.isoformat(),
        "model": "chosen",
        "timezone": "UTC",
    }
    await prepare_read_snapshot("/api/reports", params=query)
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower())

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        response = await async_client.get("/api/reports", params=query)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["observedHistoryStart"] == observed.isoformat() + "Z"
    assert value["summary"]["totalRequests"] == 1
    assert value["speedMetricsAvailable"] is False
    assert not any(
        name in sql for sql in statements for name in ("usage_history", "request_logs", "request_report_hourly_rollups")
    )
    assert len(statements) <= 12, statements


@pytest.mark.asyncio
async def test_unadmitted_timezone_uses_serviceable_exact_range_not_an_orphan_preset(db_setup):
    zones = (
        "Europe/Prague",
        "America/New_York",
        "Asia/Seoul",
        "Asia/Tokyo",
        "Australia/Sydney",
        "Europe/London",
        "America/Los_Angeles",
    )
    for zone in zones:
        await _register("report-zone", {"timezone": zone})
    today = date(2026, 10, 19)
    # Use a real current rolling range for the rejected eighth timezone.
    from app.modules.read_snapshots.keys import local_today

    today = local_today("America/Chicago")
    async with SessionLocal() as session:
        with pytest.raises(SnapshotWarmingError):
            await SnapshotReader(session).report(
                "report", start=today - timedelta(days=6), end=today, timezone="America/Chicago"
            )
        rows = (
            (
                await session.execute(
                    select(ReadSnapshot).where(ReadSnapshot.kind.in_(("report-preset", "report-range")))
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].kind == "report-range"
        assert "America/Chicago" in rows[0].parameters_json


@pytest.mark.asyncio
async def test_aged_custom_report_gets_progress_among_due_presets(db_setup, monkeypatch):
    await _register("report-zone", {"timezone": "Europe/Prague"})
    worker = ReadSnapshotScheduler()
    await worker._seed()
    key = await _register("report-range", {"start": "2026-10-01", "end": "2026-10-05", "timezone": "UTC"})
    async with SessionLocal() as session:
        await session.execute(
            update(ReadSnapshot)
            .where(ReadSnapshot.snapshot_key == key)
            .values(refresh_at=utcnow() - timedelta(hours=1))
        )
        await session.commit()
    selected = []

    async def collect(key, kind, parameters):
        selected.append(key)

    monkeypatch.setattr(worker, "refresh_one", collect)
    await worker.refresh_due(("report-preset", "report-range"))
    assert len(selected) == 4
    assert key in selected  # Three pinned canonical presets cannot consume every slot.


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/reports", "/api/reports/options"])
@pytest.mark.parametrize("parameter", ["account_id", "api_key_id"])
async def test_oversized_filters_are_client_errors_without_report_registration(
    async_client, db_setup, monkeypatch, path, parameter
):
    async def forbidden(*args, **kwargs):
        pytest.fail("Oversized HTTP filter entered analytical computation")

    monkeypatch.setattr(ReportsService, "get_reports", forbidden)
    monkeypatch.setattr(ReportsService, "get_options", forbidden)
    response = await async_client.get(path, params={parameter: "x" * 17_000})
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "read_snapshot_filters_too_large"
    assert len(response.content) < 1024
    async with SessionLocal() as session:
        keys = (
            await session.execute(
                select(ReadSnapshot.kind).where(ReadSnapshot.kind.in_(("report-range", "report-preset")))
            )
        ).all()
        assert keys == []


@pytest.mark.asyncio
async def test_transient_report_lane_error_does_not_cancel_dashboard(db_setup, monkeypatch):
    worker = ReadSnapshotScheduler(tick_seconds=0.001)
    dashboard_started = asyncio.Event()
    dashboard_survived = asyncio.Event()
    report_retried = asyncio.Event()
    report_passes = 0

    async def refresh(kinds):
        nonlocal report_passes
        if kinds == ("dashboard",):
            dashboard_started.set()
            await report_retried.wait()
            dashboard_survived.set()
            worker._stop.set()
        else:
            await dashboard_started.wait()
            report_passes += 1
            if report_passes == 1:
                raise RuntimeError("transient registry failure")
            report_retried.set()

    monkeypatch.setattr(worker, "refresh_due", refresh)
    async with asyncio.timeout(2):
        await worker._serve()
    assert dashboard_survived.is_set()
    assert report_passes >= 2


@pytest.mark.asyncio
async def test_lane_cancellation_propagates_instead_of_being_retried(monkeypatch):
    worker = ReadSnapshotScheduler(tick_seconds=0.001)
    calls = 0

    async def refresh(kinds):
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, "refresh_due", refresh)
    with pytest.raises(asyncio.CancelledError):
        await worker._lane(("dashboard",))
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("interval", "expected_delay"), [(1, 1), (30, 1), (100, 1), (300, 200)])
async def test_successful_publication_reserves_bounded_refresh_headroom(db_setup, interval, expected_delay):
    worker = ReadSnapshotScheduler()
    assert await worker.leader.try_acquire()
    try:
        key = await _register()
        async with SessionLocal() as session:
            repository = SnapshotRepository(session)
            generation = await repository.claim(key, worker.leader)
            assert generation is not None
            assert await repository.publish(
                key, generation, worker.leader, {"complete": True}, interval_seconds=interval, duration_ms=24_000
            )
            row = await session.get(ReadSnapshot, key, populate_existing=True)
            assert row is not None and row.generated_at is not None
            assert (row.refresh_at - row.generated_at).total_seconds() == expected_delay
    finally:
        assert await worker.stop()


@pytest.mark.asyncio
async def test_replacement_is_due_while_the_prior_generation_remains_fresh(db_setup, monkeypatch):
    worker = ReadSnapshotScheduler()
    assert await worker.leader.try_acquire()
    try:
        key = await _register()
        async with SessionLocal() as session:
            repository = SnapshotRepository(session)
            generation = await repository.claim(key, worker.leader)
            assert generation is not None
            assert await repository.publish(
                key, generation, worker.leader, {"complete": 1}, interval_seconds=300, duration_ms=24_000
            )
            row = await session.get(ReadSnapshot, key, populate_existing=True)
            assert row is not None and row.generated_at is not None
            published_at = row.generated_at
        monkeypatch.setattr(repository_module, "utcnow", lambda: published_at + timedelta(seconds=201))
        async with SessionLocal() as session:
            repository = SnapshotRepository(session)
            assert key in [job[0] for job in await repository.due(("dashboard",))]
            prior = await repository.read(key, interval_seconds=300)
            assert prior is not None and prior.payload == {"complete": 1} and not prior.stale
            replacement = await repository.claim(key, worker.leader)
            assert replacement is not None and replacement > generation
            during = await repository.read(key, interval_seconds=300)
            assert during is not None and during.payload == prior.payload and not during.stale and during.refreshing
            await repository.failed(key, replacement, worker.leader, "controlled_failure")
            row = await session.get(ReadSnapshot, key, populate_existing=True)
            assert row is not None
            assert row.refresh_at == published_at + timedelta(seconds=231)
            assert row.published_generation == generation and row.last_error == "controlled_failure"
        monkeypatch.setattr(repository_module, "utcnow", lambda: published_at + timedelta(days=1))
        async with SessionLocal() as session:
            expired = await SnapshotRepository(session).read(key, interval_seconds=300)
            assert expired is not None and expired.stale and expired.payload == prior.payload
    finally:
        assert await worker.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("headroom,expected_status", [(100, 200), (0, 503)])
async def test_successful_early_refresh_preserves_analytical_readiness(
    async_client, db_setup, monkeypatch, headroom, expected_status
):
    from app.modules.read_snapshots import keys as keys_module

    clock = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    initial = clock
    collecting_replacements = False
    collected = 0
    boundary = initial + timedelta(seconds=301 + repository_module.FRESHNESS_GRACE_SECONDS)
    boundary_status = None
    monkeypatch.setattr(repository_module, "utcnow", lambda: clock)
    monkeypatch.setattr(keys_module, "utcnow", lambda: clock)
    # Zero is the previous schedule: begin collection only at the interval.
    monkeypatch.setattr(repository_module, "REFRESH_HEADROOM_SECONDS", headroom)

    async def collection_elapsed():
        nonlocal clock, collected, boundary_status
        if collecting_replacements:
            completed_at = clock + timedelta(seconds=40)
            if clock < boundary <= completed_at:
                clock = boundary
                boundary_status = (await async_client.get("/health/read-snapshots")).status_code
            clock = completed_at
            collected += 1

    async def dashboard(session):
        await collection_elapsed()
        return {"complete": True}

    async def report(session, parameters):
        await collection_elapsed()
        resolved = resolve_parameters(parameters)
        return {"range": {name: resolved[name] for name in ("start", "end", "timezone")}}

    monkeypatch.setattr(scheduler_module, "dashboard_snapshot", dashboard)
    monkeypatch.setattr(scheduler_module, "report_snapshot", report)
    worker = ReadSnapshotScheduler()
    assert await worker.leader.try_acquire()
    try:
        await worker.refresh_due()
        assert (await async_client.get("/health/read-snapshots")).status_code == 200
        collecting_replacements = True
        clock = initial + timedelta(seconds=201)
        await worker.refresh_due()
        assert collected == (4 if headroom else 0)
        if not headroom:
            # Keep the old producer active at its actual deadline. Four healthy
            # 40-second collectors cross the prior value's freshness boundary.
            clock = initial + timedelta(seconds=301)
            await worker.refresh_due()
        assert collected == 4
        if clock < boundary:
            # Early collection finished by t=361, before the t=401 boundary.
            clock = boundary
            boundary_status = (await async_client.get("/health/read-snapshots")).status_code
        assert boundary_status == expected_status
        # Both producers finish successfully; only the old schedule has a gap.
        assert (await async_client.get("/health/read-snapshots")).status_code == 200
        assert (await async_client.get("/health/ready")).status_code == 200
        async with SessionLocal() as session:
            rows = (
                (await session.execute(select(ReadSnapshot).where(ReadSnapshot.published_generation > 0)))
                .scalars()
                .all()
            )
            assert len(rows) == 4
            assert all(row.published_generation == 2 for row in rows)
    finally:
        assert await worker.stop()
