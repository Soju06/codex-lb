"""Published-rate local API-key budgets, not subscription quota estimates."""

from __future__ import annotations

# Two decimal places in credits per million tokens require 10^-8 credit
# storage units. Never round individual requests to whole credits.
CREDIT_SCALE = 100_000_000
MAX_CREDIT_UNITS = 2**63 - 1
MAX_CREDIT_LIMIT = MAX_CREDIT_UNITS // CREDIT_SCALE

# Standard text rates read 2026-10-30:
# https://learn.chatgpt.com/docs/pricing#token-rates
# Tuples contain storage units PER TOKEN: uncached input, cached input, output.
# Keep this independent of the USD catalog: promotional credit rates and
# subscription usage do not necessarily follow API dollar prices.
_CREDIT_RATES: dict[str, tuple[int, int, int]] = {
    "gpt-6-astra": (25_000, 2_500, 125_000),
    "gpt-6.1-sol": (5_000, 250, 25_000),
    "gpt-6.1-terra": (2_500, 125, 12_500),
    "gpt-6-sol": (5_000, 500, 25_000),
    "gpt-6-luna": (250, 25, 1_250),
    "gpt-5.6-sol": (10_000, 1_000, 50_000),
    "gpt-5.6-terra": (5_000, 500, 30_000),
    "gpt-5.6-luna": (500, 50, 3_000),
}


class CreditPricingUnavailable(ValueError):
    """A credit-limited request cannot be priced from the published rate card."""


def credit_units_for_usage(
    model: str | None,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    service_tier: str | None = None,
) -> int:
    rates = _CREDIT_RATES.get(model or "")
    if rates is None:
        raise CreditPricingUnavailable("No published Codex credit rate for the requested model")
    tier = (service_tier or "default").strip().lower()
    if tier in {"auto", "default"}:
        multiplier = 1
    elif tier in {"fast", "priority"}:
        multiplier = 2
    elif tier == "ultrafast" and model in {"gpt-6-astra", "gpt-6.1-sol"}:
        multiplier = 6
    else:
        raise CreditPricingUnavailable("No published Codex credit rate for the requested service tier")
    input_tokens = max(0, input_tokens)
    output_tokens = max(0, output_tokens)
    cached_input_tokens = max(0, min(cached_input_tokens, input_tokens))
    input_rate, cached_rate, output_rate = rates
    charge = multiplier * (
        (input_tokens - cached_input_tokens) * input_rate
        + cached_input_tokens * cached_rate
        + output_tokens * output_rate
    )
    return min(charge, MAX_CREDIT_UNITS)
