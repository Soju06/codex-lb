"""Integration tests for the process-check workflow's validator path.

We exercise the validator CLI in isolation against synthetic contracts.
We do not run actual GitHub Actions here — the YAML shape is checked
in a separate unit test if needed.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI = REPO_ROOT / "openspec" / "process" / "scripts" / "validate_contracts.py"
CONTRACTS_DIR = REPO_ROOT / "openspec" / "process" / "contracts"


@pytest.fixture
def mirrored_contracts(tmp_path: Path) -> Path:
    """Copy real contracts to a temp dir; we'll monkey-patch the CLI to use it."""
    mirror = tmp_path / "contracts"
    shutil.copytree(CONTRACTS_DIR, mirror)
    return mirror


def _run_with_dir(contracts_dir: Path) -> subprocess.CompletedProcess:
    wrapper = f"""
import sys
from pathlib import Path
sys.path.insert(0, {str(CLI.parent)!r})
import validate_contracts
validate_contracts.CONTRACTS_DIR = Path({str(contracts_dir)!r})
sys.exit(validate_contracts.main())
"""
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_validator_passes_against_clean_mirror(mirrored_contracts: Path) -> None:
    proc = _run_with_dir(mirrored_contracts)
    assert proc.returncode == 0, proc.stderr


def test_validator_fails_when_irreversible_missing_confirmation(
    mirrored_contracts: Path,
) -> None:
    bad = mirrored_contracts / "feature.yaml"
    text = bad.read_text(encoding="utf-8")
    bad.write_text(text.replace("confirmation_phrase: \"merge PR #\"", ""), encoding="utf-8")
    proc = _run_with_dir(mirrored_contracts)
    assert proc.returncode != 0
    assert "feature.yaml" in proc.stderr
