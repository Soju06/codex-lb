from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.core.auth import parse_auth_json
from app.core.auth.refresh import TOKEN_REFRESH_INTERVAL_DAYS, should_refresh
from app.core.utils.time import to_utc_naive
from scripts.traffic_analysis import fast_canary_suite

_STALE_AUTH_JSON: dict[str, Any] = {
    "OPENAI_API_KEY": None,
    "auth_mode": "chatgpt",
    "tokens": {
        "id_token": "header.payload.signature",
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "account_id": "acct-canary",
    },
    "last_refresh": "2026-01-01T00:00:00.000000Z",
}


def _config(tmp_path: Path) -> fast_canary_suite.FastCanaryConfig:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest = repo / "Cargo.toml"
    manifest.write_text("[package]\nname='test'\n")
    baseline = repo / "scripts" / "traffic_analysis" / "baselines" / "failure-profile-v1.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("{}\n")
    approved_root = tmp_path / "runs"
    approved_root.mkdir()
    run_dir = approved_root / "run-1"
    run_dir.mkdir()
    raw_runner = tmp_path / "raw-runner"
    raw_runner.write_text("runner")
    raw_runner.chmod(0o700)
    failure_runner = tmp_path / "failure-runner"
    failure_runner.write_text("runner")
    failure_runner.chmod(0o700)
    auth_json = tmp_path / "auth.json"
    auth_json.write_text(json.dumps(_STALE_AUTH_JSON))
    auth_json.chmod(0o600)
    return fast_canary_suite.FastCanaryConfig(
        repo=repo,
        run_dir=run_dir,
        approved_run_root=approved_root,
        raw_runner=raw_runner,
        failure_runner=failure_runner,
        auth_json=auth_json,
        trigger="version_changed",
        codex_version="codex-cli 0.151.0",
    )


def _passing_gate() -> dict[str, Any]:
    return {
        "passed": True,
        "available": True,
        "required_scenarios": [],
        "scenarios": {},
        "evidence": [],
    }


def _write_raw_result(run_dir: Path, *, secret: str | None = None) -> None:
    output = run_dir / "raw-h2" / "outputs"
    output.mkdir(parents=True)
    for name in fast_canary_suite._RAW_RESULT_NAMES:
        (output / f"{name}.rc").write_text("0\n")
    (output / "http2-profile.json").write_text('{"passed":true}\n')
    capture = run_dir / "raw-h2" / "captures" / "path_a.jsonl"
    capture.parent.mkdir()
    capture.write_text(secret or "{}\n")
    data = run_dir / "raw-h2" / "data"
    data.mkdir()
    (data / "encryption.key").write_text("sensitive")
    logs = run_dir / "raw-h2" / "logs"
    logs.mkdir()
    (logs / "bootstrap.log").write_text("sensitive")


def _write_failure_working_files(run_dir: Path) -> None:
    scenario = run_dir / "failure-matrix" / "success"
    for name in ("data", "logs"):
        target = scenario / name
        target.mkdir(parents=True)
        (target / "sensitive").write_text("sensitive")


def test_suite_writes_marker_only_after_cleanup_and_privacy_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    seen_failure_environment: dict[str, str] = {}

    def command_runner(argv: Sequence[str], _cwd: Path, environment: Mapping[str, str] | None) -> None:
        if argv[0] == str(config.raw_runner):
            _write_raw_result(config.run_dir)
        elif argv[0] == str(config.failure_runner):
            _write_failure_working_files(config.run_dir)
            seen_failure_environment.update(environment or {})

    monkeypatch.setattr(fast_canary_suite, "build_failure_matrix_gate", lambda *_args, **_kwargs: _passing_gate())

    result = fast_canary_suite.run_suite(config, command_runner=command_runner)

    marker = json.loads((config.run_dir / "outputs" / "fast-canary.json").read_text())
    assert result == marker
    assert marker["evidence_scope"] == "fast_canary"
    assert marker["codex_version"] == "codex-cli 0.151.0"
    assert marker["cleanup_complete"] is True
    assert marker["privacy_scan_passed"] is True
    assert marker["full_composite_attestation"] is False
    assert stat.S_IMODE((config.run_dir / "outputs" / "fast-canary.json").stat().st_mode) == 0o600
    assert not (config.run_dir / "raw-h2" / "data").exists()
    assert not (config.run_dir / "failure-matrix" / "success" / "logs").exists()
    assert seen_failure_environment["PATH"].startswith(str(config.repo / "target/release"))
    # The removed CODEX_LB_TOKEN_REFRESH_INTERVAL_DAYS field is not resurrected
    # as an env pin: proactive refresh is suppressed by the credential stamp.
    assert "CODEX_LB_TOKEN_REFRESH_INTERVAL_DAYS" not in seen_failure_environment
    stamped = parse_auth_json(config.auth_json.read_bytes())
    assert stamped.last_refresh_at is not None
    assert should_refresh(to_utc_naive(stamped.last_refresh_at)) is False


def test_suite_failure_cleans_sensitive_files_without_marker(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)

    def command_runner(argv: Sequence[str], _cwd: Path, _environment: Mapping[str, str] | None) -> None:
        if argv[0] == str(config.raw_runner):
            _write_raw_result(config.run_dir)
        elif argv[0] == str(config.failure_runner):
            _write_failure_working_files(config.run_dir)
            raise fast_canary_suite.FastCanaryError("controlled failure")

    with pytest.raises(fast_canary_suite.FastCanaryError, match="controlled failure"):
        fast_canary_suite.run_suite(config, command_runner=command_runner)

    assert not (config.run_dir / "raw-h2" / "data").exists()
    assert not (config.run_dir / "failure-matrix" / "success" / "logs").exists()
    assert not (config.run_dir / "outputs" / "fast-canary.json").exists()


def test_suite_privacy_failure_retains_safe_report_without_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)

    def command_runner(argv: Sequence[str], _cwd: Path, _environment: Mapping[str, str] | None) -> None:
        if argv[0] == str(config.raw_runner):
            _write_raw_result(config.run_dir, secret='{"authorization":"Bearer privatecredential123"}\n')
        elif argv[0] == str(config.failure_runner):
            _write_failure_working_files(config.run_dir)

    monkeypatch.setattr(fast_canary_suite, "build_failure_matrix_gate", lambda *_args, **_kwargs: _passing_gate())

    with pytest.raises(fast_canary_suite.FastCanaryError, match="privacy scan"):
        fast_canary_suite.run_suite(config, command_runner=command_runner)

    report = json.loads((config.run_dir / "outputs" / "privacy-scan.json").read_text())
    assert report["passed"] is False
    assert "privatecredential123" not in str(report)
    assert not (config.run_dir / "raw-h2" / "captures").exists()
    assert not (config.run_dir / "outputs" / "fast-canary.json").exists()


def test_suite_rejects_non_child_run_directory_and_loose_auth_mode(tmp_path: Path) -> None:
    config = _config(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_config = replace(config, run_dir=outside)

    with pytest.raises(fast_canary_suite.FastCanaryError, match="direct child"):
        fast_canary_suite.validate_config(outside_config)

    config.auth_json.chmod(0o644)
    with pytest.raises(fast_canary_suite.FastCanaryError, match="mode 600"):
        fast_canary_suite.validate_config(config)


def test_suite_main_fails_without_runner_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    for name in (
        "CODEX_TRAFFIC_CANARY_RUN_DIR",
        "CODEX_TRAFFIC_CANARY_TRIGGER",
        "CODEX_TRAFFIC_CANARY_VERSION",
    ):
        monkeypatch.delenv(name, raising=False)

    exit_code = fast_canary_suite.main(
        [
            "--repo",
            str(config.repo),
            "--approved-run-root",
            str(config.approved_run_root),
            "--raw-runner",
            str(config.raw_runner),
            "--failure-runner",
            str(config.failure_runner),
            "--auth-json",
            str(config.auth_json),
        ]
    )

    assert exit_code == 2
    assert os.environ.get("CODEX_TRAFFIC_CANARY_RUN_DIR") is None


def test_auth_stamp_keeps_a_stale_canary_credential_inside_the_refresh_window(tmp_path: Path) -> None:
    """A run must never exchange the isolated single-use refresh token upstream.

    The imported account inherits ``last_refresh`` from this file, and the
    proactive-refresh window is the fixed ``TOKEN_REFRESH_INTERVAL_DAYS``
    constant now that ``CODEX_LB_TOKEN_REFRESH_INTERVAL_DAYS`` is gone.
    """
    config = _config(tmp_path)
    stale = parse_auth_json(config.auth_json.read_bytes())
    assert stale.last_refresh_at is not None
    assert should_refresh(to_utc_naive(stale.last_refresh_at)) is True

    stamp = fast_canary_suite.stamp_isolated_auth_refresh(config.auth_json)

    document = json.loads(config.auth_json.read_text())
    assert document["last_refresh"] == stamp
    assert "lastRefreshAt" not in document
    assert document["tokens"] == _STALE_AUTH_JSON["tokens"]
    assert document["auth_mode"] == "chatgpt"
    assert stat.S_IMODE(config.auth_json.stat().st_mode) == 0o600

    stamped = parse_auth_json(config.auth_json.read_bytes())
    assert stamped.last_refresh_at is not None
    assert should_refresh(to_utc_naive(stamped.last_refresh_at)) is False
    # The stamp is an instant, not a widened window: a run that somehow lasted
    # longer than the fixed interval would still refresh.
    assert (
        should_refresh(
            to_utc_naive(stamped.last_refresh_at),
            datetime.now(UTC) + timedelta(days=TOKEN_REFRESH_INTERVAL_DAYS + 1),
        )
        is True
    )


def test_auth_stamp_rejects_unusable_auth_json(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.auth_json.write_text("not json")
    with pytest.raises(fast_canary_suite.FastCanaryError, match="readable JSON"):
        fast_canary_suite.stamp_isolated_auth_refresh(config.auth_json)

    config.auth_json.write_text("[]")
    with pytest.raises(fast_canary_suite.FastCanaryError, match="JSON object"):
        fast_canary_suite.stamp_isolated_auth_refresh(config.auth_json)


def test_auth_stamp_covers_every_timestamp_alias_the_importer_accepts(tmp_path: Path) -> None:
    """``AuthFile`` prefers ``lastRefreshAt``; a stale alias must not survive."""
    config = _config(tmp_path)
    camel = {key: value for key, value in _STALE_AUTH_JSON.items() if key != "last_refresh"}
    camel["lastRefreshAt"] = _STALE_AUTH_JSON["last_refresh"]
    config.auth_json.write_text(json.dumps(camel))

    stamp = fast_canary_suite.stamp_isolated_auth_refresh(config.auth_json)

    document = json.loads(config.auth_json.read_text())
    assert document["lastRefreshAt"] == stamp
    assert "last_refresh" not in document
    stamped = parse_auth_json(config.auth_json.read_bytes())
    assert stamped.last_refresh_at is not None
    assert should_refresh(to_utc_naive(stamped.last_refresh_at)) is False

    # Both keys present: the preferred alias must not keep a stale value.
    both = dict(camel)
    both["last_refresh"] = _STALE_AUTH_JSON["last_refresh"]
    config.auth_json.write_text(json.dumps(both))
    stamp = fast_canary_suite.stamp_isolated_auth_refresh(config.auth_json)
    document = json.loads(config.auth_json.read_text())
    assert document["lastRefreshAt"] == stamp
    assert document["last_refresh"] == stamp
