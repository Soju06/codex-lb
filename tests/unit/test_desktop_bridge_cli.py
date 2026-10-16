from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from app import cli

pytestmark = pytest.mark.unit


def test_desktop_bridge_dispatch_isolated_from_main_server_and_its_environment(monkeypatch, tmp_path):
    """Dispatch bridge options without parsing unrelated main-server environment settings."""
    from app import desktop_bridge

    captured = []
    monkeypatch.setattr(desktop_bridge, "run_bridge", captured.append)
    monkeypatch.setattr(cli, "_run_server", lambda *args, **kwargs: pytest.fail("main server must not run"))
    monkeypatch.setenv("PORT", "invalid")
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("UVICORN_WS_MAX_SIZE", "invalid")
    monkeypatch.setenv("UVICORN_TIMEOUT_KEEP_ALIVE", "invalid")
    monkeypatch.setenv("SSL_CERTFILE", "unpaired")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    cli.main(["desktop-bridge", "--listen-port", "2456", "--lb-token-file", "synthetic-token-file"])

    assert len(captured) == 1
    assert captured[0].listen_port == 2456
    assert captured[0].lb_port == 2455
    assert captured[0].codex_home == tmp_path
    assert captured[0].lb_token_file == Path("synthetic-token-file")


def test_desktop_bridge_accepts_explicit_paths_and_port(monkeypatch, tmp_path):
    """Pass explicit ports and credential paths into the bridge configuration unchanged."""
    from app import desktop_bridge

    captured = []
    monkeypatch.setattr(desktop_bridge, "run_bridge", captured.append)
    cli.main(
        [
            "desktop-bridge",
            "--listen-port",
            "3001",
            "--lb-port",
            "3000",
            "--lb-token-file",
            str(tmp_path / "token"),
            "--codex-home",
            str(tmp_path),
        ]
    )
    assert captured[0] == desktop_bridge.BridgeConfig(3001, 3000, tmp_path, tmp_path / "token")


@pytest.mark.parametrize("flag", ["--listen-port", "--lb-port"])
@pytest.mark.parametrize("value", ["0", "-1", "65536", "invalid"])
def test_desktop_bridge_rejects_invalid_port_before_running(monkeypatch, flag, value):
    """Reject nonnumeric and out-of-range ports during argument parsing before startup."""
    monkeypatch.setattr(cli, "_run_desktop_bridge", lambda args: pytest.fail("invalid config must not run"))
    with pytest.raises(SystemExit) as error:
        cli.main(["desktop-bridge", "--listen-port", "2456", "--lb-token-file", "synthetic", flag, value])
    assert error.value.code == 2


@pytest.mark.parametrize("argv", [["desktop-bridge"], ["desktop-bridge", "--listen-port", "2456"]])
def test_desktop_bridge_requires_port_and_token_file(argv):
    """Report a usage error when either required bridge argument is absent."""
    with pytest.raises(SystemExit) as error:
        cli.main(argv)
    assert error.value.code == 2


def test_desktop_bridge_rejects_loopback_to_itself():
    """Reject a listener port that equals the default upstream port."""
    with pytest.raises(SystemExit, match="must differ"):
        cli.main(["desktop-bridge", "--listen-port", "2455", "--lb-token-file", "synthetic"])


def test_desktop_bridge_reports_credential_error_without_traceback(monkeypatch):
    """Convert credential startup failures into a concise CLI exit message."""
    from app import desktop_bridge

    def fail(config):
        """Simulate startup rejecting an unreadable or unsafe token file."""
        raise ValueError("The LB token file is missing, unreadable, or a symlink.")

    monkeypatch.setattr(desktop_bridge, "run_bridge", fail)
    with pytest.raises(SystemExit, match="LB token file is missing"):
        cli.main(["desktop-bridge", "--listen-port", "2456", "--lb-token-file", "synthetic"])


def test_help_does_not_import_bridge_or_main_server(monkeypatch):
    """Render bridge help without loading application or logging runtime modules."""
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        """Fail on runtime imports while delegating all other imports to Python."""
        if name in {"app.desktop_bridge", "app.main", "app.core.runtime_logging"}:
            pytest.fail(f"help must not import {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with pytest.raises(SystemExit) as error:
        cli.main(["desktop-bridge", "--help"])
    assert error.value.code == 0
