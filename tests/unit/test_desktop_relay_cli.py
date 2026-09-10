from unittest.mock import patch

import pytest

from app.cli import main

pytestmark = pytest.mark.unit


def test_relay_command_does_not_start_database_server_or_use_public_host():
    with patch("app.modules.desktop_relay.api.run") as run, patch("app.cli._run_server") as server:
        main(["--host", "0.0.0.0", "--port", "9000", "desktop-relay"])
    run.assert_called_once_with("http://127.0.0.1:2455")
    server.assert_not_called()


def test_relay_command_accepts_local_lb_origin():
    with patch("app.modules.desktop_relay.api.run") as run:
        main(["desktop-relay", "--lb-url", "http://localhost:3456"])
    run.assert_called_once_with("http://localhost:3456")


def test_relay_command_rejects_invalid_origin_without_echoing_credentials():
    with pytest.raises(SystemExit) as error:
        main(["desktop-relay", "--lb-url", "http://secret@example.com"])
    assert "secret" not in str(error.value)


def test_relay_startup_failure_does_not_echo_proxy_credentials():
    with patch("app.modules.desktop_relay.api.web.run_app", side_effect=ValueError("http://private:secret@proxy")):
        with pytest.raises(SystemExit) as error:
            main(["desktop-relay"])
    assert "secret" not in str(error.value)
    assert "private" not in str(error.value)
