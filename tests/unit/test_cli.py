from __future__ import annotations

import json
import logging
import sqlite3
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from app import cli
from app.core.runtime_logging import UtcDefaultFormatter

pytestmark = pytest.mark.unit


def test_main_passes_timestamped_log_config(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(sys, "argv", ["codex-lb"])
    monkeypatch.setattr(cli, "_load_uvicorn", lambda: SimpleNamespace(run=fake_run))

    cli.main()

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    log_config = kwargs["log_config"]
    assert isinstance(log_config, dict)
    formatters = log_config["formatters"]
    assert formatters["default"]["fmt"].startswith("%(asctime)s ")
    assert formatters["access"]["fmt"].startswith("%(asctime)s ")
    assert kwargs["timeout_keep_alive"] == 7200


def test_main_passes_custom_keep_alive_timeout(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(sys, "argv", ["codex-lb", "--timeout-keep-alive", "900"])
    monkeypatch.setattr(cli, "_load_uvicorn", lambda: SimpleNamespace(run=fake_run))

    cli.main()

    assert captured["kwargs"]["timeout_keep_alive"] == 900


def test_main_reports_invalid_server_port_env(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["codex-lb"])
    monkeypatch.setenv("PORT", "not-a-port")

    with pytest.raises(SystemExit, match="--port/PORT must be an integer"):
        cli.main()


def test_main_reports_invalid_keep_alive_timeout_env(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["codex-lb"])
    monkeypatch.setenv("UVICORN_TIMEOUT_KEEP_ALIVE", "not-a-timeout")

    with pytest.raises(SystemExit, match="--timeout-keep-alive/UVICORN_TIMEOUT_KEEP_ALIVE must be an integer"):
        cli.main()


def test_codex_sessions_retag_refuses_noninteractive_write_without_yes(monkeypatch, tmp_path):
    class NonInteractiveInput:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(sys, "stdin", NonInteractiveInput())

    with pytest.raises(SystemExit, match="--yes"):
        cli.main(
            [
                "codex-sessions",
                "retag",
                "--from",
                "openai",
                "--to",
                "codex-lb",
                "--codex-home",
                str(tmp_path),
            ]
        )


def test_codex_sessions_retag_ignores_invalid_server_port_env(monkeypatch, capsys, tmp_path):
    session_file = tmp_path / "sessions" / "session.jsonl"
    session_file.parent.mkdir(parents=True)
    session_file.write_text(json.dumps({"model_provider": "openai"}) + "\n", encoding="utf-8")
    monkeypatch.setenv("PORT", "not-a-port")
    monkeypatch.setenv("UVICORN_TIMEOUT_KEEP_ALIVE", "not-a-timeout")

    cli.main(
        [
            "codex-sessions",
            "retag",
            "--from",
            "openai",
            "--to",
            "codex-lb",
            "--codex-home",
            str(tmp_path),
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()
    assert "Would update JSONL files: 1" in captured.out
    assert json.loads(session_file.read_text(encoding="utf-8"))["model_provider"] == "openai"


def test_codex_sessions_retag_dry_run_skips_confirmation(capsys, tmp_path):
    session_file = tmp_path / "sessions" / "session.jsonl"
    session_file.parent.mkdir(parents=True)
    session_file.write_text(json.dumps({"model_provider": "openai"}) + "\n", encoding="utf-8")

    cli.main(
        [
            "codex-sessions",
            "retag",
            "--from",
            "openai",
            "--to",
            "codex-lb",
            "--codex-home",
            str(tmp_path),
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()
    assert "Dry run enabled" in captured.out
    assert "Would update JSONL files: 1" in captured.out
    assert json.loads(session_file.read_text(encoding="utf-8"))["model_provider"] == "openai"


def test_codex_sessions_retag_reports_file_access_errors(monkeypatch, tmp_path):
    def fail_retag(**_kwargs):
        raise PermissionError("cannot read session.jsonl")

    monkeypatch.setattr(cli, "retag_codex_sessions", fail_retag)

    with pytest.raises(SystemExit, match="Unable to read or write Codex session files: cannot read session.jsonl"):
        cli.main(
            [
                "codex-sessions",
                "retag",
                "--from",
                "openai",
                "--to",
                "codex-lb",
                "--codex-home",
                str(tmp_path),
                "--dry-run",
            ]
        )


def test_codex_sessions_retag_yes_updates_jsonl_and_sqlite(capsys, tmp_path):
    session_file = tmp_path / "sessions" / "session.jsonl"
    session_file.parent.mkdir(parents=True)
    session_file.write_text(json.dumps({"model_provider": "openai"}) + "\n", encoding="utf-8")
    state_db = tmp_path / "state_5.sqlite"
    with sqlite3.connect(state_db) as conn:
        conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT)")
        conn.execute("INSERT INTO threads (id, model_provider) VALUES ('thread-1', 'openai')")

    cli.main(
        [
            "codex-sessions",
            "retag",
            "--from",
            "openai",
            "--to",
            "codex-lb",
            "--codex-home",
            str(tmp_path),
            "--yes",
        ]
    )

    captured = capsys.readouterr()
    assert "Close Codex/Codex CLI" in captured.err
    assert "Updated JSONL files: 1" in captured.out
    assert "Updated SQLite rows: 1" in captured.out
    assert json.loads(session_file.read_text(encoding="utf-8"))["model_provider"] == "codex-lb"
    with sqlite3.connect(state_db) as conn:
        assert conn.execute("SELECT model_provider FROM threads").fetchone()[0] == "codex-lb"


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
