from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from app.db.models import UsageHistory
from app.db.session import detach_session_objects, get_background_session
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository, UsageWindowWrite


class BackgroundUsageRepository:
    async def latest_entry_for_account(
        self,
        account_id: str,
        *,
        window: str | None = None,
    ) -> UsageHistory | None:
        async with get_background_session() as session:
            entry = await UsageRepository(session).latest_entry_for_account(account_id, window=window)
            detach_session_objects(session)
            return entry

    async def add_entry(
        self,
        account_id: str,
        used_percent: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        recorded_at: datetime | None = None,
        window: str | None = None,
        reset_at: int | None = None,
        window_minutes: int | None = None,
        credits_has: bool | None = None,
        credits_unlimited: bool | None = None,
        credits_balance: float | None = None,
    ) -> UsageHistory | None:
        async with get_background_session() as session:
            entry = await UsageRepository(session).add_entry(
                account_id=account_id,
                used_percent=used_percent,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                recorded_at=recorded_at,
                window=window,
                reset_at=reset_at,
                window_minutes=window_minutes,
                credits_has=credits_has,
                credits_unlimited=credits_unlimited,
                credits_balance=credits_balance,
            )
            detach_session_objects(session)
            return entry

    async def add_account_snapshot(
        self,
        account_id: str,
        windows: Collection[UsageWindowWrite],
        *,
        recorded_at: datetime | None = None,
    ) -> list[UsageHistory]:
        async with get_background_session() as session:
            entries = await UsageRepository(session).add_account_snapshot(
                account_id,
                windows,
                recorded_at=recorded_at,
            )
            detach_session_objects(session)
            return entries


class BackgroundAdditionalUsageRepository:
    async def add_entry(
        self,
        account_id: str,
        limit_name: str,
        metered_feature: str,
        window: str,
        used_percent: float,
        reset_at: int | None = None,
        window_minutes: int | None = None,
        recorded_at: datetime | None = None,
        quota_key: str | None = None,
    ) -> None:
        async with get_background_session() as session:
            await AdditionalUsageRepository(session).add_entry(
                account_id=account_id,
                limit_name=limit_name,
                metered_feature=metered_feature,
                window=window,
                used_percent=used_percent,
                reset_at=reset_at,
                window_minutes=window_minutes,
                recorded_at=recorded_at,
                quota_key=quota_key,
            )

    async def delete_for_account(self, account_id: str) -> None:
        async with get_background_session() as session:
            await AdditionalUsageRepository(session).delete_for_account(account_id)

    async def delete_for_account_and_quota_key(self, account_id: str, quota_key: str) -> None:
        async with get_background_session() as session:
            await AdditionalUsageRepository(session).delete_for_account_and_quota_key(account_id, quota_key)

    async def delete_for_account_and_limit(self, account_id: str, limit_name: str) -> None:
        async with get_background_session() as session:
            await AdditionalUsageRepository(session).delete_for_account_and_limit(account_id, limit_name)

    async def delete_for_account_quota_key_window(
        self,
        account_id: str,
        quota_key: str,
        window: str,
    ) -> None:
        async with get_background_session() as session:
            await AdditionalUsageRepository(session).delete_for_account_quota_key_window(account_id, quota_key, window)

    async def delete_for_account_limit_window(
        self,
        account_id: str,
        limit_name: str,
        window: str,
    ) -> None:
        async with get_background_session() as session:
            await AdditionalUsageRepository(session).delete_for_account_limit_window(account_id, limit_name, window)

    async def list_quota_keys(
        self,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> list[str]:
        async with get_background_session() as session:
            return await AdditionalUsageRepository(session).list_quota_keys(account_ids=account_ids, since=since)

    async def list_limit_names(
        self,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> list[str]:
        async with get_background_session() as session:
            return await AdditionalUsageRepository(session).list_limit_names(account_ids=account_ids, since=since)

    async def latest_recorded_at_for_account(self, account_id: str) -> datetime | None:
        async with get_background_session() as session:
            return await AdditionalUsageRepository(session).latest_recorded_at_for_account(account_id)
