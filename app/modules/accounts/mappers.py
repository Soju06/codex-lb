from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core import usage as usage_core
from app.core.auth import DEFAULT_EMAIL, DEFAULT_PLAN, extract_id_token_claims, token_expiry_epoch_ms
from app.core.config import settings as config_settings
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.usage.quota import apply_usage_quota
from app.core.usage.types import UsageTrendBucket, UsageWindowRow
from app.core.utils.time import from_epoch_seconds
from app.db.models import Account, AccountLimitWarmup, AccountStatus, UsageHistory
from app.modules.accounts.schemas import (
    AccountAdditionalQuota,
    AccountAuthStatus,
    AccountLimitWarmupStatus,
    AccountRequestUsage,
    AccountSummary,
    AccountTokenStatus,
    AccountUsage,
    AccountUsageTrend,
    UsageTrendPoint,
)
from app.modules.rate_limit_reset_credits.store import (
    RateLimitResetCreditsSnapshot,
    RateLimitResetCreditsStore,
    get_rate_limit_reset_credits_store,
)
from app.modules.usage.mappers import usage_history_to_window_row

_ACCOUNT_ROUTING_POLICIES = frozenset({"burn_first", "normal", "preserve"})
_RESET_CREDITS_INELIGIBLE_STATUSES = frozenset(
    {AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED}
)
_DEFAULT_USAGE_REFRESH_INTERVAL_SECONDS = 60


def build_account_summaries(
    *,
    accounts: list[Account],
    primary_usage: dict[str, UsageHistory],
    secondary_usage: dict[str, UsageHistory],
    monthly_usage: dict[str, UsageHistory] | None = None,
    request_usage_by_account: dict[str, AccountRequestUsage] | None = None,
    additional_quotas_by_account: dict[str, list[AccountAdditionalQuota]] | None = None,
    limit_warmups_by_account: dict[str, AccountLimitWarmup] | None = None,
    encryptor: TokenEncryptor,
    include_auth: bool = True,
    reset_credits_store: RateLimitResetCreditsStore | None = None,
) -> list[AccountSummary]:
    store = reset_credits_store or get_rate_limit_reset_credits_store()
    duplicate_keys = _duplicate_detection_keys_appearing_more_than_once(accounts)
    return [
        _account_to_summary(
            account,
            primary_usage.get(account.id),
            secondary_usage.get(account.id),
            monthly_usage.get(account.id) if monthly_usage else None,
            request_usage_by_account.get(account.id) if request_usage_by_account else None,
            additional_quotas_by_account.get(account.id) if additional_quotas_by_account else None,
            limit_warmups_by_account.get(account.id) if limit_warmups_by_account else None,
            encryptor,
            include_auth=include_auth,
            is_email_duplicate=_duplicate_detection_key(account) in duplicate_keys,
            reset_credits_snapshot=_reset_credits_snapshot_for_account(account, store),
        )
        for account in accounts
    ]


def _duplicate_detection_keys_appearing_more_than_once(accounts: list[Account]) -> set[tuple[str, str, str | None]]:
    """Return duplicate (email, ChatGPT account id, workspace slot key) keys in this list.

    Emails are compared case-sensitively to match the storage normalization
    already performed at OAuth-import time. Blank/None emails, the legacy
    DEFAULT_EMAIL placeholder used by malformed imports, and rows without a
    ChatGPT account identity are excluded so valid same-email accounts in
    different workspaces are not flagged as stale/fresh duplicates.
    """
    counts: dict[tuple[str, str, str | None], int] = {}
    for account in accounts:
        key = _duplicate_detection_key(account)
        if key is None:
            continue
        counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count > 1}


def _duplicate_detection_key(account: Account) -> tuple[str, str, str | None] | None:
    email = account.email
    chatgpt_account_id = account.chatgpt_account_id
    if not _is_duplicate_detection_email(email) or not chatgpt_account_id:
        return None
    return email, chatgpt_account_id, account.workspace_id or account.workspace_label  # ty:ignore[invalid-return-type]


def _is_duplicate_detection_email(email: str | None) -> bool:
    return bool(email and email.strip()) and email != DEFAULT_EMAIL


def _account_to_summary(
    account: Account,
    primary_usage: UsageHistory | None,
    secondary_usage: UsageHistory | None,
    monthly_usage: UsageHistory | None,
    request_usage: AccountRequestUsage | None,
    additional_quotas: list[AccountAdditionalQuota] | None,
    limit_warmup: AccountLimitWarmup | None,
    encryptor: TokenEncryptor,
    include_auth: bool = True,
    is_email_duplicate: bool = False,
    reset_credits_snapshot: RateLimitResetCreditsSnapshot | None = None,
) -> AccountSummary:
    plan_type = coerce_account_plan_type(account.plan_type, DEFAULT_PLAN)
    auth_status = _build_auth_status(account, encryptor) if include_auth else None
    effective_primary_usage, effective_secondary_usage = _effective_usage_windows(
        primary_usage,
        secondary_usage,
    )

    if monthly_usage is not None and usage_core.capacity_for_plan(plan_type, "monthly") is None:
        monthly_usage = None
    monthly_used_percent = _normalize_used_percent(monthly_usage)
    monthly_remaining_percent = usage_core.remaining_percent_from_used(monthly_used_percent)
    if monthly_usage is not None:
        effective_primary_usage = None
        effective_secondary_usage = None

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

    status_primary_usage = effective_primary_usage
    status_primary_used_percent = primary_used_percent
    status_runtime_reset = float(account.reset_at) if account.reset_at else None
    status_seed = account.status
    allow_missing_runtime_reset_recovery = False
    long_quota_usage = monthly_usage or effective_secondary_usage
    long_quota_available = (
        long_quota_usage is not None
        and _usage_entry_is_recent_enough(long_quota_usage.recorded_at)
        and long_quota_usage.used_percent is not None
        and float(long_quota_usage.used_percent) < 100.0
    )
    if usage_core.capacity_for_plan(plan_type, "primary") == 0.0:
        primary_window_minutes = (
            effective_primary_usage.window_minutes
            if effective_primary_usage is not None
            else primary_usage.window_minutes
            if weekly_only_usage and primary_usage is not None
            else None
        )
        zero_capacity_primary_known_non_primary = (
            primary_window_minutes is not None and not usage_core.is_primary_window_minutes(primary_window_minutes)
        )
        keep_primary_status_signal = (
            account.status == AccountStatus.RATE_LIMITED
            and usage_core.is_primary_window_minutes(primary_window_minutes)
        )
        can_hide_zero_capacity_primary = account.status != AccountStatus.RATE_LIMITED or (
            zero_capacity_primary_known_non_primary and long_quota_available
        )
        if not keep_primary_status_signal and can_hide_zero_capacity_primary:
            status_primary_usage = None
            status_primary_used_percent = None
            if account.status == AccountStatus.RATE_LIMITED:
                status_runtime_reset = None
                status_seed = AccountStatus.ACTIVE
                allow_missing_runtime_reset_recovery = True
        effective_primary_usage = None
        primary_used_percent = None
        primary_remaining_percent = None

    reset_at_primary = (
        from_epoch_seconds(effective_primary_usage.reset_at) if effective_primary_usage is not None else None
    )
    reset_at_secondary = (
        from_epoch_seconds(effective_secondary_usage.reset_at) if effective_secondary_usage is not None else None
    )
    reset_at_monthly = from_epoch_seconds(monthly_usage.reset_at) if monthly_usage is not None else None
    window_minutes_primary = effective_primary_usage.window_minutes if effective_primary_usage is not None else None
    window_minutes_secondary = (
        effective_secondary_usage.window_minutes if effective_secondary_usage is not None else None
    )
    window_minutes_monthly = monthly_usage.window_minutes if monthly_usage is not None else None
    capacity_primary = usage_core.capacity_for_plan(plan_type, "primary")
    capacity_secondary = usage_core.capacity_for_plan(plan_type, "secondary")
    capacity_monthly = usage_core.capacity_for_plan(plan_type, "monthly")
    remaining_credits_primary = usage_core.remaining_credits_from_percent(
        primary_used_percent,
        capacity_primary,
    )
    remaining_credits_secondary = usage_core.remaining_credits_from_percent(
        secondary_used_percent,
        capacity_secondary,
    )
    remaining_credits_monthly = usage_core.remaining_credits_from_percent(
        monthly_used_percent,
        capacity_monthly,
    )
    credits_has, credits_unlimited, credits_balance = _extract_credit_status(
        effective_primary_usage,
        effective_secondary_usage,
        monthly_usage,
        primary_usage,
        secondary_usage,
    )
    effective_status = _effective_status_from_usage(
        account,
        status_seed=status_seed,
        primary_usage=status_primary_usage,
        primary_used_percent=status_primary_used_percent,
        secondary_usage=effective_secondary_usage,
        secondary_used_percent=secondary_used_percent,
        monthly_usage=monthly_usage,
        monthly_used_percent=monthly_used_percent,
        runtime_reset=status_runtime_reset,
        credits_has=credits_has,
        credits_unlimited=credits_unlimited,
        credits_balance=credits_balance,
        allow_missing_runtime_reset_recovery=allow_missing_runtime_reset_recovery,
    )
    # Provider-aware: claude accounts store the real email in claude_user_email.
    _effective_email = account.email or account.claude_user_email or ""
    return AccountSummary(
        account_id=account.id,
        chatgpt_account_id=account.chatgpt_account_id,
        email=_effective_email,
        alias=account.alias,
        display_name=account.alias or _effective_email,
        workspace_id=account.workspace_id,
        workspace_label=account.workspace_label,
        seat_type=account.seat_type,
        plan_type=plan_type,
        status=effective_status.value,
        routing_policy=_normalize_account_routing_policy(account.routing_policy),
        security_work_authorized=bool(account.security_work_authorized),
        usage=AccountUsage(
            primary_remaining_percent=primary_remaining_percent,
            secondary_remaining_percent=secondary_remaining_percent,
            monthly_remaining_percent=monthly_remaining_percent,
        ),
        reset_at_primary=reset_at_primary,
        reset_at_secondary=reset_at_secondary,
        reset_at_monthly=reset_at_monthly,
        window_minutes_primary=window_minutes_primary,
        window_minutes_secondary=window_minutes_secondary,
        window_minutes_monthly=window_minutes_monthly,
        last_refresh_at=account.last_refresh,
        capacity_credits_primary=capacity_primary,
        remaining_credits_primary=remaining_credits_primary,
        capacity_credits_secondary=capacity_secondary,
        remaining_credits_secondary=remaining_credits_secondary,
        capacity_credits_monthly=capacity_monthly,
        remaining_credits_monthly=remaining_credits_monthly,
        credits_has=credits_has,
        credits_unlimited=credits_unlimited,
        credits_balance=credits_balance,
        request_usage=request_usage,
        additional_quotas=additional_quotas or [],
        deactivation_reason=account.deactivation_reason,
        auth=auth_status,
        limit_warmup_enabled=bool(account.limit_warmup_enabled),
        limit_warmup=_limit_warmup_to_status(limit_warmup),
        is_email_duplicate=is_email_duplicate,
        available_reset_credits=reset_credits_snapshot.available_count if reset_credits_snapshot else 0,
        reset_credit_nearest_expires_at=(reset_credits_snapshot.nearest_expires_at if reset_credits_snapshot else None),
    )


def _normalize_account_routing_policy(value: str | None) -> str:
    if value in _ACCOUNT_ROUTING_POLICIES:
        return value
    return "normal"


def _reset_credits_snapshot_for_account(
    account: Account,
    store: RateLimitResetCreditsStore,
) -> RateLimitResetCreditsSnapshot | None:
    if account.status in _RESET_CREDITS_INELIGIBLE_STATUSES or not account.chatgpt_account_id:
        return None
    return store.get(account.id)


def _limit_warmup_to_status(entry: AccountLimitWarmup | None) -> AccountLimitWarmupStatus | None:
    if entry is None:
        return None
    return AccountLimitWarmupStatus(
        window=entry.window,
        reset_at=entry.reset_at,
        status=entry.status,
        model=entry.model,
        attempted_at=entry.attempted_at,
        completed_at=entry.completed_at,
        error_code=entry.error_code,
        error_message=entry.error_message,
    )


def _effective_status_from_usage(
    account: Account,
    *,
    status_seed: AccountStatus,
    primary_usage: UsageHistory | None,
    primary_used_percent: float | None,
    secondary_usage: UsageHistory | None,
    secondary_used_percent: float | None,
    monthly_usage: UsageHistory | None,
    monthly_used_percent: float | None,
    runtime_reset: float | None,
    credits_has: bool | None = None,
    credits_unlimited: bool | None = None,
    credits_balance: float | None = None,
    allow_missing_runtime_reset_recovery: bool = False,
) -> AccountStatus:
    long_window_usage = monthly_usage or secondary_usage
    long_window_used_percent = monthly_used_percent if monthly_usage is not None else secondary_used_percent
    if credits_has is None and credits_unlimited is None and credits_balance is None:
        credits_has, credits_unlimited, credits_balance = _extract_credit_status(
            primary_usage,
            long_window_usage,
        )
    status, _, _ = apply_usage_quota(
        status=status_seed,
        primary_used=primary_used_percent,
        primary_reset=primary_usage.reset_at if primary_usage is not None else None,
        primary_window_minutes=primary_usage.window_minutes if primary_usage is not None else None,
        runtime_reset=runtime_reset,
        secondary_used=long_window_used_percent,
        secondary_reset=long_window_usage.reset_at if long_window_usage is not None else None,
        credits_has=credits_has,
        credits_unlimited=credits_unlimited,
        credits_balance=credits_balance,
    )
    if account.status == AccountStatus.RATE_LIMITED and status == AccountStatus.ACTIVE:
        if runtime_reset is None and allow_missing_runtime_reset_recovery:
            return status
        if _has_credit_override(
            credits_has=credits_has,
            credits_unlimited=credits_unlimited,
            credits_balance=credits_balance,
        ):
            return status
        if (
            account.blocked_at is None
            and account.reset_at is not None
            and account.reset_at <= datetime.now(timezone.utc).timestamp()
        ):
            return status
        return account.status
    return status


def _has_credit_override(
    *,
    credits_has: bool | None,
    credits_unlimited: bool | None,
    credits_balance: float | None,
) -> bool:
    return credits_unlimited is True or credits_has is True or (credits_balance is not None and credits_balance > 0)


def _first_not_none(primary_usage: UsageHistory | None, secondary_usage: UsageHistory | None, field: str):
    if primary_usage is not None:
        value = getattr(primary_usage, field)
        if value is not None:
            return value
    if secondary_usage is not None:
        return getattr(secondary_usage, field)
    return None


def _usage_entry_is_recent_enough(recorded_at: datetime | None) -> bool:
    if recorded_at is None:
        return False
    current_time = datetime.now(timezone.utc)
    interval_seconds = max(_usage_refresh_interval_seconds() * 2, 180)
    recorded_time = recorded_at if recorded_at.tzinfo is not None else recorded_at.replace(tzinfo=timezone.utc)
    return recorded_time >= current_time - timedelta(seconds=interval_seconds)


def _usage_refresh_interval_seconds() -> int:
    settings = config_settings.get_settings()
    return int(getattr(settings, "usage_refresh_interval_seconds", _DEFAULT_USAGE_REFRESH_INTERVAL_SECONDS))


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
    if usage_core.should_use_weekly_primary(
        usage_history_to_window_row(primary_usage), usage_history_to_window_row(secondary_usage)
    ):
        return None, primary_usage
    return None, secondary_usage


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
    expires_ms = token_expiry_epoch_ms(token)
    if expires_ms is not None:
        return datetime.fromtimestamp(expires_ms / 1000, tz=timezone.utc)
    return None


def _normalize_used_percent(entry: UsageHistory | None) -> float | None:
    if not entry:
        return None
    return entry.used_percent


def _extract_credit_status(
    *entries: UsageHistory | None,
) -> tuple[bool | None, bool | None, float | None]:
    credit_entries = [
        entry
        for entry in entries
        if entry is not None
        and not (entry.credits_has is None and entry.credits_unlimited is None and entry.credits_balance is None)
    ]
    if not credit_entries:
        return None, None, None
    entry = max(
        credit_entries,
        key=lambda item: item.recorded_at if item.recorded_at is not None else datetime.min,
    )
    return entry.credits_has, entry.credits_unlimited, entry.credits_balance


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
        window = "secondary" if is_weekly_primary or b.window == "monthly" else b.window
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
