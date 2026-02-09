from __future__ import annotations

import base64
import json
from collections import deque
from dataclasses import dataclass
from io import BytesIO
from time import time

import segno

from app.core.auth.totp import build_otpauth_uri, generate_totp_secret, verify_totp_code
from app.core.crypto import TokenEncryptor
from app.modules.dashboard_auth.repository import DashboardAuthRepository
from app.modules.dashboard_auth.schemas import DashboardAuthSessionResponse, TotpSetupStartResponse

DASHBOARD_SESSION_COOKIE = "codex_lb_dashboard_session"
_SESSION_TTL_SECONDS = 12 * 60 * 60
_TOTP_ISSUER = "codex-lb"
_TOTP_ACCOUNT = "dashboard"


class TotpAlreadyConfiguredError(ValueError):
    pass


class TotpNotConfiguredError(ValueError):
    pass


class TotpInvalidCodeError(ValueError):
    pass


@dataclass(slots=True)
class DashboardSessionState:
    expires_at: int
    totp_verified: bool


class DashboardSessionStore:
    def __init__(self) -> None:
        self._encryptor: TokenEncryptor | None = None

    def _get_encryptor(self) -> TokenEncryptor:
        if self._encryptor is None:
            self._encryptor = TokenEncryptor()
        return self._encryptor

    def create(self, *, totp_verified: bool) -> str:
        expires_at = int(time()) + _SESSION_TTL_SECONDS
        payload = json.dumps({"exp": expires_at, "tv": totp_verified}, separators=(",", ":"))
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
        tv = data.get("tv")
        if not isinstance(exp, int) or not isinstance(tv, bool):
            return None
        if exp < int(time()):
            return None
        return DashboardSessionState(expires_at=exp, totp_verified=tv)

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
    def __init__(self, repository: DashboardAuthRepository, session_store: DashboardSessionStore) -> None:
        self._repository = repository
        self._session_store = session_store
        self._encryptor = TokenEncryptor()

    async def get_session_state(self, session_id: str | None) -> DashboardAuthSessionResponse:
        settings = await self._repository.get_settings()
        totp_required = settings.totp_required_on_login
        totp_configured = settings.totp_secret_encrypted is not None
        authenticated = True
        if totp_required:
            authenticated = self._session_store.is_totp_verified(session_id)
        return DashboardAuthSessionResponse(
            authenticated=authenticated,
            totp_required_on_login=totp_required,
            totp_configured=totp_configured,
        )

    async def start_totp_setup(self) -> TotpSetupStartResponse:
        settings = await self._repository.get_settings()
        if settings.totp_secret_encrypted is not None:
            raise TotpAlreadyConfiguredError("TOTP is already configured. Disable it before setting a new secret")
        secret = generate_totp_secret()
        otpauth_uri = build_otpauth_uri(secret, issuer=_TOTP_ISSUER, account_name=_TOTP_ACCOUNT)
        return TotpSetupStartResponse(
            secret=secret,
            otpauth_uri=otpauth_uri,
            qr_svg_data_uri=_qr_svg_data_uri(otpauth_uri),
        )

    async def confirm_totp_setup(self, secret: str, code: str) -> None:
        current = await self._repository.get_settings()
        if current.totp_secret_encrypted is not None:
            raise TotpAlreadyConfiguredError("TOTP is already configured. Disable it before setting a new secret")
        verification = verify_totp_code(secret, code, window=1)
        if not verification.is_valid:
            raise TotpInvalidCodeError("Invalid TOTP code")
        await self._repository.set_totp_secret(self._encryptor.encrypt(secret))

    async def verify_totp(self, code: str) -> str:
        settings = await self._repository.get_settings()
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
        await self._repository.set_totp_last_verified_step(verification.matched_step)
        return self._session_store.create(totp_verified=True)

    async def disable_totp(self, code: str) -> None:
        settings = await self._repository.get_settings()
        secret_encrypted = settings.totp_secret_encrypted
        if secret_encrypted is None:
            raise TotpNotConfiguredError("TOTP is not configured")
        secret = self._encryptor.decrypt(secret_encrypted)
        verification = verify_totp_code(secret, code, window=1)
        if not verification.is_valid:
            raise TotpInvalidCodeError("Invalid TOTP code")
        await self._repository.set_totp_secret(None)

    def logout(self, session_id: str | None) -> None:
        self._session_store.delete(session_id)


_dashboard_session_store = DashboardSessionStore()
_totp_rate_limiter = TotpRateLimiter(max_failures=8, window_seconds=60)


def get_dashboard_session_store() -> DashboardSessionStore:
    return _dashboard_session_store


def get_totp_rate_limiter() -> TotpRateLimiter:
    return _totp_rate_limiter


def _qr_svg_data_uri(payload: str) -> str:
    qr = segno.make(payload)
    buffer = BytesIO()
    qr.save(buffer, kind="svg", xmldecl=False, scale=6, border=2)
    raw = buffer.getvalue()
    return f"data:image/svg+xml;base64,{base64.b64encode(raw).decode('ascii')}"
