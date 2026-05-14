from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.proxy import platform_cache_alerts
from app.modules.proxy.platform_cache_alerts import PlatformCacheAlertService


def _install_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    proxy_url: str | None = "http://alerts.local",
    cooldown_seconds: float = 300.0,
) -> None:
    settings = SimpleNamespace(
        platform_cache_alert_proxy_url=proxy_url,
        platform_cache_alert_window_size=7,
        platform_cache_alert_threshold=4,
        platform_cache_alert_timeout_seconds=2.0,
        platform_cache_alert_cooldown_seconds=cooldown_seconds,
    )
    monkeypatch.setattr(platform_cache_alerts, "get_settings", lambda: settings)


@pytest.mark.asyncio
async def test_platform_cache_alert_fires_on_four_uncached_requests_in_seven(monkeypatch):
    _install_settings(monkeypatch)
    posts: list[tuple[str, str, float]] = []

    async def sender(url: str, suffix: str, timeout: float) -> None:
        posts.append((url, suffix, timeout))

    service = PlatformCacheAlertService(sender=sender)
    observations = [
        (10, 0),
        (10, 4),
        (10, None),
        (10, 2),
        (10, 0),
        (10, 1),
        (10, 0),
    ]

    results = [
        await service.observe(api_key_suffix="sk-platform-test", input_tokens=input_tokens, cached_input_tokens=cached)
        for input_tokens, cached in observations
    ]

    assert results == [False, False, False, False, False, False, True]
    assert posts == [("http://alerts.local", "test", 2.0)]


@pytest.mark.asyncio
async def test_platform_cache_alert_ignores_fewer_than_four_misses(monkeypatch):
    _install_settings(monkeypatch)
    posts: list[str] = []

    async def sender(url: str, suffix: str, timeout: float) -> None:
        del url, timeout
        posts.append(suffix)

    service = PlatformCacheAlertService(sender=sender)
    observations = [(10, 0), (10, 3), (10, 0), (10, 4), (10, 1), (10, 0), (10, 2)]

    for input_tokens, cached in observations:
        assert not await service.observe(
            api_key_suffix="abcd",
            input_tokens=input_tokens,
            cached_input_tokens=cached,
        )

    assert posts == []


@pytest.mark.asyncio
async def test_platform_cache_alert_is_disabled_without_proxy_url(monkeypatch):
    _install_settings(monkeypatch, proxy_url=None)
    posts: list[str] = []

    async def sender(url: str, suffix: str, timeout: float) -> None:
        del url, timeout
        posts.append(suffix)

    service = PlatformCacheAlertService(sender=sender)

    for _ in range(7):
        assert not await service.observe(
            api_key_suffix="abcd",
            input_tokens=10,
            cached_input_tokens=0,
        )

    assert posts == []


@pytest.mark.asyncio
async def test_platform_cache_alert_cooldown_suppresses_duplicates(monkeypatch):
    _install_settings(monkeypatch, cooldown_seconds=10.0)
    now = 100.0
    posts: list[str] = []

    async def sender(url: str, suffix: str, timeout: float) -> None:
        del url, timeout
        posts.append(suffix)

    service = PlatformCacheAlertService(sender=sender, clock=lambda: now)

    for _ in range(7):
        await service.observe(api_key_suffix="wxyz", input_tokens=10, cached_input_tokens=0)
    assert posts == ["wxyz"]

    for _ in range(7):
        await service.observe(api_key_suffix="wxyz", input_tokens=10, cached_input_tokens=0)
    assert posts == ["wxyz"]

    now = 111.0
    await service.observe(api_key_suffix="wxyz", input_tokens=10, cached_input_tokens=0)
    assert posts == ["wxyz", "wxyz"]


@pytest.mark.asyncio
async def test_platform_cache_alert_sender_failure_does_not_raise(monkeypatch):
    _install_settings(monkeypatch)

    async def sender(url: str, suffix: str, timeout: float) -> None:
        del url, suffix, timeout
        raise RuntimeError("alert proxy unavailable")

    service = PlatformCacheAlertService(sender=sender)

    result = False
    for _ in range(7):
        result = await service.observe(api_key_suffix="abcd", input_tokens=10, cached_input_tokens=0)

    assert result is False
