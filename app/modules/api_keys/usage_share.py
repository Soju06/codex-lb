from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isclose, isfinite

import app.core.usage as usage_core
from app.core.usage.refresh_policy import usage_freshness_horizon_seconds
from app.core.usage.types import UsageWindowRow
from app.core.utils.time import naive_utc_to_epoch, to_utc_naive
from app.db.models import Account, AccountStatus

API_KEY_USAGE_SHARE_LIMIT_REACHED = "api_key_usage_share_limit_reached"
_UNROUTABLE_STATUSES = frozenset({AccountStatus.PAUSED, AccountStatus.DEACTIVATED})
_EPOCH = datetime(1970, 1, 1)


@dataclass(frozen=True, slots=True)
class UsageShareAccount:
    account_id: str
    capacity_credits: float
    used_percent: float
    window_started_at: datetime
    observed_at: datetime
    reset_at: int
    evidence_expires_at: int | None = None


@dataclass(frozen=True, slots=True)
class UsageShareDemandWindow:
    started_at: datetime
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class UsageShareDemand:
    total_units: float
    key_units: float


@dataclass(frozen=True, slots=True)
class UsageShareEvidence:
    accounts: tuple[UsageShareAccount, ...]
    unavailable_account_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UsageShareEstimate:
    configured_percent: int
    estimated_used_credits: float
    allowance_credits: float
    capacity_credits: float
    reset_at: int | None
    account_count: int
    evidence_expires_at: int | None = None
    routing_expires_at: int | None = None

    @property
    def snapshot_expires_at(self) -> int | None:
        boundaries = tuple(
            value for value in (self.reset_at, self.evidence_expires_at, self.routing_expires_at) if value is not None
        )
        return min(boundaries, default=None)

    @property
    def exceeded(self) -> bool:
        return self.allowance_credits > 0 and (
            self.estimated_used_credits > self.allowance_credits
            or isclose(self.estimated_used_credits, self.allowance_credits, rel_tol=1e-12, abs_tol=1e-9)
        )


def build_usage_share_evidence(
    accounts: Iterable[Account],
    *,
    primary_rows: Iterable[UsageWindowRow],
    secondary_rows: Iterable[UsageWindowRow],
    monthly_rows: Iterable[UsageWindowRow],
    now: datetime,
) -> UsageShareEvidence:
    """Resolve complete current long-window evidence for a key's account pool."""

    current = to_utc_naive(now)
    current_epoch = naive_utc_to_epoch(current)
    freshness_horizon_seconds = usage_freshness_horizon_seconds()
    freshness_cutoff = current - timedelta(seconds=freshness_horizon_seconds)
    primary_rows = tuple(primary_rows)
    secondary_rows = tuple(secondary_rows)
    original_secondary_by_account = {row.account_id: row for row in secondary_rows}
    weekly_primary_account_ids = {
        row.account_id
        for row in primary_rows
        if usage_core.should_use_weekly_primary(row, original_secondary_by_account.get(row.account_id))
    }
    _, normalized_secondary = usage_core.normalize_weekly_only_rows(primary_rows, secondary_rows)
    secondary_by_account = {row.account_id: row for row in normalized_secondary}
    monthly_by_account = {row.account_id: row for row in monthly_rows}

    resolved: list[UsageShareAccount] = []
    unavailable: list[str] = []
    for account in accounts:
        if account.status in _UNROUTABLE_STATUSES or account.delete_requested_at is not None:
            continue

        monthly_capacity = usage_core.capacity_for_plan(account.plan_type, "monthly")
        # A monthly-capacity plan normally uses monthly evidence. A newer
        # explicit weekly-primary row is a different current long-window shape,
        # so it may supersede older monthly residue; an ordinary secondary row
        # never substitutes for monthly evidence.
        candidates = (
            [
                (
                    monthly_by_account.get(account.id),
                    monthly_capacity,
                    usage_core.DEFAULT_WINDOW_MINUTES_MONTHLY,
                )
            ]
            if monthly_capacity is not None
            else []
        )
        if monthly_capacity is None or account.id in weekly_primary_account_ids:
            candidates.append(
                (
                    secondary_by_account.get(account.id),
                    usage_core.capacity_for_plan(account.plan_type, "secondary"),
                    usage_core.DEFAULT_WINDOW_MINUTES_SECONDARY,
                )
            )
        row, capacity, expected_window_minutes = max(
            candidates,
            key=lambda candidate: (
                to_utc_naive(candidate[0].recorded_at)
                if candidate[0] is not None and candidate[0].recorded_at is not None
                else _EPOCH
            ),
        )
        evidence = _usage_share_account(
            account.id,
            row,
            capacity,
            expected_window_minutes=expected_window_minutes,
            now=current,
            now_epoch=current_epoch,
            freshness_cutoff=freshness_cutoff,
            freshness_horizon_seconds=freshness_horizon_seconds,
        )
        if evidence is None:
            unavailable.append(account.id)
        else:
            resolved.append(evidence)

    return UsageShareEvidence(
        accounts=tuple(resolved),
        unavailable_account_ids=tuple(sorted(unavailable)),
    )


def _usage_share_account(
    account_id: str,
    row: UsageWindowRow | None,
    capacity: float | None,
    *,
    expected_window_minutes: int,
    now: datetime,
    now_epoch: int,
    freshness_cutoff: datetime,
    freshness_horizon_seconds: int,
) -> UsageShareAccount | None:
    if (
        row is None
        or row.used_percent is None
        or row.recorded_at is None
        or row.reset_at is None
        or row.reset_at <= now_epoch
        or row.window_minutes != expected_window_minutes
        or capacity is None
        or capacity <= 0
        or not isfinite(float(capacity))
        or not isfinite(float(row.used_percent))
    ):
        return None
    recorded_at = to_utc_naive(row.recorded_at)
    if recorded_at <= freshness_cutoff or recorded_at > now:
        return None
    try:
        window_started_at = _EPOCH + timedelta(seconds=row.reset_at - row.window_minutes * 60)
    except OverflowError:
        return None
    if window_started_at > recorded_at or window_started_at >= now:
        return None
    return UsageShareAccount(
        account_id=account_id,
        capacity_credits=float(capacity),
        used_percent=float(row.used_percent),
        window_started_at=window_started_at,
        observed_at=recorded_at,
        reset_at=row.reset_at,
        evidence_expires_at=naive_utc_to_epoch(recorded_at + timedelta(seconds=freshness_horizon_seconds)),
    )


def estimate_usage_share(
    configured_percent: int,
    accounts: Iterable[UsageShareAccount],
    demand_by_account: Mapping[str, UsageShareDemand],
    *,
    routing_expires_at: int | None = None,
) -> UsageShareEstimate:
    account_rows = tuple(accounts)
    capacity_credits = sum(account.capacity_credits for account in account_rows)
    estimated_used_credits = 0.0
    for account in account_rows:
        demand = demand_by_account.get(account.account_id)
        if (
            demand is None
            or not isfinite(demand.total_units)
            or not isfinite(demand.key_units)
            or demand.total_units <= 0
        ):
            continue
        share = min(1.0, max(0.0, demand.key_units) / demand.total_units)
        used_percent = min(100.0, max(0.0, account.used_percent))
        estimated_used_credits += account.capacity_credits * used_percent / 100.0 * share
    return UsageShareEstimate(
        configured_percent=configured_percent,
        estimated_used_credits=estimated_used_credits,
        allowance_credits=capacity_credits * configured_percent / 100.0,
        capacity_credits=capacity_credits,
        reset_at=min((account.reset_at for account in account_rows), default=None),
        account_count=len(account_rows),
        evidence_expires_at=min(
            (account.evidence_expires_at for account in account_rows if account.evidence_expires_at is not None),
            default=None,
        ),
        routing_expires_at=routing_expires_at,
    )


def usage_share_limit_message(estimate: UsageShareEstimate) -> str:
    return (
        f"Estimated API key subscription-backed generation usage "
        f"({estimate.estimated_used_credits:.2f} credits) "
        f"has reached its {estimate.configured_percent}% allocation "
        f"({estimate.allowance_credits:.2f} of {estimate.capacity_credits:.2f} credits)."
    )
