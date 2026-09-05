from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from statistics import median
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy import event

from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy._service.http_bridge.request_submit import _HTTPBridgeRequestSubmitMixin
from app.modules.proxy.account_cache import AccountSelectionCache
from app.modules.proxy.load_balancer import AccountConcurrencyCaps, LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.service import _HTTPBridgeSession
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.quota_planner.repository import QuotaPlannerRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage import repository as usage_repository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["disabled", "available", "reached"])
async def test_owner_authorization_query_budget_and_cached_selection(db_setup, monkeypatch, policy):
    """Print a repeatable component benchmark without flaky latency assertions.

    Counts include SQLAlchemy statements and the SQLite direct-read path, but
    exclude connection pragmas and transaction control. No upstream is contacted.
    Run with pytest -s on each supported database to record timings.
    """
    owner_id = "authorization-query-budget"
    enabled, used = policy != "disabled", 10.0 if policy == "reached" else 5.0
    async with SessionLocal() as session:
        session.add(
            Account(
                id=owner_id,
                email="query-budget@example.test",
                plan_type="plus",
                access_token_encrypted=b"test",
                refresh_token_encrypted=b"test",
                id_token_encrypted=b"test",
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
                usage_limit_enabled=enabled,
                usage_limit_percent=10.0 if enabled else None,
            )
        )
        session.add_all(
            [
                UsageHistory(
                    account_id=owner_id, window=window, used_percent=used, window_minutes=minutes, recorded_at=utcnow()
                )
                for window, minutes in [("primary", 300), ("secondary", 10080)]
            ]
        )
        await session.commit()

    @asynccontextmanager
    async def repositories() -> AsyncIterator[ProxyRepositories]:
        async with SessionLocal() as session:
            yield ProxyRepositories(
                accounts=AccountsRepository(session),
                usage=UsageRepository(session),
                request_logs=RequestLogsRepository(session),
                sticky_sessions=StickySessionsRepository(session),
                api_keys=ApiKeysRepository(session),
                additional_usage=AdditionalUsageRepository(session),
                quota_planner=QuotaPlannerRepository(session),
                session=session,
            )

    balancer = LoadBalancer(repositories)
    balancer._selection_inputs_cache = AccountSelectionCache(ttl_seconds=60)
    bridge_owner = SimpleNamespace(_load_balancer=balancer)
    bridge_session = SimpleNamespace(account=SimpleNamespace(id=owner_id))
    allowed = policy != "reached"
    statements: list[str] = []

    def record_sql(statement: str) -> None:
        if statement.lstrip().upper().startswith(("SELECT", "WITH", "INSERT", "UPDATE", "DELETE")):
            statements.append(statement)

    def record_sqlalchemy(_conn, _cursor, statement, _parameters, _context, _many):
        record_sql(statement)

    def traced_direct_connect(*args, **kwargs):
        connection = sqlite3.connect(*args, **kwargs)
        connection.set_trace_callback(record_sql)
        return connection

    # Override this module's direct-read namespace, not global sqlite3: the
    # SQLAlchemy driver is counted separately and must not be counted twice.
    monkeypatch.setattr(
        usage_repository,
        "sqlite3",
        SimpleNamespace(
            connect=traced_direct_connect,
            PARSE_DECLTYPES=sqlite3.PARSE_DECLTYPES,
            PARSE_COLNAMES=sqlite3.PARSE_COLNAMES,
        ),
    )
    engine = SessionLocal.kw["bind"]
    event.listen(engine.sync_engine, "before_cursor_execute", record_sqlalchemy)

    async def select_owner() -> None:
        selected = await balancer.select_account(
            routing_strategy="usage_weighted",
            lease_kind="stream",
            estimated_lease_tokens=42,
            concurrency_caps=AccountConcurrencyCaps(stream_limit=2, response_create_limit=1),
        )
        try:
            assert (selected.account is not None) is allowed
            if not allowed:
                assert selected.error_code == "account_usage_limit_reached"
        finally:
            await balancer.release_account_lease(selected.lease)

    async def cold_selection() -> None:
        balancer._selection_inputs_cache.invalidate(propagate=False)
        await select_owner()

    async def authorize_bridge() -> None:
        decision = await _HTTPBridgeRequestSubmitMixin._fresh_http_bridge_owner_authorization(
            bridge_owner,
            cast(_HTTPBridgeSession, bridge_session),
        )
        assert decision.allowed is allowed

    async def measure(operation: Callable[[], Awaitable[None]], count: int) -> dict[str, object]:
        await operation()  # Warm compilation/connection setup outside measurement.
        queries, latencies = [], []
        for _ in range(count):
            before = len(statements)
            started = time.perf_counter()
            await operation()
            latencies.append((time.perf_counter() - started) * 1000)
            queries.append(len(statements) - before)
        return {
            "iterations": count,
            "queries_min": min(queries),
            "queries_max": max(queries),
            "median_ms": round(median(latencies), 3),
            "p95_ms": round(sorted(latencies)[int((len(latencies) - 1) * 0.95)], 3),
        }

    try:
        cold = await measure(cold_selection, 5)
        cached = await measure(select_owner, 20)
        bridge = await measure(authorize_bridge, 20)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record_sqlalchemy)
    assert cached["queries_max"] == 0
    assert bridge["queries_min"] == bridge["queries_max"] == 1
    assert await balancer.account_pressure_snapshot(owner_id) == (0, 0, 0.0)
    print(
        "OWNER_AUTHORIZATION_BENCHMARK "
        + json.dumps(
            {
                "database": engine.dialect.name,
                "policy": policy,
                "cold_selection": cold,
                "cached_selection": cached,
                "bridge_authorization_boundary": bridge,
            },
            sort_keys=True,
        )
    )
