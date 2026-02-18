from __future__ import annotations

import logging
import re
import time

import aiohttp
import anyio

from app.core.config.settings import get_settings

logger = logging.getLogger(__name__)

_GITHUB_RELEASES_URL = "https://api.github.com/repos/openai/codex/releases/latest"
_FETCH_TIMEOUT_SECONDS = 10.0
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


class CodexVersionCache:
    def __init__(self, *, ttl_seconds: float = 3600.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._cached_version: str | None = None
        self._cached_at = 0.0
        self._lock = anyio.Lock()

    async def get_version(self) -> str:
        now = time.monotonic()
        if self._cached_version is not None and now - self._cached_at < self._ttl_seconds:
            return self._cached_version

        async with self._lock:
            now = time.monotonic()
            if self._cached_version is not None and now - self._cached_at < self._ttl_seconds:
                return self._cached_version

            version = await self._fetch_latest_version()
            if version is not None:
                self._cached_version = version
                self._cached_at = now
                return version

            # Fallback: stale cache value
            if self._cached_version is not None:
                logger.warning("GitHub fetch failed; using stale cached version %s", self._cached_version)
                return self._cached_version

            # Fallback: settings default
            fallback = get_settings().model_registry_client_version
            logger.warning("GitHub fetch failed and no cached version; falling back to settings default %s", fallback)
            return fallback

    async def invalidate(self) -> None:
        async with self._lock:
            self._cached_version = None
            self._cached_at = 0.0

    async def _fetch_latest_version(self) -> str | None:
        try:
            timeout = aiohttp.ClientTimeout(total=_FETCH_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                headers = {"Accept": "application/vnd.github+json"}
                async with session.get(_GITHUB_RELEASES_URL, headers=headers) as resp:
                    if resp.status != 200:
                        logger.warning("GitHub releases API returned HTTP %d", resp.status)
                        return None
                    data = await resp.json(content_type=None)
        except Exception:
            logger.warning("Failed to fetch latest Codex release from GitHub", exc_info=True)
            return None

        name = data.get("name") if isinstance(data, dict) else None
        if not isinstance(name, str) or not _VERSION_RE.match(name):
            logger.warning("Unexpected release name from GitHub: %r", name)
            return None

        logger.info("Fetched latest Codex version from GitHub: %s", name)
        return name


_codex_version_cache = CodexVersionCache()


def get_codex_version_cache() -> CodexVersionCache:
    return _codex_version_cache
