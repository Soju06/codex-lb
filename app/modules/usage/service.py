from __future__ import annotations

import asyncio
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import AsyncContextManager, ClassVar, cast

from app.core import usage as usage_core
from app.core.usage.logs import (
    RequestLogLike,
    cached_input_tokens_from_log,
    cost_from_log,
    total_tokens_from_log,
    usage_tokens_from_log,
)
from app.core.usage.pricing import CostItem, calculate_costs
from app.core.usage.types import (
    UsageCostSummary,
    UsageMetricsSummary,
    UsageSummaryPayload,
    UsageWindowRow,
    UsageWindowSnapshot,
)
from app.core.utils.time import from_epoch_seconds, utcnow
from app.db.models import Account, RequestLog
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository
from app.modules.usage.schemas import (
    UsageCost,
    UsageCostByModel,
    UsageHistoryItem,
    UsageHistoryResponse,
    UsageMetrics,
    UsageSummaryResponse,
    UsageWindow,
    UsageWindowResponse,
)
from app.modules.usage.updater import UsageUpdater


@dataclass(slots=True)
class _RefreshState:
    lock: asyncio.Lock
    task: asyncio.Task[None] | None = None


class UsageService:
    _refresh_states: ClassVar[weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _RefreshState]] = (
        weakref.WeakKeyDictionary()
    )

    def __init__(
        self,
        usage_repo: UsageRepository,
        logs_repo: RequestLogsRepository,
        accounts_repo: AccountsRepository,
        refresh_repo_factory: Callable[[], AsyncContextManager[tuple[UsageRepository, AccountsRepository]]]
        | None = None,
    ) -> None:
        self._usage_repo = usage_repo
        self._logs_repo = logs_repo
        self._accounts_repo = accounts_repo
        self._usage_updater = UsageUpdater(usage_repo, accounts_repo)
        self._refresh_repo_factory = refresh_repo_factory

    async def get_usage_summary(self) -> UsageSummaryResponse:
        await self._refresh_usage()
        now = utcnow()
        accounts = await self._accounts_repo.list_accounts()
        account_map = {account.id: account for account in accounts}

        primary_rows = await self._latest_usage_rows("primary")
        secondary_rows = await self._latest_usage_rows("secondary")

        primary_window = usage_core.summarize_usage_window(primary_rows, account_map, "primary")
        secondary_window = usage_core.summarize_usage_window(secondary_rows, account_map, "secondary")

        secondary_minutes = await self._usage_repo.latest_window_minutes("secondary")
        if secondary_minutes is None:
            secondary_minutes = usage_core.default_window_minutes("secondary")
        logs_secondary: list[RequestLog]
        if secondary_minutes:
            logs_secondary = await self._logs_repo.list_since(now - timedelta(minutes=secondary_minutes))
        else:
            logs_secondary = []
        cost_items = [item for item in (_log_to_cost_item(log) for log in logs_secondary) if item]
        cost = calculate_costs(cost_items)

        metrics = _usage_metrics(logs_secondary)
        payload = usage_core.parse_usage_summary(primary_window, secondary_window, cost, metrics)
        return _summary_payload_to_response(payload)

    async def get_usage_history(self, hours: int) -> UsageHistoryResponse:
        await self._refresh_usage()
        now = utcnow()
        since = now - timedelta(hours=hours)
        accounts = await self._accounts_repo.list_accounts()
        account_map = {account.id: account for account in accounts}
        usage_rows = [row.to_window_row() for row in await self._usage_repo.aggregate_since(since, window="primary")]
        logs = await self._logs_repo.list_since(since)

        accounts_history = _build_account_history(usage_rows, logs, account_map, "primary")
        return UsageHistoryResponse(window_hours=hours, accounts=accounts_history)

    async def get_usage_window(self, window: str) -> UsageWindowResponse:
        await self._refresh_usage()
        now = utcnow()
        window_key = (window or "").lower()
        if window_key not in {"primary", "secondary"}:
            raise ValueError("window must be 'primary' or 'secondary'")
        accounts = await self._accounts_repo.list_accounts()
        account_map = {account.id: account for account in accounts}
        usage_rows = await self._latest_usage_rows(window_key)
        window_minutes = await self._usage_repo.latest_window_minutes(window_key)
        if window_minutes is None:
            window_minutes = usage_core.default_window_minutes(window_key)
        logs: list[RequestLog]
        if window_minutes:
            logs = await self._logs_repo.list_since(now - timedelta(minutes=window_minutes))
        else:
            logs = []

        accounts_history = _build_account_history(usage_rows, logs, account_map, window_key)
        return UsageWindowResponse(
            window_key=window_key,
            window_minutes=window_minutes,
            accounts=accounts_history,
        )

    async def _refresh_usage(self) -> None:
        state = self._refresh_state()
        task = state.task
        if task and not task.done():
            await asyncio.shield(task)
            return

        created = False
        async with state.lock:
            task = state.task
            if not task or task.done():
                task = asyncio.create_task(self._refresh_usage_once())
                state.task = task
                created = True

        if task is None:
            return

        try:
            if created:
                await asyncio.shield(task)
            else:
                await asyncio.shield(task)
        finally:
            if task.done() and state.task is task:
                state.task = None

    async def _refresh_usage_once(self) -> None:
        if self._refresh_repo_factory is None:
            accounts = await self._accounts_repo.list_accounts()
            latest_usage = await self._usage_repo.latest_by_account(window="primary")
            await self._usage_updater.refresh_accounts(accounts, latest_usage)
            return

        async with self._refresh_repo_factory() as (usage_repo, accounts_repo):
            latest_usage = await usage_repo.latest_by_account(window="primary")
            accounts = await accounts_repo.list_accounts()
            updater = UsageUpdater(usage_repo, accounts_repo)
            await updater.refresh_accounts(accounts, latest_usage)

    @classmethod
    def _refresh_state(cls) -> _RefreshState:
        loop = asyncio.get_running_loop()
        state = cls._refresh_states.get(loop)
        if state is None:
            state = _RefreshState(lock=asyncio.Lock())
            cls._refresh_states[loop] = state
        return state

    async def _latest_usage_rows(self, window: str) -> list[UsageWindowRow]:
        latest = await self._usage_repo.latest_by_account(window=window)
        return [
            UsageWindowRow(
                account_id=entry.account_id,
                used_percent=entry.used_percent,
                reset_at=entry.reset_at,
                window_minutes=entry.window_minutes,
            )
            for entry in latest.values()
        ]


def _build_account_history(
    usage_rows: list[UsageWindowRow],
    logs: list[RequestLog],
    account_map: dict[str, Account],
    window: str,
) -> list[UsageHistoryItem]:
    usage_by_account = {row.account_id: row for row in usage_rows}
    counts: dict[str, int] = {}
    costs: dict[str, float] = {}

    for log in logs:
        account_id = log.account_id
        counts[account_id] = counts.get(account_id, 0) + 1
        cost = cost_from_log(cast(RequestLogLike, log))
        if cost is None:
            continue
        costs[account_id] = costs.get(account_id, 0.0) + cost

    results: list[UsageHistoryItem] = []
    for account_id, account in account_map.items():
        usage = usage_by_account.get(account_id)
        used_percent = usage.used_percent if usage else None
        used_percent_value = float(used_percent) if used_percent is not None else 0.0
        remaining_percent = usage_core.remaining_percent_from_used(used_percent_value) or 0.0
        capacity = usage_core.capacity_for_plan(account.plan_type, window)
        remaining_credits = usage_core.remaining_credits_from_percent(used_percent_value, capacity) or 0.0
        results.append(
            UsageHistoryItem(
                account_id=account_id,
                email=account.email,
                remaining_percent_avg=remaining_percent,
                capacity_credits=float(capacity or 0.0),
                remaining_credits=float(remaining_credits),
                request_count=counts.get(account_id, 0),
                cost_usd=round(costs.get(account_id, 0.0), 6),
            )
        )
    return results


def _log_to_cost_item(log: RequestLog) -> CostItem | None:
    model = log.model
    usage = usage_tokens_from_log(cast(RequestLogLike, log))
    if not model or not usage:
        return None
    return CostItem(model=model, usage=usage)


def _usage_metrics(logs_secondary: list[RequestLog]) -> UsageMetricsSummary:
    total_requests = len(logs_secondary)
    error_logs = [log for log in logs_secondary if log.status != "success"]
    error_rate: float | None = None
    if total_requests > 0:
        error_rate = len(error_logs) / total_requests
    top_error = _top_error_code(error_logs)
    tokens_secondary = _sum_tokens(logs_secondary)
    cached_tokens_secondary = _sum_cached_input_tokens(logs_secondary)
    return UsageMetricsSummary(
        requests_7d=total_requests,
        tokens_secondary_window=tokens_secondary,
        cached_tokens_secondary_window=cached_tokens_secondary,
        error_rate_7d=error_rate,
        top_error=top_error,
    )


def _sum_tokens(logs: list[RequestLog]) -> int:
    total = 0
    for log in logs:
        total += total_tokens_from_log(cast(RequestLogLike, log)) or 0
    return total


def _sum_cached_input_tokens(logs: list[RequestLog]) -> int:
    total = 0
    for log in logs:
        total += cached_input_tokens_from_log(cast(RequestLogLike, log)) or 0
    return total


def _top_error_code(logs: list[RequestLog]) -> str | None:
    counts: dict[str, int] = {}
    for log in logs:
        code = log.error_code
        if not code:
            continue
        counts[code] = counts.get(code, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


def _summary_payload_to_response(payload: UsageSummaryPayload) -> UsageSummaryResponse:
    return UsageSummaryResponse(
        primary_window=_window_snapshot_to_model(payload.primary_window),
        secondary_window=_window_snapshot_to_model(payload.secondary_window) if payload.secondary_window else None,
        cost=_cost_summary_to_model(payload.cost),
        metrics=_metrics_summary_to_model(payload.metrics) if payload.metrics else None,
    )


def _window_snapshot_to_model(snapshot: UsageWindowSnapshot) -> UsageWindow:
    capacity_credits = float(snapshot.capacity_credits)
    remaining_credits = usage_core.remaining_credits_from_used(snapshot.used_credits, capacity_credits) or 0.0
    remaining_percent = max(0.0, 100.0 - float(snapshot.used_percent)) if capacity_credits > 0 else 0.0
    return UsageWindow(
        remaining_percent=remaining_percent,
        capacity_credits=capacity_credits,
        remaining_credits=remaining_credits,
        reset_at=from_epoch_seconds(snapshot.reset_at),
        window_minutes=snapshot.window_minutes,
    )


def _cost_summary_to_model(cost: UsageCostSummary) -> UsageCost:
    return UsageCost(
        currency=cost.currency,
        totalUsd7d=cost.total_usd_7d,
        by_model=[UsageCostByModel(model=item.model, usd=item.usd) for item in cost.by_model],
    )


def _metrics_summary_to_model(metrics: UsageMetricsSummary) -> UsageMetrics:
    return UsageMetrics(
        requests_7d=metrics.requests_7d,
        tokens_secondary_window=metrics.tokens_secondary_window,
        cached_tokens_secondary_window=metrics.cached_tokens_secondary_window,
        error_rate_7d=metrics.error_rate_7d,
        top_error=metrics.top_error,
    )
