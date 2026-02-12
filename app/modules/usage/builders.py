from __future__ import annotations

from typing import cast

from app.core import usage as usage_core
from app.core.usage.logs import (
    RequestLogLike,
    cached_input_tokens_from_log,
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
from app.core.utils.time import from_epoch_seconds
from app.db.models import Account, RequestLog
from app.modules.usage.schemas import (
    UsageCost,
    UsageHistoryItem,
    UsageHistoryResponse,
    UsageMetrics,
    UsageSummaryResponse,
    UsageWindow,
    UsageWindowResponse,
)


def build_usage_summary_response(
    *,
    accounts: list[Account],
    primary_rows: list[UsageWindowRow],
    secondary_rows: list[UsageWindowRow],
    logs_secondary: list[RequestLog],
) -> UsageSummaryResponse:
    account_map = {account.id: account for account in accounts}
    primary_window = usage_core.summarize_usage_window(primary_rows, account_map, "primary")
    secondary_window = usage_core.summarize_usage_window(secondary_rows, account_map, "secondary")

    cost_items = [item for item in (_log_to_cost_item(log) for log in logs_secondary) if item]
    cost = calculate_costs(cost_items)
    metrics = _usage_metrics(logs_secondary)

    payload = usage_core.parse_usage_summary(primary_window, secondary_window, cost, metrics)
    return _summary_payload_to_response(payload)


def build_usage_history_response(
    *,
    hours: int,
    usage_rows: list[UsageWindowRow],
    accounts: list[Account],
    window: str,
) -> UsageHistoryResponse:
    account_map = {account.id: account for account in accounts}
    accounts_history = _build_account_history(usage_rows, account_map, window)
    return UsageHistoryResponse(window_hours=hours, accounts=accounts_history)


def build_usage_window_response(
    *,
    window_key: str,
    window_minutes: int | None,
    usage_rows: list[UsageWindowRow],
    accounts: list[Account],
) -> UsageWindowResponse:
    account_map = {account.id: account for account in accounts}
    accounts_history = _build_account_history(usage_rows, account_map, window_key)
    return UsageWindowResponse(
        window_key=window_key,
        window_minutes=window_minutes,
        accounts=accounts_history,
    )


def _build_account_history(
    usage_rows: list[UsageWindowRow],
    account_map: dict[str, Account],
    window: str,
) -> list[UsageHistoryItem]:
    usage_by_account = {row.account_id: row for row in usage_rows}

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
                remaining_percent_avg=remaining_percent,
                capacity_credits=float(capacity or 0.0),
                remaining_credits=float(remaining_credits),
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
    )


def _metrics_summary_to_model(metrics: UsageMetricsSummary) -> UsageMetrics:
    return UsageMetrics(
        requests_7d=metrics.requests_7d,
        tokens_secondary_window=metrics.tokens_secondary_window,
        cached_tokens_secondary_window=metrics.cached_tokens_secondary_window,
        error_rate_7d=metrics.error_rate_7d,
        top_error=metrics.top_error,
    )
