from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.core.bootstrap as bootstrap_module

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_bootstrap_state():
    bootstrap_module._auto_generated_token = None
    yield
    bootstrap_module._auto_generated_token = None


def _patch_settings(monkeypatch: pytest.MonkeyPatch, *, token: str | None) -> None:
    monkeypatch.setattr(
        "app.core.config.settings.get_settings",
        lambda: SimpleNamespace(dashboard_bootstrap_token=token),
    )


def test_get_active_bootstrap_token_returns_env_var_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token="manual-token")
    bootstrap_module._auto_generated_token = "auto-token"

    assert bootstrap_module.get_active_bootstrap_token() == "manual-token"


def test_get_active_bootstrap_token_returns_auto_generated_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token=None)
    bootstrap_module._auto_generated_token = "auto-token"

    assert bootstrap_module.get_active_bootstrap_token() == "auto-token"


def test_get_active_bootstrap_token_returns_none_when_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token=None)

    assert bootstrap_module.get_active_bootstrap_token() is None


def test_maybe_generate_creates_token_when_no_env_no_password(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token=None)

    token = bootstrap_module.maybe_generate_bootstrap_token(password_exists=False)

    assert isinstance(token, str)
    assert token
    assert bootstrap_module._auto_generated_token == token


def test_maybe_generate_skips_when_env_var_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token="manual-token")

    token = bootstrap_module.maybe_generate_bootstrap_token(password_exists=False)

    assert token is None
    assert bootstrap_module._auto_generated_token is None


def test_maybe_generate_skips_when_password_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token=None)

    token = bootstrap_module.maybe_generate_bootstrap_token(password_exists=True)

    assert token is None
    assert bootstrap_module._auto_generated_token is None


def test_clear_auto_generated_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, token=None)
    bootstrap_module.maybe_generate_bootstrap_token(password_exists=False)

    bootstrap_module.clear_auto_generated_token()

    assert bootstrap_module.get_active_bootstrap_token() is None
