from __future__ import annotations

from app.core.clients.anthropic_api_proxy import _should_attempt_oauth_refresh


def test_should_refresh_on_401_auth_error() -> None:
    payload = {
        "type": "error",
        "error": {
            "type": "authentication_error",
            "message": "Authentication failed",
        },
    }
    assert _should_attempt_oauth_refresh(401, payload) is True


def test_should_refresh_on_403_revoked_oauth_token() -> None:
    payload = {
        "type": "error",
        "error": {
            "type": "permission_error",
            "message": "OAuth token has been revoked. Please obtain a new token.",
        },
    }
    assert _should_attempt_oauth_refresh(403, payload) is True


def test_should_not_refresh_on_403_oauth_not_supported() -> None:
    payload = {
        "type": "error",
        "error": {
            "type": "authentication_error",
            "message": "OAuth authentication is currently not supported.",
        },
    }
    assert _should_attempt_oauth_refresh(403, payload) is False


def test_should_not_refresh_on_other_403_errors() -> None:
    payload = {
        "type": "error",
        "error": {
            "type": "permission_error",
            "message": "Model access denied.",
        },
    }
    assert _should_attempt_oauth_refresh(403, payload) is False
