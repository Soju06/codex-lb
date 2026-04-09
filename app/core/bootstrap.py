from __future__ import annotations

import secrets

_auto_generated_token: str | None = None


def get_active_bootstrap_token() -> str | None:
    from app.core.config.settings import get_settings

    manual = (get_settings().dashboard_bootstrap_token or "").strip()
    if manual:
        return manual
    return _auto_generated_token


def maybe_generate_bootstrap_token(*, password_exists: bool) -> str | None:
    global _auto_generated_token

    from app.core.config.settings import get_settings

    manual = (get_settings().dashboard_bootstrap_token or "").strip()
    if manual or password_exists:
        _auto_generated_token = None
        return None
    _auto_generated_token = secrets.token_urlsafe(32)
    return _auto_generated_token


def clear_auto_generated_token() -> None:
    global _auto_generated_token

    _auto_generated_token = None
