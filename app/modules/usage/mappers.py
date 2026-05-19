"""Adapters between usage ORM rows and the `UsageWindowRow` value type.

The mapping itself is trivial, but it lives in three places today
(``app/modules/proxy/api.py``, ``app/modules/proxy/load_balancer.py``,
``app/modules/usage/service.py``). Pulling it into a single helper keeps
future ``UsageWindowRow`` changes from drifting across call sites.

Lives in ``app/modules/usage/`` rather than ``app/core/usage/types.py``
so that ``app/core/`` does not need to depend on ``app/db/models``.
"""

from __future__ import annotations

from app.core.usage.types import UsageWindowRow
from app.db.models import UsageHistory


def usage_history_to_window_row(entry: UsageHistory) -> UsageWindowRow:
    """Build a ``UsageWindowRow`` from a ``UsageHistory`` ORM row.

    All fields map by name. Callers that already had local
    ``_usage_entry_to_window_row`` helpers should route through this.
    """
    return UsageWindowRow(
        account_id=entry.account_id,
        used_percent=entry.used_percent,
        reset_at=entry.reset_at,
        window_minutes=entry.window_minutes,
        recorded_at=entry.recorded_at,
    )
