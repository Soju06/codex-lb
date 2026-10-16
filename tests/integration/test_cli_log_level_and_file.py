"""``--log-level`` / ``--log-file`` on the real server command.

Each test launches ``python -m app.cli`` as a subprocess with its own data
directory and SQLite database, because log configuration is applied at process
start and cannot be observed through an in-process ASGI client.
"""

from __future__ import annotations

import contextlib
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from websockets.sync.client import connect as ws_connect

pytestmark = pytest.mark.integration

_SECRET = "s3cretvalue" * 4
# URL userinfo is redacted at every level; bearer and key=value secrets only at WARNING+.
_SECRET_URL = f"https://operator:{_SECRET}@example.invalid/x"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _server_env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["CODEX_LB_DATA_DIR"] = str(tmp_path / "data")
    env["CODEX_LB_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'codex-lb.db'}"
    env["CODEX_LB_METRICS_ENABLED"] = "false"
    return env


def _run_server(
    tmp_path: Path, *flags: str, extra_env: dict[str, str] | None = None, ws_probe: str | None = None
) -> tuple[str, int]:
    """Start the server, request /health with a credentialed URL in the query, stop it; return (output, port)."""

    port = _free_port()
    # A file, not a pipe: nobody drains a pipe while the server runs, and a full
    # pipe buffer blocks the server's log writes.
    output_path = tmp_path / "server-output.txt"
    with output_path.open("w") as output:
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.cli", "--host", "127.0.0.1", "--port", str(port), *flags],
            env={**_server_env(tmp_path), **(extra_env or {})},
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
            time.sleep(0.25)
        if extra_env and extra_env.get("CODEX_LB_METRICS_ENABLED") == "true":
            # The metrics server starts during lifespan; wait until it answers so
            # the probed request happens after any logging it could reset.
            metrics_url = f"http://127.0.0.1:{extra_env['CODEX_LB_METRICS_PORT']}/metrics"
            while True:
                try:
                    if httpx.get(metrics_url, timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert time.monotonic() < deadline, "metrics server did not start"
                time.sleep(0.25)
        httpx.get(f"http://127.0.0.1:{port}/health?next={_SECRET_URL}", timeout=5)
        if ws_probe is not None:
            # A real WebSocket handshake with a distinctive header value: uvicorn's
            # DEBUG protocol tracing would echo request headers into the log.
            with contextlib.suppress(Exception):
                with ws_connect(
                    f"ws://127.0.0.1:{port}/backend-api/codex/responses",
                    additional_headers={"X-Codex-LB-Test-Probe": ws_probe},
                    open_timeout=5,
                ):
                    pass
    finally:
        proc.terminate()
        proc.wait(timeout=60)
    return output_path.read_text(), port


def test_debug_level_and_log_file_capture_app_and_access_logs(tmp_path: Path) -> None:
    log_file = tmp_path / "volume" / "logs" / "codex-lb.log"
    assert not log_file.parent.exists()

    stream_output, _ = _run_server(tmp_path, "--log-level", "debug", "--log-file", str(log_file))

    assert log_file.exists(), "parent directory was not created or file not written"
    file_text = log_file.read_text(encoding="utf-8")
    assert "DEBUG:" in file_text
    # Library loggers stay at INFO; only codex-lb and uvicorn go to DEBUG. Match the
    # logger-name column, not any text: a shutdown traceback can name aiosqlite.py.
    assert not re.search(r"^\S+ DEBUG:\s+aiosqlite\b", file_text, re.MULTILINE)
    assert "Application startup complete" in file_text
    assert f"Logging configured level=debug file={log_file} rotation=50MiBx10" in file_text
    assert 'GET /health HTTP/1.1" 200' in file_text
    assert _SECRET not in file_text
    assert _SECRET not in stream_output
    file_access = next(line for line in file_text.splitlines() if "next=" in line)
    stream_access = next(line for line in stream_output.splitlines() if "next=" in line)
    assert file_access.split(" ", 1)[1].strip() == stream_access.split(" ", 1)[1].strip()
    # The file carries the same records as the stream, without color codes.
    assert "\x1b[" not in file_text
    assert "Application startup complete" in stream_output


def test_defaults_write_no_file_and_no_debug(tmp_path: Path) -> None:
    stream_output, _ = _run_server(tmp_path)

    assert "Application startup complete" in stream_output
    assert "Logging configured level=info file=none rotation=none" in stream_output
    assert "DEBUG:" not in stream_output
    assert not list(tmp_path.rglob("*.log"))


def test_unknown_log_level_is_a_usage_error(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "app.cli", "--log-level", "verbose"],
        env=_server_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 2
    assert "invalid choice: 'verbose'" in completed.stderr


def test_unwritable_log_file_fails_startup(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    completed = subprocess.run(
        [sys.executable, "-m", "app.cli", "--port", str(_free_port()), "--log-file", str(blocker / "codex-lb.log")],
        env=_server_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode != 0
    assert "not-a-directory" in completed.stderr


def test_metrics_server_does_not_silence_access_logs(tmp_path: Path) -> None:
    pytest.importorskip("prometheus_client")  # optional ``metrics`` extra; CI installs --dev only
    log_file = tmp_path / "logs" / "codex-lb.log"
    metrics_env = {"CODEX_LB_METRICS_ENABLED": "true", "CODEX_LB_METRICS_PORT": str(_free_port())}

    stream_output, _ = _run_server(tmp_path, "--log-file", str(log_file), extra_env=metrics_env)

    file_text = log_file.read_text(encoding="utf-8")
    assert "GET /health?next=" in stream_output
    assert "GET /health?next=" in file_text
    assert _SECRET not in file_text


_PROBE = "probe-" + "marker" * 6


def test_debug_level_excludes_uvicorn_protocol_tracing(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "codex-lb.log"

    stream_output, _ = _run_server(tmp_path, "--log-level", "debug", "--log-file", str(log_file), ws_probe=_PROBE)

    file_text = log_file.read_text(encoding="utf-8")
    assert "DEBUG:" in file_text  # app.* DEBUG still flows
    for text in (file_text, stream_output):
        assert _PROBE not in text
        assert not re.search(r"^\S+ DEBUG:\s+uvicorn\.", text, re.MULTILINE)


def test_startup_line_survives_stricter_levels(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "codex-lb.log"

    stream_output, _ = _run_server(tmp_path, "--log-level", "warning", "--log-file", str(log_file))

    expected = f"Logging configured level=warning file={log_file} rotation=50MiBx10"
    assert expected in stream_output
    assert expected in log_file.read_text(encoding="utf-8")


_ROTATION_SCRIPT = """
import logging, logging.config, sys
from pathlib import Path
import app.core.runtime_logging as runtime_logging
runtime_logging.LOG_FILE_MAX_BYTES = 2048
runtime_logging.LOG_FILE_BACKUP_COUNT = 50
logging.config.dictConfig(runtime_logging.build_log_config("info", Path(sys.argv[1])))
app_logger = logging.getLogger("app.rotation_probe")
access_logger = logging.getLogger("uvicorn.access")
for i in range(400):
    app_logger.info("app-record-%04d", i)
    access_logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", f"/access-record-{i:04d}", "1.1", 200)
"""


def test_single_handler_owns_rotation(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "codex-lb.log"
    completed = subprocess.run(
        [sys.executable, "-c", _ROTATION_SCRIPT, str(log_file)],
        env=_server_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr

    current = log_file.read_text(encoding="utf-8")
    # The newest app record and the newest access record land in the same current file.
    assert "app-record-0399" in current
    assert "/access-record-0399" in current
    # One rotation sequence: every record appears exactly once across the files, in order.
    rotated = sorted(log_file.parent.glob("codex-lb.log.*"), key=lambda p: int(p.suffix[1:]), reverse=True)
    assert rotated, "expected rollovers at a 2 KiB limit"
    combined = "".join(p.read_text(encoding="utf-8") for p in [*rotated, log_file])
    app_ids = re.findall(r"app-record-(\d{4})", combined)
    access_ids = re.findall(r"/access-record-(\d{4})", combined)
    assert app_ids == sorted(app_ids) and len(app_ids) == len(set(app_ids))
    assert access_ids == sorted(access_ids) and len(access_ids) == len(set(access_ids))
