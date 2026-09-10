from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from app.core import usage
from app.core.exceptions import ProxyUpstreamError
from app.core.plan_types import normalize_account_plan_type
from app.core.usage.refresh_policy import usage_freshness_horizon_seconds
from app.core.usage.types import UsageWindowRow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory
from app.modules.proxy.helpers import _window_snapshot
from app.modules.proxy.types import (
    AdditionalRateLimitData,
    RateLimitStatusDetailsData,
    RateLimitStatusPayloadData,
    RateLimitWindowSnapshotData,
)
from app.modules.usage.additional_quota_keys import canonicalize_additional_quota_key
from app.modules.usage.mappers import usage_history_to_window_row


class PooledUsageUnavailable(ProxyUpstreamError):
    """The stored observations cannot establish current pooled capacity."""

    code = "pooled_usage_unavailable"


def eligible_accounts(accounts: Sequence[Account]) -> list[Account]:
    excluded = {AccountStatus.PAUSED, AccountStatus.DEACTIVATED, AccountStatus.REAUTH_REQUIRED}
    return [account for account in accounts if account.status not in excluded]


def _validate(row: UsageWindowRow, now: datetime) -> None:
    recorded = row.recorded_at
    if recorded is None:
        raise PooledUsageUnavailable("Missing observation time")
    if recorded.tzinfo is None:
        recorded = recorded.replace(tzinfo=timezone.utc)
    age = (now - recorded).total_seconds()
    if not 0 <= age < usage_freshness_horizon_seconds():
        raise PooledUsageUnavailable("Stale observation")
    if row.used_percent is None or not math.isfinite(row.used_percent) or not 0 <= row.used_percent <= 100:
        raise PooledUsageUnavailable("Invalid usage percentage")
    if row.reset_at is None or row.reset_at <= now.timestamp() or not row.window_minutes or row.window_minutes <= 0:
        raise PooledUsageUnavailable("Missing or elapsed window timing")
    if row.reset_at > now.timestamp() + row.window_minutes * 60:
        raise PooledUsageUnavailable("Reset exceeds window duration")


def _details(
    accounts: Mapping[str, Account], rows: Mapping[str, list[UsageWindowRow]], now: datetime
) -> tuple[RateLimitStatusDetailsData, set[str]]:
    snapshots = {}
    available = {account_id for account_id, account in accounts.items() if account.status == AccountStatus.ACTIVE}
    for window, entries in rows.items():
        for row in entries:
            _validate(row, now)
            if row.used_percent == 100:
                available.discard(row.account_id)
        summary = usage.summarize_usage_window(entries, accounts, window)
        snapshots[window] = _window_snapshot(summary, entries, window, int(now.timestamp())) if entries else None
    return (
        RateLimitStatusDetailsData(
            allowed=bool(available),
            limit_reached=not available,
            primary_window=snapshots.get("primary"),
            secondary_window=snapshots.get("secondary"),
            monthly_window=snapshots.get("monthly"),
        ),
        available,
    )


def project_pool(
    accounts: Sequence[Account],
    windows: Mapping[str, list[UsageWindowRow]],
    additional: Mapping[str, Mapping[str, list[AdditionalUsageHistory]]],
    *,
    now: datetime,
) -> RateLimitStatusPayloadData:
    account_map = {account.id: account for account in eligible_accounts(accounts)}
    if not account_map:
        raise PooledUsageUnavailable("No eligible accounts")
    rows = {
        window: [row for row in windows.get(window, []) if row.account_id in account_map]
        for window in ("primary", "secondary", "monthly")
    }
    weekly_ids = {row.account_id for row in rows["primary"] if usage.is_weekly_window_minutes(row.window_minutes)}
    # Reuse the established same-fetch placeholder and historical-row tiebreak.
    # The selected effective row must still pass strict freshness validation.
    rows["primary"], rows["secondary"] = usage.normalize_weekly_only_rows(rows["primary"], rows["secondary"])
    for account_id, account in account_map.items():
        known = {window for window, entries in rows.items() if any(row.account_id == account_id for row in entries)}
        if not known:
            raise PooledUsageUnavailable("Missing account quota")
        for window in ("primary", "secondary", "monthly"):
            capacity = usage.capacity_for_plan(account.plan_type, window)
            required = capacity is not None and capacity > 0
            # A monthly-only free plan and a reported weekly-only plan are supported shapes.
            if window == "secondary" and known == {"monthly"}:
                required = False
            if window == "monthly" and "secondary" in known:
                required = False
            if window == "primary" and account_id in weekly_ids:
                required = False
            if required and window not in known:
                raise PooledUsageUnavailable("Missing applicable window")
            if window in known and (capacity is None or capacity <= 0):
                raise PooledUsageUnavailable("Unknown window capacity")
    main_limit, main_available = _details(account_map, rows, now)
    return RateLimitStatusPayloadData(
        plan_type="guest",  # Composition retains the caller's actual plan.
        rate_limit=main_limit,
        additional_rate_limits=_additional(account_map, additional, now, main_available),
    )


def _additional(
    accounts: Mapping[str, Account],
    groups: Mapping[str, Mapping[str, list[AdditionalUsageHistory]]],
    now: datetime,
    main_available: set[str],
) -> list[AdditionalRateLimitData]:
    result = []
    for key, windows in groups.items():
        entries = [entry for rows in windows.values() for entry in rows if entry.account_id in accounts]
        if not entries:
            continue
        # Reserves belong to the original caller and are preserved by composition.
        if all(canonicalize_additional_quota_key(limit_name=entry.limit_name) == "gpt_reserve" for entry in entries):
            continue
        contributors = {entry.account_id: accounts[entry.account_id] for entry in entries}
        identities = {(entry.limit_name, entry.metered_feature) for entry in entries}
        if len(identities) != 1:
            raise PooledUsageUnavailable("Unknown additional quota equivalence or capacity")
        rows = {}
        for window, values in windows.items():
            selected = [entry for entry in values if entry.account_id in contributors]
            if not selected:
                continue
            if {entry.account_id for entry in selected} != set(contributors):
                raise PooledUsageUnavailable("Missing additional window")
            rows[window] = [usage_history_to_window_row(entry) for entry in selected]
        available = set(contributors) & main_available
        snapshots = {}
        for window, values in rows.items():
            for row in values:
                _validate(row, now)
                if row.used_percent == 100:
                    available.discard(row.account_id)
            if len({row.window_minutes for row in values}) != 1:
                raise PooledUsageUnavailable("Additional quota windows have different durations")
            plans = {normalize_account_plan_type(contributors[row.account_id].plan_type) for row in values}
            if len(plans) > 1 and len({row.used_percent for row in values}) > 1:
                raise PooledUsageUnavailable("Additional quota capacity is unknown across different plans")
            snapshots[window] = RateLimitWindowSnapshotData(
                used_percent=int(sum(row.used_percent for row in values if row.used_percent is not None) / len(values)),
                reset_at=min(row.reset_at for row in values if row.reset_at is not None),
                reset_after_seconds=min(row.reset_at for row in values if row.reset_at is not None)
                - int(now.timestamp()),
                limit_window_seconds=max(row.window_minutes for row in values if row.window_minutes is not None) * 60,
            )
        details = RateLimitStatusDetailsData(
            allowed=bool(available),
            limit_reached=not available,
            primary_window=snapshots.get("primary"),
            secondary_window=snapshots.get("secondary"),
        )
        if details.primary_window is None and details.secondary_window is None:
            raise PooledUsageUnavailable("Unknown additional window capacity")
        name, feature = next(iter(identities))
        result.append(
            AdditionalRateLimitData(quota_key=key, limit_name=name, metered_feature=feature, rate_limit=details)
        )
    return result
