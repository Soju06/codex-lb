"""Schema-level tests for process contracts.

These tests load every contract YAML and validate it against the JSON
Schema. They do not assert semantic correctness — that is the role of
the contract-specific tests in later tasks. They only assert shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS_DIR = REPO_ROOT / "openspec" / "process" / "contracts"
SCHEMA_PATH = CONTRACTS_DIR / "schema.json"

REQUIRED_CONTRACTS = (
    "feature",
    "bugfix",
    "release-beta",
    "release-stable",
    "sync-upstream",
)


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:  # ty: ignore[invalid-type-form]
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def test_all_required_contracts_exist() -> None:
    present = {p.stem for p in CONTRACTS_DIR.glob("*.yaml")}
    missing = set(REQUIRED_CONTRACTS) - present
    assert not missing, f"missing contracts: {sorted(missing)}"


@pytest.mark.parametrize("name", REQUIRED_CONTRACTS)
def test_required_contract_validates(
    name: str,
    validator: Draft202012Validator,  # ty: ignore[invalid-type-form]
) -> None:
    path = CONTRACTS_DIR / f"{name}.yaml"
    assert path.exists(), f"{path} does not exist"
    contract = yaml.safe_load(path.read_text(encoding="utf-8"))
    validator.validate(contract)  # raises on failure


def test_irreversible_phase_requires_confirmation_phrase(
    validator: Draft202012Validator,  # ty: ignore[invalid-type-form]
) -> None:
    bad = {
        "name": "demo",
        "trigger": "/process demo",
        "interruption_commands": ["stop"],
        "phases": [
            {
                "name": "merge",
                "description": "merge a PR",
                "irreversible": True,
                # confirmation_phrase intentionally missing
                "stop_signals": ["ci_red"],
            }
        ],
    }
    with pytest.raises(Exception):
        validator.validate(bad)
