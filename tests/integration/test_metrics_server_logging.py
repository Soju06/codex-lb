"""The metrics server must not reconfigure the process's logging.

Runs ``python -m app.cli`` as a subprocess, because uvicorn applies logging
configuration process-wide at startup and an in-process ASGI client never
starts the metrics server.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration

_SECRET = "s3cretvalue" * 4
# URL userinfo is redacted by codex-lb's access formatter; uvicorn's stock formatter prints it.
_SECRET_URL = f"https://operator:{_SECRET}@example.invalid/x"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_ok(url: str, proc: subprocess.Popen[bytes], output_path: Path, deadline: float) -> None:
    while True:
        assert proc.poll() is None, f"server exited early: {output_path.read_text()}"
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        assert time.monotonic() < deadline, f"{url} did not answer"
        time.sleep(0.25)


def test_metrics_server_keeps_access_logs_and_redaction(tmp_path: Path) -> None:
    pytest.importorskip("prometheus_client")  # optional ``metrics`` extra; CI installs --dev only
    port = _free_port()
    metrics_port = _free_port()
    env = {
        **os.environ,
        "CODEX_LB_DATA_DIR": str(tmp_path / "data"),
        "CODEX_LB_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'codex-lb.db'}",
        "CODEX_LB_METRICS_ENABLED": "true",
        "CODEX_LB_METRICS_PORT": str(metrics_port),
    }
    # A file, not a pipe: nobody drains a pipe while the server runs.
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
        _wait_for_ok(f"http://127.0.0.1:{port}/health", proc, output_path, deadline)
        # The metrics server starts during lifespan; once it answers, any logging
        # reconfiguration it would do has already happened.
        _wait_for_ok(f"http://127.0.0.1:{metrics_port}/metrics", proc, output_path, deadline)
        httpx.get(f"http://127.0.0.1:{port}/health?next={_SECRET_URL}", timeout=5)
    finally:
        proc.terminate()
        proc.wait(timeout=60)

    output = output_path.read_text()
    assert "GET /health?next=" in output
    assert _SECRET not in output
    # Scrapes share the process-wide uvicorn.access logger, so they are logged too.
    assert "GET /metrics" in output
