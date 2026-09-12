from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

import pytest

from scripts.local_launchagent_cutover import CutoverError, run_cutover


def _plists(tmp_path: Path) -> tuple[Path, Path]:
    executable = tmp_path / "codex-lb"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    plist = tmp_path / "local.codex-lb.plist"
    rollback = tmp_path / "rollback.plist"
    with plist.open("wb") as handle:
        plistlib.dump({"ProgramArguments": [str(executable), "--port", "2455"]}, handle)
    with rollback.open("wb") as handle:
        plistlib.dump({"ProgramArguments": [str(executable), "--port", "2455", "--rollback"]}, handle)
    return plist, rollback


def _runner(calls: list[tuple[list[str], bool]]):
    def run(command, *, check, **_kwargs):
        calls.append((list(command), check))
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


def test_cutover_uses_full_startup_window_before_declaring_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
    plist, rollback = _plists(tmp_path)
    calls: list[tuple[list[str], bool]] = []
    observed: list[tuple[str, float]] = []

    def healthy(url: str, timeout: float) -> bool:
        observed.append((url, timeout))
        return True

    run_cutover(
        plist=plist,
        rollback_plist=rollback,
        label="gui/501/local.codex-lb",
        health_url="http://127.0.0.1:2455/health",
        startup_timeout_seconds=90,
        run=_runner(calls),
        health_probe=healthy,
    )

    assert [call[0][1] for call in calls[1:]] == ["bootout", "bootstrap"]
    assert observed == [("http://127.0.0.1:2455/health", 90)]
    assert plist.read_bytes() != rollback.read_bytes()


def test_bootstrap_failure_restores_plist_and_verifies_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
    plist, rollback = _plists(tmp_path)
    calls: list[list[str]] = []

    def run(command, *, check, **_kwargs):
        calls.append(list(command))
        if command[1:2] == ["bootstrap"] and len([item for item in calls if item[1:2] == ["bootstrap"]]) <= 10:
            raise subprocess.CalledProcessError(5, command)
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(CutoverError, match="rollback is healthy"):
        run_cutover(
            plist=plist,
            rollback_plist=rollback,
            label="gui/501/local.codex-lb",
            health_url="http://127.0.0.1:2455/health",
            startup_timeout_seconds=90,
            run=run,
            health_probe=lambda _url, _timeout: True,
            sleep=lambda _seconds: None,
        )

    assert plist.read_bytes() == rollback.read_bytes()
    assert [command[1] for command in calls[1:]].count("bootstrap") == 11


def test_transient_launchd_label_release_race_is_retried_without_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
    plist, rollback = _plists(tmp_path)
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def run(command, *, check, **_kwargs):
        calls.append(list(command))
        if command[1:2] == ["bootstrap"] and len([item for item in calls if item[1:2] == ["bootstrap"]]) == 1:
            raise subprocess.CalledProcessError(5, command)
        return subprocess.CompletedProcess(command, 0, "", "")

    run_cutover(
        plist=plist,
        rollback_plist=rollback,
        label="gui/501/local.codex-lb",
        health_url="http://127.0.0.1:2455/health",
        startup_timeout_seconds=90,
        run=run,
        health_probe=lambda _url, _timeout: True,
        sleep=sleeps.append,
    )

    assert sleeps == [0.5]
    assert [command[1] for command in calls[1:]] == ["bootout", "bootstrap", "bootstrap"]
    assert plist.read_bytes() != rollback.read_bytes()


def test_candidate_health_timeout_restores_healthy_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
    plist, rollback = _plists(tmp_path)
    calls: list[tuple[list[str], bool]] = []
    probes = iter([False, True])

    with pytest.raises(CutoverError, match="did not become healthy"):
        run_cutover(
            plist=plist,
            rollback_plist=rollback,
            label="gui/501/local.codex-lb",
            health_url="http://127.0.0.1:2455/health",
            startup_timeout_seconds=90,
            run=_runner(calls),
            health_probe=lambda _url, _timeout: next(probes),
        )

    assert plist.read_bytes() == rollback.read_bytes()
    assert [call[0][1] for call in calls[1:]] == ["bootout", "bootstrap", "bootout", "bootstrap"]


def test_keepalive_launchagent_execution_is_rejected_before_cutover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plist, rollback = _plists(tmp_path)
    monkeypatch.setenv("XPC_SERVICE_NAME", "local.codex-lb-cutover")
    calls: list[tuple[list[str], bool]] = []

    with pytest.raises(CutoverError, match="one-shot cutover"):
        run_cutover(
            plist=plist,
            rollback_plist=rollback,
            label="gui/501/local.codex-lb",
            health_url="http://127.0.0.1:2455/health",
            startup_timeout_seconds=90,
            run=_runner(calls),
            health_probe=lambda _url, _timeout: True,
        )

    assert calls == []
    assert os.environ["XPC_SERVICE_NAME"] == "local.codex-lb-cutover"


def test_cli_refuses_disruptive_cutover_by_default(tmp_path):
    from scripts.local_launchagent_cutover import main

    with pytest.raises(CutoverError, match="Disruptive cutover disabled"):
        main(
            [
                "--plist",
                str(tmp_path / "missing"),
                "--rollback-plist",
                str(tmp_path / "missing-backup"),
                "--label",
                "gui/501/local.codex-lb",
            ]
        )
