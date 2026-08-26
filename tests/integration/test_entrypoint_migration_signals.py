from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, TypedDict, assert_never

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.name != "posix", reason="requires POSIX process signals"),
]

ROOT: Final = Path(__file__).parents[2]
PROBE: Final = ROOT / "tests/fixtures/entrypoint_probe.py"
SHELL_ENTRYPOINT: Final = ROOT / "scripts/docker-entrypoint.sh"
DISTROLESS_ENTRYPOINT: Final = ROOT / "scripts/distroless-entrypoint.py"
MIGRATION_ARGV: Final = ["-m", "app.db.migrate", "upgrade"]
APP_ARGV: Final = ["-m", "app.cli", "--host", "0.0.0.0", "--port", "2455"]
WAIT_SECONDS: Final = 5.0
POLL_SECONDS: Final = 0.01


class Launcher(StrEnum):
    SHELL = "shell"
    DISTROLESS = "distroless"


class ProcessMarker(TypedDict):
    pid: int
    ppid: int
    argv: list[str]


class AppMarker(ProcessMarker):
    migration_env: str | None


class SignalMarker(TypedDict):
    signum: int


@dataclass(frozen=True, slots=True)
class LaunchSettings:
    migration_mode: str = "success"
    incoming_migration: str | None = None
    migration_exit_code: int = 23
    migration_start_delay_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class RunningLauncher:
    process: subprocess.Popen[bytes]
    markers: Path


def _start_launcher(
    tmp_path: Path,
    launcher: Launcher,
    settings: LaunchSettings = LaunchSettings(),
) -> RunningLauncher:
    markers = tmp_path / "markers"
    markers.mkdir()
    env = os.environ.copy()
    env.update(
        ENTRYPOINT_PROBE_DIR=str(markers),
        ENTRYPOINT_PROBE_TIMEOUT_SECONDS=str(WAIT_SECONDS),
        ENTRYPOINT_PROBE_MIGRATION_MODE=settings.migration_mode,
        ENTRYPOINT_PROBE_MIGRATION_EXIT_CODE=str(settings.migration_exit_code),
        ENTRYPOINT_PROBE_MIGRATION_START_DELAY_SECONDS=str(settings.migration_start_delay_seconds),
    )
    if settings.incoming_migration is None:
        env.pop("CODEX_LB_DATABASE_MIGRATE_ON_STARTUP", None)
    else:
        env["CODEX_LB_DATABASE_MIGRATE_ON_STARTUP"] = settings.incoming_migration

    match launcher:
        case Launcher.SHELL:
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            (bin_dir / "python").symlink_to(PROBE)
            env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
            command = ["/bin/sh", str(SHELL_ENTRYPOINT)]
        case Launcher.DISTROLESS:
            command = [str(PROBE), str(DISTROLESS_ENTRYPOINT)]
        case unreachable:
            assert_never(unreachable)

    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return RunningLauncher(process=process, markers=markers)


def _wait_for_marker(path: Path) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(POLL_SECONDS)
    raise AssertionError(f"timed out waiting for marker {path.name}")


def _read_process_marker(path: Path) -> ProcessMarker:
    _wait_for_marker(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _read_app_marker(path: Path) -> AppMarker:
    _wait_for_marker(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _wait_for_signal_marker(running: RunningLauncher, migration_pid: int) -> SignalMarker:
    path = running.markers / "migration-signal.json"
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(POLL_SECONDS)
    raise AssertionError(
        "migration child did not record the launcher signal; "
        f"launcher_pid={running.process.pid}, launcher_returncode={running.process.poll()}, "
        f"migration_pid={migration_pid}"
    )


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for_pid_exit(pid: int) -> bool:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if not _pid_exists(pid):
            return True
        time.sleep(POLL_SECONDS)
    return False


def _signal_group(process_group_id: int, signum: signal.Signals) -> None:
    try:
        os.killpg(process_group_id, signum)
    except ProcessLookupError:
        return


def _cleanup(running: RunningLauncher) -> None:
    process_group_id = running.process.pid
    _signal_group(process_group_id, signal.SIGTERM)
    try:
        running.process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        _signal_group(process_group_id, signal.SIGKILL)
        running.process.wait(timeout=1.0)
    _signal_group(process_group_id, signal.SIGKILL)
    shutil.rmtree(running.markers.parent)


def _assert_app_handoff(running: RunningLauncher) -> None:
    app = _read_app_marker(running.markers / "app-started.json")
    assert app["argv"] == APP_ARGV
    assert app["migration_env"] == "false"
    assert app["pid"] == running.process.pid
    assert app["ppid"] == os.getpid()


@pytest.mark.parametrize("launcher", list(Launcher), ids=str)
def test_successful_migration_hands_off_to_exact_app_command(
    tmp_path: Path,
    launcher: Launcher,
) -> None:
    running = _start_launcher(tmp_path, launcher)
    try:
        migration = _read_process_marker(running.markers / "migration-started.json")
        assert migration["argv"] == MIGRATION_ARGV
        assert migration["ppid"] == running.process.pid
        _assert_app_handoff(running)
        (running.markers / "release-app").touch()
        assert running.process.wait(timeout=WAIT_SECONDS) == 0
        assert _wait_for_pid_exit(migration["pid"])
    finally:
        _cleanup(running)


def test_shell_false_bypasses_migration_and_execs_app(tmp_path: Path) -> None:
    running = _start_launcher(
        tmp_path,
        Launcher.SHELL,
        LaunchSettings(incoming_migration="false"),
    )
    try:
        _assert_app_handoff(running)
        assert not (running.markers / "migration-started.json").exists()
        (running.markers / "release-app").touch()
        assert running.process.wait(timeout=WAIT_SECONDS) == 0
    finally:
        _cleanup(running)


def test_distroless_false_still_migrates_before_app(tmp_path: Path) -> None:
    running = _start_launcher(
        tmp_path,
        Launcher.DISTROLESS,
        LaunchSettings(incoming_migration="false"),
    )
    try:
        migration = _read_process_marker(running.markers / "migration-started.json")
        assert migration["argv"] == MIGRATION_ARGV
        _assert_app_handoff(running)
        (running.markers / "release-app").touch()
        assert running.process.wait(timeout=WAIT_SECONDS) == 0
        assert _wait_for_pid_exit(migration["pid"])
    finally:
        _cleanup(running)


@pytest.mark.parametrize("launcher", list(Launcher), ids=str)
def test_positive_migration_failure_is_preserved_without_app(
    tmp_path: Path,
    launcher: Launcher,
) -> None:
    settings = LaunchSettings(migration_mode="fail", migration_exit_code=23)
    running = _start_launcher(tmp_path, launcher, settings)
    try:
        migration = _read_process_marker(running.markers / "migration-started.json")
        status = running.process.wait(timeout=WAIT_SECONDS)
        assert status == 23, f"{launcher} lost migration exit code: expected 23, got {status}"
        assert not (running.markers / "app-started.json").exists()
        assert _wait_for_pid_exit(migration["pid"])
    finally:
        _cleanup(running)


@pytest.mark.parametrize("launcher", list(Launcher), ids=str)
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["sigterm", "sigint"])
def test_launcher_forwards_signal_and_reaps_blocked_migration(
    tmp_path: Path,
    launcher: Launcher,
    signum: signal.Signals,
) -> None:
    running = _start_launcher(tmp_path, launcher, LaunchSettings(migration_mode="block"))
    try:
        migration = _read_process_marker(running.markers / "migration-started.json")
        os.kill(running.process.pid, signum)
        received = _wait_for_signal_marker(running, migration["pid"])
        assert received["signum"] == signum
        assert running.process.poll() is None, "launcher exited before migration release"
        assert not (running.markers / "app-started.json").exists()
        (running.markers / "release-migration").touch()
        assert running.process.wait(timeout=WAIT_SECONDS) == 128 + signum
        assert _wait_for_pid_exit(migration["pid"]), f"migration PID {migration['pid']} was not reaped"
    finally:
        _cleanup(running)


@pytest.mark.parametrize("launcher", list(Launcher), ids=str)
def test_cleanup_before_started_marker_removes_spawned_migration(
    tmp_path: Path,
    launcher: Launcher,
) -> None:
    settings = LaunchSettings(migration_mode="block", migration_start_delay_seconds=3.0)
    running = _start_launcher(tmp_path, launcher, settings)
    migration_pid: int | None = None
    try:
        spawned = _read_process_marker(running.markers / "migration-spawned.json")
        migration_pid = spawned["pid"]
        assert not (running.markers / "migration-started.json").exists()
        _cleanup(running)
        assert not _pid_exists(migration_pid), (
            f"migration PID {migration_pid} survived cleanup before migration-started marker"
        )
    finally:
        if migration_pid is not None and _pid_exists(migration_pid):
            os.kill(migration_pid, signal.SIGKILL)
        if running.process.poll() is None:
            running.process.kill()
        running.process.wait(timeout=1.0)
        if migration_pid is not None:
            assert _wait_for_pid_exit(migration_pid), f"migration PID {migration_pid} survived outer cleanup"
        if running.markers.parent.exists():
            shutil.rmtree(running.markers.parent)
