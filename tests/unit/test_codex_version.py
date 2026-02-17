from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.core.clients.codex_version import CodexVersionCache


def _mock_response(*, status: int = 200, json_data: object = None) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _mock_session(response: MagicMock) -> MagicMock:
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@pytest.mark.asyncio
async def test_fetches_version_from_github():
    cache = CodexVersionCache(ttl_seconds=60)
    resp = _mock_response(json_data={"name": "1.2.3", "tag_name": "rust-v1.2.3"})
    session = _mock_session(resp)

    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session):
        version = await cache.get_version()

    assert version == "1.2.3"


@pytest.mark.asyncio
async def test_returns_cached_version_within_ttl():
    cache = CodexVersionCache(ttl_seconds=60)
    resp = _mock_response(json_data={"name": "1.2.3"})
    session = _mock_session(resp)

    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session):
        first = await cache.get_version()
        second = await cache.get_version()

    assert first == second == "1.2.3"
    # Only one HTTP call — second hit served from cache
    session.get.assert_called_once()


@pytest.mark.asyncio
async def test_refetches_after_ttl_expires():
    cache = CodexVersionCache(ttl_seconds=60)
    resp1 = _mock_response(json_data={"name": "1.0.0"})
    session1 = _mock_session(resp1)

    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session1):
        v1 = await cache.get_version()
    assert v1 == "1.0.0"

    # Expire the cache
    cache._cached_at = time.monotonic() - 120

    resp2 = _mock_response(json_data={"name": "2.0.0"})
    session2 = _mock_session(resp2)

    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session2):
        v2 = await cache.get_version()
    assert v2 == "2.0.0"


@pytest.mark.asyncio
async def test_rejects_invalid_version_name():
    cache = CodexVersionCache(ttl_seconds=60)
    resp = _mock_response(json_data={"name": "rust-v1.2.3"})
    session = _mock_session(resp)

    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session):
        version = await cache.get_version()

    # Invalid name falls back to settings default
    assert version == "0.101.0"


@pytest.mark.asyncio
async def test_rejects_alpha_version_name():
    cache = CodexVersionCache(ttl_seconds=60)
    resp = _mock_response(json_data={"name": "1.2.3-alpha.1"})
    session = _mock_session(resp)

    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session):
        version = await cache.get_version()

    assert version == "0.101.0"


@pytest.mark.asyncio
async def test_fallback_to_stale_cache_on_github_failure():
    cache = CodexVersionCache(ttl_seconds=60)

    # Populate cache
    resp_ok = _mock_response(json_data={"name": "1.5.0"})
    session_ok = _mock_session(resp_ok)
    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session_ok):
        v1 = await cache.get_version()
    assert v1 == "1.5.0"

    # Expire the cache
    cache._cached_at = time.monotonic() - 120

    # GitHub returns error
    resp_fail = _mock_response(status=503, json_data=None)
    session_fail = _mock_session(resp_fail)
    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session_fail):
        v2 = await cache.get_version()

    # Returns stale cached value
    assert v2 == "1.5.0"


@pytest.mark.asyncio
async def test_fallback_to_settings_default_when_no_cache():
    cache = CodexVersionCache(ttl_seconds=60)

    resp_fail = _mock_response(status=500, json_data=None)
    session_fail = _mock_session(resp_fail)
    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session_fail):
        version = await cache.get_version()

    assert version == "0.101.0"


@pytest.mark.asyncio
async def test_fallback_on_network_exception():
    cache = CodexVersionCache(ttl_seconds=60)

    with patch(
        "app.core.clients.codex_version.aiohttp.ClientSession",
        side_effect=aiohttp.ClientError("connection refused"),
    ):
        version = await cache.get_version()

    assert version == "0.101.0"


@pytest.mark.asyncio
async def test_invalidate_clears_cache():
    cache = CodexVersionCache(ttl_seconds=60)
    resp = _mock_response(json_data={"name": "3.0.0"})
    session = _mock_session(resp)

    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session):
        v1 = await cache.get_version()
    assert v1 == "3.0.0"

    await cache.invalidate()

    resp2 = _mock_response(json_data={"name": "4.0.0"})
    session2 = _mock_session(resp2)
    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session2):
        v2 = await cache.get_version()
    assert v2 == "4.0.0"


@pytest.mark.asyncio
async def test_missing_name_field_falls_back():
    cache = CodexVersionCache(ttl_seconds=60)
    resp = _mock_response(json_data={"tag_name": "rust-v1.2.3"})
    session = _mock_session(resp)

    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session):
        version = await cache.get_version()

    assert version == "0.101.0"


def test_ttl_must_be_positive():
    with pytest.raises(ValueError, match="ttl_seconds must be positive"):
        CodexVersionCache(ttl_seconds=0)
    with pytest.raises(ValueError, match="ttl_seconds must be positive"):
        CodexVersionCache(ttl_seconds=-1)
