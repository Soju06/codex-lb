from __future__ import annotations

import argparse
import logging
import sys
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
    monkeypatch.setattr(cli, "_http_responses_session_bridge_enabled", lambda: True)
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main()

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    log_config = kwargs["log_config"]
    assert isinstance(log_config, dict)
    formatters = log_config["formatters"]
    assert formatters["default"]["fmt"].startswith("%(asctime)s ")
    assert formatters["access"]["fmt"].startswith("%(asctime)s ")


def test_main_passes_worker_and_parser_overrides(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setenv("CODEX_LB_UVICORN_WORKERS", "4")
    monkeypatch.setenv("CODEX_LB_UVICORN_LOOP", "uvloop")
    monkeypatch.setenv("CODEX_LB_UVICORN_HTTP", "httptools")
    monkeypatch.setattr(
        sys,
        "argv",
        ["codex-lb", "--workers", "2", "--loop", "asyncio", "--http", "h11"],
    )
    monkeypatch.setattr(cli, "_http_responses_session_bridge_enabled", lambda: False)
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main()

    kwargs = captured["kwargs"]
    assert kwargs["workers"] == 2
    assert kwargs["loop"] == "asyncio"
    assert kwargs["http"] == "h11"


def test_main_reads_worker_env_defaults_when_bridge_disabled(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setenv("CODEX_LB_UVICORN_WORKERS", "4")
    monkeypatch.setenv("CODEX_LB_UVICORN_LOOP", "uvloop")
    monkeypatch.setenv("CODEX_LB_UVICORN_HTTP", "httptools")
    monkeypatch.setattr(sys, "argv", ["codex-lb"])
    monkeypatch.setattr(cli, "_http_responses_session_bridge_enabled", lambda: False)
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main()

    kwargs = captured["kwargs"]
    assert kwargs["workers"] == 4
    assert kwargs["loop"] == "uvloop"
    assert kwargs["http"] == "httptools"


def test_main_uses_bridge_safe_worker_default(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.delenv("CODEX_LB_UVICORN_WORKERS", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    monkeypatch.setattr(sys, "argv", ["codex-lb"])
    monkeypatch.setattr(cli, "_http_responses_session_bridge_enabled", lambda: True)
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main()

    assert captured["kwargs"]["workers"] == 1


def test_main_uses_addressable_worker_pool_when_bridge_enabled(monkeypatch):
    from app.core.runtime import bridge_worker_pool

    captured: dict[str, Any] = {}

    def fake_worker_pool(options, *, log_config):
        captured["options"] = options
        captured["log_config"] = log_config

    monkeypatch.setenv("CODEX_LB_UVICORN_WORKERS", "2")
    monkeypatch.setenv("CODEX_LB_UVICORN_LOOP", "uvloop")
    monkeypatch.setenv("CODEX_LB_UVICORN_HTTP", "httptools")
    monkeypatch.setattr(sys, "argv", ["codex-lb"])
    monkeypatch.setattr(cli, "_http_responses_session_bridge_enabled", lambda: True)
    monkeypatch.setattr(bridge_worker_pool, "run_bridge_worker_pool", fake_worker_pool)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *_, **__: pytest.fail("uvicorn.run should not be called"))

    cli.main()

    options = captured["options"]
    assert options.workers == 2
    assert options.loop == "uvloop"
    assert options.http == "httptools"
    assert isinstance(captured["log_config"], dict)


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


def test_positive_int_rejects_non_positive():
    with pytest.raises(argparse.ArgumentTypeError):
        cli._positive_int("0")


def test_default_worker_count_is_bridge_safe():
    assert cli._default_worker_count() == 1
