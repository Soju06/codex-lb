from __future__ import annotations

from typing import Iterable

from pydantic import ValidationError

from app.core import usage as usage_core
from app.core.balancer.types import UpstreamError
from app.core.errors import OpenAIErrorDetail, OpenAIErrorEnvelope
from app.core.openai.models import OpenAIError
from app.core.plan_types import normalize_rate_limit_plan_type
from app.core.usage.types import UsageWindowRow, UsageWindowSummary
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.proxy.types import (
    CreditStatusDetailsData,
    RateLimitStatusDetailsData,
    RateLimitWindowSnapshotData,
)

PLAN_TYPE_PRIORITY = (
    "enterprise",
    "business",
    "team",
    "pro",
    "plus",
    "education",
    "edu",
    "free_workspace",
    "free",
    "go",
    "guest",
    "quorum",
    "k12",
)


def _header_account_id(account_id: str | None) -> str | None:
    if not account_id:
        return None
    if account_id.startswith(("email_", "local_")):
        return None
    return account_id


def _select_accounts_for_limits(accounts: Iterable[Account]) -> list[Account]:
    return [
        account
        for account in accounts
        if account.status not in (AccountStatus.DEACTIVATED, AccountStatus.PAUSED) and account.chatgpt_account_id
    ]


def _summarize_window(
    rows: list[UsageWindowRow],
    account_map: dict[str, Account],
    window: str,
) -> UsageWindowSummary | None:
    if not rows:
        return None
    return usage_core.summarize_usage_window(rows, account_map, window)


def _window_snapshot(
    summary: UsageWindowSummary | None,
    rows: list[UsageWindowRow],
    window: str,
    now_epoch: int,
) -> RateLimitWindowSnapshotData | None:
    if summary is None:
        return None

    used_percent = _normalize_used_percent(summary.used_percent, rows)
    if used_percent is None:
        return None

    reset_at = summary.reset_at
    if reset_at is None:
        return None

    window_minutes = summary.window_minutes or usage_core.default_window_minutes(window)
    if not window_minutes:
        return None

    limit_window_seconds = int(window_minutes * 60)
    reset_after_seconds = max(0, int(reset_at) - now_epoch)

    return RateLimitWindowSnapshotData(
        used_percent=_percent_to_int(used_percent),
        limit_window_seconds=limit_window_seconds,
        reset_after_seconds=reset_after_seconds,
        reset_at=int(reset_at),
    )


def _normalize_used_percent(
    value: float | None,
    rows: Iterable[UsageWindowRow],
) -> float | None:
    if value is not None:
        return value
    values = [row.used_percent for row in rows if row.used_percent is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _percent_to_int(value: float) -> int:
    bounded = max(0.0, min(100.0, value))
    return int(bounded)


def _rate_limit_details(
    primary: RateLimitWindowSnapshotData | None,
    secondary: RateLimitWindowSnapshotData | None,
) -> RateLimitStatusDetailsData | None:
    if not primary and not secondary:
        return None
    used_percents = [window.used_percent for window in (primary, secondary) if window]
    limit_reached = any(used >= 100 for used in used_percents)
    return RateLimitStatusDetailsData(
        allowed=not limit_reached,
        limit_reached=limit_reached,
        primary_window=primary,
        secondary_window=secondary,
    )


def _aggregate_credits(entries: Iterable[UsageHistory]) -> tuple[bool, bool, float] | None:
    has_data = False
    has_credits = False
    unlimited = False
    balance_total = 0.0

    for entry in entries:
        credits_has = entry.credits_has
        credits_unlimited = entry.credits_unlimited
        credits_balance = entry.credits_balance
        if credits_has is None and credits_unlimited is None and credits_balance is None:
            continue
        has_data = True
        if credits_has is True:
            has_credits = True
        if credits_unlimited is True:
            unlimited = True
        if credits_balance is not None and not credits_unlimited:
            try:
                balance_total += float(credits_balance)
            except (TypeError, ValueError):
                continue

    if not has_data:
        return None
    if unlimited:
        has_credits = True
    return has_credits, unlimited, balance_total


def _credits_snapshot(entries: Iterable[UsageHistory]) -> CreditStatusDetailsData | None:
    aggregate = _aggregate_credits(entries)
    if aggregate is None:
        return None
    has_credits, unlimited, balance_total = aggregate
    balance_value = str(round(balance_total, 2))
    return CreditStatusDetailsData(
        has_credits=has_credits,
        unlimited=unlimited,
        balance=balance_value,
        approx_local_messages=None,
        approx_cloud_messages=None,
    )


def _plan_type_for_accounts(accounts: Iterable[Account]) -> str:
    normalized = [_normalize_plan_type(account.plan_type) for account in accounts]
    filtered = [plan for plan in normalized if plan is not None]
    if not filtered:
        return "guest"
    unique = set(filtered)
    if len(unique) == 1:
        return filtered[0]
    for plan in PLAN_TYPE_PRIORITY:
        if plan in unique:
            return plan
    return "guest"


def _normalize_plan_type(value: str | None) -> str | None:
    return normalize_rate_limit_plan_type(value)


def _rate_limit_headers(
    window_label: str,
    summary: UsageWindowSummary,
) -> dict[str, str]:
    used_percent = summary.used_percent
    window_minutes = summary.window_minutes
    if used_percent is None or window_minutes is None:
        return {}
    headers = {
        f"x-codex-{window_label}-used-percent": str(float(used_percent)),
        f"x-codex-{window_label}-window-minutes": str(int(window_minutes)),
    }
    reset_at = summary.reset_at
    if reset_at is not None:
        headers[f"x-codex-{window_label}-reset-at"] = str(int(reset_at))
    return headers


def _credits_headers(entries: Iterable[UsageHistory]) -> dict[str, str]:
    aggregate = _aggregate_credits(entries)
    if aggregate is None:
        return {}
    has_credits, unlimited, balance_total = aggregate
    balance_value = f"{balance_total:.2f}"
    return {
        "x-codex-credits-has-credits": "true" if has_credits else "false",
        "x-codex-credits-unlimited": "true" if unlimited else "false",
        "x-codex-credits-balance": balance_value,
    }


def _normalize_error_code(code: str | None, error_type: str | None) -> str:
    value = code or error_type
    if not value:
        return "upstream_error"
    return value.lower()


def _parse_openai_error(payload: OpenAIErrorEnvelope) -> OpenAIError | None:
    error = payload.get("error")
    if not error:
        return None
    try:
        return OpenAIError.model_validate(error)
    except ValidationError:
        if not isinstance(error, dict):
            return None
        return OpenAIError(
            message=_coerce_str(error.get("message")),
            type=_coerce_str(error.get("type")),
            code=_coerce_str(error.get("code")),
            param=_coerce_str(error.get("param")),
            plan_type=_coerce_str(error.get("plan_type")),
            resets_at=_coerce_number(error.get("resets_at")),
            resets_in_seconds=_coerce_number(error.get("resets_in_seconds")),
        )


def _coerce_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _coerce_number(value: object) -> int | float | None:
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _apply_error_metadata(target: OpenAIErrorDetail, error: OpenAIError | None) -> None:
    if not error:
        return
    if error.plan_type is not None:
        target["plan_type"] = error.plan_type
    if error.resets_at is not None:
        target["resets_at"] = error.resets_at
    if error.resets_in_seconds is not None:
        target["resets_in_seconds"] = error.resets_in_seconds


def _upstream_error_from_openai(error: OpenAIError | None) -> UpstreamError:
    if not error:
        return {}
    data = error.model_dump(exclude_none=True)
    payload: UpstreamError = {}
    message = data.get("message")
    if isinstance(message, str):
        payload["message"] = message
    resets_at = data.get("resets_at")
    if isinstance(resets_at, (int, float)):
        payload["resets_at"] = resets_at
    resets_in_seconds = data.get("resets_in_seconds")
    if isinstance(resets_in_seconds, (int, float)):
        payload["resets_in_seconds"] = resets_in_seconds
    return payload
