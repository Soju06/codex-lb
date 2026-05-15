from __future__ import annotations

import time
from dataclasses import dataclass

import anyio

from app.core.config.settings import get_settings


@dataclass(slots=True)
class _CachedFirewallDecision:
    allowed: bool
    expires_at: float


class FirewallIPCache:
    def __init__(self, ttl_seconds: int = 15) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._cache: dict[str, _CachedFirewallDecision] = {}
        self._lock = anyio.Lock()
        self._version = 0

    @property
    def version(self) -> int:
        return self._version

    async def is_allowed(self, ip: str) -> bool | None:
        entry = self._cache.get(ip)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            return None
        return entry.allowed

    async def set(self, ip: str, allowed: bool, *, if_version: int | None = None) -> None:
        async with self._lock:
            if if_version is not None and if_version != self._version:
                return
            self._cache[ip] = _CachedFirewallDecision(
                allowed=allowed,
                expires_at=time.monotonic() + self._ttl_seconds,
            )

    def invalidate_all(self) -> None:
        self._cache.clear()
        self._version += 1


_firewall_ip_cache = FirewallIPCache(ttl_seconds=get_settings().firewall_cache_ttl_seconds)


def get_firewall_ip_cache() -> FirewallIPCache:
    return _firewall_ip_cache
