#!/usr/bin/env python3
"""Validate every process contract against the JSON Schema.

Exits 0 when all five required contracts validate; non-zero otherwise.

Usage:
    uv run python openspec/process/scripts/validate_contracts.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

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


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def load_contract(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def main() -> int:
    schema = load_schema()
    validator = Draft202012Validator(schema)
    errors: list[str] = []

    present = {p.stem for p in CONTRACTS_DIR.glob("*.yaml")}
    missing = sorted(set(REQUIRED_CONTRACTS) - present)
    if missing:
        errors.append(
            "Missing required contracts: " + ", ".join(missing)
        )

    for contract_path in sorted(CONTRACTS_DIR.glob("*.yaml")):
        contract = load_contract(contract_path)
        try:
            validator.validate(contract)
        except ValidationError as exc:
            errors.append(
                f"{contract_path.relative_to(REPO_ROOT)}: {exc.message}"
            )

    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        return 1

    print(
        f"Validated {len(REQUIRED_CONTRACTS)} contracts against "
        f"{SCHEMA_PATH.relative_to(REPO_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())