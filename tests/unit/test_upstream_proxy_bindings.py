from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast

import pytest

from app.core.clients.upstream_proxy import (
    UpstreamProxyConfigurationError,
    aiohttp_proxy_kwargs,
    is_socks_upstream_proxy,
    normalize_upstream_proxy_url,
    redact_upstream_proxy_url,
)
from app.core.crypto import TokenEncryptor
from app.db.models import Account
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy.repo_bundle import ProxyRepoFactory, ProxyRepositories
from app.modules.proxy.service import ProxyService
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.settings.repository import SettingsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository


def test_normalize_upstream_proxy_url_accepts_http_https_and_socks() -> None:
    assert normalize_upstream_proxy_url("http://user:pass@127.0.0.1:8080") == "http://user:pass@127.0.0.1:8080"
    assert normalize_upstream_proxy_url("https://proxy.example:8443") == "https://proxy.example:8443"
    assert (
        normalize_upstream_proxy_url("socks5://user:pass@proxy.example:1080") == "socks5://user:pass@proxy.example:1080"
    )


def test_normalize_upstream_proxy_url_rejects_unsupported_scheme() -> None:
    with pytest.raises(UpstreamProxyConfigurationError):
        normalize_upstream_proxy_url("ftp://proxy.example:21")


def test_redact_upstream_proxy_url_hides_credentials() -> None:
    assert redact_upstream_proxy_url("socks5://user:pass@proxy.example:1080") == "socks5://proxy.example:1080"


def test_aiohttp_proxy_kwargs_are_only_for_http_proxy_schemes() -> None:
    assert aiohttp_proxy_kwargs("https://proxy.example:8443") == {"proxy": "https://proxy.example:8443"}
    assert aiohttp_proxy_kwargs("socks5://proxy.example:1080") == {}
    assert is_socks_upstream_proxy("socks5h://proxy.example:1080")


def _repos(settings: SettingsRepository) -> ProxyRepositories:
    return ProxyRepositories(
        accounts=cast(AccountsRepository, None),
        usage=cast(UsageRepository, None),
        request_logs=cast(RequestLogsRepository, None),
        sticky_sessions=cast(StickySessionsRepository, None),
        api_keys=cast(ApiKeysRepository, None),
        additional_usage=cast(AdditionalUsageRepository, None),
        settings=settings,
    )


@pytest.mark.asyncio
async def test_proxy_service_resolves_account_proxy_before_group_and_global() -> None:
    encryptor = TokenEncryptor()
    account = Account(
        id="acc_proxy",
        email="proxy@example.com",
        plan_type="plus",
        access_token_encrypted=b"a",
        refresh_token_encrypted=b"r",
        id_token_encrypted=b"i",
        last_refresh=cast(object, None),
        upstream_proxy_url_encrypted=encryptor.encrypt("http://account-proxy:8080"),
        upstream_proxy_group="team-a",
    )
    settings_repo = SimpleNamespace(
        get_upstream_proxy_group=lambda name: SimpleNamespace(
            proxy_url_encrypted=encryptor.encrypt("http://group-proxy:8080")
        ),
        get_or_create=lambda: SimpleNamespace(
            upstream_proxy_url_encrypted=encryptor.encrypt("http://global-proxy:8080")
        ),
    )

    @asynccontextmanager
    async def repo_factory():
        yield _repos(cast(SettingsRepository, settings_repo))

    service = ProxyService(cast(ProxyRepoFactory, repo_factory))

    assert await service._resolve_upstream_proxy_url(account) == "http://account-proxy:8080"


@pytest.mark.asyncio
async def test_proxy_service_resolves_group_before_global() -> None:
    encryptor = TokenEncryptor()
    account = Account(
        id="acc_proxy_group",
        email="proxy-group@example.com",
        plan_type="plus",
        access_token_encrypted=b"a",
        refresh_token_encrypted=b"r",
        id_token_encrypted=b"i",
        last_refresh=cast(object, None),
        upstream_proxy_group="team-a",
    )

    class SettingsRepo:
        async def get_upstream_proxy_group(self, name: str):
            return SimpleNamespace(proxy_url_encrypted=encryptor.encrypt(f"http://{name}-proxy:8080"))

        async def get_or_create(self):
            return SimpleNamespace(upstream_proxy_url_encrypted=encryptor.encrypt("http://global-proxy:8080"))

    @asynccontextmanager
    async def repo_factory():
        yield _repos(cast(SettingsRepository, SettingsRepo()))

    service = ProxyService(cast(ProxyRepoFactory, repo_factory))

    assert await service._resolve_upstream_proxy_url(account) == "http://team-a-proxy:8080"


@pytest.mark.asyncio
async def test_proxy_service_resolves_global_proxy_when_account_has_no_binding() -> None:
    encryptor = TokenEncryptor()
    account = Account(
        id="acc_proxy_global",
        email="proxy-global@example.com",
        plan_type="plus",
        access_token_encrypted=b"a",
        refresh_token_encrypted=b"r",
        id_token_encrypted=b"i",
        last_refresh=cast(object, None),
    )

    class SettingsRepo:
        async def get_upstream_proxy_group(self, name: str):
            return None

        async def get_or_create(self):
            return SimpleNamespace(upstream_proxy_url_encrypted=encryptor.encrypt("http://global-proxy:8080"))

    @asynccontextmanager
    async def repo_factory():
        yield _repos(cast(SettingsRepository, SettingsRepo()))

    service = ProxyService(cast(ProxyRepoFactory, repo_factory))

    assert await service._resolve_upstream_proxy_url(account) == "http://global-proxy:8080"
