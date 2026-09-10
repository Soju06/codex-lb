from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.crypto import TokenEncryptor
from app.core.upstream_proxy import UpstreamProxyRouteError, resolve_proxy_endpoint, resolve_upstream_route
from app.core.upstream_proxy.resolver import _is_missing_upstream_proxy_schema
from app.db.models import (
    Account,
    AccountProxyBinding,
    AccountStatus,
    Base,
    DashboardSettings,
    ProxyEndpoint,
    ProxyPool,
    ProxyPoolMember,
)

pytestmark = pytest.mark.unit


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _encryptor() -> TokenEncryptor:
    return TokenEncryptor(key=Fernet.generate_key())


def _account(encryptor: TokenEncryptor, account_id: str = "acc_1") -> Account:
    token = encryptor.encrypt("token")
    return Account(
        id=account_id,
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=token,
        refresh_token_encrypted=token,
        id_token_encrypted=token,
        last_refresh=datetime(2026, 1, 1),
        status=AccountStatus.ACTIVE,
    )


def test_missing_upstream_proxy_schema_matches_unquoted_postgres_column_message() -> None:
    exc = OperationalError(
        "SELECT dashboard_settings.upstream_proxy_routing_enabled",
        {},
        Exception("column dashboard_settings.upstream_proxy_routing_enabled does not exist"),
    )

    assert _is_missing_upstream_proxy_schema(exc) is True


async def _pool_with_endpoints(session: AsyncSession, encryptor: TokenEncryptor, pool_id: str) -> None:
    pool = ProxyPool(id=pool_id, name=pool_id)
    first = ProxyEndpoint(
        id=f"{pool_id}_ep_1",
        name="first",
        scheme="https",
        host="proxy-one.test",
        port=8080,
        username="user",
        password_encrypted=encryptor.encrypt("secret"),
    )
    second = ProxyEndpoint(
        id=f"{pool_id}_ep_2",
        name="second",
        scheme="socks5",
        host="proxy-two.test",
        port=1080,
    )
    session.add_all(
        [
            pool,
            first,
            second,
            ProxyPoolMember(id=f"{pool_id}_m_1", pool=pool, endpoint=first, sort_order=10),
            ProxyPoolMember(id=f"{pool_id}_m_2", pool=pool, endpoint=second, sort_order=20),
        ]
    )


@pytest.mark.asyncio
async def test_account_binding_uses_bound_pool_and_same_pool_fallbacks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    encryptor = _encryptor()
    async with session_factory() as session:
        account = _account(encryptor)
        await _pool_with_endpoints(session, encryptor, "bound_pool")
        await _pool_with_endpoints(session, encryptor, "default_pool")
        session.add_all(
            [
                account,
                AccountProxyBinding(id="binding_1", account=account, pool_id="bound_pool"),
                DashboardSettings(
                    id=1,
                    upstream_proxy_routing_enabled=True,
                    upstream_proxy_default_pool_id="default_pool",
                ),
            ]
        )
        await session.commit()

        route = await resolve_upstream_route(session, account_id=account.id, operation="responses", encryptor=encryptor)

    assert route is not None
    assert route.mode == "account_bound"
    assert route.pool_id == "bound_pool"
    assert route.endpoint.id == "bound_pool_ep_1"
    assert route.endpoint.proxy_url == "https://user:secret@proxy-one.test:8080"
    assert [fallback.id for fallback in route.fallbacks] == ["bound_pool_ep_2"]
    assert route.fallbacks[0].proxy_url == "socks5h://proxy-two.test:1080"


@pytest.mark.parametrize("scheme", ["http", "socks5", "socks5h"])
def test_resolver_allows_plaintext_proxy_credentials_and_warns_once(
    scheme: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Credentials on http/socks5 proxies are allowed (the dashboard warns);
    the resolver logs one credential-free warning per endpoint, not per request."""

    from app.core.upstream_proxy import resolver as resolver_module

    monkeypatch.setattr(resolver_module, "_PLAINTEXT_CREDENTIAL_WARNINGS_EMITTED", set())
    encryptor = _encryptor()
    endpoint = ProxyEndpoint(
        id=f"plaintext-{scheme}",
        name="plaintext",
        scheme=scheme,
        host="proxy.test",
        port=8080,
        username="user",
        password_encrypted=encryptor.encrypt("secret"),
    )

    with caplog.at_level("WARNING", logger="app.core.upstream_proxy.resolver"):
        resolved = resolve_proxy_endpoint(endpoint, encryptor=encryptor)
        resolve_proxy_endpoint(endpoint, encryptor=encryptor)

    assert resolved.username == "user"
    assert resolved.password == "secret"
    assert resolved.plaintext_credentials is True
    warnings = [record for record in caplog.records if "plaintext" in record.getMessage()]
    assert len(warnings) == 1
    assert "secret" not in warnings[0].getMessage()
    assert "user" not in warnings[0].getMessage().split("proxy in plaintext")[0].replace("plaintext-", "")


def test_resolver_https_credentials_are_not_flagged_as_plaintext() -> None:
    encryptor = _encryptor()
    endpoint = ProxyEndpoint(
        id="tls",
        name="tls",
        scheme="https",
        host="proxy.test",
        port=8443,
        username="user",
        password_encrypted=encryptor.encrypt("secret"),
    )

    assert resolve_proxy_endpoint(endpoint, encryptor=encryptor).plaintext_credentials is False


def test_resolver_rejects_colon_in_proxy_username() -> None:
    # RFC 7617 Basic credentials cannot carry a colon in the user-id; aiohttp's
    # encode_basic_auth raises, so fail closed at resolution instead.
    encryptor = _encryptor()
    endpoint = ProxyEndpoint(
        id="colon",
        name="colon",
        scheme="https",
        host="proxy.test",
        port=8080,
        username="user:name",
        password_encrypted=encryptor.encrypt("secret"),
    )

    with pytest.raises(UpstreamProxyRouteError) as exc_info:
        resolve_proxy_endpoint(endpoint, encryptor=encryptor)

    assert exc_info.value.reason == "invalid_proxy_username"


@pytest.mark.asyncio
async def test_strict_default_pool_fails_closed_when_unconfigured(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(DashboardSettings(id=1, upstream_proxy_routing_enabled=True))
        await session.commit()

        with pytest.raises(UpstreamProxyRouteError) as exc_info:
            await resolve_upstream_route(session, account_id=None, operation="oauth", scope="bootstrap")

    assert exc_info.value.reason == "default_pool_unconfigured"


@pytest.mark.asyncio
async def test_account_bound_pool_does_not_fall_back_to_default(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    encryptor = _encryptor()
    async with session_factory() as session:
        account = _account(encryptor)
        session.add_all(
            [
                account,
                ProxyPool(id="empty_bound", name="empty"),
                AccountProxyBinding(id="binding_1", account=account, pool_id="empty_bound"),
                DashboardSettings(
                    id=1,
                    upstream_proxy_routing_enabled=True,
                    upstream_proxy_default_pool_id="default_pool",
                ),
            ]
        )
        await _pool_with_endpoints(session, encryptor, "default_pool")
        await session.commit()

        with pytest.raises(UpstreamProxyRouteError) as exc_info:
            await resolve_upstream_route(session, account_id=account.id, operation="models", encryptor=encryptor)

    assert exc_info.value.reason == "pool_has_no_active_endpoints"
    assert exc_info.value.pool_id == "empty_bound"
