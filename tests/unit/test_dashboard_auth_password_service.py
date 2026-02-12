from __future__ import annotations

from dataclasses import dataclass

import bcrypt
import pytest

from app.modules.dashboard_auth.service import (
    DashboardAuthService,
    DashboardSessionStore,
    InvalidCredentialsError,
    PasswordAlreadyConfiguredError,
    PasswordNotConfiguredError,
)

pytestmark = pytest.mark.unit


@dataclass(slots=True)
class _FakeSettings:
    password_hash: str | None = None
    totp_required_on_login: bool = False
    totp_secret_encrypted: bytes | None = None
    totp_last_verified_step: int | None = None


class _FakeRepository:
    def __init__(self) -> None:
        self.settings = _FakeSettings()

    async def get_settings(self) -> _FakeSettings:
        return self.settings

    async def get_password_hash(self) -> str | None:
        return self.settings.password_hash

    async def set_password_hash(self, password_hash: str) -> _FakeSettings:
        self.settings.password_hash = password_hash
        return self.settings

    async def clear_password_and_totp(self) -> _FakeSettings:
        self.settings.password_hash = None
        self.settings.totp_required_on_login = False
        self.settings.totp_secret_encrypted = None
        self.settings.totp_last_verified_step = None
        return self.settings

    async def set_totp_secret(self, secret_encrypted: bytes | None) -> _FakeSettings:
        self.settings.totp_secret_encrypted = secret_encrypted
        self.settings.totp_last_verified_step = None
        if secret_encrypted is None:
            self.settings.totp_required_on_login = False
        return self.settings

    async def try_advance_totp_last_verified_step(self, step: int) -> bool:
        current = self.settings.totp_last_verified_step
        if current is not None and current >= step:
            return False
        self.settings.totp_last_verified_step = step
        return True


@pytest.mark.asyncio
async def test_setup_password_hashes_and_rejects_duplicate() -> None:
    repository = _FakeRepository()
    service = DashboardAuthService(repository, DashboardSessionStore())

    await service.setup_password("password123")
    stored_hash = repository.settings.password_hash
    assert stored_hash is not None
    assert stored_hash != "password123"
    assert bcrypt.checkpw("password123".encode("utf-8"), stored_hash.encode("utf-8")) is True

    with pytest.raises(PasswordAlreadyConfiguredError):
        await service.setup_password("another-password")


@pytest.mark.asyncio
async def test_verify_and_change_password() -> None:
    repository = _FakeRepository()
    service = DashboardAuthService(repository, DashboardSessionStore())
    await service.setup_password("password123")

    await service.verify_password("password123")
    with pytest.raises(InvalidCredentialsError):
        await service.verify_password("wrong-password")

    await service.change_password("password123", "new-password-456")
    await service.verify_password("new-password-456")
    with pytest.raises(InvalidCredentialsError):
        await service.verify_password("password123")


@pytest.mark.asyncio
async def test_remove_password_clears_password_and_totp() -> None:
    repository = _FakeRepository()
    service = DashboardAuthService(repository, DashboardSessionStore())
    await service.setup_password("password123")
    repository.settings.totp_required_on_login = True
    repository.settings.totp_secret_encrypted = b"secret"
    repository.settings.totp_last_verified_step = 123

    await service.remove_password("password123")
    assert repository.settings.password_hash is None
    assert repository.settings.totp_required_on_login is False
    assert repository.settings.totp_secret_encrypted is None
    assert repository.settings.totp_last_verified_step is None

    with pytest.raises(PasswordNotConfiguredError):
        await service.verify_password("password123")
