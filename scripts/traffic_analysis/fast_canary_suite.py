"""Run and finalize the controlled fast traffic-parity canary suite."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.traffic_analysis.artifacts import atomic_write_json, atomic_write_text, file_digest
from scripts.traffic_analysis.failure_matrix import (
    DEFAULT_BASELINE,
    build_failure_matrix_gate,
)
from scripts.traffic_analysis.failure_matrix import (
    render_markdown as render_failure_markdown,
)
from scripts.traffic_analysis.privacy_scan import scan_tree

_RAW_RESULT_NAMES = ("client_a_reference", "client_a", "client_c", "strict")


class FastCanaryError(RuntimeError):
    """The suite could not produce a valid privacy-safe success result."""


@dataclass(frozen=True)
class FastCanaryConfig:
    repo: Path
    run_dir: Path
    approved_run_root: Path
    raw_runner: Path
    failure_runner: Path
    auth_json: Path
    trigger: str
    codex_version: str

    @property
    def native_manifest(self) -> Path:
        return self.repo / "Cargo.toml"

    @property
    def native_bin_dir(self) -> Path:
        return self.repo / "target" / "release"

    @property
    def failure_baseline(self) -> Path:
        return self.repo / "scripts" / "traffic_analysis" / "baselines" / DEFAULT_BASELINE.name


CommandRunner = Callable[[Sequence[str], Path, Mapping[str, str] | None], None]


def _resolved_directory(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise FastCanaryError(f"{label} is missing") from exc
    if not resolved.is_dir():
        raise FastCanaryError(f"{label} must be a directory")
    return resolved


def validate_config(config: FastCanaryConfig) -> FastCanaryConfig:
    repo = _resolved_directory(config.repo, "repository")
    approved_root = _resolved_directory(config.approved_run_root, "approved run root")
    run_dir = _resolved_directory(config.run_dir, "canary run directory")
    if run_dir.parent != approved_root:
        raise FastCanaryError("canary run directory must be a direct child of the approved root")
    for label, source in (
        ("raw runner", config.raw_runner),
        ("failure runner", config.failure_runner),
        ("isolated auth.json", config.auth_json),
        ("native helper manifest", config.native_manifest),
        ("failure baseline", config.failure_baseline),
    ):
        if not source.is_file():
            raise FastCanaryError(f"{label} is missing")
    for label, runner in (("raw runner", config.raw_runner), ("failure runner", config.failure_runner)):
        if not os.access(runner, os.X_OK):
            raise FastCanaryError(f"{label} must be executable")
    if stat.S_IMODE(config.auth_json.stat().st_mode) != 0o600:
        raise FastCanaryError("isolated auth.json must have mode 600")
    if not config.trigger.strip() or not config.codex_version.strip():
        raise FastCanaryError("trigger and Codex version must be non-empty")
    return FastCanaryConfig(
        repo=repo,
        run_dir=run_dir,
        approved_run_root=approved_root,
        raw_runner=config.raw_runner.resolve(strict=True),
        failure_runner=config.failure_runner.resolve(strict=True),
        auth_json=config.auth_json.resolve(strict=True),
        trigger=config.trigger,
        codex_version=config.codex_version,
    )


def _run_checked(argv: Sequence[str], cwd: Path, environment: Mapping[str, str] | None = None) -> None:
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(environment) if environment is not None else None,
            check=False,
        )
    except OSError as exc:
        raise FastCanaryError(f"cannot start suite command: {Path(argv[0]).name}") from exc
    if completed.returncode != 0:
        raise FastCanaryError(f"suite command failed: {Path(argv[0]).name} (exit {completed.returncode})")


def _sensitive_directories(run_dir: Path, *, include_captures: bool = False) -> list[Path]:
    targets = [run_dir / "raw-h2" / "data", run_dir / "raw-h2" / "logs"]
    if include_captures:
        targets.append(run_dir / "raw-h2" / "captures")
    failure_root = run_dir / "failure-matrix"
    if failure_root.is_dir() and not failure_root.is_symlink():
        for scenario in sorted(failure_root.iterdir()):
            if scenario.is_dir() and not scenario.is_symlink():
                targets.extend((scenario / "data", scenario / "logs"))
    return targets


def cleanup_sensitive(run_dir: Path) -> None:
    for target in _sensitive_directories(run_dir):
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)


def _assert_cleanup_complete(run_dir: Path) -> None:
    remaining = [str(path.relative_to(run_dir)) for path in _sensitive_directories(run_dir) if path.exists()]
    if remaining:
        raise FastCanaryError("sensitive canary working files remain after cleanup")


def _raw_results_pass(run_dir: Path) -> bool:
    output = run_dir / "raw-h2" / "outputs"
    try:
        return all((output / f"{name}.rc").read_text(encoding="utf-8").strip() == "0" for name in _RAW_RESULT_NAMES)
    except OSError:
        return False


def _relative_evidence(run_dir: Path, relative_path: str) -> dict[str, Any]:
    return {"path": relative_path, **file_digest(run_dir / relative_path)}


def _write_success_result(config: FastCanaryConfig) -> dict[str, Any]:
    result = {
        "schema_version": 1,
        "evidence_scope": "fast_canary",
        "codex_version": config.codex_version,
        "trigger": config.trigger,
        "passed": True,
        "cleanup_complete": True,
        "privacy_scan_passed": True,
        "full_composite_attestation": False,
        "evidence": [
            _relative_evidence(config.run_dir, "raw-h2/outputs/http2-profile.json"),
            _relative_evidence(config.run_dir, "outputs/failure-matrix.json"),
            _relative_evidence(config.run_dir, "outputs/privacy-scan.json"),
        ],
    }
    atomic_write_json(config.run_dir / "outputs" / "fast-canary.json", result, mode=0o600)
    return result


def run_suite(
    config: FastCanaryConfig,
    *,
    command_runner: CommandRunner = _run_checked,
) -> dict[str, Any]:
    config = validate_config(config)
    output_dir = config.run_dir / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        command_runner(
            (
                "cargo",
                "build",
                "--locked",
                "--release",
                "--package",
                "codex-lb-egress-worker",
                "--bin",
                "codex-lb-native-egress",
                "--manifest-path",
                str(config.native_manifest),
            ),
            config.repo,
            None,
        )
        command_runner((str(config.raw_runner), str(config.run_dir / "raw-h2")), config.repo, None)
        if not _raw_results_pass(config.run_dir):
            raise FastCanaryError("raw HTTP/2 canary did not satisfy every required result")

        failure_environment = os.environ.copy()
        failure_environment["CODEX_LB_TOKEN_REFRESH_INTERVAL_DAYS"] = "365"
        failure_environment["PATH"] = f"{config.native_bin_dir}{os.pathsep}{failure_environment.get('PATH', '')}"
        command_runner(
            (str(config.failure_runner), str(config.run_dir / "failure-matrix")),
            config.repo,
            failure_environment,
        )
        failure_result = build_failure_matrix_gate(
            config.run_dir / "failure-matrix",
            baseline=config.failure_baseline,
        )
        atomic_write_text(output_dir / "failure-matrix.md", render_failure_markdown(failure_result))
        atomic_write_json(output_dir / "failure-matrix.json", failure_result)
        if failure_result.get("passed") is not True:
            raise FastCanaryError("controlled failure matrix gate failed")

        cleanup_sensitive(config.run_dir)
        _assert_cleanup_complete(config.run_dir)
        privacy_result = scan_tree(config.run_dir)
        atomic_write_json(output_dir / "privacy-scan.json", privacy_result)
        if privacy_result.get("passed") is not True:
            raise FastCanaryError("retained canary evidence failed privacy scan")
        return _write_success_result(config)
    except BaseException:
        cleanup_sensitive(config.run_dir)
        for target in _sensitive_directories(config.run_dir, include_captures=True):
            if target.is_symlink():
                target.unlink()
            elif target.exists():
                shutil.rmtree(target)
        raise
    finally:
        cleanup_sensitive(config.run_dir)


def _environment_value(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise FastCanaryError(f"required environment value is missing: {name}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--approved-run-root", type=Path, required=True)
    parser.add_argument("--raw-runner", type=Path, required=True)
    parser.add_argument("--failure-runner", type=Path, required=True)
    parser.add_argument("--auth-json", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = FastCanaryConfig(
            repo=args.repo,
            run_dir=Path(_environment_value("CODEX_TRAFFIC_CANARY_RUN_DIR")),
            approved_run_root=args.approved_run_root,
            raw_runner=args.raw_runner,
            failure_runner=args.failure_runner,
            auth_json=args.auth_json,
            trigger=_environment_value("CODEX_TRAFFIC_CANARY_TRIGGER"),
            codex_version=_environment_value("CODEX_TRAFFIC_CANARY_VERSION"),
        )
        result = run_suite(config)
    except (FastCanaryError, OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"status": "passed", "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
