from __future__ import annotations

import logging
import sys
from pathlib import Path
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
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

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
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main()

    assert captured["kwargs"]["timeout_keep_alive"] == 900


def test_main_dispatches_init(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_init(*, force: bool, base_dir: Path | None = None, home_dir: Path | None = None) -> Path:
        captured["force"] = force
        captured["base_dir"] = base_dir
        captured["home_dir"] = home_dir
        return Path("/tmp/codex-lb/.env.local")

    monkeypatch.setattr(sys, "argv", ["codex-lb", "init", "--force"])
    monkeypatch.setattr(cli, "_init_runtime", fake_init)

    cli.main()

    assert captured == {
        "force": True,
        "base_dir": None,
        "home_dir": None,
    }


def test_init_runtime_copies_example_config(tmp_path):
    base_dir = tmp_path / "release"
    base_dir.mkdir()
    (base_dir / ".env.example").write_text("CODEX_LB_LOG_FORMAT=json\n", encoding="utf-8")

    home_dir = tmp_path / "Library" / "Application Support" / "codex-lb"

    env_path = cli._init_runtime(base_dir=base_dir, home_dir=home_dir)

    assert env_path == home_dir / ".env.local"
    assert env_path.read_text(encoding="utf-8") == "CODEX_LB_LOG_FORMAT=json\n"


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
