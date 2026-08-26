#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import runpy
import signal
import sys
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import FrameType
from typing import Final, NewType, TypedDict, assert_never

MIGRATION_ARGS: Final = ("-m", "app.db.migrate", "upgrade")
APP_ARGS: Final = (
    "-m",
    "app.cli",
    "--host",
    "0.0.0.0",
    "--port",
    "2455",
)
DEFAULT_TIMEOUT_SECONDS: Final = 10.0
POLL_INTERVAL_SECONDS: Final = 0.01
TIMEOUT_EXIT_CODE: Final = 124

NonnegativeSeconds = NewType("NonnegativeSeconds", float)


class MigrationMode(StrEnum):
    SUCCESS = "success"
    BLOCK = "block"
    FAIL = "fail"


class ProcessMarker(TypedDict):
    pid: int
    ppid: int
    argv: list[str]


class AppMarker(ProcessMarker):
    migration_env: str | None


class SignalMarker(TypedDict):
    signum: int


@dataclass(frozen=True, slots=True)
class ProbeRuntime:
    root: Path
    timeout_seconds: float
    migration_start_delay_seconds: NonnegativeSeconds


@dataclass(frozen=True, slots=True)
class ProbeUsageError(Exception):
    argv: tuple[str, ...]

    def __str__(self) -> str:
        return f"unsupported entrypoint probe argv: {self.argv!r}"


@dataclass(frozen=True, slots=True)
class ProbeConfigurationError(Exception):
    name: str
    value: str

    def __str__(self) -> str:
        return f"invalid {self.name}: {self.value!r}"


def write_marker(
    root: Path,
    name: str,
    marker: ProcessMarker | AppMarker | SignalMarker,
) -> None:
    root.joinpath(name).write_text(
        json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def parse_nonnegative_seconds(name: str, raw_value: str) -> NonnegativeSeconds:
    value = float(raw_value)
    if not math.isfinite(value) or value < 0:
        raise ProbeConfigurationError(name=name, value=raw_value)
    return NonnegativeSeconds(value)


def wait_for_release(runtime: ProbeRuntime, marker_name: str) -> int:
    deadline = time.monotonic() + runtime.timeout_seconds
    release_marker = runtime.root / marker_name
    while time.monotonic() < deadline:
        if release_marker.exists():
            return 0
        time.sleep(POLL_INTERVAL_SECONDS)
    return TIMEOUT_EXIT_CODE


def run_migration(
    runtime: ProbeRuntime,
    argv: list[str],
    mode: MigrationMode,
) -> int:
    def record_signal(signum: int, _frame: FrameType | None) -> None:
        write_marker(
            runtime.root,
            "migration-signal.json",
            SignalMarker(signum=signum),
        )

    signal.signal(signal.SIGTERM, record_signal)
    signal.signal(signal.SIGINT, record_signal)
    process_marker = ProcessMarker(pid=os.getpid(), ppid=os.getppid(), argv=argv)
    write_marker(runtime.root, "migration-spawned.json", process_marker)
    time.sleep(runtime.migration_start_delay_seconds)
    write_marker(runtime.root, "migration-started.json", process_marker)

    match mode:
        case MigrationMode.SUCCESS:
            return 0
        case MigrationMode.BLOCK:
            return wait_for_release(runtime, "release-migration")
        case MigrationMode.FAIL:
            raw_exit_code = os.environ.get("ENTRYPOINT_PROBE_MIGRATION_EXIT_CODE", "1")
            exit_code = int(raw_exit_code)
            if not 1 <= exit_code <= 255:
                raise ProbeConfigurationError(
                    name="ENTRYPOINT_PROBE_MIGRATION_EXIT_CODE",
                    value=raw_exit_code,
                )
            return exit_code
        case unreachable:
            assert_never(unreachable)


def run_app(runtime: ProbeRuntime, argv: list[str]) -> int:
    write_marker(
        runtime.root,
        "app-started.json",
        AppMarker(
            pid=os.getpid(),
            ppid=os.getppid(),
            argv=argv,
            migration_env=os.environ.get("CODEX_LB_DATABASE_MIGRATE_ON_STARTUP"),
        ),
    )
    return wait_for_release(runtime, "release-app")


def run_supervisor(script_path: Path, probe_path: Path) -> int:
    sys.argv = [str(script_path)]
    sys.executable = str(probe_path)
    runpy.run_path(str(script_path), run_name="__main__")
    return 0


def main() -> int:
    root = Path(os.environ["ENTRYPOINT_PROBE_DIR"])
    timeout_raw = os.environ.get(
        "ENTRYPOINT_PROBE_TIMEOUT_SECONDS",
        str(DEFAULT_TIMEOUT_SECONDS),
    )
    timeout_seconds = float(timeout_raw)
    if timeout_seconds <= 0:
        raise ProbeConfigurationError(
            name="ENTRYPOINT_PROBE_TIMEOUT_SECONDS",
            value=timeout_raw,
        )
    start_delay_raw = os.environ.get("ENTRYPOINT_PROBE_MIGRATION_START_DELAY_SECONDS", "0")
    start_delay_seconds = parse_nonnegative_seconds(
        "ENTRYPOINT_PROBE_MIGRATION_START_DELAY_SECONDS",
        start_delay_raw,
    )
    runtime = ProbeRuntime(
        root=root,
        timeout_seconds=timeout_seconds,
        migration_start_delay_seconds=start_delay_seconds,
    )

    argv = sys.argv[1:]
    if len(argv) == 1 and Path(argv[0]).name == "distroless-entrypoint.py":
        return run_supervisor(Path(argv[0]), Path(sys.argv[0]).resolve())
    if tuple(argv) == MIGRATION_ARGS:
        mode = MigrationMode(os.environ.get("ENTRYPOINT_PROBE_MIGRATION_MODE", "success"))
        return run_migration(runtime, argv, mode)
    if tuple(argv) == APP_ARGS:
        return run_app(runtime, argv)
    raise ProbeUsageError(argv=tuple(argv))


if __name__ == "__main__":
    raise SystemExit(main())
