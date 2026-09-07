"""The projections history fetch must request the EWMA-tail row cap and the
equal-weight floor.

The cap is what keeps the PostgreSQL bulk read bounded on deployments where
live snapshot ingestion densifies ``usage_history``; losing the kwarg would
silently regress the read back to full-window row counts. The floor is what
keeps the equal-weight consumers (weekly-pace smoothing mean, 3h fleet burn)
exact regardless of write density.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast

import pytest

from app.db.models import UsageHistory
from app.modules.dashboard.repository import DashboardRepository
from app.modules.dashboard.service import (
    _PROJECTION_EWMA_TAIL_ROWS,
    _load_projection_histories,
)
from app.modules.dashboard.weekly_pace import FLEET_BURN_WINDOW

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


def test_projection_tail_cap_is_a_fixed_ewma_tail() -> None:
    # The cap is sized for the count-decaying EWMA consumers (alpha 0.4). The
    # first retained row only seeds the EWMA, so a cap-row tail spanning
    # cap-many distinct recorded seconds performs cap-1 updates and the
    # pre-tail state's weight on the replayed rate is ``0.6**(cap-1)``: below
    # ~1.1e-12 %/s even at the theoretical 100 %/s per-second step, so the
    # tail replays to fp noise of the full replay. Equal-weight consumers are
    # covered by the floor, not the cap.
    assert _PROJECTION_EWMA_TAIL_ROWS == 64
    residual_weight = 0.6 ** (_PROJECTION_EWMA_TAIL_ROWS - 1)
    assert residual_weight < 1.1e-14
    assert residual_weight * 100.0 < 1.1e-12
    # Guard against loosening: the cap-1 bound is the tight one (0.6**cap is
    # ~0.6x smaller and would not describe the seed-row arithmetic).
    assert residual_weight > 0.6**_PROJECTION_EWMA_TAIL_ROWS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("smoothing_window_minutes", "expected_floor_minutes"),
    [
        # Smoothing window wider than the fleet-burn window: it sets the floor.
        (240, 240),
        # Default smoothing window (30 min): the 3h fleet-burn window wins.
        (30, int(FLEET_BURN_WINDOW.total_seconds() // 60)),
    ],
)
async def test_projection_history_fetch_passes_tail_cap_and_equal_weight_floor(
    smoothing_window_minutes: int,
    expected_floor_minutes: int,
) -> None:
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
        smoothing_window_minutes=smoothing_window_minutes,
    )

    assert len(repo.calls) == 2
    assert {call["window"] for call in repo.calls} == {"primary", "secondary"}
    for call in repo.calls:
        assert call["per_account_row_cap"] == _PROJECTION_EWMA_TAIL_ROWS
        assert call["cutoffs"] is not None
        # The weekly-pace smoothing mean and the 3h fleet burn weigh every
        # in-window sample equally, so the fetch must exempt the wider of the
        # two windows from the row cap on BOTH fetches; a write burst may
        # otherwise out-write the cap and shift those values.
        assert call["uncapped_recent_floor"] == now - timedelta(minutes=expected_floor_minutes)


@pytest.mark.asyncio
@pytest.mark.parametrize("include_primary", [True, False])
async def test_weekly_only_primary_source_account_keeps_floor_on_primary_fetch(include_primary: bool) -> None:
    """Weekly-only accounts whose history source is the primary stream feed
    ``secondary_history`` (weekly pace) from the PRIMARY bulk fetch, so that
    fetch must carry the equal-weight floor too — it is not EWMA-only."""
    now = datetime(2026, 8, 16, 12, 0, 0)
    repo = _RecordingRepo()
    # A weekly-only account: the primary stream carries the 7-day window and
    # there is no secondary row at all.
    primary_usage = {
        "weekly": _usage_entry("weekly", "primary", 10080, now - timedelta(minutes=1)),
    }
    secondary_usage: dict[str, UsageHistory] = {}

    await _load_projection_histories(
        cast(DashboardRepository, repo),
        primary_usage,
        secondary_usage,
        now,
        smoothing_window_minutes=30,
        include_primary=include_primary,
    )

    assert [call["window"] for call in repo.calls] == ["primary"]
    (call,) = repo.calls
    assert call["account_ids"] == ["weekly"]
    assert call["cutoffs"] == {"weekly": now - timedelta(minutes=10080)}
    assert call["per_account_row_cap"] == _PROJECTION_EWMA_TAIL_ROWS
    assert call["uncapped_recent_floor"] == now - FLEET_BURN_WINDOW
