from __future__ import annotations

import base64

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

    def get(self, url: str, timeout=None, allow_redirects: bool = False):
        return self._response


@pytest.mark.asyncio
async def test_fetch_image_data_url_success():
    body = b"abc"
    response = FakeResponse(200, {"Content-Type": "image/png"}, [body])
    session = FakeSession(response)

    data_url = await proxy_module._fetch_image_data_url(session, "https://example.com/a.png", 1.0)

    expected = "data:image/png;base64," + base64.b64encode(body).decode("ascii")
    assert data_url == expected


@pytest.mark.asyncio
async def test_fetch_image_data_url_failure_status():
    response = FakeResponse(404, {"Content-Type": "image/png"}, [b"abc"])
    session = FakeSession(response)

    data_url = await proxy_module._fetch_image_data_url(session, "https://example.com/a.png", 1.0)

    assert data_url is None


@pytest.mark.asyncio
async def test_fetch_image_data_url_size_limit(monkeypatch):
    monkeypatch.setattr(proxy_module, "_IMAGE_INLINE_MAX_BYTES", 4)
    response = FakeResponse(200, {"Content-Type": "image/png"}, [b"12345"])
    session = FakeSession(response)

    data_url = await proxy_module._fetch_image_data_url(session, "https://example.com/a.png", 1.0)

    assert data_url is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/a.png", True),
        ("http://example.com/a.png", False),
        ("https://127.0.0.1/a.png", False),
        ("https://localhost/a.png", False),
        ("https://169.254.169.254/a.png", False),
    ],
)
def test_is_safe_image_fetch_url(monkeypatch, url: str, expected: bool):
    monkeypatch.setattr(proxy_module, "_resolves_to_blocked_ip", lambda host: False)
    assert proxy_module._is_safe_image_fetch_url(url) is expected


def test_is_safe_image_fetch_url_blocks_resolved_private_ip(monkeypatch):
    monkeypatch.setattr(proxy_module, "_resolves_to_blocked_ip", lambda host: host == "example.com")
    assert proxy_module._is_safe_image_fetch_url("https://example.com/a.png") is False


def test_is_safe_image_fetch_url_respects_allowlist(monkeypatch):
    monkeypatch.setattr(proxy_module, "_resolves_to_blocked_ip", lambda host: False)
    settings = proxy_module.get_settings()
    original = settings.image_inline_allowed_hosts
    original_enabled = settings.image_inline_fetch_enabled
    settings.image_inline_fetch_enabled = True
    settings.image_inline_allowed_hosts = ["allowed.example"]
    try:
        assert proxy_module._is_safe_image_fetch_url("https://allowed.example/a.png") is True
        assert proxy_module._is_safe_image_fetch_url("https://denied.example/a.png") is False
    finally:
        settings.image_inline_fetch_enabled = original_enabled
        settings.image_inline_allowed_hosts = original


def test_is_safe_image_fetch_url_blocks_when_feature_disabled(monkeypatch):
    monkeypatch.setattr(proxy_module, "_resolves_to_blocked_ip", lambda host: False)
    settings = proxy_module.get_settings()
    original_enabled = settings.image_inline_fetch_enabled
    original_hosts = settings.image_inline_allowed_hosts
    settings.image_inline_fetch_enabled = False
    settings.image_inline_allowed_hosts = []
    try:
        assert proxy_module._is_safe_image_fetch_url("https://example.com/a.png") is False
    finally:
        settings.image_inline_fetch_enabled = original_enabled
        settings.image_inline_allowed_hosts = original_hosts
