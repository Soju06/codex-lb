from __future__ import annotations

import time
from dataclasses import dataclass

import anyio

# Default TTL when the firewall cache is constructed before settings are
# resolved. The application normally tunes this via
# ``firewall_ip_cache_ttl_seconds`` (env ``CODEX_LB_FIREWALL_IP_CACHE_TTL_SECONDS``),
# read lazily on first access in :func:`get_firewall_ip_cache`. The default is
# intentionally well above the worst-case request-handling latency so the cache
# provides real relief on hot paths under load.
DEFAULT_FIREWALL_IP_CACHE_TTL_SECONDS = 30


@dataclass(slots=True)
class _CachedFirewallDecision:
    allowed: bool
    expires_at: float


class FirewallIPCache:
    def __init__(self, ttl_seconds: int = DEFAULT_FIREWALL_IP_CACHE_TTL_SECONDS) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._cache: dict[str, _CachedFirewallDecision] = {}
        self._lock = anyio.Lock()
        self._version = 0

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

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


_firewall_ip_cache: FirewallIPCache | None = None


def _resolve_configured_ttl() -> int:
    """Read the configured TTL from settings, falling back to the default.

    Settings access is wrapped because the cache may be constructed before the
    settings module is fully initialised (e.g. during early module-import in
    tests).
    """
    try:
        from app.core.config.settings import get_settings

        return int(get_settings().firewall_ip_cache_ttl_seconds)
    except Exception:  # pragma: no cover — defensive only
        return DEFAULT_FIREWALL_IP_CACHE_TTL_SECONDS


def get_firewall_ip_cache() -> FirewallIPCache:
    global _firewall_ip_cache
    if _firewall_ip_cache is None:
        _firewall_ip_cache = FirewallIPCache(ttl_seconds=_resolve_configured_ttl())
    return _firewall_ip_cache


def reset_firewall_ip_cache_for_testing() -> None:
    """Drop the cached singleton so the next ``get_firewall_ip_cache()`` call
    re-reads settings. Test-only helper."""
    global _firewall_ip_cache
    _firewall_ip_cache = None
