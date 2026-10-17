from __future__ import annotations

import fcntl
import json
import os
import sys
from pathlib import Path

from scripts.traffic_analysis import canary_runner

SUCCESS_COMMAND = """
import json
import os
from pathlib import Path
root = Path(os.environ['CODEX_TRAFFIC_CANARY_RUN_DIR'])
(root / 'outputs').mkdir(parents=True)
(root / 'outputs' / 'fast-canary.json').write_text(json.dumps({
    'evidence_scope': 'fast_canary',
    'codex_version': os.environ['CODEX_TRAFFIC_CANARY_VERSION'],
    'passed': True,
    'cleanup_complete': True,
    'privacy_scan_passed': True,
}))
"""


def _config(tmp_path: Path, *, command: list[str] | None = None) -> Path:
    value = {
        "schema_version": 1,
        "command": command or [sys.executable, "-c", SUCCESS_COMMAND],
        "version_command": [sys.executable, "-c", "print('codex-cli 0.151.0')"],
        "state_file": str(tmp_path / "state" / "state.json"),
        "lock_file": str(tmp_path / "state" / "runner.lock"),
        "run_root": str(tmp_path / "runs"),
        "interval_seconds": 600,
        "timeout_seconds": 60,
        "cwd": str(tmp_path),
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(value))
    return path


def test_canary_runs_on_version_change_and_advances_only_success_state(tmp_path: Path) -> None:
    config = _config(tmp_path)

    first = canary_runner.run_canary(config, now=1_000_000)
    second = canary_runner.run_canary(config, now=1_000_100)

    assert first["status"] == "passed"
    assert first["trigger"] == "version_changed"
    assert first["state_updated"] is True
    state = json.loads((tmp_path / "state" / "state.json").read_text())
    assert state["last_success_version"] == "codex-cli 0.151.0"
    assert state["evidence_scope"] == "fast_canary"
    assert second["status"] == "not_due"
    assert second["ran"] is False


def test_canary_runs_when_weekly_interval_elapsed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = tmp_path / "state" / "state.json"
    state.parent.mkdir()
    state.write_text(
        json.dumps(
            {
                "last_success_version": "codex-cli 0.151.0",
                "last_success_at": 1_000_000,
            }
        )
    )

    result = canary_runner.run_canary(config, now=1_000_601)

    assert result["status"] == "passed"
    assert result["trigger"] == "interval_elapsed"


def test_canary_failure_and_invalid_result_do_not_advance_state(tmp_path: Path) -> None:
    config = _config(tmp_path, command=[sys.executable, "-c", "raise SystemExit(3)"])

    failed = canary_runner.run_canary(config, now=1_000_000)

    assert failed["status"] == "failed"
    assert failed["exit_code"] == 3
    assert not (tmp_path / "state" / "state.json").exists()

    config = _config(tmp_path, command=[sys.executable, "-c", "print('no result')"])
    invalid = canary_runner.run_canary(config, now=1_000_001, force=True)

    assert invalid["status"] == "invalid_result"
    assert invalid["state_updated"] is False
    assert not (tmp_path / "state" / "state.json").exists()


def test_canary_does_not_trust_success_marker_when_sensitive_artifacts_remain(tmp_path: Path) -> None:
    command = (
        SUCCESS_COMMAND
        + """
sensitive = root / 'raw-h2' / 'data'
sensitive.mkdir(parents=True)
(sensitive / 'encryption.key').write_text('secret')
"""
    )
    config = _config(tmp_path, command=[sys.executable, "-c", command])

    result = canary_runner.run_canary(config, now=1_000_000)

    assert result["status"] == "invalid_result"
    assert result["state_updated"] is False
    assert not (tmp_path / "state" / "state.json").exists()


def test_canary_dry_run_and_overlap_never_start_command(tmp_path: Path) -> None:
    config = _config(tmp_path)

    dry_run = canary_runner.run_canary(config, now=1_000_000, dry_run=True)

    assert dry_run == {
        "status": "due",
        "trigger": "version_changed",
        "ran": False,
        "passed": None,
        "state_updated": False,
        "codex_version": "codex-cli 0.151.0",
    }
    assert not (tmp_path / "runs").exists()

    lock_path = tmp_path / "state" / "runner.lock"
    lock_path.parent.mkdir(exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        overlap = canary_runner.run_canary(config, now=1_000_000)
    finally:
        os.close(descriptor)

    assert overlap["status"] == "overlap"
    assert overlap["state_updated"] is False
    assert not (tmp_path / "runs").exists()
