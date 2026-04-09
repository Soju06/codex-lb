from __future__ import annotations

import secrets

from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
from app.db.session import SessionLocal
from app.modules.dashboard_auth.repository import DashboardAuthRepository

_encryptor: TokenEncryptor | None = None


def _get_manual_bootstrap_token() -> str | None:
    manual = (get_settings().dashboard_bootstrap_token or "").strip()
    return manual or None


def _get_encryptor() -> TokenEncryptor:
    global _encryptor

    if _encryptor is None:
        _encryptor = TokenEncryptor()
    return _encryptor


async def get_active_bootstrap_token() -> str | None:
    manual = _get_manual_bootstrap_token()
    if manual:
        return manual

    settings = await get_settings_cache().get()
    if settings.password_hash is not None or settings.bootstrap_token_encrypted is None:
        return None
    return _get_encryptor().decrypt(settings.bootstrap_token_encrypted)


async def ensure_auto_bootstrap_token() -> str | None:
    manual = _get_manual_bootstrap_token()

    async with SessionLocal() as session:
        repository = DashboardAuthRepository(session)
        settings = await repository.get_settings()

        if manual or settings.password_hash is not None:
            if settings.bootstrap_token_encrypted is not None:
                await repository.clear_bootstrap_token()
                await get_settings_cache().invalidate()
            return None

        if settings.bootstrap_token_encrypted is not None:
            return _get_encryptor().decrypt(settings.bootstrap_token_encrypted)

        token = secrets.token_urlsafe(32)
        token_encrypted = _get_encryptor().encrypt(token)
        stored = await repository.store_bootstrap_token_if_absent(token_encrypted)

    await get_settings_cache().invalidate()
    if stored:
        return token

    settings = await get_settings_cache().get()
    if settings.password_hash is not None or settings.bootstrap_token_encrypted is None:
        return None
    return _get_encryptor().decrypt(settings.bootstrap_token_encrypted)


async def clear_auto_generated_token() -> None:
    async with SessionLocal() as session:
        repository = DashboardAuthRepository(session)
        cleared = await repository.clear_bootstrap_token()
    if cleared:
        await get_settings_cache().invalidate()
