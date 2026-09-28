"""Shutdown must not cancel background tasks mid-DB-work (#2505).

Two tests against the real server command on file-backed SQLite:

- A deterministic barrier: a ``sitecustomize`` injected into the server holds
  the cache-invalidation poller's own read open (suspending only that task via
  ``await_only``, so the event loop keeps running) until the poller's stop is
  requested, then lets it finish 0.5s later. Immediate cancellation interrupts
  the held read; a graceful stop lets it complete.
- A multi-run smoke test sending SIGTERM at the first healthy ``/health``: the
  only path here that exercises the leader-lease keeper, which starts at
  shutdown. Probabilistic by nature (about half of runs hit the race on
  unmodified main), so it complements rather than replaces the barrier test.
"""

from __future__ import annotations

import os
import signal
import socket
import sqlite3
import subprocess
import sys
import textwrap
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


# Injected into the server process via PYTHONPATH. It runs inside SQLAlchemy's
# greenlet on the event-loop thread, so it must never block: await_only suspends
# just the task that issued the query, leaving shutdown free to proceed.
_BARRIER_SITECUSTOMIZE = textwrap.dedent(
    """
    import asyncio
    import os
    import time
    from pathlib import Path

    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from sqlalchemy.util import await_only

    _DIR = Path(os.environ["CODEX_LB_TEST_BARRIER_DIR"])
    _engaged = []

    @event.listens_for(Engine, "before_cursor_execute")
    def _hold_poller_read(conn, cursor, statement, parameters, context, executemany):
        if _engaged or "FROM cache_invalidation" not in statement:
            return
        task = asyncio.current_task()
        if task is None or getattr(task.get_coro(), "__qualname__", "") != "CacheInvalidationPoller._run":
            return  # prime() runs the same read at startup, outside the poller task
        from app.core.cache.invalidation import get_cache_invalidation_poller

        poller = get_cache_invalidation_poller()
        if poller is None:
            return
        _engaged.append(True)
        (_DIR / "held").touch()
        try:
            deadline = time.monotonic() + 60
            while not poller._stop.is_set() and time.monotonic() < deadline:
                await_only(asyncio.sleep(0.02))
            await_only(asyncio.sleep(0.5))
        except BaseException as exc:
            (_DIR / "cancelled").write_text(type(exc).__name__)
            raise
        (_DIR / "completed").touch()
    """
)


def test_in_flight_poller_read_completes_during_shutdown(tmp_path: Path) -> None:
    hook_dir = tmp_path / "hook"
    hook_dir.mkdir()
    (hook_dir / "sitecustomize.py").write_text(_BARRIER_SITECUSTOMIZE)
    barrier_dir = tmp_path / "barrier"
    barrier_dir.mkdir()
    db_path = tmp_path / "db.sqlite"
    env = dict(os.environ)
    env.update(
        {
            "CODEX_LB_DATA_DIR": str(tmp_path / "data"),
            "CODEX_LB_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
            "CODEX_LB_METRICS_ENABLED": "false",
            "CODEX_LB_TEST_BARRIER_DIR": str(barrier_dir),
            "PYTHONPATH": os.pathsep.join(filter(None, [str(hook_dir), env.get("PYTHONPATH")])),
        }
    )
    port = _free_port()
    output_path = tmp_path / "server-output.txt"
    with output_path.open("w") as output:
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.cli", "--host", "127.0.0.1", "--port", str(port)],
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 90
        while not (barrier_dir / "held").exists():
            assert proc.poll() is None, f"server exited early: {output_path.read_text()}"
            assert time.monotonic() < deadline, "poller read never reached the barrier"
            time.sleep(0.05)
        signalled_at = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=60)
        exit_seconds = time.monotonic() - signalled_at
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    output = output_path.read_text()
    cancelled = barrier_dir / "cancelled"
    assert not cancelled.exists(), f"in-flight poller read was cancelled: {cancelled.read_text()}"
    assert (barrier_dir / "completed").exists(), "in-flight poller read did not complete"
    assert not [marker for marker in _POOL_ERRORS if marker in output]
    assert "still busy" not in output
    assert exit_seconds < _MAX_EXIT_SECONDS
