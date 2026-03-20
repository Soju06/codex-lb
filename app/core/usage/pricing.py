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
    priority_multiplier: float | None = None
    priority_input_per_1m: float | None = None
    priority_output_per_1m: float | None = None
    priority_cached_input_per_1m: float | None = None
    flex_input_per_1m: float | None = None
    flex_output_per_1m: float | None = None
    flex_cached_input_per_1m: float | None = None
    long_context_threshold_tokens: float | None = None
    long_context_input_per_1m: float | None = None
    long_context_output_per_1m: float | None = None
    long_context_cached_input_per_1m: float | None = None


@dataclass(frozen=True)
class UsageTokens:
    input_tokens: float
    output_tokens: float
    cached_input_tokens: float = 0.0


@dataclass(frozen=True)
class CostItem:
    model: str
    usage: UsageTokens
    service_tier: str | None = None


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
    "gpt-5.4": ModelPrice(
        input_per_1m=2.5,
        cached_input_per_1m=0.25,
        output_per_1m=15.0,
        priority_input_per_1m=5.0,
        priority_cached_input_per_1m=0.5,
        priority_output_per_1m=30.0,
        flex_input_per_1m=1.25,
        flex_cached_input_per_1m=0.125,
        flex_output_per_1m=7.5,
        long_context_threshold_tokens=272_000,
        long_context_input_per_1m=5.0,
        long_context_cached_input_per_1m=0.5,
        long_context_output_per_1m=22.5,
    ),
    "gpt-5.4-mini": ModelPrice(
        input_per_1m=0.75,
        cached_input_per_1m=0.075,
        output_per_1m=4.5,
        flex_input_per_1m=0.375,
        flex_cached_input_per_1m=0.0375,
        flex_output_per_1m=2.25,
    ),
    "gpt-5.4-nano": ModelPrice(
        input_per_1m=0.20,
        cached_input_per_1m=0.02,
        output_per_1m=1.25,
        flex_input_per_1m=0.10,
        flex_cached_input_per_1m=0.01,
        flex_output_per_1m=0.625,
    ),
    "gpt-5.4-pro": ModelPrice(
        input_per_1m=30.0,
        output_per_1m=180.0,
        flex_input_per_1m=15.0,
        flex_output_per_1m=90.0,
        long_context_threshold_tokens=272_000,
        long_context_input_per_1m=60.0,
        long_context_output_per_1m=270.0,
    ),
    "gpt-5.3-codex": ModelPrice(
        input_per_1m=1.75,
        cached_input_per_1m=0.175,
        output_per_1m=14.0,
        priority_input_per_1m=3.5,
        priority_cached_input_per_1m=0.35,
        priority_output_per_1m=28.0,
    ),
    "gpt-5.3": ModelPrice(
        input_per_1m=1.75,
        cached_input_per_1m=0.175,
        output_per_1m=14.0,
    ),
    "gpt-5.3-chat-latest": ModelPrice(
        input_per_1m=1.75,
        cached_input_per_1m=0.175,
        output_per_1m=14.0,
    ),
    "gpt-5.2": ModelPrice(
        input_per_1m=1.75,
        cached_input_per_1m=0.175,
        output_per_1m=14.0,
    ),
    "gpt-5.2-chat-latest": ModelPrice(
        input_per_1m=1.75,
        cached_input_per_1m=0.175,
        output_per_1m=14.0,
    ),
    "gpt-5.1": ModelPrice(
        input_per_1m=1.25,
        cached_input_per_1m=0.125,
        output_per_1m=10.0,
    ),
    "gpt-5.1-chat-latest": ModelPrice(
        input_per_1m=1.25,
        cached_input_per_1m=0.125,
        output_per_1m=10.0,
    ),
    "gpt-5": ModelPrice(
        input_per_1m=1.25,
        cached_input_per_1m=0.125,
        output_per_1m=10.0,
    ),
    "gpt-5-chat-latest": ModelPrice(
        input_per_1m=1.25,
        cached_input_per_1m=0.125,
        output_per_1m=10.0,
    ),
    "gpt-5.2-codex": ModelPrice(
        input_per_1m=1.75,
        cached_input_per_1m=0.175,
        output_per_1m=14.0,
        priority_input_per_1m=3.5,
        priority_cached_input_per_1m=0.35,
        priority_output_per_1m=28.0,
    ),
    "gpt-5.1-codex-max": ModelPrice(
        input_per_1m=1.25,
        cached_input_per_1m=0.125,
        output_per_1m=10.0,
        priority_input_per_1m=2.5,
        priority_cached_input_per_1m=0.25,
        priority_output_per_1m=20.0,
    ),
    "gpt-5.1-codex-mini": ModelPrice(
        input_per_1m=0.25,
        cached_input_per_1m=0.025,
        output_per_1m=2.0,
    ),
    "gpt-5.1-codex": ModelPrice(
        input_per_1m=1.25,
        cached_input_per_1m=0.125,
        output_per_1m=10.0,
        priority_input_per_1m=2.5,
        priority_cached_input_per_1m=0.25,
        priority_output_per_1m=20.0,
    ),
    "gpt-5-codex": ModelPrice(
        input_per_1m=1.25,
        cached_input_per_1m=0.125,
        output_per_1m=10.0,
        priority_input_per_1m=2.5,
        priority_cached_input_per_1m=0.25,
        priority_output_per_1m=20.0,
    ),
}

DEFAULT_MODEL_ALIASES: dict[str, str] = {
    "gpt-5.4-pro*": "gpt-5.4-pro",
    "gpt-5.4-mini*": "gpt-5.4-mini",
    "gpt-5.4-nano*": "gpt-5.4-nano",
    "gpt-5.4*": "gpt-5.4",
    "gpt-5.3-codex*": "gpt-5.3-codex",
    "gpt-5.3-chat-latest*": "gpt-5.3-chat-latest",
    "gpt-5.2-codex*": "gpt-5.2-codex",
    "gpt-5.2-chat-latest*": "gpt-5.2-chat-latest",
    "gpt-5.3*": "gpt-5.3",
    "gpt-5.1-chat-latest*": "gpt-5.1-chat-latest",
    "gpt-5.2*": "gpt-5.2",
    "gpt-5-chat-latest*": "gpt-5-chat-latest",
    "gpt-5.1*": "gpt-5.1",
    "gpt-5*": "gpt-5",
    "gpt-5.1-codex-max*": "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini*": "gpt-5.1-codex-mini",
    "gpt-5.1-codex*": "gpt-5.1-codex",
    "gpt-5-codex*": "gpt-5-codex",
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


def _uses_priority_tier(service_tier: str | None) -> bool:
    normalized = _normalize_service_tier(service_tier)
    if normalized is None:
        return False
    return normalized in {"priority", "fast"}


def _uses_flex_tier(service_tier: str | None) -> bool:
    normalized = _normalize_service_tier(service_tier)
    if normalized is None:
        return False
    return normalized == "flex"


def _normalize_service_tier(service_tier: str | None) -> str | None:
    if service_tier is None:
        return None
    stripped = service_tier.strip().lower()
    return stripped or None


def _effective_rates(
    usage: UsageTokens,
    price: ModelPrice,
    *,
    service_tier: str | None,
) -> tuple[float, float, float]:
    is_long_context = (
        price.long_context_threshold_tokens is not None
        and usage.input_tokens > price.long_context_threshold_tokens
        and price.long_context_input_per_1m is not None
        and price.long_context_output_per_1m is not None
    )
    input_rate = price.input_per_1m
    cached_rate = price.cached_input_per_1m if price.cached_input_per_1m is not None else input_rate
    output_rate = price.output_per_1m

    if _uses_priority_tier(service_tier):
        if price.priority_input_per_1m is not None and price.priority_output_per_1m is not None:
            priority_cached = (
                price.priority_cached_input_per_1m
                if price.priority_cached_input_per_1m is not None
                else price.priority_input_per_1m
            )
            return price.priority_input_per_1m, priority_cached, price.priority_output_per_1m
        if price.priority_multiplier is not None:
            input_rate *= price.priority_multiplier
            cached_rate *= price.priority_multiplier
            output_rate *= price.priority_multiplier
            return input_rate, cached_rate, output_rate

    if _uses_flex_tier(service_tier) and price.flex_input_per_1m is not None and price.flex_output_per_1m is not None:
        input_rate = price.flex_input_per_1m
        cached_rate = price.flex_cached_input_per_1m if price.flex_cached_input_per_1m is not None else input_rate
        output_rate = price.flex_output_per_1m
        if is_long_context:
            input_rate *= 2.0
            cached_rate *= 2.0
            output_rate *= 1.5
        return input_rate, cached_rate, output_rate

    if is_long_context:
        assert price.long_context_input_per_1m is not None
        assert price.long_context_output_per_1m is not None
        input_rate = price.long_context_input_per_1m
        cached_rate = (
            price.long_context_cached_input_per_1m if price.long_context_cached_input_per_1m is not None else input_rate
        )
        output_rate = price.long_context_output_per_1m

    return input_rate, cached_rate, output_rate


def calculate_cost_from_usage(
    usage: UsageTokens | ResponseUsage | None,
    price: ModelPrice,
    *,
    service_tier: str | None = None,
) -> float | None:
    normalized = _normalize_usage(usage)
    if not normalized:
        return None
    billable_input = normalized.input_tokens - normalized.cached_input_tokens

    input_rate, cached_rate, output_rate = _effective_rates(
        normalized,
        price,
        service_tier=service_tier,
    )

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
        cost = calculate_cost_from_usage(usage, price, service_tier=item.service_tier)
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
