from __future__ import annotations

import time

from app.core import usage as usage_core
from app.db.models import AccountStatus


def apply_usage_quota(
    *,
    status: AccountStatus,
    primary_used: float | None,
    primary_reset: int | None,
    primary_window_minutes: int | None,
    runtime_reset: float | None,
    secondary_used: float | None,
    secondary_reset: int | None,
    credits_has: bool | None = None,
    credits_unlimited: bool | None = None,
    credits_balance: float | None = None,
    infer_status_from_usage: bool = True,
) -> tuple[AccountStatus, float | None, float | None]:
    used_percent = primary_used
    reset_at = runtime_reset

    if status in (AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED, AccountStatus.PAUSED):
        return status, used_percent, reset_at

    has_credit_override = _has_credit_override(
        credits_has=credits_has,
        credits_unlimited=credits_unlimited,
        credits_balance=credits_balance,
    )
    if secondary_used is not None:
        if secondary_used >= 100.0:
            if has_credit_override:
                if status == AccountStatus.QUOTA_EXCEEDED:
                    status = AccountStatus.ACTIVE
                    reset_at = None
            else:
                used_percent = 100.0
                if infer_status_from_usage:
                    if secondary_reset is not None:
                        reset_at = secondary_reset
                    status = AccountStatus.QUOTA_EXCEEDED
                    return status, used_percent, reset_at
        if status == AccountStatus.QUOTA_EXCEEDED:
            if runtime_reset and runtime_reset > time.time():
                reset_at = runtime_reset
            else:
                status = AccountStatus.ACTIVE
                reset_at = None
    elif status == AccountStatus.QUOTA_EXCEEDED and secondary_reset is not None and infer_status_from_usage:
        reset_at = secondary_reset

    if has_credit_override and status == AccountStatus.QUOTA_EXCEEDED:
        primary_exhausted = primary_used is not None and primary_used >= 100.0
        if not primary_exhausted:
            status = AccountStatus.ACTIVE
            reset_at = None

    if primary_used is not None:
        if primary_used >= 100.0:
            used_percent = 100.0
            if infer_status_from_usage:
                if primary_reset is not None:
                    reset_at = primary_reset
                else:
                    reset_at = _fallback_primary_reset(primary_window_minutes) or reset_at
                status = AccountStatus.RATE_LIMITED
                return status, used_percent, reset_at
        if status == AccountStatus.RATE_LIMITED:
            if runtime_reset and runtime_reset > time.time():
                reset_at = runtime_reset
            else:
                status = AccountStatus.ACTIVE
                reset_at = None
    elif status == AccountStatus.RATE_LIMITED and secondary_used is not None and secondary_used < 100.0:
        # No primary data at all — upstream stopped reporting the short
        # window. The long-window sample proves availability, so the block
        # clears once its runtime reset elapses instead of pinning the
        # account rate-limited indefinitely. Callers that cannot tie the
        # sample to a reset deadline or post-block evidence must preserve
        # the block themselves.
        if runtime_reset and runtime_reset > time.time():
            reset_at = runtime_reset
        else:
            status = AccountStatus.ACTIVE
            reset_at = None

    return status, used_percent, reset_at


def _fallback_primary_reset(primary_window_minutes: int | None) -> float | None:
    window_minutes = primary_window_minutes or usage_core.default_window_minutes("primary")
    if not window_minutes:
        return None
    return time.time() + float(window_minutes) * 60.0


def _has_credit_override(
    *,
    credits_has: bool | None,
    credits_unlimited: bool | None,
    credits_balance: float | None,
) -> bool:
    return _has_usable_credits(
        credits_has=credits_has,
        credits_unlimited=credits_unlimited,
        credits_balance=credits_balance,
    )


def _has_usable_credits(
    *,
    credits_has: bool | None,
    credits_unlimited: bool | None,
    credits_balance: float | None,
) -> bool:
    if credits_unlimited is True:
        return True
    if credits_has is True:
        return True
    if credits_balance is None:
        return False
    try:
        return float(credits_balance) > 0.0
    except (TypeError, ValueError):
        return False
