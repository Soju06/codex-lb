from __future__ import annotations

import base64
import json
from collections import deque
from dataclasses import dataclass
from io import BytesIO
from time import time
from typing import Protocol

import bcrypt
import segno

from app.core.auth.totp import build_otpauth_uri, generate_totp_secret, verify_totp_code
from app.core.crypto import TokenEncryptor
from app.modules.dashboard_auth.schemas import (
    DashboardAuthSessionResponse,
    DashboardAuthUserResponse,
    TotpSetupStartResponse,
)

DASHBOARD_SESSION_COOKIE = "codex_lb_dashboard_session"
_SESSION_TTL_SECONDS = 12 * 60 * 60
_TOTP_ISSUER = "codex-lb"
_TOTP_ACCOUNT = "dashboard"


class DashboardAuthSettingsProtocol(Protocol):
    password_hash: str | None
    totp_required_on_login: bool
    totp_secret_encrypted: bytes | None
    totp_last_verified_step: int | None


class DashboardAuthUserProtocol(Protocol):
    id: str
    username: str
    password_hash: str
    role: str
    is_active: bool


class DashboardAuthRepositoryProtocol(Protocol):
    async def get_settings(self) -> DashboardAuthSettingsProtocol: ...

    async def get_password_hash(self) -> str | None: ...

    async def try_set_password_hash(self, password_hash: str) -> bool: ...

    async def set_password_hash(self, password_hash: str) -> DashboardAuthSettingsProtocol: ...

    async def clear_password_and_totp(self) -> DashboardAuthSettingsProtocol: ...

    async def set_totp_secret(self, secret_encrypted: bytes | None) -> DashboardAuthSettingsProtocol: ...

    async def try_advance_totp_last_verified_step(self, step: int) -> bool: ...

    async def get_user_by_id(self, user_id: str) -> DashboardAuthUserProtocol | None: ...

    async def get_user_by_username(self, username: str) -> DashboardAuthUserProtocol | None: ...

    async def set_user_password_hash(self, user_id: str, password_hash: str) -> DashboardAuthUserProtocol | None: ...


class TotpAlreadyConfiguredError(ValueError):
    pass


class TotpNotConfiguredError(ValueError):
    pass


class TotpInvalidCodeError(ValueError):
    pass


class TotpInvalidSetupError(ValueError):
    pass


class PasswordAlreadyConfiguredError(ValueError):
    pass


class PasswordNotConfiguredError(ValueError):
    pass


class InvalidCredentialsError(ValueError):
    pass


class PasswordSessionRequiredError(ValueError):
    pass


@dataclass(slots=True)
class DashboardSessionState:
    expires_at: int
    user_id: str
    username: str
    role: str
    password_verified: bool
    totp_verified: bool


class DashboardSessionStore:
    def __init__(self) -> None:
        self._encryptor: TokenEncryptor | None = None

    def _get_encryptor(self) -> TokenEncryptor:
        if self._encryptor is None:
            self._encryptor = TokenEncryptor()
        return self._encryptor

    def create(
        self,
        *,
        user_id: str = "dashboard-user-admin-default",
        username: str = "admin",
        role: str = "admin",
        password_verified: bool,
        totp_verified: bool,
    ) -> str:
        expires_at = int(time()) + _SESSION_TTL_SECONDS
        payload = json.dumps(
            {
                "exp": expires_at,
                "uid": user_id,
                "un": username,
                "ur": role,
                "pw": password_verified,
                "tv": totp_verified,
            },
            separators=(",", ":"),
        )
        return self._get_encryptor().encrypt(payload).decode("ascii")

    def get(self, session_id: str | None) -> DashboardSessionState | None:
        if not session_id:
            return None
        token = session_id.strip()
        if not token:
            return None
        try:
            raw = self._get_encryptor().decrypt(token.encode("ascii"))
        except Exception:
            return None
        try:
            data = json.loads(raw)
        except Exception:
            return None
        exp = data.get("exp")
        uid = data.get("uid")
        username = data.get("un")
        role = data.get("ur")
        pw = data.get("pw")
        tv = data.get("tv")
        if (
            not isinstance(exp, int)
            or not isinstance(uid, str)
            or not uid.strip()
            or not isinstance(username, str)
            or not username.strip()
            or not isinstance(role, str)
            or role not in {"admin", "user"}
            or not isinstance(pw, bool)
            or not isinstance(tv, bool)
        ):
            return None
        if exp < int(time()):
            return None
        return DashboardSessionState(
            expires_at=exp,
            user_id=uid,
            username=username,
            role=role,
            password_verified=pw,
            totp_verified=tv,
        )

    def is_password_verified(self, session_id: str | None) -> bool:
        state = self.get(session_id)
        if state is None:
            return False
        return state.password_verified

    def is_totp_verified(self, session_id: str | None) -> bool:
        state = self.get(session_id)
        if state is None:
            return False
        return state.totp_verified

    def delete(self, session_id: str | None) -> None:
        # Stateless: deletion is handled by clearing the cookie client-side.
        return


class TotpRateLimiter:
    def __init__(self, *, max_failures: int, window_seconds: int) -> None:
        if max_failures <= 0:
            raise ValueError("max_failures must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._failures: dict[str, deque[int]] = {}

    def check(self, key: str) -> int | None:
        now = int(time())
        failures = self._failures.get(key)
        if failures is None:
            return None
        cutoff = now - self._window_seconds
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            self._failures.pop(key, None)
            return None
        if len(failures) >= self._max_failures:
            retry_after = failures[0] + self._window_seconds - now
            return max(1, retry_after)
        return None

    def record_failure(self, key: str) -> None:
        now = int(time())
        failures = self._failures.setdefault(key, deque())
        failures.append(now)
        cutoff = now - self._window_seconds
        while failures and failures[0] <= cutoff:
            failures.popleft()

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


class DashboardAuthService:
    def __init__(self, repository: DashboardAuthRepositoryProtocol, session_store: DashboardSessionStore) -> None:
        self._repository = repository
        self._session_store = session_store
        self._encryptor = TokenEncryptor()

    async def get_session_state(self, session_id: str | None) -> DashboardAuthSessionResponse:
        settings = await self._repository.get_settings()
        password_required = True
        totp_required = settings.totp_required_on_login
        totp_configured = settings.totp_secret_encrypted is not None
        state = self._session_store.get(session_id)
        user = await self._repository.get_user_by_id(state.user_id) if state is not None else None
        if user is None or not user.is_active:
            state = None
        password_authenticated = bool(state and state.password_verified)
        if totp_required:
            authenticated = bool(state and state.password_verified and state.totp_verified)
        else:
            authenticated = password_authenticated

        # Surface the TOTP prompt only for password-authenticated sessions.
        totp_required_on_login = bool(totp_required and password_authenticated)
        return DashboardAuthSessionResponse(
            authenticated=authenticated,
            password_required=password_required,
            totp_required_on_login=totp_required_on_login,
            totp_configured=totp_configured,
            user=(
                DashboardAuthUserResponse(
                    id=user.id,
                    username=user.username,
                    role=user.role.value if hasattr(user.role, "value") else str(user.role),
                )
                if authenticated and user is not None
                else None
            ),
        )

    async def setup_password(self, password: str) -> None:
        _ = password
        raise PasswordAlreadyConfiguredError("Password setup is no longer supported")

    async def verify_password(self, username: str, password: str) -> DashboardAuthUserProtocol:
        user = await self._repository.get_user_by_username(username.strip().lower())
        if user is None or not user.is_active:
            raise InvalidCredentialsError("Invalid credentials")
        if not _check_password(password, user.password_hash):
            raise InvalidCredentialsError("Invalid credentials")
        return user

    async def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        user = await self._repository.get_user_by_id(user_id)
        if user is None or not user.is_active:
            raise InvalidCredentialsError("Invalid credentials")
        if not _check_password(current_password, user.password_hash):
            raise InvalidCredentialsError("Invalid credentials")
        updated = await self._repository.set_user_password_hash(user_id, _hash_password(new_password))
        if updated is None:
            raise InvalidCredentialsError("Invalid credentials")

    async def remove_password(self, password: str) -> None:
        _ = password
        raise PasswordNotConfiguredError("Removing password is not supported")

    async def _require_active_password_session(self, session_id: str | None) -> DashboardAuthSettingsProtocol:
        settings = await self._repository.get_settings()
        session = self._session_store.get(session_id)
        if session is None or not session.password_verified:
            raise PasswordSessionRequiredError("Password-authenticated session is required")
        user = await self._repository.get_user_by_id(session.user_id)
        if user is None or not user.is_active:
            raise PasswordSessionRequiredError("Password-authenticated session is required")
        return settings

    async def _require_totp_verified_session(self, session_id: str | None) -> DashboardAuthSettingsProtocol:
        settings = await self._require_active_password_session(session_id)
        session = self._session_store.get(session_id)
        if session is None or not session.totp_verified:
            raise PasswordSessionRequiredError("TOTP-verified session is required")
        return settings

    async def start_totp_setup(self, *, session_id: str | None) -> TotpSetupStartResponse:
        settings = await self._require_active_password_session(session_id)
        if settings.totp_secret_encrypted is not None:
            raise TotpAlreadyConfiguredError("TOTP is already configured. Disable it before setting a new secret")
        secret = generate_totp_secret()
        otpauth_uri = build_otpauth_uri(secret, issuer=_TOTP_ISSUER, account_name=_TOTP_ACCOUNT)
        return TotpSetupStartResponse(
            secret=secret,
            otpauth_uri=otpauth_uri,
            qr_svg_data_uri=_qr_svg_data_uri(otpauth_uri),
        )

    async def confirm_totp_setup(self, *, session_id: str | None, secret: str, code: str) -> None:
        current = await self._require_active_password_session(session_id)
        if current.totp_secret_encrypted is not None:
            raise TotpAlreadyConfiguredError("TOTP is already configured. Disable it before setting a new secret")
        try:
            verification = verify_totp_code(secret, code, window=1)
        except ValueError as exc:
            raise TotpInvalidSetupError("Invalid TOTP setup payload") from exc
        if not verification.is_valid:
            raise TotpInvalidCodeError("Invalid TOTP code")
        await self._repository.set_totp_secret(self._encryptor.encrypt(secret))

    async def verify_totp(self, *, session_id: str | None, code: str) -> str:
        settings = await self._require_active_password_session(session_id)
        session = self._session_store.get(session_id)
        if session is None:
            raise PasswordSessionRequiredError("Password-authenticated session is required")
        secret_encrypted = settings.totp_secret_encrypted
        if secret_encrypted is None:
            raise TotpNotConfiguredError("TOTP is not configured")
        secret = self._encryptor.decrypt(secret_encrypted)
        verification = verify_totp_code(
            secret,
            code,
            window=1,
            last_verified_step=settings.totp_last_verified_step,
        )
        if not verification.is_valid or verification.matched_step is None:
            raise TotpInvalidCodeError("Invalid TOTP code")
        updated = await self._repository.try_advance_totp_last_verified_step(verification.matched_step)
        if not updated:
            raise TotpInvalidCodeError("Invalid TOTP code")
        return self._session_store.create(
            user_id=session.user_id,
            username=session.username,
            role=session.role,
            password_verified=True,
            totp_verified=True,
        )

    async def disable_totp(self, *, session_id: str | None, code: str) -> None:
        settings = await self._require_totp_verified_session(session_id)
        secret_encrypted = settings.totp_secret_encrypted
        if secret_encrypted is None:
            raise TotpNotConfiguredError("TOTP is not configured")
        secret = self._encryptor.decrypt(secret_encrypted)
        verification = verify_totp_code(
            secret,
            code,
            window=1,
            last_verified_step=settings.totp_last_verified_step,
        )
        if not verification.is_valid or verification.matched_step is None:
            raise TotpInvalidCodeError("Invalid TOTP code")
        updated = await self._repository.try_advance_totp_last_verified_step(verification.matched_step)
        if not updated:
            raise TotpInvalidCodeError("Invalid TOTP code")
        await self._repository.set_totp_secret(None)

    def logout(self, session_id: str | None) -> None:
        self._session_store.delete(session_id)


_dashboard_session_store = DashboardSessionStore()
_totp_rate_limiter = TotpRateLimiter(max_failures=8, window_seconds=60)
_password_rate_limiter = TotpRateLimiter(max_failures=8, window_seconds=60)


def get_dashboard_session_store() -> DashboardSessionStore:
    return _dashboard_session_store


def get_totp_rate_limiter() -> TotpRateLimiter:
    return _totp_rate_limiter


def get_password_rate_limiter() -> TotpRateLimiter:
    return _password_rate_limiter


def _qr_svg_data_uri(payload: str) -> str:
    qr = segno.make(payload)
    buffer = BytesIO()
    qr.save(buffer, kind="svg", xmldecl=False, scale=6, border=2)
    raw = buffer.getvalue()
    return f"data:image/svg+xml;base64,{base64.b64encode(raw).decode('ascii')}"


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _check_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False
