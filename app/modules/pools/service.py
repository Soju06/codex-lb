from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.core.providers import ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME, normalize_provider_name
from app.core.utils.time import utcnow
from app.db.models import AccountStatus
from app.modules.accounts.schemas import AccountSummary
from app.modules.accounts.service import AccountsService
from app.modules.accounts.subscription_status import CANCELED_SUBSCRIPTION_STATUS, normalize_subscription_status
from app.modules.pools.schemas import (
    POOL_SOURCE_SCOPED_MARKER,
    POOL_SOURCE_WEEKLY_HEURISTIC,
    POOL_STATUS_EXHAUSTED,
    POOL_STATUS_LOW,
    POOL_STATUS_OK,
    PoolsResponse,
    PoolSummary,
)

GLM_POOL_PROVIDER = "glm"
KIMI_POOL_PROVIDER = "kimi"

# A pool is `ok` while the best account still has a quarter of its window, and
# `low` once it is inside the last quarter but above the reserve the router
# needs to finish work in flight.
_OK_HEADROOM_PERCENT = 25.0
_LOW_HEADROOM_PERCENT = 5.0

# Rows that cannot answer a request now and will not without operator action.
# Rate-limited and quota-exceeded accounts stay in: their remaining percent is
# already ~0, which is exactly the signal the pool should carry, and they
# recover on their own at the window reset.
_UNROUTABLE_STATUSES = frozenset(
    {
        AccountStatus.PAUSED.value,
        AccountStatus.REAUTH_REQUIRED.value,
        AccountStatus.DEACTIVATED.value,
    }
)


@dataclass(frozen=True, slots=True)
class _Candidate:
    remaining_percent: float
    reset_at: datetime | None


def is_pool_usable(summary: AccountSummary) -> bool:
    """Whether this account can serve pool traffic at all.

    Mirrors the routing-side gates: a canceled subscription or an operator/auth
    hold takes the account out of the pool arithmetic entirely rather than
    dragging the aggregate down with a number nothing can spend.
    """
    if normalize_subscription_status(summary.subscription.status if summary.subscription else None) == (
        CANCELED_SUBSCRIPTION_STATUS
    ):
        return False
    return summary.status not in _UNROUTABLE_STATUSES


def _secondary_remaining(summary: AccountSummary) -> float:
    # No weekly sample yet means a fresh window, not an empty one — the same
    # reading the Fable eligibility heuristic takes.
    remaining = summary.usage.secondary_remaining_percent if summary.usage else None
    return 100.0 if remaining is None else float(remaining)


def _primary_remaining(summary: AccountSummary) -> float:
    # Unmetered providers report no window at all; treat them as full.
    remaining = summary.usage.primary_remaining_percent if summary.usage else None
    return 100.0 if remaining is None else float(remaining)


def _by_provider(summaries: list[AccountSummary], provider: str) -> list[AccountSummary]:
    return [summary for summary in summaries if normalize_provider_name(summary.provider) == provider]


def _status_for(headroom_percent: float | None) -> str:
    if headroom_percent is None:
        return POOL_STATUS_EXHAUSTED
    if headroom_percent >= _OK_HEADROOM_PERCENT:
        return POOL_STATUS_OK
    if headroom_percent >= _LOW_HEADROOM_PERCENT:
        return POOL_STATUS_LOW
    return POOL_STATUS_EXHAUSTED


def _pool(
    *,
    pool_id: str,
    provider: str,
    kind: str,
    accounts: int,
    candidates: list[_Candidate],
    eligible_accounts: int | None = None,
    source: str | None = None,
) -> PoolSummary:
    best = max(candidates, key=lambda candidate: candidate.remaining_percent, default=None)
    headroom_percent = best.remaining_percent if best is not None else None
    aggregate = sum(candidate.remaining_percent for candidate in candidates) / len(candidates) if candidates else None
    return PoolSummary(
        id=pool_id,
        provider=provider,
        kind=kind,
        accounts=accounts,
        eligible_accounts=len(candidates) if eligible_accounts is None else eligible_accounts,
        headroom_percent=headroom_percent,
        aggregate_remaining_percent=aggregate,
        reset_at=best.reset_at if best is not None else None,
        status=_status_for(headroom_percent),
        source=source,
    )


def _fable_pool(anthropic: list[AccountSummary]) -> PoolSummary:
    """The Fable-scoped pool, read the way the balancer reads eligibility.

    Per account: Anthropic's own Fable-scoped marker when it is fresh, else the
    overall-weekly window. `source` reports which signal carried the pool — a
    single fresh marker anywhere is enough to make the pool authoritative.
    """
    candidates: list[_Candidate] = []
    saw_fresh_marker = False
    for summary in anthropic:
        if not is_pool_usable(summary):
            continue
        marker = summary.fable_scoped_weekly
        if marker is not None and marker.fresh:
            saw_fresh_marker = True
            candidates.append(
                _Candidate(
                    remaining_percent=max(0.0, 100.0 - float(marker.used_percent)),
                    reset_at=marker.reset_at or summary.reset_at_secondary,
                )
            )
            continue
        candidates.append(
            _Candidate(remaining_percent=_secondary_remaining(summary), reset_at=summary.reset_at_secondary)
        )
    eligible_accounts = sum(1 for summary in anthropic if is_pool_usable(summary) and summary.fable_eligible is True)
    return _pool(
        pool_id="anthropic-fable",
        provider=ANTHROPIC_PROVIDER_NAME,
        kind="fable_scoped",
        accounts=len(anthropic),
        candidates=candidates,
        eligible_accounts=eligible_accounts,
        source=POOL_SOURCE_SCOPED_MARKER if saw_fresh_marker else POOL_SOURCE_WEEKLY_HEURISTIC,
    )


def _weekly_pool(
    summaries: list[AccountSummary],
    *,
    pool_id: str,
    provider: str,
) -> PoolSummary:
    candidates = [
        _Candidate(remaining_percent=_secondary_remaining(summary), reset_at=summary.reset_at_secondary)
        for summary in summaries
        if is_pool_usable(summary)
    ]
    return _pool(
        pool_id=pool_id,
        provider=provider,
        kind="weekly",
        accounts=len(summaries),
        candidates=candidates,
    )


def _primary_window_pool(
    summaries: list[AccountSummary],
    *,
    pool_id: str,
    provider: str,
) -> PoolSummary:
    candidates = [
        _Candidate(remaining_percent=_primary_remaining(summary), reset_at=summary.reset_at_primary)
        for summary in summaries
        if is_pool_usable(summary)
    ]
    return _pool(
        pool_id=pool_id,
        provider=provider,
        kind="weekly",
        accounts=len(summaries),
        candidates=candidates,
    )


def build_pools(summaries: list[AccountSummary], *, generated_at: datetime | None = None) -> PoolsResponse:
    """Aggregate account summaries into the routable pools of contract C1.

    Pure over the summaries the accounts service already builds, so the router
    and the dashboard read the same windows the balancer routes on.
    """
    anthropic = _by_provider(summaries, ANTHROPIC_PROVIDER_NAME)
    return PoolsResponse(
        generated_at=generated_at or utcnow(),
        pools=[
            _fable_pool(anthropic),
            _weekly_pool(anthropic, pool_id="anthropic-general", provider=ANTHROPIC_PROVIDER_NAME),
            _weekly_pool(
                _by_provider(summaries, OPENAI_PROVIDER_NAME),
                pool_id="openai-codex",
                provider=OPENAI_PROVIDER_NAME,
            ),
            _primary_window_pool(
                _by_provider(summaries, KIMI_POOL_PROVIDER),
                pool_id="kimi",
                provider=KIMI_POOL_PROVIDER,
            ),
            _primary_window_pool(
                _by_provider(summaries, GLM_POOL_PROVIDER),
                pool_id="glm",
                provider=GLM_POOL_PROVIDER,
            ),
        ],
    )


class PoolsService:
    def __init__(self, accounts_service: AccountsService) -> None:
        self._accounts_service = accounts_service

    async def get_pools(self) -> PoolsResponse:
        # include_request_usage stays off: the pools view needs windows, not the
        # expensive request_logs dedup aggregation behind the token columns.
        summaries = await self._accounts_service.list_accounts()
        return build_pools(summaries)
