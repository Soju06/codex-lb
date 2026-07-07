from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import and_, case, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.config.settings_cache import get_settings_cache
from app.core.utils.time import utcnow
from app.db.models import Account, RequestKind, RequestLog, StickySession, StickySessionKind
from app.modules.fleet.schemas import (
    FleetObservabilityResponse,
    FleetPressureAccountBreakdown,
    FleetPressureClientBreakdown,
    FleetPressureKindBreakdown,
    FleetPressureMetric,
    FleetPressureObservability,
    FleetPressureWindow,
    FleetStickyAccountBreakdown,
    FleetStickyKindBreakdown,
    FleetStickyObservability,
)

_PRESSURE_WINDOWS = (("30m", "30m", 30 * 60), ("2h", "2h", 2 * 60 * 60))
_BREAKDOWN_LIMIT = 10


@dataclass(slots=True)
class _StickyAccountAccumulator:
    account_id: str
    email: str | None
    total: int = 0
    recent_count: int = 0
    stale_count: int = 0
    last_updated_at: datetime | None = None
    kinds: list[FleetStickyKindBreakdown] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.email or self.account_id


async def build_fleet_observability(
    session: AsyncSession,
    *,
    visible_account_ids: list[str] | None,
    include_usage: bool,
) -> FleetObservabilityResponse:
    generated_at = utcnow()
    if not include_usage:
        return FleetObservabilityResponse(
            available=False,
            generated_at=generated_at,
            pressure=FleetPressureObservability(available=False),
            sticky=FleetStickyObservability(available=False),
        )

    settings = await get_settings_cache().get()
    stale_threshold_seconds = int(settings.openai_cache_affinity_max_age_seconds)
    return FleetObservabilityResponse(
        generated_at=generated_at,
        pressure=FleetPressureObservability(
            windows=[
                await _build_pressure_window(
                    session,
                    key=key,
                    label=label,
                    seconds=seconds,
                    generated_at=generated_at,
                    visible_account_ids=visible_account_ids,
                )
                for key, label, seconds in _PRESSURE_WINDOWS
            ]
        ),
        sticky=await _build_sticky_observability(
            session,
            generated_at=generated_at,
            stale_threshold_seconds=stale_threshold_seconds,
            visible_account_ids=visible_account_ids,
        ),
    )


async def _build_pressure_window(
    session: AsyncSession,
    *,
    key: str,
    label: str,
    seconds: int,
    generated_at: datetime,
    visible_account_ids: list[str] | None,
) -> FleetPressureWindow:
    since = generated_at - timedelta(seconds=seconds)
    conditions = _request_log_conditions(since, visible_account_ids)
    totals = await _pressure_metrics(session, conditions)
    by_account, accounts_truncated = await _pressure_by_account(session, conditions)
    by_kind, kinds_truncated = await _pressure_by_kind(session, conditions)
    by_client, clients_truncated = await _pressure_by_client(session, conditions)
    return FleetPressureWindow(
        key=key,
        label=label,
        seconds=seconds,
        request_count=totals.request_count,
        error_count=totals.error_count,
        input_tokens=totals.input_tokens,
        cached_input_tokens=totals.cached_input_tokens,
        output_tokens=totals.output_tokens,
        cost_usd=totals.cost_usd,
        truncated=accounts_truncated or kinds_truncated or clients_truncated,
        top_error_code=await _top_error_code(session, conditions),
        by_account=by_account,
        by_kind=by_kind,
        by_client=by_client,
    )


def _request_log_conditions(
    since: datetime,
    visible_account_ids: list[str] | None,
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [
        RequestLog.requested_at >= since,
        RequestLog.deleted_at.is_(None),
        func.coalesce(RequestLog.request_kind, "").not_in((RequestKind.WARMUP.value, "limit_warmup")),
    ]
    if visible_account_ids is not None:
        conditions.append(RequestLog.account_id.in_(visible_account_ids))
    return conditions


def _metric_columns():
    return (
        func.count(RequestLog.id).label("request_count"),
        func.coalesce(
            func.sum(case((RequestLog.status != literal_column("'success'"), 1), else_=0)),
            0,
        ).label("error_count"),
        func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
        func.coalesce(func.sum(func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)), 0).label(
            "output_tokens"
        ),
        func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
    )


def _metric_from_row(row) -> FleetPressureMetric:
    return FleetPressureMetric(
        request_count=int(row.request_count or 0),
        error_count=int(row.error_count or 0),
        input_tokens=int(row.input_tokens or 0),
        cached_input_tokens=int(row.cached_input_tokens or 0),
        output_tokens=int(row.output_tokens or 0),
        cost_usd=float(row.cost_usd or 0.0),
    )


async def _pressure_metrics(session: AsyncSession, conditions: list[ColumnElement[bool]]) -> FleetPressureMetric:
    result = await session.execute(select(*_metric_columns()).where(and_(*conditions)))
    return _metric_from_row(result.one())


async def _top_error_code(session: AsyncSession, conditions: list[ColumnElement[bool]]) -> str | None:
    result = await session.execute(
        select(RequestLog.error_code, func.count(RequestLog.id).label("error_count"))
        .where(
            and_(
                *conditions,
                RequestLog.status != "success",
                RequestLog.error_code.is_not(None),
            )
        )
        .group_by(RequestLog.error_code)
        .order_by(func.count(RequestLog.id).desc(), RequestLog.error_code.asc())
        .limit(1)
    )
    row = result.first()
    return str(row.error_code) if row and row.error_code else None


async def _pressure_by_account(
    session: AsyncSession,
    conditions: list[ColumnElement[bool]],
) -> tuple[list[FleetPressureAccountBreakdown], bool]:
    result = await session.execute(
        select(
            RequestLog.account_id,
            Account.email,
            func.max(RequestLog.requested_at).label("last_selected_at"),
            *_metric_columns(),
        )
        .outerjoin(Account, Account.id == RequestLog.account_id)
        .where(and_(*conditions, RequestLog.account_id.is_not(None)))
        .group_by(RequestLog.account_id, Account.email)
        .order_by(func.count(RequestLog.id).desc(), RequestLog.account_id.asc())
        .limit(_BREAKDOWN_LIMIT + 1)
    )
    rows = result.all()
    return (
        [
            FleetPressureAccountBreakdown(
                account_id=str(row.account_id),
                email=row.email,
                label=row.email or str(row.account_id),
                last_selected_at=row.last_selected_at if isinstance(row.last_selected_at, datetime) else None,
                **_metric_from_row(row).model_dump(),
            )
            for row in rows[:_BREAKDOWN_LIMIT]
            if row.account_id
        ],
        len(rows) > _BREAKDOWN_LIMIT,
    )


async def _pressure_by_kind(
    session: AsyncSession,
    conditions: list[ColumnElement[bool]],
) -> tuple[list[FleetPressureKindBreakdown], bool]:
    kind = func.coalesce(func.nullif(RequestLog.request_kind, ""), "unknown").label("request_kind")
    result = await session.execute(
        select(kind, *_metric_columns())
        .where(and_(*conditions))
        .group_by(kind)
        .order_by(func.count(RequestLog.id).desc(), kind.asc())
        .limit(_BREAKDOWN_LIMIT + 1)
    )
    rows = result.all()
    return (
        [
            FleetPressureKindBreakdown(
                name=str(row.request_kind or "unknown"),
                request_kind=str(row.request_kind or "unknown"),
                **_metric_from_row(row).model_dump(),
            )
            for row in rows[:_BREAKDOWN_LIMIT]
        ],
        len(rows) > _BREAKDOWN_LIMIT,
    )


async def _pressure_by_client(
    session: AsyncSession,
    conditions: list[ColumnElement[bool]],
) -> tuple[list[FleetPressureClientBreakdown], bool]:
    client_group = func.coalesce(
        func.nullif(RequestLog.useragent_group, ""),
        func.nullif(RequestLog.source, ""),
        "unknown",
    ).label("client_group")
    result = await session.execute(
        select(client_group, *_metric_columns())
        .where(and_(*conditions))
        .group_by(client_group)
        .order_by(func.count(RequestLog.id).desc(), client_group.asc())
        .limit(_BREAKDOWN_LIMIT + 1)
    )
    rows = result.all()
    return (
        [
            FleetPressureClientBreakdown(
                name=str(row.client_group or "unknown"),
                client_group=str(row.client_group or "unknown"),
                **_metric_from_row(row).model_dump(),
            )
            for row in rows[:_BREAKDOWN_LIMIT]
        ],
        len(rows) > _BREAKDOWN_LIMIT,
    )


async def _build_sticky_observability(
    session: AsyncSession,
    *,
    generated_at: datetime,
    stale_threshold_seconds: int,
    visible_account_ids: list[str] | None,
) -> FleetStickyObservability:
    stale_cutoff = generated_at - timedelta(seconds=stale_threshold_seconds)
    stale_expr = and_(
        StickySession.kind == StickySessionKind.PROMPT_CACHE,
        StickySession.updated_at <= stale_cutoff,
    )
    conditions: list[ColumnElement[bool]] = []
    if visible_account_ids is not None:
        conditions.append(StickySession.account_id.in_(visible_account_ids))

    stmt = (
        select(
            StickySession.account_id,
            Account.email,
            StickySession.kind,
            func.count(StickySession.key).label("total"),
            func.coalesce(func.sum(case((stale_expr, 1), else_=0)), 0).label("stale_count"),
            func.max(StickySession.updated_at).label("last_updated_at"),
        )
        .join(Account, Account.id == StickySession.account_id)
        .group_by(StickySession.account_id, Account.email, StickySession.kind)
        .order_by(func.count(StickySession.key).desc(), StickySession.account_id.asc())
    )
    if conditions:
        stmt = stmt.where(and_(*conditions))

    result = await session.execute(stmt)
    accounts: dict[str, _StickyAccountAccumulator] = {}
    for row in result.all():
        if not row.account_id:
            continue
        account_id = str(row.account_id)
        accumulator = accounts.setdefault(
            account_id,
            _StickyAccountAccumulator(
                account_id=account_id,
                email=row.email,
            ),
        )
        total = int(row.total or 0)
        stale_count = int(row.stale_count or 0)
        accumulator.total += total
        accumulator.stale_count += stale_count
        accumulator.recent_count += max(total - stale_count, 0)
        if isinstance(row.last_updated_at, datetime):
            if accumulator.last_updated_at is None or row.last_updated_at > accumulator.last_updated_at:
                accumulator.last_updated_at = row.last_updated_at
        kind_name = _sticky_kind_name(row.kind)
        accumulator.kinds.append(
            FleetStickyKindBreakdown(
                name=kind_name,
                total=total,
                stale_count=stale_count,
            )
        )

    by_account = [
        FleetStickyAccountBreakdown(
            account_id=account.account_id,
            email=account.email,
            label=account.label,
            total=account.total,
            recent_count=account.recent_count,
            stale_count=account.stale_count,
            last_updated_at=account.last_updated_at,
            kinds=account.kinds,
        )
        for account in sorted(accounts.values(), key=lambda item: (-item.total, item.account_id))[:_BREAKDOWN_LIMIT]
    ]
    total = sum(account.total for account in accounts.values())
    stale_count = sum(account.stale_count for account in accounts.values())
    recent_count = sum(account.recent_count for account in accounts.values())
    return FleetStickyObservability(
        available=bool(by_account),
        total=total,
        recent_count=recent_count,
        stale_count=stale_count,
        stale_threshold_seconds=stale_threshold_seconds,
        truncated=len(accounts) > len(by_account),
        by_account=by_account,
    )


def _sticky_kind_name(value: StickySessionKind | str) -> str:
    if isinstance(value, StickySessionKind):
        return value.value
    return str(value)
