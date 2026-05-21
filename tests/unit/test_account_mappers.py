from __future__ import annotations

from datetime import datetime

from app.db.models import UsageHistory
from app.modules.accounts import mappers


def _usage(
    *,
    recorded_at: datetime,
    credits_has: bool,
    credits_balance: float,
) -> UsageHistory:
    return UsageHistory(
        account_id="acc",
        recorded_at=recorded_at,
        window="secondary",
        used_percent=100.0,
        credits_has=credits_has,
        credits_unlimited=False,
        credits_balance=credits_balance,
    )


def test_extract_credit_status_uses_freshest_sample() -> None:
    stale = _usage(
        recorded_at=datetime(2026, 1, 1, 12, 0, 0),
        credits_has=True,
        credits_balance=10.0,
    )
    fresh = _usage(
        recorded_at=datetime(2026, 1, 1, 12, 1, 0),
        credits_has=False,
        credits_balance=0.0,
    )

    assert mappers._extract_credit_status(stale, fresh) == (False, False, 0.0)
