from __future__ import annotations

from typing import Protocol

from app.core.usage.pricing import UsageTokens, calculate_cost_from_usage, get_pricing_for_model


class RequestLogLike(Protocol):
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    reasoning_tokens: int | None


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
    output_tokens = log.output_tokens if log.output_tokens is not None else log.reasoning_tokens
    if output_tokens is None:
        return None
    cached_tokens = cached_input_tokens_from_log(log) or 0
    return UsageTokens(
        input_tokens=float(input_tokens),
        output_tokens=float(output_tokens),
        cached_input_tokens=float(cached_tokens),
    )


def cost_from_log(log: RequestLogLike, *, precision: int | None = None) -> float | None:
    if not log.model:
        return None
    usage = usage_tokens_from_log(log)
    if not usage:
        return None
    resolved = get_pricing_for_model(log.model, None, None)
    if not resolved:
        return None
    _, price = resolved
    cost = calculate_cost_from_usage(usage, price)
    if cost is None:
        return None
    if precision is None:
        return cost
    return round(cost, precision)


def total_tokens_from_log(log: RequestLogLike) -> int | None:
    input_tokens = log.input_tokens
    output_tokens = log.output_tokens
    if output_tokens is None and log.reasoning_tokens is not None:
        output_tokens = log.reasoning_tokens
    if input_tokens is None and output_tokens is None:
        return None
    return (input_tokens or 0) + (output_tokens or 0)
