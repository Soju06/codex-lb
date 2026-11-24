"""Request-log listing filtered by upstream (issue #1870)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import event, func, select

from app.core.utils.time import utcnow
from app.db.models import RequestDemandQuarterRollup
from app.db.session import SessionLocal, engine
from app.modules.accounts.usage_time_rollup import run_hourly_fold_pass
from app.modules.request_logs import repository as logs_repository_module
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


async def _add(
    repo: RequestLogsRepository,
    request_id: str,
    *,
    source_id: str | None,
    model: str = "gpt-5.1",
    minutes_ago: int = 1,
):
    return await repo.add_log(
        account_id=None,
        request_id=request_id,
        model=model,
        input_tokens=1,
        output_tokens=1,
        latency_ms=10,
        status="success",
        error_code=None,
        requested_at=utcnow() - timedelta(minutes=minutes_ago),
        model_source_id=source_id,
        model_source_kind="openai_compatible" if source_id else None,
        source="model_source" if source_id else None,
    )


async def _seed_mixed() -> None:
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)
        await _add(repo, "req_pool_1", source_id=None, minutes_ago=5)
        await _add(repo, "req_pool_2", source_id=None, minutes_ago=4)
        await _add(repo, "req_src_a", source_id="src_a", minutes_ago=3)
        await _add(repo, "req_src_b", source_id="src_b", minutes_ago=2)


def _request_ids(payload: dict) -> set[str]:
    return {row["requestId"] for row in payload["requests"]}


@pytest.mark.asyncio
async def test_upstream_filter_single_source(async_client, db_setup):
    await _seed_mixed()

    response = await async_client.get("/api/request-logs", params={"upstream": "src_a"})

    assert response.status_code == 200
    payload = response.json()
    assert _request_ids(payload) == {"req_src_a"}
    assert payload["total"] == 1


@pytest.mark.asyncio
async def test_upstream_filter_subscription_pool_matches_rows_without_source(async_client, db_setup):
    await _seed_mixed()

    response = await async_client.get("/api/request-logs", params={"upstream": "subscription_pool"})

    payload = response.json()
    assert _request_ids(payload) == {"req_pool_1", "req_pool_2"}
    assert payload["total"] == 2


@pytest.mark.asyncio
async def test_upstream_filter_mixed_selection_uses_or(async_client, db_setup):
    await _seed_mixed()

    response = await async_client.get(
        "/api/request-logs",
        params=[("upstream", "subscription_pool"), ("upstream", "src_a")],
    )

    payload = response.json()
    assert _request_ids(payload) == {"req_pool_1", "req_pool_2", "req_src_a"}
    assert payload["total"] == 3


@pytest.mark.asyncio
async def test_upstream_filter_absent_keeps_existing_results(async_client, db_setup):
    await _seed_mixed()

    unfiltered = await async_client.get("/api/request-logs")
    blank = await async_client.get("/api/request-logs", params={"upstream": " "})

    assert unfiltered.json()["total"] == 4
    assert blank.json()["total"] == 4


@pytest.mark.asyncio
async def test_upstream_filter_distinguishes_sources_with_the_same_model(async_client, db_setup):
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)
        await _add(repo, "req_same_a", source_id="src_same_a", model="shared-model")
        await _add(repo, "req_same_b", source_id="src_same_b", model="shared-model")

    response = await async_client.get(
        "/api/request-logs",
        params=[("upstream", "src_same_a"), ("modelOption", "shared-model")],
    )

    payload = response.json()
    assert _request_ids(payload) == {"req_same_a"}
    assert payload["total"] == 1


@pytest.mark.asyncio
async def test_upstream_filter_excludes_soft_deleted_rows(async_client, db_setup):
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)
        await _add(repo, "req_live_src", source_id="src_soft")
        deleted = await _add(repo, "req_deleted_src", source_id="src_soft")
        deleted.deleted_at = utcnow()
        await session.commit()

    response = await async_client.get("/api/request-logs", params={"upstream": "src_soft"})

    payload = response.json()
    assert _request_ids(payload) == {"req_live_src"}
    assert payload["total"] == 1


@pytest.mark.asyncio
async def test_upstream_filter_combines_with_other_filters(async_client, db_setup):
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)
        await _add(repo, "req_src_ok", source_id="src_and")
        await repo.add_log(
            account_id=None,
            request_id="req_src_err",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            status="error",
            error_code="upstream_error",
            requested_at=utcnow(),
            model_source_id="src_and",
            model_source_kind="openai_compatible",
            source="model_source",
        )
        await _add(repo, "req_pool_ok", source_id=None)

    response = await async_client.get("/api/request-logs", params={"upstream": "src_and", "status": "ok"})

    payload = response.json()
    assert _request_ids(payload) == {"req_src_ok"}
    assert payload["total"] == 1


@pytest.mark.asyncio
async def test_upstream_count_cache_isolates_selections(db_setup, monkeypatch):
    monkeypatch.setattr(logs_repository_module, "_COUNT_CACHE_TTL_SECONDS", 30.0)
    logs_repository_module._clear_recent_count_cache()
    try:
        await _seed_mixed()
        async with SessionLocal() as session:
            repo = RequestLogsRepository(session)
            unfiltered = await repo.list_recent()
            only_a = await repo.list_recent(upstreams=["src_a"])
            only_pool = await repo.list_recent(upstreams=["subscription_pool"])
            only_a_again = await repo.list_recent(upstreams=["src_a"])
    finally:
        logs_repository_module._clear_recent_count_cache()

    assert unfiltered.total == 4
    assert only_a.total == 1
    assert only_pool.total == 2
    assert only_a_again.total == 1


@pytest.mark.asyncio
async def test_upstream_filtered_total_is_exact_when_demand_rollups_exist(db_setup):
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)
        # Old enough to sit behind the fold watermark.
        for index in range(3):
            await _add(repo, f"req_old_pool_{index}", source_id=None, minutes_ago=3 * 24 * 60 + index)
        for index in range(2):
            await _add(repo, f"req_old_src_{index}", source_id="src_folded", minutes_ago=3 * 24 * 60 + index)
        await _add(repo, "req_tail_src", source_id="src_folded", minutes_ago=1)

    await run_hourly_fold_pass(now=utcnow())
    async with SessionLocal() as session:
        folded_rows = (await session.execute(select(func.count()).select_from(RequestDemandQuarterRollup))).scalar_one()
    assert folded_rows > 0

    statements: list[str] = []

    def _capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        async with SessionLocal() as session:
            repo = RequestLogsRepository(session)
            unfiltered = await repo.list_recent()
            statements.clear()
            filtered = await repo.list_recent(upstreams=["src_folded"])
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)

    assert unfiltered.total == 6
    assert filtered.total == 3
    assert not any("request_demand_quarter_rollups" in statement for statement in statements)
