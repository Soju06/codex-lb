from __future__ import annotations

import builtins
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
    monkeypatch.setattr(cli, "_run_server", fake_run)

    cli.main()

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    log_config = kwargs["log_config"]
    assert isinstance(log_config, dict)
    formatters = log_config["formatters"]
    assert formatters["default"]["fmt"].startswith("%(asctime)s ")
    assert formatters["access"]["fmt"].startswith("%(asctime)s ")
    assert kwargs["timeout_keep_alive"] == 7200
    assert kwargs["ws_max_size"] == 128 * 1024 * 1024
    assert "workers" not in kwargs
    assert kwargs["proxy_headers"] is False


def test_main_pins_one_worker_when_web_concurrency_requests_more(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConfig:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            captured["workers"] = kwargs["workers"]
            self.workers = kwargs["workers"]

        def load_app(self) -> None:
            captured["loaded"] = True

    class FakeServer:
        started = True

        def __init__(self, _config: FakeConfig, *, drain_timeout_seconds: float) -> None:
            captured["drain_timeout_seconds"] = drain_timeout_seconds

        def run(self) -> None:
            captured["ran"] = True

    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    monkeypatch.setattr(cli, "_load_uvicorn", lambda: SimpleNamespace(Config=FakeConfig))
    monkeypatch.setattr(cli, "_load_graceful_drain_server", lambda: FakeServer)
    monkeypatch.setattr(cli, "_load_shutdown_drain_timeout_seconds", lambda: 17)

    cli.main([])

    assert captured == {
        "workers": 1,
        "loaded": True,
        "drain_timeout_seconds": 17,
        "ran": True,
    }


def test_main_validates_selected_port_before_loading_uvicorn(monkeypatch):
    def fail_load_uvicorn():
        pytest.fail("Uvicorn must not load when the selected port conflicts with the metrics port")

    monkeypatch.setenv("PORT", "2455")
    monkeypatch.setenv("CODEX_LB_METRICS_PORT", "9090")
    monkeypatch.setattr(cli, "_load_uvicorn", fail_load_uvicorn)

    with pytest.raises(ValueError, match="metrics_port must not match the main application port \\(9090\\)"):
        cli.main(["--port", "9090"])


def test_main_passes_custom_keep_alive_timeout(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(sys, "argv", ["codex-lb", "--timeout-keep-alive", "900"])
    monkeypatch.setattr(cli, "_run_server", fake_run)

    cli.main()

    assert captured["kwargs"]["timeout_keep_alive"] == 900


def test_main_passes_custom_ws_max_size_flag(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(sys, "argv", ["codex-lb", "--ws-max-size", "33554432"])
    monkeypatch.setattr(cli, "_run_server", fake_run)

    cli.main()

    assert captured["kwargs"]["ws_max_size"] == 33554432


def test_main_reads_ws_max_size_from_env(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(sys, "argv", ["codex-lb"])
    monkeypatch.setenv("UVICORN_WS_MAX_SIZE", "67108864")
    monkeypatch.setattr(cli, "_run_server", fake_run)

    cli.main()

    assert captured["kwargs"]["ws_max_size"] == 67108864


def test_main_ws_max_size_flag_overrides_env(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(sys, "argv", ["codex-lb", "--ws-max-size", "33554432"])
    monkeypatch.setenv("UVICORN_WS_MAX_SIZE", "67108864")
    monkeypatch.setattr(cli, "_run_server", fake_run)

    cli.main()

    assert captured["kwargs"]["ws_max_size"] == 33554432


def test_main_reports_invalid_ws_max_size_env(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["codex-lb"])
    monkeypatch.setenv("UVICORN_WS_MAX_SIZE", "not-a-size")

    with pytest.raises(SystemExit, match="--ws-max-size/UVICORN_WS_MAX_SIZE must be an integer"):
        cli.main()


def test_main_reports_non_positive_ws_max_size(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["codex-lb", "--ws-max-size", "0"])

    with pytest.raises(SystemExit, match="--ws-max-size/UVICORN_WS_MAX_SIZE must be positive"):
        cli.main()


@pytest.mark.parametrize("source", ["flag", "env"])
def test_main_reports_invalid_server_port_before_loading_uvicorn(monkeypatch, source):
    def fail_run_server(*_args, **_kwargs):
        pytest.fail("Uvicorn must not load for a non-integer server port")

    if source == "flag":
        monkeypatch.setenv("PORT", "2455")
        argv = ["--port", "not-a-port"]
    else:
        monkeypatch.setenv("PORT", "not-a-port")
        argv = []
    monkeypatch.setattr(cli, "_run_server", fail_run_server)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(argv)

    assert str(exc_info.value) == ("--port/PORT must be an integer between 0 and 65535 inclusive, got 'not-a-port'.")


@pytest.mark.parametrize("source", ["flag", "env"])
@pytest.mark.parametrize("raw_port", ["-1", "65536", "70000"])
def test_main_rejects_out_of_range_server_port_before_loading_uvicorn(monkeypatch, source, raw_port):
    def fail_run_server(*_args, **_kwargs):
        pytest.fail("Uvicorn must not load for an out-of-range server port")

    if source == "flag":
        monkeypatch.setenv("PORT", "2455")
        argv = ["--port", raw_port]
    else:
        monkeypatch.setenv("PORT", raw_port)
        argv = []
    monkeypatch.setattr(cli, "_run_server", fail_run_server)

    with pytest.raises(SystemExit, match=r"--port/PORT must be between 0 and 65535 inclusive"):
        cli.main(argv)


@pytest.mark.parametrize("source", ["flag", "env"])
@pytest.mark.parametrize("raw_port", ["0", "65535"])
def test_main_forwards_server_port_boundaries(monkeypatch, source, raw_port):
    captured: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        captured["kwargs"] = kwargs

    if source == "flag":
        monkeypatch.setenv("PORT", "70000")
        argv = ["--port", raw_port]
    else:
        monkeypatch.setenv("PORT", raw_port)
        argv = []
    monkeypatch.setattr(cli, "_run_server", fake_run)

    cli.main(argv)

    assert captured["kwargs"]["port"] == int(raw_port)


def test_run_server_uses_graceful_server_and_shared_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeConfig:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured["config_args"] = args
            captured["config_kwargs"] = kwargs
            self.workers = 1

        def load_app(self) -> None:
            captured["loaded"] = True

    class FakeServer:
        started = True

        def __init__(self, config: FakeConfig, *, drain_timeout_seconds: float) -> None:
            captured["config"] = config
            captured["drain_timeout_seconds"] = drain_timeout_seconds

        def run(self) -> None:
            captured["ran"] = True

    monkeypatch.setattr(cli, "_load_uvicorn", lambda: SimpleNamespace(Config=FakeConfig))
    monkeypatch.setattr(cli, "_load_graceful_drain_server", lambda: FakeServer)
    monkeypatch.setattr(cli, "_load_shutdown_drain_timeout_seconds", lambda: 17)

    cli._run_server("app.main:app", host="127.0.0.1", port=2455)

    from app.core.http_protocol_httptools import UpgradeTolerantHttpToolsProtocol

    assert captured["config_args"] == ("app.main:app",)
    assert captured["config_kwargs"] == {
        "host": "127.0.0.1",
        "port": 2455,
        "workers": 1,
        "http": UpgradeTolerantHttpToolsProtocol,
        "timeout_graceful_shutdown": 17,
    }
    assert captured["drain_timeout_seconds"] == 17
    assert captured["loaded"] is True
    assert captured["ran"] is True


def test_load_http_protocol_class_falls_back_to_h11_without_httptools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.http_protocol import UpgradeTolerantH11Protocol

    real_import = builtins.__import__

    def fail_httptools_import(name: str, *args: Any, **kwargs: Any) -> object:
        if name in {"httptools", "app.core.http_protocol_httptools"}:
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "app.core.http_protocol_httptools", raising=False)
    monkeypatch.setattr(builtins, "__import__", fail_httptools_import)

    assert cli._load_http_protocol_class() is UpgradeTolerantH11Protocol


def test_run_server_pins_one_worker_despite_ambient_web_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeConfig:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            workers = kwargs["workers"]
            assert isinstance(workers, int)
            captured["workers"] = workers
            self.workers = workers

        def load_app(self) -> None:
            captured["loaded"] = True

    class FakeServer:
        started = True

        def __init__(self, _config: FakeConfig, *, drain_timeout_seconds: float) -> None:
            assert drain_timeout_seconds == 17

        def run(self) -> None:
            captured["server_ran"] = True

    monkeypatch.setenv("WEB_CONCURRENCY", "3")
    monkeypatch.setattr(cli, "_load_uvicorn", lambda: SimpleNamespace(Config=FakeConfig))
    monkeypatch.setattr(cli, "_load_graceful_drain_server", lambda: FakeServer)
    monkeypatch.setattr(cli, "_load_shutdown_drain_timeout_seconds", lambda: 17)

    cli._run_server("app.main:app", host="127.0.0.1", port=2455)

    assert captured["loaded"] is True
    assert captured["workers"] == 1
    assert captured["server_ran"] is True


def test_run_server_reports_uvicorn_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeConfig:
        workers = 1

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def load_app(self) -> None:
            return None

    class FakeServer:
        started = False

        def __init__(self, _config: FakeConfig, *, drain_timeout_seconds: float) -> None:
            assert drain_timeout_seconds == 17

        def run(self) -> None:
            return None

    monkeypatch.setattr(cli, "_load_uvicorn", lambda: SimpleNamespace(Config=FakeConfig))
    monkeypatch.setattr(cli, "_load_graceful_drain_server", lambda: FakeServer)
    monkeypatch.setattr(cli, "_load_shutdown_drain_timeout_seconds", lambda: 17)

    with pytest.raises(SystemExit) as exc_info:
        cli._run_server("app.main:app", host="127.0.0.1", port=2455)

    assert exc_info.value.code == 3


@pytest.mark.parametrize("started", [False, True])
def test_run_server_handles_keyboard_interrupt_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    started: bool,
) -> None:
    class FakeConfig:
        workers = 1

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def load_app(self) -> None:
            return None

    class FakeServer:
        def __init__(self, _config: FakeConfig, *, drain_timeout_seconds: float) -> None:
            assert drain_timeout_seconds == 17
            self.started = started

        def run(self) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_load_uvicorn", lambda: SimpleNamespace(Config=FakeConfig))
    monkeypatch.setattr(cli, "_load_graceful_drain_server", lambda: FakeServer)
    monkeypatch.setattr(cli, "_load_shutdown_drain_timeout_seconds", lambda: 17)

    cli._run_server("app.main:app", host="127.0.0.1", port=2455)


def test_run_server_handles_keyboard_interrupt_during_app_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeConfig:
        workers = 1

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def load_app(self) -> None:
            raise KeyboardInterrupt

    def fail_load_server() -> None:
        pytest.fail("The server must not be constructed after interrupted app loading")

    monkeypatch.setattr(cli, "_load_uvicorn", lambda: SimpleNamespace(Config=FakeConfig))
    monkeypatch.setattr(cli, "_load_graceful_drain_server", fail_load_server)
    monkeypatch.setattr(cli, "_load_shutdown_drain_timeout_seconds", lambda: 17)

    cli._run_server("app.main:app", host="127.0.0.1", port=2455)


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
