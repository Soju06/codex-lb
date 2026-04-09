from __future__ import annotations

import logging
import secrets

from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.db.session import SessionLocal
from app.modules.dashboard_auth.repository import DashboardAuthRepository


def _get_manual_bootstrap_token() -> str | None:
    manual = (get_settings().dashboard_bootstrap_token or "").strip()
    return manual or None


def log_bootstrap_token(logger: logging.Logger, token: str, *, reason: str = "first-run") -> None:
    logger.info(
        "\n"
        "============================================\n"
        "  Dashboard bootstrap token (%s):\n"
        "  %s\n"
        "\n"
        "  Use this token for initial remote setup.\n"
        "  It is shared across replicas and stays\n"
        "  valid until a password is set.\n"
        "============================================",
        reason,
        token,
    )


async def get_active_bootstrap_token() -> str | None:
    manual = _get_manual_bootstrap_token()
    if manual:
        return manual

    settings = await get_settings_cache().get()
    if settings.password_hash is not None or settings.bootstrap_token is None:
        return None
    return settings.bootstrap_token


async def ensure_auto_bootstrap_token() -> str | None:
    manual = _get_manual_bootstrap_token()

    async with SessionLocal() as session:
        repository = DashboardAuthRepository(session)
        settings = await repository.get_settings()

        if manual or settings.password_hash is not None:
            if settings.bootstrap_token is not None:
                await repository.clear_bootstrap_token()
                await get_settings_cache().invalidate()
            return None

        if settings.bootstrap_token is not None:
            return settings.bootstrap_token

        token = secrets.token_urlsafe(32)
        stored = await repository.store_bootstrap_token_if_absent(token)

    await get_settings_cache().invalidate()
    if stored:
        return token

    settings = await get_settings_cache().get()
    if settings.password_hash is not None or settings.bootstrap_token is None:
        return None
    return settings.bootstrap_token


async def clear_auto_generated_token() -> None:
    async with SessionLocal() as session:
        repository = DashboardAuthRepository(session)
        cleared = await repository.clear_bootstrap_token()
    if cleared:
        await get_settings_cache().invalidate()
