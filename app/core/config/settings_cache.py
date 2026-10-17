from __future__ import annotations

import time

import anyio

from app.core.cache.invalidation import NAMESPACE_SETTINGS, get_cache_invalidation_poller
from app.db.models import DashboardSettings
from app.db.session import SessionLocal
from app.modules.settings.repository import SettingsRepository


class SettingsCache:
    def __init__(self, *, ttl_seconds: float = 5.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._cached_settings: DashboardSettings | None = None
        self._cached_at = 0.0
        # The last row ever loaded, kept across ``invalidate`` for the
        # fallback readers of ``cached_row`` (see its docstring).
        self._last_loaded_settings: DashboardSettings | None = None
        self._lock = anyio.Lock()

    async def get(self) -> DashboardSettings:
        now = time.monotonic()
        if self._cached_settings is not None and now - self._cached_at < self._ttl_seconds:
            return self._cached_settings

        async with self._lock:
            now = time.monotonic()
            if self._cached_settings is not None and now - self._cached_at < self._ttl_seconds:
                return self._cached_settings

            async with SessionLocal() as session:
                settings = await SettingsRepository(session).get_or_create()
                self._cached_settings = settings
                self._last_loaded_settings = settings
                self._cached_at = now
                return settings

    async def refresh(self) -> DashboardSettings:
        """Load the row now and make it the current snapshot.

        Registered as a cache-invalidation callback for the ``settings``
        namespace so that a dashboard change reaches every replica on the bus
        instead of waiting for the next request to pull it in. That bound
        matters for readers that cannot await — the conversation archive gate
        runs per archived frame, and a replica carrying only already-open
        streams would otherwise keep the previous value indefinitely.
        """
        async with self._lock:
            async with SessionLocal() as session:
                settings = await SettingsRepository(session).get_or_create()
            self._cached_settings = settings
            self._last_loaded_settings = settings
            self._cached_at = time.monotonic()
            return settings

    def cached_row(self) -> DashboardSettings | None:
        """The last row this cache loaded, even if past its TTL or invalidated.

        ``None`` only before the first successful load. For callers that must
        not fail when a refresh is impossible or has not happened yet (the
        request-scoped dashboard overrides, the conversation archive gate) and
        prefer the last known dashboard values over silently reverting to the
        environment: an invalidation means "reload before trusting this as
        current", not "the operator never configured anything".
        """
        return self._last_loaded_settings

    async def invalidate(self, *, propagate: bool = True) -> None:
        """Drop the cached settings row and, unless ``propagate`` is False, durably
        bump the cross-replica ``settings`` namespace before returning.

        Settings mutations are security-bearing (password hash, guest access, TOTP,
        API-key auth toggle), so the bump is awaited rather than coalesced. The
        cache-invalidation poller callback registers ``propagate=False`` so a remote
        bump never re-bumps (feedback-loop prevention).

        ``cached_row`` keeps returning the last loaded row: dropping it here
        would make the fallback readers revert to the environment values in the
        window between an invalidation and the next load, which for a toggle
        such as the conversation archive means the deprecated environment alias
        could resume recording that an operator just turned off.
        """
        async with self._lock:
            self._cached_settings = None
            self._cached_at = 0.0
        if propagate:
            poller = get_cache_invalidation_poller()
            if poller is not None:
                await poller.bump(NAMESPACE_SETTINGS)


_settings_cache = SettingsCache()


def get_settings_cache() -> SettingsCache:
    return _settings_cache
