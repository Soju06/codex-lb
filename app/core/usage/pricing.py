from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Iterable, Mapping

from app.core.openai.models import ResponseUsage
from app.core.usage.types import UsageCostByModel, UsageCostSummary


@dataclass(frozen=True)
class ModelPrice:
    input_per_1m: float
    output_per_1m: float
    cached_input_per_1m: float | None = None


@dataclass(frozen=True)
class UsageTokens:
    input_tokens: float
    output_tokens: float
    cached_input_tokens: float = 0.0


@dataclass(frozen=True)
class CostItem:
    model: str
    usage: UsageTokens


def _as_number(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _normalize_usage(usage: UsageTokens | ResponseUsage | None) -> UsageTokens | None:
    if isinstance(usage, UsageTokens):
        return usage
    if not usage:
        return None
    input_tokens = _as_number(usage.input_tokens)
    output_tokens = _as_number(usage.output_tokens)
    if output_tokens is None and usage.output_tokens_details is not None:
        output_tokens = _as_number(usage.output_tokens_details.reasoning_tokens)
    if input_tokens is None or output_tokens is None:
        return None
    cached_tokens = 0.0
    if usage.input_tokens_details is not None:
        cached_tokens = _as_number(usage.input_tokens_details.cached_tokens) or 0.0
    cached_tokens = max(0.0, min(cached_tokens, input_tokens))
    return UsageTokens(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_tokens,
    )


DEFAULT_PRICING_MODELS: dict[str, ModelPrice] = {
    "gpt-5.3": ModelPrice(input_per_1m=1.75, cached_input_per_1m=0.175, output_per_1m=14.0),
    "gpt-5.2": ModelPrice(input_per_1m=1.75, cached_input_per_1m=0.175, output_per_1m=14.0),
    "gpt-5.1": ModelPrice(input_per_1m=1.25, cached_input_per_1m=0.125, output_per_1m=10.0),
    "gpt-5": ModelPrice(input_per_1m=1.25, cached_input_per_1m=0.125, output_per_1m=10.0),
    "gpt-5.1-codex-max": ModelPrice(
        input_per_1m=1.25,
        cached_input_per_1m=0.125,
        output_per_1m=10.0,
    ),
    "gpt-5.1-codex-mini": ModelPrice(
        input_per_1m=0.25,
        cached_input_per_1m=0.025,
        output_per_1m=2.0,
    ),
    "gpt-5.1-codex": ModelPrice(input_per_1m=1.25, cached_input_per_1m=0.125, output_per_1m=10.0),
    "gpt-5-codex": ModelPrice(input_per_1m=1.25, cached_input_per_1m=0.125, output_per_1m=10.0),
    "claude-opus-4": ModelPrice(input_per_1m=15.0, cached_input_per_1m=1.5, output_per_1m=75.0),
    "claude-sonnet-4": ModelPrice(input_per_1m=3.0, cached_input_per_1m=0.3, output_per_1m=15.0),
    "claude-3-7-sonnet": ModelPrice(input_per_1m=3.0, cached_input_per_1m=0.3, output_per_1m=15.0),
    "claude-3-5-haiku": ModelPrice(input_per_1m=0.8, cached_input_per_1m=0.08, output_per_1m=4.0),
}

DEFAULT_MODEL_ALIASES: dict[str, str] = {
    "gpt-5.3*": "gpt-5.3",
    "gpt-5.2*": "gpt-5.2",
    "gpt-5.1*": "gpt-5.1",
    "gpt-5*": "gpt-5",
    "gpt-5.1-codex-max*": "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini*": "gpt-5.1-codex-mini",
    "gpt-5.1-codex*": "gpt-5.1-codex",
    "gpt-5-codex*": "gpt-5-codex",
    "claude-opus-4*": "claude-opus-4",
    "claude-sonnet-4*": "claude-sonnet-4",
    "claude-3-7-sonnet*": "claude-3-7-sonnet",
    "claude-3-5-haiku*": "claude-3-5-haiku",
    "anthropic/claude-opus-4*": "claude-opus-4",
    "anthropic/claude-sonnet-4*": "claude-sonnet-4",
    "anthropic/claude-3-7-sonnet*": "claude-3-7-sonnet",
    "anthropic/claude-3-5-haiku*": "claude-3-5-haiku",
}


def resolve_model_alias(model: str, aliases: Mapping[str, str]) -> str | None:
    if not model:
        return None
    normalized = model.lower()
    matched: list[tuple[int, str]] = []
    for pattern, target in aliases.items():
        if fnmatchcase(normalized, pattern.lower()):
            matched.append((len(pattern), target))
    if not matched:
        return None
    return max(matched, key=lambda item: item[0])[1]


def get_pricing_for_model(
    model: str,
    pricing: Mapping[str, ModelPrice] | None = None,
    aliases: Mapping[str, str] | None = None,
) -> tuple[str, ModelPrice] | None:
    if not model:
        return None
    pricing = pricing or DEFAULT_PRICING_MODELS
    aliases = aliases or DEFAULT_MODEL_ALIASES

    normalized = model.lower()
    for key, value in pricing.items():
        if key.lower() == normalized:
            return key, value

    alias = resolve_model_alias(normalized, aliases)
    if not alias:
        return None
    for key, value in pricing.items():
        if key.lower() == alias.lower():
            return key, value
    return None


def calculate_cost_from_usage(usage: UsageTokens | ResponseUsage | None, price: ModelPrice) -> float | None:
    normalized = _normalize_usage(usage)
    if not normalized:
        return None
    billable_input = normalized.input_tokens - normalized.cached_input_tokens

    input_rate = price.input_per_1m
    cached_rate = price.cached_input_per_1m if price.cached_input_per_1m is not None else input_rate
    output_rate = price.output_per_1m

    return (
        (billable_input / 1_000_000) * input_rate
        + (normalized.cached_input_tokens / 1_000_000) * cached_rate
        + (normalized.output_tokens / 1_000_000) * output_rate
    )


def calculate_costs(
    items: Iterable[CostItem],
    pricing: Mapping[str, ModelPrice] | None = None,
    aliases: Mapping[str, str] | None = None,
) -> UsageCostSummary:
    pricing = pricing or DEFAULT_PRICING_MODELS
    aliases = aliases or DEFAULT_MODEL_ALIASES

    totals: dict[str, float] = defaultdict(float)
    total_usd = 0.0

    for item in items:
        model = item.model
        usage = item.usage
        resolved = get_pricing_for_model(model, pricing, aliases)
        if not resolved:
            continue
        canonical, price = resolved
        cost = calculate_cost_from_usage(usage, price)
        if cost is None:
            continue
        totals[canonical] += cost
        total_usd += cost

    by_model = [UsageCostByModel(model=model, usd=round(value, 6)) for model, value in sorted(totals.items())]
    return UsageCostSummary(
        currency="USD",
        total_usd_7d=round(total_usd, 6),
        by_model=by_model,
    )
