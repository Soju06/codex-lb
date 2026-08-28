"""Regression tests for issue #1057: per-account proxy IP leak in OAuth bootstrap.

These tests verify that:
1. OAuth token exchange fails closed when per-account proxy bindings exist
   but no default pool is configured (instead of silently using direct egress).
2. Token refresh fails closed when an account has a proxy binding but route
   resolution returns None (binding toggled inactive / pool deleted).

Without the fix, both paths silently fall back to direct egress, creating an
IP split between the initial OAuth / refresh and subsequent per-account-proxy
API calls. OpenAI detects the IP change and invalidates the session, causing
"Re-auth required" after hours/days.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import cast

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth.refresh import RefreshError
from app.core.crypto import TokenEncryptor
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
from app.modules.accounts import auth_manager as auth_manager_module
from app.modules.accounts.auth_manager import AccountsRepositoryPort, AuthManager
from app.modules.oauth import service as oauth_service_module
from app.modules.oauth.service import OAuthError

pytestmark = pytest.mark.unit


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


async def _pool_with_endpoints(session: AsyncSession, encryptor: TokenEncryptor, pool_id: str) -> None:
    pool = ProxyPool(id=pool_id, name=pool_id)
    ep = ProxyEndpoint(
        id=f"{pool_id}_ep_1",
        name="first",
        scheme="http",
        host="proxy-one.test",
        port=8080,
    )
    session.add_all(
        [
            pool,
            ep,
            ProxyPoolMember(id=f"{pool_id}_m_1", pool=pool, endpoint=ep, sort_order=10),
        ]
    )


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


# ─── Test 1: OAuth fails closed when bindings exist but no default pool ───


@pytest.mark.asyncio
async def test_oauth_route_fails_closed_when_bindings_exist_but_no_default_pool(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for #1057: when per-account proxy bindings exist but no
    default pool is configured, OAuth MUST fail closed instead of silently
    falling back to direct egress.

    Without this guard, the initial token exchange goes direct while all
    subsequent API calls use the per-account proxy — an IP split that causes
    OpenAI to invalidate the session.
    """

    @asynccontextmanager
    async def fake_background_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(oauth_service_module, "get_background_session", fake_background_session)

    encryptor = _encryptor()
    async with session_factory() as session:
        account = _account(encryptor)
        await _pool_with_endpoints(session, encryptor, "bound_pool")
        session.add_all(
            [
                account,
                AccountProxyBinding(id="binding_1", account=account, pool_id="bound_pool"),
                # upstream_proxy_routing_enabled defaults to False — bindings
                # work without it, but the default-pool fallback does not.
            ]
        )
        await session.commit()

    # Without fix: _oauth_route() returns None (direct egress).
    # With fix: _oauth_route() detects active bindings and fails closed.
    with pytest.raises(OAuthError) as exc_info:
        await oauth_service_module._oauth_route()

    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_oauth_route_uses_default_pool_when_bindings_exist(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When active bindings exist and a default pool is configured,
    _oauth_route() MUST return a resolved route from that pool."""

    @asynccontextmanager
    async def fake_background_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(oauth_service_module, "get_background_session", fake_background_session)

    encryptor = _encryptor()
    async with session_factory() as session:
        account = _account(encryptor)
        await _pool_with_endpoints(session, encryptor, "default_pool")
        session.add_all(
            [
                account,
                AccountProxyBinding(id="binding_1", account=account, pool_id="default_pool"),
                DashboardSettings(
                    id=1,
                    upstream_proxy_routing_enabled=False,
                    upstream_proxy_default_pool_id="default_pool",
                ),
            ]
        )
        await session.commit()

    route = await oauth_service_module._oauth_route()

    assert route is not None
    assert route.mode == "default_pool"
    assert route.pool_id == "default_pool"


@pytest.mark.asyncio
async def test_oauth_route_preserves_direct_egress_when_no_bindings_exist(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no active proxy bindings exist, _oauth_route() MUST return None
    (direct egress) instead of failing closed."""

    @asynccontextmanager
    async def fake_background_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(oauth_service_module, "get_background_session", fake_background_session)

    route = await oauth_service_module._oauth_route()

    assert route is None


# ─── Test 2: Token refresh fails closed when binding exists but route is None ───


class _DummyRepo:
    def __init__(self) -> None:
        self.tokens_payload: dict[str, object] | None = None
        self.status_payload: dict[str, object] | None = None
        self.accounts_by_id: dict[str, Account] = {}

    async def get_by_id(self, account_id: str) -> Account | None:
        return self.accounts_by_id.get(account_id)

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None = None,
    ) -> bool:
        self.status_payload = {
            "account_id": account_id,
            "status": status,
            "deactivation_reason": deactivation_reason,
        }
        return True

    async def rotate_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        *,
        expected_refresh_token_encrypted: bytes,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
    ) -> bool:
        self.tokens_payload = {
            "account_id": account_id,
            "access_token_encrypted": access_token_encrypted,
            "refresh_token_encrypted": refresh_token_encrypted,
            "id_token_encrypted": id_token_encrypted,
            "last_refresh": last_refresh,
            "plan_type": plan_type,
            "email": email,
            "chatgpt_account_id": chatgpt_account_id,
            "workspace_id": workspace_id,
            "workspace_label": workspace_label,
            "seat_type": seat_type,
        }
        return True

    async def update_account_metadata(
        self,
        account_id: str,
        *,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
        last_refresh: datetime | None = None,
    ) -> bool:
        return True

    async def workspace_slot_taken(
        self,
        *,
        account_id: str,
        email: str,
        chatgpt_account_id: str | None,
        workspace_id: str,
    ) -> bool:
        del account_id
        return (email, chatgpt_account_id, workspace_id) in set()


@pytest.mark.asyncio
async def test_refresh_fails_closed_when_binding_exists_but_route_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for #1057: when an account has a proxy binding but route
    resolution returns None (binding toggled inactive, pool deleted), the
    refresh MUST raise instead of silently using direct egress.

    Without this guard, the refresh goes direct, creating an IP split that
    causes OpenAI to invalidate the session.
    """

    @asynccontextmanager
    async def fake_background_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def fake_resolve_route(*_args: object, **_kwargs: object) -> None:
        return None

    async def unexpected_refresh(_: str, **_kwargs: object):
        raise AssertionError("refresh_access_token must not run when route is None and account has a binding")

    monkeypatch.setattr(auth_manager_module, "get_background_session", fake_background_session)
    monkeypatch.setattr(auth_manager_module, "resolve_upstream_route", fake_resolve_route)
    monkeypatch.setattr(auth_manager_module, "refresh_access_token", unexpected_refresh)

    encryptor = TokenEncryptor()
    async with session_factory() as session:
        account = _account(encryptor, "acc_binding_none")
        await _pool_with_endpoints(session, encryptor, "bound_pool")
        session.add_all(
            [
                account,
                AccountProxyBinding(id="binding_1", account=account, pool_id="bound_pool"),
            ]
        )
        await session.commit()

    repo = _DummyRepo()
    manager = AuthManager(cast(AccountsRepositoryPort, repo))

    # Without fix: refresh silently uses direct egress (route=None, allow_direct_egress=True).
    # With fix: refresh raises RefreshError because account has a binding but route is None.
    with pytest.raises(RefreshError) as exc_info:
        await manager.refresh_account(account)

    assert exc_info.value.code == "upstream_proxy_unavailable"
    assert exc_info.value.transport_error is True
    assert repo.tokens_payload is None
