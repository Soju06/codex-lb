from __future__ import annotations

import hashlib
import logging
import secrets
from secrets import compare_digest

from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.db.session import SessionLocal
from app.modules.dashboard_auth.repository import DashboardAuthRepository


def _get_manual_bootstrap_token() -> str | None:
    manual = (get_settings().dashboard_bootstrap_token or "").strip()
    return manual or None


def _hash_bootstrap_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


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
    return _get_manual_bootstrap_token()


async def _get_shared_bootstrap_state() -> tuple[str | None, bytes | None]:
    async with SessionLocal() as session:
        settings = await DashboardAuthRepository(session).get_settings()
        return settings.password_hash, settings.bootstrap_token_hash


async def has_active_bootstrap_token() -> bool:
    manual = _get_manual_bootstrap_token()
    if manual:
        return True

    password_hash, bootstrap_token_hash = await _get_shared_bootstrap_state()
    return password_hash is None and bootstrap_token_hash is not None


async def validate_bootstrap_token(submitted_token: str) -> bool:
    manual = _get_manual_bootstrap_token()
    if manual is not None:
        return compare_digest(submitted_token.encode("utf-8"), manual.encode("utf-8"))

    password_hash, bootstrap_token_hash = await _get_shared_bootstrap_state()
    if password_hash is not None or bootstrap_token_hash is None:
        return False
    return compare_digest(_hash_bootstrap_token(submitted_token), bootstrap_token_hash)


async def ensure_auto_bootstrap_token() -> str | None:
    manual = _get_manual_bootstrap_token()

    async with SessionLocal() as session:
        repository = DashboardAuthRepository(session)
        settings = await repository.get_settings()

        if manual or settings.password_hash is not None:
            if settings.bootstrap_token_hash is not None:
                await repository.clear_bootstrap_token()
                await get_settings_cache().invalidate()
            return None

        if settings.bootstrap_token_hash is not None:
            token = secrets.token_urlsafe(32)
            rotated = await repository.replace_bootstrap_token_hash(
                settings.bootstrap_token_hash,
                _hash_bootstrap_token(token),
            )
            if rotated:
                await get_settings_cache().invalidate()
                return token
            return None

        token = secrets.token_urlsafe(32)
        stored = await repository.store_bootstrap_token_if_absent(_hash_bootstrap_token(token))

    await get_settings_cache().invalidate()
    if stored:
        return token
    return None


async def clear_auto_generated_token() -> None:
    async with SessionLocal() as session:
        repository = DashboardAuthRepository(session)
        cleared = await repository.clear_bootstrap_token()
    if cleared:
        await get_settings_cache().invalidate()
