from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import BigInteger, Date, Integer, case, cast, delete, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.models import RequestLog, RequestLogDailyAggregate
from app.db.session import sqlite_writer_section

MIN_REQUEST_LOG_RETENTION_DAYS = 7


@dataclass(frozen=True, slots=True)
class RequestLogRetentionResult:
    dry_run: bool
    cutoff: datetime
    retention_days: int
    eligible_rows: int
    aggregate_groups: int
    aggregate_rows_written: int
    raw_rows_deleted: int


@dataclass(frozen=True, slots=True)
class _AggregateGroup:
    aggregate_key: str
    bucket_date: date
    api_key_id: str | None
    account_id: str | None
    model: str
    status: str
    error_code: str | None
    request_kind: str
    service_tier: str | None
    requested_service_tier: str | None
    actual_service_tier: str | None
    transport: str | None
    upstream_transport: str | None
    source: str | None
    useragent_group: str | None
    plan_type: str | None
    is_deleted: bool
    request_count: int
    error_count: int
    input_tokens: int
    output_tokens: int
    effective_output_tokens: int
    cached_input_tokens: int
    reasoning_tokens: int
    cost_usd: float
    cost_microdollars: int
    account_request_count: int
    account_input_tokens: int
    account_output_tokens: int
    account_cached_input_tokens: int
    account_cost_usd: float
    latency_ms_sum: int
    latency_ms_count: int
    latency_first_token_ms_sum: int
    latency_first_token_ms_count: int


class RequestLogRetentionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def run(
        self,
        *,
        retention_days: int,
        dry_run: bool,
        now: datetime | None = None,
    ) -> RequestLogRetentionResult:
        if retention_days < MIN_REQUEST_LOG_RETENTION_DAYS:
            raise ValueError(
                f"request log retention must be at least {MIN_REQUEST_LOG_RETENTION_DAYS} days, got {retention_days}"
            )

        cutoff = _cutoff_for_retention(retention_days=retention_days, now=now or utcnow())
        groups = await self._load_aggregate_groups(cutoff)
        eligible_rows = sum(group.request_count for group in groups)
        if dry_run:
            return RequestLogRetentionResult(
                dry_run=True,
                cutoff=cutoff,
                retention_days=retention_days,
                eligible_rows=eligible_rows,
                aggregate_groups=len(groups),
                aggregate_rows_written=0,
                raw_rows_deleted=0,
            )

        async with sqlite_writer_section():
            aggregate_rows_written = 0
            for group in groups:
                await self._upsert_group(group)
                aggregate_rows_written += 1

            raw_rows_deleted = await self._delete_raw_rows(cutoff) if groups else 0
            if raw_rows_deleted != eligible_rows:
                await self._session.rollback()
                raise RuntimeError(
                    "request log retention aborted because aggregated and deleted row counts differ: "
                    f"aggregated {eligible_rows}, deleted {raw_rows_deleted}"
                )
            await self._session.commit()

        return RequestLogRetentionResult(
            dry_run=False,
            cutoff=cutoff,
            retention_days=retention_days,
            eligible_rows=eligible_rows,
            aggregate_groups=len(groups),
            aggregate_rows_written=aggregate_rows_written,
            raw_rows_deleted=raw_rows_deleted,
        )

    async def _load_aggregate_groups(self, cutoff: datetime) -> list[_AggregateGroup]:
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        if dialect == "postgresql":
            bucket_expr = cast(func.date_trunc("day", RequestLog.requested_at), Date)
        else:
            bucket_expr = func.date(RequestLog.requested_at)
        bucket_col = bucket_expr.label("bucket_date")
        is_deleted_col = (RequestLog.deleted_at.is_not(None)).label("is_deleted")
        latest_request_log_ids = (
            select(func.max(RequestLog.id).label("request_log_id"))
            .where(RequestLog.requested_at < cutoff)
            .group_by(RequestLog.account_id, RequestLog.request_id, RequestLog.requested_at)
        )
        account_row_is_latest = RequestLog.id.in_(latest_request_log_ids)

        dimensions = (
            bucket_col,
            RequestLog.api_key_id,
            RequestLog.account_id,
            RequestLog.model,
            RequestLog.status,
            RequestLog.error_code,
            RequestLog.request_kind,
            RequestLog.service_tier,
            RequestLog.requested_service_tier,
            RequestLog.actual_service_tier,
            RequestLog.transport,
            RequestLog.upstream_transport,
            RequestLog.source,
            RequestLog.useragent_group,
            RequestLog.plan_type,
            is_deleted_col,
        )
        stmt = (
            select(
                *dimensions,
                func.count(RequestLog.id).label("request_count"),
                func.coalesce(
                    func.sum(cast(RequestLog.status != literal_column("'success'"), Integer)),
                    0,
                ).label("error_count"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                func.coalesce(
                    func.sum(func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)),
                    0,
                ).label("effective_output_tokens"),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(RequestLog.reasoning_tokens), 0).label("reasoning_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
                func.coalesce(
                    func.sum(cast(func.floor(func.coalesce(RequestLog.cost_usd, 0.0) * 1_000_000), BigInteger)),
                    0,
                ).label("cost_microdollars"),
                func.coalesce(func.sum(case((account_row_is_latest, 1), else_=0)), 0).label("account_request_count"),
                func.coalesce(
                    func.sum(
                        case(
                            (account_row_is_latest, func.coalesce(RequestLog.input_tokens, 0)),
                            else_=0,
                        )
                    ),
                    0,
                ).label("account_input_tokens"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                account_row_is_latest,
                                func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0),
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("account_output_tokens"),
                func.coalesce(
                    func.sum(
                        case(
                            (account_row_is_latest, func.coalesce(RequestLog.cached_input_tokens, 0)),
                            else_=0,
                        )
                    ),
                    0,
                ).label("account_cached_input_tokens"),
                func.coalesce(
                    func.sum(
                        case(
                            (account_row_is_latest, func.coalesce(RequestLog.cost_usd, 0.0)),
                            else_=0.0,
                        )
                    ),
                    0.0,
                ).label("account_cost_usd"),
                func.coalesce(func.sum(RequestLog.latency_ms), 0).label("latency_ms_sum"),
                func.coalesce(func.sum(cast(RequestLog.latency_ms.is_not(None), Integer)), 0).label("latency_ms_count"),
                func.coalesce(func.sum(RequestLog.latency_first_token_ms), 0).label("latency_first_token_ms_sum"),
                func.coalesce(
                    func.sum(cast(RequestLog.latency_first_token_ms.is_not(None), Integer)),
                    0,
                ).label("latency_first_token_ms_count"),
            )
            .where(RequestLog.requested_at < cutoff)
            .group_by(*dimensions)
            .order_by(bucket_col)
        )
        result = await self._session.execute(stmt)
        groups: list[_AggregateGroup] = []
        for row in result.all():
            bucket_date = _normalize_bucket_date(row.bucket_date)
            group_fields = {
                "bucket_date": bucket_date.isoformat(),
                "api_key_id": row.api_key_id,
                "account_id": row.account_id,
                "model": row.model,
                "status": row.status,
                "error_code": row.error_code,
                "request_kind": row.request_kind,
                "service_tier": row.service_tier,
                "requested_service_tier": row.requested_service_tier,
                "actual_service_tier": row.actual_service_tier,
                "transport": row.transport,
                "upstream_transport": row.upstream_transport,
                "source": row.source,
                "useragent_group": row.useragent_group,
                "plan_type": row.plan_type,
                "is_deleted": bool(row.is_deleted),
            }
            groups.append(
                _AggregateGroup(
                    aggregate_key=_aggregate_key(group_fields),
                    bucket_date=bucket_date,
                    api_key_id=row.api_key_id,
                    account_id=row.account_id,
                    model=row.model,
                    status=row.status,
                    error_code=row.error_code,
                    request_kind=row.request_kind,
                    service_tier=row.service_tier,
                    requested_service_tier=row.requested_service_tier,
                    actual_service_tier=row.actual_service_tier,
                    transport=row.transport,
                    upstream_transport=row.upstream_transport,
                    source=row.source,
                    useragent_group=row.useragent_group,
                    plan_type=row.plan_type,
                    is_deleted=bool(row.is_deleted),
                    request_count=int(row.request_count or 0),
                    error_count=int(row.error_count or 0),
                    input_tokens=int(row.input_tokens or 0),
                    output_tokens=int(row.output_tokens or 0),
                    effective_output_tokens=int(row.effective_output_tokens or 0),
                    cached_input_tokens=int(row.cached_input_tokens or 0),
                    reasoning_tokens=int(row.reasoning_tokens or 0),
                    cost_usd=float(row.cost_usd or 0.0),
                    cost_microdollars=int(row.cost_microdollars or 0),
                    account_request_count=int(row.account_request_count or 0),
                    account_input_tokens=int(row.account_input_tokens or 0),
                    account_output_tokens=int(row.account_output_tokens or 0),
                    account_cached_input_tokens=int(row.account_cached_input_tokens or 0),
                    account_cost_usd=float(row.account_cost_usd or 0.0),
                    latency_ms_sum=int(row.latency_ms_sum or 0),
                    latency_ms_count=int(row.latency_ms_count or 0),
                    latency_first_token_ms_sum=int(row.latency_first_token_ms_sum or 0),
                    latency_first_token_ms_count=int(row.latency_first_token_ms_count or 0),
                )
            )
        return groups

    async def _upsert_group(self, group: _AggregateGroup) -> None:
        existing = await self._session.scalar(
            select(RequestLogDailyAggregate)
            .where(RequestLogDailyAggregate.aggregate_key == group.aggregate_key)
            .limit(1)
        )
        if existing is None:
            self._session.add(
                RequestLogDailyAggregate(
                    aggregate_key=group.aggregate_key,
                    bucket_date=group.bucket_date,
                    api_key_id=group.api_key_id,
                    account_id=group.account_id,
                    model=group.model,
                    status=group.status,
                    error_code=group.error_code,
                    request_kind=group.request_kind,
                    service_tier=group.service_tier,
                    requested_service_tier=group.requested_service_tier,
                    actual_service_tier=group.actual_service_tier,
                    transport=group.transport,
                    upstream_transport=group.upstream_transport,
                    source=group.source,
                    useragent_group=group.useragent_group,
                    plan_type=group.plan_type,
                    is_deleted=group.is_deleted,
                    request_count=group.request_count,
                    error_count=group.error_count,
                    input_tokens=group.input_tokens,
                    output_tokens=group.output_tokens,
                    effective_output_tokens=group.effective_output_tokens,
                    cached_input_tokens=group.cached_input_tokens,
                    reasoning_tokens=group.reasoning_tokens,
                    cost_usd=group.cost_usd,
                    cost_microdollars=group.cost_microdollars,
                    account_request_count=group.account_request_count,
                    account_input_tokens=group.account_input_tokens,
                    account_output_tokens=group.account_output_tokens,
                    account_cached_input_tokens=group.account_cached_input_tokens,
                    account_cost_usd=group.account_cost_usd,
                    latency_ms_sum=group.latency_ms_sum,
                    latency_ms_count=group.latency_ms_count,
                    latency_first_token_ms_sum=group.latency_first_token_ms_sum,
                    latency_first_token_ms_count=group.latency_first_token_ms_count,
                )
            )
            return

        existing.request_count += group.request_count
        existing.error_count += group.error_count
        existing.input_tokens += group.input_tokens
        existing.output_tokens += group.output_tokens
        existing.effective_output_tokens += group.effective_output_tokens
        existing.cached_input_tokens += group.cached_input_tokens
        existing.reasoning_tokens += group.reasoning_tokens
        existing.cost_usd += group.cost_usd
        existing.cost_microdollars += group.cost_microdollars
        existing.account_request_count += group.account_request_count
        existing.account_input_tokens += group.account_input_tokens
        existing.account_output_tokens += group.account_output_tokens
        existing.account_cached_input_tokens += group.account_cached_input_tokens
        existing.account_cost_usd += group.account_cost_usd
        existing.latency_ms_sum += group.latency_ms_sum
        existing.latency_ms_count += group.latency_ms_count
        existing.latency_first_token_ms_sum += group.latency_first_token_ms_sum
        existing.latency_first_token_ms_count += group.latency_first_token_ms_count
        existing.updated_at = utcnow()

    async def _delete_raw_rows(self, cutoff: datetime) -> int:
        result = await self._session.execute(delete(RequestLog).where(RequestLog.requested_at < cutoff))
        return int(result.rowcount or 0)


def _cutoff_for_retention(*, retention_days: int, now: datetime) -> datetime:
    aware_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    cutoff_day = (aware_now.astimezone(timezone.utc) - timedelta(days=retention_days)).date()
    return datetime.combine(cutoff_day, time.min)


def _normalize_bucket_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise TypeError(f"Unsupported aggregate bucket date: {value!r}")


def _aggregate_key(fields: dict[str, object]) -> str:
    encoded = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
