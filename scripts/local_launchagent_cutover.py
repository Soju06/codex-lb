#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import time
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path


class CutoverError(RuntimeError):
    pass


def wait_for_health(url: str, timeout_seconds: float, *, sleep: Callable[[float], None] = time.sleep) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except OSError:
            pass
        sleep(1)
    return False


def run_cutover(
    *,
    plist: Path,
    rollback_plist: Path,
    label: str,
    health_url: str,
    startup_timeout_seconds: float,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    health_probe: Callable[[str, float], bool] = wait_for_health,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if os.environ.get("XPC_SERVICE_NAME"):
        raise CutoverError("Run this one-shot cutover from a terminal, not as a KeepAlive LaunchAgent")

    with plist.open("rb") as handle:
        config = plistlib.load(handle)
    command = config.get("ProgramArguments")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
        raise CutoverError("LaunchAgent ProgramArguments are missing or invalid")
    executable = Path(command[0])
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise CutoverError(f"Candidate executable is not runnable: {executable}")

    run([str(executable), "--help"], check=True, capture_output=True, text=True, timeout=30)
    domain = label.rsplit("/", 1)[0]

    def bootstrap_with_launchd_release_retry() -> None:
        # macOS can keep a booted-out label reserved briefly.  An immediate
        # bootstrap then returns EIO (5), and an immediate rollback hits the
        # same race.  Retry the same, idempotent bootstrap while launchd
        # finishes releasing the label before treating it as a real failure.
        last_error: subprocess.CalledProcessError | None = None
        for attempt in range(10):
            try:
                run(["launchctl", "bootstrap", domain, str(plist)], check=True, capture_output=True, text=True)
                return
            except subprocess.CalledProcessError as exc:
                last_error = exc
                if attempt == 9:
                    raise
                sleep(0.5)
        if last_error is not None:  # pragma: no cover - loop always returns or raises
            raise last_error

    try:
        run(["launchctl", "bootout", label], check=True, capture_output=True, text=True)
        bootstrap_with_launchd_release_retry()
        if health_probe(health_url, startup_timeout_seconds):
            return
        raise CutoverError(f"Candidate did not become healthy within {startup_timeout_seconds:g}s")
    except BaseException as exc:
        run(["launchctl", "bootout", label], check=False, capture_output=True, text=True)
        shutil.copy2(rollback_plist, plist)
        bootstrap_with_launchd_release_retry()
        if not health_probe(health_url, startup_timeout_seconds):
            raise CutoverError("Candidate failed and rollback did not become healthy") from exc
        if isinstance(exc, CutoverError):
            raise
        raise CutoverError(f"Candidate activation failed; rollback is healthy: {exc}") from exc


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Switch a local codex-lb LaunchAgent with verified rollback.")
    parser.add_argument("--plist", type=Path, required=True)
    parser.add_argument("--rollback-plist", type=Path, required=True)
    parser.add_argument("--label", required=True, help="Full launchd label, for example gui/501/local.codex-lb")
    parser.add_argument("--health-url", default="http://127.0.0.1:2455/health")
    parser.add_argument("--startup-timeout-seconds", type=float, default=90)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    run_cutover(
        plist=args.plist,
        rollback_plist=args.rollback_plist,
        label=args.label,
        health_url=args.health_url,
        startup_timeout_seconds=args.startup_timeout_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
