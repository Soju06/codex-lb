from __future__ import annotations

import os
import runpy
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Final, Protocol, runtime_checkable

import pytest

pytestmark = pytest.mark.unit

ENTRYPOINT: Final = Path(__file__).parents[2] / "scripts/distroless-entrypoint.py"
CHILD_PID: Final = 4242

type ParentSignalHandler = Callable[[int, FrameType | None], None]


@runtime_checkable
class MigrationRunner(Protocol):
    def __call__(self) -> int: ...


@dataclass(frozen=True, slots=True)
class WaitObservation:
    si_pid: int
    si_uid: int = 0
    si_signo: int = signal.SIGCHLD
    si_status: int = 0
    si_code: int = os.CLD_EXITED


class SignalHarness:
    __slots__ = ("kill_attempts", "parent_handler", "waitid_calls")

    def __init__(self) -> None:
        self.parent_handler: ParentSignalHandler | None = None
        self.kill_attempts: list[tuple[int, int]] = []
        self.waitid_calls: list[tuple[int, int, int]] = []


class FakeChild:
    pid: int = CHILD_PID

    def __init__(self, harness: SignalHarness) -> None:
        self._harness = harness
        self.returncode: int | None = None

    def wait(self) -> int:
        handler = self._harness.parent_handler
        assert handler is not None
        handler(signal.SIGTERM, None)
        assert self.returncode is None
        self.returncode = 0
        return self.returncode


def test_signal_after_exit_observation_does_not_target_stale_child_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = runpy.run_path(str(ENTRYPOINT), run_name="distroless_entrypoint_under_test")
    run_migration = namespace["run_migration"]
    assert isinstance(run_migration, MigrationRunner)
    harness = SignalHarness()

    def fake_popen(command: list[str]) -> FakeChild:
        assert command == [sys.executable, "-m", "app.db.migrate", "upgrade"]
        return FakeChild(harness)

    def fake_signal(
        _signum: int,
        handler: ParentSignalHandler | signal.Handlers,
    ) -> ParentSignalHandler | signal.Handlers:
        if callable(handler):
            harness.parent_handler = handler
        return signal.SIG_DFL

    def record_kill(pid: int, signum: int) -> None:
        harness.kill_attempts.append((pid, signum))

    def observe_exit(idtype: int, pid: int, options: int) -> WaitObservation:
        harness.waitid_calls.append((idtype, pid, options))
        return WaitObservation(si_pid=pid)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(signal, "signal", fake_signal)
    monkeypatch.setattr(os, "kill", record_kill)
    monkeypatch.setattr(os, "waitid", observe_exit)

    status = run_migration()

    assert status == 143
    assert harness.kill_attempts == [], f"stale os.kill attempts after child exit observation: {harness.kill_attempts}"
