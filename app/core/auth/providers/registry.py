"""Process cache over ``dashboard_auth_providers`` joined with the auth mode.

Same shape as :class:`app.core.auth.dashboard_users_cache.DashboardUsersCache`:
a 5 s TTL bounds staleness inside one replica, and a provider write bumps the
``dashboard_users`` invalidation namespace (providers govern how identities
become users, so the two caches share one bus and one callback).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import anyio

from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.auth.providers import AuthProvider, PasswordProvider, TrustedHeaderProvider
from app.core.cache.invalidation import NAMESPACE_DASHBOARD_USERS, get_cache_invalidation_poller
from app.db.models import AuthProviderKind, DashboardAuthProvider
from app.db.session import SessionLocal
from app.modules.auth_providers.repository import AuthProvidersRepository

_IMPLEMENTATIONS: dict[AuthProviderKind, AuthProvider] = {
    AuthProviderKind.PASSWORD: PasswordProvider(),
    AuthProviderKind.TRUSTED_HEADER: TrustedHeaderProvider(),
}


def provider_active(row: DashboardAuthProvider, mode: DashboardAuthMode) -> bool:
    """A provider serves sign-ins when its row is enabled and the auth mode admits its kind.

    ``password`` follows the mode's existing rules (management is refused in
    ``disabled`` mode elsewhere), ``trusted_header`` needs the matching mode,
    and a kind without an implementation (``oidc`` before Phase 3) is never
    active.
    """

    if not row.enabled:
        return False
    try:
        kind = AuthProviderKind(row.kind)
    except ValueError:
        return False
    if kind not in _IMPLEMENTATIONS:
        return False
    if kind is AuthProviderKind.TRUSTED_HEADER:
        return mode == DashboardAuthMode.TRUSTED_HEADER
    return True


@dataclass(frozen=True, slots=True)
class ActiveProvider:
    row: DashboardAuthProvider
    provider: AuthProvider


class AuthProviderRegistry:
    def __init__(self, *, ttl_seconds: float = 5.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._rows: tuple[tuple[DashboardAuthProvider, ...], float] | None = None
        self._lock = anyio.Lock()

    async def rows(self) -> tuple[DashboardAuthProvider, ...]:
        now = time.monotonic()
        if self._rows is not None and now - self._rows[1] < self._ttl_seconds:
            return self._rows[0]
        async with self._lock:
            now = time.monotonic()
            if self._rows is not None and now - self._rows[1] < self._ttl_seconds:
                return self._rows[0]
            async with SessionLocal() as session:
                rows = tuple(await AuthProvidersRepository(session).list_providers())
            self._rows = (rows, now)
            return rows

    async def get_active_providers(self, mode: DashboardAuthMode) -> list[ActiveProvider]:
        """Active providers in table order; the password provider always lists first."""

        active = [
            ActiveProvider(row=row, provider=_IMPLEMENTATIONS[AuthProviderKind(row.kind)])
            for row in await self.rows()
            if provider_active(row, mode)
        ]
        return sorted(active, key=lambda item: item.row.kind != AuthProviderKind.PASSWORD.value)

    async def get_active(
        self, kind: AuthProviderKind, provider_key: str, mode: DashboardAuthMode
    ) -> ActiveProvider | None:
        for item in await self.get_active_providers(mode):
            if item.row.kind == kind.value and item.row.provider_key == provider_key:
                return item
        return None

    def clear(self) -> None:
        self._rows = None

    async def invalidate(self, *, propagate: bool = True) -> None:
        async with self._lock:
            self.clear()
        if propagate:
            poller = get_cache_invalidation_poller()
            if poller is not None:
                await poller.bump(NAMESPACE_DASHBOARD_USERS)


_registry = AuthProviderRegistry()


def get_auth_provider_registry() -> AuthProviderRegistry:
    return _registry
