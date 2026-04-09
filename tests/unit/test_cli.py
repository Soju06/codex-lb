from __future__ import annotations

import logging
import sys
from typing import Any

import pytest

from app import cli
from app.cli_runtime import RuntimeMetadata
from app.core.runtime_logging import UtcDefaultFormatter

pytestmark = pytest.mark.unit


def test_main_passes_timestamped_log_config(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(sys, "argv", ["codex-lb-cinamon"])
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main()

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    log_config = kwargs["log_config"]
    assert isinstance(log_config, dict)
    formatters = log_config["formatters"]
    assert formatters["default"]["fmt"].startswith("%(asctime)s ")
    assert formatters["access"]["fmt"].startswith("%(asctime)s ")


def test_parse_args_defaults_to_serve_for_legacy_flags(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["codex-lb-cinamon", "--port", "2555"])

    args = cli._parse_args()

    assert args.command == "serve"
    assert args.port == 2555


def test_start_command_reports_background_runtime(monkeypatch, capsys, tmp_path):
    runtime = RuntimeMetadata(pid=4242, host="127.0.0.1", port=2455, log_file=str(tmp_path / "server.log"))
    pid_file = tmp_path / "server.pid"
    log_file = tmp_path / "server.log"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex-lb-cinamon",
            "start",
            "--pid-file",
            str(pid_file),
            "--log-file",
            str(log_file),
        ],
    )
    monkeypatch.setattr(cli, "start_background_server", lambda *args, **kwargs: runtime)

    cli.main()

    output = capsys.readouterr().out
    assert "Started codex-lb-cinamon in background" in output
    assert str(pid_file) in output
    assert str(log_file) in output


def test_status_command_reports_running_server(monkeypatch, capsys, tmp_path):
    runtime = RuntimeMetadata(pid=4242, host="127.0.0.1", port=2455, log_file=str(tmp_path / "server.log"))
    pid_file = tmp_path / "server.pid"

    monkeypatch.setattr(sys, "argv", ["codex-lb-cinamon", "status", "--pid-file", str(pid_file)])
    monkeypatch.setattr(cli, "load_running_metadata", lambda path: (runtime, False))

    cli.main()

    output = capsys.readouterr().out
    assert "background server is running" in output
    assert str(pid_file) in output


def test_shutdown_command_stops_running_server(monkeypatch, capsys, tmp_path):
    runtime = RuntimeMetadata(pid=4242, host="127.0.0.1", port=2455, log_file=str(tmp_path / "server.log"))
    pid_file = tmp_path / "server.pid"

    monkeypatch.setattr(sys, "argv", ["codex-lb-cinamon", "shutdown", "--pid-file", str(pid_file)])
    monkeypatch.setattr(cli, "load_running_metadata", lambda path: (runtime, False))
    monkeypatch.setattr(cli, "shutdown_background_server", lambda path, timeout_seconds: runtime)

    cli.main()

    output = capsys.readouterr().out
    assert "Stopped codex-lb-cinamon background server" in output


def test_utc_default_formatter_formats_without_converter_binding_error():
    formatter = UtcDefaultFormatter(
        fmt="%(asctime)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        use_colors=None,
    )
    record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.created = 0.0

    assert formatter.format(record) == "1970-01-01T00:00:00Z hello"
