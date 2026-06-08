from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import AgentProviderAccount, AgentProviderQuotaWindow, AgentProviderRoutingSettings
from app.modules.agent_provider_routing.settlement import AgentProviderUsageSettlementData


class AgentProviderRoutingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_settings(self, provider_id: str) -> AgentProviderRoutingSettings:
        row = await self._session.get(AgentProviderRoutingSettings, provider_id)
        if row is not None:
            return row
        row = AgentProviderRoutingSettings(provider_id=provider_id)
        self._session.add(row)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._session.get(AgentProviderRoutingSettings, provider_id)
            if existing is None:
                raise
            return existing
        await self._session.refresh(row)
        return row

    async def save_settings(self, row: AgentProviderRoutingSettings) -> AgentProviderRoutingSettings:
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def advance_round_robin_cursor(
        self,
        provider_id: str,
        *,
        expected_cursor: str | None,
        selected_account_id: str,
    ) -> bool:
        cursor_condition = (
            AgentProviderRoutingSettings.round_robin_cursor.is_(None)
            if expected_cursor is None
            else AgentProviderRoutingSettings.round_robin_cursor == expected_cursor
        )
        result = await self._session.execute(
            update(AgentProviderRoutingSettings)
            .where(
                AgentProviderRoutingSettings.provider_id == provider_id,
                cursor_condition,
            )
            .values(round_robin_cursor=selected_account_id)
            .execution_options(synchronize_session=False)
        )
        rowcount = cast(Any, result).rowcount
        await self._session.commit()
        if rowcount == 0:
            self._session.expire_all()
        return rowcount > 0

    async def list_accounts_with_quota_windows(self, provider_id: str) -> list[AgentProviderAccount]:
        result = await self._session.execute(
            select(AgentProviderAccount)
            .where(AgentProviderAccount.provider_id == provider_id)
            .options(selectinload(AgentProviderAccount.quota_windows))
            .order_by(AgentProviderAccount.display_name, AgentProviderAccount.id)
        )
        return list(result.scalars().all())

    async def get_account_for_provider(self, provider_id: str, account_id: str) -> AgentProviderAccount | None:
        result = await self._session.execute(
            select(AgentProviderAccount)
            .where(AgentProviderAccount.provider_id == provider_id, AgentProviderAccount.id == account_id)
            .options(selectinload(AgentProviderAccount.quota_windows))
        )
        return result.scalar_one_or_none()

    async def upsert_quota_window(
        self,
        *,
        account: AgentProviderAccount,
        dimension: str,
        used: int,
        limit: int | None,
        reset_at,
    ) -> AgentProviderQuotaWindow:
        existing = next((window for window in account.quota_windows if window.dimension == dimension), None)
        if existing is None:
            existing = AgentProviderQuotaWindow(account_id=account.id, dimension=dimension)
            self._session.add(existing)
        existing.used = used
        existing.limit = limit
        existing.reset_at = reset_at
        await self._session.commit()
        await self._session.refresh(existing)
        return existing

    async def increment_quota_windows(
        self,
        account: AgentProviderAccount,
        usage: AgentProviderUsageSettlementData,
    ) -> None:
        now = datetime.now(timezone.utc)
        changed = False
        for window in account.quota_windows:
            delta = _delta_for_dimension(window.dimension, usage)
            if delta <= 0:
                continue
            if window.reset_at is not None and _is_expired(window.reset_at, now):
                result = await self._session.execute(
                    update(AgentProviderQuotaWindow)
                    .where(
                        AgentProviderQuotaWindow.id == window.id,
                        AgentProviderQuotaWindow.reset_at == window.reset_at,
                    )
                    .values(
                        used=delta,
                        reset_at=_advance_reset_at(window.reset_at, now, window.dimension),
                    )
                    .execution_options(synchronize_session=False)
                )
                if cast(Any, result).rowcount == 0:
                    await self._session.execute(
                        update(AgentProviderQuotaWindow)
                        .where(AgentProviderQuotaWindow.id == window.id)
                        .values(used=AgentProviderQuotaWindow.used + delta)
                        .execution_options(synchronize_session=False)
                    )
            else:
                await self._session.execute(
                    update(AgentProviderQuotaWindow)
                    .where(AgentProviderQuotaWindow.id == window.id)
                    .values(used=AgentProviderQuotaWindow.used + delta)
                    .execution_options(synchronize_session=False)
                )
            changed = True
        if changed:
            await self._session.commit()


def _delta_for_dimension(dimension: str, usage: AgentProviderUsageSettlementData) -> int:
    normalized = dimension.strip().lower()
    if normalized in {"requests", "request_count"} or normalized.startswith("requests_per_"):
        return usage.requests
    if normalized in {"input_tokens", "prompt_tokens"}:
        return usage.prompt_tokens or 0
    if normalized in {"output_tokens", "completion_tokens", "candidate_tokens", "candidates_tokens"}:
        return usage.completion_tokens or 0
    if normalized in {"tokens", "total_tokens"}:
        return usage.total_tokens or 0
    return 0


def _is_expired(reset_at: datetime, now: datetime) -> bool:
    if reset_at.tzinfo is None:
        return reset_at <= now.replace(tzinfo=None)
    return reset_at <= now


def _advance_reset_at(reset_at: datetime, now: datetime, dimension: str) -> datetime:
    interval = _reset_interval_for_dimension(dimension)
    compare_now = now.replace(tzinfo=None) if reset_at.tzinfo is None else now
    advanced = reset_at
    while advanced <= compare_now:
        advanced += interval
    return advanced


def _reset_interval_for_dimension(dimension: str) -> timedelta:
    normalized = dimension.lower()
    if "minute" in normalized:
        return timedelta(minutes=1)
    if "hour" in normalized:
        return timedelta(hours=1)
    if "week" in normalized:
        return timedelta(days=7)
    if "month" in normalized:
        return timedelta(days=30)
    return timedelta(days=1)
