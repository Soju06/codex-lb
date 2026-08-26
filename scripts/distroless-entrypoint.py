from __future__ import annotations

import os
import signal
import subprocess
import sys
from types import FrameType


class SupervisionState:
    """Mutable because signal handlers and the main wait path share evolving child state."""

    __slots__ = ("child", "first_signal", "pending_signals")

    def __init__(self) -> None:
        self.first_signal: int | None = None
        self.pending_signals: list[int] = []
        self.child: subprocess.Popen[bytes] | None = None


def _signal_child(child: subprocess.Popen[bytes], signum: int) -> None:
    try:
        os.kill(child.pid, signum)
    except ProcessLookupError:
        return


def _migration_status(first_signal: int | None, child_returncode: int) -> int:
    if first_signal is not None:
        return 128 + first_signal
    if child_returncode < 0:
        return 128 + abs(child_returncode)
    return child_returncode


def run_migration() -> int:
    state = SupervisionState()

    def forward_signal(signum: int, _frame: FrameType | None) -> None:
        if state.first_signal is None:
            state.first_signal = signum
        if state.child is None:
            state.pending_signals.append(signum)
            return
        _signal_child(state.child, signum)

    previous_sigterm = signal.signal(signal.SIGTERM, forward_signal)
    previous_sigint = signal.signal(signal.SIGINT, forward_signal)
    try:
        child = subprocess.Popen([sys.executable, "-m", "app.db.migrate", "upgrade"])
        state.child = child
        for signum in state.pending_signals:
            _signal_child(child, signum)
        state.pending_signals.clear()
        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
        state.child = None
        child_returncode = child.wait()
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)
    return _migration_status(state.first_signal, child_returncode)


def main() -> int:
    migration_status = run_migration()
    if migration_status != 0:
        return migration_status
    os.environ["CODEX_LB_DATABASE_MIGRATE_ON_STARTUP"] = "false"
    # app.cli, not `fastapi run`: the CLI wires ws_max_size (websocket ingress
    # budget) and timeout_keep_alive into uvicorn, matching docker-entrypoint.sh.
    os.execv(sys.executable, [sys.executable, "-m", "app.cli", "--host", "0.0.0.0", "--port", "2455"])


if __name__ == "__main__":
    raise SystemExit(main())
