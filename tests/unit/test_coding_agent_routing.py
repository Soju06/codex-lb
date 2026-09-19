from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
ROUTING = ROOT / "config" / "coding-agents" / "ROUTING.md"
ADAPTER = ROOT / "config" / "coding-agents" / "claude-adapter.md"
VERIFIER = ROOT / "config" / "coding-agents" / "verify-routing"


def test_canonical_policy_states_the_routing_rule() -> None:
    """The canon carries the rule; the seat lineup moved to the routing table.

    Router program, 2026-09-19: Fable is the one rationed pool, so it drives and
    judges and nothing else runs on it, while Opus is the unrationed default
    seat. A seat/model table in prose would be a second copy of the routing
    table, free to drift, so its absence is part of the contract.
    """
    routing = ROUTING.read_text()
    adapter = ADAPTER.read_text()
    verifier = VERIFIER.read_text()

    assert "Fable is the driver and the judge" in routing
    assert "Opus 5 is the default seat, and it is not rationed" in routing
    assert "Verification is cross-vendor" in routing
    assert "config/coding-agents/routing-table.json" in routing
    assert "route pick" in routing
    assert not [line for line in routing.splitlines() if "| Driver (main loop)" in line]
    for legacy in ("claude-opus-4-8", "Fable/high"):
        assert legacy not in routing
        assert legacy not in adapter
    assert 'settings.get("model") == "fable"' in verifier
    assert 'settings.get("effortLevel") == "high"' in verifier
    assert "model: claude-planner" in verifier
    assert "claude-opus-5" in verifier


def test_routing_table_is_the_single_source_of_seat_truth() -> None:
    table = json.loads((ROOT / "config" / "coding-agents" / "routing-table.json").read_text())
    verifier = VERIFIER.read_text()

    chains = {name: spec.get("chain") or [] for name, spec in table["classes"].items()}
    assert chains["explore"][0] == {"seat": "Explore", "model": "claude-sonnet-5"}
    assert chains["implement"][0] == {"seat": "opus-seat", "model": "claude-opus-5"}
    assert table["classes"]["verify"]["cross_vendor"] is True
    # No Fable anywhere in a seat chain: Fable is the driver, never a seat.
    assert not [entry for chain in chains.values() for entry in chain if "fable" in entry.get("model", "")]
    assert 'CCGPT_MODEL = "gpt-5.6-sol"' in verifier
    assert '"--effort", "high"' in verifier


def test_fable_telemetry_and_historical_fixtures_are_not_route_migrated() -> None:
    launcher = (ROOT / "clients" / "claude-lb-launch").read_text()
    pricing = (ROOT / "app" / "core" / "anthropic" / "pricing.py").read_text()
    pulse_test = (ROOT / "tests" / "unit" / "test_account_pulse.py").read_text()
    fixture_path = ROOT / "clients" / "macos-menubar" / "Tests" / "AgentLBTests" / "Fixtures" / "request-logs.json"
    fixture = fixture_path.read_text()

    assert 'FABLE_SCOPED_WEEKLY_QUOTA_KEY = "anthropic_fable_scoped_weekly"' in launcher
    assert '"claude-fable-5": AnthropicModelPrice(' in pricing
    assert 'calls[0]["model"] == "claude-fable-5"' in pulse_test
    assert '"model":"claude-fable-5"' in fixture
