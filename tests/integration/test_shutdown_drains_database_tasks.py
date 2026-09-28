"""Prompt SIGTERM after startup must not cancel background tasks mid-DB-work (#2505).

Launches the real server command on file-backed SQLite and sends SIGTERM the
moment ``/health`` first answers: the window in which startup scheduler ticks
and the leader-lease keeper are still inside database work. Before the fix,
unmodified main showed pool errors in about half of such runs, so several runs
make a regression near-certain to show while a fixed build stays clean.
"""

from __future__ import annotations

import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration

_RUNS = 6
_POOL_ERRORS = ("Exception closing connection", "Exception during reset")
# Well under the lease-release deadline (10s) that an unreleased lease waited out.
_MAX_EXIT_SECONDS = 5.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _shutdown_right_after_startup(tmp_path: Path, run: int) -> tuple[str, float, int]:
    """Start the server, SIGTERM it at first healthy /health; return (output, exit_seconds, lease_rows)."""

    run_dir = tmp_path / f"run-{run}"
    run_dir.mkdir()
    db_path = run_dir / "db.sqlite"
    env = dict(os.environ)
    env.update(
        {
            "CODEX_LB_DATA_DIR": str(run_dir / "data"),
            "CODEX_LB_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
            "CODEX_LB_METRICS_ENABLED": "false",
        }
    )
    port = _free_port()
    output_path = run_dir / "server-output.txt"
    # A file, not a pipe: an undrained pipe blocks the server's log writes.
    with output_path.open("w") as output:
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.cli", "--host", "127.0.0.1", "--port", str(port)],
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 90
        while True:
            assert proc.poll() is None, f"server exited early: {output_path.read_text()}"
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline, "server did not become healthy"
            time.sleep(0.05)
        signalled_at = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=60)
        exit_seconds = time.monotonic() - signalled_at
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    with sqlite3.connect(db_path) as db:
        lease_rows = db.execute("SELECT COUNT(*) FROM scheduler_leader").fetchone()[0]
    return output_path.read_text(), exit_seconds, lease_rows


def test_prompt_sigterm_after_startup_leaves_no_pool_errors_or_lease(tmp_path: Path) -> None:
    failures: list[str] = []
    for run in range(_RUNS):
        output, exit_seconds, lease_rows = _shutdown_right_after_startup(tmp_path, run)
        errors = [marker for marker in _POOL_ERRORS if marker in output]
        if errors or lease_rows or exit_seconds > _MAX_EXIT_SECONDS:
            failures.append(
                f"run {run}: pool_errors={errors} scheduler_leader_rows={lease_rows} exit_s={exit_seconds:.2f}"
            )

    assert not failures, "\n".join(failures)
