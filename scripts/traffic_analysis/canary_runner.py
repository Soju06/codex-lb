"""Run a configured fast traffic canary on Codex version drift or interval."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.traffic_analysis.artifacts import atomic_write_json, read_json
from scripts.traffic_analysis.privacy_scan import scan_tree

DEFAULT_INTERVAL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_TIMEOUT_SECONDS = 60 * 60
RESULT_RELATIVE_PATH = "outputs/fast-canary.json"


class CanaryConfigurationError(ValueError):
    """The operator-owned runner configuration is invalid."""


@dataclass(frozen=True)
class CanaryConfig:
    command: tuple[str, ...]
    version_command: tuple[str, ...]
    state_file: Path
    lock_file: Path
    run_root: Path
    interval_seconds: int
    timeout_seconds: int
    cwd: Path


def _require_argv(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise CanaryConfigurationError(f"{label} must be a non-empty string argv")
    return tuple(value)


def load_config(path: str | Path) -> CanaryConfig:
    source = Path(path)
    try:
        raw = read_json(source)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise CanaryConfigurationError("canary configuration cannot be loaded") from exc
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
        raise CanaryConfigurationError("canary configuration schema_version must be 1")
    command = _require_argv(raw.get("command"), "command")
    version_command = _require_argv(raw.get("version_command", ["codex", "--version"]), "version_command")
    paths: dict[str, Path] = {}
    for name in ("state_file", "lock_file", "run_root"):
        value = raw.get(name)
        if not isinstance(value, str) or not value:
            raise CanaryConfigurationError(f"{name} must be an absolute path")
        resolved = Path(value)
        if not resolved.is_absolute():
            raise CanaryConfigurationError(f"{name} must be an absolute path")
        paths[name] = resolved
    interval = raw.get("interval_seconds", DEFAULT_INTERVAL_SECONDS)
    timeout = raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(interval, int) or interval < 60:
        raise CanaryConfigurationError("interval_seconds must be an integer of at least 60")
    if not isinstance(timeout, int) or timeout < 60:
        raise CanaryConfigurationError("timeout_seconds must be an integer of at least 60")
    cwd_value = raw.get("cwd")
    cwd = Path(cwd_value) if isinstance(cwd_value, str) and cwd_value else source.parent
    if not cwd.is_absolute():
        raise CanaryConfigurationError("cwd must be an absolute path")
    return CanaryConfig(
        command=command,
        version_command=version_command,
        state_file=paths["state_file"],
        lock_file=paths["lock_file"],
        run_root=paths["run_root"],
        interval_seconds=interval,
        timeout_seconds=timeout,
        cwd=cwd,
    )


def _read_state(path: Path) -> dict[str, Any]:
    try:
        value = read_json(path)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"invalid": True}
    return dict(value) if isinstance(value, Mapping) else {"invalid": True}


def _detect_version(argv: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CanaryConfigurationError("Codex version command failed") from exc
    version = (completed.stdout or completed.stderr).strip().splitlines()
    if completed.returncode != 0 or not version or not version[0].strip():
        raise CanaryConfigurationError("Codex version command returned no usable version")
    return version[0].strip()


def _trigger(state: Mapping[str, Any], version: str, now: float, interval_seconds: int) -> str | None:
    if state.get("last_success_version") != version:
        return "version_changed"
    timestamp = state.get("last_success_at")
    if not isinstance(timestamp, (int, float)) or now - float(timestamp) >= interval_seconds:
        return "interval_elapsed"
    return None


def _version_slug(version: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", version).strip("-.")
    return slug[:80] or "unknown"


def _new_run_dir(root: Path, version: str, now: float) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.fromtimestamp(now, UTC).strftime("%Y%m%dT%H%M%SZ")
    base = root / f"{timestamp}-fast-canary-{_version_slug(version)}"
    for suffix in ("", f"-{os.getpid()}"):
        candidate = Path(f"{base}{suffix}")
        try:
            candidate.mkdir(mode=0o700)
            return candidate
        except FileExistsError:
            continue
    raise CanaryConfigurationError("cannot allocate a unique canary run directory")


def _validate_result(run_dir: Path, version: str) -> dict[str, Any]:
    result_path = run_dir / RESULT_RELATIVE_PATH
    try:
        value = read_json(result_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise CanaryConfigurationError("fast canary result is missing or invalid") from exc
    if not isinstance(value, Mapping):
        raise CanaryConfigurationError("fast canary result must be a JSON object")
    required = {
        "evidence_scope": "fast_canary",
        "codex_version": version,
        "passed": True,
        "cleanup_complete": True,
        "privacy_scan_passed": True,
    }
    if any(value.get(key) != expected for key, expected in required.items()):
        raise CanaryConfigurationError("fast canary result did not satisfy the success contract")
    sensitive_directories = [run_dir / "raw-h2" / "data", run_dir / "raw-h2" / "logs"]
    failure_root = run_dir / "failure-matrix"
    if failure_root.is_dir() and not failure_root.is_symlink():
        for scenario in failure_root.iterdir():
            if scenario.is_dir() and not scenario.is_symlink():
                sensitive_directories.extend((scenario / "data", scenario / "logs"))
    if any(path.exists() or path.is_symlink() for path in sensitive_directories):
        raise CanaryConfigurationError("sensitive canary working files remain after cleanup")
    privacy_result = scan_tree(run_dir)
    if privacy_result.get("passed") is not True:
        raise CanaryConfigurationError("retained canary evidence failed independent privacy validation")
    return dict(value)


def run_canary(
    config_path: str | Path,
    *,
    now: float | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    config = load_config(config_path)
    current_time = time.time() if now is None else float(now)
    lock_file = config.lock_file
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "overlap", "ran": False, "passed": None, "state_updated": False}
        version = _detect_version(config.version_command)
        state = _read_state(config.state_file)
        trigger = "forced" if force else _trigger(state, version, current_time, config.interval_seconds)
        if trigger is None:
            return {
                "status": "not_due",
                "ran": False,
                "passed": None,
                "state_updated": False,
                "codex_version": version,
            }
        if dry_run:
            return {
                "status": "due",
                "trigger": trigger,
                "ran": False,
                "passed": None,
                "state_updated": False,
                "codex_version": version,
            }

        run_dir = _new_run_dir(config.run_root, version, current_time)
        environment = os.environ.copy()
        environment.update(
            {
                "CODEX_TRAFFIC_CANARY_RUN_DIR": str(run_dir),
                "CODEX_TRAFFIC_CANARY_TRIGGER": trigger,
                "CODEX_TRAFFIC_CANARY_VERSION": version,
            }
        )
        try:
            completed = subprocess.run(
                config.command,
                cwd=config.cwd,
                env=environment,
                check=False,
                timeout=config.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "status": "command_error",
                "trigger": trigger,
                "ran": True,
                "passed": False,
                "state_updated": False,
                "codex_version": version,
                "run_dir": str(run_dir),
                "error": type(exc).__name__,
            }
        if completed.returncode != 0:
            return {
                "status": "failed",
                "trigger": trigger,
                "ran": True,
                "passed": False,
                "state_updated": False,
                "codex_version": version,
                "run_dir": str(run_dir),
                "exit_code": completed.returncode,
            }
        try:
            result = _validate_result(run_dir, version)
        except CanaryConfigurationError as exc:
            return {
                "status": "invalid_result",
                "trigger": trigger,
                "ran": True,
                "passed": False,
                "state_updated": False,
                "codex_version": version,
                "run_dir": str(run_dir),
                "error": str(exc),
            }
        state_value = {
            "schema_version": 1,
            "last_success_at": current_time,
            "last_success_iso": datetime.fromtimestamp(current_time, UTC).isoformat(),
            "last_success_version": version,
            "last_success_trigger": trigger,
            "last_success_run_dir": str(run_dir),
            "evidence_scope": result["evidence_scope"],
        }
        atomic_write_json(config.state_file, state_value, mode=0o600)
        return {
            "status": "passed",
            "trigger": trigger,
            "ran": True,
            "passed": True,
            "state_updated": True,
            "codex_version": version,
            "run_dir": str(run_dir),
        }
    finally:
        os.close(descriptor)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_canary(args.config, dry_run=args.dry_run, force=args.force)
    except CanaryConfigurationError as exc:
        result = {
            "status": "configuration_error",
            "ran": False,
            "passed": False,
            "state_updated": False,
            "error": str(exc),
        }
    print(json.dumps(result, sort_keys=True))
    return 2 if result.get("passed") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
