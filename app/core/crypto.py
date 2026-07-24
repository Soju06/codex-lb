from __future__ import annotations

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet

from app.core.config.settings import get_settings

logger = logging.getLogger(__name__)


def _get_or_create_key(key_file: Path) -> bytes:
    env_key = os.environ.get("ENCRYPTION_KEY")
    if env_key:
        return env_key.encode()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if key_file.exists():
        return key_file.read_bytes()
    logger.warning(
        "No ENCRYPTION_KEY environment variable set; generating a new key "
        "at %s. This key must be persisted (e.g., via a mounted volume) or "
        "previously encrypted data will become undecryptable.",
        key_file,
    )
    key = Fernet.generate_key()
    key_file.write_bytes(key)
    key_file.chmod(0o600)
    return key


class TokenEncryptor:
    def __init__(self, key: bytes | None = None, key_file: Path | None = None) -> None:
        settings = get_settings()
        resolved_file = key_file or settings.encryption_key_file
        resolved_key = key or _get_or_create_key(resolved_file)
        self._fernet = Fernet(resolved_key)

    def encrypt(self, token: str) -> bytes:
        return self._fernet.encrypt(token.encode())

    def decrypt(self, encrypted: bytes) -> str:
        return self._fernet.decrypt(encrypted).decode()


def get_or_create_key(key_file: Path | None = None) -> bytes:
    settings = get_settings()
    resolved_file = key_file or settings.encryption_key_file
    return _get_or_create_key(resolved_file)
