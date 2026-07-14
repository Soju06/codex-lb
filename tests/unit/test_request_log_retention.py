from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.db.models import Account, RequestLog, RequestLogDailyAggregate
from app.db.session import SessionLocal
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.request_logs.retention import MIN_REQUEST_LOG_RETENTION_DAYS, RequestLogRetentionService


def _account(account_id: str = "acc_retention") -> Account:
    return Account(
        id=account_id,
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=b"access",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        last_refresh=datetime(2026, 1, 1),
    )


async def _add_log(
    session,
    *,
    request_id: str,
    requested_at: datetime,
    account_id: str | None = None,
    status: str = "success",
    error_code: str | None = None,
    cost_usd: float = 0.25,
) -> RequestLog:
    log = await RequestLogsRepository(session).add_log(
        account_id=account_id,
        api_key_id="api_key_retention",
        request_id=request_id,
        model="gpt-5.5",
        input_tokens=100,
        output_tokens=50,
        cached_input_tokens=25,
        reasoning_tokens=10,
        latency_ms=200,
        latency_first_token_ms=80,
        status=status,
        error_code=error_code,
        requested_at=requested_at,
        service_tier="priority",
        requested_service_tier="priority",
        actual_service_tier="priority",
        transport="websocket",
        upstream_transport="websocket",
        source="codex",
        useragent_group="CodexCLI",
        plan_type="plus",
    )
    log.cost_usd = cost_usd
    return log


@pytest.mark.asyncio
async def test_request_log_retention_dry_run_does_not_mutate(db_setup) -> None:
    del db_setup
    now = datetime(2026, 7, 2, 12, 0, 0)
    old_at = datetime(2026, 5, 31, 3, 0, 0)

    async with SessionLocal() as session:
        await _add_log(session, request_id="resp_old_dry_run", requested_at=old_at)

        result = await RequestLogRetentionService(session).run(retention_days=30, dry_run=True, now=now)

        raw_count = await session.scalar(select(func.count(RequestLog.id)))
        aggregate_count = await session.scalar(select(func.count(RequestLogDailyAggregate.id)))

    assert result.dry_run is True
    assert result.eligible_rows == 1
    assert result.aggregate_groups == 1
    assert result.raw_rows_deleted == 0
    assert result.aggregate_rows_written == 0
    assert raw_count == 1
    assert aggregate_count == 0


@pytest.mark.asyncio
async def test_request_log_retention_apply_rolls_up_old_rows_and_keeps_recent_rows(db_setup) -> None:
    del db_setup
    now = datetime(2026, 7, 14, 12, 0, 0)
    old_at = datetime(2026, 7, 6, 23, 59, 59)
    recent_at = datetime(2026, 7, 7, 0, 0, 0)

    async with SessionLocal() as session:
        await _add_log(session, request_id="resp_old_apply_1", requested_at=old_at)
        await _add_log(session, request_id="resp_old_apply_2", requested_at=old_at)
        await _add_log(session, request_id="resp_recent_apply", requested_at=recent_at)

        result = await RequestLogRetentionService(session).run(retention_days=7, dry_run=False, now=now)

        raw_logs = list((await session.execute(select(RequestLog).order_by(RequestLog.request_id))).scalars().all())
        aggregate = await session.scalar(select(RequestLogDailyAggregate))

    assert result.retention_days == 7
    assert result.cutoff == datetime(2026, 7, 7, 0, 0, 0)
    assert result.eligible_rows == 2
    assert result.aggregate_groups == 1
    assert result.aggregate_rows_written == 1
    assert result.raw_rows_deleted == 2
    assert [log.request_id for log in raw_logs] == ["resp_recent_apply"]
    assert aggregate is not None
    assert aggregate.bucket_date.isoformat() == "2026-07-06"
    assert aggregate.api_key_id == "api_key_retention"
    assert aggregate.model == "gpt-5.5"
    assert aggregate.status == "success"
    assert aggregate.request_count == 2
    assert aggregate.error_count == 0
    assert aggregate.input_tokens == 200
    assert aggregate.output_tokens == 100
    assert aggregate.cached_input_tokens == 50
    assert aggregate.reasoning_tokens == 20
    assert aggregate.cost_usd == pytest.approx(0.5)
    assert aggregate.latency_ms_sum == 400
    assert aggregate.latency_ms_count == 2
    assert aggregate.latency_first_token_ms_sum == 160
    assert aggregate.latency_first_token_ms_count == 2


@pytest.mark.asyncio
async def test_request_log_retention_rolls_back_when_delete_count_differs_from_aggregate_count(db_setup) -> None:
    del db_setup
    now = datetime(2026, 7, 14, 12, 0, 0)
    old_at = datetime(2026, 7, 6, 23, 59, 59)

    async with SessionLocal() as session:
        await _add_log(session, request_id="resp_parity_guard", requested_at=old_at)
        await session.commit()
        service = RequestLogRetentionService(session)
        service._delete_raw_rows = AsyncMock(return_value=0)  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="aggregated 1, deleted 0"):
            await service.run(retention_days=7, dry_run=False, now=now)

        raw_count = await session.scalar(select(func.count(RequestLog.id)))
        aggregate_count = await session.scalar(select(func.count(RequestLogDailyAggregate.id)))

    assert raw_count == 1
    assert aggregate_count == 0


@pytest.mark.asyncio
async def test_request_log_retention_rejects_too_short_window(db_setup) -> None:
    del db_setup
    async with SessionLocal() as session:
        with pytest.raises(ValueError, match=str(MIN_REQUEST_LOG_RETENTION_DAYS)):
            await RequestLogRetentionService(session).run(
                retention_days=MIN_REQUEST_LOG_RETENTION_DAYS - 1,
                dry_run=False,
                now=datetime(2026, 7, 2, 12, 0, 0),
            )


@pytest.mark.asyncio
async def test_pruned_aggregate_does_not_satisfy_continuity_owner_lookup(db_setup) -> None:
    del db_setup
    now = datetime(2026, 7, 2, 12, 0, 0)
    old_at = datetime(2026, 5, 31, 3, 0, 0)

    async with SessionLocal() as session:
        session.add(_account())
        await session.commit()
        await _add_log(
            session,
            account_id="acc_retention",
            request_id="resp_pruned_owner",
            requested_at=old_at,
        )

        owner_before = await RequestLogsRepository(session).find_latest_account_id_for_response_id(
            response_id="resp_pruned_owner",
            api_key_id="api_key_retention",
        )
        result = await RequestLogRetentionService(session).run(retention_days=30, dry_run=False, now=now)
        owner_after = await RequestLogsRepository(session).find_latest_account_id_for_response_id(
            response_id="resp_pruned_owner",
            api_key_id="api_key_retention",
        )
        aggregate = await session.scalar(select(RequestLogDailyAggregate))

    assert owner_before == "acc_retention"
    assert result.raw_rows_deleted == 1
    assert aggregate is not None
    assert aggregate.account_id == "acc_retention"
    assert owner_after is None
