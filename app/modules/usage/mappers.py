"""Adapters between usage ORM rows and the `UsageWindowRow` value type.

The mapping itself is trivial, but it is shared by proxy, usage,
dashboard, and account-summary code. Pulling it into a single helper keeps
future ``UsageWindowRow`` changes from drifting across call sites.

Lives in ``app/modules/usage/`` rather than ``app/core/usage/types.py``
so that ``app/core/`` does not need to depend on ``app/db/models``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from app.core import usage as usage_core
from app.core.usage.account_limits import AccountUsageLimitState, evaluate_standard_usage_limit
from app.core.usage.types import UsageWindowRow
from app.db.models import Account, AdditionalUsageHistory, UsageHistory


def usage_history_to_window_row(entry: UsageHistory | AdditionalUsageHistory) -> UsageWindowRow:
    """Build a ``UsageWindowRow`` from a usage ORM row.

    All fields map by name. Callers that need a ``UsageWindowRow`` from a
    usage row should route through this helper.
    """
    row = UsageWindowRow(
        account_id=entry.account_id,
        used_percent=entry.used_percent,
        reset_at=entry.reset_at,
        window_minutes=entry.window_minutes,
        recorded_at=entry.recorded_at,
    )
    if isinstance(entry, UsageHistory) and float(entry.used_percent) == 0.0 and usage_core.is_no_data_placeholder(row):
        return replace(row, used_percent=None)
    return row


def evaluate_account_usage_limit(
    account: Account,
    *,
    primary: UsageHistory | None,
    secondary: UsageHistory | None,
    monthly: UsageHistory | None,
    refresh_interval_seconds: int,
    now: datetime | None = None,
) -> AccountUsageLimitState:
    """Evaluate one account policy from standard usage ORM rows."""
    return evaluate_standard_usage_limit(
        enabled=bool(account.usage_limit_enabled),
        limit_percent=account.usage_limit_percent,
        plan_type=account.plan_type,
        primary=usage_history_to_window_row(primary) if primary is not None else None,
        secondary=usage_history_to_window_row(secondary) if secondary is not None else None,
        monthly=usage_history_to_window_row(monthly) if monthly is not None else None,
        refresh_interval_seconds=refresh_interval_seconds,
        now=now,
    )
