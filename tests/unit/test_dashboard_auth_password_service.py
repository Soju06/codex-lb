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

    async def try_set_password_hash(self, password_hash: str) -> bool:
        if self.settings.password_hash is not None:
            return False
        self.settings.password_hash = password_hash
        return True

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
async def test_setup_password_raises_when_atomic_set_fails() -> None:
    repository = _FakeRepository()
    repository.settings.password_hash = "already-set-by-race"
    service = DashboardAuthService(repository, DashboardSessionStore())

    with pytest.raises(PasswordAlreadyConfiguredError):
        await service.setup_password("password123")


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


@pytest.mark.asyncio
async def test_verify_totp_inherits_existing_password_session_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    import pyotp

    import app.core.auth.totp as totp_module
    import app.modules.dashboard_auth.service as service_module
    from app.core.crypto import TokenEncryptor

    current = {"value": 1_700_000_000}
    monkeypatch.setattr(service_module, "time", lambda: current["value"])
    monkeypatch.setattr(totp_module, "time", lambda: current["value"])

    repository = _FakeRepository()
    store = DashboardSessionStore()
    service = DashboardAuthService(repository, store)
    await service.setup_password("password123")

    secret = pyotp.random_base32()
    encryptor = TokenEncryptor()
    repository.settings.totp_secret_encrypted = encryptor.encrypt(secret)

    # Issue an existing password session with the previous TTL setting
    # (12 hours), then change the TTL setting on the operator side and submit
    # TOTP. The new session must inherit the original session's remaining
    # lifetime, not adopt the new TTL.
    original_ttl = 12 * 60 * 60
    new_ttl_after_change = 24 * 60 * 60
    password_session_id = store.create(
        password_verified=True,
        totp_verified=False,
        ttl_seconds=original_ttl,
    )
    expected_remaining = original_ttl  # nothing has elapsed yet

    code = pyotp.TOTP(secret).at(current["value"])
    new_session_id, applied_ttl = await service.verify_totp(
        session_id=password_session_id,
        code=code,
        ttl_seconds=new_ttl_after_change,
    )

    assert applied_ttl == expected_remaining
    state = store.get(new_session_id)
    assert state is not None
    assert state.password_verified is True
    assert state.totp_verified is True
    assert state.expires_at == current["value"] + expected_remaining


@pytest.mark.asyncio
async def test_verify_totp_does_not_call_session_store_get_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pyotp

    import app.core.auth.totp as totp_module
    import app.modules.dashboard_auth.service as service_module
    from app.core.crypto import TokenEncryptor

    current = {"value": 1_700_000_000}
    monkeypatch.setattr(service_module, "time", lambda: current["value"])
    monkeypatch.setattr(totp_module, "time", lambda: current["value"])

    repository = _FakeRepository()
    store = DashboardSessionStore()
    service = DashboardAuthService(repository, store)
    await service.setup_password("password123")

    secret = pyotp.random_base32()
    encryptor = TokenEncryptor()
    repository.settings.totp_secret_encrypted = encryptor.encrypt(secret)

    # Regression for the race between two store.get(session_id) calls in
    # verify_totp: the inherited-TTL path must reuse the live state captured
    # during the active-session check rather than re-querying the store.
    # Asserting that store.get is called exactly once during verify_totp
    # locks in that single-lookup contract; previously the inherited-TTL
    # branch could mint a fresh full-length session if the second lookup
    # raced the clock past the original session's expiry.
    original_ttl = 12 * 60 * 60
    new_ttl_after_change = 24 * 60 * 60
    password_session_id = store.create(
        password_verified=True,
        totp_verified=False,
        ttl_seconds=original_ttl,
    )

    real_get = store.get
    get_calls: list[str | None] = []

    def counted_get(session_id):
        get_calls.append(session_id)
        return real_get(session_id)

    monkeypatch.setattr(store, "get", counted_get)

    code = pyotp.TOTP(secret).at(current["value"])
    _, applied_ttl = await service.verify_totp(
        session_id=password_session_id,
        code=code,
        ttl_seconds=new_ttl_after_change,
    )

    assert applied_ttl == original_ttl
    assert get_calls == [password_session_id]
