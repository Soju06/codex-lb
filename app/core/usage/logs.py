from __future__ import annotations

from typing import Protocol

from app.core.usage.pricing import (
    UsageCostBreakdown,
    UsageTokens,
    calculate_cost_breakdown_from_usage,
    calculate_cost_from_usage,
    get_pricing_for_model,
)


class RequestLogLike(Protocol):
    @property
    def model(self) -> str | None: ...

    @property
    def service_tier(self) -> str | None: ...

    @property
    def input_tokens(self) -> int | None: ...

    @property
    def output_tokens(self) -> int | None: ...

    @property
    def cached_input_tokens(self) -> int | None: ...

    @property
    def reasoning_tokens(self) -> int | None: ...

    @property
    def cost_usd(self) -> float | None: ...


def cached_input_tokens_from_log(log: RequestLogLike) -> int | None:
    cached_tokens = log.cached_input_tokens
    if cached_tokens is None:
        return None
    cached_tokens = max(0, int(cached_tokens))
    input_tokens = log.input_tokens
    if input_tokens is not None:
        cached_tokens = min(cached_tokens, int(input_tokens))
    return cached_tokens


def usage_tokens_from_log(log: RequestLogLike) -> UsageTokens | None:
    input_tokens = log.input_tokens
    if input_tokens is None:
        return None
    output_tokens = output_tokens_from_log(log)
    if output_tokens is None:
        return None
    cached_tokens = cached_input_tokens_from_log(log) or 0
    return UsageTokens(
        input_tokens=float(input_tokens),
        output_tokens=float(output_tokens),
        cached_input_tokens=float(cached_tokens),
    )


def output_tokens_from_log(log: RequestLogLike) -> int | None:
    output_tokens = log.output_tokens
    if output_tokens is not None:
        return int(output_tokens)
    reasoning_tokens = log.reasoning_tokens
    if reasoning_tokens is None:
        return None
    return int(reasoning_tokens)


def calculated_cost_from_log(log: RequestLogLike, *, precision: int | None = None) -> float | None:
    if not log.model:
        return None
    usage = usage_tokens_from_log(log)
    if not usage:
        return None
    resolved = get_pricing_for_model(log.model, None, None)
    if not resolved:
        return None
    _, price = resolved
    cost = calculate_cost_from_usage(usage, price, service_tier=log.service_tier)
    if cost is None:
        return None
    if precision is None:
        return cost
    return round(cost, precision)


def cost_from_log(log: RequestLogLike, *, precision: int | None = None) -> float | None:
    cost = log.cost_usd
    if cost is None:
        return None
    if precision is None:
        return float(cost)
    return round(float(cost), precision)


def _totals_match(left: float | None, right: float | None, *, precision: int | None) -> bool:
    if left is None or right is None:
        return False
    if precision is None:
        return left == right
    return abs(left - right) < (10 ** (-precision)) / 2


def cost_breakdown_from_log(log: RequestLogLike, *, precision: int | None = None) -> UsageCostBreakdown:
    full_breakdown: UsageCostBreakdown | None = None
    input_usd: float | None = None
    cached_input_usd: float | None = None
    output_usd: float | None = None
    raw_total_usd: float | None = None
    total_usd: float | None = None
    if log.model:
        resolved = get_pricing_for_model(log.model, None, None)
        if resolved is not None:
            _, price = resolved
            input_tokens = log.input_tokens
            cached_tokens = cached_input_tokens_from_log(log)
            output_tokens = output_tokens_from_log(log)
            usage = usage_tokens_from_log(log)
            if usage is not None:
                raw_full_breakdown = calculate_cost_breakdown_from_usage(usage, price, service_tier=log.service_tier)
                if raw_full_breakdown is not None:
                    raw_total_usd = raw_full_breakdown.total_usd
                full_breakdown = calculate_cost_breakdown_from_usage(
                    usage,
                    price,
                    service_tier=log.service_tier,
                    precision=precision,
                )
                if full_breakdown is not None:
                    total_usd = full_breakdown.total_usd
            if input_tokens is not None and cached_tokens is not None:
                input_breakdown = calculate_cost_breakdown_from_usage(
                    UsageTokens(
                        input_tokens=float(input_tokens),
                        output_tokens=0.0,
                        cached_input_tokens=float(cached_tokens),
                    ),
                    price,
                    service_tier=log.service_tier,
                    precision=precision,
                )
                if input_breakdown is not None:
                    input_usd = input_breakdown.input_usd
                    cached_input_usd = input_breakdown.cached_input_usd
            if output_tokens is not None:
                output_breakdown = calculate_cost_breakdown_from_usage(
                    UsageTokens(
                        input_tokens=float(input_tokens or 0),
                        output_tokens=float(output_tokens),
                        cached_input_tokens=float(cached_tokens or 0),
                    ),
                    price,
                    service_tier=log.service_tier,
                    precision=precision,
                )
                if output_breakdown is not None:
                    output_usd = output_breakdown.output_usd

    persisted_cost = cost_from_log(log, precision=precision)
    if persisted_cost is not None:
        persisted_raw_cost = cost_from_log(log)
        if not _totals_match(persisted_raw_cost, raw_total_usd, precision=precision):
            return UsageCostBreakdown(
                input_usd=None,
                cached_input_usd=None,
                output_usd=None,
                total_usd=persisted_cost,
            )
        return UsageCostBreakdown(
            input_usd=input_usd,
            cached_input_usd=cached_input_usd,
            output_usd=output_usd,
            total_usd=persisted_cost,
        )
    if full_breakdown is not None:
        return UsageCostBreakdown(
            input_usd=input_usd,
            cached_input_usd=cached_input_usd,
            output_usd=output_usd,
            total_usd=total_usd,
        )
    return UsageCostBreakdown(
        input_usd=input_usd,
        cached_input_usd=cached_input_usd,
        output_usd=output_usd,
        total_usd=None,
    )


def total_tokens_from_log(log: RequestLogLike) -> int | None:
    input_tokens = log.input_tokens
    output_tokens = output_tokens_from_log(log)
    if input_tokens is None and output_tokens is None:
        return None
    return (input_tokens or 0) + (output_tokens or 0)
