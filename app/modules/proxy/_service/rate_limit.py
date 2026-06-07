from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Protocol, cast

from app.core import usage as usage_core
from app.core.usage.types import UsageWindowRow
from app.db.models import Account, UsageHistory
from app.modules.proxy.helpers import (
    _credits_headers,
    _credits_snapshot,
    _plan_type_for_accounts,
    _rate_limit_details,
    _rate_limit_headers,
    _select_accounts_for_limits,
    _summarize_window,
    _window_snapshot,
)
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.proxy.repo_bundle import ProxyRepoFactory, ProxyRepositories
from app.modules.proxy.types import (
    AdditionalRateLimitData,
    RateLimitStatusDetailsData,
    RateLimitStatusPayloadData,
    RateLimitWindowSnapshotData,
)
from app.modules.usage.additional_quota_keys import get_additional_display_label_for_quota_key
from app.modules.usage.mappers import usage_history_to_window_row
from app.modules.usage.updater import UsageUpdater


class _RateLimitServiceProtocol(Protocol):
    _repo_factory: ProxyRepoFactory


def _has_available_usage_account(
    *,
    primary_rows: list[UsageWindowRow],
    secondary_rows: list[UsageWindowRow],
    monthly_rows: list[UsageWindowRow],
    account_map: Mapping[str, Account],
) -> bool:
    rows_by_window = {
        "primary": primary_rows,
        "secondary": secondary_rows,
        "monthly": monthly_rows,
    }
    rows_by_account: dict[str, dict[str, UsageWindowRow]] = {}
    for window, rows in rows_by_window.items():
        for row in rows:
            rows_by_account.setdefault(row.account_id, {})[window] = row

    for account_id, account in account_map.items():
        account_rows = rows_by_account.get(account_id)
        if not account_rows:
            continue

        known_applicable_used_percents = [
            float(row.used_percent)
            for window, row in account_rows.items()
            if usage_core.capacity_for_plan(account.plan_type, window) is not None and row.used_percent is not None
        ]
        if known_applicable_used_percents and all(
            used_percent < 100.0 for used_percent in known_applicable_used_percents
        ):
            return True

    return False


class _RateLimitMixin:
    async def rate_limit_headers(self) -> dict[str, str]:
        return await get_rate_limit_headers_cache().get(self._compute_rate_limit_headers)

    async def _compute_rate_limit_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        proxy = cast(_RateLimitServiceProtocol, self)
        async with proxy._repo_factory() as repos:
            accounts = await repos.accounts.list_accounts()
            selected_accounts = _select_accounts_for_limits(accounts)
            if not selected_accounts:
                return headers

            account_map = {account.id: account for account in selected_accounts}
            primary_rows_raw, secondary_rows_raw = await asyncio.gather(
                self._latest_usage_rows(repos, account_map, "primary"),
                self._latest_usage_rows(repos, account_map, "secondary"),
            )
            monthly_rows = await self._latest_usage_rows(repos, account_map, "monthly")
            primary_rows, secondary_rows = usage_core.normalize_weekly_only_rows(
                primary_rows_raw,
                secondary_rows_raw,
            )

            primary_summary = _summarize_window(primary_rows, account_map, "primary")
            if primary_summary is not None:
                headers.update(_rate_limit_headers("primary", primary_summary))

            secondary_summary = _summarize_window(secondary_rows, account_map, "secondary")
            if secondary_summary is not None:
                headers.update(_rate_limit_headers("secondary", secondary_summary))
            monthly_summary = _summarize_window(monthly_rows, account_map, "monthly")
            if monthly_summary is not None:
                headers.update(_rate_limit_headers("monthly", monthly_summary))

            headers.update(_credits_headers(await self._latest_usage_entries(repos, account_map)))
        return headers

    async def get_rate_limit_payload(self) -> RateLimitStatusPayloadData:
        proxy = cast(_RateLimitServiceProtocol, self)
        async with proxy._repo_factory() as repos:
            accounts = await repos.accounts.list_accounts()
            await self._refresh_usage(repos, accounts)
            selected_accounts = _select_accounts_for_limits(accounts)
            if not selected_accounts:
                return RateLimitStatusPayloadData(plan_type="guest")

            account_map = {account.id: account for account in selected_accounts}
            primary_rows_raw, secondary_rows_raw = await asyncio.gather(
                self._latest_usage_rows(repos, account_map, "primary"),
                self._latest_usage_rows(repos, account_map, "secondary"),
            )
            monthly_rows = await self._latest_usage_rows(repos, account_map, "monthly")
            primary_rows, secondary_rows = usage_core.normalize_weekly_only_rows(
                primary_rows_raw,
                secondary_rows_raw,
            )

            primary_summary = _summarize_window(primary_rows, account_map, "primary")
            secondary_summary = _summarize_window(secondary_rows, account_map, "secondary")
            monthly_summary = _summarize_window(monthly_rows, account_map, "monthly")

            now_epoch = int(time.time())
            primary_window = _window_snapshot(primary_summary, primary_rows, "primary", now_epoch)
            secondary_window = _window_snapshot(secondary_summary, secondary_rows, "secondary", now_epoch)
            monthly_window = _window_snapshot(monthly_summary, monthly_rows, "monthly", now_epoch)
            limit_reached = not _has_available_usage_account(
                primary_rows=primary_rows,
                secondary_rows=secondary_rows,
                monthly_rows=monthly_rows,
                account_map=account_map,
            )

            additional_rate_limits = await self._build_additional_rate_limits(repos, account_map, now_epoch)

            return RateLimitStatusPayloadData(
                plan_type=_plan_type_for_accounts(selected_accounts),
                rate_limit=_rate_limit_details(
                    primary_window,
                    secondary_window,
                    monthly_window,
                    limit_reached=limit_reached,
                ),
                credits=_credits_snapshot(await self._latest_usage_entries(repos, account_map)),
                additional_rate_limits=additional_rate_limits,
            )

    async def _refresh_usage(self, repos: ProxyRepositories, accounts: list[Account]) -> None:
        latest_usage = await repos.usage.latest_by_account(window="primary")
        updater = UsageUpdater(repos.usage, repos.accounts, repos.additional_usage)
        await updater.refresh_accounts(accounts, latest_usage)

    async def _latest_usage_rows(
        self,
        repos: ProxyRepositories,
        account_map: dict[str, Account],
        window: str,
    ) -> list[UsageWindowRow]:
        if not account_map:
            return []
        latest = await repos.usage.latest_by_account(window=window)
        return [usage_history_to_window_row(entry) for entry in latest.values() if entry.account_id in account_map]

    async def _latest_usage_entries(
        self,
        repos: ProxyRepositories,
        account_map: dict[str, Account],
    ) -> list[UsageHistory]:
        if not account_map:
            return []
        latest = await repos.usage.latest_by_account()
        entries = [entry for entry in latest.values() if entry.account_id in account_map]
        seen_accounts = {entry.account_id for entry in entries}
        missing_accounts = set(account_map) - seen_accounts
        if not missing_accounts:
            return entries

        monthly_latest = await repos.usage.latest_by_account(window="monthly")
        entries.extend(entry for entry in monthly_latest.values() if entry.account_id in missing_accounts)
        return entries

    async def _build_additional_rate_limits(
        self,
        repos: ProxyRepositories,
        account_map: dict[str, Account],
        now_epoch: int,
    ) -> list[AdditionalRateLimitData]:
        """Build additional rate limit entries from AdditionalUsageRepository."""
        if not account_map:
            return []

        limit_names = await repos.additional_usage.list_limit_names(account_ids=list(account_map.keys()))
        additional_limits = []

        for limit_name in limit_names:
            latest_entries = await repos.additional_usage.latest_by_account(
                limit_name=limit_name,
                window="primary",
            )
            latest_secondary = await repos.additional_usage.latest_by_account(
                limit_name=limit_name,
                window="secondary",
            )

            filtered_entries = {
                account_id: entry for account_id, entry in latest_entries.items() if account_id in account_map
            }
            filtered_secondary = {
                account_id: entry for account_id, entry in latest_secondary.items() if account_id in account_map
            }

            if not filtered_entries and not filtered_secondary:
                continue

            first_entry = (
                next(iter(filtered_entries.values())) if filtered_entries else next(iter(filtered_secondary.values()))
            )
            metered_feature = first_entry.metered_feature

            window_snapshot = None
            avg_used_percent = None
            if filtered_entries:
                used_percents = [
                    entry.used_percent for entry in filtered_entries.values() if entry.used_percent is not None
                ]
                if used_percents:
                    avg_used_percent = sum(used_percents) / len(used_percents)
                    window_minutes_values = [e.window_minutes for e in filtered_entries.values() if e.window_minutes]
                    reset_at_values = [e.reset_at for e in filtered_entries.values() if e.reset_at is not None]

                    if window_minutes_values and reset_at_values:
                        window_minutes = max(window_minutes_values)
                        limit_window_seconds = int(window_minutes * 60)
                        reset_at = int(min(reset_at_values))
                        reset_after_seconds = max(0, reset_at - now_epoch)

                        window_snapshot = RateLimitWindowSnapshotData(
                            used_percent=int(max(0.0, min(100.0, avg_used_percent))),
                            limit_window_seconds=limit_window_seconds,
                            reset_after_seconds=reset_after_seconds,
                            reset_at=reset_at,
                        )
                    else:
                        # Timing metadata absent - still emit used_percent
                        # so clients retain visibility into quota consumption.
                        window_snapshot = RateLimitWindowSnapshotData(
                            used_percent=int(max(0.0, min(100.0, avg_used_percent))),
                        )

            secondary_window_snapshot = None
            if filtered_secondary:
                sec_used_percents = [e.used_percent for e in filtered_secondary.values() if e.used_percent is not None]
                if sec_used_percents:
                    sec_avg = sum(sec_used_percents) / len(sec_used_percents)
                    sec_window_values = [e.window_minutes for e in filtered_secondary.values() if e.window_minutes]
                    sec_reset_values = [e.reset_at for e in filtered_secondary.values() if e.reset_at is not None]

                    if sec_window_values and sec_reset_values:
                        sec_window_minutes = max(sec_window_values)
                        sec_limit_window_seconds = int(sec_window_minutes * 60)
                        sec_reset_at = int(min(sec_reset_values))
                        sec_reset_after_seconds = max(0, sec_reset_at - now_epoch)
                        secondary_window_snapshot = RateLimitWindowSnapshotData(
                            used_percent=int(max(0.0, min(100.0, sec_avg))),
                            limit_window_seconds=sec_limit_window_seconds,
                            reset_after_seconds=sec_reset_after_seconds,
                            reset_at=sec_reset_at,
                        )
                    else:
                        secondary_window_snapshot = RateLimitWindowSnapshotData(
                            used_percent=int(max(0.0, min(100.0, sec_avg))),
                        )

            rate_limit_details = None
            if avg_used_percent is not None or secondary_window_snapshot is not None:
                # Per-account availability: an account is available when
                # neither its primary nor secondary window is exhausted.
                # Pool is allowed when at least one account can serve.
                all_account_ids = set(filtered_entries.keys()) | set(filtered_secondary.keys())
                any_available = False
                for aid in all_account_ids:
                    pri_pct = filtered_entries[aid].used_percent if aid in filtered_entries else 0.0
                    sec_pct = filtered_secondary[aid].used_percent if aid in filtered_secondary else 0.0
                    if pri_pct < 100.0 and sec_pct < 100.0:
                        any_available = True
                        break
                rate_limit_details = RateLimitStatusDetailsData(
                    allowed=any_available,
                    limit_reached=not any_available,
                    primary_window=window_snapshot,
                    secondary_window=secondary_window_snapshot,
                )

            additional_limits.append(
                AdditionalRateLimitData(
                    quota_key=limit_name,
                    limit_name=first_entry.limit_name,
                    display_label=get_additional_display_label_for_quota_key(limit_name) or first_entry.limit_name,
                    metered_feature=metered_feature,
                    rate_limit=rate_limit_details,
                )
            )

        return additional_limits
