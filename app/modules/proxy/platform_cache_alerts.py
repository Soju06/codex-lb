from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Final
from urllib.parse import urlsplit

from app.core.clients.http import get_http_client
from app.core.config.settings import get_settings

logger = logging.getLogger(__name__)

ALERT_PROXY_URL: Final = "https://codex-lb-alert.cinamon.io"
ALERT_FAILURE_SUPPRESSION_SECONDS: Final = 60 * 60

AlertSender = Callable[[str, str, str | None, float], Awaitable[None]]


class PlatformCacheAlertService:
    def __init__(
        self,
        *,
        sender: AlertSender | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sender = sender or _post_alert
        self._clock = clock
        self._windows: dict[str, deque[bool]] = {}
        self._last_alert_at: dict[str, float] = {}
        self._failure_suppressed_until: float | None = None
        self._lock = asyncio.Lock()

    async def observe(
        self,
        *,
        api_key_suffix: str | None,
        client_version: str | None,
        input_tokens: int | None,
        cached_input_tokens: int | None,
    ) -> bool:
        settings = get_settings()
        alert_url = _notify_url(ALERT_PROXY_URL)
        suffix = _normalize_suffix(api_key_suffix)
        if suffix is None:
            return False
        normalized_client_version = _normalize_client_version(client_version)
        if input_tokens is None or input_tokens <= 0:
            return False

        window_size = max(int(getattr(settings, "platform_cache_alert_window_size", 7)), 1)
        threshold = max(int(getattr(settings, "platform_cache_alert_threshold", 4)), 1)
        threshold = min(threshold, window_size)
        cooldown_seconds = max(float(getattr(settings, "platform_cache_alert_cooldown_seconds", 300.0)), 0.0)
        timeout_seconds = max(float(getattr(settings, "platform_cache_alert_timeout_seconds", 2.0)), 0.001)
        uncached = cached_input_tokens is None or cached_input_tokens <= 0

        async with self._lock:
            window = self._window_for(suffix, window_size)
            window.append(uncached)
            if len(window) < window_size or sum(window) < threshold:
                return False
            now = self._clock()
            if self._failure_suppressed_until is not None and now < self._failure_suppressed_until:
                return False
            last_alert_at = self._last_alert_at.get(suffix)
            if last_alert_at is not None and now - last_alert_at < cooldown_seconds:
                return False
            self._last_alert_at[suffix] = now

        try:
            await self._sender(alert_url, suffix, normalized_client_version, timeout_seconds)
        except Exception:
            async with self._lock:
                self._failure_suppressed_until = self._clock() + ALERT_FAILURE_SUPPRESSION_SECONDS
            logger.warning("Failed to send Platform cache-miss alert api_key_suffix=%s", suffix, exc_info=True)
            return False
        return True

    def _window_for(self, suffix: str, window_size: int) -> deque[bool]:
        window = self._windows.get(suffix)
        if window is None:
            window = deque(maxlen=window_size)
            self._windows[suffix] = window
            return window
        if window.maxlen != window_size:
            window = deque(window, maxlen=window_size)
            self._windows[suffix] = window
        return window


async def _post_alert(alert_url: str, suffix: str, client_version: str | None, timeout_seconds: float) -> None:
    client = get_http_client()
    async with asyncio.timeout(timeout_seconds):
        async with client.session.post(
            alert_url,
            json={
                "api_key_suffix": suffix,
                "client_version": client_version,
            },
        ) as response:
            if response.status >= 400:
                text = await response.text()
                raise RuntimeError(f"alert proxy returned {response.status}: {text[:200]}")


def _normalize_suffix(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped[-4:]


def _normalize_client_version(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _notify_url(proxy_url: str) -> str:
    parsed = urlsplit(proxy_url)
    if parsed.path and parsed.path != "/":
        return proxy_url.rstrip("/")
    return f"{proxy_url.rstrip('/')}/notify"


_platform_cache_alert_service: PlatformCacheAlertService | None = None


def get_platform_cache_alert_service() -> PlatformCacheAlertService:
    global _platform_cache_alert_service
    if _platform_cache_alert_service is None:
        _platform_cache_alert_service = PlatformCacheAlertService()
    return _platform_cache_alert_service


def reset_platform_cache_alert_service() -> None:
    global _platform_cache_alert_service
    _platform_cache_alert_service = None
