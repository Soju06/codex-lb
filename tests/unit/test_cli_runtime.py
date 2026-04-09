from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import cli_runtime
from app.cli_runtime import ServeOptions

pytestmark = pytest.mark.unit


def test_build_serve_command_includes_explicit_serve_subcommand() -> None:
    command = cli_runtime.build_serve_command(
        "/tmp/python",
        ServeOptions(host="127.0.0.1", port=2455, ssl_certfile="cert.pem", ssl_keyfile="key.pem"),
    )

    assert command == [
        "/tmp/python",
        "-m",
        "app.cli",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "2455",
        "--ssl-certfile",
        "cert.pem",
        "--ssl-keyfile",
        "key.pem",
    ]


def test_load_running_metadata_removes_stale_pid_file(monkeypatch, tmp_path: Path) -> None:
    pid_file = tmp_path / "server.pid"
    pid_file.write_text(
        json.dumps({"pid": 4242, "host": "127.0.0.1", "port": 2455, "log_file": str(tmp_path / "server.log")}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_runtime, "is_process_running", lambda pid: False)

    metadata, stale = cli_runtime.load_running_metadata(pid_file)

    assert metadata is None
    assert stale is True
    assert not pid_file.exists()


def test_start_background_server_writes_runtime_metadata(monkeypatch, tmp_path: Path) -> None:
    pid_file = tmp_path / "server.pid"
    log_file = tmp_path / "server.log"
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 7777

        @staticmethod
        def poll() -> int | None:
            return None

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(cli_runtime, "load_running_metadata", lambda path: (None, False))
    monkeypatch.setattr(cli_runtime.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli_runtime, "wait_for_server_ready", lambda metadata, timeout_seconds, poll_process: True)

    metadata = cli_runtime.start_background_server(
        ServeOptions(host="127.0.0.1", port=2455, ssl_certfile=None, ssl_keyfile=None),
        pid_file=pid_file,
        log_file=log_file,
    )

    assert metadata.pid == 7777
    assert pid_file.exists()
    saved = json.loads(pid_file.read_text(encoding="utf-8"))
    assert saved["pid"] == 7777
    assert captured["command"] == [
        cli_runtime.sys.executable,
        "-m",
        "app.cli",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "2455",
    ]


def test_shutdown_background_server_terminates_pid_and_cleans_metadata(monkeypatch, tmp_path: Path) -> None:
    pid_file = tmp_path / "server.pid"
    pid_file.write_text(
        json.dumps({"pid": 4242, "host": "127.0.0.1", "port": 2455, "log_file": str(tmp_path / "server.log")}),
        encoding="utf-8",
    )
    calls: list[int] = []
    running_states = iter([True, False])

    monkeypatch.setattr(cli_runtime, "terminate_process", lambda pid: calls.append(pid))
    monkeypatch.setattr(cli_runtime, "is_process_running", lambda pid: next(running_states))

    metadata = cli_runtime.shutdown_background_server(pid_file, timeout_seconds=0.5)

    assert metadata is not None
    assert metadata.pid == 4242
    assert calls == [4242]
    assert not pid_file.exists()
