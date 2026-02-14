from __future__ import annotations

from datetime import datetime, timezone

from app.core import usage as usage_core
from app.core.auth import DEFAULT_PLAN, extract_id_token_claims
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.utils.time import from_epoch_seconds
from app.db.models import Account, UsageHistory
from app.modules.accounts.schemas import AccountAuthStatus, AccountSummary, AccountTokenStatus, AccountUsage


def build_account_summaries(
    *,
    accounts: list[Account],
    primary_usage: dict[str, UsageHistory],
    secondary_usage: dict[str, UsageHistory],
    spark_primary_usage: dict[str, UsageHistory],
    spark_secondary_usage: dict[str, UsageHistory],
    encryptor: TokenEncryptor,
) -> list[AccountSummary]:
    return [
        _account_to_summary(
            account,
            primary_usage.get(account.id),
            secondary_usage.get(account.id),
            spark_primary_usage.get(account.id),
            spark_secondary_usage.get(account.id),
            encryptor,
        )
        for account in accounts
    ]


def _account_to_summary(
    account: Account,
    primary_usage: UsageHistory | None,
    secondary_usage: UsageHistory | None,
    spark_primary_usage: UsageHistory | None,
    spark_secondary_usage: UsageHistory | None,
    encryptor: TokenEncryptor,
) -> AccountSummary:
    plan_type = coerce_account_plan_type(account.plan_type, DEFAULT_PLAN)
    auth_status = _build_auth_status(account, encryptor)
    primary_used_percent = _normalize_used_percent(primary_usage) or 0.0
    secondary_used_percent = _normalize_used_percent(secondary_usage) or 0.0
    spark_primary_used_percent = _normalize_used_percent(spark_primary_usage)
    spark_secondary_used_percent = _normalize_used_percent(spark_secondary_usage)
    primary_remaining_percent = usage_core.remaining_percent_from_used(primary_used_percent) or 0.0
    secondary_remaining_percent = usage_core.remaining_percent_from_used(secondary_used_percent) or 0.0
    spark_primary_remaining_percent = usage_core.remaining_percent_from_used(spark_primary_used_percent)
    spark_secondary_remaining_percent = usage_core.remaining_percent_from_used(spark_secondary_used_percent)
    reset_at_primary = from_epoch_seconds(primary_usage.reset_at) if primary_usage is not None else None
    reset_at_secondary = from_epoch_seconds(secondary_usage.reset_at) if secondary_usage is not None else None
    reset_at_spark_primary = (
        from_epoch_seconds(spark_primary_usage.reset_at) if spark_primary_usage is not None else None
    )
    reset_at_spark_secondary = (
        from_epoch_seconds(spark_secondary_usage.reset_at) if spark_secondary_usage is not None else None
    )
    spark_window_label_raw = (
        spark_primary_usage.window_label if spark_primary_usage and spark_primary_usage.window_label else None
    )
    if spark_window_label_raw is None and spark_secondary_usage and spark_secondary_usage.window_label:
        spark_window_label_raw = spark_secondary_usage.window_label
    spark_window_label = (
        usage_core.normalize_spark_window_label(spark_window_label_raw)
        if spark_primary_usage is not None or spark_secondary_usage is not None
        else None
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
            spark_primary_remaining_percent=spark_primary_remaining_percent,
            spark_secondary_remaining_percent=spark_secondary_remaining_percent,
        ),
        reset_at_primary=reset_at_primary,
        reset_at_secondary=reset_at_secondary,
        reset_at_spark_primary=reset_at_spark_primary,
        reset_at_spark_secondary=reset_at_spark_secondary,
        spark_window_label=spark_window_label,
        last_refresh_at=account.last_refresh,
        capacity_credits_primary=capacity_primary,
        remaining_credits_primary=remaining_credits_primary,
        capacity_credits_secondary=capacity_secondary,
        remaining_credits_secondary=remaining_credits_secondary,
        deactivation_reason=account.deactivation_reason,
        auth=auth_status,
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
