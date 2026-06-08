"""Adapters between usage ORM rows and the `UsageWindowRow` value type.

The mapping itself is trivial, but it is shared by proxy, usage,
dashboard, and account-summary code. Pulling it into a single helper keeps
future ``UsageWindowRow`` changes from drifting across call sites.

Lives in ``app/modules/usage/`` rather than ``app/core/usage/types.py``
so that ``app/core/`` does not need to depend on ``app/db/models``.
"""

from __future__ import annotations

from app.core.usage.types import UsageWindowRow
from app.db.models import AdditionalUsageHistory, UsageHistory


def usage_history_to_window_row(entry: UsageHistory | AdditionalUsageHistory) -> UsageWindowRow:
    """Build a ``UsageWindowRow`` from a usage ORM row.

    All fields map by name. Callers that need a ``UsageWindowRow`` from a
    usage row should route through this helper.
    """
    return UsageWindowRow(
        account_id=entry.account_id,
        used_percent=entry.used_percent,
        reset_at=entry.reset_at,
        window_minutes=entry.window_minutes,
        recorded_at=entry.recorded_at,
    )
