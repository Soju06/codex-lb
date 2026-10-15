"""Process cache over ``dashboard_users`` for the request-path session check.

Mirrors :class:`app.core.config.settings_cache.SettingsCache`: a 5 s TTL bounds
staleness inside one replica, and every user write bumps the
``dashboard_users`` invalidation namespace so peer replicas drop their copy
before their next poll. Entries are keyed per user id / username and the
derived :class:`LocalAuthState` is cached as one value; ``invalidate`` drops
everything at once because any user write can change the derived state.
"""

from __future__ import annotations

import time

import anyio

from app.core.cache.invalidation import NAMESPACE_DASHBOARD_USERS, get_cache_invalidation_poller
from app.db.models import DashboardUser
from app.db.session import SessionLocal
from app.modules.dashboard_users.repository import (
    DashboardUsersRepository,
    LocalAuthState,
    is_valid_username,
    normalize_username,
)

__all__ = [
    "DashboardUsersCache",
    "LocalAuthState",
    "get_dashboard_users_cache",
    "is_valid_username",
    "normalize_username",
]


class DashboardUsersCache:
    def __init__(self, *, ttl_seconds: float = 5.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._users_by_id: dict[str, tuple[DashboardUser | None, float]] = {}
        self._users_by_username: dict[str, tuple[DashboardUser | None, float]] = {}
        self._state: tuple[LocalAuthState, float] | None = None
        self._lock = anyio.Lock()

    def _fresh(self, cached_at: float, now: float) -> bool:
        return now - cached_at < self._ttl_seconds

    async def get_user(self, user_id: str) -> DashboardUser | None:
        now = time.monotonic()
        entry = self._users_by_id.get(user_id)
        if entry is not None and self._fresh(entry[1], now):
            return entry[0]
        async with self._lock:
            now = time.monotonic()
            entry = self._users_by_id.get(user_id)
            if entry is not None and self._fresh(entry[1], now):
                return entry[0]
            async with SessionLocal() as session:
                user = await DashboardUsersRepository(session).get_by_id(user_id)
            self._users_by_id[user_id] = (user, now)
            return user

    async def get_user_by_username(self, normalized_username: str) -> DashboardUser | None:
        now = time.monotonic()
        entry = self._users_by_username.get(normalized_username)
        if entry is not None and self._fresh(entry[1], now):
            return entry[0]
        async with self._lock:
            now = time.monotonic()
            entry = self._users_by_username.get(normalized_username)
            if entry is not None and self._fresh(entry[1], now):
                return entry[0]
            async with SessionLocal() as session:
                user = await DashboardUsersRepository(session).get_by_username(normalized_username)
            self._users_by_username[normalized_username] = (user, now)
            return user

    async def local_auth_state(self) -> LocalAuthState:
        now = time.monotonic()
        if self._state is not None and self._fresh(self._state[1], now):
            return self._state[0]
        async with self._lock:
            now = time.monotonic()
            if self._state is not None and self._fresh(self._state[1], now):
                return self._state[0]
            async with SessionLocal() as session:
                state = await DashboardUsersRepository(session).local_auth_state()
            self._state = (state, now)
            return state

    def clear(self) -> None:
        """Drop every cached entry without touching the invalidation bus (tests, pollers)."""

        self._users_by_id.clear()
        self._users_by_username.clear()
        self._state = None

    async def invalidate(self, *, propagate: bool = True) -> None:
        """Drop the cache and, unless ``propagate`` is False, durably bump the
        cross-replica ``dashboard_users`` namespace before returning.

        User writes are security-bearing (credentials, status, session
        generation), so the bump is awaited rather than coalesced. The poller
        callback registers ``propagate=False`` so a remote bump never re-bumps.
        """

        async with self._lock:
            self.clear()
        if propagate:
            poller = get_cache_invalidation_poller()
            if poller is not None:
                await poller.bump(NAMESPACE_DASHBOARD_USERS)


_dashboard_users_cache = DashboardUsersCache()


def get_dashboard_users_cache() -> DashboardUsersCache:
    return _dashboard_users_cache
