from __future__ import annotations

import pytest

from app.core.openai.models import ResponseUsage, ResponseUsageDetails
from app.core.usage.pricing import (
    CostItem,
    DEFAULT_MODEL_ALIASES,
    DEFAULT_PRICING_MODELS,
    ModelPrice,
    UsageTokens,
    calculate_cost_from_usage,
    calculate_costs,
    get_pricing_for_model,
    resolve_model_alias,
)

pytestmark = pytest.mark.unit


def test_resolve_model_alias_longest_match():
    aliases = {
        "gpt-5*": "gpt-5",
        "gpt-5.1-codex*": "gpt-5.1-codex",
        "gpt-5.1-codex-max*": "gpt-5.1-codex-max",
    }
    assert resolve_model_alias("gpt-5.1-codex-max-2025", aliases) == "gpt-5.1-codex-max"


def test_get_pricing_for_model_alias():
    result = get_pricing_for_model("gpt-5.1-codex-mini-2025", DEFAULT_PRICING_MODELS, DEFAULT_MODEL_ALIASES)
    assert result is not None
    model, price = result
    assert model == "gpt-5.1-codex-mini"
    assert price.output_per_1m == 2.0


def test_calculate_cost_from_usage_cached_tokens():
    usage = ResponseUsage(
        input_tokens=1000,
        output_tokens=500,
        input_tokens_details=ResponseUsageDetails(cached_tokens=200),
    )
    price = ModelPrice(input_per_1m=2.0, cached_input_per_1m=0.5, output_per_1m=4.0)
    cost = calculate_cost_from_usage(usage, price)
    expected = (800 / 1_000_000) * 2.0 + (200 / 1_000_000) * 0.5 + (500 / 1_000_000) * 4.0
    assert cost == pytest.approx(expected)


def test_calculate_costs_aggregates_by_model():
    items = [
        CostItem(model="gpt-5.1", usage=UsageTokens(input_tokens=1000.0, output_tokens=1000.0)),
        CostItem(model="gpt-5.1-variant", usage=UsageTokens(input_tokens=2000.0, output_tokens=1000.0)),
    ]
    result = calculate_costs(items, DEFAULT_PRICING_MODELS, DEFAULT_MODEL_ALIASES)
    assert result.currency == "USD"
    by_model = {entry.model: entry.usd for entry in result.by_model}
    assert "gpt-5.1" in by_model
    assert by_model["gpt-5.1"] > 0
