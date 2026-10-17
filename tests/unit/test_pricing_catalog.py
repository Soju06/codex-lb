from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.core.usage import pricing_catalog as catalog
from app.core.usage.pricing import ModelPrice, UsageTokens, calculate_cost_from_usage, get_pricing_for_model


@pytest.fixture(autouse=True)
def isolated_catalog(monkeypatch):
    monkeypatch.setattr(catalog, "_prices", None)


def models_dev(cost=None):
    return {
        "openai": {
            "models": {
                "gpt-test": {
                    "modalities": {"output": ["text"]},
                    "cost": cost or {"input": 10, "output": 50, "cache_read": 1},
                }
            }
        }
    }


def test_models_dev_units_context_threshold_and_priority():
    data = models_dev(
        {
            "input": 10,
            "output": 50,
            "cache_read": 1,
            "tiers": [{"tier": {"type": "context", "size": 272000}, "input": 20, "output": 75, "cache_read": 2}],
        }
    )
    data["openai"]["models"]["gpt-test"]["experimental"] = {
        "modes": {
            "fast": {
                "cost": {"input": 20, "output": 100, "cache_read": 2},
                "provider": {"body": {"service_tier": "priority"}},
            }
        }
    }
    price = catalog.parse_models_dev(data)["gpt-test"]
    assert price.long_context_threshold_tokens == 272000
    assert price.priority_output_per_1m == 100
    assert calculate_cost_from_usage(UsageTokens(300000, 1000, 100000), price) == 4.275


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True, "10"])
def test_invalid_source_cannot_install_prices(bad):
    with pytest.raises(ValueError):
        catalog.parse_models_dev(models_dev({"input": bad, "output": 50}))


def test_litellm_units_and_non_openai_filter():
    entry = {
        "litellm_provider": "openai",
        "mode": "chat",
        "input_cost_per_token": 1e-5,
        "output_cost_per_token": 5e-5,
        "cache_read_input_token_cost": 1e-6,
        "input_cost_per_token_priority": 2e-5,
        "output_cost_per_token_priority": 1e-4,
    }
    prices = catalog.parse_litellm(
        {"gpt-test": entry, "azure/gpt-test": entry, "foreign": {**entry, "litellm_provider": "bedrock"}}
    )
    assert set(prices) == {"gpt-test"}
    assert prices["gpt-test"].input_per_1m == 10
    assert prices["gpt-test"].priority_output_per_1m == 100


def test_supplement_only_when_base_prices_agree():
    primary = ModelPrice(10, 50, 1)
    secondary = replace(primary, flex_input_per_1m=5, flex_output_per_1m=25)
    assert catalog.merge_catalogs({"m": primary}, {"m": secondary})["m"] == secondary
    changed = replace(primary, input_per_1m=12)
    assert catalog.merge_catalogs({"m": changed}, {"m": secondary})["m"] == changed


@pytest.mark.parametrize("tier, expected", [(None, 4.275), ("priority", 8.55), ("fast", 8.55), ("flex", 2.1375)])
def test_astra_prices_include_long_context_tiers(tier, expected):
    catalog.install_prices(
        {
            "gpt-6-astra": ModelPrice(
                10,
                50,
                1,
                long_context_threshold_tokens=272000,
                long_context_input_per_1m=20,
                long_context_output_per_1m=75,
                long_context_cached_input_per_1m=2,
                priority_input_per_1m=20,
                priority_output_per_1m=100,
                priority_cached_input_per_1m=2,
                priority_long_context_input_per_1m=40,
                priority_long_context_output_per_1m=150,
                priority_long_context_cached_input_per_1m=4,
                flex_input_per_1m=5,
                flex_output_per_1m=25,
                flex_cached_input_per_1m=0.5,
                flex_long_context_input_per_1m=10,
                flex_long_context_output_per_1m=37.5,
                flex_long_context_cached_input_per_1m=1,
            )
        }
    )
    resolved = get_pricing_for_model("gpt-6-astra-2026-09-04")
    assert resolved is not None
    assert resolved[0] == "gpt-6-astra"
    assert calculate_cost_from_usage(UsageTokens(300000, 1000, 100000), resolved[1], service_tier=tier) == expected


def test_new_dated_model_beats_legacy_gpt5_wildcard():
    catalog.install_prices({"gpt-5.99": ModelPrice(17, 80)})
    assert get_pricing_for_model("gpt-5.99-2026-09-10") == ("gpt-5.99", ModelPrice(17, 80))
    sol = get_pricing_for_model("gpt-5.6")
    assert sol is not None
    assert sol[0] == "gpt-5.6-sol"


def test_snapshot_round_trip_and_partial_refresh_preserves_missing_models():
    prices = catalog.decode_snapshot(json.loads(catalog.BUNDLE_PATH.read_text()))
    assert catalog.decode_snapshot(json.loads(catalog.encode_snapshot(prices))) == prices
    catalog.install_prices({"gpt-test": ModelPrice(1, 2)})
    catalog.install_prices({"gpt-other": ModelPrice(2, 3)})
    assert get_pricing_for_model("gpt-test") == ("gpt-test", ModelPrice(1, 2))


def test_unlisted_gpt5_family_stays_missing_until_pricing_arrives():
    assert get_pricing_for_model("gpt-5.99") is None
    catalog.install_prices({"gpt-5.99": ModelPrice(17, 80)})
    assert get_pricing_for_model("gpt-5.99") == ("gpt-5.99", ModelPrice(17, 80))


def test_partial_source_outage_retains_compatible_tier_prices():
    complete = ModelPrice(10, 50, 1, flex_input_per_1m=5, flex_output_per_1m=25)
    catalog.install_prices({"gpt-test": complete})
    catalog.install_prices({"gpt-test": ModelPrice(10, 50, 1)})
    assert get_pricing_for_model("gpt-test") == ("gpt-test", complete)


def test_conflicting_priority_rate_cannot_borrow_long_context_rates():
    primary = ModelPrice(10, 50, 1, priority_input_per_1m=25, priority_output_per_1m=125)
    secondary = replace(
        primary,
        priority_input_per_1m=20,
        priority_output_per_1m=100,
        priority_long_context_input_per_1m=40,
        priority_long_context_output_per_1m=150,
    )
    assert catalog.merge_catalogs({"m": primary}, {"m": secondary})["m"] == primary


def test_bundled_snapshot_covers_astra_without_network():
    prices = catalog.decode_snapshot(json.loads(catalog.BUNDLE_PATH.read_text()))
    assert "gpt-6-astra" in prices
    assert get_pricing_for_model("gpt-6-astra") == ("gpt-6-astra", prices["gpt-6-astra"])


@pytest.mark.parametrize("tier", ["priority", "flex"])
def test_litellm_tier_only_long_context_rates_are_used(tier):
    entry = {
        "litellm_provider": "openai",
        "mode": "chat",
        "input_cost_per_token": 1e-5,
        "output_cost_per_token": 5e-5,
        "cache_read_input_token_cost": 1e-6,
        f"input_cost_per_token_{tier}": 2e-5,
        f"output_cost_per_token_{tier}": 1e-4,
        f"cache_read_input_token_cost_{tier}": 2e-6,
        f"input_cost_per_token_above_272k_tokens_{tier}": 4e-5,
        f"output_cost_per_token_above_272k_tokens_{tier}": 1.5e-4,
        f"cache_read_input_token_cost_above_272k_tokens_{tier}": 4e-6,
    }
    price = catalog.parse_litellm({"gpt-test": entry})["gpt-test"]
    assert price.long_context_input_per_1m is None
    assert calculate_cost_from_usage(UsageTokens(300000, 1000, 100000), price, service_tier=tier) == 8.55
    assert calculate_cost_from_usage(UsageTokens(272000, 1000, 100000), price, service_tier=tier) == pytest.approx(3.74)
    assert calculate_cost_from_usage(UsageTokens(300000, 1000, 100000), price) == pytest.approx(2.15)
    # Another tier's long-context group cannot trigger guessed multipliers for this tier.
    other = "flex" if tier == "priority" else "priority"
    ordinary = replace(price, **{f"{other}_input_per_1m": 2, f"{other}_output_per_1m": 4})
    assert calculate_cost_from_usage(UsageTokens(300000, 1000, 100000), ordinary, service_tier=other) == pytest.approx(
        0.604
    )


@pytest.mark.parametrize("prefix", ["long_context_", "priority_long_context_", "flex_long_context_"])
@pytest.mark.parametrize("threshold", [None, 0])
def test_snapshot_rejects_long_context_rates_without_positive_threshold(prefix, threshold):
    values = {"input_per_1m": 10, "output_per_1m": 50, prefix + "input_per_1m": 20, prefix + "output_per_1m": 100}
    if threshold is not None:
        values["long_context_threshold_tokens"] = threshold
    with pytest.raises(ValueError, match="Missing context threshold"):
        catalog.decode_snapshot({"schema_version": 1, "models": {"gpt-test": values}})
