from __future__ import annotations

import time
from dataclasses import dataclass

import anyio


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

    async def is_allowed(self, ip: str) -> bool | None:
        entry = self._cache.get(ip)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            return None
        return entry.allowed

    async def set(self, ip: str, allowed: bool) -> None:
        async with self._lock:
            self._cache[ip] = _CachedFirewallDecision(
                allowed=allowed,
                expires_at=time.monotonic() + self._ttl_seconds,
            )

    def invalidate_all(self) -> None:
        self._cache.clear()


_firewall_ip_cache = FirewallIPCache()


def get_firewall_ip_cache() -> FirewallIPCache:
    return _firewall_ip_cache
