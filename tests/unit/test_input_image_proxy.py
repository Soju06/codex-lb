from __future__ import annotations

import base64
from typing import cast

import pytest

import app.core.clients.proxy as proxy_module


class FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, size: int):
        for chunk in self._chunks:
            yield chunk


class FakeResponse:
    def __init__(self, status: int, headers: dict[str, str], chunks: list[bytes]) -> None:
        self.status = status
        self.headers = headers
        self.content = FakeContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def get(
        self,
        url: str,
        timeout=None,
        allow_redirects: bool = False,
        headers: dict[str, str] | None = None,
        server_hostname: str | None = None,
    ):
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "server_hostname": server_hostname,
                "allow_redirects": allow_redirects,
            }
        )
        return self._response


@pytest.mark.asyncio
async def test_fetch_image_data_url_success(monkeypatch):
    async def resolve_ips(host: str, *, timeout_seconds: float):
        return ["93.184.216.34"]

    monkeypatch.setattr(proxy_module, "_resolve_global_ips", resolve_ips)
    body = b"abc"
    response = FakeResponse(200, {"Content-Type": "image/png"}, [body])
    session = FakeSession(response)

    data_url = await proxy_module._fetch_image_data_url(
        cast(proxy_module.ImageFetchSession, session),
        "https://example.com/a.png",
        1.0,
    )

    expected = "data:image/png;base64," + base64.b64encode(body).decode("ascii")
    assert data_url == expected
    assert session.calls
    first_call = session.calls[0]
    assert first_call["url"] == "https://93.184.216.34/a.png"
    assert first_call["headers"] == {"Host": "example.com"}
    assert first_call["server_hostname"] == "example.com"


@pytest.mark.asyncio
async def test_fetch_image_data_url_failure_status(monkeypatch):
    async def resolve_ips(host: str, *, timeout_seconds: float):
        return ["93.184.216.34"]

    monkeypatch.setattr(proxy_module, "_resolve_global_ips", resolve_ips)
    response = FakeResponse(404, {"Content-Type": "image/png"}, [b"abc"])
    session = FakeSession(response)

    data_url = await proxy_module._fetch_image_data_url(
        cast(proxy_module.ImageFetchSession, session),
        "https://example.com/a.png",
        1.0,
    )

    assert data_url is None


@pytest.mark.asyncio
async def test_fetch_image_data_url_size_limit(monkeypatch):
    monkeypatch.setattr(proxy_module, "_IMAGE_INLINE_MAX_BYTES", 4)

    async def resolve_ips(host: str, *, timeout_seconds: float):
        return ["93.184.216.34"]

    monkeypatch.setattr(proxy_module, "_resolve_global_ips", resolve_ips)
    response = FakeResponse(200, {"Content-Type": "image/png"}, [b"12345"])
    session = FakeSession(response)

    data_url = await proxy_module._fetch_image_data_url(
        cast(proxy_module.ImageFetchSession, session),
        "https://example.com/a.png",
        1.0,
    )

    assert data_url is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/a.png", True),
        ("http://example.com/a.png", False),
        ("https://127.0.0.1/a.png", False),
        ("https://localhost/a.png", False),
        ("https://100.64.0.1/a.png", False),
        ("https://169.254.169.254/a.png", False),
    ],
)
@pytest.mark.asyncio
async def test_is_safe_image_fetch_url(monkeypatch, url: str, expected: bool):
    async def resolve_ips(host: str, *, timeout_seconds: float):
        return ["93.184.216.34"]

    monkeypatch.setattr(proxy_module, "_resolve_global_ips", resolve_ips)
    assert await proxy_module._is_safe_image_fetch_url(url, connect_timeout=1.0) is expected


@pytest.mark.asyncio
async def test_is_safe_image_fetch_url_blocks_resolved_private_ip(monkeypatch):
    async def resolve_none(host: str, *, timeout_seconds: float):
        return None

    monkeypatch.setattr(proxy_module, "_resolve_global_ips", resolve_none)
    assert await proxy_module._is_safe_image_fetch_url("https://example.com/a.png", connect_timeout=1.0) is False


@pytest.mark.asyncio
async def test_is_safe_image_fetch_url_respects_allowlist(monkeypatch):
    async def resolve_ips(host: str, *, timeout_seconds: float):
        return ["93.184.216.34"]

    monkeypatch.setattr(proxy_module, "_resolve_global_ips", resolve_ips)
    settings = proxy_module.get_settings()
    original = settings.image_inline_allowed_hosts
    original_enabled = settings.image_inline_fetch_enabled
    settings.image_inline_fetch_enabled = True
    settings.image_inline_allowed_hosts = ["allowed.example"]
    try:
        assert await proxy_module._is_safe_image_fetch_url("https://allowed.example/a.png", connect_timeout=1.0)
        assert not await proxy_module._is_safe_image_fetch_url("https://denied.example/a.png", connect_timeout=1.0)
    finally:
        settings.image_inline_fetch_enabled = original_enabled
        settings.image_inline_allowed_hosts = original


@pytest.mark.asyncio
async def test_is_safe_image_fetch_url_blocks_when_feature_disabled(monkeypatch):
    async def resolve_ips(host: str, *, timeout_seconds: float):
        return ["93.184.216.34"]

    monkeypatch.setattr(proxy_module, "_resolve_global_ips", resolve_ips)
    settings = proxy_module.get_settings()
    original_enabled = settings.image_inline_fetch_enabled
    original_hosts = settings.image_inline_allowed_hosts
    settings.image_inline_fetch_enabled = False
    settings.image_inline_allowed_hosts = []
    try:
        assert not await proxy_module._is_safe_image_fetch_url("https://example.com/a.png", connect_timeout=1.0)
    finally:
        settings.image_inline_fetch_enabled = original_enabled
        settings.image_inline_allowed_hosts = original_hosts


@pytest.mark.asyncio
async def test_fetch_image_data_url_uses_fallback_ip_when_first_fails(monkeypatch):
    async def resolve_ips(host: str, *, timeout_seconds: float):
        return ["2001:db8::1", "93.184.216.34"]

    monkeypatch.setattr(proxy_module, "_resolve_global_ips", resolve_ips)

    class FallbackSession:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self._ok_response = FakeResponse(200, {"Content-Type": "image/png"}, [b"ok"])

        def get(
            self,
            url: str,
            timeout=None,
            allow_redirects: bool = False,
            headers: dict[str, str] | None = None,
            server_hostname: str | None = None,
        ):
            self.calls.append({"url": url, "headers": headers, "server_hostname": server_hostname})
            if "[2001:db8::1]" in url:
                raise proxy_module.aiohttp.ClientError("connect failed")
            return self._ok_response

    session = FallbackSession()
    data_url = await proxy_module._fetch_image_data_url(
        cast(proxy_module.ImageFetchSession, session),
        "https://example.com/a.png",
        1.0,
    )

    assert data_url is not None
    assert len(session.calls) == 2
    assert session.calls[0]["url"] == "https://[2001:db8::1]/a.png"
    assert session.calls[1]["url"] == "https://93.184.216.34/a.png"
