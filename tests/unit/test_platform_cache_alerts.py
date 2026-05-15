from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.proxy import platform_cache_alerts
from app.modules.proxy.platform_cache_alerts import PlatformCacheAlertService


class _FakePostResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False

    async def text(self) -> str:
        return ""


class _FakeAlertSession:
    def __init__(self) -> None:
        self.post_calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs):
        self.post_calls.append({"url": url, **kwargs})
        return _FakePostResponse()


def _install_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cooldown_seconds: float = 300.0,
) -> None:
    settings = SimpleNamespace(
        platform_cache_alert_window_size=7,
        platform_cache_alert_threshold=4,
        platform_cache_alert_timeout_seconds=2.0,
        platform_cache_alert_cooldown_seconds=cooldown_seconds,
    )
    monkeypatch.setattr(platform_cache_alerts, "get_settings", lambda: settings)


@pytest.mark.asyncio
async def test_platform_cache_alert_fires_on_four_uncached_requests_in_seven(monkeypatch):
    _install_settings(monkeypatch)
    posts: list[tuple[str, str, str | None, float]] = []

    async def sender(url: str, suffix: str, client_version: str | None, timeout: float) -> None:
        posts.append((url, suffix, client_version, timeout))

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
        await service.observe(
            api_key_suffix="sk-platform-test",
            client_version="0.120.0",
            input_tokens=input_tokens,
            cached_input_tokens=cached,
        )
        for input_tokens, cached in observations
    ]

    assert results == [False, False, False, False, False, False, True]
    assert posts == [("https://codex-lb-alert.cinamon.io/notify", "test", "0.120.0", 2.0)]


@pytest.mark.asyncio
async def test_platform_cache_alert_ignores_fewer_than_four_misses(monkeypatch):
    _install_settings(monkeypatch)
    posts: list[str] = []

    async def sender(url: str, suffix: str, client_version: str | None, timeout: float) -> None:
        del url, client_version, timeout
        posts.append(suffix)

    service = PlatformCacheAlertService(sender=sender)
    observations = [(10, 0), (10, 3), (10, 0), (10, 4), (10, 1), (10, 0), (10, 2)]

    for input_tokens, cached in observations:
        assert not await service.observe(
            api_key_suffix="abcd",
            client_version="0.120.0",
            input_tokens=input_tokens,
            cached_input_tokens=cached,
        )

    assert posts == []


@pytest.mark.asyncio
async def test_platform_cache_alert_ignores_missing_api_key_suffix(monkeypatch):
    _install_settings(monkeypatch)
    posts: list[tuple[str, str | None]] = []

    async def sender(url: str, suffix: str, client_version: str | None, timeout: float) -> None:
        del url, timeout
        posts.append((suffix, client_version))

    service = PlatformCacheAlertService(sender=sender)

    for _ in range(7):
        assert not await service.observe(
            api_key_suffix=None,
            client_version="0.120.0",
            input_tokens=10,
            cached_input_tokens=0,
        )

    assert posts == []


@pytest.mark.asyncio
async def test_platform_cache_alert_cooldown_suppresses_duplicates(monkeypatch):
    _install_settings(monkeypatch, cooldown_seconds=10.0)
    now = 100.0
    posts: list[str] = []

    async def sender(url: str, suffix: str, client_version: str | None, timeout: float) -> None:
        del url, client_version, timeout
        posts.append(suffix)

    service = PlatformCacheAlertService(sender=sender, clock=lambda: now)

    for _ in range(7):
        await service.observe(api_key_suffix="wxyz", client_version="0.120.0", input_tokens=10, cached_input_tokens=0)
    assert posts == ["wxyz"]

    for _ in range(7):
        await service.observe(api_key_suffix="wxyz", client_version="0.120.0", input_tokens=10, cached_input_tokens=0)
    assert posts == ["wxyz"]

    now = 111.0
    await service.observe(api_key_suffix="wxyz", client_version="0.120.0", input_tokens=10, cached_input_tokens=0)
    assert posts == ["wxyz", "wxyz"]


@pytest.mark.asyncio
async def test_platform_cache_alert_sender_failure_does_not_raise(monkeypatch):
    _install_settings(monkeypatch)

    async def sender(url: str, suffix: str, client_version: str | None, timeout: float) -> None:
        del url, suffix, client_version, timeout
        raise RuntimeError("alert proxy unavailable")

    service = PlatformCacheAlertService(sender=sender)

    result = False
    for _ in range(7):
        result = await service.observe(
            api_key_suffix="abcd",
            client_version="0.120.0",
            input_tokens=10,
            cached_input_tokens=0,
        )

    assert result is False


@pytest.mark.asyncio
async def test_platform_cache_alert_failure_suppresses_all_alerts_for_one_hour(monkeypatch):
    _install_settings(monkeypatch)
    now = 100.0
    attempts: list[str] = []

    async def sender(url: str, suffix: str, client_version: str | None, timeout: float) -> None:
        del url, client_version, timeout
        attempts.append(suffix)
        raise RuntimeError("alert proxy unavailable")

    service = PlatformCacheAlertService(sender=sender, clock=lambda: now)

    for _ in range(7):
        await service.observe(api_key_suffix="key-a", client_version="0.120.0", input_tokens=10, cached_input_tokens=0)
    assert attempts == ["ey-a"]

    for _ in range(7):
        await service.observe(api_key_suffix="key-b", client_version="0.121.0", input_tokens=10, cached_input_tokens=0)
    assert attempts == ["ey-a"]

    now = 3701.0
    for _ in range(7):
        await service.observe(api_key_suffix="key-b", client_version="0.121.0", input_tokens=10, cached_input_tokens=0)
    assert attempts == ["ey-a", "ey-b"]


@pytest.mark.asyncio
async def test_platform_cache_alert_post_sends_json_payload(monkeypatch):
    session = _FakeAlertSession()
    monkeypatch.setattr(platform_cache_alerts, "get_http_client", lambda: SimpleNamespace(session=session))

    await platform_cache_alerts._post_alert(
        "https://codex-lb-alert.cinamon.io/notify",
        "abcd",
        "0.120.0",
        2.0,
    )

    assert session.post_calls == [
        {
            "url": "https://codex-lb-alert.cinamon.io/notify",
            "json": {"api_key_suffix": "abcd", "client_version": "0.120.0"},
        }
    ]
