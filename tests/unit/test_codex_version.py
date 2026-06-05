from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

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


def _mock_session_per_url(responses: dict[str, MagicMock]) -> MagicMock:
    session = MagicMock()

    def _get(url, **_kwargs):
        host = urlparse(url).hostname
        if host in responses:
            return responses[host]
        raise AssertionError(f"unexpected URL: {url}")

    session.get = MagicMock(side_effect=_get)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@pytest.mark.asyncio
async def test_fetches_version_from_github():
    cache = CodexVersionCache(ttl_seconds=60)
    resp = _mock_response(json_data={"name": "1.2.3", "tag_name": "rust-v1.2.3"})
    session = _mock_session(resp)

    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session) as client_session_cls:
        version = await cache.get_version()

    assert version == "1.2.3"
    assert client_session_cls.call_args.kwargs["trust_env"] is True


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


@pytest.mark.asyncio
async def test_falls_back_to_npm_when_github_rate_limited():
    cache = CodexVersionCache(ttl_seconds=60)
    github_resp = _mock_response(status=403, json_data=None)
    npm_resp = _mock_response(json_data={"version": "0.130.0"})
    session = _mock_session_per_url({"api.github.com": github_resp, "registry.npmjs.org": npm_resp})

    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session):
        version = await cache.get_version()

    assert version == "0.130.0"


@pytest.mark.asyncio
async def test_falls_back_to_npm_when_github_returns_invalid_name():
    cache = CodexVersionCache(ttl_seconds=60)
    github_resp = _mock_response(json_data={"name": "rust-v0.130.0"})
    npm_resp = _mock_response(json_data={"version": "0.130.0"})
    session = _mock_session_per_url({"api.github.com": github_resp, "registry.npmjs.org": npm_resp})

    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session):
        version = await cache.get_version()

    assert version == "0.130.0"


@pytest.mark.asyncio
async def test_npm_invalid_version_falls_back_to_settings_default():
    cache = CodexVersionCache(ttl_seconds=60)
    github_resp = _mock_response(status=403, json_data=None)
    npm_resp = _mock_response(json_data={"version": "0.130.0-rc.1"})
    session = _mock_session_per_url({"api.github.com": github_resp, "registry.npmjs.org": npm_resp})

    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session):
        version = await cache.get_version()

    assert version == "0.101.0"


@pytest.mark.asyncio
async def test_npm_missing_version_field_falls_back_to_settings_default():
    cache = CodexVersionCache(ttl_seconds=60)
    github_resp = _mock_response(status=403, json_data=None)
    npm_resp = _mock_response(json_data={"name": "@openai/codex"})
    session = _mock_session_per_url({"api.github.com": github_resp, "registry.npmjs.org": npm_resp})

    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session):
        version = await cache.get_version()

    assert version == "0.101.0"


@pytest.mark.asyncio
async def test_github_skipped_when_returning_valid_version():
    cache = CodexVersionCache(ttl_seconds=60)
    github_resp = _mock_response(json_data={"name": "0.130.0"})
    npm_resp = _mock_response(json_data={"version": "9.9.9"})
    session = _mock_session_per_url({"api.github.com": github_resp, "registry.npmjs.org": npm_resp})

    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session):
        version = await cache.get_version()

    # GitHub wins; npm is not consulted.
    assert version == "0.130.0"
    urls = [call.args[0] for call in session.get.call_args_list]
    requested_hosts = [urlparse(url).hostname for url in urls]
    assert requested_hosts.count("api.github.com") == 1
    assert requested_hosts.count("registry.npmjs.org") == 0


@pytest.mark.asyncio
async def test_falls_back_to_stale_cache_when_both_sources_fail():
    cache = CodexVersionCache(ttl_seconds=60)

    # Populate cache via GitHub
    github_ok = _mock_response(json_data={"name": "1.5.0"})
    npm_unused = _mock_response(json_data={"version": "9.9.9"})
    session_ok = _mock_session_per_url({"api.github.com": github_ok, "registry.npmjs.org": npm_unused})
    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session_ok):
        v1 = await cache.get_version()
    assert v1 == "1.5.0"

    # Expire the cache and make both sources fail
    cache._cached_at = time.monotonic() - 120
    github_fail = _mock_response(status=403, json_data=None)
    npm_fail = _mock_response(status=503, json_data=None)
    session_fail = _mock_session_per_url({"api.github.com": github_fail, "registry.npmjs.org": npm_fail})
    with patch("app.core.clients.codex_version.aiohttp.ClientSession", return_value=session_fail):
        v2 = await cache.get_version()

    assert v2 == "1.5.0"
