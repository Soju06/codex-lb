from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.cli import main
from app.core.config.settings import Settings, desktop_relay_lb_origin

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def listener_environment(monkeypatch):
    for name in ("HOST", "PORT", "SSL_CERTFILE", "SSL_KEYFILE", "CODEX_LB_DESKTOP_RELAY_MODE"):
        monkeypatch.delenv(name, raising=False)


def test_default_off_and_unknown_mode_rejected():
    assert Settings().desktop_relay_mode == "off"
    with pytest.raises(ValidationError):
        Settings.model_validate({"desktop_relay_mode": "public"})


def test_default_off_does_not_constrain_normal_server(monkeypatch):
    monkeypatch.setenv("HOST", "::1")
    monkeypatch.setenv("SSL_CERTFILE", "server.pem")
    monkeypatch.setenv("PORT", "0")
    assert desktop_relay_lb_origin("off") is None


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "0.0.0.0"])
def test_embedded_origin_uses_local_main_listener(monkeypatch, host):
    monkeypatch.setenv("HOST", host)
    monkeypatch.setenv("PORT", "2456")
    assert desktop_relay_lb_origin("container") == "http://127.0.0.1:2456"


@pytest.mark.parametrize(
    "name,value",
    [
        ("PORT", "0"),
        ("PORT", "8000"),
        ("PORT", "-1"),
        ("PORT", "65536"),
        ("PORT", "not-a-port"),
        ("HOST", "::1"),
        ("HOST", "::"),
        ("HOST", "192.0.2.1"),
        ("SSL_CERTFILE", "server.pem"),
        ("SSL_KEYFILE", "server.key"),
    ],
)
def test_unsupported_embedded_listener_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="Embedded Desktop relay requires"):
        desktop_relay_lb_origin("loopback")


def test_cli_selected_listener_overrides_ambient_metadata(monkeypatch):
    monkeypatch.setenv("HOST", "::1")
    monkeypatch.setenv("PORT", "9000")
    with patch("app.cli._run_server"):
        main(["--host", "0.0.0.0", "--port", "2456"])
    assert desktop_relay_lb_origin("container") == "http://127.0.0.1:2456"


def test_cli_tls_selection_reaches_embedded_validation():
    with patch("app.cli._run_server"):
        main(["--ssl-certfile", "server.pem", "--ssl-keyfile", "server.key"])
    with pytest.raises(ValueError, match="TLS listeners are unsupported"):
        desktop_relay_lb_origin("loopback")
