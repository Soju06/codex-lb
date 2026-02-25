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


@dataclass(slots=True)
class _FakeUser:
    id: str
    username: str
    password_hash: str
    role: str = "admin"
    is_active: bool = True


class _FakeRepository:
    def __init__(self) -> None:
        self.settings = _FakeSettings()
        self.user = _FakeUser(
            id="user-1",
            username="admin",
            password_hash=_hash_password("password123"),
        )

    async def get_settings(self) -> _FakeSettings:
        return self.settings

    async def get_user_by_id(self, user_id: str) -> _FakeUser | None:
        if self.user.id != user_id:
            return None
        return self.user

    async def get_user_by_username(self, username: str) -> _FakeUser | None:
        if self.user.username != username:
            return None
        return self.user

    async def set_user_password_hash(self, user_id: str, password_hash: str) -> _FakeUser | None:
        if self.user.id != user_id:
            return None
        self.user.password_hash = password_hash
        return self.user

    async def get_password_hash(self) -> str | None:
        return self.settings.password_hash

    async def try_set_password_hash(self, password_hash: str) -> bool:
        if self.settings.password_hash is not None:
            return False
        self.settings.password_hash = password_hash
        return True

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


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


@pytest.mark.asyncio
async def test_setup_password_is_not_supported() -> None:
    repository = _FakeRepository()
    service = DashboardAuthService(repository, DashboardSessionStore())

    with pytest.raises(PasswordAlreadyConfiguredError):
        await service.setup_password("password123")


@pytest.mark.asyncio
async def test_verify_and_change_password_for_active_user() -> None:
    repository = _FakeRepository()
    service = DashboardAuthService(repository, DashboardSessionStore())

    user = await service.verify_password("admin", "password123")
    assert user.id == repository.user.id

    with pytest.raises(InvalidCredentialsError):
        await service.verify_password("admin", "wrong-password")

    await service.change_password(repository.user.id, "password123", "new-password-456")
    await service.verify_password("admin", "new-password-456")

    with pytest.raises(InvalidCredentialsError):
        await service.verify_password("admin", "password123")


@pytest.mark.asyncio
async def test_verify_rejects_inactive_user() -> None:
    repository = _FakeRepository()
    repository.user.is_active = False
    service = DashboardAuthService(repository, DashboardSessionStore())

    with pytest.raises(InvalidCredentialsError):
        await service.verify_password("admin", "password123")


@pytest.mark.asyncio
async def test_remove_password_is_not_supported() -> None:
    repository = _FakeRepository()
    service = DashboardAuthService(repository, DashboardSessionStore())

    with pytest.raises(PasswordNotConfiguredError):
        await service.remove_password("password123")
