from __future__ import annotations

from datetime import datetime, timezone

from app.core import usage as usage_core
from app.core.auth import DEFAULT_PLAN, extract_id_token_claims
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.usage.types import UsageTrendBucket, UsageWindowRow
from app.core.utils.time import from_epoch_seconds
from app.db.models import Account, UsageHistory
from app.modules.accounts.schemas import (
    AccountAdditionalQuota,
    AccountAuthStatus,
    AccountRequestUsage,
    AccountSummary,
    AccountTokenStatus,
    AccountUsage,
    AccountUsageTrend,
    UsageTrendPoint,
)


def build_account_summaries(
    *,
    accounts: list[Account],
    primary_usage: dict[str, UsageHistory],
    secondary_usage: dict[str, UsageHistory],
    request_usage_by_account: dict[str, AccountRequestUsage] | None = None,
    additional_quotas_by_account: dict[str, list[AccountAdditionalQuota]] | None = None,
    encryptor: TokenEncryptor,
    include_auth: bool = True,
) -> list[AccountSummary]:
    return [
        _account_to_summary(
            account,
            primary_usage.get(account.id),
            secondary_usage.get(account.id),
            request_usage_by_account.get(account.id) if request_usage_by_account else None,
            additional_quotas_by_account.get(account.id) if additional_quotas_by_account else None,
            encryptor,
            include_auth=include_auth,
        )
        for account in accounts
    ]


def _account_to_summary(
    account: Account,
    primary_usage: UsageHistory | None,
    secondary_usage: UsageHistory | None,
    request_usage: AccountRequestUsage | None,
    additional_quotas: list[AccountAdditionalQuota] | None,
    encryptor: TokenEncryptor,
    include_auth: bool = True,
) -> AccountSummary:
    plan_type = coerce_account_plan_type(account.plan_type, DEFAULT_PLAN)
    auth_status = _build_auth_status(account, encryptor) if include_auth else None
    effective_primary_usage, effective_secondary_usage = _effective_usage_windows(
        primary_usage,
        secondary_usage,
    )
    weekly_only_usage = (
        effective_primary_usage is None
        and primary_usage is not None
        and usage_core.is_weekly_window_minutes(primary_usage.window_minutes)
    )
    # Keep account payload aligned with UI semantics: weekly-only plans expose
    # their quota as secondary/7d and omit primary/5h fields.
    primary_used_percent = _normalize_used_percent(effective_primary_usage)
    secondary_used_percent = _normalize_used_percent(effective_secondary_usage)
    primary_remaining_percent = usage_core.remaining_percent_from_used(primary_used_percent)
    secondary_remaining_percent = usage_core.remaining_percent_from_used(secondary_used_percent)

    if primary_remaining_percent is None and not weekly_only_usage:
        primary_remaining_percent = 100.0
    reset_at_primary = (
        from_epoch_seconds(effective_primary_usage.reset_at) if effective_primary_usage is not None else None
    )
    reset_at_secondary = (
        from_epoch_seconds(effective_secondary_usage.reset_at) if effective_secondary_usage is not None else None
    )
    window_minutes_primary = effective_primary_usage.window_minutes if effective_primary_usage is not None else None
    window_minutes_secondary = (
        effective_secondary_usage.window_minutes if effective_secondary_usage is not None else None
    )
    capacity_primary = usage_core.capacity_for_plan(plan_type, "primary")
    capacity_secondary = usage_core.capacity_for_plan(plan_type, "secondary")
    remaining_credits_primary = usage_core.remaining_credits_from_percent(
        primary_used_percent,
        capacity_primary,
    )
    remaining_credits_secondary = usage_core.remaining_credits_from_percent(
        secondary_used_percent,
        capacity_secondary,
    )
    return AccountSummary(
        account_id=account.id,
        email=account.email,
        display_name=account.email,
        plan_type=plan_type,
        status=account.status.value,
        usage=AccountUsage(
            primary_remaining_percent=primary_remaining_percent,
            secondary_remaining_percent=secondary_remaining_percent,
        ),
        reset_at_primary=reset_at_primary,
        reset_at_secondary=reset_at_secondary,
        window_minutes_primary=window_minutes_primary,
        window_minutes_secondary=window_minutes_secondary,
        last_refresh_at=account.last_refresh,
        capacity_credits_primary=capacity_primary,
        remaining_credits_primary=remaining_credits_primary,
        capacity_credits_secondary=capacity_secondary,
        remaining_credits_secondary=remaining_credits_secondary,
        request_usage=request_usage,
        additional_quotas=additional_quotas or [],
        deactivation_reason=account.deactivation_reason,
        auth=auth_status,
    )


def _effective_usage_windows(
    primary_usage: UsageHistory | None,
    secondary_usage: UsageHistory | None,
) -> tuple[UsageHistory | None, UsageHistory | None]:
    if primary_usage is None:
        return None, secondary_usage
    if not usage_core.is_weekly_window_minutes(primary_usage.window_minutes):
        return primary_usage, secondary_usage
    if secondary_usage is None:
        return None, primary_usage
    if usage_core.should_use_weekly_primary(_to_window_row(primary_usage), _to_window_row(secondary_usage)):
        return None, primary_usage
    return None, secondary_usage


def _to_window_row(entry: UsageHistory) -> UsageWindowRow:
    return UsageWindowRow(
        account_id=entry.account_id,
        used_percent=entry.used_percent,
        reset_at=entry.reset_at,
        window_minutes=entry.window_minutes,
        recorded_at=entry.recorded_at,
    )


def _build_auth_status(account: Account, encryptor: TokenEncryptor) -> AccountAuthStatus:
    access_token = _decrypt_token(encryptor, account.access_token_encrypted)
    refresh_token = _decrypt_token(encryptor, account.refresh_token_encrypted)
    id_token = _decrypt_token(encryptor, account.id_token_encrypted)

    access_expires = _token_expiry(access_token)
    refresh_state = "stored" if refresh_token else "missing"
    id_state = "unknown"
    if id_token:
        claims = extract_id_token_claims(id_token)
        if claims.model_dump(exclude_none=True):
            id_state = "parsed"

    return AccountAuthStatus(
        access=AccountTokenStatus(expires_at=access_expires),
        refresh=AccountTokenStatus(state=refresh_state),
        id_token=AccountTokenStatus(state=id_state),
    )


def _decrypt_token(encryptor: TokenEncryptor, encrypted: bytes | None) -> str | None:
    if not encrypted:
        return None
    try:
        return encryptor.decrypt(encrypted)
    except Exception:
        return None


def _token_expiry(token: str | None) -> datetime | None:
    if not token:
        return None
    claims = extract_id_token_claims(token)
    exp = claims.exp
    if isinstance(exp, (int, float)):
        return datetime.fromtimestamp(exp, tz=timezone.utc)
    if isinstance(exp, str) and exp.isdigit():
        return datetime.fromtimestamp(int(exp), tz=timezone.utc)
    return None


def _normalize_used_percent(entry: UsageHistory | None) -> float | None:
    if not entry:
        return None
    return entry.used_percent


def build_account_usage_trends(
    buckets: list[UsageTrendBucket],
    since_epoch: int,
    bucket_seconds: int,
    bucket_count: int,
) -> dict[str, AccountUsageTrend]:
    """Convert raw UsageTrendBucket rows into per-account trend data.

    Values are expressed as remaining_percent (100 - used_percent) for UI consistency.
    Empty buckets are filled with the last known value (or 100.0 if no prior data).
    """
    # Group buckets by (account_id, window)
    grouped: dict[tuple[str, str], dict[int, float]] = {}
    secondary_schedule: dict[str, dict[int, tuple[int, int]]] = {}
    for b in _effective_usage_trend_buckets(buckets):
        is_weekly_primary = b.window == "primary" and usage_core.is_weekly_window_minutes(b.window_minutes)
        window = "secondary" if is_weekly_primary else b.window
        key = (b.account_id, window)
        grouped.setdefault(key, {})[b.bucket_epoch] = b.avg_used_percent
        if (
            (window == "secondary" or usage_core.is_weekly_window_minutes(b.window_minutes))
            and b.reset_at is not None
            and b.window_minutes
        ):
            secondary_schedule.setdefault(b.account_id, {})[b.bucket_epoch] = (
                b.reset_at,
                b.window_minutes,
            )

    # Generate the full time grid, aligned to bucket boundaries (same as SQL)
    aligned_start = (since_epoch // bucket_seconds) * bucket_seconds
    time_grid = [aligned_start + i * bucket_seconds for i in range(bucket_count)]

    result: dict[str, AccountUsageTrend] = {}
    # Collect all account_ids
    account_ids = {key[0] for key in grouped}

    for account_id in account_ids:
        primary_data = grouped.get((account_id, "primary"))
        secondary_data = grouped.get((account_id, "secondary"))

        primary_points = _fill_trend_points(time_grid, primary_data) if primary_data else []
        secondary_points = _fill_trend_points(time_grid, secondary_data) if secondary_data else []
        secondary_scheduled_points = _fill_scheduled_secondary_points(
            time_grid,
            secondary_schedule.get(account_id, {}),
        )

        result[account_id] = AccountUsageTrend(
            primary=primary_points,
            secondary=secondary_points,
            secondary_scheduled=secondary_scheduled_points,
        )

    return result


def _effective_usage_trend_buckets(buckets: list[UsageTrendBucket]) -> list[UsageTrendBucket]:
    secondary_by_key = {
        (bucket.account_id, bucket.bucket_epoch): bucket for bucket in buckets if bucket.window == "secondary"
    }
    weekly_primary_by_key = {
        (bucket.account_id, bucket.bucket_epoch): bucket
        for bucket in buckets
        if bucket.window == "primary" and usage_core.is_weekly_window_minutes(bucket.window_minutes)
    }
    result: list[UsageTrendBucket] = []
    for bucket in buckets:
        key = (bucket.account_id, bucket.bucket_epoch)
        weekly_primary = weekly_primary_by_key.get(key)
        if bucket.window == "secondary" and weekly_primary is not None:
            if usage_core.should_use_weekly_primary(
                _trend_bucket_to_window_row(weekly_primary),
                _trend_bucket_to_window_row(bucket),
            ):
                continue
        if bucket is weekly_primary and key in secondary_by_key:
            secondary = secondary_by_key[key]
            if not usage_core.should_use_weekly_primary(
                _trend_bucket_to_window_row(bucket),
                _trend_bucket_to_window_row(secondary),
            ):
                continue
        result.append(bucket)
    return result


def _trend_bucket_to_window_row(bucket: UsageTrendBucket) -> UsageWindowRow:
    return UsageWindowRow(
        account_id=bucket.account_id,
        used_percent=bucket.avg_used_percent,
        reset_at=bucket.reset_at,
        window_minutes=bucket.window_minutes,
        recorded_at=bucket.recorded_at,
    )


def _fill_trend_points(
    time_grid: list[int],
    bucket_data: dict[int, float],
) -> list[UsageTrendPoint]:
    """Fill missing buckets with last-known value and convert to remaining percent."""
    points: list[UsageTrendPoint] = []
    last_value = 100.0  # assume full remaining if no prior data
    for epoch in time_grid:
        if epoch in bucket_data:
            remaining = max(0.0, min(100.0, 100.0 - bucket_data[epoch]))
            last_value = remaining
        else:
            remaining = last_value
        points.append(
            UsageTrendPoint(
                t=datetime.fromtimestamp(epoch, tz=timezone.utc),
                v=round(remaining, 2),
            )
        )
    return points


def _fill_scheduled_secondary_points(
    time_grid: list[int],
    schedule_data: dict[int, tuple[int, int]],
) -> list[UsageTrendPoint]:
    """Build the ideal weekly remaining line from each sample's own reset deadline."""
    points: list[UsageTrendPoint] = []
    current_reset_at: int | None = None
    current_window_minutes: int | None = None

    for epoch in time_grid:
        if epoch in schedule_data:
            current_reset_at, current_window_minutes = schedule_data[epoch]

        if current_reset_at is None or not current_window_minutes:
            continue

        window_seconds = current_window_minutes * 60
        remaining_seconds = max(0, min(window_seconds, current_reset_at - epoch))
        scheduled_remaining = 100.0 * remaining_seconds / window_seconds
        points.append(
            UsageTrendPoint(
                t=datetime.fromtimestamp(epoch, tz=timezone.utc),
                v=round(scheduled_remaining, 2),
            )
        )

    return points
