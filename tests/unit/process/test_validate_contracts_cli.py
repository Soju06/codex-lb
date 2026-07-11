"""CLI-level tests for the contract validator.

Run the actual CLI as a subprocess and assert on exit code and stderr.
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


def _run() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def _run_with_dir(contracts_dir: Path) -> subprocess.CompletedProcess:
    # Override CONTRACTS_DIR inside the subprocess so the validator walks
    # the mirror (tmp_path copy) instead of the real working tree.
    wrapper = (
        "import sys\n"
        f"sys.path.insert(0, {str(CLI.parent)!r})\n"
        "from pathlib import Path\n"
        "import validate_contracts\n"
        f"validate_contracts.CONTRACTS_DIR = Path({str(contracts_dir)!r})\n"
        "sys.exit(validate_contracts.main())\n"
    )
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_cli_passes_when_all_contracts_present() -> None:
    proc = _run()
    assert proc.returncode == 0, proc.stderr
    assert "Validated 5 contracts" in proc.stdout


def test_cli_fails_when_a_contract_is_malformed(tmp_path: Path) -> None:
    # Mirror the contracts directory into tmp_path so the working tree is
    # never touched — a SIGKILL/abort before a finally block runs cannot
    # corrupt openspec/process/contracts/feature.yaml.
    mirror = tmp_path / "contracts"
    shutil.copytree(CONTRACTS_DIR, mirror)

    target = mirror / "feature.yaml"
    target.write_text("name: feature\ntrigger: not-a-valid-trigger\n", encoding="utf-8")

    proc = _run_with_dir(mirror)
    assert proc.returncode != 0
    # The error path should reference the mirror, proving the wrapper
    # override took effect (and that we did not leak into the real tree).
    assert str(mirror) in proc.stderr