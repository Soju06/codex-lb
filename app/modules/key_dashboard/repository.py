from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiKey, RequestLog, RequestUsageHourlyRollup
from app.modules.accounts.usage_time_rollup import WARMUP_REQUEST_KINDS, to_dimension
from app.modules.accounts.usage_time_rollup_read import epoch_to_datetime, raw_windows_clause, read_hourly_window


@dataclass(slots=True)
class GroupDailyUsage:
    request_count: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    total_cost_usd: float = 0.0


@dataclass(slots=True)
class GroupMemberUsage:
    key_id: str
    name: str
    key_prefix: str
    daily_usage: dict[date, GroupDailyUsage] = field(default_factory=dict)


@dataclass(slots=True)
class KeyGroupUsage:
    name: str | None
    members: list[GroupMemberUsage] = field(default_factory=list)


class KeyDashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def group_usage(self, key_id: str, since: datetime, until: datetime) -> KeyGroupUsage:
        # Read membership from persistence, never the authentication cache.
        group = await self._session.scalar(select(ApiKey.usage_group).where(ApiKey.id == key_id))
        if group is None:
            return KeyGroupUsage(name=None)
        rows = await self._session.execute(
            select(ApiKey.id, ApiKey.name, ApiKey.key_prefix)
            .where(ApiKey.usage_group == group)
            .order_by(ApiKey.name, ApiKey.id)
        )
        members = {row.id: GroupMemberUsage(key_id=row.id, name=row.name, key_prefix=row.key_prefix) for row in rows}
        if not members:
            return KeyGroupUsage(name=group)
        by_dimension = {to_dimension(key): member for key, member in members.items()}
        rollups, raw_windows = await read_hourly_window(
            self._session,
            since,
            until,
            filters=(
                RequestUsageHourlyRollup.api_key_id.in_(by_dimension),
                RequestUsageHourlyRollup.request_kind.not_in(WARMUP_REQUEST_KINDS),
            ),
        )
        for row in rollups:
            member = by_dimension[row.api_key_id]
            day = epoch_to_datetime(row.bucket_epoch).date()
            daily = member.daily_usage.setdefault(day, GroupDailyUsage())
            daily.request_count += row.request_count
            daily.total_tokens += row.input_tokens + row.output_or_reasoning_tokens
            daily.cached_input_tokens += row.cached_input_tokens_clamped
            daily.total_cost_usd += row.cost_usd
        if raw_windows:
            input_tokens = func.coalesce(RequestLog.input_tokens, 0)
            cached_tokens = func.coalesce(RequestLog.cached_input_tokens, 0)
            # Portable per-row clamp, matching the persisted rollup measure.
            clamped_cached = case(
                (cached_tokens < 0, 0),
                (RequestLog.input_tokens.is_(None), cached_tokens),
                (input_tokens < 0, 0),
                (cached_tokens > input_tokens, input_tokens),
                else_=cached_tokens,
            )
            # Both supported databases return YYYY-MM-DD from this expression.
            day_column = func.date(RequestLog.requested_at).label("day")
            totals = await self._session.execute(
                select(
                    RequestLog.api_key_id,
                    day_column,
                    func.count().label("request_count"),
                    func.sum(
                        input_tokens + func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)
                    ).label("total_tokens"),
                    func.sum(clamped_cached).label("cached_input_tokens"),
                    func.sum(func.coalesce(RequestLog.cost_usd, 0.0)).label("total_cost_usd"),
                )
                .where(
                    RequestLog.api_key_id.in_(members.keys()),
                    RequestLog.request_kind.not_in(WARMUP_REQUEST_KINDS),
                    raw_windows_clause(raw_windows),
                )
                .group_by(RequestLog.api_key_id, day_column)
            )
            for row in totals:
                member = members[row.api_key_id]
                day = date.fromisoformat(str(row.day))
                daily = member.daily_usage.setdefault(day, GroupDailyUsage())
                daily.request_count += row.request_count
                daily.total_tokens += row.total_tokens
                daily.cached_input_tokens += row.cached_input_tokens
                daily.total_cost_usd += row.total_cost_usd
        return KeyGroupUsage(name=group, members=list(members.values()))
