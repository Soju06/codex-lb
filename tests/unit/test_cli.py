from __future__ import annotations

import argparse
from typing import cast

import pytest

from app import cli

pytestmark = pytest.mark.unit


def _capture_run_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {}

    def _fake_run(*args: object, **kwargs: object) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(cli.uvicorn, "run", _fake_run)
    return captured


def _set_parse_args(
    monkeypatch: pytest.MonkeyPatch,
    *,
    host: str = "127.0.0.1",
    port: int = 2455,
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
) -> None:
    args = argparse.Namespace(
        host=host,
        port=port,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
    )
    monkeypatch.setattr(cli, "_parse_args", lambda: args)


def test_main_allows_certfile_without_keyfile(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_run_kwargs(monkeypatch)
    _set_parse_args(monkeypatch, ssl_certfile="server.pem", ssl_keyfile=None)

    cli.main()

    run_kwargs_raw = captured["kwargs"]
    assert isinstance(run_kwargs_raw, dict)
    run_kwargs = cast(dict[str, object], run_kwargs_raw)
    assert run_kwargs["ssl_certfile"] == "server.pem"
    assert run_kwargs["ssl_keyfile"] is None


def test_main_rejects_keyfile_without_certfile(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_run_kwargs(monkeypatch)
    _set_parse_args(monkeypatch, ssl_certfile=None, ssl_keyfile="server.key")

    with pytest.raises(SystemExit, match="--ssl-keyfile requires --ssl-certfile."):
        cli.main()
