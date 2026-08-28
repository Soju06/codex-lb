"""The projections history fetch must request the per-account row cap.

The cap is what keeps the PostgreSQL bulk read bounded on deployments where
live snapshot ingestion densifies ``usage_history``; losing the kwarg would
silently regress the read back to full-window row counts.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast

import pytest

from app.db.models import UsageHistory
from app.modules.dashboard.repository import DashboardRepository
from app.modules.dashboard.service import (
    _PROJECTION_HISTORY_PER_ACCOUNT_ROW_CAP,
    _load_projection_histories,
)

pytestmark = pytest.mark.unit


class _RecordingRepo:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def bulk_usage_history_since(
        self,
        account_ids,
        window,
        since,
        *,
        cutoffs=None,
        per_account_row_cap=None,
        uncapped_recent_floor=None,
    ):
        self.calls.append(
            {
                "account_ids": list(account_ids),
                "window": window,
                "since": since,
                "cutoffs": cutoffs,
                "per_account_row_cap": per_account_row_cap,
                "uncapped_recent_floor": uncapped_recent_floor,
            }
        )
        return {}


def _usage_entry(account_id: str, window: str, window_minutes: int, recorded_at: datetime) -> UsageHistory:
    return UsageHistory(
        id=1,
        account_id=account_id,
        used_percent=10.0,
        window=window,
        window_minutes=window_minutes,
        recorded_at=recorded_at,
    )


@pytest.mark.asyncio
async def test_projection_history_fetch_passes_per_account_row_cap():
    now = datetime(2026, 8, 16, 12, 0, 0)
    repo = _RecordingRepo()
    primary_usage = {
        "acc1": _usage_entry("acc1", "primary", 300, now - timedelta(minutes=1)),
    }
    secondary_usage = {
        "acc1": _usage_entry("acc1", "secondary", 10080, now - timedelta(minutes=1)),
    }

    await _load_projection_histories(
        cast(DashboardRepository, repo),
        primary_usage,
        secondary_usage,
        now,
        smoothing_window_minutes=240,
    )

    assert len(repo.calls) == 2
    assert {call["window"] for call in repo.calls} == {"primary", "secondary"}
    for call in repo.calls:
        assert call["per_account_row_cap"] == _PROJECTION_HISTORY_PER_ACCOUNT_ROW_CAP
        assert call["cutoffs"] is not None
        # The weekly-pace smoothing mean weighs every in-window sample
        # equally, so the fetch must exempt the configured smoothing window
        # from the row cap; a write burst may otherwise out-write the cap and
        # shift the smoothed schedule gap.
        assert call["uncapped_recent_floor"] == now - timedelta(minutes=240)
